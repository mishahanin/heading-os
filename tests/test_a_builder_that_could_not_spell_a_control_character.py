#!/usr/bin/env python3
"""Both repo builders read `git ls-files` by lines, so a quoted path mis-routed.

`scripts/build_engine_repo.py` and `scripts/build_data_repo.py` both enumerated
the workspace with

    git -c core.quotepath=false ls-files

and split the result with `.splitlines()`. `core.quotepath=false` suppresses
quoting for NON-ASCII bytes ONLY. Git quotes and C-escapes a path holding a
CONTROL character whatever that setting says, so the flag never covered the
class the comment in `build_engine_repo.py` claimed it closed.

MEASURED 2026-08-30, scratch repository on ext4, git 2.43.0. Tracked files were
created with a newline, a tab, a CR, a vertical tab, a non-ASCII name, a space,
a `"` and a backslash. `git -c core.quotepath=false ls-files` returned, verbatim:

    '"_secure/back\\\\slash.md"\\n"_secure/leak\\\\tb.md"\\n"_secure/leak\\\\na.md"\\n'
    '"_secure/leak\\\\vd.md"\\n"_secure/leak\\\\rc.md"\\n_secure/plain space.md\\n'
    '_secure/plain.md\\n"_secure/say \\\\"hi\\\\".md"\\n_secure/\u0444\u0430\u0439\u043b.md\\n'

Five of the nine came back wrapped in double quotes with the control character
C-escaped into two ASCII characters. The same repository under `ls-files -z`
returned every one of the nine verbatim, NUL-separated, and the output was
byte-identical with and without the `core.quotepath=false` flag.

The consequence, measured the same day against the live routing map:

    get_routing_destination('crm/contacts/leak\\na.md')     -> 'private'
    get_routing_destination('"crm/contacts/leak\\\\na.md"')  -> 'engine'

The leading quote matches no rule key, so the map's default answers `engine`.
In `build_data_repo.py` that drops a private file out of the data overlay with
no skip reported. In `build_engine_repo.py` it puts a private path in the
public-repo bucket, and the belt-and-braces refusal does not save it either:

    _suspicious_engine(['"crm/contacts/leak\\\\na.md"'])  -> []
    _suspicious_engine(['crm/contacts/leak\\na.md'])     -> ['crm/contacts/leak\\na.md']

`_suspicious_engine` matches with `startswith`, which the same leading quote
defeats, so the build prints "routing clean" over a mis-routed private path.

Second, smaller hazard on the same two lines: `text=True` with no `encoding=`
decodes git's bytes through the host locale, so the same repository answers
differently on a non-UTF-8 machine.

Sibling of the fix already landed in `scripts/utils/commit_source.py`
(`_changed_paths`, commit 6b12a8e). `tests/test_a_publisher_that_could_not_see_
a_non_ascii_path.py` recorded this exact residual for these two files and left
it unfixed; this is that residual closed.

Run: .venv/bin/python -m pytest tests/test_a_builder_that_could_not_spell_a_control_character.py -q
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.build_data_repo import _tracked_files as data_tracked_files  # noqa: E402
from scripts.build_engine_repo import _tracked_files as engine_tracked_files  # noqa: E402

# Both builders are being held to the same contract, so every behavioural test
# below runs twice, once per reader.
READERS = pytest.mark.parametrize(
    "reader",
    [
        pytest.param(engine_tracked_files, id="build_engine_repo"),
        pytest.param(data_tracked_files, id="build_data_repo"),
    ],
)

# Invented fixture names. Nothing here is a real path, person, or host.
PLAIN = "docs/plain.md"
SPACED = "docs/two words.md"
NON_ASCII = "docs/\u0437\u0430\u043c\u0435\u0442\u043a\u0430.md"
QUOTE_CHAR = 'docs/say "hi".md'
BACKSLASH = "docs/back\\slash.md"

# One entry per control character, so a filesystem that refuses one does not
# silently take the whole class with it. `_control_names` below asserts that at
# least one survived creation, and the test reports which.
CONTROL_NAMES = {
    "newline": "docs/leak\na.md",
    "tab": "docs/leak\tb.md",
    "carriage-return": "docs/leak\rc.md",
    "vertical-tab": "docs/leak\x0bd.md",
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True)


def _make_repo(tmp_path: Path, names) -> tuple[Path, list[str]]:
    """A scratch repo whose quotePath setting is git's DEFAULT, not one we chose.

    Setting `core.quotepath=false` in the fixture would measure a repository
    nobody actually has, and the point of this file is that the setting is not
    what makes the difference.
    """
    repo = tmp_path / "scratch"
    repo.mkdir()
    _git(repo, "init", "-q")
    created: list[str] = []
    for name in names:
        target = repo / name
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x\n")
        except OSError:
            continue
        created.append(name)
    _git(repo, "add", "-A")
    return repo, created


@pytest.fixture(scope="module")
def control_names(tmp_path_factory) -> dict[str, str]:
    """The control-character names this filesystem will actually hold.

    A newline in a filename is not creatable everywhere. Rather than skip the
    class silently, each candidate is tried and the survivors are reported.
    """
    probe = tmp_path_factory.mktemp("control-probe")
    survivors = {}
    for label, name in CONTROL_NAMES.items():
        target = probe / name
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x\n")
        except OSError:
            continue
        survivors[label] = name
    return survivors


def test_the_filesystem_holds_at_least_one_control_character_name(control_names) -> None:
    """The premise. Without one creatable case the rest of this file measures nothing."""
    assert control_names, (
        "no control character is legal in a filename on this filesystem, so "
        f"none of {sorted(CONTROL_NAMES)} could be created; the defect this "
        "file names cannot be exercised here and the fix is unverified"
    )


# ============================================================
# The premise, pinned: the old invocation really did quote
# ============================================================

def test_quotepath_false_still_quotes_a_control_character(tmp_path, control_names) -> None:
    """Pin the premise. `core.quotepath=false` does NOT cover this class.

    If a future git stops quoting control characters this fails loudly, rather
    than leaving the fix looking like superstition.
    """
    repo, created = _make_repo(tmp_path, list(control_names.values()))
    assert created, "fixture created nothing"
    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout
    quoted = [line for line in out.splitlines() if line.startswith('"')]
    assert len(quoted) == len(created), (
        "expected every control-character path to come back C-quoted from the "
        f"pre-fix invocation; got {out!r} for {created!r}"
    )


# ============================================================
# The fix, both directions
# ============================================================

@READERS
def test_a_control_character_path_is_returned_whole_and_unquoted(
    tmp_path, control_names, reader
) -> None:
    """The named defect. Quoted, the path routes `engine` instead of `private`."""
    repo, created = _make_repo(tmp_path, list(control_names.values()))
    got = reader(repo)
    for name in created:
        assert name in got, (
            f"{name!r} is missing from {got!r}. Pre-fix it arrived C-quoted as "
            f'{chr(34) + name + chr(34)!r}-shaped text, which matches no routing '
            f"rule and so classified `engine`."
        )
    assert not any(entry.startswith('"') for entry in got), (
        f"a returned path is still C-quoted: {got!r}"
    )


@READERS
def test_a_newline_in_a_filename_is_not_split_into_two_paths(
    tmp_path, control_names, reader
) -> None:
    """`.splitlines()` cut one path into two half-paths that name no file."""
    name = control_names.get("newline")
    if name is None:
        pytest.skip("this filesystem refuses a newline in a filename")
    repo, created = _make_repo(tmp_path, [name])
    assert created == [name]
    got = reader(repo)
    assert got == [name], f"expected exactly one whole path, got {got!r}"


@READERS
def test_a_carriage_return_is_not_rewritten_to_a_newline(tmp_path, reader) -> None:
    """The second defect, found while binding the first.

    Any subprocess text mode turns on universal newlines, and `subprocess` has
    no `newline=` knob to switch it off. MEASURED 2026-08-30: `ls-files -z`
    returned `b'docs/leak\\r\\nd.md\\x00docs/leak\\rc.md\\x00'` and
    `encoding="utf-8", errors="surrogateescape"` decoded it to
    `'docs/leak\\nd.md\\x00docs/leak\\nc.md\\x00'`. Every CR became LF and the
    CRLF name lost a byte. `-z` alone does not save you from this.
    """
    names = ["docs/leak\rc.md", "docs/leak\r\nd.md"]
    repo, created = _make_repo(tmp_path, names)
    if not created:
        pytest.skip("this filesystem refuses a carriage return in a filename")
    got = reader(repo)
    assert sorted(got) == sorted(created), (
        f"expected {created!r} verbatim, got {got!r}"
    )
    assert all("\r" in entry for entry in got), (
        f"a carriage return was translated away: {got!r}"
    )


@READERS
def test_a_non_ascii_path_still_works(tmp_path, reader) -> None:
    """The case `core.quotepath=false` was added for must keep working under -z."""
    repo, created = _make_repo(tmp_path, [NON_ASCII])
    assert created == [NON_ASCII]
    assert reader(repo) == [NON_ASCII]


@READERS
def test_a_path_with_a_space_is_not_split(tmp_path, reader) -> None:
    repo, created = _make_repo(tmp_path, [SPACED])
    assert created == [SPACED]
    assert reader(repo) == [SPACED]


@READERS
def test_a_quote_or_backslash_in_a_name_is_returned_verbatim(tmp_path, reader) -> None:
    """Both trigger git's quoting too, and both were measured doing so."""
    repo, created = _make_repo(tmp_path, [QUOTE_CHAR, BACKSLASH])
    assert sorted(reader(repo)) == sorted(created)


