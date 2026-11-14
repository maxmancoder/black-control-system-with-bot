# -*- coding: utf-8 -*-
"""
ربات تلگرام برای کنترل از راه دور لپ‌تاپ.

اجرا:
    python main.py

نکته‌ی امنیتی: فقط user id های داخل OWNER_IDS در config.py اجازه‌ی کنترل دارند.
بقیه‌ی پیام‌ها بی‌پاسخ رها می‌شوند.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import time
import urllib.parse
from typing import Dict, List, Optional, Tuple

import telegram.error
import pyautogui
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from core import (
    ai,
    apps,
    imaging,
    jobs,
    mouse,
    opencode,
    opencode_ui,
    power,
    remote,
    secrets,
    security,
    singleton,
    ui,
    windows,
)

class _NoTelegramNoise(logging.Filter):
    """
    کنسول را از پیام‌های داخلی کتابخانه‌ی تلگرام پاک می‌کند.

    دلیل: وقتی توکن اشتباه است، خود کتابخانه یک traceback کامل چاپ می‌کند
    که هیچ کمکی به کاربر نمی‌کند و فقط پنجره را پر می‌کند. جزئیات کامل در
    logs\bot.log نوشته می‌شود، پس چیزی از دست نمی‌رود.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith("telegram")


def _setup_logging() -> None:
    log_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    )

    file_handler = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(log_fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_fmt)
    console_handler.addFilter(_NoTelegramNoise())

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # این‌ها خیلی پرحرف‌اند و چیز مفیدی به کنسول اضافه نمی‌کنند
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


logger = logging.getLogger("bot")

HELP_TEXT = """
🖥 **ربات کنترل لپ‌تاپ**

**دکمه‌های پایین چت**
📸 اسکرین‌شات — عکس کل صفحه (عکس + فایل خوانا)
📷 دوربین — عکس گرفتن از دوربین لپ‌تاپ
📂 برنامه‌ها — لیست برنامه‌های نصب‌شده با جست‌وجو
✍️ ساخت پرامپت — تبدیل متن خام به پرامپت منظم با AI
🌐 تب OpenCode — تب‌های باز خود برنامه و تب جدید
🖱 کنترل ماوس — حرکت ماوس، کلیک و تایپ از داخل چت
🔍 گوگل — جست‌وجو در گوگل و فعال شدن کنترل ماوس روی نتایج
⏻ خاموش — خاموش کردن با تأیید و امکان لغو

**دستورها**
/shot — اسکرین‌شات فوری
/camera — عکس از دوربین لپ‌تاپ (از دوربین انتخاب‌شده)
`/camera_setup` — شناسایی دوربین‌ها و انتخاب دوربین پیش‌فرض
/apps — لیست برنامه‌ها
/open <نام> — باز کردن برنامه از روی نام
/search <متن> — جست‌وجو در برنامه‌ها
/prompt <متن خام> — ساخت پرامپت
/provider — انتخاب سرویس AI و وارد کردن کلید
/tabopencode — تب‌های باز خود برنامه‌ی OpenCode
/send — گرفتن پرامپت از شما و فرستادن به کادر OpenCode
/mouse — حالت کنترل ماوس و کیبورد (متن فارسی هم تایپ می‌شود)
/google <متن> — جست‌وجو در گوگل؛ بعد از باز شدن نتایج، کنترل ماوس فعال می‌شود
/remote — کنسول ریموت وب (استریم زنده‌ی صفحه + ماوس و کیبورد از مرورگر، با تونل کلودفلر)
`/remote_stop` — توقف کنسول ریموت
/approve — تأیید درخواست معلق OpenCode
/reject — رد درخواست معلق OpenCode
/stop — توقف کار جاری
/lock — قفل کردن لپ‌تاپ
/shutdown — خاموش کردن با تأیید
/cancel — لغو خاموشی یا خروج از حالت جاری
/status — وضعیت کلی
/help — همین راهنما

**ترتیب کار با OpenCode**
۱) /tabopencode  → تب دلخواه را از روی تصویر انتخاب کن
۲) /send  → پرامپت را بنویس تا داخل کادر چت نوشته و Enter زده شود
۳) اگر منتظر تأیید اجرای دستور ماند، /approve یا /reject را بزن

🔒 فقط مالک می‌تواند از این بات استفاده کند.
""".strip()


# ------------------------------------------------------------------ کمکی‌ها
def _session(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, object]:
    if "session" not in context.user_data:
        context.user_data["session"] = {}
    return context.user_data["session"]


def _owner_ids() -> List[int]:
    return sorted(config.OWNER_IDS)


async def _edit(message, text: str, **kwargs) -> bool:
    """
    ویرایش امن پیام.

    اگر متن جدید با متن فعلی یکسان باشد، تلگرام خطای
    «Message is not modified» می‌دهد. این تابع آن را بی‌صدا رد می‌کند.
    """
    try:
        await message.edit_text(text, **kwargs)
        return True
    except TelegramError as exc:
        if "not modified" in str(exc).lower():
            logger.debug("پیام از قبل همین متن را دارد؛ ویرایش رد شد.")
            return False
        raise


async def _safe_reply(message, text: str, **kwargs) -> bool:
    """پاسخ دادن با گرفتن خطاهای معمول تلگرام به‌جای رها کردن استثنا."""
    try:
        await message.reply_text(text, **kwargs)
        return True
    except TelegramError as exc:
        if "not modified" in str(exc).lower():
            return False
        logger.exception("ارسال پیام ناموفق بود")
        return False


async def _delete_quietly(message) -> None:
    """پاک کردن پیام کاربر (مثلاً پیامی که کلید API در آن بود)."""
    try:
        await message.delete()
    except TelegramError:
        logger.debug("پاک کردن پیام ممکن نشد.", exc_info=True)


async def _shot(bot, chat_id: int, caption: str = "") -> bool:
    """
    گرفتن و فرستادن اسکرین‌شات، بدون اینکه خطایش کل عملیات را خراب کند.

    دلیل این احتیاط: قبلاً خطای شبکه هنگام ارسال فایل باعث می‌شد کاربر
    پیام «باز کردن برنامه ناموفق بود» ببیند، در حالی که برنامه باز شده بود.
    """
    try:
        img = await imaging.grab_screen_async()
        return await imaging.send_screenshot_to(bot, chat_id, img, caption)
    except Exception as exc:
        logger.warning("ارسال اسکرین‌شات ناموفق بود: %s", exc)
        try:
            await bot.send_message(
                chat_id,
                f"⚠️ اسکرین‌شات ارسال نشد: {exc}\n(خودِ کار انجام شده است.)",
            )
        except Exception:
            pass
        return False


# ------------------------------------------------------------------ شروع
@security.owner_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        f"سلام 👋\n\n{HELP_TEXT}",
        reply_markup=ui.main_menu(),
        parse_mode="Markdown",
    )


@security.owner_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        HELP_TEXT, reply_markup=ui.help_keyboard(), parse_mode="Markdown"
    )


# ------------------------------------------------------------------ اسکرین‌شات
async def _do_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    bot = context.bot

    try:
        await bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
        ok = await _shot(bot, chat_id, "📸 اسکرین‌شات لحظه‌ای")
        if not ok:
            await _safe_reply(
                update.effective_message,
                "⚠️ اسکرین‌شات گرفته شد ولی ارسال نشد. لطفاً دوباره تلاش کن.",
            )
    except Exception as exc:
        logger.exception("گرفتن اسکرین‌شات ناموفق بود")
        await _safe_reply(
            update.effective_message,
            f"❌ گرفتن اسکرین‌شات ناموفق بود:\n{exc}",
        )


@security.owner_only
async def cmd_shot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _do_screenshot(update, context)


