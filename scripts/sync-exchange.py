#!/usr/bin/env python3
"""
Exchange Sync for 31C CEO Workspace

Pulls calendar events and emails from on-premises Microsoft Exchange
via EWS (Exchange Web Services) and saves them as readable markdown
files in the datastore.

Prerequisites:
    pip install exchangelib python-dotenv

Setup:
    1. Copy .env.example to .env in the workspace root
    2. Fill in your Exchange credentials
    3. Run: python scripts/sync-exchange.py

Usage:
    python scripts/sync-exchange.py                  # sync both calendar and emails
    python scripts/sync-exchange.py --calendar       # sync calendar only
    python scripts/sync-exchange.py --emails         # sync emails only
    python scripts/sync-exchange.py --days 7         # calendar: next N days (default: 7)
    python scripts/sync-exchange.py --email-count 50 # emails: last N messages (default: 30)
    python scripts/sync-exchange.py --unread         # emails: unread only
    python scripts/sync-exchange.py --folder Inbox   # emails: specific folder (default: Inbox)
    python scripts/sync-exchange.py --delete "subject text"  # delete emails matching subject
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from scripts.utils.venv import ensure_venv  # noqa: E402

ensure_venv()
# --- Dependency check ---
def check_dependencies():
    missing = []
    try:
        import exchangelib
    except ImportError:
        missing.append("exchangelib")
    if missing:
        print(f"[ERROR] Missing packages: {', '.join(missing)}")
        print(f"        Run: pip install {' '.join(missing)}")
        sys.exit(1)

check_dependencies()

from exchangelib import (
    Account, CalendarItem, Configuration, Credentials, DELEGATE, IMPERSONATION,
    EWSDateTime, EWSTimeZone, Build, Version
)
from exchangelib.protocol import BaseProtocol

from scripts.utils.html import strip_html  # noqa: E402
from scripts.utils.workspace import get_data_root, get_default_tz, get_default_tz_name, get_outputs_dir, get_workspace_root, load_env  # noqa: E402

# ============================================================
# Configuration
# ============================================================

# --- Constants ---
WORKSPACE_ROOT = get_workspace_root()
ENV_FILE = WORKSPACE_ROOT / ".env"
CALENDAR_DIR = get_outputs_dir() / "_sync" / "calendar"
EMAIL_DIR = get_outputs_dir() / "_sync" / "emails"


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

    print(f"[OK] Connected as {config['EXCHANGE_EMAIL']}")
    return account


# ============================================================
# Calendar Sync
# ============================================================

def sync_calendar(account, days=7, timezone_str=get_default_tz_name()):
    """Pull calendar events and save as markdown."""
    CALENDAR_DIR.mkdir(parents=True, exist_ok=True)

    tz = EWSTimeZone.from_timezone(
        ZoneInfo(timezone_str)
    )

    now = datetime.now()
    start = EWSDateTime(now.year, now.month, now.day, 0, 0, 0, tzinfo=tz)
    end = start + timedelta(days=days)

    print(f"[INFO] Fetching calendar events: {start.date()} to {end.date()}...")

    events = account.calendar.view(start=start, end=end)
    event_list = sorted(events, key=lambda e: e.start)

    if not event_list:
        print("[INFO] No calendar events found in this range.")

    # Group by date (in local timezone)
    local_tz = ZoneInfo(timezone_str)
    by_date = {}
    for event in event_list:
        try:
            date_key = event.start.astimezone(local_tz).date()
        except Exception:
            date_key = event.start.date() if hasattr(event.start, 'date') else str(event.start)[:10]
        date_str = str(date_key)
        if date_str not in by_date:
            by_date[date_str] = []
        by_date[date_str].append(event)

    # Write combined file
    output_file = CALENDAR_DIR / "upcoming.md"
    lines = []
    lines.append(f"# Calendar - Next {days} Days")
    lines.append(f"")
    lines.append(f"> Synced: {datetime.now().strftime('%Y-%m-%d %H:%M')} ({timezone_str})")
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
            try:
                local_start = event.start.astimezone(local_tz)
                time_str = local_start.strftime("%H:%M")
            except Exception:
                time_str = str(event.start)[11:16] if len(str(event.start)) > 10 else "All day"
            subject = (event.subject or "(No subject)").replace("|", "-")
            location = (event.location or "-").replace("|", "-") if event.location else "-"

            if event.start and event.end:
                try:
                    duration_mins = int((event.end - event.start).total_seconds() / 60)
                    if duration_mins >= 60:
                        duration = f"{duration_mins // 60}h{duration_mins % 60:02d}m"
                    else:
                        duration = f"{duration_mins}m"
                except Exception:
                    duration = "-"
            else:
                duration = "-"

            lines.append(f"| {time_str} | {subject} | {location} | {duration} |")

        lines.append("")

        # Detail section for events with body/attendees
        for event in day_events:
            has_details = (event.body and str(event.body).strip()) or event.required_attendees or event.optional_attendees
            if has_details:
                lines.append(f"### {str(event.start)[11:16]} - {event.subject or '(No subject)'}")
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

    output_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] Calendar: {total} events saved to {output_file.relative_to(get_data_root())}")

    # Also write per-day files
    for date_str, day_events in by_date.items():
        day_file = CALENDAR_DIR / f"{date_str}.md"
        day_lines = [f"# Calendar - {date_str}", "", f"> Synced: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
        day_lines.append("| Time | Subject | Location |")
        day_lines.append("|------|---------|----------|")
        for event in day_events:
            try:
                local_start = event.start.astimezone(local_tz)
                time_str = local_start.strftime("%H:%M")
            except Exception:
                time_str = str(event.start)[11:16] if len(str(event.start)) > 10 else "All day"
            subject = (event.subject or "(No subject)").replace("|", "-")
            location = (event.location or "-").replace("|", "-") if event.location else "-"
            day_lines.append(f"| {time_str} | {subject} | {location} |")
        day_file.write_text("\n".join(day_lines), encoding="utf-8")

    return total


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
    lines.append(f"> Synced: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
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

    output_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] Emails: {len(email_list)} saved to {output_file.relative_to(get_data_root())}")

    return len(email_list)


# ============================================================
# Email Deletion
# ============================================================

def delete_emails(account, subject_query, folder_name="Inbox", confirm=True):
    """Delete emails matching a subject query."""
    if folder_name.lower() == "inbox":
        folder = account.inbox
    elif folder_name.lower() == "sent":
        folder = account.sent
    elif folder_name.lower() == "drafts":
        folder = account.drafts
    else:
        folder = account.inbox / folder_name

    print(f"[INFO] Searching {folder_name} for emails matching: \"{subject_query}\"...")

    matches = list(
        folder.filter(subject__icontains=subject_query)
        .order_by("-datetime_received")[:50]
    )

    if not matches:
        print(f"[INFO] No emails found matching \"{subject_query}\".")
        return 0

    print(f"[INFO] Found {len(matches)} matching email(s):\n")
    for i, email in enumerate(matches, 1):
        date_str = str(email.datetime_received)[:16] if email.datetime_received else "-"
        sender = str(email.sender.email_address) if email.sender else "-"
        print(f"  {i}. [{date_str}] From: {sender} — {email.subject}")

    if confirm:
        print()
        answer = input(f"Delete {'this email' if len(matches) == 1 else f'all {len(matches)} emails'}? (y/N): ").strip().lower()
        if answer != "y":
            print("[INFO] Cancelled. No emails deleted.")
            return 0

    for email in matches:
        email.delete()

    print(f"[OK] Deleted {len(matches)} email(s).")
    return len(matches)


# ============================================================
# Meeting Creation
# ============================================================

def create_meeting(account, subject, start_time, duration_minutes=30, location=None, body=None, attendees=None, timezone_str=get_default_tz_name()):
    """Create a calendar meeting."""
    from exchangelib import Mailbox, Attendee

    tz = EWSTimeZone.from_timezone(
        ZoneInfo(timezone_str)
    )

    # Parse start_time: "HH:MM" (today) or "YYYY-MM-DD HH:MM"
    if len(start_time) <= 5:
        now = datetime.now()
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

    item.save()
    print(f"[OK] Meeting created: '{subject}'")
    print(f"     Time: {start} - {end} ({duration_minutes}m)")
    if location:
        print(f"     Location: {location}")
    if attendees:
        print(f"     Attendees: {', '.join(attendees)}")


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

    # Email deletion
    parser.add_argument("--delete", type=str, metavar="SUBJECT", help="Delete emails matching subject (case-insensitive)")
    parser.add_argument("--yes", action="store_true", help="Skip delete confirmation prompt")

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
        return

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
                timezone_str=config["EXCHANGE_TIMEZONE"],
            )
        except Exception as e:
            print(f"[ERROR] Failed to create meeting: {e}")
        return

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
    print("Sync complete.")
    for k, v in results.items():
        status = f"{v} items" if v >= 0 else "FAILED"
        print(f"  {k}: {status}")
    print("=" * 50)


if __name__ == "__main__":
    main()
