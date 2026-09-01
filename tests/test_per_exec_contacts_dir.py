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

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import read_sources  # noqa: E402
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
    for bad in ("../escape", "with/slash", ""):
        with pytest.raises(ValueError):
            get_per_exec_contacts_dir(bad)


# The wrong join, matched by SHAPE rather than by the three exact strings the
# five 2026-08-23 offenders happened to use. Those were
# `repo_path / "contacts"` and `get_per_exec_repo_path({slug,exec_slug}) /
# "contacts"`, and a literal list of them is green the moment somebody spells the
# variable `exec_repo`, wraps the call, or reaches for `.joinpath`. The near-miss
# a guard has to catch is the realistic one, not the one already fixed.
_WRONG_JOIN = re.compile(
    r'(?:get_per_exec_repo_path\s*\([^)]*\)|\b\w*repo\w*)\s*'
    r'(?:/\s*"contacts"|\.joinpath\(\s*"contacts"\s*\))'
)


def _joins_contacts_too_high(line: str) -> bool:
    """True for a path join that reaches `<exec repo>/contacts`, one level high.

    `crm` on the same line is the correct layout and is never an offence, which
    is what keeps `get_per_exec_contacts_dir`'s own body and every
    `root / "crm" / "contacts"` in the tree out of the report.
    """
    if line.strip().startswith("#"):
        return False
    if '"crm"' in line and '"contacts"' in line:
        return False
    return bool(_WRONG_JOIN.search(line))


@pytest.mark.parametrize("line", [
    '        contacts_dir = repo_path / "contacts"',
    '    d = get_per_exec_repo_path(slug) / "contacts"',
    '    d = get_per_exec_repo_path(exec_slug) / "contacts"',
    '    d = get_per_exec_repo_path(e["slug"]) / "contacts"',
    '        target = exec_repo / "contacts" / f"{name}.md"',
    '        target = repo_dir.joinpath("contacts")',
])
def test_the_matcher_bites_the_realistic_near_miss(line):
    """A guard that only knows the spellings already fixed guards nothing.

    The last three lines here are the ones the previous literal-substring form
    read straight past: a renamed variable, a call with an expression argument,
    and `.joinpath`. All three land in the same directory no reader opens, and
    two of the five original offenders WROTE to it.
    """
    assert _joins_contacts_too_high(line)


@pytest.mark.parametrize("line", [
    '    return get_per_exec_repo_path(slug) / "crm" / "contacts"',
    '        contacts_dir = repo_path / "crm" / "contacts"',
    '        contacts_dir = data_root / "crm" / "contacts"',
    '    # historical note: repo_path / "contacts" was the bug',
    '        staging_dir = staging / "contacts"',
])
def test_the_matcher_leaves_the_correct_layout_alone(line):
    """The other direction. A guard that fires on the fix teaches people to
    delete it, after which it proves nothing while looking as though it does."""
    assert not _joins_contacts_too_high(line)


def test_no_caller_joins_contacts_onto_the_repo_root():
    """The tree scan. Two of the five 2026-08-23 offenders wrote to that path."""
    scripts = sorted((ROOT / "scripts").rglob("*.py"))
    # An empty offender list is green over zero files, so a renamed scripts/
    # directory or a changed suffix would switch this guard off in silence.
    # Measured 386 files on 2026-09-01; the floor only catches a collapse.
    assert len(scripts) >= 220, f"the scan collapsed to {len(scripts)} files"
    # A SCAN: a script that vanished between the rglob and the read joins
    # nothing onto the repo root, so skipping it is the right answer and
    # `read_sources` warns naming it. `errors="replace"` is kept, so a decode
    # failure - a file that IS there - still raises. The floor above counts the
    # walk; the one below counts what was actually opened.
    vanished: list[Path] = []
    offenders = []
    read_count = 0
    for script, text in read_sources(scripts, vanished, errors="replace"):
        read_count += 1
        for n, line in enumerate(text.splitlines(), 1):
            if _joins_contacts_too_high(line):
                offenders.append(f"{script.relative_to(ROOT)}:{n}: {line.strip()[:90]}")
    assert read_count >= 220, (
        f"only {read_count} of {len(scripts)} files were read "
        f"({len(vanished)} vanished mid-walk: {vanished})")
    assert offenders == [], (
        "join through get_per_exec_contacts_dir(slug) instead:\n  "
        + "\n  ".join(offenders)
    )


def test_the_live_fleet_is_visible_through_the_helper():
    """Against the real overlay when one is present: the count must be non-zero
    for at least one exec, or the helper is pointing somewhere empty again."""
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
