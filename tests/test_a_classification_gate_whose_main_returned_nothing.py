"""`scripts/classification-health.py` had no failure path at all.

`main()` ended without a `return` on every branch and `if __name__ == "__main__":`
called it and discarded the result, so the process always exited 0. The CI step
at `.github/workflows/ci.yml` is named "Classification health (F-9.3)", which
reads as a gate; it was a report that could not fail on any input.

MEASURED 2026-09-02 before the fix, against a scratch tree holding one file and
a `config/routing-map.yaml` containing the unparseable `this: [is: broken`: the
script printed a complete-looking report ("Total files: 3, Corporate: 0,
CEO-only: 3") and exited 0. `load_routing_map()` fails closed to
`{"default": "private", "rules": {}}` on a parse error, which is correct for a
resolver and invisible in a report: every path then takes the default and the
summary looks full.

The three failure paths added here, and why each one is a refusal rather than a
warning:

- **The routing map did not load.** Every branch of this script resolves paths
  through it, so an empty map makes every printed verdict meaningless. This is
  the one place in the workspace that state has to be named, because the
  resolver itself is deliberately silent about it.
- **The walk returned zero files.** A workspace root pointed at the wrong
  directory returns an empty list with no error, and each count then prints a
  clean-looking zero. `scripts/ste-check.py` and `scripts/validate-crm-schema.py`
  already refuse this state in the same words; this script did not.
- **`--outputs-drift` found drift.** An opt-in diagnostic that printed its
  findings and exited 0. No workflow, hook or script passes the flag (checked),
  so gating it cannot turn CI red on its own.

The real tree must stay green, and one test below asserts exactly that: this
change gives the gate teeth without changing the verdict on a healthy
workspace.
"""
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "classification-health.py"

GOOD_MAP = 'default: engine\nrules:\n  "docs/": engine\n'


def _run(workspace: Path | None = None, *args: str) -> subprocess.CompletedProcess:
    """Run the checker, optionally against a scratch workspace root."""
    env = dict(os.environ)
    if workspace is not None:
        env["WORKSPACE_ROOT"] = str(workspace)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, cwd=str(ROOT), env=env,
    )


def _scratch(tmp_path: Path, routing_map: str | None) -> Path:
    """A minimal git workspace, because the walk asks git what to ignore."""
    ws = tmp_path / "ws"
    (ws / "config").mkdir(parents=True)
    (ws / "docs").mkdir()
    (ws / "docs" / "a.md").write_text("hello\n", encoding="utf-8")
    if routing_map is not None:
        (ws / "config" / "routing-map.yaml").write_text(routing_map, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True,
                   capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True, capture_output=True)
    return ws


# --------------------------------------------------------------------------
# The wiring itself. Without these the behaviour tests below can be satisfied
# by a `main()` that returns a code nobody passes to the interpreter.
# --------------------------------------------------------------------------

def test_the_entry_point_passes_mains_result_to_sys_exit():
    """`main()` alone discards the exit code. This is the defect, in one line."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    guards = [n for n in tree.body
              if isinstance(n, ast.If)
              and "__main__" in ast.unparse(n.test)]
    assert guards, "no `if __name__ == '__main__':` guard"
    body = "\n".join(ast.unparse(s) for s in guards[0].body)
    assert "sys.exit(main())" in body, (
        f"the entry point must pass main()'s result to sys.exit; it runs:\n{body}"
    )


def test_main_returns_an_exit_code_on_every_path():
    """Every `return` in `main()` must carry a value."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    bare = [r.lineno for r in ast.walk(main)
            if isinstance(r, ast.Return) and r.value is None]
    assert not bare, f"bare `return` in main() at line(s) {bare}"


# --------------------------------------------------------------------------
# Behaviour, both directions.
# --------------------------------------------------------------------------

def test_the_healthy_repository_still_passes():
    """The point of the gate is to fail on breakage, not on this workspace."""
    proc = _run()
    assert proc.returncode == 0, (
        f"the live tree must stay green.\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )


def test_json_output_is_still_parseable():
    """Diagnostics go to stderr so a `--json` consumer is unaffected."""
    proc = _run(None, "--json")
    payload = json.loads(proc.stdout)
    assert payload["total"] > 0


@pytest.mark.parametrize("routing_map, why", [
    ("this: [is: broken\n", "unparseable YAML"),
    (None, "the map file is absent"),
    ("default: engine\nrules: {}\n", "the map carries no rules"),
    ("default: nonsense\nrules:\n  \"docs/\": engine\n", "an illegal default"),
])
def test_a_map_that_did_not_load_is_a_refusal(tmp_path, routing_map, why):
    """Each of these produced a full report and exit 0 before the fix."""
    ws = _scratch(tmp_path, routing_map)
    proc = _run(ws)
    assert proc.returncode == 1, (
        f"{why} must fail.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "routing map" in proc.stderr, (
        f"the refusal must name the map. stderr:\n{proc.stderr}"
    )


def test_a_good_map_on_a_scratch_tree_passes():
    """The negative cases above must fail for the map, not for the scratch tree.

    Without this, a checker that refused every scratch workspace for an
    unrelated reason would satisfy all four of them.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        ws = _scratch(Path(td), GOOD_MAP)
        proc = _run(ws)
        assert proc.returncode == 0, (
            f"a scratch tree with a valid map must pass.\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


def test_an_empty_corpus_is_not_a_pass(tmp_path):
    """Zero files walked is the state that reads as a clean report."""
    ws = tmp_path / "empty"
    (ws / "config").mkdir(parents=True)
    (ws / "config" / "routing-map.yaml").write_text(GOOD_MAP, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True, capture_output=True)
    # The map file itself lives under a directory the walk keeps, so remove the
    # only walkable file after the map is read from disk by the child process:
    # instead, mark it ignored, which is the realistic shape of an empty walk.
    (ws / ".gitignore").write_text("config/\n", encoding="utf-8")
    proc = _run(ws)
    assert proc.returncode == 1, (
        f"an empty walk must refuse.\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    assert "empty corpus" in proc.stderr


def test_outputs_drift_findings_gate():
    """The drift branch printed its findings and exited 0.

    Measured on the live overlay: five `outputs/` subtrees carry more than the
    threshold with no explicit rule. No workflow, hook or script passes this
    flag, so gating it cannot turn CI red by itself.
    """
    proc = _run(None, "--outputs-drift")
    assert proc.returncode == 1, (
        f"real drift must fail.\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
