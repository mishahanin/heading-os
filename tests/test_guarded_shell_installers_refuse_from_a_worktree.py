"""Daemon installers refuse to run from a YARD, before they touch systemd.

A systemd unit template here substitutes the workspace path into
`WorkingDirectory=` and `ExecStart=`. Run an installer from a YARD and the unit
points at a throwaway worktree: the daemon works until `herdr worktree remove`,
then stops, and the failure surfaces days later as "the bridge is down" with
nothing in the change log to connect it to.

The refusal is asserted by its OBSERVABLE CONSEQUENCE and not by exit status
alone. Every run below happens with a stub `systemctl`, `systemd-analyze`,
`loginctl` and `crontab` first on PATH, each of which records that it was
called. A guard that exits 2 after enabling the unit would pass an
exit-status-only test and fail this one.

The quiet direction is not driven here, and the omission is deliberate rather
than an oversight: running these installers from HELM to prove they are
permitted would install units on the operator's machine. It is proved once,
generically, by `tests/test_clone_guard.py::test_from_helm_the_guarded_body_runs`.
What IS asserted per script is the position of the guard in the file: it must
come before the first line that reaches systemd, because a guard placed after
one is a guard that reports on work already done.
"""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.repo_files import read_sources  # noqa: E402

SCRIPTS = ROOT / "scripts"

# Guarded on 2026-09-03. Each installs, restarts or removes a daemon, or writes
# a unit file naming the workspace path.
GUARDED = (
    "install-archive-transcripts-timer.sh",
    "install-bridge-service.sh",
    "install-chronicle-timer.sh",
    "install-council-models-timer.sh",
    "install-daemon-service.sh",
    "install-datastore-map-timer.sh",
    "install-dream-shadow-timer.sh",
    "install-memory-auto-retire-timer.sh",
    "install-memory-hygiene-timer.sh",
    "install-memory-index-timer.sh",
    "install-nightly-refresh-timer.sh",
    "install-odin-cadence-timer.sh",
    "install-odin-propose-timer.sh",
    "install-ollama-guard-timer.sh",
    "install-ops-radar-timer.sh",
    "install-reminders-timer.sh",
    "install-router-accuracy-timer.sh",
    "install-update-manager-timer.sh",
    "restart-daemon-service.sh",
    "uninstall-daemon-service.sh",
)

# Deliberately left alone. Recorded here so the absence is a decision on the
# record rather than something nobody noticed.
UNGUARDED_BY_DECISION = {
    "vps-sync.sh":
        "the plan leaves this one to the operator: it syncs to a remote host "
        "rather than installing local machine state, and its blast radius is "
        "a different question from the daemon installers'.",
}

# Commands that mean the script reached real machine state. A guard must sit
# above every one of them.
MACHINE_VERBS = ("systemctl", "loginctl", "crontab", "launchctl")


def _first_line_of(path: Path, needle: str) -> int | None:
    """Line number of the first NON-COMMENT line mentioning `needle`.

    Comment lines are skipped on purpose. The guard block this change inserts
    explains itself in prose above the code, and a plain substring scan would
    read that explanation as the thing it describes. Asking about executable
    lines is the shell equivalent of asking the AST.
    """
    for index, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if needle in line:
            return index
    return None


# ============================================================
# The corpus, floored outside the loops
# ============================================================

def test_the_guarded_corpus_is_the_size_it_was_measured_at():
    """MEASURED 2026-09-03: 19 guarded, 1 left to the operator.

    20 since 2026-09-05, when `install-nightly-refresh-timer.sh` landed. It
    renders a unit substituting the workspace path, so a run from a YARD would
    point the nightly at a checkout that is deleted two days later.
    """
    assert len(GUARDED) == 20
    assert len(UNGUARDED_BY_DECISION) == 1
    for name in (*GUARDED, *UNGUARDED_BY_DECISION):
        assert (SCRIPTS / name).is_file(), f"{name} is not in scripts/ any more"


