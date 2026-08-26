import json
from pathlib import Path
from scripts.bridge_daemon.sessions import read_registry, session_for_cwd

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


def test_a_non_dict_entry_is_skipped_not_indexed(tmp_path):
    f = tmp_path / "active-sessions.json"
    f.write_text(json.dumps({
        "junk": "a bare session id from an older hook",
        "abc": {"session_id": "abc", "cwd": "/work/foo",
                "started_at": "2026-08-25T00:00:00+00:00"},
    }))
    assert session_for_cwd(f, "/work/foo") == "abc"
