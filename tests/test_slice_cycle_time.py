"""Every cut decision weighs a mechanism's catch rate against its cost, and the
cost side was never measured.

The design counts files, lines and ledger events, and carries not one number
about how long a slice takes or how much of that is friction. The denial counter
gives the catch side; without this the arithmetic still has an unknown column.
See `docs/superpowers/specs/2026-08-01-canopus-v2-design.md` §6 A9.

What it measures, and the limit stated rather than implied: the earliest
machine-recorded moment in a slice is the approval, so this reports
approve-to-release. The thinking before the approval is real work and is not in
here.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CLI = _ROOT / "scripts" / "slice-cycle-time.py"


def _run(args, root: Path):
    env = dict(os.environ)
    env["WORKSPACE_ROOT"] = str(root)
    return subprocess.run([sys.executable, str(_CLI), *args], capture_output=True,
                          text=True, cwd=str(_ROOT), env=env, timeout=120)


@pytest.fixture
def ledger_root(tmp_path):
    """A workspace root carrying a hand-built canopus history."""
    root = tmp_path / "ws"
    (root / ".claude").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("probe", encoding="utf-8")
    canopus = root / ".canopus"
    canopus.mkdir()
    rows = [
        # A clean slice: approve, freeze, ship. Two hours.
        {"event": "approve", "label": "clean", "ts": "2026-07-01T10:00:00+00:00", "kind": "", "reason": ""},
        {"event": "freeze", "label": "clean", "ts": "2026-07-01T10:05:00+00:00", "kind": "", "reason": ""},
        {"event": "release", "label": "clean", "ts": "2026-07-01T12:00:00+00:00", "kind": "ship", "reason": ""},
        # A rough slice: a window, a re-approval, a failed verify. Six hours.
        {"event": "approve", "label": "rough", "ts": "2026-07-02T09:00:00+00:00", "kind": "", "reason": ""},
        {"event": "freeze", "label": "rough", "ts": "2026-07-02T09:10:00+00:00", "kind": "", "reason": ""},
        {"event": "verify_fail", "label": "rough", "ts": "2026-07-02T10:00:00+00:00", "kind": "", "reason": "moved"},
        {"event": "release", "label": "rough", "ts": "2026-07-02T11:00:00+00:00", "kind": "window", "reason": "enforcer"},
        {"event": "anchor_replaced", "label": "rough", "ts": "2026-07-02T11:30:00+00:00", "kind": "", "reason": "retake"},
        {"event": "approve", "label": "rough", "ts": "2026-07-02T11:31:00+00:00", "kind": "", "reason": "retake"},
        {"event": "freeze", "label": "rough", "ts": "2026-07-02T11:35:00+00:00", "kind": "", "reason": "retake"},
        {"event": "release", "label": "rough", "ts": "2026-07-02T15:00:00+00:00", "kind": "ship", "reason": ""},
        # Still open: approved and frozen, never shipped.
        {"event": "approve", "label": "open", "ts": "2026-07-03T09:00:00+00:00", "kind": "", "reason": ""},
        {"event": "freeze", "label": "open", "ts": "2026-07-03T09:02:00+00:00", "kind": "", "reason": ""},
    ]
    (canopus / "history.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return root


def test_a_shipped_slice_reports_approve_to_release(ledger_root):
    proc = _run(["--json"], ledger_root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    slices = {s["label"]: s for s in json.loads(proc.stdout)["slices"]}
    assert slices["clean"]["hours"] == pytest.approx(2.0, abs=0.01)


def test_the_start_is_the_first_approval_not_the_last(ledger_root):
    """A retake must not shorten the slice it made longer."""
    proc = _run(["--json"], ledger_root)
    slices = {s["label"]: s for s in json.loads(proc.stdout)["slices"]}
    assert slices["rough"]["hours"] == pytest.approx(6.0, abs=0.01)


def test_friction_is_counted_per_slice(ledger_root):
    proc = _run(["--json"], ledger_root)
    slices = {s["label"]: s for s in json.loads(proc.stdout)["slices"]}
    assert slices["rough"]["windows"] == 1
    assert slices["rough"]["reapprovals"] == 1
    assert slices["rough"]["verify_failures"] == 1
    assert slices["clean"]["windows"] == 0
    assert slices["clean"]["reapprovals"] == 0


def test_an_unshipped_slice_is_reported_as_open_not_as_zero(ledger_root):
    """Counting an open slice as duration zero would flatter every average."""
    proc = _run(["--json"], ledger_root)
    slices = {s["label"]: s for s in json.loads(proc.stdout)["slices"]}
    assert slices["open"]["shipped"] is False
    assert slices["open"]["hours"] is None


def test_the_summary_excludes_open_slices_from_the_median(ledger_root):
    proc = _run(["--json"], ledger_root)
    payload = json.loads(proc.stdout)
    assert payload["summary"]["shipped_count"] == 2
    assert payload["summary"]["median_hours"] == pytest.approx(4.0, abs=0.01)


def test_the_human_readable_form_names_the_limit(ledger_root):
    """The earliest recorded moment is the approval, so the thinking before it
    is not in this number. A metric whose blind spot is unstated gets quoted as
    though it had none."""
    proc = _run([], ledger_root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "approve" in proc.stdout.lower()
    assert "clean" in proc.stdout and "rough" in proc.stdout


def test_a_missing_ledger_exits_zero_and_says_so(tmp_path):
    root = tmp_path / "bare"
    (root / ".claude").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("x", encoding="utf-8")
    proc = _run([], root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "no" in proc.stdout.lower()


def test_a_corrupt_line_does_not_lose_the_rest_of_the_ledger(ledger_root):
    path = ledger_root / ".canopus" / "history.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    proc = _run(["--json"], ledger_root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(json.loads(proc.stdout)["slices"]) == 3
