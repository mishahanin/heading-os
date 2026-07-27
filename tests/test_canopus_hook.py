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


WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def _write(path, tool: str = "Write") -> dict:
    """A payload for any of the four write tools the deny covers.

    NotebookEdit carries `notebook_path`, not `file_path` — the fallback in the
    check that every other case here would leave untested.
    """
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return {"tool_name": tool, "tool_input": {key: str(path), "content": "x"}}


def _freeze(root, anchor):
    write_freeze(root, build_manifest(
        [root / "tests" / "test_alpha.py"], root,
        label="demo", frozen_at=STAMP, anchor=anchor,
    ))


def test_no_freeze_means_no_deny(dispatch):
    module, tree = dispatch
    assert module.check_canopus_freeze(_write(tree / "tests" / "test_alpha.py")) is None


@pytest.mark.parametrize("tool", WRITE_TOOLS)
def test_every_write_tool_is_denied_on_a_frozen_file(dispatch, anchor, tool):
    """All four tools in _CANOPUS_WRITE_TOOLS, and both payload path keys.

    Exercising only Write/file_path let the tuple be trimmed to ("Write",
    "Edit") — or the `notebook_path` fallback deleted — with a green suite.
    """
    module, tree = dispatch
    _freeze(tree, anchor)
    decision = module.check_canopus_freeze(_write(tree / "tests" / "test_alpha.py", tool))
    assert decision["decision"] == "block"
    assert decision["_policy_deny"] is True
    assert "approval gate" in decision["reason"]


def test_the_covered_tool_tuple_is_exactly_the_four_write_tools(dispatch):
    module, _ = dispatch
    assert module._CANOPUS_WRITE_TOOLS == WRITE_TOOLS


def test_unrelated_file_is_not_denied(dispatch, anchor):
    """UPDATED in wire 2.3, when the deny learned to see directories.

    This asserted `scripts/other.py` is unrelated. There is no `scripts/` in
    this fixture's tree, so that ONE Write creates it — the tool makes missing
    parents — and an importable root directory joins the composition, which
    `verify` reports as `added == ['scripts/']`. Denying it is the deny agreeing
    with the measurement, so the expectation is corrected rather than kept.
    Unrelated now means a path under a directory the composition already
    records, which is what the word has to mean once directories are watched.
    """
    module, tree = dispatch
    _freeze(tree, anchor)
    assert module.check_canopus_freeze(_write(tree / "tests" / "sub" / "other.py")) is None


def test_a_write_that_would_create_an_importable_directory_is_denied(dispatch, anchor):
    """The prevention half of the root guard, at the layer that does the denying.

    Measured missing at the wire 2.3 review: `frozen_reason` watched a directory
    it never refused, so an agent installed the shadowing package in one
    undenied Write while detection at verify still worked.
    """
    module, tree = dispatch
    _freeze(tree, anchor)

    decision = module.check_canopus_freeze(_write(tree / "plug" / "__init__.py"))

    assert decision["decision"] == "block"
    assert decision["_policy_deny"] is True
    assert "plug/" in decision["reason"]


def test_file_added_beside_a_frozen_file_is_denied(dispatch, anchor):
    module, tree = dispatch
    _freeze(tree, anchor)
    assert module.check_canopus_freeze(_write(tree / "tests" / "conftest.py"))["decision"] == "block"


def test_write_inside_a_recursively_frozen_directory_is_denied(dispatch, anchor):
    module, tree = dispatch
    write_freeze(tree, build_manifest([tree / "tests"], tree, label="demo",
                                      frozen_at=STAMP, anchor=anchor))
    decision = module.check_canopus_freeze(_write(tree / "tests" / "test_delta.py"))
    assert decision["decision"] == "block"
    assert "frozen directory" in decision["reason"]


def test_writes_to_the_state_directory_are_denied_while_frozen(dispatch, anchor):
    module, tree = dispatch
    _freeze(tree, anchor)
    assert module.check_canopus_freeze(
        _write(tree / ".canopus" / "freeze.json"))["decision"] == "block"


def test_writes_to_the_anchor_artifact_are_denied_while_frozen(dispatch, anchor):
    module, tree = dispatch
    _freeze(tree, anchor)
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


