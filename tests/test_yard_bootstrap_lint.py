"""The YARD bootstrap's shape, and the early refusals that need no `uv sync`.

Six defects in the drafts of this script were structural: an interpreter called
before it existed, a relative path resolved against an unknown directory, a
second `trap` silently replacing the first, a heredoc that was broken in the
document it was written in, one hook body duplicated in two places, and a
fallback to `$PWD` when the event said nothing. None of them is a logic error
you can find by reading the happy path, and all six are properties of the file
that a test can hold.

Most of what is driven here is the refusals that happen BEFORE the first
expensive step, and those are cheap.

Two tests go past the gate and run the remaining steps:
`test_force_bootstrap_gets_past_the_idempotency_check` and
`test_the_status_file_it_reads_is_the_one_inside_the_worktree`. This docstring
used to deny they existed, claiming the later steps were exercised only by the
end-to-end trial, and the claim was already false when it was written.

It became expensive on 2026-09-03, when step 4 started installing every extra
plus the dev group: MEASURED that day, one run took 230 s serially, and under
`-n auto` the concurrent ones exceeded a 900 s bound. `_run_against` now puts a
stub `uv` on PATH, so step 4 succeeds instantly and these tests measure the gate
in front of it, which is all they ever asserted. The flags step 4 really passes
are held by `tests/test_a_bootstrap_that_built_a_yard_it_could_not_test_in.py`.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.clone_guard import is_main_clone  # noqa: E402

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


def test_it_stops_when_the_event_says_nothing(tmp_path, herdr_stub):
    """Driven, not read. No event, no provisioning, and a non-zero exit."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("HERDR_PLUGIN_EVENT_JSON", "HERDR_PLUGIN_CONTEXT_JSON",
                        "HERDR_WORKSPACE_ID", "HERDR_PANE_ID")}
    # This path reaches `notification show` before it exits, so it talks to the
    # server even though it provisions nothing.
    env = herdr_stub.env(env)
    env["HOME"] = str(tmp_path)
    result = subprocess.run(["bash", str(BOOTSTRAP)], cwd=str(tmp_path),
                            capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 1
    assert "did not say which worktree" in result.stderr


def test_it_does_nothing_in_somebody_elses_repository(tmp_path, herdr_stub):
    """A plugin is global to the user. A worktree of an unrelated project must
    be left completely alone, and quietly."""
    other = tmp_path / "other-project"
    other.mkdir()
    env = herdr_stub.env()
    env.update(HERDR_PLUGIN_EVENT_JSON=f'{{"worktree":{{"path":"{other}"}}}}',
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

def _stub_uv(tmp_path: Path) -> Path:
    """A `uv` on PATH that succeeds instantly, so step 4 installs nothing.

    Step 4 has run `uv sync --all-extras --group dev` since 2026-09-03. That is
    correct for a real YARD and ruinous inside a unit suite: MEASURED
    2026-09-03, one full run took 230 s serially, and under `pytest -n auto`
    three of them ran concurrently, contended for the uv cache, and blew past a
    900 s bound -- three failures this file caused in the suite it belongs to.

    Stubbing removes a cost, not an assertion. Nothing in this module is about
    dependency resolution; the module docstring says so, and the flags step 4
    passes are asserted textually in
    `tests/test_a_bootstrap_that_built_a_yard_it_could_not_test_in.py`. What is
    driven here is the gate in front of step 4.

    NOT claimed: with this stub in place the suite never executes a real
    `uv sync`. That path is covered by the end-to-end trial and by every YARD
    the plugin actually provisions, not here.
    """
    shim = tmp_path / "shim"
    shim.mkdir(exist_ok=True)
    uv = shim / "uv"
    uv.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    uv.chmod(0o755)
    return shim


def _reached_step(worktree: Path) -> int:
    """The step the run recorded in the worktree's own receipt.

    Both full-run tests below assert the ABSENCE of a phrase, and an absence is
    satisfied just as well by a bootstrap that died before it could print
    anything. MEASURED 2026-09-03: with `_stub_uv` deliberately broken to exit
    1, step 4 failed and both tests stayed green -- three mutations aimed at the
    stub, none of them caught. This is what makes them measure the run rather
    than its silence.

    With a working stub the run reaches step 6 and stops there, because step 5
    needs the `.venv` the stub did not build. Asserted as "past 4" rather than
    "equal to 6": the point is that step 4 completed, and where it stops after
    that is a property of the machine.
    """
    receipt = worktree / ".claude" / ".yard-bootstrap-status"
    assert receipt.exists(), "the run left no receipt at all"
    return json.loads(receipt.read_text(encoding="utf-8"))["step"]


def _run_against(worktree: Path, tmp_path: Path, herdr_stub, timeout: int = 180,
                 real_uv: bool = False, **extra):
    """Run the bootstrap against `worktree`.

    `herdr_stub` is mandatory, not defaulted. The herdr server is shared across
    every worktree on the machine, so a run that reaches the real binary can
    rename the operator's workspace and write into another task's pane; making
    the parameter required means a new caller cannot forget it by omission.
    See the `herdr_stub` fixture in `tests/conftest.py`.

    `real_uv=True` lets a caller opt into an actual dependency install; nothing
    in this module does, and a caller that does must raise `timeout` with it.
    """
    env = herdr_stub.env()
    env.update(HERDR_PLUGIN_EVENT_JSON=f'{{"worktree":{{"path":"{worktree}"}}}}',
               HOME=str(tmp_path), HEADING_OS_AUTOSTART="0", **extra)
    if not real_uv:
        env["PATH"] = f"{_stub_uv(tmp_path)}{os.pathsep}{env['PATH']}"
    return subprocess.run(["bash", str(worktree / BOOTSTRAP.relative_to(ROOT))],
                          cwd=str(worktree), capture_output=True, text=True,
                          env=env, timeout=timeout)


def _mark_ok(worktree: Path) -> Path:
    status = worktree / ".claude" / ".yard-bootstrap-status"
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text('{"status":"ok","step":11,"timestamp":"x","version":"5.0"}',
                      encoding="utf-8")
    return status


def test_a_healthy_yard_is_left_alone(armed_worktree, tmp_path, herdr_stub):
    status = _mark_ok(armed_worktree)
    before = status.read_bytes()
    result = _run_against(armed_worktree, tmp_path, herdr_stub)
    assert result.returncode == 0
    assert "already complete" in result.stderr + result.stdout
    assert status.read_bytes() == before, "a healthy YARD was re-provisioned"


@pytest.mark.slow
def test_force_bootstrap_gets_past_the_idempotency_check(armed_worktree,
                                                         tmp_path, herdr_stub):
    """The pair. Without it the check is indistinguishable from a script that
    always exits early, and no YARD could ever be repaired."""
    _mark_ok(armed_worktree)
    result = _run_against(armed_worktree, tmp_path, herdr_stub,
                          FORCE_BOOTSTRAP="1")
    assert "already complete" not in result.stderr + result.stdout
    assert _reached_step(armed_worktree) > 4, (
        "the run did not get past step 4, so the absence above proves nothing")
    # It goes on to do real work; where it stops depends on the machine, and
    # this test is about the gate, not about the eleven steps behind it.


def ambient_receipt_verdict(root: Path, main_clone: bool) -> str:
    """What a bootstrap receipt sitting at `root` means for the run below.

    `root` is the checkout the SUITE was launched from, which is HELM only
    sometimes. Reading it as HELM unconditionally was a defect in this file,
    MEASURED 2026-09-03: run from the YARD at .yard/.heading-os/test-123, where
    the receipt legitimately exists, the assertion below failed on
    `Path.exists()` and reported "HELM carries a YARD status file" about a
    worktree. A test that cannot run where all engine work happens is a test
    that stops being run.

    Four states, and only one of them is a defect:

    * `main-clone-carries-receipt` -- the real finding. The main clone never
      runs the YARD bootstrap, so a receipt there is stale state that a
      relatively-resolved status path could read as "already complete".
    * `main-clone-clean` -- the expected shape of HELM.
    * `worktree-receipt-is-a-confounder` -- the expected shape of a provisioned
      YARD, and the STRONGER setting for the assertion that follows: a script
      that resolved its status path against the launching checkout rather than
      against the target worktree would find this completed receipt and
      short-circuit, so the run below would catch it.
    * `worktree-no-receipt` -- a bare `git worktree add` that was never
      bootstrapped. No confounder present, so the run below is the same weaker
      check it is in HELM. Named rather than silently lumped in with the case
      above, because the two differ in what the assertion proves.
    """
    receipt = root / ".claude" / ".yard-bootstrap-status"
    if main_clone:
        return "main-clone-carries-receipt" if receipt.exists() else "main-clone-clean"
    return ("worktree-receipt-is-a-confounder" if receipt.exists()
            else "worktree-no-receipt")


def test_the_status_file_it_reads_is_the_one_inside_the_worktree(
    armed_worktree, tmp_path, herdr_stub,
):
    """The defect: a relative status path let a stale `ok` in the launching
    checkout silence every future bootstrap. That checkout's own receipt must
    be irrelevant here, whichever kind of checkout it is."""
    verdict = ambient_receipt_verdict(ROOT, is_main_clone(ROOT))
    assert verdict != "main-clone-carries-receipt", (
        f"{ROOT} is the main clone and carries a YARD status file; "
        f"it should never have one")
    result = _run_against(armed_worktree, tmp_path, herdr_stub)
    assert "already complete" not in result.stderr + result.stdout
    assert _reached_step(armed_worktree) > 4, (
        "the run did not get past step 4, so the absence above proves nothing")


@pytest.mark.slow
def test_doctor_only_is_a_real_mode(armed_worktree, tmp_path, herdr_stub):
    """Declared in the manifest, so it has to exist. A draft registered the
    action and never parsed the flag."""
    env = herdr_stub.env()
    env.update(
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
