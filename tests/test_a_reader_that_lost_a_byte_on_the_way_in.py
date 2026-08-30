#!/usr/bin/env python3
"""Git path readers that ran through subprocess TEXT MODE, so a CR never arrived.

MEASURED 2026-08-30 on a scratch ext4 repository with git 2.43.0. Any `subprocess`
text mode turns on universal newlines, which rewrites every CR byte to LF, and
`subprocess` exposes no `newline=` knob to switch it off. Naming an `encoding=`
does not help - it is the same translation either way:

    ls-files -z bytes : b'docs/leak\\r\\nd.md\\x00docs/leak\\rc.md\\x00'
    encoding=utf-8    : 'docs/leak\\nd.md\\x00docs/leak\\nc.md\\x00'
    text=True         : 'docs/leak\\nd.md\\x00docs/leak\\nc.md\\x00'

Reproduced here on two files whose names differ only by that byte, `docs/x\\r\\ny.md`
and `docs/x\\ny.md`: bytes mode returns two names, text mode returns ONE. The CRLF
file disappears from the reader's view entirely.

This is a SEPARATE defect from the C-quoting one. `-z` fixes quoting; it does
nothing about CR translation, so a reader can carry `-z` and still be wrong. The
consequence in `scripts/utils/engine_guard.py` - the unbypassable push wall - is
that `repo_carried_paths` hands `engine_text_files` a path whose `is_file()` is
False, so the file receives NO content scan and the wall reports clean over a file
it never opened.

The only correct form is bytes plus a deliberate decode: no `text=`/`encoding=` on
the `subprocess` call, then `.stdout.decode("utf-8", "surrogateescape")`. That is
the shape in `scripts/build_engine_repo.py` and `scripts/build_data_repo.py`.

Run:
    .venv/bin/python -m pytest tests/test_a_reader_that_lost_a_byte_on_the_way_in.py -q
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPTS = ROOT / "scripts"
TEXT_MODE_KWARGS = ("text", "encoding", "universal_newlines")

# Tokens that mean "this argv lists PATHS", so its output carries filename bytes.
PATH_SUBCOMMANDS = (
    "ls-files", "ls-tree", "diff-tree", "--name-only", "--name-status",
    "--porcelain",
)

# Readers this change is FORBIDDEN to touch, with the verdict measured for each.
# The assertion below is equality, not containment: fixing one of these fails
# this test with an instruction to delete its row, and a NEW bad reader fails it
# too. The list only ever shrinks.
KNOWN_UNFIXED = {
    # NOT an unfixed reader. This is a limitation of the detector below, and the
    # row is kept so the detector stays strict rather than being taught to look
    # away.
    #
    # The row's original reason -- "`_changed_paths` runs `log -m -z
    # --name-only` through `_run`, which is `text=True`" -- was true when it was
    # written and stopped being true the same day: `_run` was moved to bytes
    # plus an explicit `.decode("utf-8", "surrogateescape")` on 2026-08-30. It
    # is rewritten rather than left standing, because a stale reason inside an
    # exception list is worse than no exception: the next sweep reads it,
    # believes the file is still broken, and either re-fixes what is fixed or
    # widens the list on a false premise.
    #
    # What the detector now reports here is `str .split(chr(0)) on a value never
    # decoded here` at lines 146 and 202. Both are correct as written: the
    # decode happens in `_run`, one call away, and the heuristic is
    # function-local by design. Teaching it to follow a module-local helper
    # would let a genuinely undecoded value through any file that happens to own
    # a helper of the right shape, which is the larger risk. Verified 2026-08-30
    # by reading both call sites: each splits the return value of `_run`.
    "scripts/utils/commit_source.py",
}


# ---------------------------------------------------------------------------
# The platform behaviour the fix exists for
# ---------------------------------------------------------------------------

def _scratch_repo(base: Path, names: list[str]) -> Path:
    repo = base / "repo"
    repo.mkdir()

    def git(*args, **kw):
        return subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, check=True, **kw)

    git("init", "-q", ".")
    git("config", "user.email", "probe@example.invalid")
    git("config", "user.name", "probe")
    made = []
    for rel in names:
        target = repo / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"marker\n")
        except OSError:
            continue
        made.append(rel)
    if not made:
        pytest.skip("no exotic filename is creatable on this filesystem")
    git("add", "-A")
    git("commit", "-q", "-m", "fixtures")
    return repo


def test_text_mode_collapses_two_distinct_filenames_into_one(tmp_path):
    """The measurement. If this ever goes green-by-platform, the fix is moot."""
    repo = _scratch_repo(tmp_path, ["docs/x\r\ny.md", "docs/x\ny.md"])
    argv = ["git", "-C", str(repo), "ls-files", "-z"]

    raw = subprocess.run(argv, capture_output=True, check=True).stdout
    as_bytes = {n for n in raw.split(b"\0") if n}
    as_text = {
        n for n in subprocess.run(
            argv, capture_output=True, check=True,
            text=True, encoding="utf-8", errors="surrogateescape",
        ).stdout.split("\0") if n
    }

    assert len(as_bytes) == 2, as_bytes
    assert len(as_text) == 1, (
        "subprocess text mode no longer rewrites CR to LF; re-read this module's "
        "docstring, the whole fix may be unnecessary")
    # And the deliberate decode preserves both, which is the property every fixed
    # reader now relies on.
    decoded = {n for n in raw.decode("utf-8", "surrogateescape").split("\0") if n}
    assert decoded == {n.decode("utf-8", "surrogateescape") for n in as_bytes}


def test_a_carriage_return_reaches_the_pipe_only_under_dash_z(tmp_path):
    """Why the non `-z` porcelain readers are genuinely unaffected.

    Without `-z` git C-escapes a control character, so no raw CR byte ever
    reaches the pipe and universal newlines has nothing to translate. This is the
    discriminator the sweep used to leave `emergency-revoke.py`,
    `provision-exec.py`, `publish-marketplace.py` and `apply-wizard-answers.py`
    alone; if it stops holding, those readers need re-examining.
    """
    repo = _scratch_repo(tmp_path, ["docs/keep.md"])
    (repo / "docs" / "untracked\rz.md").write_bytes(b"x\n")

    plain = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                           capture_output=True, check=True).stdout
    zeroed = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "-z"],
                            capture_output=True, check=True).stdout

    assert b"\r" not in plain, plain
    assert b"\r" in zeroed, zeroed


# ---------------------------------------------------------------------------
# The repo-wide guard
# ---------------------------------------------------------------------------

def _python_sources() -> list[Path]:
    return sorted(p for p in SCRIPTS.rglob("*.py") if "__pycache__" not in p.parts)


def _is_nul_string_split(node: ast.AST) -> bool:
    """True for `<something>.split("\\0")` on a STR (bytes splits are correct)."""
    if not isinstance(node, ast.Call):
        return False
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == "split"):
        return False
    if len(node.args) != 1 or not isinstance(node.args[0], ast.Constant):
        return False
    return node.args[0].value == "\0"


def _decoded_names(scope: ast.AST) -> set[str]:
    """Names bound anywhere in `scope` to an expression that decodes bytes."""
    names: set[str] = set()
    for node in ast.walk(scope):
        targets = []
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value:
            targets, value = [node.target], node.value
        else:
            continue
        if ".decode(" not in ast.unparse(value):
            continue
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
    return names


def _scopes(tree: ast.Module):
    """(node, label) for the module and every function in it."""
    yield tree, "<module>"
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node, node.name


def _nul_split_offenders(tree: ast.Module) -> list[int]:
    """Line numbers of every str NUL split whose value was not decoded here."""
    bad: list[int] = []
    for scope, _label in _scopes(tree):
        decoded = _decoded_names(scope)
        for node in ast.walk(scope):
            if not _is_nul_string_split(node):
                continue
            receiver = node.func.value
            if ".decode(" in ast.unparse(receiver):
                continue
            root = receiver
            while isinstance(root, (ast.Attribute, ast.Subscript)):
                root = root.value
            if isinstance(root, ast.Name) and root.id in decoded:
                continue
            bad.append(node.lineno)
    return sorted(set(bad))


def _argv_strings(node: ast.AST) -> list[str]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    out = []
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            out.append(element.value)
        elif isinstance(element, ast.Starred):
            out.append("*")
    return out


def _text_mode_dash_z_offenders(tree: ast.Module) -> list[int]:
    """`subprocess.run/Popen` with a literal `-z` git argv AND a text-mode kwarg."""
    bad: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = ast.unparse(node.func)
        if func not in ("subprocess.run", "subprocess.Popen", "run", "Popen"):
            continue
        if not node.args:
            continue
        argv = _argv_strings(node.args[0])
        if "-z" not in argv:
            continue
        if not any(token in argv for token in PATH_SUBCOMMANDS):
            continue
        if any(kw.arg in TEXT_MODE_KWARGS for kw in node.keywords if kw.arg):
            bad.append(node.lineno)
    return sorted(set(bad))


def _offenders() -> dict[str, list[str]]:
    """{relative path: [reason, ...]} across every script."""
    found: dict[str, list[str]] = {}
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        reasons = [f"line {n}: str .split(chr(0)) on a value never decoded here"
                   for n in _nul_split_offenders(tree)]
        reasons += [f"line {n}: git -z argv run in subprocess text mode"
                    for n in _text_mode_dash_z_offenders(tree)]
        if reasons:
            found[str(path.relative_to(ROOT))] = reasons
    return found


def test_the_guard_inspects_a_nonempty_corpus():
    """A guard that walks nothing passes everything."""
    sources = _python_sources()
    assert len(sources) > 100, len(sources)
    splits = sum(len(_nul_split_offenders(ast.parse(p.read_text(encoding="utf-8"))))
                 + p.read_text(encoding="utf-8").count('.split("\\0")')
                 + p.read_text(encoding="utf-8").count('.split(b"\\0")')
                 for p in sources)
    assert splits >= 15, f"the NUL-split corpus shrank to {splits}; re-check the guard"


def test_the_guard_flags_a_synthetic_bad_reader(tmp_path):
    """Both halves of the detector must be able to say no."""
    nul_split = ast.parse(
        "import subprocess\n"
        "def read():\n"
        "    out = subprocess.run(['git', 'ls-files', '-z'], capture_output=True,\n"
        "                         text=True)\n"
        "    return [p for p in out.stdout.split('\\0') if p]\n"
    )
    assert _nul_split_offenders(nul_split) == [5]
    assert _text_mode_dash_z_offenders(nul_split) == [3]

    good = ast.parse(
        "import subprocess\n"
        "def read():\n"
        "    out = subprocess.run(['git', 'ls-files', '-z'],\n"
        "                         capture_output=True).stdout.decode('utf-8', 'surrogateescape')\n"
        "    return [p for p in out.split('\\0') if p]\n"
    )
    assert _nul_split_offenders(good) == []
    assert _text_mode_dash_z_offenders(good) == []

    inline = ast.parse(
        "import subprocess\n"
        "def read(p):\n"
        "    return p.stdout.decode('utf-8', 'surrogateescape').split('\\0')\n"
    )
    assert _nul_split_offenders(inline) == []


def test_no_git_path_reader_runs_in_subprocess_text_mode():
    offenders = _offenders()
    unexpected = {k: v for k, v in offenders.items() if k not in KNOWN_UNFIXED}
    assert not unexpected, (
        "these readers translate CR to LF on the way in:\n"
        + "\n".join(f"  {k}\n    " + "\n    ".join(v) for k, v in unexpected.items()))

    stale = KNOWN_UNFIXED - set(offenders)
    assert not stale, (
        f"{sorted(stale)} no longer offends; delete the row from KNOWN_UNFIXED")


# ---------------------------------------------------------------------------
# The push wall, both directions
# ---------------------------------------------------------------------------

def test_repo_carried_paths_returns_the_bytes_git_gave_it(tmp_path):
    from scripts.utils.engine_guard import repo_carried_paths

    names = ["docs/leak\rc.md", "docs/leak\r\nd.md", "docs/leak\ne.md",
             "docs/leak\tf.md", "docs/план.md", "docs/leak g.md",
             'docs/leak"h.md', "docs/leak\\i.md", "docs/leak\x0bj.md"]
    repo = _scratch_repo(tmp_path, names)

    carried = set(repo_carried_paths(repo))
    on_disk = {rel for rel in names if (repo / rel).is_file()}
    missing = on_disk - carried
    assert not missing, f"the wall cannot see {sorted(missing)!r}"


def test_the_content_leg_still_opens_a_carriage_return_file(tmp_path):
    """`engine_text_files` must resolve every carried path to a real file.

    This is the leak: a mistranslated name fails `is_file()`, the file is dropped
    from the content scan, and the wall reports clean over bytes it never read.
    """
    from scripts.utils.engine_guard import engine_text_files, repo_carried_paths

    repo = _scratch_repo(tmp_path, ["docs/leak\rc.md", "docs/leak\r\nd.md",
                                    "docs/plain.md"])
    carried = repo_carried_paths(repo)
    scanned = engine_text_files(repo, carried)

    for rel in ("docs/leak\rc.md", "docs/leak\r\nd.md", "docs/plain.md"):
        assert rel in scanned, f"{rel!r} was never opened by the content leg"


def test_the_wall_still_refuses_a_planted_violation(tmp_path):
    """Nothing above may weaken the refusal. Every plant is under tmp_path."""
    from scripts.utils.engine_guard import scan_engine_repo

    plants = ["crm/contacts/leak\rc.md", "crm/contacts/leak\r\nd.md",
              "crm/contacts/plain.md", "outputs/report\rx.md"]
    repo = _scratch_repo(tmp_path, plants + ["scripts/fine.py", "docs/ok.md"])

    flagged = set(scan_engine_repo(repo))
    for rel in plants:
        assert rel in flagged, f"the wall did not refuse {rel!r}"
    assert "scripts/fine.py" not in flagged
    assert "docs/ok.md" not in flagged


def test_the_demo_manifest_leg_still_refuses(tmp_path):
    from scripts.utils.engine_guard import scan_engine_repo

    repo = _scratch_repo(tmp_path, ["examples/README.md", "examples/sneak\rx.md"])
    flagged = set(scan_engine_repo(repo))
    assert "examples/sneak\rx.md" in flagged
    assert "examples/README.md" not in flagged


# ---------------------------------------------------------------------------
# CI: the leak guard is fed NUL-separated
# ---------------------------------------------------------------------------

def test_ci_feeds_the_leak_guard_nul_separated_paths():
    """`git ls-files | xargs` word-splits, so a quoted or space-bearing path
    names no file and is silently skipped by the CI leak guard."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    steps = [ln for ln in ci.splitlines()
             if "leak-guard.py check-paths" in ln and "ls-files" in ln]
    assert steps, "the CI leak-guard step disappeared; re-point this test"
    for step in steps:
        assert "ls-files -z" in step, step
        assert "xargs -0" in step, step
