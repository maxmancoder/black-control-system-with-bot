# -*- coding: utf-8 -*-
"""کیبوردهای ثابت منو."""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

import config
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

logger = logging.getLogger(__name__)

BTN_SCREENSHOT = "📸 اسکرین‌شات"
BTN_APPS = "📂 برنامه‌ها"
BTN_PROMPT = "✍️ ساخت پرامپت"
BTN_SHUTDOWN = "⏻ خاموش"
BTN_TABS = "🌐 تب مرورگر"
BTN_MOUSE = "🖱 کنترل ماوس"

MENU_BUTTONS = [BTN_SCREENSHOT, BTN_APPS, BTN_PROMPT, BTN_TABS, BTN_MOUSE, BTN_SHUTDOWN]

# کلیدواژه‌هایی که برای شناسایی دکمه‌ها استفاده می‌شوند.
# به‌جای مقایسه‌ی دقیق متن، از این کلیدواژه‌ها استفاده می‌کنیم چون ممکن است
# کلاینت تلگرام نیم‌فاصله یا شکل حروف را تغییر دهد و مقایسه‌ی دقیق بشکند.
BUTTON_KEYWORDS = {
    BTN_SCREENSHOT: [
        "اسکرین‌شات", "اسکرین‌شات", "اسکرين‌شات", "عکس از صفحه",
        "screenshot", "shot",
    ],
    BTN_APPS: ["برنامه‌ها", "برنامه ها", "برنامهها", "برامج", "apps"],
    BTN_PROMPT: ["ساخت پرامپت", "پرامپت", "prompt"],
    BTN_TABS: ["تب مرورگر", "تب‌های مرورگر", "مرورگر", "browser", "tabopencode"],
    BTN_SHUTDOWN: ["خاموش کردن", "خاموش‌کردن", "خاموش", "shutdown"],
    # «موس» تنها نیامده تا عبارتی مثل «موسیقی» با ایموجی اشتباه دکمه نشود؛
    # اگر هم کاربر فقط «ماوس» را بنویسد، همان یک کلمه کافی است.
    BTN_MOUSE: ["کنترل ماوس", "کنترل موس", "ماوس", "mouse"],
}

# طول متنی که هنوز «شبیه برچسب دکمه» حساب می‌شود
_BUTTON_MAX_LEN = 24


