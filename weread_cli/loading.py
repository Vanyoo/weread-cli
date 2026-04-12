"""终端 Loading Spinner — Python 版 rattles loading preset

等效 Rust 代码:
    rattle!(Loading, loading, 10, 100,
        ["Loading   ", "Loading.  ", "Loading.. ", "Loading..."]);
"""

import sys
import threading
import time
from typing import Optional


class LoadingSpinner:
    """Loading 旋转动画 — 与 rattles loading preset 一致。

    用法:
        # 方式 1: 上下文管理器
        with LoadingSpinner():
            do_something_slow()

        # 方式 2: 手动控制
        spinner = LoadingSpinner()
        spinner.start()
        do_something_slow()
        spinner.stop()

        # 方式 3: 带文本
        with LoadingSpinner("获取书架中"):
            books = api.get_bookshelf()
    """

    FRAMES = ["Loading   ", "Loading.  ", "Loading.. ", "Loading..."]
    INTERVAL = 0.1  # 100ms

    def __init__(self, text: str = ""):
        self.text = text
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def _spin(self):
        idx = 0
        while not self._stop_event.is_set():
            frame = self.FRAMES[idx % len(self.FRAMES)]
            if self.text:
                sys.stdout.write(f"\r{frame} {self.text}")
            else:
                sys.stdout.write(f"\r{frame}")
            sys.stdout.flush()
            self._stop_event.wait(self.INTERVAL)
            idx += 1
        # 清除当前行
        sys.stdout.write("\r" + " " * (len(self.text) + 10) + "\r")
        sys.stdout.flush()

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
