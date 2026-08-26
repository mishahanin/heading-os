"""Shard 04-p4: a CI guard that reported green when its whole scope was gone,
two transforms that no-op in silence, and one more unvalidated JSON shape.

* ``check-lfs-fixtures`` exists because "a job whose fixture tests all skipped
  reports green while proving nothing". If ``tests/`` itself is absent - a
  sparse checkout, a tree move - every fixture blob is missing, which is the
  MAXIMAL version of that failure, and the guard printed a note and exited 0.

* ``check-readme-numbers``'s docstring named two front doors for a guard that
  checks three. ROADMAP.md joined ``FRONT_DOORS`` after drifting to 554 against
  a real 563, and a reader of the old text would have "fixed" a true failure by
  reverting ROADMAP's number.

* ``split-skills-catalog`` set each page's title with an exact-literal
  ``replace`` and its meta description with an ``re.sub``, neither verified.
  Any drift in the monolith's head shipped eight pages carrying the monolith's
  title and description, and reported success. Four structural markers were
  also located with a bare ``index``, so a renamed heading killed the tool on a
  raw ValueError rather than the clean abort it gives elsewhere.

* ``wizard-simulate`` read ``.workspace-identity.json`` - the file gating the
  ceo-master refusal - and called ``.get`` on whatever JSON came back, while
  the canned-answers file beside it is shape-checked three times.

Run: python3 -m pytest tests/test_a_guard_that_was_green_over_an_absent_tree.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


split = _load("split_catalog_under_test", "scripts/dev/split-skills-catalog.py")
LFS = ROOT / "scripts" / "dev" / "check-lfs-fixtures.py"
NUMBERS = ROOT / "scripts" / "dev" / "check-readme-numbers.py"
WIZSIM = ROOT / "scripts" / "dev" / "wizard-simulate.py"


# ============================================================
# The guard that was green with nothing to check
# ============================================================

def test_an_absent_tests_tree_fails_the_guard(tmp_path, monkeypatch):
    lfs = _load("lfs_under_test", "scripts/dev/check-lfs-fixtures.py")
    monkeypatch.setattr(lfs, "SCANNED", tmp_path / "does-not-exist")
    assert lfs.main() == 1


def test_the_refusal_says_no_fixture_was_checked(tmp_path, monkeypatch, capsys):
    """A green result there claimed a check that did not happen."""
    lfs = _load("lfs_under_test2", "scripts/dev/check-lfs-fixtures.py")
    monkeypatch.setattr(lfs, "SCANNED", tmp_path / "gone")
    lfs.main()
    err = capsys.readouterr().err
    assert "REFUSED" in err
    assert "did not happen" in err


def test_an_empty_but_present_tree_is_still_green(tmp_path, monkeypatch):
    """Present-and-clean is a real pass; absent is not."""
    lfs = _load("lfs_under_test3", "scripts/dev/check-lfs-fixtures.py")
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr(lfs, "SCANNED", tmp_path / "tests")
    assert lfs.main() == 0


def _tree_with_one_file(tmp_path: Path) -> Path:
    tree = tmp_path / "tests"
    tree.mkdir()
    (tree / "fixture.docx").write_bytes(b"PK\x03\x04")
    return tree


def test_a_file_deleted_mid_scan_is_reported_and_does_not_refuse(
        tmp_path, monkeypatch, capsys):
    """The guard was flaky against its OWN repository until 2026-08-25.

    `tests/test_a_pipeline_input_keeps_its_stream_clean.py` wrote a scratch file
    under `tests/` and removed it, so a parallel worker made `rglob` list a path
    that was gone by the time this guard opened it. That is a deleted file, not
    an unread one: nothing unresolved is left behind it, so refusing the whole
    run over it reports a fixture problem that does not exist.
    """
    lfs = _load("lfs_under_test5", "scripts/dev/check-lfs-fixtures.py")
    tree = _tree_with_one_file(tmp_path)

    def _vanished(path):
        raise FileNotFoundError(2, "No such file or directory", str(path))

    monkeypatch.setattr(lfs, "is_pointer", _vanished)
    monkeypatch.setattr(lfs, "SCANNED", tree)

    assert lfs.main() == 0
    err = capsys.readouterr().err
    assert "fixture.docx" in err, "the dropped file was not named"
    assert "deleted before this guard read them" in err
    assert "could not be read" not in err, (
        "a deleted file was reported as one whose LFS state is unknown"
    )


def test_a_present_file_that_cannot_be_read_still_refuses(tmp_path, monkeypatch):
    """The distinction is the point: unreadable is a hole, deleted is not."""
    lfs = _load("lfs_under_test6", "scripts/dev/check-lfs-fixtures.py")
    tree = _tree_with_one_file(tmp_path)

    def _denied(path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(lfs, "is_pointer", _denied)
    monkeypatch.setattr(lfs, "SCANNED", tree)
    assert lfs.main() == 1


def test_scan_keeps_the_two_outcomes_in_separate_buckets(tmp_path, monkeypatch):
    lfs = _load("lfs_under_test7", "scripts/dev/check-lfs-fixtures.py")
    tree = tmp_path / "tests"
    tree.mkdir()
    (tree / "a.docx").write_bytes(b"a")
    (tree / "b.docx").write_bytes(b"b")

    def _split(path):
        if path.name == "a.docx":
            raise FileNotFoundError(2, "No such file or directory", str(path))
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(lfs, "is_pointer", _split)
    pointers, unreadable, vanished = lfs.scan(tree)
    assert pointers == []
    assert [p.name for p in vanished] == ["a.docx"]
    assert [p.name for p, _ in unreadable] == ["b.docx"]


def test_a_real_pointer_is_still_caught_beside_a_deleted_file(tmp_path, monkeypatch):
    """The tolerance must not swallow the defect the guard exists for."""
    lfs = _load("lfs_under_test8", "scripts/dev/check-lfs-fixtures.py")
    tree = tmp_path / "tests"
    tree.mkdir()
    (tree / "pointer.docx").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\noid sha256:00\nsize 1\n")
    (tree / "gone.docx").write_bytes(b"x")
    real_is_pointer = lfs.is_pointer

    def _one_vanishes(path):
        if path.name == "gone.docx":
            raise FileNotFoundError(2, "No such file or directory", str(path))
        return real_is_pointer(path)

    monkeypatch.setattr(lfs, "is_pointer", _one_vanishes)
    monkeypatch.setattr(lfs, "SCANNED", tree)
    assert lfs.main() == 1


def test_the_docstring_separates_deleted_from_unreadable():
    lfs = _load("lfs_under_test9", "scripts/dev/check-lfs-fixtures.py")
    # The docstring is hard-wrapped, so both phrases straddle a newline; match
    # on the reflowed text rather than pinning where the wrap happens to fall.
    doc = " ".join(lfs.__doc__.split())
    assert "is reported and does not refuse" in doc
    assert "present and cannot be read leaves this check incomplete" in doc


def test_the_real_repository_still_passes():
    proc = subprocess.run([sys.executable, str(LFS)], capture_output=True,
                          text=True, timeout=300, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_docstring_names_the_absent_tree_case():
    lfs = _load("lfs_under_test4", "scripts/dev/check-lfs-fixtures.py")
    doc = lfs.__doc__
    assert "when the tree itself is absent" in doc


# ============================================================
# The docstring that named two of three front doors
# ============================================================

def test_the_docstring_names_every_front_door():
    """Each door must be named in the SCOPE paragraph, not only in the history.

    The correction quotes the sentence it replaced, and that sentence names two
    of the three doors - so a bare `in doc` passed even with a door removed
    from the scope paragraph. Anchor on position: the scope comes first.
    """
    numbers = _load("numbers_under_test", "scripts/dev/check-readme-numbers.py")
    doc = numbers.__doc__
    history = doc.index('said "two front doors"')
    for door in numbers.FRONT_DOORS:
        assert door.name in doc, f"{door.name} is checked and undocumented"
        assert doc.index(door.name) < history, (
            f"{door.name} is named only in the note about the old wording"
        )


def test_roadmap_is_still_one_of_them():
    numbers = _load("numbers_under_test2", "scripts/dev/check-readme-numbers.py")
    assert any(d.name == "ROADMAP.md" for d in numbers.FRONT_DOORS)


def test_the_guard_still_passes_on_this_repository():
    proc = subprocess.run([sys.executable, str(NUMBERS)], capture_output=True,
                          text=True, timeout=600, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ============================================================
# The transforms that no-op in silence
# ============================================================

def _head(title: str = "<title>Skills, MCP &amp; plugins — HEADING OS</title>",
          meta: str = '<meta name="description" content="the monolith">') -> str:
    return f"<html>\n<head>\n{title}\n{meta}\n</head>\n<main class=\"content\">"


def test_a_page_gets_its_own_title_and_description():
    page = split.build_category_page(_head(), "Intel skills", "skills-intel.html",
                                     "<div>cards</div>")
    assert "<title>Intel skills — HEADING OS</title>" in page
    assert 'content="Intel skills in HEADING OS' in page
    assert "Skills, MCP &amp; plugins — HEADING OS</title>" not in page


def test_a_drifted_title_is_refused_not_ignored():
    """Eight pages carrying the monolith's title, and a success line, was the
    outcome; naming the missing marker is what makes it fixable."""
    head = _head(title="<title>Skills and plugins - HEADING OS</title>")
    with pytest.raises(split.MarkerMissing):
        split.build_category_page(head, "Intel skills", "skills-intel.html", "")


def test_a_drifted_meta_description_is_refused():
    head = _head(meta="<meta name='description' content='single quotes'>")
    with pytest.raises(split.MarkerMissing):
        split.build_category_page(head, "Intel skills", "skills-intel.html", "")


# ============================================================
# The four markers located with a bare index
# ============================================================

def test_a_missing_marker_raises_the_named_refusal():
    with pytest.raises(split.MarkerMissing) as exc:
        split._find("<html></html>", split.TAIL_START)
    assert split.TAIL_START in str(exc.value)


def test_a_present_marker_is_found():
    text = f"prefix{split.MAIN_OPEN}suffix"
    assert split._find(text, split.MAIN_OPEN) == len("prefix")


def test_a_slice_with_a_missing_end_marker_raises_too():
    text = f"{split.MAIN_OPEN} body"
    with pytest.raises(split.MarkerMissing):
        split._slice(text, split.MAIN_OPEN, split.INTRO_START)


def test_the_bare_index_calls_are_gone_from_the_source():
    """`str.index` raises ValueError with no marker name in it."""
    src = (ROOT / "scripts" / "dev" / "split-skills-catalog.py").read_text(
        encoding="utf-8")
    assert "text.index(" not in src


def test_main_turns_the_refusal_into_a_clean_exit(monkeypatch, capsys):
    def _boom(_args):
        raise split.MarkerMissing(split.TAIL_START)
    monkeypatch.setattr(split, "_run", _boom)
    monkeypatch.setattr(sys, "argv", ["split-skills-catalog.py", "--dry-run"])

    assert split.main() == 1
    assert split.TAIL_START in capsys.readouterr().err


# ============================================================
# The identity file that gated the refusal
# ============================================================

def _workspace(tmp_path: Path, identity: str) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".workspace-identity.json").write_text(identity, encoding="utf-8")
    return ws


def _answers(tmp_path: Path) -> Path:
    p = tmp_path / "answers.json"
    p.write_text(json.dumps({"answers": {}}), encoding="utf-8")
    return p


@pytest.mark.parametrize("identity", ["[]", '"ceo-master"', "7", "null", "true"])
def test_a_non_object_identity_file_is_refused_cleanly(tmp_path, identity):
    ws = _workspace(tmp_path, identity)
    proc = subprocess.run(
        [sys.executable, str(WIZSIM), "--workspace", str(ws),
         "--answers", str(_answers(tmp_path))],
        capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "not an object" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_the_ceo_master_refusal_still_fires(tmp_path):
    """The safety property held by crashing; it must hold by refusing."""
    ws = _workspace(tmp_path, json.dumps({"type": "ceo-master"}))
    proc = subprocess.run(
        [sys.executable, str(WIZSIM), "--workspace", str(ws),
         "--answers", str(_answers(tmp_path))],
        capture_output=True, text=True, timeout=120, check=False)
    assert "REFUSED" in proc.stderr
    assert proc.returncode != 0


def test_an_unparseable_identity_file_is_still_handled(tmp_path):
    ws = _workspace(tmp_path, "{not json")
    proc = subprocess.run(
        [sys.executable, str(WIZSIM), "--workspace", str(ws),
         "--answers", str(_answers(tmp_path))],
        capture_output=True, text=True, timeout=120, check=False)
    assert "Traceback" not in proc.stderr
