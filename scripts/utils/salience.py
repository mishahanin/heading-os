#!/usr/bin/env python3
"""salience.py -- shared type-weight + access-count reinforcement formula.

One place for the "how load-bearing is this memory fact" signal, so
`scripts/memory-index.py` (recall ranking, Gap #2) and `scripts/dream-shadow.py`
(nightly dormancy + merge worklist, Gap #1) never compute it differently.

Nothing here ranks anything for removal. Auto-memory is never pruned; a low
score means a lower position in recall, and nothing else.

Consumed by:
  - scripts/memory-index.py (_importance_score reinforcement multiplier)
  - scripts/dream-shadow.py (merge candidate ranking)
"""
from __future__ import annotations

import math

TYPE_WEIGHT = {"feedback": 1.0, "project": 0.8, "user": 0.7, "reference": 0.5}
_DEFAULT_TYPE_WEIGHT = 0.6

# Log-scaled so the bonus keeps separating past the old linear ceiling. K and
# CAP are calibrated to the previous curve at its only two meaningful points:
# exactly 1.0 at zero accesses, and 1.2997 at ten — the old curve's 1.30 to
# four decimal places, since 1 + 0.125 * ln(11) is 1.2997369 and no K makes it
# land dead on. Above ten, where the old curve was flat, this one still ranks.
# The cap is reached near 121 accesses.
REINFORCE_K = 0.125
REINFORCE_CAP = 1.6


def type_weight(mem_type: str) -> float:
    """Base weight for a memory `type` (feedback/project/user/reference).

    Unrecognized or missing types get a neutral default, never zero — an
    unknown type should not be penalized as if it were worthless.
    """
    key = (mem_type or "").strip().lower()
    return TYPE_WEIGHT.get(key, _DEFAULT_TYPE_WEIGHT)


def reinforcement_bonus(access_count: int) -> float:
    """Multiplicative bonus from retrieval frequency, capped at REINFORCE_CAP.

    access_count <= 0 yields exactly 1.0 — no bonus AND no penalty. A memory
    that has never been retrieved is not demoted, it merely gains nothing; the
    ranking effect of low use is relative position, never removal.
    """
    count = max(int(access_count or 0), 0)
    return min(1.0 + REINFORCE_K * math.log1p(count), REINFORCE_CAP)


def composite_salience(mem_type: str, access_count: int) -> float:
    """Combined salience score used to rank merge candidates."""
    return type_weight(mem_type) * reinforcement_bonus(access_count)
