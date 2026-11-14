# -*- coding: utf-8 -*-
"""گرفتن اسکرین‌شات و تطبیق تصویر."""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import sys
import time
from typing import Optional, Sequence, Tuple

import pyautogui
from PIL import Image

import config

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# خاموش کردن لاگر داخلی OpenCV
#
# هشدارهایی مثل «VIDEOIO(MSMF): backend can't be used to capture by index»،
# «Camera index out of range» و «OpenCV should be configured with libavdevice»
# مستقیماً از کد C++ به stderr چاپ می‌شوند (نه از لاگر پایتون) و با
# logging.getLogger یا OPENCV_LOG_LEVEL خاموش نمی‌شوند. چون موقع شناسایی
# دوربین عمداً ایندکس‌های ناموجود را هم امتحان می‌کنیم، این هشدارها طبیعی‌اند
# و فقط کنسول را شلوغ می‌کنند.
#
# راه‌حل: سطح لاگر OpenCV روی SILENT گذاشته می‌شود و بعد از هر ساخت
# VideoCapture دوباره اعمال می‌شود (به کمک _silence_cv_log).

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


_camera_settings_cache: Optional[dict] = None


def _load_camera_settings() -> dict:
    """تنظیمات دوربین (index انتخاب‌شده) از settings.json."""
    global _camera_settings_cache
    if _camera_settings_cache is not None:
        return _camera_settings_cache
    data: dict = {"camera_index": 0}
    if os.path.exists(config.SETTINGS_FILE):
        try:
            import json
            with open(config.SETTINGS_FILE, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            if "camera_index" in saved:
                data["camera_index"] = int(saved["camera_index"])
        except Exception:
            logger.exception("خواندن تنظیمات دوربین ناموفق بود")
    _camera_settings_cache = data
    return data


def _save_camera_settings(data: dict) -> None:
    global _camera_settings_cache
    _camera_settings_cache = data
    try:
        import json
        # settings.json مشترک با ai.py است؛ فقط کلید camera_index را
        # به‌روزرسانی می‌کنیم و بقیه کلیدها (مثل provider) دست‌نخورده می‌مانند
        full: dict = {}
        if os.path.exists(config.SETTINGS_FILE):
            try:
                with open(config.SETTINGS_FILE, "r", encoding="utf-8") as fh:
                    full = json.load(fh)
            except Exception:
                full = {}
        full.update(data)
        tmp = config.SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(full, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, config.SETTINGS_FILE)
    except Exception:
        logger.exception("ذخیره‌ی تنظیمات دوربین ناموفق بود")


def saved_camera_index() -> int:
    """ایندکس دوربین ذخیره‌شده توسط کاربر (پیش‌فرض: 0)."""
    return _load_camera_settings().get("camera_index", 0)


def set_camera_index(index: int) -> None:
    """ایندکس دوربین را ذخیره می‌کند تا /camera همیشه از همان استفاده کند."""
    data = _load_camera_settings()
    data["camera_index"] = int(index)
    _save_camera_settings(data)


def _silence_cv_log() -> None:
    """
    سطح لاگر داخلی OpenCV را روی SILENT می‌گذارد.

    OpenCV backendها را lazy بارگذاری می‌کند و هر backend موقع لود شدن
    سطح لاگ را به مقدار پیش‌فرض برمی‌گرداند؛ برای همین باید بعد از ساخت
    هر VideoCapture دوباره اعمال شود.
    """
    if not _HAS_CV2:
        return
    with contextlib.suppress(Exception):
        log = cv2.utils.logging
        level = getattr(log, "LOG_LEVEL_SILENT", None)
        log.setLogLevel(level if level is not None else 0)


# FFmpeg را از فهرست backendهای قابل انتخاب حذف می‌کنیم.
#
# نسخه‌ی prebuilt از OpenCV بدون libavdevice ساخته شده، پس FFmpeg هیچ‌وقت
# نمی‌تواند دوربین را باز کند؛ ولی هر بار که VideoCapture ساخته می‌شود
# امتحانش می‌کند و یک خط هشدار روی stderr می‌نویسد:
#   «VIDEOIO/FFMPEG: OpenCV should be configured with libavdevice»
#
# این خط فقط سروصداست و ربطی به کار ما ندارد. با حذف کردن FFmpeg از فهرست،
# OpenCV مستقیم سراغ backend بومی ویندوز (MSMF/obsensor) می‌رود که همان
# چیزی است که کار می‌کند، و هشدار هم دیگر چاپ نمی‌شود.
def _disable_ffmpeg_backend() -> None:
    if not _HAS_CV2:
        return
    with contextlib.suppress(Exception):
        cv2.setVideoCapturePriority(0, 2)  # 2 = CAP_FFMPEG
    # روش جایگزین برای نسخه‌های قدیمی‌تر که این API را ندارند
    with contextlib.suppress(Exception):
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_SILENT)


