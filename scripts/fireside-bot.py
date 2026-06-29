#!/usr/bin/env python3
"""Tribe Fireside Bot - coordinates Mon + Wed firesides via Telegram.

Implementation per:
  - Spec: docs/superpowers/specs/2026-05-03-tribe-fireside-bot-design.md
  - Plan: docs/superpowers/plans/2026-05-03-tribe-fireside-bot-implementation.md (v1.3)
  - Operating model: runs on Misha's workstation, state in datastore/operations/tribe/fireside-state/

Subcommands (current implementation status in parentheses):
  bootstrap                 - One-time: enumerate Telegram group, build initial roster (Phase 2)
  poll                      - Process Telegram updates, every 5 min (Phase 3)
  speaker-dms               - Send 2-week + 3-day speaker reminders (Phase 3)
  sunday-preview            - Post pinned weekly preview to 31C Tribe (Phase 3)
  dayof-reminders           - DM speakers Zoom link 3h before session (Phase 3)
  helmsman-brief            - Brief next week's Helmsman 7 days ahead (Phase 3)
  weekly-discrepancy-report - Report Telegram-vs-xlsx mismatches (Phase 3)
  email-backup              - Email reminder for unresponsive Tribe (Phase 3)
  stats                     - Generate stats markdown report (Phase 3)
  health-check              - Alert if poll hasn't run in 30 min (Phase 3)
  unpin-weekly              - Unpin Sunday preview after Wed session (Phase 3)
  log-session               - Log session result, manual command (Phase 3)
  test-telegram             - Smoke test: send DM to Misha (Phase 1) [IMPLEMENTED]
  xlsx-check                - Print xlsx loader summary (Phase 1 helper) [IMPLEMENTED]
  init-state                - Initialise state directory + files (Phase 1 helper) [IMPLEMENTED]

Usage:
  python scripts/fireside-bot.py <subcommand> [args]
  python scripts/fireside-bot.py --help
"""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Force UTF-8 stdout/stderr on Windows so emoji and non-ASCII names print correctly.
# Guard against pythonw.exe where sys.stdout/stderr are None (no console attached).
if sys.platform == "win32":
    if sys.stdout is not None and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if sys.stderr is not None and hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import argparse
import json
import os
import socket
import tempfile
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests
import urllib3.util.connection as _urllib3_connection

# Force IPv4 for all outbound Telegram calls. The service host
# has an AAAA record for api.telegram.org but no working IPv6 route - urllib3
# picks the IPv6 result first and stalls 30s before falling back to IPv4. On
# 2026-05-25 every webhook handler was taking 30s+ per sendMessage because of
# this. Forcing AF_INET via urllib3's allowed_gai_family hook cuts the call
# from 30.09s to 0.07s (measured on the VM). No-op on hosts where IPv6 works.
_urllib3_connection.allowed_gai_family = lambda: socket.AF_INET

from scripts.utils.colors import GREEN, YELLOW, RED, GRAY, CYAN, BOLD, RESET
from scripts.utils.healthchecks import ping as hc_ping
from scripts.utils.workspace import get_datastore_dir, get_default_tz, get_default_tz_name, get_outputs_dir, get_workspace_root, load_env, resolve_config_with_example
from scripts import fireside_topics as ft

# ============================================================
# Configuration
# ============================================================

WORKSPACE_ROOT = get_workspace_root()
STATE_DIR = get_datastore_dir() / "operations" / "tribe" / "fireside-state"
STATS_DIR = get_outputs_dir() / "operations" / "tribe-fireside" / "stats"
TRIBE_XLSX = get_datastore_dir() / "operations" / "tribe" / "31C_Tribe.xlsx"

TELEGRAM_API_BASE = "https://api.telegram.org"

# State file names (relative to STATE_DIR)
TRIBE_ROSTER = "tribe-roster.json"
SCHEDULE = "schedule.json"
HELMSMEN = "helmsmen.json"
OPT_INS = "opt-ins.json"
DM_LOG = "dm-log.jsonl"
SESSIONS_LOG = "sessions.jsonl"
LAST_UPDATE_ID = "last-update-id.json"
LAST_PINNED = "last-pinned.json"
ERRORS_LOG = "errors.log"
EXCLUSIONS = "exclusions.json"  # CEO-managed list of Tribe members excluded from fireside rotations
OUTSIDER_RATE = "outsider-forward-rate.json"  # rate-limit state for outsider DM forwards to Misha
SWAP_REQUESTS_LOG = "swap-requests.jsonl"  # append-only event log for /swap state machine
TOPIC_IDEAS = "topic-ideas.jsonl"  # append-only topic backlog (see fireside_topics)
TOPIC_STATE = "topic-collection-state.json"  # digest cursor + pending cycle invite

# /swap interactive flow tuning
SWAP_HORIZON_WEEKS = 4  # how far ahead to scan for candidate sessions
SWAP_B_RESPONSE_TTL_HOURS = 24  # how long B has to accept/decline before request expires
SWAP_CANDIDATES_LIMIT = 2  # how many buttons to show A

# Senior-leader title fragments for VP detection in xlsx
VP_TITLE_FRAGMENTS = (
    "ceo", "cfo", "cto", "csto", "cso", "cmo", "chro", "cio", "clo",
    "chief ", "vp ", "svp ", "vp,", "svp,",
    "founder", "co-founder",
)

# Cycle-1 speaker schedule is per-instance DATA: real names, themes, and the cycle
# start date live in the data overlay at <data-root>/config/fireside-schedule.json
# (resolved via get_data_config_dir()). The engine ships
# scripts/fireside-schedule.example.json as the generic template/fallback, so a
# data-less clone bootstraps cleanly. Speakers are identified by full name (the
# join to telegram_username happens at bootstrap).
_FIRESIDE_SCHEDULE_FILE = resolve_config_with_example(
    "fireside-schedule.json", WORKSPACE_ROOT / "scripts" / "fireside-schedule.example.json"
)
_fireside_schedule = json.loads(_FIRESIDE_SCHEDULE_FILE.read_text(encoding="utf-8"))
CYCLE_1_START_MONDAY = datetime.fromisoformat(_fireside_schedule["cycle_1_start_monday"]).date()
WEEK_1_TO_9_SCHEDULE = _fireside_schedule["weeks"]


# ============================================================
# Time helper (the configured timezone)
# ============================================================

def local_now() -> datetime:
    """Return current time as a timezone-aware datetime in the configured timezone."""
    return datetime.now(get_default_tz())


# ============================================================
# State file helpers (atomic writes, JSONL append, error log)
# ============================================================

def state_path(filename: str) -> Path:
    """Return absolute path to a state file under STATE_DIR."""
    return STATE_DIR / filename


def ensure_state_dir() -> None:
    """Create state directory and initialise empty state files if missing.

    Files initialised (only if they don't exist):
      - tribe-roster.json: rebuilt from 31C_Tribe.xlsx if xlsx is reachable,
        else an empty {} placeholder. Without this self-heal a VM rebuild or
        state-loss leaves the bot rejecting every DM as outsider.
      - Other JSON files with sensible empty defaults
      - JSONL files as empty text files
      - errors.log as empty text file
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Self-heal tribe-roster.json from xlsx (the source of truth for membership).
    # load_tribe_metadata already returns entries shaped like roster records
    # (active=True, telegram_user_id=None). user_ids are NOT populated here:
    # cross_reference is skipped because it only emits the intersection with the
    # live Telegram membership, which Telethon would have to enumerate. After a
    # state-loss rebuild every entry is unbound, so the operator must re-run the
    # trusted `bootstrap` to bind user_ids. DM handlers deliberately refuse to
    # bind a user_id from a self-reported username (handle-takeover guard), so
    # the bot rejects DMs from unbound members (forwarding them to Misha) until
    # bootstrap runs -- a safe failure, not a silent self-enrollment hole.
    if not state_path(TRIBE_ROSTER).exists():
        try:
            xlsx_roster = load_tribe_metadata()
            exclusions = load_state(EXCLUSIONS) or {}
            excluded = {k.lower(): v for k, v in exclusions.get("excluded", {}).items()}
            roster: dict = {}
            for username, data in xlsx_roster.items():
                entry = dict(data)
                if username.lower() in excluded:
                    entry["active"] = False
                    entry["excluded_from_fireside"] = True
                    entry["exclusion_reason"] = excluded[username.lower()].get("reason", "")
                    entry["excluded_at"] = excluded[username.lower()].get("excluded_at", "")
                roster[username] = entry
            save_state(TRIBE_ROSTER, roster)
        except (FileNotFoundError, ValueError):
            save_state(TRIBE_ROSTER, {})

    initial: dict[str, Any] = {
        SCHEDULE: [],
        HELMSMEN: {},
        OPT_INS: {"helmsman": [], "wildcard": []},
        LAST_UPDATE_ID: {"offset": 0},
        LAST_PINNED: {"message_id": None},
    }
    text_files = [DM_LOG, SESSIONS_LOG, ERRORS_LOG]

    for name, default in initial.items():
        path = state_path(name)
        if path.exists():
            continue
        save_state(name, default)

    for name in text_files:
        path = state_path(name)
        if not path.exists():
            path.write_text("", encoding="utf-8")

    # Topic-collection files (see fireside_topics). Empty backlog + default state.
    topic_ideas = state_path(TOPIC_IDEAS)
    if not topic_ideas.exists():
        topic_ideas.write_text("", encoding="utf-8")
    if not state_path(TOPIC_STATE).exists():
        save_state(TOPIC_STATE, {"last_digest_idea_id": None, "pending_cycle_invite": None})


def load_state(filename: str) -> Any:
    """Load a JSON state file. Returns None if file does not exist."""
    path = state_path(filename)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(filename: str, data: Any) -> None:
    """Atomically write a JSON state file (write-to-tmp + os.replace).

    Prevents corruption on crash mid-write. The temp file lives in the same
    directory as the target so os.replace is atomic on Windows + POSIX.
    """
    path = state_path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def append_jsonl(filename: str, event: dict) -> None:
    """Append one JSON event as a single line to a JSONL file."""
    path = state_path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def log_error(message: str, exception: Optional[BaseException] = None) -> None:
    """Append an error line to errors.log with ISO-8601 local timestamp."""
    path = state_path(ERRORS_LOG)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = local_now().isoformat()
    if exception is not None:
        line = f"[{ts}] ERROR: {message} [{type(exception).__name__}: {exception}]\n"
    else:
        line = f"[{ts}] ERROR: {message}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


# ============================================================
# Telegram Bot API wrapper (raw HTTPS via requests)
# ============================================================

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
    All API errors are logged via log_error() with the bot token redacted,
    then re-raised as TelegramAPIError so callers can inspect status_code.
    """

    def __init__(self, token: str):
        if not token:
            raise ValueError("TelegramBot requires a non-empty token")
        self.token = token
        self.base = f"{TELEGRAM_API_BASE}/bot{token}"

    def _redact(self, message: str) -> str:
        """Redact the bot token from any string before logging or raising."""
        return message.replace(self.token, "<REDACTED_TOKEN>") if self.token else message

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
            log_error(msg)
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
            log_error(msg)
            raise TelegramAPIError(msg, status_code=status) from None

        if not r.ok or not data.get("ok"):
            description = data.get("description", "<no description>")
            telegram_code = data.get("error_code")
            hint = self._hint_for_status(method, status, description)
            msg = self._redact(
                f"Telegram {method} failed (HTTP {status}, telegram_code={telegram_code}): "
                f"{description}{hint}"
            )
            log_error(msg)
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
            return " | HINT: Bot token is invalid or revoked; check FIRESIDE_BOT_TOKEN in .env."
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
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
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


def get_bot() -> TelegramBot:
    """Construct the bot from FIRESIDE_BOT_TOKEN in env."""
    load_env()
    token = os.environ.get("FIRESIDE_BOT_TOKEN")
    if not token:
        print(f"{RED}ERROR: FIRESIDE_BOT_TOKEN not set in .env{RESET}", file=sys.stderr)
        sys.exit(1)
    return TelegramBot(token)


# ============================================================
# xlsx reader
# ============================================================

def load_tribe_metadata() -> dict:
    """Read 31C_Tribe.xlsx and return metadata keyed by telegram_username.

    Returns:
        dict[telegram_username -> dict with keys: name, email, title, function,
        is_vp, languages, telegram_user_id, active]

    Notes:
        - Rows without a Telegram Username value are skipped silently here;
          the weekly-discrepancy-report subcommand surfaces them.
        - is_vp is heuristically derived from the Title (reconciled) column
          using VP_TITLE_FRAGMENTS.
        - telegram_user_id is initialised to None; populated later only via the
          trusted Telethon bootstrap (Phase 2 task 2.2). DM handlers never bind
          it from a self-reported username (handle-takeover guard).
    """
    import openpyxl  # local import - openpyxl is heavy

    if not TRIBE_XLSX.exists():
        msg = f"Tribe xlsx not found at {TRIBE_XLSX}"
        log_error(msg)
        raise FileNotFoundError(msg)

    wb = openpyxl.load_workbook(TRIBE_XLSX, data_only=True)
    ws = wb.active

    # Find header row by looking for "Name" column
    header_row_idx = None
    headers: dict[str, int] = {}
    for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if not row:
            continue
        for idx, cell in enumerate(row):
            if cell == "Name":
                header_row_idx = i
                headers = {str(c).strip(): j for j, c in enumerate(row) if c}
                break
        if header_row_idx is not None:
            break

    if header_row_idx is None:
        raise ValueError(f"Could not find header row (no 'Name' column) in {TRIBE_XLSX}")

    name_col = headers.get("Name")
    email_col = headers.get("Email")
    tg_username_col = headers.get("Telegram Username")
    title_col = headers.get("Title (reconciled)") or headers.get("Title")
    function_col = headers.get("Function / Department") or headers.get("Function")

    if name_col is None:
        raise ValueError(f"'Name' column not found in {TRIBE_XLSX}")

    if tg_username_col is None:
        # Friendly error - this is Phase 0 task 0.5 (Misha-side)
        raise ValueError(
            f"'Telegram Username' column not found in {TRIBE_XLSX}. "
            f"Add this column and populate it for all 54 Tribe members "
            f"(Phase 0 task 0.5) before running this command."
        )

    roster: dict[str, dict] = {}
    for row in ws.iter_rows(min_row=header_row_idx + 1, values_only=True):
        if not row or row[name_col] is None:
            continue
        name = str(row[name_col]).strip()
        if not name:
            continue

        username_raw = row[tg_username_col]
        if not username_raw:
            # Skip rows without telegram_username; discrepancy report will surface them
            continue
        username = str(username_raw).strip().lstrip("@")
        if not username:
            continue

        title = ""
        if title_col is not None and row[title_col] is not None:
            title = str(row[title_col]).strip()

        function = "Unknown"
        if function_col is not None and row[function_col] is not None:
            function = str(row[function_col]).strip()

        email = ""
        if email_col is not None and row[email_col] is not None:
            email = str(row[email_col]).strip()

        title_lower = title.lower()
        is_vp = any(frag in title_lower for frag in VP_TITLE_FRAGMENTS)

        roster[username] = {
            "name": name,
            "email": email,
            "title": title,
            "function": function,
            "is_vp": is_vp,
            "languages": ["en"],  # default; refine in v2 if needed
            "telegram_user_id": None,  # populated only via trusted Telethon bootstrap
            "active": True,
        }

    return roster


# ============================================================
# Schedule generator (Phase 2 task 2.3)
# ============================================================

def build_schedule(roster_by_name: dict) -> tuple[list, list[str]]:
    """Convert WEEK_1_TO_9_SCHEDULE into the schedule.json structure.

    Args:
        roster_by_name: dict[full_name -> {telegram_username, ...}] for username lookup.

    Returns:
        (schedule_entries, missing_speakers) where:
          - schedule_entries: list of one dict per (session, slot) — 18 sessions x 3 slots = 54 entries
          - missing_speakers: list of names from the schedule that have no roster match
    """
    from datetime import timedelta

    entries = []
    missing = []
    for week_data in WEEK_1_TO_9_SCHEDULE:
        week_num = week_data["week"]
        theme = week_data["theme"]
        # Mon = CYCLE_1_START + (week-1)*7 days; Wed = Mon + 2
        mon_date = CYCLE_1_START_MONDAY + timedelta(days=(week_num - 1) * 7)
        wed_date = mon_date + timedelta(days=2)

        for day_label, day_date, speaker_names in [
            ("Mon", mon_date, week_data["mon"]),
            ("Wed", wed_date, week_data["wed"]),
        ]:
            for slot_idx, name in enumerate(speaker_names, start=1):
                entry = roster_by_name.get(name)
                username = entry["telegram_username"] if entry else None
                if username is None:
                    missing.append(name)
                entries.append({
                    "cycle": 1,
                    "week": week_num,
                    "session_date": day_date.isoformat(),
                    "day": day_label,
                    "theme": theme,
                    "slot": slot_idx,
                    "speaker_name": name,
                    "speaker_username": username,
                    "swapped_with": None,
                    "no_show": False,
                    "completed": False,
                })
    return entries, missing


# ============================================================
# Telethon-based bootstrap (Phase 2 tasks 2.1, 2.2, 2.5)
# ============================================================

