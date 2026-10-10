# -*- coding: utf-8 -*-
"""گرفتن اسکرین‌شات و تطبیق تصویر."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from typing import Optional, Sequence, Tuple

import pyautogui
from PIL import Image

import config

logger = logging.getLogger(__name__)

try:
    import cv2
    import numpy as np

    _HAS_CV2 = True
except Exception:  # pragma: no cover
    _HAS_CV2 = False
    np = None
    cv2 = None

pyautogui.FAILSAFE = config.AUTOMATION_FAILSAFE
pyautogui.PAUSE = 0.08


def grab_screen() -> Image.Image:
    """کل صفحه (یا همه‌ی مانیتورها) را به‌صورت RGB برمی‌گرداند."""
    return pyautogui.screenshot()


async def grab_screen_async() -> Image.Image:
    return await asyncio.to_thread(grab_screen)


def crop_by_ratio(image: Image.Image, box: Sequence[float]) -> Optional[Image.Image]:
    """برش با نسبت‌های (left, top, right, bottom) نسبت به اندازه‌ی تصویر."""
    w, h = image.size
    l, t, r, b = box
    x0 = max(0, int(l * w))
    y0 = max(0, int(t * h))
    x1 = min(w, int(r * w))
    y1 = min(h, int(b * h))
    if x1 <= x0 or y1 <= y0:
        return None
    return image.crop((x0, y0, x1, y1))


def region_diff_ratio(a: Image.Image, b: Image.Image) -> float:
    """نسبت پیکسل‌های متفاوت بین دو تصویر هم‌اندازه (۰ تا ۱)."""
    if a is None or b is None:
        return 1.0
    if a.size != b.size:
        b = b.resize(a.size)
    ga = a.convert("L")
    gb = b.convert("L")
    if not _HAS_CV2:
        ba = ga.tobytes()
        bb = gb.tobytes()
        if len(ba) != len(bb) or not ba:
            return 1.0
        diff = sum(1 for x, y in zip(ba, bb) if abs(x - y) > 12)
        return diff / len(ba)

    arr_a = np.asarray(ga, dtype=np.int16)
    arr_b = np.asarray(gb, dtype=np.int16)
    changed = np.count_nonzero(np.abs(arr_a - arr_b) > 12)
    return float(changed) / float(arr_a.size)


def changed_pixels(a: Image.Image, b: Image.Image, tol: int = 12) -> int:
    """
    تعداد پیکسل‌های تغییرکرده.

    برای تشخیص «چیزی تایپ یا چسبانده شد» این معیار بهتر از نسبت است،
    چون نسبت به اندازه‌ی ناحیه وابسته است: نوشتن چند کلمه در ناحیه‌ی بزرگ
    فقط چند هزار پیکسل را عوض می‌کند و نسبتش زیر آستانه می‌ماند، در حالی
    که تعداد مطلق پیکسل کاملاً محسوس است.
    """
    if a is None or b is None:
        return 0
    if a.size != b.size:
        b = b.resize(a.size)
    if not _HAS_CV2:
        return int(region_diff_ratio(a, b) * a.size[0] * a.size[1])
    arr_a = np.asarray(a.convert("L"), dtype=np.int16)
    arr_b = np.asarray(b.convert("L"), dtype=np.int16)
    return int(np.count_nonzero(np.abs(arr_a - arr_b) > tol))


_template_cache: dict = {}


def load_template(name: str) -> Optional[Image.Image]:
    """قالب تصویری را از پوشه‌ی assets بارگذاری و کش می‌کند."""
    if not name:
        return None
    if name in _template_cache:
        return _template_cache[name]
    path = name if os.path.isabs(name) else os.path.join(config.ASSETS_DIR, name)
    if not os.path.exists(path):
        logger.info("قالب تصویری پیدا نشد، این روش تشخیص رد می‌شود: %s", path)
        _template_cache[name] = None
        return None
    try:
        img = Image.open(path).convert("RGB")
    except Exception:
        logger.exception("خواندن قالب ناموفق بود: %s", path)
        _template_cache[name] = None
        return None

    # قالب یکنواخت با هر ناحیه‌ی یکنواخت دیگر هم تطابق کامل می‌دهد،
    # پس نتیجه‌اش تشخیص اشتباه است. آن را نمی‌پذیریم.
    if _std_of(img) < config.MIN_TEMPLATE_STD:
        logger.warning(
            "قالب %s بیش از حد یکنواخت است (انحراف معیار %.1f < %.1f) و "
            "برای تشخیص قابل استفاده نیست؛ نادیده گرفته می‌شود.",
            name, _std_of(img), config.MIN_TEMPLATE_STD,
        )
        _template_cache[name] = None
        return None

    _template_cache[name] = img
    return img


def _std_of(img: Image.Image) -> float:
    gray = img.convert("L")
    if not _HAS_CV2:
        return 128.0
    arr = np.asarray(gray, dtype=np.float32)
    return float(arr.std())


def template_available(name: str) -> bool:
    """
    آیا قالب تصویری واقعاً وجود دارد و قابل استفاده است؟

    مهم است چون نبودِ قالب یعنی «نمی‌شود تشخیص داد»، نه «تشخیص داده شد که نیست».
    بدون این تفکیک، نبودِ قالب باعث می‌شود کار اشتباهی تمام‌شده اعلام شود.
    """
    return load_template(name) is not None


def find_template(
    screen: Image.Image,
    template_name: str,
    region: Optional[Sequence[float]] = None,
    threshold: float = 0.88,
) -> Tuple[bool, Tuple[int, int, int, int]]:
    """
    دنبال یک قالب تصویری در صفحه می‌گردد.
    خروجی: (پیدا شد؟, (x, y, w, h)) در مختصات کل صفحه.
    """
    empty = (False, (0, 0, 0, 0))
    template = load_template(template_name)
    if template is None:
        return empty

    haystack = crop_by_ratio(screen, region) if region else screen
    if haystack is None:
        return empty

    if not _HAS_CV2:
        return empty

    hay = cv2.cvtColor(np.asarray(haystack.convert("RGB")), cv2.COLOR_RGB2GRAY)
    tpl = cv2.cvtColor(np.asarray(template.convert("RGB")), cv2.COLOR_RGB2GRAY)
    if tpl.shape[0] > hay.shape[0] or tpl.shape[1] > hay.shape[1]:
        return empty

    # قالب‌های یکنواخت (مثل یک دکمه‌ی تک‌رنگ) واریانس صفر دارند و
    # روش نرمال‌شده‌ی همبستگی برایشان بی‌معنی می‌شود؛ برای آن‌ها
    # از تفاضل مربعی نرمال‌شده استفاده می‌کنیم.
    if float(tpl.std()) < 2.0:
        res = cv2.matchTemplate(hay, tpl, cv2.TM_SQDIFF_NORMED)
        min_val, _max_val, min_loc, _max_loc = cv2.minMaxLoc(res)
        score = 1.0 - float(min_val)
        x, y = min_loc
    else:
        res = cv2.matchTemplate(hay, tpl, cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(res)
        score = float(max_val)
        x, y = max_loc

    logger.debug(
        "تطبیق قالب %s: امتیاز %.3f در %s", template_name, score, (x, y)
    )
    if score < threshold:
        return False, (0, 0, 0, 0)

    h, w = tpl.shape
    # آفست باید از اندازه‌ی کل صفحه حساب شود، نه اندازه‌ی ناحیه‌ی برش‌خورده
    off_x = int(screen.size[0] * region[0]) if region else 0
    off_y = int(screen.size[1] * region[1]) if region else 0
    return True, (off_x + int(x), off_y + int(y), int(w), int(h))


def encode_jpeg(image: Image.Image, quality: Optional[int] = None) -> bytes:
    buf = io.BytesIO()
    image.convert("RGB").save(
        buf, format="JPEG", quality=quality or config.SCREENSHOT_JPEG_QUALITY
    )
    return buf.getvalue()


def encode_png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


_shot_counter = 0


def _next_shot_name() -> str:
    global _shot_counter
    _shot_counter += 1
    return f"screen_{int(time.time())}_{_shot_counter}.png"


def encode_document(image: Image.Image) -> bytes:
    """
    فایل خوانای اسکرین‌شات.

    اول PNG بدون فشرده‌سازی امتحان می‌شود، ولی اگر خیلی بزرگ شود (متن‌های ریز
    را خراب می‌کند) یا از سقف حجم بگذرد، کیفیت JPEG پایین‌تر می‌شود تا
    ارسال سریع و بدون تایم‌اوت باشد.
    """
    png = encode_png(image)
    if len(png) <= config.MAX_DOCUMENT_BYTES:
        return png
    logger.info(
        "PNG اسکرین‌شات %d بایت بود؛ با JPEG کیفیت بالاتر جایگزین شد.",
        len(png),
    )
    return encode_jpeg(image, quality=95)


async def send_screenshot_to(
    bot,
    chat_id: int,
    image: Optional[Image.Image] = None,
    caption: str = "",
    as_document: bool = True,
):
    """
    تصویر را هم به‌صورت photo (فشرده) و هم document (خوانا) می‌فرستد.

    تلگرام عکس‌ها را فشرده می‌کند، برای همین نسخه‌ی اصلی هم فرستاده می‌شود
    تا متن‌های ریز خوانا بمانند.

    نکته‌ی مهم: خطای ارسال فایل نباید کل عملیات را خراب کند. اگر
    photo فرستاده شد ولی document نه، کاربر دست‌کم عکس را می‌بیند.
    """
    if image is None:
        image = await grab_screen_async()

    await bot.send_photo(
        chat_id=chat_id,
        photo=encode_jpeg(image),
        caption=caption or None,
    )

    if not as_document:
        return True

    try:
        await bot.send_document(
            chat_id=chat_id,
            document=encode_document(image),
            filename=_next_shot_name(),
            caption="نسخه‌ی اصلی — برای خواندن متن‌های ریز.",
        )
        return True
    except Exception as exc:
        logger.warning("ارسال فایل خوانا ناموفق بود: %s", exc)
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "⚠️ عکس بالا ارسال شد ولی فایل خوانای آن نه.\n"
                f"دلیل: {exc}\n"
                "برای دیدن متن‌های ریز، از اسکرین‌شات بگیرید و بزرگ‌نمایی کنید."
            ),
        )
        return False