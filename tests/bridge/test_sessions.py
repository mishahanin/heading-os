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
    f = tmp_path / "active-sessions.json"
    f.write_text(json.dumps({"/work/foo": {"session_id": "abc"}}))
    assert session_for_cwd(f, "/work/foo") == "abc"
    assert session_for_cwd(f, "/work/bar") is None
