"""Tests for get_workspace_identity caching + parse-error behaviour.

Regression coverage for the 2026-05-27 incident where a transient read failure
on an exec's .workspace-identity.json caused get_workspace_identity() to fall
back to the CEO default mid-sync, which routed his CRM push to the wrong repo.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.utils.workspace as ws


@pytest.fixture(autouse=True)
def _reset_cache():
    ws._reset_identity_cache()
    yield
    ws._reset_identity_cache()


def _point_workspace_root(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(ws, "get_workspace_root", lambda: path)


def test_returns_file_contents_when_valid(tmp_path, monkeypatch):
    (tmp_path / ".workspace-identity.json").write_text(
        json.dumps({"slug": "alice", "type": "exec-workspace", "role": "exec"}),
        encoding="utf-8",
    )
    _point_workspace_root(monkeypatch, tmp_path)

    result = ws.get_workspace_identity()
    assert result == {"slug": "alice", "type": "exec-workspace", "role": "exec"}


def test_returns_ceo_default_when_file_missing(tmp_path, monkeypatch):
    _point_workspace_root(monkeypatch, tmp_path)

    result = ws.get_workspace_identity()
    assert result["slug"] == "misha-hanin"
    assert result["type"] == "ceo-master"
    assert result["role"] == "admin"


def test_raises_when_file_unparseable(tmp_path, monkeypatch):
    (tmp_path / ".workspace-identity.json").write_text(
        "not valid json {", encoding="utf-8"
    )
    _point_workspace_root(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="cannot be parsed"):
        ws.get_workspace_identity()


def test_cache_freezes_identity_mid_process(tmp_path, monkeypatch):
    """Mid-process mutation of the file must NOT change what callers observe."""
    identity_file = tmp_path / ".workspace-identity.json"
    identity_file.write_text(
        json.dumps({"slug": "sam-carter", "type": "exec-workspace", "role": "exec"}),
        encoding="utf-8",
    )
    _point_workspace_root(monkeypatch, tmp_path)

    first = ws.get_workspace_identity()
    identity_file.write_text(
        json.dumps({"slug": "misha-hanin", "type": "ceo-master", "role": "admin"}),
        encoding="utf-8",
    )
    second = ws.get_workspace_identity()

    assert first == second
    assert second["slug"] == "sam-carter"


def test_get_exec_slug_uses_cached_identity(tmp_path, monkeypatch):
    (tmp_path / ".workspace-identity.json").write_text(
        json.dumps({"slug": "sam-carter", "type": "exec-workspace", "role": "exec"}),
        encoding="utf-8",
    )
    _point_workspace_root(monkeypatch, tmp_path)

    assert ws.get_exec_slug() == "sam-carter"


def test_separate_workspace_roots_get_separate_cache_entries(tmp_path, monkeypatch):
    ws_a = tmp_path / "a"
    ws_b = tmp_path / "b"
    ws_a.mkdir()
    ws_b.mkdir()
    (ws_a / ".workspace-identity.json").write_text(
        json.dumps({"slug": "alice", "type": "exec-workspace", "role": "exec"}), encoding="utf-8"
    )
    (ws_b / ".workspace-identity.json").write_text(
        json.dumps({"slug": "bob", "type": "exec-workspace", "role": "exec"}), encoding="utf-8"
    )

    monkeypatch.setattr(ws, "get_workspace_root", lambda: ws_a)
    a = ws.get_workspace_identity()
    monkeypatch.setattr(ws, "get_workspace_root", lambda: ws_b)
    b = ws.get_workspace_identity()

    assert a["slug"] == "alice"
    assert b["slug"] == "bob"
