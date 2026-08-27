#!/usr/bin/env python3
"""
Sentinel -- Unified Comms Monitor for 31C CEO Workspace

Continuously monitors corporate email (Exchange EWS) and Telegram,
analyzes incoming messages for urgency using Claude API, and sends
critical items to a dedicated Telegram channel.

Prerequisites:
    pip install exchangelib telethon anthropic pyyaml python-dotenv

Usage:
    python scripts/sentinel.py              # run daemon (foreground)
    python scripts/sentinel.py --test       # single cycle, dry-run
    python scripts/sentinel.py --status     # check if running
    python scripts/sentinel.py --stop       # stop running daemon
"""

import argparse
import asyncio
import fnmatch
import hashlib
import io
import json
import logging
import os
import re
import signal
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from scripts.utils import claude_models
from scripts.utils import daemon_heartbeat  # noqa: E402
from scripts.utils import telegram_notify  # noqa: E402
from scripts.utils import tracing  # noqa: E402
from scripts.utils.healthchecks import ping as hc_ping  # noqa: E402
from scripts.utils.html_text import strip_html  # noqa: E402
from scripts.utils.llm_fallback import call_anthropic_with_fallback  # noqa: E402
from scripts.utils.observability import observe  # noqa: E402
from scripts.utils.operator_identity import get_operator  # noqa: E402
from scripts.utils.trace_filter import install_log_factory  # noqa: E402
from scripts.utils.workspace import get_default_tz, get_default_tz_name, get_workspace_root, load_env, resolve_config_with_example  # noqa: E402


def _configure_session_wal(client, busy_timeout_ms=30000):
    """Set WAL journal mode and busy_timeout on the session's sqlite3 connection.

    WAL allows concurrent reads while writes proceed, preventing 'database is
    locked' errors between Sentinel, telegram_client.py, and Viraid.
    Monkey-patches _cursor() so pragmas survive connection recycling.
    """
    session = client.session
    original_cursor = session._cursor

    def _patched_cursor():
        was_none = session._conn is None
        cursor = original_cursor()
        if was_none and session._conn is not None:
            session._conn.execute(f'PRAGMA busy_timeout = {int(busy_timeout_ms)}')
            session._conn.execute('PRAGMA journal_mode = WAL')
        return cursor

    session._cursor = _patched_cursor

    # Apply immediately if connection already exists
    conn = getattr(session, '_conn', None)
    if conn is not None:
        conn.execute(f'PRAGMA busy_timeout = {int(busy_timeout_ms)}')
        conn.execute('PRAGMA journal_mode = WAL')

# --- Paths ---
WORKSPACE_ROOT = get_workspace_root()
ENV_FILE = WORKSPACE_ROOT / ".env"
# Config-DATA: the real config lives in the data overlay (config/sentinel_config.yaml,
# resolved via get_data_config_dir()); the engine ships sentinel_config.example.yaml
# as the fallback so a data-less clone runs out of the box.
CONFIG_FILE = resolve_config_with_example(
    "sentinel_config.yaml", WORKSPACE_ROOT / "scripts" / "sentinel_config.example.yaml"
)
RUNTIME_DIR = WORKSPACE_ROOT / ".sentinel"
STATE_FILE = RUNTIME_DIR / "state.json"
LOG_FILE = RUNTIME_DIR / "sentinel.log"
PID_FILE = RUNTIME_DIR / "sentinel.pid"
TELEGRAM_SESSION_DIR = WORKSPACE_ROOT / ".sessions" / "telegram"
TELEGRAM_SESSION_PATH = TELEGRAM_SESSION_DIR / "telegram"

# --- Fix Windows console encoding ---
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# --- Load .env ---
if ENV_FILE.exists():
    load_env(WORKSPACE_ROOT)


# HTML stripping: see scripts/utils/html_text.py (imported above as strip_html)


# ============================================================
# Configuration
# ============================================================

class SentinelConfig:
    """Load and validate sentinel_config.yaml."""

    def __init__(self, config_path: Path = CONFIG_FILE):
        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")
        with open(config_path, "r", encoding="utf-8") as f:
            self._raw = yaml.safe_load(f)

        g = self._raw.get("general", {})
        self.check_interval = g.get("check_interval_minutes", 15) * 60  # seconds
        self.urgency_threshold = g.get("urgency_threshold", 7)
        self.timezone = ZoneInfo(g.get("timezone", get_default_tz_name()))
        self.log_level = g.get("log_level", "INFO")

        self.email = self._raw.get("email", {})
        self.telegram = self._raw.get("telegram", {})
        self.digest = self._raw.get("digest", {})
        self.notification = self._raw.get("notification", {})
        self.llm = self._raw.get("llm", {})
        self.calendar = self._raw.get("calendar", {})


# ============================================================
# State Manager
# ============================================================

class StateManager:
    """Persistent state tracking to avoid duplicate processing.

    `read_only` makes `save()` a no-op. A dry run still marks items in memory --
    that is what stops one cycle from processing the same message twice -- but
    it must not PERSIST those marks: doing so meant `--test` permanently
    consumed real state, and the production daemon was then blind to every
    message and invite the test had seen, with nothing said about it.
    """

    def __init__(self, state_path: Path = STATE_FILE, read_only: bool = False):
        self.path = state_path
        self.read_only = read_only
        self.data = self._load()

    def _load(self) -> dict:
        """The state file, with every required section guaranteed present.

        A file that parses is not a file that fits. A hand-edited, truncated or
        older-schema state.json was returned verbatim until 2026-08-25, and the
        first cycle then died on a bare subscript: `data["email"]["processed_ids"]`
        at is_email_processed, `data["digest"]` at record_digest_item,
        `data["email"]["last_check"]` in run_cycle. `save()` has no schema guard
        either, so once a short dict was in memory the daemon persisted it and
        cemented the shape.

        The `version` field made this look handled and did not: it is written
        here and read nowhere in the file, so it can neither gate a migration
        nor detect one. The merge below is what actually holds the contract, so
        a missing section is filled rather than discovered at the call site.
        """
        loaded = None
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
            except (json.JSONDecodeError, OSError):
                loaded = None
        skeleton = self._default_state()
        if not isinstance(loaded, dict):
            return skeleton
        for key, default in skeleton.items():
            if key not in loaded:
                loaded[key] = default
            elif isinstance(default, dict) and isinstance(loaded[key], dict):
                # One level down is enough: no required key sits deeper.
                for sub_key, sub_default in default.items():
                    loaded[key].setdefault(sub_key, sub_default)
            elif isinstance(default, dict) and not isinstance(loaded[key], dict):
                # A section of the wrong TYPE would subscript as badly as a
                # missing one, so it is replaced rather than merged.
                loaded[key] = default
        return loaded

    @staticmethod
    def _default_state() -> dict:
        return {
            "version": 2,
            "last_run": None,
            "email": {"processed_ids": [], "last_check": None},
            "telegram": {"per_chat": {}, "last_check": None},
            "notified_hashes": {},
            "digest": {
                "today": None,
                "emails_checked": 0,
                "tg_messages_checked": 0,
                "urgent_sent": 0,
                "items_by_urgency": [],
            },
            "calendar": {
                "processed_invite_ids": [],
                "decisions_today": [],
            },
        }

    def save(self):
        if self.read_only:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, default=str)
        os.replace(tmp, self.path)

    def is_email_processed(self, message_id: str) -> bool:
        return message_id in self.data["email"]["processed_ids"]

    def mark_email_processed(self, message_id: str):
        ids = self.data["email"]["processed_ids"]
        if message_id not in ids:
            ids.append(message_id)
        # Keep only last 500 to prevent unbounded growth
        if len(ids) > 500:
            self.data["email"]["processed_ids"] = ids[-500:]

    def rotate_old_state(self, max_age_days: int = 30):
        """Purge stale entries from processed_ids and invite_ids based on age.

        Since message IDs don't carry timestamps, we trim by count more
        aggressively when the list is large, and purge notified_hashes by age.
        """
        # Trim email processed_ids to last 300 if over 400
        ids = self.data["email"].get("processed_ids", [])
        if len(ids) > 400:
            self.data["email"]["processed_ids"] = ids[-300:]

        # Trim calendar invite_ids
        cal = self.data.get("calendar", {})
        invite_ids = cal.get("processed_invite_ids", [])
        if len(invite_ids) > 150:
            cal["processed_invite_ids"] = invite_ids[-100:]

        # Purge old notified_hashes (already has cleanup_old_hashes, but
        # this provides a broader sweep for any hashes older than max_age_days)
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        to_remove = []
        for h, ts in self.data.get("notified_hashes", {}).items():
            try:
                dt = datetime.fromisoformat(ts)
                if dt < cutoff:
                    to_remove.append(h)
            except (ValueError, TypeError):
                to_remove.append(h)
        for h in to_remove:
            del self.data["notified_hashes"][h]

        # Purge old digest items
        digest = self.data.get("digest", {})
        digest_items = digest.get("items_by_urgency", [])
        if len(digest_items) > 150:
            digest["items_by_urgency"] = digest_items[-100:]

    def get_telegram_last_id(self, chat_id: str) -> int:
        chat_data = self.data["telegram"]["per_chat"].get(str(chat_id), {})
        return chat_data.get("last_message_id", 0)

    def set_telegram_last_id(self, chat_id: str, name: str, msg_id: int):
        self.data["telegram"]["per_chat"][str(chat_id)] = {
            "name": name,
            "last_message_id": msg_id,
            "last_check": datetime.now(timezone.utc).isoformat(),
        }

    def is_already_notified(self, content_hash: str) -> bool:
        return content_hash in self.data["notified_hashes"]

    def mark_notified(self, content_hash: str):
        self.data["notified_hashes"][content_hash] = datetime.now(timezone.utc).isoformat()

    def cleanup_old_hashes(self, max_age_minutes: int):
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        to_remove = []
        for h, ts in self.data["notified_hashes"].items():
            try:
                dt = datetime.fromisoformat(ts)
                if dt < cutoff:
                    to_remove.append(h)
            except (ValueError, TypeError):
                to_remove.append(h)
        for h in to_remove:
            del self.data["notified_hashes"][h]

    def reset_daily_digest(self, today_str: str):
        if self.data["digest"].get("today") != today_str:
            self.data["digest"] = {
                "today": today_str,
                "emails_checked": 0,
                "tg_messages_checked": 0,
                "urgent_sent": 0,
                "items_by_urgency": [],
            }

    def record_digest_item(self, item: dict, urgency_score: int):
        self.data["digest"]["items_by_urgency"].append({
            "source": item.get("source", "unknown"),
            "sender": item.get("sender", "unknown"),
            "subject": item.get("subject", ""),
            "urgency": urgency_score,
            "time": datetime.now(timezone.utc).isoformat(),
        })
        # Cap at 200 items per day
        if len(self.data["digest"]["items_by_urgency"]) > 200:
            self.data["digest"]["items_by_urgency"] = self.data["digest"]["items_by_urgency"][-200:]

    # --- Calendar invite tracking ---

    def is_invite_processed(self, invite_id: str) -> bool:
        cal = self.data.get("calendar", {})
        return invite_id in cal.get("processed_invite_ids", [])

    def mark_invite_processed(self, invite_id: str):
        cal = self.data.setdefault("calendar", {"processed_invite_ids": [], "decisions_today": []})
        ids = cal.setdefault("processed_invite_ids", [])
        if invite_id not in ids:
            ids.append(invite_id)
        if len(ids) > 200:
            cal["processed_invite_ids"] = ids[-200:]

    def record_invite_decision(self, invite_id: str, subject: str, decision: str, reasons: list):
        cal = self.data.setdefault("calendar", {"processed_invite_ids": [], "decisions_today": []})
        decisions = cal.setdefault("decisions_today", [])
        decisions.append({
            "invite_id": invite_id,
            "subject": subject,
            "decision": decision,
            "reasons": reasons,
            "time": datetime.now(timezone.utc).isoformat(),
        })

    def reset_calendar_daily(self, today_str: str):
        cal = self.data.setdefault("calendar", {"processed_invite_ids": [], "decisions_today": []})
        if cal.get("today") != today_str:
            cal["decisions_today"] = []
            cal["today"] = today_str


# ============================================================
# Email Source
# ============================================================

# How far a cycle will page into the unread backlog before giving up and
# SAYING so. Bounds the cost of a mailbox with thousands of unread items;
# the warning is what keeps a partial scan from reading as a full one.
BACKLOG_SCAN_CAP = 500