@security.owner_only
async def cmd_camera(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await on_button_camera(update, context)


@security.owner_only
async def cmd_camera_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """شناسایی دوربین‌های متصل و نمایش لیست برای انتخاب."""
    await _camera_detect_and_show(update, context)


@security.owner_only
async def on_button_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _do_screenshot(update, context)


# ------------------------------------------------------------------ دوربین
@security.owner_only
async def on_button_camera(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """عکس گرفتن از دوربین لپ‌تاپ و فرستادن آن به کاربر.

    همیشه از دوربین ذخیره‌شده (در settings.json) استفاده می‌شود؛
    اگر کاربر دوربینی انتخاب نکرده باشد، ایندکس 0 (دوربین پیش‌فرض).
    """
    chat_id = update.effective_chat.id
    bot = context.bot
    try:
        await bot.send_chat_action(chat_id, ChatAction.UPLOAD_PHOTO)
        img = await imaging.grab_camera_photo_async()
        saved_idx = imaging.saved_camera_index()
        await bot.send_photo(
            chat_id,
            photo=imaging.encode_jpeg(img, quality=config.SCREENSHOT_JPEG_QUALITY),
            caption=f"📷 عکس از دوربین {saved_idx}",
            reply_markup=ui.camera_photo_keyboard(),
        )
    except Exception as exc:
        logger.exception("عکس دوربین ناموفق بود")
        await _safe_reply(
            update.effective_message,
            f"❌ عکس دوربین گرفته نشد:\n{exc}\n\n"
            "با دکمه‌ی «تنظیم دوربین» زیر عکس (یا دستور /camera_setup) "
            "دوربین‌های متصل را شناسایی و یکی را انتخاب کن.",
        )


async def _camera_detect_and_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دوربین‌های متصل را شناسایی کرده و لیست انتخاب را نشان می‌دهد."""
    try:
        cameras = await imaging.detect_cameras_async()
    except Exception as exc:
        logger.exception("شناسایی دوربین ناموفق بود")
        await _safe_reply(
            update.effective_message,
            f"❌ شناسایی دوربین ناموفق بود:\n{exc}",
        )
        return
    if not cameras:
        await _safe_reply(
            update.effective_message,
            "🔍 هیچ دوربینی شناسایی نشد. اتصال دوربین (USB یا داخلی) "
            "و بسته‌نبودن آن توسط برنامه‌ی دیگر را بررسی کن.",
        )
        return
    saved_idx = imaging.saved_camera_index()
    lines = ["🔍 دوربین‌های شناسایی‌شده:\n"]
    for cam in cameras:
        mark = "✅" if cam["index"] == saved_idx else "•"
        lines.append(f"{mark} دوربین {cam['index']} — {cam['width']}×{cam['height']}")
    lines.append(f"\nالان دوربین {saved_idx} انتخاب است. یکی را بزن تا ذخیره شود.")
    await _safe_reply(
        update.effective_message,
        "\n".join(lines),
        reply_markup=ui.camera_select_keyboard(cameras, saved_idx),
    )


# ------------------------------------------------------------------ برنامه‌ها
async def _app_index(context: ContextTypes.DEFAULT_TYPE) -> List[Dict[str, str]]:
    """لیست کامل برنامه‌ها (با احترام به کش یک‌ساعته)."""
    if "all_apps" not in context.user_data:
        context.user_data["all_apps"] = await apps.load_apps()
    return context.user_data["all_apps"]


def _paginate(items: List[Dict[str, str]], page: int, size: int):
    total_pages = max(1, (len(items) + size - 1) // size)
    page = max(0, min(page, total_pages - 1))
    start = page * size
    return items[start:start + size], page, total_pages


async def _send_apps_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    page: int = 0,
    notice: str = "",
    edit: bool = False,
) -> None:
    all_apps = await _app_index(context)
    if not all_apps:
        text = (
            "لیست برنامه‌ها خالی است. با دکمه‌ی «به‌روزرسانی لیست» دوباره "
            "تلاش می‌کنم."
        )
        if edit:
            await _edit_callback(update, text, ui.apps_keyboard([], 0, 1))
        else:
            await update.effective_message.reply_text(
                text, reply_markup=ui.apps_keyboard([], 0, 1)
            )
        return

    page_items, page, total_pages = _paginate(all_apps, page, config.APPS_PAGE_SIZE)
    favorites = apps.get_favorites(all_apps, limit=3)
    header = notice or "📂 **برنامه‌های نصب‌شده**"
    header += f"\nتعداد کل: {len(all_apps)} — صفحه‌ی {page + 1} از {total_pages}"
    header += "\nروی هر برنامه بزن تا اجرا شود."
    kb = ui.apps_keyboard(page_items, page, total_pages, favorites)

    if edit:
        await _edit_callback(update, header, kb)
    else:
        await update.effective_message.reply_text(
            header, reply_markup=kb, parse_mode="Markdown"
        )


async def _edit_query(query, text: str, reply_markup=None, **kwargs) -> bool:
    """
    ویرایش امن پیامی که دکمه‌ی شناور روی آن است.

    دو نکته که رعایتشان لازم است:

      ۱) تلگرام فقط «کیبورد شناور» را می‌پذیرد. کیبورد پایین چت
         (ReplyKeyboard) با ویرایش پیام قابل جایگزینی نیست و تلگرام
         خطای «Inline keyboard expected» می‌دهد. اگر کیبورد پایین چت
         داده شود، نادیده گرفته می‌شود — چون کیبورد پایین چت ذاتاً
         همیشه حاضر است و لازم نیست دوباره فرستاده شود.

      ۲) خطاهای تلگرام اینجا نباید به ترمینال نشت کنند؛ این فقط ظاهر
         پیام است و ارزش توقف کار را ندارد.

    خروجی: True اگر ویرایش انجام شد.
    """
    if reply_markup is not None and not isinstance(
        reply_markup, InlineKeyboardMarkup
    ):
        logger.debug(
            "کیبورد پایین چت با ویرایش پیام ارسال نمی‌شود؛ نادیده گرفته شد."
        )
        reply_markup = None

    try:
        await query.edit_message_text(text, reply_markup=reply_markup, **kwargs)
        return True
    except TelegramError as exc:
        text_lower = str(exc).lower()
        if "not modified" in text_lower:
            logger.debug("پیام از قبل همین متن را دارد.")
            return False
        if "inline keyboard expected" in text_lower:
            logger.error("کیبورد با پیام ناسازگار است: %s", exc)
            return False
        logger.warning("ویرایش پیام ناموفق بود: %s", exc)
        return False


async def _edit_callback(update: Update, text: str, keyboard) -> None:
    """ویرایش پیامی که دکمه‌ی شناور روی آن است."""
    query = update.callback_query
    if not query:
        return
    await _edit_query(query, text, keyboard, parse_mode="Markdown")


@security.owner_only
async def cmd_apps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    status = await update.effective_message.reply_text("در حال خواندن لیست برنامه‌ها…")
    try:
        await _send_apps_page(update, context, 0)
        await _delete_quietly(status)
    except Exception as exc:
        logger.exception("ساخت لیست برنامه‌ها ناموفق بود")
        await _edit(status, f"خطا در خواندن لیست برنامه‌ها:\n{exc}")


@security.owner_only
async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.effective_message.reply_text(
            "نام برنامه را بنویسید. مثال:\n`/open chrome`",
            parse_mode="Markdown",
        )
        return
    query = " ".join(args)
    all_apps = await _app_index(context)
    matches = apps.search(all_apps, query, limit=10)
    if not matches:
        await update.effective_message.reply_text(
            f"برنامه‌ای با نام «{query}» پیدا نشد.\n"
            "با /apps لیست را ببینید یا از دکمه‌ی جست‌وجو استفاده کنید."
        )
        return
    if len(matches) > 1:
        await update.effective_message.reply_text(
            "چند مورد پیدا شد — یکی را انتخاب کن:",
            reply_markup=ui.search_result_keyboard(matches),
        )
        return
    await _launch_app(update, context, matches[0]["app_id"], matches[0]["name"])


async def _launch_app(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    app_id: str,
    name: str,
) -> None:
    """برنامه را اجرا می‌کند و منتظر «کامل باز شدن» آن می‌ماند."""
    chat_id = update.effective_chat.id
    bot = context.bot
    opening_text = f"🚀 در حال باز کردن {name}…"

    # نکته: متن اولیه دقیقاً همان متنی است که بعداً ویرایش می‌شود، پس ویرایش
    # اول باید حذف شود وگرنه تلگرام خطای «Message is not modified» می‌دهد.
    status = await update.effective_message.reply_text(opening_text)

    async def job():
        await asyncio.to_thread(apps.bump_usage, app_id)
        await _open_and_wait(bot, chat_id, name, app_id)

    try:
        await jobs.queue.run(f"open:{name}", job)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("باز کردن برنامه ناموفق بود")
        await _safe_reply(
            status, f"❌ باز کردن «{name}» ناموفق بود:\n{exc}"
        )


async def _open_and_wait(bot, chat_id: int, name: str, app_id: str) -> None:
    """
    تشخیص «کامل باز شدن» ترکیبی است:
      ۰) اگر برنامه از قبل باز باشد، همان پنجره را جلو می‌آوریم
      ۱) لیست پنجره‌های باز قبل از اجرا ذخیره می‌شود
      ۲) منتظر پنجره‌ی جدید می‌مانیم
      ۳) منتظر می‌مانیم پنجره پاسخ‌گو شود و لود کامل شود
      ۴) ۲ تا ۳ ثانیه مهلت اضافه برای لود محتوا
    """
    # اکثر برنامه‌ها (از جمله OpenCode که Electron است) تک‌INSTANCE هستند:
    # با اجرا دوباره، پنجره‌ی جدیدی نمی‌سازند و فقط همان قبلی فعال می‌شود؛
    # بدون این بررسی، ۴۵ ثانیه معطل شده و خطا گزارش می‌شد.
    existing = await asyncio.to_thread(windows.find_window, [name])
    if existing:
        focused = await asyncio.to_thread(windows.focus, existing)
        await asyncio.sleep(0.3)
        await bot.send_message(
            chat_id,
            f"✅ «{name}» از قبل باز بود؛ "
            + ("پنجره‌اش را جلو آوردم." if focused else "جلو آوردنش ممکن نشد."),
        )
        await _shot(bot, chat_id, f"«{name}» از قبل باز بود.")
        return

    def snapshot():
        return windows.window_handles()

    before = await asyncio.to_thread(snapshot)

    await asyncio.to_thread(windows.launch, app_id)
    await asyncio.to_thread(apps.bump_usage, app_id)

    found = await asyncio.to_thread(
        windows.wait_for_new_window,
        before,
        None,
        config.APP_OPEN_TIMEOUT,
        0.5,
    )

    if not found:
        # شاید برنامه تک‌INSTANCE باشد و به جای ساختن پنجره‌ی جدید،
        # همان پنجره‌ی موجود را فعال کرده باشد (یا عنوانش دیر آمده باشد).
        late = await asyncio.to_thread(windows.find_window, [name])
        if late:
            found = {"hwnd": late, "title": name}

    if not found:
        await bot.send_message(
            chat_id,
            f"⏳ پنجره‌ی جدیدی برای «{name}» ظاهر نشد (تا {config.APP_OPEN_TIMEOUT} ثانیه).\n"
            "ممکن است هنوز در حال لود باشد — این اسکرین‌شات را می‌فرستم.",
        )
        await _shot(bot, chat_id, "احتمالاً هنوز در حال لود است.")
        return

    hwnd = found["hwnd"]
    title = str(found.get("title") or name)

    # پنجره ممکن است عنوانش را بعد از بالا آمدن کامل کند
    stable_title = await asyncio.to_thread(windows.wait_title_stable, hwnd, 8.0)
    if stable_title:
        title = stable_title
    # ویندوز ممکن است پنجره را دوباره بسازد؛ handle تازه را می‌گیریم
    hwnd = await asyncio.to_thread(
        windows.resolve_window, hwnd, [title, name]
    ) or hwnd

    await bot.send_message(chat_id, f"🪟 پنجره پیدا شد: {title}")

    responsive_hwnd = await asyncio.to_thread(
        windows.wait_until_responsive,
        hwnd,
        config.APP_OPEN_TIMEOUT,
        1.0,
        config.APP_STABLE_SECONDS,
        [title, name],
    )
    ok = responsive_hwnd is not None
    if ok:
        hwnd = responsive_hwnd

    await asyncio.to_thread(windows.focus, hwnd)
    await asyncio.sleep(0.4)

    if not ok:
        await bot.send_message(
            chat_id,
            f"⚠️ پنجره‌ی «{title}» بعد از {config.APP_OPEN_TIMEOUT} ثانیه "
            "پاسخ‌گو نشد. اسکرین‌شات وضعیت فعلی:",
        )

    await _shot(bot, chat_id, f"✅ «{name}» باز شد." if ok else "وضعیت فعلی:")


@security.owner_only
async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        _session(context)["mode"] = "search"
        await update.effective_message.reply_text(
            "🔍 اسم برنامه را بنویسید. برای خروج /cancel را بزنید."
        )
        return
    await _run_search(update, context, " ".join(args))


async def _run_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str) -> None:
    all_apps = await _app_index(context)
    matches = apps.search(all_apps, query, limit=config.MAX_SEARCH_RESULTS)
    if not matches:
        await update.effective_message.reply_text(f"چیزی برای «{query}» پیدا نشد.")
        return
    shown = matches[:30]
    text = (
        f"🔍 نتیجه‌ی «{query}»: {len(matches)} مورد\n"
        f"نمایش {len(shown)} مورد اول."
    )
    await update.effective_message.reply_text(
        text, reply_markup=ui.search_result_keyboard(shown)
    )


# ------------------------------------------------------------------ پرامپت
@security.owner_only
async def cmd_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(context)
    raw = " ".join(context.args) if context.args else ""

    if not raw:
        session["mode"] = "prompt"
        await update.effective_message.reply_text(
            "✍️ متن خام درخواستت را بنویسید. هرچه کوتاه باشد کافی است؛ "
            "بقیه‌اش را مدل منظم می‌کند.\nبرای خروج /cancel را بزنید.",
            reply_markup=ui.remove_keyboard(),
        )
        return

    session["mode"] = ""
    await _generate_prompt(update, context, raw)


async def _generate_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, raw: str) -> None:
    session = _session(context)
    session["raw"] = raw

    provider = ai.active_provider()
    status = await update.effective_message.reply_text(
        f"🧠 در حال ساخت پرامپت با {provider['name']}…"
    )

    # اگر کلیدی برای سرویس فعال نیست، اول بپرس تا الکه همه‌ی سرویس‌ها
    # امتحان نشوند و کاربر متوجه نشود چه اتفاقی افتاده.
    if not ai.api_key(provider) and provider.get("auth", "bearer") == "bearer":
        session["mode"] = ""
        session["pending_raw"] = raw
        await _edit(
            status,
            f"🔑 برای سرویس «{provider['name']}» کلید API ثبت نشده است.\n\n"
            "کلید را بفرستید تا ذخیره و تست شود. پیام شما بعد از ذخیره پاک می‌شود.\n"
            "برای انصراف /cancel را بزنید.",
            reply_markup=ui.provider_key_actions(provider["name"], False),
        )
        session["mode"] = "api_key"
        session["key_provider"] = provider["name"]
        return

    ok, text, used = await ai.build_prompt(raw)

    if not ok:
        session["last"] = ""
        await _edit(status, text + "\n\nمتن خام شما نگه داشته شد.")
        return

    session["last"] = text
    ai.save_prompt(text)

    await _edit(
        status,
        f"✅ پرامپت ساخته شد (سرویس: {used})\n\n"
        f"طول: {len(text)} کاراکتر\n\n"
        f"`{text}`",
        parse_mode="Markdown",
        reply_markup=ui.prompt_actions(),
    )


# ------------------------------------------------------------------ OpenCode
# ------------------------------------------------------------------ تب OpenCode
def _find_opencode_window() -> Optional[int]:
    """پنجره‌ی برنامه‌ی دسکتاپ OpenCode را پیدا می‌کند."""
    return windows.find_window(config.OPENCODE_WINDOW_TITLES)


def _prepare_opencode():
    """
    پنجره‌ی OpenCode را جلو می‌آورد، maximize می‌کند و تب‌ها را می‌خواند.

    maximize لازم است چون مختصات کلیک بر پایه‌ی نسبت‌های پنجره حساب می‌شود.

    خروجی: (نتیجه‌ی تشخیص، hwnd) — نتیجه None یعنی پنجره پیدا نشد.
    اگر پنجره به جلو نیاید، نتیجه‌ی ok=False برمی‌گردد تا نه اسکرین‌شات
    و نه کلیک، به پنجره‌ی اشتباه (مثلاً مرورگر) نخورد.
    """
    not_foreground = {
        "ok": False,
        "reason": (
            "پنجره‌ی OpenCode به جلو نیامد؛ احتمالاً پنجره‌ی دیگری "
            "روی آن است. دوباره تلاش کن."
        ),
        "tabs": [],
        "new_tab": None,
        "strip": None,
    }

    hwnd = _find_opencode_window()
    if not hwnd:
        return None, None

    windows.maximize(hwnd)
    windows.focus(hwnd)
    time.sleep(0.7)

    if not windows.is_foreground(hwnd):
        # بدون این بررسی، اسکرین‌شات و کلیک به پنجره‌ی جلویی می‌خورد
        # (مثلاً تب‌های Chrome با نوار تب OpenCode اشتباه گرفته می‌شوند).
        return dict(not_foreground), hwnd

    rect = windows.window_rect(hwnd)
    if not rect:
        return None, hwnd

    screen = imaging.grab_screen()
    result = opencode_ui.detect_tabs(screen, rect)
    return result, hwnd


async def _send_tab_strip(bot, chat_id: int, screen, result) -> None:
    """تصویر نوار تب را با شماره‌های روی آن می‌فرستد."""
    strip = opencode_ui.annotate_strip(screen, result)
    if strip is None:
        return
    buf = io.BytesIO()
    strip.convert("RGB").save(buf, format="JPEG", quality=92)
    await bot.send_document(
        chat_id=chat_id,
        document=buf.getvalue(),
        filename="opencode_tabs.png",
        caption=(
            "نوار تب‌های OpenCode. شماره‌ها روی تصویر با شماره‌ی دکمه‌های "
            "پایین یکی است."
        ),
    )


@security.owner_only
async def cmd_tabs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    تب‌های باز خودِ برنامه‌ی OpenCode را نشان می‌دهد.

    عنوان تب‌ها از بیرون برنامه قابل خواندن نیست (Electron این اطلاعات را
    به سیستم‌عامل نمی‌دهد)، برای همین شماره‌ها را روی تصویر نوار تب می‌نویسیم
    تا انتخاب درست باشد.
    """
    chat_id = update.effective_chat.id
    bot = context.bot
    status = await update.effective_message.reply_text(
        "🌐 در حال خواندن تب‌های OpenCode…"
    )

    def _work():
        return _prepare_opencode()

    result, hwnd = await asyncio.to_thread(_work)

    if result is None:
        await _edit(
            status,
            "❌ پنجره‌ی برنامه‌ی OpenCode پیدا نشد.\n"
            "اول OpenCode را باز کنید، بعد دوباره تلاش کنید.\n"
            "(عنوان پنجره باید شامل «opencode» باشد — "
            "فهرست عنوان‌ها در config.py است.)",
        )
        return

    if not result["ok"]:
        await _edit(
            status,
            f"❌ {result['reason']}\n"
            "اسکرین‌شات را می‌فرستم تا ببینیم چه شکلی است.",
        )
        await _shot(bot, chat_id, "وضعیت فعلی OpenCode.")
        return

    session = _session(context)
    session["tabs"] = result["tabs"]
    session["tabs_hwnd"] = hwnd

    screen = await imaging.grab_screen_async()
    try:
        await _send_tab_strip(bot, chat_id, screen, result)
    except Exception as exc:
        logger.warning("ارسال تصویر نوار تب ناموفق بود: %s", exc)

    count = len(result["tabs"])
    lines = [
        "🌐 **تب‌های OpenCode**",
        "",
        f"{count} تب باز است.",
        "",
        "شماره‌ی هر تب را از روی تصویر بالا ببین و همان شماره را بزن.",
        "با «➕ تب جدید» یک تب تازه در OpenCode باز می‌شود.",
        "",
        "بعد از انتخاب تب، با `/send` پرامپت را بفرست تا داخل کادر "
        "OpenCode نوشته و Enter زده شود.",
    ]
    await _edit(
        status,
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=ui.tabs_keyboard(result["tabs"], bool(result["new_tab"])),
    )


