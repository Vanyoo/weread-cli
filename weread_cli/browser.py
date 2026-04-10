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
        cookies: Dict[str, str],
        mode: str = "headed",  # "headed" | "headless" | "cdp"
        cdp_url: str = "http://localhost:9222",
    ):
        self.cookies = cookies
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
            
            # 注入 Canvas 文本拦截脚本
            # 微信读书使用 Canvas 渲染正文，通过拦截 fillText 可以提取文本
            self._page.add_init_script("""
                window.__CANVAS_TEXT__ = [];
                const origFillText = CanvasRenderingContext2D.prototype.fillText;
                CanvasRenderingContext2D.prototype.fillText = function(text, x, y, maxWidth) {
                    if (text && typeof text === 'string' && text.length > 0) {
                        window.__CANVAS_TEXT__.push({
                            text: text,
                            x: x,
                            y: y,
                            maxWidth: maxWidth,
                            timestamp: Date.now()
                        });
                    }
                    return origFillText.call(this, text, x, y, maxWidth);
                };
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
                            window.__CANVAS_TEXT__.push({
                                text: text,
                                x: x,
                                y: y,
                                maxWidth: maxWidth,
                                timestamp: Date.now()
                            });
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
            self._wait_for_canvas_text(timeout_ms=10000)
            self._page.wait_for_timeout(1500)

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

    def get_content_with_title(self) -> Optional[Dict]:
        """
        提取内容 + 章节标题（通过 Canvas fillText 拦截）
        """
        if not self.is_alive:
            return None

        try:
            # Extract text from Canvas fillText calls
            captured = self._page.evaluate("() => window.__CANVAS_TEXT__ || []")
            
            if not captured:
                return None
            
            # Reconstruct text from captured characters
            from .canvas_extractor import reconstruct_text_from_canvas
            text = reconstruct_text_from_canvas(captured)
            
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
                        window.__CANVAS_TEXT__.push({
                            text: text,
                            x: x,
                            y: y,
                            maxWidth: maxWidth,
                            timestamp: Date.now()
                        });
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
            # 页面可能在点击后发生了变化，不抛异常
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
        """
        try:
            start = time.time()
            while (time.time() - start) * 1000 < timeout_ms:
                count = self._page.evaluate("() => (window.__CANVAS_TEXT__ || []).length")
                if count > 10:  # 至少有一些文本了
                    return True
                self._page.wait_for_timeout(200)
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
