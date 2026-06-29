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
    monkeypatch.setattr(mod, "ODIN_BRAIN_DIR", tmp_path / "nope" / "odin-brain")
    monkeypatch.setattr(mod, "KNOWLEDGE_DIR", tmp_path / "nope")
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

    monkeypatch.setattr(mod, "ODIN_BRAIN_DIR", brain)
    monkeypatch.setattr(mod, "KNOWLEDGE_DIR", knowledge)
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


def test_excludes_index_and_template_files(mod, tmp_path, monkeypatch):
    knowledge = tmp_path / "knowledge"
    brain = knowledge / "odin-brain"
    today = date(2026, 6, 8)
    recent = (today - timedelta(days=1)).isoformat()
    _note(brain, "INDEX", recent)          # must be ignored
    _note(brain, "templates", recent)      # must be ignored
    _note(brain / "episodes", "real-note", recent)

    monkeypatch.setattr(mod, "ODIN_BRAIN_DIR", brain)
    monkeypatch.setattr(mod, "KNOWLEDGE_DIR", knowledge)
    monkeypatch.setattr(mod, "ODIN_CADENCE_SCRIPT", tmp_path / "no-cadence.py")
    monkeypatch.setattr(mod, "TODAY", today)

    payoff = mod.collect_capture_payoff()
    assert payoff["signals_week"] == 1