@security.owner_only
async def on_button_tabs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_tabs(update, context)


async def _click_opencode_tab(number: int) -> tuple:
    """
    روی تب شماره‌دار OpenCode کلیک می‌کند.

    مختصات هر بار دوباره تشخیص داده می‌شود، چون ممکن است تب جدیدی اضافه
    شده باشد و شماره‌ها جابه‌جا شده باشده باشند.
    """
    def _work():
        result, hwnd = _prepare_opencode()
        if result is None:
            return False, "پنجره‌ی OpenCode پیدا نشد."
        if not result["ok"]:
            return False, f"نوار تب خوانده نشد: {result['reason']}"
        tabs = result["tabs"]
        if not 1 <= number <= len(tabs):
            return False, (
                f"الان {len(tabs)} تب باز است، شماره‌ی {number} وجود ندارد."
            )
        if not windows.is_foreground(hwnd):
            return False, "پنجره‌ی دیگری جلو آمد؛ کلیک انجام نشد."
        point = tabs[number - 1]
        pyautogui.click(int(point["x"]), int(point["y"]))
        time.sleep(0.5)
        # ماوس را از نوار تب کنار می‌کشیم تا حالت hover روی تب نماند
        # و تشخیص بعدی درست باشد؛ ضمن اینکه کلیک بعدی کاربر به تب نخورد.
        pyautogui.moveTo(40, 40)
        return True, f"روی تب {number} کلیک کردم."
    try:
        return await asyncio.to_thread(_work)
    except Exception as exc:
        logger.exception("کلیک روی تب ناموفق بود")
        return False, str(exc)


async def _click_opencode_new_tab() -> tuple:
    """روی دکمه‌ی «تب جدید» در نوار تب OpenCode کلیک می‌کند."""
    def _work():
        result, hwnd = _prepare_opencode()
        if result is None:
            return False, "پنجره‌ی OpenCode پیدا نشد."
        if not result["ok"]:
            return False, f"نوار تب خوانده نشد: {result['reason']}"
        new_tab = result.get("new_tab")
        if not new_tab:
            return False, "دکمه‌ی تب جدید در نوار پیدا نشد."
        if not windows.is_foreground(hwnd):
            return False, "پنجره‌ی دیگری جلو آمد؛ کلیک انجام نشد."
        pyautogui.click(int(new_tab["x"]), int(new_tab["y"]))
        time.sleep(1.2)
        pyautogui.moveTo(40, 40)
        return True, "تب جدید در OpenCode باز شد."

    try:
        return await asyncio.to_thread(_work)
    except Exception as exc:
        logger.exception("باز کردن تب جدید ناموفق بود")
        return False, str(exc)


