"""Reversibility-tier loader for the Action Queue risk gate (R3).

Maps an Action Queue ``action_type`` to one of three reversibility tiers that
the executor consults to decide friction:

- ``autonomous`` - auto-execute on deposit, no CEO click (e.g. no-op ``note``).
- ``notify``     - auto-apply with a one-click undo via the disposition log.
- ``gated``      - human click required before the executor acts (every send).

The ledger lives in ``config/tool-risk.json`` and is *data*. The send-gate is
*code*: any ``action_type`` listed in the ledger's ``send_capable`` set resolves
to ``gated`` no matter what its ``tiers`` entry says. This makes the
lethal-trifecta control non-overridable by editing a config file - a tampered
ledger that marks ``email_send`` autonomous still resolves gated. Unknown or
missing types resolve to ``gated`` (safe default, matching the workspace
"missing metadata -> friction-maximal" convention).

Usage::

    from scripts.utils import tool_risk
    tool_risk.tier_for("email_send")      # -> "gated" (invariant)
    tool_risk.tier_for("note")            # -> "autonomous"
    tool_risk.tier_for("pipeline_update") # -> "notify"
    tool_risk.tier_for("unknown_type")    # -> "gated" (safe default)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.workspace import get_workspace_root

GATED = "gated"
NOTIFY = "notify"
AUTONOMOUS = "autonomous"
_VALID_TIERS = {GATED, NOTIFY, AUTONOMOUS}

_CACHE: dict | None = None


def _ledger_path() -> Path:
    return get_workspace_root() / "config" / "tool-risk.json"


def load(*, force: bool = False) -> dict:
    """Load and cache the ledger. ``force=True`` re-reads from disk (tests)."""
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    path = _ledger_path()
    data: dict = {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Missing or malformed ledger: empty tiers + empty send_capable.
        # tier_for then resolves everything to gated (safe default).
        data = {}
    _CACHE = data
    return data


def tier_for(action_type: str) -> str:
    """Resolve an ``action_type`` to ``autonomous`` / ``notify`` / ``gated``.

    Non-overridable invariant: a ``send_capable`` type always returns
    ``gated``, even if the ledger's ``tiers`` entry says otherwise. Unknown or
    missing types return ``gated``.
    """
    ledger = load()

    # Invariant first: send-capable types floor at gated, regardless of tiers.
    send_capable = ledger.get("send_capable") or []
    if action_type in send_capable:
        return GATED

    entry = (ledger.get("tiers") or {}).get(action_type)
    if not isinstance(entry, dict):
        return GATED
    tier = entry.get("tier")
    if tier not in _VALID_TIERS:
        return GATED
    return tier


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Resolve an action_type to its reversibility tier.")
    parser.add_argument("action_type", nargs="?", help="action_type to resolve; omit to dump the ledger")
    args = parser.parse_args()

    if args.action_type:
        print(tier_for(args.action_type))
    else:
        print(json.dumps(load(), indent=2))
