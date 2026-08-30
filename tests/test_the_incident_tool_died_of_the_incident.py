"""The incident tool must survive the machine state the incident produces.

`scripts/emergency-revoke.py` exists to be run when something has gone wrong with
an executive's access. One shape of "something has gone wrong" is a machine whose
private data overlay is missing, unmounted, or pointed at a dead path. Measured
2026-08-30, before the fix:

    HEADING_OS_DATA=/nonexistent-xyz .venv/bin/python scripts/emergency-revoke.py --help
    -> Traceback ... DataRootError, exit 1

`GITHUB_ORG = load_github_org()` ran at module scope, so the refusal arrived
during import: no argparse, no `--help`, and no urgent manual checklist, which is
the only thing this file still delivers. The exit code was 1, not the 2 its own
docstring documents.

The wider half was a false contract. `scripts/utils/operator_identity.py`
promised "Never raises" and did raise, through
`get_data_config_dir()` -> `get_data_root()`. Five modules call that seam at
module scope and all five inherited the crash.

These tests bind both halves, and they bind the OUTPUT, not merely the absence of
an exception. A script that exits 0 while printing nothing useful is the same
failure wearing a nicer exit code.

Nothing here reaches the network or the GitHub API: the script exits before any
`gh` call, and the one test that drives the revocation function replaces
`run_cmd` with a probe that fails the test if it is ever reached.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "emergency-revoke.py"

# Invented. The engine repo is public and carries no real fleet identities.
FAKE_SLUG = "rowan-selkirk"


_PRIMED_ORG = "treadstone-holdings"   # invented; the engine repo is public


def _prime_a_resolved_org(operator_identity) -> None:
    """Leave a NON-EMPTY org in the identity cache with a clean environment.

    That is the exact leak shape: the value survives in the cache while nothing
    in the environment explains it.
    """
    var = "HEADING_OS_OPERATOR_GITHUB_ORG"
    os.environ[var] = _PRIMED_ORG
    try:
        operator_identity._reset_cache()
        assert operator_identity.operator_org() == _PRIMED_ORG
    finally:
        os.environ.pop(var, None)


@pytest.fixture(scope="module", autouse=True)
def _leave_the_cache_clean_for_other_files():
    """This file deliberately poisons the cache between its own tests (below).
    Nothing outside it should ever see that."""
    from scripts.utils import operator_identity

    yield
    operator_identity._reset_cache()


@pytest.fixture(autouse=True)
def _identity_cache_is_not_inherited(monkeypatch):
    """`operator_org()` is an `lru_cache`, so a peer's answer outlives its test.

    MEASURED 2026-08-30, before this fixture existed: under `-n auto`, whichever
    test ran first in a worker decided the answer for the rest of it. If an
    earlier test had resolved a real identity, `operator_org()` returned a real
    org here, `load_github_org()` short-circuited at its untouched `if org:
    return org`, and `test_an_unresolvable_org_refuses_rather_than_reporting_clear`
    failed. It failed 3 runs in 4 that way, and a probe under a modified and an
    unmodified `scripts/utils/workspace.py` returned identical values, so the
    flake was never about the code under test.

    Pointing `HEADING_OS_DATA` at a dead path does not help: the cache is keyed
    on nothing, so it never re-reads the environment.

    **The teardown poisons on purpose, and that is what makes this fixture
    self-binding.** A guard whose absence changes no test result is not a guard,
    and the first version of this one was exactly that: disabling it left every
    test green, because no peer on this host happened to resolve a real org.
    Poisoning on the way out means the setup half is load-bearing for whichever
    test runs next, in any order, with or without `-p randomly`. The module
    fixture above clears once at the end so the poison never escapes this file.

    The `HEADING_OS_OPERATOR_*` variables are dropped from the module's own
    mapping rather than typed here. Typed, they were wrong: the first draft
    deleted `HEADING_OS_OPERATOR_ORG`, which is not a name this workspace uses.
    """
    from scripts.utils import operator_identity

    for var in operator_identity._ENV_KEYS.values():
        monkeypatch.delenv(var, raising=False)
    operator_identity._reset_cache()
    yield
    _prime_a_resolved_org(operator_identity)


def _dead_root(tmp_path: Path) -> str:
    """A path that does not exist. The real no-overlay condition, not a mock."""
    dead = tmp_path / "overlay-that-is-not-mounted"
    assert not dead.exists(), "the fixture must point at a path that is absent"
    return str(dead)


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, HEADING_OS_DATA=_dead_root(tmp_path))
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )


# --------------------------------------------------------------------------
# The script: --help and the manual checklist work with no overlay at all.
# --------------------------------------------------------------------------

def test_help_works_with_no_data_overlay(tmp_path):
    """`--help` is the one command an operator types under pressure."""
    proc = _run(tmp_path, "--help")

    assert "Traceback (most recent call last)" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}"
    # Not just "it exited 0": the help text has to actually be there.
    assert "usage: emergency-revoke.py" in proc.stdout
    assert "--exec" in proc.stdout
    assert "does NOT revoke anything" in proc.stdout


def test_the_manual_checklist_prints_with_no_data_overlay(tmp_path):
    """The checklist is the payload. It sat behind the import-time crash."""
    proc = _run(tmp_path, "--exec", FAKE_SLUG, "--reason", "laptop stolen")

    assert "Traceback (most recent call last)" not in proc.stderr, proc.stderr
    # The exit code the module docstring documents for the disabled run.
    assert proc.returncode == 2, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"

    out = proc.stdout
    assert "NOTHING HAS BEEN REVOKED BY THIS RUN" in out
    assert "URGENT MANUAL ACTIONS REQUIRED" in out
    # A representative spread of the checklist, so a truncated print fails here.
    assert "Revoke ALL API keys" in out
    assert "Terminate ALL active Telegram sessions" in out
    assert "Revoke VPN credentials and SSH keys" in out
    assert FAKE_SLUG in out, "the checklist must name the exec it was run for"


def test_the_unreadable_registry_is_named_not_swallowed(tmp_path):
    """Degrade clearly. The two personalised lines are gone, and it says so."""
    proc = _run(tmp_path, "--exec", FAKE_SLUG, "--reason", "laptop stolen")

    assert proc.returncode == 2
    assert "the exec registry could not be read" in proc.stderr
    assert "WITHOUT this exec's name and email" in proc.stderr
    # The generic checklist still prints; the email line degrades honestly
    # rather than inventing an address.
    assert "Disable email account: unknown" in proc.stdout


# --------------------------------------------------------------------------
# The contract: operator_identity really never raises now.
# --------------------------------------------------------------------------

@pytest.fixture
def identity(monkeypatch, tmp_path):
    """The identity seam, cache cleared, pointed at a data root that is absent."""
    sys.path.insert(0, str(REPO_ROOT))
    from scripts.utils import operator_identity as mod

    monkeypatch.setenv("HEADING_OS_DATA", _dead_root(tmp_path))
    # Env identity beats the file tiers, so it would mask the thing under test.
    for env_name in mod._ENV_KEYS.values():
        monkeypatch.delenv(env_name, raising=False)
    mod._reset_cache()
    yield mod
    mod._reset_cache()


def test_operator_identity_returns_the_documented_sentinel(identity, capsys):
    """The docstring's promise, measured against the real no-overlay condition."""
    op = identity.get_operator()

    assert op["slug"] == "operator"
    assert op["github_org"] == ""
    assert op["name"] == "Operator"
    assert identity.operator_org() == ""
    assert identity.operator_email_domain() == ""
    assert identity.operator_is_default() is True

    # Documented, not silent: the fall to a lower tier is announced once.
    err = capsys.readouterr().err
    assert "[operator-identity]" in err
    assert "private data overlay could not be resolved" in err