@security.owner_only
async def cmd_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    پرامپت را از کاربر می‌گیرد و داخل کادر OpenCode می‌نویسد.

    اگر متنی همراه دستور داده شود همان استفاده می‌شود، وگرنه از کاربر
    پرسیده می‌شود.
    """
    session = _session(context)
    prompt = " ".join(context.args) if context.args else ""

    if not prompt:
        built = session.get("last") or ai.load_prompt()
        if built:
            rows = [
                [
                    InlineKeyboardButton(
                        "🚀 ارسال پرامپت ساخته‌شده", callback_data="prompt_send"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "✍️ نوشتن پرامپت جدید", callback_data="send_ask"
                    )
                ],
            ]
            await _safe_reply(
                update.effective_message,
                "پرامپتی از قبل ساخته شده. می‌خواهی همان را بفرستم "
                "یا پرامپت تازه‌ای بنویسی؟\n\n"
                "برای فرستادن مستقیم، می‌توانی بنویسی:\n"
                "`/send متن پرامپت`",
                reply_markup=InlineKeyboardMarkup(rows),
            )
            return

        session["mode"] = "send_prompt"
        await _safe_reply(
            update.effective_message,
            "✍️ پرامپتی که باید به OpenCode داده شود را بنویسید.\n"
            "همان متن داخل کادر چت OpenCode نوشته و Enter زده می‌شود.\n"
            "برای انصراف /cancel را بزنید.",
            reply_markup=ui.remove_keyboard(),
        )
        return

    await _run_opencode(update, context, prompt)


async def _run_opencode(
    update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str
) -> None:
    chat_id = update.effective_chat.id
    bot = context.bot
    session = _session(context)

    if opencode.is_running() or jobs.queue.busy:
        await update.effective_message.reply_text(
            "⚠️ یک کار اتوماسیون دیگر در حال اجراست. صبر کن یا با /stop متوقفش کن."
        )
        return

    session["prompt"] = prompt

    async def job():
        outcome, elapsed = await opencode.send_prompt(
            prompt, bot, chat_id, lambda: jobs.queue.is_cancelled()
        )

        if outcome == "awaiting_approval":
            await _shot(bot, chat_id, "منتظر تصمیم شما درباره‌ی درخواست OpenCode")
            await bot.send_message(
                chat_id,
                "⚠️ OpenCode منتظر تأیید اجرای دستور یا ویرایش فایل است.\n"
                "با «تأیید» اجازه بده، با «رد» رد کن، "
                "یا با «توقف کامل» کل کار را متوقف کن.",
                reply_markup=ui.approval_keyboard(),
            )
            session["awaiting"] = True
            return

        if outcome == "completed":
            await opencode.capture_final(bot, chat_id)
            await bot.send_message(
                chat_id,
                f"✅ **تموم شد**\nمدت اجرا: {_fmt(elapsed)}",
                parse_mode="Markdown",
            )
            return

        if outcome == "cancelled":
            await bot.send_message(chat_id, "⏹ کار متوقف شد.")
            return

        if outcome == "timeout":
            await bot.send_message(
                chat_id,
                f"⏱️ کار هنوز در حال اجراست (سقف زمانی تمام شد).\n"
                f"مدت سپری‌شده: {_fmt(elapsed)}",
                reply_markup=ui.timeout_actions(),
            )
            return

        await bot.send_message(
            chat_id,
            "❌ پنجره‌ی OpenCode بسته شد یا برنامه کرش کرد؛ کار متوقف شد.\n"
            f"مدت: {_fmt(elapsed)}",
        )

    try:
        await jobs.queue.run("opencode", job)
    except asyncio.CancelledError:
        raise
    except opencode.Cancelled:
        await update.effective_message.reply_text("⏹ کار متوقف شد.")
    except opencode.PromptFailure as exc:
        await update.effective_message.reply_text(f"❌ {exc}")
    except Exception as exc:
        logger.exception("اجرای OpenCode ناموفق بود")
        await update.effective_message.reply_text(f"❌ خطای غیرمنتظره:\n{exc}")


def _fmt(seconds: float) -> str:
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    if m and s:
        return f"{m} دقیقه و {s} ثانیه"
    if m:
        return f"{m} دقیقه"
    return f"{s} ثانیه"


@security.owner_only
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    jobs.queue.cancel()
    opencode.request_cancel()
    ok, msg = await opencode.stop()
    await update.effective_message.reply_text(f"⏹ {msg}" if ok else f"ℹ️ {msg}")


async def _after_decision(update: Update, context: ContextTypes.DEFAULT_TYPE, msg: str) -> None:
    """
    بعد از تأیید یا رد، دوباره منتظر پایان کار می‌مانیم تا به کاربر خبر بدهیم.
    """
    session = _session(context)
    if not session.get("awaiting"):
        await update.effective_message.reply_text(msg)
        return

    session["awaiting"] = False
    chat_id = update.effective_chat.id
    bot = context.bot

    async def job():
        outcome, elapsed = await opencode.resume_watch(bot, chat_id)
        if outcome == "completed":
            await opencode.capture_final(bot, chat_id)
            await bot.send_message(
                chat_id,
                f"✅ **تموم شد**\nمدت اجرا: {_fmt(elapsed)}",
                parse_mode="Markdown",
            )
        elif outcome == "failed":
            await bot.send_message(chat_id, "❌ پنجره‌ی OpenCode بسته شد.")
        elif outcome == "timeout":
            await bot.send_message(
                chat_id, "⏱️ باز هم در حال اجراست. با /stop می‌توانی متوقفش کنی."
            )

    await update.effective_message.reply_text(msg)

    if jobs.queue.busy:
        return
    try:
        await jobs.queue.run("opencode-watch", job)
    except Exception:
        logger.exception("ادامه‌ی نظارت بر OpenCode ناموفق بود")


@security.owner_only
async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ok, msg = await opencode.approve()
    await _after_decision(
        update, context, f"✅ {msg}" if ok else f"⚠️ {msg}"
    )


@security.owner_only
async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ok, msg = await opencode.reject()
    await _after_decision(
        update, context, f"🚫 {msg}" if ok else f"⚠️ {msg}"
    )


# ------------------------------------------------------------------ سرویس AI
MODELS_PAGE_SIZE = 8


def _provider_by_name(name: str):
    """پیدا کردن تنظیمات یک سرویس AI از روی نام."""
    for p in config.AI_PROVIDERS:
        if p["name"].lower() == str(name or "").lower():
            return p
    return None


@security.owner_only
async def cmd_provider(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    active = ai.active_provider()["name"]
    statuses = ai.provider_status()
    lines = ["🔌 **سرویس‌های هوش مصنوعی**", ""]
    for name, src, note in statuses:
        mark = "✅ فعال — " if name == active else ""
        lines.append(f"{mark}**{name}**")
        lines.append(f"    {note}")
    lines.append(
        "\nروی هر سرویس بزن تا فعال شود، کلیدش را وارد کنی و مدل دلخواه را انتخاب کنی."
        "\nکلید داخل کد نوشته نمی‌شود؛ در data\\secrets.json ذخیره می‌شود و "
        "پیامِ کلید بعد از ذخیره پاک می‌شود."
    )
    await _safe_reply(
        update.effective_message,
        "\n".join(lines),
        reply_markup=ui.provider_keyboard(active, statuses),
        parse_mode="Markdown",
    )


async def _show_models(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    provider_name: str,
    page: int = 0,
    edit: bool = False,
    notice: str = "",
    force: bool = False,
) -> None:
    """نمایش لیست مدل‌های یک سرویس با فیلتر و صفحه‌بندی."""
    session = _session(context)
    provider = _provider_by_name(provider_name)
    if provider is None:
        text = "سرویس پیدا نشد."
        if edit:
            await _edit_callback(update, text)
        else:
            await _safe_reply(update.effective_message, text)
        return

    query_text = str(session.get("model_query") or "")
    session["model_provider"] = provider_name
    session["model_query"] = query_text

    ok, all_models, err = await ai.fetch_models(provider, force=force)
    if not ok:
        msg = f"❌ دریافت مدل‌های «{provider_name}» ناموفق بود:\n{err}"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 تلاش دوباره", callback_data=f"models_open:{provider_name}")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data=f"provider:{provider_name}")],
        ])
        if edit:
            await _edit_query(update.callback_query, msg, reply_markup=kb)
        else:
            await _safe_reply(update.effective_message, msg, reply_markup=kb)
        return

    filtered = ai.filter_models(all_models, query_text)
    if not filtered:
        lines = [f"📋 **مدل‌های {provider_name}**", ""]
        if notice:
            lines.extend([notice, ""])
        if query_text:
            lines.append(f"نتیجه‌ای برای «{query_text}» پیدا نشد.")
            lines.append("عبارت جست‌وجوی دیگری بفرست (مثلاً `free` برای مدل‌های رایگان) یا فیلتر را پاک کن.")
        else:
            lines.append("مدلی برای نمایش وجود ندارد.")
        kb = ui.models_keyboard([], 0, 1, query_text)
        text = "\n".join(lines)
        if edit:
            await _edit_query(update.callback_query, text, reply_markup=kb, parse_mode="Markdown")
        else:
            await _safe_reply(update.effective_message, text, reply_markup=kb, parse_mode="Markdown")
        return

    total_pages = max(1, (len(filtered) + MODELS_PAGE_SIZE - 1) // MODELS_PAGE_SIZE)
    page = max(0, min(int(page), total_pages - 1))
    session["model_page"] = page
    start = page * MODELS_PAGE_SIZE
    page_items = filtered[start:start + MODELS_PAGE_SIZE]

    current = ai.effective_model(provider)
    lines = [f"📋 **مدل‌های {provider_name}**"]
    if notice:
        lines.append(notice)
    if query_text:
        lines.append(f"جست‌وجو: `{query_text}`")
    lines.append(f"تعداد: {len(filtered)} مورد — صفحه {page + 1}/{total_pages}")
    lines.append(f"مدل فعال: `{current}`" if current else "مدل فعال: (پیش‌فرض تنظیم نشده)")
    lines.append("")
    lines.append("روی مدل بزن تا انتخاب شود.")
    lines.append("برای فیلتر، «🔍 جست‌وجو» را بزن و متن را بفرست (مثلاً `free`).")
    text = "\n".join(lines)

    kb = ui.models_keyboard(page_items, page, total_pages, query_text)
    if edit and update.callback_query is not None:
        await _edit_query(update.callback_query, text, reply_markup=kb, parse_mode="Markdown")
    else:
        await _safe_reply(update.effective_message, text, reply_markup=kb, parse_mode="Markdown")


async def _save_api_key(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    provider_name: str,
    key: str,
) -> None:
    """کلید را ذخیره می‌کند و یک درخواست آزمایشی می‌زند."""
    session = _session(context)
    provider = next(
        (p for p in config.AI_PROVIDERS if p["name"] == provider_name), None
    )
    if provider is None:
        await _safe_reply(update.effective_message, "سرویس پیدا نشد.")
        return

    if provider.get("auth", "bearer") != "bearer":
        await _safe_reply(
            update.effective_message,
            f"سرویس «{provider_name}» به کلید نیاز ندارد (سرویس محلی است).",
            reply_markup=ui.provider_keyboard(ai.active_provider()["name"],
                                              ai.provider_status()),
        )
        return

    key = key.strip()
    if not key:
        await _safe_reply(update.effective_message, "کلید خالی بود.")
        return

    secrets.set_key(provider_name, key)
    ai.set_provider(provider_name)
    logger.info("کلید سرویس %s ثبت شد.", provider_name)

    # پیام حاوی کلید را پاک می‌کنیم تا در تاریخچه‌ی چت نماند
    await _delete_quietly(update.effective_message)

    status = await update.effective_message.reply_text(
        f"🔑 کلید «{provider_name}» ذخیره شد. در حال تست اتصال…"
    )

    pending = session.pop("pending_raw", "")
    ok, result = await ai.test_connection(provider)

    if pending:
        # کاربر قبلاً درخواست ساخت پرامپت داده بود، ادامه می‌دهیم
        if ok:
            text = f"✅ اتصال به «{provider_name}» برقرار شد."
        else:
            text = (
                f"⚠️ کلید ذخیره شد ولی اتصال تست نشد:\n{result}\n\n"
                "می‌توانی سرویس دیگری را امتحان کنی."
            )
        text += "\n\nادامه‌ی ساخت پرامپت…"
        await _edit(status, text)
        await _generate_prompt(update, context, pending)
        return

    if ok:
        session["model_provider"] = provider_name
        session["model_query"] = ""
        session["model_page"] = 0
        await _edit(status, f"✅ اتصال به «{provider_name}» برقرار شد.\nدر حال دریافت لیست مدل‌ها…")
        await _show_models(
            update,
            context,
            provider_name,
            page=0,
            edit=False,
            notice="اتصال برقرار است؛ یک مدل را انتخاب کن.",
        )
        return

    text = (
        f"⚠️ کلید ذخیره شد ولی اتصال تست نشد:\n{result}\n\n"
        "می‌توانی سرویس دیگری را امتحان کنی یا مدل/تنظیمات را بررسی کنی."
    )
    await _edit(
        status,
        text,
        reply_markup=ui.provider_key_actions(
            provider_name, secrets.has_key(provider_name)
        ),
    )


# ------------------------------------------------------------------ خاموشی
@security.owner_only
async def cmd_shutdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "⚠️ مطمئنی می‌خواهی لپ‌تاپ خاموش شود؟\n"
        f"بعد از تأیید، {config.SHUTDOWN_DELAY_SECONDS} ثانیه فرصت لغو داری.",
        reply_markup=ui.confirm_shutdown(),
    )


@security.owner_only
async def cmd_cancel_shutdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ok, msg = await power.abort_shutdown()
    await update.effective_message.reply_text(f"✅ {msg}" if ok else f"ℹ️ {msg}")


@security.owner_only
async def cmd_lock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ok, msg = await power.lock_workstation()
    await update.effective_message.reply_text(f"🔒 {msg}" if ok else f"❌ {msg}")


@security.owner_only
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _session(context)["mode"] = ""
    await update.effective_message.reply_text(
        "لغو شد.", reply_markup=ui.main_menu()
    )


# ------------------------------------------------------------------ وضعیت
@security.owner_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    وضعیت کلی.

    عمداً بدون parse_mode است: نام مدل‌ها یا سرویس‌ها ممکن است کاراکتر
    markdown داشته باشند و باعث خطای تلگرام شوند.
    """
    session = _session(context)
    provider = ai.active_provider()
    src = ai.key_source(provider)

    lines = [
        "📊 وضعیت",
        "",
        jobs.queue.status_line(),
        f"OpenCode فعال: {'بله' if opencode.is_running() else 'خیر'}",
        f"سرویس AI فعال: {provider['name']}",
        f"مدل: {ai.effective_model(provider)}",
        f"کلید API: {'ثبت شده' if src != 'none' else 'ثبت نشده'} "
        f"({src if src != 'none' else 'از /provider وارد کنید'})",
        f"تعداد برنامه‌های شناسایی‌شده: {len(context.user_data.get('all_apps', []))}",
    ]

    try:
        if await power.is_shutdown_pending():
            lines.append("⚠️ خاموشی در صف ویندوز قرار دارد (/cancel برای لغو)")
    except Exception:
        logger.debug("بررسی وضعیت خاموشی ناموفق بود", exc_info=True)

    mode = session.get("mode", "")
    if mode:
        lines.append(f"حالت فعال: {mode}")

    try:
        await _safe_reply(
            update.effective_message, "\n".join(lines), reply_markup=ui.main_menu()
        )
    except Exception:
        logger.exception("ارسال وضعیت ناموفق بود")
        await _safe_reply(update.effective_message, "خطا در نمایش وضعیت.")


# ------------------------------------------------------------------ دکمه‌ها
@security.owner_only
async def on_button_shutdown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_shutdown(update, context)


@security.owner_only
async def on_button_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_prompt(update, context)


@security.owner_only
async def on_button_apps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_apps_page(update, context, 0)


# ------------------------------------------------------------------ کنترل ماوس
_MOUSE_MODE = "mouse"
# حالت «منتظر متن برای چسباندن در مکان سلکت‌شده» — بعد از خروج از حالت ماوس
# با دکمه‌ی «وارد کردن متن…» فعال می‌شود.
_MOUSE_TYPE_MODE = "mouse_type"
# حالت «منتظر متن جست‌وجوی گوگل»
_GOOGLE_MODE = "google"


def _mouse_step(session: Dict[str, object]) -> int:
    try:
        step = int(session.get("mouse_step") or config.MOUSE_STEP_DEFAULT)
    except (TypeError, ValueError):
        return config.MOUSE_STEP_DEFAULT
    return step


def _cycle_mouse_step(session: Dict[str, object], direction: str) -> int:
    """جابه‌جایی گام حرکت بین MOUSE_STEPS (با پیچیدن از انتها به اول)."""
    steps = list(config.MOUSE_STEPS)
    cur = _mouse_step(session)
    idx = (
        steps.index(cur)
        if cur in steps
        else steps.index(config.MOUSE_STEP_DEFAULT)
    )
    idx = (idx + (1 if direction == "up" else -1)) % len(steps)
    session["mouse_step"] = steps[idx]
    return steps[idx]


def _scroll_step(session: Dict[str, object]) -> int:
    """گام اسکرول فعلی (تعداد کلیک چرخ در هر فشار)."""
    try:
        return int(session.get("scroll_step") or config.MOUSE_SCROLL_STEP_DEFAULT)
    except (TypeError, ValueError):
        return config.MOUSE_SCROLL_STEP_DEFAULT


def _cycle_scroll_step(session: Dict[str, object], direction: str) -> int:
    """جابه‌جایی گام اسکرول بین MOUSE_SCROLL_STEPS (پیچ از انتها به اول)."""
    steps = list(config.MOUSE_SCROLL_STEPS)
    cur = _scroll_step(session)
    idx = (
        steps.index(cur)
        if cur in steps
        else steps.index(config.MOUSE_SCROLL_STEP_DEFAULT)
    )
    idx = (idx + (1 if direction == "up" else -1)) % len(steps)
    session["scroll_step"] = steps[idx]
    return steps[idx]


def _remember_target(session: Dict[str, object]) -> None:
    """پنجره‌ی فعال فعلی را هدف کیبورد نگه می‌داریم (برای شورتکات‌ها)."""
    h = windows.foreground()
    if h and windows.is_window(h):
        session["target_hwnd"] = h


