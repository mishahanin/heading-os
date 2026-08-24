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


def test_setup_keeps_its_stdlib_only_import_surface():
    """The atomic write is inlined deliberately: importing scripts.utils.atomic
    would reinstate the sys.path mutation this shard removed, against the
    module's own "self-contained at import" docstring."""
    src = (ROOT / "scripts" / "setup.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    for node in top_level:
        mod = getattr(node, "module", None) or ""
        assert not mod.startswith("scripts."), ast.unparse(node)


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