class EmailSource:
    """Fetch new emails from Exchange EWS."""

    def __init__(self, config: dict, state: StateManager, logger: logging.Logger):
        self.config = config
        self.state = state
        self.logger = logger
        self.account = None

    def connect(self):
        from exchangelib import Account, Configuration, Credentials, DELEGATE

        email = os.getenv("EXCHANGE_EMAIL")
        password = os.getenv("EXCHANGE_PASSWORD")
        server = os.getenv("EXCHANGE_SERVER")
        username = os.getenv("EXCHANGE_USERNAME", email)

        if not all([email, password, server]):
            raise ValueError("Missing Exchange credentials in .env")

        credentials = Credentials(username=username, password=password)
        exchange_config = Configuration(server=server, credentials=credentials)
        self.account = Account(
            primary_smtp_address=email,
            config=exchange_config,
            autodiscover=False,
            access_type=DELEGATE,
        )
        self.logger.info(f"Exchange connected as {email}")

    def check_new(self) -> list:
        if not self.account:
            self.connect()

        folder_name = self.config.get("folder", "Inbox")
        if folder_name.lower() == "inbox":
            folder = self.account.inbox
        elif folder_name.lower() == "sent":
            folder = self.account.sent
        else:
            folder = self.account.inbox / folder_name

        max_count = self.config.get("max_per_check", 50)
        unread = folder.filter(is_read=False)

        # Read from BOTH ends of the backlog, not just the newest end.
        #
        # Sentinel never marks mail read on the server, so the `is_read=False`
        # set only grows. A newest-first slice of `max_count` therefore pins a
        # window over the newest N: once those N are all in `processed_ids`,
        # every later cycle re-fetches the same N, skips every one of them, and
        # logs "0 new unread messages" while older unread mail sits permanently
        # outside the window. Nothing ages back INTO a newest-first slice.
        #
        # Newest-first stays first because this is an urgency daemon and fresh
        # mail must not queue behind a backlog. The oldest window is added only
        # when a backlog actually exists, so the ordinary case costs one count
        # query and nothing else.
        try:
            unread_total = unread.count()
        except Exception as exc:  # noqa: BLE001 - a count failure must not stop the cycle
            self.logger.warning(f"Email: could not count unread ({exc}); "
                                f"reading the newest {max_count} only")
            unread_total = None

        items = list(unread.order_by("-datetime_received")[:max_count])
        if unread_total is not None and unread_total > max_count:
            # Walk from the OLDEST end, skipping what this daemon has already
            # handled, until `max_count` genuinely-unseen items are in hand.
            #
            # A second FIXED window does not close the hole: with 120 unread and
            # max_count 50, the newest 50 and the oldest 50 leave 20 in the
            # middle that no cycle ever reaches, because a processed item stays
            # unread on the server and never leaves either slice. Skipping the
            # processed ones as we page is what makes the window ADVANCE.
            # Measured 2026-08-26 over a 120-item stand-in: two fixed windows
            # reached 100 of 120 and stopped; this reaches all 120.
            seen_ids = {it.id for it in items}
            backlog, scanned = [], 0
            for older in unread.order_by("datetime_received"):
                scanned += 1
                if scanned > BACKLOG_SCAN_CAP:
                    self.logger.warning(
                        f"Email: stopped scanning the backlog after "
                        f"{BACKLOG_SCAN_CAP} items; older unread mail was NOT "
                        f"examined this cycle")
                    break
                if older.id in seen_ids:
                    continue
                if self.state.is_email_processed(str(older.message_id or older.id)):
                    continue
                backlog.append(older)
                if len(backlog) >= max_count:
                    break
            items.extend(backlog)
            self.logger.info(
                f"Email: {unread_total} unread on the server; reading the "
                f"newest {max_count} and {len(backlog)} unseen from the backlog")

        new_items = []
        for email_item in items:
            msg_id = str(email_item.message_id or email_item.id)
            if self.state.is_email_processed(msg_id):
                continue

            sender_addr = ""
            sender_name = ""
            if email_item.sender:
                sender_addr = str(email_item.sender.email_address or "")
                sender_name = str(email_item.sender.name or sender_addr)

            if self._is_ignored(sender_addr):
                self.state.mark_email_processed(msg_id)
                continue

            # Extract body
            body = ""
            if email_item.text_body and email_item.text_body.strip():
                body = email_item.text_body.strip()
            elif email_item.body and str(email_item.body).strip():
                body = strip_html(email_item.body)

            if len(body) > 2000:
                body = body[:2000] + "\n[...truncated]"

            # Extract attachments
            attachments = []
            if email_item.has_attachments and email_item.attachments:
                attachments = [a.name for a in email_item.attachments if hasattr(a, "name") and a.name]

            date_str = str(email_item.datetime_received)[:19] if email_item.datetime_received else ""

            new_items.append({
                "source": "email",
                "message_id": msg_id,
                "sender": sender_name,
                "sender_email": sender_addr,
                "subject": email_item.subject or "(No subject)",
                "body": body,
                "date": date_str,
                "attachments": attachments,
                "is_vip": self._is_vip(sender_addr),
            })

        # Say what was NOT examined. "0 new unread messages" over a server
        # holding hundreds is the sentence that hid the defect above.
        examined = f" (examined {len(items)}"
        if unread_total is not None:
            examined += f" of {unread_total} unread"
        examined += ")"
        self.logger.info(f"Email: {len(new_items)} new unread messages{examined}")
        return new_items

    def _is_ignored(self, sender: str) -> bool:
        sender_lower = sender.lower()
        for pattern in self.config.get("ignore_patterns", []):
            if fnmatch.fnmatch(sender_lower, pattern.lower()):
                return True
        return False

    def _is_vip(self, sender: str) -> bool:
        sender_lower = sender.lower()
        for vip in self.config.get("vip_senders", []):
            if sender_lower == vip.lower():
                return True
        return False


# ============================================================
# Meeting Invite Source
# ============================================================

class MeetingInviteSource:
    """Detect and process meeting invites from Exchange inbox."""

    def __init__(self, config: dict, state: StateManager, logger: logging.Logger):
        self.config = config
        self.state = state
        self.logger = logger
        self.account = None  # Shared from EmailSource

    def check_new_invites(self) -> list:
        """Fetch unprocessed MeetingRequest items from inbox."""
        if not self.account:
            return []

        try:
            # Query for meeting request items in inbox
            invites = self.account.inbox.filter(
                item_class="IPM.Schedule.Meeting.Request"
            ).order_by("-datetime_received")[:20]
        except Exception as e:
            self.logger.error(f"Failed to query meeting invites: {e}")
            return []

        from exchangelib import UTC
        now = datetime.now(tz=UTC)

        new_invites = []
        for invite in invites:
            invite_id = str(invite.message_id or invite.id)
            if self.state.is_invite_processed(invite_id):
                continue

            # Skip past meetings - only process future invites
            if invite.start and invite.start < now:
                self.logger.debug(f"Skipping past invite: {invite.subject} ({invite.start})")
                self.state.mark_invite_processed(invite_id)
                continue

            sender_email = ""
            sender_name = ""
            if invite.sender:
                sender_email = str(invite.sender.email_address or "")
                sender_name = str(invite.sender.name or sender_email)

            # Count attendees
            attendee_count = 0
            if hasattr(invite, "required_attendees") and invite.required_attendees:
                attendee_count += len(invite.required_attendees)
            if hasattr(invite, "optional_attendees") and invite.optional_attendees:
                attendee_count += len(invite.optional_attendees)

            # Duration
            duration_minutes = 0
            if invite.start and invite.end:
                try:
                    duration_minutes = int((invite.end - invite.start).total_seconds() / 60)
                except (TypeError, AttributeError, ValueError) as e:
                    self.logger.debug(f"meeting duration calc fallback: {e}")

            # Body snippet
            body = ""
            if hasattr(invite, "text_body") and invite.text_body:
                body = invite.text_body.strip()[:500]
            elif hasattr(invite, "body") and invite.body:
                body = strip_html(invite.body)[:500]

            # Check if recurring
            is_recurring = False
            if hasattr(invite, "type") and invite.type == "RecurringMaster":
                is_recurring = True

            new_invites.append({
                "invite_id": invite_id,
                "item": invite,
                "sender": sender_name,
                "sender_email": sender_email,
                "subject": invite.subject or "(No subject)",
                "start": invite.start,
                "end": invite.end,
                "duration_minutes": duration_minutes,
                "location": str(invite.location) if invite.location else "",
                "body": body,
                "attendee_count": attendee_count,
                "is_recurring": is_recurring,
            })

        self.logger.info(f"Meeting invites: {len(new_invites)} new")
        return new_invites

    def get_existing_events(self, start_date, end_date) -> list:
        """Fetch calendar events for conflict checking."""
        if not self.account:
            return []

        from exchangelib import EWSDateTime, EWSTimeZone

        tz = EWSTimeZone(get_default_tz_name())
        ews_start = EWSDateTime(
            start_date.year, start_date.month, start_date.day,
            0, 0, 0, tzinfo=tz
        )
        ews_end = EWSDateTime(
            end_date.year, end_date.month, end_date.day,
            23, 59, 59, tzinfo=tz
        )

        events = []
        try:
            for item in self.account.calendar.view(start=ews_start, end=ews_end):
                if not hasattr(item.start, "hour"):
                    continue  # Skip all-day events
                events.append({
                    "start": item.start,
                    "end": item.end,
                    "subject": item.subject or "",
                })
        except Exception as e:
            self.logger.error(f"Failed to fetch calendar events: {e}")

        return events

    def accept_invite(self, item):
        """Accept a meeting request."""
        item.accept()
        self.logger.info(f"Accepted invite: {item.subject}")

    def decline_invite(self, item, message: str):
        """Decline a meeting request with message."""
        from exchangelib import Body
        item.decline(body=Body(message))
        self.logger.info(f"Declined invite: {item.subject}")


# ============================================================
# Calendar invite helpers (pure, unit-tested)
# ============================================================

_ZERO_WIDTH = dict.fromkeys([0x200b, 0x200c, 0x200d, 0x2060, 0xfeff], None)

_DEFAULT_RUNE_TOKEN = "[RUNE]"  # noqa: S105 — a subject tag, not a credential


def _normalize_subject(s: str) -> str:
    """Strip zero-width chars, collapse whitespace, for stable tag matching."""
    s = (s or "").translate(_ZERO_WIDTH)
    return re.sub(r"\s+", " ", s).strip()


def subject_has_rune(subject: str, rune_token: str = _DEFAULT_RUNE_TOKEN) -> bool:  # noqa: S107 — rune_token is a subject tag, not a credential
    """True when the bracketed override tag is present (case-insensitive)."""
    token = _normalize_subject(rune_token or _DEFAULT_RUNE_TOKEN).lower()
    return token in _normalize_subject(subject).lower()


def build_tribe_decline_message(subject: str, alternative,
                                rune_token: str = _DEFAULT_RUNE_TOKEN,  # noqa: S107 — rune_token is a subject tag, not a credential
                                template: str = None) -> str:
    """Warm, Tribe-specific decline body with a ready-to-copy override example."""
    subject = subject or "your meeting"
    if template:
        try:
            return template.format(subject=subject,
                                   alternative=alternative or "another time",
                                   rune_token=rune_token)
        except (KeyError, IndexError, ValueError):
            pass  # malformed operator template -- fall through to the built-in default body
    parts = [
        "Thanks for the invite. This is an internal Tribe request, so it matters to me.",
        "It clashes with time I keep protected, so I can't take this exact slot.",
    ]
    if alternative:
        parts.append(f"Could we do {alternative} instead?")
    parts.append(
        f"If it's genuinely time-critical and needs me regardless, resend the same "
        f"invite with the tag {rune_token} added to the front of the subject, square "
        f"brackets included, and it comes straight to me to decide personally."
    )
    parts.append(f"Your subject would look exactly like this: {rune_token} {subject}")
    return " ".join(parts)


def select_decline_message(is_tribe: bool, subject: str, alternative,
                           calendar_config: dict) -> str:
    """Choose the decline body: Tribe-specific vs the generic default."""
    if is_tribe:
        return build_tribe_decline_message(
            subject, alternative,
            calendar_config.get("rune_token", _DEFAULT_RUNE_TOKEN),
            calendar_config.get("tribe_decline_message"),
        )
    msg = calendar_config.get(
        "decline_message",
        "Due to some conflicts, I'd like to propose a new day and time for our meeting.",
    )
    if alternative:
        msg += f" How about {alternative}?"
    return msg


# ============================================================
# Calendar Policy Engine
# ============================================================

# How far ahead `find_alternative_slot` searches, and therefore how far ahead
# the caller must fetch events for the conflict check to mean anything.
ALTERNATIVE_SEARCH_DAYS = 5
# The extra week the search adds so a run of weekends cannot exhaust it.
_WEEKEND_BUFFER_DAYS = 7


def conflict_window_days(search_days: int = ALTERNATIVE_SEARCH_DAYS) -> int:
    """Days of calendar a caller must fetch for `find_alternative_slot`.

    The search starts at reference + 1 and runs `search_days + 7` offsets, so
    it can propose a time up to `1 + search_days + 7` days out. The caller
    fetched SEVEN days. For every candidate beyond day 7, `_has_conflict`
    filtered an event list that held nothing for that date and answered "no
    conflict" - so the daemon could propose, inside a decline sent to a real
    organizer, a time the operator was already booked for. Measured 2026-08-26:
    search reach 12 days, fetch window 7.

    Deriving it here is the point. Two numbers that must agree, written in two
    files, drifted; one function that computes both cannot.
    """
    return 1 + search_days + _WEEKEND_BUFFER_DAYS