def _refocus_target(session: Dict[str, object]) -> None:
    """
    قبل از فرستادن کلید/ترکیب/متن، هدف را جلو می‌آورد.

    اگر تلگرام دسکتاپ روی همین کامپیوتر باز باشد، زدنِ دکمه فوکوس را از
    برنامه‌ی هدف می‌گیرد و شورتکات به جای مقصد به تلگرام می‌خورد؛ بدون این
    مرحله Ctrl+C در تلگرام «کپی» می‌شود نه در برنامه‌ی هدف.
    """
    h = int(session.get("target_hwnd") or 0)
    if not h or not windows.is_window(h):
        return
    if not windows.is_foreground(h):
        windows.bring_to_front(h)


def _switchable_windows(max_items: int = 10) -> list:
    """لیست (hwnd, عنوان) پنجره‌های باز برای جابجایی (مثل تب‌های مرورگر)."""
    items = []
    for w in windows.list_windows():
        title = str(w.get("title") or "").strip()
        if not title:
            continue
        items.append((int(w["hwnd"]), title))
        if len(items) >= max_items:
            break
    return items


async def _close_window_picker(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: Dict[str, object]
) -> None:
    """پیام انتخاب پنجره را حذف می‌کند (اگر حذف ممکن نشد بی‌صدا رد می‌شود)."""
    msg_id = session.pop("win_pick_msg", None)
    if msg_id is None:
        return
    try:
        await context.bot.delete_message(update.effective_chat.id, msg_id)
    except TelegramError:
        pass


def _mouse_caption(session: Dict[str, object]) -> str:
    """کپشن عکس حالت ماوس: مختصات لحظه‌ای + راهنمای یک‌خطی."""
    x, y = mouse.position()
    step = _mouse_step(session)
    scroll_step = _scroll_step(session)
    return (
        f"🖱 مختصات نشانگر: {x} , {y} | گام حرکت: {step} پیکسل | "
        f"اسکرول: {scroll_step}\n"
        "هر دکمه همان کار را روی دسکتاپ انجام می‌دهد و عکس تازه می‌شود.\n"
        "متن بنویس تا در همین نقطه تایپ شود (اول کلیک یا Shift+کلیک برای انتخاب).\n"
        "خروج: ❎ روی عکس، یا /cancel"
    )


async def _mouse_snapshot(session: Dict[str, object]) -> Tuple[bytes, str]:
    """
    عکس JPEG با نشانگر + کپشن آن.

    همه‌ی کار (عکس، مختصات، فشردن) در یک thread می‌افتد تا مختصاتی که در
    کپشن می‌نویسیم با محتوای عکس یکی باشد و حلقه‌ی رویداد هم نخوابد.
    """

    def work() -> Tuple[bytes, str]:
        return imaging.encode_jpeg(mouse.annotated_screenshot()), _mouse_caption(session)

    return await asyncio.to_thread(work)


async def _mouse_refresh(
    update: Update, context: ContextTypes.DEFAULT_TYPE, session: Dict[str, object]
) -> None:
    """
    به‌روزرسانی عکس حالت ماوس.

    ترجیح بر ویرایش همان پیام است، نه ارسال پیام تازه: در استفاده‌ی
    طولانی، هر فلش یک پیام جدید می‌ساخت و چت را غرق عکس می‌کرد. اگر ویرایش
    ممکن نشد (پیام قدیمی، عکس حذف‌شده و...) عکس تازه فرستاده می‌شود.
    """
    jpeg, caption = await _mouse_snapshot(session)
    kb = ui.mouse_keyboard(_mouse_step(session), _scroll_step(session))
    query = update.callback_query

    if query is not None:
        try:
            await query.edit_message_media(
                media=InputMediaPhoto(jpeg, caption=caption), reply_markup=kb
            )
            # شناسه‌ی همین پیام را نگه می‌داریم؛ وگرنه متنی که بعداً از مسیر
            # پیامِ متنی می‌رسد (چسباندن در مکان سلکت‌شده) عکسِ همین پیام را
            # به‌روز نمی‌کند و یک پیام تازه می‌سازد.
            session["mouse_msg"] = getattr(
                query.message, "message_id", session.get("mouse_msg")
            )
            return
        except TelegramError as exc:
            logger.debug("ویرایش عکس حالت ماوس نشد؛ پیام تازه می‌فرستم: %s", exc)

    msg_id = session.get("mouse_msg")
    if msg_id is not None:
        try:
            await context.bot.edit_message_media(
                chat_id=update.effective_chat.id,
                message_id=msg_id,
                media=InputMediaPhoto(jpeg, caption=caption),
                reply_markup=kb,
            )
            return
        except TelegramError as exc:
            logger.debug("به‌روزرسانی عکس با شناسه نشد؛ پیام تازه: %s", exc)

    try:
        sent = await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=jpeg,
            caption=caption,
            reply_markup=kb,
        )
        session["mouse_msg"] = getattr(sent, "message_id", None)
    except TelegramError:
        logger.warning("ارسال عکس حالت ماوس ناموفق بود", exc_info=True)


async def _mouse_caption_edit(
    query, caption: str, reply_markup=None
) -> bool:
    """
    ویرایش کپشنِ پیامِ عکس حالت ماوس.

    نکته‌ی مهم: این پیام یک «عکس» است نه متن؛ edit_message_text روی آن خطای
    «no text in the message to edit» می‌دهد و بی‌صدا رد می‌شود (کپشن دست‌نخورده
    می‌ماند). راه درست edit_message_caption است — دقیقاً به همین دلیل قبلاً
    بعد از «خروج از حالت» هیچ دکمه‌ای دیده نمی‌شد.
    """
    try:
        await query.edit_message_caption(caption=caption, reply_markup=reply_markup)
        return True
    except TelegramError as exc:
        if "not modified" in str(exc).lower():
            return False
        logger.debug("ویرایش کپشن عکس حالت ماوس نشد: %s", exc)
        return False


async def _mouse_caption_by_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session: Dict[str, object],
    caption: str,
    reply_markup=None,
) -> None:
    """همان ویرایش کپشن، ولی از مسیر پیام متنی (بدون callback)."""
    msg_id = session.get("mouse_msg")
    if msg_id is None:
        return
    try:
        await context.bot.edit_message_caption(
            chat_id=update.effective_chat.id,
            message_id=msg_id,
            caption=caption,
            reply_markup=reply_markup,
        )
    except TelegramError as exc:
        logger.debug("به‌روزرسانی کپشن عکس حالت ماوس نشد: %s", exc)


@security.owner_only
async def cmd_mouse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    ورود به حالت کنترل ماوس/کیبورد.

    یک عکس زنده با نشانگر + کیبورد شناور می‌فرستد؛ هر زدنِ دکمه یک عمل
    روی دسکتاپ است و عکس همان پیام جایگزین می‌شود. متنی که کاربر بفرستد
    در نقطه‌ی فعلی ماوس چسبانده می‌شود.
    """
    session = _session(context)
    session["mode"] = _MOUSE_MODE
    session.setdefault("mouse_step", config.MOUSE_STEP_DEFAULT)
    session.setdefault("scroll_step", config.MOUSE_SCROLL_STEP_DEFAULT)
    # پنجره‌ی فعالِ هنگام ورود را هدف کیبورد نگه می‌داریم تا شورتکات‌ها
    # (حتی اگر تلگرام دسکتاپ فوکوس را بگیرد) به همان پنجره بخورد.
    _remember_target(session)
    # پیام قبلی حالت را فراموش می‌کنیم تا عکس تازه، شناسه‌ی تازه بگیرد
    session.pop("mouse_msg", None)

    try:
        await context.bot.send_chat_action(
            update.effective_chat.id, ChatAction.UPLOAD_PHOTO
        )
    except TelegramError:
        pass

    await _mouse_refresh(update, context, session)


@security.owner_only
async def on_button_mouse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_mouse(update, context)


# ------------------------------------------------------------------ گوگل
def _open_url(url: str) -> None:
    """باز کردن لینک در برنامه‌ی پیش‌فرض ویندوز (مرورگر)."""
    os.startfile(url)


def _focus_browser() -> bool:
    """
    پنجره‌ی مرورگری که لینک در آن باز شد جلو می‌آورد.

    بدون این، عکس حالت ماوس ممکن است پنجره‌ی قبلی را نشان دهد و کلیک‌ها
    به جای مرورگر، جای دیگر بخورد. اگر مرورگر پیدا نشد، بی‌صدا رد می‌شود —
    جست‌وجو خودش باز شده است.
    """
    for kw in config.BROWSER_WINDOW_KEYWORDS:
        hwnd = windows.find_window([kw])
        if hwnd:
            if windows.focus(hwnd):
                return True
    return False


async def _google_search(
    update: Update, context: ContextTypes.DEFAULT_TYPE, query: str
) -> None:
    """
    باز کردن جست‌وجوی گوگل در مرورگر و فعال کردن خودکار حالت کنترل ماوس.

    ترتیب مهم است: اول لینک باز می‌شود، بعد صبر می‌کنیم تب مرورگر باز شود،
    بعد پنجره‌اش جلو می‌آید، بعد صفحه‌ی نتایج می‌کشد، و در آخر حالت ماوس
    فعال می‌شود تا کاربر بتواند روی نتایج اسکرول کند، وارد سایت‌ها شود و
    بین صفحات جلو/عقب برود.
    """
    url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
    try:
        await asyncio.to_thread(_open_url, url)
    except Exception as exc:
        logger.exception("باز کردن لینک گوگل ناموفق بود")
        await _safe_reply(
            update.effective_message, f"❌ باز کردن مرورگر انجام نشد: {exc}"
        )
        return

    await _safe_reply(
        update.effective_message, f"🔍 در حال باز کردن نتایج «{query}»…"
    )

    await asyncio.sleep(config.GOOGLE_OPEN_DELAY)
    await asyncio.to_thread(_focus_browser)
    await asyncio.sleep(config.GOOGLE_RENDER_DELAY)

    # فعال‌سازی خودکار کنترل ماوس روی صفحه‌ی نتایج
    await cmd_mouse(update, context)


@security.owner_only
async def cmd_google(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    جست‌وجوی گوگل.

    با آرگومان (مثلاً `/google پایتون`) سریع جست‌وجو می‌کند؛ بدون آرگومان،
    حالت انتظار فعال می‌شود و متن بعدی کاربر به‌عنوان عبارت جست‌وجو می‌رود.
    """
    text = " ".join(context.args or []).strip()
    if text:
        await _google_search(update, context, text)
        return
    session = _session(context)
    session["mode"] = _GOOGLE_MODE
    await _safe_reply(
        update.effective_message,
        "🔍 چه چیزی را در گوگل جست‌وجو کنم؟\n"
        "متن را بفرست؛ بعد از باز شدن نتایج، حالت کنترل ماوس فعال می‌شود "
        "تا بین صفحات حرکت کنی یا وارد سایت‌ها شوی.\n"
        "برای انصراف /cancel را بزن.",
    )


