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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_default_tz, get_workspace_root, load_env
from scripts.utils.colors import GREEN, YELLOW, RED, BOLD, RESET
from scripts.utils.crm_autolog import resolve_recipient, bump_last_touch_in_text, atomic_write
from scripts.utils.markdown import parse_frontmatter_str


def local_day(dt):
    """The operator's calendar day for an Exchange timestamp.

    Separate and importable so a test can drive it without a mailbox. Every test
    in `tests/test_a_backfill_that_walked_the_date_backwards.py` stubs
    `fetch_sent_items_recent` and feeds ready-made date strings, so the
    conversion inside that fetch had never been measured at all.

    A naive datetime is read as UTC, because that is what exchangelib hands over
    when a zone is missing; guessing local instead would invent a shift nothing
    asked for.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(get_default_tz()).date()


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
                    # The OPERATOR's calendar day, not UTC's. `datetime_sent` is
                    # an exchangelib EWSDateTime and is always UTC-aware, so a
                    # bare `.date()` files a mail sent at 01:30 local under
                    # yesterday. That value goes straight into `last_touch:`,
                    # which the whole CRM staleness stack reads as a local date,
                    # and is compared as a plain STRING against the stored one -
                    # so the bump decision itself was made across two clocks.
                    items.append((email.lower(),
                                  local_day(msg.datetime_sent).isoformat()))
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


def _stored_date(raw: str) -> str:
    """A frontmatter `last_touch` value as a bare YYYY-MM-DD, or "".

    The comparison downstream is a STRING compare, and it assumed the stored
    value was already bare. It is not always: `last_touch: "2026-04-01"` and
    `last_touch: 2026-04-01T09:00:00` are both things a contact file carries. A
    leading quote sorts BELOW every digit, so a quoted date made every backfill
    run propose a bump even when the stored date was newer, and the dry-run
    showed that wrong list as if it were right. Unparseable returns "", which
    compares below any real date, so the bump is proposed and the operator sees
    it rather than the value being silently trusted.

    That last sentence described `--dry-run` only until 2026-08-24: `--apply`
    had no gate, so an unreadable value was overwritten with a stderr line as
    its whole notice. `compute_proposed_bumps` now tells an ABSENT value from an
    unreadable one, and `cmd_apply` SKIPS the unreadable ones — the promise the
    paragraph above makes, kept on the path that writes.
    """
    value = (raw or "").strip().strip('"').strip("'").strip()
    if not value:
        return ""
    head = value.split("T", 1)[0].split(" ", 1)[0]
    try:
        return date.fromisoformat(head).isoformat()
    except ValueError:
        print(f"crm-backfill: unreadable last_touch {raw.strip()!r}; treating as "
              f"unset.", file=sys.stderr)
        return ""


def compute_proposed_bumps(items: list) -> dict:
    """For each unique (entity, max(send_date)), determine if a last_touch bump
    is proposed. Returns
    {relationship_path: (current_last_touch, proposed_date, unreadable)}.

    `unreadable` is True when the file HAS a `last_touch` that could not be
    parsed, as opposed to not having one: `cmd_apply` skips the first and writes
    the second.

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
        # The whole frontmatter block, through the shared parser, and no longer
        # `text.split("\n")[:30]`. The WRITER, `bump_last_touch_in_text`, matches
        # `^last_touch:` over the entire file with no line cap; only this READER
        # stopped at line 30. A contact whose frontmatter runs longer (many tags,
        # aliases, entity refs) read as unset, "" compares below every ISO date,
        # so a bump was proposed and `--apply` then REGRESSED `last_touch` to an
        # older send — silently rewriting the one field `crm-health.py` scores
        # on, and pushing a healthy contact toward red.
        raw = str(parse_frontmatter_str(text)[0].get("last_touch", ""))
        current = _stored_date(raw)
        # An ABSENT value and an unreadable one both read as "", and they are not
        # the same thing. `_stored_date`'s docstring justifies the "" by saying
        # the operator "sees it rather than the value being silently trusted" —
        # true of --dry-run, and false of --apply, which had no gate at all.
        unreadable = bool(raw.strip()) and not current
        if proposed_date > current:
            proposed[rel_path] = (current, proposed_date, unreadable)
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
    for path, (current, proposed_date, unreadable) in sorted(proposed.items()):
        note = f"  {YELLOW}(last_touch unreadable; --apply will SKIP it){RESET}" if unreadable else ""
        print(f"  {path.name}: {current or '(none)'} -> {proposed_date}{note}")
    writable = sum(1 for _c, _d, bad in proposed.values() if not bad)
    print(f"\n{YELLOW}{writable} relationship records would be updated.{RESET}")
    if writable != len(proposed):
        print(f"{YELLOW}{len(proposed) - writable} skipped: last_touch unreadable, "
              f"fix by hand.{RESET}")
    print(f"Run with --apply (no --dry-run) to apply.")
    return 0


def cmd_apply(days: int) -> int:
    items = fetch_sent_items_recent(days)
    proposed = compute_proposed_bumps(items)
    if not proposed:
        print(f"{GREEN}No bumps needed.{RESET}")
        return 0
    applied = 0
    skipped = []
    for path, (current, proposed_date, unreadable) in sorted(proposed.items()):
        if unreadable:
            # The review `_stored_date`'s docstring promises, actually held. An
            # unreadable value is the one signal that this file needs a human,
            # and overwriting it destroys that signal with no confirmation.
            skipped.append(path.name)
            print(f"  {YELLOW}[skipped]{RESET} {path.name}: last_touch is present "
                  f"and unreadable; fix it by hand rather than have it overwritten")
            continue
        text = path.read_text(encoding="utf-8")
        new_text = bump_last_touch_in_text(text, proposed_date)
        atomic_write(path, new_text)
        applied += 1
        print(f"  {GREEN}[bumped]{RESET} {path.name}: {current or '(none)'} -> {proposed_date}")
    print(f"\n{GREEN}Applied {applied} bumps.{RESET}")
    if skipped:
        print(f"{YELLOW}Skipped {len(skipped)}: {', '.join(skipped)}{RESET}")
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
