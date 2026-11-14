# -*- coding: utf-8 -*-
"""
تست‌های سبک بدون نیاز به اتصال به تلگرام.

اجرا:
    python selftest.py
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from typing import List
from unittest.mock import AsyncMock, MagicMock

import config
from telegram import ReplyKeyboardMarkup

# این باید قبل از بقیه‌ی ایمپورت‌ها باشد تا main بتواند config را بخواند
from core import ai  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("selftest")

RESULTS: List[tuple] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))


def make_fake_bot():
    bot = MagicMock()
    for name in (
        "send_message",
        "send_photo",
        "send_document",
        "send_chat_action",
        "reply_text",
        "reply_photo",
        "reply_document",
        "edit_message_media",
        "edit_message_caption",
        "delete_message",
    ):
        setattr(bot, name, AsyncMock())
    return bot


def make_update(user_id: int, text: str = "/start", chat_type: str = "private"):
    user = MagicMock()
    user.id = user_id
    user.username = f"user{user_id}"
    chat = MagicMock()
    chat.id = user_id
    chat.type = chat_type
    message = MagicMock()
    message.text = text
    message.chat_id = user_id
    # reply_text باید خودِ پیام را برگرداند، چون کدها با مقدار بازگشتی
    # کار می‌کنند (مثلاً status = await reply_text(...) و بعد status.edit_text)
    message.reply_text = AsyncMock(return_value=message)
    message.reply_photo = AsyncMock(return_value=message)
    message.reply_document = AsyncMock(return_value=message)
    message.edit_text = AsyncMock(return_value=message)
    message.delete = AsyncMock()
    message.chat = chat
    message.get_bot = MagicMock(return_value=make_fake_bot())
    update = MagicMock()
    update.effective_user = user
    update.effective_chat = chat
    update.effective_message = message
    update.callback_query = None
    update.get_bot = MagicMock(return_value=message.get_bot())
    return update


def make_context():
    context = MagicMock()
    context.user_data = {}
    context.chat_data = {}
    context.args = []
    context.error = None
    context.bot = make_fake_bot()
    return context


# ---------------------------------------------------------------- تست‌ها
async def test_security():
    from core import security

    config.OWNER_IDS = {42}
    owner = 42
    stranger = 999

    async def handler(u, c):
        return "ran"

    owner_only = security.owner_only(handler)

    check(
        "مالک اجازه دارد",
        await owner_only(make_update(owner), make_context()) == "ran",
    )
    check(
        "کاربر غریبه مسدود می‌شود",
        await owner_only(make_update(stranger), make_context()) is None,
    )
    u = make_update(stranger)
    await owner_only(u, make_context())
    check(
        "به غریبه هیچ پاسخی داده نمی‌شود",
        not u.effective_message.reply_text.called,
    )

    config.PRIVATE_CHAT_ONLY = True
    check(
        "در گروه مسدود می‌شود",
        await owner_only(make_update(owner, chat_type="group"), make_context()) is None,
    )
    check(
        "در چت خصوصی کار می‌کند",
        await owner_only(make_update(owner, chat_type="private"), make_context()) == "ran",
    )
    check(
        "چک مالک روی user id است، نه نام کاربری",
        security.is_owner(MagicMock(id=42)) is True
        and security.is_owner(MagicMock(id=43)) is False,
    )
    config.OWNER_IDS = {111111111}


async def test_queue_serialization():
    from core.jobs import JobQueue

    q = JobQueue()
    order: List[str] = []

    async def slow():
        order.append("slow-start")
        await asyncio.sleep(0.4)
        order.append("slow-end")

    async def fast():
        order.append("fast")

    t1 = asyncio.create_task(q.run("slow", slow))
    await asyncio.sleep(0.05)
    check("صف در حین کار، مشغول است", q.busy)
    t2 = asyncio.create_task(q.run("fast", fast))
    await asyncio.gather(t1, t2)
    check("دو کار هم‌زمان اجرا نشدند", order == ["slow-start", "slow-end", "fast"], str(order))
    check("بعد از پایان، صف آزاد است", not q.busy)


async def test_app_source_integrity():
    from core import apps

    data = await apps.load_apps()
    check("لیست برنامه‌ها پر است", len(data) > 20, f"{len(data)} برنامه")
    check(
        "همه‌ی موارد شناسه اجرا دارند",
        all(a.get("app_id") and a.get("name") for a in data),
    )
    res = apps.search(data, "notepad")
    check("جست‌وجو کار می‌کند", len(res) > 0, f"{len(res)} نتیجه")
    exact = apps.search(data, "notepad")[0]
    check(
        "اولین نتیجه دقیق‌ترین تطابق است",
        exact["name"].lower() == "notepad",
        exact["name"],
    )
    check("جست‌وجوی بی‌ربط خالی است", len(apps.search(data, "zzzqqqxyz")) == 0)


async def test_prompt_fallback():
    from core import ai

    calls: List[str] = []
    original = ai._call_provider

    async def flaky(provider, raw):
        calls.append(provider["name"])
        if len(calls) < 3:
            raise RuntimeError("timeout شبیه‌سازی‌شده")
        return "پرامپت نهایی نمونه"

    ai._call_provider = flaky
    try:
        old_providers = config.AI_PROVIDERS
        old_default = config.DEFAULT_PROVIDER
        config.AI_PROVIDERS = [
            {"name": "A", "base_url": "http://x", "model": "m", "api_key_env": "", "auth": "none"},
            {"name": "B", "base_url": "http://y", "model": "m", "api_key_env": "", "auth": "none"},
            {"name": "C", "base_url": "http://z", "model": "m", "api_key_env": "", "auth": "none"},
        ]
        config.DEFAULT_PROVIDER = "A"
        ai.reset_cache()
        ai.set_provider("A")

        ok, text, provider = await ai.build_prompt("یک متن خام")
        check("فالبک به سرویس بعدی رفت", ok and provider == "C", f"provider={provider}")
        check("هر سه سرویس امتحان شدند", calls == ["A", "B", "C"], str(calls))

        async def all_fail(provider, raw):
            raise RuntimeError("خطا")

        ai._call_provider = all_fail
        ok2, text2, _ = await ai.build_prompt("متن")
        check("خطای همه‌ی سرویس‌ها درست مدیریت شد", not ok2 and "ممکن نشد" in text2)

        config.AI_PROVIDERS = old_providers
        config.DEFAULT_PROVIDER = old_default
        ai.reset_cache()
    finally:
        ai._call_provider = original
        ai.reset_cache()


async def test_app_callback_guard():
    """اجرا فقط از روی لیست واقعی مجاز است."""
    from core import ui

    short = "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"
    check("شناسه‌ی کوتاه دست‌نخورده می‌ماند", ui.remember_app_id(short) == short)

    # شناسه‌های واقعی میانبرها اغلب از ۶۴ بایت بلندترند
    long_id = (
        "C:\\Users\\SomeUser\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\"
        "Programs\\Some Very Long Folder Name\\Application\\program.exe"
    )
    token = ui.remember_app_id(long_id)
    check(
        "شناسه‌ی بلند به کلید کوتاه تبدیل شد",
        len(f"app:{token}".encode("utf-8")) <= 64,
        f"{len(token)} بایت",
    )
    check(
        "شناسه‌ی بلند قابل بازیابی است",
        ui.resolve_app_id(token) == long_id,
    )
    check(
        "callback_data همیشه زیر ۶۴ بایت می‌ماند",
        all(
            len(f"app:{ui.remember_app_id(a)}".encode("utf-8")) <= 64
            for a in (short, long_id, "ا:ب" * 40)
        ),
    )


async def test_shutdown_confirmation_flow():
    """تأخیر و لغو خاموشی (بدون اجرای واقعی دستور ویندوز)."""
    from core import power

    check(
        "تأخیر خاموشی بین ۳۰ تا ۶۰ ثانیه است",
        30 <= config.SHUTDOWN_DELAY_SECONDS <= 60,
        f"{config.SHUTDOWN_DELAY_SECONDS} ثانیه",
    )
    pending = await power.is_shutdown_pending()
    check("بررسی وضعیت خاموشی بدون تغییر سیستم کار می‌کند", pending is False)


async def test_window_helpers():
    from core import windows

    snaps = windows.window_handles()
    check("پنجره‌های باز شمرده می‌شوند", len(snaps) >= 0, f"{len(snaps)} پنجره")
    listed = windows.list_windows()
    check("فهرست پنجره‌ها ساخته می‌شود", isinstance(listed, list))
    check(
        "پنجره‌های سیستمی حذف شده‌اند",
        all("Shell_TrayWnd" not in str(w.get("title", "")) for w in listed),
    )
    dead = 999999999
    check("handle نامعتبر تشخیص داده می‌شود", windows.is_window(dead) is False)
    check("focus روی handle ناموفق، False برمی‌گرداند", windows.focus(dead) is False)

    # شناسه‌ی AppUserModel (مثل ai.opencode.desktop) نباید مسیر فایل تلقی شود؛
    # وگرنه launch آن را به start می‌دهد که اجرا نمی‌کند.
    check(
        "شناسه‌ی AppUserModel تشخیص داده می‌شود",
        windows._is_aumid("ai.opencode.desktop")
        and not windows._is_aumid(r"C:\Program Files\App\app.exe")
        and not windows._is_aumid(r"C:\Users\x\a.lnk")
        and not windows._is_aumid("noextension"),
    )
    aumid_lnk = windows._shortcut_by_aumid("ai.opencode.desktop")
    check(
        "میانبر OpenCode از روی شناسه‌ی AppUserModel پیدا می‌شود",
        aumid_lnk is not None and "opencode" in aumid_lnk.lower(),
        str(aumid_lnk),
    )


async def test_imaging_pipeline():
    from core import imaging

    img = await imaging.grab_screen_async()
    check("اسکرین‌شات گرفته شد", img.size[0] > 100 and img.size[1] > 100, str(img.size))
    jpeg = imaging.encode_jpeg(img)
    png = imaging.encode_png(img)
    check("نسخه‌ی JPEG ساخته شد", len(jpeg) > 1000, f"{len(jpeg)//1024} KB")
    check("نسخه‌ی PNG ساخته شد", len(png) > 1000, f"{len(png)//1024} KB")
    check(
        "هر دو نسخه مستقل هستند (تلگرام عکس را له می‌کند)",
        jpeg != png,
    )

    crop = imaging.crop_by_ratio(img, (0.0, 0.0, 0.5, 0.5))
    check("برش ناحیه کار می‌کند", crop.size == (img.size[0] // 2, img.size[1] // 2), str(crop.size))
    d = imaging.region_diff_ratio(img, img)
    check("تصویر با خودش فرقی ندارد", d == 0.0, f"diff={d}")
    d2 = imaging.region_diff_ratio(crop, imaging.crop_by_ratio(img, (0.5, 0.5, 1.0, 1.0)))
    check("ناحیه‌های متفاوت تفاوت دارند", d2 > 0.01, f"diff={d2:.4f}")


async def test_template_matching():
    from core import imaging

    img = await imaging.grab_screen_async()
    w, h = img.size
    tw, th = 48, 36

    # خودمان یک ناحیه‌ی پرجزئیات پیدا می‌کنیم (محتوای صفحه تغییر می‌کند،
    # پس نمی‌شود مختصات ثابت فرض کرد).
    best = None
    for y in range(0, h - th, 40):
        for x in range(0, w - tw, 40):
            crop = img.crop((x, y, x + tw, y + th))
            std = imaging._std_of(crop)
            if best is None or std > best[0]:
                best = (std, x, y)
    std, x0, y0 = best
    tpl = img.crop((x0, y0, x0 + tw, y0 + th))

    check(
        "ناحیه‌ی پرجزئیات برای تست پیدا شد",
        std >= config.MIN_TEMPLATE_STD,
        f"انحراف معیار {std:.1f} در ({x0}, {y0})",
    )

    path = os.path.join(config.ASSETS_DIR, "_selftest_tpl.png")
    tpl.save(path)
    imaging._template_cache.pop("_selftest_tpl.png", None)
    try:
        # ناحیه‌ی جست‌وجو را تنگ می‌گیریم تا محل درست یکتا باشد؛ رابط‌های
        # ترمینال ردیف‌های تکراری دارند و در ناحیه‌ی بزرگ، تطابقی با
        # امتیاز برابر می‌تواند زودتر پیدا شود.
        pad = 30
        region = (
            (x0 - pad) / w, (y0 - pad) / h,
            (x0 + tw + pad) / w, (y0 + th + pad) / h,
        )
        found, box = imaging.find_template(
            img, "_selftest_tpl.png", region=region, threshold=0.95
        )
        ok = (
            found
            and abs(box[0] - x0) <= 2
            and abs(box[1] - y0) <= 2
            and box[2:] == (tw, th)
        )
        check("قالب تصویری در جای درست پیدا شد", ok, f"box={box}")
    finally:
        os.remove(path)
        imaging._template_cache.pop("_selftest_tpl.png", None)

    found3, _ = imaging.find_template(img, "file_does_not_exist.png")
    check("قالب ناموجود بی‌خطر رد می‌شود", not found3)

    found4, _ = imaging.find_template(
        img, "file_does_not_exist.png", region=(0.9, 0.9, 0.95, 0.95)
    )
    check("ناحیه‌ی بی‌اعتبار بی‌خطر رد می‌شود", not found4)


async def test_handlers_registered():
    import re

    import main

    config.BOT_TOKEN = "123456:TEST-TOKEN"
    app = main.build_app()
    total = sum(len(v) for v in app.handlers.values())
    check("همه‌ی هندلرها ثبت شدند", total >= 14, f"{total} هندلر")
    check("منوی اصلی ساخته می‌شود", bool(main.ui.main_menu().keyboard))
    check(
        "کیبورد تأیید خاموشی دو دکمه دارد",
        len(main.ui.confirm_shutdown().inline_keyboard) == 1
        and len(main.ui.confirm_shutdown().inline_keyboard[0]) == 2,
    )
    #HELP_TEXT با parse_mode="Markdown" فرستاده می‌شود؛ یک علامت * یا _ زوج
    #نکرده کل پیام /start و /help را با خطای «Can't parse entities» می‌شکند.
    #داخل `code` تلگرام چیزی تفسیر نمی‌کند، پس اول آن‌ها حذف می‌شوند.
    stripped = re.sub(r"```.*?```", "", main.HELP_TEXT, flags=re.S)
    stripped = re.sub(r"`[^`]*`", "", stripped)
    unpaired = [
        m
        for m in ("*", "_")
        if len(re.findall(re.escape(m), stripped)) % 2 != 0
    ]
    check(
        "MARKDOWN HELP_TEXT علامت‌های * و _ زوج دارد",
        not unpaired,
        f"زوج‌نشده: {unpaired}",
    )


async def test_pagination():
    from core import ui

    items = [{"name": f"A{i}", "app_id": f"id{i}"} for i in range(25)]
    page0 = items[0:9]
    page2 = items[18:25]
    check("صفحه‌ی اول ۹ مورد دارد", len(page0) == config.APPS_PAGE_SIZE)
    check("صفحه‌ی آخر باقی‌مانده را دارد", len(page2) == 7)
    kb = ui.apps_keyboard(page0, 0, 3)
    check("صفحه‌بندی دکمه‌ی بعدی دارد", "بعدی ▶️" in str(kb))


def make_callback(data: str):
    update = make_update(111111111)
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = update.effective_message
    update.callback_query = query
    return update


async def test_callback_routing():
    """مسیریابی دکمه‌ها، بدون اجرای واقعی کارهای خطرناک."""
    import main

    ctx = make_context()
    ctx.user_data["all_apps"] = [
        {"name": "Notepad", "app_id": "np.exe"},
        {"name": "Paint", "app_id": "paint.exe"},
    ]

    # ناوبری صفحه‌بندی
    u = make_callback("apps:0")
    await main.on_callback(u, ctx)
    check("دکمه‌ی صفحه‌بندی کار می‌کند", u.callback_query.edit_message_text.called)

    # جست‌وجو وارد حالت search می‌شود
    u = make_callback("search_enter")
    await main.on_callback(u, ctx)
    check("دکمه‌ی جست‌وجو حالت جست‌وجو را فعال می‌کند", ctx.user_data["session"]["mode"] == "search")

    # اجرای برنامه فقط از روی لیست واقعی
    old_launch = main.windows.launch
    old_wait = main.windows.wait_for_new_window
    old_wait_resp = main.windows.wait_until_responsive
    old_shot = main.imaging.send_screenshot_to
    old_grab = main.imaging.grab_screen_async
    old_find = main.windows.find_window
    launched = []

    def fake_launch(app_id):
        launched.append(app_id)

    # این دو داخل asyncio.to_thread صدا زده می‌شوند، پس باید همگام باشند
    # (کوروتین‌ساز باعث TypeError: 'coroutine' object is not subscriptable می‌شود).
    def fake_wait(*a, **kw):
        return {"hwnd": 1, "title": "Notepad"}

    def fake_responsive(*a, **kw):
        return 1

    async def fake_shot(*a, **kw):
        return None

    main.windows.launch = fake_launch
    main.windows.wait_for_new_window = fake_wait
    main.windows.wait_until_responsive = fake_responsive
    # جست‌وجوی پنجره‌ی «از قبل باز» در تست نباید به پنجره‌ی واقعی سیستم بخورد
    main.windows.find_window = lambda *a, **kw: None
    main.imaging.send_screenshot_to = fake_shot
    main.imaging.grab_screen_async = AsyncMock(
        return_value=MagicMock(size=(10, 10))
    )
    try:
        u = make_callback("app:np.exe")
        await main.on_callback(u, ctx)
        check("برنامه از روی لیست اجرا می‌شود", launched == ["np.exe"], str(launched))

        launched.clear()
        u = make_callback("app:malware.exe")
        await main.on_callback(u, ctx)
        check("برنامه‌ی خارج از لیست اجرا نمی‌شود", not launched, str(launched))
        check(
            "به کاربر هشدار داده می‌شود",
            u.callback_query.answer.call_args.kwargs.get("show_alert") is True,
        )

        # شناسه‌ی بلند که به کلید هش‌شده تبدیل شده هم باید کار کند
        long_id = "C:\\Users\\x\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\A\\app.exe"
        token = main.ui.remember_app_id(long_id)
        ctx.user_data["all_apps"] = [{"name": "A", "app_id": long_id}]
        launched.clear()
        u = make_callback(f"app:{token}")
        await main.on_callback(u, ctx)
        check("شناسه‌ی بلند (هش‌شده) هم اجرا می‌شود", launched == [long_id], str(launched))
    finally:
        main.windows.launch = old_launch
        main.windows.wait_for_new_window = old_wait
        main.windows.wait_until_responsive = old_wait_resp
        main.windows.find_window = old_find
        main.imaging.send_screenshot_to = old_shot
        main.imaging.grab_screen_async = old_grab

    # تغییر سرویس AI
    old = config.AI_PROVIDERS
    config.AI_PROVIDERS = [
        {"name": "سرویس الف", "base_url": "http://a", "model": "m", "api_key_env": "", "auth": "none"},
        {"name": "سرویس ب", "base_url": "http://b", "model": "m", "api_key_env": "", "auth": "none"},
    ]
    from core import ai

    ai.reset_cache()
    u = make_callback("provider:سرویس ب")
    await main.on_callback(u, ctx)
    check("تغییر سرویس AI کار می‌کند", ai.active_provider()["name"] == "سرویس ب")
    config.AI_PROVIDERS = old
    ai.reset_cache()

    # تأیید/رد/توقف OpenCode
    main.opencode.approve = AsyncMock(return_value=(True, "تأیید شد"))
    u = make_callback("oc_approve")
    await main.on_callback(u, ctx)
    check("دکمه‌ی تأیید OpenCode کار می‌کند", main.opencode.approve.called)


async def test_shutdown_button_flow():
    """
    جریان تأیید و لغو خاموشی.

    خودِ دستور shutdown اجرا نمی‌شود تا سیستم واقعی خاموش نشود؛
    فقط بررسی می‌کنیم که مراحل و کیبوردها درست باشند.
    """
    import main
    from core import power

    calls = []

    async def fake_schedule(delay_seconds=None, message=None):
        calls.append(("schedule", delay_seconds))
        return True, "خاموشی زمان‌بندی شد."

    async def fake_abort():
        calls.append(("abort", None))
        return True, "لغو شد."

    old_sched, old_abort = power.schedule_shutdown, power.abort_shutdown
    power.schedule_shutdown = fake_schedule
    power.abort_shutdown = fake_abort
    try:
        u = make_callback("shutdown_ask")
        await main.on_callback(u, u and make_context())
        kb = u.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
        labels = [b.text for row in kb.inline_keyboard for b in row]
        check(
            "دکمه‌های تأیید و لغو خاموشی وجود دارند",
            "بله، خاموش کن" in labels and "لغو" in labels,
            str(labels),
        )

        u = make_callback("shutdown_yes")
        await main.on_callback(u, make_context())
        check("دستور خاموشی زمان‌بندی می‌شود", ("schedule", None) in calls)
        kb = u.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
        labels = [b.text for row in kb.inline_keyboard for b in row]
        check(
            "بعد از تأیید، دکمه‌ی لغو خاموشی فرستاده می‌شود",
            "❌ لغو خاموشی" in labels,
            str(labels),
        )

        u = make_callback("shutdown_abort")
        await main.on_callback(u, make_context())
        check("دکمه‌ی لغو خاموشی، دستور shutdown /a را اجرا می‌کند", ("abort", None) in calls)
        check("تعداد فراخوانی‌ها درست است", calls == [("schedule", None), ("abort", None)], str(calls))
    finally:
        power.schedule_shutdown = old_sched
        power.abort_shutdown = old_abort


async def test_opencode_orchestration():
    """ترتیب مراحل: پیدا کردن پنجره -> اسکرین‌شات -> چسباندن -> ارسال -> انتظار -> گزارش"""
    import main
    from core import opencode

    order = []

    async def fake_send_prompt(prompt, bot, chat_id, should_cancel):
        order.append("send_prompt")
        return "completed", 12.5

    async def fake_capture(bot, chat_id, hwnd=None, extra_shots=None):
        order.append("capture_final")

    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    fake_bot.send_photo = AsyncMock()
    fake_bot.send_document = AsyncMock()

    old_send = opencode.send_prompt
    old_capture = opencode.capture_final
    old_running = opencode.is_running
    opencode.send_prompt = fake_send_prompt
    opencode.capture_final = fake_capture
    opencode.is_running = lambda: False
    try:
        ctx = make_context()
        ctx.user_data["session"] = {"last": "پرامپت نمونه"}
        u = make_update(111111111)
        ctx.bot = fake_bot
        await main._run_opencode(u, ctx, "پرامپت نمونه")

        check("send_prompt صدا زده شد", order == ["send_prompt", "capture_final"], str(order))
        texts = [c.args[1] for c in fake_bot.send_message.call_args_list]
        check(
            "پیام پایان با مدت اجرا ارسال شد",
            any("تموم شد" in t and "12" in t for t in texts),
            str(texts[-2:]),
        )
    finally:
        opencode.send_prompt = old_send
        opencode.capture_final = old_capture
        opencode.is_running = old_running


async def test_opencode_outcomes():
    """هر نتیجه‌ی اتوماسیون باید پیام درست بدهد."""
    import main
    from core import opencode

    cases = {
        "awaiting_approval": "تأیید",
        "cancelled": "متوقف",
        "timeout": "سقف زمانی",
        "failed": "بسته شد",
    }

    old_send = opencode.send_prompt
    old_running = opencode.is_running
    opencode.is_running = lambda: False
    opencode.capture_final = AsyncMock()
    try:
        for outcome, expected in cases.items():

            async def fake_send(prompt, bot, chat_id, should_cancel, _o=outcome):
                return _o, 5.0

            opencode.send_prompt = fake_send
            ctx = make_context()
            ctx.user_data["session"] = {}
            fake_bot = MagicMock()
            fake_bot.send_message = AsyncMock()
            fake_bot.send_photo = AsyncMock()
            fake_bot.send_document = AsyncMock()
            u = make_update(111111111)
            ctx.bot = fake_bot
            await main._run_opencode(u, ctx, "x")
            texts = " ".join(c.args[1] for c in fake_bot.send_message.call_args_list)
            check(f"نتیجه‌ی «{outcome}» پیام درست می‌دهد", expected in texts, texts[:80])
    finally:
        opencode.send_prompt = old_send
        opencode.is_running = old_running


async def test_prompt_session_flow():
    """جریان متن خام -> پرامپت -> دکمه‌های ارسال"""
    import main
    from core import ai

    old_build = ai.build_prompt
    ai.build_prompt = AsyncMock(return_value=(True, "پرامپت ساخته‌شده", "TestProvider"))
    try:
        with temp_key("TestProvider", "sk-tmp-0000"):
            ai.set_provider("TestProvider")
            ctx = make_context()
            u = make_update(111111111)
            await main._generate_prompt(u, ctx, "متن خام کاربر")
            check("متن خام در نشست ذخیره شد",
                  ctx.user_data["session"]["raw"] == "متن خام کاربر")
            check("پرامپت در نشست ذخیره شد",
                  ctx.user_data["session"]["last"] == "پرامپت ساخته‌شده")
            saved = ai.load_prompt()
            check("پرامپت روی دیسک هم ذخیره شد", saved == "پرامپت ساخته‌شده", saved)

            async def failing(raw):
                return False, "ساخت پرامپت ممکن نشد.", ""

            ai.build_prompt = failing
            ctx2 = make_context()
            u2 = make_update(111111111)
            await main._generate_prompt(u2, ctx2, "متن خام دیگر")
            check("در صورت شکست، متن خام حفظ می‌شود",
                  ctx2.user_data["session"]["raw"] == "متن خام دیگر")
            check("در صورت شکست، پرامپت قبلی پاک می‌شود",
                  ctx2.user_data["session"]["last"] == "")
    finally:
        ai.build_prompt = old_build
        ai.save_prompt("")


async def test_no_false_completion_without_template():
    """
    نبودِ قالب تصویری نباید باعث اعلام اشتباه «کار تمام شد» بشود.

    چون «قالب پیدا نشد» با «دکمه نیست» فرق دارد؛ بدون این تفکیک، بات
    بلافاصله بعد از ارسال، پایان کار را اعلام می‌کرد.
    """
    import time as _time

    from core import imaging, opencode, windows

    handles = windows.window_handles()
    if not handles:
        check("پنجره‌ای برای تست پیدا شد", False, "هیچ پنجره‌ای باز نیست")
        return
    # اولین پنجره‌ای که rect معتبر و روی صفحه داشته باشد را انتخاب کن؛
    # پنجره‌های minimize شده مختصات -32000 دارند و اگر انتخاب شوند،
    # chat_region نامعتبر می‌شود، crop_by_ratio برمی‌گرداند None و حلقه‌ی
    # _wait_for_completion تا OPENCODE_TIMEOUT (چند دقیقه) می‌چرخد
    # و تست با timeout مواجه می‌شود (flaky بودن قبلی همین بود).
    sw, sh = 1920, 1080
    try:
        sw, sh = __import__("pyautogui").size()
    except Exception:
        pass
    hwnd = None
    for h in sorted(handles):
        rect = windows.window_rect(h)
        if rect is None:
            continue
        left, top, right, bottom = rect
        # روی صفحه باشد و اندازه‌ی معقول داشته باشد
        if right > left > -1000 and bottom > top > -1000 and (right - left) > 50 and (bottom - top) > 50:
            hwnd = h
            break
    if hwnd is None:
        check("پنجره‌ای برای تست پیدا شد", False, "پنجره‌ای با rect معتبر روی صفحه نیست")
        return

    old_tpl = config.OPENCODE_TEMPLATE_STOP
    old_idle = config.OPENCODE_IDLE_COMPLETE_SECONDS
    old_poll = config.OPENCODE_POLL_INTERVAL
    old_timeout = config.OPENCODE_TIMEOUT
    old_grab = imaging.grab_screen_async
    old_is_alive = opencode._window_alive

    config.OPENCODE_TEMPLATE_STOP = "definitely_missing.png"
    config.OPENCODE_IDLE_COMPLETE_SECONDS = 4.0
    config.OPENCODE_POLL_INTERVAL = 0.3
    config.OPENCODE_TIMEOUT = 15.0  # فقط برای تست؛ جلوی دویدن طولانی را می‌گیرد
    imaging._template_cache.pop("definitely_missing.png", None)

    static = await old_grab()
    imaging.grab_screen_async = AsyncMock(return_value=static)
    opencode._window_alive = lambda h: True

    # مبنایی که دقیقاً با تصویر فعلی یکی است => هیچ تغییری رخ نمی‌دهد
    region = opencode._chat_region(hwnd)
    opencode._state["baseline_chat"] = imaging.crop_by_ratio(static, region)

    sent = []

    async def notify(t):
        sent.append(t)

    async def shot(c):
        sent.append(c)

    try:
        start = _time.time()
        outcome = await opencode._wait_for_completion(
            hwnd, notify, shot, lambda: False, start
        )
        elapsed = _time.time() - start
        check(
            "بدون قالب، کار فوراً تمام‌شده اعلام نمی‌شود",
            elapsed >= config.OPENCODE_IDLE_COMPLETE_SECONDS,
            f"{elapsed:.1f} ثانیه گذشت",
        )
        check(
            "نتیجه بر اساس ثبات صفحه «completed» است",
            outcome == "completed",
            outcome,
        )
        check("کاربر مطلع شد", any("تمام شد" in s for s in sent), str(sent))
    finally:
        config.OPENCODE_TEMPLATE_STOP = old_tpl
        config.OPENCODE_IDLE_COMPLETE_SECONDS = old_idle
        config.OPENCODE_POLL_INTERVAL = old_poll
        config.OPENCODE_TIMEOUT = old_timeout
        imaging.grab_screen_async = old_grab
        opencode._window_alive = old_is_alive


async def test_resume_watch_after_approval():
    """بعد از زدن «تأیید»، بات باید دوباره منتظر پایان کار بماند."""
    import main
    from core import opencode

    fake_bot = MagicMock()
    fake_bot.send_message = AsyncMock()
    fake_bot.send_photo = AsyncMock()
    fake_bot.send_document = AsyncMock()

    old_approve = opencode.approve
    old_resume = opencode.resume_watch
    old_capture = opencode.capture_final

    resumed = {}

    async def fake_approve(hwnd=None):
        return True, "تأیید ارسال شد."

    async def fake_resume(bot, chat_id):
        resumed["called"] = (bot, chat_id)
        return "completed", 9.0

    async def fake_capture(bot, chat_id, hwnd=None, extra_shots=None):
        resumed["captured"] = True

    opencode.approve = fake_approve
    opencode.resume_watch = fake_resume
    opencode.capture_final = fake_capture
    try:
        ctx = make_context()
        ctx.user_data["session"] = {"awaiting": True}
        u = make_update(111111111)
        ctx.bot = fake_bot

        await main.cmd_approve(u, ctx)

        check("بعد از تأیید، نظارت از سر گرفته شد", "called" in resumed)
        check("اسکرین‌شات نهایی هم فرستاده شد", resumed.get("captured") is True)
        check("پرچم awaiting برداشته شد", not ctx.user_data["session"]["awaiting"])
        texts = [c.args[1] for c in fake_bot.send_message.call_args_list]
        check("پیام پایان فرستاده شد", any("تموم شد" in t for t in texts), str(texts))

        # اگر در حالت انتظار نباشیم، نباید نظارت از سر گرفته شود
        resumed.clear()
        ctx2 = make_context()
        ctx2.user_data["session"] = {"awaiting": False}
        await main.cmd_approve(make_update(111111111), ctx2)
        check("بدون حالت انتظار، نظارت دوباره شروع نمی‌شود", not resumed)
    finally:
        opencode.approve = old_approve
        opencode.resume_watch = old_resume
        opencode.capture_final = old_capture


async def test_flat_template_rejected():
    """
    قالب یکنواخت باید رد شود.

    چون با روش تفاضل مربعی، هر ناحیه‌ی یکنواخت دیگر هم تطابق کامل می‌دهد و
    نتیجه تشخیص اشتباه می‌شود (این در تست عملی دیده شد).
    """
    from core import imaging

    img = await imaging.grab_screen_async()
    flat = img.crop((0, 0, 2, 2)).resize((60, 40))
    path = os.path.join(config.ASSETS_DIR, "_selftest_flat.png")
    flat.save(path)
    imaging._template_cache.pop("_selftest_flat.png", None)
    try:
        check("قالب کاملاً یکنواخت شناسایی شد", imaging._std_of(flat) < 1.0)
        check(
            "قالب یکنواخت رد می‌شود",
            imaging.template_available("_selftest_flat.png") is False,
        )
        found, box = imaging.find_template(img, "_selftest_flat.png", threshold=0.5)
        check("قالب یکنواخت تطابق کاذب نمی‌دهد", not found, f"box={box}")
    finally:
        os.remove(path)
        imaging._template_cache.pop("_selftest_flat.png", None)


async def test_free_text_starts_prompt():
    """متن خامِ مالک بدون زدن دکمه هم باید به ساخت پرامپت برسد."""
    import main
    from core import ai

    old_build = ai.build_prompt
    seen = []

    async def fake_build(raw):
        seen.append(raw)
        return True, "پرامپت", "TestProvider"

    ai.build_prompt = fake_build
    try:
        with temp_key("TestProvider", "sk-tmp-0000"):
            ai.set_provider("TestProvider")
            ctx = make_context()
            ctx.user_data["session"] = {}
            u = make_update(111111111, text="یک درخواست ساده")
            await main.on_text(u, ctx)
            check("متن خام مستقیم به ساخت پرامپت رفت",
                  seen == ["یک درخواست ساده"], str(seen))

        # در حالت جست‌وجو باید جست‌وجو انجام شود، نه ساخت پرامپت
        seen.clear()
        ctx2 = make_context()
        ctx2.user_data["session"] = {"mode": "search"}
        ctx2.user_data["all_apps"] = [{"name": "Notepad", "app_id": "np"}]
        u2 = make_update(111111111, text="notepad")
        await main.on_text(u2, ctx2)
        check("در حالت جست‌وجو، متن به جست‌وجو می‌رود", not seen, str(seen))

        # دکمه‌های منو نباید به ساخت پرامپت بروند
        seen.clear()
        for btn in main.ui.MENU_BUTTONS:
            ctx3 = make_context()
            ctx3.user_data["session"] = {}
            ctx3.user_data["all_apps"] = [{"name": "A", "app_id": "a"}]
            await main.on_text(make_update(111111111, text=btn), ctx3)
        check("دکمه‌های منو به ساخت پرامپت نمی‌روند", not seen, str(seen))
    finally:
        ai.build_prompt = old_build
        ai.save_prompt("")


async def test_button_matching():
    """دکمه‌های منو باید با هر شکل نوشتاری شناخته شوند، بدون تطبیق کاذب."""
    from core import ui

    positives = [
        ui.BTN_SCREENSHOT, ui.BTN_CAMERA, ui.BTN_APPS, ui.BTN_PROMPT, ui.BTN_SHUTDOWN,
        ui.BTN_TABS, ui.BTN_MOUSE, ui.BTN_GOOGLE,
        "  📸 اسکرین‌شات  ",
        "📸اسکرین‌شات",
        "اسکرین‌شات",
        "📸 اسکرين‌شات",          # ی عربی
        "📸عکس از صفحه",
        "📷 دوربین",
        "دوربین",
        "عکس دوربین",
        "webcam",
        "⏻ خاموش کردن لپ‌تاپ",
        "تب مرورگر",
        "مرورگر",
    ]
    missed = [t for t in positives if not ui.match_button(t)]
    check("همه‌ی شکل‌های دکمه شناخته شدند", not missed, f"نشناخته: {missed}")

    negatives = [
        "یک متن خام درباره برنامه ها",
        "برنامه ها را در پایتون بنویس",
        "یک تابع پایتون بنویس که فایل‌ها را پاک کند",
        "لپ تاپ را خاموش نکن",
        "یک اسکرین‌شات از صفحه بگیر و توضیح بده",
        "hello world",
        "from datetime import datetime",
    ]
    wrong = [(t, ui.match_button(t)) for t in negatives if ui.match_button(t)]
    check("درخواست واقعی با دکمه اشتباه نشود", not wrong, f"تطبیق کاذب: {wrong}")

    check(
        "همه‌ی دکمه‌ها به‌عنوان دکمه شناخته می‌شوند",
        all(ui.looks_like_button(b) for b in ui.MENU_BUTTONS),
    )
    check(
        "درخواست عادی دکته نیست",
        not ui.looks_like_button("یک درخواست جدید برایت دارم"),
    )


async def test_camera_selection():
    """
    سیستم انتخاب دوربین: شناسایی، ذخیره در settings.json، و استفاده از
    ایندکس ذخیره‌شده توسط /camera.

    دوربین واقعی تست نمی‌شود؛ فقط منطق ذخیره/بازخوانی و UI شبیه‌سازی می‌شود.
    """
    import json
    import os as _os

    from core import imaging, ui

    # ذخیره در فایل موقت تا settings.json اصلی خراب نشود
    old_file = config.SETTINGS_FILE
    old_cache = imaging._camera_settings_cache
    tmp_file = os.path.join(config.DATA_DIR, "_test_camera_settings.json")
    config.SETTINGS_FILE = tmp_file
    imaging._camera_settings_cache = None
    try:
        if _os.path.exists(tmp_file):
            _os.remove(tmp_file)

        # پیش‌فرض: ایندکس 0
        check("ایندکس پیش‌فرض دوربین 0 است", imaging.saved_camera_index() == 0)

        # ذخیره‌ی ایندکس دلخواه
        imaging.set_camera_index(2)
        check("ایندکس 2 ذخیره شد", imaging.saved_camera_index() == 2)

        # بعد از پاک کردن کش، از فایل خوانده شود
        imaging._camera_settings_cache = None
        check("ایندکس بعد از reload هم 2 است", imaging.saved_camera_index() == 2)

        # فایل JSON باید معتبر باشد و کلید camera_index داشته باشد
        with open(tmp_file, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        check("فایل settings کلید camera_index دارد", saved.get("camera_index") == 2, str(saved))

        # کیبورد انتخاب دوربین
        cams = [
            {"index": 0, "width": 640, "height": 480},
            {"index": 1, "width": 1280, "height": 720},
        ]
        kb = ui.camera_select_keyboard(cams, current_index=2)
        datas = [b.callback_data for row in kb.inline_keyboard for b in row]
        check("دکمه‌ی انتخاب دوربین 0 در کیبورد هست", "camera_set:0" in datas)
        check("دکمه‌ی انتخاب دوربین 1 در کیبورد هست", "camera_set:1" in datas)
        check("دکمه‌ی شناسایی دوباره در کیبورد هست", "camera_detect" in datas)
        check("دکمه‌ی بازگشت در کیبورد هست", "camera_back" in datas)

        # کیبورد زیر عکس دوربین
        kb2 = ui.camera_photo_keyboard()
        datas2 = [b.callback_data for row in kb2.inline_keyboard for b in row]
        check("دکمه‌ی تنظیم دوربین زیر عکس هست", "camera_detect" in datas2)

        # callback ذخیره از طریق main
        import main

        old_set = imaging.set_camera_index
        saved_calls = []
        imaging.set_camera_index = lambda i: saved_calls.append(i)

        u = make_callback("camera_set:5")
        ctx = make_context()
        await main.on_callback(u, ctx)
        check("callback camera_set ایندکس را ذخیره کرد", saved_calls == [5], str(saved_calls))
        check("پیام تأیید ذخیره فرستاده شد",
              any("ذخیره شد" in str(c) for c in u.callback_query.edit_message_text.call_args_list))

        imaging.set_camera_index = old_set
    finally:
        config.SETTINGS_FILE = old_file
        imaging._camera_settings_cache = old_cache
        if _os.path.exists(tmp_file):
            _os.remove(tmp_file)


async def test_app_launch_no_not_modified():
    """
    باز کردن برنامه نباید با خطای «Message is not modified» بشکند.

    ریشه‌ی باگ: پیام وضعیت با همان متنی ساخته می‌شد که بلافاصله ویرایش
    می‌شد، و تلگرام این را خطا می‌داند.
    """
    import main

    created = []
    edited = []

    class FakeMessage:
        def __init__(self):
            self.texts = []

        async def reply_text(self, text, **kwargs):
            created.append(text)
            return self

        async def edit_text(self, text, **kwargs):
            edited.append(text)
            return self

        async def delete(self):
            return True

    launched = []
    old_launch = main.windows.launch
    old_wait = main.windows.wait_for_new_window
    old_resp = main.windows.wait_until_responsive
    old_focus = main.windows.focus
    old_find = main.windows.find_window
    old_grab = main.imaging.grab_screen_async
    old_shot = main.imaging.send_screenshot_to

    main.windows.launch = lambda app_id: launched.append(app_id)
    # این سه تا داخل asyncio.to_thread صدا زده می‌شوند، پس باید تابع
    # همگام باشند نه کوروتین‌ساز
    main.windows.wait_for_new_window = lambda *a, **k: {"hwnd": 1, "title": "Notepad"}
    main.windows.wait_until_responsive = lambda *a, **k: 1
    main.windows.focus = lambda *a, **k: True
    # جست‌وجوی پنجره‌ی «از قبل باز» نباید به پنجره‌ی واقعی سیستم بخورد
    main.windows.find_window = lambda *a, **k: None
    # این دو await می‌شوند، پس باید کوروتین باشند
    main.imaging.grab_screen_async = _async_value(MagicMock(size=(10, 10)))
    main.imaging.send_screenshot_to = _async_value(None)
    try:
        ctx = make_context()
        ctx.user_data["session"] = {}
        u = make_update(111111111)
        u.effective_message = FakeMessage()

        await main._launch_app(u, ctx, "np.exe", "Notepad")

        check("برنامه اجرا شد", launched == ["np.exe"], str(launched))
        check("پیام وضعیت ساخته شد", len(created) == 1, str(created))
        check(
            "پیام اولیه دوباره ویرایش نشد (ریشه‌ی باگ)",
            edited == [] or created[0] != edited[0],
            f"اولیه={created[:1]} ویرایش={edited[:1]}",
        )
    finally:
        main.windows.launch = old_launch
        main.windows.wait_for_new_window = old_wait
        main.windows.wait_until_responsive = old_resp
        main.windows.focus = old_focus
        main.windows.find_window = old_find
        main.imaging.grab_screen_async = old_grab
        main.imaging.send_screenshot_to = old_shot


def _async_value(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


async def test_app_open_already_running():
    """
    اگر برنامه از قبل باز باشد، نباید منتظر پنجره‌ی جدید ماند.

    ریشه‌ی باگ: برنامه‌های تک‌INSTANCE (مثل OpenCode که Electron است) با
    اجرا دوباره پنجره‌ی جدید نمی‌سازند؛ کاربر ۴۵ ثانیه بعد خطای «پنجره‌ی
    جدیدی ظاهر نشد» می‌دید و برنامه باز نمی‌شد.
    """
    import main

    launched = []
    old_find = main.windows.find_window
    old_focus = main.windows.focus
    old_launch = main.windows.launch
    old_grab = main.imaging.grab_screen_async
    old_send = main.imaging.send_screenshot_to

    main.windows.find_window = lambda keywords: 12345
    main.windows.focus = lambda hwnd: True
    main.windows.launch = lambda app_id: launched.append(app_id)
    main.imaging.grab_screen_async = _async_value(MagicMock(size=(10, 10)))
    main.imaging.send_screenshot_to = _async_value(None)
    try:
        ctx = make_context()
        await main._open_and_wait(ctx.bot, 1, "Notepad", "np.exe")

        check("برنامه‌ی باز دوباره اجرا نمی‌شود", launched == [], str(launched))
        texts = " ".join(
            str(c.args[1])
            for c in ctx.bot.send_message.call_args_list
            if len(c.args) > 1
        )
        check(
            "پیام «از قبل باز بود» فرستاده می‌شود",
            "از قبل باز بود" in texts,
            texts[:200],
        )
    finally:
        main.windows.find_window = old_find
        main.windows.focus = old_focus
        main.windows.launch = old_launch
        main.imaging.grab_screen_async = old_grab
        main.imaging.send_screenshot_to = old_send


async def test_edit_helper_tolerates_not_modified():
    """ویرایش با متن یکسان نباید استثنا بدهد."""
    import telegram

    import main

    class FakeMessage:
        def __init__(self, exc=None):
            self.exc = exc
            self.calls = 0

        async def edit_text(self, text, **kwargs):
            self.calls += 1
            if self.exc:
                raise self.exc

    same = FakeMessage(
        telegram.error.BadRequest("Message is not modified: specified new message "
                                  "content and reply markup are exactly the same")
    )
    ok = await main._edit(same, "متن")
    check("خطای «not modified» بی‌صدا رد می‌شود", ok is False)

    other = FakeMessage(telegram.error.BadRequest("chat not found"))
    raised = False
    try:
        await main._edit(other, "متن")
    except telegram.error.BadRequest:
        raised = True
    check("خطاهای دیگر واقعی پنهان نمی‌شوند", raised)


async def test_api_key_flow():
    """مسیر وارد کردن کلید: ذخیره در secrets.json و استفاده در درخواست."""
    import main
    from core import ai, secrets

    old_build = ai.build_prompt
    old_test = ai.test_connection
    old_path_fn = secrets._path
    old_providers = list(config.AI_PROVIDERS)
    secrets.reset_cache()
    try:
        secrets._path = lambda: os.path.join(config.DATA_DIR, "_test_secrets.json")
        secrets.set_key("TestProvider", "sk-test-1234")
        check("کلید ذخیره شد", secrets.get_key("TestProvider") == "sk-test-1234")
        check("کلید پوشانده می‌شود", secrets.masked("sk-test-1234").endswith("1234"))
        check("کلید کوتاه هم پوشانده می‌شود", secrets.masked("abcd") == "****")
        check("has_key درست است", secrets.has_key("TestProvider") is True)
        secrets.clear_key("TestProvider")
        check("کلید پاک شد", secrets.get_key("TestProvider") == "")

        # وارد کردن کلید از داخل تلگرام
        config.AI_PROVIDERS.append({
            "name": "TestProvider",
            "base_url": "https://example.invalid/v1",
            "model": "test-model",
            "api_key_env": "",
            "auth": "bearer",
            "timeout": 5,
        })
        ai.reset_cache()
        ai.build_prompt = AsyncMock(return_value=(True, "پرامپت", "TestProvider"))
        ai.test_connection = AsyncMock(return_value=(True, "ok"))

        ctx = make_context()
        ctx.user_data["session"] = {}
        u = make_update(111111111, text="sk-test-1234")
        await main._save_api_key(u, ctx, "TestProvider", "sk-test-1234")
        check("کلید از داخل چت ذخیره شد", secrets.get_key("TestProvider") == "sk-test-1234")
        check("اتصال تست شد", ai.test_connection.called)
        check("پیام حاوی کلید پاک شد", u.effective_message.delete.called)
        check(
            "بدون درخواست معلق، ساخت پرامپت اجرا نمی‌شود",
            not ai.build_prompt.called,
        )

        # اگر کاربر قبلاً درخواست ساخت پرامپت داده بود، بعد از ثبت کلید ادامه می‌دهیم
        ai.build_prompt.reset_mock()
        ctx_b = make_context()
        ctx_b.user_data["session"] = {"pending_raw": "یک درخواست معلق"}
        u_b = make_update(111111111, text="sk-test-1234")
        await main._save_api_key(u_b, ctx_b, "TestProvider", "sk-test-1234")
        check(
            "درخواست معلق بعد از ثبت کلید ادامه پیدا می‌کند",
            ai.build_prompt.called,
        )
        check("درخواست معلق از صفحه خارج شد",
              "pending_raw" not in ctx_b.user_data["session"],
              str(ctx_b.user_data["session"]))

        # سرویس بدون کلید نباید بی‌دلیل همه‌ی سرویس‌ها را امتحان کند
        ctx2 = make_context()
        ctx2.user_data["session"] = {}
        sent = []
        u2 = make_update(111111111, text="یک متن خام")

        async def fake_build(raw):
            sent.append(raw)
            return True, "x", "y"

        ai.build_prompt = fake_build
        secrets.clear_key("TestProvider")
        ai.set_provider("TestProvider")
        try:
            await main._generate_prompt(u2, ctx2, "یک متن خام")
            check("وقتی کلید نیست، مستقیم به AI فرستاده نمی‌شود", not sent, str(sent))
            check("کاربر به وارد کردن کلید هدایت شد",
                  ctx2.user_data["session"]["mode"] == "api_key",
                  str(ctx2.user_data["session"]))
        finally:
            secrets.set_key("TestProvider", "sk-test-1234")
    finally:
        config.AI_PROVIDERS[:] = old_providers
        secrets._path = old_path_fn
        secrets.reset_cache()
        ai.reset_cache()
        ai.build_prompt = old_build
        ai.test_connection = old_test
        p = os.path.join(config.DATA_DIR, "_test_secrets.json")
        if os.path.exists(p):
            os.remove(p)


@contextlib.contextmanager
def temp_key(provider_name: str, key: str):
    """برای تست‌ها: یک کلید موقت ثبت می‌کند و بعد پاکش می‌کند."""
    from core import secrets

    old_path_fn = secrets._path
    old_providers = list(config.AI_PROVIDERS)
    secrets.reset_cache()
    secrets._path = lambda: os.path.join(config.DATA_DIR, "_test_secrets_tmp.json")
    try:
        if not any(p["name"] == provider_name for p in config.AI_PROVIDERS):
            config.AI_PROVIDERS.append({
                "name": provider_name,
                "base_url": "https://example.invalid/v1",
                "model": "test-model",
                "api_key_env": "",
                "auth": "bearer",
                "timeout": 5,
            })
        secrets.reset_cache()
        secrets.set_key(provider_name, key)
        yield
    finally:
        config.AI_PROVIDERS[:] = old_providers
        secrets._path = old_path_fn
        secrets.reset_cache()
        ai.reset_cache()
        p = os.path.join(config.DATA_DIR, "_test_secrets_tmp.json")
        if os.path.exists(p):
            os.remove(p)


async def test_tabs_command_and_send_flow():
    """/tabopencode تب‌های خود OpenCode را نشان می‌دهد و /send پرامپت را می‌گیرد."""
    import main
    from core import imaging

    old_prepare = main._prepare_opencode
    old_strip = main._send_tab_strip
    old_run = main._run_opencode
    old_grab = imaging.grab_screen_async
    old_send_shot = main._shot

    fake_result = {
        "ok": True,
        "reason": "",
        "tabs": [
            {"index": 1, "x": 135, "y": 16, "active": True},
            {"index": 2, "x": 325, "y": 16, "active": False},
            {"index": 3, "x": 515, "y": 16, "active": False},
        ],
        "new_tab": {"x": 895, "y": 16},
        "strip": (-9, 1, 1591, 37),
    }

    # باید همگام نباشد چون داخل to_thread صدا زده می‌شود
    def sync_prepare():
        return fake_result, 4242

    async def fake_strip(bot, chat_id, screen, result):
        return None

    sent = []

    async def fake_run(update, context, prompt):
        sent.append(prompt)

    from PIL import Image

    main._prepare_opencode = sync_prepare
    main._send_tab_strip = fake_strip
    main._run_opencode = fake_run
    imaging.grab_screen_async = _async_value(
        Image.new("RGB", (300, 200), (20, 20, 20))
    )
    main._shot = _async_value(True)
    try:
        ctx = make_context()
        ctx.user_data["session"] = {}
        u = make_update(111111111)
        await main.cmd_tabs(u, ctx)
        check("تب‌ها در نشست ذخیره شدند",
              len(ctx.user_data["session"].get("tabs", [])) == 3,
              str(ctx.user_data["session"].get("tabs")))
        check("handle پنجره ذخیره شد",
              ctx.user_data["session"].get("tabs_hwnd") == 4242)

        # /send بدون متن و بدون پرامپت ذخیره‌شده => از کاربر می‌پرسد
        from core import ai

        ai.save_prompt("")
        ctx2 = make_context()
        ctx2.user_data["session"] = {}
        ctx2.args = []
        u2 = make_update(111111111)
        await main.cmd_send(u2, ctx2)
        check("/send از کاربر پرامپت می‌گیرد",
              ctx2.user_data["session"].get("mode") == "send_prompt",
              str(ctx2.user_data["session"]))

        # متن بعدی مستقیم به OpenCode می‌رود
        ctx2.user_data["session"]["mode"] = "send_prompt"
        u3 = make_update(111111111, text="یک تابع بنویس")
        await main.on_text(u3, ctx2)
        check("پرامپت کاربر به OpenCode رفت", sent == ["یک تابع بنویس"], str(sent))
        check("حالت پاک شد", ctx2.user_data["session"].get("mode", "") == "")

        # /send با متن مستقیم
        ctx3 = make_context()
        ctx3.user_data["session"] = {}
        ctx3.args = ["متن", "مستقیم"]
        u4 = make_update(111111111)
        await main.cmd_send(u4, ctx3)
        check("/send با متن مستقیم کار می‌کند",
              sent[-1] == "متن مستقیم", str(sent))
    finally:
        main._prepare_opencode = old_prepare
        main._send_tab_strip = old_strip
        main._run_opencode = old_run
        imaging.grab_screen_async = old_grab
        main._shot = old_send_shot


async def test_tabs_window_missing_message():
    """اگر پنجره‌ی OpenCode نبود، پیام راهنمای روشن داده شود."""
    import main

    old_prepare = main._prepare_opencode

    def sync_prepare():
        return None, None

    main._prepare_opencode = sync_prepare
    try:
        ctx = make_context()
        ctx.user_data["session"] = {}
        u = make_update(111111111)
        await main.cmd_tabs(u, ctx)
        # پیام نهایی با ویرایش همان پیام فرستاده می‌شود
        edited = " ".join(str(c.args[0]) for c in u.effective_message.edit_text.call_args_list)
        check("پیام «پیدا نشد» داده می‌شود", "پیدا نشد" in edited, edited[:90])
        check("راهنمای باز کردن برنامه داده می‌شود",
              "باز کنید" in edited, edited[:130])
    finally:
        main._prepare_opencode = old_prepare


async def test_status_command_runs():
    """/status باید بدون خطا و بدون Markdown جواب بدهد."""
    import main
    from core import power

    old_pending = power.is_shutdown_pending
    power.is_shutdown_pending = _async_value(False)
    try:
        ctx = make_context()
        ctx.user_data["all_apps"] = [{"name": "A", "app_id": "a"}]
        u = make_update(111111111)
        await main.cmd_status(u, ctx)
        kwargs = u.effective_message.reply_text.call_args.kwargs
        text = u.effective_message.reply_text.call_args.args[0]
        check("/status پیام فرستاد", bool(text))
        check("/status از Markdown استفاده نمی‌کند",
              "parse_mode" not in kwargs, str(kwargs.keys()))
        check("/status اطلاعات کلید را نشان می‌دهد", "کلید" in text, text[:120])
    finally:
        power.is_shutdown_pending = old_pending


def _synthetic_opencode_ui(
    n_tabs: int = 6, win_rect=(-9, -9, 1929, 1089)
):
    """
    تصویر ساختگی با اندازه‌ی واقعی پنجره‌ی maximize.

    هدف این است که آشکارساز بدون دست زدن به صفحه‌ی واقعی آزمایش شود،
    آن هم با همان نسبت‌هایی که در کد استفاده می‌شود: آیکن‌های ۲۰ پیکسلی
    با فاصله‌ی ۱۹۰، دکمه‌ی باریک تب جدید، دکمه‌ی پنل و کنترل‌های پنجره.
    """
    from PIL import Image, ImageDraw

    left, top, right, bottom = win_rect
    width, height = right - left, bottom - top

    img = Image.new("RGB", (width, height), (10, 10, 12))
    draw = ImageDraw.Draw(img)

    y0 = top + int(height * 0.010)
    y1 = top + int(height * 0.042)
    cy = (y0 + y1) // 2

    def sx(x):
        """نسبت افقی به مختصات تصویر."""
        return left + int(width * x)

    # دو آیکن خود برنامه، قبل از تب‌ها
    for r in (0.015, 0.045):
        cx = sx(r)
        draw.rectangle((cx - 8, cy - 8, cx + 8, cy + 8), fill=(200, 200, 200))

    icons = []
    r = 0.070
    for _ in range(n_tabs):
        x = sx(r)
        draw.rectangle((x, cy - 10, x + 19, cy + 9), fill=(210, 210, 210))
        icons.append(x + 10)
        # متن تب: چند لکه‌ی باریک که نباید شبیه آیکن شود
        for k in range(4):
            bx = x + 30 + k * 12
            draw.rectangle((bx, cy - 5, bx + 6, cy + 5), fill=(150, 150, 150))
        r += 190 / width

    # دکمه‌ی تب جدید: باریک‌تر از آیکن‌ها (۱۴ پیکسل)
    plus_x = sx(r + 0.010)
    draw.rectangle((plus_x, cy - 7, plus_x + 13, cy + 7), fill=(120, 120, 120))
    draw.rectangle((plus_x + 6, cy - 7, plus_x + 7, cy + 7), fill=(200, 200, 200))
    plus_center = plus_x + 7

    # دکمه‌ی پنل (عرض ۱۶، بین تب جدید و کنترل‌های پنجره)
    panel_x = sx(0.887)
    draw.rectangle((panel_x, cy - 8, panel_x + 15, cy + 8), fill=(110, 110, 110))

    # کنترل‌های پنجره، سمت راست
    for k in range(3):
        cx = sx(0.92 + k * 0.03)
        draw.rectangle((cx, cy - 8, cx + 13, cy + 8), fill=(255, 255, 255))

    return img, icons, plus_center


async def test_detect_tabs_on_synthetic_strip():
    """آشکارساز تب باید روی نوار ساختگی هم درست کار کند."""
    from core import opencode_ui

    win_rect = (-9, -9, 1929, 1089)
    img, icons, plus_center = _synthetic_opencode_ui(n_tabs=6, win_rect=win_rect)

    res = opencode_ui.detect_tabs(img, win_rect)

    check("نوار تب تشخیص داده شد", res["ok"], res.get("reason", ""))
    got = [t["x"] for t in res["tabs"]]
    check(
        f"تعداد تب درست است ({len(got)} از ۶)",
        len(got) == 6,
        f"پیدا شد: {got}",
    )
    check(
        "مختصات تب‌ها درست است",
        len(got) == 6 and all(abs(a - b) <= 4 for a, b in zip(icons, got)),
        f"انتظار {icons} گرفته شد {got}",
    )
    check("دکمه‌ی تب جدید پیدا شد", res["new_tab"] is not None)
    if res["new_tab"]:
        check(
            "مختصات تب جدید درست است",
            abs(res["new_tab"]["x"] - plus_center) <= 5,
            f"انتظار {plus_center} گرفته شد {res['new_tab']['x']}",
        )
    check("شماره‌گذاری تب‌ها پیوسته است",
          [t["index"] for t in res["tabs"]] == list(range(1, len(got) + 1)))


async def test_detect_tabs_ignores_spurious_clusters():
    """یک تکه‌ی متن شبیه آیکن نباید تب اضافه بسازد."""
    from core import opencode_ui

    win_rect = (-9, -9, 1929, 1089)
    img, icons, _plus = _synthetic_opencode_ui(n_tabs=5, win_rect=win_rect)

    # یک مستطیل ۲۲ پیکسلی وسط فاصله‌ی دو تب: شبیه آیکن ولی اضافه
    from PIL import ImageDraw

    draw = ImageDraw.Draw(img)
    left, top, right, bottom = win_rect
    height = bottom - top
    cy = top + int(height * 0.0235)
    mid_x = (icons[2] + icons[3]) // 2
    draw.rectangle((mid_x - 11, cy - 8, mid_x + 11, cy + 8), fill=(205, 205, 205))

    res = opencode_ui.detect_tabs(img, win_rect)
    check(
        "تکه‌ی اضافی باعث ساخت تب کاذب نشد",
        res["ok"] and len(res["tabs"]) == 5,
        f"{len(res['tabs'])} تب پیدا شد: {[t['x'] for t in res['tabs']]}",
    )


async def test_annotate_strip_draws_on_canvas():
    """نشان شماره‌ها باید داخل تصویر بیفتد (نه بیرون از آن)."""
    from core import opencode_ui

    win_rect = (-9, -9, 1929, 1089)
    img, _icons, _plus = _synthetic_opencode_ui(n_tabs=4, win_rect=win_rect)

    res = opencode_ui.detect_tabs(img, win_rect)
    out = opencode_ui.annotate_strip(img, res)
    check("تصویر نوار ساخته شد", out is not None)
    if out is None:
        return
    arr = out.convert("RGB")
    red_pixels = sum(
        1 for px in arr.getdata() if px[0] > 150 and px[1] < 90 and px[2] < 90
    )
    check(
        "نشان شماره‌ها داخل تصویر رسم شد",
        red_pixels > 200,
        f"{red_pixels} پیکسل قرمز",
    )


async def test_paste_escalation_verifies_result():
    """
    چسباندن باید واقعاً اندازه‌گیری شود و در صورت شکست روش بعدی را بزند.

    ریشه‌ی باگ قبلی: کلیک روی نقطه‌ای که بیرون از کادر ورودی بود و هیچ
    بررسی‌ای هم نمی‌شد، پس متن هرگز وارد کادر نمی‌شد.
    """
    from core import opencode

    clicks = []
    combos = []

    class FakePyautogui:
        @staticmethod
        def click(x, y):
            clicks.append((x, y))

        @staticmethod
        def hotkey(*keys):
            combos.append(keys)

    old_pg = opencode.pyautogui
    old_clip = opencode.pyperclip
    old_points = opencode._input_click_points
    old_region = opencode._input_region
    old_pause = opencode._pause
    old_grab = opencode.imaging.grab_screen

    seq = []

    # تعداد کل تلاش‌ها: یکی به‌ازای هر نقطه با Ctrl+V، به‌علاوه‌ی یکی
    # با Ctrl+Shift+V در انتها.
    points = [(10, 10), (20, 20), (30, 30)]
    total_attempts = len(points) + 1

    def fake_snapshot():
        from PIL import Image

        # هر تلاش دو عکس می‌گیرد؛ فقط در آخرین تلاش تغییر دیده می‌شود
        seq.append(1)
        img = Image.new("L", (50, 50), 0)
        if len(seq) >= (total_attempts - 1) * 2 + 2:
            img.paste(255, (0, 0, 50, 50))
        return img
    opencode.pyautogui = FakePyautogui
    opencode.pyperclip = MagicMock()
    opencode.pyperclip.copy = lambda t: None
    opencode.pyperclip.paste = lambda: "x"
    opencode._input_click_points = lambda hwnd: points
    opencode._input_region = lambda hwnd: (0.0, 0.0, 1.0, 1.0)
    opencode._pause = lambda *a: None
    opencode.imaging.grab_screen = fake_snapshot
    try:
        result = opencode._paste_prompt(1, "متن")
        check("در نهایت موفق شد", bool(result), f"نتیجه: {result!r}")
        check(
            "همه‌ی نقاط امتحان شدند",
            len(clicks) == total_attempts,
            f"{len(clicks)} کلیک از {total_attempts} تلاش",
        )
        check(
            "آخرین روش، Ctrl+Shift+V بود",
            combos[-1] == ("ctrl", "shift", "v"),
            f"آخرین: {combos[-1]} | همه: {combos}",
        )
    finally:
        opencode.pyautogui = old_pg
        opencode.pyperclip = old_clip
        opencode._input_click_points = old_points
        opencode._input_region = old_region
        opencode.imaging.grab_screen = old_grab

    # حالتی که هیچ روشی جواب نمی‌دهد => باید رشته‌ی خالی برگرداند
    clicks.clear()
    combos.clear()

    def flat_snapshot():
        from PIL import Image

        return Image.new("L", (50, 50), 0)

    opencode.pyautogui = FakePyautogui
    opencode.pyperclip = MagicMock()
    opencode.pyperclip.copy = lambda t: None
    opencode.pyperclip.paste = lambda: "x"
    opencode._input_click_points = lambda hwnd: [(10, 10), (20, 20)]
    opencode._input_region = lambda hwnd: (0.0, 0.0, 1.0, 1.0)
    opencode._pause = lambda *a: None
    opencode.imaging.grab_screen = flat_snapshot
    try:
        result = opencode._paste_prompt(1, "متن")
        check("اگر هیچ روشی جواب ندهد، خالی برمی‌گردد", result == "", repr(result))
        check("همه‌ی نقاط امتحان شدند", len(clicks) == 3, f"{len(clicks)} کلیک")
    finally:
        opencode.pyautogui = old_pg
        opencode.pyperclip = old_clip
        opencode._input_click_points = old_points
        opencode._input_region = old_region
        opencode._pause = old_pause
        opencode.imaging.grab_screen = old_grab


async def test_input_region_lands_inside_textarea():
    """
    ناحیه‌ی ورودی باید داخل کادر واقعی باشد و «کل پنجره» نباشد.

    ریشه‌ی باگ قبلی: _input_region کل پنجره را برمی‌گرداند، که برای پنجره‌ی
    maximize یعنی تمام صفحه (۱.۹ میلیون پیکسل). آن‌وقت تغییر چند کلمه متن
    (~۷۰۰ پیکسل) زیر آستانه می‌ماند و چسباندن همیشه «ناموفق» گزارش می‌شد،
    در حالی که متن واقعاً داخل کادر بود.
    """
    import config
    from core import opencode, windows

    # محدوده‌ی واقعی کادر ورودی که از تصویر بررسی شد (حالت maximize پنجره)
    real = (360, 760, 1560, 1005)

    # باید پنجره‌ی خود OpenCode سنجیده شود، نه هر پنجره‌ای با کوچک‌ترین
    # handle (بازشدن/بستن هر برنامه‌ای handle ها را جابه‌جا می‌کند).
    # اندازه‌گیری هم روی حالت maximize انجام می‌شود، چون «کادر واقعی» از
    # همین حالت اندازه‌گیری شده و بات هم قبل از تایپ پنجره را maximize می‌کند.
    hwnd = windows.find_window(config.OPENCODE_WINDOW_TITLES)
    if hwnd is None:
        check("پنجره‌ی OpenCode برای اندازه‌گیری هست", False, "OpenCode باز نیست")
        return

    windows.maximize(hwnd)
    await asyncio.sleep(0.4)
    rect = windows.window_rect(hwnd)
    if rect is None:
        check("مختصات پنجره هست", False)
        return

    left, top, right, bottom = rect
    w, h = right - left, bottom - top

    l, t, r, b = config.OPENCODE_INPUT_REGION
    box = (
        left + int(w * l), top + int(h * t),
        left + int(w * r), top + int(h * b),
    )
    check(
        "ناحیه‌ی ورودی داخل کادر واقعی است",
        real[0] - 8 <= box[0] and real[1] - 8 <= box[1]
        and box[2] <= real[2] + 8 and box[3] <= real[3] + 8,
        f"ناحیه = {box} | واقعی = {real}",
    )
    check(
        "ناحیه کل پنجره نیست",
        (box[2] - box[0]) < (right - left) * 0.9,
        f"عرض ناحیه = {box[2] - box[0]} از {right - left}",
    )

    # نقطه‌های کلیک: باید داخل پنجره باشند؛ اولویت با نقاط داخل کادر پیکربندی‌شده است.
    points = opencode._input_click_points(hwnd)
    if rect and points:
        lft, tp, rgt, btm = rect
        in_window = all(lft <= p[0] <= rgt and tp <= p[1] <= btm for p in points)
    else:
        in_window = False
    check("همه‌ی نقاط کلیک داخل پنجره هستند", in_window, str(points)[:120])

    inside_points = [p for p in points if opencode._inside_box(p, box)]
    check("حداقل یک نقطه داخل کادر ورودی وجود دارد",
          len(inside_points) >= 1, f"داخل کادر: {len(inside_points)} از {len(points)}")
    # اگر نقطه‌ای بیرون از کادر آمده (مثلاً UIA به‌عنوان تلاش آخر)، باید بعد از
    # همه‌ی نقاط داخل کادر باشد تا اولویت با ناحیه‌ی پیکربندی‌شده بماند.
    if len(inside_points) < len(points):
        first_out = next(i for i, p in enumerate(points) if not opencode._inside_box(p, box))
        check("نقطه‌ی بیرون از کادر فقط به‌عنوان تلاش آخر می‌آید",
              first_out >= len(inside_points), str(points)[:120])
    check("چند نقطه برای تلاش مجدد وجود دارد",
          len(points) >= 3, f"{len(points)} نقطه")

    # نقطه‌ی UI Automation: اگر بیرون از کادر ولی داخل پنجره باشد، به‌عنوان
    # تلاش آخر پذیرفته می‌شود (برای وقتی رابط عوض شده)؛ نباید حذف شود.
    bad = opencode._uia_input_point(hwnd)
    if bad:
        if opencode._inside_box(bad, box):
            check("نقطه‌ی UI Automation داخل کادر در فهرست است",
                  bad in points, f"UIA={bad}")
        elif rect and (rect[0] <= bad[0] <= rect[2] and rect[1] <= bad[1] <= rect[3]):
            check("نقطه‌ی UI Automation بیرون از کادر به‌عنوان تلاش آخر نگه داشته می‌شود",
                  bad in points and points[-1] == bad, f"UIA={bad} points={points}")
        else:
            check("نقطه‌ی UI Automation بیرون از پنجره فیلتر می‌شود",
                  bad not in points, f"UIA={bad}")


async def test_changed_pixels_is_scale_independent():
    """
    معیار تشخیص باید «تعداد پیکسل» باشد نه «نسبت».

    نسبت به اندازه‌ی ناحیه وابسته است: چند کلمه متن در ناحیه‌ی بزرگ زیر
    آستانه می‌ماند و نتیجه می‌شود چیزی تغییر نکرده.
    """
    from core import imaging
    from PIL import Image, ImageDraw

    # شبیه‌سازی یک کلمه: چند مستطیل روشن (قابل پیش‌بینی، برخلاف فونت پیش‌فرض)
    small = Image.new("L", (200, 100), 0)
    d = ImageDraw.Draw(small)
    for i in range(5):
        d.rectangle((10 + i * 18, 10, 22 + i * 18, 24), fill=255)
    blank = Image.new("L", (200, 100), 0)

    ratio = imaging.region_diff_ratio(small, blank)
    count = imaging.changed_pixels(small, blank)

    check("نسبت برای چند کلمه کوچک است", 0 < ratio < 0.2, f"ratio={ratio:.4f}")
    check("تعداد پیکسل محسوس است", count > 50, f"count={count}")
    check(
        "تعداد پیکسل از آستانه‌ی تشخیص رد می‌شود",
        count >= config_min_pixels(),
        f"{count} >= {config_min_pixels()}",
    )
    check("تصویر یکسان تغییری ندارد",
          imaging.changed_pixels(small, small) == 0)
    check("اندازه‌های متفاوت هم کار می‌کند",
          imaging.changed_pixels(small, Image.new("L", (400, 200), 0)) > 0)

    # نقطه‌ی اصلی: همان متن در ناحیه‌ی بزرگ‌تر، نسبت را زیر آستانه می‌برد
    # ولی تعداد پیکسل همچنان محسوس است. برای همین معیارِ نسبت غلط بود.
    big_blank = Image.new("L", (1200, 700), 0)
    big = big_blank.copy()
    d2 = ImageDraw.Draw(big)
    for i in range(5):
        d2.rectangle((10 + i * 18, 10, 22 + i * 18, 24), fill=255)
    big_ratio = imaging.region_diff_ratio(big, big_blank)
    big_count = imaging.changed_pixels(big, big_blank)
    check(
        "نسبت در ناحیه‌ی بزرگ بی‌فایده می‌شود",
        big_ratio < 0.004,
        f"ratio={big_ratio:.5f} زیر آستانه‌ی نسبتی",
    )
    check(
        "ولی تعداد پیکسل هنوز معنادار است",
        big_count >= config_min_pixels(),
        f"count={big_count}",
    )


def config_min_pixels() -> int:
    import config

    return config.OPENCODE_PASTE_MIN_PIXELS


async def test_telegram_timeouts_are_generous():
    """
    timeout پیش‌فرض ۵ ثانیه باعث خطای «Timed out» هنگام ارسال فایل می‌شد.
    """
    import inspect

    import config
    from telegram.request import HTTPXRequest

    default_read = inspect.signature(HTTPXRequest.__init__).parameters[
        "read_timeout"
    ].default
    check(
        "پیش‌فرض کتابخانه واقعاً کم است (تأیید علت باگ)",
        default_read <= 10,
        f"پیش‌فرض = {default_read}",
    )
    check(
        "timeout خواندن ما از پیش‌فرض بیشتر است",
        config.TELEGRAM_READ_TIMEOUT >= 60,
        f"{config.TELEGRAM_READ_TIMEOUT}",
    )
    check(
        "timeout نوشتن ما از پیش‌فرض بیشتر است",
        config.TELEGRAM_WRITE_TIMEOUT >= 60,
        f"{config.TELEGRAM_WRITE_TIMEOUT}",
    )
    check(
        "استخر اتصال برای کارهای هم‌زمان بزرگ‌تر از ۱ است",
        config.TELEGRAM_POOL_SIZE >= 4,
        f"{config.TELEGRAM_POOL_SIZE}",
    )
    # درخواست getUpdates (long-poll) باید از timeout پیش‌فرض ۵ ثانیه بزرگ‌تر
    # باشد وگرنه همیشه قبل از پاسخ ده‌ثانیه‌ای تلگرام «Timed out» می‌گیرد.
    check(
        "timeout خواندن getUpdates از long-poll (۱۰ ثانیه) بیشتر است",
        config.TELEGRAM_POLL_READ_TIMEOUT > 10,
        f"{config.TELEGRAM_POLL_READ_TIMEOUT}",
    )


async def test_request_object_uses_config():
    """ساخت Application باید واقعاً timeout های تنظیم‌شده را بگیرد."""
    import config

    config.BOT_TOKEN = "123:AAfake"
    import logging as _logging

    _logging.disable(_logging.CRITICAL)
    try:
        import main

        req = main._build_request()
        check("شیء request ساخته شد", req is not None)
        check("نام درست کلاس است",
              type(req).__name__ == "HTTPXRequest", type(req).__name__)
        # PTB برای polling یک request جدا می‌سازد؛ اگر build_app آن را
        # تنظیم نکرده باشد، request پیش‌فرض ۵ ثانیه‌ای برمی‌گردد و بات
        # هیچ پیامی نمی‌گیرد.
        poll_req = main._build_poll_request()
        check("شیء request جداگانه‌ی getUpdates ساخته شد", poll_req is not None)
        check(
            "timeout خواندن request polling از پیش‌فرض httpx بیشتر است",
            poll_req.read_timeout > 10,
            f"{poll_req.read_timeout}",
        )
        app = main.build_app()
        # ترتیب tuple داخل Bot: (شیء getUpdates، شیء معمولی) — برعکسِ چیزی که
        # آدم انتظار دارد؛ index صفر همان درخواست polling است.
        check(
            "build_app شیء request جدا برای getUpdates ثبت کرده",
            app.bot._request[0] is not app.bot._request[1],
        )
        check(
            "read_timeout شیء getUpdates مقدار تنظیم‌شده است",
            app.bot._request[0].read_timeout == config.TELEGRAM_POLL_READ_TIMEOUT,
            f"{app.bot._request[0].read_timeout}",
        )
    finally:
        _logging.disable(_logging.NOTSET)


async def test_document_falls_back_when_too_big():
    """PNG خیلی بزرگ باید با JPEG جایگزین شود تا ارسال سریع باشد."""
    from core import imaging
    from PIL import Image

    noisy = Image.new("RGB", (900, 700))
    px = noisy.load()
    for y in range(700):
        for x in range(900):
            px[x, y] = ((x * 7) % 256, (y * 13) % 256, ((x + y) * 3) % 256)
    doc = imaging.encode_document(noisy)
    check("فایل خوانا ساخته شد", len(doc) > 1000, f"{len(doc) // 1024} KB")
    check(
        "از سقف حجم عبور نکرد",
        len(doc) <= config.MAX_DOCUMENT_BYTES + 200_000,
        f"{len(doc)} بایت",
    )


async def test_shot_helper_survives_errors():
    """تابع _shot نباید با خطای شبکه کل کار را از کار بیندازد."""
    import main
    from PIL import Image

    calls = []

    class FakeBot:
        async def send_photo(self, chat_id, photo, caption=None, **kw):
            calls.append("photo")

        async def send_document(self, chat_id, document, filename, caption=None):
            raise RuntimeError("Timed out")

        async def send_message(self, chat_id, text, **kw):
            calls.append(("warn", text))

    old_grab = main.imaging.grab_screen_async
    main.imaging.grab_screen_async = _async_value(Image.new("RGB", (100, 80)))
    try:
        ok = await main._shot(FakeBot(), 1, "test")
        check("_shot خطا را مهار کرد و False برگرداند", ok is False)
        check("عکس فرستاده شد", "photo" in calls, str(calls))
        check("به کاربر هشدار داده شد",
              any(isinstance(c, tuple) for c in calls), str(calls))
    finally:
        main.imaging.grab_screen_async = old_grab


async def test_send_screenshot_document_failure_is_not_fatal():
    """
    خطای ارسال فایل خوانا نباید استثنا رد کند.

    دقیقاً همین مسیر باعث می‌شد کاربر «Timed out» ببیند در حالی که عکس
    درست فرستاده شده بود.
    """
    import telegram

    from core import imaging
    from PIL import Image

    events = []

    class FakeBot:
        async def send_photo(self, chat_id, photo, caption=None, **kw):
            events.append("photo")

        async def send_document(self, chat_id, document, filename, caption=None):
            raise telegram.error.TimedOut("Timed out")

        async def send_message(self, chat_id, text, **kw):
            events.append(("warn", text))

    img = Image.new("RGB", (240, 140), (30, 30, 30))
    ok = await imaging.send_screenshot_to(FakeBot(), 1, img, "تست")

    check("استثنا رد نشد", ok is False)
    check("عکس فشرده فرستاده شد", "photo" in events, str(events))
    check("به کاربر توضیح داده شد",
          any(isinstance(e, tuple) and "file" in str(e[1]).lower()
              or (isinstance(e, tuple) and "فایل" in e[1])
              for e in events),
          str(events))

async def test_open_app_error_not_caused_by_screenshot():
    """
    خطای اسکرین‌شات نباید به «باز کردن برنامه ناموفق بود» تبدیل شود.

    این دقیقاً همان چیزی بود که کاربر می‌دید: برنامه باز می‌شد و اسکرین‌شات
    می‌رفت، بعد پیام خطا می‌آمد.
    """
    import main
    import telegram

    created = []
    _old_send_shot = main.imaging.send_screenshot_to

    class FakeMessage:
        async def reply_text(self, text, **kwargs):
            created.append(text)
            return self

        async def edit_text(self, text, **kwargs):
            created.append(text)
            return self

        async def delete(self):
            return True

    old_launch = main.windows.launch
    old_wait = main.windows.wait_for_new_window
    old_resp = main.windows.wait_until_responsive
    old_focus = main.windows.focus
    old_find = main.windows.find_window
    old_title = main.windows.wait_title_stable
    old_shot = main._shot
    old_bump = main.apps.bump_usage

    main.windows.launch = lambda app_id: None
    main.windows.wait_for_new_window = lambda *a, **k: {"hwnd": 1, "title": "X"}
    main.windows.wait_until_responsive = lambda *a, **k: 1
    main.windows.focus = lambda *a, **k: True
    # جست‌وجوی پنجره‌ی «از قبل باز» نباید به پنجره‌ی واقعی سیستم بخورد
    main.windows.find_window = lambda *a, **k: None
    main.windows.wait_title_stable = lambda *a, **k: "X"
    main.apps.bump_usage = lambda app_id: None

    # خطا را در جای واقعی‌اش می‌گذاریم: تابع ارسال اسکرین‌شات.
    # _shot خودش باید جلوی آن را بگیرد.
    async def failing_send(bot, chat_id, image=None, caption="", as_document=True):
        raise telegram.error.TimedOut("Timed out")

    main.imaging.send_screenshot_to = failing_send
    try:
        ctx = make_context()
        ctx.user_data["session"] = {}
        u = make_update(111111111)
        u.effective_message = FakeMessage()
        await main._launch_app(u, ctx, "np.exe", "Notepad")
        failed = any("ناموفق" in str(c) for c in created)
        check("خطای اسکرین‌شات باعث پیام «ناموفق بود» نشد", not failed, str(created))
        bot_texts = " ".join(
            str(c.args[1]) for c in ctx.bot.send_message.call_args_list
        )
        check("برنامه باز شد و پیام موفق فرستاده شد",
              "پنجره پیدا شد" in bot_texts, bot_texts[:160])
        check("خطا فقط به شکل هشدار گزارش شد",
              "ارسال نشد" in bot_texts, bot_texts[:200])
    finally:
        main.windows.launch = old_launch
        main.windows.wait_for_new_window = old_wait
        main.windows.wait_until_responsive = old_resp
        main.windows.focus = old_focus
        main.windows.find_window = old_find
        main.windows.wait_title_stable = old_title
        main.apps.bump_usage = old_bump
        main._shot = old_shot
        main.imaging.send_screenshot_to = _old_send_shot


async def test_tabs_keyboard_numbers_only():
    """کیبورد تب باید شماره نشان دهد (چون عنوان از بیرون خوانده نمی‌شود)."""
    from core import ui

    tabs = [{"index": i, "active": i == 2} for i in range(1, 8)]
    kb = ui.tabs_keyboard(tabs, has_new_tab=True)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    numbered = {int(l.split(" ")[0]) for l in labels if l.split(" ")[0].isdigit()}
    check("همه‌ی شماره‌های تب وجود دارند",
          numbered == set(range(1, 8)), f"{sorted(numbered)} از {labels}")
    check("تب فعال مشخص است",
          any("فعال" in l for l in labels), str(labels))
    check("دکمه‌ی تب جدید هست", "➕ تب جدید" in labels, str(labels))

    # شماره‌ی هر دکمه باید همان شماره‌ی تب باشد
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    check("callback_data شماره‌ی درست دارد",
          [d for d in data if d.startswith("tab:")] ==
          [f"tab:{i}" for i in range(1, 8)], str(data))


async def test_edit_query_rejects_reply_keyboard():
    """
    کیبورد پایین چت نباید به ویرایش پیام داده شود.

    ریشه‌ی خطای «Inline keyboard expected»: تلگرام با ویرایش پیام فقط
    کیبورد شناور را می‌پذیرد و کیبورد پایین چت را با خطا رد می‌کند.
    """
    import telegram

    import main
    from core import ui

    calls = []

    class FakeQuery:
        def __init__(self, exc=None):
            self.exc = exc

        async def edit_message_text(self, text, reply_markup=None, **kw):
            calls.append(reply_markup)
            if self.exc:
                raise self.exc

    # کیبورد پایین چت باید نادیده گرفته شود و خطا ندهد
    q = FakeQuery()
    ok = await main._edit_query(q, "متن", reply_markup=ui.main_menu())
    check("خطایی داده نشد و نتیجه False نیست", ok is True)
    check(
        "کیبورد پایین چت نادیده گرفته شد",
        calls[-1] is None,
        str(calls[-1]),
    )

    # کیبورد شناور باید سالم عبور کند
    inline = ui.provider_key_actions("X", False)
    q2 = FakeQuery()
    await main._edit_query(q2, "متن", inline)
    check("کیبورد شناور عبور می‌کند", calls[-1] is inline, str(calls[-1]))

    # خطای not modified نباید بالا بیاید
    q3 = FakeQuery(telegram.error.BadRequest("Message is not modified"))
    ok3 = await main._edit_query(q3, "متن")
    check("خطای «not modified» بی‌صدا رد شد", ok3 is False)

    # خطای ناسازگاری کیبورد هم نباید بالا بیاید
    q4 = FakeQuery(telegram.error.BadRequest("Inline keyboard expected"))
    ok4 = await main._edit_query(q4, "متن")
    check("خطای «Inline keyboard expected» مهار شد", ok4 is False)


async def test_no_reply_keyboard_in_edit_calls():
    """
    بررسی ایستا: هیچ کیبورد پایین چتی نباید به ویرایش پیام برسد.

    این تست جلوی برگشت همان باگ را می‌گیرد، چون خطا فقط در زمان اجرا و
    فقط برای همان دکمه ظاهر می‌شود.
    """
    import ast

    import main
    from core import ui

    path = main.__file__
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    raw_edits = []
    offenders = []
    helper_span = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_edit_query":
            helper_span = (node.lineno, node.end_lineno)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # فراخوانی مستقیمِ edit_message_text فقط داخل خودِ تابع محافظ مجاز است
        if isinstance(func, ast.Attribute) and func.attr == "edit_message_text":
            inside = (
                helper_span is not None
                and helper_span[0] <= node.lineno <= helper_span[1]
            )
            if not inside:
                raw_edits.append(node.lineno)
        if isinstance(func, ast.Name) and func.id == "_edit_query":
            for kw in node.keywords:
                if kw.arg == "reply_markup" and "main_menu" in ast.dump(kw.value):
                    offenders.append(node.lineno)

    check(
        "ویرایش مستقیم بیرون از تابع محافظ باقی نمانده",
        not raw_edits,
        f"خطوط {raw_edits}",
    )
    check(
        "کیبورد پایین چت به ویرایش پیام داده نمی‌شود",
        not offenders,
        f"خطوط {offenders}",
    )
    check(
        "کیبورد پایین چت از نوع ReplyKeyboard است",
        isinstance(ui.main_menu(), ReplyKeyboardMarkup),
        type(ui.main_menu()).__name__,
    )


async def test_error_handler_is_quiet_for_network_errors():
    """
    خطای شبکه نباید traceback در ترمینال بریزد.

    چون پایتونی که این‌طور اجرا می‌شود از VPN/پراکسی استفاده می‌کند، قطع
    ارتباط در حالت polling عادی است و کتابخانه خودش دوباره تلاش می‌کند.
    """
    import telegram

    import main

    class FakeCtx:
        def __init__(self, error):
            self.error = error

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Capture()
    main.logger.addHandler(handler)
    old_level = main.logger.level
    main.logger.setLevel(logging.DEBUG)
    try:
        await main._on_error(None, FakeCtx(
            telegram.error.NetworkError("httpx.ReadError")))
        await main._on_error(None, FakeCtx(
            telegram.error.TimedOut("Timed out")))
        await main._on_error(None, FakeCtx(ValueError("boom")))
    finally:
        main.logger.removeHandler(handler)
        main.logger.setLevel(old_level)

    network = [r for r in records if "قطع ارتباط" in r.getMessage()]
    timeout = [r for r in records if "زمان مقرر" in r.getMessage()]
    real = [r for r in records if r.levelno >= logging.ERROR]

    check("خطای شبکه یک خط هشدار داد", len(network) == 1, str(len(network)))
    check("خطای تایم‌اوت هم هشدار داد", len(timeout) == 1, str(len(timeout)))
    check("خطای واقعی هنوز ERROR است", len(real) == 1, str(len(real)))
    check(
        "هشدارها traceback ندارند",
        all(r.exc_info is None for r in network + timeout),
        "exc_info present",
    )


async def test_mouse_control_mode():
    """
    حالت کنترل ماوس: فعال‌سازی، حرکت/کلیک/کلید، تایپ در نقطه‌ی انتخاب، خروج.

    نکته‌ی مهم: همه‌ی فراخوانی‌های ماوس ماک می‌شوند؛ نباید ماوس واقعی
    کاربر حین تست تکان بخورد. فقط encode_jpeg واقعی است تا مسیر عکس هم
    آزموده شود.
    """
    import main
    from core import mouse as m
    from PIL import Image

    calls = {
        "move": [], "click": [], "scroll": [], "key": [], "combo": [],
        "text": [], "hold": 0, "release": 0,
    }
    old = {
        name: getattr(m, name)
        for name in (
            "position", "annotated_screenshot", "move", "click", "scroll",
            "press", "combo", "hold", "release", "set_text",
        )
    }

    m.position = lambda: (500, 300)
    m.annotated_screenshot = (
        lambda max_width=None, screen_size=None: Image.new("RGB", (80, 60), (20, 20, 20))
    )
    m.move = lambda d, s: calls["move"].append((d, s)) or (500, 300)
    m.click = lambda k: calls["click"].append(k)
    m.scroll = lambda d, amount=3: calls["scroll"].append((d, amount))
    m.press = lambda k: calls["key"].append(k)
    m.combo = lambda k: calls["combo"].append(k)
    m.hold = lambda: calls.__setitem__("hold", calls["hold"] + 1)
    m.release = lambda: calls.__setitem__("release", calls["release"] + 1)
    m.set_text = lambda t: calls["text"].append(t)

    # ماک‌های windows برای refocus و جابجایی پنجره (بدون دست زدن به ویندوز واقعی)
    wcalls = {"fg": 0, "front": [], "list": []}
    old_w = {
        name: getattr(main.windows, name)
        for name in ("foreground", "is_window", "is_foreground",
                     "bring_to_front", "list_windows")
    }
    main.windows.foreground = lambda: 7777
    main.windows.is_window = lambda h: True
    main.windows.is_foreground = lambda h: True
    main.windows.bring_to_front = (
        lambda h: wcalls["front"].append(h) or True
    )
    main.windows.list_windows = lambda: [
        {"hwnd": 1111, "title": "Google Chrome"},
        {"hwnd": 2222, "title": "opencode"},
        {"hwnd": 3333, "title": ""},
    ]

    def cb(data: str):
        up = make_callback(data)
        up.callback_query.edit_message_media = AsyncMock()
        up.callback_query.edit_message_caption = AsyncMock()
        up.callback_query.edit_message_reply_markup = AsyncMock()
        return up

    try:
        ctx = make_context()
        ctx.user_data["all_apps"] = [{"name": "Notepad", "app_id": "np"}]

        # --- ورود با دکمه‌ی منو
        await main.on_text(make_update(111111111, text=main.ui.BTN_MOUSE), ctx)
        s = ctx.user_data["session"]
        check("دکمه‌ی منو وارد حالت ماوس می‌کند",
              s.get("mode") == "mouse", str(s.get("mode")))
        check("گام پیش‌فرض ۵۰ پیکسل است",
              s.get("mouse_step") == 50, str(s.get("mouse_step")))
        check("عکس اولیه فرستاده شد", ctx.bot.send_photo.called)
        kb = ctx.bot.send_photo.call_args.kwargs.get("reply_markup")
        check("کیبورد کنترل ماوس دکمه‌ی فلش دارد",
              kb is not None and "mouse:mv:up" in str(kb))

        # دکمه‌ی «ورود» روی پیام راهنما هم وارد حالت می‌کند
        await main.on_callback(cb("mouse_enter"), ctx)
        check("دکمه‌ی راهنما حالت ماوس را فعال می‌کند",
              s.get("mode") == "mouse")

        # --- حرکت با گام پیش‌فرض
        u = cb("mouse:mv:up")
        await main.on_callback(u, ctx)
        check("فلش بالا با گام ۵۰ حرکت می‌دهد",
              calls["move"] == [("up", 50)], str(calls["move"]))
        check("بعد از هر عمل عکس ویرایش می‌شود",
              u.callback_query.edit_message_media.called)

        # --- تغییر گام
        await main.on_callback(cb("mouse:step:up"), ctx)
        check("گام زیاد می‌شود", s.get("mouse_step") == 100,
              str(s.get("mouse_step")))
        await main.on_callback(cb("mouse:mv:left"), ctx)
        check("حرکت بعدی با گام جدید است",
              calls["move"][-1] == ("left", 100), str(calls["move"][-1]))

        # --- کلیک/درگ/اسکرول/کلید/ترکیب
        for data in (
            "mouse:click:shift", "mouse:down", "mouse:up",
            "mouse:scroll:up", "mouse:key:enter", "mouse:combo:ctrl+c",
        ):
            await main.on_callback(cb(data), ctx)
        check("Shift+کلیک انجام شد", calls["click"] == ["shift"],
              str(calls["click"]))
        check("نگه/رها برای درگ انجام شد",
              calls["hold"] == 1 and calls["release"] == 1)
        check("اسکرول با گام پیش‌فرض انجام شد",
              calls["scroll"] == [("up", 12)], str(calls["scroll"]))
        check("کلید Enter ارسال شد", calls["key"] == ["enter"], str(calls["key"]))
        check("ترکیب Ctrl+C ارسال شد", calls["combo"] == ["ctrl+c"],
              str(calls["combo"]))

        # --- گام اسکرول (مقدار ثابت ۳ روی بعضی مرورگرها دیده نمی‌شود)
        check("گام اسکرول پیش‌فرض ۱۲ است", s.get("scroll_step") == 12,
              str(s.get("scroll_step")))
        await main.on_callback(cb("mouse:sstep:up"), ctx)
        check("گام اسکرول زیاد می‌شود", s.get("scroll_step") == 25,
              str(s.get("scroll_step")))
        await main.on_callback(cb("mouse:scroll:up"), ctx)
        check("اسکرول بعدی با گام جدید است",
              calls["scroll"][-1] == ("up", 25), str(calls["scroll"][-1]))
        await main.on_callback(cb("mouse:sstep:down"), ctx)
        check("گام اسکرول کم می‌شود", s.get("scroll_step") == 12,
              str(s.get("scroll_step")))

        # --- شورتکات: هدف (پنجره‌ی فعال هنگام ورود) باید جلو باشد
        # اگر تلگرام دسکتاپ فوکوس گرفته باشد، Ctrl+C به تلگرام می‌خورد نه مقصد
        check("target_hwnd هنگام ورود ثبت شد", s.get("target_hwnd") == 7777,
              str(s.get("target_hwnd")))
        main.windows.is_foreground = lambda h: False  # تلگرام فوکوس گرفته
        await main.on_callback(cb("mouse:combo:ctrl+c"), ctx)
        check("قبل از ترکیب، هدف دوباره جلو می‌آید",
              7777 in wcalls["front"], str(wcalls["front"]))
        main.windows.is_foreground = lambda h: True  # بازگردانی برای بقیه‌ی تست

        # --- جابجایی بین پنجره‌های باز (مثل تب‌های مرورگر)
        u = cb("mouse:winlist")
        await main.on_callback(u, ctx)
        pick = u.callback_query.message.reply_text.call_args
        check("لیست پنجره‌های باز فرستاده می‌شود",
              pick is not None
              and "Google Chrome" in str(pick)
              and "opencode" in str(pick),
              str(pick)[:200])
        check("پنجره‌ی عنوان‌خالی در لیست نیست", "3333" not in str(pick),
              str(pick)[:200])
        pick_kb = pick.kwargs.get("reply_markup") if pick else None
        check("کیبورد انتخاب پنجره، بازگشت دارد",
              pick_kb is not None and "mouse:winback" in str(pick_kb),
              str(pick_kb))
        check("شناسه‌ی پیام انتخاب ذخیره شد", s.get("win_pick_msg") is not None,
              str(s.get("win_pick_msg")))

        u = cb("mouse:win:1")  # opencode (دومی)
        await main.on_callback(u, ctx)
        check("پنجره‌ی انتخابی جلو می‌آید", 2222 in wcalls["front"],
              str(wcalls["front"]))
        check("target_hwnd به پنجره‌ی انتخابی عوض شد", s.get("target_hwnd") == 2222,
              str(s.get("target_hwnd")))
        check("پیام انتخاب بعد از انتخاب حذف می‌شود", s.get("win_pick_msg") is None,
              str(s.get("win_pick_msg")))

        # بازگشت از انتخاب
        u = cb("mouse:winlist")
        await main.on_callback(u, ctx)
        u2 = cb("mouse:winback")
        await main.on_callback(u2, ctx)
        check("بازگشت، پیام انتخاب را حذف می‌کند", s.get("win_pick_msg") is None,
              str(s.get("win_pick_msg")))

        # --- دکمه‌های مرورگر (برای بعد از جست‌وجوی گوگل)
        for data in ("mouse:combo:alt+left", "mouse:key:f5"):
            await main.on_callback(cb(data), ctx)
        check("بازگشت صفحه (Alt+Left) ارسال می‌شود",
              calls["combo"][-1] == "alt+left", str(calls["combo"]))
        check("تازه‌سازی (F5) ارسال می‌شود",
              calls["key"][-1] == "f5", str(calls["key"]))

        # --- تایپ متن (فارسی) در نقطه‌ی انتخاب‌شده با ماوس
        # اگر شاخه‌ی حالت ماوس کار نکند، متن به ساخت پرامپت می‌رسد؛ پس
        # صدای build_prompt هم شنیده می‌شود و این تست شکست می‌خورد.
        old_build = main.ai.build_prompt
        builds = []

        async def fake_build(raw):
            builds.append(raw)
            return True, "پرامپت", "TestProvider"

        main.ai.build_prompt = fake_build
        try:
            await main.on_text(make_update(111111111, text="سلام دنیا"), ctx)
        finally:
            main.ai.build_prompt = old_build
        check("متن فارسی در همان نقطه چسبانده می‌شود",
              calls["text"] == ["سلام دنیا"], str(calls["text"]))
        check("متن به ساخت پرامپت نرفت", not builds, str(builds))
        check("بعد از تایپ عکس حالت ماوس ویرایش می‌شود",
              ctx.bot.edit_message_media.called)

        # --- دکمه‌ی منو حالت را می‌بندد و کار خودش را می‌کند
        await main.on_text(make_update(111111111, text=main.ui.BTN_APPS), ctx)
        check("دکمه‌ی منو حالت ماوس را می‌بندد",
              ctx.user_data["session"].get("mode") != "mouse")

        # --- خروج با دکمه‌ی خروج → صفحه‌ی «تایپ در مکان سلکت‌شده / کنسل»
        ctx.user_data["session"]["mode"] = "mouse"
        u = cb("mouse:exit")
        await main.on_callback(u, ctx)
        check("خروج از حالت ماوس", ctx.user_data["session"].get("mode") == "")
        exit_cap = u.callback_query.edit_message_caption.call_args
        exit_kb = exit_cap.kwargs.get("reply_markup") if exit_cap else None
        check("صفحه‌ی خروج دکمه‌های تایپ و کنسل را نشان می‌دهد",
              exit_kb is not None
              and "mouse:type_ask" in str(exit_kb)
              and "mouse:type_cancel" in str(exit_kb),
              str(exit_kb))
        check("کپشن عکس ویرایش می‌شود، نه متن (edit_message_text صدا نمی‌خورد)",
              u.callback_query.edit_message_caption.called
              and not u.callback_query.edit_message_text.called)

        # --- دکمه‌ی عکس قدیمی بعد از خروج نباید ماوس را تکان دهد
        before = len(calls["move"])
        u = cb("mouse:mv:up")
        await main.on_callback(u, ctx)
        check("بعد از خروج، دکمه‌های قدیمی بی‌اثرند",
              len(calls["move"]) == before, str(calls["move"]))
        check("به کاربر هشدار داده می‌شود",
              u.callback_query.answer.call_args.kwargs.get("show_alert") is True)

        # --- جریان «وارد کردن متن در مکان سلکت‌شده» بعد از خروج
        ctx.user_data["session"]["mode"] = ""
        await main.on_callback(cb("mouse:type_ask"), ctx)
        check("درخواست تایپ، حالت ورود متن را فعال می‌کند",
              ctx.user_data["session"].get("mode") == "mouse_type",
              str(ctx.user_data["session"].get("mode")))
        await main.on_text(make_update(111111111, text="متن انتخابی"), ctx)
        check("متنِ درخواستی در مکان سلکت‌شده چسبانده می‌شود",
              calls["text"][-1] == "متن انتخابی", str(calls["text"]))
        check("بعد از تایپ حالت بسته می‌شود",
              ctx.user_data["session"].get("mode") != "mouse_type")
        check("عکس پیام «چسبانده شد» می‌گیرد",
              ctx.bot.edit_message_caption.called)

        # --- کنسلِ جریان تایپ
        ctx.user_data["session"]["mode"] = "mouse_type"
        u = cb("mouse:type_cancel")
        await main.on_callback(u, ctx)
        check("کنسل، حالت را می‌بندد",
              ctx.user_data["session"].get("mode") == "")

        # --- دکمه‌ی ناشناخته اجرا نمی‌شود
        ctx.user_data["session"]["mode"] = "mouse"
        before_clicks = len(calls["click"])
        await main.on_callback(cb("mouse:click:bogus"), ctx)
        check("کلیک ناشناخته بی‌اثر است",
              len(calls["click"]) == before_clicks, str(calls["click"]))

        # --- صحت‌سنجی کیبورد
        datas = [
            b.callback_data
            for row in main.ui.mouse_keyboard(50, 12).inline_keyboard
            for b in row
        ]
        check("callback_dataهای ماوس زیر سقف ۶۴ بایت تلگرام‌اند",
              all(len(d.encode("utf-8")) <= 64 for d in datas))
        check("دکمه‌ی خروج در کیبورد هست", "mouse:exit" in datas)
        check("دکمه‌ی انتخاب گام اسکرول در کیبورد هست", "mouse:sstep:up" in datas)
        check("دکمه‌ی لیست پنجره‌ها در کیبورد هست", "mouse:winlist" in datas)
        flat = [b.text for row in main.ui.main_menu().keyboard for b in row]
        check("دکمه‌ی ماوس در منوی اصلی است", main.ui.BTN_MOUSE in flat, str(flat))
    finally:
        for name, fn in old.items():
            setattr(m, name, fn)
        for name, fn in old_w.items():
            setattr(main.windows, name, fn)


async def test_google_search():
    """
    جست‌وجوی گوگل: لینک درست باز شود و حالت ماوس خودکار فعال شود.

    os.startfile نباید واقعاً مرورگر را باز کند؛ پس ماک می‌شود.
    """
    import main
    from core import mouse as m
    from PIL import Image

    opened = []
    old_startfile = os.startfile
    old_find = main.windows.find_window
    old_focus = main.windows.focus
    old_shot = m.annotated_screenshot
    old_pos = m.position

    os.startfile = lambda url: opened.append(url)
    main.windows.find_window = lambda kws: None
    main.windows.focus = lambda hwnd: True
    m.annotated_screenshot = (
        lambda max_width=None, screen_size=None: Image.new("RGB", (80, 60), (10, 10, 10))
    )
    m.position = lambda: (100, 100)
    try:
        # ورود با دکمه‌ی منو → حالت انتظار جست‌وجو
        ctx = make_context()
        await main.on_text(make_update(111111111, text=main.ui.BTN_GOOGLE), ctx)
        check("دکمه‌ی گوگل حالت جست‌وجو را فعال می‌کند",
              ctx.user_data["session"].get("mode") == "google",
              str(ctx.user_data["session"].get("mode")))

        # ارسال متن → باز شدن لینک + فعال شدن حالت ماوس
        await main.on_text(make_update(111111111, text="open code tutorial"), ctx)
        check("لینک جست‌وجوی گوگل باز شد",
              len(opened) == 1
              and opened[0].startswith("https://www.google.com/search?q="),
              str(opened))
        check("متن در لینک کدگذاری شد",
              bool(opened) and "open%20code%20tutorial" in opened[0],
              opened[0] if opened else "")
        check("بعد از جست‌وجو حالت ماوس خودکار فعال می‌شود",
              ctx.user_data["session"].get("mode") == "mouse",
              str(ctx.user_data["session"].get("mode")))
        check("عکس حالت ماوس هم فرستاده شد", ctx.bot.send_photo.called)

        # دستور /google با آرگومان → سریع جست‌وجو (فارسی کدگذاری می‌شود)
        ctx2 = make_context()
        ctx2.args = ["پایتون", "آموزش"]
        await main.cmd_google(make_update(111111111, text="/google"), ctx2)
        check("دستور /google با آرگومان سریع جست‌وجو می‌کند",
              len(opened) == 2
              and opened[1].startswith("https://www.google.com/search?q=")
              and "%" in opened[1],
              str(opened))

        # دستور /google بدون آرگومان → حالت انتظار
        ctx3 = make_context()
        ctx3.args = []
        await main.cmd_google(make_update(111111111, text="/google"), ctx3)
        check("/google بدون آرگومان حالت انتظار را فعال می‌کند",
              ctx3.user_data["session"].get("mode") == "google")
    finally:
        os.startfile = old_startfile
        main.windows.find_window = old_find
        main.windows.focus = old_focus
        m.annotated_screenshot = old_shot
        m.position = old_pos


def _stream_loop_src() -> str:
    """کدِ تابع _handle_stream، برای بازرسی رفتار استریم."""
    import inspect
    from core import remote as _r

    return inspect.getsource(_r._Handler._handle_stream)


async def test_remote_console():
    """
    کنسول ریموت وب: صفحه با توکن، رد شدن بدون توکن، فرمان ماوس، استریم، توقف.

    تونل کلودفلر و دانلود binary در تست واقعی نمی‌شوند؛ فقط HTTP محلی
    (start با tunnel=False) و جدا کردن آدرس از خروجی تست می‌شود.
    """
    import json as _json
    import threading as _threading
    import time as _time
    import urllib.error
    import urllib.request

    import main
    from core import mouse as m
    from core import remote as rem
    from PIL import Image

    # جدا کردن آدرس تونل از خروجی cloudflared
    sample = "INF |  https://abc-123-def.trycloudflare.com  | some"
    check("آدرس تونل از خروجی cloudflared پیدا می‌شود",
          rem.parse_tunnel_url(sample) == "https://abc-123-def.trycloudflare.com",
          sample)
    check("متن بدون تونل آدرس نمی‌دهد", rem.parse_tunnel_url("hello world") is None)
    # باگ لینک /remote: آدرس پایه‌ی تونل باید مسیر /R/<token>/ بگیرد،
    # وگرنه مرورگر به / می‌رود و «not found» برمی‌گردد
    check("مسیر صفحه به آدرس پایه‌ی تونل اضافه می‌شود",
          rem._page_url("https://abc-123-def.trycloudflare.com", "tok1")
          == "https://abc-123-def.trycloudflare.com/R/tok1/")
    check("اسلش اضافی در پایه تکرار نمی‌شود",
          rem._page_url("https://x.trycloudflare.com/", "t")
          == "https://x.trycloudflare.com/R/t/")

    old = {
        "shot": m.annotated_screenshot,
        "pos": m.position,
        "move_abs": m.move_abs,
        "click": m.click,
        "hold": m.hold,
        "release": m.release,
        "scroll": m.scroll,
        "press": m.press,
        "combo": m.combo,
        "set_text": m.set_text,
    }
    calls = {k: [] for k in (
        "move_abs", "click", "hold", "release", "scroll", "press", "combo", "set_text",
    )}
    # آرگومان‌های اختیاری را می‌پذیرد: _capture_loop در کنسول ریموت
    # max_width و screen_size می‌دهد تا تصویر قبل از annotate کوچک شود.
    m.annotated_screenshot = (
        lambda max_width=None, screen_size=None: Image.new("RGB", (80, 60), (20, 20, 20))
    )
    m.position = lambda: (11, 22)
    m.move_abs = lambda x, y: calls["move_abs"].append((x, y)) or (x, y)
    m.click = lambda kind, pause=None: calls["click"].append(kind)
    m.hold = lambda pause=None: calls["hold"].append(True)
    m.release = lambda pause=None: calls["release"].append(True)
    m.scroll = lambda d, amount=3, pause=None: calls["scroll"].append((d, amount))
    m.press = lambda k, pause=None: calls["press"].append(k)
    m.combo = lambda c, pause=None: calls["combo"].append(c)
    m.set_text = lambda t: calls["set_text"].append(t)

    console = rem.console

    # ماک windows برای refocus کنسول ریموت (do_key/do_text هدف را جلو می‌آورد)
    old_w = {
        name: getattr(rem.windows, name)
        for name in ("foreground", "is_window", "is_foreground", "bring_to_front")
    }
    rem.windows.foreground = lambda: 9999
    rem.windows.is_window = lambda h: True
    rem.windows.is_foreground = lambda h: True
    rem.windows.bring_to_front = lambda h: True

    def fetch(url):
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read(65536), dict(r.headers)

    def fetch_err(url):
        try:
            urllib.request.urlopen(url, timeout=5)
            return 200
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return 0

    def post(base, path, payload):
        data = _json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            base + path, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    try:
        await asyncio.to_thread(console.start, tunnel=False)
        st = console.status()
        check("سرور کنسول بالا است", bool(st["running"]), str(st))
        check("آدرس start مسیر /R/ دارد (باگ لینک not-found)",
              st["url"] is not None and "/R/" in st["url"] and
              st["url"].endswith(f"/R/{console.token}/"),
              str(st["url"]))
        base = f"http://127.0.0.1:{st['port']}/R/{console.token}"

        # صفحه با توکن
        code, body, _h = await asyncio.to_thread(fetch, base + "/")
        check("صفحه‌ی کنسول با توکن باز می‌شود",
              code == 200 and b"<img" in body and b"stream" in body,
              f"code={code}")

        # بدون توکن درست
        code2 = await asyncio.to_thread(
            fetch_err, f"http://127.0.0.1:{st['port']}/R/wrong/"
        )
        check("بدون توکن درست، دسترسی رد می‌شود", code2 == 404, str(code2))

        # فرمان‌های ماوس
        c1 = await asyncio.to_thread(post, base, "/mouse", {"action": "move", "x": 15, "y": 25})
        c2 = await asyncio.to_thread(
            post, base, "/mouse", {"action": "click", "kind": "double", "x": 5, "y": 6}
        )
        c3 = await asyncio.to_thread(post, base, "/mouse", {"action": "down", "x": 1, "y": 2})
        c4 = await asyncio.to_thread(post, base, "/mouse", {"action": "up"})
        c5 = await asyncio.to_thread(post, base, "/mouse", {"action": "scroll", "dir": "down"})
        check("فرمان move به move_abs رسید",
              c1 == 200 and (15, 25) in calls["move_abs"], str(calls["move_abs"]))
        check("فرمان click دوباره اجرا شد",
              c2 == 200 and "double" in calls["click"], str(calls["click"]))
        check("درگ (down/up) اجرا شد",
              c3 == 200 and c4 == 200
              and calls["hold"] == [True] and calls["release"] == [True])
        check("اسکرول با گام پیش‌فرض اجرا شد",
              c5 == 200 and calls["scroll"] == [("down", 12)],
              str(calls["scroll"]))

        # کلید، ترکیب، متن
        k1 = await asyncio.to_thread(post, base, "/key", {"key": "enter"})
        k2 = await asyncio.to_thread(post, base, "/key", {"combo": "ctrl+v"})
        k3 = await asyncio.to_thread(post, base, "/key", {"key": "bogus"})
        t1 = await asyncio.to_thread(post, base, "/text", {"text": "سلام"})
        check("تک‌کلید اجرا می‌شود", k1 == 200 and calls["press"] == ["enter"],
              str(calls["press"]))
        check("ترکیب ctrl+v اجرا می‌شود", k2 == 200 and calls["combo"] == ["ctrl+v"],
              str(calls["combo"]))
        check("کلید ناشناخته رد می‌شود", k3 == 400, str(k3))
        check("متن به چسباندن می‌رود", t1 == 200 and calls["set_text"] == ["سلام"],
              str(calls["set_text"]))

        # alt+tab: سویچ بین پنجره‌های باز ویندوز (دکمه‌ی موبایل)
        k4 = await asyncio.to_thread(post, base, "/key", {"combo": "alt+tab"})
        check("ترکیب alt+tab اجرا می‌شود", k4 == 200 and "alt+tab" in calls["combo"],
              str(calls["combo"]))
        k5 = await asyncio.to_thread(post, base, "/key", {"combo": "alt+shift+tab"})
        check("ترکیب alt+shift+tab اجرا می‌شود",
              k5 == 200 and "alt+shift+tab" in calls["combo"], str(calls["combo"]))

        # کندی کنترل: مکث بعد از هر عمل در رابط وب باید خیلی کم باشد.
        # پیش‌فرضِ مخصوص تلگرام (MOUSE_ACTION_PAUSE = 0.15) برای پیام‌های
        # تکی خوب است ولی در کنسول ریموت که فرمان‌ها تعاملی و پشت‌سرهم‌اند
        # حس کندی می‌داد.
        check("مکث فرمان‌های ریموت خیلی کمتر از تلگرام است",
              config.REMOTE_ACTION_PAUSE < config.MOUSE_ACTION_PAUSE / 4,
              f"remote={config.REMOTE_ACTION_PAUSE} telegram={config.MOUSE_ACTION_PAUSE}")
        # ثبت هدف پنجره نباید با هر کلیک انجام شود (دو فراخوانی win32)
        check("هدف پنجره با هر کلیک تازه نمی‌شود",
              int(config.REMOTE_REMEMBER_EVERY) > 1,
              str(config.REMOTE_REMEMBER_EVERY))

        # صفحه باید ریسپانسیو باشد و دکمه‌های موبایل را داشته باشد
        page = ""
        with urllib.request.urlopen(base + "/", timeout=10) as r:
            page = r.read().decode("utf-8")
        check("دکمه‌ی Alt+Tab در صفحه‌ی ریموت هست", 'data-c="alt+tab"' in page)
        check("دکمه‌ی Alt+Shift+Tab در صفحه‌ی ریموت هست",
              'data-c="alt+shift+tab"' in page)
        # ریسپانسیو: viewport و media query و حفظ نسبت ابعاد
        check("viewport برای موبایل تنظیم شده",
              "width=device-width" in page and "maximum-scale=1" in page)
        check("media query برای صفحه‌های کوچک هست",
              "@media (max-width:600px)" in page)
        check("نسبت ابعاد تصویر با JS حفظ می‌شود", "function fit()" in page)
        # استریم با HTTP/1.1 و کانال باز (ریشه‌ی رفع 502)
        check("سرور ریموت روی HTTP/1.1 است",
              rem._Handler.protocol_version == "HTTP/1.1",
              rem._Handler.protocol_version)

        # قطع اتصال خشونت‌بار مرورگر/تونل نباید traceback چاپ کند.
        # با keep-alive هر بار که کلاینت صفحه را ترک می‌کند این اتفاق
        # می‌افتد و کتابخانه‌ی استاندارد برای هرکدام یک traceback کامل
        # روی stderr می‌ریخت و کنسول را پر از پیام تکراری می‌کرد.
        import socket as _socket

        def hard_reset():
            sk = _socket.create_connection(("127.0.0.1", console._port))
            sk.sendall(
                f"GET /R/{console.token}/ HTTP/1.1\r\nHost: x\r\n\r\n".encode()
            )
            _time.sleep(0.01)
            # SO_LINGER با تایم‌صفر یعنی RST به‌جای بستنِ نرم
            sk.setsockopt(
                _socket.SOL_SOCKET, _socket.SO_LINGER,
                b"\x01\x00\x00\x00\x00\x00\x00\x00",
            )
            sk.close()

        for _ in range(6):
            await asyncio.to_thread(hard_reset)
        await asyncio.sleep(0.3)
        code_reset = await asyncio.to_thread(fetch_err, base + "/")
        check("بعد از قطع‌های خشونت‌بار سرور سالم می‌ماند", code_reset == 200,
              f"status={code_reset}")

        # استریم MJPEG: اولین بایت‌ها باید header multipart باشند
        #
        # نکته: با HTTP/1.1 سرور کانال را باز نگه می‌دارد (این همان چیزی است
        # که خطای ۵۰۲ را رفع می‌کند)، پس اینجا باید فقط «اولین» بسته را
        # بخوانیم و برگردیم — read(128) صبر می‌کند تا دقیقاً ۱۲۸ بایت شود
        # و اگر هدر و فریم در دو بسته‌ی جدا برسند، هرگز پر نمی‌شود.
        def read_stream():
            with urllib.request.urlopen(base + "/stream", timeout=15) as r:
                head = r.headers.get("Content-Type", "")
                # read1 فقط هرچه در بافر هست را برمی‌گرداند (بدون انتظار)
                chunk = r.read1(4096) if hasattr(r, "read1") else r.read(128)
                return head, chunk

        head, chunk = await asyncio.to_thread(read_stream)
        check("استریم MJPEG با boundary پاسخ می‌دهد",
              "multipart/x-mixed-replace" in head and b"--frame" in chunk,
              f"{head[:40]} {chunk[:30]!r}")

        # سنجه‌ی تأخیر: نرخ واقعی فریم باید به REMOTE_FPS نزدیک باشد.
        # حلقه‌ی کپچر فقط وقتی عکس می‌گیرد که بیننده وصل باشد (برای صرفه‌جویی
        # CPU)، پس باید یک بیننده‌ی زنده نگه داریم وگرنه نرخ صفر می‌شود.
        # ترد تا پایان اندازه‌گیری باز می‌ماند و جریان را می‌خواند.
        def hold_stream(seconds):
            end = _time.time() + seconds
            with urllib.request.urlopen(base + "/stream", timeout=seconds + 5) as r:
                while _time.time() < end:
                    if not r.read1(65536):
                        break

        holder = _threading.Thread(target=hold_stream, args=(2.0,), daemon=True)
        holder.start()
        await asyncio.sleep(0.4)          # فرصت وصل شدن بیننده
        joined = console._clients
        n_before = console.hub._seq
        await asyncio.sleep(1.0)
        produced = console.hub._seq - n_before
        check("بیننده‌ی استریم ثبت شد", joined >= 1, f"clients={joined}")
        check("نرخ فریم استریم نزدیک به مقدار تنظیم‌شده است",
              produced >= max(2, int(config.REMOTE_FPS * 0.5)),
              f"{produced} فریم در ۱ ثانیه (هدف={config.REMOTE_FPS})")

        # استریم نباید به‌خاطر timeout (بی‌فریم بودن) بسته شود؛ این رفتار
        # باعث تأخیر چندثانیه‌ای می‌شد چون مرورگر دوباره وصل می‌شد.
        src = _stream_loop_src()
        check("استریم با بی‌فریم‌بودن بسته نمی‌شود",
              "if jpeg is None:" in src and "break" not in src.split("if jpeg is None:")[1].split("last = seq")[0],
              src.split("if jpeg is None:")[1].split("last = seq")[0].strip()[:60])

        # توقف
        await asyncio.to_thread(console.stop)
        st2 = console.status()
        check("بعد از stop سرور بسته می‌شود", not st2["running"], str(st2))
        code3 = await asyncio.to_thread(fetch_err, base + "/")
        check("بعد از stop درخواست جواب نمی‌گیرد", code3 == 0, str(code3))

        # جریان دستورهای /remote و /remote_stop (ماک remote.start/stop/status)
        old_start, old_stop, old_status = (
            main.remote.start, main.remote.stop, main.remote.status,
        )
        started = []
        stopped = []

        def fake_start(timeout=None, provider="cloudflare"):
            started.append((timeout, provider))
            return f"https://x.trycloudflare.com/R/tok123/{provider}/"

        main.remote.start = fake_start
        main.remote.stop = lambda: stopped.append(True) or True
        main.remote.status = lambda: {"running": False, "url": None, "port": 0, "provider": None}
        try:
            # /remote وقتی غیرفعال است باید گزینه‌های تونل را نشان دهد (بدون شروع خودکار)
            ctx = make_context()
            u = make_update(111111111, text="/remote")
            await main.cmd_remote(u, ctx)
            texts = " ".join(
                str(c.args[0]) for c in u.effective_message.reply_text.call_args_list
            )
            check("دستور /remote گزینه‌های تونل را نشان می‌دهد",
                  "Cloudflare" in texts and "ngrok" in texts, texts[:120])
            check("دستور /remote بدون انتخاب تونل را شروع نمی‌کند", started == [], str(started))
            rm = u.effective_message.reply_text.call_args_list[-1].kwargs.get("reply_markup")
            check("دستور /remote کیبورد انتخاب تونل دارد", rm is not None)

            # انتخاب Cloudflare از طریق callback → شروع و ارسال لینک
            main.remote.status = lambda: {"running": False, "url": None, "port": 0, "provider": None}
            cb = make_callback("remote_start:cloudflare")
            await main.on_callback(cb, ctx)
            cb_texts = " ".join(
                str(c.args[0]) for c in cb.effective_message.reply_text.call_args_list
            )
            check("انتخاب Cloudflare تونل را شروع می‌کند",
                  started == [(None, "cloudflare")], str(started))
            check("لینک کنسول پس از شروع Cloudflare ارسال می‌شود",
                  "trycloudflare.com" in cb_texts, cb_texts[:140])

            # انتخاب ngrok از طریق callback
            main.remote.status = lambda: {"running": False, "url": None, "port": 0, "provider": None}
            cb_ng = make_callback("remote_start:ngrok")
            await main.on_callback(cb_ng, ctx)
            cb_ng_texts = " ".join(
                str(c.args[0]) for c in cb_ng.effective_message.reply_text.call_args_list
            )
            check("انتخاب ngrok تونل را شروع می‌کند",
                  started[-1] == (None, "ngrok"), str(started))
            check("لینک کنسول پس از شروع ngrok ارسال می‌شود",
                  "https://" in cb_ng_texts, cb_ng_texts[:140])

            # از قبل فعال → همان لینک دوباره
            main.remote.status = lambda: {
                "running": True,
                "url": "https://x.trycloudflare.com/R/tok123/",
                "port": 1,
                "provider": "cloudflare",
            }
            ctx2 = make_context()
            u2 = make_update(111111111, text="/remote")
            await main.cmd_remote(u2, ctx2)
            texts2 = " ".join(
                str(c.args[0]) for c in u2.effective_message.reply_text.call_args_list
            )
            check("وقتی فعال است لینک قبلی دوباره فرستاده می‌شود",
                  "از قبل فعال" in texts2 and len(started) == 2, texts2[:100])

            # خطای RemoteError در شروع تونل → پیام فارسی
            def boom(timeout=None, provider="cloudflare"):
                raise rem.RemoteError("خطای آزمایشی تونل")

            main.remote.start = boom
            main.remote.status = lambda: {"running": False, "url": None, "port": 0, "provider": None}
            cb_err = make_callback("remote_start:cloudflare")
            await main.on_callback(cb_err, ctx)
            texts3 = " ".join(
                str(c.args[0])
                for c in cb_err.effective_message.reply_text.call_args_list
            )
            check("خطای RemoteError به کاربر پیام می‌دهد",
                  "خطای آزمایشی" in texts3, texts3[:120])

            # stop
            main.remote.stop = lambda: stopped.append(True) or True
            ctx4 = make_context()
            u4 = make_update(111111111, text="/remote_stop")
            await main.cmd_remote_stop(u4, ctx4)
            texts4 = " ".join(
                str(c.args[0])
                for c in u4.effective_message.reply_text.call_args_list
            )
            check("/remote_stop کنسول را می‌بندد", stopped == [True], str(stopped))
            check("/remote_stop پیام تأیید می‌دهد", "بسته شد" in texts4, texts4[:60])
        finally:
            main.remote.start = old_start
            main.remote.stop = old_stop
            main.remote.status = old_status
    finally:
        for name, fn in old.items():
            setattr(m, name, fn)
        for name, fn in old_w.items():
            setattr(rem.windows, name, fn)
        with contextlib.suppress(Exception):
            console.stop()


async def main():
    print("=" * 60)
    print("  تست خودکار بات کنترل لپ‌تاپ")
    print("=" * 60)
    for fn in (
        test_security,
        test_queue_serialization,
        test_app_source_integrity,
        test_prompt_fallback,
        test_app_callback_guard,
        test_shutdown_confirmation_flow,
        test_window_helpers,
        test_imaging_pipeline,
        test_template_matching,
        test_flat_template_rejected,
        test_pagination,
        test_handlers_registered,
        test_callback_routing,
        test_shutdown_button_flow,
        test_opencode_orchestration,
        test_opencode_outcomes,
        test_no_false_completion_without_template,
        test_resume_watch_after_approval,
        test_prompt_session_flow,
        test_free_text_starts_prompt,
        test_button_matching,
        test_camera_selection,
        test_app_launch_no_not_modified,
        test_app_open_already_running,
        test_edit_helper_tolerates_not_modified,
        test_api_key_flow,
        test_tabs_window_missing_message,
        test_tabs_command_and_send_flow,
        test_status_command_runs,
        test_detect_tabs_on_synthetic_strip,
        test_detect_tabs_ignores_spurious_clusters,
        test_annotate_strip_draws_on_canvas,
        test_paste_escalation_verifies_result,
        test_input_region_lands_inside_textarea,
        test_changed_pixels_is_scale_independent,
        test_edit_query_rejects_reply_keyboard,
        test_no_reply_keyboard_in_edit_calls,
        test_error_handler_is_quiet_for_network_errors,
        test_telegram_timeouts_are_generous,
        test_request_object_uses_config,
        test_document_falls_back_when_too_big,
        test_send_screenshot_document_failure_is_not_fatal,
        test_shot_helper_survives_errors,
        test_open_app_error_not_caused_by_screenshot,
        test_tabs_keyboard_numbers_only,
        test_mouse_control_mode,
        test_google_search,
        test_remote_console,
    ):
        try:
            await asyncio.wait_for(fn(), timeout=90)
        except asyncio.TimeoutError:
            check(fn.__name__, False, "بیش از ۹۰ ثانیه طول کشید (احتمالاً بست)")
            continue
        except Exception as exc:
            check(fn.__name__, False, f"استثنا: {exc}")
            import traceback

            traceback.print_exc()

    print("=" * 60)
    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"نتیجه: {passed} موفق، {failed} ناموفق")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))