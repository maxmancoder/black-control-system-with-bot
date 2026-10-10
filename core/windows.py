# -*- coding: utf-8 -*-
"""کار با پنجره‌های ویندوز: پیدا کردن، بزرگ کردن، آوردن به جلو، و انتظار."""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import time
from typing import Callable, Dict, List, Optional, Set

import config

logger = logging.getLogger(__name__)

try:
    import win32api
    import win32con
    import win32gui
    import win32process

    _HAS_WIN32 = True
except Exception:  # pragma: no cover
    _HAS_WIN32 = False
    logger.warning("pywin32 نصب نیست؛ کار با پنجره‌ها محدود می‌شود.")


# پنجره‌های سیستمی و بی‌ارزش که ربطی به برنامه‌های در حال اجرا ندارند.
# توجه: ApplicationFrameWindow را حذف نکنید — پنجره‌ی واقعی برنامه‌های
# Microsoft Store و Settings روی همین کلاس ساخته می‌شود.
_IGNORED_CLASSES = {
    "Progman",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "WorkerW",
    "Windows.UI.Core.CoreWindow",
    "DummyDWMListenerWindow",
    "ThumbnailDeviceHelperWnd",
    "EdgeUiInputTopWndClass",
    "Cua.AgentCursorOverlay",
    "PseudoConsoleWindow",
}


def _snapshot() -> Dict[int, str]:
    """نقشه‌ی handle -> عنوان برای همه‌ی پنجره‌های قابل مشاهده."""
    out: Dict[int, str] = {}
    if not _HAS_WIN32:
        return out

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        out[int(hwnd)] = win32gui.GetWindowText(hwnd) or ""
        return True

    win32gui.EnumWindows(_cb, None)
    return out


def _window_area(hwnd: int) -> int:
    rect = window_rect(hwnd)
    if not rect:
        return 0
    return max(0, rect[2] - rect[0]) * max(0, rect[3] - rect[1])


def _is_real_window(hwnd: int, min_width: int = 80, min_height: int = 24) -> bool:
    """
    پنجره‌ی واقعی است یا یکی از پنجره‌های سیستمی و بی‌ارزش.

    برنامه‌های Microsoft Store در ابتدا پنجره‌ای با عنوان خالی می‌سازند،
    پس فقط به عنوان تکیه نمی‌کنیم و اندازه را هم بررسی می‌کنیم.
    آستانه‌ها کوچک گرفته شده‌اند چون بعضی پنجره‌ها (مثل پنجره‌ی TUI در
    حالت restore شده) خیلی کوچک می‌شوند.
    """
    try:
        if not win32gui.IsWindowVisible(hwnd):
            return False
        if (win32gui.GetClassName(hwnd) or "") in _IGNORED_CLASSES:
            return False
        rect = window_rect(hwnd)
        if not rect:
            return False
        return (rect[2] - rect[0]) >= min_width and (rect[3] - rect[1]) >= min_height
    except Exception:
        return False


def window_handles() -> Set[int]:
    """handle پنجره‌هایی که می‌شود با آن‌ها کار کرد (بدون پنجره‌های سیستمی)."""
    return {h for h in _snapshot() if _is_real_window(h)}


def list_windows() -> List[Dict[str, object]]:
    if not _HAS_WIN32:
        return []
    items = []
    for hwnd, title in _snapshot().items():
        if not _is_real_window(hwnd):
            continue
        try:
            _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0
        items.append({"hwnd": hwnd, "title": title, "pid": pid})
    return items


def find_window(title_keywords: List[str]) -> Optional[int]:
    """اولین پنجره‌ای که عنوانش شامل یکی از کلیدواژه‌ها باشد."""
    snapshot = _snapshot()
    lowered = [
        (h, t.lower())
        for h, t in snapshot.items()
        if t.strip() and _is_real_window(h)
    ]
    for keyword in title_keywords:
        needle = keyword.lower()
        for hwnd, title in lowered:
            if needle in title:
                return hwnd
    return None


def maximize(hwnd: int) -> bool:
    """پنجره را maximize می‌کند تا مختصات کلیک ثابت بماند."""
    if not _HAS_WIN32:
        return False
    try:
        if not win32gui.IsWindow(hwnd):
            return False
        if ctypes.windll.user32.IsZoomed(hwnd):
            # قبلاً maximize است؛ restore+maximize لازم نیست.
            # این کار باعث پرش اندازه‌ی پنجره در هر عملیات می‌شد و
            # رندر برنامه‌های Electron (مثل OpenCode) گاهی خالی می‌ماند.
            return True
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.15)
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        return True
    except Exception:
        logger.exception("maximize ناموفق بود")
        return False


def is_foreground(hwnd: int) -> bool:
    """آیا این پنجره پنجره‌ی فعال (جلویی) است؟"""
    if not _HAS_WIN32 or not hwnd:
        return False
    try:
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


