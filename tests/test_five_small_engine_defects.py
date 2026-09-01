"""Five small defects from the 2026-08-23 engine audit's LOW band.

Grouped because they share nothing but size. Each is verified against the thing
it describes, not against a restated copy of it.

1. `_executor_job`'s docstring said autonomous `note` cards "are disposed".
   `_sweep_non_gated_cards` does the opposite: its dispatch matches the notify
   tier alone, so an autonomous card falls through untouched, "never
   auto-disposed", per the CEO decision of 2026-06-04. Two docstrings in one
   file describing opposite dispositions for the same tier, and the tier
   routing IS the queue contract.

2. `start_daemon`'s docstring credited itself with the port default. The 31415
   lives in `DEFAULTS` in `scripts/bridge_daemon/config.py`; this function
   subscripts the key and would raise `KeyError` if the merge ever stopped
   supplying it. Naming the real owner is the whole fix.

3. `_run_llm_fit_report` did `result.stdout.strip().splitlines()[-1] if
   result.stdout`. A report that exits 0 and prints only `"\\n"` makes that
   truthy, then empty, then an IndexError -- past both `except` clauses, into
   APScheduler, converting a SUCCESSFUL run into a job error that feeds the
   heartbeat's recent-error count.

4. `audit-deps.py` looked for the venv interpreter only at `.venv/bin/python`.
   Windows puts it at `.venv/Scripts/python.exe`, so the re-exec returned
   silently and the pre-commit CVE gate took its graceful-skip path on every
   Windows machine. The function's own docstring says it exists so the gate
   "would not silently skip on exactly the machines where it matters".

5. The bootcamp roster merged its title banners across A:K over a 12-column
   table. Column L is "Rationale", the widest column in the sheet.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _docstring(path: Path, func: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            return ast.get_docstring(node) or ""
    raise AssertionError(f"{func} not found in {path.name}")


# --- 1. the tier routing is described the way it behaves ---------------------

DAEMON = ROOT / "scripts" / "bridge-daemon.py"


def test_the_sweep_still_leaves_autonomous_cards_alone(tmp_path):
    """Anchor: the guard below is about the docstring, so pin the code first.

    Pinned by RUNNING the sweep, not by grepping it. This test read the source
    for the literal `if tier == tool_risk.AUTONOMOUS:` until 2026-08-24, when
    that branch was deleted as provably behaviour-neutral -- and the comment
    recording the deletion quotes the branch, so a source grep would have gone
    on passing against its own tombstone. A line of code is not the behaviour;
    the behaviour is that a note card comes back untouched.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("bridge_daemon_anchor", DAEMON)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    applied: list[str] = []

    class _AQ:
        def list_action_queue(self, _root):
            return {"items": [{"id": "n1", "action_type": "note",
                               "status": "pending"}]}

        def apply_status(self, _root, aid, status, event=None):
            applied.append(aid)

    assert mod._sweep_non_gated_cards(tmp_path, _AQ()) == 0
    assert applied == [], "an autonomous note card was disposed by the sweep"


def test_the_executor_docstring_agrees_with_the_sweep():
    doc = _docstring(DAEMON, "_executor_job")
    assert "note" in doc, "the docstring stopped describing tier routing"
    assert not re.search(r"``note`` cards are disposed", doc), (
        "the docstring says note cards are disposed; the code leaves them for "
        "the CEO. Anyone reasoning about tier routing from here builds on a "
        "false premise about where note cards go."
    )
    assert "never auto-disposed" in doc or "LEFT for the CEO" in doc, doc


# --- 2. the port default is credited to its real owner -----------------------

def test_the_port_default_is_where_the_docstring_says_it_is():
    doc = _docstring(DAEMON, "start_daemon")
    assert "config.py" in doc, (
        "the docstring no longer names where the port default lives, so a "
        "reader credits this function with a fallback it does not implement"
    )
    cfg = (ROOT / "scripts" / "bridge_daemon" / "config.py").read_text(encoding="utf-8")
    m = re.search(r'"port_range_start":\s*(\d+)', cfg)
    assert m, "DEFAULTS no longer carries port_range_start; the subscript can KeyError"
    assert m.group(1) in doc, (
        f"the docstring quotes a port default the config does not set (config "
        f"says {m.group(1)})"
    )


# --- 3. a silent successful report is not an error ---------------------------

