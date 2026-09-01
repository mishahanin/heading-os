"""Documentation must name the corporate repo directory the code actually uses.

Found by the 2026-08-23 engine audit. `docs/EMERGENCY-PROCEDURES.md` opened its
fleet-wide-outage recovery with `cd ../31c-corporate`, a directory that does not
exist: the repo was renamed to `heading-os-corporate` and nine other files say
so. Step 1 of the runbook fails with "no such file or directory", on the page
people reach for when something is already broken.

The guard DERIVES the expected name from `scripts/setup.py:CORPORATE_REPO`
rather than restating it, so a future rename moves both sides together. A test
that hard-coded "heading-os-corporate" would be a second list to keep in sync,
which is the defect it is meant to prevent.

Scope, stated because the detector is narrow: this matches `<name>-corporate`
directory references in prose and shell blocks. It says nothing about
`31c-crm-central`, which is a DIFFERENT repository whose name is genuinely still
`31c-crm-central` (`scripts/bridge_daemon/sources/contacts.py:CRM_CENTRAL_DIRNAME`).
"""
from __future__ import annotations

import re
from pathlib import Path
from tests.repo_files import read_sources, tracked_paths

ROOT = Path(__file__).resolve().parent.parent

# A `<slug>-corporate` used as a DIRECTORY: either reached with `../` or
# written with a trailing slash. Nothing else qualifies.
#
# The first cut matched any `-corporate` substring and drowned in false
# positives: the skills `/publish-corporate`, `/promote-corporate`,
# `/rollback-corporate`, the script `sync-corporate.py`, the HTML anchor
# `#s-corporate-letter`, and the tail `os-corporate` of the canonical name
# itself. A detector whose output is mostly noise gets muted, so it is narrowed
# to the shape the defect actually took.
#
# A trailing slash alone is not enough either: `.claude/skills/publish-corporate/`
# is a skill folder, not the repo. So the two shapes are named explicitly --
# the sibling reached with `../`, and the dot-prefixed local clone.
_CORPORATE_REF = re.compile(
    r"\.\./([A-Za-z0-9][\w.\-]*-corporate)(?![\w.\-])"
    r"|(?<![\w/\-])\.([A-Za-z0-9][\w.\-]*-corporate)/"
)


def _refs(text: str) -> list[str]:
    return [a or b for a, b in _CORPORATE_REF.findall(text)]


def _canonical_name() -> str:
    src = (ROOT / "scripts" / "setup.py").read_text(encoding="utf-8")
    m = re.search(r'^CORPORATE_REPO\s*=\s*"([^"]+)"', src, re.M)
    assert m, "scripts/setup.py no longer defines CORPORATE_REPO; this guard is unanchored"
    return m.group(1)


CANONICAL = _canonical_name()


def _files() -> list[Path]:
    out = tracked_paths(("docs/*.md", "docs/*.html", "*.md", "reference/*.md",
                         ".claude/rules/*.md"))
    # The search index is generated FROM docs/*.html; checking it twice adds
    # noise without adding coverage.
    return [p for p in out if p.name != "search-index.json"]


def test_the_canonical_name_is_readable():
    assert CANONICAL.endswith("-corporate"), CANONICAL


def test_the_guard_matches_something():
    """A detector that finds no references passes every rename silently."""
    # A COUNT, so a quiet skip would report a number nobody measured. Read
    # through `read_sources` for the walk/read race, retry once, and fail naming
    # the file if it is genuinely gone rather than tally around it.
    files = _files()
    lost: list[Path] = []
    read = list(read_sources(files, lost, errors="ignore"))
    if lost:
        still_gone: list[Path] = []
        read += list(read_sources(lost, still_gone, errors="ignore"))
        assert not still_gone, (
            "file(s) disappeared between the walk and the read and are still "
            "gone on retry; the reference count would be short by an unknown "
            "amount: " + ", ".join(str(p) for p in still_gone))
    hits = sum(len(_refs(text)) for _p, text in read)
    assert hits >= 5, f"only {hits} corporate-repo references found; the pattern rotted"


def test_no_document_names_a_corporate_repo_the_code_does_not_use():
    bad: list[str] = []
    # A SCAN, unlike the count above: a document that vanished between the walk
    # and the read names no directory at all, so skipping it is the right
    # answer. `read_sources` warns naming it, and the count rides the assertion.
    vanished: list[Path] = []
    for path, text in read_sources(_files(), vanished, errors="ignore"):
        for n, line in enumerate(text.splitlines(), 1):
            for name in _refs(line):
                if name != CANONICAL:
                    rel = path.relative_to(ROOT)
                    bad.append(f"{rel}:{n}: {name!r} (code uses {CANONICAL!r})")
    assert not bad, (
        "documentation names a corporate repo directory the code does not use "
        f"({len(vanished)} file(s) vanished mid-walk):\n"
        + "\n".join(bad)
    )
