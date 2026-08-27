"""Tests for scripts/check-contract-gate.py — the advisory contract-gate check.

Covers the original TEST-1..TEST-6 contract: happy-path FOUND, MISSING,
SKIPPED, derive_slug parity with the trajectory helper, exact-match (no
substring), and the staleness note. The helper must ALWAYS exit 0 — it is
advisory and never blocks /implement.

Re-pointed 2026-08-07 from `plans/<date>-pre-impl-<slug>.md` to
`tests/contract/<date>-<slug>/`. Nothing could write the old filename after
`/canopus plan` was retired, so every run took the MISSING branch and warned
about an artifact that could not exist. The properties are unchanged; only the
artifact they are asserted over moved, which is why every test below is the same
test with a directory where a file used to be.
"""
import importlib.util
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(module_name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(module_name, str(ROOT / rel_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load("check_contract_gate", "scripts/check-contract-gate.py")
traj = _load("implement_trajectory_log", "scripts/implement-trajectory-log.py")


# TEST-1 [happy-path]: matching artifact -> FOUND
def test_found(tmp_path):
    (tmp_path / "2026-06-28-foo").mkdir()
    status, _ = gate.check_gate("plans/2026-06-28-foo.md", contract_dir=tmp_path,
                                today=date(2026, 6, 28))
    assert status == "FOUND"


# TEST-2 [failure-mode]: no artifact -> MISSING, never crashes
def test_missing(tmp_path):
    (tmp_path / "2026-06-28-foo").mkdir()
    status, _ = gate.check_gate("plans/2026-06-28-bar.md", contract_dir=tmp_path,
                                today=date(2026, 6, 28))
    assert status == "MISSING"


# TEST-3 [edge-case]: no/undecodable plan path -> SKIPPED
def test_skipped_none(tmp_path):
    assert gate.check_gate(None, contract_dir=tmp_path)[0] == "SKIPPED"
    assert gate.check_gate("", contract_dir=tmp_path)[0] == "SKIPPED"


# TEST-4 [integration/parity]: derive_slug identical to the trajectory helper
def test_derive_slug_parity():
    for inp in ["2026-06-28-foo.md", "foo.md", "2026-06-28-a-b-c.md",
                "plans/2026-05-27-r12-trajectory-evaluation.md", "refactor-foo.md"]:
        assert gate.derive_slug(inp) == traj.derive_slug(inp), inp


# TEST-5 [exact-match]: no substring mis-match (foo must not match foobar)
def test_exact_match_no_substring(tmp_path):
    (tmp_path / "2026-06-28-foobar").mkdir()
    status, _ = gate.check_gate("plans/2026-06-28-foo.md", contract_dir=tmp_path,
                                today=date(2026, 6, 28))
    assert status == "MISSING"


# TEST-6 [staleness]: old artifact still FOUND, with a stale note
def test_staleness(tmp_path):
    (tmp_path / "2026-06-01-foo").mkdir()
    status, detail = gate.check_gate("plans/2026-06-28-foo.md", contract_dir=tmp_path,
                                     today=date(2026, 6, 28))
    assert status == "FOUND"
    assert "stale" in detail


# TEST-6b [newest wins]: the comparator, with something to compare
def test_the_newest_contract_wins_when_a_slug_has_two(tmp_path):
    """`_dir_slug` strips the date prefix, so two directories can legitimately
    share one slug after a plan is re-contracted. That is the ONLY reason the
    `max(...)` at check-contract-gate.py exists, and every test that called
    `check_gate` created at most one directory per slug - so `matches` never had
    length above 1 and the comparator was never consulted.

    Replacing `max(...)` with `min(...)` or with `matches[0]` was green. The
    advisory would then name the superseded contract and tell the operator that
    a freshly-contracted plan is stale.
    """
    (tmp_path / "2026-06-01-foo").mkdir()
    (tmp_path / "2026-08-20-foo").mkdir()
    status, detail = gate.check_gate("plans/2026-08-20-foo.md", contract_dir=tmp_path,
                                     today=date(2026, 8, 25))
    assert status == "FOUND"
    assert detail.startswith("2026-08-20-foo"), detail
    assert "stale" not in detail, (
        f"the fresh contract was reported stale, so the gate is reading the "
        f"superseded one: {detail}"
    )


def test_the_newest_wins_whatever_order_the_directories_are_created_in(tmp_path):
    """`iterdir()` order is filesystem-dependent, so creating the newest last
    could be what makes the test above pass. Create them the other way round."""
    (tmp_path / "2026-08-20-foo").mkdir()
    (tmp_path / "2026-06-01-foo").mkdir()
    (tmp_path / "2026-07-04-foo").mkdir()
    status, detail = gate.check_gate("plans/2026-08-20-foo.md", contract_dir=tmp_path,
                                     today=date(2026, 8, 25))
    assert status == "FOUND"
    assert detail.startswith("2026-08-20-foo"), detail


def test_a_directory_with_an_unparsable_date_never_outranks_a_real_one(tmp_path):
    """The comparator's first key, isolated from its second.

    The two tests above do NOT pin `_artifact_date`: directory names start with
    an ISO date, and ISO dates sort alphabetically in chronological order, so
    `max(matches, key=lambda p: p.name)` returns the right answer by accident.
    Mutation-confirmed 2026-08-27: dropping the date key survived both.

    `_dir_slug` accepts any digits-and-dashes prefix, but `_artifact_date` calls
    `date.fromisoformat` and returns None on a bad one. So a typo'd month makes
    a candidate that sorts LAST by name and FIRST by date - the one case where
    the two keys disagree, and a realistic one: 2026-13-45 is a fat-fingered
    2026-03-04.
    """
    (tmp_path / "2026-08-20-foo").mkdir()
    (tmp_path / "2026-13-45-foo").mkdir()   # unparsable: month 13, day 45
    assert gate._dir_slug("2026-13-45-foo") == "foo", "the bad name is not a candidate"
    assert gate._artifact_date(tmp_path / "2026-13-45-foo") is None
    assert max("2026-08-20-foo", "2026-13-45-foo") == "2026-13-45-foo", (
        "the fixture no longer distinguishes name order from date order"
    )

    status, detail = gate.check_gate("plans/2026-08-20-foo.md", contract_dir=tmp_path,
                                     today=date(2026, 8, 25))
    assert status == "FOUND"
    assert detail.startswith("2026-08-20-foo"), (
        f"a directory whose date does not parse was ranked above one whose date "
        f"does, so the gate is sorting by name: {detail}"
    )


# Advisory invariant: the CLI exits 0 even on MISSING
def test_cli_exit_zero_on_missing(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check-contract-gate.py"),
         "--plan", "plans/2026-06-28-bar.md", "--contract-dir", str(tmp_path),
         "--json"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert '"status": "MISSING"' in proc.stdout


# Re-point regression: a FILE named like a contract is not a contract.
def test_a_file_is_not_a_contract_directory(tmp_path):
    """The glob matches on name, so the is_dir() filter is what carries this.

    Without it the old `<date>-pre-impl-<slug>.md` artifacts still sitting in a
    tree would keep answering FOUND after the re-point, and the check would go on
    reporting on the thing it was moved off.
    """
    (tmp_path / "2026-06-28-foo").write_text("not a directory", encoding="utf-8")
    status, _ = gate.check_gate("plans/2026-06-28-foo.md", contract_dir=tmp_path,
                                today=date(2026, 6, 28))
    assert status == "MISSING"
