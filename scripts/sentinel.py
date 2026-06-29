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

from scripts.utils import daemon_heartbeat  # noqa: E402
from scripts.utils import trace  # noqa: E402
from scripts.utils.healthchecks import ping as hc_ping  # noqa: E402
from scripts.utils.html import strip_html  # noqa: E402
from scripts.utils.llm_fallback import call_anthropic_with_fallback  # noqa: E402
from scripts.utils.observability import observe  # noqa: E402
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


# HTML stripping: see scripts/utils/html.py (imported above as strip_html)


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
    """Persistent state tracking to avoid duplicate processing."""

    def __init__(self, state_path: Path = STATE_FILE):
        self.path = state_path
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
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
        items = folder.filter(is_read=False).order_by("-datetime_received")[:max_count]

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

        self.logger.info(f"Email: {len(new_items)} new unread messages")
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
# Calendar Policy Engine
# ============================================================

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
        """Check for back-to-back violations."""
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
                              search_days: int = 5) -> str:
        """Find a policy-compliant alternative slot."""
        if reference_date is None:
            reference_date = datetime.now(self.tz)
        else:
            try:
                reference_date = reference_date.astimezone(self.tz)
            except Exception:
                reference_date = datetime.now(self.tz)

        # Start searching from next business day
        search_start = reference_date + timedelta(days=1)

        for day_offset in range(search_days + 7):  # Extra buffer for weekends
            candidate_date = search_start + timedelta(days=day_offset)
            weekday = candidate_date.weekday()

            # Skip weekends
            if weekday >= 5:
                continue

            # Generate 30-min increment slots from 09:30 to 18:00
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

    async def _check_personal_dms(self) -> list:
        from telethon import types

        ignore_chats = [c.lower() for c in self.config.get("ignore_chats", [])]
        max_msgs = self.config.get("max_messages_per_chat", 30)

        # Collect dialogs with unread DMs first
        pending_dialogs = []
        async for dialog in self.client.iter_dialogs(limit=100):
            if dialog.unread_count == 0:
                continue
            if not isinstance(dialog.entity, types.User):
                continue
            if dialog.entity.bot:
                continue
            chat_name = self._entity_name(dialog.entity)
            if chat_name.lower() in ignore_chats:
                continue
            pending_dialogs.append((dialog, chat_name))

        if not pending_dialogs:
            return []

        # Fetch messages from all unread DMs in parallel
        async def _fetch_dm(dialog, chat_name):
            chat_id = str(dialog.entity.id)
            last_known_id = self.state.get_telegram_last_id(chat_id)
            try:
                messages = await asyncio.wait_for(
                    self.client.get_messages(
                        dialog.entity,
                        limit=min(dialog.unread_count, max_msgs),
                        min_id=last_known_id,
                    ),
                    timeout=15,
                )
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout fetching DMs from {chat_name}")
                return None

            if not messages:
                return None

            max_id = max(m.id for m in messages)
            self.state.set_telegram_last_id(chat_id, chat_name, max_id)

            combined_text = []
            for msg in reversed(messages):
                if msg.text:
                    combined_text.append(msg.text)
                elif msg.media:
                    combined_text.append("[media attachment]")

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
                "message_count": len(messages),
                "is_vip": False,
            }

        results = await asyncio.gather(
            *[_fetch_dm(d, name) for d, name in pending_dialogs],
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
                messages = await asyncio.wait_for(
                    self.client.get_messages(entity, limit=max_msgs, min_id=last_known_id),
                    timeout=15
                )
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

    SYSTEM_PROMPT = """You are Sentinel, an urgency triage system for Misha Hanin, CEO of 31 Concept (31C).
31C builds ODUN.ONE, a sovereign deep packet intelligence platform.

Your job: analyze incoming messages and score their urgency on a 1-10 scale.
Misha is extremely busy running a high-growth startup with active deals across multiple regions.

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

Consider Misha's priorities: active deals (multiple regions), ODUN.ONE product development, investor relations, and Tribe management. Flag anything affecting deal velocity, partner relationships, or product delivery.

Respond ONLY in this JSON format (no markdown, no code fences):
{{"urgency_score": <1-10>, "reason": "<1 sentence>", "summary": "<2-3 sentences>", "recommended_action": "<specific action using categories above>"}}"""

    def __init__(self, config: dict, logger: logging.Logger):
        self.model = config.get("model", "claude-haiku-4-5-20251001")
        self.max_tokens = config.get("max_tokens", 500)
        self.logger = logger
        self.client = None
        self.business_context = ""

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

        brace_depth = 0
        json_end = -1
        for i, ch in enumerate(text):
            if ch == "{":
                brace_depth += 1
            elif ch == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    json_end = i + 1
                    break
        if json_end > 0:
            text = text[:json_end]

        return json.loads(text)

    @observe()
    def analyze(self, item: dict) -> dict:
        """Analyze a single item for urgency. Returns dict with score and details."""
        client = self._get_client()

        system_prompt = self.SYSTEM_PROMPT.format(business_context=self.business_context)

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
    def analyze_batch(self, items: list) -> list:
        """Analyze multiple items in a single LLM call. Returns list of dicts."""
        if not items:
            return []
        if len(items) == 1:
            result = self.analyze(items[0])
            return [result]

        client = self._get_client()
        system_prompt = self.SYSTEM_PROMPT.format(business_context=self.business_context)

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

        except json.JSONDecodeError as e:
            self.logger.warning(f"Batch LLM analysis JSON parse error ({e}), falling back to individual calls")
            return [self.analyze(item) for item in items]
        except Exception as e:
            self.logger.warning(f"Batch LLM analysis unexpected error ({e}), falling back to individual calls")
            return [self.analyze(item) for item in items]


# ============================================================
# Telegram Notifier
# ============================================================

class TelegramNotifier:
    """Send urgent notifications to the target Telegram channel."""

    def __init__(self, client, target_chat: str, logger: logging.Logger):
        self.client = client
        self.target_chat = target_chat
        self.logger = logger
        self.target_entity = None

    async def _ensure_connected(self):
        """Reconnect Telegram client if disconnected, with DB lock retry."""
        if not self.client.is_connected():
            self.logger.warning("Telegram disconnected before send -- reconnecting")
            for attempt in range(1, 4):
                try:
                    await self.client.connect()
                    return
                except (sqlite3.OperationalError, OSError) as e:
                    if 'locked' in str(e).lower() and attempt < 3:
                        delay = 2 * attempt
                        self.logger.warning(f"Session DB locked on reconnect (attempt {attempt}/3), retrying in {delay}s...")
                        await asyncio.sleep(delay)
                        continue
                    raise

    async def _resolve_target(self):
        if self.target_entity is None:
            from telethon import errors
            try:
                self.target_entity = await self.client.get_entity(self.target_chat)
            except (ValueError, errors.RPCError):
                # Fuzzy match
                async for dialog in self.client.iter_dialogs(limit=200):
                    if dialog.name and dialog.name.lower() == self.target_chat.lower():
                        self.target_entity = dialog.entity
                        break
            if self.target_entity is None:
                raise ValueError(f"Cannot find notification target: '{self.target_chat}'")
        return self.target_entity

    async def send_notification(self, item: dict, analysis: dict):
        await self._ensure_connected()
        entity = await self._resolve_target()
        message = self._format_message(item, analysis)
        await self.client.send_message(entity, message)
        self.logger.info(f"Notification sent: [{analysis['urgency_score']}/10] {item.get('subject', '')}")

    async def send_digest(self, message: str):
        await self._ensure_connected()
        entity = await self._resolve_target()
        await self.client.send_message(entity, message)
        self.logger.info("Digest sent")

    def _format_message(self, item: dict, analysis: dict) -> str:
        score = analysis.get("urgency_score", 0)

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

    def __init__(self, config_path: Path = CONFIG_FILE, dry_run: bool = False):
        self.config = SentinelConfig(config_path)
        self.dry_run = dry_run
        self.logger = self._setup_logging()
        self.state = StateManager()
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
        trace.mint()
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

    async def start(self):
        self.logger.info("=" * 50)
        self.logger.info("Sentinel starting...")
        self.logger.info(f"Check interval: {self.config.check_interval // 60} minutes")
        self.logger.info(f"Urgency threshold: {self.config.urgency_threshold}")
        self.logger.info(f"Dry run: {self.dry_run}")
        self.logger.info("=" * 50)

        # Write PID file with file lock (SEC-016)
        RUNTIME_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        my_pid = os.getpid()
        self._pid_file_handle = open(PID_FILE, "w")
        try:
            if sys.platform != "win32":
                import fcntl
                fcntl.flock(self._pid_file_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # On Windows, msvcrt.locking holds the file locked and blocks readers.
            # Windows already prevents two processes from writing the same PID file
            # atomically, so no explicit lock is needed - we just write and close.
        except (IOError, OSError):
            self._pid_file_handle.close()
            self.logger.error("Another Sentinel instance is already running (PID file locked)")
            sys.exit(1)
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

        # Connect Telegram (needed for both reading and sending)
        if self.config.telegram.get("enabled", True) or not self.dry_run:
            await self.telegram_source.connect()
            target = self.config.notification.get("target_chat", "Urgent Stuff for M")
            if self.dry_run:
                # In dry-run, send to Saved Messages
                self.notifier = TelegramNotifier(
                    self.telegram_source.client, "me", self.logger
                )
                self.logger.info("Dry-run: notifications will go to Saved Messages")
            else:
                self.notifier = TelegramNotifier(
                    self.telegram_source.client, target, self.logger
                )

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
                if self.dry_run:
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

    async def _analyze_and_notify(self, items: list):
        threshold = self.config.urgency_threshold
        max_notifs = self.config.notification.get("max_notifications_per_cycle", 10)
        sent_count = 0

        # Pre-process: mark emails, dedup, collect items needing LLM analysis
        items_to_analyze = []  # (item, content_hash) pairs
        for item in items:
            if item.get("source") == "email" and item.get("message_id"):
                self.state.mark_email_processed(item["message_id"])

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
                # LLM failed -- fallback for VIP
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

            score = analysis.get("urgency_score", 0)
            self.state.record_digest_item(item, score)

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

        # Check within a 15-minute window (matches check interval)
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

    def _time_in_window(self, current: str, target: str) -> bool:
        """Check if current time is within 15 minutes after target time."""
        try:
            c_h, c_m = map(int, current.split(":"))
            t_h, t_m = map(int, target.split(":"))
            c_mins = c_h * 60 + c_m
            t_mins = t_h * 60 + t_m
            return 0 <= (c_mins - t_mins) < 15
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

    async def _process_meeting_invites(self):
        """Check and process new meeting invites per CEO Calendar Policy."""
        invites = self.invite_source.check_new_invites()
        if not invites:
            return

        # Fetch next 7 days of calendar for conflict checking
        now = datetime.now(self.config.timezone)
        existing_events = self.invite_source.get_existing_events(
            now, now + timedelta(days=7)
        )

        for invite in invites:
            invite_id = invite["invite_id"]

            # Recurring invites: always escalate
            if invite.get("is_recurring"):
                await self._escalate_invite(
                    invite, ["Recurring meeting change -- requires CEO review"]
                )
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

            # Execute decision
            if decision == "accept" and self.config.calendar.get("auto_accept", True):
                try:
                    self.invite_source.accept_invite(invite["item"])
                    await self._notify_invite_decision(invite, "ACCEPTED", reasons)
                except Exception as e:
                    self.logger.error(f"Failed to accept invite: {e}")
                    await self._escalate_invite(invite, [f"Auto-accept failed: {e}"])

            elif decision == "decline" and self.config.calendar.get("auto_decline", True):
                decline_msg = self.config.calendar.get(
                    "decline_message",
                    "Due to some conflicts, I'd like to propose a new day and time for our meeting."
                )
                alternative = result.get("proposed_alternative")
                if alternative:
                    decline_msg += f" How about {alternative}?"

                try:
                    self.invite_source.decline_invite(invite["item"], decline_msg)
                    await self._notify_invite_decision(
                        invite, "DECLINED", reasons, alternative
                    )
                except Exception as e:
                    self.logger.error(f"Failed to decline invite: {e}")
                    await self._escalate_invite(invite, [f"Auto-decline failed: {e}"])

            else:
                # Escalate (VIP, external, soft violations, or auto-action disabled)
                await self._escalate_invite(invite, reasons)

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

    async def _escalate_invite(self, invite: dict, reasons: list):
        """Send urgent notification requiring CEO decision on an invite."""
        if not self.notifier:
            return

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


def check_status():
    """Check if Sentinel is running."""
    if not PID_FILE.exists():
        print("Sentinel is NOT running (no PID file)")
        return

    pid = int(PID_FILE.read_text().strip())
    if _is_pid_alive(pid):
        print(f"Sentinel is RUNNING (PID: {pid})")
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

    pid = int(PID_FILE.read_text().strip())
    if not _is_pid_alive(pid):
        print(f"Process {pid} not found (already stopped?)")
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
    parser.add_argument("--test", action="store_true", help="Run one cycle (dry-run, notifications to Saved Messages)")
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
    if not args.test and PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        if _is_pid_alive(pid):
            print(f"Sentinel is already running (PID: {pid}). Use --stop first.")
            sys.exit(1)
        else:
            PID_FILE.unlink(missing_ok=True)

    sentinel = Sentinel(config_path=Path(args.config), dry_run=args.test)

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