class CalendarPolicyEngine:
    """Evaluate meeting invites against CEO Calendar Policy."""

    THEME_KEYWORDS = {
        "Strategy & Leadership": [
            "strategy", "leadership", "co-founder", "planning", "all-hands",
            "standup", "tribe", "vision", "board", "quarterly",
        ],
        "Technical & Product": [
            "product", "technical", "engineering", "architecture", "sprint",
            "demo", "review", "research", "lab", "development", "design",
            "testing", "qa", "release", "deployment",
        ],
        "External & Business": [
            "investor", "partner", "sales", "customer", "prospect", "deal",
            "pipeline", "channel", "legal", "contract", "nda", "mou",
        ],
        "People & Operations": [
            "1:1", "one-on-one", "hr", "interview", "hiring", "onboarding",
            "performance", "operations", "operational", "culture",
        ],
        "Review & Think": [
            "review", "weekly", "catch-up", "catchup", "marketing", "pr",
            "content", "linkedin",
        ],
    }

    def __init__(self, config: dict, tz: ZoneInfo, logger: logging.Logger,
                 analyzer=None):
        self.config = config
        self.tz = tz
        self.logger = logger
        self.analyzer = analyzer  # UrgencyAnalyzer for LLM theme classification

    def evaluate(self, invite: dict, existing_events: list) -> dict:
        """Evaluate invite against policy. Returns decision + reasons."""
        violations = []
        start = invite.get("start")
        end = invite.get("end")
        duration = invite.get("duration_minutes", 0)
        attendee_count = invite.get("attendee_count", 0)
        subject = invite.get("subject", "")
        sender_email = invite.get("sender_email", "")

        is_tribe = self._is_tribe(sender_email)

        # RUNE override: top precedence, before any policy evaluation.
        if self.config.get("rune_override_enabled", True) and subject_has_rune(
            subject, self.config.get("rune_token", _DEFAULT_RUNE_TOKEN)
        ):
            return {
                "decision": "escalate",
                "reasons": ["RUNE override -- held for operator"],
                "proposed_alternative": None,
                "violations": [],
                "is_vip": self._is_vip_or_external(sender_email),
                "is_tribe": is_tribe,
            }

        if start and end:
            # Check protected time
            prot = self._check_protected_time(start, end)
            if prot:
                violations.append({"type": "protected_time", "detail": prot})

            # Check back-to-back
            btb = self._check_back_to_back(start, end, existing_events)
            if btb:
                violations.append({"type": "back_to_back", "detail": btb})

            # Check theme alignment
            if start.weekday() < 5:  # Mon-Fri only
                theme_issue = self._check_theme_alignment(subject, invite.get("body", ""), start.weekday())
                if theme_issue:
                    violations.append({"type": "theme_mismatch", "detail": theme_issue})

        # Check duration
        max_dur = self.config.get("max_duration_minutes", 80)
        if duration > max_dur:
            violations.append({"type": "duration", "detail": f"Duration {duration}m exceeds {max_dur}m limit"})

        # Check attendees
        max_att = self.config.get("max_attendees", 6)
        if attendee_count > max_att:
            violations.append({"type": "attendees", "detail": f"{attendee_count} attendees exceeds {max_att} limit"})

        # Determine VIP/external status
        is_vip = self._is_vip_or_external(sender_email)

        # Make decision
        decision = self._make_decision(violations, is_vip)
        reasons = [v["detail"] for v in violations] if violations else []

        # Find alternative if declining
        proposed_alternative = None
        if decision == "decline" and start:
            proposed_alternative = self.find_alternative_slot(
                duration or 25, subject, existing_events, start
            )

        return {
            "decision": decision,
            "reasons": reasons,
            "proposed_alternative": proposed_alternative,
            "violations": [v["type"] for v in violations],
            "is_vip": is_vip,
            "is_tribe": is_tribe,
        }

    def _check_protected_time(self, start, end) -> str:
        """Check if invite falls within a protected time block."""
        blocks = self.config.get("protected_blocks", [])
        # Convert to local time
        try:
            local_start = start.astimezone(self.tz)
            local_end = end.astimezone(self.tz)
        except Exception:
            return ""

        day = local_start.weekday()
        start_time = local_start.strftime("%H:%M")
        end_time = local_end.strftime("%H:%M")

        for block in blocks:
            block_days = block.get("days", [])
            if day not in block_days:
                continue

            # All-day block (no time constraints)
            if "before" not in block and "after" not in block and "start" not in block:
                day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                return f"Protected time: {day_names[day]} is blocked"

            # Before X
            if "before" in block:
                if start_time < block["before"]:
                    return f"Protected time: before {block['before']}"

            # After X
            if "after" in block:
                if start_time >= block["after"]:
                    return f"Protected time: after {block['after']}"

            # Start-End range
            if "start" in block and "end" in block:
                if start_time < block["end"] and end_time > block["start"]:
                    return f"Protected time: {block['start']}-{block['end']} block"

        return ""

    def _check_back_to_back(self, start, end, existing_events: list) -> str:
        """Check for back-to-back violations.

        KNOWN DEFECT, DELIBERATELY NOT FIXED (2026-08-25). Both gap tests below
        read `0 < gap < min_gap`, so a gap of exactly zero - a meeting starting
        the instant another ends, the clearest violation of a
        `min_gap_minutes: 15` policy - is judged compliant. Nothing else catches
        it: `_has_conflict` needs a real overlap, and the consecutive-run
        counter only fires above `max_consecutive`.

        The one-character fix is `0 <= gap`. It is not applied because a
        back_to_back violation routes to `decline` in `_make_decision`, and a
        decline SENDS a message to the organizer. That would change which real
        people receive an automated refusal, and the calendar auto-accept /
        auto-decline behaviour is the operator's own design, frozen on
        2026-08-23 pending his redesign. Changing it here would be an
        outward-facing change nobody asked for.

        Raise it with the operator; the fix is one comparison in each of the two
        branches below. `tests/test_a_pid_file_emptied_before_the_lock.py` pins
        the CURRENT behaviour so this stays visible rather than drifting.
        """
        min_gap = self.config.get("min_gap_minutes", 15)
        max_consecutive = self.config.get("max_consecutive", 3)

        try:
            local_start = start.astimezone(self.tz)
            local_end = end.astimezone(self.tz)
        except Exception:
            return ""

        # Check gap to nearest events
        for evt in existing_events:
            try:
                evt_start = evt["start"].astimezone(self.tz)
                evt_end = evt["end"].astimezone(self.tz)
            except Exception as e:
                self.logger.debug(f"skipping malformed event in gap check: {e}")
                continue

            # Same day check
            if evt_start.date() != local_start.date():
                continue

            # Gap before: event ends, then our invite starts
            if evt_end <= local_start:
                gap = (local_start - evt_end).total_seconds() / 60
                if 0 < gap < min_gap:
                    return f"Only {int(gap)}m gap (need {min_gap}m) after '{evt['subject'][:30]}'"

            # Gap after: our invite ends, then event starts
            if local_end <= evt_start:
                gap = (evt_start - local_end).total_seconds() / 60
                if 0 < gap < min_gap:
                    return f"Only {int(gap)}m gap (need {min_gap}m) before '{evt['subject'][:30]}'"

        # Check consecutive meetings
        same_day_events = []
        for evt in existing_events:
            try:
                evt_start = evt["start"].astimezone(self.tz)
                if evt_start.date() == local_start.date():
                    same_day_events.append(evt)
            except Exception as e:
                self.logger.debug(f"skipping malformed event in same-day scan: {e}")
                continue

        # Sort by start time and count consecutive
        all_events = same_day_events + [{"start": start, "end": end, "subject": "new invite"}]
        all_events.sort(key=lambda e: e["start"])

        consecutive = 1
        for i in range(1, len(all_events)):
            try:
                prev_end = all_events[i - 1]["end"].astimezone(self.tz)
                curr_start = all_events[i]["start"].astimezone(self.tz)
                gap = (curr_start - prev_end).total_seconds() / 60
                if gap < 30:  # Less than 30 min break = consecutive
                    consecutive += 1
                else:
                    consecutive = 1
            except Exception:
                consecutive = 1

            if consecutive > max_consecutive:
                return f"Would create {consecutive} consecutive meetings (max {max_consecutive})"

        return ""

    def _check_theme_alignment(self, subject: str, body: str, weekday: int) -> str:
        """Check if meeting topic aligns with the day's theme."""
        themes = self.config.get("day_themes", {})
        day_theme = themes.get(weekday, "")
        if not day_theme:
            return ""

        text = (subject + " " + body).lower()

        # Try LLM classification first
        if self.config.get("use_llm_for_theme", False) and self.analyzer:
            try:
                classified = self._classify_theme_llm(subject, body[:200])
                if classified and classified != day_theme:
                    return f"Topic appears to be '{classified}' but {['Mon','Tue','Wed','Thu','Fri'][weekday]} theme is '{day_theme}'"
            except Exception as e:
                # LLM call can raise anthropic API errors, network errors, or parse errors.
                # We intentionally fall through to keyword matching on any failure.
                self.logger.debug(f"LLM theme classification fallback to keywords: {e}")

        # Keyword fallback
        best_theme = None
        best_score = 0
        for theme, keywords in self.THEME_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > best_score:
                best_score = score
                best_theme = theme

        if best_theme and best_theme != day_theme and best_score >= 2:
            return f"Topic appears to be '{best_theme}' but {['Mon','Tue','Wed','Thu','Fri'][weekday]} theme is '{day_theme}'"

        return ""

    @observe()
    def _classify_theme_llm(self, subject: str, body_snippet: str) -> str:
        """Use LLM to classify meeting into a day theme.

        Note: prompt is small (~150 chars, no system prompt) and varies per call -
        below any prompt-cache threshold. No cache_control applied here by design.
        """
        if not self.analyzer:
            return ""

        client = self.analyzer._get_client()
        themes = list(self.THEME_KEYWORDS.keys())
        prompt = (
            f"Classify this meeting into exactly one theme.\n"
            f"Themes: {', '.join(themes)}\n"
            f"Subject: {subject}\nBody: {body_snippet}\n"
            f"Reply with ONLY the theme name, nothing else."
        )

        try:
            r = call_anthropic_with_fallback(
                client=client,
                model=self.analyzer.model,
                max_tokens=50,
                system="",
                messages=[{"role": "user", "content": prompt}],
                skill_name="sentinel.classify_theme_llm",
            )
            result = r.text
            # Match against known themes
            for theme in themes:
                if theme.lower() in result.lower():
                    return theme
            return result
        except Exception as e:
            self.logger.warning(f"LLM theme classification failed across all vendors: {e}")
            return ""

    def _is_vip_or_external(self, sender_email: str) -> bool:
        """Check if sender is VIP or from an external domain."""
        sender_lower = sender_email.lower()

        for vip in self.config.get("vip_senders", []):
            if sender_lower == vip.lower():
                return True

        for domain in self.config.get("external_domains", []):
            if sender_lower.endswith(f"@{domain.lower()}"):
                return True

        return False

    def _tribe_domains(self) -> list:
        """Configured Tribe domains, defaulting to the operator's own domain."""
        doms = [d.lower() for d in (self.config.get("tribe_domains") or [])]
        if not doms:
            email = (get_operator().get("email") or "").strip().lower()
            if "@" in email:
                doms = [email.rsplit("@", 1)[-1]]
        return doms

    def _is_tribe(self, sender_email: str) -> bool:
        """True when the sender's domain is a Tribe (internal) domain."""
        email = (sender_email or "").strip().lower()
        if "@" not in email:
            return False
        return email.rsplit("@", 1)[-1] in self._tribe_domains()

    def _make_decision(self, violations: list, is_vip: bool) -> str:
        """Decide: accept, decline, or escalate."""
        if not violations:
            return "accept"

        hard_types = {"protected_time"}
        soft_types = {"theme_mismatch", "duration", "attendees"}

        hard = [v for v in violations if v["type"] in hard_types]
        soft = [v for v in violations if v["type"] in soft_types]
        btb = [v for v in violations if v["type"] == "back_to_back"]

        # VIP/external: always escalate, never auto-decline
        if is_vip:
            return "escalate"

        if hard:
            return "decline"

        if btb:
            return "decline"

        if soft:
            return "escalate"

        return "decline"

    def find_alternative_slot(self, duration_minutes: int, subject: str,
                              existing_events: list, reference_date=None,
                              search_days: int = ALTERNATIVE_SEARCH_DAYS) -> str:
        """Find a policy-compliant alternative slot.

        The caller MUST supply `existing_events` covering
        `conflict_window_days(search_days)` from the reference date, or the
        conflict check below is judging some candidate days against an empty
        list. See that helper for what went wrong when the two disagreed.
        """
        if reference_date is None:
            reference_date = datetime.now(self.tz)
        else:
            try:
                reference_date = reference_date.astimezone(self.tz)
            except Exception:
                reference_date = datetime.now(self.tz)

        # Start searching from next business day
        search_start = reference_date + timedelta(days=1)

        for day_offset in range(search_days + _WEEKEND_BUFFER_DAYS):
            candidate_date = search_start + timedelta(days=day_offset)
            weekday = candidate_date.weekday()

            # Skip weekends
            if weekday >= 5:
                continue

            # Generate 30-min increment slots from 09:30 to 17:30 (the last
            # START, not the last end). The comment said 18:00 until
            # 2026-08-25; `range(9, 18)` yields 9..17, so an 18:00 start is
            # never generated and the end guard below never got the chance to
            # judge one. Only the sentence is corrected here. Extending the
            # range would change which alternative time is offered inside a
            # decline message sent to a real organizer, and the calendar
            # auto-reply is the operator's design and is frozen - see the note
            # on `_check_back_to_back`.
            for hour in range(9, 18):
                for minute in [0, 30]:
                    if hour == 9 and minute == 0:
                        continue  # Before 09:30
                    slot_start = candidate_date.replace(
                        hour=hour, minute=minute, second=0, microsecond=0
                    )
                    slot_end = slot_start + timedelta(minutes=duration_minutes)

                    # Don't go past 19:00
                    if slot_end.hour >= 19 or (slot_end.hour == 19 and slot_end.minute > 0):
                        continue

                    # Check protected time
                    if self._check_protected_time_simple(slot_start, slot_end, weekday):
                        continue

                    # Check conflicts with existing events
                    if self._has_conflict(slot_start, slot_end, existing_events):
                        continue

                    # Format the alternative
                    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
                    return f"{day_names[weekday]}, {slot_start.strftime('%B %d')} at {slot_start.strftime('%H:%M')} local time"

        return None

    def _check_protected_time_simple(self, start, end, weekday: int) -> bool:
        """Quick protected time check for slot finding."""
        start_time = start.strftime("%H:%M")
        end_time = end.strftime("%H:%M")

        for block in self.config.get("protected_blocks", []):
            if weekday not in block.get("days", []):
                continue
            if "before" not in block and "after" not in block and "start" not in block:
                return True  # Full day block
            if "before" in block and start_time < block["before"]:
                return True
            if "after" in block and start_time >= block["after"]:
                return True
            if "start" in block and "end" in block:
                if start_time < block["end"] and end_time > block["start"]:
                    return True
        return False

    def _has_conflict(self, slot_start, slot_end, existing_events: list) -> bool:
        """Check if slot conflicts with existing events (with gap buffer)."""
        min_gap = self.config.get("min_gap_minutes", 15)
        buffered_start = slot_start - timedelta(minutes=min_gap)
        buffered_end = slot_end + timedelta(minutes=min_gap)

        for evt in existing_events:
            try:
                evt_start = evt["start"].astimezone(self.tz)
                evt_end = evt["end"].astimezone(self.tz)
            except Exception as e:
                self.logger.debug(f"skipping malformed event in conflict check: {e}")
                continue

            if evt_start.date() != slot_start.date():
                continue

            # Check overlap including gap buffer
            if buffered_start < evt_end and buffered_end > evt_start:
                return True

        return False


