#!/usr/bin/env python3
"""Cold-Sweep deterministic core (R2) - importable, no LLM, no HTTP.

Reads CRM health and routes overdue contacts into Action Queue cards. Two
consumers (plan 2026-06-03, Design Decision 5):

- the bridge daemon's scheduled job imports this and calls ``run()`` in-process,
  then appends via ``action_queue.append_cards`` under the queue lock;
- the thin ``scripts/cold-sweep.py`` CLI calls ``run()`` for manual runs and
  deposits via the daemon's ``/action-queue/deposit`` endpoint.

Snake_case filename because it is imported, not just executed (hyphens are
illegal in module names). ``build_cards`` is a pure function (synthetic rows in,
cards out) so it is unit-testable with no network. Dedup/cooldown is NOT done
here - that is the deposit/append helper's job (scrutiny L2).

Routing rules (deterministic):

| health | has email | route      | priority | card type  |
|--------|-----------|------------|----------|------------|
| red    | yes       | warm       | P1       | email_send |
| yellow | yes       | follow-up  | P2       | email_send |
| red/yel| no email  | cold       | P3       | note       |

Contacts that are not red/yellow (green/gray) are skipped - gray is the
dormant/no-cadence signal, so the health filter already excludes it. Contacts
inside an active ``radar_freeze_until`` window are skipped.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

CONTACTS_DIR_REL = "crm/contacts"  # leak-guard: ok (relative reference string, not a filesystem path)
OVERDUE_HEALTH = ("red", "yellow")


def _frozen(radar_freeze_until: str | None, now: datetime) -> bool:
    """True if the contact is within an active radar-freeze window."""
    if not radar_freeze_until or not str(radar_freeze_until).strip():
        return False
    try:
        dt = datetime.fromisoformat(str(radar_freeze_until).strip())
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt > now


def route(row: dict, now: datetime) -> tuple[str, str, str] | None:
    """Return (route_label, priority, action_type) for a contact, or None to skip."""
    if row.get("health") not in OVERDUE_HEALTH:
        return None
    if _frozen(row.get("radar_freeze_until"), now):
        return None
    has_email = bool((row.get("email") or "").strip())
    if has_email:
        if row.get("health") == "red":
            return ("warm", "P1", "email_send")
        return ("follow-up", "P2", "email_send")
    return ("cold", "P3", "note")


def build_cards(rows: list[dict], *, now: datetime, cooldown_days: int = 14) -> list[dict]:
    """Pure: map crm-health rows to Action Queue cards. No dedup, no IO.

    ``cooldown_days`` is accepted for signature stability but applied by the
    deposit/append helper (the sole dedup authority), not here.
    """
    cards: list[dict] = []
    for row in rows or []:
        routed = route(row, now)
        if routed is None:
            continue
        route_label, priority, action_type = routed
        name = row.get("name") or "(unknown)"
        company = row.get("company") or ""
        last_touch = row.get("last_touch") or "never"
        days_overdue = row.get("days_overdue") or 0
        cadence = row.get("cadence") or 0
        fname = row.get("file") or ""
        contact_file = f"{CONTACTS_DIR_REL}/{fname}" if fname else None
        health = (row.get("health") or "").upper()
        reasoning = (f"{health} - {days_overdue}d overdue (cadence {cadence}d). "
                     f"Last touch {last_touch}.")
        citations = [{
            "source": contact_file or "crm",
            "excerpt": f"last_touch {last_touch}, {days_overdue}d overdue",
        }]
        title = f"{route_label}: {name}" + (f" ({company})" if company else "")
        card: dict = {
            "action_type": action_type,
            "source": "cold-sweep",
            "priority": priority,
            "route": route_label,
            "title": title,
            "reasoning": reasoning,
            "citations": citations,
            "contact_file": contact_file,
        }
        if action_type == "email_send":
            card["to"] = row.get("email")
            card["subject"] = ""
            card["draft_body"] = ""
            card["draft_status"] = "needs_draft"
        cards.append(card)
    return cards


def _fetch_rows(workspace_root: Path) -> list[dict]:
    """Run ``crm-health.py --json`` and return the contact list."""
    cmd = [sys.executable, str(workspace_root / "scripts" / "crm-health.py"), "--json"]
    out = subprocess.run(
        cmd, cwd=str(workspace_root), capture_output=True, text=True,
        timeout=180, check=True,
    )
    data = json.loads(out.stdout)
    return data if isinstance(data, list) else []


def run(workspace_root, *, now: datetime | None = None, cooldown_days: int = 14) -> list[dict]:
    """Fetch CRM health and build cards. Returns the card list (does not deposit)."""
    now = now or datetime.now(timezone.utc)
    rows = _fetch_rows(Path(workspace_root))
    return build_cards(rows, now=now, cooldown_days=cooldown_days)