@READERS
def test_the_ordinary_repository_is_unchanged(tmp_path, reader) -> None:
    """Negative control: the common case must produce exactly what it did before.

    `.splitlines()` over the un-`-z` output is the pre-fix reader, run here
    against the same repo. On a tree of ordinary names the two must agree
    exactly, or this fix changed something it was not asked to change.
    """
    ordinary = [PLAIN, "scripts/tool.py", "docs/nested/deep/page.md", "README.md"]
    repo, created = _make_repo(tmp_path, ordinary)
    assert sorted(created) == sorted(ordinary)

    pre_fix = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout
    pre_fix_paths = [line for line in pre_fix.splitlines() if line.strip()]

    assert sorted(reader(repo)) == sorted(pre_fix_paths) == sorted(ordinary)


@READERS
def test_the_trailing_empty_entry_is_dropped(tmp_path, reader) -> None:
    """`-z` NUL-TERMINATES, so a naive split leaves an empty final entry."""
    repo, _ = _make_repo(tmp_path, [PLAIN])
    assert "" not in reader(repo)


# The one line of `_tracked_files` that both readers comment and neither pinned:
#
#     # No `.strip()` filter: a filename may legally begin or end with
#     # whitespace, and trimming it produces a path that matches neither a
#     # routing rule nor anything on disk. Only the empty trailing entry `-z`
#     # leaves is dropped.
#
# MEASURED 2026-09-01 in a scratch copy: changing the return in BOTH builders to
# `[entry.strip() for entry in out.split("\0") if entry.strip()]` left this file
# green at 27 passed. Every existing case here holds its whitespace in the
# MIDDLE of the name (`docs/two words.md`, `docs/leak\tb.md`), which `.strip()`
# never touches, so the comment was a claim nothing tested.
#
# The harm is silent, which is why it is worth a case. The copy loop in both
# builders is `if not src.is_file(): continue`, so a trimmed name that opens no
# file is DROPPED with no error, no warning and a `copied` count that quietly
# under-reports. On the data side that is a private file missing from the
# backup; on the engine side it is a file missing from the public build. Neither
# run says anything.
# The whitespace has to sit at an END OF THE WHOLE PATH, which is the only place
# `.strip()` reaches. `docs/two words.md` and `docs/leak\tb.md` above hold theirs
# in the middle and are untouched by it, which is exactly why they left the
# comment unverified.
EDGE_WHITESPACE_NAMES = (
    "docs/trailing.md ",     # a space after the extension
    "docs/tabbed.md\t",      # a tab, which `.strip()` also takes
    " docs/leading.md",      # a top-level directory whose name starts with a space
)


