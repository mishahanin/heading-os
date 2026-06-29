"""Tests for scripts/eval-flag.py - one-keystroke capture (R13).

Covers: the offline path stages a well-formed draft keyed by the env trace_id,
atomically, into evals/outcomes/_staged/ and NEVER into live evals/outcomes/ or
evals/cases/; defensive card-field mapping (a card with only a title still
stages a usable draft); --list enumeration; and the live-card path exits 2 with
a plain message when the daemon is unreachable.

eval-flag.py is kebab-case, so it is importlib-loaded by path.
"""
from __future__ import annotations

import importlib.util
import json
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def mod(tmp_path, monkeypatch):
    """Load eval-flag.py, then redirect its ROOT/SKILLS_DIR at a temp workspace
    so staging never touches the real skills tree."""
    path = ROOT / "scripts" / "eval-flag.py"
    spec = importlib.util.spec_from_file_location("eval_flag", str(path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "ROOT", tmp_path)
    monkeypatch.setattr(m, "SKILLS_DIR", tmp_path / ".claude" / "skills")
    return m


def _staged_files(mod, skill):
    return list((mod.SKILLS_DIR / skill / "evals" / "outcomes" / "_staged").glob("*.json"))


def test_offline_stages_draft_with_trace_id(mod, monkeypatch):
    monkeypatch.setenv("X31C_TRACE_ID", "abc123trace")
    rc = mod.cmd_offline("email-intel", "wrong contact logged", None, "outcome", as_json=True)
    assert rc == 0

    files = _staged_files(mod, "email-intel")
    assert len(files) == 1
    draft = json.loads(files[0].read_text(encoding="utf-8"))
    assert draft["status"] == "draft"
    assert draft["trace_id"] == "abc123trace"
    assert draft["description"] == "wrong contact logged"
    assert "outcome" in draft  # --type outcome default
    # No live cases/outcomes files were created - only the _staged/ draft.
    out_dir = mod.SKILLS_DIR / "email-intel" / "evals" / "outcomes"
    assert list(out_dir.glob("*.json")) == []  # top-level outcomes/ is untouched
    assert not (mod.SKILLS_DIR / "email-intel" / "evals" / "cases").exists()


def test_offline_atomic_no_tmp_left(mod, monkeypatch):
    monkeypatch.setenv("X31C_TRACE_ID", "t2")
    mod.cmd_offline("proposal", "bad render", None, "prose", as_json=True)
    staged_dir = mod.SKILLS_DIR / "proposal" / "evals" / "outcomes" / "_staged"
    assert list(staged_dir.glob("*.tmp")) == []
    draft = json.loads(_staged_files(mod, "proposal")[0].read_text(encoding="utf-8"))
    assert "checks" in draft  # --type prose


def test_card_path_defensive_mapping(mod, monkeypatch):
    """A note/alert card has only a title - no to/subject/draft_body. The draft
    must still be usable, built from the title."""
    monkeypatch.setattr(mod, "_read_state", lambda root, name: "x")
    card = {"id": "card12345", "title": "Pipeline drifted out of state",
            "action_type": "alert", "trace_id": "card-trace-9"}
    monkeypatch.setattr(mod, "_request", lambda *a, **k: {"items": [card]})

    rc = mod.cmd_from_card("card123", skill="email-intel", case_type="outcome", as_json=True)
    assert rc == 0
    draft = json.loads(_staged_files(mod, "email-intel")[0].read_text(encoding="utf-8"))
    assert draft["description"] == "Pipeline drifted out of state"
    assert "Pipeline drifted out of state" in draft["input"]
    assert draft["trace_id"] == "card-trace-9"
    assert draft["source"] == "action-queue-card:card12345"


def test_list_enumerates(mod, monkeypatch, capsys):
    monkeypatch.setenv("X31C_TRACE_ID", "t3")
    mod.cmd_offline("crm", "note one", None, "outcome", as_json=False)
    capsys.readouterr()  # drain the staging output so only cmd_list JSON remains
    out = mod.cmd_list(as_json=True)
    assert out == 0
    printed = capsys.readouterr().out
    drafts = json.loads(printed)
    assert any(d["skill"] == "crm" for d in drafts)


def test_card_path_daemon_down_exits_2(mod, monkeypatch):
    monkeypatch.setattr(mod, "_read_state", lambda root, name: "x")

    def _boom(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(mod.urllib.request, "urlopen", _boom)
    with pytest.raises(SystemExit) as exc:
        mod.cmd_from_card("anyid", skill=None, case_type="outcome", as_json=False)
    assert exc.value.code == 2
