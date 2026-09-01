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


# --- 1b. a byte git wrote must not defeat the warning ------------------------

# A command that writes an undecodable byte on stderr and exits nonzero, which
# is what `git pull` does whenever the message it is quoting - a branch, a path,
# a remote's own output - is not valid UTF-8 in this process's encoding.
_BAD_BYTE_CMD = [
    sys.executable, "-c",
    "import sys; sys.stderr.buffer.write(b'fatal: could not read \\xff\\xfe\\n');"
    " sys.exit(1)",
]


def test_run_cmd_survives_git_writing_a_byte_that_is_not_utf8():
    """`subprocess.run(text=True)` with no `errors=` raises `UnicodeDecodeError`.

    That is a `ValueError`, so it is caught by none of
    `CalledProcessError` / `FileNotFoundError` / `OSError` - the exact tuple
    `ensure_per_exec_repos` wraps this call in - and by none of
    `read_last_commit`'s `(OSError, FileNotFoundError)` either. MEASURED
    2026-09-01 before the fix: one exec whose pull emitted a single 0xff byte
    took down the WHOLE dashboard with a raw traceback, and the warning three
    lines below the call, which exists so a failed pull is never silent, never
    ran. A byte defeated the control built for exactly this failure.

    `errors="replace"` is the fix rather than a wider `except`, because the
    stderr TEXT is what the warning quotes: degrading the message keeps the
    operator's diagnostic, where swallowing the exception would print
    "no output".
    """
    result = AH.run_cmd(_BAD_BYTE_CMD, check=False)
    assert result.returncode == 1
    assert "could not read" in result.stderr, result.stderr


def test_read_last_commit_configures_its_decode_the_same_way(monkeypatch, tmp_path):
    """The third site, asked through the kwargs it actually passes.

    `read_last_commit` builds its own `subprocess.run` call rather than going
    through `run_cmd`, so fixing one says nothing about the other. Rather than
    assert the source text, this captures the call's real keyword arguments and
    REPLAYS them against a command known to emit an undecodable byte: if the
    decode configuration it ships is unsafe, the replay raises exactly as
    production would.
    """
    captured: dict = {}
    real_run = subprocess.run

    def _capture(cmd, **kw):
        captured.update(kw)
        return subprocess.CompletedProcess(cmd, 0, "2026-08-20T09:00:00+00:00", "")

    monkeypatch.setattr(AH.subprocess, "run", _capture)
    AH.read_last_commit(tmp_path)
    assert captured, "read_last_commit no longer calls subprocess.run"

    kw = dict(captured)
    kw.pop("check", None)
    replayed = real_run(_BAD_BYTE_CMD, check=False, **kw)
    assert "could not read" in (replayed.stderr or ""), (
        "read_last_commit decodes git's output strictly; an undecodable byte "
        "raises UnicodeDecodeError past its (OSError, FileNotFoundError) "
        "handler and the documented 'returns None' fallback never runs"
    )


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
