#!/usr/bin/env python3
"""The canonical knowledge-note types, in one place.

The same eight names were maintained twice: as `VALID_TYPES` in
`scripts/promote-knowledge.py` (the `--type` choices) and as
`KNOWLEDGE_SUBDIRS` in `scripts/provision-exec.py` (the directories a new exec
workspace gets). A ninth type added to one of them would have left the promoter
accepting a type with no directory to promote into, or a directory nothing could
ever be promoted to.
"""

from __future__ import annotations

KNOWLEDGE_TYPES: tuple[str, ...] = (
    "fleeting", "signals", "decisions", "meetings",
    "research", "strategy", "people", "technology",
)