@pytest.fixture(scope="module")
def whitespace_names(tmp_path_factory) -> list[str]:
    """The edge-whitespace names this filesystem will actually hold.

    Some filesystems trim silently, so each candidate is created and then read
    back from its own directory listing before it is trusted.
    """
    probe = tmp_path_factory.mktemp("whitespace-probe")
    survivors = []
    for name in EDGE_WHITESPACE_NAMES:
        target = probe / name
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x\n")
        except OSError:
            continue
        listed = [p.name for p in target.parent.iterdir()]
        if Path(name).name in listed:
            survivors.append(name)
    return survivors


def test_the_filesystem_holds_a_trailing_whitespace_name(whitespace_names) -> None:
    """The premise, so the two tests below cannot pass over an empty corpus."""
    assert whitespace_names, (
        "this filesystem trims trailing whitespace from a filename, so the "
        "`.strip()` case cannot be exercised here and the readers' `No "
        "`.strip()` filter` comment stays unverified"
    )


@READERS
def test_a_trailing_whitespace_path_is_returned_verbatim(tmp_path, whitespace_names,
                                                         reader) -> None:
    """`.strip()` on each entry renames the file to one that does not exist."""
    repo, created = _make_repo(tmp_path, whitespace_names)
    assert created, "fixture created nothing"
    got = reader(repo)
    for name in created:
        assert name in got, (
            f"{name!r} came back as {got!r}. A trimmed name opens no file, and "
            f"the copy loop's `if not src.is_file(): continue` then drops it "
            f"with no error and no line in the report."
        )