def test_a_whitespace_only_report_does_not_raise():
    """Reproduce the expression in isolation; the job itself needs a scheduler."""
    for stdout in ("\n", "   \n\t", "", None):
        lines = (stdout or "").strip().splitlines()
        assert (lines[-1] if lines else "") == ""      # the shape now used
    src = DAEMON.read_text(encoding="utf-8")
    assert ".splitlines()[-1] if result.stdout" not in src, (
        "the old expression is back: `\"\\n\"` is truthy, `.strip()` empties it, "
        "and `[-1]` on the empty list raises past both except clauses"
    )


def test_the_last_line_is_still_logged_when_there_is_one():
    lines = "first\nlast\n".strip().splitlines()
    assert lines[-1] == "last"


# --- 4. the CVE gate finds the venv on both layouts --------------------------

AUDIT = ROOT / "scripts" / "audit-deps.py"


def test_both_venv_interpreter_layouts_are_checked():
    src = AUDIT.read_text(encoding="utf-8")
    assert '"bin" / "python"' in src
    assert '"Scripts" / "python.exe"' in src, (
        "only the POSIX layout is checked, so the CVE gate silently skips on "
        "every Windows machine -- the failure this function exists to prevent"
    )


def test_the_docstring_still_states_the_promise_being_kept():
    doc = _docstring(AUDIT, "_reexec_in_venv_if_needed")
    assert "silently skip" in doc


def _audit_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_audit_deps_under_test", AUDIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("layout", [("bin", "python"), ("Scripts", "python.exe")])
def test_either_venv_layout_is_the_one_re_execed(monkeypatch, tmp_path, layout):
    """Drive the SELECTION, not the source text.

    The two tests above ask whether both paths appear in the file. That is a
    grep, and a grep cannot see which of them the code goes on to pick.
    MEASURED 2026-09-01: rewriting the picker from
    `next((c for c in candidates if c.exists()), None)` to
    `candidates[0] if candidates[0].exists() else None` left both literals in
    the list, both source assertions green, and the Windows layout unreachable
    again -- the whole defect back, under a passing guard.

    Here only ONE layout exists on disk per case, so a picker that looks at a
    fixed element cannot answer both.
    """
    mod = _audit_module()
    venv = tmp_path / ".venv" / layout[0]
    venv.mkdir(parents=True)
    interpreter = venv / layout[1]
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")

    execed: list[list[str]] = []
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "_have", lambda _name: False)
    monkeypatch.delenv("_AUDIT_DEPS_REEXEC", raising=False)
    monkeypatch.setattr(mod.sys, "executable", str(tmp_path / "some-other-python"))
    monkeypatch.setattr(mod.sys, "argv", ["audit-deps.py"])
    monkeypatch.setattr(mod.os, "execv", lambda path, argv: execed.append([path, *argv]))

    mod._reexec_in_venv_if_needed()

    assert execed, f"no re-exec for the {layout[0]} layout; the CVE gate skips there"
    assert execed[0][0] == str(interpreter)


def test_no_venv_at_all_does_not_re_exec(monkeypatch, tmp_path):
    """The negative case. A picker that always re-execs would pass the two above
    while sending the gate at a path that is not there."""
    mod = _audit_module()
    execed: list = []
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "_have", lambda _name: False)
    monkeypatch.delenv("_AUDIT_DEPS_REEXEC", raising=False)
    monkeypatch.setattr(mod.os, "execv", lambda path, argv: execed.append(path))

    mod._reexec_in_venv_if_needed()

    assert execed == []


def test_an_already_reexeced_run_does_not_re_exec_again(monkeypatch, tmp_path):
    """The guard flag, so the re-exec cannot become a loop."""
    mod = _audit_module()
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    execed: list = []
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "_have", lambda _name: False)
    monkeypatch.setenv("_AUDIT_DEPS_REEXEC", "1")
    monkeypatch.setattr(mod.os, "execv", lambda path, argv: execed.append(path))

    mod._reexec_in_venv_if_needed()

    assert execed == []


# --- 5. the banner spans the table ------------------------------------------

ROSTER = ROOT / "scripts" / "bootcamp-roster.py"


def test_the_title_banner_covers_every_column():
    src = ROSTER.read_text(encoding="utf-8")
    merges = re.findall(r'merge_cells\("A(\d+):([A-Z])\1"\)', src)
    assert merges, "the banner merges moved; this guard is unanchored"
    # Count the header cells the sheet actually writes.
    block = src[src.index("headers = ["):]
    block = block[:block.index("]") + 1]
    n_cols = len(ast.literal_eval(block.split("=", 1)[1].strip()))
    last = chr(ord("A") + n_cols - 1)
    for row, col in merges:
        assert col == last, (
            f"row {row}'s banner ends at column {col} while the table runs to "
            f"{last} ({n_cols} headers)"
        )
