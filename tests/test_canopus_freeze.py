"""Tests for the Canopus freeze primitive (wire 1)."""
import json
from pathlib import Path

import pytest

from scripts.utils.canopus_freeze import (
    ANCHOR_MISSING,
    ANCHOR_NONE,
    ANCHOR_RECORDED,
    ANCHOR_UNRECORDED,
    GUARD_NAMES_TREE_ROOT,
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
    dir_member_rels,
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


def test_validate_refuses_the_tree_root_itself(tree: Path):
    """The deny and the measurement have to watch the same set, or neither does.

    A root freeze recorded `dirs["."]`, the POSIX spelling of an empty relative
    path, while `_guard_ancestors` spells the same directory `""`. `frozen_reason`
    matches neither against an ordinary relative path, so the PreToolUse deny went
    silently inert over the WHOLE frozen set while `verify` kept catching the
    change -- the exact split `matches_guard` exists to prevent, arriving through
    the one path that bypasses it.

    Refused rather than taught a second spelling of the root: that removes the
    divergence instead of patching one of its two sides.
    """
    with pytest.raises(FreezeError, match="working tree root"):
        validate_freeze_path(tree, tree)


def test_the_tree_root_guard_is_denied_as_a_dot_not_as_a_slash(tree: Path):
    """The ancestor guard on the root reads `./`, the way `status` already prints it.

    The empty relative path went into the deny message raw, so an operator was
    told a new top-level file "would join the guarded composition of /" -- the
    FILESYSTEM root, which is not what is guarded and not what `cmd_status`
    calls the same manifest entry.
    """
    manifest = build_manifest([tree / "tests" / "test_alpha.py"], tree,
                              label="l", frozen_at=STAMP)

    assert "" in manifest["dirs"], "the tree root carries the *.py ancestor guard"
    reason = frozen_reason("target.py", manifest)
    assert "composition of ./" in reason
    assert "composition of /" not in reason


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


def test_a_package_directory_at_the_root_joins_the_composition(tree: Path):
    """The hole the root guard's own comment stated, and wire 2.2 measured.

    pyproject declares `pythonpath = ["."]`, so the tree root is the first
    sys.path entry the contract's own run-time imports resolve against. A
    package directory dropped there shadows an installed module while every
    frozen byte stays intact.
    """
    manifest = build_manifest(
        [tree / "tests" / "test_alpha.py"], tree, label="l", frozen_at=STAMP
    )
    (tree / "plug").mkdir()

    report = verify_manifest(manifest, tree)

    assert report["held"] is False
    assert report["added"] == ["plug/"]


def test_a_directory_that_cannot_be_imported_does_not(tree: Path):
    """`docs-2` is not an identifier, so it cannot shadow an import.

    The fix's failure mode is over-reach: a guard that reddens on every new
    top-level directory is one an operator learns to release around, which is
    worse than no guard.
    """
    manifest = build_manifest(
        [tree / "tests" / "test_alpha.py"], tree, label="l", frozen_at=STAMP
    )
    (tree / "docs-2").mkdir()

    assert verify_manifest(manifest, tree)["held"] is True


def test_the_state_directory_is_not_watched(tree: Path):
    """`.canopus` holds the manifest being compared; watching it is a loop."""
    manifest = build_manifest(
        [tree / "tests" / "test_alpha.py"], tree, label="l", frozen_at=STAMP
    )
    (tree / ".canopus").mkdir(exist_ok=True)
    (tree / ".canopus" / "scratch").mkdir()

    assert verify_manifest(manifest, tree)["held"] is True


def test_a_directory_is_listed_with_a_trailing_slash(tmp_path: Path):
    """Composition is a digest over rendered names; `plug` and `plug/` are two.

    Asserted directly rather than by comparing a directory listing against a
    file listing: under `*.py` a file named `plug` is filtered out anyway, so
    the comparison form passes without ever proving the mark is written.
    """
    (tmp_path / "plug").mkdir()

    assert dir_member_rels(
        tmp_path, tmp_path, recursive=False, names=GUARD_NAMES_TREE_ROOT
    ) == ["plug/"]


def test_the_directory_rule_keys_on_the_pattern_set_not_on_rootness(tree: Path):
    """What the code implements, named accurately after the wire 2.3 review.

    The discriminator reads the guard's PATTERNS, so a SHALLOW walk of any
    directory asked for the root pattern set lists its subdirectories too. That
    is latent rather than live only because `_guard_ancestors` hands the root
    set out when `at_root` and the ancestor set otherwise. The recursive half is
    a real guard: without it the same request over a recursive walk would pull
    every nested directory in, and dropping it fails nothing else.
    """
    # Shallow, NOT the root: the pattern set is what decides, so `sub/` is here.
    assert dir_member_rels(
        tree / "tests", tree, recursive=False, names=GUARD_NAMES_TREE_ROOT
    ) == ["tests/sub/", "tests/test_alpha.py", "tests/test_beta.py"]

    # Recursive with the same patterns: files only, no directories at any depth.
    assert dir_member_rels(
        tree, tree, recursive=True, names=GUARD_NAMES_TREE_ROOT
    ) == ["tests/sub/test_gamma.py", "tests/test_alpha.py", "tests/test_beta.py"]


def test_the_composition_still_watches_directories_after_the_deny_retreated(tree: Path):
    """DETECTION is what wire 2.3 keeps. This pins the half that did not retreat.

    The write-deny for created directories was withdrawn (see the sibling test
    below for why), and the risk of withdrawing it is that the WATCH goes with it
    on the next edit — leaving the root guard blind to `plug/` entirely, which is
    the shadowing case the guard exists for. So this asserts the measurement
    directly: a new importable root directory reddens `verify`, a hyphenated one
    does not.
    """
    manifest = build_manifest(
        [tree / "tests" / "test_alpha.py"], tree, label="l", frozen_at=STAMP
    )

    (tree / "plug").mkdir()
    (tree / "docs-2").mkdir()

    added = verify_manifest(manifest, tree)["added"]
    assert "plug/" in added
    assert "docs-2/" not in added


def test_the_deny_does_not_refuse_a_write_under_a_directory_that_is_absent(tree: Path):
    """The retreat, held by a test so it cannot be silently re-closed.

    Wire 2.3 briefly denied any Write that would CREATE a watched top-level
    directory. Measured under a held freeze, that refused an ordinary note under
    the workspace's private `threads/` tree: an identifier-shaped top-level name
    that is data-routed and absent from a fresh engine clone. The dispatcher runs
    `check_canopus_freeze` BEFORE `check_protect_personal_threads`, so the deny
    took writes the workspace's own design routes to that later check, for the
    whole duration of every frozen slice.

    A guard that reddens on ordinary work is one an operator learns to release
    around, so prevention retreated and detection stayed. Each name below is a
    real gitignored, data-routed root directory absent from a fresh engine clone.
    """
    manifest = build_manifest(
        [tree / "tests" / "test_alpha.py"], tree, label="l", frozen_at=STAMP
    )

    for absent in ("threads", "crm", "knowledge", "context", "plans", "outputs"):
        assert not (tree / absent).exists(), f"fixture drift: {absent}/ exists"
        assert frozen_reason(f"{absent}/note.md", manifest) is None, absent

    # The verify half is untouched, so the same names still redden the guard.
    (tree / "threads").mkdir()
    assert "threads/" in verify_manifest(manifest, tree)["added"]


def test_the_deny_leaves_writes_under_an_existing_root_directory_alone(tree: Path):
    """`tests/` is already in the composition, so nothing joins by writing under it."""
    manifest = build_manifest(
        [tree / "tests" / "test_alpha.py"], tree, label="l", frozen_at=STAMP
    )

    assert frozen_reason("tests/sub/test_delta.py", manifest) is None
    assert frozen_reason("docs-2/note.md", manifest) is None
    assert frozen_reason("__pycache__/thing.pyc", manifest) is None


def test_a_generated_cache_directory_at_the_root_does_not_redden(tree: Path):
    """`__pycache__`.isidentifier() is True, so isidentifier alone is not the rule.

    CACHE_DIRNAMES exists because a lock bound to an artifact the build
    regenerates reports LOSS OF LOCK for a change nobody made. Admitting
    directories to the composition without carrying that exclusion across
    re-opens it on the one directory Python creates most often.
    """
    manifest = build_manifest(
        [tree / "tests" / "test_alpha.py"], tree, label="l", frozen_at=STAMP
    )
    (tree / "__pycache__").mkdir(exist_ok=True)

    assert verify_manifest(manifest, tree)["held"] is True


def test_the_recomputed_root_agrees_with_the_built_one(tree: Path):
    """Wire 2.2's blocker B1, guarded rather than remembered.

    `_guard_ancestors` builds with the TUPLE GUARD_NAMES_TREE_ROOT; `recompute`
    reads the LIST the manifest round-tripped through JSON. A discriminator
    written with `is`, or with `==` against the tuple, is true on one path and
    false on the other, directories enter the stored digest and never the
    recomputed one, and the tree reports LOSS OF LOCK forever with nothing
    moved.
    """
    (tree / "plug").mkdir()
    manifest = build_manifest(
        [tree / "tests" / "test_alpha.py"], tree, label="l", frozen_at=STAMP
    )

    report = verify_manifest(manifest, tree)

    assert report["held"] is True
    assert report["added"] == []


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


def test_lock_state_compares_the_whole_digest_not_a_shared_prefix():
    """No prefix comparison, pinned on the function that decides LOCK HELD.

    The other lock_state tests pair 'a'*64 against 'b'*64, which differ at
    character 0 and so cannot tell a full comparison from a prefix one. These two
    share their first twelve characters, so an implementation comparing any
    prefix reports LOCK HELD over a tree that has moved. Measured: mutating the
    comparison to a 12-character startswith passed the whole suite.

    The sibling axis, approval_state, was pinned this way already. This is the
    function an operator actually reads for green.
    """
    approved = "a" * 64
    recomputed = "a" * 12 + "b" * 52

    assert lock_state(_report(True, recomputed), ANCHOR_RECORDED, approved) == LOSS_OF_LOCK


def test_lock_state_refuses_a_truncated_anchor_hash():
    """A strict prefix of the recomputed digest is not the recomputed digest.

    A builder with a shell can brute-force a short prefix by appending whitespace
    to a frozen file, so a truncated digest that looks rigorous is worse than a
    full one.
    """
    assert lock_state(_report(True, "a" * 64), ANCHOR_RECORDED, "a" * 12) == LOSS_OF_LOCK


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
    """RESTORED after the wire 2.3 retreat, to the input it was written with.

    The created-directory deny had briefly made `scripts/canopus.py` a DENIAL
    here, since no `scripts/` exists in this fixture. That deny is withdrawn, so
    the original expectation is correct again and is put back rather than left
    pointed at a rule that no longer exists.
    """
    manifest = build_manifest([tree / "tests" / "test_alpha.py"], tree, label="l", frozen_at=STAMP)
    assert frozen_reason("scripts/canopus.py", manifest) is None
    assert frozen_reason("tests/sub/notes.py", manifest) is None


def test_frozen_reason_does_not_leak_across_a_similar_prefix(tree: Path):
    """RESTORED after the wire 2.3 retreat: `tests_extra/` is not inside `tests/`.

    The created-directory deny had made this path denied for an unrelated reason,
    so the assertion was weakened to "whatever it says, not `frozen directory`".
    With that deny withdrawn the original, stronger claim holds again and is
    restored: nothing at all is returned.
    """
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

    # In the ENFORCER map, not in `files`: the split is what keeps an enforcer
    # edit off the contract root. The guard on `scripts/` is still absent, which
    # is what this test was written for.
    assert "scripts/run-tests.py" in manifest["content"]
    assert "scripts/run-tests.py" not in manifest["files"]
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
    # Reported on its OWN axis. The edit is still caught -- that is what this
    # test has always been for -- and it is named as the enforcer moving rather
    # than as the contract moving, because the two have different cures.
    assert report["enforcer_moved"] == ["scripts/run-tests.py"]
    assert report["changed"] == []


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
    kwargs = {"label": "t", "frozen_at": "2026-07-25T00:00:00+00:00"}

    one = build_manifest([target], tmp_path, baseline={"tests/test_a.py": 7}, **kwargs)
    two = build_manifest([target], tmp_path, baseline={"tests/test_a.py": 1}, **kwargs)

    assert one["root"] != two["root"]


def test_the_plugin_baseline_enters_the_root_hash(tmp_path):
    """Captured, in the hash, and carried through recompute unchanged.

    In the hash so the operator's commit protects it. Carried rather than
    derived because recompute cannot re-run pytest, and a hash field the
    recompute path cannot reproduce is a permanent LOSS OF LOCK on an untouched
    tree. That is wire 2.2's blocker B1 verbatim.
    """
    from scripts.utils.canopus_freeze import build_manifest, verify_manifest

    target = _tree_with_one_file(tmp_path)
    kwargs = {"label": "t", "frozen_at": "2026-07-25T00:00:00+00:00"}

    one = build_manifest([target], tmp_path, plugins={"xdist": "/a"}, **kwargs)
    two = build_manifest([target], tmp_path, plugins={"xdist": "/a", "evil": "/b"}, **kwargs)

    assert one["root"] != two["root"]
    assert verify_manifest(one, tmp_path)["held"] is True


def test_the_plugin_baseline_is_recorded_by_name_never_by_origin(tmp_path):
    """An origin is an absolute path inside `.venv`, and this repository is public.

    It differs per machine and per clone, so carrying it would redden every
    fresh checkout and would put an operator's home directory inside a hash the
    engine repository commits against. The names ARE the identities derived in
    `process_facts`, which already carry their provenance (`dist:`, `intree:`).
    """
    from scripts.utils.canopus_freeze import build_manifest

    target = _tree_with_one_file(tmp_path)
    kwargs = {"label": "t", "frozen_at": "2026-07-25T00:00:00+00:00"}

    named = build_manifest([target], tmp_path, plugins=["dist:xdist"], **kwargs)
    with_origin = build_manifest(
        [target], tmp_path, plugins={"dist:xdist": "/home/somebody/.venv/x.py"}, **kwargs)

    assert named["plugins"] == ["dist:xdist"]
    assert named["root"] == with_origin["root"]


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
    from scripts.utils.production_shape import first_party_closure
    from scripts.utils.workspace import get_workspace_root

    # The walk this test used to carry inline now lives in production_shape,
    # which is where the production-shape gate needed the same computation. The
    # extraction was declared in that slice's gate artifact and left undone, so
    # the helper shipped with two contract tests and no consumer; completing it
    # here is what makes those criteria pin live code. The behaviour is
    # unchanged: both readings of `from X import y` are followed, because
    # `from scripts.utils import venv as _venv` yields the PACKAGE
    # `scripts.utils`, which is not a file, and following only `node.module`
    # dropped venv.py from the set while this test still passed.
    root = get_workspace_root()
    seen = {
        rel for rel in first_party_closure(
            ["scripts/utils/canopus_freeze.py", "scripts/utils/canopus_gate.py",
             "scripts/run-tests.py", "tests/conftest.py"],
            root,
        )
        if (root / rel).is_file()
    }

    skill = (root / ".claude" / "skills" / "canopus" / "SKILL.md").read_text(encoding="utf-8")
    missing = sorted(rel for rel in seen if f"--content {rel}" not in skill)
    assert not missing, (
        f"the documented freeze command does not freeze {missing}; an enforcer's "
        f"import tail is outside the guarantee it enforces"
    )


def test_the_canopus_skill_writes_real_contract_files_and_freezes_them():
    """The gap wire 2 closed, ported here when the wire 2 contract was retired.

    Before wire 2 the /pre-impl skill DESCRIBED the contract in prose and
    labelled the description a draft, so what the operator approved at the gate
    was an account of tests rather than the tests themselves. A description
    cannot be frozen, and an approval over one is an approval of nothing.

    The skill therefore has to name the directory the tests are WRITTEN to and
    the command that freezes them. The dated per-slice directory is asserted
    rather than the bare prefix: `tests/contract/` alone survives a skill that
    merely mentions the path in passing, which is the state this test exists to
    refuse.

    The skill was `/pre-impl` until 2026-08-02 and is now `/canopus`; the gate it
    carries moved to `references/planning-gate.md` and the property is unchanged.
    """
    from scripts.utils.workspace import get_workspace_root

    skill = get_workspace_root() / ".claude" / "skills" / "canopus" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")

    assert "tests/contract/{YYYY-MM-DD}-{slug}/" in text
    assert "scripts/canopus.py freeze" in text
    # The label the prose draft carried. Its return would mean the gate is being
    # asked to approve an unapproved description again.
    assert "CEO-UNAPPROVED DRAFT" not in text


def test_approval_is_verified_only_by_a_matching_committed_hash():
    from scripts.utils.canopus_freeze import APPROVED, approval_state

    assert approval_state("a" * 64, "committed", "a" * 64) == (APPROVED, "")


def test_a_committed_hash_that_disagrees_is_not_an_approval():
    """The two hashes differ in their LAST character, deliberately.

    Two digests differing at character 0 leave a prefix comparison green, so a
    test built that way pins nothing on the one axis where a prefix collision
    would produce a false APPROVED.
    """
    from scripts.utils.canopus_freeze import APPROVAL_UNVERIFIED, approval_state

    disagreeing = "a" * 63 + "b"
    axis, reason = approval_state("a" * 64, "committed", disagreeing)
    assert axis == APPROVAL_UNVERIFIED
    assert disagreeing in reason


def test_an_unrecognised_approval_status_is_answered_rather_than_raised():
    """This runs inside the test gate, and a raise there fails OPEN."""
    from scripts.utils.canopus_freeze import APPROVAL_UNVERIFIED, approval_state

    axis, reason = approval_state("a" * 64, "a status no module defines", None)
    assert axis == APPROVAL_UNVERIFIED
    assert "a status no module defines" in reason


def test_an_empty_hash_on_both_sides_is_not_an_approval():
    """Otherwise the axis reads APPROVED over nothing at all."""
    from scripts.utils.canopus_freeze import APPROVAL_UNVERIFIED, approval_state

    assert approval_state("", "committed", "")[0] == APPROVAL_UNVERIFIED
    assert approval_state("", "committed", None)[0] == APPROVAL_UNVERIFIED


def test_each_unverifiable_approval_status_keeps_its_own_reason():
    """A generic "could not check" hides which of three worlds you are in."""
    from scripts.utils.canopus_freeze import APPROVAL_UNVERIFIED, approval_state

    seen = {}
    for status in ("uncommitted", "no_repo", "no_git"):
        axis, reason = approval_state("a" * 64, status, None)
        assert axis == APPROVAL_UNVERIFIED
        seen[status] = reason
    assert len(set(seen.values())) == 3


def test_the_manifest_records_the_anchors_repository(tmp_path):
    from scripts.utils.canopus_freeze import build_manifest

    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    anchor = tmp_path / "outside" / "gate.md"
    anchor.parent.mkdir(parents=True)
    anchor.write_text("# gate\n")

    manifest = build_manifest(
        [tree / "tests"], tree, label="demo",
        frozen_at="2026-01-01T00:00:00+00:00", anchor=anchor,
        anchor_repo={"in_repo": True, "identity": "c" * 64},
    )

    assert manifest["anchor_repo"] == {"in_repo": True, "identity": "c" * 64}


def test_the_binding_is_covered_by_the_root_hash(tmp_path):
    """Otherwise a builder edits the field and the approved hash still matches.

    The two identities below share a 63-character prefix, so a comparison that
    truncated would pass this test while proving nothing.
    """
    from scripts.utils.canopus_freeze import build_manifest

    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    anchor = tmp_path / "outside" / "gate.md"
    anchor.parent.mkdir(parents=True)
    anchor.write_text("# gate\n")

    def build(identity):
        return build_manifest(
            [tree / "tests"], tree, label="demo",
            frozen_at="2026-01-01T00:00:00+00:00", anchor=anchor,
            anchor_repo={"in_repo": True, "identity": identity},
        )["root"]

    assert build("d" * 63 + "0") != build("d" * 63 + "1")
    assert build("d" * 64) != build_manifest(
        [tree / "tests"], tree, label="demo",
        frozen_at="2026-01-01T00:00:00+00:00", anchor=anchor,
    )["root"]


def test_a_bound_freeze_still_verifies_as_held(tmp_path):
    """The RECOMPUTED root must cover the binding the STORED root covered.

    recompute() rebuilds the hashed payload from disk, and the binding is not on
    disk: it is a recorded fact, like the baseline, which recompute already
    carries through verbatim for exactly this reason. Leave anchor_repo out of
    recompute's result and root_hash falls back to ANCHOR_REPO_UNBOUND while the
    stored root hashed the real binding. The two can then never match, so every
    bound freeze reports LOSS OF LOCK forever with nothing having moved.

    This is the only test that can see that defect. Three of the four
    behavioural cases in Task 5 assert a RED and would pass on the wrong cause,
    and the fourth is the unbound plain-folder case, where the fallback value
    happens to be correct.
    """
    from scripts.utils.canopus_freeze import build_manifest, verify_manifest

    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_a.py").write_text("def test_a():\n    assert True\n")
    anchor = tmp_path / "outside" / "gate.md"
    anchor.parent.mkdir(parents=True)
    anchor.write_text("# gate\n")

    manifest = build_manifest(
        [tree / "tests"], tree, label="demo",
        frozen_at="2026-01-01T00:00:00+00:00", anchor=anchor,
        anchor_repo={"in_repo": True, "identity": "c" * 64},
    )

    assert verify_manifest(manifest, tree)["held"]


def test_a_manifest_from_the_previous_recipe_is_refused(tmp_path):
    from scripts.utils.canopus_freeze import FreezeCorrupt, freeze_state_path, read_freeze

    tree = tmp_path / "tree"
    tree.mkdir()
    path = freeze_state_path(tree)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "recipe": "canopus-freeze-v3", "label": "old",
        "frozen_at": "2026-01-01T00:00:00+00:00", "anchor": "", "git_sha": "",
        "root": "e" * 64, "files": {}, "dirs": {}, "baseline": {},
    }))

    with pytest.raises(FreezeCorrupt, match="canopus-freeze-v3"):
        read_freeze(tree)