@security.owner_only
async def on_button_google(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_google(update, context)


@security.owner_only
async def cmd_remote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    کنسول ریموت وب: انتخاب تونل (Cloudflare یا ngrok) و ساخت لینک.

    اگر از قبل فعال است، همان لینک قبلی نمایش داده می‌شود؛ در غیر این صورت
    دو گزینه‌ی تونل نشان داده می‌شود تا کاربر یکی را انتخاب کند.
    """
    st = remote.status()
    if st["running"] and st["url"]:
        provider = str(st.get("provider") or "نامشخص")
        label = {"cloudflare": "Cloudflare", "ngrok": "ngrok", "local": "محلی"}.get(
            provider, provider
        )
        await _safe_reply(
            update.effective_message,
            "🔗 کنسول ریموت از قبل فعال است "
            f"(تونل: {label}):\n"
            f"{st['url']}\n\n"
            "برای توقف: /remote_stop\n"
            "⚠️ این لینک مثل رمز است؛ به کسی نده.",
        )
        return

    await _safe_reply(
        update.effective_message,
        "🌐 کدام تونل برای کنسول ریموت راه‌اندازی شود؟\n\n"
        "• Cloudflare: تونل سریع بدون نیاز به حساب (دانلود خودکار در اولین اجرا)\n"
        "• ngrok: اگر نصب/تنظیم شده باشد (ممکن است نیاز به authtoken داشته باشد)\n\n"
        "یکی را انتخاب کن:",
        reply_markup=ui.remote_tunnel_keyboard(),
    )


async def _start_remote_tunnel(
    update: Update, context: ContextTypes.DEFAULT_TYPE, provider: str
) -> None:
    """شروع تونل کنسول ریموت با سرویس انتخاب‌شده و ارسال لینک."""
    query = update.callback_query
    label = {"cloudflare": "Cloudflare", "ngrok": "ngrok"}.get(provider, provider)

    st = remote.status()
    if st["running"] and st["url"]:
        cur = str(st.get("provider") or "")
        cur_label = {"cloudflare": "Cloudflare", "ngrok": "ngrok", "local": "محلی"}.get(
            cur, cur
        )
        if query is not None:
            await _edit_query(
                query,
                "🔗 کنسول ریموت از قبل فعال است "
                f"(تونل: {cur_label}):\n{st['url']}\n\n"
                "برای توقف: /remote_stop\n⚠️ این لینک مثل رمز است؛ به کسی نده.",
            )
        return

    if query is not None:
        await _edit_query(
            query,
            f"⏳ در حال راه‌اندازی کنسول ریموت با تونل {label}...\n"
            "این کار ممکن است تا یک دقیقه طول بکشد.",
        )

    target_msg = update.effective_message
    try:
        url = await asyncio.to_thread(remote.start, None, provider)
    except remote.RemoteError as exc:
        await _safe_reply(target_msg, f"❌ راه‌اندازی با {label} ناموفق بود:\n{exc}")
        return
    except Exception as exc:
        logger.exception("راه‌اندازی کنسول ریموت (%s) ناموفق بود", provider)
        await _safe_reply(target_msg, f"❌ راه‌اندازی کنسول ریموت ناموفق بود: {exc}")
        return

    await _safe_reply(
        target_msg,
        "🖥 کنسول ریموت آماده است "
        f"(تونل: {label}):\n{url}\n\n"
        "• صفحه زنده با نشانگر ماوس دیده می‌شود\n"
        "• کلیک/درگ/اسکرول و تایپ (فارسی هم) از مرورگر کار می‌کند\n"
        "• توقف: /remote_stop\n"
        "⚠️ این لینک مثل رمز است؛ به کسی نده. بعد از راه‌اندازی تازه، "
        "لینک قبلی می‌میرد.",
    )


@security.owner_only
async def cmd_remote_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """توقف کنسول ریموت و بستن تونل کلودفلر."""
    was = await asyncio.to_thread(remote.stop)
    if was:
        await _safe_reply(update.effective_message, "✅ کنسول ریموت و تونل بسته شد.")
    else:
        await _safe_reply(update.effective_message, "کنسول ریموت از قبل بسته بود.")


async def _mouse_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, data: str
) -> None:
    """
    اجرای دکمه‌های کیبورد حالت ماوس.

    هر callback به شکل «mouse:کار:آرگومان» است؛ اول اعتبارسنجی، بعد اجرا
    در یک thread، بعد عکس تازه — چون بدون عکس، کاربر نمی‌بیند چه اتفاقی
    افتاده است.
    """
    query = update.callback_query
    session = _session(context)
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""

    # این سه تا قبل از چک حالت‌اند، چون بعد از «خروج» (یعنی خارج از حالت
    # ماوس) هم باید کار کنند؛ دکمه‌های حرکتی برعکس بعد از خروج بی‌اثرند.
    if action == "exit":
        session["mode"] = ""
        # موقعیت نشانگر را نگه می‌داریم تا در صفحه‌ی بعدی به کاربر نشان
        # داده شود (خودِ چسباندن با فوکوس پنجره انجام می‌شود، نه مختصات).
        x, y = mouse.position()
        await _mouse_caption_edit(
            query,
            "❎ از حالت کنترل ماوس خارج شدی.\n"
            f"نشانگر روی {x} , {y} بود.\n"
            "اگر جایی را انتخاب کردی، با دکمه‌ی زیر متن‌ات را در همان‌جا "
            "می‌گذارم:",
            reply_markup=ui.mouse_exit_keyboard(),
        )
        return

    if action == "type_ask":
        session["mode"] = _MOUSE_TYPE_MODE
        x, y = mouse.position()
        await _mouse_caption_edit(
            query,
            f"✍️ متن را بفرست تا در مکان سلکت‌شده (نشانگر {x} , {y}) "
            "چسبانده شود.\nبرای انصراف «✖️ کنسل» یا /cancel.",
            reply_markup=ui.mouse_type_keyboard(),
        )
        return

    if action == "type_cancel":
        session["mode"] = ""
        await _mouse_caption_edit(
            query, "❎ انصراف؛ چیزی چسبانده نشد.", reply_markup=None
        )
        if query.message is not None:
            await query.message.reply_text(
                "👌 انصراف. منوی پایین چت سر جایش است.",
                reply_markup=ui.main_menu(),
            )
        return

    if session.get("mode") != _MOUSE_MODE:
        # دکمه‌های روی عکس‌های قدیمی بعد از خروج نباید ماوس را تکان دهند
        await query.answer(
            "حالت ماوس بسته است؛ برای ادامه /mouse را بزن.", show_alert=True
        )
        return

    # ---- جابجایی بین پنجره‌های باز (مثل تب‌ها در مرورگر)
    if action == "winlist":
        items = await asyncio.to_thread(_switchable_windows)
        if not items:
            await query.answer("پنجره‌ای باز نیست.", show_alert=True)
            return
        session["win_list"] = items
        msg = await query.message.reply_text(
            "🗂 کدام پنجره را جلو بیاورم؟ (مثل تعویض تب)",
            reply_markup=ui.windows_keyboard(items),
        )
        session["win_pick_msg"] = getattr(msg, "message_id", None)
        return

    if action == "win":
        items = session.get("win_list") or []
        try:
            idx = int(arg)
        except ValueError:
            idx = -1
        if not (0 <= idx < len(items)):
            await query.answer("این پنجره دیگر در لیست نیست.", show_alert=True)
            return
        hwnd = items[idx][0]
        ok = await asyncio.to_thread(windows.bring_to_front, hwnd)
        await _close_window_picker(update, context, session)
        if ok:
            session["target_hwnd"] = hwnd
        await query.answer("جلو آمد ✅" if ok else "نیامد ❌", show_alert=not ok)
        await _mouse_refresh(update, context, session)
        return

    if action == "winback":
        await _close_window_picker(update, context, session)
        await query.answer()
        return

    step = _mouse_step(session)

    def act() -> bool:
        if action == "mv":
            if arg not in mouse.MOVES:
                return False
            mouse.move(arg, step)
        elif action == "step":
            if arg not in ("up", "down"):
                return False
            _cycle_mouse_step(session, arg)
        elif action == "sstep":
            if arg not in ("up", "down"):
                return False
            _cycle_scroll_step(session, arg)
        elif action == "click":
            if arg not in mouse.CLICKS:
                return False
            mouse.click(arg)
            _remember_target(session)
        elif action == "down":
            mouse.hold()
            _remember_target(session)
        elif action == "up":
            mouse.release()
            _remember_target(session)
        elif action == "scroll":
            if arg not in ("up", "down"):
                return False
            mouse.scroll(arg, _scroll_step(session))
        elif action == "key":
            if arg not in mouse.KEYS:
                return False
            _refocus_target(session)
            mouse.press(arg)
        elif action == "combo":
            if arg not in mouse.COMBOS:
                return False
            _refocus_target(session)
            mouse.combo(arg)
        elif action == "shot":
            pass  # فقط عکس تازه می‌خواهد
        else:
            return False
        return True

    ok = await asyncio.to_thread(act)
    if not ok:
        await query.answer("این دکمه پشتیبانی نمی‌شود.", show_alert=True)
        return

    await _mouse_refresh(update, context, session)


# ------------------------------------------------------------------ پیام متنی
@security.owner_only
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = _session(context)
    text = (update.effective_message.text or "").strip()
    mode = session.get("mode", "")

    # ---- اول حالت‌های در جریان، بعد دکمه‌ها
    if mode == "api_key":
        session["mode"] = ""
        provider = str(session.pop("key_provider", "") or "")
        if provider:
            await _save_api_key(update, context, provider, text)
        return

    if mode == "send_prompt":
        session["mode"] = ""
        if not text:
            await _safe_reply(update.effective_message, "پرامپت خالی بود.")
            return
        await _run_opencode(update, context, text)
        return

    if mode in (_MOUSE_MODE, _MOUSE_TYPE_MODE):
        # در این حالت‌ها، هر متنی به‌جای رفتن به AI، در همان نقطه‌ای که
        # نشانگر است چسبانده می‌شود (خواسته‌ی اصلی: تایپ روی جایی که با
        # ماوس انتخاب کردی). استثنا: دکمه‌ی منو که حالت را می‌بندد و
        # از همینجا به dispatch پایین می‌رود.
        if ui.match_button(text):
            session["mode"] = ""
        else:
            try:
                # اول هدف را جلو می‌آوریم؛ وگرنه اگر تلگرام دسکتاپ فوکوس
                # گرفته باشد، چسباندن در پنجره‌ی تلگرام انجام می‌شود نه مقصد
                _refocus_target(session)
                await asyncio.to_thread(mouse.set_text, text)
            except Exception as exc:
                logger.exception("چسباندن متن در حالت ماوس ناموفق بود")
                await _safe_reply(
                    update.effective_message, f"❌ چسباندن متن انجام نشد: {exc}"
                )
                return
            preview = text if len(text) <= 40 else text[:40] + "…"
            await _safe_reply(
                update.effective_message,
                f"⌨️ چسبانده شد: {preview}",
                reply_markup=ui.main_menu(),
            )
            if mode == _MOUSE_TYPE_MODE:
                # جریان «تایپ بعد از خروج» تمام شد؛ عکس هم پیام «تمام» می‌گیرد
                session["mode"] = ""
                await _mouse_caption_by_id(
                    update, context, session,
                    "✅ متن در مکان سلکت‌شده چسبانده شد.",
                )
            else:
                await _mouse_refresh(update, context, session)
            return

    # اگر در جست‌وجوی مدل هستیم و کاربر دکمه‌ی منو زد، حالت جست‌وجو بسته شود
    if mode == "model_search" and ui.match_button(text):
        session["mode"] = ""
        mode = ""

    button = ui.match_button(text)
    if button:
        if button == ui.BTN_SCREENSHOT:
            await on_button_screenshot(update, context)
        elif button == ui.BTN_CAMERA:
            await on_button_camera(update, context)
        elif button == ui.BTN_APPS:
            await on_button_apps(update, context)
        elif button == ui.BTN_PROMPT:
            await on_button_prompt(update, context)
        elif button == ui.BTN_SHUTDOWN:
            await on_button_shutdown(update, context)
        elif button == ui.BTN_TABS:
            await on_button_tabs(update, context)
        elif button == ui.BTN_MOUSE:
            await on_button_mouse(update, context)
        elif button == ui.BTN_GOOGLE:
            await on_button_google(update, context)
        return

    if mode == _GOOGLE_MODE:
        session["mode"] = ""
        await _google_search(update, context, text)
        return

    if mode == "search":
        session["mode"] = ""
        await _run_search(update, context, text)
        return

    if mode == "prompt":
        session["mode"] = ""
        await _generate_prompt(update, context, text)
        return

    if mode == "model_search":
        session["model_query"] = text
        session["model_page"] = 0
        pname = str(session.get("model_provider") or ai.active_provider()["name"])
        await _show_models(
            update, context, pname, page=0, edit=False,
            notice=f'نتایج جست‌وجو برای «{text}»',
        )
        return

    # دکمه‌ی ناشناخته (مثلاً کیبوردِ نسخه‌ی قدیمی): نباید به AI فرستاده شود
    if ui.looks_like_button(text):
        await _safe_reply(
            update.effective_message,
            "این دکمه برای این نسخه شناخته نشد. "
            "منوی پایین چت به‌روزرسانی شد؛ یکی از همان دکمه‌ها را بزن.",
            reply_markup=ui.main_menu(),
        )
        return

    # چون فقط مالک به این بات دسترسی دارد، هر متن خامی مستقیم به ساخت پرامپت
    # می‌رود؛ لازم نیست اول دکمه‌ی ✍️ زده شود.
    await _generate_prompt(update, context, text)


# ------------------------------------------------------------------ callback
@security.owner_only
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data or ""
    await query.answer()

    if data == "noop":
        return

    # ---- دوربین
    if data == "camera_detect":
        await _camera_detect_and_show(update, context)
        return

    if data.startswith("camera_set:"):
        try:
            idx = int(data.split(":", 1)[1])
        except ValueError:
            idx = 0
        imaging.set_camera_index(idx)
        await _edit_query(query, f"✅ دوربین {idx} ذخیره شد. /camera همیشه از همین عکس می‌گیرد.")
        return

    if data == "camera_back":
        await _edit_query(query, "⚙️ تنظیمات دوربین بسته شد.")
        return

    # ---- برنامه‌ها
    if data.startswith("apps:"):
        page = int(data.split(":", 1)[1] or 0)
        await _send_apps_page(update, context, page, edit=True)
        return

    if data == "apps_refresh":
        await _edit_query(query, "🔄 در حال به‌روزرسانی لیست برنامه‌ها…")
        fresh = await apps.refresh_apps()
        context.user_data["all_apps"] = fresh
        page_items, page, total_pages = _paginate(fresh, 0, config.APPS_PAGE_SIZE)
        favorites = apps.get_favorites(fresh, limit=3)
        header = f"🔄 لیست تازه شد — {len(fresh)} برنامه"
        await _edit_query(query, 
            header,
            reply_markup=ui.apps_keyboard(page_items, page, total_pages, favorites),
            parse_mode="Markdown",
        )
        return

    if data == "search_enter":
        _session(context)["mode"] = "search"
        await _edit_query(query, 
            "🔍 اسم برنامه را بنویسید. برای خروج /cancel را بزنید."
        )
        return

    if data.startswith("app:"):
        token = data.split(":", 1)[1]
        app_id = ui.resolve_app_id(token)
        all_apps = await _app_index(context)
        name = next(
            (a["name"] for a in all_apps if a["app_id"] == app_id), "برنامه"
        )
        if app_id not in {a["app_id"] for a in all_apps}:
            # فقط اجازه‌ی اجرا از لیست واقعی سیستم
            await query.answer("این برنامه در لیست نیست.", show_alert=True)
            return
        await _edit_query(query, f"🚀 در حال باز کردن {name}…")
        await _launch_app(update, context, app_id, name)
        return

    # ---- پرامپت
    if data == "prompt_start":
        _session(context)["mode"] = "prompt"
        await _edit_query(query, 
            "✍️ متن خام درخواستت را بنویسید.\nبرای خروج /cancel را بزنید."
        )
        return

    if data == "prompt_send":
        prompt = _session(context).get("last") or ai.load_prompt()
        if not prompt:
            await query.answer("پرامپتی وجود ندارد.", show_alert=True)
            return
        await _edit_query(query, "در حال ارسال به OpenCode…")
        await _run_opencode(update, context, prompt)
        return

    if data == "prompt_edit_raw":
        raw = _session(context).get("raw", "")
        _session(context)["mode"] = "prompt"
        await _edit_query(query, 
            "✏️ متن خام فعلی:\n\n"
            f"`{raw}`\n\nنسخه‌ی اصلاح‌شده را بنویسید:"
        )
        return

    if data == "prompt_rebuild":
        raw = _session(context).get("raw", "")
        if not raw:
            await query.answer("متن خامی وجود ندارد.", show_alert=True)
            return
        await _edit_query(query, "♻️ در حال بازسازی پرامپت…")
        await _generate_prompt(update, context, raw)
        return

    if data == "prompt_clear":
        _session(context).update({"last": "", "raw": "", "prompt": ""})
        ai.save_prompt("")
        await _edit_query(query, "🗑 پاک شد. منوی پایین چت سر جایش است.")
        return

    # ---- OpenCode
    if data == "oc_approve":
        ok, msg = await opencode.approve()
        await _after_decision(update, context, f"✅ {msg}" if ok else f"⚠️ {msg}")
        return

    if data == "oc_reject":
        ok, msg = await opencode.reject()
        await _after_decision(update, context, f"🚫 {msg}" if ok else f"⚠️ {msg}")
        return

    if data == "oc_stop":
        jobs.queue.cancel()
        opencode.request_cancel()
        _session(context)["awaiting"] = False
        ok, msg = await opencode.stop()
        await query.message.reply_text(f"⏹ {msg}")
        return

    if data == "oc_keep_watching":
        _session(context)["awaiting"] = False
        await query.message.reply_text("👌 ادامه می‌دهم؛ خبر می‌دهم که تمام شد.")
        return

    # ---- تب‌های خود برنامه‌ی OpenCode
    if data in {"tabs_show", "tabs_refresh"}:
        await cmd_tabs(update, context)
        return

    if data == "tab_new":
        await query.answer("در حال باز کردن تب جدید…")
        ok, msg = await _click_opencode_new_tab()
        await query.message.reply_text(f"➕ {msg}" if ok else f"❌ {msg}")
        return

    if data.startswith("tab:"):
        number = int(data.split(":", 1)[1] or 0)
        if not 1 <= number <= 9:
            await query.answer("شماره‌ی تب نامعتبر است.", show_alert=True)
            return
        ok, msg = await _click_opencode_tab(number)
        if ok:
            _session(context)["awaiting"] = False
            await query.message.reply_text(
                f"✅ {msg}\nحالا با /send پرامپت را بفرست."
            )
        else:
            await query.message.reply_text(f"❌ {msg}")
        return

    if data == "send_ask":
        _session(context)["mode"] = "send_prompt"
        await _edit_query(query,
            "✍️ پرامپتی که باید به OpenCode داده شود را بنویسید.\n"
            "برای انصراف /cancel را بزنید."
        )
        return

    # ---- کنترل ماوس و کیبورد
    if data == "mouse_enter":
        await cmd_mouse(update, context)
        return

    if data.startswith("mouse:"):
        await _mouse_callback(update, context, data)
        return

    # ---- کنسول ریموت (انتخاب تونل)
    if data.startswith("remote_start:"):
        provider = data.split(":", 1)[1].strip().lower()
        if provider not in ("cloudflare", "ngrok"):
            await query.answer("تونل نامعتبر است.", show_alert=True)
            return
        await _start_remote_tunnel(update, context, provider)
        return

    # ---- سرویس AI و کلید
    if data.startswith("provider:"):
        name = data.split(":", 1)[1]
        if not ai.set_provider(name):
            await query.answer("سرویس پیدا نشد.", show_alert=True)
            return
        provider = ai.active_provider()
        has_key = bool(ai.api_key(provider))
        lines = [
            f"✅ سرویس فعال: **{provider['name']}**",
            f"مدل: `{ai.effective_model(provider)}`",
            f"آدرس: `{provider['base_url']}`",
            "",
        ]
        if has_key:
            src = ai.key_source(provider)
            where = (
                "متغیر محیطی" if src == "env" else f"data\\secrets.json ({secrets.masked(ai.api_key(provider))})"
            )
            lines.append(f"🔑 کلید ثبت شده — منبع: {where}")
        else:
            lines.append(
                "🔑 کلیدی ثبت نشده. با دکمه‌ی زیر کلید را بفرستید؛ "
                "پیام شما بعد از ذخیره پاک می‌شود."
            )
        await _edit_query(query, 
            "\n".join(lines),
            reply_markup=ui.provider_key_actions(provider["name"], has_key),
            parse_mode="Markdown",
        )
        return

    if data == "provider_back":
        active = ai.active_provider()["name"]
        await _edit_query(query, 
            "🔌 سرویس فعال: **{}**\nروی سرویس دلخواه بزن.".format(active),
            reply_markup=ui.provider_keyboard(active, ai.provider_status()),
            parse_mode="Markdown",
        )
        return

    if data == "provider_help":
        await _edit_query(query, 
            "🔌 **نحوه‌ی اتصال سرویس**\n\n"
            "۱) روی سرویس مورد نظر بزن تا فعال شود.\n"
            "۲) روی «وارد کردن کلید» بزن و کلید را بفرست.\n"
            "۳) پیام کلید بعد از ذخیره پاک می‌شود.\n"
            "۴) کلید در data\\secrets.json ذخیره می‌شود، نه داخل کد.\n\n"
            "اگر کلید را از قبل در متغیر محیطی گذاشته‌ای، همان استفاده می‌شود:\n"
            "`[Environment]::SetEnvironmentVariable(\"OPENAI_API_KEY\", \"sk-...\", \"User\")`\n\n"
            "سرویس‌ها به‌ترتیب امتحان می‌شوند و اگر اولی خطا بدهد، "
            "بات خودکار می‌رود سراغ بعدی.",
            parse_mode="Markdown",
        )
        return

    if data.startswith("key_enter:"):
        name = data.split(":", 1)[1]
        session = _session(context)
        session["mode"] = "api_key"
        session["key_provider"] = name
        await _edit_query(query, 
            f"🔑 کلید سرویس «{name}» را بفرستید.\n"
            "پیام شما بعد از ذخیره پاک می‌شود.\n"
            "برای انصراف /cancel را بزنید."
        )
        return

    if data.startswith("key_clear:"):
        name = data.split(":", 1)[1]
        if secrets.clear_key(name):
            await _edit_query(query, 
                f"🗑 کلید «{name}» پاک شد.",
                reply_markup=ui.provider_key_actions(name, secrets.has_key(name)),
            )
        else:
            await query.answer("کلیدی برای این سرویس نبود.", show_alert=True)
        return

    # ---- مدل‌های سرویس AI
    if data.startswith("models_open:"):
        pname = data.split(":", 1)[1]
        if _provider_by_name(pname) is None:
            await query.answer("سرویس پیدا نشد.", show_alert=True)
            return
        session = _session(context)
        session["model_provider"] = pname
        session["model_query"] = ""
        session["model_page"] = 0
        await _edit_query(query, f"⏳ در حال دریافت مدل‌های «{pname}»…")
        await _show_models(update, context, pname, page=0, edit=True)
        return

    if data.startswith("models_page:"):
        try:
            page = int(data.split(":", 1)[1] or 0)
        except ValueError:
            page = 0
        session = _session(context)
        pname = str(session.get("model_provider") or ai.active_provider()["name"])
        await _show_models(update, context, pname, page=page, edit=True)
        return

    if data == "models_refresh":
        session = _session(context)
        pname = str(session.get("model_provider") or ai.active_provider()["name"])
        await _edit_query(query, f"⏳ در حال تازه‌سازی لیست مدل‌های «{pname}»…")
        await _show_models(update, context, pname, page=0, edit=True, force=True)
        return

    if data == "models_search_ask":
        session = _session(context)
        pname = str(session.get("model_provider") or ai.active_provider()["name"])
        session["mode"] = "model_search"
        session["model_provider"] = pname
        await _edit_query(
            query,
            f"🔍 عبارت جست‌وجو برای مدل‌های «{pname}» را بفرست.\n"
            "مثال: `free` برای دیدن مدل‌های رایگان.\n"
            "برای انصراف /cancel را بزن.",
            parse_mode="Markdown",
        )
        return

    if data == "models_search_clear":
        session = _session(context)
        session["model_query"] = ""
        pname = str(session.get("model_provider") or ai.active_provider()["name"])
        await _show_models(update, context, pname, page=0, edit=True)
        return

    if data.startswith("model_pick:"):
        token = data.split(":", 1)[1]
        model_id = ui.resolve_model_id(token)
        session = _session(context)
        pname = str(session.get("model_provider") or ai.active_provider()["name"])
        provider = _provider_by_name(pname)
        if provider is None or not model_id:
            await query.answer("مدل یا سرویس نامعتبر است.", show_alert=True)
            return
        ai.set_selected_model(pname, model_id)
        ai.set_provider(pname)
        await _edit_query(
            query,
            f"✅ مدل انتخاب شد.\n\n"
            f"سرویس: **{provider['name']}**\n"
            f"مدل: `{ai.effective_model(provider)}`",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 بازگشت به لیست مدل‌ها", callback_data=f"models_open:{pname}")],
                [InlineKeyboardButton("🔙 تنظیمات سرویس", callback_data=f"provider:{pname}")],
            ]),
            parse_mode="Markdown",
        )
        return

    if data == "models_back":
        session = _session(context)
        pname = str(session.get("model_provider") or ai.active_provider()["name"])
        provider = _provider_by_name(pname)
        if provider is None:
            await query.answer("سرویس پیدا نشد.", show_alert=True)
            return
        ai.set_provider(pname)
        has_key = bool(ai.api_key(provider))
        lines = [
            f"✅ سرویس فعال: **{provider['name']}**",
            f"مدل: `{ai.effective_model(provider)}`",
            f"آدرس: `{provider['base_url']}`",
            "",
        ]
        if has_key:
            src = ai.key_source(provider)
            where = (
                "متغیر محیطی" if src == "env" else f"data\\secrets.json ({secrets.masked(ai.api_key(provider))})"
            )
            lines.append(f"🔑 کلید ثبت شده — منبع: {where}")
        else:
            lines.append(
                "🔑 کلیدی ثبت نشده. با دکمه‌ی زیر کلید را بفرستید؛ "
                "پیام شما بعد از ذخیره پاک می‌شود."
            )
        await _edit_query(
            query,
            "\n".join(lines),
            reply_markup=ui.provider_key_actions(provider["name"], has_key),
            parse_mode="Markdown",
        )
        return

    # ---- خاموشی
    if data == "shutdown_ask":
        await _edit_query(query, 
            "⚠️ مطمئنی می‌خواهی لپ‌تاپ خاموش شود؟\n"
            f"بعد از تأیید، {config.SHUTDOWN_DELAY_SECONDS} ثانیه فرصت لغو داری.",
            reply_markup=ui.confirm_shutdown(),
        )
        return

    if data == "shutdown_yes":
        ok, msg = await power.schedule_shutdown()
        if not ok:
            await _edit_query(query, f"❌ خاموش کردن ناموفق بود:\n{msg}")
            return
        await _edit_query(query, 
            f"⏻ {msg}\nاگر اشتباه زدی، از دکمه‌ی زیر لغو کن.",
            reply_markup=ui.shutdown_pending(),
        )
        return

    if data in {"shutdown_no", "shutdown_abort"}:
        ok, msg = await power.abort_shutdown()
        await _edit_query(query, f"✅ {msg}" if ok else f"ℹ️ {msg}")
        return


# ------------------------------------------------------------------ راه‌اندازی
async def _post_init(app: Application) -> None:
    logger.info("بات در حال اجرا — مالک‌ها: %s", _owner_ids())

    # خطاهای taskهای asyncio که به هیچ هندلری نمی‌رسند (مثلاً crash در
    # task پس‌زمینه) به‌صورت پیش‌فرض فقط در کنسول گم می‌شوند و گاهی
    # کل event loop را می‌کشند. این handler همه را در لاگ می‌نویسد.
    def _loop_exception_handler(loop, context):
        exc = context.get("exception")
        msg = context.get("message", "")
        if exc is not None:
            logger.critical("خطای رسیده‌نگه‌داشته‌شده در asyncio: %s", msg, exc_info=exc)
        else:
            logger.critical(" رویداد asyncio بدون استثنا: %s | کلیدها: %s",
                            msg, list(context.keys()))

    asyncio.get_event_loop().set_exception_handler(_loop_exception_handler)

    # پیش‌گرم کردن کش برنامه‌ها تا اولین استفاده سریع باشد
    asyncio.create_task(_warm_cache(app))


async def _warm_cache(app: Application) -> None:
    try:
        data = await apps.load_apps()
        logger.info("کش برنامه‌ها آماده شد: %d مورد", len(data))
    except Exception:
        logger.exception("گرم کردن کش برنامه‌ها ناموفق بود")


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    گزارش خطا بدون ریختن traceback در ترمینال.

    خطاهای شبکه در حالت polling (به‌خصوص وقتی VPN یا پراکسی در مسیر
    باشد) عادی‌اند و خود کتابخانه دوباره تلاش می‌کند؛ فقط یک خط کافی است.
    بقیه‌ی خطاها کامل در logs\bot.log نوشته می‌شوند.
    """
    err = context.error
    if err is None:
        return

    # ترتیب مهم است: TimedOut زیرمجموعه‌ی NetworkError است، پس اول آن
    # بررسی می‌شود وگرنه پیام کلی «قطع ارتباط» نشان داده می‌شود.
    if isinstance(err, telegram.error.TimedOut):
        logger.warning("تلگرام در زمان مقرر جواب نداد؛ دوباره تلاش می‌کنم. (%s)", err)
        return

    if isinstance(err, telegram.error.NetworkError):
        logger.warning("قطع ارتباط با تلگرام؛ خودکار دوباره تلاش می‌کنم. (%s)", err)
        return

    logger.error("خطای پیش‌بینی‌نشده", exc_info=err)


def _build_request() -> "HTTPXRequest":
    """
    تنظیم ارتباط با تلگرام.

    پیش‌فرض کتابخانه فقط ۵ ثانیه timeout دارد و ارسال فایل اسکرین‌شات
    روی اینترنت کند از آن رد می‌شود و خطای «Timed out» می‌دهد.
    """
    return HTTPXRequest(
        connection_pool_size=config.TELEGRAM_POOL_SIZE,
        connect_timeout=config.TELEGRAM_CONNECT_TIMEOUT,
        read_timeout=config.TELEGRAM_READ_TIMEOUT,
        write_timeout=config.TELEGRAM_WRITE_TIMEOUT,
        pool_timeout=config.TELEGRAM_POOL_TIMEOUT,
    )


def _build_poll_request() -> "HTTPXRequest":
    """
    request جداگانه‌ی getUpdates (polling).

    کتابخانه‌ی PTB برای polling از یک HTTPXRequest دوم استفاده می‌کند که
    فقط با get_updates_request روی builder تنظیم می‌شود. بدون آن، درخواست
    long-poll ده‌ثانیه‌ای با timeout پیش‌فرض httpx (۵ ثانیه) ارسال می‌شد و
    همیشه «Timed out» می‌گرفت؛ بات بالا می‌آمد ولی هیچ پیامی نمی‌گرفت.
    read_timeout باید بزرگ‌تر از timeout خود long-poll باشد.
    """
    return HTTPXRequest(
        connection_pool_size=1,
        connect_timeout=config.TELEGRAM_CONNECT_TIMEOUT,
        read_timeout=config.TELEGRAM_POLL_READ_TIMEOUT,
        write_timeout=config.TELEGRAM_WRITE_TIMEOUT,
        pool_timeout=config.TELEGRAM_POOL_TIMEOUT,
    )


def build_app() -> Application:
    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .request(_build_request())
        .get_updates_request(_build_poll_request())
        .post_init(_post_init)
        .concurrent_updates(True)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))

    app.add_handler(CommandHandler(["shot", "screenshot"], cmd_shot))
    app.add_handler(CommandHandler("camera", cmd_camera))
    app.add_handler(CommandHandler("camera_setup", cmd_camera_setup))

    app.add_handler(CommandHandler("apps", cmd_apps))
    app.add_handler(CommandHandler("open", cmd_open))
    app.add_handler(CommandHandler("search", cmd_search))

    app.add_handler(CommandHandler(["prompt", "ai"], cmd_prompt))
    app.add_handler(CommandHandler("provider", cmd_provider))
    app.add_handler(CommandHandler(["tabopencode", "tabs", "tab"], cmd_tabs))
    app.add_handler(CommandHandler("mouse", cmd_mouse))
    app.add_handler(CommandHandler("google", cmd_google))
    app.add_handler(CommandHandler("remote", cmd_remote))
    app.add_handler(CommandHandler("remote_stop", cmd_remote_stop))
    app.add_handler(CommandHandler("send", cmd_send))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("reject", cmd_reject))
    app.add_handler(CommandHandler("stop", cmd_stop))

    app.add_handler(CommandHandler("shutdown", cmd_shutdown))
    app.add_handler(CommandHandler("cancel_shutdown", cmd_cancel_shutdown))
    app.add_handler(CommandHandler("lock", cmd_lock))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_text)
    )
    app.add_handler(CallbackQueryHandler(on_callback))

    app.add_error_handler(_on_error)
    return app


