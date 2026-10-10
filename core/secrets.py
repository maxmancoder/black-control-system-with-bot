# -*- coding: utf-8 -*-
"""
نگهداری کلیدهای API.

کلیدها داخل کد نوشته نمی‌شوند. اول از متغیر محیطی خوانده می‌شوند و اگر
آنجا نبود، از همین فایل که در پوشه‌ی data ساخته می‌شود.

نگهداری در فایل به این دلیل است که بتوانی کلید را از داخل خود تلگرام
(بدون دسترسی به خط فرمان) وارد یا عوض کنی. پیامی که کلید را می‌فرستی
بعد از ذخیره پاک می‌شود تا در تاریخچه‌ی چت نماند.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Dict, Optional

import config

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cache: Optional[Dict[str, str]] = None


def _path() -> str:
    return os.path.join(config.DATA_DIR, "secrets.json")


def _load() -> Dict[str, str]:
    global _cache
    if _cache is not None:
        return _cache
    data: Dict[str, str] = {}
    path = _path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                data = {str(k): str(v) for k, v in loaded.items()}
        except Exception:
            logger.exception("خواندن secrets.json ناموفق بود")
    _cache = data
    return data


def _save(data: Dict[str, str]) -> None:
    global _cache
    path = _path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        # روی ویندوز دسترسی فایل را به کاربر جاری محدود می‌کنیم
        _restrict_to_user(path)
        _cache = data
    except Exception:
        logger.exception("ذخیره‌ی secrets.json ناموفق بود")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _restrict_to_user(path: str) -> None:
    """حذف دسترسی گروه‌های دیگر (Users/Everyone) از فایل کلیدها."""
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        return
    try:
        import subprocess

        user = os.environ.get("USERNAME", "")
        domain = os.environ.get("USERDOMAIN", "")
        account = f"{domain}\\{user}" if domain else user
        subprocess.run(
            ["icacls", path, "/inheritance:r", "/grant:r", f"{account}:(F)"],
            capture_output=True,
            timeout=20,
        )
    except Exception:
        logger.debug("تنظیم دسترسی فایل کلیدها ناموفق بود", exc_info=True)


def get_key(provider_name: str) -> str:
    """کلید ذخیره‌شده‌ی یک سرویس (اگر وجود داشته باشد)."""
    return _load().get(provider_name.strip().lower(), "")


def set_key(provider_name: str, key: str) -> bool:
    name = provider_name.strip().lower()
    key = key.strip()
    if not name or not key:
        return False
    with _lock:
        data = dict(_load())
        data[name] = key
        _save(data)
    logger.info("کلید سرویس %s ذخیره شد.", name)
    return True


def clear_key(provider_name: str) -> bool:
    name = provider_name.strip().lower()
    with _lock:
        data = dict(_load())
        if name not in data:
            return False
        data.pop(name, None)
        _save(data)
    logger.info("کلید سرویس %s پاک شد.", name)
    return True


def masked(key: str) -> str:
    """نمایش امن کلید: فقط چهار کاراکتر آخر."""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{'*' * 6}{key[-4:]}"


def has_key(provider_name: str) -> bool:
    return bool(get_key(provider_name))


def stored_providers() -> list:
    return sorted(_load().keys())


def reset_cache() -> None:
    global _cache
    _cache = None