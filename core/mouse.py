# -*- coding: utf-8 -*-
"""
کنترل ماوس و کیبورد برای حالت «کنترل ماوس» (دستور /mouse).

این ماژول عمداً همگام (بدون await) است؛ main آن را داخل asyncio.to_thread
صدا می‌زند تا حلقه‌ی رویداد تلگرام حین حرکت ماوس نخوابد.

نکته‌ی امنیتی: همه‌ی فراخوانی‌ها از هندلرهای owner_only در main می‌آیند؛
اینجا عمداً چک دسترسی نیست چون لایه‌ی امنیت در همان لایه‌ی بالاتر است.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional, Tuple

import pyautogui
import pyperclip
from PIL import Image, ImageDraw

import config

logger = logging.getLogger(__name__)

# مجموعه‌های معتبر برای اعتبارسنجی callback_data در main؛ اینجا نگه داشته
# می‌شوند تا «دکمه‌ی ناشناخته» قبل از رسیدن به pyautogui رد شود.
MOVES = {"up", "down", "left", "right"}
CLICKS = {"left", "right", "double", "shift"}
KEYS = {"enter", "esc", "tab", "space", "backspace", "delete", "f5"}

# ترکیب‌های Ctrl/Alt برای دکمه‌های کنترلی (کلیدهای کوچک، چون hotkey آن‌ها را
# خودش Shift نمی‌کشد). alt+left/right همان «صفحه قبل/بعد» مرورگر است و
# alt+tab سویچ بین پنجره‌های باز ویندوز.
COMBOS: Dict[str, Tuple[str, ...]] = {
    "ctrl+a": ("ctrl", "a"),
    "ctrl+c": ("ctrl", "c"),
    "ctrl+v": ("ctrl", "v"),
    "ctrl+x": ("ctrl", "x"),
    "ctrl+z": ("ctrl", "z"),
    "alt+left": ("alt", "left"),
    "alt+right": ("alt", "right"),
    "alt+tab": ("alt", "tab"),
    "alt+shift+tab": ("alt", "shift", "tab"),
}


def position() -> Tuple[int, int]:
    p = pyautogui.position()
    return int(p[0]), int(p[1])


def _screen() -> Tuple[int, int]:
    size = pyautogui.size()
    return int(size[0]), int(size[1])


def move(direction: str, step: int) -> Tuple[int, int]:
    """
    حرکت نسبی ماوس با گام داده‌شده، با گیر کردن در مرز صفحه‌ی اصلی.

    مرزبندی اینجا انجام می‌شود نه با moveTo بیرون از صفحه: هدف این است
    نشانگر همیشه داخل همان ناحیه‌ای بماند که عکسش را می‌فرستیم.
    """
    sign = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
    sx, sy = sign.get(direction, (0, 0))
    w, h = _screen()
    x, y = position()
    nx = min(max(x + sx * int(step), 0), w - 1)
    ny = min(max(y + sy * int(step), 0), h - 1)
    pyautogui.moveTo(nx, ny)
    time.sleep(config.MOUSE_ACTION_PAUSE)
    return nx, ny


def move_abs(x: int, y: int) -> Tuple[int, int]:
    """
    حرکت مطلق ماوس به مختصات داده‌شده (برای کنترل ریموت وب که با
    مختصات صفحه کار می‌کند؛ بر خلاف move که نسبی است).

    عمداً بدون مکث: در درگ کشیدنی، مرورگر ده‌ها رویداد move در ثانیه
    می‌فرستد و sleep اینجا همه‌چیز را می‌کِشد. مکثِ کلیک‌ها سر جایش است.
    """
    w, h = _screen()
    nx = min(max(int(x), 0), w - 1)
    ny = min(max(int(y), 0), h - 1)
    pyautogui.moveTo(nx, ny)
    return nx, ny


def click(kind: str, pause: Optional[float] = None) -> None:
    """
    کلیک چپ/راست/دوباره یا Shift+کلیک.

    Shift باید هنگام فشردن کلیک پایین نگه داشته شود تا سیستم‌عامل انتخاب
    را «ادامه‌ی انتخاب قبلی» حساب کند، نه شروعِ انتخاب جدید — این دکمه
    دقیقاً برای انتخاب کردن متن با ماوس است.

    pause: مکث بعد از کلیک. پیش‌فرض MOUSE_ACTION_PAUSE است که برای پیام
    تلگرام مناسب است (چون هر پیام یک عمل جداست و کاربر فرصت دیدن نتیجه
    دارد). در کنسول ریموت وب مقدار خیلی کوچک‌تری داده می‌شود، چون
    فرمان‌ها پشت‌سرهم و تعاملی می‌آیند و ۱۵۰ مکث برای هر کلیک حس
    کندی محسوس می‌شد.
    """
    if kind == "right":
        pyautogui.click(button="right")
    elif kind == "double":
        pyautogui.doubleClick(button="left")
    elif kind == "shift":
        pyautogui.keyDown("shift")
        try:
            pyautogui.click(button="left")
        finally:
            pyautogui.keyUp("shift")
    else:
        pyautogui.click(button="left")
    time.sleep(config.MOUSE_ACTION_PAUSE if pause is None else pause)


def hold(pause: Optional[float] = None) -> None:
    """پایین نگه داشتن دکمه‌ی چپ — شروع درگ یا انتخاب با کشیدن."""
    pyautogui.mouseDown(button="left")
    time.sleep(config.MOUSE_ACTION_PAUSE if pause is None else pause)


def release(pause: Optional[float] = None) -> None:
    """رها کردن دکمه‌ی چپ — پایان درگ."""
    pyautogui.mouseUp(button="left")
    time.sleep(config.MOUSE_ACTION_PAUSE if pause is None else pause)


def scroll(direction: str, amount: int = 3, pause: Optional[float] = None) -> None:
    """
    اسکرول با چرخ ماوس. amount تعداد کلیک چرخ است (مثبت = بالا).

    مقدار پیش‌فرض ۳ عمداً کم نگه داشته شده تا فراخوانی‌های قدیمی رفتارشان
    عوض نشود؛ کیبورد حالت ماوس مقدار انتخابی کاربر را می‌فرستد.
    """
    pyautogui.scroll(int(amount) if direction == "up" else -int(amount))
    time.sleep(config.MOUSE_ACTION_PAUSE if pause is None else pause)


def press(key: str, pause: Optional[float] = None) -> None:
    """تک‌کلید (نام‌ها همان نام‌های pyautoguiاند: enter, esc, tab, ...)."""
    pyautogui.press(key)
    time.sleep(config.MOUSE_ACTION_PAUSE if pause is None else pause)


def combo(key: str, pause: Optional[float] = None) -> None:
    keys = COMBOS.get(key)
    if not keys:
        raise ValueError(f"ترکیب ناشناخته: {key}")
    pyautogui.hotkey(*keys)
    time.sleep(config.MOUSE_ACTION_PAUSE if pause is None else pause)


def set_text(text: str) -> None:
    """
    چسباندن متن در همان‌جا که نشانگر/فوکوس است، با Ctrl+V.

    دلیل کلیپ‌بورد: pyautogui.write فقط متن انگلیسی را می‌نویسد و فارسی را
    از دست می‌دهد؛ با کپی + چسباندن هر زبانی سر جای خودش می‌نشیند. این همان
    الگویی است که چسباندن پرامپت داخل OpenCode با آن جواب داد.

    کلیپ‌بورد ممکن است «قفل» باشد (بعضی برنامه‌ها نگهش می‌دارند) و copy
    بی‌سروصدا شکست بخورد؛ پس متن خوانده‌شده با متن خواسته‌شده مقایسه می‌شود
    و در صورت نبودِ برابری یک بار دیگر تلاش می‌شود.
    """
    if not text:
        return
    pyperclip.copy(text)
    time.sleep(0.15)
    if pyperclip.paste() != text:
        time.sleep(0.3)
        pyperclip.copy(text)
        time.sleep(0.15)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(config.MOUSE_ACTION_PAUSE)


def annotated_screenshot(
    max_width: Optional[int] = None,
    screen_size: Optional[Tuple[int, int]] = None,
) -> Image.Image:
    """
    عکس صفحه با نشانگر مشخص روی آن.

    نشانگر خودِ ویندوز داخل عکس pyautogui دیده نمی‌شود، پس صلیب قرمز خودمان
    کشیده می‌شود تا در تلگرام جای ماوس معلوم باشد. برچسب مختصات در گوشه
    می‌نشیند، نه کنار نشانگر، چون نشانگر ممکن است لبه‌ی صفحه باشد.

    max_width: اگر داده شود، تصویر **قبل از** annotate به این عرض کوچک
    می‌شود. این کار کل زمان پردازش را کم می‌کند (در کنسول ریموت وب هر فریم
    لازم است، و کشیدن چند خط روی تصویر ۱۹۲۰×۱۰۸۰ گران‌تر از کل resize است).
    screen_size: اندازه‌ی صفحه‌ی اصلی، برای اینکه مختصات نشانگر بعد از
    کوچک‌سازی درست مقیاس شود (اگر داده نشود، خودش گرفته می‌شود).
    """
    img = pyautogui.screenshot()
    sw, sh = screen_size if screen_size else _screen()

    if max_width and img.width > max_width:
        scale = max_width / float(img.width)
        img = img.resize((max_width, max(1, int(img.height * scale))))
    else:
        scale = 1.0

    # مختصات نشانگر روی تصویرِ کوچک‌شده
    x, y = position()
    cx = int(x * scale)
    cy = int(y * scale)

    draw = ImageDraw.Draw(img)
    arm = max(10, int(24 * scale))          # طول خط صلیب
    ring = max(5, int(9 * scale))          # شعاع حلقه
    draw.line((cx - arm, cy, cx + arm, cy), fill=(255, 40, 40), width=2)
    draw.line((cx, cy - arm, cx, cy + arm), fill=(255, 40, 40), width=2)
    draw.ellipse((cx - ring, cy - ring, cx + ring, cy + ring), outline=(255, 40, 40), width=2)
    # برچسب مختصاتِ واقعی (نه مقیاس‌شده) در گوشه
    label = f"X={x}  Y={y}"
    draw.rectangle((8, 8, 12 + 7 * len(label), 30), fill=(0, 0, 0))
    draw.text((14, 12), label, fill=(255, 220, 0))
    return img
