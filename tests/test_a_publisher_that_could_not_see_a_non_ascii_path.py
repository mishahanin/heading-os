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
    monkeypatch.setattr(publisher, "source_root", lambda p=repo: p)
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

# Git global options that sit BEFORE the subcommand. `git -C <dir> ls-files` is
# an ordinary calling pattern in this codebase, and until 2026-08-30 the scanner
# read `argv[0]` straight after dropping the leading `git`, saw `-C`, and
# cleared the call. The listing subcommand was never reached.
_GLOBAL_OPTS_WITH_VALUE = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
})
_GLOBAL_FLAGS = frozenset({"-P", "--no-pager", "--literal-pathspecs",
                           "--no-optional-locks", "--bare"})


def _literal_argv(node: ast.Call) -> list[str] | None:
    """The literal argv this call passes to a subprocess, or None.

    Reads `args[0]` AND the `args=` keyword, and accepts a TUPLE as well as a
    list. All three were unreadable to the old scanner, which required an
    `ast.List` positionally -- so `subprocess.run(("git", "ls-files"))` and
    `subprocess.run(args=["git", "ls-files"])` were invisible.

    A sequence holding any non-constant element returns None rather than a
    partial list. The old version silently DROPPED such elements, which could
    turn `["git", flag, "ls-files"]` into a `["git", "ls-files"]` it then judged.
    """
    seq = None
    if node.args and isinstance(node.args[0], (ast.List, ast.Tuple)):
        seq = node.args[0]
    else:
        for kw in node.keywords:
            if kw.arg == "args" and isinstance(kw.value, (ast.List, ast.Tuple)):
                seq = kw.value
                break
    if seq is None:
        return None
    if not all(isinstance(el, ast.Constant) and isinstance(el.value, str)
               for el in seq.elts):
        return None
    return [el.value for el in seq.elts]


def _subcommand_argv(argv: list[str]) -> list[str]:
    """`argv` from the subcommand onward, with `git` and its globals removed."""
    if argv and argv[0] == "git":
        argv = argv[1:]
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in _GLOBAL_OPTS_WITH_VALUE:
            index += 2
        elif token in _GLOBAL_FLAGS or "=" in token and token.startswith("--"):
            index += 1
        else:
            break
    return argv[index:]


def _enumerating_git_calls(tree: ast.AST):
    """Every literal argv in a tree that asks git to LIST paths.

    `--error-unmatch` is excluded: that form names one path and answers with an
    exit code, so quoting cannot drop anything.

    WIDENED 2026-08-30. The docstring promised "every literal argv"; the
    implementation recognised a listing subcommand only when it was the FIRST
    token after `git`, only from a positional `ast.List`, and only for
    `ls-files` / `diff --name-only`. Global options, tuple argv, `args=` argv
    and `diff-tree --name-only` all walked past a scan that then reported clean.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        argv = _literal_argv(node)
        if not argv:
            continue
        sub = _subcommand_argv(argv)
        if not sub or "--error-unmatch" in sub:
            continue
        listing = sub[0] == "ls-files" or (
            sub[0] in ("diff", "diff-tree") and "--name-only" in sub)
        if listing:
            yield node.lineno, argv


def _quoting_is_disabled(argv: list[str]) -> bool:
    """Does this argv stop git from C-quoting the paths it prints?

    Two ways, and both are accepted because both close the hole this test is
    named for:

      -z                              NUL-terminated output is never quoted.
      -c core.quotepath=false         turns off the escaping of non-ASCII bytes.

    The second was added on 2026-08-30, when widening the scanner to see global
    options first surfaced `scripts/build_data_repo.py` and
    `scripts/build_engine_repo.py`. Those two are NOT instances of the defect
    this test names: the publisher dropped Cyrillic filenames because git
    escaped them, and `core.quotepath=false` is the documented switch for
    exactly that.

    RESIDUAL, recorded rather than silently accepted: `core.quotepath=false`
    does not stop git quoting a path that holds a control character, so a
    filename containing a newline still arrives quoted and `splitlines()` still
    splits it. `-z` is immune to both. Those two call sites live outside this
    audit package and were left alone; the gap is REPORTED, not fixed here.
    """
    if "-z" in argv:
        return True
    return any(tok.replace(" ", "") == "core.quotepath=false" for tok in argv)


SEARCH_ROOTS = ("scripts", "tests", ".claude/hooks")


@pytest.mark.parametrize("src,seen", [
    ('subprocess.run(["git", "ls-files"])', True),
    ('subprocess.run(["git", "-C", d, "ls-files"])', False),        # d is not literal
    ('subprocess.run(["git", "-C", "repo", "ls-files"])', True),
    ('subprocess.run(["git", "-c", "core.quotepath=false", "ls-files"])', True),
    ('subprocess.run(["git", "--git-dir", "g", "ls-files"])', True),
    ('subprocess.run(("git", "ls-files"))', True),
    ('subprocess.run(args=["git", "ls-files"])', True),
    ('subprocess.run(["git", "diff-tree", "--name-only", "HEAD"])', True),
    ('subprocess.run(["git", "-C", "repo", "status"])', False),
    ('subprocess.run(["git", "ls-files", "--error-unmatch", "a"])', False),
    ('subprocess.run(["ls", "-l"])', False),
])
def test_the_argv_scanner_sees_each_calling_form(src: str, seen: bool) -> None:
    """The negative case for the widening. NEW 2026-08-30.

    Five of these were invisible to the old scanner, and it had no unit test at
    all -- its only exercise was a sweep over a corpus with nothing to report,
    which is green whatever the detector does. Both directions are pinned: the
    listing forms must be found and the four non-listings must not.
    """
    hits = list(_enumerating_git_calls(ast.parse(src)))
    assert bool(hits) is seen, hits


def test_the_scanner_ignores_a_partly_dynamic_argv_rather_than_misreading_it() -> None:
    """A sequence holding a non-literal element is UNKNOWN, not a shorter argv.

    The old scanner dropped non-constant elements and judged what was left, so
    `["git", flag, "ls-files"]` read as `git ls-files` -- a verdict about an
    argv the code never runs.
    """
    assert list(_enumerating_git_calls(
        ast.parse('subprocess.run(["git", flag, "ls-files"])'))) == []


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
                if not _quoting_is_disabled(argv):
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno} {argv}")
    assert not offenders, (
        "git C-quotes any non-ASCII path, so these readers drop files without "
        "saying so:\n  " + "\n  ".join(offenders)
    )