class _quiet_stderr:
    """
    موقتاً stderr واقعی (file descriptor) را به devnull وصل می‌کند.

    چرا این‌قدر پایین؟ هشدارهای OpenCV از کد C++ نوشته می‌شوند و از
    «sys.stderr» پایتون عبور نمی‌کنند؛ تنها راه ساکت‌کردنشان دستکاری
    خود file descriptor است.

    با تعویض sys.stderr این کار انجام نمی‌شود، چون DLL نوشتن را مستقیم
    روی handle 2 انجام می‌دهد. این کلاس با os.dup2 کار می‌کند و در
    finally مسیر قبلی را برمی‌گرداند، پس هیچ پیام واقعی‌ای گم نمی‌شود.
    """

    def __enter__(self) -> None:
        self._saved_fd = -1
        self._null_fd = -1
        if not _HAS_CV2:
            return
        stderr = sys.stderr
        # اگر stderr به جایی وصل نباشد (مثلاً pytest capture) دست نمی‌زنیم
        try:
            fd = stderr.fileno()
        except Exception:
            return
        if not fd or fd < 2:
            return
        try:
            self._saved_fd = os.dup(fd)
            self._null_fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(self._null_fd, fd)
        except Exception:
            self._cleanup()

    def _cleanup(self) -> None:
        for attr in ("_saved_fd", "_null_fd"):
            fd = getattr(self, attr, -1)
            if fd is not None and fd >= 0:
                with contextlib.suppress(Exception):
                    os.close(fd)
                setattr(self, attr, -1)

    def __exit__(self, *exc) -> None:
        stderr = sys.stderr
        if self._saved_fd is not None and self._saved_fd >= 0:
            with contextlib.suppress(Exception):
                fd = stderr.fileno()
                os.dup2(self._saved_fd, fd if (fd and fd >= 2) else 2)
        self._cleanup()


# حذف FFmpeg و خاموش کردن لاگر در همان لحظه‌ی لود شدن ماژول، تا پیش از
# اولین استفاده اعمال شده باشند.
_disable_ffmpeg_backend()
_silence_cv_log()


def _open_camera(camera_index: int):
    """
    دوربین را باز می‌کند و VideoCapture برگشتی را می‌دهد (یا None).

    فقط backend پیش‌فرض OpenCV استفاده می‌شود، چون روی این سیستم
    backendهای صریح کار نمی‌کنند:

      * CAP_DSHOW  → «backend is generally available but can't be used
        to capture by index»
      * CAP_MSMF   → همان پیام

    backend پیش‌فرض (بدون آرگومان دوم) هر دو دوربین را درست باز می‌کند.

    ساخت VideoCapture داخل _quiet_stderr انجام می‌شود چون OpenCV موقع
    بارگذاری lazy هر backend (از جمله FFmpeg که در این نسخه libavdevice
    ندارد) پیام هشدار روی stderr می‌نویسد که ربطی به کار ما ندارد.
    """
    try:
        with _quiet_stderr():
            cap = cv2.VideoCapture(camera_index)
    except Exception:
        return None
    _silence_cv_log()
    if cap.isOpened():
        return cap
    with contextlib.suppress(Exception):
        cap.release()
    return None


def detect_cameras(max_index: int = 10) -> list:
    """
    همه‌ی دوربین‌های متصل به سیستم را پیدا می‌کند.

    ایندکس‌ها را از 0 به بالا امتحان می‌کند و به‌محض دیدن
    max_miss پشت‌سرهم ایندکس ناموجود متوقف می‌شود؛ دوربین‌های USB معمولاً
    ایندکس‌های پشت‌سرهم دارند، پس ادامه دادن بعد از چند misses پشت‌سرهم
    فقط هشدار اضافه تولید می‌کند و چیزی پیدا نمی‌کند.

    خروجی: لیست dict با کلیدهای index, width, height.
    """
    if not _HAS_CV2:
        raise RuntimeError("OpenCV (cv2) نصب نیست؛ دوربین در دسترس نیست.")
    found = []
    misses = 0
    for idx in range(max_index):
        cap = _open_camera(idx)
        if cap is None:
            misses += 1
            if misses >= 2:
                break
            continue
        try:
            ok, frame = cap.read()
            if not ok or frame is None:
                misses += 1
                if misses >= 2:
                    break
                continue
            misses = 0
            h, w = frame.shape[:2]
            found.append({"index": idx, "width": int(w), "height": int(h)})
        finally:
            with contextlib.suppress(Exception):
                cap.release()
    return found


async def detect_cameras_async(max_index: int = 10) -> list:
    return await asyncio.to_thread(detect_cameras, max_index)


def grab_camera_photo(camera_index: Optional[int] = None) -> Image.Image:
    """
    یک فریم از دوربین (webcam) لپ‌تاپ می‌گیرد و به PIL تبدیل می‌کند.

    اگر camera_index داده نشود، از ایندکس ذخیره‌شده توسط کاربر
    (در settings.json) استفاده می‌شود؛ پس /camera همیشه از همان
    دوربینی که کاربر انتخاب کرده عکس می‌گیرد.

    cv2.VideoCapture فریم‌های اول را معمولاً تار یا خیلی تاریک برمی‌گرداند
    (نیاز به تنظیم نوردهی دوربین)؛ به همین دلیل چند فریم اول را دور
    می‌ریزیم و از فریم‌های بعدی استفاده می‌کنیم.
    """
    if not _HAS_CV2:
        raise RuntimeError("OpenCV (cv2) نصب نیست؛ دوربین در دسترس نیست.")
    if camera_index is None:
        camera_index = saved_camera_index()
    cap = _open_camera(camera_index)
    if cap is None:
        raise RuntimeError(
            f"دوربین (index={camera_index}) باز نشد؛ "
            "شاید برنامه‌ی دیگری از آن استفاده می‌کند یا دوربین قطع است. "
            "با دکمه‌ی «تنظیم دوربین» دوباره دوربین‌ها را شناسایی کن."
        )
    try:
        # چند فریم اول را دور بریز تا نوردهی دوربین تنظیم شود
        for _ in range(5):
            cap.read()
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError("دوربین باز شد ولی فریمی برنگرداند.")
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)
    finally:
        cap.release()


async def grab_camera_photo_async(camera_index: Optional[int] = None) -> Image.Image:
    return await asyncio.to_thread(grab_camera_photo, camera_index)


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