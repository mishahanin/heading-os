#!/usr/bin/env python3
"""crm-backfill-exchange.py -- One-shot 90-day back-fill of last_touch from
Exchange Sent Items. Repairs historical drift caused by sends that bypassed
send-email.py (i.e. emails sent directly from Outlook).

For each Sent Items message in the last N days, resolve the To address against
the CRM address book. If matched, bump last_touch on the relationship record
to the most recent send date per contact. Does NOT write log entries (those
would be retrospective fabrications; only the date is reliable).

Usage:
  python3 scripts/crm-backfill-exchange.py --dry-run            # show proposed changes
  python3 scripts/crm-backfill-exchange.py --days 90 --apply    # apply (default 90 days)
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root, load_env
from scripts.utils.colors import GREEN, YELLOW, RED, BOLD, RESET
from scripts.utils.crm_autolog import resolve_recipient, bump_last_touch_in_text, atomic_write


def _get_exchange_config() -> dict:
    """Load Exchange credentials from .env. Matches the pattern used by send-email.py."""
    load_env()
    required = ["EXCHANGE_SERVER", "EXCHANGE_EMAIL", "EXCHANGE_PASSWORD"]
    config = {}
    for key in required:
        val = os.getenv(key)
        if not val:
            print(f"[ERROR] Missing {key} in .env", file=sys.stderr)
            sys.exit(1)
        config[key] = val
    # EXCHANGE_USERNAME may differ from EMAIL for NTLM domain auth (e.g. domain\user).
    config["EXCHANGE_USERNAME"] = os.getenv("EXCHANGE_USERNAME", config["EXCHANGE_EMAIL"])
    return config


def fetch_sent_items_recent(days: int) -> list:
    """Fetch Sent Items messages from the last N days. Returns list of
    (recipient_email, sent_date_iso) tuples.

    Exits with code 1 on Exchange connection failure (auth, network, TLS).
    """
    try:
        from exchangelib import Account, Configuration, Credentials, DELEGATE
        from exchangelib.errors import UnauthorizedError, TransportError, ErrorAccessDenied
    except ImportError as e:
        print(f"{RED}[ERROR]{RESET} exchangelib not installed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        config = _get_exchange_config()
        creds = Credentials(config["EXCHANGE_USERNAME"], config["EXCHANGE_PASSWORD"])
        ex_config = Configuration(server=config["EXCHANGE_SERVER"], credentials=creds)
        account = Account(
            config["EXCHANGE_EMAIL"],
            config=ex_config,
            autodiscover=False,
            access_type=DELEGATE,
        )

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        sent = account.sent
        items: list = []
        for msg in sent.filter(datetime_sent__gte=cutoff).order_by("-datetime_sent"):
            if not msg.to_recipients:
                continue
            for r in msg.to_recipients:
                email = getattr(r, "email_address", None) or ""
                if email:
                    items.append((email.lower(), msg.datetime_sent.date().isoformat()))
        return items
    except UnauthorizedError as e:
        print(f"{RED}[ERROR]{RESET} Exchange auth failed (check EXCHANGE_EMAIL / EXCHANGE_PASSWORD in .env): {e}", file=sys.stderr)
        sys.exit(1)
    except (TransportError, ErrorAccessDenied) as e:
        print(f"{RED}[ERROR]{RESET} Exchange connection error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"{RED}[ERROR]{RESET} Unexpected error fetching Sent Items: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


def compute_proposed_bumps(items: list) -> dict:
    """For each unique (entity, max(send_date)), determine if a last_touch bump
    is proposed. Returns {relationship_path: (current_last_touch, proposed_date)}.

    Two-pass design:
      1. Walk all send events, accumulating max send date per resolved path
         (no disk reads -- just dict updates).
      2. Read each matched path's current last_touch once, filter to those
         needing a bump.
    """
    # Pass 1: find max send date per resolved path
    max_by_path: dict = {}
    for email, date in items:
        rel_path = resolve_recipient(email)
        if rel_path is None:
            continue
        if rel_path not in max_by_path or date > max_by_path[rel_path]:
            max_by_path[rel_path] = date

    # Pass 2: read current value once per path, filter
    proposed: dict = {}
    for rel_path, proposed_date in max_by_path.items():
        text = rel_path.read_text(encoding="utf-8")
        current = ""
        for line in text.split("\n")[:30]:
            if line.startswith("last_touch:"):
                current = line.split(":", 1)[1].strip()
                break
        if proposed_date > current:
            proposed[rel_path] = (current, proposed_date)
    return proposed


def cmd_dry_run(days: int) -> int:
    print(f"Fetching Sent Items from last {days} days...")
    items = fetch_sent_items_recent(days)
    print(f"  {len(items)} send events found.")
    proposed = compute_proposed_bumps(items)
    if not proposed:
        print(f"{GREEN}No bumps needed - all relationship records already up to date.{RESET}")
        return 0
    print(f"{BOLD}Proposed bumps:{RESET}")
    for path, (current, proposed_date) in sorted(proposed.items()):
        print(f"  {path.name}: {current or '(none)'} -> {proposed_date}")
    print(f"\n{YELLOW}{len(proposed)} relationship records would be updated.{RESET}")
    print(f"Run with --apply (no --dry-run) to apply.")
    return 0


def cmd_apply(days: int) -> int:
    items = fetch_sent_items_recent(days)
    proposed = compute_proposed_bumps(items)
    if not proposed:
        print(f"{GREEN}No bumps needed.{RESET}")
        return 0
    for path, (current, proposed_date) in sorted(proposed.items()):
        text = path.read_text(encoding="utf-8")
        new_text = bump_last_touch_in_text(text, proposed_date)
        atomic_write(path, new_text)
        print(f"  {GREEN}[bumped]{RESET} {path.name}: {current or '(none)'} -> {proposed_date}")
    print(f"\n{GREEN}Applied {len(proposed)} bumps.{RESET}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Back-fill last_touch from Exchange Sent Items.")
    parser.add_argument("--days", type=int, default=90, help="Days to look back (default 90)")
    parser.add_argument("--dry-run", action="store_true", help="Show proposed changes without applying")
    parser.add_argument("--apply", action="store_true", help="Apply the bumps")
    args = parser.parse_args()

    if args.dry_run and args.apply:
        parser.error("--dry-run and --apply are mutually exclusive")

    if not (args.dry_run or args.apply):
        parser.error("Specify --dry-run or --apply")

    if args.dry_run:
        sys.exit(cmd_dry_run(args.days))
    else:
        sys.exit(cmd_apply(args.days))


if __name__ == "__main__":
    main()