def test_a_manifest_missing_the_binding_is_refused(tmp_path):
    """A current recipe string with no binding field is a hand-edited manifest."""
    from scripts.utils.canopus_freeze import (
        RECIPE, FreezeCorrupt, freeze_state_path, read_freeze,
    )

    tree = tmp_path / "tree"
    tree.mkdir()
    path = freeze_state_path(tree)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "recipe": RECIPE, "label": "x",
        "frozen_at": "2026-01-01T00:00:00+00:00", "anchor": "", "git_sha": "",
        "root": "e" * 64, "files": {}, "content": {}, "dirs": {}, "baseline": {},
        "plugins": [],
    }))

    with pytest.raises(FreezeCorrupt, match="anchor_repo"):
        read_freeze(tree)


def test_a_binding_with_a_wrong_typed_field_is_refused(tmp_path):
    from scripts.utils.canopus_freeze import (
        RECIPE, FreezeCorrupt, freeze_state_path, read_freeze,
    )

    tree = tmp_path / "tree"
    tree.mkdir()
    path = freeze_state_path(tree)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "recipe": RECIPE, "label": "x",
        "frozen_at": "2026-01-01T00:00:00+00:00", "anchor": "", "git_sha": "",
        "root": "e" * 64, "files": {}, "content": {}, "dirs": {}, "baseline": {},
        "plugins": [], "anchor_repo": {"in_repo": "yes", "identity": ""},
    }))

    with pytest.raises(FreezeCorrupt, match="in_repo"):
        read_freeze(tree)


