# -*- coding: utf-8 -*-
"""
شناسایی نوار تب در برنامه‌ی دسکتاپ OpenCode.

چرا پیکسل‌پردازی؟ برنامه‌ی OpenCode دسکتاپ روی Electron اجرا می‌شود و
UI Automation فقط دکمه‌های خود پنجره (کمینه/بزرگ‌نمایی/بستن) را نشان می‌دهد؛
محتوای صفحه‌ی آن قابل دسترسی نیست. پس تنها راه، نگاه کردن به خود تصویر است.

نوار تب در بالای پنجره است و هر تب با یک آیکن مربعی روشن شروع می‌شود.
آیکن‌ها فاصله‌ی منظم (~۱۹۰ پیکسل) دارند و همین منظم بودن، لنگر تشخیص است.

خروجی مختصات به‌صورت صفحه است تا مستقیم به pyautogui داده شود.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

try:
    import numpy as np

    _HAS_NUMPY = True
except Exception:  # pragma: no cover
    _HAS_NUMPY = False


# نسبت‌های نوار تب نسبت به اندازه‌ی پنجره (پنجره maximize می‌شود)
STRIP_TOP = 0.010
STRIP_BOTTOM = 0.042
STRIP_CLICK_Y = 0.0235

# نوار تب از این نسبت به بعد شروع می‌شود (قبل از آن دو آیکن خود برنامه است)
TABS_START_RATIO = 0.062

# از این نسبت به بعد دیگر تب نیست: دکمه‌ی پنل و کنترل‌های پنجره‌اند
RIGHT_ANCHOR_RATIO = 0.875

# آستانه‌های تشخیص
BRIGHT_THRESHOLD = 30       # روشنایی ستون که «محتوا» حساب می‌شود
FILL_THRESHOLD = 50         # روشنایی پیکسل که «پر» حساب می‌شود
MERGE_GAP = 6               # ادغام ستون‌های نزدیک
MIN_ICON_WIDTH = 10         # کمترین عرض برای آیکن
MAX_ICON_WIDTH = 40         # بیشترین عرض برای آیکن تب
ACTIVE_TAB_MIN_WIDTH = 100  # پس‌زمینه‌ی تب فعال
ACTIVE_TAB_MIN_FILL = 0.75   # پس‌زمینه پر است، برخلاف متنِ تب
MAX_TABS = 9


def _luminance(image: Image.Image):
    if not _HAS_NUMPY:
        return None
    arr = np.asarray(image.convert("L"), dtype=float)
    return arr


def _strip_rows(win_rect: Tuple[int, int, int, int]) -> Tuple[int, int]:
    """ردیف‌های نوار تب در مختصات کل صفحه."""
    left, top, right, bottom = win_rect
    height = bottom - top
    y0 = top + int(height * STRIP_TOP)
    y1 = top + int(height * STRIP_BOTTOM)
    return max(0, y0), min(int(top + height), y1)


def _clusters(col_max) -> List[Tuple[int, int]]:
    """ستون‌های روشن را به گروه‌های پیوسته تبدیل می‌کند."""
    out: List[Tuple[int, int]] = []
    start = None
    gap = 0
    for x, value in enumerate(col_max):
        if value > BRIGHT_THRESHOLD:
            if start is None:
                start = x
            gap = 0
        else:
            if start is not None:
                gap += 1
                if gap >= MERGE_GAP:
                    out.append((start, x - gap))
                    start = None
                    gap = 0
    if start is not None:
        out.append((start, len(col_max) - 1))
    return out


def _fill_ratio(lum, y0: int, y1: int, x0: int, x1: int) -> float:
    """نسبت پیکسل‌های روشن در محدوده‌ی یک گروه."""
    block = lum[y0:y1, x0:x1 + 1]
    if block.size == 0:
        return 0.0
    rows = np.where(block.max(axis=1) > BRIGHT_THRESHOLD)[0]
    if len(rows) == 0:
        return 0.0
    block = block[rows[0]:rows[-1] + 1, :]
    return float((block > FILL_THRESHOLD).mean())


def _lock_to_pitch(items: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """
    گزینه‌ها را روی ریتم منظم آیکن‌های تب می‌نشاند.

    آیکن تب‌ها در OpenCode تقریباً هم‌فاصله‌اند (حدود ۱۹۰ پیکسل). گاهی یک
    تکه‌ی متن یا بخشی از آیکن، شبیه آیکن تب دیده می‌شود و در نتیجه تب
    اضافه ساخته می‌شود. این تابع با «قفل شدن» روی فاصله‌ی متوسط، آن
    موارد اضافی را حذف می‌کند.
    """
    if len(items) < 4:
        return items

    xs = [int(it["x"]) for it in items]
    deltas = sorted(
        b - a for a, b in zip(xs, xs[1:]) if b - a > 0
    )
    if not deltas:
        return items

    pitch = deltas[len(deltas) // 2]
    if pitch <= 0:
        return items

    tolerance = int(pitch * 0.25)
    chosen = [items[0]]
    for entry in items[1:]:
        expected = int(chosen[-1]["x"]) + pitch
        if int(entry["x"]) >= expected - tolerance:
            chosen.append(entry)

    logger.info(
        "قفل روی ریتم تب: فاصله=%d پیکسل، %d گزینه از %d تا",
        pitch, len(chosen), len(items),
    )
    return chosen if len(chosen) >= 2 else items


def detect_tabs(
    screen: Image.Image,
    win_rect: Tuple[int, int, int, int],
) -> Dict[str, object]:
    """
    نوار تب OpenCode را می‌خواند.

    خروجی:
        ok        آیا چیزی پیدا شد
        reason    دلیل ناموفق بودن (اگر ok نباشد)
        tabs      فهرست {"index": 1..n, "x": ..., "y": ...} به‌ترتیب نمایش
        new_tab   مختصات دکمه‌ی تب جدید یا None
        strip     محدوده‌ی (چپ، بالا، راست، پایین) نوار تب برای برش تصویر
    """
    empty = {
        "ok": False,
        "reason": "نوار تب پیدا نشد.",
        "tabs": [],
        "new_tab": None,
        "strip": None,
    }

    lum = _luminance(screen)
    if lum is None:
        empty["reason"] = "numpy نصب نیست."
        return empty

    left, top, right, bottom = win_rect
    width = right - left
    height = bottom - top

    y0, y1 = _strip_rows(win_rect)
    if y1 - y0 < 4:
        empty["reason"] = "ارتفاع پنجره کم است."
        return empty

    strip = lum[y0:y1, :]
    col_max = strip.max(axis=0)
    clusters = _clusters(col_max)
    if not clusters:
        empty["reason"] = "محتوایی در نوار تب دیده نشد."
        return empty

    click_y = top + int(height * STRIP_CLICK_Y)

    # توجه: ستون‌های تصویر مختصات کل صفحه‌اند، پس x ها دیگر نباید با
    # لبه‌ی پنجره جمع شوند (یک بار دیگر جمع کنیم، ۹ پیکسل خطا می‌افتد).
    items: List[Dict[str, object]] = []

    for x_start, x_end in clusters:
        width_px = x_end - x_start + 1
        center_x = (x_start + x_end) // 2
        ratio = (center_x - left) / max(1, width)

        # آیکن‌های خود برنامه (قبل از تب‌ها) و دکمه‌ی پنل و کنترل‌های پنجره
        # (بعد از تب‌ها) کنار گذاشته می‌شوند.
        if ratio < TABS_START_RATIO or ratio > RIGHT_ANCHOR_RATIO:
            continue

        fill = _fill_ratio(lum, y0, y1, x_start, x_end)

        # پس‌زمینه‌ی روشن تب فعال از کل نوار پهن‌تر است.
        # پرکنندگیِ بالا لازم است تا متنِ تب با آن اشتباه نشود.
        if width_px >= ACTIVE_TAB_MIN_WIDTH and fill >= ACTIVE_TAB_MIN_FILL:
            items.append({
                "x": x_start + int(width_px * 0.06),
                "y": click_y,
                "w": width_px,
                "fill": fill,
                "active": True,
                "ratio": ratio,
            })
            continue

        if not (MIN_ICON_WIDTH <= width_px <= MAX_ICON_WIDTH):
            continue

        items.append({
            "x": center_x,
            "y": click_y,
            "w": width_px,
            "fill": fill,
            "active": False,
            "ratio": ratio,
        })

    if not items:
        empty["reason"] = "تبی در نوار بالا پیدا نشد."
        return empty

    items = _lock_to_pitch(items)

    # --- تشخیص دکمه‌ی «تب جدید»
    #
    # این دکمه باریک‌تر از آیکن تب‌هاست (حدود ۱۴ در برابر ۱۸ تا ۲۰ پیکسل).
    # معیار نسبی است تا با تغییر بزرگ‌نمایی یا اندازه‌ی صفحه نشکند.
    #
    # نکته‌ی مهم: فاصله‌اش تا لبه‌ی راست ثابت نیست، چون با کم و زیاد شدن تعداد
    # تب‌ها جابه‌جا می‌شود. تنها نشانه‌ی قابل اتکا این است که آخرین گزینه‌ی
    # باریک باشد. موارد اضافی پیش از این مرحله با «قفل ریتم» حذف شده‌اند.
    new_tab: Optional[Dict[str, int]] = None
    icon_widths = [int(it["w"]) for it in items if not it["active"]]
    if len(icon_widths) >= 3:
        median_icon = sorted(icon_widths)[len(icon_widths) // 2]
        # ۰.۸۵ به‌جای ۰.۸: در برنامه‌ی واقعی دکمه‌ی «+» حدود ۱۵ پیکسل
        # و آیکن تب‌ها ۱۸ تا ۲۰ پیکسل است؛ با آستانه‌ی ۰.۸، «+» تشخیص
        # داده نمی‌شد و به‌عنوان تبِ شماره‌دار در کیبورد چت ظاهر می‌شد.
        thin_limit = max(MIN_ICON_WIDTH, int(median_icon * 0.85))
        thin = [it for it in items if int(it["w"]) < thin_limit]
        if thin and len(items) > 1:
            chosen = thin[-1]
            new_tab = {"x": int(chosen["x"]), "y": click_y}
            items.remove(chosen)

    tabs: List[Dict[str, object]] = []
    for entry in items[:MAX_TABS]:
        tabs.append({
            "x": int(entry["x"]),
            "y": int(entry["y"]),
            "active": bool(entry["active"]),
            "index": len(tabs) + 1,
        })

    return {
        "ok": bool(tabs),
        "reason": "" if tabs else "تبی پیدا نشد.",
        "tabs": tabs,
        "new_tab": new_tab,
        "strip": (left, y0, right, y1),
    }


def annotate_strip(
    screen: Image.Image,
    result: Dict[str, object],
) -> Optional[Image.Image]:
    """
    روی برش نوار تب شماره می‌نویسد تا کاربر بداند کدام دکمه کدام تب است.

    چون عنوان تب‌ها از بیرون برنامه قابل خواندن نیست، همین تصویر بهترین
    راهنماست: شماره‌ی روی تصویر با شماره‌ی دکمه در چت یکی است.
    """
    strip = result.get("strip")
    if not strip:
        return None

    left, top, right, bottom = strip
    pad = 4
    origin = (
        max(0, left - pad),
        max(0, top - pad),
        min(screen.size[0], right + pad),
        min(screen.size[1], bottom + pad + 8),
    )
    crop = screen.crop(origin).convert("RGB")
    draw = ImageDraw.Draw(crop)

    def to_crop(point: Dict[str, int]) -> Tuple[int, int]:
        # مختصات صفحه را به مختصات برش تبدیل می‌کند.
        # استفاده از «مبدأ برش» لازم است؛ با اندازه‌ی برش اشتباه می‌شود و
        # نشان‌ها بیرون تصویر می‌افتند.
        return int(point["x"]) - origin[0], int(point["y"]) - origin[1]

    r = 10

    for tab in result.get("tabs", []):
        cx, cy = to_crop(tab)
        draw.ellipse(
            (cx - r, cy - r - 3, cx + r, cy + r - 3),
            fill=(200, 25, 25),
            outline=(255, 255, 255),
            width=2,
        )
        draw.text((cx - 3, cy - 10), str(tab["index"]), fill=(255, 255, 255))

    new_tab = result.get("new_tab")
    if new_tab:
        cx, cy = to_crop(new_tab)
        draw.ellipse(
            (cx - r, cy - r - 3, cx + r, cy + r - 3),
            fill=(25, 105, 200),
            outline=(255, 255, 255),
            width=2,
        )
        draw.text((cx - 3, cy - 10), "+", fill=(255, 255, 255))

    return crop


def strip_click_count(result: Dict[str, object]) -> int:
    return len(result.get("tabs", []) or [])
