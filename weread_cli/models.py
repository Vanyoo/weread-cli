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
    
    def __str__(self):
        vip_mark = "👑 " if self.is_vip else ""
        return f"{vip_mark}{self.title}"


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