def is_window(hwnd: int) -> bool:
    if not _HAS_WIN32 or not hwnd:
        return False
    try:
        return bool(win32gui.IsWindow(hwnd))
    except Exception:
        return False


def resolve_window(hwnd: int, titles: Optional[List[str]] = None) -> Optional[int]:
    """
    بعد از اجرای یک برنامه ممکن است ویندوز پنجره را دوباره بسازد و handle عوض شود.

    اگر handle قدیمی مرده باشد، پنجره‌ای با عنوان مشابه پیدا می‌کند.
    """
    if is_window(hwnd):
        return hwnd
    if titles:
        found = find_window(titles)
        if found:
            logger.info("handle پنجره عوض شد؛ hwnd جدید: %s", found)
            return found
    return None


def focus(hwnd: int) -> bool:
    """پنجره را به جلو می‌آورد."""
    if not _HAS_WIN32:
        return False
    try:
        if not win32gui.IsWindow(hwnd):
            return False
        maximize(hwnd)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            # ویندوز گاهی اجازه‌ی جلو آوردن را نمی‌دهد؛
            # شبیه‌سازی کلیک روی نوار وظیفه راه‌حل رایج است.
            _force_foreground(hwnd)
        time.sleep(0.2)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        logger.exception("focus ناموفق بود")
        return False


def _force_foreground(hwnd: int) -> None:
    """راه‌حل جایگزین: اتصال به ترد پنجره‌ی هدف و اجبار به فوکوس."""
    fg = win32gui.GetForegroundWindow()
    cur_thread = win32api.GetCurrentThreadId()
    target_thread, _pid = win32process.GetWindowThreadProcessId(hwnd)
    fg_thread, _pid2 = win32process.GetWindowThreadProcessId(fg)

    user32 = ctypes.windll.user32
    user32.AttachThreadInput(cur_thread, target_thread, True)
    user32.AttachThreadInput(cur_thread, fg_thread, True)
    try:
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.SetActiveWindow(hwnd)
    finally:
        user32.AttachThreadInput(cur_thread, fg_thread, False)
        user32.AttachThreadInput(cur_thread, target_thread, False)


def window_rect(hwnd: int):
    if not _HAS_WIN32:
        return None
    try:
        return win32gui.GetWindowRect(hwnd)
    except Exception:
        return None


def is_responding(hwnd: int, timeout_ms: int = 500) -> bool:
    """با پیام WM_NULL بررسی می‌کند که پنجره قفل نشده باشد."""
    if not _HAS_WIN32:
        return True
    try:
        result = win32gui.SendMessageTimeout(
            hwnd, win32con.WM_NULL, 0, 0, win32con.SMTO_ABORTIFHUNG, timeout_ms
        )
        return bool(result)
    except Exception:
        return False


