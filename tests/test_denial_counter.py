"""The denied-attempt counter.

Promoted from `tests/contract/2026-08-01-denial-counter/` when the slice shipped
on 2026-08-02. It was this slice's frozen Canopus contract; a contract left in
place after its slice binds every later slice to that slice's behaviour
verbatim, so the coverage moves here and the frozen copy goes. Nothing was
dropped in the move: no other test in the suite held any of it.

The addition it covers (`docs/superpowers/specs/2026-08-01-canopus-v2-design.md`,
§6 A1):

    Every refusal, by any guard, appends one line: what was refused, by which
    mechanism, on which path.

Until this exists, "the guard is a successful deterrent" and "the guard is
pointless ceremony" produce the SAME observation — nothing counts a refusal —
and every subtraction candidate in §5 is a matter of taste.

Two properties carry the security weight and are asserted here, not left to
review:

1. A refusal record must never carry the refused CONTENT. A guard's reason text
   is written by the guard, and a future guard may interpolate the thing it
   caught; a counter that copies it verbatim would write credentials to disk on
   every catch — the exact defect class the 2026-07-31 slice fixed in the
   handoff pointer.
2. A logging failure must never turn a deny into an allow. The counter is
   telemetry; the block is the guarantee. If the log cannot be written, the
   write is still refused.

Every guard is driven AS PRODUCTION DRIVES IT — the PreToolUse hook through
`runpy.run_path(path, run_name="__main__")` in a subprocess with a JSON payload
on stdin, the scanner through its CLI. A guard tested by importing its data
is not tested.

Credential-shaped samples are assembled from fragments at runtime; this file
carries no whole credential-shaped literal.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_HOOK = _ROOT / ".claude" / "hooks" / "_dispatch.py"
_SCANNER = _ROOT / "scripts" / "secret-scanner.py"
_CLI = _ROOT / "scripts" / "denials.py"

# The production invocation of the PreToolUse hook (see
# tests/security/test_SEC_018_gate_behaviour.py for the settings.local.json
# form this reproduces). run_name="__main__" is the property that matters.
_RUNNER = "import sys,runpy;runpy.run_path(sys.argv[1], run_name='__main__')"

# A path no allow-list covers, so the scan actually runs.
_PROBE_PATH = "outputs/scratch/denial-counter-probe.txt"
_PROBE_NOTEBOOK = "outputs/scratch/denial-counter-probe.ipynb"


def _secret_sample() -> str:
    """One credential-shaped value the secret gate refuses, assembled at runtime."""
    return "sk-ant-" + ("A" * 16)


def _run_hook(payload: dict, log_root: Path, extra_env: dict | None = None):
    """Drive the hook as production drives it, with the log redirected."""
    env = dict(os.environ)
    env["WORKSPACE_LOG_DIR"] = str(log_root)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-c", _RUNNER, str(_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(_ROOT),
        env=env,
        timeout=120,
    )
    return proc


def _decision(proc) -> dict:
    assert proc.returncode == 0, f"hook exited {proc.returncode}; stderr:\n{proc.stderr}"
    if not proc.stdout.strip():
        return {}
    return json.loads(proc.stdout)


def _blocked(decision: dict) -> bool:
    if decision.get("decision") == "block":
        return True
    hook_out = decision.get("hookSpecificOutput") or {}
    return hook_out.get("permissionDecision") == "deny"


def _records(log_root: Path) -> list:
    path = log_root / "denials" / "denials.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------

def test_log_denial_appends_exactly_one_line(tmp_path, monkeypatch):
    from scripts.utils.denial_log import log_denial

    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path))
    log_denial(mechanism="check_prevent_secrets", action="Write",
               path="outputs/x.txt", reason="test reason")
    log_denial(mechanism="check_protect_docs", action="Edit",
               path="docs/y.md", reason="test reason")
    assert len(_records(tmp_path)) == 2


def test_a_record_names_the_mechanism_the_action_and_the_path(tmp_path, monkeypatch):
    from scripts.utils.denial_log import log_denial

    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path))
    log_denial(mechanism="check_prevent_secrets", action="Write",
               path="outputs/x.txt", reason="a reason")
    record = _records(tmp_path)[0]
    assert record["mechanism"] == "check_prevent_secrets"
    assert record["action"] == "Write"
    assert record["path"] == "outputs/x.txt"
    assert isinstance(record["ts"], (int, float))


def test_a_credential_in_the_reason_never_reaches_the_log(tmp_path, monkeypatch):
    """Property 1. The guard writes the reason; the counter must not trust it."""
    from scripts.utils.denial_log import log_denial

    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path))
    secret = _secret_sample()
    log_denial(mechanism="check_prevent_secrets", action="Write",
               path="outputs/x.txt", reason=f"BLOCKED: found {secret} in content")
    raw = (tmp_path / "denials" / "denials.jsonl").read_text(encoding="utf-8")
    assert secret not in raw


def test_a_credential_in_the_path_never_reaches_the_log(tmp_path, monkeypatch):
    """A path is attacker-influenced too: a filename can carry a token."""
    from scripts.utils.denial_log import log_denial

    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path))
    secret = _secret_sample()
    log_denial(mechanism="check_prevent_secrets", action="Write",
               path=f"outputs/{secret}.txt", reason="a reason")
    raw = (tmp_path / "denials" / "denials.jsonl").read_text(encoding="utf-8")
    assert secret not in raw


def test_a_long_reason_is_truncated(tmp_path, monkeypatch):
    from scripts.utils.denial_log import log_denial

    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path))
    log_denial(mechanism="m", action="Write", path="p", reason="x" * 5000)
    record = _records(tmp_path)[0]
    assert len(record["reason"]) <= 512


def test_a_failed_write_neither_raises_nor_poisons_the_next_one(tmp_path, monkeypatch):
    from scripts.utils.denial_log import log_denial

    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(blocker))
    assert log_denial(mechanism="m", action="Write", path="p", reason="r") is False
    good = tmp_path / "good"
    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(good))
    assert log_denial(mechanism="m", action="Write", path="p", reason="r") is True
    assert len(_records(good)) == 1


def test_a_write_failure_is_reported_on_stderr_not_swallowed(tmp_path, capsys, monkeypatch):
    """No silent swallow — the workspace's exception rule applies to telemetry too."""
    from scripts.utils.denial_log import log_denial

    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(blocker))
    log_denial(mechanism="m", action="Write", path="p", reason="r")
    assert "denial-log" in capsys.readouterr().err