def normalize_text(value: str) -> str:
    """
    یکسان‌سازی متن برای مقایسه.

    نیم‌فاصله، کاراکترهای نامرئی، فاصله‌های اضافه و شکل عربی/فارسی حروف
    را یکسان می‌کند تا مقایسه‌ی دکمه‌ها به‌هم نریزد.
    """
    if not value:
        return ""
    text = value.strip().lower()
    replacements = {
        "\u200c": " ",   # نیم‌فاصله
        "\u200f": "",    # RTL mark
        "\u200e": "",    # LTR mark
        "\u200b": "",    # zero width space
        "\u00a0": " ",   # non breaking space
        "\u064a": "\u06cc",  # ي -> ی
        "\u0649": "\u06cc",  # ى -> ی
        "\u0643": "\u06a9",  # ك -> ک
        "\u06d5": "\u0647",  # ه عربی
        "\u0629": "\u0647",  # ة -> ه
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return " ".join(text.split())


def _has_emoji(value: str) -> bool:
    """
    آیا متن نماد گرافیکی دارد؟

    فقط محدوده‌ی ۱F000 کافی نیست: ⏻ و ✍️ و ➕ در بلوک‌های پایین‌تری هستند،
    پس چند بلوک بررسی می‌شود.
    """
    ranges = (
        (0x1F000, 0x1FAFF),  # شکلک‌ها
        (0x2600, 0x27BF),    # نمادهای miscellaneous و dingbats
        (0x2300, 0x23FF),    # نمادهای فنی، شامل ⏻ و ⏸
        (0x2B00, 0x2BFF),    # فلش‌ها
        (0x25A0, 0x25FF),    # اشکال هندسی
        (0x2700, 0x27BF),
    )
    for ch in value:
        code = ord(ch)
        for low, high in ranges:
            if low <= code <= high:
                return True
    return False


def match_button(value: str) -> Optional[str]:
    """
    می‌گوید متن ورودی کدام دکمه‌ی منو است (یا None).

    دو شرط، تا درخواست واقعی کاربر با دکمه اشتباه نشود:

      ۱) متن پس از یکسان‌سازی دقیقاً برابر برچسب یا کلیدواژه باشد
      ۲) متن کوتاه باشد، ایموجی داشته باشد و کلیدواژه را دربر بگیرد

    شرط دوم باعث می‌شود جمله‌ای مثل «درخواستی درباره‌ی برنامه‌ها بنویس»
    به اشتباه دکمه‌ی برنامه‌ها فعال نشود.
    """
    text = normalize_text(value)
    if not text:
        return None
    has_emoji = _has_emoji(text)

    for button, keywords in BUTTON_KEYWORDS.items():
        if text == normalize_text(button):
            return button
        for keyword in keywords:
            nkw = normalize_text(keyword)
            if not nkw:
                continue
            if text == nkw:
                return button
            if has_emoji and len(text) <= _BUTTON_MAX_LEN and nkw in text:
                return button
    return None


def looks_like_button(value: str) -> bool:
    """
    آیا متن شبیه یک دکمه‌ی ناشناخته است؟

    اگر کاربر روی کیبوردِ قدیمی (یا با برچسب عوض‌شده) بزند، نباید متن آن
    به‌عنوان درخواست ساخت پرامپت به AI فرستاده شود.
    """
    text = (value or "").strip()
    if not text:
        return False
    # متن‌های خیلی کوتاه یا خیلی بلند، درخواست واقعی نیستند
    if len(text) > 60 or len(text) < 2:
        return False
    # اگر نماد گرافیکی داشته باشد، به احتمال زیاد دکمه است نه درخواست
    return _has_emoji(text)


def main_menu() -> ReplyKeyboardMarkup:
    """منوی اصلی: دکمه‌های ثابت پایین چت."""
    return ReplyKeyboardMarkup(
        [
            [BTN_SCREENSHOT, BTN_APPS],
            [BTN_PROMPT, BTN_TABS],
            [BTN_MOUSE, BTN_SHUTDOWN],
        ],
        resize_keyboard=True,
        input_field_placeholder="پیامت را بنویس…",
    )


def remove_keyboard() -> "ReplyKeyboardRemove":
    return ReplyKeyboardRemove()


def apps_keyboard(
    apps: List[Dict[str, str]],
    page: int = 0,
    total_pages: int = 1,
    show_favorites: Optional[List[Dict[str, str]]] = None,
) -> InlineKeyboardMarkup:
    """لیست برنامه‌ها با صفحه‌بندی و دکمه‌های ناوبری."""
    show_favorites = show_favorites or []
    rows: List[List[InlineKeyboardButton]] = []

    if page == 0 and show_favorites:
        rows.append([
            InlineKeyboardButton(
                text=f"⭐ {a['name']}"[:40], callback_data=f"app:{_short(a['app_id'])}"
            )
            for a in show_favorites
        ])

    for app in apps:
        rows.append([
            InlineKeyboardButton(
                text=app["name"][:40], callback_data=f"app:{_short(app['app_id'])}"
            )
        ])

    nav: List[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"apps:{page - 1}"))
    nav.append(
        InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="noop")
    )
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"apps:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([
        InlineKeyboardButton("🔍 جست‌وجو", callback_data="search_enter"),
        InlineKeyboardButton("🔄 به‌روزرسانی لیست", callback_data="apps_refresh"),
    ])

    return InlineKeyboardMarkup(rows)


def _short(app_id: str) -> str:
    """کوتاه کردن شناسه برای جا شدن در callback_data (حداکثر ۶۴ بایت)."""
    return remember_app_id(app_id)


_HASHED: Dict[str, str] = {}
_MAP_FILE = None


def _load_id_map() -> Dict[str, str]:
    """نگاشت «کلید کوتاه -> شناسه‌ی کامل» در یک فایل تا بعد از ری‌استارت هم کار کند."""
    global _MAP_FILE
    if _MAP_FILE is None:
        _MAP_FILE = os.path.join(config.DATA_DIR, "app_id_map.json")
        try:
            with open(_MAP_FILE, "r", encoding="utf-8") as fh:
                import json

                _HASHED.update(json.load(fh))
        except Exception:
            pass
    return _HASHED


def _save_id_map() -> None:
    if _MAP_FILE is None:
        return
    try:
        import json

        tmp = _MAP_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_HASHED, fh, ensure_ascii=False)
        os.replace(tmp, _MAP_FILE)
    except Exception:
        logger.exception("ذخیره‌ی نگاشت شناسه‌ها ناموفق بود")