def wait_for_new_window(
    before: Set[int],
    titles: Optional[List[str]] = None,
    timeout: float = 20.0,
    poll: float = 0.5,
    predicate: Optional[Callable[[Dict[str, object]], bool]] = None,
) -> Optional[Dict[str, object]]:
    """
    منتظر می‌ماند تا پنجره‌ای تازه ظاهر شود.

    اگر titles داده شود، اولویت با پنجره‌ای است که عنوانش شامل آن کلیدواژه‌هاست.
    اگر چنین پنجره‌ای پیدا نشد، هر پنجره‌ی واقعی و جدیدی (با اندازه‌ی کافی)
    قبول می‌شود، چون برنامه‌های Store در ابتدا عنوان خالی دارند.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = _snapshot()
        candidates = [
            h
            for h in snapshot
            if h not in before and _is_real_window(h)
        ]

        if candidates and titles:
            needles = [t.lower() for t in titles]
            matched = [
                h for h in candidates
                if any(n in snapshot[h].lower() for n in needles)
            ]
            if matched:
                hwnd = matched[0]
                return {"hwnd": hwnd, "title": snapshot[hwnd]}

        if candidates:
            # بزرگ‌ترین پنجره‌ی جدید، محتمل‌ترین گزینه است
            hwnd = max(
                candidates,
                key=lambda h: (lambda r: (r[2] - r[0]) * (r[3] - r[1]))(
                    window_rect(h) or (0, 0, 0, 0)
                ),
            )
            entry = {"hwnd": hwnd, "title": snapshot[hwnd]}
            if predicate and not predicate(entry):
                time.sleep(poll)
                continue
            return entry

        time.sleep(poll)
    return None


def wait_until_responsive(
    hwnd: int,
    timeout: float = 30.0,
    poll: float = 1.0,
    min_wait: float = 2.0,
    titles: Optional[List[str]] = None,
) -> Optional[int]:
    """
    منتظر می‌ماند تا پنجره از حالت «در حال لود/هنگ» خارج شود.

    اگر ویندوز پنجره را از نو بسازد، handle جدید برگردانده می‌شود.
    خروجی: handle پنجره‌ی آماده یا None
    """
    start = time.time()
    while time.time() - start < min_wait:
        time.sleep(0.2)

    while time.time() - start < timeout:
        resolved = resolve_window(hwnd, titles)
        if resolved is None:
            return None
        hwnd = resolved
        if win32gui.IsWindowVisible(hwnd) and is_responding(hwnd):
            # مهلت کوتاه اضافه تا محتوای پنجره لود شود
            time.sleep(config.APP_STABLE_SECONDS)
            return resolve_window(hwnd, titles)
        time.sleep(poll)
    return None


def wait_title_stable(hwnd: int, timeout: float = 15.0, stable_for: float = 2.0) -> str:
    """منتظر می‌ماند تا عنوان پنجره (معمولاً نشانه‌ی لود کامل) ثابت شود."""
    deadline = time.time() + timeout
    last = ""
    since = time.time()
    while time.time() < deadline:
        try:
            title = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            return last
        if title != last:
            last = title
            since = time.time()
        elif time.time() - since >= stable_for and last.strip():
            return last
        time.sleep(0.4)
    return last


def _is_aumid(app_id: str) -> bool:
    """شناسه‌ی AppUserModel مثل ai.opencode.desktop — نه مسیر فایل."""
    return "." in app_id and "\\" not in app_id and "/" not in app_id and " " not in app_id


def _shortcut_by_aumid(aumid: str) -> Optional[str]:
    """
    میانبر Start Menu که این AppUserModelID را دارد پیدا می‌کند.

    بعضی برنامه‌ها (مثل OpenCode) در Get-StartApps فقط با شناسه‌ی
    AppUserModel دیده می‌شوند، نه مسیر فایل. «start» چنین شناسه‌ای را
    فایل فرضی می‌کند و اجرا نمی‌کند («The system cannot find the file»)؛
    باید میانبر واقعی‌اش را پیدا کنیم.
    """
    roots = []
    for env in ("ProgramData", "APPDATA"):
        base = os.environ.get(env, "")
        if not base:
            continue
        path = os.path.join(base, r"Microsoft\Windows\Start Menu\Programs")
        if os.path.isdir(path):
            roots.append(path)

    try:
        import win32com.client

        shell = win32com.client.Dispatch("Shell.Application")
        want = aumid.lower()
        for root in roots:
            for base, _dirs, files in os.walk(root):
                folder = None
                for fn in files:
                    if not fn.lower().endswith(".lnk"):
                        continue
                    if folder is None:
                        folder = shell.NameSpace(base)
                    if folder is None:
                        break
                    item = folder.ParseName(fn)
                    if item is None:
                        continue
                    try:
                        got = item.ExtendedProperty("System.AppUserModel.ID") or ""
                    except Exception:
                        continue
                    if str(got).lower() == want:
                        return os.path.join(base, fn)
    except Exception:
        logger.exception("جست‌وجوی میانبر برای %s ناموفق بود", aumid)
    return None


def launch(app_id: str) -> None:
    """یک برنامه‌ی نصب‌شده را اجرا می‌کند.

    app_id یکی از این‌هاست: مسیر فایل/میانبر، شناسه‌ی AppUserModel
    (مثل ai.opencode.desktop) یا شناسه‌ی اپ Microsoft Store.
    """
    logger.info("اجرای برنامه: %s", app_id)
    if app_id.startswith("shell:"):
        subprocess.Popen(["explorer.exe", app_id])
    elif app_id.endswith(":") or ("!" in app_id and "\\" not in app_id):
        # قالب شناسه‌ی اپ‌های استور مثل PackageFamilyName!App
        subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + app_id])
    elif _is_aumid(app_id):
        # شناسه‌ی خالی مثل ai.opencode.desktop را start نمی‌تواند اجرا کند؛
        # میانبر مربوط را در Start Menu پیدا می‌کنیم و همان را اجرا می‌کنیم.
        lnk = _shortcut_by_aumid(app_id)
        if lnk:
            subprocess.Popen(["cmd", "/c", "start", "", lnk])
        else:
            # شناسه‌ی ناشناخته؛ رفتار قبلی (بدون دیالوگ احتمالی explorer)
            subprocess.Popen(["cmd", "/c", "start", "", app_id])
    else:
        subprocess.Popen(["cmd", "/c", "start", "", app_id])


def launch_command(cmd: List[str]) -> None:
    """اجرای یک دستور مستقیم (مثلاً باز کردن opencode)."""
    logger.info("اجرای دستور: %s", cmd)
    subprocess.Popen(cmd, shell=False)