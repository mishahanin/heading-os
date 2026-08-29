#!/usr/bin/env python3
"""A tree sweep that does not ask git sees the copies git is hiding.

Sixteen tests swept the repository root, or `.claude/` under it, with a plain
`rglob` and a hand-written skip list. `.gitignore` line 347 already covers
`.claude/worktrees/`, which is where an agent checks out a full second copy of
the tree, and none of the sixteen knew it.

MEASURED 2026-08-29 on the CI-shaped run (`env -u HEADING_OS_DATA -u
WORKSPACE_ROOT .venv/bin/python -m pytest tests/ -q -n auto`):

    without the worktree   15609 passed, 90 skipped, 0 failed
    with the worktree       8 failed, 15601 passed

`git worktree add -f --detach .claude/worktrees/agent-probe HEAD` was the only
change between the two runs. The eight were the registry sweeps -- every
frontmatter reader, every character fence, every provider endpoint, the slug
rule -- and each reported the COPY as a new undeclared site:

    new frontmatter regex disagreeing with the shared grammar:
      .claude/worktrees/agent-probe/scripts/merge-contacts.py on ['CRLF throughout']

The loud half sends the next reader after a file they cannot fix. The quiet half
is worse: while the copy is present every sweep runs over a doubled corpus, so a
real new defect arrives inside twice the noise, and the "the scan did not
collapse" floors that several of these tests carry stop meaning anything.

The fix is one implementation, `tests/repo_files.py`, which asks
`git check-ignore` once per sweep. This file holds two things: that the helper
actually excludes an ignored path (proved against a real git repository built in
a temp directory, not asserted), and that no test goes back to walking the
exposed roots by hand.

The rule below has NO allow-list on purpose. Sixteen sites migrated in one
change, so there is nothing to grandfather, and an empty exception list is the
only kind that cannot quietly grow.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import ignored_paths, tracked_paths  # noqa: E402


# ============================================================
# The helper really excludes what git ignores
# ============================================================

def _repo(tmp_path: Path) -> Path:
    """A real git repository with one ignored directory and one that is not."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / ".claude" / "worktrees" / "agent-probe" / "scripts").mkdir(parents=True)
    (repo / ".claude" / "hooks").mkdir(parents=True)

    (repo / ".gitignore").write_text(".claude/worktrees/\n", encoding="utf-8")
    (repo / "scripts" / "real.py").write_text("x = 1\n", encoding="utf-8")
    (repo / ".claude" / "hooks" / "real.py").write_text("y = 2\n", encoding="utf-8")
    (repo / ".claude" / "worktrees" / "agent-probe" / "scripts" / "real.py").write_text(
        "x = 1\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def test_the_fixture_really_hides_the_copy_from_git(tmp_path):
    """Anti-vacuity for every test below: if the fixture's ignore rule did not
    bite, an excluding helper and a blind one would agree and nothing here would
    measure anything."""
    repo = _repo(tmp_path)
    copy = repo / ".claude" / "worktrees" / "agent-probe" / "scripts" / "real.py"
    proc = subprocess.run(["git", "-C", str(repo), "check-ignore", str(copy)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, (
        "the fixture's .gitignore does not cover the worktree copy, so the "
        f"tests below prove nothing. check-ignore said: {proc.stderr!r}")


def test_a_blind_walk_finds_the_copy_and_the_helper_does_not(tmp_path):
    """The two halves of the defect, side by side, on the same tree."""
    repo = _repo(tmp_path)

    blind = sorted(p.relative_to(repo).as_posix() for p in repo.glob("**/*.py"))
    asked = sorted(p.relative_to(repo).as_posix()
                   for p in tracked_paths(("**/*.py",), repo))

    assert ".claude/worktrees/agent-probe/scripts/real.py" in blind, (
        "the blind walk did not even reach the copy; the fixture is wrong")
    assert ".claude/worktrees/agent-probe/scripts/real.py" not in asked
    assert asked == sorted([".claude/hooks/real.py", "scripts/real.py"]), asked


def test_the_helper_still_returns_the_files_that_are_not_ignored(tmp_path):
    """The mirror case. A helper that returned nothing would pass the test above
    and switch every sweep in the repository off."""
    repo = _repo(tmp_path)
    asked = {p.relative_to(repo).as_posix() for p in tracked_paths(("**/*.py",), repo)}
    assert asked == {"scripts/real.py", ".claude/hooks/real.py"}


def test_two_patterns_matching_one_file_report_it_once(tmp_path):
    """A sweep that counts its corpus must not double-count an overlap."""
    repo = _repo(tmp_path)
    got = tracked_paths(("scripts/*.py", "scripts/**/*.py"), repo)
    assert [p.name for p in got] == ["real.py"], got


def test_a_directory_is_not_reported_as_a_file(tmp_path):
    repo = _repo(tmp_path)
    got = tracked_paths(("scripts",), repo)
    assert got == [], got


def test_an_empty_pattern_list_asks_git_nothing(tmp_path):
    """`git check-ignore --stdin` with an empty payload exits 1 and would be
    read as "nothing is ignored" -- harmless here, but the call is skipped so a
    caller with no patterns cannot be charged for a subprocess."""
    repo = _repo(tmp_path)
    assert ignored_paths([], repo) == set()


def test_a_non_repository_raises_instead_of_reporting_nothing_ignored(tmp_path):
    """The degradation that would restore the defect.

    `git check-ignore` exits 128 outside a repository. Reading that as an empty
    ignore set is exactly the silent failure this module exists to prevent: every
    sweep would go back to seeing the copies, and nothing would say so."""
    plain = tmp_path / "not-a-repo"
    (plain / "scripts").mkdir(parents=True)
    (plain / "scripts" / "a.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="check-ignore failed"):
        tracked_paths(("scripts/*.py",), plain)


# ============================================================
# And no test walks the exposed roots by hand again
# ============================================================

WALK_METHODS = ("glob", "rglob", "iterdir", "walk")

# The two bases a gitignored copy of the tree can appear under. `.claude/` holds
# `worktrees/`; the root itself holds `.venv/`, `.tmp/` and `.worktrees/`. A walk
# rooted at `scripts/` cannot reach any of them, which is why this rule is
# narrow: a rule that flagged every walk in the suite would be turned off.
EXPOSED_ROOT_NAMES = ("ROOT", "_ROOT", "REPO_ROOT", "WORKSPACE_ROOT")


def _is_exposed_base(receiver: str) -> bool:
    """Does this walk receiver resolve to the repo root, or to `.claude` in it?"""
    if receiver in EXPOSED_ROOT_NAMES:
        return True
    names_root = any(tok in receiver for tok in EXPOSED_ROOT_NAMES)
    return names_root and (".claude" in receiver)


def exposed_walks(source: str) -> list[tuple[int, str]]:
    """(line, unparsed call) for every walk of the repo root or `.claude`."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        func = getattr(node, "func", None)
        if not (isinstance(node, ast.Call) and isinstance(func, ast.Attribute)
                and func.attr in WALK_METHODS):
            continue
        if _is_exposed_base(ast.unparse(func.value).strip()):
            found.append((node.lineno, ast.unparse(node)[:100]))
    return found


_DEFECT_FIXTURE = '''
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

def sweep():
    return sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md"))
'''

_FIXED_FIXTURE = '''
from pathlib import Path
from tests.repo_files import tracked_paths
ROOT = Path(__file__).resolve().parent.parent

def sweep():
    return tracked_paths((".claude/skills/*/SKILL.md",))
'''

_BARE_ROOT_FIXTURE = '''
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

def sweep(pattern):
    return sorted(ROOT.glob(pattern))
'''

_HARMLESS_FIXTURE = '''
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

def sweep(tmp_path):
    return sorted(SCRIPTS.rglob("*.py")) + sorted(tmp_path.rglob("*.md"))
'''


def test_the_rule_fires_on_the_shape_that_caused_the_incident():
    assert [c for _, c in exposed_walks(_DEFECT_FIXTURE)] == [
        "(ROOT / '.claude' / 'skills').glob('*/SKILL.md')"]


def test_the_rule_fires_on_a_walk_of_the_bare_repository_root():
    """The other exposed base, and the one the `.claude` clause cannot see.
    `ROOT.glob(pattern)` reaches `.tmp/`, `.venv/` and `.worktrees/` as well as
    `.claude/worktrees/`, and five of the sixteen migrated sites were written
    this way."""
    assert [c for _, c in exposed_walks(_BARE_ROOT_FIXTURE)] == ["ROOT.glob(pattern)"]


def test_the_rule_accepts_the_shape_that_replaced_it():
    assert exposed_walks(_FIXED_FIXTURE) == []


def test_the_rule_leaves_a_subtree_walk_and_a_tmp_path_walk_alone():
    """`scripts/` and a pytest `tmp_path` cannot hold a gitignored tree copy."""
    assert exposed_walks(_HARMLESS_FIXTURE) == []


def _test_modules() -> list[Path]:
    return tracked_paths(("tests/**/*.py",))


def test_the_sweep_reaches_the_real_test_corpus():
    """Green over an empty corpus otherwise. 500+ test modules on 2026-08-29."""
    modules = _test_modules()
    assert len(modules) > 300, f"only {len(modules)} test modules found"


def walk_violations(modules) -> list[str]:
    """`rel:line  call` for every hand walk in `modules`, a (rel, source) list.

    Extracted from the repository sweep below and unit-tested on synthetic
    input, because the sweep is green over an empty offender set: with the tree
    clean, deleting the line that COLLECTS a violation changes no result and the
    mutation survives. Measured 2026-08-29 -- `violations.extend([])` passed the
    whole suite.
    """
    out: list[str] = []
    for rel, source in modules:
        try:
            hits = exposed_walks(source)
        except SyntaxError:  # pragma: no cover - another test's job
            continue
        # Importing the helper does not excuse a hand walk beside it: a module
        # that asks git for one sweep and not for the next is the shape this
        # repository calls a fix that landed in one of two copies.
        out.extend(f"{rel}:{line}  {call}" for line, call in hits)
    return out


def test_the_collector_reports_a_synthetic_offender():
    """Anti-vacuity for the repository sweep, which has nothing to report today."""
    got = walk_violations([("tests/test_fake.py", _DEFECT_FIXTURE),
                           ("tests/test_clean.py", _FIXED_FIXTURE)])
    assert got == [
        "tests/test_fake.py:6  (ROOT / '.claude' / 'skills').glob('*/SKILL.md')"], got


def test_the_collector_reports_nothing_for_a_clean_module():
    assert walk_violations([("tests/test_clean.py", _FIXED_FIXTURE),
                            ("tests/test_ok.py", _HARMLESS_FIXTURE)]) == []


def test_the_collector_survives_a_module_it_cannot_parse():
    assert walk_violations([("tests/test_broken.py", "def (:\n")]) == []


def test_no_test_walks_the_repo_root_or_dot_claude_without_asking_git():
    """The rule that stops the seventeenth one.

    Use `tests.repo_files.tracked_paths(patterns)`; the patterns are relative to
    the repository root and `**` matches zero or more directories, so
    `scripts/**/*.py` covers `scripts/a.py` too.
    """
    modules = []
    for path in _test_modules():
        if path.name == Path(__file__).name:
            continue
        try:
            modules.append((path.relative_to(ROOT).as_posix(),
                            path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:  # pragma: no cover - another test's job
            continue
    violations = walk_violations(modules)

    assert not violations, (
        "these walk the repository root or .claude/ without asking git what it "
        "ignores, so an agent worktree under .claude/worktrees/ doubles their "
        "corpus. Route them through tests.repo_files.tracked_paths:\n  "
        + "\n  ".join(violations))


def spells_a_batch_filter(text: str) -> bool:
    """Does this module build its own `git check-ignore --stdin` batch filter?

    Extracted and unit-tested below for the same reason as `walk_violations`:
    with the only duplicate migrated, the repository sweep runs over an empty
    set and a predicate that always answered False survived the mutation run.
    """
    return "check-ignore" in text and "--stdin" in text


def test_the_predicate_sees_a_batch_filter():
    assert spells_a_batch_filter(
        'subprocess.run(["git", "check-ignore", "--stdin", "-z"], input=payload)')


def test_the_predicate_leaves_a_single_path_question_alone():
    """Six modules ask git whether ONE named path is ignored. That is a
    different question and must not be flagged."""
    assert not spells_a_batch_filter(
        'subprocess.run(["git", "check-ignore", "-q", str(path)])')


def test_the_predicate_leaves_prose_about_the_command_alone():
    assert not spells_a_batch_filter("# `git check-ignore` answers about a path")


def test_the_shared_helper_is_the_only_place_a_batch_filter_is_spelled():
    """A second implementation is a second place the semantics can drift, and
    the second copy is the one that stops being fixed. Three of them were
    written on 2026-08-29 alone, for the same incident, with three different
    contracts on git failure.

    Keyed on `check-ignore --stdin`, the BATCH form, and not on `check-ignore`
    at large. Six other modules ask git whether ONE named path is ignored -
    `.env`, a lock sidecar, a wizard fixture - and that is a different question
    with a different answer shape. A rule that flagged them too would be turned
    off, and the real duplicate would go with it.
    """
    owner = "tests/repo_files.py"
    holders = []
    for path in _test_modules():
        rel = path.relative_to(ROOT).as_posix()
        if rel in (owner, Path(__file__).relative_to(ROOT).as_posix()):
            continue
        if spells_a_batch_filter(path.read_text(encoding="utf-8", errors="ignore")):
            holders.append(rel)
    assert not holders, (
        f"these batch-filter through `git check-ignore --stdin` themselves "
        f"instead of calling {owner}: {holders}")
