"""Tests for the Canopus freeze primitive (wire 1)."""
import json
from pathlib import Path

import pytest

from scripts.utils.canopus_freeze import (
    ANCHOR_MISSING,
    ANCHOR_NONE,
    ANCHOR_RECORDED,
    ANCHOR_UNRECORDED,
    LOCK_HELD,
    LOCK_UNCONFIRMED,
    LOSS_OF_LOCK,
    RECIPE,
    FreezeCorrupt,
    FreezeError,
    _validate_manifest_shape,
    anchor_state,
    append_history,
    build_manifest,
    clear_freeze,
    dir_members_digest,
    file_digest,
    freeze_state_path,
    frozen_reason,
    history_state_path,
    lock_state,
    read_anchor,
    read_freeze,
    root_hash,
    validate_anchor_path,
    validate_freeze_path,
    verify_manifest,
    write_freeze,
)

STAMP = "2026-01-01T00:00:00+00:00"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A synthetic working tree: tests/ with two files and a nested subdir."""
    root = tmp_path / "tree"
    (root / "tests" / "sub").mkdir(parents=True)
    (root / "tests" / "test_alpha.py").write_text("def test_a():\n    assert True\n")
    (root / "tests" / "test_beta.py").write_text("def test_b():\n    assert True\n")
    (root / "tests" / "sub" / "test_gamma.py").write_text("def test_g():\n    assert True\n")
    return root


@pytest.fixture
def anchor(tmp_path: Path) -> Path:
    """An anchor artifact OUTSIDE the working tree, as the design requires."""
    path = tmp_path / "outside" / "gate-artifact.md"
    path.parent.mkdir(parents=True)
    path.write_text("# gate artifact\n")
    return path


def test_file_digest_is_lf_normalized(tmp_path: Path):
    lf = tmp_path / "lf.txt"
    crlf = tmp_path / "crlf.txt"
    lf.write_bytes(b"one\ntwo\n")
    crlf.write_bytes(b"one\r\ntwo\r\n")
    assert file_digest(lf) == file_digest(crlf)


def test_dir_members_digest_ignores_creation_order(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a.py").write_text("x")
    (first / "b.py").write_text("y")
    (second / "b.py").write_text("different content")
    (second / "a.py").write_text("also different")
    assert dir_members_digest(first, recursive=False) == dir_members_digest(second, recursive=False)


def test_dir_members_digest_recursive_differs_from_shallow(tree: Path):
    shallow = dir_members_digest(tree / "tests", recursive=False)
    deep = dir_members_digest(tree / "tests", recursive=True)
    assert shallow != deep


def test_directory_freeze_skips_tool_generated_caches(tree: Path, anchor: Path):
    """A recursive freeze must not bind the lock to artifacts a build regenerates.

    Measured at the first real use of the tool on itself, 2026-07-25: freezing a
    test directory captured `__pycache__/*.pyc`, and removing it the way any
    fresh clone or cache clean does reported LOSS OF LOCK for a change nobody
    made.
    """
    cache = tree / "tests" / "__pycache__"
    cache.mkdir()
    (cache / "test_alpha.cpython-311.pyc").write_bytes(b"\x00compiled")
    (tree / "tests" / "stray.pyc").write_bytes(b"\x00compiled")

    manifest = build_manifest(
        [tree / "tests"], tree, label="l", frozen_at=STAMP, anchor=anchor
    )

    captured = list(manifest["files"]) + manifest["dirs"]["tests"]["members"]
    assert not [name for name in captured if "__pycache__" in name or name.endswith(".pyc")]


def test_a_cache_appearing_after_the_freeze_holds_the_lock(tree: Path, anchor: Path):
    """The build loop regenerates these on every run, so this is the live case."""
    manifest = build_manifest(
        [tree / "tests"], tree, label="l", frozen_at=STAMP, anchor=anchor
    )

    cache = tree / "tests" / "sub" / "__pycache__"
    cache.mkdir()
    (cache / "test_gamma.cpython-311.pyc").write_bytes(b"\x00compiled")

    result = verify_manifest(manifest, tree)
    assert result["held"], result


def test_root_hash_ignores_label_and_timestamp(tree: Path):
    paths = [tree / "tests" / "test_alpha.py"]
    one = build_manifest(paths, tree, label="first", frozen_at=STAMP)
    two = build_manifest(paths, tree, label="second", frozen_at="2030-12-31T23:59:59+00:00")
    assert one["root"] == two["root"]
    assert one["label"] != two["label"]


def test_root_hash_ignores_git_sha(tree: Path):
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    before = manifest["root"]
    manifest["git_sha"] = "deadbeef"
    assert root_hash(manifest) == before


def test_root_hash_changes_with_content(tree: Path):
    paths = [tree / "tests" / "test_alpha.py"]
    before = build_manifest(paths, tree, label="l", frozen_at=STAMP)
    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    after = build_manifest(paths, tree, label="l", frozen_at=STAMP)
    assert before["root"] != after["root"]


def test_root_hash_changes_with_the_anchor_path(tree: Path, anchor: Path, tmp_path: Path):
    other = tmp_path / "outside" / "other-artifact.md"
    other.write_text("# other\n")
    paths = [tree / "tests" / "test_alpha.py"]
    one = build_manifest(paths, tree, label="l", frozen_at=STAMP, anchor=anchor)
    two = build_manifest(paths, tree, label="l", frozen_at=STAMP, anchor=other)
    assert one["root"] != two["root"]


def test_freezing_a_file_adds_a_non_recursive_parent_guard(tree: Path):
    manifest = build_manifest([tree / "tests" / "test_alpha.py"], tree, label="l", frozen_at=STAMP)
    assert set(manifest["files"]) == {"tests/test_alpha.py"}
    assert manifest["dirs"]["tests"]["mode"] == "members"


def test_freezing_a_directory_is_recursive_and_records_every_member(tree: Path):
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    assert manifest["dirs"]["tests"]["mode"] == "recursive"
    assert set(manifest["files"]) == {
        "tests/test_alpha.py",
        "tests/test_beta.py",
        "tests/sub/test_gamma.py",
    }


def test_manifest_carries_the_recipe(tree: Path):
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    assert manifest["recipe"] == RECIPE


def test_root_level_file_guards_the_roots_importable_composition(tree: Path):
    """Wire 1 installed no guard at all here; wire 2 measured why that was wrong.

    The root is the first sys.path entry, so it is the one directory whose
    composition the contract's own imports resolve against. The guard watches
    what is importable and ignores the rest, which is the same objection wire 1
    raised, answered instead of conceded.
    """
    (tree / "conftest.py").write_text("# root conftest\n")
    manifest = build_manifest([tree / "conftest.py"], tree, label="l", frozen_at=STAMP)
    assert manifest["dirs"][""]["names"] == ["*.py"]

    (tree / "README.md").write_text("notes\n")
    assert verify_manifest(manifest, tree)["held"] is True

    (tree / "stub.py").write_text("answer = 42\n")
    assert verify_manifest(manifest, tree)["held"] is False


def test_validate_refuses_a_missing_path(tree: Path):
    with pytest.raises(FreezeError, match="does not exist"):
        validate_freeze_path(tree / "tests" / "nope.py", tree)


def test_validate_refuses_a_path_outside_the_tree(tree: Path, anchor: Path):
    with pytest.raises(FreezeError, match="outside the working tree"):
        validate_freeze_path(anchor, tree)


def test_validate_refuses_a_symlink(tree: Path):
    link = tree / "tests" / "link_to_alpha.py"
    link.symlink_to(tree / "tests" / "test_alpha.py")
    with pytest.raises(FreezeError, match="symlink"):
        validate_freeze_path(link, tree)


def test_validate_refuses_the_state_directory(tree: Path):
    (tree / ".canopus").mkdir()
    with pytest.raises(FreezeError, match=r"\.canopus"):
        validate_freeze_path(tree / ".canopus", tree)


def test_validate_anchor_refuses_a_path_inside_the_tree(tree: Path):
    inside = tree / "gate.md"
    inside.write_text("# nope\n")
    with pytest.raises(FreezeError, match="inside the working tree"):
        validate_anchor_path(inside, tree)


def test_validate_anchor_refuses_a_missing_file(tree: Path, tmp_path: Path):
    with pytest.raises(FreezeError, match="does not exist"):
        validate_anchor_path(tmp_path / "outside" / "absent.md", tree)


def test_manifest_is_json_serializable(tree: Path, anchor: Path):
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP, anchor=anchor)
    assert json.loads(json.dumps(manifest)) == manifest


def test_verify_holds_on_an_untouched_tree(tree: Path):
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    report = verify_manifest(manifest, tree)
    assert report["held"] is True
    assert report["recomputed_root"] == manifest["root"]
    assert report["changed"] == []
    assert report["added"] == []
    assert report["removed"] == []


def test_verify_detects_a_changed_file(tree: Path):
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    report = verify_manifest(manifest, tree)
    assert report["held"] is False
    assert report["changed"] == ["tests/test_alpha.py"]


def test_verify_detects_a_removed_file(tree: Path):
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    (tree / "tests" / "test_beta.py").unlink()
    report = verify_manifest(manifest, tree)
    assert report["held"] is False
    assert report["removed"] == ["tests/test_beta.py"]


def test_verify_detects_a_file_added_beside_a_frozen_file(tree: Path):
    manifest = build_manifest([tree / "tests" / "test_alpha.py"], tree, label="l", frozen_at=STAMP)
    (tree / "tests" / "conftest.py").write_text("# neutralizes the frozen test\n")
    report = verify_manifest(manifest, tree)
    assert report["held"] is False
    assert report["added"] == ["tests/conftest.py"]


def test_implicit_guard_ignores_a_sibling_subdirectory(tree: Path):
    manifest = build_manifest([tree / "tests" / "test_alpha.py"], tree, label="l", frozen_at=STAMP)
    (tree / "tests" / "sub" / "test_delta.py").write_text("def test_d():\n    assert True\n")
    report = verify_manifest(manifest, tree)
    assert report["held"] is True


def test_implicit_guard_reports_neither_added_nor_removed_when_nothing_moved(tree: Path):
    """A guard covers siblings that were never frozen individually.

    tests/ holds test_beta.py, which is inside the guard but outside the file
    map. Diffing composition against the file map would call it "added" on the
    very first verify, so the guard would cry wolf before anyone touched it.
    """
    manifest = build_manifest([tree / "tests" / "test_alpha.py"], tree, label="l", frozen_at=STAMP)
    report = verify_manifest(manifest, tree)
    assert report["held"] is True
    assert report["added"] == []
    assert report["removed"] == []

    # The ancestor guard watches conftest.py, so a sibling test coming or going
    # is the builder's business. What the guard is for is the file pytest
    # imports on its own, and that one still moves the lock.
    (tree / "tests" / "test_beta.py").unlink()
    assert verify_manifest(manifest, tree)["held"] is True

    (tree / "tests" / "conftest.py").write_text("import sys\n")
    after = verify_manifest(manifest, tree)
    assert after["held"] is False
    assert after["added"] == ["tests/conftest.py"]


def test_explicit_directory_freeze_catches_a_subdirectory_addition(tree: Path):
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    (tree / "tests" / "sub" / "test_delta.py").write_text("def test_d():\n    assert True\n")
    report = verify_manifest(manifest, tree)
    assert report["held"] is False
    assert report["added"] == ["tests/sub/test_delta.py"]


def test_read_anchor_reports_missing(tmp_path: Path):
    assert read_anchor(tmp_path / "absent.md") == (ANCHOR_MISSING, None)


def test_read_anchor_reports_unrecorded(anchor: Path):
    assert read_anchor(anchor) == (ANCHOR_UNRECORDED, None)


def test_read_anchor_returns_the_recorded_hash(anchor: Path):
    anchor.write_text("# gate\n\ncanopus-anchor: " + "a" * 64 + "\n\nmore prose\n")
    assert read_anchor(anchor) == (ANCHOR_RECORDED, "a" * 64)


def test_anchor_state_reads_the_manifest_anchor(tree: Path, anchor: Path):
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP,
                              anchor=anchor)
    anchor.write_text("canopus-anchor: " + "a" * 64 + "\n")
    assert anchor_state(manifest) == (str(anchor.resolve()), ANCHOR_RECORDED, "a" * 64)


def test_anchor_state_prefers_an_override(tree: Path, anchor: Path, tmp_path: Path):
    other = tmp_path / "outside" / "other.md"
    other.write_text("canopus-anchor: " + "b" * 64 + "\n")
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP,
                              anchor=anchor)
    assert anchor_state(manifest, str(other)) == (str(other), ANCHOR_RECORDED, "b" * 64)


def test_anchor_state_reports_none_for_an_anchorless_manifest(tree: Path):
    """One producer of the status string, so a typo cannot degrade a state.

    ANCHOR_NONE is not a bare literal in either caller: lock_state matching a
    misspelled status would silently read it as "recorded but disagreeing" and
    report LOSS OF LOCK.
    """
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    assert anchor_state(manifest) == ("", ANCHOR_NONE, None)


def _report(held: bool, digest: str = "a" * 64) -> dict:
    return {"recomputed_root": digest, "changed": [], "added": [], "removed": [], "held": held}


def test_lock_state_held():
    assert lock_state(_report(True), ANCHOR_RECORDED, "a" * 64) == LOCK_HELD


def test_lock_state_loss_on_content_change():
    assert lock_state(_report(False), ANCHOR_RECORDED, "a" * 64) == LOSS_OF_LOCK


def test_lock_state_loss_on_anchor_disagreement():
    assert lock_state(_report(True), ANCHOR_RECORDED, "b" * 64) == LOSS_OF_LOCK


def test_lock_state_loss_on_missing_anchor():
    assert lock_state(_report(True), ANCHOR_MISSING, None) == LOSS_OF_LOCK


def test_lock_state_unconfirmed_without_a_recorded_hash():
    assert lock_state(_report(True), ANCHOR_UNRECORDED, None) == LOCK_UNCONFIRMED


def test_lock_state_unconfirmed_without_an_anchor():
    assert lock_state(_report(True), ANCHOR_NONE, None) == LOCK_UNCONFIRMED


def test_frozen_reason_names_a_frozen_file(tree: Path):
    manifest = build_manifest([tree / "tests" / "test_alpha.py"], tree, label="l", frozen_at=STAMP)
    assert "frozen contract file" in frozen_reason("tests/test_alpha.py", manifest)


def test_frozen_reason_covers_the_composition_guard(tree: Path):
    manifest = build_manifest([tree / "tests" / "test_alpha.py"], tree, label="l", frozen_at=STAMP)
    assert "composition" in frozen_reason("tests/conftest.py", manifest)


def test_frozen_reason_covers_a_recursive_directory(tree: Path):
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    assert "frozen directory" in frozen_reason("tests/sub/test_delta.py", manifest)


def test_frozen_reason_is_none_for_an_unrelated_path(tree: Path):
    manifest = build_manifest([tree / "tests" / "test_alpha.py"], tree, label="l", frozen_at=STAMP)
    assert frozen_reason("scripts/canopus.py", manifest) is None


def test_frozen_reason_does_not_leak_across_a_similar_prefix(tree: Path):
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    assert frozen_reason("tests_extra/test_x.py", manifest) is None


def test_read_freeze_returns_none_when_no_freeze_is_active(tree: Path):
    assert read_freeze(tree) is None


def test_write_then_read_round_trips(tree: Path):
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    write_freeze(tree, manifest)
    assert read_freeze(tree) == manifest


def test_read_freeze_raises_on_malformed_json(tree: Path):
    path = freeze_state_path(tree)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json")
    with pytest.raises(FreezeCorrupt, match="unreadable"):
        read_freeze(tree)


def test_read_freeze_raises_on_invalid_utf8_bytes(tree: Path):
    """A partially written or byte-corrupted manifest must not escape as a
    bare UnicodeDecodeError. Path.read_text(encoding="utf-8") raises
    UnicodeDecodeError on invalid byte sequences, and UnicodeDecodeError
    subclasses ValueError, not OSError -- so it slipped past the original
    (OSError, json.JSONDecodeError) catch. The Task 5 dispatcher only catches
    FreezeCorrupt and OSError specifically; anything else falls into its
    generic handler, which logs and continues -- fail open, on frozen
    contract paths.
    """
    path = freeze_state_path(tree)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"recipe": "x\xff\xfe"}')
    with pytest.raises(FreezeCorrupt, match="unreadable"):
        read_freeze(tree)


@pytest.mark.parametrize("payload", ["[]", "null", "42", '"a string"'],
                         ids=["array", "null", "number", "string"])
def test_read_freeze_raises_on_valid_json_that_is_not_an_object(tree: Path, payload):
    """Well-formed JSON of the wrong TOP-LEVEL type is still a corrupt manifest.

    This is the branch that keeps an AttributeError from escaping to the
    dispatcher: every later step calls manifest.get(...) or subscripts it, and
    the dispatcher catches only FreezeCorrupt and OSError — anything else falls
    into its outer handler, which logs an advisory and CONTINUES, i.e. fails
    open on a frozen contract path.
    """
    path = freeze_state_path(tree)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload)
    with pytest.raises(FreezeCorrupt, match="not a JSON object"):
        read_freeze(tree)


def test_read_freeze_raises_on_an_unknown_recipe(tree: Path):
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    manifest["recipe"] = "canopus-freeze-v99"
    path = freeze_state_path(tree)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest))
    with pytest.raises(FreezeCorrupt, match="recipe"):
        read_freeze(tree)


def test_read_freeze_raises_on_a_missing_required_key(tree: Path):
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    del manifest["files"]
    path = freeze_state_path(tree)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest))
    with pytest.raises(FreezeCorrupt, match="files"):
        read_freeze(tree)


def _corrupt_files_not_dict(manifest: dict) -> None:
    manifest["files"] = []


def _corrupt_files_key_not_str(manifest: dict) -> None:
    manifest["files"] = {123: "a" * 64, **manifest["files"]}


def _corrupt_files_value_not_str(manifest: dict) -> None:
    first = next(iter(manifest["files"]))
    manifest["files"][first] = 12345


def _corrupt_dirs_not_dict(manifest: dict) -> None:
    manifest["dirs"] = "x"


def _corrupt_dirs_entry_not_dict(manifest: dict) -> None:
    manifest["dirs"]["tests"] = "not-a-dict"


def _corrupt_dirs_entry_missing_mode(manifest: dict) -> None:
    del manifest["dirs"]["tests"]["mode"]


def _corrupt_dirs_entry_missing_hash(manifest: dict) -> None:
    del manifest["dirs"]["tests"]["hash"]


def _corrupt_dirs_entry_missing_members(manifest: dict) -> None:
    del manifest["dirs"]["tests"]["members"]


def _corrupt_mode_not_str(manifest: dict) -> None:
    manifest["dirs"]["tests"]["mode"] = 7


def _corrupt_mode_unrecognised(manifest: dict) -> None:
    manifest["dirs"]["tests"]["mode"] = "shallow"


def _corrupt_members_not_list(manifest: dict) -> None:
    manifest["dirs"]["tests"]["members"] = {"nested": "dict"}


def _corrupt_members_element_not_str(manifest: dict) -> None:
    # The exact escape a second review reproduced: a `dirs` entry's `members`
    # list holding a dict, which reaches verify_manifest()'s
    # `set(entry["members"])` as an uncaught TypeError: unhashable type: 'dict'.
    manifest["dirs"]["tests"]["members"] = [{"nested": "dict"}]


def _corrupt_root_not_str(manifest: dict) -> None:
    manifest["root"] = 12345


def _corrupt_label_not_str(manifest: dict) -> None:
    manifest["label"] = 42


def _corrupt_anchor_not_str(manifest: dict) -> None:
    manifest["anchor"] = 42


def _corrupt_frozen_at_not_str(manifest: dict) -> None:
    manifest["frozen_at"] = 42


def _corrupt_git_sha_not_str(manifest: dict) -> None:
    manifest["git_sha"] = 42


@pytest.mark.parametrize(
    "corrupt",
    [
        _corrupt_files_not_dict,
        _corrupt_files_value_not_str,
        _corrupt_dirs_not_dict,
        _corrupt_dirs_entry_not_dict,
        _corrupt_dirs_entry_missing_mode,
        _corrupt_dirs_entry_missing_hash,
        _corrupt_dirs_entry_missing_members,
        _corrupt_mode_not_str,
        _corrupt_mode_unrecognised,
        _corrupt_members_not_list,
        _corrupt_members_element_not_str,
        _corrupt_root_not_str,
        _corrupt_label_not_str,
        _corrupt_anchor_not_str,
        _corrupt_frozen_at_not_str,
        _corrupt_git_sha_not_str,
    ],
    ids=lambda fn: fn.__name__.removeprefix("_corrupt_"),
)
def test_manifest_shape_validation_rejects_corrupted_shapes(tree: Path, corrupt):
    """Table-driven regression for the complete manifest shape (round two).

    Round one guarded only the top-level container types (files/dirs/root/
    label). A second review reproduced the same bug class one level deeper: a
    `dirs` entry's `members` list held a dict, which is a list so it passed
    the top-level isinstance(list) check, and `verify_manifest` crashed on
    `set(entry["members"])`. This table validates the complete shape
    `build_manifest()` produces in one pass, so a future escape has to defeat
    every case here at once instead of finding the next unchecked corner. All
    five `_STR_SCALAR_KEYS` (label, frozen_at, anchor, git_sha, root) are
    exercised here so the completeness claim in `_validate_manifest_shape`'s
    docstring matches the test evidence.

    Every case here is reachable through the public interface, so it is
    routed through `read_freeze()` against a real freeze.json on disk -- that
    exercises the validator AND the wiring a consumer actually depends on,
    not the validator in isolation. `_corrupt_files_key_not_str` is the one
    exception: JSON object member names are always strings by spec (RFC
    8259), so a non-string dict key cannot survive a json.dumps/json.loads
    round trip and this shape can never actually reach read_freeze() from a
    file. It gets its own test below, called directly against the validator.
    """
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    corrupt(manifest)
    path = freeze_state_path(tree)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest))
    with pytest.raises(FreezeCorrupt):
        read_freeze(tree)


def test_manifest_shape_validation_rejects_a_non_string_files_key(tree: Path):
    """The one unreachable corrupted shape, kept honest about why.

    JSON object member names are always strings (RFC 8259), so
    `_corrupt_files_key_not_str` cannot survive a real json.dumps/json.loads
    round trip and can never actually reach `read_freeze()` from a file on
    disk. Exercised directly against the validator instead, as defense in
    depth against any future non-JSON manifest source -- not dressed up as
    reachable through the public interface.
    """
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    _corrupt_files_key_not_str(manifest)
    with pytest.raises(FreezeCorrupt):
        _validate_manifest_shape(manifest, freeze_state_path(tree))


def test_clear_freeze_is_idempotent(tree: Path):
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    write_freeze(tree, manifest)
    clear_freeze(tree)
    clear_freeze(tree)
    assert read_freeze(tree) is None


def test_history_is_append_only(tree: Path):
    append_history(tree, "freeze", digest="aaa", label="one")
    append_history(tree, "release", digest="aaa", label="one", reason="done")
    lines = history_state_path(tree).read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "freeze"
    assert json.loads(lines[1])["reason"] == "done"


def test_history_survives_a_corrupt_manifest(tree: Path):
    """The logged escape must work when freeze.json cannot be parsed."""
    path = freeze_state_path(tree)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json")
    append_history(tree, "force_release", digest="", label="", reason="corrupt")
    assert "force_release" in history_state_path(tree).read_text()


def test_history_entries_carry_a_utc_timestamp(tree: Path):
    append_history(tree, "freeze", digest="aaa", label="one")
    entry = json.loads(history_state_path(tree).read_text().strip())
    assert entry["ts"].endswith("+00:00")


# ============================================================
# The conftest chain (amendment 3)
# ============================================================
#
# A composition guard records member PATHS, never their bytes, so a conftest.py
# sitting beside a frozen test is listed and never hashed. That file is exactly
# where a good-faith edit silently changes what the contract measures: pytest
# filtering inside pytest_collection_modifyitems fires no deselection hook, so
# the attestation's arithmetic still balances on a shrunken set.

def _tree_with_conftests(tmp_path):
    root = tmp_path / "tree"
    (root / "tests" / "sub").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "scripts" / "run-tests.py").write_text("# stub gate\n")
    (root / "tests" / "conftest.py").write_text("# level 1\n")
    (root / "tests" / "sub" / "conftest.py").write_text("# level 2\n")
    target = root / "tests" / "sub" / "test_x.py"
    target.write_text("def test_x():\n    assert True\n")
    return root, target


def _anchor_for(tmp_path):
    path = tmp_path / "outside" / "gate.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# gate\n")
    return path


def _manifest(paths, root, anchor):
    return build_manifest(
        paths, root, label="chain", frozen_at="2026-07-25T00:00:00+00:00", anchor=anchor,
    )


def test_freezing_a_file_pulls_in_every_conftest_above_it(tmp_path):
    root, target = _tree_with_conftests(tmp_path)
    manifest = _manifest([target], root, _anchor_for(tmp_path))

    assert "tests/conftest.py" in manifest["files"]
    assert "tests/sub/conftest.py" in manifest["files"]
    assert manifest["files"]["tests/conftest.py"] != manifest["files"]["tests/sub/conftest.py"]


def test_editing_a_pulled_in_conftest_is_a_loss_of_lock(tmp_path):
    root, target = _tree_with_conftests(tmp_path)
    manifest = _manifest([target], root, _anchor_for(tmp_path))

    (root / "tests" / "conftest.py").write_text("# level 1, edited\n")
    report = verify_manifest(manifest, root)

    assert report["held"] is False
    assert "tests/conftest.py" in report["changed"]


def test_a_root_level_conftest_is_pulled_in_too(tmp_path):
    # The composition guard deliberately skips the tree root, which leaves a
    # repository-root conftest.py as the cheapest way to filter collection
    # without touching anything frozen.
    root, target = _tree_with_conftests(tmp_path)
    (root / "conftest.py").write_text("# root level\n")
    manifest = _manifest([target], root, _anchor_for(tmp_path))

    assert "conftest.py" in manifest["files"]


def test_a_tree_with_no_conftest_is_unchanged(tmp_path):
    root = tmp_path / "tree"
    (root / "tests").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "scripts" / "run-tests.py").write_text("# stub gate\n")
    target = root / "tests" / "test_x.py"
    target.write_text("def test_x():\n    assert True\n")

    manifest = _manifest([target], root, _anchor_for(tmp_path))
    assert list(manifest["files"]) == ["tests/test_x.py"]


def test_an_explicitly_frozen_conftest_appears_exactly_once(tmp_path):
    root, target = _tree_with_conftests(tmp_path)
    conftest = root / "tests" / "conftest.py"
    manifest = _manifest([target, conftest], root, _anchor_for(tmp_path))

    assert list(manifest["files"]).count("tests/conftest.py") == 1
    assert manifest["files"]["tests/conftest.py"] == file_digest(conftest)


def test_the_chain_is_pulled_in_for_a_frozen_directory_too(tmp_path):
    root, target = _tree_with_conftests(tmp_path)
    manifest = _manifest([root / "tests" / "sub"], root, _anchor_for(tmp_path))

    # The directory freeze already hashes its own conftest recursively; the
    # chain adds the one ABOVE it, which the recursive walk never reaches.
    assert "tests/sub/conftest.py" in manifest["files"]
    assert "tests/conftest.py" in manifest["files"]


def test_the_chain_never_reaches_outside_the_tree(tmp_path):
    root, target = _tree_with_conftests(tmp_path)
    outside = tmp_path / "conftest.py"
    outside.write_text("# outside the tree\n")

    manifest = _manifest([target], root, _anchor_for(tmp_path))
    assert all(not rel.startswith("..") for rel in manifest["files"])
    assert str(outside) not in manifest["files"]


def test_content_only_file_installs_no_parent_guard(tmp_path):
    from scripts.utils.canopus_freeze import build_manifest

    (tmp_path / "scripts").mkdir()
    target = tmp_path / "scripts" / "run-tests.py"
    target.write_text("print('gate')\n", encoding="utf-8")

    manifest = build_manifest(
        [], tmp_path, label="t", frozen_at="2026-07-25T00:00:00+00:00",
        content_only=[target],
    )

    assert "scripts/run-tests.py" in manifest["files"]
    assert "scripts" not in manifest["dirs"]


def test_content_only_ignores_a_new_sibling_but_catches_an_edit(tmp_path):
    from scripts.utils.canopus_freeze import build_manifest, verify_manifest

    (tmp_path / "scripts").mkdir()
    target = tmp_path / "scripts" / "run-tests.py"
    target.write_text("print('gate')\n", encoding="utf-8")
    manifest = build_manifest(
        [], tmp_path, label="t", frozen_at="2026-07-25T00:00:00+00:00",
        content_only=[target],
    )

    (tmp_path / "scripts" / "new_helper.py").write_text("x = 1\n", encoding="utf-8")
    assert verify_manifest(manifest, tmp_path)["held"] is True

    target.write_text("print('moved')\n", encoding="utf-8")
    report = verify_manifest(manifest, tmp_path)
    assert report["held"] is False
    assert report["changed"] == ["scripts/run-tests.py"]


def test_content_only_refuses_a_directory(tmp_path):
    import pytest as _pytest

    from scripts.utils.canopus_freeze import FreezeError, build_manifest

    (tmp_path / "scripts").mkdir()
    with _pytest.raises(FreezeError, match="freezes file bytes only"):
        build_manifest(
            [], tmp_path, label="t", frozen_at="2026-07-25T00:00:00+00:00",
            content_only=[tmp_path / "scripts"],
        )


def test_same_file_positional_and_content_only_keeps_the_guard(tmp_path):
    from scripts.utils.canopus_freeze import build_manifest, file_digest

    (tmp_path / "scripts").mkdir()
    target = tmp_path / "scripts" / "run-tests.py"
    target.write_text("print('gate')\n", encoding="utf-8")

    manifest = build_manifest(
        [target], tmp_path, label="t", frozen_at="2026-07-25T00:00:00+00:00",
        content_only=[target],
    )

    assert manifest["dirs"]["scripts"]["mode"] == "members"
    # files is a dict, so a duplicate-key count can never exceed 1; the property
    # worth pinning is that the second write carries the SAME digest.
    assert manifest["files"]["scripts/run-tests.py"] == file_digest(target)


def _tree_with_one_file(tmp_path):
    (tmp_path / "tests").mkdir()
    target = tmp_path / "tests" / "test_a.py"
    target.write_text("def test_a():\n    assert False\n", encoding="utf-8")
    return target


def test_baseline_enters_the_root_hash(tmp_path):
    from scripts.utils.canopus_freeze import build_manifest

    target = _tree_with_one_file(tmp_path)
    kwargs = dict(label="t", frozen_at="2026-07-25T00:00:00+00:00")

    one = build_manifest([target], tmp_path, baseline={"tests/test_a.py": 7}, **kwargs)
    two = build_manifest([target], tmp_path, baseline={"tests/test_a.py": 1}, **kwargs)

    assert one["root"] != two["root"]


def test_edited_baseline_reads_as_loss_of_lock(tmp_path):
    from scripts.utils.canopus_freeze import build_manifest, verify_manifest

    target = _tree_with_one_file(tmp_path)
    manifest = build_manifest(
        [target], tmp_path, label="t", frozen_at="2026-07-25T00:00:00+00:00",
        baseline={"tests/test_a.py": 7},
    )
    assert verify_manifest(manifest, tmp_path)["held"] is True

    manifest["baseline"]["tests/test_a.py"] = 1
    assert verify_manifest(manifest, tmp_path)["held"] is False


def test_non_integer_baseline_is_corrupt(tmp_path):
    import json

    import pytest as _pytest

    from scripts.utils.canopus_freeze import (
        FreezeCorrupt, build_manifest, freeze_state_path, read_freeze, write_freeze,
    )

    target = _tree_with_one_file(tmp_path)
    manifest = build_manifest(
        [target], tmp_path, label="t", frozen_at="2026-07-25T00:00:00+00:00",
        baseline={"tests/test_a.py": 7},
    )
    freeze_state_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    write_freeze(tmp_path, manifest)

    raw = json.loads(freeze_state_path(tmp_path).read_text(encoding="utf-8"))
    raw["baseline"]["tests/test_a.py"] = "7"
    freeze_state_path(tmp_path).write_text(json.dumps(raw), encoding="utf-8")

    with _pytest.raises(FreezeCorrupt, match="baseline"):
        read_freeze(tmp_path)


def test_a_v1_manifest_is_corrupt(tmp_path):
    import json

    import pytest as _pytest

    from scripts.utils.canopus_freeze import FreezeCorrupt, freeze_state_path, read_freeze

    state = freeze_state_path(tmp_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({
        "recipe": "canopus-freeze-v1", "label": "old", "frozen_at": "",
        "anchor": "", "git_sha": "", "root": "0" * 64, "files": {}, "dirs": {},
    }), encoding="utf-8")

    with _pytest.raises(FreezeCorrupt, match="canopus-freeze-v1"):
        read_freeze(tmp_path)


def test_read_anchor_returns_the_last_recorded_hash(anchor: Path):
    """A replaced anchor appends; the newest approval governs.

    Returning the FIRST line would pin the artifact to a superseded hash forever
    and make every legitimate re-freeze read as LOSS OF LOCK.
    """
    anchor.write_text(
        f"# gate\n\ncanopus-anchor: {'a' * 64}\n\ncanopus-anchor: {'b' * 64}\n"
    )
    assert read_anchor(anchor) == (ANCHOR_RECORDED, "b" * 64)


def test_a_conftest_appearing_above_a_frozen_directory_breaks_the_lock(tmp_path: Path):
    """The C1 hole, measured at the wire 2 intent audit and closed here.

    A directory freeze guarded only the target directory, so a conftest.py
    created in an ANCESTOR was invisible: verify held with nothing changed,
    added or removed. That conftest can insert a stub module onto sys.path, and
    because the mandated authoring rule resolves the code under test INSIDE the
    test body at RUN time, the frozen contract goes from red to green with every
    frozen byte intact. The item count is unchanged, so the baseline matches and
    the run attests. LOCK HELD and ATTESTED over a hijacked contract.

    The rule that makes a contract measurable is the same rule that makes it
    hijackable, so the composition guard has to reach every ancestor.
    """
    root = tmp_path / "tree"
    (root / "tests" / "contract" / "slice").mkdir(parents=True)
    (root / "tests" / "contract" / "slice" / "test_c.py").write_text(
        "def test_a():\n    from target import answer\n    assert answer() == 42\n"
    )
    manifest = build_manifest(
        [root / "tests" / "contract" / "slice"], root, label="l", frozen_at=STAMP
    )
    assert verify_manifest(manifest, root)["held"] is True

    (root / "tests" / "contract" / "conftest.py").write_text("import sys\n")

    report = verify_manifest(manifest, root)
    assert report["held"] is False
    assert "tests/contract/conftest.py" in report["added"]


def _frozen_slice(root: Path) -> dict:
    """A contract frozen in its own directory, the shape /pre-impl mandates."""
    (root / "tests" / "contract" / "slice").mkdir(parents=True)
    (root / "tests" / "contract" / "slice" / "test_c.py").write_text(
        "def test_a():\n    from target import answer\n    assert answer() == 42\n"
    )
    return build_manifest(
        [root / "tests" / "contract" / "slice"], root, label="l", frozen_at=STAMP
    )


def test_a_module_appearing_at_the_tree_root_breaks_the_lock(tmp_path: Path):
    """The blocker the wire 2 final pass measured, closed here.

    pyproject declares `pythonpath = ["."]`, so the tree root is the first entry
    on sys.path. A module dropped there resolves the contract's in-body import
    to a stub, and the earlier guard walked only the ancestors BELOW the root,
    so verify printed LOCK HELD over a hijacked contract.
    """
    root = tmp_path / "tree"
    manifest = _frozen_slice(root)
    assert verify_manifest(manifest, root)["held"] is True

    (root / "target.py").write_text("def answer():\n    return 42\n")

    report = verify_manifest(manifest, root)
    assert report["held"] is False
    assert "target.py" in report["added"]


def test_a_non_python_file_at_the_tree_root_does_not_break_the_lock(tmp_path: Path):
    """The root guard watches importable names, not the whole directory.

    Guarding every top-level file would fire on a note, a config, or a
    lockfile written during the slice, and a guard that fires on noise is one
    people route around.
    """
    root = tmp_path / "tree"
    manifest = _frozen_slice(root)

    (root / "NOTES.md").write_text("scratch\n")
    assert verify_manifest(manifest, root)["held"] is True


def test_an_ordinary_new_unit_test_does_not_break_the_lock(tmp_path: Path):
    """The regression the first version of this guard introduced.

    An ancestor guard over the FULL composition of tests/ made every new unit
    test read as `added`, so the builder's own next test failed the whole suite
    with LOSS OF LOCK. The guard watches conftest.py in an ancestor, because
    that is the file pytest imports without being told to.
    """
    root = tmp_path / "tree"
    manifest = _frozen_slice(root)

    (root / "tests" / "test_builder_wrote_this.py").write_text(
        "def test_b():\n    assert True\n"
    )
    assert verify_manifest(manifest, root)["held"] is True


def test_the_ancestor_guard_denies_a_conftest_but_not_an_ordinary_test(tmp_path: Path):
    """The write-deny filter tracks the verify filter.

    Leaving frozen_reason wide while narrowing verify would keep the PreToolUse
    hook refusing 200 files it no longer has a reason to refuse.
    """
    root = tmp_path / "tree"
    manifest = _frozen_slice(root)

    assert "composition" in frozen_reason("tests/contract/conftest.py", manifest)
    assert frozen_reason("tests/test_builder_wrote_this.py", manifest) is None
    assert "composition" in frozen_reason("target.py", manifest)
    assert frozen_reason("NOTES.md", manifest) is None


def test_the_ancestor_guard_does_not_freeze_the_ancestors_contents(tmp_path: Path):
    """Composition only: a sibling test elsewhere under tests/ can still be edited.

    The guard answers "did a file appear beside the contract", not "did anything
    under tests/ change". Freezing ancestor CONTENT would stop the builder
    editing its own unit tests, and the practice would be routed around.
    """
    root = tmp_path / "tree"
    (root / "tests" / "contract" / "slice").mkdir(parents=True)
    (root / "tests" / "contract" / "slice" / "test_c.py").write_text(
        "def test_a():\n    assert False\n"
    )
    (root / "tests" / "test_builder_suite.py").write_text("def test_b():\n    assert True\n")
    manifest = build_manifest(
        [root / "tests" / "contract" / "slice"], root, label="l", frozen_at=STAMP
    )

    (root / "tests" / "test_builder_suite.py").write_text("def test_b():\n    assert 1\n")
    assert verify_manifest(manifest, root)["held"] is True


def test_the_documented_enforcer_set_covers_its_import_closure():
    """C4, found at the wire 2 intent audit: the enforcers had an unfrozen tail.

    The documented freeze command named four files, but canopus_freeze imports
    atomic (which WRITES the manifest), run-tests imports venv (which re-execs
    the interpreter and so chooses which Python runs the gate), and both reach
    colors. The write path of the guarantee sat outside the guarantee.

    This recomputes the transitive first-party closure rather than pinning the
    three files that were missing on the day, so a new import cannot escape the
    documented set silently. It asserts against the SKILL text because that is
    the command an operator actually copies.
    """
    import ast

    from scripts.utils.workspace import get_workspace_root

    root = get_workspace_root()
    seen: set[str] = set()
    queue = ["scripts/utils/canopus_freeze.py", "scripts/utils/canopus_gate.py",
             "scripts/run-tests.py", "tests/conftest.py"]
    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        for node in ast.walk(ast.parse((root / rel).read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            for module in modules:
                if not module.startswith("scripts"):
                    continue
                candidate = root / (module.replace(".", "/") + ".py")
                if candidate.is_file():
                    queue.append(candidate.relative_to(root).as_posix())

    skill = (root / ".claude" / "skills" / "pre-impl" / "SKILL.md").read_text(encoding="utf-8")
    missing = sorted(rel for rel in seen if f"--content {rel}" not in skill)
    assert not missing, (
        f"the documented freeze command does not freeze {missing}; an enforcer's "
        f"import tail is outside the guarantee it enforces"
    )