def _bound(identity):
    return {"anchor_repo": {"in_repo": True, "identity": identity}}


def test_a_matching_repository_leaves_the_binding_intact():
    from scripts.utils.canopus_freeze import (
        BINDING_INTACT, REPO_PRESENT, repo_binding_state,
    )

    verdict, reason = repo_binding_state(_bound("f" * 64), REPO_PRESENT, "f" * 64)

    assert verdict == BINDING_INTACT
    assert reason == ""


def test_a_different_repository_breaks_the_binding():
    """The identities differ at the LAST character on purpose.

    A prefix comparison would call these equal, and a prefix comparison is what
    hands a builder with a shell a short value to brute-force.
    """
    from scripts.utils.canopus_freeze import (
        BINDING_BROKEN, REPO_PRESENT, repo_binding_state,
    )

    verdict, reason = repo_binding_state(
        _bound("f" * 63 + "0"), REPO_PRESENT, "f" * 63 + "1")

    assert verdict == BINDING_BROKEN
    assert "different repository" in reason


def test_an_invisible_repository_breaks_the_binding():
    """The Critical. Blinding must cost the builder a red, not buy a green."""
    from scripts.utils.canopus_freeze import (
        BINDING_BROKEN, REPO_ABSENT, REPO_UNKNOWN, repo_binding_state,
    )

    for status in (REPO_ABSENT, REPO_UNKNOWN):
        verdict, reason = repo_binding_state(_bound("f" * 64), status, "")
        assert verdict == BINDING_BROKEN
        assert status in reason


