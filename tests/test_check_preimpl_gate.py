"""Tests for scripts/check-preimpl-gate.py — the advisory /pre-impl gate check.

Covers the TEST-1..TEST-6 contract from the gate artifact
(plans/2026-06-28-pre-impl-implement-preimpl-soft-check.md):
happy-path FOUND, MISSING, SKIPPED, derive_slug parity with the trajectory
helper, exact-match (no substring), and the staleness note. The helper must
ALWAYS exit 0 — it is advisory and never blocks /implement.
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


gate = _load("check_preimpl_gate", "scripts/check-preimpl-gate.py")
traj = _load("implement_trajectory_log", "scripts/implement-trajectory-log.py")


# TEST-1 [happy-path]: matching artifact -> FOUND
def test_found(tmp_path):
    (tmp_path / "2026-06-28-pre-impl-foo.md").write_text("x")
    status, _ = gate.check_gate("plans/2026-06-28-foo.md", plans_dir=tmp_path,
                                today=date(2026, 6, 28))
    assert status == "FOUND"


# TEST-2 [failure-mode]: no artifact -> MISSING, never crashes
def test_missing(tmp_path):
    (tmp_path / "2026-06-28-pre-impl-foo.md").write_text("x")
    status, _ = gate.check_gate("plans/2026-06-28-bar.md", plans_dir=tmp_path,
                                today=date(2026, 6, 28))
    assert status == "MISSING"


# TEST-3 [edge-case]: no/undecodable plan path -> SKIPPED
def test_skipped_none(tmp_path):
    assert gate.check_gate(None, plans_dir=tmp_path)[0] == "SKIPPED"
    assert gate.check_gate("", plans_dir=tmp_path)[0] == "SKIPPED"


# TEST-4 [integration/parity]: derive_slug identical to the trajectory helper
def test_derive_slug_parity():
    for inp in ["2026-06-28-foo.md", "foo.md", "2026-06-28-a-b-c.md",
                "plans/2026-05-27-r12-trajectory-evaluation.md", "refactor-foo.md"]:
        assert gate.derive_slug(inp) == traj.derive_slug(inp), inp


# TEST-5 [exact-match]: no substring mis-match (foo must not match foobar)
def test_exact_match_no_substring(tmp_path):
    (tmp_path / "2026-06-28-pre-impl-foobar.md").write_text("x")
    status, _ = gate.check_gate("plans/2026-06-28-foo.md", plans_dir=tmp_path,
                                today=date(2026, 6, 28))
    assert status == "MISSING"


# TEST-6 [staleness]: old artifact still FOUND, with a stale note
def test_staleness(tmp_path):
    (tmp_path / "2026-06-01-pre-impl-foo.md").write_text("x")
    status, detail = gate.check_gate("plans/2026-06-28-foo.md", plans_dir=tmp_path,
                                     today=date(2026, 6, 28))
    assert status == "FOUND"
    assert "stale" in detail


# Advisory invariant: the CLI exits 0 even on MISSING
def test_cli_exit_zero_on_missing(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check-preimpl-gate.py"),
         "--plan", "plans/2026-06-28-bar.md", "--plans-dir", str(tmp_path), "--json"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert '"status": "MISSING"' in proc.stdout
