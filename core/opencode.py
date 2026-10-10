# -*- coding: utf-8 -*-
"""
اتوماسیون OpenCode: وارد کردن پرامپت، ارسال، و تشخیص پایان کار.

سه مرحله دارد:
  ۱) فوکوس روی پنجره + کلیک روی کادر چت + چسباندن با کلیپ‌بورد (Ctrl+V)
  ۲) ارسال با Enter و تأیید اینکه واقعاً ارسال شد
  ۳) تشخیص پایان کار با ترکیبی از: تطبیق تصویر دکمه‌ی توقف، ثابت ماندن
     ناحیه‌ی چت، و تشخیص صفحه‌ای که منتظر تأیید انسان مانده است.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import pyautogui
import pyperclip
from PIL import Image

import config
from core import imaging, windows

logger = logging.getLogger(__name__)


class Cancelled(Exception):
    """کاربر خواسته کار متوقف شود."""


def _pause(seconds: float) -> None:
    """
    مکث کوتاه بین کارهای اتوماسیون.

    یک تابع جدا به‌جای time.sleep مستقیم، تا تست‌ها بتوانند بدون دست زدن به
    ماژول سراسری time، آن را بی‌اثر کنند.
    """
    time.sleep(seconds)


class PromptFailure(Exception):
    """پرامپت نتوانست وارد یا ارسال شود."""


# ---------------------------------------------------------------- وضعیت اجرا
_state: Dict[str, Any] = {
    "running": False,
    "hwnd": None,
    "prompt": "",
    "baseline_input": None,
    "baseline_chat": None,
    "cancel": False,
    "started": 0.0,
}


def reset_state() -> None:
    _state.update(
        running=False, hwnd=None, prompt="", baseline_input=None,
        baseline_chat=None, cancel=False, started=0.0,
    )


def is_running() -> bool:
    return bool(_state.get("running"))


def current_prompt() -> str:
    return str(_state.get("prompt", ""))


def request_cancel() -> None:
    _state["cancel"] = True


def _cancelled() -> bool:
    return bool(_state.get("cancel"))


# ---------------------------------------------------------------- پنجره
def _find_window(launch_if_missing: bool = True) -> int:
    hwnd = windows.find_window(config.OPENCODE_WINDOW_TITLES)
    if hwnd:
        return hwnd

    if not launch_if_missing:
        raise PromptFailure("پنجره‌ی OpenCode پیدا نشد.")

    before = windows.window_handles()
    logger.info("پنجره‌ی OpenCode باز نیست؛ تلاش برای اجرا")
    windows.launch_command(config.OPENCODE_LAUNCH_COMMAND)

    found = windows.wait_for_new_window(before, timeout=30, poll=0.5)
    if not found:
        raise PromptFailure(
            "پنجره‌ی OpenCode پیدا نشد و اجرای آن هم جواب نداد. "
            "دستور اجرا را در فایل config.py بررسی کن."
        )
    hwnd = found["hwnd"]
    windows.wait_title_stable(hwnd, timeout=15)
    return windows.find_window(config.OPENCODE_WINDOW_TITLES) or hwnd


def _focus_window(hwnd: int) -> None:
    if not windows.focus(hwnd):
        raise PromptFailure("نتوانستم پنجره‌ی OpenCode را به جلو بیاورم.")
    _pause(0.6)


def _input_box_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """
    کادر نوشتن پرامپت در مختصات صفحه، از روی نسبت‌های تنظیم‌شده.

    همه‌ی کلیک‌ها و همه‌ی اندازه‌گیری‌های «کادر ورودی» باید از همین
    مستطیل بیایند، وگرنه ممکن است کلیک روی بخش اشتباه رابط بیفتد یا
    تغییر متن در مقایسه با یک ناحیه‌ی غلط گم شود.
    """
    rect = windows.window_rect(hwnd)
    if rect is None:
        return None
    left, top, right, bottom = rect
    w = right - left
    h = bottom - top
    l, t, r, b = config.OPENCODE_INPUT_REGION
    return (
        left + int(w * l),
        top + int(h * t),
        left + int(w * r),
        top + int(h * b),
    )


def _inside_box(point, box) -> bool:
    """آیا نقطه داخل کادر ورودی هست؟"""
    if not point or not box:
        return False
    x, y = point
    x0, y0, x1, y1 = box
    return x0 <= x <= x1 and y0 <= y <= y1


def _uia_input_point(hwnd: int) -> Optional[Tuple[int, int]]:
    """
    نقطه‌ی کادر ورودی از طریق UI Automation.

    در برنامه‌ی دسکتاپ OpenCode (Electron) این روش عنصرهای عمومی مثل
    «Text» را برمی‌گرداند که می‌تواند کادر پیام‌ها باشد نه کادر ورودی.
    برای همین نتیجه‌اش فقط وقتی استفاده می‌شود که واقعاً داخل کادر
    ورودی تنظیم‌شده باشد.
    """
    try:
        from pywinauto import Desktop  # type: ignore

        win = Desktop(backend="uia").window(handle=hwnd)
        for ctrl in win.descendants():
            try:
                ctype = ctrl.friendly_class_name() or ""
            except Exception:
                continue
            if ctype in ("Edit", "Document", "Text"):
                r = ctrl.rectangle()
                if r.width() > 100 and r.height() > 10:
                    pt = r.mid_point()
                    return (int(pt.x), int(pt.y))
    except Exception:
        logger.info("UI Automation کادر ورودی را پیدا نکرد.")
    return None


def _input_point(hwnd: int) -> Tuple[int, int]:
    """نقطه‌ی اصلی کلیک روی کادر ورودی (مرکز کادر تنظیم‌شده)."""
    box = _input_box_rect(hwnd)
    if box is None:
        raise PromptFailure("مختصات پنجره‌ی OpenCode در دسترس نیست.")
    x0, y0, x1, y1 = box
    return ((x0 + x1) // 2, (y0 + y1) // 2)


def _input_click_points(hwnd: int) -> List[Tuple[int, int]]:
    """
    نقاطی که برای فوکوس روی کادر ورودی امتحان می‌شوند.

    ترتیب عمدی است: اول مرکز کادر تنظیم‌شده چون قابل اتکاست، بعد نقطه‌ی
    UI Automation ولی فقط اگر داخل همان کادر باشد، و در آخر چند نقطه‌ی
    جایگزین. هیچ نقطه‌ای بیرون از کادر برگردانده نمی‌شود.
    """
    points: List[Tuple[int, int]] = []
    box = _input_box_rect(hwnd)
    if box is None:
        return points

    def add(point) -> None:
        if point and _inside_box(point, box) and point not in points:
            points.append(point)

    rect = windows.window_rect(hwnd)
    left, top, right, bottom = rect
    w = right - left
    h = bottom - top

    x0, y0, x1, y1 = box
    add(((x0 + x1) // 2, (y0 + y1) // 2))

    add(_uia_input_point(hwnd))

    for fx, fy in config.OPENCODE_INPUT_FALLBACKS:
        add((left + int(w * fx), top + int(h * fy)))

    return points


def _set_clipboard(text: str) -> None:
    """کلیپ‌بورد را پر می‌کند و مطمئن می‌شود واقعاً نوشته شده است."""
    pyperclip.copy(text)
    _pause(0.15)
    if pyperclip.paste() != text:
        # اگر کلیپ‌بورد قفل باشد ممکن است بی‌سروصدا شکست بخورد
        _pause(0.3)
        pyperclip.copy(text)
        _pause(0.15)


def _paste_prompt(hwnd: int, prompt: str) -> str:
    """
    کلیک روی کادر چت و چسباندن متن با کلیپ‌بورد (نه تایپ مستقیم).

    برخلاف نسخه‌ی قبلی، اینجا نتیجه واقعاً اندازه‌گیری می‌شود: بعد از هر
    تلاش، ناحیه‌ی کادر دوباره عکس‌برداری و با قبلش مقایسه می‌شود. اگر
    تغییری نکند یعنی چسباندن انجام نشده و نقطه یا روش بعدی امتحان می‌شود.

    خروجی: توصیف روشی که جواب داد (برای لاگ)، یا رشته‌ی خالی.
    """
    _set_clipboard(prompt)

    points = _input_click_points(hwnd)
    if not points:
        raise PromptFailure("مختصات کادر ورودی OpenCode پیدا نشد.")

    region = _input_region(hwnd)

    def snapshot():
        return imaging.crop_by_ratio(imaging.grab_screen(), region)

    # ترتیب تلاش‌ها: نقاط مختلف با Ctrl+V، و در آخر Ctrl+Shift+V که
    # بعضی رابط‌ها فقط با آن کار می‌کنند.
    attempts = [(point, ("ctrl", "v")) for point in points]
    if points:
        attempts.append((points[0], ("ctrl", "shift", "v")))

    for point, keys in attempts:
        combo = "+".join(keys)
        logger.info("تلاش چسباندن در %s با %s", point, combo)
        before = snapshot()

        pyautogui.click(point[0], point[1])
        _pause(0.4)
        pyautogui.hotkey(*keys)
        _pause(0.9)

        after = snapshot()
        changed = imaging.changed_pixels(before, after)
        if changed >= config.OPENCODE_PASTE_MIN_PIXELS:
            logger.info(
                "چسباندن موفق بود (%d پیکسل تغییر کرد)", changed
            )
            return f"{point} / {combo}"

        logger.info("تغییر محسوسی نبود (%d پیکسل)؛ روش بعدی.", changed)

    return ""


def _send() -> None:
    pyautogui.press("enter")
    _pause(1.0)


def _send_ctrl_enter() -> None:
    """بعضی حالت‌های رابط فقط با Ctrl+Enter ارسال می‌کنند."""
    pyautogui.hotkey("ctrl", "enter")
    _pause(1.0)


# ---------------------------------------------------------------- ناحیه‌ها
def _screen_size() -> Tuple[int, int]:
    return pyautogui.size()


def _input_region(hwnd: int):
    """
    ناحیه‌ی کادر نوشتن پرامپت، به‌صورت نسبت از کل صفحه.

    نکته‌ی مهم: این ناحیه «کادر ورودی» است، نه کل پنجره. قبلاً کل پنجره
    برمی‌گشت که برای پنجره‌ی maximize یعنی تمام صفحه؛ آن‌وقت تغییر چند
    کلمه متن در مقایسه با دو میلیون پیکسل گم می‌شد و تشخیص چسباندن همیشه
    شکست می‌خورد.
    """
    rect = windows.window_rect(hwnd)
    if rect is None:
        return None
    sw, sh = _screen_size()
    left, top, right, bottom = rect
    w = right - left
    h = bottom - top
    l, t, r, b = config.OPENCODE_INPUT_REGION
    return (
        max(0.0, (left + w * l) / sw),
        max(0.0, (top + h * t) / sh),
        min(1.0, (left + w * r) / sw),
        min(1.0, (top + h * b) / sh),
    )


def _chat_region(hwnd: int):
    """ناحیه‌ی نمایش پاسخ‌ها، برای تشخیص ثابت ماندن صفحه."""
    rect = windows.window_rect(hwnd)
    if rect is None:
        return None
    sw, sh = _screen_size()
    left, top, right, bottom = rect
    return (
        max(0.0, left / sw),
        max(0.0, top / sh),
        min(1.0, right / sw),
        min(1.0, (top + (bottom - top) * 0.70) / sh),
    )


def _input_cleared(hwnd: int) -> bool:
    """آیا کادر ورودی بعد از ارسال خالی شده است؟"""
    baseline = _state.get("baseline_input")
    if baseline is None:
        return False
    screen = imaging.grab_screen()
    region = _input_region(hwnd)
    current = imaging.crop_by_ratio(screen, region) if region else None
    if current is None:
        return False
    # تغییر محسوس یعنی متن از کادر رفته و پیام ارسال شده
    changed = imaging.changed_pixels(baseline, current)
    return changed >= config.OPENCODE_CLEAR_MIN_PIXELS


def _window_alive(hwnd: int) -> bool:
    return windows.is_window(hwnd)


# ---------------------------------------------------------------- چرخه‌ی اصلی
async def send_prompt(
    prompt: str,
    bot,
    chat_id: int,
    should_cancel: Callable[[], bool],
) -> Tuple[str, float]:
    """
    کل چرخه‌ی اتوماسیون را اجرا می‌کند.

    خروجی: (نتیجه، مدت اجرا به ثانیه)
    نتیجه یکی از: completed | timeout | cancelled | failed
    """
    async def notify(text: str) -> None:
        await bot.send_message(chat_id=chat_id, text=text)

    async def shot(caption: str) -> None:
        img = await imaging.grab_screen_async()
        await imaging.send_screenshot_to(bot, chat_id, img, caption)

    reset_state()
    _state.update(running=True, prompt=prompt, started=time.time())
    started = time.time()

    def _check_cancel():
        if should_cancel():
            raise Cancelled()

    try:
        # ---------------- مرحله ۱: پیدا کردن و فوکوس پنجره
        await notify("در حال پیدا کردن پنجره‌ی OpenCode…")
        hwnd = await asyncio.to_thread(_find_window)
        _state["hwnd"] = hwnd
        _check_cancel()

        await asyncio.to_thread(_focus_window, hwnd)

        await notify(
            "پنجره پیدا و فوکوس شد. اسکرین‌شات را می‌فرستم تا مطمئن شوی "
            "همه‌چیز درست است. اگر نبود، با /stop متوقف کن."
        )
        await asyncio.sleep(0.5)
        await shot("وضعیت دسکتاپ پیش از ارسال پرامپت.")

        _check_cancel()

        # ---------------- مرحله ۲: وارد کردن پرامپت
        await notify("در حال وارد کردن پرامپت…")
        paste_method = await asyncio.to_thread(_paste_prompt, hwnd, prompt)
        if not paste_method:
            # هیچ روشی جواب نداد؛ ادامه دادن بی‌فایده است چون Enter
            # روی کادر خالی فقط یک خط جدید می‌سازد.
            await notify("چسباندن متن انجام نشد. وضعیت فعلی را می‌فرستم:")
            await shot(
                "⚠️ نتوانستم متن را داخل کادر OpenCode بگذارم. "
                "شاید کادر در جای دیگری است."
            )
            raise PromptFailure(
                "چسباندن متن در کادر OpenCode انجام نشد.\n"
                "اگر رابط OpenCode عوض شده، نسبت‌های OPENCODE_INPUT_REGION و "
                "OPENCODE_INPUT_FALLBACKS را در config.py اصلاح کنید."
            )
        logger.info("پرامپت با روش %s چسبانده شد.", paste_method)
        await asyncio.sleep(0.5)

        screen = await imaging.grab_screen_async()
        _state["baseline_input"] = _crop(screen, _input_region(hwnd))
        _state["baseline_chat"] = _crop(screen, _chat_region(hwnd))

        # ---------------- مرحله ۳: ارسال با تأیید
        _check_cancel()
        await notify("در حال ارسال…")

        sent = False
        for attempt in range(3):
            await asyncio.to_thread(_send)
            await asyncio.sleep(1.4)
            if await asyncio.to_thread(_input_cleared, hwnd):
                sent = True
                break
            check_screen = await imaging.grab_screen_async()
            if await asyncio.to_thread(
                imaging.find_template,
                check_screen,
                config.OPENCODE_TEMPLATE_STOP,
                threshold=0.8,
            )[0]:
                sent = True
                break
            # اگر دکمه‌ی توقف ظاهر نشده ولی کادر هم خالی نشده، ممکن است
            # Enter به‌جای ارسال، خط جدید ساخته باشد؛ با Ctrl+Enter امتحان می‌کنیم.
            if attempt == 1:
                logger.info("Enter جواب نداد؛ با Ctrl+Enter امتحان می‌کنم.")
                await asyncio.to_thread(_send_ctrl_enter)
                await asyncio.sleep(1.4)
                if await asyncio.to_thread(_input_cleared, hwnd):
                    sent = True
                    break
            logger.info("تلاش %d برای ارسال ناموفق بود", attempt + 1)

        if not sent:
            await notify("ارسال تأیید نشد. وضعیت فعلی را می‌فرستم:")
            await shot("⚠️ نتوانستم مطمئن شوم که پرامپت ارسال شده است.")
            raise PromptFailure(
                "متن داخل کادر رفت ولی ارسال نشد.\n"
                "اسکرین‌شات را ببین: اگر متن هنوز داخل کادر است، "
                "یک بار دستی Enter بزن."
            )

        await notify("✅ پرامپت ارسال شد. منتظر پایان کار می‌مانم…")

        # ---------------- مرحله ۴: تشخیص پایان
        outcome = await _wait_for_completion(hwnd, notify, shot, should_cancel, started)
        return outcome, time.time() - started

    finally:
        _state["running"] = False


def _crop(screen: Image.Image, region) -> Optional[Image.Image]:
    if not region:
        return None
    return imaging.crop_by_ratio(screen, region)


async def _wait_for_completion(
    hwnd: int,
    notify: Callable[[str], Any],
    shot: Callable[[str], Any],
    should_cancel: Callable[[], bool],
    started: float,
) -> str:
    chat_region = _chat_region(hwnd)

    # آیا قالب دکمه‌ی توقف موجود است؟ اگر نباشد، تکیه بر «نبودِ دکمه» برای
    # اعلام پایان کار بی‌معنی است و باید فقط از ثابت ماندن صفحه استفاده کرد.
    has_stop_template = await asyncio.to_thread(
        imaging.template_available, config.OPENCODE_TEMPLATE_STOP
    )
    if not has_stop_template:
        logger.info(
            "قالب دکمه‌ی توقف موجود نیست؛ تشخیص پایان کار فقط با ثبات صفحه انجام می‌شود."
        )

    deadline = started + config.OPENCODE_TIMEOUT
    baseline = _state.get("baseline_chat")
    last_change = time.time()
    stall_reported = False
    last_report = 0.0

    while time.time() < deadline:
        if should_cancel():
            await stop(hwnd)
            return "cancelled"

        if not _window_alive(hwnd):
            await notify(
                "❌ پنجره‌ی OpenCode بسته شد یا برنامه کرش کرد. کار متوقف شد."
            )
            return "failed"

        screen = await imaging.grab_screen_async()
        current = _crop(screen, chat_region)

        # روش ۱ (قوی‌ترین): دکمه‌ی توقف به حالت ارسال برگشته یعنی کار تمام است.
        # فقط وقتی قالب موجود است.
        if has_stop_template:
            stop_visible = await asyncio.to_thread(
                imaging.find_template,
                screen,
                config.OPENCODE_TEMPLATE_STOP,
                threshold=0.8,
            )[0]
            if not stop_visible:
                await notify("✅ کار تمام شد.")
                return "completed"

        # روش ۲: ثبات ناحیه‌ی چت
        if current is not None and baseline is not None:
            diff = imaging.region_diff_ratio(baseline, current)
            if diff > config.OPENCODE_STABILITY_THRESHOLD:
                baseline = current
                last_change = time.time()
                stall_reported = False
            else:
                idle = time.time() - last_change

                if has_stop_template:
                    # دکمه‌ی توقف هنوز هست ولی صفحه ثابت است:
                    # به احتمال زیاد منتظر تأیید انسان است
                    if idle >= config.OPENCODE_STALL_SECONDS and not stall_reported:
                        stall_reported = True
                        await notify(
                            "⚠️ صفحه مدتی ثابت مانده ولی کار تمام نشده. "
                            "احتمالاً OpenCode منتظر تأیید اجرای دستور یا ویرایش "
                            "فایل است.\nاسکرین‌شات را می‌فرستم؛ از دکمه‌های تأیید/رد "
                            "استفاده کن یا با /stop متوقف کن."
                        )
                        await shot("OpenCode منتظر تأیید شماست.")
                        return "awaiting_approval"
                else:
                    # بدون قالب، تنها راه این است که صفحه مدتی هیچ تغییری نکند
                    if idle >= config.OPENCODE_IDLE_COMPLETE_SECONDS:
                        await notify(
                            "✅ به نظر می‌رسد کار تمام شده (صفحه دیگر تغییر نمی‌کند)."
                        )
                        return "completed"

        # گزارش دوره‌ای
        now = time.time()
        if now - last_report >= 60:
            last_report = now
            remaining = int(deadline - now) // 60
            await notify(f"⏳ هنوز در حال اجراست. حدود {remaining} دقیقه وقت باقی است.")

        await asyncio.sleep(config.OPENCODE_POLL_INTERVAL)

    await notify(
        "⏱️ سقف زمانی تمام شد ولی کار هنوز در حال اجراست. "
        "با /stop می‌توانی متوقفش کنی."
    )
    return "timeout"


async def resume_watch(bot, chat_id: int) -> str:
    """
    بعد از تأیید یا رد یک درخواست، دوباره منتظر پایان کار می‌مانیم.

    بدون این، بعد از زدن دکمه‌ی «تأیید» دیگر هیچ خبری به کاربر نمی‌رسید.
    """
    hwnd = _state.get("hwnd")
    if not hwnd or not _window_alive(hwnd):
        return "failed"

    async def notify(text: str) -> None:
        await bot.send_message(chat_id=chat_id, text=text)

    async def shot(caption: str) -> None:
        img = await imaging.grab_screen_async()
        await imaging.send_screenshot_to(bot, chat_id, img, caption)

    return await _wait_for_completion(
        hwnd, notify, shot, lambda: False, time.time()
    )


# ---------------------------------------------------------------- کنترل دستی
async def approve(hwnd: Optional[int] = None) -> Tuple[bool, str]:
    """تأیید درخواست معلق OpenCode."""
    def _do():
        target = hwnd or _state.get("hwnd")
        if not target:
            return False, "اتوماسیون فعالی وجود ندارد."
        if not windows.focus(target):
            return False, "نتوانستم پنجره را فوکوس کنم."
        pyautogui.press("enter")
        _pause(0.5)
        return True, "تأیید ارسال شد."

    return await asyncio.to_thread(_do)


async def reject(hwnd: Optional[int] = None) -> Tuple[bool, str]:
    """رد درخواست معلق OpenCode."""
    def _do():
        target = hwnd or _state.get("hwnd")
        if not target:
            return False, "اتوماسیون فعالی وجود ندارد."
        if not windows.focus(target):
            return False, "نتوانستم پنجره را فوکوس کنم."
        pyautogui.press("escape")
        _pause(0.4)
        return True, "درخواست رد شد."

    return await asyncio.to_thread(_do)


async def stop(hwnd: Optional[int] = None) -> Tuple[bool, str]:
    """توقف کار جاری با کلید Esc."""
    def _do():
        target = hwnd or _state.get("hwnd")
        if not target:
            return False, "کار فعالی وجود ندارد."
        windows.focus(target)
        pyautogui.press("escape")
        _pause(0.4)
        return True, "کلید Esc ارسال شد."

    return await asyncio.to_thread(_do)


async def capture_final(
    bot, chat_id: int, hwnd: Optional[int] = None, extra_shots: Optional[int] = None
) -> None:
    """اسکرین‌شات نهایی + در صورت خواست، چند عکت دیگر با اسکرول به پایین."""
    n = config.OPENCODE_SEND_MORE_SHOTS if extra_shots is None else extra_shots
    target = hwnd or _state.get("hwnd")
    if target:
        await asyncio.to_thread(windows.focus, target)
        await asyncio.sleep(0.4)

    img = await imaging.grab_screen_async()
    await imaging.send_screenshot_to(bot, chat_id, img, "📸 وضعیت نهایی OpenCode.")

    # اسکرول باید روی خودِ پنجره انجام شود، نه هرجای صفحه
    rect = await asyncio.to_thread(windows.window_rect, target) if target else None
    scroll_xy = None
    if rect:
        scroll_xy = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)

    for i in range(max(0, n)):
        if scroll_xy:
            pyautogui.moveTo(*scroll_xy)
        pyautogui.scroll(-400)
        await asyncio.sleep(0.7)
        img = await imaging.grab_screen_async()
        await imaging.send_screenshot_to(
            bot, chat_id, img, f"ادامه‌ی خروجی ({i + 1}/{n})."
        )
    if n and scroll_xy:
        pyautogui.moveTo(*scroll_xy)
        pyautogui.scroll(800)