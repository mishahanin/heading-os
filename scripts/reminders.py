#!/usr/bin/env python3
"""Reminders CLI -- console-first writer for durable reminders.

Usage:
    python scripts/reminders.py add --once 2026-09-01 --message "..." [--command "/x"] [--thread PATH]
    python scripts/reminders.py add --recurring first-friday-minus-1 --message "..."
    python scripts/reminders.py list [--json]
    python scripts/reminders.py rm <id>
    python scripts/reminders.py done <id>
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import reminders_store as rs  # noqa: E402


def cmd_add(args) -> int:
    if args.once:
        try:
            date.fromisoformat(args.once)  # validate
        except ValueError:
            print(f"invalid date: {args.once}", file=sys.stderr)
            return 2
        rec = {"kind": "once", "when": args.once, "message": args.message}
    elif args.recurring:
        if args.recurring not in rs.RECURRENCE_RULES:
            print(f"unknown recurrence rule: {args.recurring}", file=sys.stderr)
            return 2
        rec = {"kind": "recurring", "when": args.recurring, "message": args.message}
    else:
        print("one of --once or --recurring is required", file=sys.stderr)
        return 2
    if args.command:
        rec["command"] = args.command
    if args.thread:
        rec["thread"] = args.thread
    saved = rs.add(rec)
    print(f"added {saved['id']} ({saved['kind']})")
    return 0


def cmd_list(args) -> int:
    records = rs.load()
    if args.json:
        print(json.dumps(records, indent=2, ensure_ascii=False))
        return 0
    if not records:
        print("(no reminders)")
        return 0
    for r in records:
        tail = f"  cmd={r['command']}" if r.get("command") else ""
        status = r.get("status") or f"last_fired={r.get('last_fired')}"
        print(f"{r['id']}  {r['kind']:9}  {r['when']:22}  {status:14}  {r['message']}{tail}")
    return 0


def cmd_rm(args) -> int:
    ok = rs.remove(args.id)
    print("removed" if ok else "not found", args.id)
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Durable reminders CLI.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add", help="Add a reminder")
    g = pa.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", metavar="YYYY-MM-DD")
    g.add_argument("--recurring", metavar="RULE")
    pa.add_argument("--message", required=True)
    pa.add_argument("--command", default=None)
    pa.add_argument("--thread", default=None)
    pa.set_defaults(fn=cmd_add)

    pl = sub.add_parser("list", help="List reminders")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(fn=cmd_list)

    pr = sub.add_parser("rm", help="Remove a reminder by id")
    pr.add_argument("id")
    pr.set_defaults(fn=cmd_rm)

    pd = sub.add_parser("done", help="Mark a reminder done (remove it)")
    pd.add_argument("id")
    pd.set_defaults(fn=cmd_rm)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
