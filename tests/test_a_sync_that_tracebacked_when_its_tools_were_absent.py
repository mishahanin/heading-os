"""sync-corporate.py broke its own exit-code contract two ways.

1. Only `subprocess.TimeoutExpired` was caught around `git pull --ff-only` and
   `gh repo clone`. A machine without the executable on PATH raises
   `FileNotFoundError` (an OSError), which propagated out of `main()` as a
   traceback -- no result dict, and with `--json` nothing machine-readable at
   all. The module's documented exit codes are 0 and "1  clone/pull failed
   (degrades clearly; never silent)", and both documented callers (setup.py,
   `/sync`) run it headless during onboarding, which is precisely where a
   missing executable turns up.

   Measured before the fix, against a tmp workspace with `PATH=""`:
     pull branch  -> FileNotFoundError [Errno 2] No such file or directory: 'git'
     clone branch -> FileNotFoundError [Errno 2] No such file or directory: 'gh'

2. The failure hint in `main()` re-called `load_github_org()` unconditionally.
   When the failure WAS the missing org, that returns "" and the hint read
   "Check access to /heading-os-corporate and your gh auth" -- the same empty-org
   interpolation the `org_note` comment in `sync_corporate` says was fixed.

   Measured before the fix, exec workspace with no org configured:
     "Corporate content not updated. Check access to /heading-os-corporate ..."

Safety: this module clones and pulls into OTHER repositories. Every test here
replaces the module's `subprocess` reference with a shim that cannot spawn a
process, and pins the workspace root to `tmp_path`, so no real sync can run and
nothing is written outside the temporary tree.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "sync-corporate.py"


class _NeverSpawns:
    """Stand-in for the `subprocess` module that records instead of spawning."""

    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self, raises=None, returncode=0, stdout="", stderr=""):
        self.raises = raises
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append(list(argv))
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            argv, self.returncode, stdout=self.stdout, stderr=self.stderr
        )


def _load():
    sys.path.insert(0, str(REPO))
    spec = importlib.util.spec_from_file_location("sync_corporate_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    """The module, wired to a tmp workspace that is an exec workspace."""
    module = _load()
    monkeypatch.setattr(module, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(module, "is_exec_workspace", lambda: True)
    monkeypatch.setattr(module, "load_github_org", lambda: "universal-exports")
    monkeypatch.setattr(module, "load_env", lambda root: None)
    return module


def _install_shim(module, monkeypatch, shim):
    monkeypatch.setattr(module, "subprocess", shim)
    return shim


def test_the_script_exists() -> None:
    assert SCRIPT.is_file(), f"nothing to test at {SCRIPT}"


def test_missing_git_on_the_pull_branch_returns_a_result_not_a_traceback(
    mod, tmp_path, monkeypatch
) -> None:
    (tmp_path / ".corporate-repo" / ".git").mkdir(parents=True)
    shim = _install_shim(mod, monkeypatch, _NeverSpawns(
        raises=FileNotFoundError(2, "No such file or directory", "git")))

    res = mod.sync_corporate()

    assert shim.calls and shim.calls[0][0] == "git", "the pull branch must be the one exercised"
    assert res["status"] == "error"
    assert res["action"] == "pull"
    assert "git" in res["message"]
    assert set(res) == {"status", "action", "path", "message"}


def test_missing_gh_on_the_clone_branch_returns_a_result_not_a_traceback(
    mod, tmp_path, monkeypatch
) -> None:
    shim = _install_shim(mod, monkeypatch, _NeverSpawns(
        raises=FileNotFoundError(2, "No such file or directory", "gh")))

    res = mod.sync_corporate()

    assert shim.calls and shim.calls[0][0] == "gh", "the clone branch must be the one exercised"
    assert res["status"] == "error"
    assert res["action"] == "clone"
    assert "gh" in res["message"]
    assert set(res) == {"status", "action", "path", "message"}


def test_a_permission_error_degrades_the_same_way(mod, tmp_path, monkeypatch) -> None:
    """FileNotFoundError is one OSError; a non-executable `gh` is another."""
    _install_shim(mod, monkeypatch, _NeverSpawns(
        raises=PermissionError(13, "Permission denied", "gh")))

    res = mod.sync_corporate()

    assert res["status"] == "error"
    assert "Permission denied" in res["message"]


def test_a_missing_executable_still_yields_json_and_exit_one(
    mod, tmp_path, monkeypatch, capsys
) -> None:
    """The headless contract: --json emits a parseable result, exit code 1."""
    import json

    _install_shim(mod, monkeypatch, _NeverSpawns(
        raises=FileNotFoundError(2, "No such file or directory", "gh")))
    monkeypatch.setattr(sys, "argv", ["sync-corporate.py", "--json"])

    rc = mod.main()

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["action"] == "clone"


def test_a_timeout_is_still_reported_as_a_timeout(mod, tmp_path, monkeypatch) -> None:
    """The new OSError handler must not swallow the pre-existing branch.

    The CLONE branch. Its pull twin is the test below, and until 2026-09-01
    there was only this one.
    """
    _install_shim(mod, monkeypatch, _NeverSpawns(
        raises=subprocess.TimeoutExpired(cmd=["gh"], timeout=mod.CLONE_TIMEOUT_S)))

    res = mod.sync_corporate()

    assert res["status"] == "error"
    assert res["action"] == "clone"
    assert str(mod.CLONE_TIMEOUT_S) in res["message"]
    assert "exceeded" in res["message"]


def test_a_pull_timeout_is_still_reported_as_a_timeout(mod, tmp_path, monkeypatch) -> None:
    """The twin the file was missing. `sync_corporate` has TWO timeout handlers.

    Every other pair in this file is measured both ways: the OSError handler has
    a pull test and a clone test, and so does the happy path. The timeout had
    only the clone one, and the clone branch is the one an exec workspace takes
    exactly once. Afterwards every run takes the PULL branch, which is where a
    stalled network actually shows up.

    MEASURED 2026-09-01: replacing the pull branch's `except
    subprocess.TimeoutExpired` with a clause that cannot match left this file,
    `tests/test_sync_corporate.py` and `tests/integration/test_setup_wizard_e2e.py`
    at 16 passed, while `git pull` on a hung remote would take the traceback out
    of `main()` - the same contract breach as the missing executable this file
    is named for, one branch over. Doing the same to the CLONE handler failed a
    test immediately.

    `PULL_TIMEOUT_S` is 120 and `CLONE_TIMEOUT_S` is 300, so the number in the
    message is what distinguishes the two branches rather than two spellings of
    one value.
    """
    (tmp_path / ".corporate-repo" / ".git").mkdir(parents=True)
    shim = _install_shim(mod, monkeypatch, _NeverSpawns(
        raises=subprocess.TimeoutExpired(cmd=["git"], timeout=mod.PULL_TIMEOUT_S)))

    res = mod.sync_corporate()

    assert shim.calls and shim.calls[0][0] == "git", "the pull branch must be the one exercised"
    assert res["status"] == "error"
    assert res["action"] == "pull"
    assert str(mod.PULL_TIMEOUT_S) in res["message"]
    assert str(mod.CLONE_TIMEOUT_S) not in res["message"], (
        "the pull branch reported the clone timeout")
    assert "exceeded" in res["message"]


def test_the_two_timeouts_are_different_numbers(mod) -> None:
    """Anti-vacuity for the pair above. If the two constants ever converge, the
    `not in` assertion in the pull test becomes unsatisfiable and the pair stops
    telling the branches apart."""
    assert mod.PULL_TIMEOUT_S != mod.CLONE_TIMEOUT_S


def test_a_successful_pull_is_unaffected(mod, tmp_path, monkeypatch) -> None:
    """Both directions: the happy path must not have been captured by the fix."""
    (tmp_path / ".corporate-repo" / ".git").mkdir(parents=True)
    _install_shim(mod, monkeypatch, _NeverSpawns(returncode=0, stdout="Already up to date.\n"))

    res = mod.sync_corporate()

    assert res["status"] == "ok"
    assert res["action"] == "pull"


def test_no_access_hint_names_an_empty_org(mod, tmp_path, monkeypatch, capsys) -> None:
    """When the failure IS the missing org, the hint must not invent a repo path."""
    monkeypatch.setattr(mod, "load_github_org", lambda: "")
    _install_shim(mod, monkeypatch, _NeverSpawns(
        raises=AssertionError("no subprocess may run when no org is configured")))
    monkeypatch.setattr(sys, "argv", ["sync-corporate.py"])

    rc = mod.main()

    assert rc == 1
    out = capsys.readouterr().out
    assert f"/{mod.CORPORATE_REPO}" not in out, (
        f"the hint interpolated an empty org into a repo path: {out!r}"
    )
    assert "github_org" in out, "the error must still name the seam to set"


def test_the_access_hint_survives_when_an_org_is_configured(
    mod, tmp_path, monkeypatch, capsys
) -> None:
    """The other direction: a real org still gets the real access hint."""
    _install_shim(mod, monkeypatch, _NeverSpawns(returncode=1, stderr="repository not found"))
    monkeypatch.setattr(sys, "argv", ["sync-corporate.py"])

    rc = mod.main()

    assert rc == 1
    out = capsys.readouterr().out
    assert f"universal-exports/{mod.CORPORATE_REPO}" in out


def test_the_ceo_workspace_remains_a_noop(mod, monkeypatch) -> None:
    """Nothing in this change may make the CEO workspace consume a clone."""
    monkeypatch.setattr(mod, "is_exec_workspace", lambda: False)
    shim = _install_shim(mod, monkeypatch, _NeverSpawns(
        raises=AssertionError("the CEO workspace must never clone or pull")))

    res = mod.sync_corporate()

    assert res["status"] == "noop"
    assert shim.calls == []
