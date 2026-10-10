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
from typing import Dict, Tuple

import pyautogui
import pyperclip
from PIL import Image, ImageDraw

import config

logger = logging.getLogger(__name__)

# مجموعه‌های معتبر برای اعتبارسنجی callback_data در main؛ اینجا نگه داشته
# می‌شوند تا «دکمه‌ی ناشناخته» قبل از رسیدن به pyautogui رد شود.
MOVES = {"up", "down", "left", "right"}
CLICKS = {"left", "right", "double", "shift"}
KEYS = {"enter", "esc", "tab", "space", "backspace", "delete"}

# ترکیب‌های Ctrl برای دکمه‌های کنترلی (کلیدهای کوچک، چون hotkey آن‌ها را
# خودش Shift نمی‌کشد).
COMBOS: Dict[str, Tuple[str, ...]] = {
    "ctrl+a": ("ctrl", "a"),
    "ctrl+c": ("ctrl", "c"),
    "ctrl+v": ("ctrl", "v"),
    "ctrl+x": ("ctrl", "x"),
    "ctrl+z": ("ctrl", "z"),
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


def click(kind: str) -> None:
    """
    کلیک چپ/راست/دوباره یا Shift+کلیک.

    Shift باید هنگام فشردن کلیک پایین نگه داشته شود تا سیستم‌عامل انتخاب
    را «ادامه‌ی انتخاب قبلی» حساب کند، نه شروعِ انتخاب جدید — این دکمه
    دقیقاً برای انتخاب کردن متن با ماوس است.
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
    time.sleep(config.MOUSE_ACTION_PAUSE)


def hold() -> None:
    """پایین نگه داشتن دکمه‌ی چپ — شروع درگ یا انتخاب با کشیدن."""
    pyautogui.mouseDown(button="left")
    time.sleep(config.MOUSE_ACTION_PAUSE)


def release() -> None:
    """رها کردن دکمه‌ی چپ — پایان درگ."""
    pyautogui.mouseUp(button="left")
    time.sleep(config.MOUSE_ACTION_PAUSE)


def scroll(direction: str) -> None:
    pyautogui.scroll(3 if direction == "up" else -3)
    time.sleep(config.MOUSE_ACTION_PAUSE)


def press(key: str) -> None:
    """تک‌کلید (نام‌ها همان نام‌های pyautoguiاند: enter, esc, tab, ...)."""
    pyautogui.press(key)
    time.sleep(config.MOUSE_ACTION_PAUSE)


def combo(key: str) -> None:
    keys = COMBOS.get(key)
    if not keys:
        raise ValueError(f"ترکیب ناشناخته: {key}")
    pyautogui.hotkey(*keys)
    time.sleep(config.MOUSE_ACTION_PAUSE)


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


def annotated_screenshot() -> Image.Image:
    """
    عکس صفحه با نشانگر مشخص روی آن.

    نشانگر خودِ ویندوز داخل عکس pyautogui دیده نمی‌شود، پس صلیب قرمز خودمان
    کشیده می‌شود تا در تلگرام جای ماوس معلوم باشد. برچسب مختصات در گوشه
    می‌نشیند، نه کنار نشانگر، چون نشانگر ممکن است لبه‌ی صفحه باشد.
    """
    img = pyautogui.screenshot()
    x, y = position()
    draw = ImageDraw.Draw(img)
    draw.line((x - 24, y, x + 24, y), fill=(255, 40, 40), width=2)
    draw.line((x, y - 24, x, y + 24), fill=(255, 40, 40), width=2)
    draw.ellipse((x - 9, y - 9, x + 9, y + 9), outline=(255, 40, 40), width=2)
    label = f"X={x}  Y={y}"
    draw.rectangle((8, 8, 12 + 7 * len(label), 30), fill=(0, 0, 0))
    draw.text((14, 12), label, fill=(255, 220, 0))
    return img
