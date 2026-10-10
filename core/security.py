# -*- coding: utf-8 -*-
"""لایه‌ی امنیت: فقط مالک اجازه‌ی کنترل سیستم را دارد."""

import functools
import logging

import telegram
from telegram.ext import ContextTypes

import config

logger = logging.getLogger(__name__)

# چند بار متوالی رد شدن یک کاربر غریبه، قبل از اینکه بی‌خیالش شویم
_abuse_counter = {}


def is_owner(user) -> bool:
    if user is None:
        return False
    try:
        return int(user.id) in config.OWNER_IDS
    except (TypeError, ValueError):
        return False


def note_rejection(update) -> None:
    """تلاش دسترسی غیرمجاز را لاگ می‌کند (با محدودیت نرخ)."""
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return
    key = (chat.id, user.id)
    count = _abuse_counter.get(key, 0) + 1
    _abuse_counter[key] = count
    if count > 10:
        return
    username = user.username or "-"
    logger.warning(
        "دسترسی رد شد | user_id=%s username=%s chat=%s",
        user.id, username, chat.id,
    )


def owner_only(func):
    """دکوراتور: هندلر فقط برای مالک و فقط در چت خصوصی اجرا می‌شود."""

    @functools.wraps(func)
    async def wrapper(update: telegram.Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        chat = update.effective_chat

        if not is_owner(user):
            note_rejection(update)
            # عمداً هیچ پاسخی به غریبه داده نمی‌شود.
            return None

        if config.PRIVATE_CHAT_ONLY and chat is not None and chat.type != telegram.Chat.PRIVATE:
            note_rejection(update)
            return None

        return await func(update, context, *args, **kwargs)

    return wrapper