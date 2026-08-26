#!/usr/bin/env python3
"""
Exchange Sync for 31C CEO Workspace

Pulls calendar events and emails from on-premises Microsoft Exchange
via EWS (Exchange Web Services) and saves them as readable markdown
files in the datastore.

Prerequisites:
    uv sync --extra email   (exchangelib; `require("exchangelib", extra="email")`
    is what the code itself enforces, so a bare `pip install` diverges from the
    pinned set)

Setup:
    1. Copy .env.example to .env in the workspace root
    2. Fill in your Exchange credentials
    3. Run: python scripts/sync-exchange.py

Usage:
    python scripts/sync-exchange.py --help           # the authoritative flag list

    python scripts/sync-exchange.py                  # sync both calendar and emails
    python scripts/sync-exchange.py --calendar       # sync calendar only
    python scripts/sync-exchange.py --emails         # sync emails only
    python scripts/sync-exchange.py --days 7         # calendar: next N days (default: 7)
    python scripts/sync-exchange.py --email-count 50 # emails: last N messages (default: 30)
    python scripts/sync-exchange.py --unread         # emails: unread only
    python scripts/sync-exchange.py --folder Inbox   # emails: specific folder (default: Inbox)
    python scripts/sync-exchange.py --create-meeting "Subject" --time "14:30" \
        --duration 30 --location Room --attendees a@x.example  # HOLD, no invite
    python scripts/sync-exchange.py --create-meeting "Subject" --time "14:30" \
        --attendees a@x.example --send-invites       # actually emails the invite
    python scripts/sync-exchange.py --delete "subject text"       # asks first
    python scripts/sync-exchange.py --delete "subject text" --yes # no prompt

This docstring is hand-maintained and has drifted before; `--help` is generated
from argparse and cannot.
"""

import argparse
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from scripts.utils.venv_guard import ensure_venv  # noqa: E402
from scripts.utils.atomic import atomic_write_text  # noqa: E402

ensure_venv()
# exchangelib names are bound lazily (F-2.1: import stays pure). The daemon
# always calls connect() before any sync work, so binding there covers every
# downstream constructor.
Account = CalendarItem = Configuration = Credentials = DELEGATE = None
EWSDateTime = EWSTimeZone = None


def _ensure_exchangelib():
    global Account, CalendarItem, Configuration, Credentials, DELEGATE
    global EWSDateTime, EWSTimeZone
    if Account is not None:
        return
    from scripts.utils.optdeps import require
    require("exchangelib", extra="email")
    from exchangelib import (
        Account, CalendarItem, Configuration, Credentials, DELEGATE,
        EWSDateTime, EWSTimeZone,
    )


from scripts.utils.html_text import strip_html  # noqa: E402
from scripts.utils.workspace import get_data_root, get_default_tz, get_default_tz_name, get_outputs_dir, get_personal_root, get_workspace_root, load_env  # noqa: E402

# ============================================================
# Configuration
# ============================================================

# --- Constants ---
WORKSPACE_ROOT = get_workspace_root()
ENV_FILE = WORKSPACE_ROOT / ".env"
CALENDAR_DIR = get_outputs_dir() / "_sync" / "calendar"
EMAIL_DIR = get_outputs_dir() / "_sync" / "emails"


def _display_path(path):
    """A short path for a log line, and never an exception.

    These directories come from `get_outputs_dir()`, which on an exec workspace
    resolves under `../.heading-os-data-{slug}` while `get_data_root()` resolves
    under `../.heading-os-data`. Those are different trees, so the old
    `relative_to(get_data_root())` raised ValueError right after upcoming.md was
    written: the per-day files were never written, the stale-file prune never
    ran, and a sync that had SUCCEEDED was reported as FAILED with exit 1.

    A cosmetic path in a success message must not be able to abort the caller,
    so an unrelatable path degrades to its absolute form.
    """
    for base in (get_personal_root(), get_data_root(), get_workspace_root()):
        try:
            return str(path.relative_to(base))
        except ValueError:
            continue
    return str(path)


