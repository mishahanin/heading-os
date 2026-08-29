#!/usr/bin/env python3
"""`docs/daemons.html` claimed "every scheduled task" and carried four of fourteen.

MEASURED 2026-08-29 against the tree the page describes:

    git ls-files 'scripts/install-*-timer.sh' | wc -l   ->  14
    installers named anywhere on docs/daemons.html      ->   4
    rows in the page's systemd-user timer table         ->   5

The four named were memory-index, memory-hygiene, odin-cadence and ops-radar.
Nine live installers were absent: archive-transcripts, chronicle,
council-models-check, dream-shadow, odin-propose, ollama-guard, reminders,
router-accuracy, update-manager. A tenth, memory-auto-retire, is deliberately
retired (its installer refuses with exit 9), and it was absent too, which reads
identically to an oversight.

The fifth table row was worse than a gap. `ollama-autoupdate.timer` appears in
the whole repository exactly once, on that page: the engine ships no template
for it and no installer, so the one row a reader could not act on was the row
for a unit that does not exist here. `grep -c "scheduled-tasks" docs/daemons.html`
also returned 0, so the page did not point at `reference/scheduled-tasks.md`,
where the session-scoped-versus-durable distinction lives.

The page was completed rather than narrowed: the daemons page is the only place
an operator looks for "what runs on a schedule here", and a page that documents
four of fourteen timers while a table in `scripts/templates/systemd/README.md`
lists all fourteen is a page that will be trusted and be wrong. A completeness
claim needs a mechanism behind it, which is the rest of this file.

Both directions are held, because each catches a different drift. A new
installer with no row is the measured defect. A row with no installer is how
`ollama-autoupdate.timer` survived, and it is also what a deleted installer
leaves behind.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import tracked_paths  # noqa: E402

PAGE = ROOT / "docs" / "daemons.html"

_INSTALLER_RE = re.compile(r"install-([a-z0-9-]+)-timer\.sh")
_UNIT_RE = re.compile(r"\b([a-z0-9]+(?:-[a-z0-9]+)*)\.timer\b")

# Unit stems the page may name without the engine shipping a template, because
# they are generic placeholders in a command example rather than real units.
# `systemctl --user list-timers <name>.timer` renders as `&lt;name&gt;.timer`,
# so "name" reaches the regex as a word.
PLACEHOLDER_UNITS = {"name"}


# ============================================================
# What the tree ships, and what the page says
# ============================================================

def shipped_installers() -> set[str]:
    """The timer-installer stem of every tracked `scripts/install-*-timer.sh`."""
    stems = set()
    for path in tracked_paths(("scripts/install-*-timer.sh",)):
        match = _INSTALLER_RE.fullmatch(path.name)
        assert match, f"{path.name} matched the glob but not the naming convention"
        stems.add(match.group(1))
    return stems


def shipped_units() -> set[str]:
    """The unit stem of every tracked `scripts/templates/systemd/*.timer`."""
    return {p.stem for p in tracked_paths(("scripts/templates/systemd/*.timer",))}


def named_installers(text: str) -> set[str]:
    return set(_INSTALLER_RE.findall(text))


def named_units(text: str) -> set[str]:
    return set(_UNIT_RE.findall(text)) - PLACEHOLDER_UNITS


# ============================================================
# The two predicates, pure so they can be measured on synthetic input
# ============================================================

def missing_from_page(shipped: set[str], named: set[str]) -> list[str]:
    """Everything the tree ships that the page never names."""
    return sorted(shipped - named)


def absent_from_tree(shipped: set[str], named: set[str]) -> list[str]:
    """Everything the page names that the tree does not ship."""
    return sorted(named - shipped)


_SYNTHETIC_SHIPPED = {"ops-radar", "chronicle", "reminders"}
_SYNTHETIC_COMPLETE = {"ops-radar", "chronicle", "reminders"}
_SYNTHETIC_PARTIAL = {"ops-radar"}
_SYNTHETIC_FICTION = {"ops-radar", "chronicle", "reminders", "ollama-autoupdate"}


def test_the_missing_rule_names_the_undocumented_installers_only():
    assert missing_from_page(_SYNTHETIC_SHIPPED, _SYNTHETIC_PARTIAL) == [
        "chronicle", "reminders"]


def test_the_missing_rule_is_silent_when_the_page_is_complete():
    """The other direction. A rule that always fires is as useless as one that
    never does, and only the pair of cases separates them."""
    assert missing_from_page(_SYNTHETIC_SHIPPED, _SYNTHETIC_COMPLETE) == []


def test_the_fiction_rule_names_a_row_with_no_installer_behind_it():
    """The shape that let `ollama-autoupdate.timer` sit in the table."""
    assert absent_from_tree(_SYNTHETIC_SHIPPED, _SYNTHETIC_FICTION) == [
        "ollama-autoupdate"]


def test_the_fiction_rule_is_silent_when_every_row_is_real():
    assert absent_from_tree(_SYNTHETIC_SHIPPED, _SYNTHETIC_COMPLETE) == []


def test_a_partial_page_trips_the_missing_rule_and_not_the_fiction_rule():
    """The measured state of the page on 2026-08-29, in miniature. The two rules
    must disagree on it, or one of them is redundant."""
    assert missing_from_page(_SYNTHETIC_SHIPPED, _SYNTHETIC_PARTIAL)
    assert absent_from_tree(_SYNTHETIC_SHIPPED, _SYNTHETIC_PARTIAL) == []


# ============================================================
# The live tree
# ============================================================

def test_every_shipped_timer_installer_is_on_the_daemons_page():
    text = PAGE.read_text(encoding="utf-8")
    missing = missing_from_page(shipped_installers(), named_installers(text))
    assert missing == [], (
        "docs/daemons.html claims to cover every scheduled task the engine ships "
        f"an installer for, and never names: {missing}"
    )


def test_the_daemons_page_names_no_installer_the_engine_does_not_ship():
    text = PAGE.read_text(encoding="utf-8")
    stale = absent_from_tree(shipped_installers(), named_installers(text))
    assert stale == [], (
        f"docs/daemons.html tells the operator to run installers that do not exist: {stale}"
    )


def test_every_shipped_timer_unit_is_on_the_daemons_page():
    """The installer names and the unit names drift independently: an installer
    can be renamed while its template keeps the old stem. Both are checked."""
    text = PAGE.read_text(encoding="utf-8")
    missing = missing_from_page(shipped_units(), named_units(text))
    assert missing == [], f"timer units shipped but never named on the page: {missing}"


def test_the_daemons_page_names_no_timer_unit_the_engine_does_not_ship():
    """This is the assertion `ollama-autoupdate.timer` failed. It was on the page,
    in a table of engine-installable units, and nowhere else in the repository."""
    text = PAGE.read_text(encoding="utf-8")
    stale = absent_from_tree(shipped_units(), named_units(text))
    assert stale == [], (
        "docs/daemons.html presents units the engine ships no template for, in a "
        f"table of units it installs: {stale}"
    )


def test_the_retired_timer_is_documented_as_retired_and_not_merely_listed():
    """memory-auto-retire ships an installer that refuses. Listing it beside the
    live timers with no marking would trade one wrong page for another."""
    text = PAGE.read_text(encoding="utf-8")
    assert "memory-auto-retire" in text
    row = next(line for line in text.splitlines() if "memory-auto-retire.timer" in line)
    assert "retired" in row.lower(), row
    assert "refuses" in row.lower(), row


def test_the_page_points_at_the_reference_that_carries_the_rest():
    """`reference/scheduled-tasks.md` holds the session-scoped-versus-durable
    distinction, and the page linked nothing to it (grep -c returned 0)."""
    text = PAGE.read_text(encoding="utf-8")
    assert "reference/scheduled-tasks.md" in text
    assert "scripts/templates/systemd/README.md" in text


# ============================================================
# The sweep reaches a real corpus
# ============================================================

def test_the_sweep_reaches_a_real_corpus():
    """Green over an empty corpus otherwise: every rule above compares two sets,
    and two empty sets agree. 14 installers and 14 unit templates on 2026-08-29.
    """
    installers = shipped_installers()
    units = shipped_units()
    assert len(installers) >= 14, f"only {len(installers)} timer installers found"
    assert len(units) >= 14, f"only {len(units)} timer templates found"
    assert "ops-radar" in installers and "ops-radar" in units

    text = PAGE.read_text(encoding="utf-8")
    assert len(text) > 5000, "the page read short; the sweep is measuring nothing"
    assert len(named_installers(text)) >= 14
    assert len(named_units(text)) >= 14
