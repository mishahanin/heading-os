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
    feed(monkeypatch, "что мы решили по Омеге и почему")
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
    feed(monkeypatch, "что мы решили по Омеге и почему")
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
    feed(monkeypatch, "что мы решили по Омеге и почему")

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
    feed(monkeypatch, "что мы решили по Омеге и почему")
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
    feed(monkeypatch, "что мы решили по Омеге и почему")

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
    feed(monkeypatch, "что мы решили по Омеге и почему")

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
    feed(monkeypatch, "что мы решили по Омеге и почему")
    monkeypatch.setattr(mod.subprocess, "run", fake_run({
        "hits": [{
            "path": "threads/business/2026-06-20-examplecorp-omega-demo.md",
            "title": "ExampleCorp Omega demo",
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
    assert "threads/business/2026-06-20-examplecorp-omega-demo.md" in ctx

    for leaked in ("score", "0.4642", "channels", "dense", "chunk", "chunks_total",
                   "classification", "ceo-only", "collection", "below_threshold"):
        assert leaked not in ctx, f"hit field leaked into the prompt: {leaked}"


def test_near_miss_block_is_capped_shorter_than_confident(monkeypatch, capsys):
    """A below-threshold lead gets a smaller budget than a confident hit.

    An unconfident result may be pure noise, so its cost has to be lower than
    the cost of a result that cleared the threshold: three pointers, not five.
    """
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеге и почему")
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
    feed(monkeypatch, "что мы решили по Омеге и почему")
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
    """`recall_inject.enabled: false` is honoured, as the hooks reference says.

    The tracker RECORDS before it raises, and the assertion is on the record.
    Until 2026-09-01 it only raised, and `main()` wraps the backend call in
    `except Exception -> _emit("")`, so the AssertionError was swallowed by the
    code under test and the hook fell silent for the wrong reason. MEASURED:
    both `if cfg is None or not cfg["enabled"]` reduced to `if cfg is None`, and
    the whole `cfg.update(...)` of the config block deleted, left this test
    green. A raise inside a fake is a message to a handler, not an assertion.
    """
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеге и почему")
    cfg = tmp_path / "memory-index.yaml"
    cfg.write_text("recall_inject:\n  enabled: false\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CONFIG_PATH", cfg)
    called = []

    def tracker(cmd, **kwargs):
        called.append(cmd)
        raise AssertionError("backend must not run when the hook is switched off")

    monkeypatch.setattr(mod.subprocess, "run", tracker)
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    assert called == [], "the backend ran with recall_inject.enabled: false"
    assert capsys.readouterr().out == ""


def test_a_missing_backend_stays_silent_and_never_spawns(monkeypatch, capsys, tmp_path):
    """No venv interpreter means no recall, and the hook says why on stderr.

    A fresh clone with no `.venv` is an ordinary state, not an error, and the
    hook must degrade to silence rather than hand `subprocess.run` a `None`
    interpreter. The branch had no test at all until 2026-09-01.
    """
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеге и почему")
    monkeypatch.setattr(mod, "INTERPRETERS", (tmp_path / "absent" / "bin" / "python",))
    called = []

    def tracker(cmd, **kwargs):
        called.append(cmd)
        raise AssertionError("no backend exists to invoke")

    monkeypatch.setattr(mod.subprocess, "run", tracker)
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert called == []
    assert captured.out == ""
    assert "recall backend missing" in captured.err


def test_touch_flag_reaches_the_backend_argv(monkeypatch, capsys):
    """`--touch` is the ONLY thing wiring retrieval to reinforcement.

    memory-index.py's `_should_touch` gate fires on `getattr(args, "touch",
    False)` and nothing else — this argv is the sole place that intent is
    expressed. Every other fake in this file (`fake_run`, `tracker`, `boom`,
    `garbled`, `slow`) ignores argv content entirely, so a careless edit, a bad
    merge, or a refactor of the argument list could drop this flag and every
    test above would stay green: the hook would keep answering prompts
    correctly while access_count silently froze on every memory in the
    workspace, reverting the whole feature to the dead state the plan exists
    to fix — invisibly, because nothing else here looks at what was actually
    passed to the backend. This is that assertion.
    """
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеге и почему")
    captured = {}

    def tracker(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout=_json.dumps({"hits": [], "gap": False}), stderr="")

    monkeypatch.setattr(mod.subprocess, "run", tracker)
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    cmd = [str(part) for part in captured["cmd"]]
    assert "--touch" in cmd, cmd

    # The rest of the argv contract, pinned in the one place that looks at it.
    # MEASURED 2026-09-01: dropping `--top-k` and dropping the `--` terminator
    # were each caught by nothing in this file.
    assert "--top-k" in cmd, cmd
    assert cmd[cmd.index("--top-k") + 1] == str(mod.TOP_K), cmd

    # `--` terminates option parsing, because `text` is a POSITIONAL argument of
    # memory-index.py's query command. Without it a prompt beginning with "-" is
    # read as an unknown option and the backend exits 2, silently. The prompt
    # must be the argument immediately after the terminator, and nothing else
    # may follow it.
    assert cmd[-2] == "--", cmd
    assert cmd[-1] == "что мы решили по Омеге и почему", cmd


def test_a_prompt_that_begins_with_a_dash_is_still_passed_as_text(monkeypatch, capsys):
    """The reason the `--` terminator above is not decoration.

    A prompt like "-- what did we decide" is a real thing to type. Without the
    terminator the backend's argparse rejects it and exits 2, and the hook logs
    "recall exited 2" and goes silent - a whole class of question that memory
    quietly never answers.
    """
    mod = load_hook()
    prompt = "-- почему мы отказались от Омеги и что решили"
    feed(monkeypatch, prompt)
    captured = {}

    def tracker(cmd, **kwargs):
        captured["cmd"] = [str(part) for part in cmd]
        return subprocess.CompletedProcess(
            cmd, 0, stdout=_json.dumps({"hits": [], "gap": False}), stderr="")

    monkeypatch.setattr(mod.subprocess, "run", tracker)
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    cmd = captured["cmd"]
    assert cmd[-1] == prompt, cmd
    assert cmd[cmd.index(prompt) - 1] == "--", cmd


def test_interpreter_probes_both_platform_layouts():
    """POSIX puts the venv interpreter in bin/, Windows in Scripts/python.exe.

    Assuming the POSIX layout made the hook permanently silent under the shipped
    windows settings template.
    """
    mod = load_hook()
    names = {p.parent.name + "/" + p.name for p in mod.INTERPRETERS}
    assert names == {"bin/python", "Scripts/python.exe"}, names


# --- the fallback-embedder alert -------------------------------------------

def test_a_fallback_embedder_is_announced_in_the_injected_context(monkeypatch, capsys):
    """The backend's red banner goes to stderr, which this hook discards on a
    zero exit. Without this the session -- the surface Misha reads all day --
    would never learn the pinned Windows GPU host was asleep.

    Operator directive, 2026-08-21: say it at once, loudly.
    """
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеге и почему")
    monkeypatch.setattr(mod.subprocess, "run", fake_run({
        "hits": [{"path": "knowledge/x.md", "title": "X", "layer": "odin"}],
        "gap": False,
        "embed_fallback": {"wanted": "auto:11436", "got": "http://localhost:11434"},
    }))
    with pytest.raises(SystemExit):
        mod.main()
    ctx = _json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "FALLBACK embedder" in ctx
    assert "auto:11436" in ctx and "http://localhost:11434" in ctx
    assert "Memory relevant to this message" in ctx   # the hits still arrive


def test_a_gap_on_the_fallback_embedder_still_announces_it(monkeypatch, capsys):
    """"Nothing found" and "found nothing because the host was asleep" read the
    same to the caller, and only the first is a gap. So a gap stays silent about
    memory and loud about the embedder."""
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеге и почему")
    monkeypatch.setattr(mod.subprocess, "run", fake_run({
        "hits": [], "gap": True,
        "embed_fallback": {"wanted": "auto:11436", "got": "http://localhost:11434"},
    }))
    with pytest.raises(SystemExit):
        mod.main()
    ctx = _json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "FALLBACK embedder" in ctx
    assert "Memory relevant to this message" not in ctx


def test_no_alert_when_the_pinned_host_answered(monkeypatch, capsys):
    """An alert on every prompt is an alert nobody reads."""
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеге и почему")
    monkeypatch.setattr(mod.subprocess, "run", fake_run({
        "hits": [{"path": "knowledge/x.md", "title": "X", "layer": "odin"}], "gap": False,
    }))
    with pytest.raises(SystemExit):
        mod.main()
    ctx = _json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "FALLBACK" not in ctx


def test_an_empty_index_is_still_valid_json_for_the_hook(monkeypatch, capsys):
    """`query --json` must emit JSON on EVERY exit, including the empty store.

    Until 2026-08-21 the empty-index path printed prose regardless of `--json`, so
    this hook's `json.loads` raised, it logged "unparseable JSON" and went silent.
    That degrades safely but BLINDLY: an empty index and a broken backend became
    the same observation. The fix is upstream, in `cmd_query`; this asserts the
    hook's half of the contract, that the documented payload parses and yields no
    context rather than an error.
    """
    mod = load_hook()
    feed(monkeypatch, "что мы решили по Омеге и почему")
    monkeypatch.setattr(mod.subprocess, "run",
                        fake_run({"hits": [], "gap": True, "empty_index": True}))
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == ""