def _telethon_session_path() -> Path:
    """Return path to the existing /telegram skill's Telethon session."""
    return WORKSPACE_ROOT / ".sessions" / "telegram" / "telegram"


def _telethon_credentials() -> tuple[int, str, str]:
    """Load TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE from env."""
    load_env()
    api_id_str = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    phone = os.environ.get("TELEGRAM_PHONE")
    missing = [k for k, v in
               [("TELEGRAM_API_ID", api_id_str), ("TELEGRAM_API_HASH", api_hash),
                ("TELEGRAM_PHONE", phone)] if not v]
    if missing:
        raise RuntimeError(f"Missing Telethon env vars: {', '.join(missing)}")
    try:
        api_id = int(api_id_str)
    except ValueError:
        raise RuntimeError(f"TELEGRAM_API_ID must be integer, got {api_id_str!r}")
    return api_id, api_hash, phone


async def _enumerate_tribe_members(client, chat_id: int) -> dict:
    """Enumerate participants of the 31C Tribe group via Telethon.

    Returns:
        dict[telegram_username -> {user_id, first_name, last_name, full_name}]
        plus a special key '_no_username' which is a list of dicts for users
        who have no username set on their Telegram account.
    """
    members_by_username: dict = {"_no_username": []}
    chat = await client.get_entity(chat_id)
    async for user in client.iter_participants(chat):
        first = (user.first_name or "").strip()
        last = (user.last_name or "").strip()
        full_name = f"{first} {last}".strip()
        record = {
            "user_id": user.id,
            "first_name": first,
            "last_name": last,
            "full_name": full_name,
            "is_bot": bool(user.bot),
        }
        if user.username:
            members_by_username[user.username.lower()] = record
        else:
            members_by_username["_no_username"].append(record)
    return members_by_username


async def _read_launch_reactions(client, chat_id: int, msg_id: int) -> dict:
    """Read 🧭 (helmsman) and 🌟 (wildcard) reactions on the launch announcement.

    Returns:
        dict with keys 'helmsman' and 'wildcard', each a list of dicts
        {user_id, username (or None)}. If the message can't be read, returns
        empty lists and logs the error.
    """
    from telethon.tl.functions.messages import GetMessageReactionsListRequest
    from telethon.tl.types import ReactionEmoji
    from telethon import errors as terrors

    out = {"helmsman": [], "wildcard": []}
    chat = await client.get_entity(chat_id)

    for emoji, key in [("🧭", "helmsman"), ("🌟", "wildcard")]:
        try:
            offset = ""
            collected = 0
            while True:
                result = await client(GetMessageReactionsListRequest(
                    peer=chat,
                    id=msg_id,
                    reaction=ReactionEmoji(emoticon=emoji),
                    offset=offset,
                    limit=100,
                ))
                for reaction in result.reactions:
                    peer = reaction.peer_id
                    user_id = getattr(peer, "user_id", None)
                    if user_id is None:
                        continue
                    # Resolve username via the users list returned alongside
                    username = None
                    for u in result.users:
                        if u.id == user_id:
                            username = u.username
                            break
                    out[key].append({
                        "user_id": user_id,
                        "username": (username.lower() if username else None),
                    })
                    collected += 1
                if not result.next_offset:
                    break
                offset = result.next_offset
                if collected > 1000:  # safety bound
                    break
        except terrors.MessageIdInvalidError:
            log_error(f"Launch announcement message_id={msg_id} not found in chat {chat_id}")
            continue
        except terrors.ReactionInvalidError:
            # The emoji isn't allowed in this chat - treat as zero reactions
            continue
        except Exception as e:
            log_error(f"Failed to read {emoji} reactions on msg {msg_id}", e)
            continue

    return out


