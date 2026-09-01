#!/usr/bin/env python3
"""Shard scripts-11-p2: the recovery paths that removed the way back.

`setup.py` calls itself idempotent and safe to re-run. It was not:

  - A FAILED corporate clone was recorded as a completed step, so every later
    run skipped it. On a fresh clone with no gh auth yet, the workspace never
    got `.corporate-repo/` and the only way to retry was deleting
    `.sync/setup-state.json` by hand.
  - Ctrl+C at the API-key prompt was swallowed, so the user bailing out instead
    got a keyless `.env`, ten more steps, and a `.env` that then "already
    exists" -- the key prompt never appearing again.
  - The state file that carries all of this was written non-atomically, and
    `load_state` silently reset on a parse error. An interrupted setup destroyed
    the record of what it had done.
  - `.env` was created at the process umask and chmod'd afterwards, so the API
    key was world-readable for the window in between.
  - `sync-corporate` had no timeouts, no empty-org check, and no way past a
    directory left by an interrupted clone.

And three that reported the wrong state:
  - `sync-exchange-pulse` exited 0 when the daemon was down and auto-start
    failed -- it is the liveness check /prime calls.
  - `pid_is_running` read PermissionError as dead, so a daemon owned by another
    user looked stopped and the pulse spawned a duplicate beside it.
  - `skill-trigger-test` had no handler on the judge call, so one transient 529
    threw away a 96-skill sweep of paid calls.

Run: .venv/bin/python -m pytest tests/test_a_setup_that_locks_out_its_own_retry.py -q
"""

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.pid_liveness import pid_is_running  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


stt = _load("skill_trigger_test_p11b", "scripts/skill-trigger-test.py")
sco = _load("sync_corporate_p11b", "scripts/sync-corporate.py")
ste = _load("ste_check_p11b", "scripts/ste-check.py")


# ============================================================
# 1 - a failed step stays retryable
# ============================================================
def test_the_clone_step_is_marked_done_only_on_success():
    src = (ROOT / "scripts" / "setup.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "step_clone_corporate")
    marks = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "mark_done"]
    assert len(marks) == 1, ast.unparse(fn)
    # And it must sit inside the success branch, not after the try.
    body = src.split("def step_clone_corporate", 1)[1].split("\ndef ", 1)[0]
    success_branch = body.split("if result.returncode == 0:", 1)[1].split("else:", 1)[0]
    assert "mark_done" in success_branch, success_branch


def test_the_failure_branch_says_the_step_will_retry():
    src = (ROOT / "scripts" / "setup.py").read_text(encoding="utf-8")
    body = src.split("def step_clone_corporate", 1)[1].split("\ndef ", 1)[0]
    assert "NOT marking this step" in body


# ============================================================
# 2 - Ctrl+C aborts; only EOF is absorbed
# ============================================================
def test_keyboardinterrupt_is_not_swallowed_at_the_key_prompt():
    """`main` has a handler that prints "re-run to continue". Catching the
    interrupt at the prompt meant nothing ever reached it."""
    src = (ROOT / "scripts" / "setup.py").read_text(encoding="utf-8")
    body = src.split("def step_setup_env", 1)[1].split("\ndef ", 1)[0]
    assert "except (EOFError, KeyboardInterrupt):" not in body, body
    assert "except EOFError:" in body


# ============================================================
# 3 - the state file survives an interrupted write
# ============================================================
def test_the_state_write_goes_through_a_temp_file():
    src = (ROOT / "scripts" / "setup.py").read_text(encoding="utf-8")
    body = src.split("def save_state", 1)[1].split("\ndef ", 1)[0]
    assert "os.replace(" in body, body
    assert "STATE_FILE.write_text(" not in body, body


# ============================================================
# 3b - the state file that parsed but did not fit (2026-08-25)
# ============================================================
#
# The atomic write above stopped the file being TRUNCATED. It never made a
# well-formed file COMPLETE. `load_state` returned whatever `json.loads` gave
# back, and `main`'s very next line is `state["started_at"]`, so a hand-edited
# or older-schema file killed the wizard on the run whose whole purpose is to
# resume an interrupted one - the same failure this shard was opened for,
# reached through a different door.


