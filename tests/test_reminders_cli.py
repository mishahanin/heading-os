import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(args, env_home):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "reminders.py"), *args],
        cwd=str(ROOT), capture_output=True, text=True,
        env={**__import__("os").environ, "HEADING_OS_DATA": str(env_home)},
    )


def test_add_then_list(tmp_path):
    (tmp_path / "outputs" / "operations" / "reminders").mkdir(parents=True)
    r = _run(["add", "--once", "2026-07-26", "--message", "Prep Aspire"], tmp_path)
    assert r.returncode == 0, r.stderr
    out = _run(["list"], tmp_path)
    assert "Prep Aspire" in out.stdout


def test_unknown_recurring_rule(tmp_path):
    (tmp_path / "outputs" / "operations" / "reminders").mkdir(parents=True)
    r = _run(["add", "--recurring", "unknown-rule", "--message", "Test"], tmp_path)
    assert r.returncode == 2, f"Expected exit 2, got {r.returncode}"
    assert "unknown recurrence rule" in r.stderr


def test_known_recurring_rule(tmp_path):
    (tmp_path / "outputs" / "operations" / "reminders").mkdir(parents=True)
    r = _run(["add", "--recurring", "first-friday-minus-1", "--message", "Monthly check"], tmp_path)
    assert r.returncode == 0, r.stderr
    out = _run(["list"], tmp_path)
    assert "Monthly check" in out.stdout


def test_rm_removes_reminder(tmp_path):
    (tmp_path / "outputs" / "operations" / "reminders").mkdir(parents=True)
    # Add a reminder
    r = _run(["add", "--once", "2026-07-26", "--message", "Test reminder"], tmp_path)
    assert r.returncode == 0
    # Get the list as JSON to capture the ID
    out = _run(["list", "--json"], tmp_path)
    assert out.returncode == 0
    records = json.loads(out.stdout)
    assert len(records) == 1
    rid = records[0]["id"]
    # Remove it
    r = _run(["rm", rid], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "removed" in r.stdout
    # Verify it's gone
    out = _run(["list"], tmp_path)
    assert "Test reminder" not in out.stdout


def test_rm_nonexistent_id(tmp_path):
    (tmp_path / "outputs" / "operations" / "reminders").mkdir(parents=True)
    r = _run(["rm", "nonexistent"], tmp_path)
    assert r.returncode == 1
    assert "not found" in r.stdout


def test_done_removes_once_reminder(tmp_path):
    (tmp_path / "outputs" / "operations" / "reminders").mkdir(parents=True)
    # Add a reminder
    r = _run(["add", "--once", "2026-07-26", "--message", "Test done"], tmp_path)
    assert r.returncode == 0
    # Get the list as JSON to capture the ID
    out = _run(["list", "--json"], tmp_path)
    records = json.loads(out.stdout)
    rid = records[0]["id"]
    # Use 'done' to remove it
    r = _run(["done", rid], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "removed" in r.stdout
    # Verify it's gone
    out = _run(["list"], tmp_path)
    assert "Test done" not in out.stdout


def test_done_spares_recurring_reminder(tmp_path):
    (tmp_path / "outputs" / "operations" / "reminders").mkdir(parents=True)
    r = _run(["add", "--recurring", "first-friday-minus-1", "--message", "Monthly AMA"], tmp_path)
    assert r.returncode == 0
    out = _run(["list", "--json"], tmp_path)
    records = json.loads(out.stdout)
    rid = records[0]["id"]
    r = _run(["done", rid], tmp_path)
    assert r.returncode == 2, f"Expected exit 2, got {r.returncode}"
    assert "rm" in r.stderr and rid in r.stderr
    # Still present -- 'done' must not permanently delete a recurring reminder.
    out = _run(["list"], tmp_path)
    assert "Monthly AMA" in out.stdout


def test_invalid_once_date_exits_2(tmp_path):
    (tmp_path / "outputs" / "operations" / "reminders").mkdir(parents=True)
    r = _run(["add", "--once", "not-a-date", "--message", "Bad date"], tmp_path)
    assert r.returncode == 2, f"Expected exit 2, got {r.returncode}"
    assert "invalid date" in r.stderr
    # Verify nothing was written
    out = _run(["list"], tmp_path)
    assert "Bad date" not in out.stdout
