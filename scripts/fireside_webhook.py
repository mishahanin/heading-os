"""FastAPI webhook receiver for the Tribe Fireside bot.

Replaces the 25-second long-poll architecture with real-time webhook delivery:
Telegram POSTs each update to /telegram-webhook the moment it arrives, the
handler routes it through fireside-bot.py's existing `_handle_update` logic,
and the response goes out within ~1 second instead of 5-30s.

The X-Telegram-Bot-Api-Secret-Token header is verified on every request
against the secret set in the matching setWebhook call. Telegram is the only
party that knows this secret, so the check rejects any other POSTer that
manages to discover the public URL.

Loaded by fireside-bot-daemon.py when FIRESIDE_WEBHOOK_ENABLED=true. The
poll cron job is skipped in that mode (Telegram does not allow both webhook
and getUpdates on the same bot).

Snake-case filename because this module is imported by the daemon.

Tests: tests/test_a_spawn_that_reported_a_daemon_it_never_confirmed.py
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import Any

# fastapi names are bound lazily (F-2.1: import stays pure so the module is
# collectable without the `dashboard` extra installed).
FastAPI = Header = HTTPException = Request = None


def _ensure_fastapi():
    global FastAPI, Header, HTTPException, Request
    if FastAPI is not None:
        return
    from scripts.utils.optdeps import require
    require("fastapi", extra="dashboard")
    from fastapi import FastAPI, Header, HTTPException, Request


def _dict_or_empty(value: Any) -> dict:
    """`value` when it is a mapping, `{}` otherwise. Never `or {}`.

    `or {}` converts only FALSY non-dicts, so a truthy one (`["x"]`, `"hi"`, a
    number) survives the guard and reaches `.get`. The webhook's logging
    preview reads three nested fields out of an attacker-reachable body, and
    each one needs the same question asked.
    """
    return value if isinstance(value, dict) else {}


def _preview(value: Any) -> str:
    """A 40-character log preview of an arbitrary JSON value. Never `or ""`.

    Same half-guard, one line over from `from`, and the audit named only
    `from`. `(msg.get("text") or "")[:40]` slices whatever it is handed, so a
    truthy unsliceable value raised instead. MEASURED 2026-09-02,
    `{"message": {"text": 42}}` and `{"callback_query": {"data": 42}}` both
    raised `TypeError: 'int' object is not subscriptable` out of the handler,
    which FastAPI serves as a 500 on the same public endpoint. Telegram's own
    schema says these are strings; the endpoint's contract is that a body which
    is not is the caller's error, and a preview is cosmetic either way.
    """
    if value is None:
        return ""
    return (value if isinstance(value, str) else repr(value))[:40]


def create_app(fb_module: Any, secret_token: str, logger: logging.Logger):
    """Build the webhook FastAPI app.

    Args:
        fb_module: the dynamically-imported fireside-bot module (the daemon
            already loads it via importlib.util).
        secret_token: the value Telegram will send in the
            X-Telegram-Bot-Api-Secret-Token header on every webhook POST.
        logger: the daemon's logger; the webhook layer writes errors here so
            they land in the daemon's log file (.fireside/daemon.log under the
            workspace root, resolved by fireside-bot-daemon.py at runtime).
    """
    if not secret_token:
        raise ValueError("create_app requires a non-empty secret_token")

    _ensure_fastapi()
    app = FastAPI(title="fireside-webhook")

    # ONE update is handled at a time. Every handler in fireside-bot.py works by
    # load -> mutate -> save on a JSON file (schedule.json, opt-ins.json,
    # helmsmen.json, the swap records), and none of them takes a lock. Before
    # 2026-08-23 the tasks below ran concurrently, so two updates a second apart
    # - a member tapping a swap button while the expiry sweep runs, two people
    # sending /start together - interleaved load and save and one write vanished
    # silently, leaving valid JSON that had lost somebody's action.
    #
    # The cost is stated rather than hidden: a slow handler now delays the
    # PROCESSING of later updates. It does not delay their ACCEPTANCE, which is
    # what Telegram's 60-second contract measures - the POST still returns 200 OK
    # in milliseconds. At this bot's volume, correctness is worth the queue.
    handler_lock = asyncio.Lock()

    # asyncio only holds a WEAK reference to a task, so a fire-and-forget
    # `create_task` can be garbage-collected mid-flight and the update is lost
    # with no error anywhere. Documented CPython behaviour, and the reason this
    # set exists.
    background: set = set()

    async def _process_in_background(update: dict, kind: str, t_recv: float) -> None:
        """Run _handle_update off the request path, one at a time.

        Telegram's webhook contract: any response slower than 60s makes
        Telegram re-deliver the same update. We return 200 OK from the POST
        handler immediately and do the actual work here. Even a 90-second
        sendMessage stall (e.g. DMing a user who never /started the bot)
        no longer causes duplicate-delivery to other users.
        """
        update_id = update.get("update_id")
        bot = fb_module.get_bot()
        t_queued = time.monotonic()
        async with handler_lock:
            # Started AFTER the lock. Taking it before meant `handler_ms`
            # silently included time spent queued behind another update, so
            # under contention it converged on `total_ms` and an operator
            # graphing handler latency saw lock queueing mislabelled as handler
            # slowness. The lock's comment above makes a point of STATING the
            # queueing cost; the metric named "handler" then hid it.
            t_start = time.monotonic()
            try:
                await asyncio.to_thread(fb_module._handle_update, bot, update)
            except Exception as e:
                # The offset is deliberately NOT advanced: this update's work did
                # not happen, and acking it would drop it. What was missing is any
                # record that it failed -- so a permanently-raising update is
                # re-fetched and re-raised on every poll fallback, forever, with
                # nothing anywhere naming it. The id is logged at ERROR now, so
                # the poison message is findable in the daemon log.
                logger.exception(
                    "webhook: _handle_update raised for update=%s kind=%s: %s "
                    "(offset NOT advanced; this update will be re-served on a "
                    "poll fallback)", update_id, kind, e)
                return
            t_done = time.monotonic()
            logger.info("webhook: ok update=%s kind=%s handler_ms=%d queued_ms=%d "
                        "total_ms=%d",
                        update_id, kind, int((t_done - t_start) * 1000),
                        int((t_start - t_queued) * 1000),
                        int((t_done - t_recv) * 1000))
            try:
                if isinstance(update_id, int):
                    _advance_offset(update_id)
            except Exception:
                logger.exception("webhook: failed to update last-update-id")

    def _advance_offset(update_id: int) -> None:
        """Record the offset, never rewinding it.

        A plain `{"offset": update_id + 1}` is written by whichever task
        finishes last, not by whichever id is highest, so a slow update 100
        finishing after a fast 101 used to rewind the offset by one. Harmless
        while the webhook runs, and a re-processed update the moment the daemon
        falls back to polling.
        """
        current = 0
        try:
            existing = fb_module.load_state(fb_module.LAST_UPDATE_ID) or {}
            current = int(existing.get("offset", 0) or 0)
        except Exception:  # noqa: BLE001 - an unreadable/absent state file means 0
            logger.exception("webhook: last-update-id unreadable; treating as 0")
        fb_module.save_state(fb_module.LAST_UPDATE_ID,
                             {"offset": max(current, update_id + 1)})

    @app.post("/telegram-webhook")
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ):
        t0 = time.monotonic()
        # `compare_digest`, not `!=`. This header is the ONLY authorization on
        # a publicly reachable endpoint, and `!=` short-circuits at the first
        # differing byte. A timing side channel is a needless thing to hand an
        # attacker when the constant-time comparison costs nothing.
        if not secrets.compare_digest(x_telegram_bot_api_secret_token or "", secret_token):
            logger.warning("webhook: rejected request with invalid/missing secret token")
            raise HTTPException(status_code=401, detail="invalid secret token")

        try:
            update = await request.json()
        except Exception as e:
            logger.exception("webhook: JSON decode failed: %s", e)
            raise HTTPException(status_code=400, detail="invalid JSON")

        if not isinstance(update, dict):
            # `[]` and `"x"` are valid JSON and reached `.get` as a 500
            # AttributeError, past the handler's own "invalid JSON -> 400"
            # boundary. The endpoint is public; a malformed body is the
            # caller's error, not the server's.
            logger.warning("webhook: body parsed as %s, not an object",
                           type(update).__name__)
            raise HTTPException(status_code=400, detail="update must be a JSON object")

        update_id = update.get("update_id")
        if "message" in update:
            kind = "message"
            # `isinstance` on both, matching the callback_query branch below. A
            # body with `"message": null`, `"message": "hi"`, or `"from": null`
            # hit `.get` on a non-dict and left as a 500 AttributeError -- past
            # this handler's own stated boundary two comments up, that a
            # malformed body is the caller's error. Only the logging preview
            # needs these; `_handle_update` does its own type checks.
            #
            # `or {}` was the first spelling and it half-enforced that boundary:
            # it converts only FALSY non-dicts, so a TRUTHY one still reached
            # `.get`. MEASURED 2026-09-02, `{"message": {"from": ["not", "a",
            # "dict"], "text": "hi"}}` raised `AttributeError: 'list' object has
            # no attribute 'get'` out of the handler, which FastAPI serves as a
            # 500 on a publicly reachable endpoint. `_dict_or_empty` asks the
            # only question that settles it.
            msg = _dict_or_empty(update.get("message"))
            text_preview = _preview(msg.get("text"))
            user = _dict_or_empty(msg.get("from")).get("username", "?")
            logger.info("webhook: recv update=%s message from=@%s text=%r",
                        update_id, user, text_preview)
        elif "callback_query" in update:
            kind = "callback_query"
            cq = _dict_or_empty(update.get("callback_query"))
            data_preview = _preview(cq.get("data"))
            user = _dict_or_empty(cq.get("from")).get("username", "?")
            logger.info("webhook: recv update=%s callback_query from=@%s data=%r",
                        update_id, user, data_preview)
        else:
            kind = next((k for k in ("message_reaction", "chat_member", "my_chat_member",
                                      "message_reaction_count") if k in update), "unknown")
            logger.info("webhook: recv update=%s type=%s", update_id, kind)

        # Schedule the actual work. Returning 200 OK NOW satisfies Telegram's
        # webhook contract within milliseconds, so no retry-driven duplicate
        # delivery regardless of how slow the handler turns out to be.
        task = asyncio.create_task(_process_in_background(update, kind, t0))
        background.add(task)
        task.add_done_callback(background.discard)
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"ok": True, "service": "fireside-webhook"}

    return app