def test_an_unbound_freeze_in_a_plain_folder_stays_intact():
    """The supported case: an operator whose gate artifact is a file in a folder."""
    from scripts.utils.canopus_freeze import (
        BINDING_INTACT, REPO_ABSENT, repo_binding_state,
    )

    manifest = {"anchor_repo": {"in_repo": False, "identity": ""}}

    assert repo_binding_state(manifest, REPO_ABSENT, "")[0] == BINDING_INTACT


def test_an_unbound_freeze_whose_anchor_is_now_in_a_repository_breaks():
    """Closes blinding at FREEZE time.

    Without this row a builder freezes under a poisoned environment, records
    in_repo false, and wins the working-copy fallback for the rest of the slice.
    """
    from scripts.utils.canopus_freeze import (
        BINDING_BROKEN, REPO_PRESENT, repo_binding_state,
    )

    manifest = {"anchor_repo": {"in_repo": False, "identity": ""}}
    verdict, reason = repo_binding_state(manifest, REPO_PRESENT, "f" * 64)

    assert verdict == BINDING_BROKEN
    assert "release and re-freeze" in reason


def test_a_manifest_with_no_binding_key_is_treated_as_unbound():
    """repo_binding_state is called from the gate, which must never raise."""
    from scripts.utils.canopus_freeze import (
        BINDING_INTACT, REPO_ABSENT, repo_binding_state,
    )

    assert repo_binding_state({}, REPO_ABSENT, "")[0] == BINDING_INTACT


