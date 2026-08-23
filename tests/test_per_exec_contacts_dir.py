#!/usr/bin/env python3
"""An exec's contacts live at `<data-repo>/crm/contacts/`, not `<data-repo>/contacts/`.

Found 2026-08-23 while checking that the fleet commands worked after the
registry split. `scripts/admin-health.py` listed the whole fleet as DEAD with
0 contacts. It was not dead: two `.heading-os-data-{slug}/crm/contacts/`
directories held 11 and 7 contact files. Five call sites joined `contacts`
straight onto the repo root, one level above where the files are, and an empty
directory reads exactly like an empty fleet.

Two of the five WRITE there (`transfer-contact.py`, `merge-contacts.py`), so a
transfer would have filed a contact into a directory no reader ever looks at.

The layout is the one `get_per_exec_repo_path` already documents: "each exec's
full data overlay is cloned as `../.heading-os-data-{slug}` ... with CRM
contacts inside it at `crm/contacts/`". The docstring was right and the callers
were not, which is why the resolution is now a helper instead of a path join
repeated five times.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.workspace import (  # noqa: E402
    get_per_exec_contacts_dir,
    get_per_exec_repo_path,
)


def test_contacts_sit_under_crm_inside_the_exec_repo():
    d = get_per_exec_contacts_dir("probe")
    assert d == get_per_exec_repo_path("probe") / "crm" / "contacts"
    assert d.name == "contacts" and d.parent.name == "crm"


def test_it_is_not_the_repo_root_join_that_was_wrong():
    assert get_per_exec_contacts_dir("probe") != get_per_exec_repo_path("probe") / "contacts"


def test_a_bad_slug_is_refused_by_the_repo_resolver():
    import pytest
    for bad in ("../escape", "with/slash", ""):
        with pytest.raises(ValueError):
            get_per_exec_contacts_dir(bad)


def test_no_caller_joins_contacts_onto_the_repo_root():
    """The grep guard. Two of the five offenders wrote to that path."""
    offenders = []
    for script in sorted((ROOT / "scripts").rglob("*.py")):
        text = script.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if 'repo_path / "contacts"' in line or \
               'get_per_exec_repo_path(exec_slug) / "contacts"' in line or \
               'get_per_exec_repo_path(slug) / "contacts"' in line:
                offenders.append(f"{script.relative_to(ROOT)}:{n}: {stripped[:90]}")
    assert offenders == [], (
        "join through get_per_exec_contacts_dir(slug) instead:\n  "
        + "\n  ".join(offenders)
    )


def test_the_live_fleet_is_visible_through_the_helper():
    """Against the real overlay when one is present: the count must be non-zero
    for at least one exec, or the helper is pointing somewhere empty again."""
    import pytest
    from scripts.utils.workspace import get_all_active_exec_slugs

    slugs = get_all_active_exec_slugs()
    if not slugs:
        pytest.skip("no fleet on this clone")
    found = {s: len(list(get_per_exec_contacts_dir(s).glob("*.md")))
             for s in slugs if get_per_exec_contacts_dir(s).is_dir()}
    if not found:
        pytest.skip("no exec data repos cloned on this machine")
    assert sum(found.values()) > 0, (
        f"every exec reads as zero contacts: {found}. That was the bug."
    )