def remember_app_id(app_id: str) -> str:
    """
    برای شناسه‌های بلند یک کلید کوتاه می‌سازد تا در callback_data جا شود
    (تلگرام حداکثر ۶۴ بایت می‌پذیرد) و نگاشتش را نگه می‌دارد.
    """
    if len(f"app:{app_id}".encode("utf-8")) <= 64:
        return app_id

    import hashlib

    digest = hashlib.sha1(app_id.encode("utf-8")).hexdigest()[:16]
    token = "h" + digest
    mapping = _load_id_map()
    if mapping.get(token) != app_id:
        mapping[token] = app_id
        _save_id_map()
    return token


def resolve_app_id(token: str) -> str:
    """برعکسِ remember_app_id."""
    if token.startswith("h") and len(token) == 17:
        return _load_id_map().get(token, "")
    return token


def search_result_keyboard(apps: List[Dict[str, str]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(a["name"][:40], callback_data=f"app:{_short(a['app_id'])}")]
        for a in apps
    ]
    rows.append([InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="apps:0")])
    return InlineKeyboardMarkup(rows)


def confirm_shutdown() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("بله، خاموش کن", callback_data="shutdown_yes"),
            InlineKeyboardButton("لغو", callback_data="shutdown_no"),
        ]
    ])


def shutdown_pending() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ لغو خاموشی", callback_data="shutdown_abort")]
    ])


def prompt_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚀 ارسال به OpenCode", callback_data="prompt_send"),
            InlineKeyboardButton("✏️ ویرایش متن خام", callback_data="prompt_edit_raw"),
        ],
        [
            InlineKeyboardButton("♻️ بازسازی", callback_data="prompt_rebuild"),
            InlineKeyboardButton("🗑 حذف", callback_data="prompt_clear"),
        ],
    ])


def approval_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تأیید", callback_data="oc_approve"),
            InlineKeyboardButton("❌ رد", callback_data="oc_reject"),
        ],
        [InlineKeyboardButton("⏹ توقف کامل", callback_data="oc_stop")],
    ])


def timeout_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("ادامه بده", callback_data="oc_keep_watching"),
            InlineKeyboardButton("متوقف کن", callback_data="oc_stop"),
        ]
    ])


def provider_keyboard(active: str, statuses: List) -> InlineKeyboardMarkup:
    """
    فهرست سرویس‌ها با وضعیت کلید هر کدام.

    با زدن هر سرویس، هم فعال می‌شود و هم فرصت وارد کردن کلیدش را می‌دهد.
    """
    rows = []
    for name, src, _note in statuses:
        mark = "✅ " if name == active else ""
        if src == "none":
            flag = "🔑"
        elif src == "saved":
            flag = "🔒"
        else:
            flag = "🌱"
        rows.append([
            InlineKeyboardButton(
                f"{mark}{flag} {name}", callback_data=f"provider:{name}"
            )
        ])
    rows.append([
        InlineKeyboardButton("🔄 راهنما", callback_data="provider_help")
    ])
    return InlineKeyboardMarkup(rows)


def provider_key_actions(name: str, has_key: bool) -> InlineKeyboardMarkup:
    """بعد از انتخاب سرویس: تأیید اتصال یا ثبت/پاک کردن کلید."""
    rows = [[
        InlineKeyboardButton("🔑 وارد کردن کلید", callback_data=f"key_enter:{name}"),
    ]]
    if has_key:
        rows.append([
            InlineKeyboardButton("🗑 پاک کردن کلید", callback_data=f"key_clear:{name}")
        ])
    rows.append([
        InlineKeyboardButton("🔙 بازگشت به سرویس‌ها", callback_data="provider_back")
    ])
    return InlineKeyboardMarkup(rows)


def tabs_keyboard(
    tabs: List[dict], has_new_tab: bool = True
) -> InlineKeyboardMarkup:
    """
    فهرست تب‌های OpenCode.

    عنوان تب‌ها از بیرون برنامه خوانده نمی‌شود، برای همین فقط شماره
    نشان داده می‌شود؛ شماره روی تصویر نوار تب هم با همین دکمه‌ها یکی است.
    """
    rows: List[List[InlineKeyboardButton]] = []
    line: List[InlineKeyboardButton] = []
    for tab in tabs:
        n = int(tab.get("index") or 0)
        if tab.get("active"):
            text = f"{n} · فعال"
        else:
            text = str(n)
        line.append(InlineKeyboardButton(text, callback_data=f"tab:{n}"))
        if len(line) == 5:
            rows.append(line)
            line = []
    if line:
        rows.append(line)

    extra = []
    if has_new_tab:
        extra.append(
            InlineKeyboardButton("➕ تب جدید", callback_data="tab_new")
        )
    extra.append(
        InlineKeyboardButton("🔄 به‌روزرسانی", callback_data="tabs_refresh")
    )
    rows.append(extra)
    rows.append([
        InlineKeyboardButton("✍️ ساخت پرامپت", callback_data="prompt_start")
    ])
    return InlineKeyboardMarkup(rows)