@pytest.mark.parametrize("binding", ["sneaky", ["sneaky"], 7])
def test_a_non_dict_binding_is_treated_as_unbound(binding):
    """It must ANSWER, never raise, whatever shape the key carries.

    A manifest reaching this function without passing through read_freeze is
    exactly the case the guard exists for, so "_validate_manifest_shape already
    refuses that" is a guarantee about a DIFFERENT function. The raise matters
    because the PreToolUse dispatcher catches only FreezeCorrupt and OSError: an
    AttributeError falls to its catch-all, logs an advisory and CONTINUES, so
    writes to frozen paths sail through while the hook reports healthy.
    """
    from scripts.utils.canopus_freeze import (
        BINDING_BROKEN, BINDING_INTACT, REPO_ABSENT, REPO_PRESENT, repo_binding_state,
    )

    manifest = {"anchor_repo": binding}

    assert repo_binding_state(manifest, REPO_ABSENT, "")[0] == BINDING_INTACT
    # Read as unbound rather than as bound to nothing: an anchor now inside a
    # repository is the freeze-taken-blind case, and it stays red.
    assert repo_binding_state(manifest, REPO_PRESENT, "f" * 64)[0] == BINDING_BROKEN


def _skeleton(binding) -> dict:
    """The smallest manifest root_hash and recompute both read."""
    return {
        "recipe": RECIPE, "label": "x", "frozen_at": STAMP, "anchor": "",
        "git_sha": "", "root": "", "files": {}, "dirs": {}, "baseline": {},
        "plugins": [], "anchor_repo": binding,
    }