@READERS
def test_a_trailing_whitespace_path_still_names_a_file_on_disk(
        tmp_path, whitespace_names, reader) -> None:
    """The consequence, asserted the way the copy loop asks the question.

    `src.is_file()` is the only thing standing between a returned path and a
    silent drop, so that is what this checks rather than string equality.
    """
    repo, created = _make_repo(tmp_path, whitespace_names)
    assert created, "fixture created nothing"
    unopenable = [rel for rel in reader(repo) if not (repo / rel).is_file()]
    assert unopenable == [], (
        f"the reader returned {unopenable!r}, which the copy loop would skip "
        f"without a word"
    )


# ============================================================
# The routing consequence, end to end
# ============================================================

@pytest.mark.parametrize("builder", ["build_engine_repo", "build_data_repo"])
def test_partition_routes_a_control_character_path_by_its_real_name(
    tmp_path, control_names, builder
) -> None:
    """The reader feeds `partition`, and `partition` is where the harm lands.

    A quoted `crm/contacts/...` matches no rule key and falls to the map's
    `engine` default, which is the PUBLIC repo's bucket and, on the data side,
    the bucket that is never copied.
    """
    import importlib

    module = importlib.import_module(f"scripts.{builder}")
    name = next(iter(control_names.values()))
    private_rel = f"crm/contacts/{Path(name).name}"
    repo, created = _make_repo(tmp_path, [private_rel])
    assert created == [private_rel]

    buckets = module.partition(repo)
    assert buckets["private"] == [private_rel], (
        f"expected {private_rel!r} in the private bucket; buckets were {buckets!r}"
    )
    assert buckets["engine"] == []


