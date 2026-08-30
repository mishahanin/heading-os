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

The fix is one implementation, `scripts/utils/repo_files.py`, which asks
`git check-ignore` once per sweep. `tests/repo_files.py` is a thin re-export of
it, kept so the existing `from tests.repo_files import tracked_paths` imports
still resolve. The implementation MOVED there because production code could not
import out of `tests/`, and grew a second copy instead; the `owner` constant
further down and `test_the_batch_rule_reaches_production_code_too` both name the
new location. This paragraph still said `tests/repo_files.py` was the one
implementation until 2026-08-30, which sent a reader to the wrong file to fix
the filter -- the dated-figure staleness this suite's own guards exist to catch.

This file holds two things: that the helper actually excludes an ignored path
(proved against a real git repository built in a temp directory, not asserted),
and that no test goes back to walking the exposed roots by hand.

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


# Walkers that take the directory as an ARGUMENT rather than as a receiver.
# `os.walk(ROOT)` unparses its receiver to `os`, which names no root, so the
# rule examined the call and then cleared it -- one of the two spellings of the
# incident shape that stayed invisible until 2026-08-30.
ARG_WALKERS = ("walk", "fwalk", "scandir", "listdir")

# `Path(ROOT)` is the same base as `ROOT`. Written that way, `names_root` was
# True but `".claude" in receiver` was False, so `Path(ROOT).rglob("**/*.py")`
# passed the rule -- the other invisible spelling.
_PATH_WRAPPERS = ("Path", "pathlib.Path")


def _unwrap_receiver(receiver: str) -> str:
    """`Path(ROOT)` -> `ROOT`. Anything else is returned unchanged."""
    for wrapper in _PATH_WRAPPERS:
        if receiver.startswith(wrapper + "(") and receiver.endswith(")"):
            return receiver[len(wrapper) + 1:-1].strip()
    return receiver


def _is_exposed_base(receiver: str) -> bool:
    """Does this base resolve to the repo root, or to `.claude` in it?

    Applied to a walk's RECEIVER and, for `ARG_WALKERS`, to its first argument.
    """
    receiver = _unwrap_receiver(receiver.strip())
    if receiver in EXPOSED_ROOT_NAMES:
        return True
    names_root = any(tok in receiver for tok in EXPOSED_ROOT_NAMES)
    return names_root and (".claude" in receiver)


def _is_narrow_literal_glob(call: ast.Call) -> bool:
    """A one-level `glob` for a literal name, which is not a corpus sweep.

    The defect this rule exists for is a walk that COLLECTS the tree and holds
    the result against a registry, because a worktree under `.claude/worktrees/`
    doubles that corpus. `ROOT.glob("_content_guard_probe*")` does something
    else: it names one specific thing among the root's direct children and
    asserts there is none of it.

    Routing that through `tracked_paths` would BREAK it. Litter is untracked by
    definition, and often gitignored, so a git-aware filter would hide exactly
    what the assertion exists to find. Added 2026-08-29 after this rule flagged
    such an assertion and the only "fix" available was one that defeats it.

    Narrow means all three: `glob` (not `rglob`, `iterdir` or `walk`), a literal
    pattern, and no `**`. A leading `*` is still a sweep -- `ROOT.glob("*.py")`
    collects a corpus -- so it is not narrow.
    """
    func = call.func
    if getattr(func, "attr", None) != "glob" or len(call.args) != 1:
        return False
    arg = call.args[0]
    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
        return False
    pattern = arg.value
    return "**" not in pattern and "/" not in pattern and not pattern.startswith("*")


