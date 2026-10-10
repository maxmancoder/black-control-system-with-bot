# -*- coding: utf-8 -*-
"""
ابزار ساخت قالب‌های تصویری برای تشخیص دقیق‌تر پایان کار OpenCode.

تشخیص پایان کار دو روش دارد:

  ۱) تطبیق تصویر دکمه‌ی «توقف»  — دقیق‌تر، ولی به قالب نیاز دارد
  ۲) ثابت ماندن ناحیه‌ی چت      — بدون قالب، ولی ضعیف‌تر

اگر قالب‌ها در پوشه‌ی assets نباشند، بات به‌طور خودکار از روش ۲ استفاده
می‌کند و هیچ چیز خراب نمی‌شود. برای فعال کردن روش ۱، این ابزار را یک بار
اجرا کنید.

راهنمای استفاده (روی خود لپ‌تاپ، نه از راه دور):
    1) OpenCode را باز کنید و صبر کنید تا کامل بالا بیاید.
    2) این اسکریپت را اجرا کنید.
    3) وقتی خواست، دکمه‌ی توقف (یا ارسال) را روی صفحه پیدا کنید و
       مختصات نسبی آن را در فایل config.py بگذارید و دوباره اجرا کنید.

اجرا:
    python capture_templates.py --check      # فقط عکس می‌گیرد و مختصات می‌دهد
    python capture_templates.py --build      # قالب‌ها را می‌سازد
"""

from __future__ import annotations

import argparse
import os
import sys

import config
from core import imaging, windows


def snapshot_opencode():
    """پنجره‌ی OpenCode را جلو می‌آورد و maximize می‌کند."""
    hwnd = windows.find_window(config.OPENCODE_WINDOW_TITLES)
    if not hwnd:
        return None
    if not windows.focus(hwnd):
        print("نتوانستم پنجره‌ی OpenCode را به جلو بیاورم.", file=sys.stderr)
        return None
    import time

    time.sleep(1.0)
    return hwnd


def save_reference(path: str) -> str:
    """یک عکس مرجع از کل صفحه می‌گیرد تا مختصات را از روی آن پیدا کنی."""
    img = imaging.grab_screen()
    img.save(path)
    return path


def report_position(img, box):
    """مختصات پیکسلی یک کادر نسبی را چاپ می‌کند."""
    w, h = img.size
    l, t, r, b = box
    px = (int(l * w), int(t * h), int(r * w), int(b * h))
    size = (px[2] - px[0], px[3] - px[1])
    print(f"  اندازه‌ی پیشنهادی: ({size[0]}x{size[1]})")
    return px


def cmd_check() -> int:
    print("=" * 64)
    print("  بررسی وضعیت پنجره‌ی OpenCode")
    print("=" * 64)

    handles = windows.window_handles()
    print(f"پنجره‌های باز: {len(handles)}")
    for w in windows.list_windows()[:12]:
        print(f"  - {w['title'][:52]}")

    hwnd = snapshot_opencode()
    if not hwnd:
        print(
            "\nپنجره‌ی OpenCode پیدا نشد.\n"
            "اول OpenCode را باز کنید و دوباره اجرا کنید.\n"
            "عنوان‌هایی که دنبالشان می‌گردد: "
            + ", ".join(config.OPENCODE_WINDOW_TITLES)
        )
        return 1

    rect = windows.window_rect(hwnd)
    print(f"\nپنجره‌ی OpenCode پیدا شد، مختصات: {rect}")

    img = imaging.grab_screen()
    ref = os.path.join(config.DATA_DIR, "reference_screen.png")
    save_reference(ref)
    print(f"عکس مرجع ذخیره شد: {ref}")
    print(f"اندازه‌ی کل صفحه: {img.size}")

    print("\nمختصات نسبی کادرهای پیشنهادی از فایل config.py:")
    for name, box in config.OPENCODE_TEMPLATE_CAPTURE.items():
        print(f"  {name}: {box}")
        report_position(img, box)

    print(
        "\nبرای ساخت قالب‌ها، مختصات نسبی دکمه‌ی توقف را در همین فایل"
        "\n(OPENCODE_TEMPLATE_CAPTURE) بگذارید و بعد اجرا کنید:"
        "\n    python capture_templates.py --build"
    )
    return 0


