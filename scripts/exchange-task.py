#!/usr/bin/env python3
"""
Exchange Task Manager for 31C CEO Workspace

Creates, lists, and completes tasks in the Exchange Tasks folder via EWS.
Tasks created here appear in Outlook desktop, Outlook mobile, and Teams
with Outlook integration — independently of Claude Code being open.

Usage:
    python scripts/exchange-task.py --subject "Follow up" --due 2026-04-29
    python scripts/exchange-task.py --subject "Follow up" --due 2026-04-29 --remind-at "2026-04-29 09:47"
    python scripts/exchange-task.py --subject "Follow up" --due 2026-04-29 --body "Check Meridian Capital thread"
    python scripts/exchange-task.py --list
    python scripts/exchange-task.py --list --status NotStarted
    python scripts/exchange-task.py --complete "Follow up"

Requirements:
    .env must contain: EXCHANGE_EMAIL, EXCHANGE_PASSWORD, EXCHANGE_SERVER
"""

import argparse
import os
import sys
import zoneinfo
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from scripts.utils.venv import ensure_venv  # noqa: E402

ensure_venv()
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import get_default_tz, get_default_tz_name, get_workspace_root, load_env

# ============================================================
# Dependency check
# ============================================================

def _check_deps():
    import importlib
    missing = []
    for pkg in ("exchangelib",):
        try:
            importlib.import_module(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"{RED}[ERROR]{RESET} Missing packages: {', '.join(missing)}")
        print(f"        Run: pip install {' '.join(missing)}")
        sys.exit(1)

_check_deps()

from exchangelib import (
    Account, Configuration, Credentials, DELEGATE,
    EWSDateTime, EWSTimeZone,
)
from exchangelib.items import Task

# ============================================================
# Config & connection
# ============================================================

WORKSPACE_ROOT = get_workspace_root()
DEFAULT_TZ = get_default_tz_name()


def load_config() -> dict:
    if not (WORKSPACE_ROOT / ".env").exists():
        print(f"{RED}[ERROR]{RESET} .env not found at {WORKSPACE_ROOT / '.env'}")
        sys.exit(1)
    load_env(WORKSPACE_ROOT)
    required = ["EXCHANGE_EMAIL", "EXCHANGE_PASSWORD", "EXCHANGE_SERVER"]
    config = {}
    for key in required:
        val = os.getenv(key)
        if not val:
            print(f"{RED}[ERROR]{RESET} Missing {key} in .env")
            sys.exit(1)
        config[key] = val
    config["EXCHANGE_USERNAME"] = os.getenv("EXCHANGE_USERNAME", config["EXCHANGE_EMAIL"])
    config["EXCHANGE_TIMEZONE"] = os.getenv("EXCHANGE_TIMEZONE", DEFAULT_TZ)
    return config


