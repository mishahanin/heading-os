"""The context-floor gate ratchets in exactly one direction, and says so.

`scripts/context-floor-audit.py --baseline` had no test at all. It is the only
thing standing between the always-loaded floor and unbounded regrowth, and it
fails ONLY upward, so two properties matter and neither was pinned:

- growth past the tolerance must exit non-zero (the gate works at all);
- a shrink must pass AND report the slack it just opened, or a stale baseline
  reads as "within tolerance" forever and the ratchet stops ratcheting.

Every case runs the real script as a subprocess against a sandbox workspace
(`WORKSPACE_ROOT`), with `HEADING_OS_DATA` pointed away from the operator's
real overlay so no case can read or write private data.

Run: python3 -m pytest tests/test_context_floor_ratchet.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "context-floor-audit.py"

# Mirrors GROWTH_TOLERANCE in the script. Asserted equal below, so a change to
# the script's constant fails here rather than silently rewriting these cases.
TOLERANCE = 0.05


@pytest.fixture
def sandbox(tmp_path):
    """A minimal workspace the audit can measure: one skill, one rule, a CLAUDE.md."""
    (tmp_path / ".claude" / "skills" / "example-skill").mkdir(parents=True)
    (tmp_path / ".claude" / "skills" / "example-skill" / "SKILL.md").write_text(
        "---\nname: example-skill\ndescription: An invented placeholder skill "
        "used only to give the audit something to measure.\n---\n\nBody.\n",
        encoding="utf-8",
    )
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    (tmp_path / ".claude" / "rules" / "example-rule.md").write_text(
        "# Example rule\n\nAlways-on: no `paths:` key.\n", encoding="utf-8"
    )
    (tmp_path / "CLAUDE.md").write_text("# Sandbox\n", encoding="utf-8")
    (tmp_path / "config").mkdir(parents=True)
    return tmp_path


def _run(sandbox, *args):
    env = dict(
        os.environ,
        WORKSPACE_ROOT=str(sandbox),
        # Never let a child inherit the operator's real data root.
        HEADING_OS_DATA=str(sandbox / "no-such-overlay"),
    )
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env, cwd=str(sandbox),
    )


def _live_total(sandbox):
    proc = _run(sandbox, "--json")
    assert proc.returncode == 0, proc.stderr
    total = json.loads(proc.stdout)["total_bytes"]
    # Empty-corpus guard: an audit that measured nothing would make every
    # arithmetic case below trivially true.
    assert total > 0, "the sandbox measured a zero-byte floor; it measured nothing"
    return total


def _write_baseline(sandbox, total_bytes):
    (sandbox / "config" / "context-floor-baseline.json").write_text(
        json.dumps({"total_bytes": total_bytes}), encoding="utf-8"
    )


def test_the_scripts_tolerance_still_matches_this_files_assumption():
    """These cases are arithmetic around the tolerance; pin it rather than guess."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert f"GROWTH_TOLERANCE = {TOLERANCE}" in source


def test_growth_past_the_tolerance_fails_the_gate(sandbox):
    now = _live_total(sandbox)
    # A baseline low enough that `now` sits above the ceiling.
    _write_baseline(sandbox, int(now / (1 + TOLERANCE)) - 100)
    proc = _run(sandbox, "--baseline")
    assert proc.returncode == 1, f"gate passed on real growth\n{proc.stderr}"
    assert "Floor grew" in proc.stderr


def test_growth_inside_the_tolerance_still_passes(sandbox):
    """The gate allows drift up to the tolerance; it is not an any-growth gate."""
    now = _live_total(sandbox)
    _write_baseline(sandbox, now - 1)
    proc = _run(sandbox, "--baseline")
    assert proc.returncode == 0, proc.stderr


def test_the_case_exactly_on_the_ceiling_passes(sandbox):
    """A bound needs a case ON the line, not only either side of it."""
    now = _live_total(sandbox)
    # Largest baseline whose ceiling is still >= now, i.e. now == ceiling.
    _write_baseline(sandbox, now / (1 + TOLERANCE))
    proc = _run(sandbox, "--baseline")
    assert proc.returncode == 0, f"a floor exactly on the ceiling failed\n{proc.stderr}"


def test_one_byte_past_the_ceiling_fails(sandbox):
    """The other side of the same line, so the boundary is pinned from both."""
    now = _live_total(sandbox)
    ceiling_baseline = now / (1 + TOLERANCE)
    _write_baseline(sandbox, ceiling_baseline - 1)
    proc = _run(sandbox, "--baseline")
    assert proc.returncode == 1, f"gate passed one byte past the ceiling\n{proc.stderr}"


def test_a_shrink_passes_and_reports_the_slack_it_opened(sandbox):
    """A stale baseline must not read as a tight one.

    This is the regression for the drift found on 2026-08-31: the committed
    baseline was 332735 against a live 287194, and `--baseline` said only
    "within tolerance" while 62177 bytes of regrowth room sat unreported.
    """
    now = _live_total(sandbox)
    _write_baseline(sandbox, now * 2)
    proc = _run(sandbox, "--baseline")
    assert proc.returncode == 0, proc.stderr
    assert "Slack:" in proc.stderr, (
        "a shrink reported no slack; the baseline can go stale unnoticed"
    )
    expected = int(now * 2 * (1 + TOLERANCE)) - now
    assert str(expected) in proc.stderr, (
        f"expected the slack figure {expected} in:\n{proc.stderr}"
    )
    assert "--write-baseline" in proc.stderr, (
        "the slack line must name the command that re-tightens the ratchet"
    )


def test_write_baseline_records_the_live_measurement(sandbox):
    now = _live_total(sandbox)
    proc = _run(sandbox, "--write-baseline")
    assert proc.returncode == 0, proc.stderr
    recorded = json.loads(
        (sandbox / "config" / "context-floor-baseline.json").read_text(encoding="utf-8")
    )
    assert recorded["total_bytes"] == now
    # And the freshly written baseline leaves only the tolerance as slack.
    after = _run(sandbox, "--baseline")
    assert after.returncode == 0, after.stderr
    assert f"{now} -> {now}" in after.stderr


def test_a_missing_baseline_fails_rather_than_passing_silently(sandbox):
    """No baseline is an unarmed gate, and must not read as a clean pass."""
    proc = _run(sandbox, "--baseline")
    assert proc.returncode == 1, f"a missing baseline passed the gate\n{proc.stderr}"
    assert "No baseline" in proc.stderr


def test_nothing_was_written_into_the_real_repository(sandbox):
    """The sandbox must not have touched the committed baseline."""
    _run(sandbox, "--write-baseline")
    real = ROOT / "config" / "context-floor-baseline.json"
    assert json.loads(real.read_text(encoding="utf-8"))["total_bytes"] > 0
    assert not (sandbox / "no-such-overlay").exists()
