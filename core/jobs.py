# -*- coding: utf-8 -*-
"""
صف کارهای اتوماسیون.

انتظار کشیدن باید در پس‌زمینه انجام شود تا بقیه‌ی دکمه‌ها هم‌زمان جواب بدهند.
از طرفی هر لحظه فقط یک کار اتوماسیون فعال باشد، چون کلیک و تایپ روی
دسکتاپ هم‌زمان با هم به‌هم نمی‌ریزد.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


class JobQueue:
    """یک کار اتوماسیون فعال در هر لحظه."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._name: Optional[str] = None
        self._started_at: Optional[float] = None
        self._cancel = False

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    @property
    def current(self) -> Optional[str]:
        return self._name

    @property
    def elapsed(self) -> float:
        if self._started_at is None:
            return 0.0
        return time.time() - self._started_at

    def cancel(self) -> None:
        """درخواست لغو کار فعال."""
        self._cancel = True

    def is_cancelled(self) -> bool:
        return self._cancel

    async def run(self, name: str, func: Callable[[], Awaitable[Any]]) -> Any:
        """
        func را در صف اجرا می‌کند.

        اگر کار دیگری در جریان باشد، منتظر می‌ماند و سپس اجرا می‌کند.
        """
        async with self._lock:
            self._name = name
            self._started_at = time.time()
            self._cancel = False
            try:
                return await func()
            finally:
                self._name = None
                self._started_at = None
                self._cancel = False

    def status_line(self) -> str:
        if not self.busy or not self._name:
            return "هیچ کاری در حال اجرا نیست."
        return f"کار جاری: {self._name} — {self.elapsed:.0f} ثانیه از زمان گذشته."


# یک نمونه‌ی مشترک در کل برنامه
queue = JobQueue()