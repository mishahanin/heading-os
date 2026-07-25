"""Tests for the Canopus freeze primitive (wire 1)."""
import json
from pathlib import Path

import pytest

from scripts.utils.canopus_freeze import (
    FreezeError,
    RECIPE,
    build_manifest,
    dir_members_digest,
    file_digest,
    root_hash,
    validate_anchor_path,
    validate_freeze_path,
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


def test_root_level_file_gets_no_implicit_directory_guard(tree: Path):
    (tree / "conftest.py").write_text("# root conftest\n")
    manifest = build_manifest([tree / "conftest.py"], tree, label="l", frozen_at=STAMP)
    assert manifest["dirs"] == {}


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


from scripts.utils.canopus_freeze import (
    LOCK_HELD,
    LOCK_UNCONFIRMED,
    LOSS_OF_LOCK,
    frozen_reason,
    lock_state,
    read_anchor,
    verify_manifest,
)


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

    (tree / "tests" / "test_beta.py").unlink()
    after = verify_manifest(manifest, tree)
    assert after["held"] is False
    assert after["removed"] == ["tests/test_beta.py"]


def test_explicit_directory_freeze_catches_a_subdirectory_addition(tree: Path):
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    (tree / "tests" / "sub" / "test_delta.py").write_text("def test_d():\n    assert True\n")
    report = verify_manifest(manifest, tree)
    assert report["held"] is False
    assert report["added"] == ["tests/sub/test_delta.py"]


def test_read_anchor_reports_missing(tmp_path: Path):
    assert read_anchor(tmp_path / "absent.md") == ("missing", None)


def test_read_anchor_reports_unrecorded(anchor: Path):
    assert read_anchor(anchor) == ("unrecorded", None)


def test_read_anchor_returns_the_recorded_hash(anchor: Path):
    anchor.write_text("# gate\n\ncanopus-anchor: " + "a" * 64 + "\n\nmore prose\n")
    assert read_anchor(anchor) == ("recorded", "a" * 64)


def _report(held: bool, digest: str = "a" * 64) -> dict:
    return {"recomputed_root": digest, "changed": [], "added": [], "removed": [], "held": held}


def test_lock_state_held():
    assert lock_state(_report(True), "recorded", "a" * 64) == LOCK_HELD


def test_lock_state_loss_on_content_change():
    assert lock_state(_report(False), "recorded", "a" * 64) == LOSS_OF_LOCK


def test_lock_state_loss_on_anchor_disagreement():
    assert lock_state(_report(True), "recorded", "b" * 64) == LOSS_OF_LOCK


def test_lock_state_loss_on_missing_anchor():
    assert lock_state(_report(True), "missing", None) == LOSS_OF_LOCK


def test_lock_state_unconfirmed_without_a_recorded_hash():
    assert lock_state(_report(True), "unrecorded", None) == LOCK_UNCONFIRMED


def test_lock_state_unconfirmed_without_an_anchor():
    assert lock_state(_report(True), "none", None) == LOCK_UNCONFIRMED


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


from scripts.utils.canopus_freeze import (
    FreezeCorrupt,
    _validate_manifest_shape,
    append_history,
    clear_freeze,
    freeze_state_path,
    history_state_path,
    read_freeze,
    write_freeze,
)


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


@pytest.mark.parametrize(
    "corrupt",
    [
        _corrupt_files_not_dict,
        _corrupt_files_key_not_str,
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
    every case here at once instead of finding the next unchecked corner.

    `_corrupt_files_key_not_str` is exercised against the validator directly
    (not via a real freeze.json on disk): JSON object member names are always
    strings by spec (RFC 8259), so a non-string dict key cannot survive a
    json.dumps/json.loads round trip and this shape can never actually reach
    read_freeze() from a file. The validator still refuses it (defense in
    depth against any future non-JSON manifest source), so the case stays in
    the table rather than being silently dropped.
    """
    manifest = build_manifest([tree / "tests"], tree, label="l", frozen_at=STAMP)
    corrupt(manifest)
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