def test_non_write_tools_are_ignored(dispatch, anchor):
    module, tree = dispatch
    _freeze(tree, anchor)
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
    {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": None}},
    {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": 42}},
])
def test_check_never_raises_on_hostile_payloads(dispatch, anchor, payload):
    """The dispatcher swallows exceptions and continues, so a raise here fails
    open and silently disables the deny."""
    module, tree = dispatch
    _freeze(tree, anchor)
    module.check_canopus_freeze(payload)


def test_check_is_registered_in_the_dispatch_chain(dispatch):
    module, _ = dispatch
    assert module.check_canopus_freeze in module.CHECKS


def test_the_import_is_lazy_and_off_the_non_write_hot_path(dispatch, monkeypatch, anchor):
    """The dispatcher is a fresh process on every Bash and Read too, and the
    check returns None for those before the module is ever imported."""
    module, tree = dispatch
    _freeze(tree, anchor)
    monkeypatch.setattr(module, "_CANOPUS_AVAILABLE", None)

    for payload in (
        {"tool_name": "Bash", "tool_input": {"command": "sed -i s/x/y/ tests/x.py"}},
        {"tool_name": "Read", "tool_input": {"file_path": str(tree / "tests" / "test_alpha.py")}},
    ):
        assert module.check_canopus_freeze(payload) is None
        assert module._CANOPUS_AVAILABLE is None, "import must not run for a non-write tool"

    assert module.check_canopus_freeze(
        _write(tree / "tests" / "test_alpha.py"))["decision"] == "block"
    assert module._CANOPUS_AVAILABLE is True  # cached for the rest of the process


def test_degraded_state_is_permissive_by_design_the_manifest_not_the_deny_is_the_guarantee(
    dispatch, monkeypatch, anchor
):
    """When the canopus_freeze import failed to load, _CANOPUS_AVAILABLE is
    False and the check must early-return None — never deny — even with an
    active freeze covering the write target, and it must never raise. The
    deny is only a convenience; the freeze check run by tests/conftest.py and
    scripts/run-tests.py is the actual guarantee and is unaffected by whether
    this hook-level check is available."""
    module, tree = dispatch
    _freeze(tree, anchor)
    monkeypatch.setattr(module, "_CANOPUS_AVAILABLE", False)
    assert module.check_canopus_freeze(_write(tree / "tests" / "test_alpha.py")) is None


def test_the_corrupt_manifest_escape_names_a_command_that_parses(dispatch):
    """The instruction printed when every write is denied has to be runnable.

    Not a style point: FreezeCorrupt denies Write and Edit fail-closed, so this
    sentence is the operator's only exit, and `release --force --reason` stopped
    parsing the moment the kind became required."""
    from scripts.canopus import build_parser

    module, tree = dispatch
    (tree / ".canopus").mkdir(parents=True, exist_ok=True)
    (tree / ".canopus" / "freeze.json").write_text("{ not json", encoding="utf-8")

    reason = module.check_canopus_freeze(_write(tree / "tests" / "test_alpha.py"))["reason"]

    assert "--force --window" in reason
    build_parser().parse_args(["release", "--force", "--window", "--reason", "x"])


def test_the_frozen_path_deny_names_the_release_window(dispatch, anchor):
    """"Fix the code instead" names no action when the frozen file IS the code.

    That is the ordinary case whenever this tool is under its own maintenance —
    six of the seven tasks in wire 2.2 changed a frozen enforcer — and an
    operator told to fix the code instead is being told to do the thing that was
    just denied. The real move is a release window, so the deny names it, and it
    has to parse: a release names its kind.
    """
    from scripts.canopus import build_parser

    module, tree = dispatch
    _freeze(tree, anchor)

    reason = module.check_canopus_freeze(_write(tree / "tests" / "test_alpha.py"))["reason"]

    assert "release --window --reason" in reason
    # The test half is not dropped: a frozen TEST is still fixed by changing the
    # code, and a deny that named only the window would teach an operator to open
    # one for every contract they disagree with.
    assert "approval gate" in reason
    build_parser().parse_args(["release", "--window", "--reason", "x"])
