"""`scripts/utils/repo_files.git_index_paths` -- the one reader of git's index.

Two gates written the same afternoon, `check-gate-integrity.py` and
`audit-rotation.py`, each grew a private copy of "ask git what it tracks". Both
copies carried the same two defects, and the second one was written by someone
who had just fixed the first. That is this repository's dominant defect shape --
a fix that lands in one of N copies, 35 commits of the September 2026 campaign --
reproduced within an hour of writing the rule against it.

So there is one implementation, and this file holds it to what it promises. Each
case builds a REAL repository and puts a real pathological filename in it,
because the failures here are all about bytes and none of them reproduce on a
mock.

* A newline in a filename. Without `-z` git C-quotes the path, and a reader
  splitting on newlines turns one file into two fragments, neither of which
  exists.
* A carriage return in a filename. Text mode applies universal newlines and
  silently rewrites it to a line feed, producing a path that is not the file.
* A byte that is not valid UTF-8. Strict decoding raises on a tree git handles
  fine; `errors="replace"` corrupts the name into one that matches nothing.
* An empty listing. Reading it as "no paths to check" is how a scanner reports
  clean over a tree it never enumerated.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.repo_files import (  # noqa: E402
    IndexUnreadable,
    git_index_paths,
    read_sources,
    tracked_python_files,
)


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    # core.quotepath default is true, which is exactly the setting that makes a
    # non-ASCII path arrive C-quoted without -z. Left at its default on purpose.
    return tmp_path


def _add(repo: Path, name: bytes) -> None:
    (repo / os.fsdecode(name)).write_bytes(b"x")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)


def test_a_plain_repository_lists_its_files(tmp_path):
    """The anchor. Every pathological case below is worthless if the ordinary
    one does not work."""
    repo = _repo(tmp_path)
    _add(repo, b"ordinary.py")
    assert git_index_paths(repo) == ["ordinary.py"]


def test_a_filename_holding_a_newline_survives(tmp_path):
    """Without -z this arrives C-quoted and a newline split makes two fragments
    out of one file, so a sweep reports paths that do not exist and misses one
    that does."""
    repo = _repo(tmp_path)
    _add(repo, b"two\nlines.py")
    assert git_index_paths(repo) == ["two\nlines.py"]


def test_a_filename_holding_a_carriage_return_is_not_rewritten(tmp_path):
    """The defect `text=True` introduces. Universal newlines turn the CR into a
    LF on the way in, and the reader then names a file that is not there.

    MEASURED 2026-09-02: with `text=True`, this path came back as
    `carriage\\nreturn.py`. `tests/test_a_reader_that_lost_a_byte_on_the_way_in.py`
    caught the same shape in both gate scripts.
    """
    repo = _repo(tmp_path)
    _add(repo, b"carriage\rreturn.py")
    listed = git_index_paths(repo)
    assert listed == ["carriage\rreturn.py"]
    assert "\n" not in listed[0]


def test_a_filename_that_is_not_valid_utf8_survives_round_trip(tmp_path):
    """Strict decoding raises here and `replace` corrupts. surrogateescape is
    the only mode that returns a string `os.fsencode` turns back into the exact
    bytes git holds."""
    repo = _repo(tmp_path)
    raw = b"bad\xffbyte.py"
    _add(repo, raw)
    listed = git_index_paths(repo)
    assert len(listed) == 1
    assert os.fsencode(listed[0]) == raw


def test_a_non_ascii_filename_survives(tmp_path):
    repo = _repo(tmp_path)
    _add(repo, "отчёт.py".encode())
    assert git_index_paths(repo) == ["отчёт.py"]


def test_every_file_is_listed_once(tmp_path):
    """A splitter that kept the empty tail, or dropped a separator, changes the
    count without changing any single name, which is the quiet failure."""
    repo = _repo(tmp_path)
    for name in (b"a.py", b"b\nc.py", b"d\re.py", b"f\xffg.py"):
        _add(repo, name)
    assert len(git_index_paths(repo)) == 4


# ============================================================
# Refusals
# ============================================================

def test_an_empty_index_refuses(tmp_path):
    """Not "an empty repository". A listing that came back with nothing is a
    failure, and reading it as "no paths to check" is how a scanner reports
    clean over a tree it never enumerated."""
    repo = _repo(tmp_path)
    with pytest.raises(IndexUnreadable, match="returned nothing"):
        git_index_paths(repo)


def test_a_directory_that_is_not_a_repository_refuses(tmp_path):
    """The other branch, and it must not carry the empty-index wording, or the
    two states collapse into one message."""
    with pytest.raises(IndexUnreadable, match="git ls-files failed"):
        git_index_paths(tmp_path)


# ============================================================
# One implementation, and it stays one
# ============================================================

# Three call sites spawn a PLAIN full enumeration, which is exactly what
# `git_index_paths` replaces. They are pre-existing, each is recorded as an open
# finding in `config/audit-rotation-ledger.json` with an estimate, and each is
# frozen here with the reason rather than converted in the same change that
# wrote the rule. Converting a security-critical guard is its own piece of work
# with its own evidence.
DUPLICATE_ENUMERATIONS = {
    "scripts/overlay-writer-census.py": (
        "a plain -z enumeration, byte-correct today, and a second copy of the "
        "reader. Consolidation is the fix, not a bug fix."
    ),
    # scripts/publish-corporate.py is NOT here, and the omission is the
    # measurement rather than an oversight. Its argv is `["git", "ls-files",
    # "-z", *extra]`, so the narrowed rule reads it as the filtered question it
    # is and never flags it. Freezing it anyway would put an entry in this dict
    # that nothing fires on, which `test_every_frozen_duplicate_still_exists`
    # correctly refused within a minute of it being written. It is still worth
    # consolidating one day, and that lives in the rotation ledger as an open
    # finding, where work belongs.
    "scripts/utils/overlay_write_guard.py": (
        "a plain -z enumeration inside a security-critical guard, with its own "
        "timeout. Converting it needs the timeout preserved and the guard's own "
        "refusal cases re-measured."
    ),
}


def _plain_enumerations() -> list[str]:
    """`path:line` for every spawn of a PLAIN `git ls-files` over the whole index.

    Narrow on purpose, and the narrowing was measured. The first version flagged
    any `git ls-files` argv and named six sites, of which three ask a DIFFERENT
    question: `--error-unmatch <path>` is a membership test on one path,
    `--others --exclude-standard` lists untracked files, and a trailing pathspec
    is a filtered enumeration. None of those is what `git_index_paths` returns,
    and a rule that colours correct code as a defect is a rule people turn off.

    Asked of the AST, so a docstring may name the command it governs: a text
    scan would go red on this paragraph, which teaches people to delete it.
    """
    import ast

    # `read_sources` rather than a bare read: this sweep hunts offenders, and a
    # file created and deleted between the walk and the read cannot be one. The
    # pre-push gate produces exactly that window on its own, so a bare
    # `read_text` here reports a violation where nothing was violated.
    found = []
    for path, source in read_sources(tracked_python_files(), errors="replace"):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "scripts/utils/repo_files.py":
            continue  # the implementation itself
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"run", "check_output", "Popen"}
                    and node.args):
                continue
            argv = node.args[0]
            if not isinstance(argv, ast.List):
                continue
            words = [element.value for element in argv.elts
                     if isinstance(element, ast.Constant)]
            if len(words) != len(argv.elts):
                continue  # a computed element: a different, filtered question
            if words in (["git", "ls-files"], ["git", "ls-files", "-z"]):
                found.append(f"{rel}:{node.lineno}")
    return found


def test_no_new_module_copies_the_full_index_enumeration():
    """The copies are what this function exists to prevent, and it caught three
    the hour it was written."""
    offenders = sorted(site for site in _plain_enumerations()
                       if site.rsplit(":", 1)[0] not in DUPLICATE_ENUMERATIONS)
    assert offenders == [], (
        f"these modules spawn their own full `git ls-files` instead of calling "
        f"git_index_paths: {offenders}")


def test_every_frozen_duplicate_still_exists():
    """A frozen entry that no longer fires overstates the debt, and the next
    reader trusts the whole list less."""
    live = {site.rsplit(":", 1)[0] for site in _plain_enumerations()}
    stale = sorted(set(DUPLICATE_ENUMERATIONS) - live)
    assert stale == [], f"frozen duplicates that no longer fire: {stale}"


def test_the_two_new_gates_are_not_among_the_duplicates():
    """They were, an hour before this file existed. Both now call the shared
    reader, and that is the whole point of the exercise."""
    live = {site.rsplit(":", 1)[0] for site in _plain_enumerations()}
    assert "scripts/check-gate-integrity.py" not in live
    assert "scripts/audit-rotation.py" not in live


def test_the_sweep_above_reads_a_real_corpus():
    """A walk that collapsed to nothing would report no offenders over a tree it
    never opened, which is the finding this whole file descends from."""
    assert len(tracked_python_files()) >= 250, "the module sweep read too little"
