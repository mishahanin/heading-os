#!/usr/bin/env python3
"""Cold-Sweep deterministic core (R2) - importable, no LLM, no HTTP.

Reads CRM health and routes overdue contacts into Action Queue cards. Two
consumers (plan 2026-06-03, Design Decision 5):

- the bridge daemon's scheduled job imports this and calls ``run()`` in-process,
  then appends via ``action_queue.append_cards`` under the queue lock;
- the thin ``scripts/cold-sweep.py`` CLI calls ``run()`` for manual runs and
  appends in-process through ``action_queue.append_cards``, exactly as the
  daemon does. It reached the daemon's ``/action-queue/deposit`` endpoint until
  2026-06-27; this line still said so until 2026-08-24, which taught a reader an
  architecture that no longer exists and implied a manual run needs the bridge
  daemon up. It does not (``.claude/rules/console-first.md``).

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.crm import is_radar_frozen  # noqa: E402

CONTACTS_DIR_REL = "crm/contacts"  # leak-guard: ok (relative reference string, not a filesystem path)
OVERDUE_HEALTH = ("red", "yellow")


def _frozen(radar_freeze_until: str | None, now: datetime) -> bool:
    """True if the contact is within an active radar-freeze window.

    A thin delegate, deliberately. This was a third private copy of the same
    parse, alongside `crm_next.rank_candidates` and `crm.is_radar_frozen`, and
    all three silently treated an unparseable value as NOT frozen — so a typo
    in a contact file turned a do-not-contact marker into an outreach card, and
    fixing any one of them left the other two wrong. Consolidated 2026-08-24;
    the fail-closed behaviour and its reasoning live in `is_radar_frozen`.
    """
    return is_radar_frozen(radar_freeze_until, now)


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
    """Run ``crm-health.py --json`` and return the contact list.

    Raises RuntimeError, naming the producer, when the health scorer cannot be
    run or does not answer with a JSON list. The bare ``json.loads`` here used
    to end a manual run in a raw JSONDecodeError traceback, which says nothing
    about which of the two scripts is at fault. `crm_next.py` and
    `utils/ops_signals.py` both grew their own guard for the same producer;
    this is the third, and it refuses out loud rather than degrading to zero,
    because an empty card list reads to the caller as "no one is overdue".

    The third clause is the decode. `text=True` decodes STRICTLY and does it
    INSIDE `subprocess.run` - before `json.loads`, before any handler here. A
    `UnicodeDecodeError` is a `ValueError`: a sibling of `JSONDecodeError`, no
    relation to `OSError` or `SubprocessError`, so it walked past both clauses
    below and left `run()` as exactly the raw traceback the paragraph above says
    this guard exists to prevent. MEASURED 2026-09-01 with a producer writing one
    0xff byte to its own file descriptor: `UnicodeDecodeError: 'utf-8' codec
    can't decode byte 0xff in position 12`.

    It refuses rather than passing `errors="replace"`, and that is the whole
    decision. Replacing decodes to `J��ane`, which is still valid JSON,
    so the run would continue and the mangled name would go out in the subject
    line of a real e-mail. Every other failure in this function is answered by
    naming the producer; a name nobody can read is not a lesser failure than
    prose where JSON was expected. Contract: `tests/test_cold_sweep_routing.py`.
    """
    cmd = [sys.executable, str(workspace_root / "scripts" / "crm-health.py"), "--json"]
    try:
        out = subprocess.run(
            cmd, cwd=str(workspace_root), capture_output=True, text=True,
            timeout=180, check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"crm-health.py --json could not be run: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            f"crm-health.py --json answered bytes that are not UTF-8 ({exc}); "
            "refusing rather than guessing at a contact name that becomes an email"
        ) from exc
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        head = out.stdout.strip().partition("\n")[0][:120] or "(empty)"
        raise RuntimeError(
            "crm-health.py --json exited 0 but its output is not JSON: "
            f"{head}"
        ) from exc
    if not isinstance(data, list):
        raise RuntimeError(
            f"crm-health.py --json returned {type(data).__name__}, expected a list"
        )
    return data


def run(workspace_root, *, now: datetime | None = None, cooldown_days: int = 14) -> list[dict]:
    """Fetch CRM health and build cards. Returns the card list (does not deposit)."""
    now = now or datetime.now(timezone.utc)
    rows = _fetch_rows(Path(workspace_root))
    return build_cards(rows, now=now, cooldown_days=cooldown_days)