# ============================================================
# Telegram Source
# ============================================================

class TelegramSource:
    """Fetch new Telegram messages via Telethon."""

    def __init__(self, config: dict, state: StateManager, logger: logging.Logger):
        self.config = config
        self.state = state
        self.logger = logger
        self.client = None

    async def connect(self):
        from telethon import TelegramClient

        # Reuse existing client — just reconnect the network layer.
        # Creating a new TelegramClient each cycle leaks SQLite handles on
        # Windows, causing "database is locked" on the next reconnect.
        if self.client is not None:
            if not self.client.is_connected():
                await self.client.connect()
            me = await self.client.get_me()
            self.logger.info(f"Telegram reconnected as {me.first_name} (@{me.username})")
            return

        # First-time setup
        api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
        api_hash = os.getenv("TELEGRAM_API_HASH", "")

        if not api_id or not api_hash:
            raise ValueError("Missing Telegram credentials in .env")

        os.makedirs(TELEGRAM_SESSION_DIR, exist_ok=True)

        # WAL checkpoint: clear any stale locks from previous crashes
        session_file = str(TELEGRAM_SESSION_PATH) + ".session"
        if os.path.exists(session_file):
            try:
                import sqlite3 as _sqlite3
                _tmp = _sqlite3.connect(session_file, timeout=5)
                _tmp.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                _tmp.close()
            except sqlite3.Error as e:
                # WAL checkpoint is best-effort cleanup before opening the real session.
                # A locked/corrupt session will surface via the subsequent TelegramClient call.
                self.logger.debug(f"telegram session WAL checkpoint fallback: {e}")

        self.client = TelegramClient(str(TELEGRAM_SESSION_PATH), api_id, api_hash)
        _configure_session_wal(self.client)
        await self.client.connect()

        if not await self.client.is_user_authorized():
            raise RuntimeError("Telegram session not authorized. Run telegram_client.py setup first.")

        me = await self.client.get_me()
        self.logger.info(f"Telegram connected as {me.first_name} (@{me.username})")

    async def check_new(self) -> list:
        if not self.client or not self.client.is_connected():
            await self.connect()

        items = []

        # Check personal DMs (with timeout)
        if self.config.get("check_personal_dms", True):
            try:
                dm_items = await asyncio.wait_for(
                    self._check_personal_dms(), timeout=120
                )
                items.extend(dm_items)
            except asyncio.TimeoutError:
                self.logger.error("Telegram personal DM check timed out (120s)")
            except Exception as e:
                self.logger.error(f"Telegram personal DM check failed: {e}")

        # Check monitored chats (with timeout)
        monitored = self.config.get("monitored_chats", [])
        if monitored:
            try:
                mc_items = await asyncio.wait_for(
                    self._check_monitored_chats(monitored), timeout=120
                )
                items.extend(mc_items)
            except asyncio.TimeoutError:
                self.logger.error("Telegram monitored chats check timed out (120s)")
            except Exception as e:
                self.logger.error(f"Telegram monitored chats check failed: {e}")

        self.logger.info(f"Telegram: {len(items)} new messages")
        return items

    # How many pages of `limit` messages one dialog may drag out of Telegram in
    # a single cycle. A bound is needed - a dialog that has been quiet for a
    # month should not stall the whole cycle - but the bound must be REPORTED
    # when it bites, or the loss is silent again.
    MAX_FETCH_PAGES = 5

    # SEC-014 bounds how long ONE chat may stall the cycle. The single fetch
    # this paging loop replaced carried `timeout=15`, and a per-page timeout of
    # 15 would have quietly raised that ceiling to 75 - five times the budget
    # the control exists to enforce. The whole loop shares one deadline instead,
    # so the bound is unchanged no matter how many pages it takes.
    FETCH_TIMEOUT_SECONDS = 15

    async def _fetch_since(self, entity, last_known_id, limit, chat_name,
                           max_pages=None):
        """Every message above `last_known_id`, newest first, in pages.

        A single `get_messages(limit=N, min_id=cursor)` returns the N NEWEST
        messages above the cursor and nothing older. Both callers then advanced
        the cursor to `max(m.id)`, so whenever more than N messages had arrived
        since the last cycle, every message between the cursor and the oldest
        one fetched was skipped PERMANENTLY, with no log line and no counter. A
        30-message burst in a group chat between two cycles is routine, and the
        default `max_messages_per_chat` is 30.

        Paging backwards with `max_id` closes the gap. `MAX_FETCH_PAGES` bounds
        it, and when the bound is hit the number of messages left behind is
        logged rather than swallowed.

        `max_pages` overrides that bound, and the FIRST-SIGHT caller sets it to
        1. With `last_known_id == 0` there is no lower bound at all, so the
        paging loop does not stop at the unread window: it kept going while
        pages came back full and pulled `MAX_FETCH_PAGES * limit` messages -
        150 at the shipped `max_messages_per_chat: 30` - of which 120 were
        already-read history, concatenated into the alert body and sent to the
        model. That is the backfill the caller's own comment says it is
        avoiding. Measured 2026-08-26 against a 1000-message stand-in dialog.
        """
        collected = []
        next_max_id = 0  # 0 means "no upper bound" in Telethon
        page_budget = self.MAX_FETCH_PAGES if max_pages is None else max_pages

        async def _all_pages():
            nonlocal next_max_id
            for _ in range(page_budget):
                page = await self.client.get_messages(
                    entity, limit=limit, min_id=last_known_id, max_id=next_max_id)
                if not page:
                    return True
                collected.extend(page)
                if len(page) < limit:
                    return True
                next_max_id = min(m.id for m in page)
            return False

        # One deadline for the whole loop (SEC-014), not one per page.
        drained = await asyncio.wait_for(
            _all_pages(), timeout=self.FETCH_TIMEOUT_SECONDS)
        if not drained and last_known_id == 0:
            # No lower bound, so `oldest - last_known_id - 1` is "every message
            # id below the oldest one fetched" - the whole chat history, a
            # number this function never measured against anything unread. It
            # was logged as an alarm about hundreds of missed messages. Say
            # what is true instead.
            self.logger.info(
                f"{chat_name}: first sight, read the newest {len(collected)} "
                f"message(s); older history is not backfilled by design")
        elif not drained:
            # The page cap bit. Say how much was left behind: a silent bound is
            # the original defect in a smaller shape.
            oldest = min((m.id for m in collected), default=last_known_id + 1)
            skipped = oldest - last_known_id - 1
            if skipped > 0:
                self.logger.warning(
                    f"{chat_name}: stopped after {page_budget} pages; "
                    f"{skipped} message(s) between id {last_known_id} and {oldest} "
                    f"were NOT read and will not be reported")
        return collected

    async def _check_personal_dms(self) -> list:
        from telethon import types

        ignore_chats = [c.lower() for c in self.config.get("ignore_chats", [])]
        max_msgs = self.config.get("max_messages_per_chat", 30)

        # Newness is the dialog's top message id against our cursor, NOT the
        # unread badge. Reading a message on the phone clears the badge, and a
        # dialog where the operator wrote last never had one -- both used to
        # make this reader permanently blind to those messages. iter_dialogs
        # already carries the top message, so this test costs no API call.
        pending_dialogs = []
        async for dialog in self.client.iter_dialogs(limit=100):
            if not isinstance(dialog.entity, types.User):
                continue
            if dialog.entity.bot:
                continue
            chat_name = self._entity_name(dialog.entity)
            if chat_name.lower() in ignore_chats:
                continue

            chat_id = str(dialog.entity.id)
            last_known_id = self.state.get_telegram_last_id(chat_id)
            top_id = dialog.message.id if dialog.message else 0

            if top_id and top_id <= last_known_id:
                continue

            if last_known_id == 0:
                # First sight. Seed the cursor instead of backfilling, or the
                # first run after this change drags max_msgs out of every
                # dialog at once. Anything genuinely unread is still reported,
                # so a brand new counterpart is not silent for a cycle.
                if dialog.unread_count == 0:
                    if top_id:
                        self.state.set_telegram_last_id(chat_id, chat_name, top_id)
                    continue
                limit = min(dialog.unread_count, max_msgs)
            else:
                limit = max_msgs

            pending_dialogs.append((dialog, chat_name, last_known_id, limit))

        if not pending_dialogs:
            return []

        async def _fetch_dm(dialog, chat_name, last_known_id, limit):
            chat_id = str(dialog.entity.id)
            try:
                messages = await self._fetch_since(
                    dialog.entity, last_known_id, limit, chat_name,
                    # First sight has no lower bound, so paging would walk the
                    # whole history. One page is the unread window.
                    max_pages=1 if last_known_id == 0 else None)
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout fetching DMs from {chat_name}")
                return None

            if not messages:
                return None

            # The cursor advances over everything fetched, including the
            # operator's own messages. Skipping that would rescan the dialog
            # every cycle for as long as he happened to write last.
            max_id = max(m.id for m in messages)
            self.state.set_telegram_last_id(chat_id, chat_name, max_id)

            combined_text = []
            for msg in reversed(messages):
                if getattr(msg, "out", False):
                    continue  # his own message is not an alert about himself
                if msg.text:
                    combined_text.append(msg.text)
                elif msg.media:
                    combined_text.append("[media attachment]")

            if not combined_text:
                return None

            full_text = "\n---\n".join(combined_text)
            if len(full_text) > 2000:
                full_text = full_text[:2000] + "\n[...truncated]"

            return {
                "source": "telegram",
                "chat_id": chat_id,
                "chat_name": chat_name,
                "sender": chat_name,
                "subject": f"Telegram DM from {chat_name}",
                "body": full_text,
                "date": datetime.now(timezone.utc).isoformat()[:19],
                "message_count": len(combined_text),
                "is_vip": False,
            }

        results = await asyncio.gather(
            *[_fetch_dm(d, name, cursor, limit) for d, name, cursor, limit in pending_dialogs],
            return_exceptions=True,
        )

        items = []
        for result in results:
            if isinstance(result, Exception):
                self.logger.error(f"DM fetch error: {result}")
            elif result is not None:
                items.append(result)

        return items

    async def _check_monitored_chats(self, monitored: list) -> list:
        items = []
        max_msgs = self.config.get("max_messages_per_chat", 30)

        for chat_cfg in monitored:
            chat_name_or_id = chat_cfg.get("name", "")
            priority = chat_cfg.get("priority", "medium")

            try:
                entity = await self._resolve_chat(chat_name_or_id)
            except Exception as e:
                self.logger.warning(f"Could not resolve chat '{chat_name_or_id}': {e}")
                continue

            chat_id = str(entity.id)
            last_known_id = self.state.get_telegram_last_id(chat_id)

            try:
                messages = await self._fetch_since(
                    entity, last_known_id, max_msgs, str(chat_name_or_id))
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout checking chat '{chat_name_or_id}', skipping")
                continue

            if not messages:
                continue

            max_id = max(m.id for m in messages)
            chat_display = self._entity_name(entity)
            self.state.set_telegram_last_id(chat_id, chat_display, max_id)

            combined_text = []
            for msg in reversed(messages):
                sender_name = await self._get_sender_name(msg)
                text = msg.text or "[media]"
                combined_text.append(f"[{sender_name}]: {text}")

            full_text = "\n".join(combined_text)
            if len(full_text) > 2000:
                full_text = full_text[:2000] + "\n[...truncated]"

            items.append({
                "source": "telegram",
                "chat_id": chat_id,
                "chat_name": chat_display,
                "sender": chat_display,
                "subject": f"Telegram Group: {chat_display}",
                "body": full_text,
                "date": datetime.now(timezone.utc).isoformat()[:19],
                "message_count": len(messages),
                "is_vip": priority == "high",
            })

        return items

    async def _resolve_chat(self, identifier: str):
        """Resolve chat identifier (mirrors telegram_client.py logic)."""
        from telethon import errors

        ident = identifier.strip()

        # Try numeric ID
        try:
            num_id = int(ident)
            return await self.client.get_entity(num_id)
        except (ValueError, errors.RPCError):
            pass

        # Try @username
        if ident.startswith("@"):
            return await self.client.get_entity(ident)

        # Try as username without @
        try:
            return await self.client.get_entity(ident)
        except (ValueError, errors.RPCError):
            pass

        # Fuzzy match against dialog names
        ident_lower = ident.lower()
        best_match = None
        best_score = 0
        async for dialog in self.client.iter_dialogs(limit=200):
            name = dialog.name or ""
            name_lower = name.lower()
            if ident_lower == name_lower:
                return dialog.entity
            if ident_lower in name_lower:
                score = len(ident_lower) / len(name_lower) if name_lower else 0
                if score > best_score:
                    best_score = score
                    best_match = dialog.entity

        if best_match and best_score > 0.3:
            return best_match

        raise ValueError(f"Could not resolve chat: '{identifier}'")

    def _entity_name(self, entity) -> str:
        from telethon import types

        if isinstance(entity, types.User):
            parts = [entity.first_name or "", entity.last_name or ""]
            name = " ".join(p for p in parts if p)
            return name or entity.username or str(entity.id)
        if isinstance(entity, (types.Chat, types.Channel)):
            return entity.title or str(entity.id)
        return str(entity)

    async def _get_sender_name(self, msg) -> str:
        if msg.sender:
            return self._entity_name(msg.sender)
        try:
            sender = await msg.get_sender()
            return self._entity_name(sender)
        except Exception:
            return f"User#{msg.sender_id}"

    async def disconnect(self):
        if self.client:
            if self.client.is_connected():
                await self.client.disconnect()
            # Explicitly close the SQLite session handle to release the file
            # lock on Windows. Without this, the GC may not collect the handle
            # before the next reconnect, causing "database is locked".
            session = getattr(self.client, 'session', None)
            if session:
                conn = getattr(session, '_conn', None)
                if conn:
                    try:
                        conn.close()
                        session._conn = None
                    except (sqlite3.Error, AttributeError) as e:
                        # Session handle may already be invalid; safe to ignore.
                        self.logger.debug(f"session _conn close fallback: {e}")


