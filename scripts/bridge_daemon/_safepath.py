"""The bridge daemon's path guard: separator normalisation, and symlinks.

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


def normalize_rel_path(rel_path: str) -> str:
    """Forward-slash the separators and trim. Strips NO prefix, deliberately.

    Every file reader under ``sources/`` normalises its caller-supplied
    ``rel_path`` and then checks the result starts with its own directory
    prefix. Six of them wrote that normalisation as::

        rel_path.replace("\\\\", "/").lstrip("./")

    ``str.lstrip`` takes a CHARACTER SET, not a prefix, so it removes EVERY
    leading ``.`` and ``/`` rather than one ``./``. The prefix check then runs
    against a string the caller never sent. ``approvals.py`` found this first
    and dropped the strip; the other five kept it until 2026-08-29.

    Measured that day against the real readers, ``library.read_note``,
    ``threads.read_thread``, ``investors.read_dossier``,
    ``studio.read_inflight`` and ``studio.resolve_artifact_image``: 25 of 30
    hostile inputs were accepted where ``validate_draft_rel_path`` refused all
    5 of the same shapes. ``../../knowledge/note.md`` returned
    ``{"ok": True, "path": "knowledge/note.md"}``, and ``/knowledge/note.md``,
    ``...knowledge/note.md`` and ``.././/knowledge/note.md`` all read the same
    file.

    Nothing escaped the served tree. ``lstrip`` eats the ``..`` along with the
    dots, so by the time the path is joined there is no traversal segment left,
    and ``../secret.md`` was refused. What was lost is refusal fidelity: a
    string the reference validator rejects was silently rewritten into an
    in-tree path, served, and reported back under a ``path`` field holding the
    rewritten value rather than the requested one. That is the same asymmetry
    ``validate_draft_rel_path`` was written to end, one reader disagreeing with
    another about what a valid path is.

    Trimming whitespace and unifying separators is all the normalisation a
    relative path needs here. Anything with a leading dot or slash is not the
    caller naming a served file, so the prefix check each reader already owns
    is the right place for it to die.

    It does NOT lowercase. The value builds a real path, and the served trees
    sit on a case-sensitive filesystem.
    """
    return rel_path.replace("\\", "/").strip()


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
