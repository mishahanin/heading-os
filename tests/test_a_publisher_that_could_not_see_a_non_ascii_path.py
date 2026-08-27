#!/usr/bin/env python3
"""The corporate publisher classified quoted paths, so non-ASCII files vanished.

`git ls-files` C-quotes any path with a byte outside printable ASCII, because
`core.quotePath` defaults to on. A Cyrillic filename comes back as
`"datastore/\\320\\261\\321\\200\\320\\265\\320\\275\\320\\264/x.md"`, quotes and
octal escapes included. `scripts/publish-corporate.py` fed that string straight
to `get_routing_destination`, where every rule key missed it and the map's
default answered `engine`.

Measured against the live data overlay on 2026-08-27: 8294 tracked files, 66 of
them C-quoted. Resolved from their real names, 65 route `private` and one routes
`corporate`. That corporate file has never been published to any executive, and
nothing reported a skip, because a path the classifier cannot read is not an
error to the classifier.

The engine default is what kept this from being a leak rather than an omission.
That is luck, not a control.

Three sibling tools already got this right: `scripts/push-all.py` uses
`git ls-files -z`, and `build_engine_repo.py` and `build_data_repo.py` pass
`-c core.quotepath=false`. This publisher was the last one reading the quoted
form, and no test caught it because every existing test monkeypatches both
enumerators away.

Found by the engine defect hunt, 2026-08-27.
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "publish-corporate.py"

# A Cyrillic directory and filename: every byte outside printable ASCII, which is
# exactly what git quotes.
CYRILLIC_REL = "datastore/бренд/заметка.md"
ASCII_REL = "datastore/brand/note.md"


@pytest.fixture(scope="module")
def publisher():
    spec = importlib.util.spec_from_file_location("publish_corporate_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["publish_corporate_under_test"] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def source_repo(tmp_path, publisher, monkeypatch):
    """A git repo whose quotePath setting is git's DEFAULT, not one we chose.

    Setting `core.quotepath=false` in the fixture would test a repo nobody has.
    The defect only exists because the default is on, so the fixture leaves it
    alone.
    """
    repo = tmp_path / "data"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    monkeypatch.setattr(publisher, "SOURCE_ROOT", repo)
    return repo


def _add(repo: Path, rel: str, body: str = "x\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _commit(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")


# ============================================================
# The enumerator returns real paths, not quoted ones
# ============================================================

def test_a_cyrillic_path_is_returned_unquoted(publisher, source_repo):
    _add(source_repo, CYRILLIC_REL)
    _commit(source_repo)
    tracked = publisher.list_tracked_files()
    assert CYRILLIC_REL in tracked, (
        f"the enumerator returned the quoted form: {tracked}"
    )


def test_every_returned_path_exists_on_disk(publisher, source_repo):
    """A quoted path is not a path: nothing opens it, and the copy step is next."""
    _add(source_repo, CYRILLIC_REL)
    _add(source_repo, ASCII_REL)
    _commit(source_repo)
    for rel in publisher.list_tracked_files():
        assert (source_repo / rel).is_file(), f"not a real path: {rel!r}"


def test_a_cyrillic_path_classifies_corporate_not_engine(publisher, source_repo):
    """The consequence, stated as the publisher sees it.

    `datastore/` routes corporate. The quoted form matches no rule key and falls
    to the map's `engine` default, so the file is silently never published.
    """
    from scripts.utils.workspace import get_routing_destination

    _add(source_repo, CYRILLIC_REL)
    _commit(source_repo)
    destinations = {rel: get_routing_destination(rel)
                    for rel in publisher.list_tracked_files()}
    assert destinations.get(CYRILLIC_REL) == "corporate", destinations


def test_an_ascii_path_is_unaffected(publisher, source_repo):
    _add(source_repo, ASCII_REL)
    _commit(source_repo)
    assert publisher.list_tracked_files() == [ASCII_REL]


def test_a_path_holding_a_newline_is_returned_whole(publisher, source_repo):
    """`splitlines()` cuts such a path in two, and both halves are nonsense.

    This is the reason to read the NUL-separated form rather than only turning
    quoting off.
    """
    rel = "datastore/brand/two\nlines.md"
    _add(source_repo, rel)
    _commit(source_repo)
    assert publisher.list_tracked_files() == [rel]


def test_no_blank_entry_survives_the_split(publisher, source_repo):
    """A NUL-terminated list ends with a trailing separator; the empty tail must go."""
    _add(source_repo, ASCII_REL)
    _commit(source_repo)
    assert all(rel.strip() for rel in publisher.list_tracked_files())


def test_an_empty_repo_returns_nothing(publisher, source_repo):
    assert publisher.list_tracked_files() == []


# ============================================================
# The untracked enumerator has the same mouth
# ============================================================

def test_an_untracked_cyrillic_corporate_file_is_found(publisher, source_repo):
    """This one is the safety net, so a blind spot here is a blind spot twice."""
    _add(source_repo, CYRILLIC_REL)
    assert publisher.list_untracked_corporate_files() == [CYRILLIC_REL]


def test_an_untracked_private_file_is_not_reported(publisher, source_repo):
    _add(source_repo, "outputs/приватное.md")
    assert publisher.list_untracked_corporate_files() == []


def test_a_committed_file_is_not_reported_as_untracked(publisher, source_repo):
    _add(source_repo, CYRILLIC_REL)
    _commit(source_repo)
    assert publisher.list_untracked_corporate_files() == []


# ============================================================
# Pinned against a rewrite that drops the flag
# ============================================================

def test_both_enumerators_share_one_reader() -> None:
    """Two copies of the same command is how one of them stops being fixed.

    The tracked and untracked enumerators each carried their own `subprocess.run`
    with its own splitting. Fixing one and missing the other is the failure this
    file exists to describe, so the shape is pinned rather than left to care.
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    functions = {node.name: node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)}
    for name in ("list_tracked_files", "list_untracked_corporate_files"):
        body = functions[name]
        called = {n.func.id for n in ast.walk(body)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "_ls_files" in called, (
            f"{name} does not go through the shared reader; it has its own"
        )


def test_no_ls_files_call_in_the_publisher_omits_the_nul_separator() -> None:
    """Asked of the syntax tree, not of a grep over the text.

    A grep also matches the comment and the docstring above, and a comment is
    what promised correctness here for eleven weeks.
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.List):
            continue
        argv = [el.value for el in first.elts if isinstance(el, ast.Constant)]
        if argv[:2] != ["git", "ls-files"]:
            continue
        calls += 1
        assert "-z" in argv, (
            f"line {node.lineno}: `git ls-files` without -z; git C-quotes any "
            f"non-ASCII path and the classifier cannot read the quoted form"
        )
    assert calls >= 1, (
        "no `git ls-files` call found at all: this test can no longer see what "
        "it claims to check"
    )


# ============================================================
# The class, not just this instance
# ============================================================

def _enumerating_git_calls(tree: ast.AST):
    """Every literal argv in a tree that asks git to LIST paths.

    `--error-unmatch` is excluded: that form names one path and answers with an
    exit code, so quoting cannot drop anything.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.List):
            continue
        argv = [el.value for el in first.elts
                if isinstance(el, ast.Constant) and isinstance(el.value, str)]
        if argv and argv[0] == "git":
            argv = argv[1:]
        if not argv:
            continue
        if "--error-unmatch" in argv:
            continue
        listing = argv[0] == "ls-files" or (
            argv[0] == "diff" and "--name-only" in argv)
        if listing:
            yield node.lineno, argv


SEARCH_ROOTS = ("scripts", "tests", ".claude/hooks")


def test_no_engine_reader_enumerates_paths_without_the_nul_separator() -> None:
    """The whole class, held down once.

    Four more readers carried this defect beside the publisher: turn-check,
    the routing gate, the trajectory reconciler, and three test guards. Two of
    them split on whitespace rather than newlines, which also breaks any path
    holding a space. Each silently dropped files while reporting a clean pass.
    """
    offenders = []
    for root in SEARCH_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            if ".venv" in path.parts or path.name == Path(__file__).name:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for lineno, argv in _enumerating_git_calls(tree):
                if "-z" not in argv:
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno} {argv}")
    assert not offenders, (
        "git C-quotes any non-ASCII path, so these readers drop files without "
        "saying so:\n  " + "\n  ".join(offenders)
    )