# ============================================================
# Urgency Analyzer
# ============================================================

class UrgencyAnalyzer:
    """Classify message urgency using Claude API."""

    # `{operator}` is filled from `get_operator()`, which resolves from the DATA
    # overlay and yields "Operator" on a fresh clone. It used to be the
    # operator's name, employer and product hardcoded here, so a stranger who
    # cloned the public engine got a triage system that believed it worked for
    # somebody else. What the business actually IS belongs in
    # `{business_context}`, loaded from the overlay's `context_files` -- which
    # is the mechanism that already exists for exactly this.
    SYSTEM_PROMPT = """You are Sentinel, an urgency triage system for {operator}.

Your job: analyze incoming messages and score their urgency on a 1-10 scale.
{operator} is extremely busy; treat their attention as the scarce resource.

URGENCY SCORING GUIDE:
- 9-10: CRITICAL - Requires immediate action. Deal at risk, security incident, investor/partner emergency, legal deadline, production system down.
- 7-8: HIGH - Needs attention today. Important partner/client communication, time-sensitive opportunity, meeting confirmation needed, financial matter.
- 5-6: MEDIUM - Should see within 24 hours. Business update, non-urgent partner message, internal Tribe matter, follow-up request.
- 3-4: LOW - Can wait. Informational, routine approvals, general updates.
- 1-2: NOISE - Ignore. Spam, automated notifications, marketing, bulk newsletters.

{business_context}

RECOMMENDED ACTIONS - be specific and CEO-appropriate. Use one of these categories:
- "Reply needed: [draft key points to address]" - when a response from the CEO is expected
- "Forward to [person/role] for handling" - when delegation is appropriate
- "Schedule follow-up: [topic] by [timeframe]" - for items needing future action
- "Review attachment: [what to look for]" - for documents needing CEO eyes
- "Approve/Decide: [what decision is needed]" - for items awaiting CEO decision
- "FYI only - no action needed" - for informational items
- "Escalate: [why and to whom]" - for items requiring immediate escalation

Consider the priorities named in the business context above. Flag anything affecting deal velocity, partner relationships, or product delivery.

Respond ONLY in this JSON format (no markdown, no code fences):
{{"urgency_score": <1-10>, "reason": "<1 sentence>", "summary": "<2-3 sentences>", "recommended_action": "<specific action using categories above>"}}"""

    def __init__(self, config: dict, logger: logging.Logger):
        self.model = config.get("model") or claude_models.latest("haiku")
        self.max_tokens = config.get("max_tokens", 500)
        self.logger = logger
        self.client = None
        self.business_context = ""
        self.operator_name = get_operator()["name"]

        # Load business context from files
        context_files = config.get("context_files", [])
        self._load_business_context(context_files)

    def _load_business_context(self, context_files: list):
        parts = []
        for rel_path in context_files:
            full_path = WORKSPACE_ROOT / rel_path
            if full_path.exists():
                try:
                    content = full_path.read_text(encoding="utf-8")
                    # Truncate each context file to keep prompt manageable
                    if len(content) > 3000:
                        content = content[:3000] + "\n[...truncated]"
                    parts.append(f"--- {rel_path} ---\n{content}")
                except Exception as e:
                    self.logger.warning(f"Could not read context file {rel_path}: {e}")

        if parts:
            self.business_context = "BUSINESS CONTEXT:\n" + "\n\n".join(parts)

    def _get_client(self):
        if self.client is None:
            import anthropic
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY not found in .env. "
                    "Get one at console.anthropic.com and add to .env"
                )
            self.client = anthropic.Anthropic(api_key=api_key)
        return self.client

    def _format_item_prompt(self, item: dict, index: int = None) -> str:
        """Format a single item for the LLM prompt."""
        attachments_str = ""
        if item.get("attachments"):
            attachments_str = f"\nATTACHMENTS: {', '.join(item['attachments'])}"

        prefix = f"MESSAGE {index}:\n" if index is not None else ""
        return f"""{prefix}SOURCE: {item.get('source', 'unknown')}
FROM: {item.get('sender', 'unknown')} ({item.get('sender_email', item.get('chat_name', ''))})
DATE: {item.get('date', '')}
SUBJECT: {item.get('subject', '')}
BODY:
---
{item.get('body', '(empty)')}
---{attachments_str}"""

    def _extract_json(self, text: str) -> dict:
        """Extract first valid JSON object from text."""
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```\s*$", "", text)

        # `raw_decode` and not a brace counter. The counter that used to sit
        # here read every `{` and `}` in the text, including the ones inside
        # JSON string values, and failed in three different ways on responses
        # that were valid JSON:
        #
        #   {"summary": "a } b", ...}      the count hit zero mid-string, the
        #                                  object was cut in half -> Unterminated
        #                                  string
        #   {"summary": "use { braces"}    the count never returned to zero, so
        #     ...trailing prose            NO truncation happened at all and the
        #                                  model's closing sentence reached
        #                                  json.loads -> Extra data
        #   {"s": "he said \\"} \\" ok"}     an escaped quote, same as the first
        #
        # Each landed in the caller's JSONDecodeError branch, which returns
        # urgency 5 and "LLM response could not be parsed" - a made-up middling
        # score standing in for a real one the model actually sent.
        #
        # `json.JSONDecoder().raw_decode` parses ONE value from an offset and
        # reports where it ended, so it is string-literal aware by construction
        # and cutting the object out of surrounding prose is what it is for.
        start = text.find("{")
        if start == -1:
            # No object at all. Let json.loads produce the error, so a scalar
            # or an empty response still reaches the caller's existing branch.
            return json.loads(text)
        obj, _end = json.JSONDecoder().raw_decode(text, start)
        return obj

    @observe()
    def analyze(self, item: dict) -> dict:
        """Analyze a single item for urgency. Returns dict with score and details."""
        client = self._get_client()

        system_prompt = self.SYSTEM_PROMPT.format(
            business_context=self.business_context, operator=self.operator_name)

        user_prompt = f"Analyze this incoming message:\n\n{self._format_item_prompt(item)}"

        try:
            r = call_anthropic_with_fallback(
                client=client,
                model=self.model,
                max_tokens=self.max_tokens,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_prompt}],
                skill_name="sentinel.analyze",
            )
            return self._extract_json(r.text)

        except json.JSONDecodeError as e:
            self.logger.error(f"LLM returned invalid JSON: {e}")
            return {
                "urgency_score": 5,
                "reason": "LLM response could not be parsed",
                "summary": item.get("subject", "Unknown message"),
                "recommended_action": "Review manually",
            }
        except Exception as e:
            self.logger.error(f"LLM analysis failed across all vendors: {e}")
            return None

    @observe()
    @staticmethod
    def _clamp_score(value) -> int:
        """A usable 1-10 urgency, whatever the model returned.

        `urgency_score` was trusted to be an int. A well-formed
        `{"urgency_score": "8"}` parses fine and then raises TypeError at the
        first `score >= threshold`, or later in the digest's sort -- and
        `record_digest_item` had already poisoned `items_by_urgency`, so every
        subsequent evening digest crashed too.
        """
        try:
            score = int(float(value))
        except (TypeError, ValueError):
            return 5
        return max(1, min(10, score))

    def analyze_batch(self, items: list) -> list:
        """Analyze multiple items in a single LLM call. Returns list of dicts."""
        if not items:
            return []
        if len(items) == 1:
            result = self.analyze(items[0])
            return [result]

        client = self._get_client()
        system_prompt = self.SYSTEM_PROMPT.format(
            business_context=self.business_context, operator=self.operator_name)

        # Build combined prompt
        parts = [f"Analyze these {len(items)} incoming messages. For EACH message, provide a separate JSON object.\n"
                 f"Respond with a JSON array containing one object per message, in the same order.\n"]
        for i, item in enumerate(items, 1):
            parts.append(self._format_item_prompt(item, index=i))
        user_prompt = "\n\n".join(parts)

        try:
            r = call_anthropic_with_fallback(
                client=client,
                model=self.model,
                max_tokens=self.max_tokens * min(len(items), 8),
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_prompt}],
                skill_name="sentinel.analyze_batch",
            )

            result_text = r.text
            # Strip markdown code fences
            if result_text.startswith("```"):
                result_text = re.sub(r"^```(?:json)?\s*", "", result_text)
                result_text = re.sub(r"\s*```\s*$", "", result_text)

            parsed = json.loads(result_text)
            if isinstance(parsed, list):
                # ELEMENT type, not just the container. The guard below covers
                # "the reply is not a list"; a reply that IS a list of scalars
                # (`["urgent", "ignore"]`, `[1, 2]`) walked straight past it,
                # got padded to length, and every consumer then called
                # `analysis.get(...)` on a string. AttributeError, out of a
                # daemon whose `run_cycle` and `start` catch neither - the exact
                # shape the comment twenty lines below records for the outer
                # type, one level in. A model that answered the wrong SHAPE is
                # no more trustworthy per-element than per-response, so the
                # whole batch falls back rather than one item.
                if any(not isinstance(entry, dict) for entry in parsed):
                    kinds = sorted({type(e).__name__ for e in parsed
                                    if not isinstance(e, dict)})
                    self.logger.warning(
                        f"Batch LLM analysis returned a list containing "
                        f"{', '.join(kinds)} where objects were required; "
                        f"falling back to individual calls")
                    return [self.analyze(item) for item in items]
                # Pad or truncate to match input length
                while len(parsed) < len(items):
                    parsed.append({
                        "urgency_score": 5,
                        "reason": "Missing from batch response",
                        "summary": items[len(parsed)].get("subject", "Unknown"),
                        "recommended_action": "Review manually",
                    })
                return parsed[:len(items)]
            elif isinstance(parsed, dict):
                # Single object returned despite batch request — use for first item
                return [parsed] + [self.analyze(item) for item in items[1:]]
            else:
                # Valid JSON that is neither list nor dict -- a bare string or
                # number. There was no else here, so the function fell off the
                # end and returned None; `all_analyses.extend(None)` then raised
                # TypeError, and neither run_cycle nor start() catches it, so one
                # type-deviant response killed the daemon. Same fallback as a
                # parse error, for the same reason.
                self.logger.warning(
                    f"Batch LLM analysis returned {type(parsed).__name__}, not a "
                    f"list or object; falling back to individual calls")
                return [self.analyze(item) for item in items]

        except json.JSONDecodeError as e:
            self.logger.warning(f"Batch LLM analysis JSON parse error ({e}), falling back to individual calls")
            return [self.analyze(item) for item in items]
        except Exception as e:
            self.logger.warning(f"Batch LLM analysis unexpected error ({e}), falling back to individual calls")
            return [self.analyze(item) for item in items]


# ============================================================
# Telegram Notifier
# ============================================================

def resolve_notify_target() -> str:
    """Bot-resolvable notification target, read from .env only.

    SENTINEL_TELEGRAM_TARGET -> ODIN_CADENCE_TELEGRAM_TARGET -> "" (no send),
    mirroring scripts/reminders-notify.py so every HEADING OS notification
    lands in one place.

    The sentinel_config.yaml `notification.target_chat` name is deliberately
    NOT consulted: it names a channel for the userbot, and the bot transport
    cannot resolve a human-readable channel name.

    Each candidate is stripped BEFORE the chain, never once at the end. Stripped
    afterwards, a whitespace-only `SENTINEL_TELEGRAM_TARGET` is truthy, so it
    wins the `or` chain over a perfectly good `ODIN_CADENCE_TELEGRAM_TARGET` and
    only then collapses to "". Measured 2026-08-07: with the two set to `"   "`
    and `"100200300"`, this returned `""`, `Sentinel.start` logged "alerts will
    NOT be delivered", and the daemon ran on watching everything and telling
    nobody. A trailing space on an edited `.env` line is the ordinary way a
    value becomes whitespace-only, and the fallback exists precisely so that a
    missing specific target is survivable.
    """
    load_env(WORKSPACE_ROOT)
    return (
        (os.environ.get("SENTINEL_TELEGRAM_TARGET") or "").strip()
        or (os.environ.get("ODIN_CADENCE_TELEGRAM_TARGET") or "").strip()
    )