def _load_state_with(tmp_path, monkeypatch, payload):
    setup = _load("setup_p13a", "scripts/setup.py")
    state_file = tmp_path / "setup-state.json"
    if payload is not None:
        state_file.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(setup, "STATE_FILE", state_file)
    return setup, setup.load_state()


@pytest.mark.parametrize("payload,why", [
    ("{}", "an empty object: KeyError on started_at"),
    ('{"started_at": null}', "no completed_steps: dies later, inside mark_done"),
    ('{"completed_steps": ["a"]}', "no started_at: KeyError immediately"),
    ('{"last_updated": "2026-01-01"}', "an older schema"),
])
def test_a_short_state_file_still_carries_both_required_keys(
        tmp_path, monkeypatch, payload, why):
    _, state = _load_state_with(tmp_path, monkeypatch, payload)
    assert "started_at" in state, why
    assert isinstance(state["completed_steps"], list), why


def test_real_progress_is_never_discarded_by_the_fill(tmp_path, monkeypatch):
    """Filling a gap must not cost the record the file exists to keep."""
    _, state = _load_state_with(
        tmp_path, monkeypatch,
        '{"completed_steps": ["identity", "prereqs"], "started_at": "2026-01-01"}')
    assert state["completed_steps"] == ["identity", "prereqs"]
    assert state["started_at"] == "2026-01-01"


@pytest.mark.parametrize("payload", ['["a", "list"]', '"a string"', "42", "null"])
def test_a_wrong_shaped_payload_falls_back_to_the_skeleton(
        tmp_path, monkeypatch, payload):
    """Valid JSON, wrong type. A list raises TypeError rather than KeyError, so
    a fix that caught only KeyError would have left this open."""
    _, state = _load_state_with(tmp_path, monkeypatch, payload)
    assert state == {"completed_steps": [], "started_at": None}


def test_a_wrong_typed_completed_steps_is_replaced(tmp_path, monkeypatch):
    """`mark_done` appends to it and `is_done` searches it; a string would do
    both without raising and mean nothing."""
    _, state = _load_state_with(
        tmp_path, monkeypatch, '{"completed_steps": "identity", "started_at": null}')
    assert state["completed_steps"] == []


def test_a_corrupt_file_still_gives_a_usable_state(tmp_path, monkeypatch):
    _, state = _load_state_with(tmp_path, monkeypatch, "{ not json")
    assert state == {"completed_steps": [], "started_at": None}


# `load_state` caught `(json.JSONDecodeError, OSError)` until 2026-09-01.
# `UnicodeDecodeError` is a SIBLING of `JSONDecodeError` under `ValueError`, not
# a subclass, and it comes out of `read_text` BEFORE `json.loads` is handed
# anything - so the corruption this function most has to survive was the one it
# did not catch.
#
# It is not hypothetical here. `save_state` became atomic in this same shard, so
# a state file left behind by the older non-atomic write can be cut mid
# UTF-8 sequence. MEASURED that day on `b'{"completed_steps": ["ident\xc3'`: a
# raw UnicodeDecodeError out of `load_state`, which `main` calls on its first
# line. The wizard died on the run whose whole purpose is to resume an
# interrupted one.
UNDECODABLE_STATES = [
    b'{"completed_steps": ["ident\xc3',      # cut mid multi-byte sequence
    b'{"completed_steps": [], "started_at": "\xff"}',   # a lone invalid byte
    b"\xff\xfe\x00\x00",                     # not text at all
]


@pytest.mark.parametrize("raw", UNDECODABLE_STATES)
def test_an_undecodable_state_file_still_gives_a_usable_state(
        tmp_path, monkeypatch, raw):
    setup = _load("setup_p13a_decode", "scripts/setup.py")
    state_file = tmp_path / "setup-state.json"
    state_file.write_bytes(raw)
    monkeypatch.setattr(setup, "STATE_FILE", state_file)

    state = setup.load_state()

    assert state == {"completed_steps": [], "started_at": None}
    # The two keys `main` and `mark_done` subscript, which is the whole contract.
    setup.mark_done(state, "identity")
    assert setup.is_done(state, "identity") is True


