# -*- coding: utf-8 -*-
"""خاموش کردن، لغو خاموشی، و قفل کردن لپ‌تاپ."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Tuple

import config

logger = logging.getLogger(__name__)


async def schedule_shutdown(
    delay_seconds: int = None, message: str = None
) -> Tuple[bool, str]:
    """
    خاموش شدن را با تأخیر زمان‌بندی می‌کند تا فرصت لغو باشد.
    از دستور داخلی ویندوز استفاده می‌شود؛ کتابخانه‌ی اضافه لازم نیست.
    """
    delay = config.SHUTDOWN_DELAY_SECONDS if delay_seconds is None else delay_seconds
    msg = config.SHUTDOWN_MESSAGE if message is None else message

    def _run():
        proc = subprocess.run(
            ["shutdown", "/s", "/t", str(int(delay)), "/c", msg],
            capture_output=True,
            timeout=15,
        )
        return proc.returncode, proc.stderr.decode("utf-8", errors="replace")

    try:
        code, err = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.exception("زمان‌بندی خاموشی ناموفق بود")
        return False, str(exc)

    if code != 0:
        return False, err.strip() or f"exit code {code}"
    return True, f"خاموشی در {delay} ثانیه دیگر انجام می‌شود."


async def abort_shutdown() -> Tuple[bool, str]:
    """دستور لغو خاموشی ویندوز: shutdown /a"""

    def _run():
        proc = subprocess.run(
            ["shutdown", "/a"], capture_output=True, timeout=15
        )
        return proc.returncode, proc.stderr.decode("utf-8", errors="replace")

    try:
        code, err = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.exception("لغو خاموشی ناموفق بود")
        return False, str(exc)

    if code != 0:
        # اگر خاموشی‌ای در جریان نبوده، ویندوز پیام خطا می‌دهد
        return False, err.strip() or "خاموشی‌ای در جریان نبود."
    return True, "خاموشی لغو شد. لپ‌تاپ روشن می‌ماند."


async def lock_workstation() -> Tuple[bool, str]:
    """قفل کردن نشست کاری ویندوز (بدون نیاز به pywin32)."""

    def _run():
        proc = subprocess.run(
            ["rundll32.exe", "user32.dll,LockWorkStation"],
            capture_output=True,
            timeout=15,
        )
        return proc.returncode

    try:
        code = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.exception("قفل کردن ناموفق بود")
        return False, str(exc)

    if code != 0:
        return False, f"exit code {code}"
    return True, "نشست کاری قفل شد."


async def is_shutdown_pending() -> bool:
    """
    بررسی می‌کند که آیا خاموشی‌ای در صف ویندوز هست یا نه.

    از طریق کلاس WMI و پروپرتی‌های Win32_OperatingSystem چک می‌شود،
    بدون اینکه چیزی را تغییر دهد (برخلاف فراخوانی shutdown /a).
    """
    script = (
        "(Get-CimInstance Win32_OperatingSystem).ShutdownDate"
    )

    def _run():
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=20,
        )
        return proc.stdout.decode("utf-8", errors="replace").strip()

    try:
        out = await asyncio.to_thread(_run)
    except Exception:
        logger.exception("بررسی وضعیت خاموشی ناموفق بود")
        return False

    # اگر shutdown اجرا نشده باشد این پروپرتی null یا خالی است
    return bool(out) and out.lower() not in {"null", "", "\x00"}