"""Tests for scripts/resolve_customization.py — the three-layer TOML merge resolver.

Ported and extended from BMAD's resolver test. Covers the structural merge primitives
(scalar override, deep table merge, keyed-array merge, append fallback for mixed arrays)
and the layered resolve() against a temp workspace (defaults / team / user / missing layer).

The resolver is snake_case precisely because this test imports its functions directly
(deep_merge / _merge_by_key / _detect_keyed_merge_field / resolve) — hyphens are illegal
in Python module names.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import resolve_customization as rc  # noqa: E402


# --- scalar override -----------------------------------------------------

def test_scalar_override_wins():
    assert rc.deep_merge({"name": "base"}, {"name": "over"}) == {"name": "over"}


def test_scalar_added_when_absent():
    assert rc.deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}


# --- deep table merge ----------------------------------------------------

def test_deep_table_merge():
    base = {"agent": {"name": "A", "icon": "x", "nested": {"k": 1}}}
    over = {"agent": {"icon": "y", "nested": {"j": 2}}}
    merged = rc.deep_merge(base, over)
    assert merged == {"agent": {"name": "A", "icon": "y", "nested": {"k": 1, "j": 2}}}


# --- keyed-array merge ---------------------------------------------------

def test_keyed_merge_by_code_replaces_and_appends():
    base = [{"code": "a", "v": 1}, {"code": "b", "v": 2}]
    over = [{"code": "b", "v": 20}, {"code": "c", "v": 3}]
    merged = rc.deep_merge(base, over)
    assert merged == [{"code": "a", "v": 1}, {"code": "b", "v": 20}, {"code": "c", "v": 3}]


def test_keyed_merge_by_id():
    assert rc._detect_keyed_merge_field([{"id": 1}, {"id": 2}]) == "id"


def test_detect_returns_none_for_mixed_identifier_keys():
    # some items use code, others use id → no shared identifier → append fallback
    assert rc._detect_keyed_merge_field([{"code": "a"}, {"id": 1}]) is None


# --- append fallback -----------------------------------------------------

def test_plain_arrays_append():
    assert rc.deep_merge([1, 2], [3, 4]) == [1, 2, 3, 4]


def test_mixed_identifier_arrays_append_not_merge():
    base = [{"code": "a"}]
    over = [{"id": 1}]
    assert rc.deep_merge(base, over) == [{"code": "a"}, {"id": 1}]


# --- extract_key ---------------------------------------------------------

def test_extract_key_dotted():
    data = {"workflow": {"facts": ["x"]}}
    assert rc.extract_key(data, "workflow.facts") == ["x"]


def test_extract_key_missing_is_sentinel():
    assert rc.extract_key({"a": 1}, "a.b") is rc._MISSING


# --- layered resolve() against a temp workspace --------------------------

def _make_workspace(tmp_path, *, defaults, team=None, user=None):
    skill_dir = tmp_path / ".claude" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "customize.toml").write_text(defaults, encoding="utf-8")
    custom = tmp_path / "config" / "skill-custom"
    custom.mkdir(parents=True)
    if team is not None:
        (custom / "demo.toml").write_text(team, encoding="utf-8")
    if user is not None:
        (custom / "demo.user.toml").write_text(user, encoding="utf-8")
    return skill_dir


def test_resolve_defaults_only(tmp_path, monkeypatch):
    skill_dir = _make_workspace(tmp_path, defaults='[workflow]\nmode = "deep"\n')
    monkeypatch.setattr(rc, "get_workspace_root", lambda: tmp_path)
    assert rc.resolve(skill_dir) == {"workflow": {"mode": "deep"}}


def test_resolve_team_overrides_default(tmp_path, monkeypatch):
    skill_dir = _make_workspace(
        tmp_path, defaults='[workflow]\nmode = "deep"\n', team='[workflow]\nmode = "fast"\n'
    )
    monkeypatch.setattr(rc, "get_workspace_root", lambda: tmp_path)
    assert rc.resolve(skill_dir)["workflow"]["mode"] == "fast"


def test_resolve_user_overrides_team(tmp_path, monkeypatch):
    skill_dir = _make_workspace(
        tmp_path,
        defaults='[workflow]\nmode = "deep"\n',
        team='[workflow]\nmode = "fast"\n',
        user='[workflow]\nmode = "user"\n',
    )
    monkeypatch.setattr(rc, "get_workspace_root", lambda: tmp_path)
    assert rc.resolve(skill_dir)["workflow"]["mode"] == "user"


def test_resolve_missing_layers_is_just_defaults(tmp_path, monkeypatch):
    # no team, no user file → resolve returns the defaults unchanged
    skill_dir = _make_workspace(tmp_path, defaults='[workflow]\nmode = "deep"\nfacts = ["a"]\n')
    monkeypatch.setattr(rc, "get_workspace_root", lambda: tmp_path)
    assert rc.resolve(skill_dir) == {"workflow": {"mode": "deep", "facts": ["a"]}}
