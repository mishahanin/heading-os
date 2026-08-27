#!/usr/bin/env python3
"""The active-threads panel, restored from the record instead of from a copy.

The `## Active Threads` block in the auto-memory index put the running set in
front of the operator at every session start, and it was retired on 2026-08-27
because it was a stale COPY: on its last day it listed 3 threads against 33
active on disk, and every row quoted a live status and a live date, which
`.claude/rules/memory-discipline.md` forbids in an always-loaded index.

What went with it was passive awareness. The operator named the gap in one line:
threads are visible only from `/prime` or when they ask. So the panel is back, as
a computation over the thread FILES at every session start. It cannot go stale
because there is nothing to keep in sync, and it writes nothing.

Two properties matter more than the formatting, and both are asserted here:

* A quiet thread is never shown. This panel is the definition of proactive
  surfacing, so it is the first place that rule binds.
* Every drop is named. A panel that shows 12 of 32 and says only "12 threads"
  reads as "that is all of them", which is the exact failure the retired block
  committed for months.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.workspace import get_default_tz  # noqa: E402

HOOK = ROOT / ".claude" / "hooks" / "session-start.py"


def _today():
    """The date the HOOK computes, not the one this host happens to show.

    The panel resolves today through `get_default_tz()`. A fixture built on the
    stdlib `date.today` disagrees with it for part of every day, which is a test
    that reads the host clock rather than the code.
    """
    return datetime.now(get_default_tz()).date()


@pytest.fixture(scope="module")
def hook():
    spec = importlib.util.spec_from_file_location("session_start_panel", HOOK)
    module = importlib.util.module_from_spec(spec)
    sys.modules["session_start_panel"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def threads(tmp_path, monkeypatch):
    root = tmp_path / "threads"
    (root / "business").mkdir(parents=True)
    (root / "personal").mkdir(parents=True)
    monkeypatch.setenv("THREADS_ROOT", str(root))
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    return root


def _write(threads: Path, slug: str, *, status="active", days_ago=1,
           title=None, type_="business", quiet_until=None, do_not_remind=False,
           last_touched=None):
    if last_touched is None:
        last_touched = (_today() - timedelta(days=days_ago)).isoformat()
    body = [
        "---",
        f"id: {slug}",
        f"title: {title or slug}",
        f"status: {status}",
        f"type: {type_}",
        "classification: ceo-only",
        'opened: "2026-01-01"',
        f'last_touched: "{last_touched}"',
        "counterparties: []",
        "links: {}",
        "tags: []",
    ]
    if quiet_until:
        body.append(f'quiet_until: "{quiet_until}"')
    if do_not_remind:
        body.append("do_not_remind: true")
    body += ["---", "", "## Log", ""]
    path = threads / type_ / f"{slug}.md"
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


def _panel(hook, threads):
    lines, note = hook._thread_panel_lines(str(ROOT))
    assert note == "", f"panel reported it could not run: {note}"
    return lines


# ============================================================
# What the panel shows
# ============================================================

def test_an_active_thread_inside_the_window_is_shown(hook, threads):
    _write(threads, "2026-08-01-live-deal", title="A live deal", days_ago=3)
    lines = _panel(hook, threads)
    assert any("A live deal" in ln for ln in lines)
    assert any("(3d)" in ln for ln in lines)


def test_a_thread_touched_today_says_today(hook, threads):
    _write(threads, "2026-08-01-fresh", title="Fresh", days_ago=0)
    assert any("(today)" in ln for ln in _panel(hook, threads))


@pytest.mark.parametrize("status", ["closed", "on-hold"])
def test_a_retired_thread_is_never_shown(hook, threads, status):
    _write(threads, "2026-08-01-retired", title="Retired", status=status, days_ago=1)
    lines = _panel(hook, threads)
    assert not any("Retired" in ln for ln in lines)


def test_a_personal_thread_is_shown_with_its_type(hook, threads):
    _write(threads, "2026-08-01-family", title="Family matter", type_="personal")
    assert any("personal/2026-08-01-family" in ln for ln in _panel(hook, threads))


def test_an_empty_registry_produces_no_panel(hook, threads):
    assert _panel(hook, threads) == []


def test_a_missing_threads_directory_is_silent(hook, tmp_path, monkeypatch):
    """No registry is not a defect, so it earns neither a panel nor a note."""
    monkeypatch.setenv("THREADS_ROOT", str(tmp_path / "nowhere"))
    lines, note = hook._thread_panel_lines(str(ROOT))
    assert (lines, note) == ([], "")


# ============================================================
# The quiet rule binds here first
# ============================================================

def test_a_dated_quiet_thread_is_counted_and_not_shown(hook, threads):
    _write(threads, "2026-08-01-hushed", title="Hushed deal",
           quiet_until=(_today() + timedelta(days=30)).isoformat())
    lines = _panel(hook, threads)
    assert not any("Hushed deal" in ln for ln in lines)
    assert "1 quiet" in lines[0]


def test_an_indefinite_freeze_is_counted_and_not_shown(hook, threads):
    _write(threads, "2026-08-01-frozen", title="Frozen deal", do_not_remind=True)
    lines = _panel(hook, threads)
    assert not any("Frozen deal" in ln for ln in lines)
    assert "1 quiet" in lines[0]


def test_a_quiet_period_that_has_expired_is_shown_again(hook, threads):
    """A freeze that ran out must not silence the thread forever."""
    _write(threads, "2026-08-01-woken", title="Woken deal",
           quiet_until=(_today() - timedelta(days=1)).isoformat())
    assert any("Woken deal" in ln for ln in _panel(hook, threads))


# ============================================================
# Every drop is named
# ============================================================

def test_a_thread_outside_the_window_is_counted_as_older(hook, threads):
    _write(threads, "2026-01-01-dormant", title="Dormant",
           days_ago=hook.THREAD_PANEL_DAYS + 1)
    lines = _panel(hook, threads)
    assert not any("Dormant" in ln for ln in lines)
    assert "1 older" in lines[0]
    assert "1 active" in lines[0]


def test_a_thread_on_the_window_boundary_is_shown(hook, threads):
    """The bound needs a case ON the line, not only past it."""
    _write(threads, "2026-01-01-edge", title="Edge", days_ago=hook.THREAD_PANEL_DAYS)
    assert any("Edge" in ln for ln in _panel(hook, threads))


def test_the_row_cap_is_applied_and_stated(hook, threads):
    cap = hook.THREAD_PANEL_ROWS
    for i in range(cap + 3):
        _write(threads, f"2026-08-01-thread-{i:02d}", title=f"Thread {i:02d}",
               days_ago=1)
    lines = _panel(hook, threads)
    rows = [ln for ln in lines if ln.startswith("- ")]
    assert len(rows) == cap
    assert f"Showing {cap} of {cap + 3}" in lines[0], (
        f"the cap dropped {3} thread(s) without saying so: {lines[0]!r}")


def test_no_cap_notice_when_nothing_was_cut(hook, threads):
    _write(threads, "2026-08-01-only", title="Only one", days_ago=1)
    head = _panel(hook, threads)[0]
    assert "Showing the 1 touched" in head
    assert " of " not in head


def test_an_unreadable_thread_is_counted_not_swallowed(hook, threads):
    _write(threads, "2026-08-01-good", title="Good", days_ago=1)
    (threads / "business" / "2026-08-01-broken.md").write_text(
        "not frontmatter at all\n", encoding="utf-8")
    head = _panel(hook, threads)[0]
    assert "1 unreadable" in head


def test_an_unreadable_thread_is_named_not_only_counted(hook, threads):
    """A count says a file is broken. Only a name says which one to repair."""
    _write(threads, "2026-08-01-good", title="Good", days_ago=1)
    (threads / "business" / "2026-08-01-broken.md").write_text(
        "not frontmatter at all\n", encoding="utf-8")
    lines = _panel(hook, threads)
    named = [ln for ln in lines if ln.strip().startswith("Unreadable:")]
    assert len(named) == 1
    assert "2026-08-01-broken.md" in named[0]
    # The name alone says a file is broken. The reason says what to repair.
    assert "Error" in named[0] or "Exception" in named[0], (
        f"the panel named the file but not the failure: {named[0]!r}")


def test_the_named_list_of_unreadable_files_is_capped_and_says_how_many_more(
        hook, threads):
    cap = hook.THREAD_PANEL_UNREADABLE_NAMED
    for i in range(cap + 2):
        (threads / "business" / f"2026-08-01-broken-{i:02d}.md").write_text(
            "not frontmatter at all\n", encoding="utf-8")
    lines = _panel(hook, threads)
    named = [ln for ln in lines if ln.strip().startswith("Unreadable:")]
    assert len(named) == cap + 1
    assert f"and {2} more" in named[-1]
    assert f"{cap + 2} unreadable" in lines[0]


def test_a_broken_thread_file_does_not_hide_the_readable_ones(hook, threads):
    """One bad file must not cost the panel the threads it could parse."""
    (threads / "business" / "2026-08-01-broken.md").write_text(
        "not frontmatter at all\n", encoding="utf-8")
    _write(threads, "2026-08-01-good", title="Still here", days_ago=1)
    assert any("Still here" in ln for ln in _panel(hook, threads))


def test_the_parse_helper_returns_the_reason_rather_than_absorbing_it(hook, tmp_path):
    """The handler hands the failure back; the caller decides how to report it."""
    def _boom(_path):
        raise ValueError("bad frontmatter")

    thread, reason = hook._parse_thread_or_reason(_boom, tmp_path / "x.md")
    assert thread is None
    assert "ValueError" in reason
    assert "bad frontmatter" in reason


def test_the_panel_names_the_full_set_command(hook, threads):
    """Console-first: the panel is a convenience over a CLI it must point at."""
    _write(threads, "2026-08-01-one", title="One", days_ago=1)
    assert any("scripts/thread.py list" in ln for ln in _panel(hook, threads))


# ============================================================
# Ordering, and a date that will not parse
# ============================================================

def test_the_newest_thread_comes_first(hook, threads):
    _write(threads, "2026-08-01-old", title="Older one", days_ago=9)
    _write(threads, "2026-08-01-new", title="Newer one", days_ago=1)
    rows = [ln for ln in _panel(hook, threads) if ln.startswith("- ")]
    assert "Newer one" in rows[0]
    assert "Older one" in rows[1]


def test_an_unparseable_date_sorts_first_and_says_so(hook, threads):
    """A broken date is a defect to see, not a thread to bury under a cap."""
    _write(threads, "2026-08-01-fine", title="Fine", days_ago=1)
    _write(threads, "2026-08-01-broken-date", title="Broken date",
           last_touched="sometime")
    rows = [ln for ln in _panel(hook, threads) if ln.startswith("- ")]
    assert "Broken date" in rows[0]
    assert "(no date)" in rows[0]


# ============================================================
# It reads, and only reads
# ============================================================

def test_the_panel_writes_nothing(hook, threads, tmp_path):
    _write(threads, "2026-08-01-one", title="One", days_ago=1)
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    _panel(hook, threads)
    after = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


def test_a_resolver_that_raises_is_reported_not_swallowed(hook, threads, monkeypatch):
    """A panel that could not run must say so, never print an empty one.

    Silence about a check that did not run reads as "nothing to report", which is
    the failure this whole change exists to end (`.claude/rules/scope-claims.md`).
    """
    from scripts.utils import workspace

    def _boom():
        raise RuntimeError("no data root")

    monkeypatch.setattr(workspace, "get_threads_dir", _boom)
    lines, note = hook._thread_panel_lines(str(ROOT))
    assert lines == []
    assert "unavailable" in note
    assert "no data root" in note