SETUP_HELP = """
────────────────────────────────────────────────────────────
  راه‌اندازی بات کنترل لپ‌تاپ
────────────────────────────────────────────────────────────

  فایل config.py را باز کنید و این دو مورد را پر کنید:

    1) BOT_TOKEN
       توکنی که از @BotFather گرفته‌اید.

    2) OWNER_IDS
       عددِ user id خودتان. آن را از @userinfobot بگیرید.
       مثال:   OWNER_IDS = {123456789}

       ⚠️ حتماً عدد خودتان باشد. هر کسی که در این لیست نباشد
       نمی‌تواند سیستم شما را کنترل کند.

  همچنین برای ساخت پرامپت، کلید سرویس هوش مصنوعی را در متغیر محیطی
  بگذارید (کلیدها داخل کد نوشته نمی‌شوند):

    [Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "sk-...", "User")

  بعد از ذخیره، دوباره start.bat را اجرا کنید.

  تست سلامت پروژه:      python selftest.py
  بررسی پنجره‌ی OpenCode: python capture_templates.py --check
────────────────────────────────────────────────────────────
""".strip()


def print_setup_help() -> int:
    """راهنمای پیکربندی، فقط وقتی config.py هنوز پر نشده است."""
    print(SETUP_HELP)
    return 0


