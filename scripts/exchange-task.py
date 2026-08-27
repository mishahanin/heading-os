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


from scripts.utils.venv_guard import ensure_venv  # noqa: E402

ensure_venv()
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import get_default_tz_name, get_workspace_root, load_env

# ============================================================
# Dependency check
# ============================================================

# exchangelib names are bound lazily (F-2.1: import stays pure).
Account = Configuration = Credentials = DELEGATE = None
EWSDateTime = EWSTimeZone = Task = None


def _ensure_exchangelib():
    global Account, Configuration, Credentials, DELEGATE, EWSDateTime, EWSTimeZone, Task
    if Account is not None:
        return
    from scripts.utils.optdeps import require
    require("exchangelib", extra="email")
    from exchangelib import Account, Configuration, Credentials, DELEGATE, EWSDateTime, EWSTimeZone
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
    _ensure_exchangelib()
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
    # NOT required. The group carried `required=True` while the usage block and
    # the --create help both say creation is the default when --subject is
    # given, so the documented primary command failed argument parsing before
    # it reached Exchange. `main()` already falls through to create, so the
    # code always meant this; only the parser disagreed.
    mode = p.add_mutually_exclusive_group(required=False)
    mode.add_argument("--create", action="store_true", help="Create a new task (default when --subject is given)")
    mode.add_argument("--list", action="store_true", help="List tasks")
    mode.add_argument("--complete", metavar="SUBJECT", help="Mark a task complete by subject keyword")

    p.add_argument("--subject", help="Task subject/title")
    p.add_argument("--body", help="Task body/notes")
    p.add_argument("--due", metavar="YYYY-MM-DD", help="Due date")
    p.add_argument(
        "--remind-at",
        metavar="YYYY-MM-DD HH:MM",
        help="Reminder date and time, read in the mailbox timezone "
             "(EXCHANGE_TIMEZONE in .env). Defaults to 09:00 on the due date.",
    )
    p.add_argument(
        "--status",
        choices=["NotStarted", "InProgress", "WaitingOnOthers", "Deferred", "Completed"],
        default="NotStarted",
        help="Task status filter for --list, or initial status for --create (default: NotStarted)",
    )
    p.add_argument("--all-statuses", action="store_true", help="List tasks of all statuses (overrides --status filter)")
    args = p.parse_args()
    # Dropping `required=True` must not let a bare invocation reach Exchange
    # and try to create a task with no subject. Create is the default mode, so
    # the subject is what makes it a create.
    if not args.list and not args.complete and not args.subject:
        p.error("nothing to do: pass --subject to create a task, or --list, or --complete SUBJECT")
    return args

# ============================================================
# Create
# ============================================================

def parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        print(f"{RED}[ERROR]{RESET} Invalid date '{s}'. Use YYYY-MM-DD format.")
        sys.exit(1)


def parse_remind_at(s: str, tz: EWSTimeZone) -> EWSDateTime:
    """Read a wall-clock reminder and stamp it in the EXCHANGE timezone.

    The `.replace(tzinfo=get_default_tz())` that used to sit here was a no-op:
    the return value is rebuilt from the naive Y/M/D/H/M fields with `tz`, so
    the workspace tz was attached and then discarded without converting
    anything. Harmless while EXCHANGE_TIMEZONE and HEADING_OS_TZ agree, and a
    silent hour shift the moment they do not - while `--remind-at --help` said
    "local timezone", which was then the wrong clock.

    The Exchange tz is the right one and always was: the default reminder path
    (09:00 on the due date) already stamps in `tz`, and Outlook shows the
    reminder in the mailbox's timezone. Only the dead line and the help string
    disagreed.
    """
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M")  # noqa: DTZ007 - tz applied below
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

def in_mailbox_zone(dt, tz_name: str):
    """Move an Exchange datetime onto the mailbox's own clock.

    exchangelib returns `due_date` and `reminder_due_by` as UTC-aware
    `DateTimeField` values, so rendering them with `strftime` printed the UTC
    wall clock. The create path in this same file has always done the opposite,
    labelling its confirmation with `config['EXCHANGE_TIMEZONE']`, and
    `parse_remind_at` carries a docstring about the silent hour shift being the
    failure to avoid. So the two commands of one script reported the same
    reminder on two different clocks, and only the create path said which.

    On a +04 mailbox the listing was four hours early, and for anything set
    before 04:00 it showed the PREVIOUS day. The output looked exactly like a
    correct one, which is what made it worth fixing rather than tolerating.
    """
    if dt is None:
        return None
    try:
        zone = zoneinfo.ZoneInfo(tz_name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        # A misconfigured EXCHANGE_TIMEZONE must not take the listing down; the
        # caller still labels whatever zone it asked for, so the operator sees
        # the name that failed rather than a silently different clock.
        return dt
    return dt.astimezone(zone)


def list_tasks(account: Account, args: argparse.Namespace, config: dict) -> None:
    tz_name = config.get("EXCHANGE_TIMEZONE") or "UTC"
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
        due_local = in_mailbox_zone(t.due_date, tz_name)
        due_str = due_local.strftime("%Y-%m-%d") if due_local else "no due date"
        reminder_str = ""
        if t.reminder_is_set and t.reminder_due_by:
            remind_local = in_mailbox_zone(t.reminder_due_by, tz_name)
            reminder_str = (f"  remind {remind_local.strftime('%Y-%m-%d %H:%M')} "
                            f"({tz_name})")
        status_color = YELLOW if t.status != "Completed" else GRAY
        print(f"  {status_color}{t.status:<20}{RESET} {BOLD}{t.subject}{RESET}")
        print(f"  {GRAY}due {due_str}{reminder_str}{RESET}")
        # `[0]` on the split, not on the stripped text. A body of "\n" or " "
        # is TRUTHY and strips to "", whose splitlines() is [] - so one task
        # whose notes field holds only whitespace raised IndexError in the
        # middle of the listing, and the operator got a half-printed list plus
        # a traceback with no way to tell where the list stopped.
        lines = str(t.body).strip().splitlines() if t.body else []
        if lines:
            print(f"  {GRAY}{lines[0][:100]}{RESET}")
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
        list_tasks(account, args, config)
    elif args.complete:
        complete_task(account, args.complete)
    else:
        create_task(account, args, config)


if __name__ == "__main__":
    main()