def load_config():
    """Load Exchange credentials from .env file."""
    if not ENV_FILE.exists():
        print(f"[ERROR] .env file not found at: {ENV_FILE}")
        print(f"        Copy .env.example to .env and fill in your Exchange credentials.")
        sys.exit(1)

    load_env(WORKSPACE_ROOT)

    required = ["EXCHANGE_EMAIL", "EXCHANGE_PASSWORD", "EXCHANGE_SERVER"]
    config = {}
    for key in required:
        val = os.getenv(key)
        if not val:
            print(f"[ERROR] Missing {key} in .env file")
            sys.exit(1)
        config[key] = val

    # Optional settings
    config["EXCHANGE_USERNAME"] = os.getenv("EXCHANGE_USERNAME", config["EXCHANGE_EMAIL"])
    config["EXCHANGE_AUTH_TYPE"] = os.getenv("EXCHANGE_AUTH_TYPE", "NTLM")
    config["EXCHANGE_TIMEZONE"] = os.getenv("EXCHANGE_TIMEZONE", get_default_tz_name())

    return config


# ============================================================
# Exchange Connection
# ============================================================

def connect(config):
    """Connect to Exchange server via EWS."""
    _ensure_exchangelib()
    print(f"[INFO] Connecting to {config['EXCHANGE_SERVER']}...")

    credentials = Credentials(
        username=config["EXCHANGE_USERNAME"],
        password=config["EXCHANGE_PASSWORD"]
    )

    exchange_config = Configuration(
        server=config["EXCHANGE_SERVER"],
        credentials=credentials,
    )

    account = Account(
        primary_smtp_address=config["EXCHANGE_EMAIL"],
        config=exchange_config,
        autodiscover=False,
        access_type=DELEGATE,
    )

    # NOT "Connected". `Account(...)` with `autodiscover=False` does no network
    # I/O -- exchangelib is lazy and the first real request is what reaches the
    # server. The old wording claimed a connection the constructor never made,
    # so a dead server printed "[OK] Connected", then two [ERROR] lanes. Say
    # what the call established, per `.claude/rules/scope-claims.md`.
    print(f"[OK] Account configured for {config['EXCHANGE_EMAIL']} (not yet contacted)")
    return account


# ============================================================
# Calendar Sync
# ============================================================

_LOCALISE_WARNED = set()


def _warn_once(message):
    """Print a degradation warning the first time it happens, then stay quiet.

    A calendar range holds many items and a bad one is usually a whole class of
    bad ones, so warning per item would bury the sync output. Warning zero times
    is what the four blanket `except Exception` handlers below used to do.
    """
    if message in _LOCALISE_WARNED:
        return
    _LOCALISE_WARNED.add(message)
    print(f"[WARN] {message}")


def _to_local(value, local_tz):
    """Return `value` converted to local time, or None when it cannot be.

    All-day events arrive as an `EWSDate`, which has no `astimezone`, and a
    malformed item can arrive with `start=None`. Both used to hit a blanket
    `except Exception` that then guessed by slicing `str(value)`, so a real
    timezone fault was indistinguishable from an all-day event and reported
    nothing at all.
    """
    try:
        return value.astimezone(local_tz)
    except (AttributeError, TypeError, ValueError) as exc:
        _warn_once(f"could not convert {type(value).__name__} start to local time: {exc}")
        return None


def _event_time_str(value, local_tz):
    """`HH:MM` in local time, or a best-effort label for a non-datetime start."""
    local = _to_local(value, local_tz)
    if local is not None:
        return local.strftime("%H:%M")
    text = str(value)
    return text[11:16] if len(text) > 10 else "All day"