def cmd_build() -> int:
    print("=" * 64)
    print("  ساخت قالب‌های تصویری")
    print("=" * 64)

    hwnd = snapshot_opencode()
    if not hwnd:
        print(
            "\nپنجره‌ی OpenCode پیدا نشد. اول آن را باز کنید.",
            file=sys.stderr,
        )
        return 1

    img = imaging.grab_screen()
    save_reference(os.path.join(config.DATA_DIR, "reference_screen.png"))

    built = 0
    for name, box in config.OPENCODE_TEMPLATE_CAPTURE.items():
        if box is None:
            print(f"  رد شد: {name} (مختصات تعریف نشده)")
            continue
        crop = imaging.crop_by_ratio(img, box)
        if crop is None:
            print(f"  رد شد: {name} (کادر خالی یا نامعتبر)")
            continue
        path = os.path.join(config.ASSETS_DIR, f"{name}.png")
        crop.save(path)
        std = imaging._std_of(crop)
        built += 1
        warn = ""
        if std < config.MIN_TEMPLATE_STD:
            warn = (
                f"  ⚠️  این کادر بیش از حد یکنواخت است (انحراف معیار {std:.1f})"
                f"\n      و برای تشخیص تصویری قابل استفاده نیست."
                f"\n      کادر کوچک‌تر یا دقیق‌تری انتخاب کنید."
            )
        print(f"  ساخته شد: {path}  ({crop.size[0]}x{crop.size[1]})")
        print(warn)

    imaging._template_cache.clear()

    if built == 0:
        print("\nهیچ قالبی ساخته نشد.", file=sys.stderr)
        return 1

    print("\nحالا تست کنید که قالب‌ها روی صفحه پیدا می‌شوند یا نه:")
    print("    python capture_templates.py --verify")
    return 0


def cmd_verify() -> int:
    print("=" * 64)
    print("  بررسی اینکه قالب‌ها پیدا می‌شوند یا نه")
    print("=" * 64)

    img = imaging.grab_screen()
    ok_all = True
    for name in (
        config.OPENCODE_TEMPLATE_STOP,
        config.OPENCODE_TEMPLATE_SEND,
        config.OPENCODE_TEMPLATE_APPROVE,
    ):
        raw = imaging.load_template(name)
        path = os.path.join(config.ASSETS_DIR, name)
        state = (
            "موجود" if raw
            else ("بیش از حد یکنواخت" if os.path.exists(path) else "موجود نیست")
        )
        print(f"\n{name}: {state}")
        if not raw:
            if os.path.exists(path):
                print(
                    "  انحراف معیار این کادر خیلی کم است، پس با هر ناحیه‌ی"
                    "\n  یکنواخت دیگر هم مطابقت می‌کند و نتیجه‌اش غلط می‌شود."
                    "\n  کادر کوچک‌تر یا دقیق‌تری انتخاب کنید و دوباره --build بزنید."
                )
                ok_all = False
            continue
        found, box = imaging.find_template(img, name, threshold=0.80)
        if found:
            print(f"  ✅ پیدا شد، مختصات: {box}")
        else:
            print(
                "  ❌ پیدا نشد. یعنی دکمه در این حالتِ صفحه نیست یا مختصات"
                "\n     کادر اشتباه است. یک بار دیگر در همان حالتی که دکمه"
                "\n     دیده می‌شود اجرا کنید."
            )
            ok_all = False

    if not imaging.template_available(config.OPENCODE_TEMPLATE_STOP):
        print(
            "\nبدون قالب دکمه‌ی توقف، بات از روش «ثابت ماندن صفحه» استفاده"
            "\nمی‌کند که کار می‌کند ولی دقت کمتری دارد."
        )
    print()
    return 0 if ok_all else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="ساخت قالب تصویری OpenCode")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="عکس بگیر و مختصات بده")
    group.add_argument("--build", action="store_true", help="قالب‌ها را بساز")
    group.add_argument("--verify", action="store_true", help="بررسی پیدا شدن قالب‌ها")
    args = parser.parse_args()

    if args.check:
        return cmd_check()
    if args.build:
        return cmd_build()
    return cmd_verify()


if __name__ == "__main__":
    sys.exit(main())