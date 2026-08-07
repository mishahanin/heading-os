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
    """The gap FLAG alone must silence the hook.

    The fixture carries a hit on purpose. With empty hits the assertion held
    whether or not the gap flag was read at all, so the test passed a mutation
    that deleted the flag check -- it was guarding nothing. Now the hit is the
    only thing that could produce output, so the silence is attributable.
    """
    mod = load_hook()
    feed(monkeypatch, "нечто, чего в памяти заведомо нет")
    monkeypatch.setattr(mod.subprocess, "run", fake_run({
        "hits": [{"path": "threads/business/x.md", "title": "X", "layer": "thread"}],
        "gap": True,
    }))
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out == ""


def test_no_hits_emits_nothing(monkeypatch, capsys):
    """No gap declared, but nothing came back -> nothing to point at."""
    mod = load_hook()
    feed(monkeypatch, "нечто, чего в памяти заведомо нет")
    monkeypatch.setattr(mod.subprocess, "run", fake_run({"hits": [], "gap": False}))
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
    """Recall backend exits nonzero -> hook stays silent and never blocks the prompt.

    The payload is deliberately a GOOD one: a real hit and no gap. The earlier
    fixture returned an empty gap result, so the hook fell silent down the gap
    branch and the test stayed green with the return-code check deleted. Here
    the return code is the only reason silence is possible.
    """
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеги и почему")
    monkeypatch.setattr(mod.subprocess, "run", fake_run({
        "hits": [{"path": "threads/business/x.md", "title": "X", "layer": "thread"}],
        "gap": False,
    }, returncode=1))
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


@pytest.mark.parametrize("filler", ["продолжай", "спасибо, давай дальше"])
def test_conversational_filler_skips_backend(monkeypatch, capsys, filler):
    """Measured fillers must cost nothing.

    Before the threshold moved to 25, "продолжай" (9 chars) produced 1067
    characters of pointers and "спасибо, давай дальше" (21) produced 975 under a
    confident "Memory relevant to this message" heading -- roughly 250 tokens
    and a backend round trip per conversational reply, presented as if the
    memory index had understood the question.
    """
    mod = load_hook()
    feed(monkeypatch, filler)
    called = []

    def tracker(cmd, **kwargs):
        called.append(cmd)
        raise AssertionError("backend must not be invoked for conversational filler")

    monkeypatch.setattr(mod.subprocess, "run", tracker)
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    assert called == []
    assert capsys.readouterr().out == ""


def test_emits_pointers_only_never_hit_internals(monkeypatch, capsys):
    """The block carries title, layer and path, and nothing else from the hit.

    A pointer is the entry to a record, not the record (memory-discipline.md).
    The recall backend returns scores, matched channels, chunk indices and a
    classification alongside each hit; dumping those into every prompt would be
    both noise and, for `classification`, a private field with no business in
    the model's context.
    """
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеги и почему")
    monkeypatch.setattr(mod.subprocess, "run", fake_run({
        "hits": [{
            "path": "threads/business/2026-06-20-examplecorp-region-demo.md",
            "title": "ExampleCorp (Region) ODUN.ONE demo",
            "layer": "thread",
            "score": 0.4642,
            "channels": ["dense"],
            "chunk": 4,
            "chunks_total": 12,
            "ntype": "business",
            "classification": "ceo-only",
            "collection": "content",
            "below_threshold": False,
        }],
        "gap": False,
    }))
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    ctx = _json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]

    assert "Memory relevant to this message" in ctx
    assert "thread" in ctx
    assert "threads/business/2026-06-20-examplecorp-region-demo.md" in ctx

    for leaked in ("score", "0.4642", "channels", "dense", "chunk", "chunks_total",
                   "classification", "ceo-only", "collection", "below_threshold"):
        assert leaked not in ctx, f"hit field leaked into the prompt: {leaked}"


def test_near_miss_block_is_capped_shorter_than_confident(monkeypatch, capsys):
    """A below-threshold lead gets a smaller budget than a confident hit.

    An unconfident result may be pure noise, so its cost has to be lower than
    the cost of a result that cleared the threshold: three pointers, not five.
    """
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеги и почему")
    monkeypatch.setattr(mod.subprocess, "run", fake_run({
        "hits": [{"path": f"threads/business/hit-{i}.md",
                  "title": f"Hit {i}", "layer": "thread"} for i in range(5)],
        "gap": False,
        "near_miss": True,
    }))
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    ctx = _json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    pointers = [ln for ln in ctx.splitlines() if ln.startswith("- [")]
    assert len(pointers) == mod.NEAR_MISS_MAX == 3, pointers
    assert "hit-3.md" not in ctx and "hit-4.md" not in ctx, ctx


def test_unreadable_config_stays_silent_and_never_runs(monkeypatch, capsys, tmp_path):
    """No readable config means no confirmation that the hook was switched on.

    The hook runs under the system `python3`, where `import yaml` may simply not
    resolve. Falling back to "enabled" there ignored `recall_inject.enabled:
    false` on precisely the machines that could not see it happening, while the
    docs promised the flag turned the hook off entirely. Fail closed instead:
    stay silent, log the reason, exit 0, and never reach the backend.
    """
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеги и почему")
    monkeypatch.setattr(mod, "CONFIG_PATH", tmp_path / "absent" / "memory-index.yaml")
    called = []

    def tracker(cmd, **kwargs):
        called.append(cmd)
        raise AssertionError("backend must not run when the switch is unconfirmed")

    monkeypatch.setattr(mod.subprocess, "run", tracker)
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert called == []
    assert "cannot confirm recall_inject.enabled" in captured.err


def test_disabled_flag_stays_silent(monkeypatch, capsys, tmp_path):
    """`recall_inject.enabled: false` is honoured, as the hooks reference says."""
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеги и почему")
    cfg = tmp_path / "memory-index.yaml"
    cfg.write_text("recall_inject:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CONFIG_PATH", cfg)

    def tracker(cmd, **kwargs):
        raise AssertionError("backend must not run when the hook is switched off")

    monkeypatch.setattr(mod.subprocess, "run", tracker)
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out == ""


def test_interpreter_probes_both_platform_layouts():
    """POSIX puts the venv interpreter in bin/, Windows in Scripts/python.exe.

    Assuming the POSIX layout made the hook permanently silent under the shipped
    windows settings template.
    """
    mod = load_hook()
    names = {p.parent.name + "/" + p.name for p in mod.INTERPRETERS}
    assert names == {"bin/python", "Scripts/python.exe"}, names
