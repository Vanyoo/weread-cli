# -*- coding: utf-8 -*-
"""微信读书 chapter 响应的容错还原。

Web 端的 chapter 响应由一个短十六进制标记和 Base64/文本负载组成，
但负载的字符边界会因分片长度变化，不能直接固定使用 slice(32)。
这里保留多种候选偏移并按内容特征选择最接近原文的候选，供 CLI 在
不改变登录、付费和访问控制语义的前提下读取当前页面已授权返回的内容。
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Optional


_B64_RE = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")
_TEXT_SIGNAL_RE = re.compile(
    r"(?:<\?xml|<html|<body|<p\b|</p>|/\*|\.frontCover|"
    r"[\u3400-\u9fff]|见了狼嗥|妈老在暗中|我在抽屉)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DecodedResponse:
    """响应解码结果，包含用于诊断的候选 framing 信息."""

    text: str
    payload_start: int
    trailing_trim: int
    decoded_bytes: int
    score: float


def _valid_utf8(buffer: bytes) -> bool:
    try:
        buffer.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False
    return True


def _score(buffer: bytes, text: str, payload_start: int) -> float:
    printable = sum(
        ch in "\r\n\t" or ch >= " " for ch in text
    ) / max(1, len(text))
    signal = 20.0 if _TEXT_SIGNAL_RE.search(text) else 0.0
    # Base64 字符流的四字节相位让某些偏移产生带 U+FFFD 的假正文。
    # 这里轻微偏好内容起点更干净的候选，但不强依赖固定 32 字符边界。
    clean_text = text.replace("\uFFFD", "")
    clean_bonus = 0.5 if clean_text and not clean_text.startswith(("\x08", "\x0f")) else 0.0
    return printable + signal - (1.0 if "\uFFFD" in text else 0.0) + clean_bonus


def decode_response(raw: str | bytes) -> DecodedResponse:
    """还原一个已授权捕获的 chapter 响应。

    参数可以是浏览器 XHR 的 responseText/response，也可以是离线捕获的
    响应字符串。函数不会发起请求，也不会绕过任何访问控制。
    """
    if isinstance(raw, bytes):
        raw_text = raw.decode("ascii", errors="replace")
    else:
        raw_text = raw
    if not raw_text:
        raise ValueError("chapter response is empty")

    candidates = []
    for start in range(0, min(48, len(raw_text)) + 1):
        for trailing_trim in range(0, 3):
            payload = raw_text[start:]
            if trailing_trim and len(payload) > trailing_trim:
                payload = payload[:-trailing_trim]
            payload = payload.rstrip("=")
            if len(payload) % 4 == 1:
                payload = payload[:-1]
            payload += "=" * ((4 - len(payload) % 4) % 4)
            if not _B64_RE.match(payload):
                continue
            try:
                buffer = base64.b64decode(payload, validate=True)
            except (binascii.Error, ValueError):
                continue
            text = buffer.decode("utf-8", errors="replace")
            score = _score(buffer, text, start)
            candidates.append((score, start, trailing_trim, buffer, text))

    if not candidates:
        raise ValueError("no Base64 payload found in chapter response")

    # 评分相同时优先选择字节最多的候选，再选择更早的候选，
    # 避免因响应末尾 padding 产生重复的低质量答案。
    candidates.sort(key=lambda item: (item[0], len(item[3]), -item[1]), reverse=True)
    score, start, trailing_trim, buffer, text = candidates[0]
    return DecodedResponse(
        text=text,
        payload_start=start,
        trailing_trim=trailing_trim,
        decoded_bytes=len(buffer),
        score=score,
    )


def html_to_text(content: str) -> str:
    """将已还原的 XHTML/HTML 片段转换为 CLI 可显示文本。"""
    text = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", " ", content, flags=re.I)
    text = re.sub(r"<style\b[^>]*>[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\r?\n\s*", "\n", text)
    return text.strip()
