#!/usr/bin/env python3
"""Reminders store: atomic JSON persistence + due-date logic for durable reminders.

Sole owner of outputs/operations/reminders/reminders.json. Imported by the CLI
(scripts/reminders.py), the dispatcher (scripts/reminders-notify.py), and the
/prime check. Pure stdlib; no network, no LLM. See
docs/superpowers/specs/2026-07-14-durable-reminders-design.md.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.workspace import get_outputs_dir  # noqa: E402

RECURRENCE_RULES = {"first-friday-minus-1"}


def store_path() -> Path:
    return get_outputs_dir() / "operations" / "reminders" / "reminders.json"


def load() -> list[dict]:
    p = store_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"reminders store unreadable ({p}): {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"reminders store is not a list ({p})")
    return data


def save(records: list[dict]) -> None:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def new_id() -> str:
    return secrets.token_hex(4)


def add(record: dict) -> dict:
    rec = dict(record)
    rec.setdefault("id", new_id())
    rec.setdefault("created", datetime.now(timezone.utc).isoformat())
    if rec.get("kind") == "once":
        rec.setdefault("status", "active")
    elif rec.get("kind") == "recurring":
        rec.setdefault("last_fired", None)
    records = load()
    records.append(rec)
    save(records)
    return rec


def remove(rid: str) -> bool:
    records = load()
    kept = [r for r in records if r.get("id") != rid]
    if len(kept) == len(records):
        return False
    save(kept)
    return True


def mark_fired(rid: str, today: date) -> None:
    records = load()
    for r in records:
        if r.get("id") == rid:
            if r.get("kind") == "once":
                r["status"] = "fired"
            else:
                r["last_fired"] = today.isoformat()
            break
    save(records)


def first_friday_minus_1(year: int, month: int) -> date:
    d = date(year, month, 1)
    # weekday(): Mon=0 .. Fri=4. Days until the first Friday.
    first_friday = d + timedelta(days=(4 - d.weekday()) % 7)
    return first_friday - timedelta(days=1)


def _recurring_due_date(rule: str, today: date) -> date:
    if rule not in RECURRENCE_RULES:
        raise ValueError(f"unknown recurrence rule: {rule}")
    return first_friday_minus_1(today.year, today.month)


def _next_month_date(today: date) -> date:
    """First day of the month after `today`, rolling the year at December."""
    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)


def _recurring_candidates(rule: str, today: date) -> list[date]:
    """Both the current-month and next-month target dates for `rule`.

    A recurring target computed for month M can land in month M-1 (e.g.
    first-friday-minus-1 when the first Friday is the 1st). Evaluating only
    `today`'s own month therefore misses that boundary day. Checking both
    candidates against `today` closes the gap without special-casing any
    rule by name.
    """
    return [
        _recurring_due_date(rule, today),
        _recurring_due_date(rule, _next_month_date(today)),
    ]


def is_due(record: dict, today: date) -> bool:
    kind = record.get("kind")
    if kind == "once":
        if record.get("status") == "fired":
            return False
        return date.fromisoformat(record["when"]) <= today
    if kind == "recurring":
        if today not in _recurring_candidates(record["when"], today):
            return False
        last = record.get("last_fired")
        return last != today.isoformat()
    raise ValueError(f"unknown reminder kind: {kind}")


def due_records(today: date) -> list[dict]:
    return [r for r in load() if is_due(r, today)]


def upcoming(today: date, days: int = 7) -> list[dict]:
    out: list[dict] = []
    horizon = today + timedelta(days=days)
    for r in load():
        if r.get("kind") == "once" and r.get("status") != "fired":
            when = date.fromisoformat(r["when"])
            if today < when <= horizon:
                out.append(r)
        elif r.get("kind") == "recurring":
            for target in _recurring_candidates(r["when"], today):
                if today < target <= horizon and r.get("last_fired") != target.isoformat():
                    out.append(r)
                    break
    return out
