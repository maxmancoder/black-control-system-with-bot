# -*- coding: utf-8 -*-
"""
تضمین اجرای تنها یک نمونه از بات.

اگر کاربر start.bat را دوبار دابل کلیک کند، یا هم‌زمان Task Scheduler هم
بات را بالا آورده باشد، دو نمونه روی یک توکن تلگرام polling می‌کنند و
بات با خطای Conflict شکست می‌خورد.

این ماژول با یک فایل قفل و بررسی زنده بودن پروسه، این وضعیت را می‌گیرد.
"""

from __future__ import annotations

import ctypes
import logging
import os
import time
from typing import Optional

import config

logger = logging.getLogger(__name__)

# مقداری که ویندوز برای پروسه‌ی در حال اجرا برمی‌گرداند
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _pid_alive(pid: int) -> bool:
    """آیا این پروسه هنوز در حال اجراست؟"""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def read_owner_pid() -> Optional[int]:
    """شماره‌ی پروسه‌ای که قبلاً فایل قفل را در اختیار گرفته، اگر زنده باشد."""
    path = config.LOCK_FILE
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            pid = int((fh.read() or "0").strip())
    except Exception:
        return None
    return pid if _pid_alive(pid) else None


def acquire() -> bool:
    """
    قفل را می‌گیرد.

    True  -> این نمونه تنها نمونه‌ی فعال است
    False -> نمونه‌ی دیگری از قبل در حال اجراست
    """
    owner = read_owner_pid()
    if owner is not None and owner != os.getpid():
        logger.error(
            "یک نمونه‌ی دیگر از بات در حال اجراست (pid=%s). اجرا متوقف شد.",
            owner,
        )
        return False

    try:
        with open(config.LOCK_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except Exception:
        logger.exception("نوشتن فایل قفل ناموفق بود")
        return True  # نباید به‌خاطر قفل، اجرای بات را متوقف کنیم
    return True


def release() -> None:
    """قفل را آزاد می‌کند (در پایان عادی یا Ctrl+C)."""
    try:
        if read_owner_pid() == os.getpid():
            os.remove(config.LOCK_FILE)
    except Exception:
        pass


def describe() -> str:
    owner = read_owner_pid()
    if owner:
        return f"بات در حال حاضر در حال اجراست (pid={owner})."
    return "هیچ نمونه‌ی فعالی از بات وجود ندارد."


def wait_for_release(timeout: float = 0.0) -> None:
    """اختیاری: صبر می‌کند تا نمونه‌ی قبلی قفل را آزاد کند."""
    deadline = time.time() + timeout
    while time.time() < deadline and read_owner_pid() is not None:
        time.sleep(0.5)