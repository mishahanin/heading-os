"""The suite failed 97 times in a YARD and passed in HELM, from assumptions.

MEASURED 2026-09-03, same commit, two checkouts:

    HELM  (/home/.../claude-workspaces/.heading-os)      24377 passed, 1 skipped, 0 failed
    YARD  (.yard/.heading-os/test-123)                   97 failed

Not one of the 97 was a code regression. Every one was a test assumption about
WHERE the suite runs, in two shapes:

    25  treat `Path(__file__).parents[1]` AS the main clone -- feeding it to
        `is_main_clone()` expecting True, contrasting a synthetic yard against
        it, or requiring a write into it to be refused. Each polarity inverts
        when the launching checkout is itself a worktree.
    72  drive a script whose `main()` calls `require_main_clone(__file__)`,
        which exits 2 from a worktree before the behaviour under test runs.
        45 load it in-process; 27 spawn a child, where no patch can reach.

That matters more than a red number. A YARD exists so engine work can be judged
in isolation, and a YARD with 97 red tests cannot confirm anything: every change
is then reviewed by eye. The repair is three fixtures in `tests/conftest.py`,
not 27 local edits -- a fix that lands in one of N copies is this repository's
dominant defect shape.

WHAT THIS FILE HOLDS. The 27 child-process cases are genuinely unrunnable
outside the main clone, so they skip. A skip nobody counts is a green suite that
checks nothing, which is the failure this file exists to refuse: it pins the
skip count against the measured number, requires every one of them to carry the
one shared reason, and refuses a skip written any other way.

Run: python3 -m pytest tests/test_a_suite_that_could_not_run_where_the_work_happens.py
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.clone_guard import is_main_clone  # noqa: E402
from scripts.utils.repo_files import git_index_paths, read_sources  # noqa: E402
from tests.conftest import MAIN_CLONE_SKIP  # noqa: E402

# MEASURED 2026-09-03: the seven files whose child-process cases cannot run
# outside the main clone. Named rather than counted, so a NEW file that starts
# skipping has to be added here deliberately and is visible in the diff.
# MEASURED 2026-09-03 from this YARD, by running these seven files with `-rs`
# and counting the shared reason string. Per file, not one total: a total lets a
# skip appear in one file and vanish from another without moving, which is the
# shape a single number cannot see. 19 in all.
#
# These numbers were GUESSED first, at 27 and 7, and both were wrong. The
# guesses came from a classification rather than from a run, which is the exact
# thing this repository's second obligation forbids.
#
# The replacement was then measured SLOPPILY, with `grep -c` over the report,
# which counts LINES and not the `[N]` multiplicity pytest prints beside a
# parametrised skip. That said 18. The per-file map below, read the way the
# test reads it, says 19. The test was stricter than the shell one-liner used
# to write it, which is the argument for pinning the map rather than a total.
CLONE_GATED_SKIPS = {
    "tests/test_a_data_overlay_guard_that_overwrote_what_was_there.py": 8,
    "tests/test_three_admin_tools_that_died_one_frame_below_the_seam.py": 3,
    "tests/test_memory_expiry.py": 2,
    "tests/integration/test_aggregate_crm_per_exec.py": 2,
    "tests/test_a_flag_the_code_never_read.py": 2,
    "tests/test_timer_timezone.py": 1,
    "tests/test_a_bootstrap_that_could_not_be_run_twice.py": 1,
    # Added 2026-09-03 with the daemon clone gates. All three spawn
    # `scripts/sentinel.py` as a CHILD, so the child re-imports the real guard
    # and `disarm_clone_guard` cannot reach it; each asserts on the script AS
    # INVOKED (rendered --help, argparse's "unrecognized arguments"), so
    # converting the call to an in-process `main()` would change the subject.
    "tests/test_three_promises_the_code_could_not_keep.py": 2,
    "tests/test_a_dry_run_that_was_not_dry.py": 1,
}
CLONE_GATED_FILES = set(CLONE_GATED_SKIPS)


def _test_sources():
    paths = [ROOT / p for p in git_index_paths(ROOT)
             if p.startswith("tests/") and p.endswith(".py")]
    vanished: list[Path] = []
    for path, text in read_sources(paths, vanished, errors="ignore"):
        yield path.relative_to(ROOT).as_posix(), text


# ============================================================
# Every clone-gated skip goes through the one fixture
# ============================================================

def test_the_shared_skip_reason_exists_and_says_why():
    assert "worktree" in MAIN_CLONE_SKIP
    assert "child process" in MAIN_CLONE_SKIP
    assert "MAIN_CLONE_SKIP" in MAIN_CLONE_SKIP, (
        "the reason must name itself so a reader can find the one definition")


def test_no_test_writes_its_own_clone_skip():
    """A hand-written skip escapes the count below.

    Asked of the AST rather than of the text, so a file that QUOTES the phrase
    while explaining it -- this one does -- is not punished for explaining.
    """
    offenders = []
    scanned = 0
    for rel, text in _test_sources():
        if rel == Path(__file__).relative_to(ROOT).as_posix():
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        scanned += 1
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "skip"):
                continue
            for arg in list(node.args) + [k.value for k in node.keywords]:
                if not (isinstance(arg, ast.Constant)
                        and isinstance(arg.value, str)):
                    continue
                low = arg.value.lower()
                if "main clone" in low or "helm" in low:
                    offenders.append(f"{rel}:{node.lineno}")
    assert scanned >= 200, (
        f"only {scanned} test modules parsed; the walk collapsed and this "
        f"test would pass over nothing")
    assert not offenders, (
        f"these write their own clone skip instead of requesting the "
        f"`main_clone_only` fixture, so they escape the count: {offenders}")


def test_the_gated_files_all_request_the_fixture():
    """Each named file must actually use the fixture it is listed for."""
    missing = []
    for rel in sorted(CLONE_GATED_FILES):
        path = ROOT / rel
        if not path.is_file():
            missing.append(f"{rel} (gone)")
            continue
        if "main_clone_only" not in path.read_text(encoding="utf-8"):
            missing.append(f"{rel} (does not request main_clone_only)")
    assert not missing, missing


def test_no_file_uses_the_fixture_without_appearing_in_the_map():
    """The direction that was missing, and its absence cost a real undercount.

    The forward check above asks "does every mapped file request the fixture?".
    Nothing asked the reverse, so a NEW user of `main_clone_only` was invisible
    to the count. MEASURED 2026-09-03: adding the daemon clone gates sent three
    more tests through the fixture, across two files in neither the map nor any
    assertion, and the pinned total stayed green while under-counting by three.
    A skip nobody counts is a green suite that checks nothing, which is the
    whole premise of this file.

    Asked of the AST -- a test's PARAMETER list -- so the prose above, which
    names the fixture repeatedly, is not mistaken for a use of it.
    """
    escaped = []
    scanned = 0
    for rel, text in _test_sources():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        scanned += 1
        uses = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
            and any(a.arg == "main_clone_only" for a in node.args.args)
            for node in ast.walk(tree))
        if uses and rel not in CLONE_GATED_SKIPS:
            escaped.append(rel)

    # A floor: with no sources read this passes over a suite it never opened.
    # MEASURED 2026-09-03: 9 mapped files, and the tree holds far more tests.
    assert scanned >= 500, f"only {scanned} test module(s) parsed"
    assert len(CLONE_GATED_SKIPS) == 9, sorted(CLONE_GATED_SKIPS)

    assert not escaped, (
        f"these request `main_clone_only` and are absent from "
        f"CLONE_GATED_SKIPS, so their skips are counted by nothing: {escaped}. "
        f"Add each with its measured skip count and raise the total below.")


# ============================================================
# The count, pinned
# ============================================================

def test_the_clone_gated_skips_are_what_was_measured():
    """The numbers, not the intention, and per file rather than in total.

    Driven as a child run over the nine files, reading pytest's own skip
    report. MEASURED 2026-09-03 from this YARD: 22 in all, distributed as
    `CLONE_GATED_SKIPS` above. From the main clone the same run skips none of
    them, and that direction is asserted rather than assumed.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *sorted(CLONE_GATED_FILES),
         "-q", "--no-header", "--color=no", "-p", "no:cacheprovider",
         "-p", "no:xdist", "-rs"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=1800)
    assert " failed" not in proc.stdout, proc.stdout[-3000:]

    seen: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        if MAIN_CLONE_SKIP not in line or not line.startswith("SKIPPED"):
            continue
        count = int(line.split("]")[0].split("[")[1])
        rel = line.split("] ", 1)[1].split(":", 1)[0]
        seen[rel] = seen.get(rel, 0) + count

    if is_main_clone(ROOT):
        assert not seen, (
            "running in the main clone, yet tests skipped as if in a worktree: "
            f"{seen}")
        return

    assert seen == CLONE_GATED_SKIPS, (
        f"clone-gated skips moved.\n  measured 2026-09-03: {CLONE_GATED_SKIPS}\n"
        f"  now:                {seen}\n"
        f"A rise means a test stopped running without anyone deciding it "
        f"should. A fall means one was repaired and this table was not "
        f"updated. Either way it is a decision, not a drift.")
    # Stated separately from the map, on purpose: a typo in one entry of
    # CLONE_GATED_SKIPS would still satisfy `seen == CLONE_GATED_SKIPS` if the
    # same typo were made twice. The total is an independent copy of the same
    # measurement.
    assert sum(seen.values()) == 22


