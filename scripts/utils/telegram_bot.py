"""Shared Telegram Bot API wrapper (raw HTTPS via requests).

Extracted from scripts/fireside-bot.py (which implemented this correctly and
production-hardened it against real Telegram error responses) so it can be
reused by any bot-backed sender in the workspace - Fireside itself, and the
dedicated system-notifications bot in scripts/utils/telegram_notify.py.

No python-telegram-bot dependency. All API errors are redacted (bot token
never appears in a raised message or log line) and re-raised as
TelegramAPIError so callers can inspect status_code. Error reporting is
generalized via an injectable on_error callback: pass one to route errors
into a caller-specific log (Fireside's own errors.log); omit it to fall back
to the standard logging module.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, Optional

import requests

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramAPIError(Exception):
    """Raised when the Telegram Bot API returns a failure.

    The bot token is always redacted from the message before raising.
    """

    def __init__(self, message: str, status_code: Optional[int] = None,
                 telegram_description: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.telegram_description = telegram_description


class TelegramBot:
    """Thin wrapper around the Telegram Bot API.

    Uses raw HTTPS via requests - no python-telegram-bot dependency.
    All API errors are reported via on_error() (or the module logger if
    on_error is not supplied) with the bot token redacted, then re-raised
    as TelegramAPIError so callers can inspect status_code.
    """

    def __init__(self, token: str, *, on_error: Optional[Callable[[str], None]] = None):
        if not token:
            raise ValueError("TelegramBot requires a non-empty token")
        self.token = token
        self.base = f"{TELEGRAM_API_BASE}/bot{token}"
        self._on_error = on_error

    def _redact(self, message: str) -> str:
        """Redact the bot token from any string before logging or raising."""
        return message.replace(self.token, "<REDACTED_TOKEN>") if self.token else message

    def _log_error(self, message: str) -> None:
        """Report an error via the injected callback, or the module logger."""
        if self._on_error is not None:
            self._on_error(message)
        else:
            logging.getLogger("telegram_bot").error(message)

    def _call(self, method: str, _timeout: int = 30, **params) -> Any:
        """Make a Telegram Bot API call. Returns the 'result' field on success.

        All errors have the bot token redacted before logging or raising,
        so transcripts and error logs cannot leak credentials.

        Raises:
            TelegramAPIError on any failure (transport, JSON, or ok=false)
        """
        url = f"{self.base}/{method}"
        try:
            r = requests.post(url, json=params, timeout=_timeout)
        except (requests.ConnectionError, requests.Timeout) as e:
            msg = self._redact(f"Telegram {method} transport failure: {e}")
            self._log_error(msg)
            raise TelegramAPIError(msg, status_code=None) from None

        # Capture response details before raising, in redacted form
        status = r.status_code
        try:
            data = r.json()
        except json.JSONDecodeError:
            text = r.text[:300] if r.text else "<empty body>"
            msg = self._redact(
                f"Telegram {method} returned non-JSON (HTTP {status}): {text!r}"
            )
            self._log_error(msg)
            raise TelegramAPIError(msg, status_code=status) from None

        if not r.ok or not data.get("ok"):
            description = data.get("description", "<no description>")
            telegram_code = data.get("error_code")
            hint = self._hint_for_status(method, status, description)
            msg = self._redact(
                f"Telegram {method} failed (HTTP {status}, telegram_code={telegram_code}): "
                f"{description}{hint}"
            )
            self._log_error(msg)
            raise TelegramAPIError(msg, status_code=status, telegram_description=description)

        return data.get("result")

    @staticmethod
    def _hint_for_status(method: str, status: int, description: str) -> str:
        """Return a helpful one-line hint for common error patterns."""
        desc_lower = (description or "").lower()
        if method == "sendMessage" and "chat not found" in desc_lower:
            # Telegram returns 400 "chat not found" when DMing a user who has never
            # /started the bot. Same semantic as 403; the user_id is fine but no
            # private chat exists yet.
            return (
                " | HINT: User has not /started this bot yet. "
                "Bots can DM users ONLY after the user sends /start once. "
                "Ask them to open @<bot_username> in Telegram and tap Start."
            )
        if status == 403 and method == "sendMessage":
            if "bot was blocked" in desc_lower:
                return " | HINT: User has blocked this bot. Cannot DM them."
            return (
                " | HINT: User has not /started this bot yet. "
                "Bots can DM users ONLY after the user sends /start to the bot."
            )
        if status == 400 and "chat not found" in desc_lower:
            return " | HINT: User has not /started this bot yet."  # for non-sendMessage methods
        if status == 401:
            return " | HINT: Bot token is invalid or revoked; check your bot token in .env."
        if status == 429:
            return " | HINT: Rate-limited by Telegram; back off and retry."
        return ""

    def get_me(self) -> dict:
        """Return the bot's own user record. Quick auth check."""
        return self._call("getMe")

    def send_message(self, chat_id, text: str, parse_mode: str = "Markdown",
                     disable_web_page_preview: bool = True,
                     reply_to_message_id: Optional[int] = None,
                     reply_markup: Optional[dict] = None) -> dict:
        """Send a message to a chat. chat_id is integer (user/group id) or '@channel' string.

        reply_markup is an optional Telegram InlineKeyboardMarkup dict, e.g.
        {"inline_keyboard": [[{"text": "Yes", "callback_data": "x"}]]}.
        """
        params = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        # Omit parse_mode when falsy (None / "") so the text is sent as plain
        # text. Passing it through would make Telegram parse Markdown entities
        # and reject any message with an unbalanced _ or * (e.g. a file path
        # like 2026-07-17_odin-reflect-proposal.md) with HTTP 400.
        if parse_mode:
            params["parse_mode"] = parse_mode
        if reply_to_message_id is not None:
            params["reply_to_message_id"] = reply_to_message_id
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        return self._call("sendMessage", **params)

    def send_dm(self, user_id: int, text: str, parse_mode: str = "Markdown") -> dict:
        """Send a private message to a user.

        user_id MUST be the integer Telegram user_id captured from a prior
        /start interaction or Telethon enumeration. Bot API does NOT resolve
        @username strings to user_ids for private chats - only for channels.
        """
        if not isinstance(user_id, int):
            raise TypeError(
                f"send_dm requires integer user_id (got {type(user_id).__name__}). "
                f"Bot API cannot resolve usernames to private user_ids - the user must "
                f"have /started the bot first, OR the user_id must be captured via Telethon."
            )
        return self.send_message(user_id, text, parse_mode=parse_mode)

    def get_updates(self, offset: int = 0, timeout: int = 25, limit: int = 100,
                    allowed_updates: Optional[list] = None) -> list:
        """Long-poll for updates.

        allowed_updates defaults to message + reactions + chat_member events
        (these four are NOT in Telegram's default set and must be requested
        explicitly to receive them).
        """
        if allowed_updates is None:
            allowed_updates = [
                "message",
                "message_reaction",
                "message_reaction_count",
                "chat_member",
                "my_chat_member",
                "callback_query",
            ]
        # _timeout for the HTTP layer is timeout + 5s buffer for long-poll
        return self._call(
            "getUpdates",
            _timeout=timeout + 5,
            offset=offset,
            timeout=timeout,
            limit=limit,
            allowed_updates=allowed_updates,
        )

    def pin_chat_message(self, chat_id, message_id: int,
                         disable_notification: bool = True) -> bool:
        return self._call(
            "pinChatMessage",
            chat_id=chat_id,
            message_id=message_id,
            disable_notification=disable_notification,
        )

    def unpin_chat_message(self, chat_id, message_id: int) -> bool:
        return self._call("unpinChatMessage", chat_id=chat_id, message_id=message_id)

    def edit_message_text(self, chat_id, message_id: int, text: str,
                          parse_mode: str = "Markdown",
                          disable_web_page_preview: bool = True,
                          reply_markup: Optional[dict] = None) -> dict:
        params = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        return self._call("editMessageText", **params)

    def edit_message_reply_markup(self, chat_id, message_id: int,
                                  reply_markup: Optional[dict] = None) -> dict:
        """Edit only the reply_markup of a message. Pass reply_markup=None to remove buttons."""
        params = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        return self._call("editMessageReplyMarkup", **params)

    def answer_callback_query(self, callback_query_id: str,
                              text: Optional[str] = None,
                              show_alert: bool = False) -> bool:
        """Dismiss the loading spinner on a tapped inline button.

        Telegram requires this call within ~15s of a callback_query, otherwise
        the user's button stays in a loading state. Pass `text` to flash a
        short toast (max 200 chars).
        """
        params = {"callback_query_id": callback_query_id, "show_alert": show_alert}
        if text is not None:
            params["text"] = text[:200]
        return self._call("answerCallbackQuery", **params)
