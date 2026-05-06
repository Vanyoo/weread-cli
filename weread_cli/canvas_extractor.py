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

        # 拼接行内文本 — 动态统计字符间距，只在单词间大间隙处插入空格
        # 先计算行内相邻字符的平均间距，以区分「词内间距」和「词间空格」
        gaps = []
        for i in range(1, len(cleaned)):
            g = cleaned[i]["x"] - cleaned[i-1]["x"]
            prev_t = cleaned[i-1]["text"]
            # 只统计英文字符之间的间距
            if prev_t.isascii() and prev_t.isalpha() and cleaned[i]["text"].isascii() and cleaned[i]["text"].isalpha():
                gaps.append(g)

        # 用中位数作为词内基准间距
        if gaps:
            sorted_gaps = sorted(gaps)
            median_gap = sorted_gaps[len(sorted_gaps) // 2]
            space_threshold = median_gap * 2.5  # 词间间隙通常显著大于词内间距
        else:
            space_threshold = None  # 无英文连续字符，不加空格

        parts = []
        for i, item in enumerate(cleaned):
            if i > 0 and space_threshold is not None:
                gap = item["x"] - cleaned[i-1]["x"]
                prev_t = cleaned[i-1]["text"]
                cur_t = item["text"]
                # 只有当前后都是英文且间隙超过阈值时才插入空格
                if prev_t.isascii() and prev_t.isalpha() and cur_t.isascii() and cur_t.isalpha() and gap > space_threshold:
                    parts.append(" ")
            parts.append(item["text"])
        line_text = "".join(parts).strip()
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
            if current_paragraph:
                prev_para_text = current_paragraph[-1]
                # 上一段尾字符和当前行首字符都是英文 → 插入空格防止断行粘连
                if prev_para_text and prev_para_text[-1].isascii() and prev_para_text[-1].isalpha() and line_text[0].isascii() and line_text[0].isalpha():
                    current_paragraph.append(" ")
            current_paragraph.append(line_text)
        prev_y = y

    if current_paragraph:
        paragraphs.append("".join(current_paragraph))

    # ── 第七步：过滤字体预加载测试行 ─────────────────────
    # 微信读书会在 Canvas 初始化时渲染完整的 ASCII 字符集用于字体映射，
    # 这不是正文内容，需要过滤掉。
    known_noise = [
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ`~1!2@3#4$5%6^7&8*9(0)-_=+[{]}|;:',<.>/?",
    ]
    filtered = []
    for p in paragraphs:
        # 过滤已知的字体测试字符串
        if p in known_noise:
            continue
        # 也过滤包含完整 ASCII 字符集的超长行（>80 字符且包含所有字母）
        if len(p) > 80:
            has_all_lower = all(c in p for c in "abcdefghijklmnopqrstuvwxyz")
            has_all_upper = all(c in p for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
            if has_all_lower and has_all_upper:
                continue
        filtered.append(p)

    return "\n\n".join(filtered)
