# -*- coding: utf-8 -*-
"""WeRead CLI —— TUI 微信读书

架构:
  weread-cli (TUI)  ←→  CDP  ←→  微信读书 Web（持久运行）
  提取DOM文本展示到终端          所有操作同步到服务器
"""

import sys
import json
import signal
from typing import Optional, List
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt
from rich.table import Table
from rich.markdown import Markdown
from rich.text import Text

from .api import WeReadAPI
from .debug import is_debug_enabled
from .login import QRLogin
from .models import Book

console = Console()


# ── 阅读状态持久化 ────────────────────────────────────────

_STATE_FILE = Path.home() / ".weread_cli_state.json"


def _save_reading_state(book_id: str, title: str, author: str, chapter_index: int, chapters: list):
    """保存阅读进度到本地"""
    # 章节列表太大不持久化，只保存 book_id 和章节索引
    state = {
        "book_id": book_id,
        "title": title,
        "author": author,
        "chapter_index": chapter_index,
    }
    try:
        _STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    except Exception:
        pass


def _load_reading_state() -> Optional[dict]:
    """加载上次阅读进度"""
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text())
        except Exception:
            return None
    return None


class WeReadCLI:
    """微信读书 CLI"""

    def __init__(self):
        self.api = WeReadAPI()
        self.login_manager = QRLogin()
        self.current_book: Book = None
        self.current_chapters: list = []
        self.current_chapter_index: int = 0
        self._reading_mode = False

    # ── 信号处理 ────────────────────────────────────────────

    def _setup_signals(self):
        """注册信号处理，确保退出时关闭浏览器并保存状态"""
        def cleanup(signum, frame):
            console.print("\n[yellow]正在关闭浏览器...[/yellow]")
            # 保存当前阅读进度
            if self.current_book and self.current_chapters:
                _save_reading_state(
                    self.current_book.book_id,
                    self.current_book.title,
                    self.current_book.author,
                    self.current_chapter_index,
                    self.current_chapters,
                )
            self.api.close()
            console.print("[green]👋 再见！[/green]")
            sys.exit(0)

        signal.signal(signal.SIGINT, cleanup)
        signal.signal(signal.SIGTERM, cleanup)

    # ── 登录 ────────────────────────────────────────────────

    def check_login(self) -> bool:
        """检查登录状态，如 Cookie 过期则尝试自动刷新"""
        if not self.login_manager.check_login():
            console.print("[yellow]⚠️  未找到登录信息[/yellow]\n")
            return False

        user = self.api.check_login()
        if user:
            vip_badge = "👑 VIP" if user.is_vip else ""
            console.print(f"[green]✅ 已登录：{user.name} {vip_badge}[/green]\n")
            return True

        # Cookie 验证失败，尝试刷新 wr_skey
        console.print("[yellow]⚠️  Cookie 已过期，尝试自动刷新...[/yellow]")
        if self.login_manager.refresh_skey():
            # 刷新后重新验证
            self.api._load_cookies()
            user = self.api.check_login()
            if user:
                vip_badge = "👑 VIP" if user.is_vip else ""
                console.print(f"[green]✅ Cookie 刷新成功！已登录：{user.name} {vip_badge}[/green]\n")
                return True

        console.print("[yellow]💡 Cookie 刷新失败或仍无效，需要重新登录[/yellow]\n")
        return False

    def login(self):
        """登录"""
        console.print("\n[yellow]请选择登录方式：[/yellow]")
        console.print("  1. 终端二维码登录（推荐）")
        console.print("  2. 从 Chrome 导入 Cookie")
        console.print("  3. 从 Firefox 导入 Cookie")
        console.print("  4. 从 Safari 导入 Cookie")

        choice = Prompt.ask("请选择", choices=["1", "2", "3", "4"], default="1")

        if choice == "1":
            success = self.login_manager.login_via_qr()
        elif choice == "2":
            success = self.api.import_from_browser("chrome")
        elif choice == "3":
            success = self.api.import_from_browser("firefox")
        elif choice == "4":
            success = self.api.import_from_browser("safari")
        else:
            return

        if success:
            # 登录成功后，重新加载 Cookie 到 API 客户端
            self.api._load_cookies()
            
            user = self.api.check_login()
            if user:
                vip_badge = "👑 VIP" if user.is_vip else ""
                console.print(f"[green]✅ 登录成功！欢迎：{user.name} {vip_badge}[/green]\n")
            else:
                console.print("[green]✅ Cookie 已保存[/green]\n")
        else:
            console.print("[red]❌ 登录失败[/red]\n")

    def logout(self):
        """退出登录"""
        self.login_manager.logout()

    def refresh_cookies(self):
        """手动刷新 Cookie"""
        console.print("\n[yellow]正在刷新 Cookie...[/yellow]")
        if self.api.refresh_cookies():
            console.print("[green]✅ Cookie 刷新成功！[/green]\n")
        else:
            console.print("[red]❌ Cookie 刷新失败[/red]\n")

    # ── 书架 ────────────────────────────────────────────────

    def show_bookshelf(self, interactive: bool = False) -> Optional[List[Book]]:
        """显示书架

        Args:
            interactive: 如果为 True，显示后允许输入序号直接阅读
        """
        books = self.api.get_bookshelf()
        if not books:
            console.print("[yellow]书架为空[/yellow]\n")
            return None

        if interactive:
            page_size = 20
            page = 0
            filtered = list(enumerate(books, 1))

            while True:
                total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
                page = max(0, min(page, total_pages - 1))
                start = page * page_size
                page_items = filtered[start:start + page_size]

                table = Table(
                    title=f"📚 我的书架（第 {page + 1}/{total_pages} 页，共 {len(filtered)} 本）",
                    show_lines=True
                )
                table.add_column("序", style="cyan", justify="center", width=8)
                table.add_column("书名", style="green", width=50)
                table.add_column("作者", style="yellow", width=20)
                table.add_column("进度", justify="center", width=10)

                for idx, book in page_items:
                    progress_str = f"{book.progress}%" if book.progress > 0 else "未读"
                    table.add_row(
                        str(idx),
                        book.title[:48] + "..." if len(book.title) > 50 else book.title,
                        book.author[:18] + "..." if len(book.author) > 20 else book.author,
                        progress_str
                    )

                console.print(table)
                console.print("[dim]输入序号阅读，n 下一页，p 上一页，/关键词过滤，a 全部，0 返回主菜单[/dim]")
                choice = Prompt.ask("请选择", default="0").strip()

                if choice == "0":
                    return books
                if choice.lower() == "n":
                    if page < total_pages - 1:
                        page += 1
                    else:
                        console.print("[yellow]⚠️ 已是最后一页[/yellow]\n")
                    continue
                if choice.lower() == "p":
                    if page > 0:
                        page -= 1
                    else:
                        console.print("[yellow]⚠️ 已是第一页[/yellow]\n")
                    continue
                if choice.lower() == "a":
                    filtered = list(enumerate(books, 1))
                    page = 0
                    continue
                if choice.startswith("/"):
                    query = choice[1:].strip().lower()
                    if query:
                        filtered = [
                            item for item in enumerate(books, 1)
                            if query in item[1].title.lower() or query in item[1].author.lower()
                        ]
                        page = 0
                        if not filtered:
                            console.print(f"[yellow]未找到包含 '{query}' 的书籍[/yellow]\n")
                            filtered = list(enumerate(books, 1))
                    continue
                if choice.isdigit():
                    num = int(choice)
                    if 1 <= num <= len(books):
                        self._load_book_for_reading(books[num - 1])
                        return books
                    console.print("[yellow]⚠️ 无效序号[/yellow]\n")
                    continue

                console.print("[yellow]⚠️ 无效输入[/yellow]\n")

        table = Table(title="📚 我的书架", show_lines=True)
        table.add_column("序", style="cyan", justify="center", width=8)
        table.add_column("书名", style="green", width=50)
        table.add_column("作者", style="yellow", width=20)
        table.add_column("进度", justify="center", width=10)

        for idx, book in enumerate(books, 1):
            progress_str = f"{book.progress}%" if book.progress > 0 else "未读"
            table.add_row(
                str(idx),
                book.title[:48] + "..." if len(book.title) > 50 else book.title,
                book.author[:18] + "..." if len(book.author) > 20 else book.author,
                progress_str
            )

        console.print(table)
        console.print()

        return books

    def search_books(self):
        """搜索书籍，搜索后直接选择"""
        query = Prompt.ask("\n🔍 搜索关键词")
        books = self.api.search_books(query)

        if not books:
            console.print(f"[yellow]未找到包含 '{query}' 的书籍[/yellow]\n")
            return

        console.print(f"\n[green]找到 {len(books)} 本书：[/green]\n")
        for idx, book in enumerate(books, 1):
            progress_str = f"{book.progress}%" if book.progress > 0 else "未读"
            console.print(f"  {idx}. {book.title} - {book.author} [{progress_str}]")
        console.print()

        # 搜索后直接选择
        console.print("[dim]输入序号直接阅读，0 返回主菜单，r 重新搜索[/dim]")
        choice = Prompt.ask("请选择", default="0")

        if choice == "0":
            return
        elif choice.lower() == "r":
            self.search_books()
            return
        elif choice.isdigit():
            num = int(choice)
            if 1 <= num <= len(books):
                self._load_book_for_reading(books[num - 1])
            else:
                console.print("[yellow]⚠️  无效序号[/yellow]\n")

    def _load_book_for_reading(self, book: Book, start_chapter: int = -1):
        """加载一本书准备阅读

        Args:
            book: 书籍对象
            start_chapter: 起始章节索引，-1 表示自动从远端进度恢复
        """
        self.current_book = book
        console.print(f"\n[green]✅ 已选择：{book.title}[/green]")

        # 获取章节列表
        self.current_chapters = self.api.get_chapters(book.book_id)
        if not self.current_chapters:
            console.print("[yellow]⚠️  无法获取章节列表[/yellow]\n")
            return

        # 确定起始章节索引
        if start_chapter >= 0:
            # 显式指定（从本地状态恢复）
            self.current_chapter_index = min(start_chapter, len(self.current_chapters) - 1)
            # 但远端进度可能更靠后，取两者最大值防止本地缓存过时
            if book.last_chapter_uid > 0:
                uid_str = str(book.last_chapter_uid)
                for i, ch in enumerate(self.current_chapters):
                    if ch.chapter_id == uid_str:
                        remote_index = i
                        if remote_index > self.current_chapter_index:
                            self.current_chapter_index = remote_index
                            console.print(f"[yellow]📌 远端进度更新：第 {self.current_chapter_index + 1} 章（本地缓存已过时）[/yellow]\n")
                        break
        elif book.last_chapter_uid > 0:
            # 从远端阅读进度自动定位
            uid_str = str(book.last_chapter_uid)
            for i, ch in enumerate(self.current_chapters):
                if ch.chapter_id == uid_str:
                    self.current_chapter_index = i
                    break
            else:
                self.current_chapter_index = 0

        console.print(f"共 {len(self.current_chapters)} 章")
        if self.current_chapter_index > 0:
            console.print(f"[yellow]📌 从第 {self.current_chapter_index + 1} 章开始[/yellow]\n")
        else:
            console.print()

        # 直接进入阅读模式
        self._enter_reading_mode()

    # ── 选择书籍 ────────────────────────────────────────────

    def select_book(self) -> bool:
        """选择书籍阅读"""
        books = self.api.get_bookshelf()
        if not books:
            console.print("[yellow]书架为空[/yellow]\n")
            return False

        self.show_bookshelf()

        choice = IntPrompt.ask("请输入书籍序号 (0 返回)", default=0)
        if choice == 0 or choice < 1 or choice > len(books):
            return False

        self._load_book_for_reading(books[choice - 1])
        return True

    def show_chapters(self, highlight_index: int = -1):
        """显示章节列表

        Args:
            highlight_index: 当前章节索引（0-based），-1 表示不高亮
        """
        if not self.current_book:
            console.print("[yellow]请先选择书籍[/yellow]\n")
            return

        if not self.current_chapters:
            console.print("[yellow]暂无章节[/yellow]\n")
            return

        total = len(self.current_chapters)

        # 如果章节很多，只显示当前章节附近的
        if total > 30 and highlight_index >= 0:
            # 显示当前章节前后的章节
            start = max(0, highlight_index - 10)
            end = min(total, highlight_index + 11)
            display_range = list(range(start, end))

            console.print(f"[dim]共 {total} 章，显示第 {start+1}-{end} 章（当前章节附近）[/dim]\n")
        else:
            display_range = list(range(total))

        table = Table(
            title=f"📖 {self.current_book.title} - 目录",
            show_lines=True
        )
        table.add_column("序号", style="cyan", justify="center", width=6)
        table.add_column("章节标题", style="green")
        table.add_column("状态", justify="center", width=6)

        for idx in display_range:
            chapter = self.current_chapters[idx]
            num = idx + 1

            if idx == highlight_index:
                # 当前章节：高亮显示
                title_style = "[bold yellow]"
                vip_mark = "👑" if chapter.is_vip else ""
                status = f"{vip_mark} [bold yellow]◀ 当前[/bold yellow]"
                table.add_row(
                    f"[bold yellow]{num}[/bold yellow]",
                    f"{title_style}{chapter.title[:50]}[/]",
                    status
                )
            else:
                vip_mark = "👑" if chapter.is_vip else ""
                table.add_row(
                    str(num),
                    chapter.title[:50] + "..." if len(chapter.title) > 50 else chapter.title,
                    vip_mark
                )

        console.print(table)
        console.print()

    # ── 阅读模式 ────────────────────────────────────────────

    def read_book(self):
        """阅读书籍（TUI 模式）"""
        if not self.current_book:
            if not self.select_book():
                return

        self._enter_reading_mode()

    def _enter_reading_mode(self):
        """进入阅读模式（启动浏览器、打开书、跳转到当前章节、进入阅读循环）"""
        if not self.current_book:
            console.print("[yellow]⚠️  未选择书籍[/yellow]\n")
            return

        if not self.current_chapters:
            console.print("[yellow]暂无章节[/yellow]\n")
            return

        # 保存阅读状态
        _save_reading_state(
            self.current_book.book_id,
            self.current_book.title,
            self.current_book.author,
            self.current_chapter_index,
            self.current_chapters,
        )

        # 启动浏览器（如果需要）
        console.print("[yellow]🌐 正在连接微信读书 Web...[/yellow]")
        if not self.api._ensure_browser():
            console.print("[red]❌ 无法启动浏览器，请检查登录状态[/red]")
            return

        # 导航到书籍（不跳转章节，让 WeRead 自动恢复上次阅读进度）
        console.print(f"[yellow]📖 正在打开《{self.current_book.title}》...[/yellow]")
        browser = self.api._browser
        if not browser.navigate_to_book(self.current_book.book_id):
            console.print("[red]❌ 无法打开书籍[/red]")
            return

        # 等待 WeRead SPA 完成自动恢复进度（轮询 URL 直到出现章节 hash）
        # WeRead 初始打开 book_url 后，会从服务器获取进度并 SPA 导航到目标章节
        console.print("[dim]⏳ 等待 WeRead 恢复阅读进度...[/dim]")
        import time
        start = time.time()
        detected_chapter_idx = None
        while time.time() - start < 15:  # 从 10s 增加到 15s，给冷启动更多时间
            current_url = browser._page.url
            detected_chapter_idx = browser._detect_chapter_index_from_url(current_url)
            if detected_chapter_idx is not None:
                break
            browser._page.wait_for_timeout(500)

        if detected_chapter_idx is not None and 0 <= detected_chapter_idx < len(self.current_chapters):
            if detected_chapter_idx != self.current_chapter_index:
                console.print(f"[yellow]📄 同步章节: CLI={self.current_chapter_index + 1} → WeRead={detected_chapter_idx + 1} ({self.current_chapters[detected_chapter_idx].title})[/yellow]")
            self.current_chapter_index = detected_chapter_idx
        else:
            console.print(f"[yellow]📄 使用 CLI 记录的章节: 第 {self.current_chapter_index + 1} 章[/yellow]")

        # 关键修复：WeRead 自动恢复过程中 Canvas 累积了多页文本
        # 方案：等待 Canvas 渲染完成 → 翻到下一页 → 清空 Canvas → 翻回当前页 → 只捕获当前页渲染
        console.print("[dim]🔄 刷新 Canvas 内容...[/dim]")
        
        # 等待 Canvas 渲染完成（首次打开时需要等待 WeRead SPA 完成数据加载和渲染）
        browser._wait_for_canvas_text(timeout_ms=15000)
        browser._page.wait_for_timeout(1500)
        
        next_btn = browser._find_text('下一页')
        if next_btn:
            next_btn.click(force=True)
            browser._page.wait_for_timeout(1200)
            # 清空下一页的文本
            browser._page.evaluate("() => { if (window.__CANVAS_TEXT__) window.__CANVAS_TEXT__ = []; }")
            # 翻回当前页
            prev_btn = browser._find_text('上一页')
            if prev_btn:
                prev_btn.click(force=True)
                browser._page.wait_for_timeout(1500)
                # 等待翻回后 Canvas 重新渲染
                browser._wait_for_canvas_text(timeout_ms=10000)
            else:
                # 翻到下一页后没有"上一页"，说明整本书只有1页
                # 无法用翻页刷新，依赖多页截断逻辑（应该只有1页所以没问题）
                console.print("[dim]📄 仅1页，跳过刷新[/dim]")
        else:
            # 没有下一页按钮，说明在章节末尾，使用多页截断逻辑
            console.print("[dim]📄 无下一页，依赖多页截断[/dim]")

        # 进入阅读模式前保存一次 Cookie
        browser.save_session_cookies(self.login_manager.cookie_file)
        
        # 确保 canvas extractor 模块可用
        try:
            from .canvas_extractor import reconstruct_text_from_canvas  # noqa
        except ImportError:
            console.print("[red]❌ 缺少 canvas_extractor 模块[/red]")
            return

        self._reading_mode = True
        self._reading_loop()
        self._reading_mode = False
        
        # 退出阅读模式时再保存一次 Cookie
        browser.save_session_cookies(self.login_manager.cookie_file)

    def _reading_loop(self):
        """阅读主循环"""
        browser = self.api._browser
        first_open = True  # 首次打开：直接从浏览器提取，不走 API
        browser_navigated = False  # 标记是否已通过浏览器导航

        while True:
            try:
                chapter = self.current_chapters[self.current_chapter_index] if self.current_chapter_index < len(self.current_chapters) else None

                # 获取内容
                if first_open:
                    # 首次打开：_enter_reading_mode() 已导航到目标章节，
                    # 等待 Canvas 渲染完成后提取（解决首次打开超时问题）
                    raw = browser.get_content_with_title(wait_for_render=True)
                    browser_navigated = True  # 后续同一章内的翻页也从浏览器提取
                    first_open = False
                elif browser_navigated:
                    # 浏览器已经导航过了（翻页/跳转章节后），直接提取当前页面内容
                    raw = browser.get_content_with_title()
                    browser_navigated = False
                elif chapter:
                    raw = self.api.get_chapter_content(
                        self.current_book.book_id,
                        chapter.chapter_id,
                        chapter_index=self.current_chapter_index
                    )
                else:
                    raw = browser.get_content_with_title()

                # 打印提取的章节标题和内容首句用于调试
                if raw and is_debug_enabled():
                    if isinstance(raw, dict):
                        debug_title = raw.get("title", "")
                        debug_text = raw.get("content", "")[:50]
                    else:
                        debug_title = raw.title if raw.title else ""
                        debug_text = raw.content[:50]
                    console.print(f"[dim][调试] 提取标题={debug_title or '无'}, 首句={debug_text}...[/dim]")

                # 统一归一化 content：支持 BookContent 对象和 dict
                if raw:
                    if isinstance(raw, dict):
                        content_title = raw.get("title", "")
                        content_text = raw.get("content", "")
                    else:
                        content_title = raw.title if raw.title else ""
                        content_text = raw.content
                else:
                    content_title = ""
                    content_text = ""

                if content_text and len(content_text) > 30:
                    # 显示章节标题
                    title = content_title or (
                        chapter.title if chapter else "未知章节"
                    )

                    # 自动检测章节变化：通过提取的标题匹配章节列表
                    if content_title and len(content_title) < 80:
                        for idx, ch in enumerate(self.current_chapters):
                            if ch.title == content_title or content_title in ch.title or ch.title in content_title:
                                if idx != self.current_chapter_index:
                                    console.print(f"[dim]📄 自动同步章节: {self.current_chapter_index + 1} → {idx + 1} ({content_title})[/dim]")
                                    self.current_chapter_index = idx
                                    chapter = ch
                                break

                    # 构建章节信息字符串
                    total_chaps = len(self.current_chapters)
                    current_chap_num = self.current_chapter_index + 1
                    chapter_info = f"第 {current_chap_num}/{total_chaps} 章 · {title}"
                    has_prev = self.current_chapter_index > 0
                    has_next = self.current_chapter_index < total_chaps - 1

                    # 显示内容（无页码，回归4月模式）
                    result = self._display_content(content_text, chapter_info, has_prev, has_next)
                    if result == "quit":
                        break
                    elif result == "prev_chapter":
                        if self.current_chapter_index > 0:
                            self.current_chapter_index -= 1
                            browser_navigated = False  # 让下一轮重新加载
                            continue
                    elif result == "next_chapter":
                        if self.current_chapter_index < len(self.current_chapters) - 1:
                            self.current_chapter_index += 1
                            browser_navigated = False
                            continue
                    elif result == "next_page":
                        # 浏览器翻到下一页
                        try:
                            success = browser.next_page()
                            if success:
                                browser_navigated = True  # 下一轮重新提取
                            else:
                                console.print("[yellow]⚠️ 没有下一页了[/yellow]")
                            continue
                        except Exception:
                            console.print("[yellow]⚠️ 没有下一页了[/yellow]")
                    elif result == "prev_page":
                        # 浏览器翻到上一页
                        try:
                            success = browser.prev_page()
                            if success:
                                browser_navigated = True  # 下一轮重新提取
                            else:
                                console.print("[yellow]⚠️ 没有上一页了[/yellow]")
                            continue
                        except Exception:
                            console.print("[yellow]⚠️ 没有上一页了[/yellow]")
                    elif result == "refresh":
                        console.print("[yellow]🔄 正在刷新页面...[/yellow]")
                        try:
                            refresh_ok = browser.refresh_current_page()
                            if refresh_ok:
                                browser_navigated = True
                                continue
                            else:
                                console.print("[red]❌ 刷新失败[/red]")
                        except Exception as e:
                            console.print(f"[red]⚠️ 刷新异常: {e}[/red]")
                    elif result == "chapters":
                        try:
                            self.show_chapters(highlight_index=self.current_chapter_index)
                            ch_choice = IntPrompt.ask("输入序号跳转（0 取消）", default=0)
                            if 0 < ch_choice <= len(self.current_chapters):
                                self.current_chapter_index = ch_choice - 1
                                chapter = self.current_chapters[self.current_chapter_index]
                                console.print(f"[yellow]📄 正在跳转到第 {ch_choice} 章：{chapter.title}...[/yellow]")
                                browser.go_to_chapter(
                                    int(chapter.chapter_id),
                                    chapter_title=chapter.title,
                                    chapter_index=ch_choice - 1,
                                    chapter_idx=chapter.chapter_idx
                                )
                                browser_navigated = True
                                _save_reading_state(
                                    self.current_book.book_id,
                                    self.current_book.title,
                                    self.current_book.author,
                                    self.current_chapter_index,
                                    self.current_chapters,
                                )
                                continue
                        except Exception as e:
                            console.print(f"[red]⚠️ 章节选择异常: {e}[/red]")
                    elif result == "debug":
                        try:
                            captured = browser._page.evaluate("() => window.__CANVAS_TEXT__ || []")
                            if captured:
                                debug_text = browser._reconstruct_dual_column_spread(captured, debug=True)
                                console.print(f"[cyan]{debug_text}[/cyan]")
                            else:
                                console.print("[yellow]Canvas 文本为空[/yellow]")
                        except Exception as e:
                            console.print(f"[red]⚠️ 诊断异常: {e}[/red]")
                        continue
                    elif result == "toggle_mode":
                        console.print("[yellow]🔄 正在切换阅读模式...[/yellow]")
                        try:
                            new_mode = browser.toggle_reading_mode()
                            if new_mode != "unknown":
                                mode_label = "📜 滚动模式" if new_mode == "scroll" else "📖 双栏翻页模式"
                                console.print(f"[green]✅ 已切换到 {mode_label}[/green]")
                                browser_navigated = True
                                continue
                            else:
                                console.print("[yellow]⚠️ 切换模式失败[/yellow]")
                        except Exception as e:
                            console.print(f"[red]⚠️ 切换模式异常: {e}[/red]")
                else:
                    # 内容为空，检测页面是否过期
                    if browser.is_page_stale():
                        console.print("[yellow]⚠️  页面可能已过期，自动刷新中...[/yellow]")
                        try:
                            refresh_ok = browser.refresh_current_page()
                            if refresh_ok:
                                browser_navigated = True
                                continue  # 重新进入循环获取内容
                            else:
                                console.print("[red]❌ 自动刷新失败[/red]")
                        except Exception as e:
                            console.print(f"[red]⚠️  自动刷新异常: {e}[/red]")
                    else:
                        console.print("[yellow]⚠️  无法获取内容，尝试翻页...[/yellow]")
                        try:
                            browser.next_page()
                            browser._page.wait_for_timeout(1000)
                            fb_raw = browser.get_content_with_title()
                            if fb_raw:
                                fb_text = fb_raw.get("content", "") if isinstance(fb_raw, dict) else fb_raw.content
                                if fb_text and len(fb_text) > 30:
                                    total_chaps = len(self.current_chapters)
                                    chapter_info = f"第 {self.current_chapter_index + 1}/{total_chaps} 章 · {chapter.title if chapter else ''}"
                                    has_prev = self.current_chapter_index > 0
                                    has_next = self.current_chapter_index < total_chaps - 1
                                    result = self._display_content(fb_text, chapter_info, has_prev, has_next)
                                    if result == "quit":
                                        break
                                    elif result == "prev_chapter" and self.current_chapter_index > 0:
                                        self.current_chapter_index -= 1
                                        browser_navigated = False
                                        continue
                                    elif result == "next_chapter" and self.current_chapter_index < total_chaps - 1:
                                        self.current_chapter_index += 1
                                        browser_navigated = False
                                        continue
                                    elif result == "refresh":
                                        console.print("[yellow]🔄 正在刷新页面...[/yellow]")
                                        refresh_ok = browser.refresh_current_page()
                                        if refresh_ok:
                                            browser_navigated = True
                                            continue
                                    elif result == "next_page":
                                        try:
                                            success = browser.next_page()
                                            if success:
                                                browser_navigated = True
                                            continue
                                        except Exception:
                                            pass
                                    elif result == "prev_page":
                                        try:
                                            success = browser.prev_page()
                                            if success:
                                                browser_navigated = True
                                            continue
                                        except Exception:
                                            pass
                                else:
                                    console.print("[yellow]⚠️  翻页后仍无内容，按 n 进入下一章[/yellow]")
                            else:
                                console.print("[yellow]⚠️  翻页后仍无内容，按 n 进入下一章[/yellow]")
                        except Exception:
                            console.print("[red]⚠️  翻页失败，页面可能已变化[/red]")

                action = Prompt.ask(
                    "内容未显示，r 刷新，n 下一章，q 返回",
                    choices=["r", "n", "q"],
                    default="r",
                )
                if action == "q":
                    break
                if action == "n" and self.current_chapter_index < len(self.current_chapters) - 1:
                    self.current_chapter_index += 1
                    browser_navigated = False
                    continue
                if action == "r":
                    try:
                        if browser.refresh_current_page():
                            browser_navigated = True
                            continue
                    except Exception as e:
                        console.print(f"[red]⚠️ 刷新异常: {e}[/red]")
            except KeyboardInterrupt:
                break
            except Exception as e:
                console.print(f"[red]⚠️ 阅读循环异常: {e}[/red]")
                # 尝试恢复：检查浏览器是否还活着
                if not browser or not browser.is_alive:
                    console.print("[red]⚠️ 浏览器已断开，返回主菜单[/red]")
                    break
                browser._page.wait_for_timeout(1000)

    def _display_content(self, text: str, chapter_info: str = "", 
                         has_prev_chapter: bool = False, has_next_chapter: bool = False) -> str:
        """显示内容（回归4月模式：无页码，每次提取显示当前页面全部内容）

        Args:
            text: 章节正文（当前浏览器页面的内容）
            chapter_info: 章节信息字符串，如 "第 3/151 章 · 第三章 改革开放"
            has_prev_chapter: 是否有上一章可跳转
            has_next_chapter: 是否有下一章可跳转

        Returns:
            用户操作：'prev_chapter'、'next_chapter'、'next_page'、'prev_page'、'refresh'、'quit'
        """
        try:
            if not text or not text.strip():
                console.print("[dim]（本页无文本内容）[/dim]")
                return "continue"

            # 按段落分割
            paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

            if not paragraphs:
                console.print("[dim]（本页无文本内容）[/dim]")
                return "continue"

            # 页眉：显示章节信息（无页码）
            if chapter_info:
                console.print(f"[bold cyan]{chapter_info}[/bold cyan]\n")

            # 显示全部内容
            content_text = "\n".join(paragraphs)
            console.print(content_text)

            # 页脚（不再显示"内容结束"）
            console.print()

            # 等待用户操作：回车=下一页
            choices = [""]
            hints = "Enter 下一页"
            choices.append("b")
            hints += "，b 上一页"
            if has_prev_chapter:
                choices.append("p")
                hints += "，p 上一章"
            if has_next_chapter:
                choices.append("n")
                hints += "，n 下一章"
            choices.extend(["f", "c", "m", "d", "r", "q"])
            hints += "，f 下一页，c 目录，m 模式，d 诊断，r 刷新页面，q 返回"

            next_action = Prompt.ask(hints, choices=choices, default="")
            if next_action == "q":
                return "quit"
            elif next_action == "p":
                return "prev_chapter"
            elif next_action == "n":
                return "next_chapter"
            elif next_action == "f" or next_action == "":
                return "next_page"
            elif next_action == "b":
                return "prev_page"
            elif next_action == "r":
                return "refresh"
            elif next_action == "c":
                return "chapters"
            elif next_action == "d":
                return "debug"
            elif next_action == "m":
                return "toggle_mode"
            return "continue"
        except KeyboardInterrupt:
            return "quit"
        except Exception as e:
            console.print(f"[dim]（内容显示异常: {e}）[/dim]")
            return "continue"

    # ── 主菜单 ──────────────────────────────────────────────

    def main_menu(self):
        """主菜单"""
        while True:
            console.print("\n[bold]主菜单：[/bold]")
            console.print("  [cyan]1[/cyan] - 我的书架（输入序号直接阅读）")
            console.print("  [cyan]2[/cyan] - 搜索书籍")
            console.print("  [cyan]3[/cyan] - 继续阅读")
            console.print("  [cyan]4[/cyan] - 刷新 Cookie")
            console.print("  [cyan]5[/cyan] - 登录")
            console.print("  [cyan]6[/cyan] - 退出登录")
            console.print("  [cyan]0[/cyan] - 退出")

            choice = Prompt.ask(
                "请选择",
                choices=["0", "1", "2", "3", "4", "5", "6"],
                default="1"
            )

            if choice == "1":
                self.show_bookshelf(interactive=True)
            elif choice == "2":
                self.search_books()
            elif choice == "3":
                if self.current_book and self.current_chapters:
                    console.print(f"[green]📖 继续阅读：{self.current_book.title}[/green]")
                    self._enter_reading_mode()
                else:
                    # 尝试从本地状态恢复
                    state = _load_reading_state()
                    if state:
                        console.print(f"[yellow]📌 发现上次阅读记录：{state['title']}[/yellow]")
                        # 查询远端最新进度，确保不会用旧的本地缓存覆盖远端
                        remote_book = self.api.get_book_progress(state["book_id"])
                        if remote_book:
                            self._load_book_for_reading(remote_book, start_chapter=state["chapter_index"])
                        else:
                            book = Book(
                                book_id=state["book_id"],
                                title=state["title"],
                                author=state["author"],
                            )
                            self._load_book_for_reading(book, start_chapter=state["chapter_index"])
                    else:
                        console.print("[yellow]⚠️  还没有阅读记录，请从书架或搜索中选择[/yellow]\n")
            elif choice == "4":
                self.refresh_cookies()
            elif choice == "5":
                self.login()
            elif choice == "6":
                self.logout()
            elif choice == "0":
                # 保存阅读进度
                if self.current_book and self.current_chapters:
                    _save_reading_state(
                        self.current_book.book_id,
                        self.current_book.title,
                        self.current_book.author,
                        self.current_chapter_index,
                        self.current_chapters,
                    )
                console.print("\n[yellow]正在关闭浏览器...[/yellow]")
                self.api.close()
                console.print("[green]👋 再见！[/green]\n")
                break


def main():
    """入口函数"""
    cli = WeReadCLI()
    cli._setup_signals()

    banner = r"""
 _       ____________  _________    ____     ________    ____
| |     / / ____/ __ \/ ____/   |  / __ \   / ____/ /   /  _/
| | /| / / __/ / /_/ / __/ / /| | / / / /  / /   / /    / /
| |/ |/ / /___/ _, _/ /___/ ___ |/ /_/ /  / /___/ /____/ /
|__/|__/_____/_/ |_/_____/_/  |_/_____/   \____/_____/___/
    """
    console.print(Panel(
        "[bold blue]" + banner + "[/bold blue]\n"
        "命令行微信读书工具 v0.2.0 (CDP Edition)",
        title="📚 WeRead CLI",
        border_style="blue"
    ))

    # 检查登录
    if not cli.check_login():
        console.print("[yellow]未登录，请先登录[/yellow]\n")
        cli.login()

    # 主菜单
    cli.main_menu()


if __name__ == "__main__":
    main()
