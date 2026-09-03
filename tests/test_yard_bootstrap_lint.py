"""The YARD bootstrap's shape, and the early refusals that need no `uv sync`.

Six defects in the drafts of this script were structural: an interpreter called
before it existed, a relative path resolved against an unknown directory, a
second `trap` silently replacing the first, a heredoc that was broken in the
document it was written in, one hook body duplicated in two places, and a
fallback to `$PWD` when the event said nothing. None of them is a logic error
you can find by reading the happy path, and all six are properties of the file
that a test can hold.

The later steps (`uv sync`, `setup-platform.sh`, the canary) are exercised by
the end-to-end trial in the plan, not here: a unit test that runs `uv sync` in a
fresh worktree costs minutes and proves nothing this file is about. What IS
driven here is every refusal that happens BEFORE the first expensive step.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "scripts" / "herdr" / "heading-os-yard"
BOOTSTRAP = PLUGIN / "yard-bootstrap.sh"
HOOK_BODY = PLUGIN / "data-overlay-pre-commit"
MANIFEST = PLUGIN / "herdr-plugin.toml"


@pytest.fixture(scope="module")
def source() -> str:
    return BOOTSTRAP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code_lines(source) -> list[tuple[int, str]]:
    """Executable lines only: comments carry the explanations, not the rules."""
    out = []
    for index, raw in enumerate(source.splitlines(), 1):
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append((index, line))
    return out


def _first(code_lines, needle: str) -> int | None:
    return next((n for n, line in code_lines if needle in line), None)


# ============================================================
# It parses
# ============================================================

@pytest.mark.parametrize("path", [BOOTSTRAP, HOOK_BODY])
def test_the_shell_files_parse(path):
    """A draft shipped a heredoc whose opening line had been eaten, and the
    breakage was only findable by running `bash -n` at step 7 of an
    implementation that had already written everything."""
    result = subprocess.run(["bash", "-n", str(path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_the_manifest_is_valid_toml_and_declares_what_it_implements():
    try:
        import tomllib
    except ImportError:  # pragma: no cover - py<3.11
        pytest.skip("tomllib unavailable")
    data = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["id"] == "heading-os.yard"
    assert data["min_herdr_version"]
    assert any(e["on"] == "worktree.created" for e in data["events"])

    # A draft registered a `doctor` action the script did not implement, so the
    # documented emergency procedure crashed on a missing environment variable.
    actions = {a["id"] for a in data.get("actions", [])}
    if "doctor" in actions:
        assert "--doctor-only" in BOOTSTRAP.read_text(encoding="utf-8")


# ============================================================
# The interpreter that did not exist yet
# ============================================================

def test_nothing_calls_the_venv_interpreter_before_uv_sync(code_lines):
    """MEASURED 2026-09-03: `.venv` is absent in a fresh worktree.

    A draft wrote the status file with `.venv/bin/python`, at step 1, four
    steps before `uv sync` created it. Bootstrap died on the first line of
    every YARD, always, so the whole status-marker feature never ran once.
    """
    sync_line = _first(code_lines, "uv sync")
    assert sync_line, "the bootstrap no longer runs `uv sync`; re-check this rule"
    early = [(n, line) for n, line in code_lines
             if ".venv/bin/python" in line and n < sync_line]
    assert not early, (
        f"these lines call the venv interpreter before `uv sync` at line "
        f"{sync_line}, and a fresh worktree has no .venv: {early}")


def test_the_status_writer_needs_no_interpreter(source):
    body = re.search(r"write_status\(\)\s*\{(.*?)\n\}", source, re.S)
    assert body, "write_status() not found"
    assert "printf" in body.group(1)
    assert "python" not in body.group(1)


# ============================================================
# The path resolved against a directory nobody chose
# ============================================================

def test_the_status_file_is_made_absolute_after_the_cd(code_lines):
    """A draft set `STATUS_FILE=".claude/..."` before `cd`, so it resolved
    against the plugin runner's directory. Its own diagram showed the file
    landing in HELM -- and the idempotency check reads the same path, so one
    stale `status: ok` there would make every later bootstrap exit 0 and
    provision nothing."""
    cd_line = _first(code_lines, 'cd "$WT_PATH"')
    absolute = _first(code_lines, 'STATUS_FILE="$(pwd)')
    assert cd_line and absolute, (cd_line, absolute)
    assert absolute > cd_line
    earlier = [(n, line) for n, line in code_lines
               if "STATUS_FILE=" in line and n < cd_line]
    assert not earlier, f"STATUS_FILE is set before the cd: {earlier}"


def test_the_worktree_path_never_falls_back_to_pwd(code_lines):
    """`WT_PATH="$PWD"` is how a script ends up provisioning a directory it did
    not choose, silently. The original script did exactly that when `jq` was
    missing, which on this machine is always."""
    assert not [(n, line) for n, line in code_lines
                if re.search(r'WT_PATH="?\$(PWD|\{PWD)', line)]


def test_it_stops_when_the_event_says_nothing(tmp_path):
    """Driven, not read. No event, no provisioning, and a non-zero exit."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("HERDR_PLUGIN_EVENT_JSON", "HERDR_PLUGIN_CONTEXT_JSON",
                        "HERDR_WORKSPACE_ID", "HERDR_PANE_ID")}
    env["HOME"] = str(tmp_path)
    result = subprocess.run(["bash", str(BOOTSTRAP)], cwd=str(tmp_path),
                            capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 1
    assert "did not say which worktree" in result.stderr


def test_it_does_nothing_in_somebody_elses_repository(tmp_path):
    """A plugin is global to the user. A worktree of an unrelated project must
    be left completely alone, and quietly."""
    other = tmp_path / "other-project"
    other.mkdir()
    env = dict(os.environ,
               HERDR_PLUGIN_EVENT_JSON=f'{{"worktree":{{"path":"{other}"}}}}',
               HOME=str(tmp_path))
    result = subprocess.run(["bash", str(BOOTSTRAP)], cwd=str(tmp_path),
                            capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 0
    assert not (other / ".claude").exists(), "it wrote into a foreign checkout"


# ============================================================
# Traps, and refusals that record themselves
# ============================================================

def test_there_is_one_signal_trap(code_lines):
    """A draft installed `trap cleanup EXIT INT TERM` over
    `trap write_status ERR INT TERM`, so an interrupt during the canary removed
    the decoy and left the status at `in_progress` for ever."""
    traps = [line for _, line in code_lines if line.startswith("trap ")]
    signal_traps = [t for t in traps if "INT" in t or "TERM" in t]
    assert len(signal_traps) == 1, f"more than one signal trap: {signal_traps}"


def test_every_refusal_after_the_trap_records_its_step(code_lines):
    """`exit 1` inside `if ! ...` does not fire an ERR trap.

    A draft relied on one, so the two most likely failure points -- the guards
    check and the canary -- would have recorded nothing. Every deliberate
    refusal below the trap goes through `fail`, which writes the status, marks
    the pane and notifies.
    """
    trap_line = _first(code_lines, "trap on_signal")
    assert trap_line
    bare = [(n, line) for n, line in code_lines
            if n > trap_line and re.fullmatch(r"exit 1;?", line)]
    assert not bare, (
        f"these lines exit without recording which step failed: {bare}")


def test_the_canary_refuses_rather_than_warning(source):
    """The original logged "guards not confirmed" and carried on, which makes
    the most dangerous case look exactly like the safe one."""
    assert re.search(r'\[ -n "\$PROBE" \] \|\| fail 10', source), (
        "an empty probe path must be a refusal, not a warning")


def test_the_workspace_root_variable_is_stripped_and_then_verified(
    source, code_lines,
):
    """Stripping is not enough: the check must be on the RESULT.

    `WORKSPACE_ROOT` carried over from HELM points every guard in the worktree
    at the main clone, and each of them then reports clean.
    """
    assert "WORKSPACE_ROOT" in source
    verify = _first(code_lines, "get_workspace_root")
    strip = _first(code_lines, "WORKSPACE_ROOT|HEADING_OS_DATA")
    assert strip and verify and verify > strip


# ============================================================
# One body, in one place
# ============================================================

def test_the_data_hook_body_is_not_duplicated_inside_the_bootstrap(source):
    """A draft inlined the hook as a heredoc while also keeping it in a file.
    One hook, two copies, guaranteed to diverge at the first edit."""
    assert "HEADING_OS_YARD:-0" not in source, (
        "the data-overlay hook body is inlined here; it belongs only in "
        "scripts/herdr/heading-os-yard/data-overlay-pre-commit")


def test_the_bootstrap_does_not_install_the_shared_hook_at_all(source):
    """Installing it belongs to `install-data-overlay-guard.py`, run once from
    HELM. A per-worktree bootstrap writing into a SHARED repository would do it
    several times a day and would overwrite whatever was there."""
    assert ".git/hooks/pre-commit" not in source


# ============================================================
# Idempotency, driven before anything expensive runs
# ============================================================

def _run_against(worktree: Path, tmp_path: Path, **extra):
    env = dict(os.environ,
               HERDR_PLUGIN_EVENT_JSON=f'{{"worktree":{{"path":"{worktree}"}}}}',
               HOME=str(tmp_path), HEADING_OS_AUTOSTART="0", **extra)
    return subprocess.run(["bash", str(worktree / BOOTSTRAP.relative_to(ROOT))],
                          cwd=str(worktree), capture_output=True, text=True,
                          env=env, timeout=180)


def _mark_ok(worktree: Path) -> Path:
    status = worktree / ".claude" / ".yard-bootstrap-status"
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text('{"status":"ok","step":11,"timestamp":"x","version":"5.0"}',
                      encoding="utf-8")
    return status


def test_a_healthy_yard_is_left_alone(armed_worktree, tmp_path):
    status = _mark_ok(armed_worktree)
    before = status.read_bytes()
    result = _run_against(armed_worktree, tmp_path)
    assert result.returncode == 0
    assert "already complete" in result.stderr + result.stdout
    assert status.read_bytes() == before, "a healthy YARD was re-provisioned"


@pytest.mark.slow
def test_force_bootstrap_gets_past_the_idempotency_check(armed_worktree,
                                                         tmp_path):
    """The pair. Without it the check is indistinguishable from a script that
    always exits early, and no YARD could ever be repaired."""
    _mark_ok(armed_worktree)
    result = _run_against(armed_worktree, tmp_path, FORCE_BOOTSTRAP="1")
    assert "already complete" not in result.stderr + result.stdout
    # It goes on to do real work; where it stops depends on the machine, and
    # this test is about the gate, not about the eleven steps behind it.


def test_the_status_file_it_reads_is_the_one_inside_the_worktree(
    armed_worktree, tmp_path,
):
    """The defect: a relative status path let a stale `ok` in HELM silence
    every future bootstrap. HELM's own status file must be irrelevant here."""
    helm_status = ROOT / ".claude" / ".yard-bootstrap-status"
    assert not helm_status.exists(), (
        "HELM carries a YARD status file; it should never have one")
    result = _run_against(armed_worktree, tmp_path)
    assert "already complete" not in result.stderr + result.stdout


@pytest.mark.slow
def test_doctor_only_is_a_real_mode(armed_worktree, tmp_path):
    """Declared in the manifest, so it has to exist. A draft registered the
    action and never parsed the flag."""
    env = dict(os.environ,
               HERDR_PLUGIN_EVENT_JSON=f'{{"worktree":{{"path":"{armed_worktree}"}}}}',
               HOME=str(tmp_path))
    result = subprocess.run(
        ["bash", str(armed_worktree / BOOTSTRAP.relative_to(ROOT)),
         "--doctor-only"],
        cwd=str(armed_worktree), capture_output=True, text=True,
        env=env, timeout=180,
    )
    # It must not have rewritten the environment or the push url: doctor reads.
    assert not (armed_worktree / ".env").exists(), (
        "--doctor-only provisioned the worktree instead of checking it")
    assert shutil.which("bash")  # keeps the import used and the intent obvious
    assert result.returncode in (0, 1)