def test_every_shell_script_that_reaches_systemd_is_accounted_for():
    """The list cannot silently fall behind the tree.

    A new installer added next month is either in `GUARDED` or in
    `UNGUARDED_BY_DECISION` with a reason. A hand-maintained security list that
    nothing compares against the tree is the shape that falls behind, so this
    derives the set from the tree on every run.

    The read goes through `read_sources` because a file can vanish between the
    glob and the read, and a bare `read_text` raises `FileNotFoundError` there.
    But the skip is NOT silent: this makes a completeness claim over the whole
    of `scripts/*.sh`, so a file that could not be opened is named and fails the
    test rather than being quietly dropped out of the accounting.
    """
    reaches_systemd = set()
    vanished: list[Path] = []
    for script, text in read_sources(sorted(SCRIPTS.glob("*.sh")), vanished):
        if any(verb in text for verb in MACHINE_VERBS):
            reaches_systemd.add(script.name)
    assert not vanished, (
        f"could not read {[p.name for p in vanished]}; this sweep claims to have "
        f"accounted for every installer in scripts/, and it did not open these")
    assert len(reaches_systemd) >= 19, (
        f"only {len(reaches_systemd)} shell scripts reach machine state; the "
        f"corpus this test walks has collapsed")
    unaccounted = reaches_systemd - set(GUARDED) - set(UNGUARDED_BY_DECISION)
    assert not unaccounted, (
        f"these shell scripts reach systemd/cron and are in neither list: "
        f"{sorted(unaccounted)}. Guard them, or record why not.")


# ============================================================
# Static: the guard sits ABOVE the first line that touches the machine
# ============================================================

@pytest.mark.parametrize("name", GUARDED)
def test_the_guard_is_sourced_and_called(name):
    path = SCRIPTS / name
    assert _first_line_of(path, "lib/require-main-clone.sh") is not None, (
        f"{name} does not source the guard helper")
    assert _first_line_of(path, "require_main_clone") is not None


@pytest.mark.parametrize("name", GUARDED)
def test_the_guard_runs_before_anything_reaches_the_machine(name):
    path = SCRIPTS / name
    guard_line = _first_line_of(path, "require_main_clone")
    assert guard_line is not None
    for verb in MACHINE_VERBS:
        verb_line = _first_line_of(path, verb)
        if verb_line is None:
            continue
        assert guard_line < verb_line, (
            f"{name}: `{verb}` is reached at line {verb_line}, before the "
            f"guard at line {guard_line}. A guard below the work reports on "
            f"a machine that has already been changed.")


@pytest.mark.parametrize("name", sorted(UNGUARDED_BY_DECISION))
def test_the_deliberate_exception_stays_unguarded(name):
    text = (SCRIPTS / name).read_text(encoding="utf-8")
    assert "require_main_clone" not in text, (
        f"{name} is unguarded on purpose: {UNGUARDED_BY_DECISION[name]}")


# ============================================================
# Dynamic: the real script, in a real worktree, with systemd stubbed
# ============================================================