def test_the_undecodable_corpus_really_is_undecodable():
    """The straw-man check. A payload that happens to decode would make the
    parametrised case above a second copy of the plain-corrupt test."""
    assert UNDECODABLE_STATES
    for raw in UNDECODABLE_STATES:
        with pytest.raises(UnicodeDecodeError):
            raw.decode("utf-8")


def test_no_json_read_in_setup_is_blind_to_an_undecodable_byte():
    """The sibling sweep, because the same fix landed in one of three sites.

    `load_state` was the site this shard reached; `.workspace-identity.json` is
    read the same way in two more places in this file, both catching
    `(json.JSONDecodeError, OSError)` and neither reaching its own written
    degraded answer for a byte that will not decode. A per-site test for each
    would decay the moment a fourth read is added, so the property is asserted
    over the AST of the whole file.
    """
    tree = ast.parse((ROOT / "scripts" / "setup.py").read_text(encoding="utf-8"))
    offenders = []
    handlers = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        caught = {ast.unparse(x) for x in (
            node.type.elts if isinstance(node.type, ast.Tuple) else [node.type])}
        flat = " ".join(caught)
        if "JSONDecodeError" not in flat:
            continue
        handlers += 1
        # `ValueError` is the shared parent, so a handler naming it is covered.
        if "UnicodeDecodeError" not in flat and "ValueError" not in flat:
            offenders.append((node.lineno, sorted(caught)))
    assert handlers >= 3, (
        f"only {handlers} JSON handlers were inspected; the detector has "
        f"decayed and this sweep is passing over nothing")
    assert offenders == [], (
        f"these handlers catch a JSON parse error but not the decode error that "
        f"precedes it: {offenders}")


def test_a_decodable_state_file_is_still_read_rather_than_reset(
        tmp_path, monkeypatch):
    """The bound on the other side.

    A `load_state` that answered the skeleton for everything would satisfy every
    corruption case in this file while throwing away the operator's progress on
    every ordinary run - which is the same "an interrupted setup destroyed the
    record of what it had done" this shard exists to stop.
    """
    _, state = _load_state_with(
        tmp_path, monkeypatch,
        '{"completed_steps": ["identity"], "started_at": "2026-01-01"}')
    assert state["completed_steps"] == ["identity"]


def test_a_missing_file_still_gives_a_usable_state(tmp_path, monkeypatch):
    _, state = _load_state_with(tmp_path, monkeypatch, None)
    assert state == {"completed_steps": [], "started_at": None}


def test_the_loaded_state_survives_mark_done_and_is_done(tmp_path, monkeypatch):
    """The asymmetry that hid the defect: `is_done` uses `.get` and `mark_done`
    subscripts, so a short file passed the early checks and died mid-run."""
    setup, state = _load_state_with(tmp_path, monkeypatch, '{"started_at": null}')
    assert setup.is_done(state, "identity") is False
    setup.mark_done(state, "identity")
    assert setup.is_done(state, "identity") is True


# ============================================================
# 3c - two checks that named a tree the workspace retired (2026-08-25)
# ============================================================
#
# `corporate/` was an in-tree copy of the corporate repo. It is gone; content is
# read in place from `.corporate-repo/`. The verify step and the dependency
# installer were repointed at the live path and kept PRINTING the dead one, so a
# single run named the same file two different ways and one of the two named
# nothing on disk. The verify case is the worse of the two: a PASSING check that
# asserts a file exists at a location it never opened is exactly the
# `.claude/rules/scope-claims.md` defect, and a green line gives the operator no
# reason to look.