@pytest.mark.parametrize("binding", ["sneaky", ["sneaky"], 7])
def test_root_hash_answers_on_a_malformed_binding(binding):
    """The eighth appearance of this project's signature defect, closed.

    `repo_binding_state` got an isinstance guard in wire 2.2 and its two siblings
    did not: both spelled `dict(manifest.get("anchor_repo") or ...)`, and `dict`
    raises on all three of these — ValueError for a string and a list, TypeError
    for an int. `root_hash` is reached from `verify_manifest`, which the gate and
    the PreToolUse dispatcher both call, where a raise fails OPEN.

    The assertion is equality with the unbound default, not merely "did not
    raise": answering with some OTHER payload would give a malformed binding its
    own root hash, which is a second defect wearing the first one's clothes.
    """
    from scripts.utils.canopus_freeze import ANCHOR_REPO_UNBOUND

    assert root_hash(_skeleton(binding)) == root_hash(
        _skeleton(dict(ANCHOR_REPO_UNBOUND)))


@pytest.mark.parametrize("binding", ["sneaky", ["sneaky"], 7])
def test_recompute_answers_on_a_malformed_binding(binding, tmp_path):
    from scripts.utils.canopus_freeze import ANCHOR_REPO_UNBOUND, recompute

    rebuilt = recompute(_skeleton(binding), tmp_path)

    assert rebuilt["anchor_repo"] == dict(ANCHOR_REPO_UNBOUND)


@pytest.mark.parametrize("binding", ["sneaky", ["sneaky"], 7, None, {}])
def test_the_shared_accessor_answers_for_every_reader(binding):
    """One accessor, so the next reader inherits the guard instead of the defect.

    The empty dict is in the set deliberately: all three call sites spelled
    `... or ANCHOR_REPO_UNBOUND`, so `{}` already read as unbound, and dropping
    that arm would change the payload root_hash covers for such a manifest —
    LOSS OF LOCK over a tree where nothing moved.
    """
    from scripts.utils.canopus_freeze import ANCHOR_REPO_UNBOUND, anchor_binding

    assert anchor_binding({"anchor_repo": binding}) == dict(ANCHOR_REPO_UNBOUND)
    assert anchor_binding({}) == dict(ANCHOR_REPO_UNBOUND)
    # A copy, never the shared proxy: callers store this into manifests they
    # serialize, and one in-place edit would change the fallback process-wide.
    assert anchor_binding({}) is not ANCHOR_REPO_UNBOUND


def test_the_unbound_fallback_cannot_be_mutated_in_place():
    """One in-place edit would change the fallback for the whole process.

    Every stored unbound root would then stop matching, and verify would report
    LOSS OF LOCK over a tree where nothing moved.
    """
    from scripts.utils.canopus_freeze import ANCHOR_REPO_UNBOUND

    with pytest.raises(TypeError):
        ANCHOR_REPO_UNBOUND["in_repo"] = True

    assert ANCHOR_REPO_UNBOUND["in_repo"] is False


def test_a_manifest_taking_the_unbound_fallback_serializes_as_a_plain_dict(tree, anchor):
    """The read-only fallback must never reach the JSON on disk."""
    manifest = build_manifest(
        [tree / "tests"], tree, label="unbound", frozen_at=STAMP, anchor=anchor
    )

    assert type(manifest["anchor_repo"]) is dict
    assert json.loads(json.dumps(manifest))["anchor_repo"] == {
        "in_repo": False, "identity": ""
    }


def test_an_unbound_anchor_status_reddens_the_lock():
    from scripts.utils.canopus_freeze import ANCHOR_UNBOUND, LOSS_OF_LOCK, lock_state

    report = {"held": True, "recomputed_root": "f" * 64}

    assert lock_state(report, ANCHOR_UNBOUND, None) == LOSS_OF_LOCK


def test_an_open_window_is_the_last_release_without_a_freeze_after_it():
    from scripts.utils.canopus_freeze import open_release_window

    entries = [
        {"event": "freeze", "ts": "2026-01-01T00:00:00+00:00"},
        {"event": "release", "ts": "2026-01-01T01:00:00+00:00", "kind": "window",
         "reason": "recipe change"},
    ]

    window = open_release_window(entries)

    assert window is not None
    assert window["reason"] == "recipe change"


def test_a_later_freeze_closes_the_window():
    from scripts.utils.canopus_freeze import open_release_window

    entries = [
        {"event": "release", "ts": "2026-01-01T01:00:00+00:00", "kind": "window"},
        {"event": "freeze", "ts": "2026-01-01T02:00:00+00:00"},
    ]

    assert open_release_window(entries) is None


def test_a_ship_release_opens_no_window():
    from scripts.utils.canopus_freeze import open_release_window

    entries = [
        {"event": "freeze", "ts": "2026-01-01T00:00:00+00:00"},
        {"event": "release", "ts": "2026-01-01T01:00:00+00:00", "kind": "ship"},
    ]

    assert open_release_window(entries) is None


def test_a_legacy_release_with_no_kind_opens_no_window():
    """Every ledger entry written before this slice carries no kind.

    Reading them as windows would turn a quiet past amber retroactively on every
    workspace in the fleet, on the first pytest run after the update.
    """
    from scripts.utils.canopus_freeze import open_release_window

    entries = [{"event": "release", "ts": "2026-01-01T01:00:00+00:00"}]

    assert open_release_window(entries) is None


def test_a_forced_release_can_open_a_window():
    from scripts.utils.canopus_freeze import open_release_window

    entries = [{"event": "force_release", "ts": "2026-01-01T01:00:00+00:00",
                "kind": "window", "reason": "manifest damaged"}]

    assert open_release_window(entries) is not None


def test_an_unreleased_freeze_is_the_last_lock_event_being_a_freeze():
    """The reading that made `rm freeze.json` quieter than releasing it.

    `open_release_window` answers None here, correctly, and that answer used to
    be the ONLY one anything asked for. The same walk carries the other half.
    """
    from scripts.utils.canopus_freeze import open_release_window, unreleased_freeze

    entries = [
        {"event": "approve", "ts": "2026-01-01T00:00:00+00:00"},
        {"event": "freeze", "ts": "2026-01-01T01:00:00+00:00", "label": "demo"},
    ]

    assert open_release_window(entries) is None
    assert unreleased_freeze(entries)["label"] == "demo"