def sync_calendar(account, days=7, timezone_str=None):
    """Pull calendar events and save as markdown.

    `timezone_str=None` resolves per call. A `get_default_tz_name()` DEFAULT is
    evaluated once, at function-definition time, so importing this module ran
    env resolution as a side effect and any later config change was ignored for
    the life of the process.
    """
    # The lazy names this function reads are module globals bound by
    # `_ensure_exchangelib`, and nothing here bound them. It worked only because
    # `main()` calls `connect()` first; a caller that reaches this function any
    # other way got `AttributeError: 'NoneType' object has no attribute
    # 'from_timezone'` instead of the clean "install the email extra" refusal
    # the lazy-import contract promises. Reproduced 2026-08-26, from a test that
    # passed or failed depending on which xdist worker ran it.
    _ensure_exchangelib()

    if timezone_str is None:
        timezone_str = get_default_tz_name()
    CALENDAR_DIR.mkdir(parents=True, exist_ok=True)

    local_tz = ZoneInfo(timezone_str)
    tz = EWSTimeZone.from_timezone(local_tz)

    # "Today" in the zone the window is expressed in. It used to be read from
    # `get_default_tz()` and then stamped with the Exchange zone, so whenever an
    # operator set EXCHANGE_TIMEZONE apart from HEADING_OS_TZ the window could
    # start on the wrong calendar day.
    now = datetime.now(local_tz)
    start = EWSDateTime(now.year, now.month, now.day, 0, 0, 0, tzinfo=tz)
    end = start + timedelta(days=days)

    print(f"[INFO] Fetching calendar events: {start.date()} to {end.date()}...")

    events = account.calendar.view(start=start, end=end)
    event_list = sorted(events, key=lambda e: e.start)

    if not event_list:
        print("[INFO] No calendar events found in this range.")

    # Group by date (in local timezone)
    by_date = {}
    for event in event_list:
        local = _to_local(event.start, local_tz)
        if local is not None:
            date_key = local.date()
        else:
            date_key = event.start.date() if hasattr(event.start, "date") else str(event.start)[:10]
        date_str = str(date_key)
        if date_str not in by_date:
            by_date[date_str] = []
        by_date[date_str].append(event)

    # Write combined file
    output_file = CALENDAR_DIR / "upcoming.md"
    lines = []
    lines.append(f"# Calendar - Next {days} Days")
    lines.append(f"")
    # The clock comes from the zone the label names. It used to be read from
    # `get_default_tz()` and labelled `timezone_str`, so a workspace with the
    # two set apart wrote a timestamp that asserted the zone it was not in.
    lines.append(f"> Synced: {datetime.now(local_tz).strftime('%Y-%m-%d %H:%M')} ({timezone_str})")
    lines.append(f"> Range: {start.date()} to {end.date()}")
    lines.append("")

    total = 0
    for date_str in sorted(by_date.keys()):
        day_events = by_date[date_str]
        lines.append(f"## {date_str}")
        lines.append("")
        lines.append("| Time | Subject | Location | Duration |")
        lines.append("|------|---------|----------|----------|")

        for event in day_events:
            total += 1
            time_str = _event_time_str(event.start, local_tz)
            subject = (event.subject or "(No subject)").replace("|", "-")
            location = (event.location or "-").replace("|", "-") if event.location else "-"

            if event.start and event.end:
                try:
                    duration_mins = int((event.end - event.start).total_seconds() / 60)
                    if duration_mins >= 60:
                        duration = f"{duration_mins // 60}h{duration_mins % 60:02d}m"
                    else:
                        duration = f"{duration_mins}m"
                except (AttributeError, TypeError, ValueError) as exc:
                    _warn_once(f"could not measure event duration: {exc}")
                    duration = "-"
            else:
                duration = "-"

            lines.append(f"| {time_str} | {subject} | {location} | {duration} |")

        lines.append("")

        # Detail section for events with body/attendees
        for event in day_events:
            has_details = (event.body and str(event.body).strip()) or event.required_attendees or event.optional_attendees
            if has_details:
                # `_event_time_str`, like the table above. Slicing str(event.start)
                # gave the UTC wall clock, so a 09:00 UTC meeting in Asia/Dubai
                # was listed at 13:00 in the table and titled 09:00 in the
                # detail section right below - the section carrying the agenda
                # and the attendees.
                detail_time = _event_time_str(event.start, local_tz)
                lines.append(f"### {detail_time} - {event.subject or '(No subject)'}")
                lines.append("")

                if event.required_attendees:
                    attendees = [a.mailbox.email_address for a in event.required_attendees if a.mailbox]
                    if attendees:
                        lines.append(f"**Attendees:** {', '.join(attendees)}")
                        lines.append("")

                if event.optional_attendees:
                    optional = [a.mailbox.email_address for a in event.optional_attendees if a.mailbox]
                    if optional:
                        lines.append(f"**Optional:** {', '.join(optional)}")
                        lines.append("")

                if event.body and str(event.body).strip():
                    body_text = strip_html(event.body)
                    # Truncate very long bodies
                    if len(body_text) > 1000:
                        body_text = body_text[:1000] + "\n\n[...truncated]"
                    lines.append(body_text)
                    lines.append("")

    atomic_write_text(output_file, "\n".join(lines))
    print(f"[OK] Calendar: {total} events saved to {_display_path(output_file)}")

    # Also write per-day files
    written_days = set()
    for date_str, day_events in by_date.items():
        day_file = CALENDAR_DIR / f"{date_str}.md"
        written_days.add(day_file.name)
        day_lines = [f"# Calendar - {date_str}", "",
                     f"> Synced: {datetime.now(local_tz).strftime('%Y-%m-%d %H:%M')} ({timezone_str})", ""]
        day_lines.append("| Time | Subject | Location |")
        day_lines.append("|------|---------|----------|")
        for event in day_events:
            time_str = _event_time_str(event.start, local_tz)
            subject = (event.subject or "(No subject)").replace("|", "-")
            location = (event.location or "-").replace("|", "-") if event.location else "-"
            day_lines.append(f"| {time_str} | {subject} | {location} |")
        atomic_write_text(day_file, "\n".join(day_lines))

    # Prune per-day files that fell out of the window. The loop above only ever
    # CREATES, so a day that emptied (meeting cancelled) or slid out of range
    # kept its old file on disk forever, and any reader globbing this directory
    # served last month's meetings as current.
    _prune_stale_day_files(written_days, start.date(), end.date())

    return total


