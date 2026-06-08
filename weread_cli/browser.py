# -*- coding: utf-8 -*-
"""
CDP 浏览器管理器 —— 持久化微信读书 Web 实例

架构:
  weread-cli (TUI)  ←→  CDP  ←→  微信读书 Web（持久运行）
       ↑                              ↓
    提取DOM文本                    正常渲染/翻页
    展示到终端                    同步阅读进度到服务器

所有阅读操作都通过浏览器进行，确保与微信读书 Web 端完全同步。
"""

import json
import os
import re
import time
import hashlib
import subprocess
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass, field


def _md5(data: str) -> str:
    return hashlib.md5(data.encode()).hexdigest()


def weread_hash(data) -> str:
    """
    微信读书的 hash 编码算法。
    用于将 bookId, chapterUid 编码为 URL 路径参数。
    来源: wrx/src/sites/weread/utils.js
    """
    if isinstance(data, int):
        data = str(data)
    if not isinstance(data, str):
        return data

    data_md5 = _md5(data)
    result = data_md5[:3]

    if data.isdigit():
        parts = []
        for i in range(0, len(data), 9):
            chunk = data[i:i + 9]
            parts.append(hex(int(chunk))[2:])
        prefix = "3"
        encoded_parts = parts
    else:
        hex_str = "".join(hex(ord(c))[2:] for c in data)
        prefix = "4"
        encoded_parts = [hex_str]

    result += prefix
    result += "2" + data_md5[-2:]

    for i, part in enumerate(encoded_parts):
        length_hex = hex(len(part))[2:]
        if len(length_hex) == 1:
            length_hex = "0" + length_hex
        result += length_hex + part
        if i < len(encoded_parts) - 1:
            result += "g"

    if len(result) < 20:
        result += data_md5[:20 - len(result)]

    result += _md5(result)[:3]
    return result


def _reverse_weread_hash_int(hash_str: str) -> Optional[str]:
    """
    反向解码 weread_hash 编码的整数。
    URL 中的 chapter_hash 格式: xxx32yy02<hex>...
    提取 hex 部分转回十进制字符串。
    """
    # 查找 "32" 后的十六进制部分
    # 格式: ...32yy LLLL hexdata ...
    # 其中 yy 是 md5 最后两位，LLLL 是长度（2位十六进制），hexdata 是实际的十六进制数据
    match = re.search(r'32[0-9a-f]{2}([0-9a-f]{2})([0-9a-f]+)', hash_str)
    if match:
        length_hex = match.group(1)
        hex_data = match.group(2)
        expected_len = int(length_hex, 16)
        hex_value = hex_data[:expected_len]
        try:
            return str(int(hex_value, 16))
        except ValueError:
            pass
    return None


@dataclass
class ReaderState:
    """阅读器当前状态"""
    book_id: str = ""
    book_title: str = ""
    chapter_uid: int = 0
    chapter_title: str = ""
    chapter_index: int = 0
    total_chapters: int = 0
    progress_pct: float = 0.0
    reader_url: str = ""


