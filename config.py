# -*- coding: utf-8 -*-
"""
تنظیمات اصلی بات. فقط همین فایل را ویرایش کن.
"""

import os

# ---------------------------------------------------------------- Telegram
# توکنی که از @BotFather گرفتی. این را نگه دار و در جای دیگری منتشرش نکن.
BOT_TOKEN = "PUT_YOUR_BOT_TOKEN_HERE"

# فقط این user id ها اجازه‌ی کنترل سیستم را دارند.
# عدد را از @userinfobot بگیر. همه‌ی پیام‌های بقیه بی‌پاسخ رها می‌شوند.
OWNER_IDS = {
    111111111
}

# اگر True باشد، فقط در چت خصوصی کار می‌کند (برای امنیت بیشتر پیشنهاد می‌شود)
PRIVATE_CHAT_ONLY = True

# ---------------------------------------------------------------- مسیرها
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "bot.log")

APPS_CACHE_FILE = os.path.join(DATA_DIR, "apps_cache.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
PROMPT_FILE = os.path.join(DATA_DIR, "last_prompt.txt")
APPS_USAGE_FILE = os.path.join(DATA_DIR, "apps_usage.json")
LOCK_FILE = os.path.join(DATA_DIR, "bot.lock")
DEPS_STAMP_FILE = os.path.join(DATA_DIR, "deps.stamp")

for _d in (DATA_DIR, ASSETS_DIR, LOG_DIR):
    os.makedirs(_d, exist_ok=True)

# ---------------------------------------------------------------- رفتار عمومی
APPS_PAGE_SIZE = 9
MAX_SEARCH_RESULTS = 300
SCREENSHOT_JPEG_QUALITY = 88

# مدت انتظار (ثانیه) برای اینکه یک برنامه «کامل باز شود»
APP_OPEN_TIMEOUT = 45
APP_STABLE_SECONDS = 3.0

# اگر True باشد، وقتی نشانگر ماوس در گوشه‌ی صفحه باشد اتوماسیون متوقف می‌شود.
# پیش‌فرض False است چون در حالت ریموت، ماوس فیزیکی بی‌حرکت در گوشه می‌ماند و
# این محافظ باعث قطع اشتباه کار می‌شد.
AUTOMATION_FAILSAFE = False

# ---------------------------------------------------------------- کنترل ماوس از راه دور
# گام‌های حرکت ماوس به پیکسل. دکمه‌ی «گام» بین همین اعداد جابه‌جا می‌شود؛
# عدد کوچک برای جای دقیق (مثلاً لبه‌ی یک متن) و عدد بزرگ برای جابه‌جایی سریع.
MOUSE_STEPS = (10, 25, 50, 100, 250)
MOUSE_STEP_DEFAULT = 50

# مکث (ثانیه) بعد از هر کلیک/کلید تا پنجره‌ی مقصد فرصت واکنش داشته باشد.
MOUSE_ACTION_PAUSE = 0.15

# ---------------------------------------------------------------- OpenCode
# عنوان پنجره‌ی OpenCode (برای پیدا کردنش بین چند پنجره)
OPENCODE_WINDOW_TITLES = ["opencode", "OpenCode"]

# دستور باز کردن OpenCode اگر پنجره‌اش باز نبود (به‌صورت fallback)
OPENCODE_LAUNCH_COMMAND = ["opencode", "tui"]

# جای کادر نوشتن پیام در پنجره‌ی OpenCode دسکتاپ.
# نسبت‌ها نسبت به اندازه‌ی پنجره‌ی maximize هستند:
#   left   = 0.19  (چپ کادر)
#   top    = 0.70  (بالای کادر)
#   right  = 0.81  (راست کادر)
#   bottom = 0.92  (پایین کادر)
# نکته: کلیک روی «مرکز» این ناحیه انجام می‌شود، نه گوشه‌ی بالا-چپ.
OPENCODE_INPUT_REGION = (0.19, 0.70, 0.81, 0.92)

# نقطه‌های جایگزین برای کلیک، به‌صورت نسبت از اندازه‌ی پنجره.
# اگر چسباندن در نقطه‌ی اول جواب نداد، این‌ها به‌ترتیب امتحان می‌شوند.
# تنوع عمدی است: بعضی حالت‌های رابط کادر را جابه‌جا می‌کنند
# (مثلاً وقتی پنل کناری باز و بسته می‌شود).
OPENCODE_INPUT_FALLBACKS = [
    (0.50, 0.86),
    (0.50, 0.75),
    (0.30, 0.80),
    (0.70, 0.80),
]

# تشخیص اینکه متن واقعاً داخل کادر نشست.
#
# معیار «تعداد پیکسل تغییرکرده» است، نه نسبت: نسبت به اندازه‌ی ناحیه وابسته
# است و نوشتن چند کلمه در ناحیه‌ی بزرگ زیر آستانه می‌ماند.
# ۲۵۰ پیکسل تقریباً معادل یک تا دو کلمه است؛ کمتر از آن یعنی فقط پرش
# نور یا نشانگر که مهم نیست.
OPENCODE_PASTE_MIN_PIXELS = 250

# تشخیص خالی شدن کادر بعد از Enter (متن بیرون می‌رود و صفحه بالا می‌رود)
OPENCODE_CLEAR_MIN_PIXELS = 400

# آستانه‌ی تشخیص «پنجره ثابت شده» (۰ تا ۱)؛ هرچه کمتر، حساس‌تر
OPENCODE_STABILITY_THRESHOLD = 0.004
# چند ثانیه ثبات بدون تغییر = احتمالاً کار تمام شده
OPENCODE_IDLE_COMPLETE_SECONDS = 18
# اگر این‌قدر طول کشید و صفحه ثابت بود، ممکنه است منتظر تأیید باشد
OPENCODE_STALL_SECONDS = 25
# سقف زمان کل (۳۰ دقیقه)
OPENCODE_TIMEOUT = 30 * 60
# آیا بعد از پایان، چند اسکرین‌شات دیگر هم بفرستد؟ (اسکرول به پایین)
OPENCODE_SEND_MORE_SHOTS = 3
# فاصله‌ی زمانی بین دو بار بررسی وضعیت
OPENCODE_POLL_INTERVAL = 3.0

# نام فایل قالب‌های تصویری در پوشه‌ی assets (اختیاری).
# اگر فایل وجود نداشته باشد، آن روش تشخیص رد می‌شود و به روش بعدی می‌رود
# (یعنی تشخیص پایان کار فقط با ثابت ماندن صفحه انجام می‌شود).
# برای ساختنشان ابزار capture_templates.py را یک بار اجرا کنید.
OPENCODE_TEMPLATE_SEND = "opencode_send.png"
OPENCODE_TEMPLATE_STOP = "opencode_stop.png"
OPENCODE_TEMPLATE_APPROVE = "opencode_approve.png"
OPENCODE_TEMPLATE_INPUT = "opencode_input.png"

# جای قالب‌ها روی صفحه، به‌صورت نسبت از کل صفحه (نسبت به جایی که
# پنجره‌ی OpenCode معمولاً maximize است). برای هر کدام چهار عدد:
# (چپ، بالا، راست، پایین) — اگر None باشد، آن قالب ساخته نمی‌شود.
# برای پیدا کردن این اعداد، ابزار capture_templates.py عکس کل صفحه را
# در پوشه‌ی data می‌گذارد و مختصات را با اسکرین‌شات چاپ می‌کند.
OPENCODE_TEMPLATE_CAPTURE = {
    "opencode_stop": (0.72, 0.93, 0.77, 0.965),
    "opencode_send": (0.72, 0.93, 0.77, 0.965),
}

# کمترین انحراف معیاری که یک قالب باید داشته باشد تا قابل تشخیص باشد.
# قالب یکنواخت (مثل یک مستطیل تک‌رنگ) با هر ناحیه‌ی یکنواخت دیگری هم
# مطابقت کامل نشان می‌دهد و نتیجه، تشخیص اشتباه است.
# اگر قالبی این حد را نداشته باشد، نادیده گرفته می‌شود و بات به‌طور خودکار
# از روش «ثابت ماندن صفحه» استفاده می‌کند.
MIN_TEMPLATE_STD = 8.0

# ---------------------------------------------------------------- شبکه
# پیش‌فرض کتابخانه‌ی تلگرام فقط ۵ ثانیه برای خواندن پاسخ وقت می‌دهد.
# ارسال فایل PNG یک مگابایتی روی اینترنت کند یا با VPN به‌راحتی از این
# زمان رد می‌شود و خطای «Timed out» می‌دهد. این مقادیر عمداً بزرگ‌اند.
TELEGRAM_CONNECT_TIMEOUT = 45.0
TELEGRAM_READ_TIMEOUT = 180.0
TELEGRAM_WRITE_TIMEOUT = 180.0
TELEGRAM_POOL_TIMEOUT = 45.0
# چون چند کار هم‌زمان انجام می‌شود (اسکرین‌شات و اتوماسیون)، استخر اتصال
# باید بزرگ‌تر از حالت پیش‌فرضِ تک‌اتصال باشد.
TELEGRAM_POOL_SIZE = 8

# بیشترین حجم هر اسکرین‌شات (بایت). فایل خیلی بزرگ کند می‌شود و احتمال
# تایم‌اوت بالا می‌رود، پس اگر از این حد بزرگ‌تر شد به JPEG کیفیت پایین‌تر
# فرستاده می‌شود.
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024

# ---------------------------------------------------------------- خاموشی
SHUTDOWN_DELAY_SECONDS = 60
SHUTDOWN_MESSAGE = "خاموش کردن لپ‌تاپ توسط ربات — برای لغو از دکمه‌ی زیر استفاده کنید."

# ---------------------------------------------------------------- سرویس‌های AI
# کلیدها فقط از متغیر محیطی خوانده می‌شوند، نه از داخل کد.
# نمونه‌ی تنظیم در ویندوز (PowerShell):
#   [Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "sk-...", "User")
#
# هر سرویس فقط به سه چیز نیاز دارد: base_url و model و نام متغیر محیطی کلید.
PROMPT_SYSTEM = (
    "تو یک مهندس پرامپت حرفه‌ای هستی. متن خام کاربر را به یک پرامپت کامل، "
    "دقیق و ساختاریافته تبدیل کن. پرامپت خروجی باید شامل این بخش‌ها باشد: "
    "نقش و زمینه، هدف دقیق، محدودیت‌ها، مراحل انجام کار، خروجی مورد انتظار، "
    "و معیارهای پذیرش. فقط خودِ پرامپت نهایی را برگردان، بدون توضیح اضافه. "
    "اگر متن کاربر فارسی است، پرامپت را فارسی بنویس."
)

PROMPT_USER_TEMPLATE = "متن خام کاربر:\n\"\"\"\n{raw}\n\"\"\"\n\nپرامپت نهایی را بنویس."

# فهرست سرویس‌ها. اگر اولی خطا بدهد یا تایم‌اوت شود، خودکار به بعدی می‌رود.
# "auth" تعیین می‌کند هدر Authorization چطور ساخته شود.
#   "bearer" -> Authorization: Bearer <key>
#   "none"   -> بدون هدر (برای Ollama محلی)
AI_PROVIDERS = [
    {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
        "auth": "bearer",
        "timeout": 60,
    },
    {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "api_key_env": "GROQ_API_KEY",
        "auth": "bearer",
        "timeout": 45,
    },
    {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
        "api_key_env": "OPENROUTER_API_KEY",
        "auth": "bearer",
        "timeout": 60,
    },
    {
        "name": "Ollama (محلی)",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "llama3.1",
        "api_key_env": "",
        "auth": "none",
        "timeout": 90,
    },
]

# نام سرویسی که در شروع به‌صورت پیش‌فرض انتخاب می‌شود
DEFAULT_PROVIDER = "OpenAI"