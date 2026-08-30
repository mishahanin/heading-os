#!/usr/bin/env python3
"""Shard scripts-03-p2: two clocks, an import above its own guard, two claims.

`capped_marker`'s docstring is exact about the guarantee it provides: "`<` is the
filter, so a cutoff EQUAL to the failed date leaves that session selectable while
everything genuinely older stays covered." That holds only if both dates come off
the same clock, and they did not. The marker is written from `_session_date`,
which reads the transcript's own ISO stamps — UTC days. `select_sessions`
compared it against `date.fromtimestamp(mtime)`, libc's LOCAL day.

On a host behind UTC, a session started just after midnight UTC has a local mtime
date one day EARLIER than its own UTC date. Fail its summarization, and the
marker is capped at that UTC date; every later run then reads `mday < cutoff` as
true and skips it. Chronicled never, reported never, `cmd_build` exiting 0 into a
nightly timer that shows a clean build. Exactly the orphan `capped_marker` exists
to prevent, arriving through the one thing its proof assumed.

`clip.py` imported `PIL` ABOVE `ensure_venv()` — the one third-party import in
the file, placed where the guard cannot help it, while `venv_guard`'s own
docstring says to call it "before the heavy third-party imports". Launched with a
bare interpreter the module died on ModuleNotFoundError before the re-exec into
the venv that has Pillow ever ran.

Two claims went with them. `summarize`'s docstring named a return shape the code
does not produce and omitted the `{"skip": True}` shape that `cmd_build` already
branches on. And `classify_files`'s docstring said an unclassified file "was
counted as CEO-only", which was never true of any code this repository has
carried: an unmatched path resolves `engine`, and `get_classification` collapses
everything that is not `private` to "corporate". The 2026-08-23 audit read that
sentence, reasoned from it, and reported a counting defect that does not exist —
which is what a wrong claim about the past costs.

Found by the 2026-08-23 engine audit, shard `scripts-03-p2`. Fixed 2026-08-24;
the classification finding is REFUTED here, by the test that measures it.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import chronicle as ch  # noqa: E402
from scripts.utils.workspace import get_classification, matched_routing_rule  # noqa: E402

# `time.tzset` is Unix-only, and the TZ environment variable it reads has no
# effect on Windows either, so the round-the-world loop below is a POSIX test.
# Without this it raised `AttributeError` on a platform this engine supports
# (`test_windows_still_uses_its_own_flag` in
# `test_a_label_that_names_the_opposite_metric.py` pins that support), which is
# a crash where a skip belongs.
needs_tzset = pytest.mark.skipif(
    not hasattr(time, "tzset"), reason="time.tzset is Unix-only")


# ---------------------------------------------------------------------------
# Finding 1 -- the marker and the filter, on one clock
# ---------------------------------------------------------------------------

def _session_written_at(tmp_path: Path, name: str, when: datetime) -> Path:
    f = tmp_path / f"{name}.jsonl"
    f.write_text("{}", encoding="utf-8")
    stamp = when.timestamp()
    os.utime(f, (stamp, stamp))
    return f


def _selected(monkeypatch, tmp_path, cutoff: str) -> list[str]:
    monkeypatch.setattr(ch, "already_chronicled", lambda sid: False)
    monkeypatch.setattr(ch, "read_marker", lambda: cutoff)
    return [p.stem for p in ch.select_sessions(tmp_path, None, False, 0)]


def test_a_session_is_never_excluded_by_its_own_high_water_mark(monkeypatch,
                                                                tmp_path):
    """The whole guarantee, at the boundary that broke it: a session last written
    just after midnight UTC, whose summarization failed, so the marker was capped
    at its own UTC date.

    DOCSTRING CORRECTED 2026-08-30. It used to say "a session whose only turns
    are just after midnight UTC", which described `_session_date` reading the
    transcript's own ISO stamps -- a path this test never touches.
    `select_sessions` compares the cutoff against
    `datetime.fromtimestamp(f.stat().st_mtime, timezone.utc)`, so MTIME is the
    clock under test and the empty `{}` body the fixture writes is correct
    rather than a gap. A reader following the old wording would have added
    stamped turns to the fixture and changed nothing.
    """
    just_after_midnight_utc = datetime(2026, 8, 21, 0, 30, tzinfo=timezone.utc)
    _session_written_at(tmp_path, "abc123", just_after_midnight_utc)
    assert "abc123" in _selected(monkeypatch, tmp_path, "2026-08-21"), (
        "the failed session is invisible to every later run: chronicled never, "
        "reported never, cmd_build exiting 0"
    )


@needs_tzset
def test_the_boundary_holds_from_either_side_of_utc(monkeypatch, tmp_path):
    """Not one host's zone. `date.fromtimestamp` follows TZ, so the defect
    appeared only west of UTC and this machine sits east of it — a test pinned
    to the local clock would have passed here while the fleet's other host
    orphaned sessions."""
    moment = datetime(2026, 8, 21, 0, 30, tzinfo=timezone.utc)
    _session_written_at(tmp_path, "abc123", moment)
    # Put the zone BACK, rather than deleting the variable. `delenv` + `tzset`
    # sends libc to /etc/localtime, which is the host's zone and not
    # necessarily the one the run was started in, and libc keeps that until
    # something calls `tzset` again. Monkeypatch restores `os.environ` at
    # teardown but never re-runs `tzset`, so the deletion leaked a zone into
    # every later test in the same xdist worker. MEASURED 2026-08-30 with the
    # suite started at `TZ=America/New_York`: after this file, `astimezone()`
    # answered +0400 and the calendar day it reported was 2026-08-30 while the
    # environment still said New York and 2026-08-29. Two tests in
    # `test_the_crm_migration_loses_nothing_and_leaves_nothing.py` went red on
    # that day-shift, on code nobody had touched. The sibling case in
    # `test_a_config_scalar_that_matched_every_sender.py` already gets this
    # right by calling `tzset` AFTER `monkeypatch.undo()`; undo cannot be used
    # inside this loop, because `_selected` sets attributes through the same
    # monkeypatch and undoing would drop them too.
    original_tz = os.environ.get("TZ")
    for tz in ("UTC", "America/New_York", "Asia/Dubai", "Pacific/Kiritimati"):
        monkeypatch.setenv("TZ", tz)
        time.tzset()
        try:
            assert "abc123" in _selected(monkeypatch, tmp_path, "2026-08-21"), tz
        finally:
            if original_tz is None:
                monkeypatch.delenv("TZ", raising=False)
            else:
                monkeypatch.setenv("TZ", original_tz)
            time.tzset()


def test_a_genuinely_older_session_is_still_covered(monkeypatch, tmp_path):
    """Anchor: a filter that selected everything would pass the two above, and
    would re-walk the whole history on every nightly run."""
    old = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    _session_written_at(tmp_path, "old999", old)
    assert _selected(monkeypatch, tmp_path, "2026-08-21") == []


def test_a_newer_session_is_selected(monkeypatch, tmp_path):
    newer = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    _session_written_at(tmp_path, "new777", newer)
    assert _selected(monkeypatch, tmp_path, "2026-08-21") == ["new777"]


def test_the_session_date_and_the_filter_read_the_same_clock(tmp_path):
    """The invariant underneath all of the above, stated directly: the mtime
    date the filter computes is never EARLIER than the date the marker would be
    written from, because mtime is the last write of a session whose first turn
    is what dates it."""
    moment = datetime(2026, 8, 21, 0, 30, tzinfo=timezone.utc)
    f = _session_written_at(tmp_path, "abc123", moment)
    env = {"started_at_utc": "", "user_turns": [], "assistant_turns": [],
           "system_reminders": []}
    sdate = ch._session_date(env, f)
    mday = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).date().isoformat()
    assert mday >= sdate


def test_a_backfill_still_ignores_the_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(ch, "already_chronicled", lambda sid: False)
    monkeypatch.setattr(ch, "read_marker", lambda: "2030-01-01")
    _session_written_at(tmp_path, "old999",
                        datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc))
    assert [p.stem for p in ch.select_sessions(tmp_path, None, True, 0)] == ["old999"]


def test_an_already_chronicled_session_is_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(ch, "already_chronicled", lambda sid: True)
    monkeypatch.setattr(ch, "read_marker", lambda: None)
    _session_written_at(tmp_path, "done111", datetime.now(timezone.utc))
    assert ch.select_sessions(tmp_path, None, False, 0) == []


def test_the_default_window_still_reaches_back(monkeypatch, tmp_path):
    """No marker: the cutoff is today minus the window, and a session inside it
    must be selected."""
    monkeypatch.setattr(ch, "already_chronicled", lambda sid: False)
    monkeypatch.setattr(ch, "read_marker", lambda: None)
    recent = datetime.now(timezone.utc) - timedelta(days=ch.DEFAULT_WINDOW_DAYS - 1)
    _session_written_at(tmp_path, "recent1", recent)
    assert [p.stem for p in ch.select_sessions(tmp_path, None, False, 0)] == ["recent1"]


def test_the_marker_is_never_raised_past_a_failure():
    """`capped_marker`'s own guarantee, unchanged and still pinned here: the
    clock fix is what makes it true, so it is worth asserting beside it."""
    assert ch.capped_marker("2026-08-25", ["2026-08-21"]) == "2026-08-21"
    assert ch.capped_marker("2026-08-25", []) == "2026-08-25"


# ---------------------------------------------------------------------------
# Finding 4 -- the third return shape
# ---------------------------------------------------------------------------

def test_the_summarize_docstring_names_every_shape_it_returns():
    doc = ch.summarize.__doc__
    assert '{"skip": True}' in doc, (
        "`cmd_build` already branches on this shape and the contract omitted it"
    )
    assert "None" in doc
    for key in ("gist", "topics", "personal", "reasoning", "considered", "open"):
        assert key in doc, f"the success shape carries {key} and the doc omits it"


def test_the_skip_shape_is_still_what_the_code_returns():
    """Guard the premise: a docstring describing a shape the code stopped
    producing is the same defect the other way round."""
    src = (ROOT / "scripts" / "chronicle.py").read_text(encoding="utf-8")
    body = src.split("def summarize")[1].split("\ndef ")[0]
    assert 'return {"skip": True}' in body
    assert '"reasoning": reasoning' in body


# ---------------------------------------------------------------------------
# Finding 3 -- an import above the guard that exists for it
# ---------------------------------------------------------------------------

def _clip_lines() -> list[str]:
    return (ROOT / "scripts" / "clip.py").read_text(encoding="utf-8").splitlines()


def test_the_venv_guard_runs_before_the_third_party_import():
    lines = _clip_lines()
    guard = next(i for i, ln in enumerate(lines) if ln.strip() == "ensure_venv()")
    pil = next(i for i, ln in enumerate(lines) if ln.startswith("from PIL import"))
    assert pil > guard, (
        "PIL is imported before the guard whose job is to re-exec into the venv "
        "that HAS PIL; a bare interpreter dies before the guard ever runs"
    )


def test_the_guard_is_still_called_at_all():
    """Anchor: deleting the call would satisfy an ordering assertion that only
    looked for the import's position."""
    lines = _clip_lines()
    assert any(ln.strip() == "ensure_venv()" for ln in lines)
    assert any(ln.startswith("from PIL import") for ln in lines)


def test_clip_still_imports_under_the_venv_interpreter():
    """The ordering must not have broken the module. Imported by path, because
    `scripts/clip.py` is kebab-cased for the CLI."""
    spec = importlib.util.spec_from_file_location("p03p2_clip",
                                                  ROOT / "scripts" / "clip.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "ImageGrab")


# ---------------------------------------------------------------------------
# Finding 2 -- REFUTED, and pinned so the claim cannot drift back
# ---------------------------------------------------------------------------

def test_an_unclassified_path_is_counted_corporate_not_ceo_only():
    """The audit reported that a file with no routing rule lands in `ceo_only`.
    It does not, and never did: the map default is `engine`, and
    `get_classification` collapses everything that is not `private` to
    "corporate". Measured, not read."""
    path = "scripts/a-path-no-rule-will-ever-match-zzq.py"
    assert matched_routing_rule(path) is None
    assert get_classification(path) == "corporate"


def test_a_private_path_is_still_ceo_only():
    """Anchor: a collapse that answered "corporate" for everything would pass
    the test above and would be a leak, not a counting quirk."""
    assert get_classification("outputs/anything.md") == "ceo-only"
    assert get_classification("crm/contacts/someone.md") == "ceo-only"
