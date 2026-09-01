#!/usr/bin/env python3
"""Behavioural tests for scripts/import-legacy-records.py.

The importer is one-shot, local, no-network, no-delete, idempotent. These tests
exercise its public behaviour through the data-root seam: copy, skip-on-collision
(never overwrite), idempotency, dry-run, --only scoping, path-traversal safety,
and the no-delete guarantee on the source.

Destinations resolve through the data-root helpers; the data root is redirected
to a tmp dir via HEADING_OS_DATA (mirrors tests/test_data_config_seam.py).
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """Strip ANSI colour escapes so assertions match the literal words."""
    return _ANSI.sub("", text)


def _load_importer():
    """Load the hyphenated CLI module by path (not importable as a name)."""
    spec = importlib.util.spec_from_file_location(
        "import_legacy_records", ROOT / "scripts" / "import-legacy-records.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """A data root distinct from the engine root; identity cache reset so the
    CEO-default resolution (data_root == personal == corporate) holds."""
    d = tmp_path / ".heading-os-data"
    d.mkdir(parents=True)
    monkeypatch.setenv("HEADING_OS_DATA", str(d))
    # Ensure no THREADS_ROOT override leaks in from the real environment.
    monkeypatch.delenv("THREADS_ROOT", raising=False)
    from scripts.utils import workspace
    workspace._reset_identity_cache()
    return d


@pytest.fixture
def old_root(tmp_path):
    """A legacy on-disk workspace with all four subtrees populated."""
    old = tmp_path / "old-workspace"
    (old / "crm" / "contacts").mkdir(parents=True)
    (old / "threads" / "business").mkdir(parents=True)
    (old / "knowledge").mkdir(parents=True)
    (old / "personal" / "context").mkdir(parents=True)

    (old / "crm" / "contacts" / "jane-doe.md").write_text("# Jane\n", encoding="utf-8")
    (old / "crm" / "contacts" / "john-roe.md").write_text("# John\n", encoding="utf-8")
    (old / "threads" / "business" / "deal-x.md").write_text("thread\n", encoding="utf-8")
    (old / "knowledge" / "note.md").write_text("kb\n", encoding="utf-8")
    (old / "personal" / "context" / "personal-info.md").write_text("me\n", encoding="utf-8")
    return old


def _run(mod, argv):
    """Invoke main() with a synthetic argv; return the exit code."""
    old_argv = sys.argv
    sys.argv = ["import-legacy-records.py", *argv]
    try:
        return mod.main()
    finally:
        sys.argv = old_argv


def test_fresh_import_copies_all(data_root, old_root, capsys):
    mod = _load_importer()
    rc = _run(mod, ["--from", str(old_root)])
    assert rc == 0

    from scripts.utils.workspace import (
        get_crm_contacts_dir,
        get_knowledge_dir,
        get_personal_context_dir,
        get_threads_dir,
    )
    assert (get_crm_contacts_dir() / "jane-doe.md").read_text() == "# Jane\n"
    assert (get_crm_contacts_dir() / "john-roe.md").exists()
    # Nested structure under threads/ is preserved.
    assert (get_threads_dir() / "business" / "deal-x.md").exists()
    assert (get_knowledge_dir() / "note.md").exists()
    assert (get_personal_context_dir() / "personal-info.md").exists()

    out = capsys.readouterr().out
    assert "imported" in out


def test_idempotent_second_run_imports_nothing(data_root, old_root, capsys):
    mod = _load_importer()
    _run(mod, ["--from", str(old_root)])
    capsys.readouterr()  # discard first-run output

    rc = _run(mod, ["--from", str(old_root)])
    assert rc == 0
    out = _plain(capsys.readouterr().out)
    # All five files already exist -> total imported 0, all skipped.
    assert "Total: imported 0" in out
    assert "skipped 5 (already exist)" in out

    # Source untouched (no-delete on source).
    assert (old_root / "crm" / "contacts" / "jane-doe.md").exists()


def test_collision_never_overwrites(data_root, old_root):
    from scripts.utils.workspace import get_crm_contacts_dir

    # Pre-seed a destination file with distinct content.
    dest = get_crm_contacts_dir()
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "jane-doe.md").write_text("KEEP-ME\n", encoding="utf-8")

    mod = _load_importer()
    _run(mod, ["--from", str(old_root)])

    # The pre-existing file is preserved byte-for-byte; the other is imported.
    assert (dest / "jane-doe.md").read_text() == "KEEP-ME\n"
    assert (dest / "john-roe.md").read_text() == "# John\n"


def test_dry_run_writes_nothing(data_root, old_root, capsys):
    from scripts.utils.workspace import get_crm_contacts_dir, get_knowledge_dir

    mod = _load_importer()
    rc = _run(mod, ["--from", str(old_root), "--dry-run"])
    assert rc == 0

    assert not (get_crm_contacts_dir() / "jane-doe.md").exists()
    assert not (get_knowledge_dir() / "note.md").exists()

    out = capsys.readouterr().out
    assert "DRY-RUN" in out or "Dry-run" in out


def test_only_scopes_to_one_subtree(data_root, old_root):
    from scripts.utils.workspace import get_crm_contacts_dir, get_knowledge_dir

    mod = _load_importer()
    _run(mod, ["--from", str(old_root), "--only", "crm"])

    assert (get_crm_contacts_dir() / "jane-doe.md").exists()
    # knowledge/ was not selected -> not imported.
    assert not (get_knowledge_dir() / "note.md").exists()


def test_traversal_is_refused(data_root, tmp_path):
    """A source tree engineered to escape the destination writes nothing outside it.

    Until 2026-08-23 this test attempted no escape at all: it copied one benign
    file and asserted that benign file landed where it should. The named security
    property had zero coverage, so a regression that followed a symlink or a
    crafted nested path out of the destination passed cleanly.

    Three real attempts now, all of which a legacy tree could genuinely contain:
    a symlinked FILE aimed above the destination, a symlinked DIRECTORY aimed at
    a sibling tree, and a nested path whose components spell a climb.

    Two of those three were still unasserted until 2026-09-01. Every check below
    watches the DESTINATION, and a source symlink is a READ out of the source
    tree: the destination stays perfectly tidy, no symlink is copied, nothing
    resolves outside, and the content of a file the operator never named lands
    in the overlay as a regular .md. Measured that day, deleting the
    `if src_file.is_symlink()` skip from the importer left all 8 tests in this
    file green. The content assertion below is what closes it; the neighbouring
    tests/test_an_import_that_overwrote_and_an_import_that_gave_up.py holds the
    same property from the counting side.

    The third attempt is honestly a non-attempt: `rel` comes from
    `src_file.relative_to(src_dir)` over an `rglob` walk, so it can never carry
    a `..` component and the importer's own prefix check is unreachable through
    this path. Deleting that check changes no verdict here or in any neighbour.
    It is defence in depth against a future caller that builds `rel` some other
    way, and it is named as such rather than left looking measured.
    """
    from scripts.utils.workspace import get_knowledge_dir

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "victim.md").write_text("original\n", encoding="utf-8")

    old = tmp_path / "evil-old"
    (old / "knowledge").mkdir(parents=True)
    (old / "knowledge" / "safe.md").write_text("safe\n", encoding="utf-8")
    # 1. a file symlink aimed at a file outside the source tree
    (old / "knowledge" / "link.md").symlink_to(outside / "victim.md")
    # 2. a directory symlink aimed at the tree outside
    (old / "knowledge" / "linkdir").symlink_to(outside, target_is_directory=True)
    # 3. a nested path that spells a climb once joined
    nested = old / "knowledge" / "sub"
    nested.mkdir()
    (nested / "deep.md").write_text("deep\n", encoding="utf-8")

    mod = _load_importer()
    rc = _run(mod, ["--from", str(old), "--only", "knowledge"])
    assert rc in (0, 2), f"the importer neither imported nor refused cleanly: rc={rc}"

    kd = get_knowledge_dir().resolve()
    # Prove the import actually ran, so the assertions below are about its
    # behaviour and not about a no-op.
    assert (kd / "safe.md").exists(), "the import did nothing; the rest proves nothing"

    # Whatever it chose to import, nothing may sit outside the destination and
    # nothing outside may have been modified.
    assert (outside / "victim.md").read_text(encoding="utf-8") == "original\n", \
        "the import wrote through a symlink into a file outside the destination"
    assert not (kd.parent / "safe.md").exists()
    assert not (kd.parent / "victim.md").exists()

    for path in kd.rglob("*"):
        assert not path.is_symlink(), f"a symlink was copied into the destination: {path}"
        resolved = path.resolve()
        assert resolved == kd or kd in resolved.parents, \
            f"an imported path resolves outside the destination: {path} -> {resolved}"

    # The read, not the write. Following the link produces a REGULAR file in the
    # destination whose bytes came from outside the source tree, which every
    # assertion above accepts.
    assert not (kd / "link.md").exists(), (
        "the source symlink was followed and its target imported as a regular "
        "file; the importer's collection surface is no longer the four subtrees "
        "its docstring names")
    # NOT a scan to skip through: this asks whether ANY imported file carries
    # content from outside the source tree, so a file dropped between the walk
    # and the read is the one file that could hold it. The tree is the test's
    # own destination directory, so a vanish here is a defect in the importer
    # rather than a parallel agent; one retry, then fail by name.
    imported = []
    for p in kd.rglob("*"):
        if not p.is_file():
            continue
        try:
            imported.append(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            try:
                imported.append(p.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise AssertionError(
                    f"{p} was listed in the destination and gone by the read, "
                    f"twice; this assertion is about every imported file, so it "
                    f"cannot answer with one of them missing."
                ) from exc
    assert "original\n" not in imported, (
        f"content from outside the source tree reached the destination: {imported}")


def test_missing_from_dir_exits_nonzero(data_root, tmp_path):
    mod = _load_importer()
    rc = _run(mod, ["--from", str(tmp_path / "does-not-exist")])
    assert rc == 2


def test_source_files_survive(data_root, old_root):
    """No-delete guarantee: every source file is still present after import."""
    mod = _load_importer()
    _run(mod, ["--from", str(old_root)])

    for rel in [
        "crm/contacts/jane-doe.md",
        "crm/contacts/john-roe.md",
        "threads/business/deal-x.md",
        "knowledge/note.md",
        "personal/context/personal-info.md",
    ]:
        assert (old_root / rel).exists(), f"source vanished: {rel}"
