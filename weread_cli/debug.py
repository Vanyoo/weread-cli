# -*- coding: utf-8 -*-
"""Debug output helpers."""

import os


def is_debug_enabled() -> bool:
    value = os.environ.get("WEREAD_DEBUG", "")
    return value.lower() in {"1", "true", "yes", "on"}


def debug_print(*args, **kwargs) -> None:
    if is_debug_enabled():
        print(*args, **kwargs)