def test_the_verify_step_names_the_path_it_actually_probed():
    src = (ROOT / "scripts" / "setup.py").read_text(encoding="utf-8")
    body = src.split("def step_verify(state", 1)[1].split("\ndef ", 1)[0]
    assert '"corporate/context/business-info.md exists"' not in body, body
    assert "biz_info.relative_to(WORKSPACE_ROOT)" in body


def test_the_verify_step_still_probes_the_live_corporate_path():
    """The fix must not have repointed the message at a wrong probe."""
    src = (ROOT / "scripts" / "setup.py").read_text(encoding="utf-8")
    body = src.split("def step_verify(state", 1)[1].split("\ndef ", 1)[0]
    assert 'WORKSPACE_ROOT / ".corporate-repo" / "context" / "business-info.md"' in body


@pytest.mark.parametrize("present,expect_ok", [(True, True), (False, False)])
def test_the_verify_check_reports_what_it_found(tmp_path, monkeypatch, capsys,
                                                present, expect_ok):
    """Behavioural, not just textual: the line has to follow the FILE.

    A check that passes whatever is on disk is worse than one that always
    fails - the operator has no reason to doubt a green line, which is what
    made the wrong path name dangerous rather than untidy.
    """
    setup = _load("setup_p13a_verify", "scripts/setup.py")
    monkeypatch.setattr(setup, "WORKSPACE_ROOT", tmp_path)
    biz = tmp_path / ".corporate-repo" / "context" / "business-info.md"
    if present:
        biz.parent.mkdir(parents=True)
        biz.write_text("# business\n", encoding="utf-8")
    setup.step_verify({})
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "business-info.md" in ln)
    assert (" exists" in line) is expect_ok, line
    assert ("not found" in line) is (not expect_ok), line


def test_the_verify_check_names_the_probed_path_in_both_outcomes(
        tmp_path, monkeypatch, capsys):
    """Whichever way it goes, the path printed is the path opened."""
    setup = _load("setup_p13a_verify2", "scripts/setup.py")
    monkeypatch.setattr(setup, "WORKSPACE_ROOT", tmp_path)
    setup.step_verify({})
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "business-info.md" in ln)
    assert ".corporate-repo/context/business-info.md" in line, line


