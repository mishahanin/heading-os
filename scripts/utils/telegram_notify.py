"""Single entry point for HEADING OS system notifications: notify(target, message).

Sends via the dedicated notifications bot (TELEGRAM_NOTIFY_BOT_TOKEN), not the
userbot client - a Bot API sendMessage always push-notifies reliably, unlike a
message the userbot sends to a chat/channel it already owns.

This transport is SELF-NOTIFICATION ONLY. It may reach the operator's own
notification sink and nothing else. Six timer-driven callers (ops-radar-notify,
council-models-notify, reminders-notify, sentinel, alert, odin-cadence-notify)
plus the checkpoint-offer hook drive it with no human in the loop, so a
recipient it accepts is a recipient that leaves the machine unreviewed. That is
the third leg of the lethal trifecta, and the workspace closes it by construction
here rather than by asking every caller to behave: see
`.claude/rules/lethal-trifecta.md` under self-notification.

The allowlist and its resolution order live in `own_targets()` below.

notify() NEVER raises. It degrades to False (with a clear, distinct log hint)
on: a missing token, an empty/falsy target, Telegram's own "me"/"self"/"saved"
(Saved Messages) sentinels - which have no Bot API equivalent, since a bot
cannot resolve its caller's own account - or a target that is not on the
allowlist. Nothing in HEADING OS may ever send a notification to Saved Messages,
so that rejection is deliberate and permanent, not a placeholder for a future
fix.

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

# The one variable that PINS the sink. When it carries a value, it is the whole
# allowlist and every other target is refused, however it was configured.
SELF_TARGET_ENV_VAR = "HEADING_OS_SELF_TELEGRAM_TARGET"

# The per-feature target variables the seven callers actually read, measured
# 2026-08-30 by reading every call site. Each caller resolves its own chain and
# hands the result here; every chain bottoms out in ODIN_CADENCE_TELEGRAM_TARGET.
# Ordered as the callers fall back, most specific first.
_FEATURE_TARGET_ENV_VARS = (
    "SENTINEL_TELEGRAM_TARGET",
    "COUNCIL_MODELS_TELEGRAM_TARGET",
    "OPS_RADAR_TELEGRAM_TARGET",
    "REMINDERS_TELEGRAM_TARGET",
    "CHECKPOINT_TELEGRAM_TARGET",
    "ODIN_CADENCE_TELEGRAM_TARGET",
)

# Both source groups end in _TELEGRAM_TARGET on purpose. tests/conftest.py blanks
# every environment name with that suffix at session start so a test run cannot
# message the operator; a declaration variable spelled any other way would walk
# straight through that containment.


def _normalise(target: object) -> str:
    """Fold one target to the form the allowlist is compared in.

    Whitespace and case are stripped, and a leading "@" is dropped, so
    "@My_Alerts" and "my_alerts" resolve to the same chat rather than to two
    entries the operator has to remember to keep in step. A numeric chat id is
    unaffected. A non-string (None is the one the callers can produce) folds to
    the empty string, which every check below already refuses.

    Comparison is exact set membership after this fold, never a prefix or
    substring test: "@my_alerts_public" must not pass because "@my_alerts" is
    allowed.
    """
    if not isinstance(target, str):
        return ""
    value = target.strip().lower()
    return value[1:] if value.startswith("@") else value


def _split_declared(raw: str) -> list[str]:
    """Split one environment value into candidate targets.

    Commas and semicolons both separate, because an operator writing a list of
    channels in a .env line reaches for either.
    """
    return raw.replace(";", ",").split(",")


def own_targets() -> set[str]:
    """The operator's own notification sinks, normalised. May be empty.

    Resolution order, and why it is this order:

    1. ``HEADING_OS_SELF_TELEGRAM_TARGET``, when set, IS the allowlist. It is
       the operator declaring "this, and only this, is my sink". A per-feature
       variable that disagrees with it is then refused rather than obeyed, so
       one designated value governs all seven callers.
    2. Otherwise the allowlist is the union of the six per-feature
       ``*_TELEGRAM_TARGET`` variables the callers read. Those values are the
       operator's own routing, normally edited by hand into a gitignored
       ``.env``. Accepting them keeps a working install working while refusing
       the recipients a caller can produce WITHOUT touching the environment: a
       literal in a caller, a value derived from fetched content, an argument
       handed in by a skill. Those are the shapes this guard closes.

       **What it does not close, stated because the sentence here used to claim
       it did.** The read is ``os.environ``, not the ``.env`` FILE, so a value
       the running process assigned to one of these names is indistinguishable
       from one the operator typed. MEASURED 2026-09-01: with the six names
       cleared, ``os.environ["OPS_RADAR_TELEGRAM_TARGET"] = "@example_stranger"``
       followed by ``notify("@example_stranger", ...)`` returned True and reached
       the transport.

       Reading the file instead would be a worse trade, not a better one, and
       that is why the seam is here: ``tests/conftest.py`` contains the whole
       suite by BLANKING these names in ``os.environ``, so a resolver that went
       to the file would let a test run message the operator, and a systemd unit
       that passes the target via ``Environment=`` would go dark. An adversary
       who can assign to ``os.environ`` in this process can also call
       ``TelegramBot`` directly and skip this module entirely, so the boundary
       buys nothing against that one. Do not "harden" this to a file read
       without settling both of those first.
    3. Nothing set means an empty set, and an empty set refuses everything.
       Absent configuration must not resolve to "send anyway, somewhere".

    The read is per call, not cached at import: the callers run under systemd
    timers that call ``load_env`` after this module is imported, so a snapshot
    taken at import would be empty for exactly the processes this guards.
    """
    pinned = os.environ.get(SELF_TARGET_ENV_VAR) or ""
    sources = [pinned] if pinned.strip() else [
        os.environ.get(name) or "" for name in _FEATURE_TARGET_ENV_VARS
    ]

    allowed: set[str] = set()
    for raw in sources:
        for candidate in _split_declared(raw):
            normalised = _normalise(candidate)
            # A sentinel declared in .env is still unreachable for a bot, so it
            # never becomes an allowlist entry that a caller could then match.
            if normalised and normalised not in _UNRESOLVABLE_TARGETS:
                allowed.add(normalised)
    return allowed


def notify(target: str, message: str) -> bool:
    """Send a system notification to the operator's own sink via the bot.

    Returns True on a clean send, False on any failure (missing token,
    unresolvable target, a target that is not an own sink, transport/API
    error). NEVER raises. NEVER sends to a target outside ``own_targets()``.
    """
    load_env()
    token = os.environ.get("TELEGRAM_NOTIFY_BOT_TOKEN")
    if not token:
        logger.warning(
            "telegram_notify: TELEGRAM_NOTIFY_BOT_TOKEN not set in .env - "
            "no notification sent. See docs/TELEGRAM-AND-ALERTS.md for one-time setup."
        )
        return False

    wanted = _normalise(target)
    if not wanted or wanted in _UNRESOLVABLE_TARGETS:
        logger.warning(
            "telegram_notify: target %r is not bot-resolvable (a bot cannot target "
            "Telegram's own-account sentinel 'me'/'self'/'saved') - no notification "
            "sent. Configure a real channel id/@username via the relevant "
            "*_TELEGRAM_TARGET env var.",
            target,
        )
        return False

    allowed = own_targets()
    if wanted not in allowed:
        # ERROR, not WARNING, and the word REFUSED: the sibling degradations
        # above are ordinary "not configured yet" states, while this one means
        # something asked this transport to reach a recipient the operator never
        # declared. That is the case a reader must be able to find in a log.
        logger.error(
            "telegram_notify: REFUSED - target %r is not one of the operator's own "
            "notification sinks, so nothing was sent. This transport reaches the "
            "operator and no one else; an outbound message to anyone else is "
            "human-gated (.claude/rules/lethal-trifecta.md). Declare the sink in "
            "%s, or in one of %s, in the gitignored .env. Currently declared: %d "
            "target(s).",
            target,
            SELF_TARGET_ENV_VAR,
            ", ".join(_FEATURE_TARGET_ENV_VARS),
            len(allowed),
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