def main() -> int:
    """
    نقطه‌ی ورود.

    کد خروج:
      0  خاموش شدن عادی
      1  پیکربندی ناقص (config.py)
      2  نمونه‌ی دیگری از بات در حال اجراست
      3  توکن نامعتبر
      4  خطای شبکه هنگام اتصال به تلگرام
    """
    # crash های لایه‌ی C (درون pywin32/PIL/ctypes) هیچ traceback پایتونی
    # ندارند و پروسه را با کد -1 می‌کشند. faulthandler آدرس‌ها را در
    # logs\crash.log می‌نویسد تا محل خرابی قابل پیگیری باشد.
    try:
        import faulthandler

        crash_path = os.path.join(os.path.dirname(config.LOG_FILE), "crash.log")
        os.makedirs(os.path.dirname(crash_path), exist_ok=True)
        _crash_fh = open(crash_path, "a", encoding="utf-8")
        faulthandler.enable(file=_crash_fh, all_threads=True)
    except Exception:
        pass

    # هر استثنایی که از run_polling بیرون بزند (حتی BaseExceptionهای عجیب)
    # باید در لاگ دیده شود؛ وگرنه فقط کد خروج -1 می‌ماند.
    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical(
            "استثنای رسیده‌نگه‌داشته‌شده در سطح بالا",
            exc_info=(exc_type, exc_value, exc_tb),
        )

    sys.excepthook = _excepthook

    if config.BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        return print_setup_help()
    if not config.OWNER_IDS or all(x == 111111111 for x in config.OWNER_IDS):
        print("هشدار: OWNER_IDS هنوز پیکربندی نشده است (config.py).\n")
        return print_setup_help()

    _setup_logging()

    # دو نمونه روی یک توکن تلگرام با خطای Conflict می‌شکنند،
    # پس فقط یکی اجازه‌ی اجرا دارد.
    if not singleton.acquire():
        print(singleton.describe())
        print(
            "برای اجرای دستی، ابتدا نمونه‌ی قبلی را ببندید "
            "(اگر در Task Scheduler است: Stop-ScheduledTask)."
        )
        return 2

    try:
        build_app().run_polling(allowed_updates=["message", "callback_query"])
    except KeyboardInterrupt:
        logger.info("توقف با درخواست کاربر (Ctrl+C)")
    except telegram.error.InvalidToken:
        # رایج‌ترین اشتباه در اجرای اول؛ traceback خام کمکی نمی‌کند
        logger.error(
            "توکن از سمت تلگرام رد شد. توکن را با BotFather بررسی کنید."
        )
        print(
            "\n"
            "────────────────────────────────────────────────────────────\n"
            "  توکن بات معتبر نیست و تلگرام آن را رد کرد.\n"
            "\n"
            "  ۱) به @BotFather پیام /token بدهید و توکن را بگیرید\n"
            "  ۲) دقت کنید فاصله یا کوتیشن اضافه نباشد\n"
            "  ۳) مقدار BOT_TOKEN را در config.py بگذارید\n"
            "────────────────────────────────────────────────────────────"
        )
        return 3
    except telegram.error.NetworkError as exc:
        logger.error("خطای شبکه هنگام اتصال به تلگرام: %s", exc)
        print(
            "\n"
            "────────────────────────────────────────────────────────────\n"
            "  اتصال به تلگرام برقرار نشد.\n"
            "  اینترنت، فایروال، یا فیلتر را بررسی کنید.\n"
            "  (اگر از VPN یا پراکسی استفاده می‌کنید، آن را بررسی کنید.)\n"
            "────────────────────────────────────────────────────────────"
        )
        return 4
    except BaseException:
        # هر استثنای دیگری (حتی SystemExit عجیب) باید در لاگ دیده شود؛
        # وگرنه فقط «exit code -1» می‌ماند و هیچ سرنخی نیست.
        logger.critical("خطای غیرمنتظره در run_polling", exc_info=True)
        raise
    finally:
        singleton.release()
    return 0


if __name__ == "__main__":
    if "--setup-help" in sys.argv:
        sys.exit(print_setup_help())
    sys.exit(main())
