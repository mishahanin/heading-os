"""UserPromptSubmit recall hook.

Surfaces memory relevant to what the CEO just typed, instead of the
date-ordered snapshot memory-inject.py emits at session start.

Guarantees under test:
  - a substantive prompt yields an additionalContext block naming the hit;
  - a failing / absent recall backend yields NOTHING and exit 0;
  - a timeout yields NOTHING and exit 0 (the cold-start case: ollama reloading
    the model was measured at 7.29s against 1.05s warm);
  - a trivially short prompt is skipped without spawning the backend at all.

Run: .venv/bin/python -m pytest tests/test_recall_inject.py
"""

import importlib.util
import json as _json
import subprocess
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
HOOK = WORKSPACE / ".claude" / "hooks" / "recall-inject.py"


def load_hook():
    sys.path.insert(0, str(WORKSPACE))
    spec = importlib.util.spec_from_file_location("recall_inject_mod", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def feed(monkeypatch, prompt: str):
    """Feed the hook a UserPromptSubmit payload on stdin."""
    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO(_json.dumps({"prompt": prompt})))


def fake_run(payload, *, returncode=0):
    """Build a subprocess.run stand-in returning a canned recall JSON."""
    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, returncode, stdout=_json.dumps(payload), stderr="")
    return _run


def test_substantive_prompt_emits_relevant_block(monkeypatch, capsys):
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеги и почему")
    monkeypatch.setattr(mod.subprocess, "run", fake_run({
        "hits": [{
            "path": "knowledge/odin-brain/episodes/20260803-omega-left-alone-tier1-prohibited.md",
            "title": "ExampleCorp's Omega opportunity left alone",
            "layer": "odin",
        }],
        "gap": False,
    }))
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    out = _json.loads(capsys.readouterr().out)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "omega-left-alone" in ctx
    assert "ExampleCorp's Omega opportunity left alone" in ctx


def test_near_miss_block_disclaims_relevance(monkeypatch, capsys):
    """A near-miss must NOT be presented as relevant memory.

    Measured 2026-08-07: absolute cosine does not separate answerable from
    unanswerable question-shaped queries, so a below-threshold result is a lead,
    not context. Presenting it as context trades a false "not in memory" for a
    false "here is your answer".
    """
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеги и почему")
    monkeypatch.setattr(mod.subprocess, "run", fake_run({
        "hits": [{"path": "knowledge/odin-brain/episodes/x.md",
                  "title": "Something", "layer": "odin"}],
        "gap": False,
        "near_miss": True,
    }))
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    ctx = _json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "NO confident match" in ctx, ctx
    assert "may be entirely irrelevant" in ctx, ctx
    assert "Memory relevant to this message" not in ctx, ctx


def test_gap_emits_nothing(monkeypatch, capsys):
    mod = load_hook()
    feed(monkeypatch, "нечто, чего в памяти заведомо нет")
    monkeypatch.setattr(mod.subprocess, "run", fake_run({"hits": [], "gap": True}))
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out == ""


def test_backend_failure_emits_nothing(monkeypatch, capsys):
    """Recall backend explodes -> hook stays silent and never blocks the prompt."""
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеги и почему")

    def boom(cmd, **kwargs):
        raise OSError("backend gone")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out == ""


def test_nonzero_returncode_emits_nothing(monkeypatch, capsys):
    """Recall backend exits nonzero -> hook stays silent and never blocks the prompt."""
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеги и почему")
    monkeypatch.setattr(mod.subprocess, "run", fake_run(
        {"hits": [], "gap": True}, returncode=1))
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out == ""


def test_unparseable_json_emits_nothing(monkeypatch, capsys):
    """Recall backend prints garbage on stdout -> hook stays silent, never blocks."""
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеги и почему")

    def garbled(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="not json{{{", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", garbled)
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out == ""


def test_timeout_emits_nothing(monkeypatch, capsys):
    """Cold ollama (measured 7.29s) must not hold the prompt hostage."""
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеги и почему")

    def slow(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, mod.TIMEOUT_SECONDS)

    monkeypatch.setattr(mod.subprocess, "run", slow)
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out == ""


def test_short_prompt_skips_backend_entirely(monkeypatch, capsys):
    """"да" must not cost a second of embed. The backend is never invoked."""
    mod = load_hook()
    feed(monkeypatch, "да")
    called = []

    def tracker(cmd, **kwargs):
        called.append(cmd)
        raise AssertionError("backend must not be invoked for a short prompt")

    monkeypatch.setattr(mod.subprocess, "run", tracker)
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    assert called == []
    assert capsys.readouterr().out == ""