def test_the_hook_cases_run_rather_than_skip():
    """The seven that did NOT have to be skipped.

    Blanket-skipping the overlay-guard file would have taken 15 tests out.
    MEASURED 2026-09-03: eight of them need the HELM-only installer, and the
    other seven only needed the hook FILE in place, which a copy achieves from
    either checkout. This asserts those seven are still running, because the
    cheap wrong answer is to gate the whole file.

    The split is 8/7, not the 7/8 this test first claimed:
    `test_the_installed_hook_is_byte_for_byte_the_tracked_body` belongs to the
    installer side. That identity therefore goes unchecked from a YARD, and is
    covered when the suite runs in HELM.
    """
    rel = "tests/test_a_data_overlay_guard_that_overwrote_what_was_there.py"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", rel, "-q", "--no-header",
         "--color=no", "-p", "no:cacheprovider", "-p", "no:xdist", "-rs"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=900)
    assert " failed" not in proc.stdout, proc.stdout[-3000:]
    if is_main_clone(ROOT):
        pytest.skip("in the main clone nothing here skips; the split is moot")
    skipped = sum(int(line.split("]")[0].split("[")[1])
                  for line in proc.stdout.splitlines()
                  if line.startswith("SKIPPED") and MAIN_CLONE_SKIP in line)
    assert skipped == CLONE_GATED_SKIPS[rel], (
        f"{skipped} skipped in that file, measured "
        f"{CLONE_GATED_SKIPS[rel]} on 2026-09-03; the seven hook cases "
        f"exercise the hook from either checkout and must still "
        f"run\n{proc.stdout[-2000:]}")


