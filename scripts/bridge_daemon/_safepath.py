"""The symlink half of the bridge daemon's path guard.

Nine of the ten file readers under ``sources/`` carried this shape::

    target = (base / rel_path).resolve()
    target.relative_to(base_resolved)          # containment: works
    if target.is_symlink():                    # symlink ban: DEAD
        return {"ok": False, "error": "symlinks not allowed"}

``Path.resolve()`` dereferences every symlink in the path, so by the time
``is_symlink()`` runs it is asking whether the RESOLVED file is a link, and a
resolved path is never one. Measured 2026-08-24 across
``scripts/bridge_daemon/sources/``: nine dead guards, one live
(``studio.py`` line 372, which tests an unresolved glob result).

The containment check still holds, so nothing escaped the served directory --
this is not a traversal hole. What was lost is the workspace's own
no-symlinks-ever policy: a link INSIDE the served tree pointing at another file
inside it was followed while the endpoint reported it had refused links, and
four of those readers advertise "No symlinks" in their docstrings. A control
that is documented and absent is worse than one that was never claimed, because
the next reader budgets for it.

``contains_symlink`` asks the question the guards meant to ask, of the path they
meant to ask it about: the UNRESOLVED one, component by component.

Found by the 2026-08-23 engine audit (findings 3 and 6, which named two of the
nine).
"""
from __future__ import annotations

from pathlib import Path


def contains_symlink(root: Path, target: Path) -> bool:
    """True when any component from ``root`` down to ``target`` is a symlink.

    Both paths are taken UNRESOLVED -- passing a ``.resolve()``d target is the
    original bug. Components at or above ``root`` are not examined: the
    workspace itself may legitimately sit under a linked mount, and that is not
    what the ban is about.

    A ``target`` that is not under ``root`` returns True: out of scope for this
    function to judge, and the containment check that owns that case treats it
    as a refusal too.
    """
    try:
        rel = target.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False