def connect(config: dict) -> Account:
    print(f"{GRAY}[INFO]{RESET} Connecting to {config['EXCHANGE_SERVER']}...")
    credentials = Credentials(
        username=config["EXCHANGE_USERNAME"],
        password=config["EXCHANGE_PASSWORD"],
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
    print(f"{GREEN}[OK]{RESET} Connected as {config['EXCHANGE_EMAIL']}")
    return account

# ============================================================
# Argument parsing
# ============================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Create and manage Exchange Tasks from the CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--create", action="store_true", help="Create a new task (default when --subject is given)")
    mode.add_argument("--list", action="store_true", help="List tasks")
    mode.add_argument("--complete", metavar="SUBJECT", help="Mark a task complete by subject keyword")

    p.add_argument("--subject", help="Task subject/title")
    p.add_argument("--body", help="Task body/notes")
    p.add_argument("--due", metavar="YYYY-MM-DD", help="Due date")
    p.add_argument(
        "--remind-at",
        metavar="YYYY-MM-DD HH:MM",
        help="Reminder date and time (local timezone). Defaults to 09:00 on due date.",
    )
    p.add_argument(
        "--status",
        choices=["NotStarted", "InProgress", "WaitingOnOthers", "Deferred", "Completed"],
        default="NotStarted",
        help="Task status filter for --list, or initial status for --create (default: NotStarted)",
    )
    p.add_argument("--all-statuses", action="store_true", help="List tasks of all statuses (overrides --status filter)")
    return p.parse_args()

# ============================================================
# Create
# ============================================================

def parse_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        print(f"{RED}[ERROR]{RESET} Invalid date '{s}'. Use YYYY-MM-DD format.")
        sys.exit(1)


def parse_remind_at(s: str, tz: EWSTimeZone) -> EWSDateTime:
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError:
        print(f"{RED}[ERROR]{RESET} Invalid --remind-at '{s}'. Use 'YYYY-MM-DD HH:MM' format.")
        sys.exit(1)
    return EWSDateTime(dt.year, dt.month, dt.day, dt.hour, dt.minute, tzinfo=tz)


def create_task(account: Account, args: argparse.Namespace, config: dict) -> None:
    if not args.subject:
        print(f"{RED}[ERROR]{RESET} --subject is required for --create")
        sys.exit(1)
    if not args.due:
        print(f"{RED}[ERROR]{RESET} --due is required for --create")
        sys.exit(1)

    tz = EWSTimeZone.from_timezone(zoneinfo.ZoneInfo(config["EXCHANGE_TIMEZONE"]))
    due = parse_date(args.due)

    if args.remind_at:
        reminder_dt = parse_remind_at(args.remind_at, tz)
    else:
        reminder_dt = EWSDateTime(due.year, due.month, due.day, 9, 0, tzinfo=tz)

    task = Task(
        folder=account.tasks,
        subject=args.subject,
        body=args.body or "",
        due_date=due,
        start_date=due,
        reminder_is_set=True,
        reminder_due_by=reminder_dt,
        status=args.status,
    )
    task.save()

    print(f"{GREEN}[CREATED]{RESET} {BOLD}{args.subject}{RESET}")
    print(f"  Due:      {due.strftime('%A, %d %B %Y')}")
    print(f"  Reminder: {reminder_dt.strftime('%A, %d %B %Y at %H:%M')} ({config['EXCHANGE_TIMEZONE']})")
    if args.body:
        print(f"  Body:     {args.body[:80]}{'...' if len(args.body) > 80 else ''}")

# ============================================================
# List
# ============================================================

def list_tasks(account: Account, args: argparse.Namespace) -> None:
    tasks = account.tasks.all().order_by("due_date")

    if not args.all_statuses:
        tasks = tasks.filter(status=args.status)

    items = list(tasks)
    if not items:
        label = "all statuses" if args.all_statuses else args.status
        print(f"{YELLOW}[INFO]{RESET} No tasks found ({label})")
        return

    label = "all statuses" if args.all_statuses else args.status
    print(f"\n{BOLD}{CYAN}Exchange Tasks ({label}){RESET}\n")
    for t in items:
        due_str = t.due_date.strftime("%Y-%m-%d") if t.due_date else "no due date"
        reminder_str = ""
        if t.reminder_is_set and t.reminder_due_by:
            reminder_str = f"  remind {t.reminder_due_by.strftime('%Y-%m-%d %H:%M')}"
        status_color = YELLOW if t.status != "Completed" else GRAY
        print(f"  {status_color}{t.status:<20}{RESET} {BOLD}{t.subject}{RESET}")
        print(f"  {GRAY}due {due_str}{reminder_str}{RESET}")
        if t.body:
            first_line = str(t.body).strip().splitlines()[0][:100] if t.body else ""
            if first_line:
                print(f"  {GRAY}{first_line}{RESET}")
        print()

# ============================================================
# Complete
# ============================================================

def complete_task(account: Account, keyword: str) -> None:
    tasks = list(account.tasks.filter(subject__icontains=keyword))
    if not tasks:
        print(f"{YELLOW}[INFO]{RESET} No tasks found matching '{keyword}'")
        return
    if len(tasks) > 1:
        print(f"{YELLOW}[WARN]{RESET} Multiple tasks match '{keyword}':")
        for t in tasks:
            print(f"  - {t.subject}")
        print("Be more specific.")
        return

    t = tasks[0]
    t.status = "Completed"
    t.reminder_is_set = False
    t.save()
    print(f"{GREEN}[COMPLETED]{RESET} {t.subject}")

# ============================================================
# Entry point
# ============================================================

def main() -> None:
    args = parse_args()
    config = load_config()
    account = connect(config)

    if args.list:
        list_tasks(account, args)
    elif args.complete:
        complete_task(account, args.complete)
    else:
        create_task(account, args, config)


if __name__ == "__main__":
    main()