def test_a_release_closes_the_unreleased_freeze():
    from scripts.utils.canopus_freeze import unreleased_freeze

    entries = [
        {"event": "freeze", "ts": "2026-01-01T01:00:00+00:00"},
        {"event": "release", "ts": "2026-01-01T02:00:00+00:00", "kind": "ship"},
    ]

    assert unreleased_freeze(entries) is None


def test_a_verify_fail_does_not_hide_the_freeze_that_holds_the_lock():
    """Only freeze and release change who holds the lock; the rest describe it.

    `verify_fail` is written by the command an operator runs while a freeze is
    HELD, so a reader that stopped at the newest entry of any kind would answer
    "no freeze here" on the most ordinary state this ledger records.
    """
    from scripts.utils.canopus_freeze import unreleased_freeze

    entries = [
        {"event": "freeze", "ts": "2026-01-01T01:00:00+00:00", "label": "demo"},
        {"event": "verify_fail", "ts": "2026-01-01T02:00:00+00:00"},
    ]

    assert unreleased_freeze(entries)["label"] == "demo"


def test_an_empty_ledger_has_no_unreleased_freeze():
    from scripts.utils.canopus_freeze import unreleased_freeze

    assert unreleased_freeze([]) is None


def test_tree_drift_is_silent_between_two_identical_states():
    from scripts.utils.canopus_freeze import tree_drift

    state = {"recipe": "canopus-tree-v1", "head": "a" * 40,
             "dirty": {"x.py": "b" * 64}}
    assert tree_drift(state, dict(state)) == []


def test_tree_drift_names_the_path_that_moved():
    from scripts.utils.canopus_freeze import tree_drift

    before = {"recipe": "canopus-tree-v1", "head": "a" * 40,
              "dirty": {"x.py": "b" * 64}}
    after = {"recipe": "canopus-tree-v1", "head": "a" * 40,
             "dirty": {"x.py": "c" * 64}}
    reasons = tree_drift(before, after)
    assert len(reasons) == 1
    assert "x.py" in reasons[0]


def test_tree_drift_sees_an_appearance_a_disappearance_and_a_moved_head():
    """Three different truths, three different sentences. An operator who reads
    one string for all of them cannot tell a new file from a deleted one from a
    commit."""
    from scripts.utils.canopus_freeze import tree_drift

    before = {"recipe": "canopus-tree-v1", "head": "a" * 40,
              "dirty": {"gone.py": "b" * 64}}
    after = {"recipe": "canopus-tree-v1", "head": "d" * 40,
             "dirty": {"new.py": "c" * 64}}
    joined = " | ".join(tree_drift(before, after))
    assert "gone.py" in joined
    assert "new.py" in joined
    assert "HEAD" in joined


def test_tree_drift_refuses_an_absent_or_damaged_state():
    """Not proved is not proved innocent, the rule wire 3.1 settled."""
    from scripts.utils.canopus_freeze import tree_drift

    good = {"recipe": "canopus-tree-v1", "head": "a" * 40, "dirty": {}}
    for damaged in (None, {}, "text", 7, {"head": "a" * 40}):
        assert tree_drift(damaged, good) != []
        assert tree_drift(good, damaged) != []


def test_tree_drift_never_raises_on_a_hostile_state():
    from scripts.utils.canopus_freeze import tree_drift

    good = {"recipe": "canopus-tree-v1", "head": "a" * 40, "dirty": {}}
    for hostile in ({"recipe": "canopus-tree-v1", "head": 7, "dirty": {}},
                    {"recipe": "canopus-tree-v1", "head": "a" * 40, "dirty": 7},
                    {"recipe": "other", "head": "a" * 40, "dirty": {}},
                    # Non-string keys inside an otherwise well-shaped `dirty`
                    # map: `_usable_tree_state` checks the map IS a dict, never
                    # that its keys are strings, so these reach the `sorted()`
                    # walk. `sorted()` on a raw key set raises TypeError the
                    # moment two keys disagree on type -- str vs int, or
                    # anything vs None -- which is exactly the shape a
                    # hand-edited or hostile record can carry even though
                    # `tree_state` itself only ever writes string keys. Each
                    # `dirty` map below mixes a string key with the hostile one
                    # SO THE TYPE MISMATCH IS WITHIN ONE SIDE: `good`'s dirty is
                    # `{}`, so a hostile side holding only one key type would
                    # never actually cross-compare against a string and this
                    # loop would pass even with the bug present.
                    {"recipe": "canopus-tree-v1", "head": "a" * 40,
                     "dirty": {1: "h" * 64, "x.py": "h" * 64}},
                    {"recipe": "canopus-tree-v1", "head": "a" * 40,
                     "dirty": {None: "h" * 64, "x.py": "h" * 64}},
                    {"recipe": "canopus-tree-v1", "head": "a" * 40,
                     "dirty": {(1, 2): "h" * 64, "x.py": "h" * 64}}):
        assert isinstance(tree_drift(hostile, good), list)


def test_tree_drift_compares_across_a_type_mismatched_key_pair():
    """The reproduction the reviewer ran: one side's `dirty` map keyed by an
    int, the other's by a string. `sorted(set(was) | set(now))` raises
    TypeError comparing `str` against `int`, which violates this function's
    "Never raises" contract and the constraint that every reporting surface
    calls it through `attestation_state`.
    """
    from scripts.utils.canopus_freeze import tree_drift

    was = {"recipe": "canopus-tree-v1", "head": "a" * 40, "dirty": {1: "h"}}
    now = {"recipe": "canopus-tree-v1", "head": "a" * 40, "dirty": {"x.py": "h"}}

    result = tree_drift(was, now)

    assert isinstance(result, list)
    assert result != []