def test_the_never_raises_docstring_is_not_a_second_false_promise(identity):
    """The promise and the behaviour have to agree when this is finished."""
    assert "Never raises" in identity.__doc__
    # Every public entry point on the seam, under the condition that broke it.
    for fn in (identity.get_operator, identity.operator_slug, identity.operator_org,
               identity.operator_email_domain, identity.operator_is_default):
        fn()  # must not raise


# --------------------------------------------------------------------------
# No silent partial run: an unknown org refuses instead of calling the API.
# --------------------------------------------------------------------------

def _load_revoke_module():
    """Import the hyphen-named script. Safe now: module scope touches no data."""
    sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location("emergency_revoke_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_importing_the_script_needs_no_data_overlay(monkeypatch, tmp_path):
    """The defect itself: import used to be the crash site."""
    monkeypatch.setenv("HEADING_OS_DATA", _dead_root(tmp_path))
    mod = _load_revoke_module()
    assert callable(mod.github_org)


def test_an_unresolvable_org_refuses_rather_than_reporting_clear(monkeypatch, tmp_path, capsys):
    """An empty org would make every repo path a guess and every 404 read as
    'no access'. It must STOP, and never reach the GitHub API to do it."""
    monkeypatch.setenv("HEADING_OS_DATA", _dead_root(tmp_path))
    mod = _load_revoke_module()

    def _never(*args, **kwargs):
        raise AssertionError(f"the GitHub API was called with an unknown org: {args!r}")

    monkeypatch.setattr(mod, "run_cmd", _never)

    assert mod.github_org() == "", "a dead data root must resolve the org as unknown"
    mod.revoke_all_github_access(FAKE_SLUG, {"github_username": "example-handle"})

    captured = capsys.readouterr()
    assert "[STOP]" in captured.out
    assert "GitHub org could not be resolved" in captured.out
    assert "MANUAL ACTION REQUIRED" in captured.out
    # The reassuring wording must NOT appear: that was the original defect class.
    assert "not a direct collaborator" not in captured.out
    assert "[REVOKED]" not in captured.out
