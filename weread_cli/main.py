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
        """检查登录状态"""
        if not self.login_manager.check_login():
            console.print("[yellow]⚠️  未找到登录信息[/yellow]\n")
            return False

        user = self.api.check_login()
        if user:
            vip_badge = "👑 VIP" if user.is_vip else ""
            console.print(f"[green]✅ 已登录：{user.name} {vip_badge}[/green]\n")
            return True

        console.print("[yellow]⚠️  发现本地 Cookie，但服务器验证失败[/yellow]")
        console.print("[yellow]💡 将尝试使用，如失败请重新登录[/yellow]\n")
        return True

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

        table = Table(title="📚 我的书架", show_lines=True)
        table.add_column("序号", style="cyan", justify="center", width=6)
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

        if interactive:
            console.print("[dim]输入序号直接阅读，0 返回主菜单[/dim]")
            choice = IntPrompt.ask("请选择", default=0)
            if 1 <= choice <= len(books):
                self._load_book_for_reading(books[choice - 1])

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

    def show_chapters(self):
        """显示章节列表"""
        if not self.current_book:
            console.print("[yellow]请先选择书籍[/yellow]\n")
            return

        if not self.current_chapters:
            console.print("[yellow]暂无章节[/yellow]\n")
            return

        table = Table(
            title=f"📖 {self.current_book.title} - 章节列表",
            show_lines=True
        )
        table.add_column("序号", style="cyan", justify="center", width=6)
        table.add_column("章节标题", style="green")
        table.add_column("VIP", justify="center", width=5)

        for idx, chapter in enumerate(self.current_chapters, 1):
            vip_mark = "👑" if chapter.is_vip else ""
            table.add_row(
                str(idx),
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
        """进入阅读模式（启动浏览器、打开书、进入阅读循环）"""
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

        # 导航到书籍
        console.print(f"[yellow]📖 正在打开《{self.current_book.title}》...[/yellow]")
        browser = self.api._browser
        if not browser.navigate_to_book(self.current_book.book_id):
            console.print("[red]❌ 无法打开书籍[/red]")
            return

        # 等待阅读器加载，Canvas 钩子已在 start() 和 navigate_to_book() 中注入
        browser._page.wait_for_timeout(2000)
        
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
        browser_navigated = False  # 标记是否已通过浏览器导航

        while True:
            try:
                chapter = self.current_chapters[self.current_chapter_index] if self.current_chapter_index < len(self.current_chapters) else None

                # 获取内容
                if browser_navigated:
                    # 浏览器已经导航过了，直接提取当前页面内容
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

                    # 构建章节信息字符串
                    total_chaps = len(self.current_chapters)
                    current_chap_num = self.current_chapter_index + 1
                    chapter_info = f"第 {current_chap_num}/{total_chaps} 章 · {title}"
                    has_prev = self.current_chapter_index > 0
                    has_next = self.current_chapter_index < total_chaps - 1

                    console.print(Panel(
                        f"[bold]{self.current_book.title}[/bold]\n"
                        f"[dim]{title}[/dim]",
                        title="📖 阅读中",
                        border_style="green"
                    ))

                    # 分页显示内容（带章节信息 + 章跳转）
                    result = self._display_content(content_text, chapter_info, has_prev, has_next)

                    # 处理阅读中的章节跳转
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
                            else:
                                console.print("[yellow]⚠️  翻页后仍无内容，按 n 进入下一章[/yellow]")
                        else:
                            console.print("[yellow]⚠️  翻页后仍无内容，按 n 进入下一章[/yellow]")
                    except Exception:
                        console.print("[red]⚠️  翻页失败，页面可能已变化[/red]")

                # 操作菜单（根据当前位置动态显示选项）
                console.print()
                console.print("[bold]操作菜单：[/bold]")
                total = len(self.current_chapters)
                current = self.current_chapter_index + 1
                menu_items = []
                
                if self.current_chapter_index < total - 1:
                    menu_items.append("  [cyan]n[/cyan] - 下一章")
                else:
                    menu_items.append("  [dim]n[/dim] - 已是最后一章")
                menu_items.append("  [cyan]p[/cyan] - 上一章" if self.current_chapter_index > 0 else "  [dim]p[/dim] - 已是第一章")
                menu_items.append("  [cyan]f[/cyan] - 下一页")
                menu_items.append("  [cyan]b[/cyan] - 上一页")
                menu_items.append("  [cyan]c[/cyan] - 章节列表")
                menu_items.append("  [cyan]q[/cyan] - 返回主菜单")
                
                # 两列排版
                for i in range(0, len(menu_items), 2):
                    line = menu_items[i]
                    if i + 1 < len(menu_items):
                        line += "    " + menu_items[i + 1]
                    console.print(line)
                console.print(f"[dim]当前：第 {current}/{total} 章[/dim]")

                action = Prompt.ask(
                    "请选择",
                    choices=["n", "p", "f", "b", "c", "q"],
                    default="n"
                )

                if action == "n":
                    if self.current_chapter_index >= total - 1:
                        console.print("[yellow]⚠️ 已是最后一章[/yellow]")
                        continue
                    # 使用浏览器下一章（会自动处理章节过渡）
                    console.print("[dim]正在跳转下一章...[/dim]")
                    try:
                        success = browser.next_chapter()
                        if success:
                            browser_navigated = True
                            if self.current_chapter_index < len(self.current_chapters) - 1:
                                self.current_chapter_index += 1
                            # 保存最新 Cookie 和阅读进度
                            browser.save_session_cookies(self.login_manager.cookie_file)
                            _save_reading_state(
                                self.current_book.book_id,
                                self.current_book.title,
                                self.current_book.author,
                                self.current_chapter_index,
                                self.current_chapters,
                            )
                        else:
                            console.print("[yellow]⚠️ 下一章跳转失败，可能已是末尾[/yellow]")
                    except Exception as e:
                        console.print(f"[red]⚠️ 下一章跳转异常: {e}[/red]")
                elif action == "p":
                    console.print("[dim]正在跳转上一章...[/dim]")
                    try:
                        success = browser.prev_chapter()
                        if success:
                            browser_navigated = True
                            if self.current_chapter_index > 0:
                                self.current_chapter_index -= 1
                            # 保存阅读进度
                            _save_reading_state(
                                self.current_book.book_id,
                                self.current_book.title,
                                self.current_book.author,
                                self.current_chapter_index,
                                self.current_chapters,
                            )
                    except Exception as e:
                        console.print(f"[red]⚠️ 上一章跳转异常: {e}[/red]")
                elif action == "f":
                    try:
                        success = browser.next_page()
                        if success:
                            browser_navigated = True
                            console.print("[dim]已翻到下一页[/dim]")
                        else:
                            console.print("[yellow]⚠️ 没有下一页了，试试按 n 进入下一章[/yellow]")
                    except Exception as e:
                        console.print(f"[red]⚠️ 翻页异常: {e}[/red]")
                elif action == "b":
                    try:
                        success = browser.prev_page()
                        if success:
                            browser_navigated = True
                            console.print("[dim]已翻到上一页[/dim]")
                        else:
                            console.print("[yellow]⚠️ 没有上一页了[/yellow]")
                    except Exception as e:
                        console.print(f"[red]⚠️ 上一页异常: {e}[/red]")
                elif action == "c":
                    try:
                        self.show_chapters()
                        ch_choice = IntPrompt.ask("跳转到章节 (0 取消)", default=0)
                        if 0 < ch_choice <= len(self.current_chapters):
                            self.current_chapter_index = ch_choice - 1

                            chapter = self.current_chapters[self.current_chapter_index]
                            browser.go_to_chapter(int(chapter.chapter_id), chapter_title=chapter.title, chapter_index=ch_choice - 1, chapter_idx=chapter.chapter_idx)
                            browser_navigated = True
                            # 保存阅读进度
                            _save_reading_state(
                                self.current_book.book_id,
                                self.current_book.title,
                                self.current_book.author,
                                self.current_chapter_index,
                                self.current_chapters,
                            )
                    except Exception as e:
                        console.print(f"[red]⚠️ 章节选择异常: {e}[/red]")
                elif action == "q":
                    break

                console.print()
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
        """分页显示内容

        Args:
            text: 章节正文
            chapter_info: 章节信息字符串，如 "第 3/151 章 · 第三章 改革开放"
            has_prev_chapter: 是否有上一章可跳转
            has_next_chapter: 是否有下一章可跳转

        Returns:
            用户操作：'continue'（继续操作菜单）、'prev_chapter'、'next_chapter'、'quit'
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

            lines_per_page = 25
            page = 0
            total_pages = max(1, len(paragraphs) // lines_per_page + (1 if len(paragraphs) % lines_per_page else 0))

            while page < total_pages:
                start = page * lines_per_page
                end = min(start + lines_per_page, len(paragraphs))
                current_paragraphs = paragraphs[start:end]

                # 页眉：章节信息
                if chapter_info:
                    console.print(f"[bold cyan]{chapter_info}  │  第 {page + 1}/{total_pages} 页[/bold cyan]\n")

                # 显示当前页
                content_text = "\n".join(current_paragraphs)
                console.print(content_text)

                # 页脚
                console.print(f"\n[dim]— 第 {page + 1}/{total_pages} 页 —[/dim]\n")

                if page < total_pages - 1:
                    # 非最后一页：可以上下翻页、跳章节、退出
                    choices = [""]
                    hints = "Enter 下一页"
                    if page > 0:
                        choices.append("b")
                        hints += "，b 上一页"
                    if has_prev_chapter:
                        choices.append("p")
                        hints += "，p 上一章"
                    if has_next_chapter:
                        choices.append("n")
                        hints += "，n 下一章"
                    choices.append("q")
                    hints += "，q 返回"

                    next_action = Prompt.ask(hints, choices=choices, default="")
                    if next_action == "q":
                        return "quit"
                    elif next_action == "p":
                        return "prev_chapter"
                    elif next_action == "n":
                        return "next_chapter"
                    elif next_action == "b":
                        if page > 0:
                            page -= 1
                            continue
                else:
                    # 最后一页
                    choices = [""]
                    hints = "Enter 继续"
                    if has_prev_chapter:
                        choices.append("p")
                        hints += "，p 上一章"
                    if has_next_chapter:
                        choices.append("n")
                        hints += "，n 下一章"
                    choices.append("q")
                    hints += "，q 返回"

                    next_action = Prompt.ask(hints, choices=choices, default="")
                    if next_action == "q":
                        return "quit"
                    elif next_action == "p":
                        return "prev_chapter"
                    elif next_action == "n":
                        return "next_chapter"

                page += 1

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
            console.print("  [cyan]4[/cyan] - 登录")
            console.print("  [cyan]5[/cyan] - 退出登录")
            console.print("  [cyan]0[/cyan] - 退出")

            choice = Prompt.ask(
                "请选择",
                choices=["0", "1", "2", "3", "4", "5"],
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
                self.login()
            elif choice == "5":
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

    banner = """
 ███████╗██╗  ██╗ █████╗ ███████╗██╗  ██╗ 
 ██╔════╝██║  ██║██╔══██╗██╔════╝██║  ██║ 
 ███████╗███████║███████║███████╗███████║ 
 ╚════██║██╔══██║██╔══██║╚════██║██╔══██║ 
 ███████║██║  ██║██║  ██║███████║██║  ██║ 
 ╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝ 
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
