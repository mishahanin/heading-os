"""A bootstrap test that could not run in the checkout where the work happens.

`tests/test_yard_bootstrap_lint.py::test_the_status_file_it_reads_is_the_one_
inside_the_worktree` opened `ROOT / ".claude" / ".yard-bootstrap-status"` and
asserted it did not exist, under the message "HELM carries a YARD status file".
`ROOT` is not HELM. It is whichever checkout the suite was launched from, and
all engine work in this workspace happens in a YARD, where that receipt exists
by design.

MEASURED 2026-09-03 in the YARD at `.yard/.heading-os/test-123`:

    E  AssertionError: HELM carries a YARD status file; it should never have one
    E  assert not True
    E   +  where True = exists()
    E   +    where exists = PosixPath('.../test-123/.claude/.yard-bootstrap-status').exists

The receipt had been written at 07:06:19 by the bootstrap that created the
worktree. The test therefore failed in every YARD, always, and passed only in a
bare checkout that had never been provisioned. A test that goes red wherever
the work is done is a test that gets read as noise and then ignored, which is
worse than not having it: the preceding commit on this branch had to argue in
its own message that this failure was not its fault.

The repair asks `scripts/utils/clone_guard.is_main_clone()` instead of assuming.
It is a REWRITE rather than a skip. A skip would have removed the assertion from
the only checkout that runs it, and the YARD case is not merely tolerable there:
a provisioned worktree's receipt is a genuine CONFOUNDER, so the run that
follows proves more from a YARD than it ever did from HELM. Skipping would have
thrown away the stronger half.

Both directions are exercised below without provisioning a worktree, by driving
`ambient_receipt_verdict` over all four (clone shape x receipt present) states.

Run: python3 -m pytest tests/test_a_test_that_assumed_the_checkout_it_ran_from_was_helm.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.clone_guard import is_main_clone  # noqa: E402
from scripts.utils.repo_files import read_sources  # noqa: E402

TARGET = ROOT / "tests" / "test_yard_bootstrap_lint.py"

lint = pytest.importorskip("tests.test_yard_bootstrap_lint")


def _target_source() -> str:
    vanished: list[Path] = []
    texts = dict(read_sources([TARGET], vanished))
    assert not vanished, f"the target test vanished mid-read: {vanished}"
    return texts[TARGET]


def _receipt(root: Path) -> Path:
    return root / ".claude" / ".yard-bootstrap-status"


# ============================================================
# The verdict function, over all four states
# ============================================================

def _make(tmp_path: Path, *, receipt: bool) -> Path:
    root = tmp_path / "checkout"
    (root / ".claude").mkdir(parents=True)
    if receipt:
        _receipt(root).write_text(
            '{"status":"ok","step":11,"timestamp":"x","version":"5.0"}',
            encoding="utf-8")
    return root


def test_a_main_clone_carrying_a_receipt_is_the_finding(tmp_path):
    """The one state that is a defect. This is what the original assertion
    meant to catch, and it still catches it."""
    root = _make(tmp_path, receipt=True)
    assert lint.ambient_receipt_verdict(root, True) == "main-clone-carries-receipt"


def test_a_clean_main_clone_is_fine(tmp_path):
    root = _make(tmp_path, receipt=False)
    assert lint.ambient_receipt_verdict(root, True) == "main-clone-clean"


def test_a_worktree_receipt_is_a_confounder_not_a_finding(tmp_path):
    """The state that used to fail the suite. It is expected, and it is the
    setting in which the assertion that follows it proves the most."""
    root = _make(tmp_path, receipt=True)
    assert lint.ambient_receipt_verdict(root, False) == (
        "worktree-receipt-is-a-confounder")


def test_a_bare_worktree_is_named_separately(tmp_path):
    """Not lumped in with the case above: with no receipt present there is no
    confounder, so the downstream assertion is the weaker check it is in HELM,
    and the two deserve different names."""
    root = _make(tmp_path, receipt=False)
    assert lint.ambient_receipt_verdict(root, False) == "worktree-no-receipt"


def test_only_the_main_clone_state_is_ever_a_finding(tmp_path):
    """The property the caller asserts on, stated once over the whole space.

    A verdict function that returned the finding string for a worktree would
    restore the defect exactly, and each test above alone would not catch it.
    """
    findings = set()
    for receipt in (True, False):
        for main in (True, False):
            root = _make(tmp_path / f"{receipt}-{main}", receipt=receipt)
            verdict = lint.ambient_receipt_verdict(root, main)
            if verdict == "main-clone-carries-receipt":
                findings.add((receipt, main))
    assert findings == {(True, True)}, findings


# ============================================================
# The caller asks, rather than assuming
# ============================================================

PREDICATE = "is_main_clone"
OWNER = "scripts.utils.clone_guard"


def test_the_target_calls_the_predicate_rather_than_assuming():
    """Asked of the AST, not of the text.

    A mention in a comment or a docstring is not evidence that the code calls
    it, which is the distinction `.claude/rules/scope-claims.md` draws for
    exactly this kind of claim.
    """
    tree = ast.parse(_target_source())
    called = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert PREDICATE in called, (
        "the bootstrap lint no longer asks which clone it is running in")


def test_the_predicate_is_the_shared_one_and_not_a_local_stub():
    """Calling the NAME is not the same as calling the guard.

    A mutation that replaced the import with
    `def is_main_clone(_p=None): return True` SURVIVED the assertion above:
    the call is still there, still spelled the same, and now always answers
    "main clone". Measured 2026-09-03. That is the shape of a local
    reimplementation, which this repository already names as its dominant
    defect: the second copy is the one that stops being fixed.
    """
    tree = ast.parse(_target_source())

    imported = any(
        isinstance(node, ast.ImportFrom) and node.module == OWNER
        and any(a.name == PREDICATE for a in node.names)
        for node in ast.walk(tree))
    assert imported, f"{PREDICATE} is not imported from {OWNER}"

    rebound = []
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == PREDICATE):
            rebound.append(f"def {PREDICATE} at line {node.lineno}")
        if isinstance(node, ast.Assign):
            rebound += [f"{PREDICATE} = ... at line {node.lineno}"
                        for t in node.targets
                        if isinstance(t, ast.Name) and t.id == PREDICATE]
    assert not rebound, (
        f"{PREDICATE} is shadowed locally, so the import proves nothing: "
        f"{rebound}")


def test_the_shadow_detector_sees_a_planted_stub():
    """The negative case for the check above, on source it controls."""
    planted = ast.parse(
        f"from {OWNER} import {PREDICATE}\n"
        f"def {PREDICATE}(_p=None): return True\n")
    shadows = [n for n in ast.walk(planted)
               if isinstance(n, ast.FunctionDef) and n.name == PREDICATE]
    assert shadows, "the detector would not see a local override"


def _live_strings(tree: ast.AST) -> list[str]:
    """Every string literal the code can EMIT, docstrings excluded.

    Asked of the AST rather than of the text, per development-standards
    obligation 8. A substring scan over the source goes red the moment the fix
    quotes the old message to explain it -- which is exactly what happened on
    the first draft of this test, against the repair's own docstring. Punishing
    an explanation teaches people to stop explaining.
    """
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def test_the_unconditional_assumption_is_gone():
    """The literal shape of the defect, refused where it would still run."""
    tree = ast.parse(_target_source())
    emitted = _live_strings(tree)
    assert emitted, "the AST walk found no live strings; it stopped measuring"
    offenders = [s for s in emitted if "HELM carries a YARD status file" in s]
    assert not offenders, (
        f"this message names HELM unconditionally about a path that is often "
        f"a worktree: {offenders}")


def test_the_defect_is_refused_in_code_not_only_in_prose():
    """The companion negative: the detector above must see a real occurrence.

    Without this, `_live_strings` returning only harmless strings would satisfy
    the assertion for any target at all.
    """
    planted = ast.parse(
        '"""HELM carries a YARD status file — explaining it is fine."""\n'
        'def f():\n'
        '    assert False, "HELM carries a YARD status file"\n')
    emitted = _live_strings(planted)
    assert any("HELM carries a YARD status file" in s for s in emitted)
    assert not any(s.startswith("HELM carries a YARD status file —")
                   for s in emitted), "the docstring must have been excluded"


# ============================================================
# The end-to-end: it now runs where it always failed
# ============================================================

def test_the_repaired_test_is_not_re_run_here():
    """Deliberately NOT driving the target node from this file.

    A first draft spawned `pytest <target node>` as a subprocess to prove the
    repair works in this checkout. It does, but the suite already runs that
    node, from this same checkout, on every run -- so the subprocess bought no
    information and cost a second full bootstrap. MEASURED 2026-09-03: it
    pushed this file to 230 s and, under `-n auto`, contributed to three
    timeout failures in the suite.

    What is asserted instead is that the node exists and is collectible, which
    is the part a rename would break silently.
    """
    tree = ast.parse(_target_source())
    names = {n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "test_the_status_file_it_reads_is_the_one_inside_the_worktree" in names
    assert "ambient_receipt_verdict" in names


def test_this_checkout_is_the_shape_the_measurement_described():
    """Names what the run above actually covered, rather than implying both.

    From a YARD this is the state that used to fail. From HELM it is the state
    that always passed. Either is a real observation; asserting which one
    happened keeps the test from reading as coverage of both.
    """
    main = is_main_clone(ROOT)
    receipt_present = _receipt(ROOT).exists()
    if main:
        assert not receipt_present, (
            "the main clone carries a YARD receipt; that is the finding")
    else:
        # No assertion on presence: a bare worktree legitimately has none.
        assert lint.ambient_receipt_verdict(ROOT, main) in (
            "worktree-receipt-is-a-confounder", "worktree-no-receipt")