def test_no_operator_facing_string_in_setup_names_the_retired_tree():
    """A sweep, not a spot check: the same literal was in three places and two
    audits fixed one each. Fix records ARE allowed to name it, since their whole
    job is to say what changed - they are comments, not output."""
    src = (ROOT / "scripts" / "setup.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders = []
    inspected = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        text = node.value
        inspected += 1
        cleaned = text.replace(".corporate-repo/", "")
        if "corporate/requirements.txt" in cleaned or \
                "corporate/context/business-info.md" in cleaned:
            offenders.append(text[:80])
    # Floor on the SURVIVORS, not the tree: 334 string constants reached the
    # match on 2026-08-26. If the isinstance filter above ever drifts (say the
    # ast.Constant test stops holding for the nodes setup.py actually contains),
    # every node would be skipped, offenders would be empty, and this sweep
    # would pass while reading nothing.
    assert inspected >= 200, f"only {inspected} string constants inspected"
    assert offenders == [], offenders


def test_setup_keeps_its_stdlib_only_import_surface():
    """The atomic write is inlined deliberately: importing scripts.utils.atomic
    would reinstate the sys.path mutation this shard removed, against the
    module's own "self-contained at import" docstring."""
    src = (ROOT / "scripts" / "setup.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert top_level, "setup.py has no top-level imports at all; nothing was checked"
    # `node.module` exists only on ast.ImportFrom. The check used to be
    # `getattr(node, "module", None) or ""`, which yields "" for every
    # ast.Import, so plain `import scripts.utils.atomic` - the exact line this
    # guard's docstring names as the regression - passed vacuously. The nodes
    # were collected and then never inspected.
    for node in top_level:
        names = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                 else [alias.name for alias in node.names])
        for name in names:
            assert not name.startswith("scripts."), ast.unparse(node)


def test_the_corporate_repo_name_is_still_anchored():
    """It looked dead. It is the canonical name a docs guard reads out of this
    file, and deleting it unanchored that guard."""
    src = (ROOT / "scripts" / "setup.py").read_text(encoding="utf-8")
    import re
    assert re.search(r'^CORPORATE_REPO\s*=\s*"([^"]+)"', src, re.M)


# ============================================================
# 4 - .env is owner-only from birth
# ============================================================
def test_the_env_file_is_created_with_mode_600_not_chmodded_after():
    src = (ROOT / "scripts" / "setup.py").read_text(encoding="utf-8")
    body = src.split("def step_setup_env", 1)[1].split("\ndef ", 1)[0]
    assert "os.open(" in body and "0o600" in body, body
    # The old shape wrote first at the umask, then narrowed.
    posix = body.split('if platform.system() != "Windows":', 1)[1]
    assert posix.index("os.open(") < posix.index("chmod"), posix


# ============================================================
# 5 - sync-corporate names what the operator must fix
# ============================================================
def test_an_unset_github_org_is_named_not_turned_into_a_clone_target(monkeypatch):
    monkeypatch.setattr(sco, "load_github_org", lambda: "")
    monkeypatch.setattr(sco, "is_exec_workspace", lambda: True)
    out = sco.sync_corporate(dry_run=False)
    assert out["status"] == "error"
    assert "operator.yaml" in out["message"], out


def test_a_directory_without_git_is_refused_with_the_remedy(
        tmp_path, monkeypatch):
    """`gh repo clone` refuses a non-empty target, so one interrupted clone
    bricked the seam and the error never said to delete the directory."""
    monkeypatch.setattr(sco, "load_github_org", lambda: "acme")
    monkeypatch.setattr(sco, "is_exec_workspace", lambda: True)
    monkeypatch.setattr(sco, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(sco, "load_env", lambda root: None)
    (tmp_path / sco.CLONE_DIRNAME).mkdir()
    out = sco.sync_corporate(dry_run=False)
    assert out["status"] == "error"
    assert "Delete it" in out["message"], out


def test_both_subprocesses_are_bounded():
    src = (ROOT / "scripts" / "sync-corporate.py").read_text(encoding="utf-8")
    assert "timeout=PULL_TIMEOUT_S" in src
    assert "timeout=CLONE_TIMEOUT_S" in src


# ============================================================
# 6 - a live process owned by someone else is not "dead"
# ============================================================
def test_this_process_reads_as_running():
    assert pid_is_running(os.getpid()) is True


def test_pid_one_reads_as_running_despite_being_unsignalable():
    """PID 1 exists and belongs to another user, so `os.kill(1, 0)` raises
    PermissionError for a normal user. Both old copies returned False, which is
    what let the pulse spawn a duplicate daemon."""
    if os.name == "nt":
        pytest.skip("POSIX EPERM semantics")
    assert pid_is_running(1) is True


def test_an_absent_pid_reads_as_not_running():
    assert pid_is_running(999999) is False
    assert pid_is_running(0) is False
    assert pid_is_running(-5) is False


def test_neither_daemon_file_keeps_a_private_copy():
    for rel in ("scripts/sync-exchange-daemon.py", "scripts/sync-exchange-pulse.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
        assert "except (ProcessLookupError, PermissionError)" not in code, rel
        assert "pid_is_running" in code, rel


# ============================================================
# 7 - the pulse reports failure as failure
# ============================================================
def test_every_pulse_exit_path_returns_a_code():
    src = (ROOT / "scripts" / "sync-exchange-pulse.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            bare = [n for n in ast.walk(node)
                    if isinstance(n, ast.Return) and n.value is None]
            assert bare == [], "a bare `return` in main() exits 0"
            break
    else:
        pytest.fail("main() not found")
    assert "sys.exit(main())" in src


def test_the_failed_autostart_path_returns_nonzero():
    src = (ROOT / "scripts" / "sync-exchange-pulse.py").read_text(encoding="utf-8")
    body = src.split("auto-start failed", 1)[1][:500]
    assert "return 1" in body, body


# ============================================================
# 8 - the daemon cannot be started twice into the same window
# ============================================================
def test_a_second_starter_cannot_take_the_lock(tmp_path, monkeypatch):
    sed = _load("sync_exchange_daemon_p11b", "scripts/sync-exchange-daemon.py")
    monkeypatch.setattr(sed, "RUNTIME_DIR", tmp_path)
    first = sed._acquire_start_lock()
    assert first is not None
    try:
        # A second acquisition from THIS process would succeed under flock
        # semantics, so the contention is proved from a child process.
        probe = subprocess.run(
            [sys.executable, "-c",
             "import fcntl,sys\n"
             f"h=open({str(tmp_path / 'daemon.start.lock')!r},'w')\n"
             "try:\n"
             "    fcntl.flock(h, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
             "    print('TOOK')\n"
             "except OSError:\n"
             "    print('BLOCKED')\n"],
            capture_output=True, text=True, timeout=30)
        assert probe.stdout.strip() == "BLOCKED", probe
    finally:
        first.close()


def test_the_lock_is_released_when_the_holder_exits(tmp_path, monkeypatch):
    """The kernel releases flock on exit, so a crashed daemon leaves no stale
    lock -- which is why this is flock and not an O_EXCL marker file."""
    sed = _load("sync_exchange_daemon_p11b2", "scripts/sync-exchange-daemon.py")
    monkeypatch.setattr(sed, "RUNTIME_DIR", tmp_path)
    handle = sed._acquire_start_lock()
    assert handle is not None
    handle.close()
    again = sed._acquire_start_lock()
    assert again is not None
    again.close()


# ============================================================
# 9 - a judge fault is unmeasured, not fatal
# ============================================================
def test_an_api_error_yields_an_unmeasured_verdict():
    class _Boom:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("529 overloaded")

    verdict = stt.judge_query(_Boom(), "m", "sys", "q", "osint")
    assert verdict.get("routes_to_target") is None
    assert "api error" in verdict.get("reason", "")


@pytest.mark.parametrize("payload", [
    '[{"routes_to_target": true}]',     # a list-wrapped verdict
    'Sure! {"routes_to_target": false} hope that helps',   # a chatty judge
    'no json at all',
    '{not json}',
    '',
])
def test_every_reply_shape_parses_to_a_dict(payload):
    """The audit asked for an `isinstance(parsed, dict)` guard here. It is
    unreachable: the slice runs from the first `{` to the last `}`, so
    json.loads sees a string starting with `{` and can only return a dict or
    raise. The list-wrapped case has its brackets stripped by that same slice.
    Pinned as a property so the reasoning does not have to be re-derived."""
    out = stt._parse_verdict(payload)
    assert isinstance(out, dict), (payload, out)


def test_a_well_formed_verdict_still_parses():
    out = stt._parse_verdict('{"routes_to_target": true, "skill": "osint"}')
    assert out["routes_to_target"] is True


def test_the_git_probe_cannot_raise(monkeypatch):
    """The rev-parse probe bypassed the defensive helper: no timeout, no
    FileNotFoundError guard, so a box without git crashed the --changed gate."""
    def boom(*a, **k):
        raise FileNotFoundError("no git here")

    monkeypatch.setattr(stt.subprocess, "run", boom)
    assert stt._git_changed_files("origin/main") == set()


# ============================================================
# 10 - a CRLF document is stripped like any other
# ============================================================
def test_crlf_frontmatter_is_blanked_not_audited_as_prose():
    body = "---\ndescription: a very long description field\nname: thing\n---\nHello.\n"
    lf = ste.strip_noise(body)
    crlf = ste.strip_noise(body.replace("\n", "\r\n"))
    assert "description:" not in crlf, crlf
    assert crlf.strip() == lf.strip()


def test_an_lf_document_is_unaffected_by_the_normalisation():
    text = "# Title\n\nA short sentence.\n"
    assert "\r" not in ste.strip_noise(text)