# ============================================================
# The fixtures answer, rather than assume
# ============================================================

def test_the_fixtures_ask_git_which_checkout_this_is():
    """The shared root, asserted where it lives.

    Each of the three fixtures must derive the answer rather than take
    `parents[1]` for the main clone, which is the defect all 97 shared.
    """
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    tree = ast.parse(conftest)
    names = {n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for fixture in ("helm_root", "armed_main_clone", "unguard_main_clone",
                    "main_clone_only"):
        assert fixture in names, f"{fixture} is gone from conftest"
    assert "main_clone_path" in conftest
    assert "is_main_clone" in conftest


def test_the_main_clone_fixture_produces_something_git_calls_a_main_clone(
        armed_main_clone):
    """Both directions in one place: the clone answers True, this checkout is
    whatever it is, and the two are not assumed to be the same."""
    assert is_main_clone(armed_main_clone) is True
    assert (armed_main_clone / ".git").is_dir()
    assert (armed_main_clone / "scripts" / "utils" / "clone_guard.py").is_file()


def test_the_main_clone_fixture_carries_the_uncommitted_working_tree(request):
    """The copy, proven by a file that exists ONLY in this working tree.

    Asserting that a TRACKED file is present proves nothing: `git clone` brings
    it either way. A mutation that removed the copy loop therefore survived,
    and a fixture whose clone holds HELM's committed code would quietly make
    every test using it measure the wrong tree -- the exact defect the copy
    exists to prevent.

    The marker is created BEFORE the fixture is requested, which is why this
    test asks for it by name rather than as a parameter.
    """
    marker = ROOT / ".armed-main-clone-copy-probe"
    marker.write_text("probe", encoding="utf-8")
    try:
        clone = request.getfixturevalue("armed_main_clone")
        copied = clone / marker.name
        assert copied.is_file(), (
            "the fixture did not carry this checkout's uncommitted files, so a "
            "test using it would run the committed code instead of the change "
            "under test")
        assert copied.read_text(encoding="utf-8") == "probe"
    finally:
        marker.unlink(missing_ok=True)


def test_the_unguard_fixture_refuses_a_module_it_would_not_patch(
        unguard_main_clone):
    """A fixture that silently patches nothing is how a neutralised guard
    becomes an unnoticed one."""
    class Bare:
        __name__ = "bare"

    with pytest.raises(AssertionError, match="require_main_clone"):
        unguard_main_clone(Bare())


def test_the_clone_gate_lets_a_main_clone_through(request, monkeypatch):
    """The half a YARD cannot observe by running.

    From a worktree `is_main_clone()` is False, so a gate that skipped
    UNCONDITIONALLY would behave identically to the real one and no run here
    could tell them apart -- a mutation making it unconditional survived for
    exactly that reason. Forcing the predicate makes the branch observable from
    either checkout, which is what stops this file from being a test that only
    works where it is not needed.
    """
    from scripts.utils import clone_guard
    monkeypatch.setattr(clone_guard, "is_main_clone", lambda *a, **k: True)
    # CAUGHT, not allowed to propagate. Letting the skip escape would mark THIS
    # test skipped, and a skipped test does not fail a run -- so a gate that
    # skipped unconditionally would have gone unnoticed. MEASURED 2026-09-03: a
    # mutation making it unconditional SURVIVED the first draft of this test for
    # exactly that reason. Turning the skip into a failure is the whole check.
    try:
        request.getfixturevalue("main_clone_only")
    except pytest.skip.Exception as exc:  # pragma: no cover - the defect path
        pytest.fail(f"the gate skipped inside a main clone: {exc}")


def test_the_clone_gate_stops_a_worktree(request, monkeypatch):
    """The other half, forced the same way rather than left to the ambient
    checkout, so both directions are asserted wherever this runs."""
    from scripts.utils import clone_guard
    monkeypatch.setattr(clone_guard, "is_main_clone", lambda *a, **k: False)
    with pytest.raises(pytest.skip.Exception) as raised:
        request.getfixturevalue("main_clone_only")
    assert MAIN_CLONE_SKIP in str(raised.value)
