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
    tool_risk.send_capable_types()        # -> frozenset({"email_send", ...})
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
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        # Missing or malformed ledger: empty tiers + empty send_capable.
        # tier_for then resolves everything to gated (safe default).
        #
        # `UnicodeDecodeError` sits in this tuple because it is a SIBLING of
        # `json.JSONDecodeError` under `ValueError`, not a subclass of it, and
        # the decode happens inside `read_text` before `json.loads` is handed
        # anything at all. Without it the documented fallback two lines above
        # was false for the commonest kind of corruption there is.
        #
        # MEASURED 2026-09-01 against a copy of the shipped ledger with one
        # 0xff byte spliced in: `load`, `send_capable_types` and `tier_for`
        # each raised a raw UnicodeDecodeError instead of resolving `gated`.
        # `tier_for` IS the lethal-trifecta send gate, so the reach was the
        # whole send path: `approve_and_send` (scripts/action-queue.py:195)
        # calls it outside any handler and `cmd_approve` has none, so the CEO's
        # own approve command answered a traceback rather than a refusal; the
        # batch executor's broad `except Exception` turned it into a
        # `send_failed` result, which sends a reader looking for a mail problem
        # that is really a corrupt config file; and the three bridge-daemon
        # callers (sources/action_queue.py:344 and :659, bridge-daemon.py:189)
        # had no handler either. Nothing SENT - the gate fails closed by
        # crashing - but "resolves gated" and "raises" are different answers
        # and only one of them is the one this module promises.
        #
        # `scripts/action-queue.py` already caught this class at its own two
        # reads (lines 406 and 426). This module, one layer below both, had
        # fallen behind them.
        data = {}
    _CACHE = data
    return data


def send_capable_types() -> frozenset[str]:
    """The ledger's ``send_capable`` set - every ``action_type`` that can reach
    a third party.

    The ONE place a consumer asks "which action_types are sends". Consumers
    must not keep their own copy: a second list is a list that can fall out of
    step with this one, and the copy is the one that stops being updated.
    ``scripts/action-queue.py`` and ``scripts/action-queue-execute.py`` each
    carried a hardcoded ``("email_send", "telegram_send")`` tuple until
    2026-08-31, and each had its tier check INSIDE the branch that tuple keyed,
    so a type registered here but absent from the tuple never reached the
    check.

    A missing or malformed ``send_capable`` yields an EMPTY set. That is why
    membership here must never be the only gate: a consumer pairs this with an
    unconditional ``tier_for`` check, which is total over all action_types and
    answers ``gated`` for one it does not know, so an emptied or tampered
    ledger cannot switch a consumer's gate OFF - only widen or narrow which
    refusal it reports.
    """
    raw = load().get("send_capable")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(x for x in raw if isinstance(x, str))


def tier_for(action_type: str) -> str:
    """Resolve an ``action_type`` to ``autonomous`` / ``notify`` / ``gated``.

    Non-overridable invariant: a ``send_capable`` type always returns
    ``gated``, even if the ledger's ``tiers`` entry says otherwise. Unknown or
    missing types return ``gated``.
    """
    ledger = load()

    # Invariant first: send-capable types floor at gated, regardless of tiers.
    if action_type in send_capable_types():
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