def test_the_engine_refusal_sees_a_control_character_path(tmp_path, control_names) -> None:
    """`_suspicious_engine` is the last guard, and the quote defeated it too.

    Asserted on the raw classifier rather than through `partition`, because with
    the fix in place such a path no longer reaches the engine bucket at all. The
    guard still has to be able to name it if it ever does.
    """
    from scripts.build_engine_repo import _suspicious_engine

    name = next(iter(control_names.values()))
    private_rel = f"crm/contacts/{Path(name).name}"
    assert _suspicious_engine([private_rel]) == [private_rel]
    assert _suspicious_engine([f'"{private_rel}"']) == [], (
        "the quoted form is expected to slip past this guard; that is why the "
        "reader must never produce one"
    )


# ============================================================
# The class, not just this instance
# ============================================================

BUILDERS = (
    ROOT / "scripts" / "build_engine_repo.py",
    ROOT / "scripts" / "build_data_repo.py",
)


@pytest.mark.parametrize("script", BUILDERS, ids=lambda p: p.name)
def test_the_ls_files_call_carries_z_and_an_explicit_encoding(script: Path) -> None:
    """Asked of the syntax tree, not of a grep.

    A grep also matches the comment and the docstring, and a comment is exactly
    what promised correctness here before.
    """
    tree = ast.parse(script.read_text(encoding="utf-8"))
    calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, (ast.List, ast.Tuple)):
            continue
        if not all(isinstance(el, ast.Constant) and isinstance(el.value, str)
                   for el in first.elts):
            continue
        argv = [el.value for el in first.elts]
        if "ls-files" not in argv:
            continue
        calls += 1
        assert "-z" in argv, (
            f"{script.name} line {node.lineno}: `git ls-files` without -z. "
            f"`core.quotepath=false` does not stop git C-quoting a path that "
            f"holds a control character."
        )
        keywords = {kw.arg for kw in node.keywords}
        assert not keywords & {"text", "encoding", "universal_newlines"}, (
            f"{script.name} line {node.lineno}: subprocess text mode. Bare "
            f"`text=True` decodes through the host locale, and naming an "
            f"`encoding=` does not help because text mode also turns on "
            f"universal newlines, which rewrites a CR in a filename to LF. "
            f"`subprocess` has no `newline=` knob. Read bytes and decode."
        )
    assert calls >= 1, (
        f"no `git ls-files` call found in {script.name} at all: this test can "
        f"no longer see what it claims to check"
    )


@pytest.mark.parametrize("script", BUILDERS, ids=lambda p: p.name)
def test_the_reader_decodes_deliberately(script: Path) -> None:
    """Bytes mode is only half the answer; the decode has to be named too.

    Without `errors="surrogateescape"` a path whose bytes are not valid UTF-8
    raises, and the whole build dies on one filename.
    """
    tree = ast.parse(script.read_text(encoding="utf-8"))
    functions = [n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_tracked_files"]
    assert len(functions) == 1, f"{script.name}: expected one `_tracked_files`"
    decodes = [n for n in ast.walk(functions[0])
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "decode"]
    assert len(decodes) == 1, (
        f"{script.name}: `_tracked_files` should decode git's bytes exactly "
        f"once; found {len(decodes)}"
    )
    args = [a.value for a in decodes[0].args if isinstance(a, ast.Constant)]
    assert args[:2] == ["utf-8", "surrogateescape"], (
        f"{script.name}: decode arguments are {args!r}; a path whose bytes are "
        f"not valid UTF-8 must survive to `os.fsencode`, not raise"
    )


@pytest.mark.parametrize("script", BUILDERS, ids=lambda p: p.name)
def test_the_reader_does_not_split_git_output_by_lines(script: Path) -> None:
    """`splitlines()` anywhere in `_tracked_files` reopens the newline case."""
    tree = ast.parse(script.read_text(encoding="utf-8"))
    functions = [n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_tracked_files"]
    assert len(functions) == 1, f"{script.name}: expected one `_tracked_files`"
    attributes = {n.attr for n in ast.walk(functions[0]) if isinstance(n, ast.Attribute)}
    assert "splitlines" not in attributes, (
        f"{script.name}: `_tracked_files` splits git output by lines again; a "
        f"filename holding a newline becomes two paths that name no file"
    )
