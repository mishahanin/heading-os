"""A frontmatter key written with no value, and a log path that was a directory.

TWO DEFECTS, both silent for a long time, both found on 2026-08-29.

ONE. `keywords:` on a line by itself is legal YAML and `yaml.safe_load` returns
None for it. Six readers across two health scripts spelled the coercion
themselves, and five handled the STRING form but not the None. MEASURED: one
note with a bare `keywords:` made `keyword_frequency` raise
`TypeError: 'NoneType' object is not iterable`, killing all three output modes
of `knowledge-health.py` and both health-check callers that run it
(`scripts/memory.py`, `scripts/prime-health-parallel.py`). The note passed the
validity check on the way in, because `REQUIRED_FIELDS` tests key PRESENCE.

The fix is one shared `frontmatter_list`, beside `frontmatter_date`, not six
patched copies: a coercion written six times is a coercion that gets fixed in
one place and stays broken in five. The last test in the first section is what
holds that, by refusing the default-list spelling anywhere under `scripts/`.

TWO. `log_dir(*parts)` mkdirs the WHOLE joined path, so
`log_dir("memory-auto-retire.log")` created a DIRECTORY named
`memory-auto-retire.log`. Every append raised `IsADirectoryError` into an
`except OSError: pass`, so `memory-auto-retire`'s audit trail recorded NOTHING
from 2026-07-06 until this was found: 54 days of a retirement tool keeping no
record of what it retired. The directory was on disk, empty, the whole time.

Fixing only the caller would leave the trap armed for the next one, so
`log_dir` now refuses a part that names a file. That is the writer, and fixing
the writer is what stops the class.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.markdown import frontmatter_list  # noqa: E402
from scripts.utils.paths import log_dir  # noqa: E402
from scripts.utils.repo_files import read_sources, tracked_python_files  # noqa: E402


# ============================================================
# The coercion, in both directions
# ============================================================

EMPTY = [None, "", [], (), set()]
NON_EMPTY = [
    ("solo", ["solo"]),
    (["a", "b"], ["a", "b"]),
    (("c",), ["c"]),
    ([None, "d"], ["d"]),
    (42, ["42"]),
    (["x", 7], ["x", "7"]),
]


@pytest.mark.parametrize("value", EMPTY, ids=[repr(v) for v in EMPTY])
def test_an_absent_field_becomes_an_empty_list(value):
    assert frontmatter_list(value) == []


@pytest.mark.parametrize("value, expected", NON_EMPTY,
                         ids=[repr(v) for v, _ in NON_EMPTY])
def test_a_present_field_keeps_its_content(value, expected):
    """The other direction, so a body that returned `[]` for everything could
    not satisfy the case above on its own."""
    assert frontmatter_list(value) == expected


def test_the_two_directions_are_disjoint():
    assert all(frontmatter_list(v) == [] for v in EMPTY)
    assert all(frontmatter_list(v) for v, _ in NON_EMPTY)


# The list-shaped frontmatter fields. `fm.get("keywords", [])` returns None for
# a key written with no value, because a `dict.get` default applies only when
# the key is ABSENT, and a key present with an empty value is present.
_LIST_FIELDS = ("keywords", "sources", "principles", "tags")


def spells_a_lossy_list_default(source: str) -> list[int]:
    """Line numbers of `<expr>.get("<list field>", [])` calls in `source`.

    AST, not a substring search. Two of the five files the first version of
    this rule flagged were FALSE POSITIVES: the text appeared inside a comment
    explaining this very defect, in files that already handle it. A rule that
    punishes a file for documenting the trap teaches people to stop
    documenting it.

    Pure, so both directions are measurable on synthetic input.
    """
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "get"):
            continue
        if len(node.args) != 2:
            continue
        key, default = node.args
        if not (isinstance(key, ast.Constant) and key.value in _LIST_FIELDS):
            continue
        if isinstance(default, ast.List) and not default.elts:
            hits.append(node.lineno)
    return hits


LOSSY_SNIPPETS = [
    'x = fm.get("keywords", [])',
    'x = fm.get("sources", [])',
    'x = record["frontmatter"].get("principles", [])',
    'for t in meta.get("tags", []): pass',
]
SAFE_SNIPPETS = [
    'x = frontmatter_list(fm.get("keywords"))',
    'x = fm.get("keywords")',
    'x = fm.get("keywords", None)',
    'x = fm.get("title", [])',            # not a list-shaped field
    'x = fm.get("keywords", ["a"])',      # a real default, not the empty trap
    '# `.get("keywords", [])` returns None here - the key is present',
    'x = get("keywords", [])',            # not an attribute call
]


@pytest.mark.parametrize("snippet", LOSSY_SNIPPETS)
def test_the_rule_sees_the_lossy_spelling(snippet):
    assert spells_a_lossy_list_default(snippet)


@pytest.mark.parametrize("snippet", SAFE_SNIPPETS)
def test_the_rule_leaves_everything_else_alone(snippet):
    assert spells_a_lossy_list_default(snippet) == []


def test_no_reader_spells_the_coercion_for_itself_again():
    """The rule that keeps the fix from landing in some of nine copies.

    Nine sites across five files read a list-shaped field the same wrong way.
    A patch applied to six of them would have left three as live defects with
    nothing pointing at them; this sweep is what found the last three.
    """
    paths = tracked_python_files(root=ROOT)
    # A corpus of zero makes "no offenders" true while reading nothing, and this
    # sweep is the only thing standing between the fix and six of the nine sites
    # keeping the defect. MEASURED 2026-09-01 by making `tracked_paths` return
    # `[]`: all 39 tests in this file passed over an empty walk. Every other
    # tree sweep in the suite carries this line - see
    # `tests/test_no_os_path_join.py`, `tests/test_per_exec_contacts_dir.py`,
    # `tests/test_transcript_dir_has_one_owner.py` - and this one did not.
    # 443 files matched on 2026-09-01. The floor is asserted on what was READ
    # rather than on what was walked, because a corpus can also shrink between
    # the two: `read_sources` skips a file that vanished in that window (it
    # cannot hold the tenth copy) and warns naming it, and the count below is
    # what the verdict was actually formed over.
    offenders = {}
    vanished = []
    read = 0
    for path, text in read_sources(paths, vanished, errors="replace"):
        if path.name == Path(__file__).name:
            continue
        read += 1
        try:
            lines = spells_a_lossy_list_default(text)
        except SyntaxError:
            continue
        if lines:
            offenders[str(path.relative_to(ROOT))] = lines
    assert read >= 300, (
        f"the scan collapsed to {read} files read of {len(paths)} walked "
        f"({len(vanished)} vanished mid-walk)")
    assert not offenders, (
        "these take a list-shaped frontmatter field with a `[]` default, which "
        "yields None for a key written with no value. Use "
        f"frontmatter_list(): {offenders}")


def test_the_health_scripts_call_the_shared_helper():
    """The other direction of the sweep above: a rule that forbids the wrong
    spelling is satisfied by deleting the call entirely."""
    for rel in ("scripts/knowledge-health.py", "scripts/odin-brain-health.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "frontmatter_list" in called, rel


# ============================================================
# The reader that died: end to end through its own seam
# ============================================================

@pytest.fixture(scope="module")
def kh():
    spec = importlib.util.spec_from_file_location(
        "knowledge_health_probe", ROOT / "scripts" / "knowledge-health.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["knowledge_health_probe"] = module
    spec.loader.exec_module(module)
    return module


NOTE_BARE = ("---\nid: '1'\ntitle: Bare\ntype: permanent\ncreated: 2026-01-01\n"
             "keywords:\n---\n\nbody text\n")
NOTE_ONE = ("---\nid: '2'\ntitle: One\ntype: permanent\ncreated: 2026-01-01\n"
            "keywords: solo\n---\n\nbody text\n")
NOTE_MANY = ("---\nid: '3'\ntitle: Many\ntype: permanent\ncreated: 2026-01-01\n"
             "keywords: [alpha, beta]\n---\n\nbody text\n")


@pytest.fixture()
def three_notes(kh, tmp_path, monkeypatch):
    """A private corpus behind the one seam `scan_notes` reads.

    `scan_notes()` takes no arguments and resolves its own root, so an earlier
    probe that reassigned `get_knowledge_dir` silently read the operator's LIVE
    knowledge directory and proved nothing about the fixture it thought it had
    written. `scanned_note_files` is the seam it actually iterates.
    """
    written = []
    for name, body in (("bare.md", NOTE_BARE), ("one.md", NOTE_ONE),
                       ("many.md", NOTE_MANY)):
        path = tmp_path / name
        path.write_text(body, encoding="utf-8")
        written.append(("permanent", path))
    monkeypatch.setattr(kh, "scanned_note_files", lambda: iter(written))
    return kh


def test_a_bare_keywords_note_scans_to_an_empty_list(three_notes):
    notes = {n["title"]: n["keywords"] for n in three_notes.scan_notes()}
    assert notes["Bare"] == []
    assert notes["One"] == ["solo"]
    assert notes["Many"] == ["alpha", "beta"]


def test_the_report_no_longer_dies_on_it(three_notes):
    """The failure as the operator met it: a TypeError out of the counter."""
    counts = three_notes.keyword_frequency(three_notes.scan_notes())
    assert dict(counts) == {"solo": 1, "alpha": 1, "beta": 1}


# ============================================================
# The log path that was a directory
# ============================================================

FILE_PARTS = ["run.log", "events.jsonl", "a.ndjson", "b.json", "c.txt", "d.csv",
              "MIXED.Log"]
DIR_PARTS = ["denials", "memory-ops", "sub/dir", "v1.2-runs"]


@pytest.mark.parametrize("part", FILE_PARTS)
def test_log_dir_refuses_a_part_that_names_a_file(part, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="names a file"):
        log_dir(part)
    assert not (tmp_path / part).exists(), "it created the trap before refusing"


@pytest.mark.parametrize("part", DIR_PARTS)
def test_log_dir_still_makes_a_directory(part, tmp_path, monkeypatch):
    """The other direction. A guard that refused every part would satisfy the
    cases above and break every real caller."""
    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path))
    made = log_dir(part)
    assert made.is_dir()


def _retire_module():
    spec = importlib.util.spec_from_file_location(
        "memory_auto_retire_probe", ROOT / "scripts" / "memory-auto-retire.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_retire_log_is_a_file_and_can_be_written(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path))
    module = _retire_module()

    assert not module._log_path().is_dir()
    module._log_line("a line the audit trail should keep")
    assert module._log_path().is_file()
    assert "should keep" in module._log_path().read_text(encoding="utf-8")


def test_importing_the_retire_script_creates_no_log_directory(tmp_path, monkeypatch):
    """Import must be inert. `LOG_PATH = log_dir() / ...` at module level ran
    `log_dir()`, which mkdirs, so merely loading this file to read `--help` or
    to inspect it left a `.logs` directory behind.
    """
    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path / "unwanted"))
    _retire_module()
    assert not (tmp_path / "unwanted").exists(), (
        "importing memory-auto-retire.py created its log directory")


def test_a_redirect_set_after_the_import_still_moves_the_audit_trail(tmp_path, monkeypatch):
    """The ordering the fix is really about.

    `main()` calls `load_env()` as its first statement because this process
    starts without the gitignored `.env` loaded, and `log_dir()` reads
    `WORKSPACE_LOG_DIR` from `os.environ`. A path frozen at import is resolved
    before that, so the audit trail of a destructive edit could land somewhere
    the run's own configuration does not name.
    """
    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path / "at-import"))
    module = _retire_module()

    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path / "after-load-env"))
    module._log_line("written after the environment was loaded")

    late = tmp_path / "after-load-env" / module.LOG_NAME
    assert late.is_file(), "the audit line did not follow the environment"
    assert "after the environment" in late.read_text(encoding="utf-8")
    assert not (tmp_path / "at-import" / module.LOG_NAME).exists(), (
        "the audit line went to the path frozen at import time")


def test_an_unwritable_audit_trail_is_reported_rather_than_swallowed(tmp_path, monkeypatch, capsys):
    """The handler said `pass`. That is how an IsADirectoryError hid for eight
    weeks while every run reported success and wrote nothing."""
    monkeypatch.setenv("WORKSPACE_LOG_DIR", str(tmp_path))
    module = _retire_module()
    trap = tmp_path / module.LOG_NAME
    trap.mkdir(parents=True)

    module._log_line("this cannot be written")

    err = capsys.readouterr().err
    assert "NOT written" in err and str(trap) in err, (
        f"an audit write failed in silence; stderr was {err!r}")