def test_the_invocation_context_is_recorded_when_the_env_names_one(tmp_path, monkeypatch):
    """A scanner refusal during a push and during a commit are different events."""
    from scripts.utils.denial_log import log_denial

    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("HEADING_OS_DENIAL_CONTEXT", "push")
    log_denial(mechanism="secret-scanner", action="scan", path="a.md", reason="r")
    assert _records(tmp_path)[0]["context"] == "push"


def test_the_log_lives_under_the_gitignored_logs_directory(tmp_path, monkeypatch):
    """Denial records name real paths, and the engine repo is public."""
    from scripts.utils.denial_log import denial_log_path

    monkeypatch.delenv("WORKSPACE_LOG_DIR", raising=False)
    path = denial_log_path()
    assert ".logs/denials/denials.jsonl" in path.as_posix()
    result = subprocess.run(["git", "check-ignore", "-q", str(path)],
                            cwd=str(_ROOT), capture_output=True)
    assert result.returncode == 0, f"{path} is NOT gitignored"


def test_read_and_summarize_return_counts_per_mechanism(tmp_path, monkeypatch):
    from scripts.utils.denial_log import log_denial, read_denials, summarize

    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path))
    log_denial(mechanism="a", action="Write", path="p", reason="r")
    log_denial(mechanism="a", action="Write", path="p", reason="r")
    log_denial(mechanism="b", action="Edit", path="p", reason="r")
    assert summarize(read_denials()) == {"a": 2, "b": 1}


