#!/usr/bin/env python3
"""`check-gate-integrity` asked for one spelling of a path and got the other.

Question 2 of `scripts/check-gate-integrity.py` is "is the script this hook
invokes named, as a literal string, anywhere under `tests/`?". It asked by
substring, for exactly `scripts/<name>.py`. A test that reaches its target the
way every test in this tree reaches it -- `ROOT / "scripts" / "lint-ratchet.py"`
-- never writes that substring, so the check could not see it.

MEASURED 2026-09-08 on this checkout, before the fix, from
`.venv/bin/python scripts/check-gate-integrity.py --json`:

    "hook:sentinel-integration-tests":
        "scripts/run-integration-tests.py is named by no file under tests/",
    "hook:lint-ratchet":
        "scripts/lint-ratchet.py is named by no file under tests/"

Both are false, and each had been frozen in BASELINE since 2026-09-02 with the
reason "Real gap, pre-existing, and fixing it is writing a behavioural test for
the ratchet". That test already existed when the reason was written:
`tests/test_lint_ratchet.py` drives `cmd_check()` to return 1 on a new finding
and on a bucket that grew, and `tests/test_a_dry_run_that_said_the_case_did_not_exist.py`
drives `run_tests()` over pytest exit codes 2 through 6 and 77. The refusals had
been observed; only the gate that reports on gates could not tell.

The cost is the one the file's own docstring names for everyone else: a claim
about coverage that is wrong in the reassuring direction for the auditor and in
the alarming direction here, which is how a finding gets re-filed every night
against code that is already correct.

Run: .venv/bin/python -m pytest tests/test_a_naming_check_that_could_not_see_a_path_join.py -q
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "gate_integrity_naming", ROOT / "scripts" / "check-gate-integrity.py")
gate_integrity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate_integrity)


def _hook(script: str) -> dict:
    """A local hook whose entry invokes `script` and whose scope matches."""
    return {"id": "example", "entry": f".venv/bin/python {script} check",
            "always_run": True}


TRACKED = ["scripts/lint-ratchet.py", "tests/test_example.py"]


# ============================================================
# 1 - the spelling every test in this tree actually uses
# ============================================================

@pytest.mark.parametrize("corpus,label", [
    ('subprocess.run([sys.executable, str(ROOT / "scripts" / "lint-ratchet.py"), "check"])',
     "Path join, double quotes"),
    ("src = (ROOT / 'scripts' / 'lint-ratchet.py').read_text()",
     "Path join, single quotes"),
    ('spec_from_file_location("lint_ratchet", ROOT/"scripts"/"lint-ratchet.py")',
     "Path join, no spaces around the operator"),
    ('proc = run(["python", "scripts/lint-ratchet.py", "check"])',
     "the plain posix path, which already worked"),
    ('# see scripts/lint-ratchet.py for the ratchet',
     "a bare mention in a comment, which the docstring says counts"),
])
def test_a_test_that_names_the_script_clears_question_two(corpus, label):
    found = gate_integrity.findings(
        [_hook("scripts/lint-ratchet.py")], TRACKED, corpus)
    assert found == {}, f"{label}: the naming check missed it -> {found}"


# ============================================================
# 2 - the other direction: the guard still refuses
# ============================================================

@pytest.mark.parametrize("corpus,label", [
    ("", "an empty corpus"),
    ("def test_something_else():\n    assert True\n", "a corpus about other code"),
    ('mod = _load("lint-ratchet.py")',
     "the BASENAME alone, with no scripts/ segment -- five basenames under "
     "scripts/ are not unique (paths.py, pulse.py, search.py, state.py, "
     "__init__.py), so a basename is not evidence that THIS script is named"),
    ('src = (ROOT / "scripts" / "lint-ratchet-other.py").read_text()',
     "a longer name that merely starts with this one"),
    ('src = (ROOT / "docs" / "lint-ratchet.py").read_text()',
     "the right basename under the wrong directory"),
    ('src = (ROOT / "scripts" / "other.py").read_text()  # unlike lint-ratchet.py',
     "both segments on one line but belonging to different paths -- a "
     "separator allowed to swallow arbitrary text would join them into a "
     "phantom match, and `.` not spanning newlines must not be what saves it"),
])
def test_a_script_no_test_names_is_still_a_finding(corpus, label):
    found = gate_integrity.findings(
        [_hook("scripts/lint-ratchet.py")], TRACKED, corpus)
    assert found == {
        "hook:example": "scripts/lint-ratchet.py is named by no file under tests/"
    }, f"{label}: the guard stopped refusing -> {found}"


def test_the_scope_half_of_the_gate_is_untouched():
    """A `files:` regex matching nothing still wins over question 2.

    Widening the naming check must not let a vacuously-scoped hook through the
    first branch on its way to a corpus that happens to name it.
    """
    hook = {"id": "example", "files": r"^no/such/dir/.*\.py$",
            "entry": ".venv/bin/python scripts/lint-ratchet.py check"}
    found = gate_integrity.findings(
        [hook], TRACKED, 'ROOT / "scripts" / "lint-ratchet.py"')
    assert list(found) == ["hook:example"]
    assert "matches no tracked path" in found["hook:example"]


# ============================================================
# 3 - the real entry point, over the real repository
# ============================================================

def test_the_real_gate_no_longer_reports_the_two_phantom_findings():
    """Drive the shipped command and read the exit code and the JSON it prints."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check-gate-integrity.py"),
         "--check", "--json"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)

    # A floor outside every loop: a parse that found no hooks would satisfy
    # every assertion below. MEASURED 2026-09-08: 25 local hooks.
    assert report["hooks"] >= gate_integrity.MIN_HOOKS, report

    for key in ("hook:lint-ratchet", "hook:sentinel-integration-tests"):
        assert key not in report["findings"], (
            f"{key} is still reported as named by no test, but "
            f"tests/test_lint_ratchet.py and "
            f"tests/test_a_dry_run_that_said_the_case_did_not_exist.py "
            f"both drive those scripts to a non-zero exit")

    assert report["stale_baseline"] == [], (
        "BASELINE still freezes a finding that no longer fires; the entries go "
        "out with the commit that fixed them")


def test_the_baseline_no_longer_carries_the_two_refuted_reasons():
    """The frozen reason claimed no behavioural test existed. One did."""
    for key in ("hook:lint-ratchet", "hook:sentinel-integration-tests"):
        assert key not in gate_integrity.BASELINE, (
            f"{key}: the reason frozen on 2026-09-02 says the gate is named by "
            f"no test. It was already false when it was written.")