class TelegramNotifier:
    """Send urgent notifications through the HEADING OS notifications bot.

    Delivery is the shared bot transport (scripts/utils/telegram_notify), not
    the userbot client: a Bot API sendMessage always push-notifies, whereas a
    message the userbot posts into a channel it already owns does not
    reliably. Because the bot needs no Telethon session, this notifier is
    independent of whether Telegram *reading* is connected.
    """

    def __init__(self, target: str, logger: logging.Logger, dry_run: bool = False):
        self.target = target
        self.logger = logger
        self.dry_run = dry_run

    async def _send(self, message: str) -> bool:
        """Hand one message to the bot transport. Never raises."""
        if self.dry_run:
            self.logger.info(f"Dry-run: notification NOT sent:\n{message}")
            return True
        # notify() is blocking urllib; keep the daemon's event loop free.
        ok = await asyncio.to_thread(telegram_notify.notify, self.target, message)
        if not ok:
            self.logger.error(
                "Notification send failed (see telegram_notify log). Check "
                "TELEGRAM_NOTIFY_BOT_TOKEN and SENTINEL_TELEGRAM_TARGET in .env."
            )
        return ok

    async def send_notification(self, item: dict, analysis: dict):
        if await self._send(self._format_message(item, analysis)):
            # `_clamp_score(analysis.get(...))`, not `analysis['urgency_score']`.
            # The hard subscript ran AFTER the alert was already on the wire, so
            # a response missing the key raised out of here into the caller's
            # `except Exception`, which logged "Notification failed" for an
            # alert that WAS delivered - the exact inversion the send-failure
            # test exists to prevent, in the other direction. It also cost the
            # `urgent_sent` counter (so the digest under-reported) and the
            # `notified_hashes` entry (so a byte-identical repeat re-alerted).
            # Every other reader of this field already goes through _clamp_score.
            score = UrgencyAnalyzer._clamp_score(analysis.get("urgency_score"))
            self.logger.info(
                f"Notification sent: [{score}/10] {item.get('subject', '')}")

    async def send_digest(self, message: str):
        if await self._send(message):
            self.logger.info("Digest sent")

    def _format_message(self, item: dict, analysis: dict) -> str:
        score = UrgencyAnalyzer._clamp_score(analysis.get("urgency_score"))

        if score >= 9:
            icon = "\U0001f534"  # red circle
            label = "CRITICAL"
        elif score >= 7:
            icon = "\U0001f7e1"  # yellow circle
            label = "HIGH"
        else:
            icon = "\U0001f7e0"  # orange circle
            label = "ALERT"

        source = item.get("source", "unknown").upper()
        source_icon = "\U0001f4e9" if source == "EMAIL" else "\U0001f4ac"

        sender_line = item.get("sender", "Unknown")
        if item.get("sender_email"):
            sender_line += f" <{item['sender_email']}>"

        subject_line = ""
        if item.get("subject") and item["source"] == "email":
            subject_line = f"\n\U0001f4cb Subject: {item['subject']}"

        # Snippet of original
        body = item.get("body", "")
        snippet = body[:300].strip()
        if len(body) > 300:
            snippet += "..."

        msg = f"""{icon} {label} [{score}/10] -- {source}

{source_icon} From: {sender_line}
\U0001f4c5 {item.get('date', 'unknown')}{subject_line}

Summary: {analysis.get('summary', 'N/A')}

\u26a1 Why urgent: {analysis.get('reason', 'N/A')}

\u2705 Action: {analysis.get('recommended_action', 'Review manually')}

--- Original snippet ---
\"{snippet}\""""

        return msg


# ============================================================
# Sentinel Daemon
# ============================================================

