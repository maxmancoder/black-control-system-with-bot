# -*- coding: utf-8 -*-
"""
لیست برنامه‌های نصب‌شده، فقط از منابع واقعی سیستم.

منبع اصلی: PowerShell cmdlet ‎Get-StartApps‎
منبع کمکی: میانبرهای پوشه‌ی Start Menu
هیچ‌وقت اجرا از روی متن آزاد انجام نمی‌شود؛ فقط از روی شناسه‌ای که
از همین منابع آمده.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from typing import Dict, List, Optional

import config

logger = logging.getLogger(__name__)

_PS_SCRIPT = (
    "Get-StartApps | "
    "Select-Object Name, AppID | "
    "ForEach-Object { \"$($_.Name)`t$($_.AppID)\" }"
)


def _run_powershell(script: str, timeout: int = 45) -> str:
    """اجرای اسکریپت پاورشل و برگرداندن stdout."""
    proc = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy", "Bypass",
            "-Command", script,
        ],
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"PowerShell failed: {err[:400]}")
    return proc.stdout.decode("utf-8", errors="replace")


def _collect_start_apps() -> List[Dict[str, str]]:
    out = _run_powershell(_PS_SCRIPT)
    apps: Dict[str, Dict[str, str]] = {}
    for line in out.splitlines():
        if "\t" not in line:
            continue
        name, app_id = line.split("\t", 1)
        name = name.strip()
        app_id = app_id.strip()
        if not name or not app_id:
            continue
        apps.setdefault(app_id, {"name": name, "app_id": app_id})
    return list(apps.values())


def _start_menu_dirs() -> List[str]:
    program_data = os.environ.get("ProgramData", r"C:\ProgramData")
    app_data = os.environ.get("APPDATA", "")
    dirs = [
        os.path.join(program_data, r"Microsoft\Windows\Start Menu\Programs"),
    ]
    if app_data:
        dirs.append(os.path.join(app_data, r"Microsoft\Windows\Start Menu\Programs"))
    return [d for d in dirs if os.path.isdir(d)]


def _collect_shortcuts() -> List[Dict[str, str]]:
    """میانبرهای Start Menu به‌عنوان منبع کمکی."""
    found: Dict[str, Dict[str, str]] = {}
    for root_dir in _start_menu_dirs():
        for base, _dirs, files in os.walk(root_dir):
            for fn in files:
                if not fn.lower().endswith((".lnk", ".url")):
                    continue
                full = os.path.join(base, fn)
                display = os.path.splitext(fn)[0].replace("&", "and")
                found.setdefault(full, {"name": display, "app_id": full})
    return list(found.values())


def _cache_age() -> Optional[float]:
    if not os.path.exists(config.APPS_CACHE_FILE):
        return None
    return time.time() - os.path.getmtime(config.APPS_CACHE_FILE)


def _build_index_sync() -> Dict[str, object]:
    apps = _collect_start_apps()
    try:
        shortcuts = _collect_shortcuts()
    except Exception:
        logger.exception("خواندن میانبرهای Start Menu ناموفق بود")
        shortcuts = []

    # میانبرها فقط وقتی اضافه می‌شوند که همان نام از Get-StartApps نیامده باشد،
    # وگرنه هر برنامه دو بار در لیست می‌آید.
    names_seen = {_norm(a["name"]) for a in apps}
    for sc in shortcuts:
        key = _norm(sc["name"])
        if key in names_seen:
            continue
        names_seen.add(key)
        apps.append(sc)

    apps.sort(key=lambda a: a["name"].lower())
    return {"built_at": time.time(), "apps": apps}


def _norm(name: str) -> str:
    """یکسان‌سازی حروف فارسی/عربی و فاصله‌ها برای حذف تکراری."""
    s = name.strip().lower()
    for a, b in (("\u064a", "\u06cc"), ("\u0643", "\u06a9"), ("\u200c", " ")):
        s = s.replace(a, b)
    return " ".join(s.split())


async def _build_index() -> Dict[str, object]:
    data = await asyncio.to_thread(_build_index_sync)
    await _write_cache(data)
    return data


async def _write_cache(data: Dict[str, object]) -> None:
    def _write():
        tmp = config.APPS_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, config.APPS_CACHE_FILE)

    await asyncio.to_thread(_write)


async def load_apps(max_age_seconds: Optional[float] = None) -> List[Dict[str, str]]:
    """لیست برنامه‌ها را از کش می‌خواند و در صورت کهنگی، تازه می‌کند."""
    if max_age_seconds is None:
        max_age_seconds = 3600

    age = _cache_age()
    if age is not None and age < max_age_seconds:
        def _read():
            with open(config.APPS_CACHE_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)

        try:
            data = await asyncio.to_thread(_read)
            return data.get("apps", [])
        except Exception:
            logger.exception("خواندن کش برنامه‌ها ناموفق بود؛ کش تازه ساخته می‌شود")

    data = await _build_index()
    return data.get("apps", [])


async def refresh_apps() -> List[Dict[str, str]]:
    return (await _build_index()).get("apps", [])


def search(apps: List[Dict[str, str]], query: str, limit: int = 200) -> List[Dict[str, str]]:
    """جست‌وجوی ساده‌ی نام برنامه (با یکسان‌سازی حروف فارسی)."""
    q = _norm(query)
    if not q:
        return apps[:limit]
    exact, starts, contains = [], [], []
    for a in apps:
        n = _norm(a["name"])
        if n == q:
            exact.append(a)
        elif n.startswith(q):
            starts.append(a)
        elif q in n:
            contains.append(a)
    out: List[Dict[str, str]] = []
    seen = set()
    for group in (exact, starts, contains):
        for a in group:
            if a["app_id"] in seen:
                continue
            seen.add(a["app_id"])
            out.append(a)
            if len(out) >= limit:
                return out
    return out


# ------------------------------------------------------------------ پراستفاده
def _usage_path() -> str:
    return config.APPS_USAGE_FILE


def _read_usage() -> Dict[str, int]:
    if not os.path.exists(_usage_path()):
        return {}
    try:
        with open(_usage_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _write_usage(data: Dict[str, int]) -> None:
    try:
        with open(_usage_path(), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("ذخیره‌ی آمار استفاده ناموفق بود")


def bump_usage(app_id: str) -> None:
    data = _read_usage()
    data[app_id] = data.get(app_id, 0) + 1
    _write_usage(data)


def _usage_counts() -> Dict[str, int]:
    return _read_usage()


def get_favorites(apps: List[Dict[str, str]], limit: int = 5) -> List[Dict[str, str]]:
    """برنامه‌های پراستفاده، به ترتیب تعداد اجرا."""
    counts = _read_usage()
    if not counts:
        return []
    by_id = {a["app_id"]: a for a in apps}
    out: List[Dict[str, str]] = []
    for app_id, _count in sorted(counts.items(), key=lambda kv: -kv[1]):
        if app_id in by_id:
            out.append(by_id[app_id])
        if len(out) >= limit:
            break
    return out