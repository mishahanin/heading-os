"""A thread in a deliberate quiet period must say so where rollups read it.

Before this, a freeze was prose only: `do_not_remind: true` sat in one thread's
frontmatter, nothing read it, and `write_thread_file` rebuilt frontmatter from a
fixed field list so the next `/thread log` would have deleted it outright.

The surface changed on 2026-08-27. The freeze used to have to reach a
`## Active Threads` block in MEMORY.md, and five tests here pinned the marker on
that index line. That index is retired, so the marker's one surface is now
`thread.py list`, which every rollup reads. The invariant did not move: a frozen
thread must not look like work that wants attention.

Governed by .claude/rules/memory-discipline.md.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from scripts.utils.threads_lib import (
    ThreadFile,
    is_quiet,
    parse_thread_file,
    scan_for_archive,
    write_thread_file,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _thread(**over) -> ThreadFile:
    base = {
        "id": "2026-05-20-example-alliance",
        "title": "Example alliance",
        "status": "active",
        "type": "business",
        "classification": "ceo-only",
        "opened": "2026-05-20",
        "last_touched": "2026-06-22",
        "links": {"crm": [], "pipeline": [], "outputs": [], "knowledge": []},
        "tags": [],
        "body": "# Example alliance\n\n## Log (newest first)\n",
    }
    base.update(over)
    return ThreadFile(**base)


@pytest.fixture()
def threads_root(tmp_path, monkeypatch):
    root = tmp_path / "threads"
    (root / "business").mkdir(parents=True)
    monkeypatch.setenv("THREADS_ROOT", str(root))
    # Pinned so no code path here can reach the operator's live overlay, whether
    # or not today's CLI tries to. See tests/test_thread_cli.py::threads_root.
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    return root


def _cli(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/thread.py", *argv],
        capture_output=True, text=True, check=False, cwd=REPO_ROOT,
    )


# --- frontmatter survives a rewrite -------------------------------------


def test_quiet_until_survives_a_write_read_round_trip(tmp_path):
    path = tmp_path / "2026-05-20-example-alliance.md"
    write_thread_file(path, _thread(quiet_until="2026-08-25"))
    assert parse_thread_file(path).quiet_until == "2026-08-25"


def test_a_thread_without_a_quiet_period_writes_no_field(tmp_path):
    path = tmp_path / "2026-05-20-example-alliance.md"
    write_thread_file(path, _thread())
    assert "quiet_until" not in path.read_text(encoding="utf-8")
    assert parse_thread_file(path).quiet_until is None


# --- the semantic --------------------------------------------------------


def test_is_quiet_is_true_up_to_and_including_the_lift_date():
    t = _thread(quiet_until="2026-08-25")
    assert is_quiet(t, date(2026, 8, 12)) is True
    assert is_quiet(t, date(2026, 8, 25)) is True


def test_is_quiet_is_false_after_the_lift_date_and_when_unset():
    assert is_quiet(_thread(quiet_until="2026-08-25"), date(2026, 8, 26)) is False
    assert is_quiet(_thread(), date(2026, 8, 12)) is False


def test_an_unparseable_lift_date_is_not_treated_as_quiet():
    """Fail toward surfacing: a broken date must not silence a thread forever."""
    assert is_quiet(_thread(quiet_until="soon"), date(2026, 8, 12)) is False


def test_an_indefinite_freeze_is_quiet_on_any_date():
    """Some freezes have no date -- they lift when the operator raises it."""
    t = _thread(do_not_remind=True)
    assert is_quiet(t, date(2026, 8, 12)) is True
    assert is_quiet(t, date(2030, 1, 1)) is True


def test_an_indefinite_freeze_survives_a_write_read_round_trip(tmp_path):
    """The field that carried the only existing freeze must not be rebuilt away."""
    path = tmp_path / "2026-05-20-example-alliance.md"
    write_thread_file(path, _thread(do_not_remind=True))
    assert parse_thread_file(path).do_not_remind is True
    assert "do_not_remind: true" in path.read_text(encoding="utf-8")


def test_an_unmodelled_frontmatter_key_survives_a_rewrite(tmp_path):
    """The general form of the bug: a rewrite must not delete what it cannot name."""
    path = tmp_path / "2026-05-20-example-alliance.md"
    write_thread_file(path, _thread())
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("tags: []\n", "tags: []\nfrozen: '2026-06-25'\n"), encoding="utf-8")

    t = parse_thread_file(path)
    assert t.extra["frozen"] == "2026-06-25"
    t.last_touched = "2026-08-12"
    write_thread_file(path, t)
    assert parse_thread_file(path).extra["frozen"] == "2026-06-25"


def test_an_indefinite_freeze_is_never_reported_as_expired(tmp_path):
    root = tmp_path / "threads"
    (root / "business").mkdir(parents=True)
    write_thread_file(
        root / "business" / "2026-05-20-example-alliance.md",
        _thread(last_touched="2026-06-01", do_not_remind=True),
    )
    actions = {c.action for c in scan_for_archive(root, today=date(2030, 1, 1))}
    assert actions == set()


# --- the surface a rollup reads: `thread.py list` ------------------------
#
# Five tests stood here against the retired `## Active Threads` index line.
# These ask the same question of the command that replaced it, which is what
# `/prime` reads (.claude/skills/prime/SKILL.md, step 1 of the panel).


def test_the_listing_marks_a_thread_that_is_still_quiet(threads_root):
    write_thread_file(
        threads_root / "business" / "2026-05-20-example-alliance.md",
        _thread(quiet_until="2999-01-01"),
    )
    out = _cli("list").stdout
    assert "Example alliance" in out
    assert "[quiet until 2999-01-01]" in out


def test_the_listing_carries_no_marker_once_the_quiet_has_lifted(threads_root):
    write_thread_file(
        threads_root / "business" / "2026-05-20-example-alliance.md",
        _thread(quiet_until="2000-01-01"),
    )
    out = _cli("list").stdout
    assert "Example alliance" in out
    assert "[quiet until" not in out


def test_setting_a_quiet_period_from_the_cli_reaches_the_listing(threads_root):
    write_thread_file(
        threads_root / "business" / "2026-05-20-example-alliance.md", _thread(),
    )
    assert "[quiet until" not in _cli("list").stdout
    r = _cli("quiet", "2026-05-20-example-alliance", "--until", "2999-01-01")
    assert r.returncode == 0, r.stderr
    assert "[quiet until 2999-01-01]" in _cli("list").stdout


def test_clearing_the_quiet_period_from_the_cli_drops_the_marker(threads_root):
    write_thread_file(
        threads_root / "business" / "2026-05-20-example-alliance.md",
        _thread(quiet_until="2999-01-01"),
    )
    r = _cli("quiet", "2026-05-20-example-alliance", "--clear")
    assert r.returncode == 0, r.stderr
    out = _cli("list").stdout
    assert "Example alliance" in out
    assert "[quiet until" not in out


def test_an_indefinite_freeze_is_marked_and_never_shows_a_none_date(threads_root):
    """`do_not_remind` has no date, so the suffix must not pretend to one.

    `is_quiet` is true for an indefinite freeze while `quiet_until` is None. The
    single-branch suffix interpolated that None and printed `[quiet until None]`
    on every indefinitely frozen thread, which `/prime` reads as the optional
    `[quiet until DATE]` suffix it documents.
    """
    write_thread_file(
        threads_root / "business" / "2026-05-20-example-alliance.md",
        _thread(do_not_remind=True),
    )
    out = _cli("list").stdout
    assert "Example alliance" in out
    assert "[quiet indefinitely]" in out
    assert "None" not in out


def test_a_thread_with_neither_freeze_gets_no_suffix_at_all(threads_root):
    write_thread_file(
        threads_root / "business" / "2026-05-20-example-alliance.md", _thread(),
    )
    out = _cli("list").stdout
    assert "Example alliance" in out
    assert "quiet" not in out


# --- the loop closes: an expired quiet gets surfaced --------------------


def test_the_hygiene_scan_reports_a_quiet_period_that_has_expired(tmp_path):
    root = tmp_path / "threads"
    (root / "business").mkdir(parents=True)
    write_thread_file(
        root / "business" / "2026-05-20-example-alliance.md",
        _thread(last_touched="2026-08-20", quiet_until="2026-08-25"),
    )
    actions = {c.action for c in scan_for_archive(root, today=date(2026, 8, 26))}
    assert "quiet-expired" in actions


def test_the_hygiene_scan_stays_silent_while_the_quiet_period_holds(tmp_path):
    root = tmp_path / "threads"
    (root / "business").mkdir(parents=True)
    write_thread_file(
        root / "business" / "2026-05-20-example-alliance.md",
        _thread(last_touched="2026-08-10", quiet_until="2026-08-25"),
    )
    actions = {c.action for c in scan_for_archive(root, today=date(2026, 8, 12))}
    assert "quiet-expired" not in actions


@pytest.mark.parametrize("today, expected", [
    (date(2026, 8, 24), False),   # the day before the lift
    (date(2026, 8, 25), False),   # the lift date itself: still quiet
    (date(2026, 8, 26), True),    # the first day after
])
def test_the_scan_turns_on_the_lift_date_and_not_a_day_early(tmp_path, today,
                                                             expected):
    """The two cases above sit 13 days either side of the lift, so a one-day
    slip in the scan's own comparison would pass both. The boundary is asked at
    the exact day here, on both sides of it."""
    root = tmp_path / "threads"
    (root / "business").mkdir(parents=True)
    write_thread_file(
        root / "business" / "2026-05-20-example-alliance.md",
        _thread(last_touched="2026-08-20", quiet_until="2026-08-25"),
    )
    actions = {c.action for c in scan_for_archive(root, today=today)}
    assert ("quiet-expired" in actions) is expected


def test_a_quiet_thread_is_not_nagged_as_stale(tmp_path):
    """The 60-day 'propose on-hold' nudge is exactly the noise a quiet suppresses."""
    root = tmp_path / "threads"
    (root / "business").mkdir(parents=True)
    write_thread_file(
        root / "business" / "2026-05-20-example-alliance.md",
        _thread(last_touched="2026-06-01", quiet_until="2026-08-25"),
    )
    actions = {c.action for c in scan_for_archive(root, today=date(2026, 8, 12))}
    assert "propose-on-hold" not in actions
