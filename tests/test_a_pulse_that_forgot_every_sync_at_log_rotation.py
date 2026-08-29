"""Shard 15-p1: `scripts/sync-exchange-pulse.py`, two findings, both measured.

THE LIVENESS SIGNAL RESET EVERY TIME THE LOG ROTATED.

The daemon logs through `RotatingFileHandler(maxBytes=1_000_000,
backupCount=3)`. A rotation moves all history into `daemon.log.1`-`.3` and
starts a fresh, empty `daemon.log`. `_last_job_ok` read only the active file, so
the moment after a rotation it found nothing. Measured 2026-08-29 with a
seven-minute-old `job-ok sync-exchange` line sitting in `daemon.log.1` and an
empty `daemon.log`: the function returned None, and the pulse printed

    Sync-Exchange: daemon up pid=N, no sync logged yet

for a daemon that had synced seven minutes earlier. The sync interval is two
hours, so that sentence stood for up to two hours on every rotation.

The second finding is a comment that told the next reader to break the code. The
line reads the parsed stamp back as naive, one line below the `.replace(tzinfo=
...)` that makes it aware. Acting on it, by dropping the `.replace`, gives
`TypeError: can't subtract offset-naive and offset-aware datetimes`, verified on
the same date. The arithmetic was right; the sentence describing it was not, and
a comment that survives review by being obviously true is the one that gets
believed.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PULSE_SRC = ROOT / "scripts" / "sync-exchange-pulse.py"


def _load():
    spec = importlib.util.spec_from_file_location("pulse_shard15", str(PULSE_SRC))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pulse_shard15"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pulse():
    return _load()


@pytest.fixture
def logs(pulse, tmp_path, monkeypatch):
    """Point the module at a tmp log set and hand back a writer."""
    monkeypatch.setattr(pulse, "LOG_FILE", tmp_path / "daemon.log")

    def write(suffix, *ages):
        """Write one job-ok line per age (a timedelta) into daemon.log<suffix>."""
        name = "daemon.log" + suffix
        lines = []
        for age in ages:
            when = datetime.now().astimezone() - age
            lines.append(when.strftime("%Y-%m-%d %H:%M:%S") + ",000 "
                         "INFO [0d6113bbcc6b] job-ok sync-exchange (exit=0)")
        (tmp_path / name).write_text("\n".join(lines) + ("\n" if lines else ""),
                                     encoding="utf-8")
        return tmp_path / name

    return write


# ============================================================
# 1. The rotation that erased the daemon's history
# ============================================================

def test_a_job_in_the_rotated_backup_is_still_found(pulse, logs):
    """The finding. Immediately after a rotation this returned None."""
    logs(".1", timedelta(minutes=7))
    logs("", *[])                       # freshly rotated: present and empty
    assert pulse._last_job_ok() == "7m ago"


def test_a_rotation_does_not_report_the_daemon_as_never_having_synced(pulse, logs):
    """Stated as the operator read it on the pulse line."""
    logs(".1", timedelta(minutes=7))
    logs("")
    assert pulse._last_job_ok() is not None


def test_the_active_file_wins_over_a_backup(pulse, logs):
    """A backup can only hold lines older than the file that displaced it."""
    logs(".1", timedelta(hours=4))
    logs("", timedelta(minutes=3))
    assert pulse._last_job_ok() == "3m ago"


def test_the_newest_backup_wins_over_an_older_one(pulse, logs):
    logs(".2", timedelta(days=2))
    logs(".1", timedelta(minutes=9))
    logs("")
    assert pulse._last_job_ok() == "9m ago"


def test_the_third_backup_is_read_too(pulse, logs):
    """backupCount=3, so `.3` exists and holding it back would be arbitrary."""
    logs(".3", timedelta(minutes=41))
    logs("")
    assert pulse._last_job_ok() == "41m ago"


def test_a_fourth_backup_is_not_invented(pulse, logs):
    """`.4` is never written by backupCount=3; reading it would be a guess."""
    logs(".4", timedelta(minutes=5))
    logs("")
    assert pulse._last_job_ok() is None


def test_the_last_line_of_a_file_wins_within_that_file(pulse, logs):
    logs("", timedelta(hours=6), timedelta(minutes=2))
    assert pulse._last_job_ok() == "2m ago"


def test_a_torn_tail_line_does_not_erase_the_match_above_it(pulse, tmp_path,
                                                            monkeypatch):
    """A rotating log tears at its tail, which is where the newest line is.

    The regex accepts `2026-02-30`; `strptime` does not. Skipping that line is
    what keeps the real match from turning into "no sync logged yet", which is
    the answer this whole function was fixed to stop giving.
    """
    monkeypatch.setattr(pulse, "LOG_FILE", tmp_path / "daemon.log")
    when = datetime.now().astimezone() - timedelta(minutes=6)
    (tmp_path / "daemon.log").write_text(
        when.strftime("%Y-%m-%d %H:%M:%S") + ",000 INFO job-ok sync-exchange\n"
        "2026-02-30 25:61:00,000 INFO job-ok sync-exchange\n",
        encoding="utf-8")
    assert pulse._last_job_ok() == "6m ago"


def test_no_log_at_all_is_still_no_answer(pulse, tmp_path, monkeypatch):
    monkeypatch.setattr(pulse, "LOG_FILE", tmp_path / "absent.log")
    assert pulse._last_job_ok() is None


def test_a_log_with_no_job_line_is_still_no_answer(pulse, tmp_path, monkeypatch):
    log = tmp_path / "daemon.log"
    log.write_text("2026-08-29 10:00:00,000 INFO [abc] daemon booted\n",
                   encoding="utf-8")
    monkeypatch.setattr(pulse, "LOG_FILE", log)
    assert pulse._last_job_ok() is None


def test_an_unreadable_backup_does_not_hide_a_readable_one(pulse, tmp_path,
                                                           monkeypatch):
    """A directory where a rotated file should be: skip it, keep looking."""
    monkeypatch.setattr(pulse, "LOG_FILE", tmp_path / "daemon.log")
    (tmp_path / "daemon.log").write_text("", encoding="utf-8")
    (tmp_path / "daemon.log.1").mkdir()
    when = datetime.now().astimezone() - timedelta(minutes=11)
    (tmp_path / "daemon.log.2").write_text(
        when.strftime("%Y-%m-%d %H:%M:%S") + ",000 INFO job-ok sync-exchange\n",
        encoding="utf-8")
    assert pulse._last_job_ok() == "11m ago"


def test_the_pre_r12_format_still_parses_after_a_rotation(pulse, tmp_path,
                                                          monkeypatch):
    """The trace-id token is optional, and a backup is where old lines live."""
    monkeypatch.setattr(pulse, "LOG_FILE", tmp_path / "daemon.log")
    (tmp_path / "daemon.log").write_text("", encoding="utf-8")
    when = datetime.now().astimezone() - timedelta(minutes=14)
    (tmp_path / "daemon.log.1").write_text(
        when.strftime("%Y-%m-%d %H:%M:%S") + ",000 INFO job-ok sync-exchange\n",
        encoding="utf-8")
    assert pulse._last_job_ok() == "14m ago"


# ============================================================
# 2. The comment that described the state the code eliminates
# ============================================================

def test_the_parsed_stamp_is_aware_whatever_any_comment_says(pulse, logs):
    """The behaviour the comment denied, asserted rather than described."""
    logs("", timedelta(minutes=1))
    assert pulse._last_job_ok() == "1m ago"


def test_dropping_the_tzinfo_is_what_the_old_comment_asked_for():
    """The counterfactual, so the fix is not just a reworded sentence.

    This is what a reader who trusted "last_ts is naive" produced.
    """
    naive = datetime.strptime("2026-09-01 10:00:00", "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007
    with pytest.raises(TypeError, match="offset-naive and offset-aware"):
        datetime.now().astimezone() - naive


def test_the_function_still_attaches_a_timezone_to_what_it_parsed():
    """Pins the MECHANISM, not the prose.

    This started as a substring sweep for the phrase the old comment used, and
    it failed against the corrected file: the new comment quotes the wrong
    sentence in order to record what it replaced, and the sweep read the
    quotation as the defect. That is the same false positive fixed twice on
    2026-08-29, in `test_no_os_path_join` and in the frontmatter-coercion sweep.
    A rule that punishes a file for documenting its own trap teaches people to
    stop documenting it. A call is a call; prose about a call is not.

    What actually makes the stamp aware is the `.replace(tzinfo=...)` on the
    parsed value. Deleting that line is the regression, and an AST match on the
    keyword sees it whether or not any comment survives.
    """
    tree = ast.parse(PULSE_SRC.read_text(encoding="utf-8"))
    func = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_last_job_ok"), None)
    assert func is not None, "_last_job_ok is gone from the pulse"
    attaches = [
        node for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "replace"
        and any(kw.arg == "tzinfo" for kw in node.keywords)
    ]
    assert attaches, (
        "_last_job_ok no longer attaches a tzinfo to the parsed stamp; the "
        "subtraction below it will raise on an aware `now()`")
