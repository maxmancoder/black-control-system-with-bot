# -*- coding: utf-8 -*-
"""
ساخت پرامپت با مدل زبانی.

از رابط سازگار با OpenAI استفاده می‌شود، پس تقریباً همه‌ی سرویس‌ها
(و مدل‌های محلی مثل Ollama) پشتیبانی می‌شوند. عوض کردن سرویس فقط یعنی
عوض کردن آدرس، مدل و نام متغیر محیطی کلید.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional, Tuple

import httpx

import config
from core import secrets

logger = logging.getLogger(__name__)

_settings_cache: Optional[Dict[str, str]] = None


def _load_settings() -> Dict[str, str]:
    """تنظیمات پایدار مثل سرویس انتخاب‌شده."""
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache
    data: Dict[str, str] = {"provider": config.DEFAULT_PROVIDER}
    if os.path.exists(config.SETTINGS_FILE):
        try:
            with open(config.SETTINGS_FILE, "r", encoding="utf-8") as fh:
                import json

                data.update(json.load(fh))
        except Exception:
            logger.exception("خواندن تنظیمات ناموفق بود")
    _settings_cache = data
    return data


def _save_settings(data: Dict[str, str]) -> None:
    global _settings_cache
    _settings_cache = data
    try:
        import json

        tmp = config.SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, config.SETTINGS_FILE)
    except Exception:
        logger.exception("ذخیره‌ی تنظیمات ناموفق بود")


def available_providers() -> List[Dict[str, str]]:
    return list(config.AI_PROVIDERS)


def active_provider() -> Dict[str, str]:
    name = _load_settings().get("provider", config.DEFAULT_PROVIDER)
    for p in config.AI_PROVIDERS:
        if p["name"].lower() == name.lower():
            return p
    return config.AI_PROVIDERS[0]


def set_provider(name: str) -> bool:
    for p in config.AI_PROVIDERS:
        if p["name"].lower() == name.lower():
            data = _load_settings()
            data["provider"] = p["name"]
            _save_settings(data)
            return True
    return False


def api_key(provider: Dict[str, str]) -> str:
    """
    کلید یک سرویس.

    اول متغیر محیطی، بعد کلیدی که از داخل تلگرام ذخیره شده.
    """
    env_name = provider.get("api_key_env") or ""
    if env_name:
        from_env = os.environ.get(env_name, "").strip()
        if from_env:
            return from_env
    return secrets.get_key(provider.get("name", ""))


def key_source(provider: Dict[str, str]) -> str:
    """مشخص می‌کند کلید از کجا آمده: env / ذخیره‌شده / هیچ‌کدام."""
    env_name = provider.get("api_key_env") or ""
    if env_name and os.environ.get(env_name, "").strip():
        return "env"
    if secrets.get_key(provider.get("name", "")):
        return "saved"
    return "none"


def provider_status() -> List[Tuple[str, str, str]]:
    """نام سرویس، منبع کلید، و توضیح کوتاه."""
    out = []
    for p in config.AI_PROVIDERS:
        src = key_source(p)
        if src == "env":
            note = f"کلید از متغیر محیطی {p['api_key_env']}"
        elif src == "saved":
            note = f"کلید ذخیره‌شده {secrets.masked(api_key(p))}"
        elif not (p.get("api_key_env") or ""):
            note = "بدون کلید (سرویس محلی)"
        else:
            note = "کلید ثبت نشده"
        out.append((p["name"], src, note))
    return out


async def _call_provider(provider: Dict[str, str], raw: str) -> str:
    base = provider["base_url"].rstrip("/")
    url = base if base.endswith("/completions") else base + "/chat/completions"

    headers = {"Content-Type": "application/json"}
    if provider.get("auth", "bearer") == "bearer":
        key = api_key(provider)
        if key:
            headers["Authorization"] = f"Bearer {key}"

    payload = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": config.PROMPT_SYSTEM},
            {"role": "user", "content": config.PROMPT_USER_TEMPLATE.format(raw=raw)},
        ],
        "temperature": 0.4,
    }

    timeout = httpx.Timeout(float(provider.get("timeout", 60)))
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"HTTP {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json()

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("پاسخ نامعتبر از سرویس") from exc

    return (content or "").strip()


async def test_connection(provider: Dict[str, str]) -> Tuple[bool, str]:
    """
    یک درخواست کوچک می‌فرستد تا اعتبار کلید و آدرس سرویس بررسی شود.

    خروجی: (موفق؟, توضیح)
    """
    if provider.get("auth", "bearer") == "bearer" and not api_key(provider):
        return False, "کلیدی برای این سرویس ثبت نشده است."
    try:
        text = await _call_provider(provider, "سلام")
        if text:
            return True, "اتصال برقرار است."
        return False, "پاسخ خالی برگشت."
    except Exception as exc:
        return False, str(exc)[:300]


async def build_prompt(raw: str) -> Tuple[bool, str, str]:
    """
    متن خام را به پرامپت ساختاریافته تبدیل می‌کند.

    خروجی: (موفق؟, متن نتیجه، نام سرویسی که جواب داد)
    اگر سرویس فعال خطا بدهد، خودکار سرویس بعدی امتحان می‌شود.
    """
    if not raw.strip():
        return False, "متن خام خالی است.", ""

    ordered = list(config.AI_PROVIDERS)
    act = active_provider()
    # سرویس انتخابی کاربر اول امتحان می‌شود
    ordered.sort(key=lambda p: 0 if p["name"] == act["name"] else 1)

    errors: List[str] = []
    for provider in ordered:
        # سرویس‌های محلی مثل Ollama کلید نمی‌خواهند
        needs_key = provider.get("auth", "bearer") == "bearer"
        if needs_key and not api_key(provider):
            errors.append(f"{provider['name']}: کلید API ثبت نشده")
            continue
        try:
            logger.info("درخواست ساخت پرامپت به %s", provider["name"])
            text = await _call_provider(provider, raw)
            if not text:
                errors.append(f"{provider['name']}: پاسخ خالی")
                continue
            set_provider(provider["name"])
            return True, text, provider["name"]
        except Exception as exc:
            logger.warning("سرویس %s ناموفق بود: %s", provider["name"], exc)
            errors.append(f"{provider['name']}: {exc}")

    detail = "\n".join(f"• {e}" for e in errors)
    return False, f"ساخت پرامپت ممکن نشد.\n\n{detail}", ""


def save_prompt(text: str) -> None:
    try:
        with open(config.PROMPT_FILE, "w", encoding="utf-8") as fh:
            fh.write(text)
    except Exception:
        logger.exception("ذخیره‌ی پرامپت ناموفق بود")


def load_prompt() -> str:
    if not os.path.exists(config.PROMPT_FILE):
        return ""
    try:
        with open(config.PROMPT_FILE, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except Exception:
        return ""


def reset_cache() -> None:
    global _settings_cache
    _settings_cache = None