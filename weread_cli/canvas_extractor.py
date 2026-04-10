# -*- coding: utf-8 -*-
"""Canvas 文本重建模块

从 fillText 调用捕获的坐标数据中重建可读文本。
"""

from typing import List, Dict


def reconstruct_text_from_canvas(captured: List[Dict]) -> str:
    """从 Canvas fillText 捕获的数据重建文本

    Canvas 引擎会对同一字符调用多次 fillText（阴影、描边、填充等），
    此函数负责去重、分行、分段，输出可读文本。

    Args:
        captured: 捕获的 fillText 调用列表，每项包含 text, x, y

    Returns:
        重建后的文本，段落用空行分隔
    """
    if not captured:
        return ""

    # ── 第一步：按时间排序 ──────────────────────────────
    items = [item for item in captured if item.get("text") and item["text"].strip()]
    items.sort(key=lambda i: i.get("timestamp", 0))

    # ── 第二步：坐标去重 ────────────────────────────────
    # 同一字符被多次渲染（阴影/描边/填充），坐标几乎相同。
    # 策略：在容差范围内，只保留第一次出现。
    x_tol = 3.0
    y_tol = 3.0

    deduped = []
    for item in items:
        x = item.get("x", 0)
        y = item.get("y", 0)
        text = item["text"]

        is_dup = False
        for ex in deduped:
            if (abs(ex["x"] - x) < x_tol and
                    abs(ex["y"] - y) < y_tol and
                    ex["text"] == text):
                is_dup = True
                break

        if not is_dup:
            deduped.append(item)

    if not deduped:
        return ""

    # ── 第三步：按 y 坐标分组为行 ────────────────────────
    line_y_tol = 6  # 同一行的 y 容差
    deduped.sort(key=lambda i: (i.get("y", 0), i.get("x", 0)))

    line_groups = []
    cur_y = None
    cur_items = []

    for item in deduped:
        y = item.get("y", 0)
        if cur_y is None:
            cur_y = y
            cur_items = [item]
        elif abs(y - cur_y) < line_y_tol:
            cur_items.append(item)
        else:
            line_groups.append(cur_items)
            cur_y = y
            cur_items = [item]

    if cur_items:
        line_groups.append(cur_items)

    # ── 第四步：行内去重 + 拼接 ──────────────────────────
    # 同一行内也可能有重复，按 x 排序后去重
    processed_lines = []  # List of (y, text)

    for line_items in line_groups:
        line_items.sort(key=lambda i: i.get("x", 0))

        # 行内坐标去重
        cleaned = []
        for item in line_items:
            if (not cleaned or
                    abs(item["x"] - cleaned[-1]["x"]) > x_tol * 3 or
                    item["text"] != cleaned[-1]["text"]):
                cleaned.append(item)

        line_text = "".join(item["text"] for item in cleaned).strip()
        if line_text:
            processed_lines.append((line_items[0].get("y", 0), line_text))

    if not processed_lines:
        return ""

    # ── 第五步：去除重复行 ───────────────────────────────
    # 有些页面会渲染相同文字到不同 y 位置（例如翻页过渡效果）
    # 如果两行文字完全相同且 y 接近，只保留一行
    unique_lines = []
    prev_text = None
    prev_y = None

    for y, text in processed_lines:
        if text == prev_text and abs(y - prev_y) < 50:
            # 完全相同的行且位置接近，跳过
            continue
        unique_lines.append((y, text))
        prev_text = text
        prev_y = y

    # ── 第六步：分段 ────────────────────────────────────
    paragraphs = []
    current_paragraph = []
    prev_y = None
    para_gap = 35  # 行间距超过此值认为是新段落

    for y, line_text in unique_lines:
        if prev_y is not None and (y - prev_y) > para_gap:
            if current_paragraph:
                paragraphs.append("".join(current_paragraph))
                current_paragraph = []

        if line_text:
            current_paragraph.append(line_text)
        prev_y = y

    if current_paragraph:
        paragraphs.append("".join(current_paragraph))

    return "\n\n".join(paragraphs)