def test_tree_drift_refuses_a_state_with_the_wrong_tree_recipe():
    """Pins `_usable_tree_state`'s `candidate.get("recipe") == TREE_RECIPE`
    line specifically. The existing damaged-shape test
    (`test_tree_drift_refuses_an_absent_or_damaged_state`) never supplies a
    dict that is otherwise well-formed -- correct `head` and `dirty` types --
    but carries the wrong recipe, so mutating that one comparison to
    `and True` leaves every existing test green: a well-shaped state with the
    wrong recipe would then read as usable and be compared for real, which
    is exactly what this test catches.
    """
    from scripts.utils.canopus_freeze import tree_drift

    good = {"recipe": "canopus-tree-v1", "head": "a" * 40, "dirty": {}}
    wrong_recipe = {"recipe": "some-other-recipe", "head": "a" * 40, "dirty": {}}
    assert tree_drift(wrong_recipe, good) != []
    assert tree_drift(good, wrong_recipe) != []


def test_tree_drift_distinguishes_appeared_from_no_longer_reported():
    """Four kinds get four sentences, this function's own docstring says, and
    'appeared' is not interchangeable with 'no longer reported': one names a
    path that is NEW, the other one that VANISHED. Swapping the two sentence
    templates in `tree_drift` leaves both path names present SOMEWHERE in the
    output, which is exactly why
    `test_tree_drift_sees_an_appearance_a_disappearance_and_a_moved_head`
    (which only checks substring membership, not which sentence carries which
    path) does not catch the swap. This test checks the pairing instead.
    """
    from scripts.utils.canopus_freeze import tree_drift

    before = {"recipe": "canopus-tree-v1", "head": "a" * 40,
              "dirty": {"gone.py": "b" * 64}}
    after = {"recipe": "canopus-tree-v1", "head": "a" * 40,
             "dirty": {"new.py": "c" * 64}}
    reasons = tree_drift(before, after)

    vanished = [r for r in reasons if "gone.py" in r]
    appeared = [r for r in reasons if "new.py" in r]
    assert vanished and "no longer reported" in vanished[0]
    assert appeared and "appeared" in appeared[0]
    assert "no longer reported" not in appeared[0]
    assert "appeared" not in vanished[0]


def test_attestation_state_reasons_are_not_duplicated_between_modules():
    """`REASON_DIFFERENT_RECIPE` and `REASON_DIFFERENT_ROOT` are the ONE
    spelling of these two strings; `canopus.py:_print_attestation` imports and
    compares against these same names rather than carrying its own literal
    copies. Pinned here so a reader who greps for either string finds both
    call sites agree by construction, not by two authors independently typing
    the same sentence.
    """
    from scripts.utils.canopus_freeze import (
        ATTEST_RECIPE,
        REASON_DIFFERENT_RECIPE,
        REASON_DIFFERENT_ROOT,
        attestation_state,
    )

    stale_recipe = {"recipe": "canopus-attest-v2", "root": "a" * 64,
                    "attested": True, "frozen_tests": {}, "reasons": [],
                    "tree": {"recipe": "canopus-tree-v1", "head": "a" * 40,
                             "dirty": {}}}
    state, reason = attestation_state(stale_recipe, "a" * 64, stale_recipe["tree"])
    assert state == "NOT ATTESTED"
    assert reason == REASON_DIFFERENT_RECIPE

    stale_root = {"recipe": ATTEST_RECIPE, "root": "a" * 64,
                  "attested": True, "frozen_tests": {}, "reasons": [],
                  "tree": {"recipe": "canopus-tree-v1", "head": "a" * 40,
                           "dirty": {}}}
    state, reason = attestation_state(stale_root, "b" * 64, stale_root["tree"])
    assert state == "NOT ATTESTED"
    assert reason == REASON_DIFFERENT_ROOT


def test_tree_drift_accounts_for_a_dropped_non_string_key():
    """The comment above the sort key in `tree_drift` states the reason for
    sorting by `(type(rel).__name__, repr(rel))` rather than filtering by
    type: every key of every hashable type must be compared, never dropped.
    Pinned directly: a `dirty` map keyed by a bare int, present in `was` and
    absent from `now`, must be REPORTED as a vanished path. Sorting with a
    string-only filter (`isinstance(k, str)`) silently drops the int key
    instead, and the result reads as a clean, unmoved tree over a path that
    disappeared -- the greener and therefore wrong direction.
    """
    from scripts.utils.canopus_freeze import tree_drift

    was = {"recipe": "canopus-tree-v1", "head": "a" * 40, "dirty": {1: "h"}}
    now = {"recipe": "canopus-tree-v1", "head": "a" * 40, "dirty": {}}

    result = tree_drift(was, now)

    assert result != [], "the vanished int key must not be silently dropped"
    assert any("1" in reason for reason in result)


def test_the_drift_comparison_reads_no_disk_and_runs_no_git():
    """Promoted from the wire 3.2 frozen contract when that contract retired.

    `canopus_freeze` is the module the gate imports at EVERY pytest session
    start, and its import tail is stdlib plus `scripts.utils.atomic`. Its half
    of the tree feature is a pure comparison of two structures; the git half
    lives in `canopus_tree`, which the gate imports lazily. Reaching for
    `subprocess` or `git_output` here would put a git call on every session
    start of every suite in the workspace, and no other test says so.
    """
    import scripts.utils.canopus_freeze as cf

    assert not hasattr(cf, "subprocess")
    assert not hasattr(cf, "git_output")
    clean = {"recipe": "canopus-tree-v1", "head": "a" * 40, "dirty": {}}
    assert cf.tree_drift(dict(clean), dict(clean)) == []