class CDPBrowserManager:
    """
    管理一个持久的微信读书浏览器实例。

    生命周期:
        manager = CDPBrowserManager(cookies)
        manager.start()              # 启动浏览器（一次）
        manager.navigate_to_book(...) # 打开书
        manager.go_to_chapter(...)    # 跳到某章
        content = manager.get_content()  # 提取文本
        manager.close()              # 退出时关闭
    """

    def __init__(
        self,
        cookies,  # Dict[str, str] or List[dict]
        mode: str = "headed",  # "headed" | "headless" | "cdp"
        cdp_url: str = "http://localhost:9222",
    ):
        # 兼容 dict 和 Playwright 数组格式
        if isinstance(cookies, list):
            self.cookies = {c.get("name", ""): c.get("value", "") for c in cookies if c.get("name")}
        else:
            self.cookies = cookies or {}
        self.mode = mode
        self.cdp_url = cdp_url

        # Playwright 实例
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

        # 状态
        self.state = ReaderState()
        self._book_loaded = False
        self._chapter_pages: Dict[int, str] = {}  # chapter_uid -> encoded chapter hash

    # ── 生命周期 ─────────────────────────────────────────────

    def start(self) -> bool:
        """启动或连接浏览器实例

        三种模式:
        - "headed": 启动有头浏览器（可以看到界面，内容正常渲染）
        - "headless": 启动无头浏览器（后台运行，但部分网站不渲染正文）
        - "cdp": 连接已运行的 Chrome（需启动时加 --remote-debugging-port=9222）
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            print("❌ 缺少 playwright，请运行: pip install playwright && python -m playwright install chromium")
            return False

        try:
            self._playwright = sync_playwright().start()

            if self.mode == "cdp":
                # 连接已有的 Chrome 实例
                print(f"🔌 连接 Chrome CDP: {self.cdp_url}")
                self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)

                # 复用已有上下文或使用默认上下文
                if self._browser.contexts:
                    self._context = self._browser.contexts[0]
                else:
                    self._context = self._browser.new_context()

                # 注入 Cookie 到已有上下文中
                if self.cookies:
                    self._context.add_cookies([
                        {
                            "name": k,
                            "value": v,
                            "domain": ".weread.qq.com",
                            "path": "/",
                        }
                        for k, v in self.cookies.items()
                    ])

                # 使用已有页面或新建
                if self._context.pages:
                    self._page = self._context.pages[0]
                else:
                    self._page = self._context.new_page()

                print(f"✅ CDP 连接成功（{len(self._browser.contexts)} 个上下文, {len(self._context.pages)} 个页面）")

            else:
                # 启动新浏览器
                is_headless = self.mode == "headless"
                mode_label = "无头" if is_headless else "有头"
                print(f"🌐 启动{mode_label}浏览器...")

                self._browser = self._playwright.chromium.launch(
                    headless=is_headless,
                    args=[
                        "--disable-gpu",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--window-size=1200,900",
                    ]
                )

                self._context = self._browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1200, "height": 900},
                )

                # 注入 Cookie
                if self.cookies:
                    self._context.add_cookies([
                        {
                            "name": k,
                            "value": v,
                            "domain": ".weread.qq.com",
                            "path": "/",
                        }
                        for k, v in self.cookies.items()
                    ])

                self._page = self._context.new_page()
            
            # 最小化浏览器窗口（macOS）
            if self.mode == "headed":
                self._minimize_window()
            
            # 注入 Canvas 文本拦截脚本
            # 微信读书使用 Canvas 渲染正文，通过拦截 fillText 可以提取文本
            # 关键：捕获 canvas 元素的屏幕偏移量，用于区分左右页
            self._page.add_init_script("""
                window.__CANVAS_TEXT__ = [];
                if (!window.__CANVAS_TEXT_HOOKED__) {
                    window.__CANVAS_TEXT_HOOKED__ = true;
                    const origFillText = CanvasRenderingContext2D.prototype.fillText;
                    CanvasRenderingContext2D.prototype.fillText = function(text, x, y, maxWidth) {
                        if (text && typeof text === 'string' && text.length > 0) {
                            try {
                                var rect = this.canvas.getBoundingClientRect();
                                window.__CANVAS_TEXT__.push({
                                    text: text,
                                    x: x,
                                    y: y + rect.top,
                                    canvasOffsetX: Math.round(rect.left),
                                    canvasOffsetY: Math.round(rect.top),
                                    canvasWidth: Math.round(rect.width),
                                    timestamp: Date.now()
                                });
                            } catch(e) {
                                window.__CANVAS_TEXT__.push({
                                    text: text,
                                    x: x,
                                    y: y,
                                    canvasOffsetX: 0,
                                    canvasOffsetY: 0,
                                    canvasWidth: 0,
                                    timestamp: Date.now()
                                });
                            }
                        }
                        return origFillText.call(this, text, x, y, maxWidth);
                    };
                }
            """)

            return True

        except Exception as e:
            print(f"❌ 启动浏览器失败: {e}")
            self._cleanup()
            return False

    def close(self):
        """关闭浏览器"""
        self._cleanup()

    def _cleanup(self):
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    @property
    def is_alive(self) -> bool:
        return self._page is not None

    # ── 导航 ─────────────────────────────────────────────────

    def _detect_chapter_index_from_url(self, url: str) -> Optional[int]:
        """
        从 WeRead 阅读器 URL 中解析当前章节索引。
        
        URL 格式: https://weread.qq.com/web/reader/{book_hash}k{chapter_hash}
        chapter_hash 是 weread_hash(chapter_idx) 编码的整数。
        通过反向解码得到 chapter_idx。
        """
        match = re.search(r'/web/reader/[0-9a-f]+k([0-9a-f]+)', url)
        if not match:
            return None
        chapter_hash = match.group(1)
        chapter_idx_str = _reverse_weread_hash_int(chapter_hash)
        if chapter_idx_str:
            return int(chapter_idx_str)
        return None

    def navigate_to_book(self, book_id: str) -> bool:
        """
        导航到一本书。打开阅读器首页（封面页）。
        """
        if not self.is_alive:
            return False

        encoded_book = weread_hash(book_id)
        url = f"https://weread.qq.com/web/reader/{encoded_book}"

        try:
            # First inject the canvas text interception script
            self._page.add_init_script("""
                window.__CANVAS_TEXT__ = [];
                if (!window.__CANVAS_TEXT_HOOKED__) {
                    window.__CANVAS_TEXT_HOOKED__ = true;
                    const origFillText = CanvasRenderingContext2D.prototype.fillText;
                    CanvasRenderingContext2D.prototype.fillText = function(text, x, y, maxWidth) {
                        if (text && typeof text === 'string' && text.length > 0) {
                            try {
                                var rect = this.canvas.getBoundingClientRect();
                                window.__CANVAS_TEXT__.push({
                                    text: text,
                                    x: x,
                                    y: y + rect.top,
                                    canvasOffsetX: Math.round(rect.left),
                                    canvasOffsetY: Math.round(rect.top),
                                    canvasWidth: Math.round(rect.width),
                                    timestamp: Date.now()
                                });
                            } catch(e) {
                                window.__CANVAS_TEXT__.push({
                                    text: text,
                                    x: x,
                                    y: y,
                                    canvasOffsetX: 0,
                                    canvasOffsetY: 0,
                                    canvasWidth: 0,
                                    timestamp: Date.now()
                                });
                            }
                        }
                        return origFillText.call(this, text, x, y, maxWidth);
                    };
                }
            """)

            self._page.goto(url, wait_until="domcontentloaded", timeout=20000)
            self._page.wait_for_timeout(3000)

            # 注入 Canvas 文本拦截（页面加载后立即注入）
            self._inject_canvas_hook()

            # 提取书籍信息
            info = self._page.evaluate("""() => {
                const titleEl = document.querySelector('.readerTopBar_title_link, .readerTopBar_title');
                const allText = document.body.innerText;
                return {
                    title: titleEl ? titleEl.innerText.trim() : '',
                    url: window.location.href,
                    hasNextButton: allText.includes('下一页'),
                    hasReadButton: allText.includes('阅读'),
                    hasStartButton: allText.includes('开始阅读'),
                };
            }""")

            self.state.book_id = book_id
            self.state.book_title = info.get("title", "")
            self.state.reader_url = info.get("url", url)
            self._book_loaded = True

            return True

        except Exception as e:
            print(f"❌ 导航到书籍失败: {e}")
            return False

    def _click_through_intro(self) -> bool:
        """
        点击通过封面/版权/简介等前导页面，直到进入正文。
        
        注意：微信读书水平翻页阅读器在 headless 模式下，
        正文内容不会渲染到 DOM 的 innerText 中（使用 canvas 渲染）。
        此方法只能确认导航到章节页面，无法验证正文内容。
        """
        if not self.is_alive:
            return False

        max_clicks = 15
        for i in range(max_clicks):
            next_btn = self._find_text('下一页')
            if not next_btn:
                next_btn = self._find_text('阅读')
                if not next_btn:
                    next_btn = self._find_text('开始阅读')

            if not next_btn:
                # 没有"下一页"按钮了，可能已经到正文或封面
                break

            try:
                next_btn.click(force=True)
                self._page.wait_for_timeout(800)
            except Exception:
                break

            # 检查是否有章节内容元素
            has_chapter_content = self._page.evaluate("""() => {
                const content = document.querySelector('.readerChapterContent');
                if (!content) return false;
                // 在 headless 模式下，正文可能不渲染到 innerText
                // 但至少检查元素存在
                return content.innerHTML.length > 1000;
            }""")

            if has_chapter_content:
                return True

        return False

    def go_to_chapter(self, chapter_uid: int, chapter_title: str = "", chapter_index: int = 0, chapter_idx: int = 0) -> Optional[str]:
        """
        跳转到指定章节。

        策略：通过 JS 修改 location 触发 SPA 内部路由，不触发完整页面重载。
        章节 URL 格式: /web/reader/{book_hash}k{chapter_hash}
        注意: chapter_hash = weread_hash(chapter_idx)，不是 weread_hash(chapter_uid)
        """
        if not self.is_alive:
            return None

        try:
            if not self._book_loaded:
                return None

            # 清空旧 Canvas 文本
            self._page.evaluate("() => window.__CANVAS_TEXT__ = []")

            # 构建完整章节 URL: {book_hash}k{chapter_hash}
            # chapter_hash 使用 chapter_idx（API 返回的章节索引），不是 chapter_uid
            book_hash = weread_hash(self.state.book_id)
            hash_input = chapter_idx if chapter_idx > 0 else chapter_uid
            chapter_hash = weread_hash(hash_input)
            target_path = f"/web/reader/{book_hash}k{chapter_hash}"

            # 通过 JS 修改 location 触发 SPA 路由，而非 page.goto()（会导致页面重载 + Canvas hook 丢失）
            self._page.evaluate(f"() => window.location.href = '{target_path}'")

            # 等待 SPA 路由完成 + Canvas 渲染
            # 首次章节切换给更多时间
            self._wait_for_canvas_text(timeout_ms=15000)
            self._page.wait_for_timeout(2000)

            extracted_title = self._extract_chapter_title_from_canvas()
            self.state.chapter_uid = chapter_uid

            return extracted_title or chapter_title or "未知章节"

        except Exception as e:
            print(f"❌ 跳转章节失败: {e}")
            return None

    def _extract_chapter_title_from_canvas(self) -> str:
        """从 Canvas 文本中提取章节标题（通常是第一行非空文本）"""
        try:
            captured = self._page.evaluate("() => window.__CANVAS_TEXT__ || []")
            if not captured or len(captured) < 2:
                return ""

            # 重建文本
            from .canvas_extractor import reconstruct_text_from_canvas
            text = reconstruct_text_from_canvas(captured)
            if not text:
                return ""

            # 过滤掉导航文字后取第一行
            nav_words = ['上一页', '下一页', '下一章', '上一章', '开始阅读', '阅读', '加入书架']
            lines = [l.strip() for l in text.split('\n\n') if l.strip()]
            for line in lines:
                # 跳过导航词
                if any(w in line for w in nav_words):
                    continue
                # 跳过字体预加载测试行（包含完整 ASCII 字符集）
                if len(line) > 80:
                    has_all_lower = all(c in line for c in "abcdefghijklmnopqrstuvwxyz")
                    has_all_upper = all(c in line for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
                    if has_all_lower and has_all_upper:
                        continue
                # 如果行太长，可能是正文而非标题
                if len(line) < 50:
                    return line
                return ""

            return ""
        except Exception:
            return ""

    def _find_and_click_chapter(self, chapter_uid: int) -> Optional[str]:
        """在目录面板中找到并点击指定 chapterUid 的章节"""
        try:
            # 获取目录中所有章节的 data-chapter-uid 或文本
            items = self._page.evaluate("""(targetUid) => {
                const listItems = document.querySelectorAll('.readerCatalog_list_item');
                const results = [];
                for (const li of listItems) {
                    const titleEl = li.querySelector('.readerCatalog_list_item_title_text');
                    if (titleEl) {
                        results.push({
                            text: titleEl.innerText.trim(),
                            element: li,
                            // WeRead doesn't store chapterUid in DOM directly,
                            // so we need to match by text
                        });
                    }
                }
                return results;
            }""", chapter_uid)

            # We can\'t directly map chapter_uid to DOM text without the chapter list
            # So this method relies on the caller providing the title
            return None

        except Exception:
            return None

    def _navigate_by_pagination(self, target_chapter_uid: int) -> Optional[str]:
        """
        通过翻页导航到目标章节。
        这是一个较慢的 fallback 方法。
        """
        if not self.is_alive:
            return None

        # 逐页翻页，同时检查当前章节
        max_pages = 50
        for _ in range(max_pages):
            next_btn = self._find_text('下一页')
            if not next_btn:
                break

            next_btn.click()
            self._page.wait_for_timeout(800)

            # 检查当前章节
            info = self._extract_content_info()
            if info and info.get("has_content"):
                self.state.chapter_uid = target_chapter_uid
                self.state.chapter_title = info.get("title", "")
                return info.get("title")

        return None

    # ── 内容提取 ─────────────────────────────────────────────

    def get_content(self) -> Optional[str]:
        """
        从当前页面提取章节正文内容（纯文本）。
        """
        if not self.is_alive:
            return None

        try:
            result = self._page.evaluate("""() => {
                const contentEl = document.querySelector('.readerChapterContent');
                if (!contentEl) {
                    return null;
                }

                // 获取所有文本节点，过滤掉导航元素
                const walker = document.createTreeWalker(
                    contentEl,
                    NodeFilter.SHOW_TEXT,
                    null,
                    false
                );

                const texts = [];
                let node;
                while (node = walker.nextNode()) {
                    const text = node.textContent.trim();
                    // 过滤掉导航文字和空行
                    const navWords = ['上一页', '下一页', '阅读', '开始阅读', '加入书架'];
                    const isNav = navWords.some(w => text === w);
                    if (text.length > 0 && !isNav) {
                        texts.push(text);
                    }
                }

                // 也尝试从 innerHTML 提取
                const innerText = contentEl.innerText;
                let cleanText = innerText;
                for (const word of ['上一页', '下一页']) {
                    cleanText = cleanText.replaceAll(word, '');
                }
                cleanText = cleanText.trim();

                return {
                    paragraphs: texts,
                    fullText: cleanText,
                    textLength: cleanText.length,
                    hasSubstantialContent: cleanText.length > 50
                };
            }""")

            if result and result.get("hasSubstantialContent"):
                return result["fullText"]
            elif result and result.get("paragraphs"):
                return "\n\n".join(result["paragraphs"])

            return None

        except Exception as e:
            print(f"❌ 提取内容失败: {e}")
            return None

    def _collect_all_scrolled_content(self) -> Optional[str]:
        """滚动模式下的内容收集：自动滚动到底部，分段收集 Canvas 文本。

        适用于长页面（非分页模式），Canvas 只渲染当前可视区域。
        通过逐屏滚动 + 每屏收集 + 去重拼接，获取完整章节内容。
        """
        try:
            # 检查是否处于滚动模式：没有"下一页"按钮
            has_next_btn = self._page.evaluate("""() => {
                const body = document.body.innerText;
                return body.includes('下一页') || body.includes('下一章');
            }""")
            if has_next_btn:
                return None  # 翻页模式，不用滚动

            # 滚动模式：从顶部开始逐屏收集
            self._page.evaluate("""() => {
                window.__CANVAS_TEXT__ = [];
                document.documentElement.scrollTop = 0;
                document.body.scrollTop = 0;
            }""")
            self._page.wait_for_timeout(1500)

            all_segments = []
            max_scrolls = 50  # 最多 50 屏
            prev_scroll_top = -1

            for i in range(max_scrolls):
                # 收集当前屏的 Canvas 文本
                captured = self._page.evaluate("() => window.__CANVAS_TEXT__ || []")
                if captured:
                    from .canvas_extractor import reconstruct_text_from_canvas
                    text = reconstruct_text_from_canvas(captured)
                    if text and len(text.strip()) > 20:
                        all_segments.append(text.strip())

                # 获取当前滚动位置和最大可滚动距离
                scroll_info = self._page.evaluate("""() => ({
                    scrollTop: Math.max(document.documentElement.scrollTop, document.body.scrollTop),
                    scrollHeight: Math.max(document.documentElement.scrollHeight, document.body.scrollHeight),
                    clientHeight: Math.max(document.documentElement.clientHeight, window.innerHeight)
                })""")
                scroll_top = scroll_info.get("scrollTop", 0)
                scroll_height = scroll_info.get("scrollHeight", 0)
                client_height = scroll_info.get("clientHeight", 0)

                # 如果已经到底部，退出
                if scroll_top + client_height >= scroll_height - 10:
                    break
                # 如果滚动没有变化（卡住了），也退出
                if scroll_top == prev_scroll_top:
                    break
                prev_scroll_top = scroll_top

                # 清掉旧 Canvas 文本，向下滚动一屏
                self._page.evaluate("() => window.__CANVAS_TEXT__ = []")
                self._page.evaluate(f"() => window.scrollBy(0, {client_height - 50})")
                self._page.wait_for_timeout(1200)

                # 等待新内容渲染
                self._wait_for_canvas_text(timeout_ms=5000)

            # 去重拼接：移除相邻 segment 的重叠部分
            if not all_segments:
                return None

            result = all_segments[0]
            for seg in all_segments[1:]:
                # 找重叠：后一段的开头与前一段的结尾
                overlap_found = False
                overlap_len = min(200, len(result), len(seg))
                for ol in range(overlap_len, 20, -1):
                    if result[-ol:] == seg[:ol]:
                        result += seg[ol:]
                        overlap_found = True
                        break
                if not overlap_found:
                    result += "\n\n" + seg

            # 清理
            result = re.sub(r'\n{3,}', '\n\n', result).strip()
            return result if len(result) > 50 else None

        except Exception as e:
            print(f"⚠️ 滚动收集内容失败: {e}")
            return None

    def _collect_scrolled_content(self) -> Optional[str]:
        """滚动模式：逐屏滚动收集 Canvas 文本，返回完整内容。

        适用于没有"上一页/下一页"按钮的滚动模式（如邓小平时代）。
        Canvas 只渲染当前可视区域，需要滚动触发下方内容渲染。
        """
        try:
            # 检测是否为滚动模式（没有翻页按钮）
            has_page_btn = self._page.evaluate("""() => {
                const body = document.body.innerText;
                return body.includes('下一页') || body.includes('上一章') || body.includes('下一章');
            }""")
            if has_page_btn:
                return None  # 翻页模式，不走滚动收集

            # 滚动到顶部并清空 Canvas
            self._page.evaluate("""() => {
                window.scrollTo(0, 0);
                window.__CANVAS_TEXT__ = [];
            }""")
            self._page.wait_for_timeout(2000)

            all_items = []
            prev_scroll_top = -1
            max_scrolls = 60

            for i in range(max_scrolls):
                # 获取当前视口信息
                view_info = self._page.evaluate("""() => ({
                    scrollTop: document.documentElement.scrollTop || document.body.scrollTop,
                    scrollHeight: Math.max(document.documentElement.scrollHeight, document.body.scrollHeight),
                    clientHeight: window.innerHeight
                })""")

                scroll_top = view_info.get("scrollTop", 0)
                scroll_height = view_info.get("scrollHeight", 0)
                client_height = view_info.get("clientHeight", 800)

                # 到达底部就停止
                if scroll_top + client_height >= scroll_height - 20:
                    break
                if scroll_top == prev_scroll_top:
                    break  # 滚不动了
                prev_scroll_top = scroll_top

                # 收集当前屏 Canvas 文本
                captured = self._page.evaluate("() => window.__CANVAS_TEXT__ || []")
                if captured:
                    all_items.extend(captured)

                # 向下滚动一屏（留 50px 重叠用于后续去重）
                scroll_amount = client_height - 50
                self._page.evaluate(f"() => window.scrollBy(0, {scroll_amount})")
                self._page.wait_for_timeout(800)

                # 等待 Canvas 渲染新内容
                self._wait_for_canvas_text(timeout_ms=5000)
                self._page.wait_for_timeout(500)

            # 最后一次收集
            captured = self._page.evaluate("() => window.__CANVAS_TEXT__ || []")
            if captured:
                all_items.extend(captured)

            if not all_items:
                return None

            # 重建完整文本
            from .canvas_extractor import reconstruct_text_from_canvas
            text = reconstruct_text_from_canvas(all_items)

            # 清理
            nav_words = ['上一页', '下一页', '下一章', '上一章', '开始阅读', '阅读', '加入书架']
            for word in nav_words:
                text = text.replace(word, '')
            text = re.sub(r'\n{3,}', '\n\n', text).strip()

            return text if len(text) > 100 else None

        except Exception as e:
            print(f"⚠️ 滚动收集内容异常: {e}")
            return None

    def _reconstruct_page_text(self, captured: list) -> str:
        """重建单页文本，使用 canvas_extractor 模块。"""
        if not captured:
            return ""
        from .canvas_extractor import reconstruct_text_from_canvas
        return reconstruct_text_from_canvas(captured)

    def _reconstruct_dual_column_spread(self, captured: list, debug: bool = False) -> str:
        """重建一个 spread（左右双栏）的文本。

        双栏模式下 WeRead 使用单个 Canvas 按阅读顺序渲染：
        先渲染左栏（从上到下），再渲染右栏（从上到下）。
        左右栏共享相同的 x 坐标范围（各自相对于栏的起始位置）。
        策略：按 timestamp 排序后，检测 y 重置点将内容分为左右两栏。

        注意：WeRead 初始加载时会预渲染多个 spread（页）。
        本函数只提取第一个 spread，忽略预渲染的后续内容。
        """
        if not captured:
            return ""

        # 按 timestamp 排序
        items = [item for item in captured
                 if item.get("text") and item["text"] != ""
                 and isinstance(item.get("x"), (int, float))]
        items.sort(key=lambda i: i.get("timestamp", 0))
        if not items:
            return ""

        # 检测左右栏分界点：在 timestamp 顺序中，左栏的 y 值递增，
        # 到达左栏末尾后 y 值突然变小（右栏从顶部开始）
        ys = [item.get("y", 0) for item in items]

        # 检测所有 y 突变点（每个突变点 = 新栏开始）
        drops = []
        window = 5
        for i in range(10, len(items) - 10):
            y_before = sum(ys[i - window:i]) / window
            y_now = ys[i]
            drop = y_before - y_now
            if drop > 50:  # 显著下降（约等于行高）
                drops.append((i, drop))

        if not drops:
            # 没有突降，直接单栏重建
            return self._reconstruct_page_text(items)

        # 第一个显著突降 = 左栏结束 → 右栏开始
        first_drop_idx = drops[0][0]
        best_drop = drops[0][1]

        # 但如果第一个突降太早（<20%），可能是标题区，用更大的突降
        if first_drop_idx < len(items) * 0.2 and len(drops) > 1:
            # 在前 3 个突降中选最大的
            candidate_drops = drops[:min(3, len(drops))]
            best_drop_idx = max(candidate_drops, key=lambda d: d[1])[0]
            best_split = best_drop_idx
        else:
            best_split = first_drop_idx

        # 检测是否预渲染了多页：在第一个突降之后还有突降
        # 找到第二个突降（第二页开始的位置），截断多余内容
        second_spread_start = None
        for idx, drop in drops[1:]:
            if idx > best_split and idx - best_split > 20:
                second_spread_start = idx
                break
        if second_spread_start:
            items = items[:second_spread_start]
            ys = ys[:second_spread_start]
            print(f"[多页检测] 截断预渲染内容: {len(items)} 字符")

        left_items = items[:best_split]
        right_items = items[best_split:]

        # 调试输出（总是打印）
        if len(left_items) > 0 and len(right_items) > 0:
            left_y_min = min(i.get('y', 0) for i in left_items)
            left_y_max = max(i.get('y', 0) for i in left_items)
            right_y_min = min(i.get('y', 0) for i in right_items)
            right_y_max = max(i.get('y', 0) for i in right_items)
            print(f"[双栏分割] 分界点={best_split}/{len(items)}, y突变={best_drop:.0f}")
            print(f"[双栏分割] 左栏 y: {left_y_min:.0f}~{left_y_max:.0f} ({len(left_items)}字符)")
            print(f"[双栏分割] 右栏 y: {right_y_min:.0f}~{right_y_max:.0f} ({len(right_items)}字符)")

        debug_info = []
        if debug:
            debug_info.append(f"{'='*50}")
            debug_info.append(f"[双栏诊断] 总字符: {len(items)}, 分界点: {best_split}/{len(items)}")
            debug_info.append(f"[双栏诊断] 左栏: {len(left_items)} 字符, 右栏: {len(right_items)} 字符")
            if left_items and right_items:
                debug_info.append(f"[双栏诊断] 左栏 y: {left_y_min:.0f}~{left_y_max:.0f}, 右栏 y: {right_y_min:.0f}~{right_y_max:.0f}")
                debug_info.append(f"[双栏诊断] y 突变值: {best_drop:.0f}")
                # 打印分界点附近的 y 值
                debug_info.append(f"[双栏诊断] 分界点附近 y 值:")
                for j in range(max(0, best_split - 5), min(len(items), best_split + 5)):
                    marker = " ← 分界" if j == best_split else ""
                    debug_info.append(f"  [{j}] y={ys[j]:.0f}, text={items[j]['text'][:3]}{marker}")
            # 打印前 5 和后 5 个字符
            debug_info.append(f"[双栏诊断] 前5个: {[items[j]['text'][:5] for j in range(min(5, len(items)))]}")
            debug_info.append(f"[双栏诊断] 后5个: {[items[j]['text'][:5] for j in range(len(items)-5, len(items))]}")

        # 如果分割严重不均，可能不是双栏
        if len(left_items) < 5 or len(right_items) < 5:
            if debug:
                debug_info.append("[双栏诊断] 分割不均，单栏重建")
            result = self._reconstruct_page_text(items)
        else:
            left_text = self._reconstruct_page_text(left_items)
            right_text = self._reconstruct_page_text(right_items)

            parts = []
            if left_text and len(left_text.strip()) > 5:
                parts.append(left_text.strip())
            if right_text and len(right_text.strip()) > 5:
                parts.append(right_text.strip())

            # 智能连接左右栏：检查左栏结尾是否有结束标点
            if len(parts) == 2:
                left_stripped = parts[0]
                # 查找左栏最后一个非空白字符
                last_char = ''
                for ch in reversed(left_stripped):
                    if ch not in ' \t\n\r':
                        last_char = ch
                        break

                # 结束标点：句号、问号、叹号、分号、冒号、右引号/括号等
                ending_punct = set('。！？；，、：""\'\'」』）》）】}〉』')
                # 空格也算结束信号（英文换行）
                if last_char in ending_punct or last_char.isspace() or not last_char:
                    # 有结束标记 → 换行
                    result = '\n\n'.join(parts)
                else:
                    # 无结束标记 → 直接拼接，保持文字连贯
                    result = parts[0] + parts[1]
            else:
                result = '\n\n'.join(parts)

        if debug:
            debug_info.append(f"[双栏诊断] 最终文本长度: {len(result)} 字符")
            preview = result[:200].replace('\n', '\\n') + ('...' if len(result) > 200 else '')
            debug_info.append(f"[双栏诊断] 内容预览: {preview}")
            debug_info.append(f"{'='*50}")
            return '\n'.join(debug_info)

        return result

    def get_content_with_title(self, wait_for_render: bool = False) -> Optional[Dict]:
        """提取内容 + 章节标题（通过 Canvas fillText 拦截）

        Args:
            wait_for_render: 首次调用时设为 True，等待 Canvas 渲染完成后再提取。
                这解决了首次打开程序时 Canvas 尚未渲染导致的超时问题。
        """
        if not self.is_alive:
            return None

        try:
            # 首次打开时等待 Canvas 渲染完成
            if wait_for_render:
                if not self._wait_for_canvas_text(timeout_ms=15000):
                    print("[内容提取] Canvas 文本为空，等待超时")
                    return None
                self._page.wait_for_timeout(1000)

            captured = self._page.evaluate("() => window.__CANVAS_TEXT__ || []")

            if not captured:
                return None

            # 检测是否为双栏翻页模式
            is_dual_column = self._find_text('下一页') is not None

            if is_dual_column:
                # 双栏：分离左右栏后重建，避免左右文字交错
                text = self._reconstruct_dual_column_spread(captured)
                # 调试输出
                if text and len(text) > 100:
                    preview = text[:80].replace('\n', ' ')
                    print(f"[内容提取] 双栏模式, 重建长度={len(text)}, 预览: {preview}")
            else:
                # 滚动/单栏：直接重建
                from .canvas_extractor import reconstruct_text_from_canvas
                text = reconstruct_text_from_canvas(captured)
                if text and len(text) > 100:
                    preview = text[:80].replace('\n', ' ')
                    print(f"[内容提取] 单栏模式, 重建长度={len(text)}, 预览: {preview}")

            if not text or len(text) < 50:
                return None

            # 过滤掉 Canvas 中的导航按钮文字
            nav_words = ['上一页', '下一页', '下一章', '上一章', '开始阅读', '阅读', '加入书架']
            for word in nav_words:
                text = text.replace(word, '')

            # 清理多余空行
            text = re.sub(r'\n{3,}', '\n\n', text).strip()

            if len(text) < 50:
                return None

            # Try to extract chapter title from the first line
            lines = text.split('\n\n')
            title = lines[0] if lines else ""
            content = '\n\n'.join(lines[1:]) if len(lines) > 1 else text

            # If title is too long, it's probably part of the content
            if len(title) > 50:
                content = text
                title = ""

            return {
                "title": title,
                "content": content,
                "textLength": len(text)
            }

        except Exception as e:
            print(f"❌ 提取内容失败: {e}")
            return None

    def _inject_canvas_hook(self):
        """注入 Canvas 文本拦截脚本"""
        self._page.evaluate("""
            window.__CANVAS_TEXT__ = window.__CANVAS_TEXT__ || [];
            if (!window.__CANVAS_TEXT_HOOKED__) {
                window.__CANVAS_TEXT_HOOKED__ = true;
                const origFillText = CanvasRenderingContext2D.prototype.fillText;
                CanvasRenderingContext2D.prototype.fillText = function(text, x, y, maxWidth) {
                    if (text && typeof text === 'string' && text.length > 0) {
                        try {
                            var rect = this.canvas.getBoundingClientRect();
                            window.__CANVAS_TEXT__.push({
                                text: text,
                                x: x,
                                y: y + rect.top,
                                canvasOffsetX: Math.round(rect.left),
                                canvasOffsetY: Math.round(rect.top),
                                canvasWidth: Math.round(rect.width),
                                timestamp: Date.now()
                            });
                        } catch(e) {
                            window.__CANVAS_TEXT__.push({
                                text: text,
                                x: x,
                                y: y,
                                canvasOffsetX: 0,
                                canvasOffsetY: 0,
                                canvasWidth: 0,
                                timestamp: Date.now()
                            });
                        }
                    }
                    return origFillText.call(this, text, x, y, maxWidth);
                };
            }
        """)

    def _extract_content_info(self) -> Optional[Dict]:
        """提取当前页面内容信息"""
        try:
            result = self._page.evaluate("""() => {
                const contentEl = document.querySelector('.readerChapterContent');
                if (!contentEl) return null;

                const text = contentEl.innerText;
                let cleanText = text;
                for (const word of ['上一页', '下一页', '阅读', '开始阅读']) {
                    cleanText = cleanText.replaceAll(word, '');
                }
                cleanText = cleanText.trim();

                // 尝试提取标题
                let title = '';
                const lines = cleanText.split('\n');
                if (lines.length > 0) {
                    title = lines[0].trim();
                }

                return {
                    title: title,
                    has_content: cleanText.length > 30,
                    textLength: cleanText.length
                };
            }""")
            return result
        except Exception:
            return None

    def _find_text(self, text: str):
        """Find an element by text content using immediate DOM check.
        
        使用 JS 立即检查，不等待。避免 Playwright locator.count() 的隐式等待。
        """
        try:
            found = self._page.evaluate(f"""() => {{
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while (node = walker.nextNode()) {{
                    if (node.textContent.includes({json.dumps(text)})) {{
                        const el = node.parentElement;
                        if (el) {{
                            const rect = el.getBoundingClientRect();
                            // 只返回可见元素
                            if (rect.width > 0 && rect.height > 0) {{
                                return true;
                            }}
                        }}
                    }}
                }}
                return false;
            }}""")
            if found:
                return self._page.get_by_text(text).first
            return None
        except Exception:
            # 页面可能正在过渡中，JS 评估失败
            return None

    # ── 翻页操作 ─────────────────────────────────────────────

    def next_page(self) -> bool:
        """翻到下一页。如果当前是章节最后一页，尝试进入下一章。"""
        if not self.is_alive:
            return False
        try:
            # 优先找"下一页"，找不到则找"下一章"
            btn = self._find_text('下一页')
            if not btn:
                btn = self._find_text('下一章')
            
            if not btn:
                # 没有翻页按钮，可能是章节末尾的特殊页面
                return False
            
            # 先清除旧 canvas 文本
            self._page.evaluate("() => window.__CANVAS_TEXT__ = []")
            # force=True 避免元素被遮挡或过渡状态导致失败
            btn.click(force=True, timeout=5000)
            # 等待新内容渲染（Canvas fillText 被调用）
            self._wait_for_canvas_text()
            return True
        except Exception as e:
            return False

    def prev_page(self) -> bool:
        """翻到上一页"""
        if not self.is_alive:
            return False
        try:
            btn = self._find_text('上一页')
            if not btn:
                btn = self._find_text('上一章')
            
            if btn:
                self._page.evaluate("() => window.__CANVAS_TEXT__ = []")
                btn.click(force=True, timeout=5000)
                self._wait_for_canvas_text()
                return True
            return False
        except Exception:
            return False

    def _wait_for_canvas_text(self, timeout_ms: int = 5000):
        """等待 Canvas 有新文本渲染。
        
        Canvas 渲染是异步的，需要等待 fillText 调用完成。
        采用渐进式等待策略：首次等待时间长，后续轮询间隔递增。
        """
        try:
            start = time.time()
            # 首次渲染可能需要更长时间，先给一个初始等待
            self._page.wait_for_timeout(500)
            while (time.time() - start) * 1000 < timeout_ms:
                count = self._page.evaluate("() => (window.__CANVAS_TEXT__ || []).length")
                if count > 10:  # 至少有一些文本了
                    return True
                self._page.wait_for_timeout(300)
            return False
        except Exception:
            # 页面可能正在导航或已变化
            return False

    def next_chapter(self) -> bool:
        """翻到下一章。
        
        策略：
        1. 先检查是否有"下一章"按钮（章节结尾时会出现）
        2. 如果没有，通过目录面板跳转到下一章
        """
        if not self.is_alive:
            return False
        try:
            # 策略 1: 直接找"下一章"按钮
            btn = self._find_text('下一章')
            if btn:
                self._page.evaluate("() => window.__CANVAS_TEXT__ = []")
                btn.click(force=True, timeout=5000)
                self._wait_for_canvas_text()
                return True

            # 策略 2: 先翻到最后一页，再找"下一章"
            max_flips = 20
            for _ in range(max_flips):
                btn = self._find_text('下一页')
                if not btn:
                    break
                self._page.evaluate("() => window.__CANVAS_TEXT__ = []")
                btn.click(force=True, timeout=3000)
                self._page.wait_for_timeout(400)
            
            # 翻完后检查是否出现了"下一章"
            btn = self._find_text('下一章')
            if btn:
                self._page.evaluate("() => window.__CANVAS_TEXT__ = []")
                btn.click(force=True, timeout=5000)
                self._wait_for_canvas_text()
                return True

            # 策略 3: 通过目录面板跳转
            return self._skip_to_next_chapter_via_catalog()

        except Exception:
            return False

    def prev_chapter(self) -> bool:
        """翻到上一章"""
        if not self.is_alive:
            return False
        try:
            # 优先找"上一章"按钮
            btn = self._find_text('上一章')
            if btn:
                self._page.evaluate("() => window.__CANVAS_TEXT__ = []")
                btn.click(force=True, timeout=5000)
                self._wait_for_canvas_text()
                return True

            # Fallback: 通过目录面板跳转
            return self._skip_to_prev_chapter_via_catalog()

        except Exception:
            return False

    def _skip_to_next_chapter_via_catalog(self) -> bool:
        """通过目录面板跳转到下一章"""
        return self._skip_chapter_via_catalog(direction="next")

    def _skip_to_prev_chapter_via_catalog(self) -> bool:
        """通过目录面板跳转到上一章"""
        return self._skip_chapter_via_catalog(direction="prev")

    def _skip_chapter_via_catalog(self, direction: str = "next") -> bool:
        """通过目录面板跳转章节。
        
        打开目录面板，找到当前章节的前一个或后一个章节并点击。
        """
        try:
            # 打开目录
            catalog_btn = self._page.query_selector('.readerControls_item.catalog')
            if not catalog_btn:
                return False
            
            catalog_btn.click()
            self._page.wait_for_timeout(1000)

            # 通过 JS 在目录中找到当前章节的相邻章节并点击
            result = self._page.evaluate("""(dir) => {
                const items = document.querySelectorAll('.readerCatalog_list_item');
                if (!items || items.length === 0) return false;
                
                // 找到当前高亮的章节
                let currentIndex = -1;
                for (let i = 0; i < items.length; i++) {
                    if (items[i].classList.contains('active') || 
                        items[i].querySelector('.active') ||
                        items[i].getAttribute('aria-current') === 'true') {
                        currentIndex = i;
                        break;
                    }
                }
                
                // 如果没找到高亮的，默认选第一个或最后一个
                let targetIndex;
                if (dir === 'next') {
                    targetIndex = currentIndex >= 0 ? currentIndex + 1 : 1;
                } else {
                    targetIndex = currentIndex > 0 ? currentIndex - 1 : 0;
                }
                
                if (targetIndex < 0 || targetIndex >= items.length) return false;
                
                const target = items[targetIndex];
                const titleEl = target.querySelector('.readerCatalog_list_item_title_text, .readerCatalog_list_item_title');
                if (titleEl) {
                    titleEl.click();
                    return true;
                }
                return false;
            }""", direction)

            # 清除旧文本，等待新章节渲染
            self._page.evaluate("() => window.__CANVAS_TEXT__ = []")
            self._page.wait_for_timeout(1000)

            # 关闭目录
            catalog_btn = self._page.query_selector('.readerControls_item.catalog')
            if catalog_btn:
                try:
                    catalog_btn.click()
                except Exception:
                    pass

            self._wait_for_canvas_text()
            return result

        except Exception:
            return False

    # ── 辅助方法 ─────────────────────────────────────────────

    def _minimize_window(self) -> None:
        """最小化浏览器窗口（macOS）"""
        try:
            # 等待窗口出现
            time.sleep(0.5)
            subprocess.run([
                'osascript', '-e',
                'tell application "System Events"\n'
                '  set procs to every process whose name contains "Chromium"\n'
                '  repeat with proc in procs\n'
                '    set windows to every window of proc\n'
                '    if (count of windows) > 0 then\n'
                '      set miniaturized of first window of proc to true\n'
                '      exit repeat\n'
                '    end if\n'
                '  end repeat\n'
                'end tell'
            ], capture_output=True, timeout=5)
        except Exception:
            pass  # 最小化失败不影响后续流程

    def is_page_stale(self) -> bool:
        """检测页面是否过期（长时间驻留后需要刷新）。

        判断依据：
        1. Canvas 文本为空或极少（少于 5 条记录）
        2. DOM 中 .readerChapterContent 为空
        3. 页面出现刷新提示或登录弹窗
        """
        if not self.is_alive:
            return True

        try:
            check = self._page.evaluate("""() => {
                const canvasText = window.__CANVAS_TEXT__ || [];
                const contentEl = document.querySelector('.readerChapterContent');
                const bodyText = document.body ? document.body.innerText : '';

                // 检测是否出现刷新提示
                const hasRefreshPrompt = bodyText.includes('刷新') || bodyText.includes('页面已过期');

                // 检测是否弹出登录框
                const hasLoginOverlay = !!document.querySelector('.loginWrap, .loginContainer');

                // 检测页面是否为空/白屏
                const isEmptyPage = !contentEl && !document.querySelector('.readerTopBar');

                // 检测 Canvas 文本是否足够
                const hasCanvasText = canvasText.length > 5;

                // 检测 DOM 内容是否足够
                const hasDomContent = contentEl && contentEl.innerText.trim().length > 30;

                return {
                    stale: hasRefreshPrompt || hasLoginOverlay || isEmptyPage || (!hasCanvasText && !hasDomContent),
                    reason: hasRefreshPrompt ? 'refresh_prompt' :
                            hasLoginOverlay ? 'login_overlay' :
                            isEmptyPage ? 'empty_page' :
                            (!hasCanvasText && !hasDomContent) ? 'no_content' : null
                };
            }""")

            return bool(check and check.get("stale", False))

        except Exception:
            return True

    def toggle_reading_mode(self) -> str:
        """切换阅读模式（双栏翻页 / 滚动）。

        策略：修改 localStorage.readerConfig.mode + 强制刷新。
        刷新后通过 SPA 路由恢复页面。
        """
        if not self.is_alive:
            return "unknown"

        try:
            # 读取当前配置
            pre_config = self._page.evaluate("""() => {
                try { return JSON.parse(localStorage.getItem('readerConfig')); }
                catch(e) { return null; }
            }""")
            print(f"[切换前] readerConfig: {pre_config}")

            # 切换 mode: 1 = 双栏翻页, 2 = 滚动
            current = self._page.evaluate("""() => {
                try {
                    const raw = localStorage.getItem('readerConfig');
                    if (raw) return JSON.parse(raw);
                } catch(e) {}
                return { mode: 1 };
            }""")
            if current is None:
                current = {'mode': 1}
            new_mode = 2 if current.get('mode') == 1 else 1
            current['mode'] = new_mode

            # 写入配置
            self._page.evaluate("""(cfg) => {
                localStorage.setItem('readerConfig', JSON.stringify(cfg));
            }""", current)
            print(f"[localStorage] 设置 mode={new_mode}")

            # 保存当前 URL 以便恢复
            current_url = self._page.url

            # 使用 page.reload() 而非 evaluate("window.location.reload()")
            # page.reload() 由 Playwright 控制，更可靠
            self._page.reload(timeout=15000)
            self._page.wait_for_timeout(3000)

            # 重新注入 Canvas 钩子（reload 后丢失）
            self._inject_canvas_hook()

            # 如果 reload 后不在阅读器页面，重新导航
            if 'reader' not in self._page.url:
                if self.state.book_id and self.state.chapter_uid:
                    print(f"[恢复导航] 返回章节 {self.state.chapter_uid}")
                    self.go_to_chapter(
                        self.state.chapter_uid,
                        chapter_title=self.state.chapter_title,
                        chapter_index=self.state.chapter_index,
                        chapter_idx=self.state.chapter_index
                    )
                    self._page.wait_for_timeout(2000)

            self._wait_for_canvas_text(timeout_ms=15000)

            return self.get_current_reading_mode()

        except Exception as e:
            print(f"⚠️ 切换阅读模式失败: {e}")
            return "unknown"

    def get_current_reading_mode(self) -> str:
        """获取当前阅读模式，不切换。

        Returns:
            "dual_column" / "scroll" / "unknown"
        """
        if not self.is_alive:
            return "unknown"
        try:
            has_next = self._find_text('下一页') is not None
            if has_next:
                return "dual_column"
            config = self._page.evaluate("""() => {
                try {
                    const raw = localStorage.getItem('readerConfig');
                    return raw ? JSON.parse(raw) : null;
                } catch(e) { return null; }
            }""")
            if config and config.get('mode') == 2:
                return "scroll"
            return "dual_column"
        except Exception:
            return "unknown"

    def refresh_current_page(self) -> bool:
        """刷新当前页面并重新注入 Canvas 钩子。

        使用当前书籍的 URL 重新导航，恢复 Canvas 文本拦截。
        在页面长时间驻留导致内容加载失败时调用。
        """
        if not self.is_alive:
            return False

        if not self.state.book_id or not self.state.reader_url:
            print("⚠️  没有当前书籍信息，无法刷新")
            return False

        try:
            url = self.state.reader_url

            # 清除旧 Canvas 文本
            try:
                self._page.evaluate("() => window.__CANVAS_TEXT__ = []")
            except Exception:
                pass

            print(f"🔄 正在刷新页面...")

            # 重新导航到当前书籍页面（会触发完整重载 + 重新注入 init_script）
            self._page.goto(url, wait_until="domcontentloaded", timeout=20000)
            self._page.wait_for_timeout(3000)

            # 重新注入 Canvas 钩子（页面重载后 hook 丢失）
            self._inject_canvas_hook()

            # 等待内容渲染
            canvas_ready = self._wait_for_canvas_text(timeout_ms=8000)

            if canvas_ready:
                print("✅ 页面刷新成功，内容已加载")
                return True
            else:
                # Canvas 可能没有内容（如封面页），检查 DOM
                dom_check = self._page.evaluate("""() => {
                    const el = document.querySelector('.readerChapterContent');
                    return el && el.innerText.trim().length > 30;
                }""")
                if dom_check:
                    print("✅ 页面刷新成功（DOM 内容可用）")
                    return True
                print("⚠️  页面刷新后内容仍为空")
                return False

        except Exception as e:
            print(f"❌ 页面刷新失败: {e}")
            return False

    def screenshot(self, path: str = "/tmp/weread_page.png"):
        """截图当前页面（调试用）"""
        if self.is_alive:
            self._page.screenshot(path=path)
            print(f"📸 截图已保存: {path}")

    def get_cookies(self) -> Dict[str, str]:
        """获取当前 Cookie（可能已更新）"""
        if not self.is_alive:
            return {}
        try:
            cookies = self._context.cookies()
            return {c["name"]: c["value"] for c in cookies if c["domain"] and "weread" in c["domain"]}
        except Exception:
            return {}

    def save_session_cookies(self, cookie_file: Optional[Path] = None) -> bool:
        """将浏览器当前 Cookie 保存到文件。
        
        在章节切换、程序退出等时机调用，确保 Cookie 始终最新。
        """
        cookies = self.get_cookies()
        if not cookies:
            return False
        
        import os
        target = cookie_file or Path.home() / ".weread_cookies.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = str(target) + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(cookies, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(target))
        return True
