# -*- coding: utf-8 -*-
"""数据模型"""

from dataclasses import dataclass
from typing import Optional, List


@dataclass
class Book:
    """书籍信息"""
    book_id: str
    title: str
    author: str
    cover_url: str = ""
    progress: int = 0
    last_read_chapter: str = ""
    # 远端阅读进度的章节 UID（用于定位起始章节）
    last_chapter_uid: int = 0
    
    def __str__(self):
        progress_str = f"{self.progress}%" if self.progress > 0 else "未读"
        return f"📖 {self.title} - {self.author} [{progress_str}]"


@dataclass
class Chapter:
    """章节信息"""
    chapter_id: str
    title: str
    index: int = 0
    is_vip: bool = False
    chapter_idx: int = 0  # API 返回的 chapterIdx，用于生成 URL chapter_hash
    word_count: int = 0  # 章节字数（来自 API），用于估算总页数

    def __str__(self):
        vip_mark = "👑 " if self.is_vip else ""
        return f"{vip_mark}{self.title}"

    @property
    def estimated_pages(self) -> int:
        """估算双栏模式下的总页数（每页约 500-600 字）"""
        if self.word_count <= 0:
            return 1
        return max(1, (self.word_count + 500) // 500)


@dataclass
class BookContent:
    """章节内容"""
    chapter_id: str
    title: str
    content: str


@dataclass
class UserInfo:
    """用户信息"""
    user_id: str
    name: str
    avatar: str = ""
    is_vip: bool = False
