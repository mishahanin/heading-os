"""Bootstrap tests reached the real herdr, which is one server for the machine.

`yard-bootstrap.sh` calls herdr at six places: `workspace get`,
`workspace rename`, `pane report-metadata` (every step, through `badge`),
`pane run`, and two `notification show`. Every test that executes the script
made those calls against the operator's live server.

MEASURED 2026-09-03. A mutation run over the bootstrap tests put

    YARD: the engine/data contour is broken
    step 6: the PreToolUse walls are not registered in this copy

on the operator's screen, from a test run, describing a worktree that was
working correctly. That is the visible half.

The silent half is worse. The script reads `WS_ID="${HERDR_WORKSPACE_ID:-}"`
and `PANE_ID="${HERDR_PANE_ID:-}"` from the environment; the test helpers built
their env from `os.environ`; and inside a herdr-managed session both are set and
name the operator's OWN workspace and pane. So `workspace rename "$WS_ID"` and
`pane run "$PANE_ID" ...` were aimed at a live pane that belonged to whatever
else was running at the time. Nothing in the suite would have reported that.

Same shape as the `git worktree prune` defect repaired in `tests/conftest.py`
the day before: an operation that reads as scoped to one test and is in fact
scoped to the whole machine.

The seam existed and nothing used it -- line 41 of the bootstrap is
`HERDR="${HERDR_BIN_PATH:-herdr}"`. One fixture now fills it for every caller.

Two directions, and the second is the one with teeth:

* the stub is reached, records the calls, and the bootstrap still gets where it
  is supposed to get;
* NO test anywhere reaches the real binary. Asserted mechanically rather than
  by intent: the corpus is re-run with a POISONED `herdr` first on PATH, one
  that records any call and exits non-zero. The suite must stay green and the
  poison log must stay empty.

Run: python3 -m pytest tests/test_a_bootstrap_test_that_talked_to_the_shared_herdr_server.py
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.conftest import write_herdr_stub  # noqa: E402

CORPUS = ROOT / "tests" / "test_yard_bootstrap_lint.py"
BOOTSTRAP = (ROOT / "scripts" / "herdr" / "heading-os-yard" / "yard-bootstrap.sh")


# ============================================================
# The stub is real, and it is reached
# ============================================================

def test_the_stub_records_a_call_and_succeeds(tmp_path):
    stub = write_herdr_stub(tmp_path / "s")
    proc = subprocess.run([str(stub.binary), "pane", "report-metadata", "p1"],
                          capture_output=True)
    assert proc.returncode == 0
    assert stub.calls == [["pane", "report-metadata", "p1"]]


def test_the_stub_round_trips_an_argument_holding_spaces(tmp_path):
    """A workspace label is one. A space-joined log would not survive it."""
    stub = write_herdr_stub(tmp_path / "s")
    subprocess.run([str(stub.binary), "workspace", "rename", "ws1",
                    "yard: test-123"], capture_output=True)
    assert stub.calls == [["workspace", "rename", "ws1", "yard: test-123"]]


def test_the_stub_env_points_the_bootstrap_at_it_and_drops_the_identifiers(
        tmp_path, monkeypatch):
    """`HERDR_BIN_PATH` alone makes the calls harmless. Dropping the workspace
    and pane ids means there is no live target left to name."""
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "ws-live")
    monkeypatch.setenv("HERDR_PANE_ID", "pane-live")
    stub = write_herdr_stub(tmp_path / "s")
    env = stub.env()
    assert env["HERDR_BIN_PATH"] == str(stub.binary)
    assert "HERDR_WORKSPACE_ID" not in env
    assert "HERDR_PANE_ID" not in env


def test_the_bootstrap_reads_the_seam_the_fixture_fills():
    """If the script stopped honouring `HERDR_BIN_PATH`, the fixture would go on
    passing it and every call would silently return to the real binary."""
    src = BOOTSTRAP.read_text(encoding="utf-8")
    assert 'HERDR="${HERDR_BIN_PATH:-herdr}"' in src
    # And nothing calls the bare name behind the variable's back.
    bare = [n for n, line in enumerate(src.splitlines(), 1)
            if line.strip().startswith("herdr ")
            or ' herdr ' in line and '"$HERDR"' not in line
            and not line.strip().startswith("#")]
    assert not bare, f"these lines invoke herdr without the seam: {bare}"


# ============================================================
# The direction with teeth: nothing reaches the real binary
# ============================================================

def test_no_bootstrap_test_reaches_the_real_herdr(tmp_path):
    """Re-run the corpus with a poisoned `herdr` first on PATH.

    Two assertions, and they catch different failures. The suite must stay
    GREEN, which is the operator's stated requirement. And the poison log must
    stay EMPTY, which is the stronger half: almost every herdr call in the
    bootstrap ends in `>/dev/null 2>&1 || true`, so a non-zero exit alone would
    be swallowed and a green suite would prove nothing. The log is what
    actually witnesses a call escaping.
    """
    poison_dir = tmp_path / "poison"
    poison = write_herdr_stub(poison_dir, exit_code=3)

    env = dict(os.environ)
    env["PATH"] = f"{poison_dir}{os.pathsep}{env['PATH']}"
    # Do NOT set HERDR_BIN_PATH here: the point is to catch a caller that never
    # sets it and falls through to the PATH lookup.
    env.pop("HERDR_BIN_PATH", None)

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(CORPUS.relative_to(ROOT)),
         "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=900, env=env)

    assert not poison.calls, (
        f"the bootstrap corpus invoked the real herdr {len(poison.calls)} "
        f"time(s): {poison.calls[:5]}")
    assert proc.returncode == 0, proc.stdout[-3000:]


def test_the_poison_would_be_seen_if_something_called_it(tmp_path):
    """The negative case for the test above.

    A poison that recorded nothing, or a PATH that never reached it, would make
    that assertion vacuous. This drives the same wiring by hand.
    """
    poison_dir = tmp_path / "poison"
    poison = write_herdr_stub(poison_dir, exit_code=3)
    env = dict(os.environ)
    env["PATH"] = f"{poison_dir}{os.pathsep}{env['PATH']}"

    proc = subprocess.run(["bash", "-c", 'herdr notification show hi || true'],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, "the `|| true` shape is what hides the exit code"
    assert poison.calls == [["notification", "show", "hi"]], (
        "the poison did not witness a call that definitely happened")


# ============================================================
# Every executor of the script goes through the fixture
# ============================================================

def test_every_bootstrap_execution_in_the_corpus_is_wired_to_the_stub():
    """Asked of the AST: each test that runs the script names `herdr_stub`.

    A future test that spawns the bootstrap without the fixture is the way this
    defect comes back, and it comes back silently.
    """
    tree = ast.parse(CORPUS.read_text(encoding="utf-8"))
    offenders = []
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = ast.dump(node)
        runs_script = ("BOOTSTRAP" in body and "subprocess" in body) or \
                      "_run_against" in body
        if not runs_script:
            continue
        # `bash -n` parses the file without executing it; it reaches no server.
        if node.name == "test_the_shell_files_parse":
            continue
        checked += 1
        args = {a.arg for a in node.args.args}
        if "herdr_stub" not in args:
            offenders.append(node.name)
    assert checked >= 5, (
        f"only {checked} bootstrap-executing functions found in {CORPUS.name}; "
        f"the walk collapsed and this test would pass over nothing")
    assert not offenders, (
        f"these execute the bootstrap without the herdr stub: {offenders}")