DAY_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


def _prune_stale_day_files(written_days, window_start, window_end):
    """Delete `YYYY-MM-DD.md` files inside the synced window that we did not write.

    Scoped to the window on purpose: a file dated outside the range was written
    by an earlier run with different `--days` and is not this run's to judge.
    `upcoming.md` and any hand-made note are untouched, because the name has to
    match the date pattern exactly.
    """
    for existing in CALENDAR_DIR.glob("*.md"):
        if not DAY_FILE_RE.match(existing.name) or existing.name in written_days:
            continue
        try:
            stamp = date.fromisoformat(existing.stem)
        except ValueError:
            continue
        if not (window_start <= stamp < window_end):
            continue
        try:
            existing.unlink()
            print(f"[INFO] Pruned stale calendar day file: {existing.name}")
        except OSError as exc:
            print(f"[WARN] Could not prune {existing.name}: {exc}")


# ============================================================
# Email Sync
# ============================================================

def sync_emails(account, count=30, unread_only=False, folder_name="Inbox"):
    """Pull emails and save as markdown."""
    EMAIL_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Fetching emails from {folder_name}" +
          (f" (unread only)" if unread_only else f" (last {count})") + "...")

    # Get the folder
    if folder_name.lower() == "inbox":
        folder = account.inbox
    elif folder_name.lower() == "sent":
        folder = account.sent
    elif folder_name.lower() == "drafts":
        folder = account.drafts
    else:
        folder = account.inbox / folder_name

    if unread_only:
        items = folder.filter(is_read=False).order_by("-datetime_received")[:count]
    else:
        items = folder.all().order_by("-datetime_received")[:count]

    email_list = list(items)

    if not email_list:
        print(f"[INFO] No emails found in {folder_name}.")
        return 0

    # Write combined file
    suffix = "unread" if unread_only else "latest"
    output_file = EMAIL_DIR / f"{folder_name.lower()}-{suffix}.md"

    lines = []
    lines.append(f"# {folder_name} - {'Unread' if unread_only else f'Last {count}'}")
    lines.append("")
    lines.append(f"> Synced: {datetime.now(get_default_tz()).strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"> Count: {len(email_list)} emails")
    lines.append("")

    # Summary table
    lines.append("| # | Date | From | Subject | Read |")
    lines.append("|---|------|------|---------|------|")

    for i, email in enumerate(email_list, 1):
        date_str = str(email.datetime_received)[:16] if email.datetime_received else "-"
        sender = str(email.sender.email_address) if email.sender else "-"
        subject = (email.subject or "(No subject)").replace("|", "-")
        read = "Yes" if email.is_read else "**No**"
        lines.append(f"| {i} | {date_str} | {sender} | {subject} | {read} |")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Full email content
    for i, email in enumerate(email_list, 1):
        date_str = str(email.datetime_received)[:16] if email.datetime_received else "-"
        sender = str(email.sender.email_address) if email.sender else "-"
        sender_name = str(email.sender.name) if email.sender and email.sender.name else sender
        subject = email.subject or "(No subject)"

        lines.append(f"## {i}. {subject}")
        lines.append("")
        lines.append(f"**From:** {sender_name} <{sender}>")
        lines.append(f"**Date:** {date_str}")

        if email.to_recipients:
            to_list = [r.email_address for r in email.to_recipients if r.email_address]
            if to_list:
                lines.append(f"**To:** {', '.join(to_list)}")

        if email.cc_recipients:
            cc_list = [r.email_address for r in email.cc_recipients if r.email_address]
            if cc_list:
                lines.append(f"**CC:** {', '.join(cc_list)}")

        read_status = "Read" if email.is_read else "Unread"
        lines.append(f"**Status:** {read_status}")

        if email.has_attachments and email.attachments:
            att_names = [a.name for a in email.attachments if hasattr(a, 'name') and a.name]
            if att_names:
                lines.append(f"**Attachments:** {', '.join(att_names)}")

        lines.append("")

        # Email body - prefer plain text, fall back to HTML-stripped
        if email.text_body and email.text_body.strip():
            body = email.text_body.strip()
        elif email.body and str(email.body).strip():
            body = strip_html(email.body)
        else:
            body = "(No body)"

        # Truncate very long emails
        if len(body) > 3000:
            body = body[:3000] + "\n\n[...truncated - full email too long]"

        lines.append(body)
        lines.append("")
        lines.append("---")
        lines.append("")

        # Auto-bump CRM last_touch on inbound: silently update the matched
        # relationship record. Log entry creation stays under /email-intel
        # approval flow. Strict email match. Silent no-op on no match.
        # Added 2026-05-15 (Phase 1 of CRM action engine).
        try:
            from scripts.utils.crm_autolog import bump_inbound
            sender_addr = (getattr(email.sender, "email_address", None) or "").strip().lower()
            # Self-bump guard: when --folder Sent is used, the sender is the
            # authenticated Exchange user (us). Bumping our own contact on
            # every outbound email is semantically wrong -- the outbound
            # auto-log path (send-email.py) handles those. Skip here.
            # EXCHANGE_EMAIL is loaded into os.environ by load_config() before
            # sync_emails() is ever called, so os.getenv() is safe here.
            self_email = (os.getenv("EXCHANGE_EMAIL", "") or "").strip().lower()
            if sender_addr and sender_addr != self_email:
                bump_inbound(sender_email=sender_addr)
        except Exception as _e:
            # Best-effort: never disrupt the email sync primary work.
            print(f"[WARN] crm_autolog.bump_inbound failed: {_e}", file=sys.stderr)

    atomic_write_text(output_file, "\n".join(lines))
    print(f"[OK] Emails: {len(email_list)} saved to {_display_path(output_file)}")

    return len(email_list)


# ============================================================
# Email Deletion
# ============================================================

DELETE_MATCH_CAP = 50
"""How many matches one `--delete` run will act on.

A bound is right -- an unbounded destructive loop over a broad query is worse --
but the bound used to be an anonymous `[:50]` slice, so a query matching 300
messages printed "Found 50 matching email(s)" and then "Deleted 50 email(s)".
Both lines were true and together they read as "the mailbox is now clean". The
cap is named, counted against the real total, and reported when it bites.
"""


def delete_emails(account, subject_query, folder_name="Inbox", confirm=True):
    """Delete emails matching a subject query.

    Returns the number deleted. Raises ValueError on a blank query, which would
    otherwise match every message in the folder.
    """
    if not subject_query or not subject_query.strip():
        raise ValueError("refusing a blank --delete query: it matches every message in the folder")
    if folder_name.lower() == "inbox":
        folder = account.inbox
    elif folder_name.lower() == "sent":
        folder = account.sent
    elif folder_name.lower() == "drafts":
        folder = account.drafts
    else:
        folder = account.inbox / folder_name

    print(f"[INFO] Searching {folder_name} for emails matching: \"{subject_query}\"...")

    hits = folder.filter(subject__icontains=subject_query).order_by("-datetime_received")
    matches = list(hits[:DELETE_MATCH_CAP])

    if not matches:
        print(f"[INFO] No emails found matching \"{subject_query}\".")
        return 0

    truncated = len(matches) == DELETE_MATCH_CAP
    total = None
    if truncated:
        try:
            total = folder.filter(subject__icontains=subject_query).count()
        except Exception as exc:  # noqa: BLE001 - the count is a courtesy, the cap is not
            print(f"[WARN] Could not count the full match set: {exc}")
        if total is not None and total <= DELETE_MATCH_CAP:
            truncated = False

    print(f"[INFO] Found {len(matches)} matching email(s):\n")
    for i, email in enumerate(matches, 1):
        date_str = str(email.datetime_received)[:16] if email.datetime_received else "-"
        sender = str(email.sender.email_address) if email.sender else "-"
        print(f"  {i}. [{date_str}] From: {sender} — {email.subject}")

    if truncated:
        shown = f"{total} match" if total is not None else "more than this"
        print(
            f"\n[WARN] Capped at {DELETE_MATCH_CAP}; {shown}. "
            f"Only the {DELETE_MATCH_CAP} newest are listed above and only those "
            f"will be deleted. Re-run to take the next batch."
        )

    if confirm:
        print()
        answer = input(f"Delete {'this email' if len(matches) == 1 else f'all {len(matches)} emails'}? (y/N): ").strip().lower()
        if answer != "y":
            print("[INFO] Cancelled. No emails deleted.")
            return 0

    for email in matches:
        email.delete()

    if truncated:
        remaining = f"{total - len(matches)} " if total is not None else ""
        print(f"[OK] Deleted {len(matches)} email(s). {remaining}still match; re-run to continue.")
    else:
        print(f"[OK] Deleted {len(matches)} email(s).")
    return len(matches)


# ============================================================
# Meeting Creation
# ============================================================

def create_meeting(account, subject, start_time, duration_minutes=30, location=None, body=None, attendees=None, send_invites=False, timezone_str=None):
    """Create a calendar meeting.

    When send_invites is True and attendees are present, the meeting invitation
    is emailed to the attendees. Otherwise the item is saved as a private HOLD
    with no invitation sent.
    """
    # Same binding gap as `sync_calendar`: the two `from exchangelib import`
    # lines below are local names, and `CalendarItem`, `EWSDateTime` and
    # `EWSTimeZone` are module globals that only `_ensure_exchangelib` sets.
    _ensure_exchangelib()

    from exchangelib import Mailbox, Attendee
    from exchangelib.items import SEND_ONLY_TO_ALL, SEND_TO_NONE

    if timezone_str is None:
        timezone_str = get_default_tz_name()

    tz = EWSTimeZone.from_timezone(
        ZoneInfo(timezone_str)
    )

    # Parse start_time: "HH:MM" (today) or "YYYY-MM-DD HH:MM"
    if len(start_time) <= 5:
        now = datetime.now(get_default_tz())
        hour, minute = map(int, start_time.split(":"))
        start = EWSDateTime(now.year, now.month, now.day, hour, minute, 0, tzinfo=tz)
    else:
        parts = start_time.split(" ")
        date_parts = list(map(int, parts[0].split("-")))
        time_parts = list(map(int, parts[1].split(":")))
        start = EWSDateTime(date_parts[0], date_parts[1], date_parts[2],
                           time_parts[0], time_parts[1], 0, tzinfo=tz)

    end = start + timedelta(minutes=duration_minutes)

    item = CalendarItem(
        account=account,
        folder=account.calendar,
        subject=subject,
        start=start,
        end=end,
        location=location,
        body=body or "",
    )

    if attendees:
        item.required_attendees = [
            Attendee(mailbox=Mailbox(email_address=email.strip()))
            for email in attendees
        ]

    invite_mode = SEND_ONLY_TO_ALL if (send_invites and attendees) else SEND_TO_NONE
    item.save(send_meeting_invitations=invite_mode)
    print(f"[OK] Meeting created: '{subject}'")
    print(f"     Time: {start} - {end} ({duration_minutes}m)")
    if location:
        print(f"     Location: {location}")
    if attendees:
        sent = "invite sent" if invite_mode == SEND_ONLY_TO_ALL else "HOLD only, no invite sent"
        print(f"     Attendees: {', '.join(attendees)} ({sent})")


# ============================================================
# Main / CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Sync Exchange calendar and emails to workspace")
    parser.add_argument("--calendar", action="store_true", help="Sync calendar only")
    parser.add_argument("--emails", action="store_true", help="Sync emails only")
    parser.add_argument("--days", type=int, default=7, help="Calendar: days ahead (default: 7)")
    parser.add_argument("--email-count", type=int, default=30, help="Emails: how many to fetch (default: 30)")
    parser.add_argument("--unread", action="store_true", help="Emails: unread only")
    parser.add_argument("--folder", type=str, default="Inbox", help="Emails: folder name (default: Inbox)")

    # Meeting creation
    parser.add_argument("--create-meeting", type=str, metavar="SUBJECT", help="Create a calendar meeting")
    parser.add_argument("--time", type=str, help="Meeting start: 'HH:MM' (today) or 'YYYY-MM-DD HH:MM'")
    parser.add_argument("--duration", type=int, default=30, help="Meeting duration in minutes (default: 30)")
    parser.add_argument("--location", type=str, help="Meeting location")
    parser.add_argument("--body", type=str, help="Meeting description")
    parser.add_argument("--attendees", type=str, nargs="*", help="Attendee email addresses")
    parser.add_argument("--send-invites", action="store_true", help="Send the meeting invitation to --attendees (default: HOLD only, no invite sent)")

    # Email deletion
    parser.add_argument("--delete", type=str, metavar="SUBJECT", help=f"Delete emails matching subject (case-insensitive); acts on at most {DELETE_MATCH_CAP} per run, newest first")
    parser.add_argument("--yes", action="store_true", help="Skip the delete confirmation prompt; the per-run cap still applies")

    args = parser.parse_args()

    print("=" * 50)
    print("31C Exchange Sync")
    print("=" * 50)

    config = load_config()
    account = connect(config)

    # Handle email deletion
    if args.delete:
        try:
            delete_emails(
                account,
                subject_query=args.delete,
                folder_name=args.folder,
                confirm=not args.yes,
            )
        except Exception as e:
            print(f"[ERROR] Failed to delete emails: {e}")
            return 1
        return 0

    # Handle meeting creation
    if args.create_meeting:
        if not args.time:
            print("[ERROR] --time is required for --create-meeting (e.g., --time 14:30)")
            sys.exit(1)
        try:
            create_meeting(
                account,
                subject=args.create_meeting,
                start_time=args.time,
                duration_minutes=args.duration,
                location=args.location,
                body=args.body,
                attendees=args.attendees,
                send_invites=args.send_invites,
                timezone_str=config["EXCHANGE_TIMEZONE"],
            )
        except Exception as e:
            print(f"[ERROR] Failed to create meeting: {e}")
            return 1
        return 0

    # If neither specified, sync both
    sync_cal = args.calendar or (not args.calendar and not args.emails)
    sync_mail = args.emails or (not args.calendar and not args.emails)

    results = {}

    if sync_cal:
        try:
            results["calendar"] = sync_calendar(account, days=args.days, timezone_str=config["EXCHANGE_TIMEZONE"])
        except Exception as e:
            print(f"[ERROR] Calendar sync failed: {e}")
            results["calendar"] = -1

    if sync_mail:
        try:
            results["emails"] = sync_emails(account, count=args.email_count, unread_only=args.unread, folder_name=args.folder)
        except Exception as e:
            print(f"[ERROR] Email sync failed: {e}")
            results["emails"] = -1

    print("")
    print("=" * 50)
    failed = [k for k, v in results.items() if v < 0]
    print("Sync complete." if not failed else "Sync FAILED.")
    for k, v in results.items():
        status = f"{v} items" if v >= 0 else "FAILED"
        print(f"  {k}: {status}")
    print("=" * 50)
    # Non-zero when any lane failed. Every failure path here printed [ERROR] and
    # returned normally, so the process exited 0 and cron, systemd, wrappers and
    # the hook ecosystem could not tell "synced 0 items" from "sync failed"
    # without parsing stdout.
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
