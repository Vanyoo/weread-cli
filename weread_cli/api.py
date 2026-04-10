# -*- coding: utf-8 -*-
"""WeRead API 封装

经实测验证的正确 API 端点:
- 用户信息: GET /web/user?userVid={vid}
- 书架:     GET /web/shelf/sync
- 章节列表: POST /web/book/chapterInfos  body={"bookIds":[...]}
- 章节内容: 通过 CDP 浏览器渲染后提取
"""

import json
import os
import httpx
from pathlib import Path
from typing import Optional, List, Dict

from .models import Book, Chapter, BookContent, UserInfo
from .browser import CDPBrowserManager


class WeReadAPI:
    """微信读书 API"""

    BASE_URL = "https://weread.qq.com"

    def __init__(self, cookie_file: Optional[Path] = None):
        self.cookie_file = cookie_file or Path.home() / ".weread_cookies.json"
        self.client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
            }
        )
        self._cookies: dict = {}
        self._load_cookies()

        # CDP 浏览器管理器（延迟初始化）
        self._browser: Optional[CDPBrowserManager] = None

    # ── Cookie 管理 ─────────────────────────────────────────

    def _load_cookies(self) -> bool:
        """从文件加载 Cookie"""
        if self.cookie_file.exists():
            try:
                with open(self.cookie_file) as f:
                    self._cookies = json.load(f)
                if not self._cookies:
                    return False
                for name, value in self._cookies.items():
                    self.client.cookies.set(name, value, domain=".weread.qq.com")
                critical = ['wr_vid', 'wr_rt', 'wr_skey']
                return any(k in self._cookies for k in critical)
            except Exception:
                pass
        return False

    def _save_cookies(self, cookies: Optional[Dict] = None) -> None:
        """保存 Cookie 到文件"""
        if cookies:
            self._cookies = cookies
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        
        # 先写到临时文件，再原子替换，避免崩溃时写入不完整的数据
        tmp_path = str(self.cookie_file) + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(self._cookies, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(self.cookie_file))
        print(f"💾 Cookie 已保存到 {self.cookie_file} ({len(self._cookies)} 个)")

    def set_cookies(self, cookies: dict) -> None:
        """设置 Cookie 并同时持久化到文件"""
        for name, value in cookies.items():
            self.client.cookies.set(name, value, domain=".weread.qq.com")
        self._cookies = cookies
        # 同时保存到文件
        self._save_cookies(cookies)

    def import_from_browser(self, browser: str = "chrome") -> bool:
        """从浏览器导入 Cookie"""
        try:
            import browser_cookie3
            browser_func = {
                "chrome": browser_cookie3.chrome,
                "firefox": browser_cookie3.firefox,
                "safari": browser_cookie3.safari,
                "edge": browser_cookie3.edge,
            }
            if browser not in browser_func:
                print(f"❌ 不支持的浏览器：{browser}")
                return False

            print(f"正在从 {browser} 导入 Cookie...")
            cj = browser_func[browser](domain_name="weread.qq.com")
            cookies = {}
            for cookie in cj:
                cookies[cookie.name] = cookie.value
                self.client.cookies.set(cookie.name, cookie.value, domain=cookie.domain)

            if cookies:
                self._save_cookies(cookies)
                print(f"✅ 从 {browser} 导入 {len(cookies)} 个 Cookie")
                return True
            else:
                print(f"⚠️  未找到微信读书 Cookie")
                return False
        except Exception as e:
            print(f"❌ 导入失败：{e}")
        return False

    # ── HTTP API（无需浏览器）────────────────────────────────

    def check_login(self) -> Optional[UserInfo]:
        """
        检查登录状态。
        
        使用正确的 API: GET /web/user?userVid={vid}
        返回: UserInfo 或 None
        """
        vid = self._cookies.get("wr_vid")
        if not vid:
            return None

        try:
            resp = self.client.get(
                f"{self.BASE_URL}/web/user",
                params={"userVid": vid},
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("userVid"):
                    return UserInfo(
                        user_id=str(data["userVid"]),
                        name=data.get("name", "User"),
                        avatar=data.get("avatar", ""),
                        is_vip=bool(data.get("isV", False))
                    )
        except Exception as e:
            print(f"检查登录状态失败: {e}")
        return None

    def get_bookshelf(self) -> List[Book]:
        """
        获取书架。
        
        使用正确的 API: GET /web/shelf/sync
        返回包含 bookProgress（阅读进度）和 books（书籍详情）
        """
        books = []
        try:
            resp = self.client.get(f"{self.BASE_URL}/web/shelf/sync")
            if resp.status_code == 200:
                data = resp.json()
                book_progress = data.get("bookProgress", [])
                books_list = data.get("books", [])

                # books 是一个列表，需要转为 dict 方便查找
                books_info = {b["bookId"]: b for b in books_list if "bookId" in b}

                for bp in book_progress:
                    book_id = bp.get("bookId", "")
                    info = books_info.get(book_id, {})

                    # 获取远端阅读进度的章节 UID
                    chapter_uid = bp.get("chapterUid", 0) or info.get("chapterUid", 0)

                    books.append(Book(
                        book_id=book_id,
                        title=info.get("title", "Unknown"),
                        author=info.get("author", "Unknown"),
                        cover_url=info.get("cover", ""),
                        progress=bp.get("progress", 0),
                        last_read_chapter=info.get("lastChapterTitle", ""),
                        last_chapter_uid=chapter_uid,
                    ))
        except Exception as e:
            print(f"❌ 获取书架失败：{e}")
        return books

    def get_book_progress(self, book_id: str) -> Optional[Book]:
        """
        获取单本书的阅读进度（轻量请求，复用书架 API 返回）。
        
        用于选项3恢复时获取最新远端进度。
        """
        try:
            resp = self.client.get(f"{self.BASE_URL}/web/shelf/sync")
            if resp.status_code == 200:
                data = resp.json()
                book_progress = data.get("bookProgress", [])
                books_list = data.get("books", [])
                books_info = {b["bookId"]: b for b in books_list if "bookId" in b}

                for bp in book_progress:
                    if bp.get("bookId") == book_id:
                        info = books_info.get(book_id, {})
                        chapter_uid = bp.get("chapterUid", 0) or info.get("chapterUid", 0)
                        return Book(
                            book_id=book_id,
                            title=info.get("title", "Unknown"),
                            author=info.get("author", "Unknown"),
                            last_chapter_uid=chapter_uid,
                        )
        except Exception as e:
            print(f"❌ 获取书籍进度失败：{e}")
        return None

    def get_chapters(self, book_id: str) -> List[Chapter]:
        """
        获取章节列表。
        
        使用正确的 API: POST /web/book/chapterInfos  body={"bookIds":["..."]}
        返回 data[0].updated 中的章节数组
        """
        chapters = []
        try:
            resp = self.client.post(
                f"{self.BASE_URL}/web/book/chapterInfos",
                json={"bookIds": [book_id]}
            )
            if resp.status_code == 200:
                data = resp.json()
                book_data = data.get("data", [{}])[0]
                chapter_list = book_data.get("updated", [])

                for idx, ch in enumerate(chapter_list):
                    chapters.append(Chapter(
                        chapter_id=str(ch.get("chapterUid", idx)),
                        title=ch.get("title", f"Chapter {idx+1}"),
                        index=idx,
                        is_vip=ch.get("price", 0) > 0,
                        chapter_idx=ch.get("chapterIdx", idx)
                    ))
        except Exception as e:
            print(f"❌ 获取章节失败：{e}")
        return chapters

    # ── CDP 浏览器（章节内容读取）────────────────────────────

    def _ensure_browser(self) -> bool:
        """确保浏览器已启动（默认使用有头模式，确保内容正常渲染）"""
        if self._browser and self._browser.is_alive:
            return True

        if not self._cookies:
            print("❌ 未登录，无法启动浏览器")
            return False

        # 使用 headed 模式，确保微信读书内容正常渲染
        self._browser = CDPBrowserManager(self._cookies, mode="headed")
        if not self._browser.start():
            self._browser = None
            return False
        return True

    def get_chapter_content(self, book_id: str, chapter_id: str, chapter_index: int = 0) -> Optional[BookContent]:
        """
        获取章节内容。
        
        流程:
        1. 确保浏览器已启动
        2. 如果该书未加载，导航到该书
        3. 跳转到目标章节
        4. 从 DOM 提取已渲染的文本内容
        """
        if not self._ensure_browser():
            return None

        chapter_uid = int(chapter_id) if chapter_id.isdigit() else 0

        # 加载书籍（如果尚未加载）
        if self._browser.state.book_id != book_id:
            if not self._browser.navigate_to_book(book_id):
                return None
            # 点击通过前导页面
            self._browser._click_through_intro()

        # 跳转到目标章节（使用正确的索引）
        chapter_title = self._browser.go_to_chapter(chapter_uid, chapter_title="", chapter_index=chapter_index)

        # 提取内容
        result = self._browser.get_content_with_title()
        if result and result.get("textLength", 0) > 50:
            return BookContent(
                chapter_id=chapter_id,
                title=result.get("title", chapter_title or f"Chapter {chapter_id}"),
                content=result["content"]
            )

        # Fallback: 直接提取
        content = self._browser.get_content()
        if content and len(content) > 50:
            return BookContent(
                chapter_id=chapter_id,
                title=chapter_title or f"Chapter {chapter_id}",
                content=content
            )

        return None

    def next_page(self) -> bool:
        """翻页"""
        if self._browser and self._browser.is_alive:
            return self._browser.next_page()
        return False

    def prev_page(self) -> bool:
        """上一页"""
        if self._browser and self._browser.is_alive:
            return self._browser.prev_page()
        return False

    def next_chapter(self) -> bool:
        """下一章"""
        if self._browser and self._browser.is_alive:
            return self._browser.next_chapter()
        return False

    def prev_chapter(self) -> bool:
        """上一章"""
        if self._browser and self._browser.is_alive:
            return self._browser.prev_chapter()
        return False

    def search_books(self, query: str) -> List[Book]:
        """搜索书架中的书籍"""
        books = self.get_bookshelf()
        query_lower = query.lower()
        return [
            book for book in books
            if query_lower in book.title.lower() or query_lower in book.author.lower()
        ]

    def close(self):
        """关闭浏览器（程序退出时调用）"""
        if self._browser:
            # 同步 Cookie
            new_cookies = self._browser.get_cookies()
            if new_cookies:
                self._save_cookies(new_cookies)
            self._browser.close()
            self._browser = None
        try:
            self.client.close()
        except Exception:
            pass
