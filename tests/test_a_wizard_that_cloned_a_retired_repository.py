"""Nothing may create a directory OUTSIDE the workspace as a side effect.

`31c-crm-central` was retired when the fleet seam was hard-cut to per-exec
repos. Three places still reached for it, and each reached differently:

  1. `scripts/setup.py` steps 7 and 9 once cloned it and made it, as ordinary
     first-run wizard steps. They are no-ops in HEAD and nothing asserted that,
     so the only thing standing between a fresh install and a rebuilt retired
     tree was the absence of a diff. `codegraph` reports "no covering tests
     found" for both. This file is that cover.
  2. `scripts/emergency-revoke.py` cloned it in `audit_recent_commits` and, far
     worse, ran `audit_dir.mkdir(parents=True, exist_ok=True)` in
     `log_security_event`. The mkdir needed no network, no gh auth and no
     clone: one stray import-and-call materialised
     `<workspace-parent>/31c-crm-central/audit/` on the operator's disk, and
     that is exactly how it got made once already.
  3. `scripts/utils/workspace.get_crm_central_path()` RESOLVES the path. That
     is fine and stays fine. Resolving a retired path costs nothing;
     rebuilding a retired tree outside the workspace is a side effect nobody
     asked for.

The line this file draws is between resolving and creating, because that is
the line the invariant is written on.

Deliberate design notes, since each was a way to write a test that measures
less than it claims:

  * It asserts on the FILESYSTEM, never on the source text of a script. A
    grep for "gh repo clone" passes the moment someone spells the same call
    another way.
  * The `gh` and `git` shims CREATE their clone destination, because a real
    clone does. A shim that only recorded its argument would let the
    filesystem assertion sail straight over a restored clone step, and the
    mutation proof would be theatre.
  * `PATH` holds the shim directory and nothing else, so no real `git`, `gh`,
    `uv`, `node` or `claude` can run and nothing reaches the network.
  * The sandbox workspace root is `tmp_path/ws`, so the path the wizard would
    build, `root.parent / "31c-crm-central"`, lands at `tmp_path` and is
    caught by the sweep. The real sibling directory is stat'd before and
    after and never written to.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SETUP_PY = REPO_ROOT / "scripts" / "setup.py"
REVOKE_PY = REPO_ROOT / "scripts" / "emergency-revoke.py"

RETIRED_DIRNAME = "31c-crm-central"

# The real sibling path a CEO workspace resolves. Named here ONLY so the test
# can prove it was not created. It is stat'd, never written, never removed.
REAL_RETIRED_PATH = REPO_ROOT.parent / RETIRED_DIRNAME


# ---------------------------------------------------------------------------
# Sandbox construction
# ---------------------------------------------------------------------------

# `#!/bin/sh`, an absolute interpreter, NOT `#!/usr/bin/env bash`: `env`
# resolves its argument through PATH, and PATH here holds only the shim
# directory. With `env bash` every shim exited 127, the wizard reported all
# five prerequisites missing and died at step 3 -- and both invariant tests
# below passed anyway, over a wizard that had never reached the steps they
# claim to measure. A sandbox strict enough to prove something is strict
# enough to break the tools doing the proving, so the fixture now refuses a
# run that did not get past the prerequisite check.
# Every external the shims themselves use is named by ABSOLUTE path, and the
# tool name is baked in rather than read from `$0`, for the same reason as the
# shebang: `basename` and `mkdir` are not on the stripped PATH either. `$0`
# alone would work, but baking the name keeps the log readable and removes one
# more thing the sandbox can quietly take away.
_RECORDING_SHIM = """#!/bin/sh
printf '%s\\n' "{name} $*" >> "{log}"
exit 0
"""

# `gh` and `git` additionally emulate the ONE side effect that matters here: a
# clone CREATES its destination directory. Without this the filesystem
# assertion could not tell a fixed wizard from a broken one.
_CLONING_SHIM = """#!/bin/sh
printf '%s\\n' "{name} $*" >> "{log}"
dest=""
if [ "$1" = "repo" ] && [ "$2" = "clone" ]; then dest="$4"; fi
if [ "$1" = "clone" ]; then dest="$3"; fi
if [ -n "$dest" ]; then /bin/mkdir -p "$dest/.git"; fi
exit 0
"""


def _shim_bin(tmp_path: Path) -> tuple[Path, Path]:
    """A PATH directory holding only recording shims. Returns (bin, log)."""
    bin_dir = tmp_path / "shimbin"
    bin_dir.mkdir()
    log = tmp_path / "shim-calls.log"
    log.write_text("", encoding="utf-8")

    for name in ("node", "claude", "uv", "python3", "python", "py"):
        p = bin_dir / name
        p.write_text(_RECORDING_SHIM.format(log=log, name=name), encoding="utf-8")
        p.chmod(0o755)
    for name in ("git", "gh"):
        p = bin_dir / name
        p.write_text(_CLONING_SHIM.format(log=log, name=name), encoding="utf-8")
        p.chmod(0o755)

    # The cloning shim is the mutation detector; a broken one would make every
    # removal below look proven. Verify it can actually create a destination
    # before any of that rests on it.
    probe = tmp_path / "shim-probe"
    subprocess.run([str(bin_dir / "gh"), "repo", "clone", "org/x", str(probe)],
                   check=True, env={"PATH": str(bin_dir)})
    assert probe.is_dir(), "the cloning shim cannot create a directory"
    shutil.rmtree(probe)
    log.write_text("", encoding="utf-8")
    return bin_dir, log


def _sandbox(tmp_path: Path) -> Path:
    """A minimal exec workspace holding a copy of the real setup.py."""
    ws = tmp_path / "ws"
    (ws / "scripts" / "utils").mkdir(parents=True)
    shutil.copy2(SETUP_PY, ws / "scripts" / "setup.py")

    (ws / ".workspace-identity.json").write_text(
        json.dumps({"slug": "quill-marsden", "type": "exec-workspace",
                    "role": "exec"}),
        encoding="utf-8")

    # Pre-created so step 5 skips instead of reaching getpass, which would try
    # /dev/tty and could block the run.
    (ws / ".env").write_text("ANTHROPIC_API_KEY=\n", encoding="utf-8")

    # Step 6 shells out to this. A local no-op keeps the step honest (it marks
    # itself done) with no clone and no network.
    (ws / "scripts" / "sync-corporate.py").write_text(
        "import sys\nsys.exit(0)\n", encoding="utf-8")

    # Step 11 lazily imports scripts.utils.schedule after putting the workspace
    # root at sys.path[0]. This repo's .venv carries a plain path entry for the
    # real tree, so without a sandbox-local stand-in the step could import the
    # real module. `--no-sentinel-schedule` returns before calling into it; the
    # body asserts, so a run that DID call it would fail loudly rather than
    # install a systemd timer.
    (ws / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    (ws / "scripts" / "utils" / "__init__.py").write_text("", encoding="utf-8")
    (ws / "scripts" / "utils" / "schedule.py").write_text(
        "def install_sentinel_schedule(*a, **k):\n"
        "    raise AssertionError('the sandbox must never install a schedule')\n",
        encoding="utf-8")
    return ws


def _run_wizard(ws: Path, bin_dir: Path) -> subprocess.CompletedProcess:
    env = {
        "PATH": str(bin_dir),          # shims ONLY: nothing real, nothing networked
        "HOME": str(ws.parent / "home"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    (ws.parent / "home").mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, str(ws / "scripts" / "setup.py"),
         "--no-sentinel-schedule"],
        cwd=str(ws), env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=180,
    )


@pytest.fixture()
def wizard_run(tmp_path):
    """Run the real setup wizard once, fully sandboxed. Yields the evidence."""
    bin_dir, log = _shim_bin(tmp_path)
    ws = _sandbox(tmp_path)
    real_before = REAL_RETIRED_PATH.exists()
    proc = _run_wizard(ws, bin_dir)
    # A wizard that died before step 7 creates no retired repo either, and
    # would hand the two invariant tests a free pass. Refuse that shape here,
    # once, rather than let three tests report green over it.
    if "NOT FOUND" in proc.stdout or "clone_crm_central" not in (
            (ws / ".sync" / "setup-state.json").read_text(encoding="utf-8")
            if (ws / ".sync" / "setup-state.json").is_file() else ""):
        pytest.fail("the sandbox wizard never reached the crm-central steps, so "
                    "nothing below measures what it claims\nSTDOUT:\n"
                    + proc.stdout + "\nSTDERR:\n" + proc.stderr)
    return {
        "proc": proc,
        "ws": ws,
        "tmp": tmp_path,
        "calls": log.read_text(encoding="utf-8").splitlines(),
        "real_before": real_before,
    }


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------

def test_the_wizard_creates_no_retired_repository_anywhere(wizard_run):
    """After a full first-run wizard, no `31c-crm-central` exists on disk.

    Asserted on the filesystem, in three directions: nothing by that name
    anywhere in the sandbox, the workspace's own resolved sibling absent, and
    the operator's REAL sibling path left exactly as it was found.
    """
    tmp = wizard_run["tmp"]

    strays = [p for p in tmp.rglob("*") if RETIRED_DIRNAME in p.name]
    assert strays == [], (
        f"the wizard created {len(strays)} path(s) named {RETIRED_DIRNAME!r} "
        f"inside the sandbox: {[str(p) for p in strays]}")

    # The exact path the resolver builds for this sandbox: root.parent/<name>.
    assert not (wizard_run["ws"].parent / RETIRED_DIRNAME).exists()

    assert REAL_RETIRED_PATH.exists() == wizard_run["real_before"], (
        f"the wizard changed the existence of {REAL_RETIRED_PATH}, which is "
        f"outside the sandbox and must never be touched by a test run")


def test_the_wizard_asks_no_tool_to_fetch_the_retired_repository(wizard_run):
    """No subprocess the wizard issued named the retired repo at all.

    The filesystem test above is the load-bearing one. This one closes the gap
    where a clone is attempted and fails: a wizard that TRIES is still a wizard
    that reaches for a retired repo on every fresh install.
    """
    named = [c for c in wizard_run["calls"] if RETIRED_DIRNAME in c]
    assert named == [], (
        f"the wizard invoked {len(named)} command(s) naming {RETIRED_DIRNAME!r}: "
        f"{named}")


# ---------------------------------------------------------------------------
# The negative direction: the rest of the wizard still works
# ---------------------------------------------------------------------------

def test_the_wizard_still_completes_its_other_steps(wizard_run):
    """A fix that disables the wizard passes the invariant and is worse.

    Asserted on observable effects, not on the absence of an error: the run
    exits 0, reaches step 13, records every other step in its resumable state
    file, and really did shell out to the dependency installer.
    """
    proc = wizard_run["proc"]
    assert proc.returncode == 0, (
        f"the wizard exited {proc.returncode}\nSTDOUT:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr}")
    assert "Setup complete!" in proc.stdout, (
        f"the wizard never reached its summary step\nSTDOUT:\n{proc.stdout}")

    state_file = wizard_run["ws"] / ".sync" / "setup-state.json"
    assert state_file.is_file(), "the wizard wrote no resumable state file"
    done = set(json.loads(state_file.read_text(encoding="utf-8"))["completed_steps"])

    # Every step that marks itself done on a clean run, INCLUDING the two
    # crm-central steps: they are no-ops, and a no-op that still records
    # itself is what keeps the resume sequence stable.
    for step in ("check_prerequisites", "verify_github_auth", "clone_corporate",
                 "corporate_sync", "clone_crm_central", "crm_central_dir",
                 "install_python_deps"):
        assert step in done, f"step {step!r} did not complete: {sorted(done)}"

    assert any(c.startswith("uv sync") for c in wizard_run["calls"]), (
        f"step 10 never invoked the dependency installer: {wizard_run['calls']}")


# ---------------------------------------------------------------------------
# The resolver, and the two callers that abused it
# ---------------------------------------------------------------------------

def _load_revoke():
    spec = importlib.util.spec_from_file_location("_emergency_revoke_uut",
                                                  REVOKE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_resolver_creates_nothing(tmp_path, monkeypatch):
    """`get_crm_central_path()` names a path and stops there."""
    from scripts.utils import workspace as ws_mod

    root = tmp_path / "engine"
    root.mkdir()
    monkeypatch.setattr(ws_mod, "get_workspace_root", lambda: root)
    monkeypatch.setattr(ws_mod, "is_ceo_workspace", lambda: True)

    resolved = ws_mod.get_crm_central_path()
    assert resolved == tmp_path / RETIRED_DIRNAME
    assert not resolved.exists(), "a pure resolver created its own answer"


def test_logging_a_security_event_does_not_build_the_retired_tree(tmp_path,
                                                                  capsys):
    """`log_security_event` refuses rather than mkdir outside the workspace.

    This is the call that actually made the directory. It needs no network and
    no gh auth, so the `sys.exit(2)` gate in `main()` never protected it: a
    stray import and one call was the whole exploit.
    """
    mod = _load_revoke()
    target = tmp_path / RETIRED_DIRNAME
    mod.get_crm_central_path = lambda: target

    def _no_subprocess(*a, **k):
        raise AssertionError(f"the refusal path shelled out: {a!r}")
    mod.run_cmd = _no_subprocess

    mod.log_security_event("quill-marsden", "laptop stolen", [])

    assert not target.exists(), (
        f"log_security_event created {target}, outside the workspace")
    assert list(tmp_path.iterdir()) == [], (
        f"log_security_event left {[p.name for p in tmp_path.iterdir()]} behind")
    out = capsys.readouterr().out
    assert "was NOT persisted" in out, (
        f"the refusal was silent, so the event vanished unreported:\n{out}")


def test_auditing_commits_does_not_clone_the_retired_repository(tmp_path,
                                                                capsys):
    """`audit_recent_commits` skips an absent crm-central instead of cloning."""
    mod = _load_revoke()
    target = tmp_path / RETIRED_DIRNAME
    mod.get_crm_central_path = lambda: target
    mod.get_corporate_repo_path = lambda: tmp_path / "absent-corporate"

    calls = []

    def _record(cmd, cwd=None, check=True):
        calls.append(list(cmd))
        raise AssertionError(f"the skip path shelled out: {cmd!r}")
    mod.run_cmd = _record

    assert mod.audit_recent_commits("quill-marsden") == []

    assert calls == [], f"a command ran against a retired repo: {calls}"
    assert not target.exists(), f"audit_recent_commits created {target}"
    assert "NOT PERFORMED" in capsys.readouterr().out


def test_an_existing_legacy_clone_is_still_read(tmp_path):
    """Refusing to CREATE the tree must not mean refusing to READ one.

    An operator who already has a legacy clone keeps their evidence. Losing
    that during an incident would be a worse defect than the one being fixed,
    so the read path is pinned here as well as the refusal.
    """
    mod = _load_revoke()
    target = tmp_path / RETIRED_DIRNAME
    (target / "audit").mkdir(parents=True)
    mod.get_crm_central_path = lambda: target
    mod.get_corporate_repo_path = lambda: tmp_path / "absent-corporate"

    calls = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def _record(cmd, cwd=None, check=True):
        calls.append((list(cmd), cwd))
        return _Result()
    mod.run_cmd = _record

    mod.audit_recent_commits("quill-marsden")

    verbs = [c[0][:2] for c in calls]
    assert ["git", "pull"] in verbs, f"the existing clone was not read: {calls}"
    assert not any("clone" in c[0] for c in calls), (
        f"an existing clone was re-cloned: {calls}")
