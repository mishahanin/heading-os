#!/usr/bin/env python3
"""salience.py -- shared type-weight + access-count reinforcement formula.

One place for the "how load-bearing is this memory fact" signal, so
`scripts/memory-index.py` (recall ranking, Gap #2) and `scripts/dream-shadow.py`
(nightly prune/merge worklist, Gap #1) never compute it differently.

Consumed by:
  - scripts/memory-index.py (_importance_score reinforcement multiplier)
  - scripts/dream-shadow.py (prune/merge candidate ranking)
"""
from __future__ import annotations

TYPE_WEIGHT = {"feedback": 1.0, "project": 0.8, "user": 0.7, "reference": 0.5}
_DEFAULT_TYPE_WEIGHT = 0.6

REINFORCE_K = 0.03
REINFORCE_CAP = 1.3


def type_weight(mem_type: str) -> float:
    """Base weight for a memory `type` (feedback/project/user/reference).

    Unrecognized or missing types get a neutral default, never zero -- an
    unknown type should not be penalized as if it were worthless.
    """
    key = (mem_type or "").strip().lower()
    return TYPE_WEIGHT.get(key, _DEFAULT_TYPE_WEIGHT)


def reinforcement_bonus(access_count: int) -> float:
    """Multiplicative bonus from citation frequency, capped at REINFORCE_CAP.

    access_count <= 0 yields exactly 1.0 (no bonus, no penalty).
    """
    count = max(int(access_count or 0), 0)
    return min(1.0 + count * REINFORCE_K, REINFORCE_CAP)


def composite_salience(mem_type: str, access_count: int) -> float:
    """Combined salience score used to rank prune/merge candidates."""
    return type_weight(mem_type) * reinforcement_bonus(access_count)
