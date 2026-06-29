#!/usr/bin/env python3
"""Dead-letter queue console driver (R14, console-first).

A failed finalizer (permanent send failure, exhausted transient retry) is
recorded by ``scripts/utils/dead_letter.py`` as an inert JSON artifact under
``outputs/operations/dead-letter/``. This CLI surfaces, retries, and purges
those artifacts.

All four subcommands work with the bridge daemon DOWN. ``list`` / ``show`` /
``purge`` read and mutate the artifact files directly. ``retry`` re-enqueues a
failed send as a fresh *pending* Action Queue card IN-PROCESS via
``action_queue.append_cards`` (daemon-free since 2026-06-27) - re-approval is
required, the send is NEVER auto-replayed.

Usage:
  python scripts/dead-letter.py list
  python scripts/dead-letter.py show <trace-id-or-prefix>
  python scripts/dead-letter.py retry <trace-id-or-prefix>
  python scripts/dead-letter.py purge [--older-than N]

Exit codes: 0 ok, 1 request/usage error.

CEO-only: part of the CEO-only spine prove-out.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.bridge_daemon.sources.action_queue import append_cards
from scripts.utils import dead_letter
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import get_data_root


def _age_str(entry: dict, path: Path) -> str:
    recorded = entry.get("recorded_at")
    try:
        if recorded:
            dt = datetime.fromisoformat(recorded)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except (ValueError, OSError):
        return "?"
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    if secs < 3600:
        return f"{int(secs / 60)}m"
    if secs < 86400:
        return f"{int(secs / 3600)}h"
    return f"{int(secs / 86400)}d"


def _resolve(prefix: str) -> Path:
    """Resolve a trace-id (or filename-stem) prefix to a single DLQ entry path."""
    entries = dead_letter.list_entries()
    matches = []
    for p in entries:
        try:
            tid = dead_letter.load(p).get("trace_id", "")
        except (OSError, json.JSONDecodeError):
            tid = ""
        if p.stem == prefix or tid == prefix or p.stem.startswith(prefix) or str(tid).startswith(prefix):
            matches.append(p)
    if not matches:
        print(f"{RED}no dead-letter entry matches '{prefix}'{RESET}", file=sys.stderr)
        sys.exit(1)
    if len(matches) > 1:
        print(f"{RED}ambiguous prefix '{prefix}' ({len(matches)} matches){RESET}", file=sys.stderr)
        sys.exit(1)
    return matches[0]


def cmd_list(args) -> int:
    entries = dead_letter.list_entries()
    if not entries:
        print(f"{GRAY}dead-letter queue empty{RESET}")
        return 0
    print(f"{BOLD}{len(entries)} dead-letter entr(ies){RESET}")
    print(f"  {'TRACE':<14} {'KIND':<14} {'CLASS':<10} {'AGE':<6} ERROR")
    for p in entries:
        try:
            e = dead_letter.load(p)
        except (OSError, json.JSONDecodeError):
            print(f"  {GRAY}{p.name} (unreadable){RESET}")
            continue
        cls = e.get("classification", "?")
        ccol = RED if cls == "permanent" else YELLOW
        err = (e.get("error") or "").replace("\n", " ")[:48]
        print(f"  {str(e.get('trace_id', '-'))[:14]:<14} {str(e.get('kind', '-'))[:14]:<14} "
              f"{ccol}{cls:<10}{RESET} {_age_str(e, p):<6} {GRAY}{err}{RESET}")
    return 0


def cmd_show(args) -> int:
    path = _resolve(args.id)
    print(json.dumps(dead_letter.load(path), indent=2, ensure_ascii=False))
    return 0


def cmd_purge(args) -> int:
    removed = dead_letter.purge(older_than_days=args.older_than)
    print(f"{GREEN}purged{RESET} {removed} entr(ies) older than {args.older_than}d")
    return 0


def cmd_retry(args) -> int:
    path = _resolve(args.id)
    entry = dead_letter.load(path)
    payload = entry.get("payload") or {}
    if entry.get("kind") != "email_send":
        print(f"{RED}retry only supports email_send entries (got '{entry.get('kind')}'){RESET}",
              file=sys.stderr)
        return 1
    to = payload.get("to")
    if not to:
        print(f"{RED}entry has no recipient; cannot re-enqueue{RESET}", file=sys.stderr)
        return 1

    # Re-enqueue as a fresh PENDING card. Re-approval is required - the gate
    # holds, nothing is auto-sent. The drafted body is preserved so the CEO
    # reviews the same content that previously failed.
    card = {
        "action_type": "email_send",
        "to": to,
        "subject": payload.get("subject", ""),
        "draft_body": payload.get("draft_body", ""),
        "draft_status": "ready_for_review",
        "contact_file": payload.get("contact_file", ""),
        "title": payload.get("title") or f"Retry send to {to}",
        "priority": "P1",
        "reasoning": f"Re-enqueued from dead-letter ({entry.get('error', 'failed send')[:120]})",
        "source": "dead-letter-retry",
    }
    # Daemon-free deposit (2026-06-27): append in-process under the DATA root.
    resp = append_cards(get_data_root(), [card])
    if resp.get("added"):
        # Recovered: drop the artifact so it doesn't linger as a duplicate.
        try:
            path.unlink()
        except OSError:
            pass
        print(f"{GREEN}re-enqueued{RESET} {to} as a fresh pending card "
              f"(needs approval; nothing sent). Dead-letter entry removed.")
        return 0
    print(f"{YELLOW}deposit returned no new card{RESET} "
          f"(likely deduped against an existing card): {CYAN}{resp}{RESET}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Dead-letter queue console driver.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list dead-letter entries")
    p_show = sub.add_parser("show", help="print one entry's full JSON")
    p_show.add_argument("id")
    p_retry = sub.add_parser("retry", help="re-enqueue a failed send as a fresh pending card")
    p_retry.add_argument("id")
    p_purge = sub.add_parser("purge", help="delete entries older than N days")
    p_purge.add_argument("--older-than", type=int, default=90)
    args = ap.parse_args()

    dispatch = {
        "list": cmd_list, "show": cmd_show, "retry": cmd_retry, "purge": cmd_purge,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
