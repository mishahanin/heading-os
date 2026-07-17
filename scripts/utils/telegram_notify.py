"""Single entry point for HEADING OS system notifications: notify(target, message).

Sends via the dedicated notifications bot (TELEGRAM_NOTIFY_BOT_TOKEN), not the
userbot client - a Bot API sendMessage always push-notifies reliably, unlike a
message the userbot sends to a chat/channel it already owns.

notify() NEVER raises. It degrades to False (with a clear, distinct log hint)
on: a missing token, an empty/falsy target, or Telegram's own "me"/"self"/
"saved" (Saved Messages) sentinels - which have no Bot API equivalent, since a
bot cannot resolve its caller's own account. This is a hard system invariant:
nothing in HEADING OS may ever send a notification to Saved Messages, so this
rejection is deliberate and permanent, not a placeholder for a future fix.

Usage::

    from scripts.utils import telegram_notify
    ok = telegram_notify.notify(os.environ.get("ODIN_CADENCE_TELEGRAM_TARGET", ""), "text")
"""
from __future__ import annotations

import logging
import os

from scripts.utils.paths import load_env
from scripts.utils.telegram_bot import TelegramAPIError, TelegramBot

logger = logging.getLogger("telegram_notify")

_UNRESOLVABLE_TARGETS = {"me", "self", "saved"}


def notify(target: str, message: str) -> bool:
    """Send a system notification via the dedicated notifications bot.

    Returns True on a clean send, False on any failure (missing token,
    unresolvable target, transport/API error). NEVER raises.
    """
    load_env()
    token = os.environ.get("TELEGRAM_NOTIFY_BOT_TOKEN")
    if not token:
        logger.warning(
            "telegram_notify: TELEGRAM_NOTIFY_BOT_TOKEN not set in .env - "
            "no notification sent. See docs/TELEGRAM-AND-ALERTS.md for one-time setup."
        )
        return False

    if not target or target.strip().lower() in _UNRESOLVABLE_TARGETS:
        logger.warning(
            "telegram_notify: target %r is not bot-resolvable (a bot cannot target "
            "Telegram's own-account sentinel 'me'/'self'/'saved') - no notification "
            "sent. Configure a real channel id/@username via the relevant "
            "*_TELEGRAM_TARGET env var.",
            target,
        )
        return False

    bot = TelegramBot(token)
    try:
        # Plain text (parse_mode=None): system notifications are literal lines
        # that routinely contain Markdown-special chars (_ in file paths,
        # access_count, etc.). Markdown parsing would 400 on an unbalanced token.
        bot.send_message(target, message, parse_mode=None, disable_web_page_preview=True)
    except TelegramAPIError as exc:
        logger.warning("telegram_notify: send failed: %s", exc)
        return False
    return True