def exposed_walks(source: str) -> list[tuple[int, str]]:
    """(line, unparsed call) for every walk of the repo root or `.claude`.

    WIDENED 2026-08-30. The rule inspected `func.value` -- the receiver -- and
    nothing else, so two ordinary spellings of the exact incident shape passed
    while the docstring below promised "NO allow-list on purpose":

      os.walk(ROOT)              the receiver is `os`; the root is an ARGUMENT
      Path(ROOT).rglob("**/*.py")  the receiver is `Path(ROOT)`, not `ROOT`

    A seventeenth sweep written either way kept the whole rule green while a
    worktree under `.claude/worktrees/` doubled its corpus. Both the receiver
    and, for `ARG_WALKERS`, the first argument are now checked. Measured
    2026-08-30 over the tracked `tests/**/*.py` corpus: 0 sites newly flagged,
    so the widening reports no pre-existing violation and cannot be mistaken
    for one.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name not in set(WALK_METHODS) | set(ARG_WALKERS):
            continue
        if _is_narrow_literal_glob(node):
            continue
        bases: list[str] = []
        if isinstance(func, ast.Attribute):
            bases.append(ast.unparse(func.value).strip())
        if name in ARG_WALKERS and node.args:
            bases.append(ast.unparse(node.args[0]).strip())
        if any(_is_exposed_base(base) for base in bases):
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


_ARG_WALKER_FIXTURE = '''
import os
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

def sweep():
    return list(os.walk(ROOT)) + list(Path(ROOT).rglob("*.py"))
'''

_ARG_WALKER_HARMLESS_FIXTURE = '''
import os
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

def sweep(tmp_path):
    return list(os.walk(SCRIPTS)) + list(Path(tmp_path).rglob("*.py"))
'''


def test_the_rule_fires_on_the_root_passed_as_an_argument():
    """`os.walk(ROOT)` and `Path(ROOT).rglob(...)`. NEW 2026-08-30.

    Both were invisible: the first because the receiver is `os`, the second
    because the receiver is `Path(ROOT)` and the old predicate demanded the
    literal string `.claude` alongside the root name. Without this case the
    widening is a change nothing measures.
    """
    assert [c for _, c in exposed_walks(_ARG_WALKER_FIXTURE)] == [
        "os.walk(ROOT)", "Path(ROOT).rglob('*.py')"]


def test_the_widened_rule_still_leaves_a_subtree_and_a_tmp_path_alone():
    """The other jaw. A rule that flagged every `os.walk` would be turned off,
    which is the failure mode the narrowness of this rule exists to avoid."""
    assert exposed_walks(_ARG_WALKER_HARMLESS_FIXTURE) == []


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

    READS THE CODE, NOT THE CHARACTERS. This was `"check-ignore" in text and
    "--stdin" in text`, and on 2026-08-29 that flagged two modules whose only
    sin was DESCRIBING the command in a docstring, plus a test file whose
    parametrised fixtures quote it as example source. A rule with false
    positives gets suppressed, and it takes the true positive with it. The AST
    form asks whether an argument list actually carries both words.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:  # pragma: no cover - another test's job
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for arg in node.args:
            if not isinstance(arg, (ast.List, ast.Tuple)):
                continue
            words = [e.value for e in arg.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if "check-ignore" in words and "--stdin" in words:
                return True
    return False


def test_the_predicate_sees_a_batch_filter():
    assert spells_a_batch_filter(
        'subprocess.run(["git", "check-ignore", "--stdin", "-z"], input=payload)')


def test_the_predicate_sees_a_batch_filter_carrying_a_repo_flag():
    assert spells_a_batch_filter(
        'run(["git", "-C", str(repo), "check-ignore", "--stdin", "-z"])')


def test_the_predicate_leaves_a_single_path_question_alone():
    """Eight modules ask git whether ONE named path is ignored. That is a
    different question and must not be flagged."""
    assert not spells_a_batch_filter(
        'subprocess.run(["git", "check-ignore", "-q", str(path)])')


def test_the_predicate_leaves_prose_about_the_command_alone():
    assert not spells_a_batch_filter("# `git check-ignore` answers about a path")


def test_the_predicate_leaves_a_quoted_example_alone():
    """The false positive that forced the AST rewrite: a test whose fixtures
    quote the command as example source is not running it."""
    assert not spells_a_batch_filter(
        'CASES = [(\'run(["git", "check-ignore", "--stdin"])\', True)]')


def test_the_predicate_leaves_another_git_command_alone():
    assert not spells_a_batch_filter('subprocess.run(["git", "ls-files", "-z"])')


def _batch_rule_corpus() -> list[Path]:
    """Every module the one-implementation rule inspects: tests AND scripts.

    A function rather than an expression inlined in the rule, so the scope test
    below can ask the rule what it sweeps instead of agreeing with it by
    coincidence. Mutation found the difference: narrowing the rule back to
    `tests/**` changed no test result while a separate list said `scripts/` was
    covered.
    """
    return _test_modules() + tracked_paths(("scripts/**/*.py",))


# `_is_narrow_literal_glob` on synthetic input, both directions. Without these
# the carve-out is only exercised through whatever the tree happens to contain,
# and two mutations widening it survived: accepting a leading `*`, and
# accepting `rglob`.

_NARROW = [
    "ROOT.glob('_content_guard_probe*')",
    "ROOT.glob('probe.md')",
    "(ROOT / '.claude').glob('settings.local.json')",
]

_NOT_NARROW = [
    "ROOT.glob('*.py')",                  # a leading star IS a corpus sweep
    "ROOT.glob('scripts/**/*.py')",       # recursive
    "ROOT.glob('scripts/*.py')",          # reaches into a subtree
    "ROOT.rglob('*.py')",                 # rglob is recursive by definition
    # A narrow literal name is still recursive under rglob, so it DOES reach
    # a worktree copy. Mutation found this: with every other rglob fixture
    # excluded by its pattern anyway, deleting the `glob` check changed no
    # verdict and the guard was decorative.
    "ROOT.rglob('probe.md')",
    "ROOT.iterdir()",
    "ROOT.glob(pattern)",                 # not a literal, so unknowable
    "ROOT.glob('a', 'b')",                # not the one-argument form
]


@pytest.mark.parametrize("source", _NARROW)
def test_a_narrow_literal_glob_is_not_a_corpus_sweep(source):
    node = ast.parse(source).body[0].value
    assert _is_narrow_literal_glob(node) is True


@pytest.mark.parametrize("source", _NOT_NARROW)
def test_everything_wider_is_still_a_corpus_sweep(source):
    node = ast.parse(source).body[0].value
    assert _is_narrow_literal_glob(node) is False


def test_the_carve_out_separates_the_two_lists():
    """Both directions in one call, so a predicate ignoring its argument cannot
    satisfy the two parametrised suites separately."""
    yes = [s for s in _NARROW
           if _is_narrow_literal_glob(ast.parse(s).body[0].value)]
    no = [s for s in _NOT_NARROW
          if _is_narrow_literal_glob(ast.parse(s).body[0].value)]
    assert (yes, no) == (_NARROW, [])


def test_the_carve_out_reaches_the_rule_it_exempts():
    """The wiring. The predicate can be right and unreached: `exposed_walks` is
    what must honour it."""
    assert exposed_walks("ROOT.glob('_content_guard_probe*')") == []
    assert [c for _, c in exposed_walks("ROOT.glob('*.py')")] == ["ROOT.glob('*.py')"]


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
    owner = "scripts/utils/repo_files.py"
    holders = []
    for path in _batch_rule_corpus():
        rel = path.relative_to(ROOT).as_posix()
        if rel in (owner, Path(__file__).relative_to(ROOT).as_posix()):
            continue
        if spells_a_batch_filter(path.read_text(encoding="utf-8", errors="ignore")):
            holders.append(rel)
    assert not holders, (
        f"these batch-filter through `git check-ignore --stdin` themselves "
        f"instead of calling {owner}: {holders}")


def test_the_batch_rule_reaches_production_code_too():
    """The scope this rule did NOT have until 2026-08-29.

    It swept `tests/**` only, and the whole reason the implementation moved out
    of `tests/repo_files.py` is that production could not import it and grew its
    own copy. `scripts/check-path-references.py` held one for weeks, with the
    OPPOSITE contract on git failure, and this rule could not see it.

    Asks `_batch_rule_corpus`, the function the rule itself calls. Building its
    own list here is what let a mutation narrowing the rule back to `tests/**`
    survive: the test agreed with the old scope and never consulted the new one.
    """
    rels = {p.relative_to(ROOT).as_posix() for p in _batch_rule_corpus()}
    assert "scripts/check-path-references.py" in rels
    assert "scripts/utils/repo_files.py" in rels
    assert any(r.startswith("tests/") for r in rels)


def test_the_owner_still_spells_the_batch_filter():
    """The exemption must be earned. If the shared module stopped batching, the
    sweep above would run over an empty corpus and the rule would silently stop
    meaning anything."""
    owner = ROOT / "scripts" / "utils" / "repo_files.py"
    assert spells_a_batch_filter(owner.read_text(encoding="utf-8"))
