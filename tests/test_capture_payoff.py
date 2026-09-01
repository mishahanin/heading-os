"""Tests for R10 -- the daily /zk capture-payoff dashboard panel.

Loads the kebab-case generator via importlib (same pattern as
test_memory_index_ranking.py) and exercises collect_capture_payoff /
build_capture_payoff against synthetic brains. Covers: graceful degradation
when no Odin brain (exec workspace), the 7-day signal window, the promote
signal, and the panel hiding itself when unavailable.
"""

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
SCRIPT = WORKSPACE / "scripts" / "generate-dashboard.py"
sys.path.insert(0, str(WORKSPACE))


def _load():
    spec = importlib.util.spec_from_file_location("dashboard_gen_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _load()


def _note(path: Path, slug: str, day: str):
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{slug}.md").write_text(
        f'---\nid: "1"\ntitle: "{slug}"\ntype: episode\nupdated: {day}\n---\n\nbody\n',
        encoding="utf-8",
    )


def test_no_brain_degrades(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "odin_brain_dir", lambda p=tmp_path / "nope" / "odin-brain": p)
    monkeypatch.setattr(mod, "knowledge_dir", lambda p=tmp_path / "nope": p)
    payoff = mod.collect_capture_payoff()
    assert payoff == {"available": False}
    # the panel hides entirely
    assert mod.build_capture_payoff(payoff) == ""


def test_counts_signals_in_7d_window(mod, tmp_path, monkeypatch):
    knowledge = tmp_path / "knowledge"
    brain = knowledge / "odin-brain"
    today = date(2026, 6, 8)
    recent = (today - timedelta(days=2)).isoformat()
    old = (today - timedelta(days=30)).isoformat()
    _note(brain / "episodes", "recent-episode-a", recent)
    _note(brain / "episodes", "recent-episode-b", recent)
    _note(brain / "principles", "old-principle", old)
    _note(knowledge / "signal", "recent-signal", recent)

    monkeypatch.setattr(mod, "odin_brain_dir", lambda p=brain: p)
    monkeypatch.setattr(mod, "knowledge_dir", lambda p=knowledge: p)
    monkeypatch.setattr(mod, "ODIN_CADENCE_SCRIPT", tmp_path / "no-cadence.py")
    monkeypatch.setattr(mod, "TODAY", today)

    payoff = mod.collect_capture_payoff()
    assert payoff["available"] is True
    assert payoff["signals_week"] == 3       # two episodes + one zk signal, NOT the 30-day-old one
    assert payoff["promote_ready"] is None    # cadence script absent -> None, not a crash

    html = mod.build_capture_payoff(payoff)
    assert "Capture Payoff" in html
    assert "Signals Captured (7d)" in html
    assert ">3<" in html


def _brain(mod, monkeypatch, tmp_path, today):
    """A knowledge tree with the cadence helper absent and the clock pinned."""
    knowledge = tmp_path / "knowledge"
    brain = knowledge / "odin-brain"
    monkeypatch.setattr(mod, "odin_brain_dir", lambda p=brain: p)
    monkeypatch.setattr(mod, "knowledge_dir", lambda p=knowledge: p)
    monkeypatch.setattr(mod, "ODIN_CADENCE_SCRIPT", tmp_path / "no-cadence.py")
    monkeypatch.setattr(mod, "TODAY", today)
    return knowledge, brain


def _dated(path: Path, slug: str, **dates: str):
    """A note carrying whichever date fields the caller names, in that order."""
    path.mkdir(parents=True, exist_ok=True)
    front = "".join(f"{key}: {value}\n" for key, value in dates.items())
    (path / f"{slug}.md").write_text(
        f'---\nid: "1"\ntitle: "{slug}"\ntype: episode\n{front}---\n\nbody\n',
        encoding="utf-8",
    )


def test_the_date_fields_are_a_fallback_chain_not_a_first_hit_verdict(
    mod, tmp_path, monkeypatch
):
    """The one number this panel exists to report, and nothing measured it.

    `_recent` walks `("updated", "created", "date", "ingested")` and the loop is
    a FALLBACK CHAIN: the first field present that reads as recent wins, and an
    old one does not end the search. The code carries a comment saying it once
    behaved as a first-hit verdict, so a note captured THIS WEEK from an old
    source was missing from "Signals Captured (7d)".

    MEASURED 2026-09-01: rewriting the body back to `return frontmatter_date(val)
    >= cutoff` -- exactly the verdict shape the comment describes -- left all
    three tests in this file green, because every fixture here carried a single
    `updated:` field and no fixture could tell a chain from a verdict.

    The note below is the ordinary case, not a contrived one: a signal
    distilled today out of a source published in April carries the source's
    `date:` and its own `ingested:`.
    """
    today = date(2026, 6, 8)
    _knowledge, brain = _brain(mod, monkeypatch, tmp_path, today)
    _dated(brain / "episodes", "distilled-today",
           date=(today - timedelta(days=60)).isoformat(),
           ingested=(today - timedelta(days=1)).isoformat())

    payoff = mod.collect_capture_payoff()

    assert payoff["signals_week"] == 1, (
        "an old `date:` ended the search before `ingested:` was read, so a note "
        "captured this week was dropped from the only number the panel reports")


def test_a_note_whose_every_date_is_broken_is_not_counted_and_says_so(
    mod, tmp_path, monkeypatch, capsys
):
    """Said, not swallowed: an undercount that looks measured is the failure.

    The panel prints a number the CEO reads as "signals captured". A note the
    reader could not date is not counted, so the line naming it on stderr is the
    only thing separating a quiet week from a corpus this reader cannot parse.
    Nothing asserted that line existed.

    `"2026-05-25garbage"` is the specific shape the module records as MEASURED:
    a blind ten-character slice read it as a real date and counted the note as a
    signal captured this week. Through the shared coercion it raises, so the
    note is dropped AND named.
    """
    today = date(2026, 6, 8)
    _knowledge, brain = _brain(mod, monkeypatch, tmp_path, today)
    _dated(brain / "episodes", "broken-date", updated="2026-05-25garbage")
    _dated(brain / "episodes", "good-date",
           updated=(today - timedelta(days=1)).isoformat())

    payoff = mod.collect_capture_payoff()

    assert payoff["signals_week"] == 1, "an unparseable date was read as a date"
    err = capsys.readouterr().err
    assert "broken-date.md" in err, err
    assert "not counted as a captured signal" in err, err
    assert "good-date.md" not in err, "a note that dated fine was reported anyway"


def test_excludes_index_and_template_files(mod, tmp_path, monkeypatch):
    knowledge = tmp_path / "knowledge"
    brain = knowledge / "odin-brain"
    today = date(2026, 6, 8)
    recent = (today - timedelta(days=1)).isoformat()
    _note(brain, "INDEX", recent)          # must be ignored
    _note(brain, "templates", recent)      # must be ignored
    _note(brain / "episodes", "real-note", recent)

    monkeypatch.setattr(mod, "odin_brain_dir", lambda p=brain: p)
    monkeypatch.setattr(mod, "knowledge_dir", lambda p=knowledge: p)
    monkeypatch.setattr(mod, "ODIN_CADENCE_SCRIPT", tmp_path / "no-cadence.py")
    monkeypatch.setattr(mod, "TODAY", today)

    payoff = mod.collect_capture_payoff()
    assert payoff["signals_week"] == 1
