"""The session-registry reader, and the shape guard nothing here ever tested.

`read_registry`'s `isinstance(data, dict)` guard is the only thing standing
between a hand-edited or half-written registry and `session_for_cwd`, which
calls `.items()` on whatever comes back. Until 2026-08-31 this file exercised
the guard from ONE side only: every test here wrote a JSON object, so the
`else {}` arm was never reached from any test in `tests/bridge/`.

MEASURED 2026-08-31, in a clone under /tmp, by replacing the whole line

    return data if isinstance(data, dict) else {}

with `return data` and running:

    .venv/bin/python -m pytest tests/bridge -q
    -> 1312 passed, 1 skipped

The mutation survived the entire bridge directory. `tests/test_a_daemon_that_
answered_for_the_wrong_tree.py::test_a_session_registry_holding_a_list_reads_
as_empty` does catch it, so the guard was not naked tree-wide. But it covers
the LIST shape and nothing else, and a bridge-only run (which is what anyone
touching `sessions.py` reaches for) reported green. A registry top level can
arrive as a string, as `null`, or as a number just as easily: `null` is what a
truncated atomic write leaves behind, and a bare string is what an older hook
wrote. Each is a different failure without the guard, so each needs its own
case ON the line rather than one representative.

`active_count` had no test at all. Full-suite branch coverage on 2026-08-31
reported `scripts/bridge_daemon/sessions.py ... 95% Missing 62->56, 68`, and
line 68 IS the whole body of `active_count`: 19,835 tests never called it, so
mutating it to `return 0` cannot fail anything.

Stated narrowly, because it is easy to overstate: `grep -rn "active_count"
scripts/ .claude/hooks/` on 2026-08-31 found no caller of THIS function
anywhere in the tree (the other hits are unrelated identifiers in
`admin-health.py` and `pipeline-summary.py`). So it is dead as well as
untested. It is pinned here anyway rather than left alone, because the module
exports it as a public reader and `heartbeat.py` maintains a SECOND, separate
count of the same file, which is the drift `registry_path`'s docstring records
as a real incident: that copy read a path nothing writes and reported
`active_sessions: 0` for a daemon serving live sessions. A second reader of one
file is exactly the shape that needs its own case.
"""
import json
from pathlib import Path

import pytest

from scripts.bridge_daemon.sessions import active_count, read_registry, session_for_cwd

def test_read_registry_missing_file_returns_empty(tmp_path):
    assert read_registry(tmp_path / "absent.json") == {}

def test_read_registry_returns_dict(tmp_path):
    f = tmp_path / "active-sessions.json"
    f.write_text(json.dumps({"/work/foo": {"session_id": "abc", "pid": 123}}))
    data = read_registry(f)
    assert data["/work/foo"]["session_id"] == "abc"

def test_session_for_cwd_returns_id(tmp_path):
    """Keyed by session_id, with cwd as a field: the shape the hook writes.

    This seeded the pre-2026-08-23 cwd-keyed shape, which the hook can no longer
    produce, so it stayed green over a lookup that could never hit in production.
    """
    f = tmp_path / "active-sessions.json"
    f.write_text(json.dumps({
        "abc": {"session_id": "abc", "cwd": "/work/foo",
                "started_at": "2026-08-25T00:00:00+00:00"},
    }))
    assert session_for_cwd(f, "/work/foo") == "abc"
    assert session_for_cwd(f, "/work/bar") is None


def test_session_for_cwd_picks_the_newest_of_several(tmp_path):
    f = tmp_path / "active-sessions.json"
    f.write_text(json.dumps({
        "old": {"session_id": "old", "cwd": "/work/foo",
                "started_at": "2026-08-01T00:00:00+00:00"},
        "new": {"session_id": "new", "cwd": "/work/foo",
                "started_at": "2026-08-25T00:00:00+00:00"},
        "other": {"session_id": "other", "cwd": "/work/bar",
                  "started_at": "2026-08-26T00:00:00+00:00"},
    }))
    assert session_for_cwd(f, "/work/foo") == "new"


@pytest.mark.parametrize("payload,what", [
    ('[{"session_id": "abc", "cwd": "/work/foo"}]', "a JSON array"),
    ('"abc"', "a bare session-id string"),
    ("null", "a null left by a truncated write"),
    ("17", "a number"),
    ("true", "a boolean"),
])
def test_a_registry_whose_top_level_is_not_an_object_reads_as_empty(
        tmp_path, payload, what):
    """One case per shape, each ON the line the guard draws.

    All three public readers of the registry are asserted, not just the one
    with a live caller. `session_for_cwd` is reached from `/launch`
    (`app.py:999`); without the guard it raises AttributeError on a string and
    on a number, and on a list it calls `.items()` on a list. `active_count`
    has no caller today and still must not answer 3 for a three-element array,
    because a length is not a session count.
    """
    f = tmp_path / "active-sessions.json"
    f.write_text(payload, encoding="utf-8")
    assert read_registry(f) == {}, what
    assert session_for_cwd(f, "/work/foo") is None, what
    assert active_count(f) == 0, what


def test_active_count_counts_the_registered_sessions(tmp_path):
    """The anchor. Without it the guard above is satisfied by `return 0`.

    `active_count` was executed by no test in the tree until 2026-08-31, so
    `return 0` was a free pass: every shape assertion above would still hold.
    """
    f = tmp_path / "active-sessions.json"
    f.write_text(json.dumps({
        "s1": {"session_id": "s1", "cwd": "/work/foo",
               "started_at": "2026-08-25T00:00:00+00:00"},
        "s2": {"session_id": "s2", "cwd": "/work/bar",
               "started_at": "2026-08-26T00:00:00+00:00"},
    }), encoding="utf-8")
    assert active_count(f) == 2
    assert active_count(tmp_path / "absent.json") == 0


def test_a_non_dict_entry_is_skipped_not_indexed(tmp_path):
    f = tmp_path / "active-sessions.json"
    f.write_text(json.dumps({
        "junk": "a bare session id from an older hook",
        "abc": {"session_id": "abc", "cwd": "/work/foo",
                "started_at": "2026-08-25T00:00:00+00:00"},
    }))
    assert session_for_cwd(f, "/work/foo") == "abc"
