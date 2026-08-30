"""Two ways the fleet dashboard could look green while it was not.

Both found by the 2026-08-23 engine audit. Both are the same class: a failure
that produces a plausible number instead of a signal, on a screen whose only
job is to say which executive needs attention.

1. **A failed `git pull` was invisible.** `ensure_per_exec_repos` ran
   `run_cmd(["git", "pull"], check=False)` and never looked at the exit code.
   Auth expiry, a merge conflict, or an offline machine left a stale clone that
   the dashboard then read and presented as current. Only the CLONE branch
   warned. So the first time an executive's overlay was set up you were told it
   failed, and every time afterwards you were not.

2. **A commit dated in the future read as OK.** `delta` is
   `now - sync_time` and nothing floored it, so an executive machine whose clock
   ran ahead produced a negative delta: `-3600 sec ago` in the table, and
   `delta < OK_THRESHOLD` is trivially true for any negative number, so the row
   said OK. Clock skew is exactly when a fleet dashboard should not be trusted,
   and it was the one condition guaranteed to read as healthy.

The second fix deliberately does NOT add a fourth status. `print_dashboard`
sums `OK + STALE` into "Active executives" and prints a three-way summary, so a
new status would be counted in one place and silently dropped in two -- the same
defect in a new spot. STALE plus a `time_ago` that says what happened carries
the whole message with no vocabulary change.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _admin_health():
    path = ROOT / "scripts" / "admin-health.py"
    spec = importlib.util.spec_from_file_location("admin_health_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


AH = _admin_health()


def _iso(offset: timedelta) -> str:
    return (datetime.now(timezone.utc) + offset).isoformat()


# --- 1. a failed pull must be visible ----------------------------------------

def test_a_failed_pull_warns(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(AH, "get_all_active_exec_slugs", lambda: ["someone"])
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(AH, "get_per_exec_repo_path", lambda _slug: repo)

    def failing(cmd, cwd=None, check=True):
        return subprocess.CompletedProcess(cmd, 1, "", "fatal: could not read Username")

    monkeypatch.setattr(AH, "run_cmd", failing)
    pairs = AH.ensure_per_exec_repos()
    # ONE readouterr(). The call DRAINS the buffer, so the old
    # `capsys.readouterr().out + capsys.readouterr().err` always concatenated
    # stdout with an empty string: the line was written to accept the warning on
    # either stream and only ever searched stdout. A warning printed to stderr -
    # the natural stream for one, and where git's own "fatal: could not read
    # Username" goes - failed this test against a correct implementation, and
    # the obvious repair is to move the warning to the wrong stream.
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert pairs == [("someone", repo)], "the exec was dropped instead of flagged"
    assert "someone" in out and "pull" in out.lower(), (
        f"a failed pull produced no warning; the dashboard would read a stale "
        f"clone as current. Output was {out!r}"
    )


def test_a_successful_pull_is_quiet(monkeypatch, tmp_path, capsys):
    """A warning on every run is a warning nobody reads."""
    monkeypatch.setattr(AH, "get_all_active_exec_slugs", lambda: ["someone"])
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(AH, "get_per_exec_repo_path", lambda _slug: repo)
    monkeypatch.setattr(AH, "run_cmd",
                        lambda cmd, cwd=None, check=True:
                        subprocess.CompletedProcess(cmd, 0, "Already up to date.", ""))
    AH.ensure_per_exec_repos()
    assert capsys.readouterr().out.strip() == ""


def test_a_pull_that_cannot_start_is_also_visible(monkeypatch, tmp_path, capsys):
    """`git` missing from PATH raises rather than returning a code."""
    monkeypatch.setattr(AH, "get_all_active_exec_slugs", lambda: ["someone"])
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(AH, "get_per_exec_repo_path", lambda _slug: repo)

    def boom(cmd, cwd=None, check=True):
        raise FileNotFoundError("git")

    monkeypatch.setattr(AH, "run_cmd", boom)
    pairs = AH.ensure_per_exec_repos()
    out = capsys.readouterr().out
    assert pairs == [("someone", repo)]
    assert "someone" in out


# --- 2. a future timestamp must not read as fresh ----------------------------

def test_a_commit_an_hour_in_the_future_is_not_ok():
    status, _, time_ago = AH.calculate_status({"last_commit": _iso(timedelta(hours=1))})
    assert status != "OK", (
        "a commit dated in the future reported OK. Any negative delta clears "
        "every threshold, so the one state that means 'do not trust this clock' "
        "was the state guaranteed to look healthy."
    )
    assert "sec ago" not in time_ago and "-" not in time_ago, (
        f"the table still prints a negative age: {time_ago!r}"
    )


def test_the_future_row_says_what_happened():
    _, _, time_ago = AH.calculate_status({"last_commit": _iso(timedelta(days=3))})
    assert "ahead" in time_ago.lower(), time_ago


def test_a_few_seconds_of_jitter_is_not_treated_as_skew():
    """NTP-normal drift must not turn every healthy row yellow."""
    status, _, _ = AH.calculate_status({"last_commit": _iso(timedelta(seconds=2))})
    assert status == "OK"


def test_the_ordinary_bands_are_unchanged():
    assert AH.calculate_status({"last_commit": _iso(-timedelta(hours=2))})[0] == "OK"
    assert AH.calculate_status({"last_commit": _iso(-timedelta(days=10))})[0] == "STALE"
    assert AH.calculate_status({"last_commit": _iso(-timedelta(days=40))})[0] == "DEAD"
    assert AH.calculate_status({"last_commit": None})[0] == "DEAD"
    assert AH.calculate_status({"last_commit": "not a date"})[0] == "DEAD"


def test_the_status_vocabulary_did_not_grow():
    """`print_dashboard` sums OK+STALE for 'Active executives' and prints a
    three-way summary. A fourth status would be counted once and dropped twice."""
    seen = set()
    for offset in (timedelta(hours=1), -timedelta(hours=2), -timedelta(days=10),
                   -timedelta(days=40)):
        seen.add(AH.calculate_status({"last_commit": _iso(offset)})[0])
    assert seen <= {"OK", "STALE", "DEAD"}, seen
