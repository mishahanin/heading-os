"""Tests for the Canopus deny branch in the PreToolUse dispatcher."""
import importlib.util
from pathlib import Path

import pytest

from scripts.utils.canopus_freeze import build_manifest, write_freeze

HOOK_PATH = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "_dispatch.py"
STAMP = "2026-01-01T00:00:00+00:00"


@pytest.fixture
def anchor(tmp_path: Path) -> Path:
    path = tmp_path / "outside" / "gate-artifact.md"
    path.parent.mkdir(parents=True)
    path.write_text("# gate artifact\n")
    return path


@pytest.fixture
def dispatch(monkeypatch, tmp_path: Path):
    """Load _dispatch.py with WORKSPACE pointed at a synthetic tree."""
    root = tmp_path / "tree"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_alpha.py").write_text("def test_a():\n    assert True\n")
    spec = importlib.util.spec_from_file_location("canopus_dispatch_under_test", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "WORKSPACE", root)
    return module, root


def _write(path) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": "x"}}


def _freeze(root, anchor=None):
    write_freeze(root, build_manifest(
        [root / "tests" / "test_alpha.py"], root,
        label="demo", frozen_at=STAMP, anchor=anchor,
    ))


def test_no_freeze_means_no_deny(dispatch):
    module, tree = dispatch
    assert module.check_canopus_freeze(_write(tree / "tests" / "test_alpha.py")) is None


def test_frozen_file_is_denied(dispatch):
    module, tree = dispatch
    _freeze(tree)
    decision = module.check_canopus_freeze(_write(tree / "tests" / "test_alpha.py"))
    assert decision["decision"] == "block"
    assert decision["_policy_deny"] is True
    assert "approval gate" in decision["reason"]


def test_unrelated_file_is_not_denied(dispatch):
    module, tree = dispatch
    _freeze(tree)
    assert module.check_canopus_freeze(_write(tree / "scripts" / "other.py")) is None


def test_file_added_beside_a_frozen_file_is_denied(dispatch):
    module, tree = dispatch
    _freeze(tree)
    assert module.check_canopus_freeze(_write(tree / "tests" / "conftest.py"))["decision"] == "block"


def test_write_inside_a_recursively_frozen_directory_is_denied(dispatch):
    module, tree = dispatch
    write_freeze(tree, build_manifest([tree / "tests"], tree, label="demo", frozen_at=STAMP))
    decision = module.check_canopus_freeze(_write(tree / "tests" / "test_delta.py"))
    assert decision["decision"] == "block"
    assert "frozen directory" in decision["reason"]


def test_writes_to_the_state_directory_are_denied_while_frozen(dispatch):
    module, tree = dispatch
    _freeze(tree)
    assert module.check_canopus_freeze(
        _write(tree / ".canopus" / "freeze.json"))["decision"] == "block"


def test_writes_to_the_anchor_artifact_are_denied_while_frozen(dispatch, anchor):
    module, tree = dispatch
    _freeze(tree, anchor=anchor)
    decision = module.check_canopus_freeze(_write(anchor))
    assert decision["decision"] == "block"
    assert "anchor" in decision["reason"]


def test_a_corrupt_manifest_denies_everything_and_names_the_logged_escape(dispatch):
    module, tree = dispatch
    (tree / ".canopus").mkdir(parents=True, exist_ok=True)
    (tree / ".canopus" / "freeze.json").write_text("{ not json")
    decision = module.check_canopus_freeze(_write(tree / "anything.txt"))
    assert decision["decision"] == "block"
    assert "release --force" in decision["reason"]


def test_non_write_tools_are_ignored(dispatch):
    module, tree = dispatch
    _freeze(tree)
    assert module.check_canopus_freeze({
        "tool_name": "Read",
        "tool_input": {"file_path": str(tree / "tests" / "test_alpha.py")},
    }) is None


@pytest.mark.parametrize("payload", [
    {},
    {"tool_name": "Write"},
    {"tool_name": "Write", "tool_input": None},
    {"tool_name": "Write", "tool_input": {"file_path": None}},
    {"tool_name": "Write", "tool_input": {"file_path": 42}},
    {"tool_name": "Write", "tool_input": {"file_path": "\x00bad"}},
    {"tool_name": None, "tool_input": {"file_path": "x"}},
])
def test_check_never_raises_on_hostile_payloads(dispatch, payload):
    """The dispatcher swallows exceptions and continues, so a raise here fails
    open and silently disables the deny."""
    module, tree = dispatch
    _freeze(tree)
    module.check_canopus_freeze(payload)


def test_check_is_registered_in_the_dispatch_chain(dispatch):
    module, _ = dispatch
    assert module.check_canopus_freeze in module.CHECKS