@pytest.fixture
def stubbed_machine(tmp_path):
    """A PATH whose systemd/cron commands only record that they were called."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    marker = tmp_path / "machine-was-touched.log"
    for verb in MACHINE_VERBS:
        stub = bin_dir / verb
        stub.write_text(
            f'#!/usr/bin/env bash\necho "{verb} $*" >> "{marker}"\nexit 0\n',
            encoding="utf-8",
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return env, marker


# One script refuses on its own account before the clone guard is reached, and
# the ordering is deliberate. `install-memory-auto-retire-timer.sh` is RETIRED:
# its own gate exits 9 by default and is asserted, by
# `tests/test_memory_expiry.py`, to be the first statement after
# `set -euo pipefail`, with PATH pinned to `dirname` alone to prove it. So the
# clone guard sits below that gate, and reaching it needs the documented
# override. Nothing is weakened: the default run is refused outright either way.
GATE_FIRST_OVERRIDES = {
    "install-memory-auto-retire-timer.sh":
        {"MEMORY_AUTO_RETIRE_OVERRIDE": "1"},
}


@pytest.mark.parametrize("name", GUARDED)
def test_running_it_from_a_worktree_refuses_before_touching_systemd(
    armed_worktree, stubbed_machine, name,
):
    env, marker = stubbed_machine
    env = dict(env, **GATE_FIRST_OVERRIDES.get(name, {}))
    result = subprocess.run(
        ["bash", str(armed_worktree / "scripts" / name)],
        cwd=str(armed_worktree), capture_output=True, text=True,
        env=env, timeout=120,
    )
    assert result.returncode == 2, (
        f"{name}: exit {result.returncode}\nstdout={result.stdout}\n"
        f"stderr={result.stderr}")
    assert "HELM" in result.stderr, result.stderr
    assert not marker.exists(), (
        f"{name} reached the machine before refusing: "
        f"{marker.read_text(encoding='utf-8')}")


def test_the_stub_would_actually_have_recorded_a_call(stubbed_machine):
    """The control for the assertion above.

    Without this, `not marker.exists()` is satisfied by a stub that never
    worked, and nineteen tests would pass over a machine that WAS touched.
    """
    env, marker = stubbed_machine
    subprocess.run(["bash", "-c", "systemctl --user daemon-reload"],
                   env=env, capture_output=True, check=True, timeout=30)
    assert marker.exists()
    assert "systemctl" in marker.read_text(encoding="utf-8")


def test_the_helper_refuses_when_sourced_into_a_bare_shell(tmp_path):
    """`$0` is "bash" for a sourced script, and a guard that reads that as a
    path answers about the wrong thing. It refuses instead."""
    result = subprocess.run(
        ["bash", "-c",
         f'source "{SCRIPTS}/lib/require-main-clone.sh"; require_main_clone'],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 2
    assert "cannot determine which script is running" in result.stderr


def test_the_shell_predicate_agrees_with_the_python_one(armed_main_clone,
                                                        armed_worktree):
    """The two copies of one rule must not drift.

    `clone_guard.py` cannot be used by the shell helper: an installer is run
    with PATH pinned to `dirname` alone in another test, so spawning python is
    not available on the common path. The rule is therefore implemented twice,
    and this is what holds them together. Both directions, both checkouts.
    """
    probe = (
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'source "$(dirname "$0")/lib/require-main-clone.sh"\n'
        "require_main_clone\n"
        'echo MAIN\n'
    )
    # `armed_main_clone`, not ROOT: the True side needs a checkout that really
    # is a main clone, and ROOT is a YARD worktree whenever this suite is run
    # from one. MEASURED 2026-09-03: both predicates answered False for ROOT
    # here, which is correct of them and made the pair fail.
    for checkout, expected in ((armed_main_clone, True), (armed_worktree, False)):
        script = checkout / "scripts" / "_predicate-probe.sh"
        script.write_text(probe, encoding="utf-8")
        try:
            shell = subprocess.run(
                ["bash", str(script)], cwd=str(checkout),
                capture_output=True, text=True, timeout=60,
            )
            python = subprocess.run(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, sys.argv[1]);"
                 "from scripts.utils.clone_guard import is_main_clone;"
                 "print(is_main_clone(sys.argv[1]))",
                 str(checkout)],
                cwd=str(checkout), capture_output=True, text=True, timeout=60,
            )
        finally:
            script.unlink(missing_ok=True)
        shell_says_main = shell.returncode == 0
        python_says_main = python.stdout.strip() == "True"
        assert shell_says_main == expected, (checkout, shell.stderr)
        assert python_says_main == expected, (checkout, python.stderr)


def test_the_helper_adds_no_external_command_of_its_own(
    armed_worktree, tmp_path,
):
    """PATH holds `dirname` and nothing else, and the guard still refuses.

    `dirname` is the one external the CALLER already needs, for the
    `source "$(dirname "$0")/lib/..."` line every guarded installer carries.
    `tests/test_memory_expiry.py` pins exactly that PATH to prove nothing runs
    before its script's own gate, and the first version of this helper died
    there with `basename: command not found` and status 127 instead of the
    status the caller was asserting.

    So the property is not "no externals at all" -- it is "no external the
    caller did not already depend on". With that PATH the verdict must still be
    reached, from the shape of `.git` alone: no git, no python, no basename.
    """
    bin_dir = tmp_path / "minimal-bin"
    bin_dir.mkdir()
    dirname_path = shutil.which("dirname")
    assert dirname_path, "no dirname on this machine; the probe cannot be pinned"
    (bin_dir / "dirname").symlink_to(dirname_path)

    script = armed_worktree / "scripts" / "_minimal-path-probe.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'source "$(dirname "$0")/lib/require-main-clone.sh"\n'
        "require_main_clone\n"
        'echo REACHED\n',
        encoding="utf-8",
    )
    bash = shutil.which("bash") or "/bin/bash"
    result = subprocess.run(
        [bash, str(script)], cwd=str(armed_worktree),
        capture_output=True, text=True, timeout=60,
        env={"PATH": str(bin_dir), "HOME": str(tmp_path)},
    )
    assert result.returncode == 2, (result.returncode, result.stderr)
    assert "HELM" in result.stderr
    assert "REACHED" not in result.stdout
    assert "command not found" not in result.stderr


def test_the_helper_refuses_outside_any_git_repository(tmp_path):
    """Fail closed: git cannot say which clone this is, so nothing proceeds."""
    script = tmp_path / "scripts" / "pretend-installer.sh"
    script.parent.mkdir(parents=True)
    lib = tmp_path / "scripts" / "lib"
    lib.mkdir()
    (lib / "require-main-clone.sh").write_bytes(
        (SCRIPTS / "lib" / "require-main-clone.sh").read_bytes())
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'source "$(dirname "$0")/lib/require-main-clone.sh"\n'
        "require_main_clone\n"
        'echo "REACHED THE BODY"\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(script)], cwd=str(tmp_path),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 2
    assert "not inside a git repository" in result.stderr
    assert "REACHED THE BODY" not in result.stdout