def test_a_corrupt_line_does_not_lose_the_rest_of_the_log(tmp_path, monkeypatch):
    from scripts.utils.denial_log import log_denial, read_denials

    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path))
    log_denial(mechanism="a", action="Write", path="p", reason="r")
    path = tmp_path / "denials" / "denials.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    log_denial(mechanism="b", action="Write", path="p", reason="r")
    assert {r["mechanism"] for r in read_denials()} == {"a", "b"}


# ---------------------------------------------------------------------------
# The live PreToolUse gate — every write shape production registers
# ---------------------------------------------------------------------------

def _payload_for(tool: str, sample: str) -> dict:
    if tool == "Write":
        return {"tool_name": "Write",
                "tool_input": {"file_path": _PROBE_PATH, "content": "k = " + repr(sample)}}
    if tool == "Edit":
        return {"tool_name": "Edit",
                "tool_input": {"file_path": _PROBE_PATH, "old_string": "a",
                               "new_string": "k = " + repr(sample)}}
    if tool == "MultiEdit":
        return {"tool_name": "MultiEdit",
                "tool_input": {"file_path": _PROBE_PATH,
                               "edits": [{"old_string": "a", "new_string": "k = " + repr(sample)}]}}
    if tool == "NotebookEdit":
        return {"tool_name": "NotebookEdit",
                "tool_input": {"notebook_path": _PROBE_NOTEBOOK,
                               "new_source": "k = " + repr(sample)}}
    if tool == "Bash":
        return {"tool_name": "Bash",
                "tool_input": {"command": "export K=" + sample}}
    raise AssertionError(tool)


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"])
def test_a_refused_write_is_counted_for_every_tool_shape(tool, tmp_path):
    proc = _run_hook(_payload_for(tool, _secret_sample()), tmp_path)
    assert _blocked(_decision(proc)), f"{tool} was not blocked"
    records = _records(tmp_path)
    assert len(records) == 1, f"{tool}: expected 1 record, got {records}"
    assert records[0]["mechanism"] == "check_prevent_secrets"
    assert records[0]["action"] == tool


def test_the_blocked_content_never_reaches_the_log_through_the_live_gate(tmp_path):
    """Property 1, end to end: the real guard's real reason text, real writer."""
    sample = _secret_sample()
    proc = _run_hook(_payload_for("Write", sample), tmp_path)
    assert _blocked(_decision(proc))
    raw = (tmp_path / "denials" / "denials.jsonl").read_text(encoding="utf-8")
    assert sample not in raw


def test_a_policy_deny_is_counted_with_its_own_mechanism(tmp_path):
    """The personal-threads deny renders through the OTHER terminal branch."""
    payload = {"tool_name": "Bash",
               "tool_input": {"command": "cp threads/personal/a.md /tmp/b.md"}}
    proc = _run_hook(payload, tmp_path)
    assert _blocked(_decision(proc))
    records = _records(tmp_path)
    assert len(records) == 1
    assert records[0]["mechanism"] == "check_protect_personal_threads"


def test_an_allowed_call_is_not_counted(tmp_path):
    """The counter measures refusals. Counting every call makes it useless."""
    payload = {"tool_name": "Write",
               "tool_input": {"file_path": _PROBE_PATH, "content": "nothing to see"}}
    proc = _run_hook(payload, tmp_path)
    assert not _blocked(_decision(proc))
    assert _records(tmp_path) == []


