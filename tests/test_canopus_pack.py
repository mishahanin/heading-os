"""Canopus wire 2: the Fix 2 evidence page."""
import json
from datetime import datetime, timezone


def _ts(minute):
    return datetime(2026, 7, 25, 12, minute, tzinfo=timezone.utc)


def test_freeze_windows_pairs_freeze_with_release():
    from scripts.utils.canopus_pack import freeze_windows

    windows = freeze_windows([
        {"event": "freeze", "ts": _ts(0).isoformat()},
        {"event": "release", "ts": _ts(10).isoformat()},
        {"event": "freeze", "ts": _ts(20).isoformat()},
    ])

    assert windows == [(_ts(0), _ts(10)), (_ts(20), None)]


def test_commits_outside_flags_a_commit_between_windows():
    from scripts.utils.canopus_pack import commits_outside

    windows = [(_ts(0), _ts(10)), (_ts(20), None)]
    commits = [
        ("aaa1111", _ts(5), "inside"),
        ("bbb2222", _ts(15), "outside"),
        ("ccc3333", _ts(25), "inside the open window"),
    ]

    assert [sha for sha, _when, _subject in commits_outside(commits, windows)] == ["bbb2222"]


def test_read_ledger_skips_damaged_lines(tmp_path):
    from scripts.utils.canopus_freeze import history_state_path
    from scripts.utils.canopus_pack import read_ledger

    path = history_state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"event": "freeze", "ts": _ts(0).isoformat()}) + "\n"
        + "{not json\n"
        + json.dumps({"event": "release", "ts": _ts(10).isoformat()}) + "\n",
        encoding="utf-8",
    )

    assert [entry["event"] for entry in read_ledger(tmp_path)] == ["freeze", "release"]


def test_read_ledger_is_empty_when_there_is_no_ledger(tmp_path):
    from scripts.utils.canopus_pack import read_ledger

    assert read_ledger(tmp_path) == []


def test_freeze_windows_ignores_an_entry_with_an_unparseable_timestamp():
    from scripts.utils.canopus_pack import freeze_windows

    assert freeze_windows([
        {"event": "freeze", "ts": "not a timestamp"},
        {"event": "freeze", "ts": _ts(0).isoformat()},
    ]) == [(_ts(0), None)]


def test_a_naive_ledger_timestamp_does_not_break_the_comparison():
    """A hand-edited ledger line without an offset must not raise.

    git's `%cI` is always offset-aware, so a naive window boundary would make
    `start <= when` raise TypeError, inside the one command whose whole promise
    is to answer rather than traceback.
    """
    from scripts.utils.canopus_pack import commits_outside, freeze_windows

    windows = freeze_windows([{"event": "freeze", "ts": "2026-07-25T12:00:00"}])
    assert commits_outside([("aaa1111", _ts(5), "inside")], windows) == []


def test_git_helpers_degrade_outside_a_repository(tmp_path):
    """Every git helper answers rather than raising when git cannot help.

    The pack is read at the one moment the operator is deciding to keep the
    work. A traceback there is worse than a missing section.
    """
    from scripts.utils.canopus_pack import diff_stat, git_commits, is_dirty, merge_base

    assert git_commits(tmp_path, "HEAD") == []
    assert merge_base(tmp_path, "main") is None
    assert is_dirty(tmp_path) is False
    assert diff_stat(tmp_path, "HEAD") == ""