def help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📸 اسکرین‌شات", callback_data="noop"),
            InlineKeyboardButton("📂 برنامه‌ها", callback_data="apps:0"),
        ],
        [
            InlineKeyboardButton("🌐 تب مرورگر", callback_data="tabs_show"),
            InlineKeyboardButton("✍️ ساخت پرامپت", callback_data="prompt_start"),
        ],
        [
            InlineKeyboardButton("🖱 کنترل ماوس", callback_data="mouse_enter"),
        ],
        [
            InlineKeyboardButton("⏻ خاموش", callback_data="shutdown_ask"),
        ],
    ])


def mouse_keyboard(step: int) -> InlineKeyboardMarkup:
    """
    کیبورد کنترل ماوس/کیبورد (حالت /mouse).

    همه‌ی callback_dataها با «mouse:» شروع می‌شوند و کوتاه‌اند (سقف تلگرام
    ۶۴ بایت است). برچسب‌ها عمداً ایموجی + کلمه‌ی فارسی‌اند تا حتی اگر
    تلگرام جهت ردیف را در چت RTL عوض کرد، هر دکمه خودش گویا باشد.
    """
    rows: List[List[InlineKeyboardButton]] = [
        # حرکت ماوس — گام را دکمه‌ی وسطی ردیف بعدی عوض می‌کند
        [
            InlineKeyboardButton("⬆️ بالا", callback_data="mouse:mv:up"),
            InlineKeyboardButton("⬇️ پایین", callback_data="mouse:mv:down"),
            InlineKeyboardButton("⬅️ چپ", callback_data="mouse:mv:left"),
            InlineKeyboardButton("➡️ راست", callback_data="mouse:mv:right"),
        ],
        # گام حرکت (فاصله‌ی هر فلش به پیکسل)
        [
            InlineKeyboardButton("گام −", callback_data="mouse:step:down"),
            InlineKeyboardButton(f"📏 {step}px", callback_data="noop"),
            InlineKeyboardButton("گام +", callback_data="mouse:step:up"),
        ],
        [
            InlineKeyboardButton("👆 کلیک چپ", callback_data="mouse:click:left"),
            InlineKeyboardButton("👆 کلیک راست", callback_data="mouse:click:right"),
            InlineKeyboardButton("👆👆 دوکلیک", callback_data="mouse:click:double"),
        ],
        # Shift+کلیک برای انتخاب کردن، نگه/رها برای درگ با کشیدن
        [
            InlineKeyboardButton("✨ Shift+کلیک", callback_data="mouse:click:shift"),
            InlineKeyboardButton("🤝 نگه داشتن", callback_data="mouse:down"),
            InlineKeyboardButton("👐 رها کردن", callback_data="mouse:up"),
        ],
        [
            InlineKeyboardButton("🔽 اسکرول پایین", callback_data="mouse:scroll:down"),
            InlineKeyboardButton("🔼 اسکرول بالا", callback_data="mouse:scroll:up"),
        ],
        [
            InlineKeyboardButton("Enter", callback_data="mouse:key:enter"),
            InlineKeyboardButton("Esc", callback_data="mouse:key:esc"),
            InlineKeyboardButton("Tab", callback_data="mouse:key:tab"),
            InlineKeyboardButton("Space", callback_data="mouse:key:space"),
            InlineKeyboardButton("⌫", callback_data="mouse:key:backspace"),
            InlineKeyboardButton("Del", callback_data="mouse:key:delete"),
        ],
        [
            InlineKeyboardButton("Ctrl+A", callback_data="mouse:combo:ctrl+a"),
            InlineKeyboardButton("Ctrl+C", callback_data="mouse:combo:ctrl+c"),
            InlineKeyboardButton("Ctrl+V", callback_data="mouse:combo:ctrl+v"),
            InlineKeyboardButton("Ctrl+X", callback_data="mouse:combo:ctrl+x"),
            InlineKeyboardButton("Ctrl+Z", callback_data="mouse:combo:ctrl+z"),
        ],
        [
            InlineKeyboardButton("🔄 عکس تازه", callback_data="mouse:shot"),
            InlineKeyboardButton("❎ خروج از حالت", callback_data="mouse:exit"),
        ],
    ]
    return InlineKeyboardMarkup(rows)