def test_a_broken_log_destination_does_not_turn_a_deny_into_an_allow(tmp_path):
    """Property 2. Telemetry is not allowed to weaken the gate."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    proc = _run_hook(_payload_for("Write", _secret_sample()), blocker)
    assert _blocked(_decision(proc)), (
        "the write was ALLOWED when the denial log was unwritable; "
        f"stderr:\n{proc.stderr}"
    )


# ---------------------------------------------------------------------------
# The other enforcement points
# ---------------------------------------------------------------------------

def test_the_scanner_cli_counts_its_own_refusal(tmp_path):
    target = tmp_path / "leaky.md"
    target.write_text("token = " + _secret_sample() + "\n", encoding="utf-8")
    log_root = tmp_path / "logs"
    env = dict(os.environ)
    env["WORKSPACE_LOG_DIR"] = str(log_root)
    proc = subprocess.run([sys.executable, str(_SCANNER), str(target)],
                          capture_output=True, text=True, cwd=str(_ROOT),
                          env=env, timeout=120)
    assert proc.returncode == 1, f"scanner exited {proc.returncode}\n{proc.stdout}{proc.stderr}"
    records = _records(log_root)
    assert len(records) == 1
    assert records[0]["mechanism"] == "secret-scanner"


def test_a_clean_scan_is_not_counted(tmp_path):
    target = tmp_path / "clean.md"
    target.write_text("nothing to see here\n", encoding="utf-8")
    log_root = tmp_path / "logs"
    env = dict(os.environ)
    env["WORKSPACE_LOG_DIR"] = str(log_root)
    proc = subprocess.run([sys.executable, str(_SCANNER), str(target)],
                          capture_output=True, text=True, cwd=str(_ROOT),
                          env=env, timeout=120)
    assert proc.returncode == 0
    assert _records(log_root) == []


def test_the_push_wall_counts_a_routing_refusal(tmp_path, monkeypatch):
    """engine_clean_scan is the unbypassable routing wall; drive the function."""
    import importlib.util

    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("push_all_probe", _ROOT / "scripts" / "push-all.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # `extra_paths` is accepted and ignored on purpose: this test is about the
    # denial RECORD, not about which world flagged the path. The stub has to
    # carry the real signature all the same -- a lambda that takes only `repo`
    # made the wall raise TypeError, and a TypeError inside `pytest.raises(
    # SystemExit)` would have looked like a pass in a slightly different test.
    monkeypatch.setattr(module, "scan_engine_repo",
                        lambda repo, extra_paths=(): ["crm/contacts/someone.md"])
    monkeypatch.setattr(module, "unpushed_paths", lambda repo: [])
    with pytest.raises(SystemExit):
        module.engine_clean_scan(_ROOT)
    records = _records(tmp_path)
    assert len(records) == 1
    assert records[0]["mechanism"] == "push:engine-clean-scan"


def test_the_leak_guard_counts_a_staged_refusal(tmp_path, monkeypatch):
    import importlib.util

    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("leak_guard_probe", _ROOT / "scripts" / "leak-guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_in_engine_repo", lambda: True)
    monkeypatch.setattr(module, "get_routing_destination", lambda rel: "private")
    assert module.check_staged(["crm/contacts/someone.md"]) == 1
    records = _records(tmp_path)
    assert len(records) == 1
    assert records[0]["mechanism"] == "leak-guard:check-staged"


# ---------------------------------------------------------------------------
# The console-first read path
# ---------------------------------------------------------------------------

def test_the_report_cli_exits_zero_when_nothing_has_been_refused(tmp_path):
    env = dict(os.environ)
    env["WORKSPACE_LOG_DIR"] = str(tmp_path)
    proc = subprocess.run([sys.executable, str(_CLI)], capture_output=True,
                          text=True, cwd=str(_ROOT), env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "0" in proc.stdout or "no" in proc.stdout.lower()


def test_the_report_cli_prints_a_count_per_mechanism(tmp_path):
    from scripts.utils.denial_log import log_denial

    os.environ["WORKSPACE_LOG_DIR"] = str(tmp_path)
    try:
        log_denial(mechanism="check_prevent_secrets", action="Write", path="p", reason="r")
        log_denial(mechanism="check_prevent_secrets", action="Write", path="p", reason="r")
        log_denial(mechanism="leak-guard:check-staged", action="commit", path="p", reason="r")
    finally:
        os.environ.pop("WORKSPACE_LOG_DIR", None)
    env = dict(os.environ)
    env["WORKSPACE_LOG_DIR"] = str(tmp_path)
    proc = subprocess.run([sys.executable, str(_CLI)], capture_output=True,
                          text=True, cwd=str(_ROOT), env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "check_prevent_secrets" in proc.stdout
    assert "2" in proc.stdout


def test_the_report_cli_emits_machine_readable_json(tmp_path):
    from scripts.utils.denial_log import log_denial

    os.environ["WORKSPACE_LOG_DIR"] = str(tmp_path)
    try:
        log_denial(mechanism="check_prevent_secrets", action="Write", path="p", reason="r")
    finally:
        os.environ.pop("WORKSPACE_LOG_DIR", None)
    env = dict(os.environ)
    env["WORKSPACE_LOG_DIR"] = str(tmp_path)
    proc = subprocess.run([sys.executable, str(_CLI), "--json"], capture_output=True,
                          text=True, cwd=str(_ROOT), env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["by_mechanism"]["check_prevent_secrets"] == 1
    assert payload["total"] == 1


# ---------------------------------------------------------------------------
# Coverage is asserted, not assumed
# ---------------------------------------------------------------------------

def test_the_counter_sits_where_a_decision_becomes_terminal(tmp_path):
    """A partial counter gives a partial denominator — the defect A8 names in our
    own false-positive instrument.

    The structural guarantee against that is placement: the counter is called in
    the dispatcher's main loop, where any check's block becomes terminal, and
    NOT inside the individual checks. A ninth check added tomorrow is then
    counted by construction rather than by its author remembering. Per THE LAW,
    a step that depends on remembering is already dead.
    """
    import ast

    tree = ast.parse(_HOOK.read_text(encoding="utf-8"))

    def _callers_of(target: str) -> dict:
        found = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    name = (getattr(inner.func, "id", None)
                            or getattr(inner.func, "attr", None))
                    if name == target:
                        found[node.name] = found.get(node.name, 0) + 1
        return found

    called_in = _callers_of("log_denial")
    assert called_in, "the dispatcher never calls the counter"
    offenders = [n for n in called_in if n.startswith("check_")]
    assert not offenders, (
        f"the counter is called inside individual checks {offenders}; a new check "
        "would then go uncounted unless its author remembered"
    )

    # The same question, asked of the name the dispatcher actually spells.
    #
    # The block above looks for `log_denial`, and this file has only ever
    # contained ONE such call: the one inside `_record_denial`, the wrapper.
    # So the `check_` filter it applies has never had a candidate to reject in
    # either direction, which is a guard with no negative case. MEASURED
    # 2026-09-01: inserting `_record_denial("check_prevent_secrets", payload,
    # "probe")` at the top of `check_prevent_secrets` - the exact defect this
    # test names - left this assertion green. Seven other tests in this file
    # caught it on the record COUNTS, so the wall held; the test that says it
    # is watching placement was not the one watching.
    wrapper_callers = _callers_of("_record_denial")
    assert wrapper_callers, "the dispatcher never calls the denial wrapper either"
    wrapper_offenders = sorted(n for n in wrapper_callers if n.startswith("check_"))
    assert not wrapper_offenders, (
        f"the counter wrapper is called inside individual checks "
        f"{wrapper_offenders}; a refusal would be counted twice there and a new "
        f"check would still depend on its author remembering")
    assert sum(wrapper_callers.values()) == 1, (
        f"the counter wrapper is called from {wrapper_callers}; it is meant to "
        f"have exactly one call site, the dispatcher's terminal deny path, so "
        f"that every check is counted by construction")