class Sentinel:
    """Main orchestrator."""

    def __init__(self, config_path: Path = CONFIG_FILE, dry_run: bool = False,
                 once: bool = False):
        self.config = SentinelConfig(config_path)
        self.dry_run = dry_run
        # `once` is a LIVE single cycle, which is not the same thing as
        # `dry_run`. `scripts/utils/schedule.py` installs a 15-minute timer for
        # every provisioned exec, on all three platforms, running
        # `scripts/sentinel.py --check` - a flag this file never defined, so
        # argparse exited 2 with "unrecognized arguments" every fifteen minutes
        # and no cycle ever ran. `--test` could not stand in: it is a true dry
        # run, so it neither sends a notification nor writes state back.
        self.once = once
        self.logger = self._setup_logging()
        # A dry run reads the real state but never writes it back.
        self.state = StateManager(read_only=dry_run)
        self.email_source = EmailSource(self.config.email, self.state, self.logger)
        self.telegram_source = TelegramSource(self.config.telegram, self.state, self.logger)
        self.analyzer = UrgencyAnalyzer(self.config.llm, self.logger)
        self.notifier = None  # Initialized after Telegram connects
        self._running = True
        self._stop_event = asyncio.Event()
        self._consecutive_email_failures = 0
        self._consecutive_tg_failures = 0
        self._heartbeat_task = None

        # Calendar invite monitoring
        self.invite_source = MeetingInviteSource(self.config.calendar, self.state, self.logger)
        self.policy_engine = CalendarPolicyEngine(
            self.config.calendar, self.config.timezone, self.logger,
            analyzer=self.analyzer,
        )

    def _setup_logging(self) -> logging.Logger:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        # R12: mint trace ID + install record factory before any handler so
        # every line carries [trace_id].
        tracing.mint()
        install_log_factory()
        logger = logging.getLogger("sentinel")
        logger.setLevel(getattr(logging, self.config.log_level, logging.INFO))

        # File handler (rotating, 5MB x 3 files)
        fh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(trace_id)s] %(message)s"))
        logger.addHandler(fh)

        # Console handler
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(trace_id)s] %(message)s"))
        logger.addHandler(ch)

        return logger

    async def _heartbeat_loop(self):
        """R14: dedicated 60s liveness beat, decoupled from the up-to-15-min
        work cycle. A beat tied to the work loop would advance the heartbeat
        at most every check_interval, forcing the watchdog grace above the
        cycle length and delaying crash detection. One file:
        .daemon-state/heartbeats/sentinel.json. Beats once immediately, then
        every 60s until cancelled at shutdown."""
        try:
            while True:
                daemon_heartbeat.beat("sentinel")
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            return

    def install_signal_handlers(self) -> str:
        """Wire SIGINT/SIGTERM into `_stop_event` THROUGH THE RUNNING LOOP.

        `main()` registers the same intent with `signal.signal`, and that alone
        does not work. A `signal.signal` handler runs between bytecodes; the
        loop is blocked in `select()` with no pending callback, so nothing wakes
        it and `Event.set()` sits unnoticed until the current
        `asyncio.wait_for` timeout expires on its own. Measured 2026-08-27
        against the exact shape of the wait in `start()`: SIGTERM delivered at
        2 s, `wait_for(..., timeout=30)` returned at **29.54 s**. With a real
        check_interval that is up to fifteen minutes of "shutting down".

        `loop.add_signal_handler` writes to the loop's self-pipe, which IS the
        thing `select()` is watching, so the wait returns immediately: the same
        probe returned at 1.84 s, i.e. the moment the signal arrived.

        Returns the mechanism actually installed, so a caller can say which one
        it got. Windows' event loops raise NotImplementedError here and keep the
        `signal.signal` path `main()` already set up; that is the platform's
        limit, not a choice.
        """
        loop = asyncio.get_running_loop()

        def _request_stop(name: str) -> None:
            self.logger.info(f"Shutdown signal received ({name}); stopping.")
            self._running = False
            self._stop_event.set()

        installed = "loop"
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_stop, sig.name)
            except (NotImplementedError, AttributeError, ValueError, RuntimeError):
                # NotImplementedError: Windows. ValueError/RuntimeError: not the
                # main thread. Either way the process keeps the handler main()
                # installed, which stops the loop late rather than not at all.
                installed = "signal.signal"
        return installed

    async def start(self):
        self.install_signal_handlers()
        self.logger.info("=" * 50)
        self.logger.info("Sentinel starting...")
        self.logger.info(f"Check interval: {self.config.check_interval // 60} minutes")
        self.logger.info(f"Urgency threshold: {self.config.urgency_threshold}")
        self.logger.info(f"Dry run: {self.dry_run}")
        self.logger.info("=" * 50)

        # Write PID file with file lock (SEC-016)
        RUNTIME_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        my_pid = os.getpid()
        # "a+" and NOT "w". `open(path, "w")` is O_WRONLY|O_CREAT|O_TRUNC, and
        # the truncation happens at open(2) - BEFORE the flock below can fail.
        # So a second instance starting beside a healthy daemon emptied the live
        # daemon's PID file, then exited on the lock. The write-back at the
        # bottom of this block is never reached on that branch, so the file
        # stayed zero-length: `--status` printed UNKNOWN, `--stop` deleted the
        # file without signalling anything, and the only way left to stop the
        # running daemon was a manual pkill. "a+" never truncates, so the losing
        # instance leaves the file exactly as it found it.
        self._pid_file_handle = open(PID_FILE, "a+")
        try:
            if sys.platform != "win32":
                import fcntl
                fcntl.flock(self._pid_file_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # WINDOWS HAS NO LOCK HERE, and the justification that used to sit
            # on this line was false: it claimed "Windows already prevents two
            # processes from writing the same PID file atomically". It does not.
            # Two processes can each open this path with "w" and truncate it, so
            # on Windows two daemons -- or a --test beside the live one -- can
            # run concurrently, double-notify, race on state.json and the
            # Telethon SQLite session, and leave --stop pointing at the wrong
            # PID. This daemon runs on Linux, where the flock above is real, so
            # the gap is named rather than papered over; closing it needs an
            # msvcrt byte-range lock (or an O_CREAT|O_EXCL lock file) held for
            # the process lifetime, plus a separate PID namespace for --test.
        except (IOError, OSError):
            self._pid_file_handle.close()
            self.logger.error("Another Sentinel instance is already running (PID file locked)")
            sys.exit(1)
        # The lock is held from here on, so this is the first moment at which
        # emptying the file is safe. "a+" left any previous content in place.
        self._pid_file_handle.seek(0)
        self._pid_file_handle.truncate()
        self._pid_file_handle.write(str(my_pid))
        self._pid_file_handle.flush()
        if sys.platform == "win32":
            # Close immediately on Windows - keeping it open locks it from readers
            self._pid_file_handle.close()
            self._pid_file_handle = None
        self.logger.info(f"PID file written: {PID_FILE} (PID: {my_pid})")

        # R14: start the dedicated 60s liveness heartbeat before the (possibly
        # slow) Telegram/Exchange connects so the watchdog sees the daemon
        # alive within the first second of boot.
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        # Notifications go out over the bot transport, which needs no Telethon
        # session -- so the notifier is built before (and independently of) the
        # Telegram *reading* connect below.
        target = resolve_notify_target()
        self.notifier = TelegramNotifier(target, self.logger, dry_run=self.dry_run)
        if self.dry_run:
            self.logger.info("Dry-run: notifications will be logged, not sent")
        elif target:
            self.logger.info(f"Notifications route to bot target {target}")
        else:
            self.logger.error(
                "No notification target set (SENTINEL_TELEGRAM_TARGET / "
                "ODIN_CADENCE_TELEGRAM_TARGET in .env) -- alerts will NOT be delivered"
            )

        # Connect Telegram (needed for reading Telegram sources)
        # `enabled OR not dry_run` is always true in live mode, so the flag was
        # dead at the one place it matters: an operator who set
        # `telegram.enabled: false` still had connect() called, and connect()
        # raises ValueError on absent credentials with no handler here -- so
        # email-only monitoring could not boot at all.
        if self.config.telegram.get("enabled", True):
            await self.telegram_source.connect()

        # Connect Exchange
        if self.config.email.get("enabled", True):
            try:
                self.email_source.connect()
                # Share Exchange account with invite source
                if self.config.calendar.get("enabled", False):
                    self.invite_source.account = self.email_source.account
                    self.logger.info("Calendar invite monitoring enabled")
            except Exception as e:
                self.logger.error(f"Exchange connection failed: {e}")
                self.logger.info("Will retry on next cycle")

        # Run loop
        try:
            while self._running:
                # Reconnect Telegram at cycle start (was disconnected after last cycle)
                if self.config.telegram.get("enabled", True):
                    try:
                        if not self.telegram_source.client or not self.telegram_source.client.is_connected():
                            await self.telegram_source.connect()
                    except Exception as e:
                        self.logger.error(f"Telegram reconnect at cycle start failed: {e}")

                await self.run_cycle()
                if self.dry_run or self.once:
                    break
                # Deadman: a completed work cycle pings the Healthchecks.io
                # check so a silently-stuck sentinel (hung Telegram/Exchange,
                # crashed loop) trips an external alert. Best-effort, never
                # raises (see scripts/utils/healthchecks.ping).
                hc_ping("STEWARD_HC_SENTINEL")

                # Disconnect Telegram during sleep to release SQLite session lock
                if self.config.telegram.get("enabled", True):
                    try:
                        await self.telegram_source.disconnect()
                        self.logger.debug("Telegram disconnected (releasing session lock for sleep)")
                    except Exception as e:
                        # Disconnect can fail on network errors, already-closed sockets, or telethon internals.
                        # Lock release will happen via GC; logging the specific cause aids debugging.
                        self.logger.debug(f"Telegram disconnect-for-sleep fallback: {e}")

                self.logger.info(f"Next check in {self.config.check_interval // 60} minutes")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self.config.check_interval)
                except asyncio.TimeoutError:
                    pass  # Normal: interval elapsed, continue loop
                if self._stop_event.is_set():
                    break
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def run_cycle(self):
        cycle_start = datetime.now(timezone.utc)
        self.logger.info("-" * 40)
        self.logger.info(f"Cycle starting at {cycle_start.isoformat()[:19]}")
        try:
            await self._run_cycle_body(cycle_start)
        finally:
            self.state.save()

    async def _run_cycle_body(self, cycle_start):

        # Reset daily digest counters if new day
        now_local = datetime.now(self.config.timezone)
        today_str = now_local.strftime("%Y-%m-%d")
        self.state.reset_daily_digest(today_str)
        self.state.reset_calendar_daily(today_str)

        # Cleanup old notification hashes and rotate stale state
        cooldown = self.config.notification.get("dedup_cooldown_minutes", 60)
        self.state.cleanup_old_hashes(cooldown)
        self.state.rotate_old_state(max_age_days=30)

        items = []

        # --- Email check ---
        if self.config.email.get("enabled", True):
            try:
                email_items = self.email_source.check_new()
                items.extend(email_items)
                self.state.data["digest"]["emails_checked"] += len(email_items)
                self._consecutive_email_failures = 0
                # Keep invite source in sync with email account
                if self.config.calendar.get("enabled", False) and not self.invite_source.account:
                    self.invite_source.account = self.email_source.account
            except Exception as e:
                self.logger.error(f"Email check failed: {e}")
                self._consecutive_email_failures += 1
                # Force reconnect on connection errors (socket reset, timeout, etc.)
                if "Connection" in str(e) or "connection" in str(e) or "timeout" in str(type(e).__name__).lower():
                    self.logger.warning("Connection error -- forcing reconnect next cycle")
                    self.email_source.account = None
                    self.invite_source.account = None
                elif self._consecutive_email_failures >= 3:
                    self.logger.warning("3 consecutive email failures -- reconnecting next cycle")
                    self.email_source.account = None
                    self.invite_source.account = None

        # --- Meeting invite check ---
        if self.config.calendar.get("enabled", False) and self.invite_source.account:
            try:
                await self._process_meeting_invites()
            except Exception as e:
                self.logger.error(f"Meeting invite check failed: {e}")

        # --- Telegram check (with sqlite3 DB lock retry) ---
        if self.config.telegram.get("enabled", True):
            tg_success = False
            for tg_attempt in range(1, 4):  # up to 3 attempts
                try:
                    tg_items = await self.telegram_source.check_new()
                    items.extend(tg_items)
                    self.state.data["digest"]["tg_messages_checked"] += len(tg_items)
                    self._consecutive_tg_failures = 0
                    tg_success = True
                    break
                except (sqlite3.OperationalError, OSError) as e:
                    if 'locked' in str(e).lower() and tg_attempt < 3:
                        delay = 2 * tg_attempt
                        self.logger.warning(
                            f"Session DB locked (attempt {tg_attempt}/3), retrying in {delay}s..."
                        )
                        try:
                            await self.telegram_source.disconnect()
                        except Exception as disc_err:
                            # Retry-recovery disconnect; any error here is non-fatal
                            # since the next connect() attempts a fresh session.
                            self.logger.debug(f"Telegram retry-disconnect fallback: {disc_err}")
                        await asyncio.sleep(delay)
                        continue
                    self.logger.error(f"Telegram check failed: {e}")
                    self._consecutive_tg_failures += 1
                    break
                except Exception as e:
                    self.logger.error(f"Telegram check failed: {e}")
                    self._consecutive_tg_failures += 1
                    break

            if not tg_success and self._consecutive_tg_failures >= 3:
                self.logger.warning("3 consecutive Telegram failures -- reconnecting")
                try:
                    await self.telegram_source.disconnect()
                    await self.telegram_source.connect()
                except Exception as re_err:
                    self.logger.error(f"Telegram reconnect failed: {re_err}")

        # --- Analyze and notify ---
        if items:
            await self._analyze_and_notify(items)

        # --- Check if digest is due ---
        await self._check_digest_schedule(now_local)

        # Update state
        self.state.data["last_run"] = cycle_start.isoformat()
        self.state.data["email"]["last_check"] = cycle_start.isoformat()
        self.state.data["telegram"]["last_check"] = cycle_start.isoformat()
        self.state.save()

        elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        self.logger.info(f"Cycle complete: {len(items)} items in {elapsed:.1f}s")

    def _mark_item_processed(self, item: dict) -> None:
        """Record that an email reached a terminal outcome this cycle."""
        if item.get("source") == "email" and item.get("message_id"):
            self.state.mark_email_processed(item["message_id"])

    async def _analyze_and_notify(self, items: list):
        threshold = self.config.urgency_threshold
        max_notifs = self.config.notification.get("max_notifications_per_cycle", 10)
        sent_count = 0

        # Pre-process: dedup and collect items needing LLM analysis.
        #
        # Emails are NOT marked processed here any more. Marking before analysis
        # meant an Anthropic outage, or a cycle that hit
        # max_notifications_per_cycle, permanently consumed the message: never
        # retried, never in the digest, never notified. Silence is the worst
        # failure an urgency monitor has. A message is now marked only once it
        # reaches a terminal outcome below.
        items_to_analyze = []  # (item, content_hash) pairs
        for item in items:
            content_hash = hashlib.md5(
                f"{item.get('source')}{item.get('sender')}{item.get('body', '')[:500]}".encode(),
                usedforsecurity=False,
            ).hexdigest()

            if self.state.is_already_notified(content_hash):
                self.logger.debug(f"Skipping duplicate: {item.get('subject', '')}")
                continue

            items_to_analyze.append((item, content_hash))

        if not items_to_analyze:
            return

        # Batch LLM analysis (up to 8 items per call to stay within token limits)
        BATCH_SIZE = 8
        all_analyses = []
        for batch_start in range(0, len(items_to_analyze), BATCH_SIZE):
            batch = items_to_analyze[batch_start:batch_start + BATCH_SIZE]
            batch_items = [pair[0] for pair in batch]
            batch_results = self.analyzer.analyze_batch(batch_items)
            all_analyses.extend(batch_results)

        # Process results
        for (item, content_hash), analysis in zip(items_to_analyze, all_analyses):
            if analysis is None:
                # LLM failed. NOT marked processed: the next cycle retries it.
                # The VIP fallback below is a best-effort notify on top of that,
                # not a substitute for the retry.
                if item.get("is_vip") and self.notifier:
                    fallback_analysis = {
                        "urgency_score": 7,
                        "reason": "VIP sender (LLM unavailable)",
                        "summary": item.get("subject", "Message from VIP contact"),
                        "recommended_action": "Review this message manually",
                    }
                    try:
                        await self.notifier.send_notification(item, fallback_analysis)
                        self.state.mark_notified(content_hash)
                        sent_count += 1
                    except Exception as e:
                        self.logger.error(f"Fallback notification failed: {e}")
                continue

            score = UrgencyAnalyzer._clamp_score(analysis.get("urgency_score"))
            self.state.record_digest_item(item, score)
            # Terminal: it has been scored and it is in the digest, so it is
            # accounted for whether or not a notification goes out below.
            self._mark_item_processed(item)

            self.logger.info(
                f"  [{score}/10] {item.get('source')}: {item.get('sender')} - {item.get('subject', '')[:60]}"
            )

            if score >= threshold and self.notifier and sent_count < max_notifs:
                try:
                    await self.notifier.send_notification(item, analysis)
                    self.state.mark_notified(content_hash)
                    self.state.data["digest"]["urgent_sent"] += 1
                    sent_count += 1
                except Exception as e:
                    self.logger.error(f"Notification failed: {e}")

    async def _check_digest_schedule(self, now_local: datetime):
        if not self.config.digest.get("enabled", False) or not self.notifier:
            return

        current_time = now_local.strftime("%H:%M")
        morning = self.config.digest.get("morning_time", "08:00")
        evening = self.config.digest.get("evening_time", "22:00")

        # The window is COMPUTED from the configured interval, never assumed to
        # match it. The comment here used to assert the match while the window
        # was the literal 15 in `_time_in_window` and the interval was
        # `check_interval_minutes`, so at any other interval the digest could
        # never fire. It also missed at the SHIPPED default: the run loop sleeps
        # check_interval AFTER each cycle finishes, so the true period is the
        # interval plus the cycle's own duration and the start time drifts
        # forward. A day's cycles straddling 07:58 -> 08:16 skipped 08:00-08:15
        # with no config change at all.
        morning_due = self._time_in_window(current_time, morning)
        evening_due = self._time_in_window(current_time, evening)

        # Prevent double-sending with state key
        digest_state = self.state.data.get("digest", {})
        today_str = now_local.strftime("%Y-%m-%d")

        if morning_due and digest_state.get("morning_sent") != today_str:
            msg = self._build_morning_digest(now_local)
            try:
                await self.notifier.send_digest(msg)
                self.state.data.setdefault("digest", {})["morning_sent"] = today_str
            except Exception as e:
                self.logger.error(f"Morning digest failed: {e}")

        if evening_due and digest_state.get("evening_sent") != today_str:
            msg = self._build_evening_digest(now_local)
            try:
                await self.notifier.send_digest(msg)
                self.state.data.setdefault("digest", {})["evening_sent"] = today_str
            except Exception as e:
                self.logger.error(f"Evening digest failed: {e}")

    def _digest_window_minutes(self) -> int:
        """How long after its target time a digest may still fire.

        Twice the configured check interval, floored at 30 minutes. Twice,
        because the real period is the interval plus however long a cycle takes
        (Exchange fetch, Telethon connect, batched LLM calls), and a window
        merely EQUAL to the interval is missed the moment a cycle runs long. The
        floor keeps a very short interval from producing a window so tight that
        one slow cycle skips the day's digest.

        Firing late cannot double-send: `_check_digest_schedule` only sends when
        the `morning_sent` / `evening_sent` date key is not today's.
        """
        return max(2 * (self.config.check_interval // 60), 30)

    def _time_in_window(self, current: str, target: str) -> bool:
        """True when `current` is at or after `target`, within the digest window."""
        try:
            c_h, c_m = map(int, current.split(":"))
            t_h, t_m = map(int, target.split(":"))
            c_mins = c_h * 60 + c_m
            t_mins = t_h * 60 + t_m
            return 0 <= (c_mins - t_mins) < self._digest_window_minutes()
        except (ValueError, IndexError):
            return False

    def _build_morning_digest(self, now_local: datetime) -> str:
        d = self.state.data.get("digest", {})
        items = d.get("items_by_urgency", [])
        urgent_count = d.get("urgent_sent", 0)

        # Top 3 items by urgency
        sorted_items = sorted(items, key=lambda x: x.get("urgency", 0), reverse=True)[:3]
        top_items_str = ""
        for i, it in enumerate(sorted_items, 1):
            top_items_str += f"\n  {i}. [{it.get('urgency', '?')}/10] {it.get('source', '?').upper()}: {it.get('sender', '?')} - {it.get('subject', '')[:50]}"

        if not top_items_str:
            top_items_str = "\n  No items processed overnight"

        return f"""\U0001f4ca Morning Brief -- {now_local.strftime('%Y-%m-%d %H:%M')}

Overnight summary:
  \U0001f4e7 Emails checked: {d.get('emails_checked', 0)}
  \U0001f4ac Telegram messages: {d.get('tg_messages_checked', 0)}
  \U0001f6a8 Urgent alerts sent: {urgent_count}

Top items by urgency:{top_items_str}"""

    def _build_evening_digest(self, now_local: datetime) -> str:
        d = self.state.data.get("digest", {})
        items = d.get("items_by_urgency", [])
        urgent_count = d.get("urgent_sent", 0)

        # Medium items (5-6 score) that didn't trigger alerts
        medium_items = [
            it for it in items
            if 5 <= it.get("urgency", 0) <= 6
        ]
        medium_str = ""
        for it in medium_items[:5]:
            medium_str += f"\n  - [{it.get('urgency')}/10] {it.get('source', '?').upper()}: {it.get('sender', '?')} - {it.get('subject', '')[:50]}"

        if not medium_str:
            medium_str = "\n  None"

        # Top senders by volume
        sender_counts = {}
        for it in items:
            s = it.get("sender", "unknown")
            sender_counts[s] = sender_counts.get(s, 0) + 1
        top_senders = sorted(sender_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        senders_str = ""
        for name, count in top_senders:
            senders_str += f"\n  - {name}: {count} messages"

        if not senders_str:
            senders_str = "\n  No activity"

        return f"""\U0001f4ca Evening Summary -- {now_local.strftime('%Y-%m-%d %H:%M')}

Today's stats:
  \U0001f4e7 Emails checked: {d.get('emails_checked', 0)}
  \U0001f4ac Telegram messages: {d.get('tg_messages_checked', 0)}
  \U0001f6a8 Urgent alerts sent: {urgent_count}
  \U0001f4cb Total items analyzed: {len(items)}

Medium-priority (worth a glance):{medium_str}

Top senders:{senders_str}"""

    # --- Meeting invite processing ---

    def _unprocessed_after_failed_escalation(self, invite_id, what):
        """Log why an invite is being left for the next cycle.

        The `else` branch below has done this since it was written; the other
        four escalation call sites discarded `_escalate_invite`'s bool and fell
        through to `mark_invite_processed` regardless. An invite consumed after
        a failed notification is an invite NOBODY was told about, and it never
        comes back - which is the exact outcome the escalation path exists to
        prevent. `_escalate_invite` returns True when there is no notifier at
        all, so a permanently unconfigured workspace is not retried forever.
        """
        self.logger.warning(
            f"Invite {invite_id} left unprocessed: {what} escalation was not "
            f"delivered; it will be retried next cycle")

    async def _process_meeting_invites(self):
        """Check and process new meeting invites per CEO Calendar Policy."""
        invites = self.invite_source.check_new_invites()
        if not invites:
            return

        # Fetch the calendar for the WHOLE window the alternative search can
        # reach, not seven days of it. Derived from the same constant the
        # search uses, so the two cannot drift apart again.
        now = datetime.now(self.config.timezone)
        existing_events = self.invite_source.get_existing_events(
            now, now + timedelta(days=conflict_window_days())
        )

        for invite in invites:
            invite_id = invite["invite_id"]

            # Recurring invites: always escalate
            if invite.get("is_recurring"):
                escalated = await self._escalate_invite(
                    invite, ["Recurring meeting change -- requires CEO review"]
                )
                if not escalated:
                    self._unprocessed_after_failed_escalation(
                        invite_id, "the recurring-change")
                    continue
                self.state.mark_invite_processed(invite_id)
                self.state.record_invite_decision(
                    invite_id, invite["subject"], "escalate",
                    ["Recurring meeting change"]
                )
                continue

            # Evaluate against policy
            result = self.policy_engine.evaluate(invite, existing_events)
            decision = result["decision"]
            reasons = result["reasons"]

            # Execute decision.
            #
            # `--test` is documented as a dry run, and until now it only muted
            # Telegram: accept_invite and decline_invite had no dry_run guard,
            # so a first "safe" test run against a real Exchange account really
            # accepted invites and really sent decline replies to real people.
            # Nothing about the auto-accept/auto-decline POLICY changes here --
            # that design is the operator's and is untouched. What changes is
            # that the dry run stops lying.
            if self.dry_run and decision in ("accept", "decline"):
                self.logger.info(
                    f"[dry-run] would {decision} invite {invite.get('subject')!r} "
                    f"({'; '.join(reasons) or 'no reason recorded'}); no calendar "
                    f"action taken and no reply sent")
                if not await self._escalate_invite(invite, reasons):
                    self._unprocessed_after_failed_escalation(
                        invite_id, "the dry-run")
                    continue

            elif decision == "accept" and self.config.calendar.get("auto_accept", True):
                try:
                    self.invite_source.accept_invite(invite["item"])
                    await self._notify_invite_decision(invite, "ACCEPTED", reasons)
                except Exception as e:
                    self.logger.error(f"Failed to accept invite: {e}")
                    if not await self._escalate_invite(
                            invite, [f"Auto-accept failed: {e}"]):
                        self._unprocessed_after_failed_escalation(
                            invite_id, "the failed-accept")
                        continue

            elif decision == "decline" and self.config.calendar.get("auto_decline", True):
                alternative = result.get("proposed_alternative")
                decline_msg = select_decline_message(
                    result.get("is_tribe", False),
                    invite["subject"],
                    alternative,
                    self.config.calendar,
                )

                try:
                    self.invite_source.decline_invite(invite["item"], decline_msg)
                    await self._notify_invite_decision(
                        invite, "DECLINED", reasons, alternative
                    )
                except Exception as e:
                    self.logger.error(f"Failed to decline invite: {e}")
                    if not await self._escalate_invite(
                            invite, [f"Auto-decline failed: {e}"]):
                        self._unprocessed_after_failed_escalation(
                            invite_id, "the failed-decline")
                        continue

            else:
                # Escalate (VIP, external, soft violations, or auto-action disabled)
                escalated = await self._escalate_invite(invite, reasons)
                if not escalated:
                    # Leave it UNPROCESSED so the next cycle re-escalates. The
                    # escalation path exists precisely for the invites a human
                    # must see -- VIP, external, RUNE overrides -- and marking
                    # them processed after a failed notify meant the operator
                    # never learned they existed.
                    self._unprocessed_after_failed_escalation(
                        invite_id, "the policy")
                    continue

            self.state.mark_invite_processed(invite_id)
            self.state.record_invite_decision(
                invite_id, invite["subject"], decision, reasons
            )

        self.state.save()

    async def _notify_invite_decision(self, invite: dict, decision_label: str,
                                       reasons: list, alternative: str = None):
        """Notify Misha about an auto-handled invite via Telegram."""
        if not self.notifier:
            return

        icon = "\u2705" if decision_label == "ACCEPTED" else "\u274c"
        reasons_str = "\n".join(f"  - {r}" for r in reasons) if reasons else "  Policy compliant"
        alt_line = f"\n\U0001f4c5 Proposed alternative: {alternative}" if alternative else ""

        start_str = str(invite.get("start", ""))[:16]
        end_str = str(invite.get("end", ""))[:16]

        msg = f"""{icon} Meeting {decision_label}

\U0001f4e8 From: {invite['sender']} <{invite['sender_email']}>
\U0001f4cb Subject: {invite['subject']}
\U0001f552 When: {start_str} - {end_str}
\u23f1 Duration: {invite['duration_minutes']}m

Policy check:
{reasons_str}{alt_line}"""

        try:
            await self.notifier.send_digest(msg)
        except Exception as e:
            self.logger.error(f"Invite notification failed: {e}")

    async def _escalate_invite(self, invite: dict, reasons: list) -> bool:
        """Send urgent notification requiring CEO decision on an invite.

        Returns True when the escalation was delivered (or when there is no
        notifier at all), False when a configured notifier failed. The caller
        retries on False, so the two cases must not be conflated: a missing
        notifier is a permanent configuration state, and returning False for it
        would leave every escalated invite unprocessed forever, retried on every
        cycle. That is a startup-level gap, logged here and never silent.
        """
        if not self.notifier:
            self.logger.warning(
                f"No notifier configured; invite {invite.get('subject')!r} needs a "
                f"decision and NOBODY WAS TOLD. Configure Telegram, or handle it "
                f"in Outlook.")
            return True

        reasons_str = "\n".join(f"  - {r}" for r in reasons)
        start_str = str(invite.get("start", ""))[:16]
        end_str = str(invite.get("end", ""))[:16]
        location = invite.get("location", "-") or "-"

        msg = f"""\u26a0\ufe0f MEETING NEEDS YOUR DECISION

\U0001f4e8 From: {invite['sender']} <{invite['sender_email']}>
\U0001f4cb Subject: {invite['subject']}
\U0001f552 When: {start_str} - {end_str}
\u23f1 Duration: {invite['duration_minutes']}m
\U0001f4cd Location: {location}

Issues found:
{reasons_str}

Reply with your decision or handle in Outlook."""

        try:
            await self.notifier.send_digest(msg)
        except Exception as e:
            self.logger.error(f"Invite escalation notification failed: {e}")
            return False
        return True

    async def shutdown(self):
        self.logger.info("Sentinel shutting down...")
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        self.state.save()
        await self.telegram_source.disconnect()
        try:
            PID_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        self.logger.info("Sentinel stopped.")


# ============================================================
# CLI
# ============================================================

def _is_pid_alive(pid: int) -> bool:
    """Check if a process is alive (works for detached processes on Windows)."""
    if sys.platform == "win32":
        import subprocess
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True
        )
        return str(pid) in result.stdout
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _read_pid_file() -> int | None:
    """The PID in PID_FILE, or None when it is absent, empty or corrupt.

    All three CLI paths (--status, --stop, and the already-running check in
    main) did a bare `int(PID_FILE.read_text().strip())`, so a truncated file --
    the normal residue of a crash mid-write -- raised ValueError out of main as
    a traceback instead of a diagnosable state.
    """
    try:
        raw = PID_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _pid_is_sentinel(pid: int) -> bool:
    """True only when `pid` is THIS program, not merely a live process.

    `--stop` used to verify the PID existed and then SIGKILL / `taskkill /F` it.
    After a crash the PID file outlives the process, the number gets reused, and
    the operator's next `--stop` destroys whatever unrelated program inherited
    it. Liveness is not identity.

    Unknown (unreadable /proc, tasklist unavailable) returns False: refusing to
    kill a process we cannot identify is the safe direction, and the stale PID
    file is cleaned either way.
    """
    if sys.platform == "win32":
        import subprocess
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return False
        # tasklist gives the image name only; python.exe/pythonw.exe running
        # this daemon is as tight as it gets without WMI.
        return "python" in (result.stdout or "").lower()
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode(
            "utf-8", "replace")
    except OSError:
        return False
    return "sentinel.py" in cmdline


def check_status():
    """Check if Sentinel is running."""
    if not PID_FILE.exists():
        print("Sentinel is NOT running (no PID file)")
        return

    pid = _read_pid_file()
    if pid is None:
        print(f"Sentinel status UNKNOWN: the PID file at {PID_FILE} is empty or corrupt")
        return
    # Liveness is not identity, and this line is the one the operator acts on.
    # `os.kill(pid, 0)` establishes only that SOME process holds that number.
    # After a crash the PID file outlives the daemon and the number gets reused,
    # so until 2026-08-25 `--status` announced "Sentinel is RUNNING" over an
    # unrelated program - a sentence far wider than its method, and the same
    # defect class `.claude/rules/scope-claims.md` was written for. `--stop`
    # already checked identity; the other two callers did not.
    if _is_pid_alive(pid):
        if _pid_is_sentinel(pid):
            print(f"Sentinel is RUNNING (PID: {pid})")
        else:
            print(f"Sentinel is NOT running: PID {pid} is alive but is not this "
                  f"daemon (the PID was reused after a crash). Removing the "
                  f"stale PID file.")
            PID_FILE.unlink(missing_ok=True)
            return
    else:
        print(f"Sentinel is NOT running (stale PID file, PID {pid})")
        PID_FILE.unlink(missing_ok=True)
        return

    # Show last run info
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            last_run = state.get("last_run", "never")
            digest = state.get("digest", {})
            print(f"  Last check: {last_run}")
            print(f"  Today: {digest.get('emails_checked', 0)} emails, "
                  f"{digest.get('tg_messages_checked', 0)} TG messages, "
                  f"{digest.get('urgent_sent', 0)} urgent alerts")
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
            # State file missing/corrupt/partial - status display is best-effort.
            print(f"  (state file unreadable: {e})", file=sys.stderr)


def stop_daemon():
    """Stop the running Sentinel daemon."""
    if not PID_FILE.exists():
        print("Sentinel is not running")
        return

    pid = _read_pid_file()
    if pid is None:
        print(f"PID file at {PID_FILE} is empty or corrupt; removing it.")
        PID_FILE.unlink(missing_ok=True)
        return

    if not _is_pid_alive(pid):
        print(f"Process {pid} not found (already stopped?)")
        PID_FILE.unlink(missing_ok=True)
        return

    if not _pid_is_sentinel(pid):
        print(f"REFUSING to kill PID {pid}: it is alive but is not this daemon "
              f"(the PID was reused after a crash). Removing the stale PID file.")
        PID_FILE.unlink(missing_ok=True)
        return

    if sys.platform == "win32":
        # Windows: use taskkill for reliable termination
        import subprocess
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True)
        print(f"Terminated Sentinel (PID: {pid})")
    else:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to Sentinel (PID: {pid})")
        time.sleep(2)
        try:
            os.kill(pid, 0)
            print("Process still alive, sending SIGKILL")
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    time.sleep(1)
    PID_FILE.unlink(missing_ok=True)
    print("Sentinel stopped.")


def launch_daemon(config_path):
    """Launch Sentinel as a fully detached background process.

    Windows: CREATE_NO_WINDOW + DETACHED_PROCESS for true background.
    POSIX: start_new_session=True puts the child in its own session so it
    survives parent shell exit. For Linux production deployments, prefer
    running the daemon under a systemd user unit (see
    scripts/templates/systemd/sentinel.service when Phase 3 of the
    cross-platform plan lands) and invoke the script in the foreground
    without --daemon — systemd handles the backgrounding.
    """
    import subprocess
    python = sys.executable
    script = str(Path(__file__).resolve())
    cmd = [python, script, "--config", str(config_path)]

    if sys.platform == "win32":
        CREATE_NO_WINDOW = 0x08000000
        DETACHED_PROCESS = 0x00000008
        proc = subprocess.Popen(
            cmd,
            creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    else:
        proc = subprocess.Popen(
            cmd,
            start_new_session=True,
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    print(f"Sentinel launched as detached process (PID: {proc.pid})")
    print("Use --status to check, --stop to stop, logs at .sentinel/sentinel.log")


def main():
    parser = argparse.ArgumentParser(description="Sentinel -- Unified Comms Monitor")
    parser.add_argument("--test", action="store_true",
                        help="Run one cycle as a TRUE dry run: notifications are "
                             "logged not sent, calendar invites are neither accepted "
                             "nor declined, and state is read but never written back")
    parser.add_argument("--check", action="store_true",
                        help="Run ONE live cycle and exit: notifications are sent "
                             "and state is written. This is what the 15-minute "
                             "scheduled task installed by scripts/utils/schedule.py "
                             "runs; --test is a dry run and cannot stand in for it")
    parser.add_argument("--status", action="store_true", help="Check if Sentinel is running")
    parser.add_argument("--stop", action="store_true", help="Stop running Sentinel daemon")
    parser.add_argument("--daemon", action="store_true", help="Launch as detached background process (cross-platform; on Linux, prefer systemd user unit)")
    parser.add_argument("--config", type=str, default=str(CONFIG_FILE), help="Path to config file")
    args = parser.parse_args()

    if args.status:
        check_status()
        return

    if args.stop:
        stop_daemon()
        return

    if args.daemon:
        launch_daemon(args.config)
        return

    # Check if already running
    # Same identity check as --status, and it matters more here: this guard
    # REFUSES a legitimate start. On a reused PID it used to tell the operator
    # the daemon was already running and exit 1, when nothing was running at
    # all. It is advisory anyway - the real second-instance guard is the flock
    # in Sentinel.start - so failing toward "let it start and let the lock
    # decide" is the safe direction.
    if not args.test and PID_FILE.exists():
        pid = _read_pid_file()
        if pid is not None and _is_pid_alive(pid) and _pid_is_sentinel(pid):
            print(f"Sentinel is already running (PID: {pid}). Use --stop first.")
            sys.exit(1)
        else:
            PID_FILE.unlink(missing_ok=True)

    sentinel = Sentinel(config_path=Path(args.config), dry_run=args.test,
                        once=args.check)

    # Handle graceful shutdown (registered AFTER sentinel object created - SEC-011)
    def signal_handler(sig, frame):
        print("\nShutdown signal received...")
        sentinel._running = False
        if hasattr(sentinel, '_stop_event'):
            sentinel._stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    try:
        asyncio.run(sentinel.start())
    except Exception as e:
        # Catch-all so daemon mode crashes are logged, not lost
        sentinel.logger.critical(f"Sentinel crashed: {e}", exc_info=True)
        PID_FILE.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    main()