async def _bootstrap_async() -> dict:
    """Async heart of the bootstrap subcommand. Returns a dict of results."""
    from telethon import TelegramClient

    api_id, api_hash, phone = _telethon_credentials()
    session_path = _telethon_session_path()

    chat_id_str = os.environ.get("FIRESIDE_TRIBE_CHAT_ID")
    if not chat_id_str:
        raise RuntimeError("FIRESIDE_TRIBE_CHAT_ID not set in .env")
    chat_id = int(chat_id_str)

    msg_id_str = os.environ.get("FIRESIDE_LAUNCH_ANNOUNCEMENT_MSG_ID")
    if not msg_id_str:
        raise RuntimeError("FIRESIDE_LAUNCH_ANNOUNCEMENT_MSG_ID not set in .env")
    msg_id = int(msg_id_str)

    client = TelegramClient(str(session_path), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError(
            "Telethon session not authorised. "
            "Run /telegram skill setup first to create the session."
        )

    try:
        tg_members = await _enumerate_tribe_members(client, chat_id)
        reactions = await _read_launch_reactions(client, chat_id, msg_id)
    finally:
        await client.disconnect()

    return {
        "telegram_members": tg_members,
        "reactions": reactions,
    }


def cross_reference(xlsx_roster: dict, telegram_members: dict) -> tuple[dict, dict]:
    """Build the operational tribe-roster.json and a discrepancy report.

    Args:
        xlsx_roster: dict[telegram_username -> metadata] from load_tribe_metadata()
        telegram_members: dict[telegram_username -> telegram record] from Telethon

    Returns:
        (roster, discrepancy):
            roster: dict[telegram_username -> merged record with telegram_user_id populated]
            discrepancy: dict with keys: in_tg_not_xlsx, in_xlsx_not_tg, no_username_in_tg
    """
    # Lowercase compare
    xlsx_lower = {u.lower(): (u, data) for u, data in xlsx_roster.items()}
    tg_lower = {u: m for u, m in telegram_members.items() if u != "_no_username"}

    # Load exclusions list (CEO-managed; persists across roster rebuilds)
    exclusions = load_state(EXCLUSIONS) or {}
    excluded_users = {k.lower(): v for k, v in exclusions.get("excluded", {}).items()}

    roster = {}
    in_tg_not_xlsx = []
    in_xlsx_not_tg = []

    for u_lower, tg_record in tg_lower.items():
        if tg_record.get("is_bot"):
            continue  # skip the bot itself and any other bots in the group
        if u_lower in xlsx_lower:
            original_username, xlsx_data = xlsx_lower[u_lower]
            merged = dict(xlsx_data)  # copy metadata
            merged["telegram_user_id"] = tg_record["user_id"]
            merged["telegram_full_name"] = tg_record["full_name"]
            # Apply CEO-managed exclusions: stays in Tribe (xlsx + Telegram group)
            # but bot excludes from speaker/helmsman/wildcard rotations.
            if u_lower in excluded_users:
                merged["active"] = False
                merged["excluded_from_fireside"] = True
                merged["exclusion_reason"] = excluded_users[u_lower].get("reason", "")
                merged["excluded_at"] = excluded_users[u_lower].get("excluded_at", "")
            roster[original_username] = merged
        else:
            in_tg_not_xlsx.append({
                "username": u_lower,
                "user_id": tg_record["user_id"],
                "full_name": tg_record["full_name"],
            })

    for u_lower, (original_username, xlsx_data) in xlsx_lower.items():
        if u_lower not in tg_lower:
            in_xlsx_not_tg.append({
                "username": original_username,
                "name": xlsx_data["name"],
            })

    discrepancy = {
        "in_telegram_not_in_xlsx": in_tg_not_xlsx,
        "in_xlsx_not_in_telegram": in_xlsx_not_tg,
        "no_username_in_telegram": telegram_members.get("_no_username", []),
    }
    return roster, discrepancy


def build_roster_by_name(roster: dict) -> dict:
    """Build a name -> roster_entry index for schedule lookups."""
    by_name = {}
    for username, data in roster.items():
        # Index by exact name match (xlsx 'name' field)
        full_name = data.get("name", "").strip()
        if full_name:
            entry = dict(data)
            entry["telegram_username"] = username
            by_name[full_name] = entry
    return by_name


def print_discrepancy_report(discrepancy: dict, missing_in_schedule: list) -> None:
    """Print a human-readable discrepancy summary."""
    in_tg = discrepancy["in_telegram_not_in_xlsx"]
    in_xlsx = discrepancy["in_xlsx_not_in_telegram"]
    no_un = discrepancy["no_username_in_telegram"]

    print()
    print(f"{BOLD}=== Discrepancy report ==={RESET}")

    if not in_tg and not in_xlsx and not no_un and not missing_in_schedule:
        print(f"{GREEN}OK{RESET}  No discrepancies. Telegram group + xlsx fully aligned.")
        return

    if in_tg:
        print(f"{YELLOW}In Telegram group but missing from xlsx ({len(in_tg)}):{RESET}")
        for r in in_tg:
            print(f"  @{r['username']} (id={r['user_id']}) - {r['full_name']}")
    if in_xlsx:
        print(f"{YELLOW}In xlsx but missing from Telegram group ({len(in_xlsx)}):{RESET}")
        for r in in_xlsx:
            print(f"  @{r['username']} - {r['name']}")
    if no_un:
        print(f"{YELLOW}In Telegram group but no username set ({len(no_un)}):{RESET}")
        print(f"      (these users cannot be matched via Telegram Username column)")
        for r in no_un:
            print(f"  user_id={r['user_id']} - {r['full_name']}")
    if missing_in_schedule:
        print(f"{RED}Names in 9-week schedule with no roster match ({len(missing_in_schedule)}):{RESET}")
        for n in sorted(set(missing_in_schedule)):
            print(f"  {n}")
        print(f"      (Verify name spelling in xlsx 'Name' column matches the schedule)")


def cmd_bootstrap(args) -> None:
    """Bootstrap the bot: enumerate Telegram, cross-reference with xlsx, generate schedule + opt-ins."""
    import asyncio

    print(f"{BOLD}=== Phase 2 Bootstrap ==={RESET}")
    ensure_state_dir()

    print(f"{CYAN}1. Loading xlsx roster...{RESET}")
    try:
        xlsx_roster = load_tribe_metadata()
    except (FileNotFoundError, ValueError) as e:
        print(f"{RED}xlsx load failed:{RESET} {e}", file=sys.stderr)
        print(f"{YELLOW}Hint: complete Phase 0 task 0.5 (add Telegram Username column "
              f"to xlsx and populate for all 54 Tribe members).{RESET}", file=sys.stderr)
        sys.exit(1)
    print(f"     {len(xlsx_roster)} entries with telegram_username")

    print(f"{CYAN}2. Connecting to Telegram via Telethon (as Misha)...{RESET}")
    try:
        bootstrap_result = asyncio.run(_bootstrap_async())
    except Exception as e:
        print(f"{RED}Telethon bootstrap failed:{RESET} {e}", file=sys.stderr)
        sys.exit(1)

    tg_members = bootstrap_result["telegram_members"]
    reactions = bootstrap_result["reactions"]
    n_tg = len(tg_members) - 1  # exclude '_no_username' bucket
    n_no_un = len(tg_members.get("_no_username", []))
    print(f"     {n_tg} members with username + {n_no_un} without username")
    print(f"     🧭 helmsman reactors: {len(reactions['helmsman'])}")
    print(f"     🌟 wildcard reactors: {len(reactions['wildcard'])}")

    print(f"{CYAN}3. Cross-referencing xlsx + Telegram members...{RESET}")
    roster, discrepancy = cross_reference(xlsx_roster, tg_members)
    print(f"     {len(roster)} matched entries written to tribe-roster.json")
    save_state(TRIBE_ROSTER, roster)

    print(f"{CYAN}4. Building schedule.json from Week 1-9 calendar...{RESET}")
    roster_by_name = build_roster_by_name(roster)
    existing_schedule = load_state(SCHEDULE)
    if existing_schedule:
        # Don't clobber the live schedule on re-bootstrap. Manual swaps and
        # exclusions are recorded directly in schedule.json (e.g. a mid-cycle
        # speaker swap and a member exclusion); rebuilding from the
        # WEEK_1_TO_9_SCHEDULE constant would wipe both. Compute missing-
        # speaker stats for the discrepancy report but do not save.
        _, missing_in_schedule = build_schedule(roster_by_name)
        print(f"     {len(existing_schedule)} schedule entries already populated; not overwriting "
              f"(delete schedule.json manually for a clean rebuild)")
    else:
        schedule_entries, missing_in_schedule = build_schedule(roster_by_name)
        save_state(SCHEDULE, schedule_entries)
        print(f"     {len(schedule_entries)} schedule entries written ({len(missing_in_schedule)} unresolved)")

    print(f"{CYAN}5. Seeding helmsmen.json (empty - Misha selects Week 1 from reactors)...{RESET}")
    if load_state(HELMSMEN) is None or load_state(HELMSMEN) == {}:
        save_state(HELMSMEN, {})
        print(f"     helmsmen.json initialised empty")
    else:
        print(f"     helmsmen.json already populated; not overwriting")

    print(f"{CYAN}6. Writing opt-ins.json from launch-announcement reactions...{RESET}")
    opt_ins = {
        "helmsman": [
            {"username": r["username"], "user_id": r["user_id"]}
            for r in reactions["helmsman"]
        ],
        "wildcard": [
            {"username": r["username"], "user_id": r["user_id"]}
            for r in reactions["wildcard"]
        ],
    }
    save_state(OPT_INS, opt_ins)
    print(f"     {len(opt_ins['helmsman'])} helmsman opt-ins, {len(opt_ins['wildcard'])} wildcard opt-ins")

    print_discrepancy_report(discrepancy, missing_in_schedule)

    print()
    print(f"{GREEN}OK{RESET}  Bootstrap complete. State files written to {STATE_DIR}")


# ============================================================
# Subcommand: test-telegram (Phase 1 DoD)
# ============================================================

def cmd_test_telegram(args) -> None:
    """Send a smoke-test DM to Misha. Confirms bot can authenticate and DM.

    This is the Phase 1 Definition-of-Done check.
    """
    load_env()
    bot = get_bot()

    misha_id_str = os.environ.get("MISHA_TELEGRAM_USER_ID")
    if not misha_id_str:
        print(f"{RED}ERROR: MISHA_TELEGRAM_USER_ID not set in .env{RESET}", file=sys.stderr)
        sys.exit(1)
    try:
        misha_id = int(misha_id_str)
    except ValueError:
        print(f"{RED}ERROR: MISHA_TELEGRAM_USER_ID is not an integer: {misha_id_str!r}{RESET}",
              file=sys.stderr)
        sys.exit(1)

    # Confirm bot identity first
    me = bot.get_me()
    bot_handle = me.get("username", "unknown")
    bot_id = me.get("id", "?")

    hostname = socket.gethostname()
    now_str = local_now().strftime("%Y-%m-%d %H:%M:%S %Z")
    text = (
        f"\U0001F916 *Bot online — first contact*\n\n"
        f"Host: `{hostname}`\n"
        f"Time: `{now_str}`\n"
        f"Bot: `@{bot_handle}` (id={bot_id})\n\n"
        f"This is the Phase 1 smoke test from `scripts/fireside-bot.py test-telegram`. "
        f"If you're reading this, the bot can DM you. Phase 1 DoD ✅"
    )
    try:
        result = bot.send_dm(misha_id, text)
    except TelegramAPIError as e:
        print(f"{RED}FAIL{RESET}  Could not DM Misha (user_id={misha_id})", file=sys.stderr)
        print(f"      {e}", file=sys.stderr)
        sys.exit(1)

    msg_id = result.get("message_id") if isinstance(result, dict) else "?"
    print(f"{GREEN}OK{RESET}  DM sent to Misha (user_id={misha_id})")
    print(f"     Telegram message_id = {msg_id}")
    print(f"     Bot: @{bot_handle} (id={bot_id})")
    print(f"     Time: {now_str}")


# ============================================================
# Subcommand: xlsx-check (Phase 1 DoD helper)
# ============================================================

def cmd_xlsx_check(args) -> None:
    """Print xlsx loader output summary. Verifies load_tribe_metadata() works."""
    try:
        roster = load_tribe_metadata()
    except (FileNotFoundError, ValueError) as e:
        print(f"{RED}xlsx load failed:{RESET} {e}", file=sys.stderr)
        sys.exit(1)

    n = len(roster)
    print(f"{CYAN}xlsx loaded: {n} entries with telegram_username{RESET}")
    if n == 0:
        print(f"{YELLOW}Note: 0 entries means the Telegram Username column exists but is empty.{RESET}")
        print(f"{YELLOW}      Populate it for all 54 Tribe members (Phase 0 task 0.5).{RESET}")
        return

    sample = list(roster.items())[:3]
    print(f"{GRAY}Sample (first {len(sample)}):{RESET}")
    for username, data in sample:
        vp_marker = " [VP]" if data["is_vp"] else ""
        print(f"  @{username}: {data['name']}{vp_marker}")
        print(f"      function={data['function']!r}, title={data['title']!r}")
        print(f"      email={data['email']}, user_id={data['telegram_user_id']}")

    vps = sum(1 for d in roster.values() if d["is_vp"])
    no_user_id = sum(1 for d in roster.values() if d["telegram_user_id"] is None)
    print(f"{GRAY}Totals: {n} entries, {vps} VPs, {no_user_id} awaiting telegram_user_id{RESET}")


# ============================================================
# Subcommand: init-state (Phase 1 DoD helper)
# ============================================================

def cmd_init_state(args) -> None:
    """Initialise state directory + empty state files. Idempotent."""
    ensure_state_dir()
    print(f"{GREEN}OK{RESET}  State directory ready: {STATE_DIR}")
    files = [TRIBE_ROSTER, SCHEDULE, HELMSMEN, OPT_INS,
             DM_LOG, SESSIONS_LOG, LAST_UPDATE_ID, LAST_PINNED, ERRORS_LOG]
    for name in files:
        path = state_path(name)
        if path.exists():
            size = path.stat().st_size
            print(f"     {path.name}: {size} bytes")
        else:
            print(f"     {RED}{path.name}: MISSING{RESET}")


# ============================================================
# Templates (Phase 3) - mirror outputs/operations/tribe-fireside/*-template.md
# ============================================================

# Note: keep these in sync with the .md template files. The .md is the human
# reference; this is what the bot actually renders.

SPEAKER_DM_2WK = """Hi {name},

You're on the speaker list for the Tribe fireside on {session_date} ({session_day}). The theme that week is **{theme}**.

You'll have 5 minutes to share + a couple of minutes for questions from the Tribe.

Some category ideas if helpful: a book, a place you've lived or want to live, a kid story, something you're proud of (outside work), the most interesting thing you learned this month, a question you want to ask the Tribe, your hometown, a skill you're learning, your last weekend - or something completely else if the theme sparks something.

Two weeks gives you time to think about what you want to share. No pressure to be polished.

If the date doesn't work, send /swap to the bot - it'll show you open dates you can move to (or arrange a swap with another speaker) on the spot.

— 31C Fireside Bot"""

SPEAKER_DM_3DAY = """Hi {name},

Your Tribe fireside slot is in 3 days — {session_day} {session_date}. Theme: **{theme}**.

5 minutes to share, then Q&A. Format is intentionally informal — no slides, just talk.

Drop a 1-paragraph preview into the 31C Tribe group on Sunday evening if you'd like — it warms the room. Optional.

— 31C Fireside Bot"""

SPEAKER_DM_DAYOF = """Hi {name},

Your Tribe fireside is at 18:30 local time today.

Zoom: {zoom_link}

5 min share + Q&A. Helmsman this week is {helmsman_name}.

— 31C Fireside Bot"""

HELMSMAN_BRIEF = """Hi {name},

You're the Helmsman for the week starting {week_starting}. Your job: open and close two firesides — Mon {monday_date} and Wed {wednesday_date} at 18:30 local time — and hold the line on the format.

**This week's speakers:**
- Mon: {monday_speakers}
- Wed: {wednesday_speakers}
- Theme: **{theme}**

**Wildcard roster** (in case of no-show, in priority order):
{wildcard_list}

**Pop-up rules to read at session open:**
"Welcome. For the next thirty minutes — no work talk except things you're proud of. No laptops. No Slack. We're here to actually meet each other. Theme this week: {theme}. Speakers today are [names]. Five minutes each plus a couple of minutes for questions. Let's start."

**Closing go-around (last 5 min):** pick 4 random Tribe members from the audience. "One thing you're taking from this session — 30 seconds each."

**If a speaker no-shows:** DM the wildcard roster in order. First to respond within 90 sec takes the slot. If no response, run a group prompt: "Quick — [theme-relevant question]. Anyone share for two minutes."

**If somebody slips into work talk:** gentle redirect. "Let's hold that for the standup, this is the fireside."

**Time discipline:** 30 min default. Flex to 35 max if room is in flow. Beyond 35 — close cleanly.

You've got this. The role rotates - somebody else next week. DM Misha if anything goes sideways.

— 31C Fireside Bot"""

EMAIL_BACKUP_SUBJECT = "Your Tribe fireside slot — {session_date}"
EMAIL_BACKUP_BODY = """Hi {name},

Quick note from the Tribe fireside system — you're on the speaker list for {session_date} ({session_day}). I've sent you a few Telegram DMs about it but haven't seen a response, so wanted to make sure you saw this here too.

Theme that week: {theme}

5 minutes to share, plus a couple of minutes for Q&A. Format is informal.

If the date doesn't work, reply to this email and we'll find a swap.

— 31C Fireside Bot
(via ceo@31c.io if you need to reach a human)"""

SUNDAY_PREVIEW = """🔥 **Tribe fireside this week**

**Theme:** {theme}

**Monday {monday_date}:** {monday_speakers}
**Wednesday {wednesday_date}:** {wednesday_speakers}

Speakers — drop a 1-paragraph preview + a photo as a reply to this message if you'd like. Warms the room.
Tribe — react with 🔥 📚 🌍 ❤️ to whatever resonates.

**Helmsman this week:** {helmsman_name}

Zoom: {zoom_link} · Same recurring link every week.

See you Monday at 18:30 local time."""


UNAUTHORIZED_REPLY = (
    "This bot is private to the 31C Tribe Fireside. "
    "If you think you should have access, message Misha."
)

OUTSIDER_FORWARD_COOLDOWN_S = 3600  # 1 forward to Misha per outsider per hour


WELCOME_DM = """Welcome to the Tribe Fireside Bot.

You're subscribed to automatic reminders:
  - 2 weeks before you speak
  - 3 days before
  - Day-of, with the Zoom link

Format: Tribe Fireside runs every Mon and Wed at 18:30 local time. Three speakers per session (~7 min each). Theme rotates weekly.

Commands you can use anytime:
  /me        - when am I scheduled to speak?
  /next      - who's at the next session?
  /who       - speakers and theme for this week
  /theme     - this week's theme only
  /schedule  - full 9-week schedule
  /zoom      - Zoom link
  /swap      - show open dates or counterparty swaps you can move to
  /idea      - propose a topic for a future fireside
  /help      - this menu

Questions or feedback? Just reply here. Misha reads DMs."""


HELP_DM = """Tribe Fireside Bot - commands:

  /me        - when am I scheduled to speak?
  /next      - who's at the next session?
  /who       - speakers and theme for this week
  /theme     - this week's theme only
  /schedule  - full 9-week schedule
  /zoom      - Zoom link
  /swap      - show open dates or counterparty swaps you can move to
  /idea      - propose a topic for a future fireside
  /help      - this menu

Sessions run Mon and Wed at 18:30 local time. Reply here with any question."""


# ============================================================
# Phase 3 helpers
# ============================================================

def _format_session_date(date_iso: str) -> tuple[str, str]:
    """Return ('YYYY-MM-DD', 'Mon'/'Wed') human strings."""
    from datetime import date as _date
    d = _date.fromisoformat(date_iso)
    day_name = d.strftime("%a")
    return d.isoformat(), day_name


def _zoom_url() -> str:
    """Return the recurring Zoom URL from env, or a placeholder warning string."""
    load_env()
    url = os.environ.get("FIRESIDE_ZOOM_URL", "").strip()
    if not url:
        return "[FIRESIDE_ZOOM_URL not set in .env - Misha needs to add it]"
    return url


def _week_speakers(schedule: list, week: int, day: str) -> list[dict]:
    """Return speaker entries for a given week + day (Mon/Wed) sorted by slot."""
    return sorted(
        [s for s in schedule if s["week"] == week and s["day"] == day],
        key=lambda s: s["slot"],
    )


def _today_local_date():
    """Return today's date in local timezone as a date object."""
    return local_now().date()


def _current_or_upcoming_week(schedule: list, today=None) -> Optional[int]:
    """Return the week number of the current or next upcoming session.

    If today's date matches a session date, return that week.
    Otherwise return the week of the next future session.
    Returns None if no future sessions remain.
    """
    from datetime import date as _date
    if today is None:
        today = _today_local_date()
    upcoming = [s for s in schedule if _date.fromisoformat(s["session_date"]) >= today]
    if not upcoming:
        return None
    upcoming.sort(key=lambda s: s["session_date"])
    return upcoming[0]["week"]


def _resolve_speaker_user_id(roster: dict, speaker_username: Optional[str]) -> Optional[int]:
    """Look up a speaker's telegram_user_id from the roster by username."""
    if not speaker_username:
        return None
    entry = roster.get(speaker_username)
    if entry is None:
        # try lowercase match
        for username, data in roster.items():
            if username.lower() == speaker_username.lower():
                entry = data
                break
    return entry.get("telegram_user_id") if entry else None


def _dm_already_sent(dm_log_path: Path, speaker_username: str, dm_type: str,
                     session_date: str) -> bool:
    """Check dm-log.jsonl to see if a specific DM type has already been sent
    for a specific speaker + session."""
    if not dm_log_path.exists():
        return False
    with open(dm_log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (entry.get("dm_type") == dm_type
                    and entry.get("speaker_username") == speaker_username
                    and entry.get("session_date") == session_date
                    and entry.get("delivered")):
                return True
    return False


def _log_dm(dm_type: str, speaker_username: str, session_date: str,
            user_id: Optional[int], delivered: bool, error: Optional[str] = None) -> None:
    """Append a DM event to dm-log.jsonl."""
    append_jsonl(DM_LOG, {
        "ts": local_now().isoformat(),
        "dm_type": dm_type,
        "speaker_username": speaker_username,
        "session_date": session_date,
        "user_id": user_id,
        "delivered": delivered,
        "error": error,
    })


def _log_event(event_type: str, **fields) -> None:
    """Append a generic event to sessions.jsonl."""
    payload = {"ts": local_now().isoformat(), "event_type": event_type}
    payload.update(fields)
    append_jsonl(SESSIONS_LOG, payload)


# ============================================================
# Subcommand: poll (Phase 3 task 3.1)
# ============================================================

def cmd_poll(args) -> None:
    """Process Telegram updates: /start, /swap, message_reaction, chat_member events.

    Cron: every 5 min.
    Drains queue if 100 updates returned (cap-hit) to prevent 24h retention loss.
    """
    bot = get_bot()
    last = load_state(LAST_UPDATE_ID) or {"offset": 0}
    offset = int(last.get("offset", 0))
    total_processed = 0

    # Append a poll-start marker to dm-log so health-check can detect liveness
    append_jsonl(DM_LOG, {
        "ts": local_now().isoformat(),
        "dm_type": "poll-tick",
    })

    while True:
        try:
            updates = bot.get_updates(offset=offset, timeout=25, limit=100)
        except TelegramAPIError as e:
            print(f"{RED}poll: getUpdates failed: {e}{RESET}", file=sys.stderr)
            return

        if not updates:
            break

        for update in updates:
            try:
                _handle_update(bot, update)
            except Exception as e:
                log_error(f"poll: failed to handle update {update.get('update_id')}", e)
            new_offset = update.get("update_id", 0) + 1
            if new_offset > offset:
                offset = new_offset
            total_processed += 1

        save_state(LAST_UPDATE_ID, {"offset": offset})

        if len(updates) < 100:
            break  # not at cap, queue is drained

    if total_processed:
        print(f"{GRAY}poll: processed {total_processed} update(s){RESET}")
    hc_ping("FIRESIDE_HC_POLL")


def _handle_update(bot: TelegramBot, update: dict) -> None:
    """Route a single Telegram update by type."""
    # Lazy expiration: walk swap-requests.jsonl, expire any past-deadline `proposed_to_b`.
    # Cheap while the log stays small; revisit if it grows past ~10k events.
    try:
        _sweep_expired_swap_requests(bot)
    except Exception as e:
        log_error("swap-expiration sweep failed", e)

    if "message" in update:
        _handle_message(bot, update["message"])
    elif "callback_query" in update:
        _handle_callback_query(bot, update["callback_query"])
    elif "message_reaction" in update:
        _handle_message_reaction(update["message_reaction"])
    elif "chat_member" in update:
        _handle_chat_member(update["chat_member"])
    elif "my_chat_member" in update:
        # bot's own membership changed; log and ignore for now
        log_error(f"my_chat_member event: {json.dumps(update['my_chat_member'])[:200]}")


def _resolve_my_username(user_id: int) -> Optional[str]:
    """Return the canonical roster-key @username bound to this Telegram user_id.

    Roster keys are the @usernames used as join keys with schedule.json /
    helmsmen.json. Resolution is by telegram_user_id ONLY -- the immutable,
    Telegram-assigned id. We deliberately do NOT fall back to a self-reported
    @username: that would let someone who has claimed a former member's dropped
    handle resolve to that member's schedule/helmsman rows (handle takeover).
    telegram_user_id is bound only by the trusted bootstrap, so any authorized
    caller already has a user_id match here.
    """
    roster = load_state(TRIBE_ROSTER) or {}
    for k, v in roster.items():
        if v.get("telegram_user_id") == user_id:
            return k
    return None


def _cmd_me_text(user_id: int) -> str:
    """Personalised schedule view for the calling user (identity by user_id)."""
    from datetime import date as _date
    schedule = load_state(SCHEDULE) or []
    helmsmen = load_state(HELMSMEN) or {}
    my_username = _resolve_my_username(user_id)
    if not my_username:
        return ("Couldn't find you in the Tribe roster. "
                "If you think this is wrong, reply here and Misha will check.")
    today = _today_local_date()
    upcoming, past = [], []
    for e in schedule:
        if (e.get("speaker_username") or "").lower() != my_username.lower():
            continue
        d = _date.fromisoformat(e["session_date"])
        line = (f"  - {e['session_date']} ({e['day']}) - "
                f"Week {e['week']}, slot {e['slot']}, theme: {e['theme']}")
        (upcoming if d >= today else past).append(line)
    lines = []
    if upcoming:
        lines.append("Upcoming speaker slots:")
        lines.extend(upcoming)
    else:
        lines.append("No upcoming speaker slots in this cycle.")
    if past:
        lines.append("")
        lines.append(f"Completed: {len(past)} session(s).")
    upcoming_helmsman = []
    for week_start, entry in helmsmen.items():
        if (entry.get("username") or "").lower() != my_username.lower():
            continue
        try:
            d = _date.fromisoformat(week_start)
        except ValueError:
            continue
        if d >= today:
            tag = " (already briefed)" if entry.get("briefed") else ""
            upcoming_helmsman.append((d, week_start, tag))
    if upcoming_helmsman:
        upcoming_helmsman.sort()
        lines.append("")
        lines.append("Helmsman weeks (you run the sessions):")
        for d, ws, tag in upcoming_helmsman:
            lines.append(f"  - Week starting {ws}{tag}")
    return "\n".join(lines)


def _cmd_next_text() -> str:
    """The very next future session: date, speakers, helmsman.

    Treats today as "past" after 19:30 local (sessions are 18:30-19:00 + buffer).
    """
    from datetime import date as _date, timedelta
    schedule = load_state(SCHEDULE) or []
    helmsmen = load_state(HELMSMEN) or {}
    now = local_now()
    today = now.date()
    cutoff = today
    if now.hour > 19 or (now.hour == 19 and now.minute >= 30):
        cutoff = today + timedelta(days=1)
    upcoming = [e for e in schedule if _date.fromisoformat(e["session_date"]) >= cutoff]
    if not upcoming:
        return "No upcoming sessions in the current cycle."
    upcoming.sort(key=lambda e: (e["session_date"], e["slot"]))
    next_date = upcoming[0]["session_date"]
    next_speakers = sorted([e for e in upcoming if e["session_date"] == next_date],
                           key=lambda e: e["slot"])
    d = _date.fromisoformat(next_date)
    mon_of_week = d - timedelta(days=d.weekday())
    h_entry = helmsmen.get(mon_of_week.isoformat()) or {}
    lines = [
        f"Next session: {d.strftime('%A')} {next_date} at 18:30 local time",
        f"Week {next_speakers[0]['week']}, theme: {next_speakers[0]['theme']}",
        "",
        "Speakers:",
    ]
    for e in next_speakers:
        lines.append(f"  {e['slot']}. {e['speaker_name']} (@{e['speaker_username']})")
    lines.append("")
    lines.append(f"Helmsman: {h_entry.get('name', 'TBD')}")
    return "\n".join(lines)


def _cmd_who_text() -> str:
    """This week's both sessions (Mon + Wed)."""
    schedule = load_state(SCHEDULE) or []
    helmsmen = load_state(HELMSMEN) or {}
    week_num = _current_or_upcoming_week(schedule)
    if week_num is None:
        return "No active week in the schedule."
    mon = _week_speakers(schedule, week_num, "Mon")
    wed = _week_speakers(schedule, week_num, "Wed")
    if not mon and not wed:
        return f"Week {week_num} has no scheduled speakers."
    theme = (mon[0] if mon else wed[0])["theme"]
    lines = [f"Week {week_num} - theme: {theme}", ""]
    if mon:
        lines.append(f"Mon {mon[0]['session_date']}:")
        for e in mon:
            lines.append(f"  {e['slot']}. {e['speaker_name']} (@{e['speaker_username']})")
    if wed:
        if mon:
            lines.append("")
        lines.append(f"Wed {wed[0]['session_date']}:")
        for e in wed:
            lines.append(f"  {e['slot']}. {e['speaker_name']} (@{e['speaker_username']})")
    week_start = mon[0]["session_date"] if mon else None
    if week_start:
        h_entry = helmsmen.get(week_start) or {}
        if h_entry.get("name"):
            lines.append("")
            lines.append(f"Helmsman: {h_entry['name']}")
    return "\n".join(lines)


def _cmd_theme_text() -> str:
    schedule = load_state(SCHEDULE) or []
    week_num = _current_or_upcoming_week(schedule)
    if week_num is None:
        return "No active week in the schedule."
    entries = [e for e in schedule if e["week"] == week_num]
    if not entries:
        return f"Week {week_num} has no entries."
    return f"Week {week_num} theme: {entries[0]['theme']}"


def _cmd_schedule_text() -> str:
    schedule = load_state(SCHEDULE) or []
    if not schedule:
        return "No schedule loaded."
    weeks = sorted(set(e["week"] for e in schedule))
    lines = ["Tribe Fireside - full 9-week schedule (Mon and Wed at 18:30 local time)"]
    for w in weeks:
        wk = [e for e in schedule if e["week"] == w]
        if not wk:
            continue
        lines.append("")
        lines.append(f"Week {w} - {wk[0]['theme']}")
        mon = sorted([e for e in wk if e["day"] == "Mon"], key=lambda e: e["slot"])
        wed = sorted([e for e in wk if e["day"] == "Wed"], key=lambda e: e["slot"])
        if mon:
            names = ", ".join(e["speaker_name"] for e in mon)
            lines.append(f"  Mon {mon[0]['session_date']}: {names}")
        if wed:
            names = ", ".join(e["speaker_name"] for e in wed)
            lines.append(f"  Wed {wed[0]['session_date']}: {names}")
    return "\n".join(lines)


def _cmd_zoom_text() -> str:
    return (f"Zoom: {_zoom_url()}\n"
            "Same recurring link every Mon and Wed at 18:30 local time.")


def _is_authorized_user(user_id: int, username: Optional[str] = None) -> bool:
    """True iff user_id maps to an active, non-excluded Tribe roster member.

    Authorization is by Telegram user_id ONLY. A user_id is immutable and
    assigned by Telegram; a @username is mutable and reclaimable, so a username
    match is NOT proof of identity. Binding a user_id to a roster entry happens
    exclusively through the trusted `bootstrap` subcommand, which enumerates the
    real Tribe group via Misha's authenticated Telethon session (see
    cross_reference()). DM handlers never persist telegram_user_id from a
    self-reported username -- doing so would let anyone who claims a former
    member's dropped @handle take over that member's authorization (handle
    takeover). After a state-loss that leaves entries with telegram_user_id=None,
    recovery is a `bootstrap` run, not a self-reported DM.

    `username` is accepted only so callers can log it; it is deliberately ignored
    for the authorization decision.
    """
    if not user_id:
        return False
    roster = load_state(TRIBE_ROSTER) or {}
    for v in roster.values():
        if v.get("telegram_user_id") == user_id:
            return bool(v.get("active")) and not v.get("excluded_from_fireside", False)
    return False


def _maybe_forward_outsider(bot: "TelegramBot", user_id: int,
                             username: str, text: str) -> None:
    """Forward an outsider DM to Misha at most once per hour per user_id.

    Rate-limited via OUTSIDER_RATE state file. Failures to forward are silent;
    callers still log to sessions.jsonl regardless.
    """
    try:
        misha_id = int(os.environ.get("MISHA_TELEGRAM_USER_ID", "0"))
    except ValueError:
        misha_id = 0
    if not misha_id:
        return
    rate = load_state(OUTSIDER_RATE) or {}
    last_iso = rate.get(str(user_id))
    if last_iso:
        try:
            last_dt = datetime.fromisoformat(last_iso)
            if (local_now() - last_dt).total_seconds() < OUTSIDER_FORWARD_COOLDOWN_S:
                return
        except ValueError:
            pass  # corrupt timestamp - forward and rewrite
    preview = (text or "(no text)")[:300]
    if text and len(text) > 300:
        preview += "..."
    try:
        bot.send_message(
            misha_id,
            f"Outsider DM to Fireside bot from @{username or '(no username)'} "
            f"(id={user_id}):\n{preview}",
            parse_mode="",
        )
    except TelegramAPIError:
        pass
    rate[str(user_id)] = local_now().isoformat()
    save_state(OUTSIDER_RATE, rate)


# ============================================================
# /swap interactive flow (Phase 3.5: self-serve swap state machine)
# ============================================================
#
# Event log lives at SWAP_REQUESTS_LOG (append-only JSONL). Each event row
# carries `rid` (request id, 8 hex chars). Latest event per rid defines
# current status: initiated -> a_tapped_vacancy|a_tapped_counterparty|cancelled_by_a;
# a_tapped_counterparty -> b_accepted|b_declined|expired. Terminal events are
# *_completed / *_declined / cancelled_by_a / expired.
#
# Callback_data schemas (Telegram limit: 64 bytes):
#   sw:a:<rid>:<idx>     - A tapped candidate idx 0/1
#   sw:a:<rid>:x         - A tapped Cancel
#   sw:b:<rid>:y         - B accepted
#   sw:b:<rid>:n         - B declined

import secrets as _secrets  # noqa: E402  (module-level import already present elsewhere)


def _new_request_id() -> str:
    """Return an 8-char hex request id, collision risk negligible per session."""
    return _secrets.token_hex(4)


def _format_dm_date(date_iso: str, day: str) -> str:
    """'2026-06-08' + 'Mon' -> 'Mon, 8 Jun'."""
    from datetime import date as _date
    d = _date.fromisoformat(date_iso)
    return f"{day}, {d.day} {d.strftime('%b')}"


def _user_current_slot(schedule: list, username: str, today) -> Optional[dict]:
    """Return A's nearest future schedule entry, or None.

    'Future' means session_date >= today. If A has multiple future slots,
    returns the closest one by date.
    """
    from datetime import date as _date
    candidates = []
    uname_lc = username.lower()
    for e in schedule:
        if (e.get("speaker_username") or "").lower() != uname_lc:
            continue
        try:
            d = _date.fromisoformat(e["session_date"])
        except (ValueError, KeyError):
            continue
        if d >= today:
            candidates.append((d, e))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def find_swap_candidates(schedule: list, current_username: str, today,
                          horizon_weeks: int = SWAP_HORIZON_WEEKS,
                          limit: int = SWAP_CANDIDATES_LIMIT) -> list[dict]:
    """Return up to `limit` swap candidates for `current_username`.

    Candidate shape:
      {"kind": "vacancy"|"counterparty", "date": "YYYY-MM-DD", "day": "Mon"|"Wed",
       "slot": int, "b_username": str|None, "b_user_id": int|None, "b_name": str|None}

    Logic:
      - Horizon: sessions where session_date in [today, today+horizon_weeks*7].
      - Exclude A's own current week (no point swapping into your own session).
      - Vacancies first (sessions with <3 filled slots OR entries with null speaker),
        ordered by date proximity.
      - Then counterparties (other speakers in future sessions, one per session,
        slot 1 preferred), ordered by date proximity.
      - Returns at most `limit` candidates total.
    """
    from datetime import date as _date, timedelta
    from collections import defaultdict

    horizon = today + timedelta(weeks=horizon_weeks)
    uname_lc = current_username.lower()
    by_date = defaultdict(list)
    for e in schedule:
        try:
            d = _date.fromisoformat(e["session_date"])
        except (ValueError, KeyError):
            continue
        if today <= d <= horizon:
            by_date[e["session_date"]].append(e)

    # Identify A's own session dates so we don't propose swapping into them.
    a_dates = {e["session_date"] for e in schedule
               if (e.get("speaker_username") or "").lower() == uname_lc}

    vacancies = []
    counterparties = []
    for date_iso in sorted(by_date.keys()):
        if date_iso in a_dates:
            continue
        entries = by_date[date_iso]
        day = entries[0].get("day", "")
        filled_slots = {e["slot"] for e in entries if e.get("speaker_username")}
        # Vacancy: missing slot number, or entry with null speaker
        for slot_num in (1, 2, 3):
            if slot_num not in filled_slots:
                vacancies.append({
                    "kind": "vacancy",
                    "date": date_iso,
                    "day": day,
                    "slot": slot_num,
                    "b_username": None,
                    "b_user_id": None,
                    "b_name": None,
                })
                break  # one vacancy per session is enough for picker
        else:
            # Session is full - pick a counterparty (lowest slot number)
            counterparty_entries = sorted(
                [e for e in entries if (e.get("speaker_username") or "").lower() != uname_lc],
                key=lambda e: e["slot"],
            )
            if counterparty_entries:
                b = counterparty_entries[0]
                counterparties.append({
                    "kind": "counterparty",
                    "date": date_iso,
                    "day": day,
                    "slot": b["slot"],
                    "b_username": b.get("speaker_username"),
                    "b_user_id": None,  # resolved later via roster
                    "b_name": b.get("speaker_name"),
                })

    combined = vacancies + counterparties
    return combined[:limit]


def _resolve_user_id(username: str) -> Optional[int]:
    """Look up a Tribe member's Telegram user_id by username (case-insensitive)."""
    if not username:
        return None
    roster = load_state(TRIBE_ROSTER) or {}
    uname_lc = username.lower()
    for k, v in roster.items():
        if k.lower() == uname_lc:
            uid = v.get("telegram_user_id")
            return int(uid) if uid else None
    return None


def _append_swap_event(payload: dict) -> None:
    """Append one event to swap-requests.jsonl with ts auto-set."""
    enriched = {"ts": local_now().isoformat()}
    enriched.update(payload)
    append_jsonl(SWAP_REQUESTS_LOG, enriched)


def _load_swap_requests() -> dict:
    """Return {rid: list[event]} from the swap-requests JSONL."""
    from collections import defaultdict
    by_rid = defaultdict(list)
    path = state_path(SWAP_REQUESTS_LOG)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = e.get("rid")
            if rid:
                by_rid[rid].append(e)
    return dict(by_rid)


def _swap_request_status(events: list) -> str:
    """Derive current status from an ordered list of events for one rid."""
    if not events:
        return "unknown"
    return events[-1].get("event", "unknown")


def _swap_request_context(events: list) -> dict:
    """Merge all events for one rid into a single dict (last-wins). Use for read-only lookup."""
    ctx: dict = {}
    for e in events:
        ctx.update(e)
    return ctx


def _apply_vacancy_swap(a_username: str, a_current_date: str, a_current_slot: int,
                        target_date: str, target_slot: int, target_day: str,
                        theme_target: str, week_target: int) -> bool:
    """Move A from (a_current_date, a_current_slot) into the vacancy (target_date, target_slot).

    Mutates schedule.json atomically via save_state. Returns True on success.

    Side effects:
      - The entry at A's old slot is removed (creates a vacancy at the old date).
      - A new entry is appended for the target slot with A's name + swapped_with metadata.
    """
    schedule = load_state(SCHEDULE) or []
    uname_lc = a_username.lower()
    # Find A's entry to lift name + display name
    a_entry = None
    new_schedule = []
    for e in schedule:
        is_a_current = (
            (e.get("speaker_username") or "").lower() == uname_lc
            and e.get("session_date") == a_current_date
            and e.get("slot") == a_current_slot
        )
        if is_a_current:
            a_entry = e
            continue  # drop A from old slot
        new_schedule.append(e)
    if a_entry is None:
        log_error(f"_apply_vacancy_swap: A's slot not found ({a_username} @ {a_current_date} #{a_current_slot})")
        return False
    new_schedule.append({
        "cycle": a_entry.get("cycle", 1),
        "week": week_target,
        "session_date": target_date,
        "day": target_day,
        "theme": theme_target,
        "slot": target_slot,
        "speaker_name": a_entry.get("speaker_name"),
        "speaker_username": a_entry.get("speaker_username"),
        "swapped_with": {
            "with_username": None,  # vacancy fill, no counterparty
            "old_date": a_current_date,
            "old_slot": a_current_slot,
            "swapped_at": local_now().isoformat(),
        },
        "no_show": False,
        "completed": False,
    })
    save_state(SCHEDULE, new_schedule)
    return True


def _apply_bilateral_swap(a_username: str, a_current_date: str, a_current_slot: int,
                          b_username: str, b_date: str, b_slot: int) -> bool:
    """Swap A's (date, slot) with B's (date, slot). Atomic.

    Both entries are updated in-place: speaker_name/username swap between them,
    and both get `swapped_with` metadata pointing to the other.
    """
    schedule = load_state(SCHEDULE) or []
    uname_lc_a = a_username.lower()
    uname_lc_b = (b_username or "").lower()
    a_idx = b_idx = None
    for i, e in enumerate(schedule):
        if (a_idx is None
                and (e.get("speaker_username") or "").lower() == uname_lc_a
                and e.get("session_date") == a_current_date
                and e.get("slot") == a_current_slot):
            a_idx = i
        elif (b_idx is None
                and (e.get("speaker_username") or "").lower() == uname_lc_b
                and e.get("session_date") == b_date
                and e.get("slot") == b_slot):
            b_idx = i
    if a_idx is None or b_idx is None:
        log_error(f"_apply_bilateral_swap: entries not found "
                  f"(a={a_username}@{a_current_date}#{a_current_slot} a_idx={a_idx}, "
                  f"b={b_username}@{b_date}#{b_slot} b_idx={b_idx})")
        return False

    swap_ts = local_now().isoformat()
    a_entry = schedule[a_idx]
    b_entry = schedule[b_idx]

    # Swap speaker identity between the two entries; preserve session metadata
    # (date, slot, week, theme) on each row.
    a_name, a_user = a_entry.get("speaker_name"), a_entry.get("speaker_username")
    b_name, b_user = b_entry.get("speaker_name"), b_entry.get("speaker_username")
    a_entry["speaker_name"] = b_name
    a_entry["speaker_username"] = b_user
    a_entry["swapped_with"] = {
        "with_username": a_user,  # B's row now records that the previous occupant was A
        "old_date": b_date,
        "old_slot": b_slot,
        "swapped_at": swap_ts,
    }
    b_entry["speaker_name"] = a_name
    b_entry["speaker_username"] = a_user
    b_entry["swapped_with"] = {
        "with_username": b_user,
        "old_date": a_current_date,
        "old_slot": a_current_slot,
        "swapped_at": swap_ts,
    }
    save_state(SCHEDULE, schedule)
    return True


def _sweep_expired_swap_requests(bot: TelegramBot) -> None:
    """Walk swap-requests.jsonl, expire any `proposed_to_b` past deadline.

    Lazy: runs at the top of every `_handle_update`. Performance is fine while
    the log stays small; if it grows past ~10k entries, switch to a tail-only
    read or move to a dedicated daemon refresher.
    """
    from datetime import datetime as _dt
    swap_requests = _load_swap_requests()
    now = local_now()
    for rid, events in swap_requests.items():
        status = _swap_request_status(events)
        if status != "proposed_to_b":
            continue
        ctx = _swap_request_context(events)
        deadline_iso = ctx.get("deadline")
        if not deadline_iso:
            continue
        try:
            deadline = _dt.fromisoformat(deadline_iso)
        except ValueError:
            continue
        if now <= deadline:
            continue
        # Expired - notify A and Misha, mark terminal
        _append_swap_event({"rid": rid, "event": "expired",
                            "expired_at": now.isoformat()})
        a_user_id = ctx.get("a_user_id")
        a_username = ctx.get("a_username", "")
        b_username = ctx.get("b_username", "")
        try:
            if a_user_id:
                bot.send_message(
                    int(a_user_id),
                    f"Your swap request to @{b_username} expired (no response in 24h). "
                    f"I'll let Misha know - he'll arrange another date with you.",
                    parse_mode="",
                )
        except TelegramAPIError:
            pass
        misha_id = int(os.environ.get("MISHA_TELEGRAM_USER_ID", "0"))
        if misha_id:
            try:
                bot.send_message(
                    misha_id,
                    f"Swap request expired: @{a_username} -> @{b_username} "
                    f"(no response in 24h). A will need manual help.",
                    parse_mode="",
                )
            except TelegramAPIError:
                pass


def _swap_kickoff_for_a(bot: TelegramBot, user_id: int, username: str) -> None:
    """Run the /swap command flow for user A. Either auto-fallback or present 2 buttons.

    `username` MUST be the canonical roster-key username resolved from the
    authorized user_id (see _resolve_my_username), never a self-reported Telegram
    handle. The schedule is keyed by roster username, so passing a spoofable
    handle here would let a caller operate on another member's slot.
    """
    from datetime import date as _date
    schedule = load_state(SCHEDULE) or []
    today = _today_local_date()
    a_slot = _user_current_slot(schedule, username, today)
    if not a_slot:
        bot.send_message(
            user_id,
            "You have no upcoming speaker slots in this cycle, so there's nothing to swap. "
            "If this looks wrong, message Misha.",
            parse_mode="",
        )
        return

    candidates = find_swap_candidates(schedule, username, today)
    if not candidates:
        # Fallback to legacy manual flow
        bot.send_message(
            user_id,
            "No open slots in the next 4 weeks. I'll let Misha know you'd like to swap - "
            "he'll reach out shortly to arrange a different date.",
            parse_mode="",
        )
        misha_id = int(os.environ.get("MISHA_TELEGRAM_USER_ID", "0"))
        if misha_id:
            try:
                bot.send_message(
                    misha_id,
                    f"/swap from @{username} (id={user_id}) - "
                    f"no auto-candidates available. Currently scheduled "
                    f"{a_slot['session_date']} #{a_slot['slot']}. Reach out manually.",
                    parse_mode="",
                )
            except TelegramAPIError:
                pass
        _log_event("swap_requested", user_id=user_id, username=username,
                   outcome="no_candidates")
        return

    # Resolve B user_ids for counterparty candidates (best-effort; None means we can't DM)
    for c in candidates:
        if c["kind"] == "counterparty" and c.get("b_username"):
            c["b_user_id"] = _resolve_user_id(c["b_username"])

    rid = _new_request_id()
    # Build inline keyboard
    buttons = []
    for idx, c in enumerate(candidates):
        label_date = _format_dm_date(c["date"], c["day"])
        if c["kind"] == "vacancy":
            label = f"📅 {label_date} (open slot)"
        else:
            label = f"🔄 {label_date} - swap with @{c['b_username']}"
        buttons.append([{"text": label, "callback_data": f"sw:a:{rid}:{idx}"}])
    buttons.append([{"text": "Cancel", "callback_data": f"sw:a:{rid}:x"}])
    reply_markup = {"inline_keyboard": buttons}

    preamble = (f"You're currently on {_format_dm_date(a_slot['session_date'], a_slot['day'])}, "
                f"slot {a_slot['slot']}.\n\nPick a new date:")
    sent = bot.send_message(user_id, preamble, parse_mode="", reply_markup=reply_markup)
    a_message_id = sent.get("message_id") if isinstance(sent, dict) else None

    _append_swap_event({
        "rid": rid,
        "event": "initiated",
        "a_user_id": user_id,
        "a_username": username,
        "a_current_date": a_slot["session_date"],
        "a_current_slot": a_slot["slot"],
        "a_message_id": a_message_id,
        "candidates": candidates,
    })
    _log_event("swap_requested", user_id=user_id, username=username,
               rid=rid, candidates_count=len(candidates))


def _handle_callback_query(bot: TelegramBot, cq: dict) -> None:
    """Route inline-keyboard taps. Only `sw:*` payloads are handled here."""
    cq_id = cq.get("id")
    data = (cq.get("data") or "").strip()
    user = cq.get("from", {})
    tapper_user_id = user.get("id")
    tapper_username = (user.get("username") or "").lower()
    msg = cq.get("message", {}) or {}
    msg_chat_id = (msg.get("chat") or {}).get("id")
    msg_id = msg.get("message_id")

    # Topic feature: CEO approval of the cycle-end invite. Handled before the
    # sw:* gate. Only the CEO (MISHA_TELEGRAM_USER_ID) may approve/cancel.
    if data.startswith("cycle_invite:"):
        _handle_cycle_invite_tap(bot, cq_id, data, tapper_user_id, msg_chat_id, msg_id)
        return

    if not data.startswith("sw:"):
        # Not our domain - dismiss the spinner silently
        if cq_id:
            try:
                bot.answer_callback_query(cq_id)
            except TelegramAPIError:
                pass
        return

    # Authorization: only active Tribe members may tap swap buttons
    if not _is_authorized_user(tapper_user_id, username=tapper_username):
        if cq_id:
            try:
                bot.answer_callback_query(cq_id, text="Not authorized.")
            except TelegramAPIError:
                pass
        return

    parts = data.split(":")
    # Expected forms:
    #   sw:a:<rid>:<idx-or-x>
    #   sw:b:<rid>:<y|n>
    if len(parts) != 4:
        try:
            bot.answer_callback_query(cq_id, text="Malformed request.")
        except TelegramAPIError:
            pass
        return

    _, role, rid, choice = parts
    swap_requests = _load_swap_requests()
    events = swap_requests.get(rid, [])
    if not events:
        try:
            bot.answer_callback_query(cq_id, text="Request not found.")
            if msg_chat_id and msg_id:
                bot.edit_message_reply_markup(msg_chat_id, msg_id, None)
        except TelegramAPIError:
            pass
        return

    status = _swap_request_status(events)
    ctx = _swap_request_context(events)

    if role == "a":
        _handle_a_tap(bot, cq_id, rid, choice, ctx, status, msg_chat_id, msg_id, tapper_user_id)
    elif role == "b":
        _handle_b_tap(bot, cq_id, rid, choice, ctx, status, msg_chat_id, msg_id, tapper_user_id)
    else:
        try:
            bot.answer_callback_query(cq_id, text="Unknown action.")
        except TelegramAPIError:
            pass


def _handle_cycle_invite_tap(bot: TelegramBot, cq_id, data: str,
                             tapper_user_id, msg_chat_id, msg_id) -> None:
    """Process the CEO's tap on the cycle-end invite draft (send | cancel)."""
    misha_id = int(os.environ.get("MISHA_TELEGRAM_USER_ID", "0"))
    if not misha_id or tapper_user_id != misha_id:
        if cq_id:
            try:
                bot.answer_callback_query(cq_id, text="Only Misha can approve this.")
            except TelegramAPIError:
                pass
        return

    state = ft.load_topic_state(STATE_DIR)
    pending = state.get("pending_cycle_invite")
    if not pending:
        if cq_id:
            try:
                bot.answer_callback_query(cq_id, text="No pending invite.")
                if msg_chat_id and msg_id:
                    bot.edit_message_reply_markup(msg_chat_id, msg_id, None)
            except TelegramAPIError:
                pass
        return

    choice = data.split(":", 1)[1]
    if choice == "cancel":
        state["pending_cycle_invite"] = None
        ft.save_topic_state(STATE_DIR, state)
        _log_event("cycle_end_invite_cancelled", cycle=pending.get("cycle"))
        try:
            bot.answer_callback_query(cq_id, text="Cancelled.")
            if msg_chat_id and msg_id:
                bot.edit_message_text(msg_chat_id, msg_id,
                                      "✖️ Cancelled — nothing was sent to the Tribe.",
                                      parse_mode="", reply_markup=None)
        except TelegramAPIError:
            pass
        return

    if choice == "send":
        try:
            chat_id = int(os.environ["FIRESIDE_TRIBE_CHAT_ID"])
            result = bot.send_message(chat_id, pending["text"])
        except (TelegramAPIError, KeyError, ValueError) as e:
            try:
                bot.answer_callback_query(cq_id, text="Send failed — see logs.")
            except TelegramAPIError:
                pass
            log_error(f"cycle-end-invite send failed: {e}")
            _log_event("cycle_end_invite_send_failed", cycle=pending.get("cycle"), error=str(e))
            return
        # Post succeeded — clear pending immediately so a re-tap cannot double-post.
        state["pending_cycle_invite"] = None
        ft.save_topic_state(STATE_DIR, state)
        # Pin is best-effort; a pin failure must not revert the cleared state.
        try:
            bot.pin_chat_message(chat_id, result.get("message_id"), disable_notification=True)
        except TelegramAPIError:
            pass
        _log_event("cycle_end_invite_sent", cycle=pending.get("cycle"),
                   message_id=result.get("message_id"))
        try:
            bot.answer_callback_query(cq_id, text="Sent to the Tribe.")
            if msg_chat_id and msg_id:
                bot.edit_message_text(msg_chat_id, msg_id,
                                      "✅ Sent to the Tribe and pinned.",
                                      parse_mode="", reply_markup=None)
        except TelegramAPIError:
            pass
        return

    # Unknown choice
    try:
        bot.answer_callback_query(cq_id, text="Unknown action.")
    except TelegramAPIError:
        pass


def _handle_a_tap(bot: TelegramBot, cq_id: str, rid: str, choice: str,
                  ctx: dict, status: str, msg_chat_id, msg_id,
                  tapper_user_id: int) -> None:
    """Process A's button tap. choice is '0', '1', ..., or 'x' (cancel)."""
    # Only the original A may tap
    if ctx.get("a_user_id") != tapper_user_id:
        try:
            bot.answer_callback_query(cq_id, text="This button is for someone else.")
        except TelegramAPIError:
            pass
        return

    if status != "initiated":
        try:
            bot.answer_callback_query(cq_id, text="This request is already closed.")
            if msg_chat_id and msg_id:
                bot.edit_message_reply_markup(msg_chat_id, msg_id, None)
        except TelegramAPIError:
            pass
        return

    if choice == "x":
        _append_swap_event({"rid": rid, "event": "cancelled_by_a"})
        try:
            bot.answer_callback_query(cq_id, text="Cancelled.")
            if msg_chat_id and msg_id:
                bot.edit_message_text(msg_chat_id, msg_id,
                                      "Swap request cancelled. No changes made.",
                                      parse_mode="", reply_markup=None)
        except TelegramAPIError:
            pass
        return

    try:
        idx = int(choice)
    except ValueError:
        try:
            bot.answer_callback_query(cq_id, text="Bad choice.")
        except TelegramAPIError:
            pass
        return

    candidates = ctx.get("candidates") or []
    if idx < 0 or idx >= len(candidates):
        try:
            bot.answer_callback_query(cq_id, text="Choice out of range.")
        except TelegramAPIError:
            pass
        return

    chosen = candidates[idx]
    a_username = ctx.get("a_username", "")
    a_current_date = ctx.get("a_current_date")
    a_current_slot = ctx.get("a_current_slot")

    if chosen["kind"] == "vacancy":
        # Auto-apply immediately; no counterparty consent needed
        # Look up theme/week for the target session by reading any existing entry there
        schedule = load_state(SCHEDULE) or []
        target_week = None
        target_theme = None
        for e in schedule:
            if e.get("session_date") == chosen["date"]:
                target_week = e.get("week")
                target_theme = e.get("theme")
                break
        if target_week is None or target_theme is None:
            try:
                bot.answer_callback_query(cq_id, text="Target session metadata missing.")
            except TelegramAPIError:
                pass
            log_error(f"swap rid={rid}: vacancy target {chosen['date']} has no metadata")
            return

        ok = _apply_vacancy_swap(
            a_username=a_username,
            a_current_date=a_current_date,
            a_current_slot=a_current_slot,
            target_date=chosen["date"],
            target_slot=chosen["slot"],
            target_day=chosen["day"],
            theme_target=target_theme,
            week_target=target_week,
        )
        if not ok:
            try:
                bot.answer_callback_query(cq_id, text="Could not apply swap. Try again or message Misha.")
            except TelegramAPIError:
                pass
            return

        _append_swap_event({
            "rid": rid, "event": "a_tapped_vacancy", "chosen_idx": idx,
            "target_date": chosen["date"], "target_slot": chosen["slot"],
        })
        _append_swap_event({
            "rid": rid, "event": "completed", "outcome": "vacancy_fill",
            "freed_date": a_current_date, "freed_slot": a_current_slot,
        })
        label_date = _format_dm_date(chosen["date"], chosen["day"])
        try:
            bot.answer_callback_query(cq_id, text="Done.")
            if msg_chat_id and msg_id:
                bot.edit_message_text(
                    msg_chat_id, msg_id,
                    f"Done. You're now on {label_date}, slot {chosen['slot']}. "
                    f"Your previous slot ({a_current_date}) is now open.",
                    parse_mode="", reply_markup=None,
                )
        except TelegramAPIError:
            pass
        misha_id = int(os.environ.get("MISHA_TELEGRAM_USER_ID", "0"))
        if misha_id:
            try:
                bot.send_message(
                    misha_id,
                    f"/swap done (vacancy): @{a_username} moved "
                    f"{a_current_date} #{a_current_slot} -> {chosen['date']} #{chosen['slot']}. "
                    f"Freed slot at {a_current_date} #{a_current_slot}.",
                    parse_mode="",
                )
            except TelegramAPIError:
                pass
        _log_event("swap_completed", rid=rid, outcome="vacancy_fill",
                   a_username=a_username,
                   from_date=a_current_date, from_slot=a_current_slot,
                   to_date=chosen["date"], to_slot=chosen["slot"])
        return

    # Counterparty path: propose to B
    b_username = chosen.get("b_username")
    b_user_id = chosen.get("b_user_id")
    if not b_user_id:
        try:
            bot.answer_callback_query(cq_id,
                text=f"@{b_username} hasn't started the bot yet - I can't DM them. Misha will help.")
        except TelegramAPIError:
            pass
        # Fall back to manual
        misha_id = int(os.environ.get("MISHA_TELEGRAM_USER_ID", "0"))
        if misha_id:
            try:
                bot.send_message(
                    misha_id,
                    f"/swap stuck: @{a_username} wants to swap with @{b_username} "
                    f"({chosen['date']} #{chosen['slot']}) but B has no telegram_user_id. "
                    f"Reach out manually.",
                    parse_mode="",
                )
            except TelegramAPIError:
                pass
        _append_swap_event({"rid": rid, "event": "b_unreachable", "b_username": b_username})
        if msg_chat_id and msg_id:
            try:
                bot.edit_message_text(
                    msg_chat_id, msg_id,
                    f"@{b_username} hasn't activated the bot yet. Misha will arrange this one manually.",
                    parse_mode="", reply_markup=None,
                )
            except TelegramAPIError:
                pass
        return

    from datetime import timedelta
    deadline = local_now() + timedelta(hours=SWAP_B_RESPONSE_TTL_HOURS)
    a_label = _format_dm_date(a_current_date, ctx.get("a_day") or "")
    b_label = _format_dm_date(chosen["date"], chosen["day"])
    b_text = (
        f"@{a_username} would like to swap fireside slots with you.\n\n"
        f"They're on {a_label} (slot {a_current_slot}); "
        f"you're on {b_label} (slot {chosen['slot']}).\n\n"
        f"If you accept, you'll move to {a_label} and they'll take your {b_label} slot. "
        f"This request expires in {SWAP_B_RESPONSE_TTL_HOURS}h."
    )
    b_buttons = {"inline_keyboard": [[
        {"text": "✅ Accept", "callback_data": f"sw:b:{rid}:y"},
        {"text": "❌ Decline", "callback_data": f"sw:b:{rid}:n"},
    ]]}
    try:
        sent_b = bot.send_message(int(b_user_id), b_text, parse_mode="", reply_markup=b_buttons)
        b_msg_id = sent_b.get("message_id") if isinstance(sent_b, dict) else None
    except TelegramAPIError as e:
        log_error(f"swap rid={rid}: failed to DM B (@{b_username}): {e}")
        try:
            bot.answer_callback_query(cq_id, text="Could not reach the other speaker. Misha will help.")
        except TelegramAPIError:
            pass
        return

    _append_swap_event({
        "rid": rid, "event": "a_tapped_counterparty", "chosen_idx": idx,
        "target_date": chosen["date"], "target_slot": chosen["slot"],
        "b_username": b_username, "b_user_id": int(b_user_id),
    })
    _append_swap_event({
        "rid": rid, "event": "proposed_to_b",
        "b_user_id": int(b_user_id), "b_message_id": b_msg_id,
        "deadline": deadline.isoformat(),
    })

    try:
        bot.answer_callback_query(cq_id, text="Sent to the other speaker.")
        if msg_chat_id and msg_id:
            bot.edit_message_text(
                msg_chat_id, msg_id,
                f"Request sent to @{b_username}. They have {SWAP_B_RESPONSE_TTL_HOURS}h "
                f"to accept or decline. I'll let you know.",
                parse_mode="", reply_markup=None,
            )
    except TelegramAPIError:
        pass


def _handle_b_tap(bot: TelegramBot, cq_id: str, rid: str, choice: str,
                  ctx: dict, status: str, msg_chat_id, msg_id,
                  tapper_user_id: int) -> None:
    """Process B's accept/decline tap. choice is 'y' or 'n'."""
    expected_b = ctx.get("b_user_id")
    if expected_b is None or int(expected_b) != tapper_user_id:
        try:
            bot.answer_callback_query(cq_id, text="This button is for someone else.")
        except TelegramAPIError:
            pass
        return

    if status != "proposed_to_b":
        try:
            bot.answer_callback_query(cq_id, text="This request is already closed.")
            if msg_chat_id and msg_id:
                bot.edit_message_reply_markup(msg_chat_id, msg_id, None)
        except TelegramAPIError:
            pass
        return

    a_username = ctx.get("a_username", "")
    a_user_id = ctx.get("a_user_id")
    a_current_date = ctx.get("a_current_date")
    a_current_slot = ctx.get("a_current_slot")
    b_username = ctx.get("b_username", "")
    target_date = ctx.get("target_date")
    target_slot = ctx.get("target_slot")

    if choice == "n":
        _append_swap_event({"rid": rid, "event": "b_declined"})
        try:
            bot.answer_callback_query(cq_id, text="Declined.")
            if msg_chat_id and msg_id:
                bot.edit_message_text(
                    msg_chat_id, msg_id,
                    "You declined the swap. No changes made.",
                    parse_mode="", reply_markup=None,
                )
        except TelegramAPIError:
            pass
        # Notify A and Misha
        if a_user_id:
            try:
                bot.send_message(
                    int(a_user_id),
                    f"@{b_username} declined the swap. Your slot stays at "
                    f"{a_current_date} #{a_current_slot}. Reply /swap to try another date, "
                    f"or message Misha for help.",
                    parse_mode="",
                )
            except TelegramAPIError:
                pass
        misha_id = int(os.environ.get("MISHA_TELEGRAM_USER_ID", "0"))
        if misha_id:
            try:
                bot.send_message(
                    misha_id,
                    f"/swap declined: @{b_username} said no to @{a_username} "
                    f"({a_current_date} <-> {target_date}).",
                    parse_mode="",
                )
            except TelegramAPIError:
                pass
        return

    if choice != "y":
        try:
            bot.answer_callback_query(cq_id, text="Bad choice.")
        except TelegramAPIError:
            pass
        return

    # Accepted - apply bilateral swap atomically
    ok = _apply_bilateral_swap(
        a_username=a_username,
        a_current_date=a_current_date,
        a_current_slot=a_current_slot,
        b_username=b_username,
        b_date=target_date,
        b_slot=target_slot,
    )
    if not ok:
        try:
            bot.answer_callback_query(cq_id,
                text="Could not apply swap (entries changed since request). Misha will help.")
        except TelegramAPIError:
            pass
        _append_swap_event({"rid": rid, "event": "apply_failed"})
        return

    _append_swap_event({
        "rid": rid, "event": "b_accepted",
    })
    _append_swap_event({
        "rid": rid, "event": "completed", "outcome": "bilateral_swap",
    })
    a_label = _format_dm_date(a_current_date, "")
    b_label = _format_dm_date(target_date, "")
    try:
        bot.answer_callback_query(cq_id, text="Accepted. Swap applied.")
        if msg_chat_id and msg_id:
            bot.edit_message_text(
                msg_chat_id, msg_id,
                f"Accepted. You're now on {a_label}, slot {a_current_slot}. "
                f"@{a_username} takes {b_label}, slot {target_slot}.",
                parse_mode="", reply_markup=None,
            )
    except TelegramAPIError:
        pass
    if a_user_id:
        try:
            bot.send_message(
                int(a_user_id),
                f"@{b_username} accepted. You're now on {b_label}, slot {target_slot}. "
                f"Your old slot ({a_label}, slot {a_current_slot}) is now theirs.",
                parse_mode="",
            )
        except TelegramAPIError:
            pass
    misha_id = int(os.environ.get("MISHA_TELEGRAM_USER_ID", "0"))
    if misha_id:
        try:
            bot.send_message(
                misha_id,
                f"/swap done (bilateral): @{a_username} <-> @{b_username}. "
                f"@{a_username}: {a_current_date} #{a_current_slot} -> {target_date} #{target_slot}. "
                f"@{b_username}: {target_date} #{target_slot} -> {a_current_date} #{a_current_slot}.",
                parse_mode="",
            )
        except TelegramAPIError:
            pass
    _log_event("swap_completed", rid=rid, outcome="bilateral_swap",
               a_username=a_username, b_username=b_username,
               from_date=a_current_date, from_slot=a_current_slot,
               to_date=target_date, to_slot=target_slot)


def _handle_message(bot: TelegramBot, message: dict) -> None:
    """Handle private DMs to the bot. Routes /start, /swap, and query commands.

    Authorization gate: only users whose Telegram user_id maps to an active,
    non-excluded entry in tribe-roster.json may interact. Outsiders get a
    generic 'private bot' reply, are logged to sessions.jsonl, and forwarded
    to Misha at most once per hour per user_id.
    """
    chat = message.get("chat", {})
    if chat.get("type") != "private":
        return  # ignore group messages
    text = (message.get("text") or "").strip()
    user = message.get("from", {})
    user_id = user.get("id")
    username = (user.get("username") or "").lower()

    # /start: greet members already bound to the roster by the trusted
    # `bootstrap` (Telethon enumeration of the real group). We deliberately do
    # NOT bind telegram_user_id from the self-reported @username here -- a handle
    # is reclaimable, so trusting it would allow handle takeover. Unbound or
    # unknown senders get the private-bot reply and are forwarded to Misha, who
    # re-runs `bootstrap` to enroll them.
    if text.startswith("/start"):
        if _is_authorized_user(user_id, username=username):
            bot.send_message(user_id, WELCOME_DM, parse_mode="")
            _log_event("start_received", user_id=user_id, username=username,
                       matched_in_roster=True)
            return
        # Unbound or outsider /start - unauthorized path
        _log_event("unauthorized_start", user_id=user_id, username=username)
        _maybe_forward_outsider(bot, user_id, username, text)
        bot.send_message(user_id, UNAUTHORIZED_REPLY, parse_mode="")
        return

    # All other commands: gate by user_id in active+non-excluded roster
    if not _is_authorized_user(user_id, username=username):
        _log_event("unauthorized_dm", user_id=user_id, username=username,
                   text_preview=text[:200])
        _maybe_forward_outsider(bot, user_id, username, text)
        bot.send_message(user_id, UNAUTHORIZED_REPLY, parse_mode="")
        return

    # User is authorized - existing command dispatch
    if text.startswith("/help"):
        bot.send_message(user_id, HELP_DM, parse_mode="")
        return

    if ft._is_idea_command(text):
        body = ft.parse_idea_command(text)
        if body is None:
            bot.send_message(
                user_id,
                "Send your idea after the command, e.g.\n`/idea a real DPI incident, start to finish`",
            )
            return
        schedule = load_state(SCHEDULE) or []
        roster = load_state(TRIBE_ROSTER) or {}
        name = (roster.get(_resolve_my_username(user_id) or "", {}) or {}).get("name", "")
        ft.append_idea(
            STATE_DIR,
            now_iso=local_now().isoformat(),
            user_id=user_id, username=username, name=name,
            text=body, cycle=ft.current_cycle(schedule, _today_local_date()),
        )
        _log_event("idea_submitted", user_id=user_id, username=username,
                   text_preview=body[:120])
        bot.send_message(
            user_id,
            "Logged ✓ — thank you. Your idea goes into the pool we draw the next fireside topics from.",
        )
        return

    if text.startswith("/me"):
        bot.send_message(user_id, _cmd_me_text(user_id), parse_mode="")
        return

    if text.startswith("/next"):
        bot.send_message(user_id, _cmd_next_text(), parse_mode="")
        return

    if text.startswith("/who"):
        bot.send_message(user_id, _cmd_who_text(), parse_mode="")
        return

    if text.startswith("/theme"):
        bot.send_message(user_id, _cmd_theme_text(), parse_mode="")
        return

    if text.startswith("/schedule"):
        bot.send_message(user_id, _cmd_schedule_text(), parse_mode="")
        return

    if text.startswith("/zoom"):
        bot.send_message(user_id, _cmd_zoom_text(), parse_mode="")
        return

    if text.startswith("/swap"):
        # Identity by user_id, not the self-reported @username: the schedule is
        # keyed by roster username, so resolving from user_id stops a caller who
        # has claimed a former member's handle from swapping that member's slot.
        my_username = _resolve_my_username(user_id)
        if not my_username:
            bot.send_message(
                user_id,
                "Couldn't find you in the Tribe roster, so I can't set up a swap. "
                "Reply here and Misha will sort it out.",
                parse_mode="",
            )
            return
        _swap_kickoff_for_a(bot, user_id, my_username)
        return

    # Unrecognised message from authorized user - forward to Misha so the Tribe member feels heard
    misha_id = int(os.environ.get("MISHA_TELEGRAM_USER_ID", "0"))
    if misha_id and text:
        try:
            preview = text[:300] + ("..." if len(text) > 300 else "")
            bot.send_message(
                misha_id,
                f"DM to bot from @{username} (id={user_id}):\n{preview}",
                parse_mode="",
            )
        except TelegramAPIError:
            pass
    bot.send_message(
        user_id,
        "Got it - Misha will see this. Type /help for the command menu.",
        parse_mode="",
    )


def _handle_message_reaction(event: dict) -> None:
    """Update opt-ins.json when a Tribe member adds or removes 🧭/🌟 on the launch announcement."""
    msg_id = event.get("message_id")
    expected_msg = int(os.environ.get("FIRESIDE_LAUNCH_ANNOUNCEMENT_MSG_ID", "0"))
    if msg_id != expected_msg:
        return  # only track reactions on the launch announcement

    user = event.get("user", {})
    user_id = user.get("id")
    username = (user.get("username") or "").lower() if user else ""
    if not user_id:
        return

    # Identity by user_id: only a bound, authorized Tribe member may opt in, and
    # we store the canonical roster-key username (not the self-reported handle),
    # so an outsider reacting cannot pollute opt-ins and a reclaimed handle never
    # makes a stored opt-in stale. Removal is keyed by user_id, so a member who
    # later becomes unbound can still remove their own opt-in.
    my_username = _resolve_my_username(user_id)

    new_reactions = event.get("new_reaction", []) or []
    emojis = {r.get("emoji") for r in new_reactions if r.get("type") == "emoji"}

    opt_ins = load_state(OPT_INS) or {"helmsman": [], "wildcard": []}
    changed = False
    for emoji, key in [("🧭", "helmsman"), ("🌟", "wildcard")]:
        existing = next((x for x in opt_ins[key] if x.get("user_id") == user_id), None)
        if emoji in emojis and not existing:
            if my_username is None:
                continue  # not an authorized/bound member -- ignore the opt-in
            opt_ins[key].append({"user_id": user_id, "username": my_username})
            changed = True
        elif emoji not in emojis and existing:
            opt_ins[key] = [x for x in opt_ins[key] if x.get("user_id") != user_id]
            changed = True
    if changed:
        save_state(OPT_INS, opt_ins)
        _log_event("opt_in_changed", user_id=user_id,
                   username=my_username or username, reactions=list(emojis))


def _handle_chat_member(event: dict) -> None:
    """Track joiners and leavers in the 31C Tribe group."""
    expected_chat = int(os.environ.get("FIRESIDE_TRIBE_CHAT_ID", "0"))
    chat_id = event.get("chat", {}).get("id")
    if chat_id != expected_chat:
        return

    new_member = event.get("new_chat_member", {})
    old_member = event.get("old_chat_member", {})
    user = new_member.get("user", {})
    user_id = user.get("id")
    username = (user.get("username") or "").lower()
    new_status = new_member.get("status", "")
    old_status = old_member.get("status", "")

    if new_status in ("member", "administrator") and old_status in ("left", "kicked", ""):
        _log_event("tribe_join", user_id=user_id, username=username)
        misha_id = int(os.environ.get("MISHA_TELEGRAM_USER_ID", "0"))
        if misha_id:
            try:
                bot = get_bot()
                bot.send_message(misha_id,
                    f"📥 New 31C Tribe member: @{username or '<no username>'} "
                    f"(id={user_id}). Add to xlsx with their function/title for the next bootstrap.")
            except TelegramAPIError:
                pass
    elif new_status in ("left", "kicked") and old_status in ("member", "administrator"):
        _log_event("tribe_leave", user_id=user_id, username=username)
        # Mark inactive in roster
        roster = load_state(TRIBE_ROSTER) or {}
        for k, v in list(roster.items()):
            if v.get("telegram_user_id") == user_id:
                v["active"] = False
                save_state(TRIBE_ROSTER, roster)
                break


# ============================================================
# Subcommand: speaker-dms (Phase 3 task 3.2)
# ============================================================

def cmd_speaker_dms(args) -> None:
    """Send 2-week and 3-day speaker reminders. Cron: daily 09:00 local time."""
    from datetime import date as _date, timedelta

    bot = get_bot()
    schedule = load_state(SCHEDULE) or []
    roster = load_state(TRIBE_ROSTER) or {}
    today = _today_local_date()
    dm_log_path = state_path(DM_LOG)

    sent_2wk = 0
    sent_3day = 0
    skipped = 0
    failed = 0

    for entry in schedule:
        username = entry.get("speaker_username")
        if not username:
            continue
        session_date = entry["session_date"]
        d = _date.fromisoformat(session_date)
        days_until = (d - today).days
        user_id = _resolve_speaker_user_id(roster, username)
        name = entry["speaker_name"].split()[0]  # first name
        session_day = d.strftime("%a")
        theme = entry["theme"]

        for window, dm_type, template in [
            ((10, 14), "2wk", SPEAKER_DM_2WK),
            ((1, 3), "3day", SPEAKER_DM_3DAY),
        ]:
            if not (window[0] <= days_until <= window[1]):
                continue
            if _dm_already_sent(dm_log_path, username, dm_type, session_date):
                skipped += 1
                continue
            if user_id is None:
                _log_dm(dm_type, username, session_date, None, False,
                        error="no telegram_user_id (user has not /started bot)")
                failed += 1
                continue
            text = template.format(
                name=name, session_date=session_date, session_day=session_day, theme=theme,
            )
            try:
                bot.send_dm(user_id, text)
                _log_dm(dm_type, username, session_date, user_id, True)
                if dm_type == "2wk":
                    sent_2wk += 1
                else:
                    sent_3day += 1
            except TelegramAPIError as e:
                _log_dm(dm_type, username, session_date, user_id, False, error=str(e))
                failed += 1

    print(f"{GREEN}speaker-dms{RESET}: 2wk={sent_2wk} 3day={sent_3day} skipped={skipped} failed={failed}")
    hc_ping("FIRESIDE_HC_SPEAKER_DMS")


# ============================================================
# Subcommand: sunday-preview (Phase 3 task 3.3)
# ============================================================

def cmd_sunday_preview(args) -> None:
    """Post weekly preview to 31C Tribe group + pin. Cron: Sunday 18:00 local time."""
    schedule = load_state(SCHEDULE) or []
    helmsmen = load_state(HELMSMEN) or {}

    today = _today_local_date()
    week_num = _current_or_upcoming_week(schedule, today)
    if week_num is None:
        print(f"{YELLOW}sunday-preview: no upcoming sessions in schedule{RESET}")
        return

    mon = _week_speakers(schedule, week_num, "Mon")
    wed = _week_speakers(schedule, week_num, "Wed")
    if not mon or not wed:
        print(f"{RED}sunday-preview: incomplete week {week_num} in schedule{RESET}", file=sys.stderr)
        return

    week_start = mon[0]["session_date"]
    helmsman_entry = helmsmen.get(week_start, {})
    helmsman_name = helmsman_entry.get("name", "[Helmsman not yet assigned - please pick one]")

    monday_speakers = " · ".join(s["speaker_name"] for s in mon)
    wednesday_speakers = " · ".join(s["speaker_name"] for s in wed)

    text = SUNDAY_PREVIEW.format(
        theme=mon[0]["theme"],
        monday_date=mon[0]["session_date"],
        wednesday_date=wed[0]["session_date"],
        monday_speakers=monday_speakers,
        wednesday_speakers=wednesday_speakers,
        helmsman_name=helmsman_name,
        zoom_link=_zoom_url(),
    )

    if getattr(args, "dry_run", False):
        print(f"{CYAN}--- Sunday preview (DRY RUN, would post to chat_id={os.environ.get('FIRESIDE_TRIBE_CHAT_ID')}) ---{RESET}")
        print(text)
        print(f"{CYAN}--- end ---{RESET}")
        return

    bot = get_bot()
    chat_id = int(os.environ["FIRESIDE_TRIBE_CHAT_ID"])
    try:
        result = bot.send_message(chat_id, text)
        msg_id = result.get("message_id")
        bot.pin_chat_message(chat_id, msg_id, disable_notification=True)
        save_state(LAST_PINNED, {"message_id": msg_id, "week": week_num,
                                 "posted_at": local_now().isoformat()})
        _log_event("sunday_preview_posted", week=week_num, message_id=msg_id)
        print(f"{GREEN}sunday-preview{RESET}: posted week {week_num}, message_id={msg_id}, pinned")
        hc_ping("FIRESIDE_HC_SUNDAY_PREVIEW")
    except TelegramAPIError as e:
        print(f"{RED}sunday-preview failed: {e}{RESET}", file=sys.stderr)


# ============================================================
# Subcommand: topic-nudge (weekly topic-collection invite)
# ============================================================

def cmd_topic_nudge(args) -> None:
    """Post the weekly 'topic box is open' invite to the Tribe group.

    Auto-send (same trust class as sunday-preview). Not pinned. Guards: silent
    no-op outside an active cycle, and silent no-op during the final week (the
    CEO-approved cycle-end invite owns that week). Cron: Saturday 12:00 local.
    """
    schedule = load_state(SCHEDULE) or []
    today = _today_local_date()
    if ft._upcoming_week(schedule, today) is None:
        print(f"{GRAY}topic-nudge: no active cycle; skip{RESET}")
        return
    if ft.is_final_week(schedule, today):
        print(f"{GRAY}topic-nudge: final week owned by cycle-end invite; skip{RESET}")
        return

    text = ft.render_nudge()
    if getattr(args, "dry_run", False):
        print(f"{CYAN}--- topic-nudge (DRY RUN, chat_id={os.environ.get('FIRESIDE_TRIBE_CHAT_ID')}) ---{RESET}")
        print(text)
        print(f"{CYAN}--- end ---{RESET}")
        return

    bot = get_bot()
    chat_id = int(os.environ["FIRESIDE_TRIBE_CHAT_ID"])
    try:
        result = bot.send_message(chat_id, text)
        _log_event("topic_nudge_posted", message_id=result.get("message_id"))
        print(f"{GREEN}topic-nudge{RESET}: posted, message_id={result.get('message_id')}")
    except TelegramAPIError as e:
        print(f"{RED}topic-nudge failed: {e}{RESET}", file=sys.stderr)


# ============================================================
# Subcommand: topic-digest (weekly CEO digest of new ideas)
# ============================================================

def cmd_topic_digest(args) -> None:
    """DM the CEO any topic ideas submitted since the last digest. Cron: Sun 09:00.

    Silent no-op when there are no new ideas. Advances the digest cursor only
    after a successful send so a failed DM is retried next run.
    """
    state = ft.load_topic_state(STATE_DIR)
    cursor = state.get("last_digest_idea_id")
    new, new_cursor = ft.new_ideas_since(STATE_DIR, cursor)
    if not new:
        print(f"{GRAY}topic-digest: no new ideas since last digest{RESET}")
        return

    text = ft.render_digest(new)
    if getattr(args, "dry_run", False):
        print(f"{CYAN}--- topic-digest (DRY RUN, {len(new)} new) ---{RESET}")
        print(text)
        print(f"{CYAN}--- end ---{RESET}")
        return

    misha_id = int(os.environ.get("MISHA_TELEGRAM_USER_ID", "0"))
    if not misha_id:
        print(f"{RED}topic-digest: MISHA_TELEGRAM_USER_ID not set{RESET}", file=sys.stderr)
        return

    bot = get_bot()
    try:
        bot.send_message(misha_id, text, parse_mode="")
    except TelegramAPIError as e:
        print(f"{RED}topic-digest DM failed: {e}{RESET}", file=sys.stderr)
        return
    state["last_digest_idea_id"] = new_cursor
    ft.save_topic_state(STATE_DIR, state)
    _log_event("topic_digest_sent", count=len(new), cursor=new_cursor)
    print(f"{GREEN}topic-digest{RESET}: DMed {len(new)} new idea(s) to CEO")


# ============================================================
# Subcommand: cycle-end-invite (CEO-approved end-of-cycle invite)
# ============================================================

def cmd_cycle_end_invite(args) -> None:
    """On the final-week Sunday, DRAFT the cycle-end topic invite to the CEO.

    Sends the CEO the warm invite + the full backlog summary with inline buttons
    [Send to Tribe] / [Cancel]. Posting to the group happens only on the CEO's
    tap (_handle_callback_query, namespace cycle_invite:*). Daily cron, but
    cycle_end_trigger_today() makes every non-trigger day a no-op. Idempotent:
    a pending draft for the current cycle is not re-drafted.
    """
    schedule = load_state(SCHEDULE) or []
    today = _today_local_date()
    if not ft.cycle_end_trigger_today(schedule, today):
        print(f"{GRAY}cycle-end-invite: not the final-week Sunday; skip{RESET}")
        return

    cycle = ft.current_cycle(schedule, today)
    state = ft.load_topic_state(STATE_DIR)
    pending = state.get("pending_cycle_invite")
    if pending and pending.get("cycle") == cycle:
        print(f"{GRAY}cycle-end-invite: draft already pending for cycle {cycle}; skip{RESET}")
        return

    invite = ft.render_cycle_end_invite()
    backlog = ft.render_backlog_summary(ft.load_ideas(STATE_DIR, cycle=cycle))
    ceo_text = (
        "*Draft — cycle-end topic invite (your approval needed before it posts to the Tribe)*\n\n"
        "————— message preview —————\n"
        f"{invite}\n"
        "———————————————————\n\n"
        f"{backlog}"
    )
    markup = {"inline_keyboard": [[
        {"text": "✅ Send to Tribe", "callback_data": "cycle_invite:send"},
        {"text": "✖️ Cancel", "callback_data": "cycle_invite:cancel"},
    ]]}

    if getattr(args, "dry_run", False):
        print(f"{CYAN}--- cycle-end-invite (DRY RUN, draft to CEO) ---{RESET}")
        print(ceo_text)
        print(f"{CYAN}--- end ---{RESET}")
        return

    misha_id = int(os.environ.get("MISHA_TELEGRAM_USER_ID", "0"))
    if not misha_id:
        print(f"{RED}cycle-end-invite: MISHA_TELEGRAM_USER_ID not set{RESET}", file=sys.stderr)
        return

    bot = get_bot()
    try:
        result = bot.send_message(misha_id, ceo_text, reply_markup=markup, parse_mode="")
    except TelegramAPIError as e:
        print(f"{RED}cycle-end-invite draft DM failed: {e}{RESET}", file=sys.stderr)
        return

    state["pending_cycle_invite"] = {
        "text": invite,                      # the exact text posted on approval
        "approval_msg_id": result.get("message_id"),
        "drafted_at": local_now().isoformat(),
        "cycle": cycle,
    }
    ft.save_topic_state(STATE_DIR, state)
    _log_event("cycle_end_invite_drafted", cycle=cycle,
               approval_msg_id=result.get("message_id"))
    print(f"{GREEN}cycle-end-invite{RESET}: drafted to CEO for approval (cycle {cycle})")


# ============================================================
# Subcommand: topic-ideas (console-first backlog reader)
# ============================================================

def cmd_topic_ideas(args) -> None:
    """Print the topic backlog to the terminal. No Telegram dependency.

    --cycle N  : only that cycle.  --new : only ideas since the last digest.
    """
    cycle = getattr(args, "cycle", None)
    since = None
    if getattr(args, "new", False):
        since = ft.load_topic_state(STATE_DIR).get("last_digest_idea_id")
    ideas = ft.load_ideas(STATE_DIR, cycle=cycle, since_id=since)
    if not ideas:
        print(f"{GRAY}No topic ideas{' since last digest' if since else ''}.{RESET}")
        return
    print(f"{BOLD}{len(ideas)} topic idea(s):{RESET}")
    for n, i in enumerate(ideas, 1):
        who = i.get("name") or i.get("username") or "unknown"
        when = (i.get("ts") or "")[:10]
        print(f"  {n}. {i.get('text','').strip()}")
        print(f"       {GRAY}— {who}, {when}, cycle {i.get('cycle')}{RESET}")


# ============================================================
# Subcommand: dayof-reminders (Phase 3 task 3.4)
# ============================================================

def cmd_dayof_reminders(args) -> None:
    """DM today's 3 speakers their Zoom link. Cron: Mon + Wed 15:30 local (3h before 18:30)."""
    schedule = load_state(SCHEDULE) or []
    helmsmen = load_state(HELMSMEN) or {}
    roster = load_state(TRIBE_ROSTER) or {}
    today_iso = _today_local_date().isoformat()

    bot = get_bot()
    today_entries = [s for s in schedule if s["session_date"] == today_iso]
    if not today_entries:
        print(f"{GRAY}dayof-reminders: no sessions today ({today_iso}){RESET}")
        return

    week_num = today_entries[0]["week"]
    mon_entry = next((s for s in schedule if s["week"] == week_num and s["day"] == "Mon"), None)
    week_start = mon_entry["session_date"] if mon_entry else today_iso
    helmsman_name = (helmsmen.get(week_start, {})).get("name", "[Helmsman TBD]")
    zoom = _zoom_url()

    sent = 0
    failed = 0
    for entry in sorted(today_entries, key=lambda s: s["slot"]):
        username = entry.get("speaker_username")
        if not username:
            continue
        user_id = _resolve_speaker_user_id(roster, username)
        if user_id is None:
            _log_dm("dayof", username, today_iso, None, False,
                    error="no telegram_user_id")
            failed += 1
            continue
        name = entry["speaker_name"].split()[0]
        text = SPEAKER_DM_DAYOF.format(name=name, zoom_link=zoom, helmsman_name=helmsman_name)
        try:
            bot.send_dm(user_id, text)
            _log_dm("dayof", username, today_iso, user_id, True)
            sent += 1
        except TelegramAPIError as e:
            _log_dm("dayof", username, today_iso, user_id, False, error=str(e))
            failed += 1
    print(f"{GREEN}dayof-reminders{RESET}: sent={sent} failed={failed}")
    hc_ping("FIRESIDE_HC_DAYOF_REMINDERS")


# ============================================================
# Subcommand: helmsman-brief (Phase 3 task 3.5)
# ============================================================

def cmd_helmsman_brief(args) -> None:
    """Brief the closest unbrief'd Helmsman whose week starts within 7 days. Cron: daily 10:00 local time.

    The earlier tight `today + 7 days == key` rule meant any missed run (bot down, scheduler
    failure) silently skipped the brief forever. Window-based + idempotent via `briefed` flag
    means a missed day catches up on the next run.
    """
    from datetime import date as _date, timedelta

    schedule = load_state(SCHEDULE) or []
    helmsmen = load_state(HELMSMEN) or {}
    opt_ins = load_state(OPT_INS) or {"helmsman": [], "wildcard": []}
    roster = load_state(TRIBE_ROSTER) or {}
    today = _today_local_date()
    horizon = today + timedelta(days=7)

    candidates = []
    for key, entry in helmsmen.items():
        if entry.get("briefed"):
            continue
        try:
            key_date = _date.fromisoformat(key)
        except ValueError:
            continue
        if today < key_date <= horizon:
            candidates.append((key_date, key, entry))

    if not candidates:
        print(f"{GRAY}helmsman-brief: no pending Helmsman briefs within 7 days{RESET}")
        hc_ping("FIRESIDE_HC_HELMSMAN_BRIEF")
        return

    candidates.sort(key=lambda t: t[0])
    target_date, target_week_start, helmsman_entry = candidates[0]

    user_id = helmsman_entry.get("user_id")
    if not user_id:
        # Resolve via roster username
        username = helmsman_entry.get("username")
        user_id = _resolve_speaker_user_id(roster, username) if username else None

    if not user_id:
        print(f"{RED}helmsman-brief: no user_id for Helmsman {helmsman_entry}{RESET}", file=sys.stderr)
        return

    week_num = next((s["week"] for s in schedule if s["session_date"] == target_week_start), None)
    if week_num is None:
        print(f"{RED}helmsman-brief: no schedule for week starting {target_week_start}{RESET}", file=sys.stderr)
        return

    mon = _week_speakers(schedule, week_num, "Mon")
    wed = _week_speakers(schedule, week_num, "Wed")
    monday_speakers = ", ".join(s["speaker_name"] for s in mon)
    wednesday_speakers = ", ".join(s["speaker_name"] for s in wed)
    wildcard_lines = "\n".join(
        f"  - @{w['username']}" for w in opt_ins.get("wildcard", [])
    ) or "  (none yet - opt-ins are still open in the 31C Tribe group)"

    name = helmsman_entry.get("name", "Helmsman").split()[0]
    text = HELMSMAN_BRIEF.format(
        name=name,
        week_starting=target_week_start,
        monday_date=mon[0]["session_date"],
        wednesday_date=wed[0]["session_date"],
        monday_speakers=monday_speakers,
        wednesday_speakers=wednesday_speakers,
        theme=mon[0]["theme"],
        wildcard_list=wildcard_lines,
    )

    bot = get_bot()
    try:
        bot.send_dm(user_id, text)
        helmsman_entry["briefed"] = True
        helmsman_entry["briefed_at"] = local_now().isoformat()
        save_state(HELMSMEN, helmsmen)
        _log_event("helmsman_briefed", week=week_num, user_id=user_id)
        print(f"{GREEN}helmsman-brief{RESET}: sent to {name} (user_id={user_id}) for week {week_num}")
        hc_ping("FIRESIDE_HC_HELMSMAN_BRIEF")
    except TelegramAPIError as e:
        print(f"{RED}helmsman-brief failed: {e}{RESET}", file=sys.stderr)


# ============================================================
# Subcommand: weekly-discrepancy-report (Phase 3 task 3.6)
# ============================================================

def cmd_weekly_discrepancy_report(args) -> None:
    """Re-run cross-reference; DM Misha if discrepancies found. Cron: Sunday 17:00 local time."""
    import asyncio
    try:
        bootstrap_result = asyncio.run(_bootstrap_async())
    except Exception as e:
        print(f"{RED}weekly-discrepancy-report: Telethon failed: {e}{RESET}", file=sys.stderr)
        log_error("weekly-discrepancy-report Telethon failed", e)
        return

    try:
        xlsx_roster = load_tribe_metadata()
    except (FileNotFoundError, ValueError) as e:
        print(f"{RED}weekly-discrepancy-report: xlsx load failed: {e}{RESET}", file=sys.stderr)
        return

    _, discrepancy = cross_reference(xlsx_roster, bootstrap_result["telegram_members"])
    in_tg = discrepancy["in_telegram_not_in_xlsx"]
    in_xlsx = discrepancy["in_xlsx_not_in_telegram"]
    no_un = discrepancy["no_username_in_telegram"]

    if not in_tg and not in_xlsx and not no_un:
        print(f"{GREEN}weekly-discrepancy-report: no discrepancies{RESET}")
        return

    lines = ["**Weekly Tribe roster discrepancy report**", ""]
    if in_tg:
        lines.append(f"In Telegram, missing from xlsx ({len(in_tg)}):")
        for r in in_tg:
            lines.append(f"  - @{r['username']}: {r['full_name']}")
        lines.append("")
    if in_xlsx:
        lines.append(f"In xlsx, missing from Telegram ({len(in_xlsx)}):")
        for r in in_xlsx:
            lines.append(f"  - @{r['username']}: {r['name']}")
        lines.append("")
    if no_un:
        lines.append(f"In Telegram, no username set ({len(no_un)}):")
        for r in no_un:
            lines.append(f"  - {r['full_name']} (id={r['user_id']})")
    text = "\n".join(lines)

    misha_id = int(os.environ.get("MISHA_TELEGRAM_USER_ID", "0"))
    if not misha_id:
        print(text)
        return
    bot = get_bot()
    try:
        bot.send_message(misha_id, text, parse_mode="Markdown")
        print(f"{GREEN}weekly-discrepancy-report{RESET}: DM sent to Misha")
    except TelegramAPIError as e:
        print(f"{RED}weekly-discrepancy-report DM failed: {e}{RESET}", file=sys.stderr)


# ============================================================
# Subcommand: email-backup (Phase 3 task 3.7)
# ============================================================

def cmd_email_backup(args) -> None:
    """Email speakers who haven't responded to bot DMs. Cron: Sunday 19:00 local time."""
    import subprocess
    from datetime import date as _date, timedelta

    schedule = load_state(SCHEDULE) or []
    roster = load_state(TRIBE_ROSTER) or {}
    today = _today_local_date()
    dm_log_path = state_path(DM_LOG)

    # Read entire DM log once into a per-user response set
    responded_user_ids: set[int] = set()
    if dm_log_path.exists():
        with open(dm_log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("event_type") in ("start_received", "swap_requested"):
                    if e.get("user_id"):
                        responded_user_ids.add(int(e["user_id"]))

    sent = 0
    skipped = 0
    for entry in schedule:
        username = entry.get("speaker_username")
        if not username:
            continue
        d = _date.fromisoformat(entry["session_date"])
        days_until = (d - today).days
        if not (1 <= days_until <= 14):
            continue  # only current 2-week window
        roster_entry = roster.get(username)
        if not roster_entry:
            continue
        user_id = roster_entry.get("telegram_user_id")
        email = roster_entry.get("email")
        if not email:
            continue
        if user_id and user_id in responded_user_ids:
            continue  # they've engaged via bot, no need for email
        # Only email if the bot has tried to DM them at least once and either failed or got no response
        try_send = False
        with open(dm_log_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (e.get("speaker_username") == username
                        and e.get("session_date") == entry["session_date"]
                        and e.get("dm_type") in ("2wk", "3day")
                        and not e.get("delivered")):
                    try_send = True
                    break
        if not try_send:
            skipped += 1
            continue

        name = roster_entry["name"].split()[0]
        subject = EMAIL_BACKUP_SUBJECT.format(session_date=entry["session_date"])
        body_text = EMAIL_BACKUP_BODY.format(
            name=name,
            session_date=entry["session_date"],
            session_day=d.strftime("%A"),
            theme=entry["theme"],
        )
        body_html = "<p>" + body_text.replace("\n\n", "</p><p>").replace("\n", "<br/>") + "</p>"

        cmd = [
            "python", "scripts/send-email.py",
            "--to", email,
            "--subject", subject,
            "--body", body_html,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                               cwd=str(WORKSPACE_ROOT))
            ok = (r.returncode == 0)
        except Exception as e:
            log_error(f"email-backup subprocess failed for {email}", e)
            ok = False
        _log_dm("email-backup", username, entry["session_date"], user_id, ok,
                error=None if ok else r.stderr[:200] if hasattr(r, 'stderr') else None)
        if ok:
            sent += 1
    print(f"{GREEN}email-backup{RESET}: sent={sent} skipped={skipped}")


# ============================================================
# Subcommand: stats (Phase 3 task 3.8)
# ============================================================

def cmd_stats(args) -> None:
    """Generate markdown stats report from dm-log + sessions logs. On-demand."""
    schedule = load_state(SCHEDULE) or []
    roster = load_state(TRIBE_ROSTER) or {}
    opt_ins = load_state(OPT_INS) or {"helmsman": [], "wildcard": []}
    helmsmen = load_state(HELMSMEN) or {}
    today = _today_local_date()

    spoken_users: set[str] = set()
    no_show_count: dict[str, int] = {}
    swap_count = 0

    sessions_path = state_path(SESSIONS_LOG)
    if sessions_path.exists():
        with open(sessions_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line.strip())
                except (json.JSONDecodeError, ValueError):
                    continue
                t = e.get("event_type")
                if t == "session_logged":
                    for u in (e.get("shared") or "").split(","):
                        if u.strip():
                            spoken_users.add(u.strip())
                    for u in (e.get("no_shows") or "").split(","):
                        if u.strip():
                            no_show_count[u.strip()] = no_show_count.get(u.strip(), 0) + 1
                elif t == "swap_requested":
                    swap_count += 1

    delivered = total = 0
    dm_log_path = state_path(DM_LOG)
    if dm_log_path.exists():
        with open(dm_log_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line.strip())
                except (json.JSONDecodeError, ValueError):
                    continue
                if e.get("dm_type") in ("2wk", "3day", "dayof", "helmsman_brief", "email-backup"):
                    total += 1
                    if e.get("delivered"):
                        delivered += 1

    completed = sum(1 for s in schedule if s.get("completed"))
    sessions_total = len(set((s["session_date"]) for s in schedule))
    completed_sessions = len(set(s["session_date"] for s in schedule if s.get("completed")))
    current_week = _current_or_upcoming_week(schedule, today) or 1

    # Tribe rotation health
    all_speakers = sorted({s["speaker_name"] for s in schedule})
    spoken_names = sorted(spoken_users)
    unspoken = [n for n in all_speakers if n not in spoken_users]

    lines = [
        f"# Tribe Fireside Stats — {today.isoformat()}",
        "",
        f"## Cycle progress",
        f"- Current week: **{current_week}** of 9",
        f"- Sessions completed: **{completed_sessions}** of {sessions_total}",
        f"- Speaker entries completed: **{completed}** of {len(schedule)}",
        "",
        f"## Speaker rotation",
        f"- Spoken so far ({len(spoken_names)}): {', '.join(spoken_names) or '(none yet)'}",
        f"- Not spoken yet ({len(unspoken)}): {', '.join(unspoken[:10])}{'...' if len(unspoken) > 10 else ''}",
        "",
        f"## No-show counts",
    ]
    if no_show_count:
        for name, count in sorted(no_show_count.items(), key=lambda x: -x[1]):
            lines.append(f"- {name}: {count}")
    else:
        lines.append("- No no-shows recorded")
    lines.extend([
        "",
        f"## Swap requests",
        f"- Total: **{swap_count}**",
        "",
        f"## Opt-in rosters",
        f"- 🧭 Helmsmen: **{len(opt_ins['helmsman'])}** opted in",
        f"- 🌟 Wildcards: **{len(opt_ins['wildcard'])}** opted in",
        "",
        f"## DM delivery",
        f"- Delivered: {delivered} / {total} ({(100*delivered/total) if total else 0:.0f}%)",
        "",
        f"## Roster health",
        f"- Active members in roster: **{sum(1 for r in roster.values() if r.get('active', True))}**",
        f"- Members with telegram_user_id (have /started bot or pre-populated): "
        f"**{sum(1 for r in roster.values() if r.get('telegram_user_id'))}**",
        f"- Members without telegram_user_id (won't receive DMs until they /start): "
        f"**{sum(1 for r in roster.values() if not r.get('telegram_user_id'))}**",
        "",
        f"## Helmsman schedule",
    ])
    if helmsmen:
        for week_start, h in sorted(helmsmen.items()):
            briefed = "✓ briefed" if h.get("briefed") else "pending brief"
            lines.append(f"- {week_start}: {h.get('name', '?')} ({briefed})")
    else:
        lines.append("- No Helmsmen assigned yet")

    report = "\n".join(lines) + "\n"
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = STATS_DIR / f"{today.isoformat()}_stats.md"
    out_path.write_text(report, encoding="utf-8")

    print(f"{GREEN}stats{RESET}: written to {out_path}")
    if getattr(args, "show", False):
        print()
        print(report)


# ============================================================
# Subcommand: health-check (Phase 3 task 3.9)
# ============================================================

def cmd_health_check(args) -> None:
    """Alert Misha if no liveness tick (poll-tick or heartbeat-tick) in 30 min. Cron: every 30 min."""
    from datetime import timedelta

    dm_log_path = state_path(DM_LOG)
    if not dm_log_path.exists():
        print(f"{YELLOW}health-check: dm-log.jsonl missing{RESET}")
        return

    last_tick_ts = None
    for line in dm_log_path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        # Either tick type counts as proof of life. poll-tick comes from
        # cmd_poll (polling mode); heartbeat-tick from cmd_heartbeat (webhook
        # mode). Looking at the latest of either is enough.
        if e.get("dm_type") in ("poll-tick", "heartbeat-tick"):
            last_tick_ts = e.get("ts")

    now = local_now()
    if last_tick_ts is None:
        msg = "⚠️ Fireside Bot health-check: no liveness tick ever recorded. Bot may not be running."
    else:
        last_dt = datetime.fromisoformat(last_tick_ts)
        age = now - last_dt
        if age > timedelta(minutes=30):
            mins = int(age.total_seconds() // 60)
            msg = (f"⚠️ Fireside Bot health-check: last liveness tick was {mins} min ago "
                   f"(threshold 30 min). Check daemon: "
                   f"`systemctl status <fireside-unit>` on the service host.")
        else:
            print(f"{GREEN}health-check{RESET}: last tick {int(age.total_seconds())}s ago, healthy")
            return

    misha_id = int(os.environ.get("MISHA_TELEGRAM_USER_ID", "0"))
    if not misha_id:
        print(msg)
        return
    bot = get_bot()
    try:
        bot.send_message(misha_id, msg)
        print(f"{YELLOW}health-check{RESET}: alert DM sent to Misha")
    except TelegramAPIError as e:
        print(f"{RED}health-check alert failed: {e}{RESET}", file=sys.stderr)


# ============================================================
# Subcommand: unpin-weekly (Phase 3 task 3.10)
# ============================================================

def cmd_unpin_weekly(args) -> None:
    """Unpin the Sunday preview message. Cron: Wed 16:00 local (after Wed session)."""
    last = load_state(LAST_PINNED) or {}
    msg_id = last.get("message_id")
    if not msg_id:
        print(f"{GRAY}unpin-weekly: no pinned message recorded; nothing to unpin{RESET}")
        return
    bot = get_bot()
    chat_id = int(os.environ["FIRESIDE_TRIBE_CHAT_ID"])
    try:
        bot.unpin_chat_message(chat_id, msg_id)
        save_state(LAST_PINNED, {"message_id": None})
        print(f"{GREEN}unpin-weekly{RESET}: unpinned message_id={msg_id}")
    except TelegramAPIError as e:
        print(f"{YELLOW}unpin-weekly: {e}{RESET}", file=sys.stderr)


# ============================================================
# Subcommand: log-session (Phase 3 task 3.11)
# ============================================================

def cmd_log_session(args) -> None:
    """Manually log a session result. Run after each Mon/Wed session.

    CLI: python scripts/fireside-bot.py log-session --date 2026-05-12 \
                                                    --shared misha,junaid,sabina \
                                                    --no-shows ""
    """
    if not args.date:
        print(f"{RED}log-session: --date YYYY-MM-DD required{RESET}", file=sys.stderr)
        sys.exit(1)
    if not args.shared:
        print(f"{RED}log-session: --shared required (comma-separated speaker names){RESET}", file=sys.stderr)
        sys.exit(1)

    schedule = load_state(SCHEDULE) or []
    shared_names = [s.strip() for s in args.shared.split(",") if s.strip()]
    no_show_names = [s.strip() for s in (args.no_shows or "").split(",") if s.strip()]

    # Mark schedule entries as completed
    updated = 0
    for entry in schedule:
        if entry["session_date"] != args.date:
            continue
        if entry["speaker_name"] in no_show_names:
            entry["no_show"] = True
            entry["completed"] = True
            updated += 1
        elif entry["speaker_name"] in shared_names:
            entry["completed"] = True
            updated += 1
    save_state(SCHEDULE, schedule)

    _log_event(
        "session_logged",
        date=args.date,
        shared=args.shared,
        no_shows=args.no_shows or "",
        swaps=getattr(args, "swaps", "") or "",
    )
    print(f"{GREEN}log-session{RESET}: {args.date} - shared={len(shared_names)}, "
          f"no-shows={len(no_show_names)}, schedule entries updated={updated}")


# ============================================================
# Phase 2/3 subcommand stubs (anything not yet wired)
# ============================================================

def _not_implemented(name: str, phase: str):
    def stub(args):
        print(f"{YELLOW}Subcommand '{name}' not implemented yet ({phase}){RESET}",
              file=sys.stderr)
        sys.exit(2)
    return stub


# ============================================================
# Webhook subcommands (Phase 4 — real-time delivery via setWebhook)
# ============================================================

def cmd_heartbeat(args) -> None:
    """Daemon liveness signal in webhook mode.

    Polls no longer run when FIRESIDE_WEBHOOK_ENABLED=true, so the per-poll
    side effects vanish: nothing pings FIRESIDE_HC_POLL (healthchecks.io flags
    DOWN), and nothing appends poll-tick to dm-log (cmd_health_check DMs Misha
    about stale polls). This heartbeat reinstates both signals from a 1-min
    cron job. Cheap: one HTTP ping + one JSONL append.
    """
    hc_ping("FIRESIDE_HC_POLL")
    append_jsonl(DM_LOG, {
        "ts": local_now().isoformat(),
        "dm_type": "heartbeat-tick",
    })


def cmd_set_webhook(args) -> None:
    """Register the bot's webhook URL with Telegram and upload the self-signed cert.

    Reads FIRESIDE_WEBHOOK_PUBLIC_URL, FIRESIDE_WEBHOOK_SECRET, FIRESIDE_WEBHOOK_CERT
    from .env. Telegram will POST every future update to PUBLIC_URL with the
    SECRET in the X-Telegram-Bot-Api-Secret-Token header.
    """
    bot = get_bot()
    url = os.environ.get("FIRESIDE_WEBHOOK_PUBLIC_URL")
    secret = os.environ.get("FIRESIDE_WEBHOOK_SECRET")
    cert_path = os.environ.get("FIRESIDE_WEBHOOK_CERT")
    if not (url and secret and cert_path):
        print(f"{RED}Missing one of FIRESIDE_WEBHOOK_PUBLIC_URL / SECRET / CERT in .env{RESET}",
              file=sys.stderr)
        sys.exit(1)
    if not Path(cert_path).exists():
        print(f"{RED}Cert file not found: {cert_path}{RESET}", file=sys.stderr)
        sys.exit(1)

    api_url = f"{TELEGRAM_API_BASE}/bot{bot.token}/setWebhook"
    data = {
        "url": url,
        "secret_token": secret,
        "allowed_updates": json.dumps([
            "message", "message_reaction", "message_reaction_count",
            "chat_member", "my_chat_member", "callback_query",
        ]),
        "drop_pending_updates": "false",
    }
    with open(cert_path, "rb") as f:
        files = {"certificate": (Path(cert_path).name, f, "application/x-pem-file")}
        r = requests.post(api_url, data=data, files=files, timeout=30)

    result = r.json()
    if not result.get("ok"):
        print(f"{RED}setWebhook failed: {result.get('description')}{RESET}", file=sys.stderr)
        sys.exit(1)
    print(f"{GREEN}OK{RESET}  webhook set to {url}")
    print(f"     description: {result.get('description', '')}")


def cmd_delete_webhook(args) -> None:
    """Clear the bot's webhook. Polling becomes possible again immediately."""
    bot = get_bot()
    result = bot._call("deleteWebhook", drop_pending_updates=False)
    print(f"{GREEN}OK{RESET}  webhook deleted (result={result})")


def cmd_webhook_info(args) -> None:
    """Print the current webhook registration as Telegram sees it."""
    bot = get_bot()
    info = bot._call("getWebhookInfo")
    print(json.dumps(info, indent=2, ensure_ascii=False))


# ============================================================
# Main
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="fireside-bot",
        description="Tribe Fireside Bot - coordinates Mon + Wed firesides via Telegram.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="See docs/superpowers/specs/2026-05-03-tribe-fireside-bot-design.md for details.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<subcommand>")

    # Phase 1 - implemented
    sub.add_parser("test-telegram", help="Smoke test: send DM to Misha (Phase 1 DoD)")
    sub.add_parser("xlsx-check", help="Print xlsx loader summary (Phase 1 helper)")
    sub.add_parser("init-state", help="Initialise state directory + files (Phase 1 helper)")

    # Phase 2+ - stubs registered so --help shows full surface
    sub.add_parser("bootstrap", help="One-time: enumerate Telegram group + build initial roster (Phase 2)")
    sub.add_parser("poll", help="Process Telegram updates, every 5 min (Phase 3)")
    sub.add_parser("speaker-dms", help="Send 2-week + 3-day speaker reminders (Phase 3)")
    sunday_preview = sub.add_parser("sunday-preview", help="Post pinned weekly preview (Phase 3)")
    sunday_preview.add_argument("--dry-run", action="store_true",
                                help="Print rendered preview without posting to group")
    sub.add_parser("dayof-reminders", help="DM speakers Zoom link 3h before session (Phase 3)")
    sub.add_parser("helmsman-brief", help="Brief next week's Helmsman 7 days ahead (Phase 3)")
    sub.add_parser("weekly-discrepancy-report", help="Report Telegram-vs-xlsx mismatches (Phase 3)")
    sub.add_parser("email-backup", help="Email reminder for unresponsive Tribe (Phase 3)")
    stats = sub.add_parser("stats", help="Generate stats markdown report (Phase 3)")
    stats.add_argument("--show", action="store_true", help="Print the report after writing")
    sub.add_parser("health-check", help="Alert if poll hasn't run in 30 min (Phase 3)")
    sub.add_parser("unpin-weekly", help="Unpin Sunday preview after Wed session (Phase 3)")
    topic_nudge = sub.add_parser("topic-nudge", help="Post weekly topic-collection invite to Tribe")
    topic_nudge.add_argument("--dry-run", action="store_true", help="Print without posting")
    topic_digest = sub.add_parser("topic-digest", help="DM CEO new topic ideas since last digest")
    topic_digest.add_argument("--dry-run", action="store_true", help="Print without sending")
    cycle_end = sub.add_parser("cycle-end-invite", help="Draft end-of-cycle topic invite to CEO for approval")
    cycle_end.add_argument("--dry-run", action="store_true", help="Print draft without DMing CEO")
    topic_ideas = sub.add_parser("topic-ideas", help="List the topic backlog (terminal)")
    topic_ideas.add_argument("--cycle", type=int, default=None, help="Filter to one cycle")
    topic_ideas.add_argument("--new", action="store_true", help="Only ideas since last digest")

    log_session = sub.add_parser("log-session", help="Log session result, manual (Phase 3)")
    log_session.add_argument("--date", help="Session date YYYY-MM-DD")
    log_session.add_argument("--shared", help="Comma-separated speakers who shared")
    log_session.add_argument("--no-shows", default="", help="Comma-separated speakers who no-showed")
    log_session.add_argument("--swaps", default="", help="Comma-separated swap notes")

    # Phase 4 - webhook subcommands
    sub.add_parser("set-webhook", help="Register webhook URL with Telegram (Phase 4)")
    sub.add_parser("delete-webhook", help="Clear webhook so polling can resume (Phase 4)")
    sub.add_parser("webhook-info", help="Show current Telegram webhook registration (Phase 4)")
    sub.add_parser("heartbeat", help="Ping FIRESIDE_HC_POLL — alive signal in webhook mode (Phase 4)")

    args = parser.parse_args()

    handlers = {
        "test-telegram": cmd_test_telegram,
        "xlsx-check": cmd_xlsx_check,
        "init-state": cmd_init_state,
        "bootstrap": cmd_bootstrap,
        "poll": cmd_poll,
        "speaker-dms": cmd_speaker_dms,
        "sunday-preview": cmd_sunday_preview,
        "dayof-reminders": cmd_dayof_reminders,
        "helmsman-brief": cmd_helmsman_brief,
        "weekly-discrepancy-report": cmd_weekly_discrepancy_report,
        "email-backup": cmd_email_backup,
        "stats": cmd_stats,
        "health-check": cmd_health_check,
        "unpin-weekly": cmd_unpin_weekly,
        "topic-nudge": cmd_topic_nudge,
        "topic-digest": cmd_topic_digest,
        "cycle-end-invite": cmd_cycle_end_invite,
        "topic-ideas": cmd_topic_ideas,
        "log-session": cmd_log_session,
        "set-webhook": cmd_set_webhook,
        "delete-webhook": cmd_delete_webhook,
        "webhook-info": cmd_webhook_info,
        "heartbeat": cmd_heartbeat,
    }
    handler = handlers.get(args.cmd)
    if handler is None:
        parser.error(f"Unknown subcommand: {args.cmd}")
    handler(args)
    return 0


if __name__ == "__main__":
    # Wrap main() so uncaught exceptions land in errors.log even when the script
    # is launched via pythonw (no console, stderr discarded). Cron-fired runs
    # would otherwise fail silently.
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as _exc:
        try:
            log_error(f"uncaught exception in main()", _exc)
        except BaseException:  # noqa: S110 - last-resort handler; log_error itself failed and we are already exiting non-zero, so there is nothing safe left to do.
            pass
        sys.exit(1)
