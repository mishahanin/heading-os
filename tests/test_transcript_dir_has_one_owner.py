"""The project-slug rule lives in ONE place, and it refuses off POSIX.

Found by the 2026-08-23 engine audit, which reported the Windows half. The
duplication underneath it is the reason the Windows half existed.

**The slug rule had three copies.** `scripts/utils/checkpoint_paths.py` owns
`transcript_dir(project)` and its docstring says why: "it lives here rather
than in either caller because the second copy of a path-mangling rule is the one
that stops being fixed." `scripts/archive-transcripts.py` then wrote a third
copy anyway, with a docstring pointing at a FOURTH place (`scripts/calibrate.py`)
as the authority. The prediction in the shared docstring came true inside its own
repository.

**Every copy was wrong on Windows.** The rule replaces `/` and `.` with `-`.
A Windows workspace path is `C:\\Users\\...`: the backslashes survive and the
drive colon survives, so `Path.home()/".claude"/"projects"/slug` names a
directory that cannot exist. `archive()` then hits
`if not source_dir.is_dir(): return counts` and reports success-zero on every
run; `--status` prints `live 0 file(s)`. The script exits 0 while archiving
nothing, indefinitely -- which is the silent transcript loss it was written to
prevent. Its own header records that transcripts live where no backup reaches.

The correct Windows slug is not something this repository can verify, so the
resolver **returns None rather than guessing one**, the same choice
`.claude/hooks/memory-reconcile.py:_native_from_hook` made the same day. Each
caller says so out loud instead of reporting an empty directory as an empty
archive: a monitor that guesses is worse than one that abstains, and one that
abstains silently is worse than both.

## And then this gate checked the spelling instead of the answer

Until 2026-09-06 the one-owner half of this file was a substring scan for the
literal `.replace("/", "-").replace(".", "-")`. Two live copies were sitting
outside the owner the whole time, written as
`re.sub(r"[^a-zA-Z0-9]", "-", ...)`, and both walked past a guard that was
looking for a different spelling of the same idea:

  - `scripts/calibrate.py:_derive_sessions_dir` (reads transcripts to measure)
  - `scripts/prime-health-parallel.py:run_memory_health` (counts memory files)

`[^a-zA-Z0-9]` also eats the UNDERSCORE, which the owner keeps, and the second
copy skipped `.resolve()` as well. MEASURED IN HELM on 2026-09-05, four paths:

    /home/administrator/ai/claude-workspaces/.heading-os
        owner   -home-administrator-ai-claude-workspaces--heading-os
        copies  identical                                     AGREE
    /home/administrator/ai/claude_workspaces/.heading-os
        owner   ...-ai-claude_workspaces--heading-os
        copies  ...-ai-claude-workspaces--heading-os          DIVERGE
    .../.heading-os/yard_ops_backlog
        owner   ...-yard_ops_backlog
        copies  ...-yard-ops-backlog                          DIVERGE
    .../.heading-os/../.heading-os
        owner   ...--heading-os
        prime   ...--heading-os-----heading-os                DIVERGE

The first line is why nobody noticed: the operator's own path carries no
underscore, so on this machine the three answers agreed. A diverged slug names a
directory that does not exist, and both wrappers read an absent directory as an
empty one, so the cost is `0 transcripts` / `0 memory files` reported as a clean
green panel with no error anywhere.

So this file now asks BEHAVIOUR of every resolver it can find, over a path set
containing an underscore, a dot mid-path, a `..` and a run of dots, and requires
each answer to equal the owner's. A fourth copy spelled any way at all fails.

**How candidates are found, and what that cannot see.** Discovery is an AST
scan (never a substring one) for the file naming the DESTINATION rather than the
transformation: a non-docstring string constant containing `.claude` together
with one equal to `projects`. A copy has to name `~/.claude/projects` to build a
path into it. Measured 2026-09-06: 5 files of 468 walked. Each must either call
`transcript_dir` or be registered below with a reason.

Named blind spots, in order of how likely they are to bite:

  1. A copy that never names the destination because it receives the projects
     root from a caller, an argument or an environment variable, and mangles the
     slug only. `scripts/herdr-brief.py --projects-root` is the shape; it
     delegates today, and a future sibling that does not would be invisible here.
  2. A copy in a file outside `scripts/**/*.py` and `.claude/**/*.py`, or in
     another language (a shell script, a hook written in anything but Python).
  3. A resolver with no callable seam, inlined in the middle of a larger
     function. The discovery half still catches the file; the behavioural half
     cannot run it, so nothing compares its answer to the owner's. That is why
     `run_memory_health` got `memory_dir_for` extracted rather than being asked
     to prove itself through its own output.
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

from tests.repo_files import read_sources, tracked_paths

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ARCH = _load("scripts/archive-transcripts.py", "archive_transcripts_under_test")
PRIME = _load("scripts/prime-health-parallel.py", "prime_health_under_test")

import scripts.calibrate as CAL  # noqa: E402
import scripts.chronicle as CHRON  # noqa: E402
from scripts.utils import checkpoint_paths as CP  # noqa: E402

OWNER_REL = "scripts/utils/checkpoint_paths.py"


# --- discovery: who else names the transcript store --------------------------

# A file that names `~/.claude/projects` and does NOT ask the owner for the
# directory. Each entry states why that is not a second copy of the slug rule.
# Checked in both directions: an entry naming a file that no longer matches the
# discovery scan fails too, so the registry cannot outlive what it excuses.
NOT_A_SLUG_RESOLVER = {
    "scripts/utils/memory_stores.py": (
        "It enumerates every native store with `base.glob('*/memory')` and "
        "computes no slug at all. There is no path here that could disagree "
        "with the owner, because nothing here derives a directory NAME."
    ),
}


def _non_docstring_constants(tree: ast.AST) -> list[str]:
    """Every string constant except the docstrings.

    Docstrings are excluded because this file, the owner and three callers all
    DISCUSS `~/.claude/projects` in prose. A scan that counted prose would make
    explaining the rule the thing that trips the guard, which teaches people to
    stop explaining it.
    """
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _names_the_transcript_store(tree: ast.AST) -> bool:
    values = _non_docstring_constants(tree)
    return (any(".claude" in v for v in values)
            and any(v == "projects" or "/projects" in v for v in values))


def _calls(tree: ast.AST, func_name: str) -> bool:
    """Does this module CALL `func_name`, asked of the AST?

    A substring scan would be satisfied by the name appearing in a comment
    explaining why the rule is not reimplemented here, which is exactly the
    sentence a fresh copy would carry.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == func_name:
            return True
        if isinstance(func, ast.Attribute) and func.attr == func_name:
            return True
    return False


def _discover() -> tuple[list[str], list[str], int]:
    """(files naming the store, those that do not delegate, files read)."""
    paths = list(tracked_paths(("scripts/**/*.py", ".claude/**/*.py")))
    vanished: list = []
    named: list[str] = []
    offenders: list[str] = []
    read = 0
    for path, text in read_sources(paths, vanished, errors="ignore"):
        read += 1
        rel = str(path.relative_to(ROOT))
        if rel == OWNER_REL:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        if not _names_the_transcript_store(tree):
            continue
        named.append(rel)
        if not _calls(tree, "transcript_dir"):
            offenders.append(rel)
    return named, offenders, read


def test_every_file_that_names_the_transcript_store_asks_the_owner():
    """The discovery half. A copy has to name the destination to reach it."""
    named, offenders, read = _discover()
    # "no offenders" is green over zero files, so a renamed directory or a
    # changed suffix would switch this guard off without failing anything.
    # Measured 2026-09-06: 468 files walked and read across the two patterns.
    # Before this change 5 named `~/.claude/projects` (the owner, plus
    # calibrate, prime-health, herdr-brief and memory_stores); after it, 3 -
    # the owner and the two below, since the fix replaced the literals in the
    # two copies with a call to the owner.
    assert read >= 400, (
        f"the scan collapsed to {read} files read of {len(list(tracked_paths(('scripts/**/*.py', '.claude/**/*.py'))))} walked")
    # The floor that stops the whole guard passing vacuously: if
    # `_non_docstring_constants` or the AST walk ever stops matching, `named`
    # and `offenders` both go empty and the assertion below is green over
    # nothing. Two is the measured post-fix set, named so a change is
    # re-measured rather than absorbed.
    assert len(named) >= 2, (
        f"only {named} name the transcript store; the discovery signal has "
        "stopped matching the tree it was measured against")
    unexcused = [f for f in offenders if f not in NOT_A_SLUG_RESOLVER]
    assert not unexcused, (
        "these name ~/.claude/projects and never call transcript_dir, so they "
        "carry their own copy of the slug rule (owner: "
        f"{OWNER_REL}):\n  " + "\n  ".join(unexcused)
    )


def test_the_registry_does_not_outlive_what_it_excuses():
    """A stale exemption is a hole nobody can see. Both directions."""
    _named, offenders, _read = _discover()
    stale = [f for f in NOT_A_SLUG_RESOLVER if f not in offenders]
    assert not stale, (
        "registered as 'not a slug resolver' but no longer matched by the "
        "discovery scan (it now delegates, moved, or stopped naming the "
        "store); drop the entry:\n  " + "\n  ".join(stale)
    )


def test_the_owner_still_exists():
    """A guard that points at a moved owner proves nothing."""
    cp = _load("scripts/utils/checkpoint_paths.py", "checkpoint_paths_under_test")
    assert callable(cp.transcript_dir)


def test_the_three_callers_call_the_owner_and_do_not_spell_the_rule():
    """Asked of the AST, not of the text. Named individually so a caller that
    silently stops delegating fails by name rather than by count."""
    for rel in ("scripts/archive-transcripts.py", "scripts/calibrate.py",
                "scripts/prime-health-parallel.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        assert _calls(tree, "transcript_dir"), f"{rel} stopped asking the owner"


# --- behaviour: every resolver returns the OWNER's answer ---------------------

# Each entry must fail for a DIFFERENT reason under a divergent copy, or the
# corpus is one case wearing four hats.
#
#   plain          the operator's own shape; the control, and the reason the two
#                  copies survived three weeks unnoticed
#   underscore     `[^a-zA-Z0-9]` eats it, the owner keeps it
#   underscore_leaf same, in the last segment (a yard name)
#   dot_mid        a dot that is not part of a leading `.name`
#   dotdot         only `.resolve()` removes it
#   dot_run        a copy collapsing `\.+` into one dash would pass every other
#                  case here
#   space          a character neither implementation is written around
PATH_CORPUS = {
    "plain": "/home/administrator/ai/claude-workspaces/.heading-os",
    "underscore": "/home/administrator/ai/claude_workspaces/.heading-os",
    "underscore_leaf": "/home/administrator/ai/.heading-os/yard_ops_backlog",
    "dot_mid": "/home/administrator/ai/some.dir/heading-os",
    "dotdot": "/home/administrator/ai/.heading-os/../.heading-os",
    "dot_run": "/home/administrator/ai/a...b/heading-os",
    "space": "/home/administrator/ai/two words/heading-os",
}


def _calibrate_resolver(monkeypatch):
    """`_derive_sessions_dir()` takes no argument: it reads the workspace root
    from its own module namespace, so the corpus is fed in there."""
    def resolve(path: Path):
        monkeypatch.setattr(CAL, "get_workspace_root", lambda: path)
        return CAL._derive_sessions_dir()
    return resolve


def _prime_resolver(_monkeypatch):
    """`memory_dir_for` answers `<project dir>/memory`; the leaf is stripped so
    the comparison is against the same thing every other resolver returns."""
    def resolve(path: Path):
        got = PRIME.memory_dir_for(path)
        return None if got is None else got.parent
    return resolve


def _herdr_resolver(_monkeypatch):
    """No `--projects-root`, so `target_dir` answers the owner's directory."""
    brief = _load("scripts/herdr-brief.py", "herdr_brief_under_test")
    return lambda path: brief.target_dir(path, None)


def _archiver_resolver(monkeypatch):
    """`archive-transcripts.transcript_dir()` takes NO argument: it is a
    zero-arg wrapper over the workspace root, so the corpus is fed through the
    root it reads."""
    def resolve(path: Path):
        monkeypatch.setattr(ARCH, "get_workspace_root", lambda: path)
        return ARCH.transcript_dir()
    return resolve


RESOLVERS = {
    "calibrate._derive_sessions_dir": _calibrate_resolver,
    "prime-health.memory_dir_for": _prime_resolver,
    "herdr-brief.target_dir": _herdr_resolver,
    "archive-transcripts.transcript_dir": _archiver_resolver,
}


def test_every_resolver_answers_exactly_what_the_owner_answers(monkeypatch):
    """The half that catches the next copy however it is spelled.

    One assertion per (resolver, path) with the pair named, because a failure
    that says only "they disagree" sends the reader back to re-derive which
    input separated them.
    """
    disagreements = []
    for label, factory in RESOLVERS.items():
        resolve = factory(monkeypatch)
        for case, raw in PATH_CORPUS.items():
            path = Path(raw)
            expected = CP.transcript_dir(path)
            try:
                got = resolve(path)
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                disagreements.append(f"{label} [{case}] raised {exc!r}")
                continue
            if got != expected:
                disagreements.append(
                    f"{label} [{case}] {raw}\n"
                    f"      owner: {expected}\n"
                    f"      got:   {got}")
    assert not disagreements, (
        "a resolver disagrees with scripts/utils/checkpoint_paths.transcript_dir; "
        "a slug the harness never wrote names a directory that does not exist, "
        "and every wrapper reads that as empty:\n    "
        + "\n    ".join(disagreements)
    )
    # The floor. A corpus that shrank to nothing, or a RESOLVERS dict that lost
    # its entries, asserts nothing at all above.
    assert len(RESOLVERS) >= 4 and len(PATH_CORPUS) >= 7


def test_the_corpus_would_actually_separate_the_two_rules():
    """The negative anchor: prove the path set can TELL the implementations
    apart. Without this, every case above could be one the old copies also got
    right, and the suite would go green against the very defect it names."""
    import re
    separated = [
        case for case, raw in PATH_CORPUS.items()
        if re.sub(r"[^a-zA-Z0-9]", "-", str(Path(raw).resolve()))
        != CP.transcript_dir(Path(raw)).name
    ]
    assert len(separated) >= 2, (
        "no path in the corpus distinguishes the owner from the "
        f"`re.sub(r'[^a-zA-Z0-9]', '-')` copies; separated only by {separated}")


# --- it resolves on POSIX ----------------------------------------------------

def test_it_resolves_a_real_directory_name_on_posix():
    cp = _load("scripts/utils/checkpoint_paths.py", "cp_posix")
    got = cp.transcript_dir(Path("/home/x/ai/.heading-os"))
    assert got is not None
    assert got.name == "-home-x-ai--heading-os", got
    assert got.parent == Path.home() / ".claude" / "projects"


def test_the_owner_normalises_before_it_mangles():
    """`.resolve()` is part of the rule, not a formality.

    Without it, an unnormalised path mangles its own `..` into dashes:
    `.../.heading-os/../.heading-os` becomes `...--heading-os-----heading-os`.
    Every resolver in RESOLVERS is compared against the OWNER, so a `.resolve()`
    dropped from the owner would move all of them together and the equality
    test above would stay green over a rule that had changed. This is the
    absolute anchor that stops that."""
    cp = _load("scripts/utils/checkpoint_paths.py", "cp_normalise")
    got = cp.transcript_dir(Path("/home/x/ai/.heading-os/../.heading-os"))
    assert got.name == "-home-x-ai--heading-os", got
    # And the underscore survives, which is the half `[^a-zA-Z0-9]` ate.
    kept = cp.transcript_dir(Path("/home/x/ai/yard_ops_backlog"))
    assert kept.name == "-home-x-ai-yard_ops_backlog", kept


def test_the_sessions_dir_override_still_wins(monkeypatch, tmp_path):
    """The override is not about the slug and had to survive the change.
    Checked in both directions: set, it wins; unset, the owner answers."""
    monkeypatch.setattr(CAL, "get_workspace_root", lambda: Path("/home/x/w"))
    monkeypatch.setenv("CLAUDE_SESSIONS_DIR", str(tmp_path))
    assert CAL._derive_sessions_dir() == tmp_path
    monkeypatch.delenv("CLAUDE_SESSIONS_DIR")
    assert CAL._derive_sessions_dir() == CP.transcript_dir(Path("/home/x/w"))


def test_the_archiver_agrees_with_the_owner():
    cp = _load("scripts/utils/checkpoint_paths.py", "cp_agree")
    from scripts.utils.workspace import get_workspace_root
    assert ARCH.transcript_dir() == cp.transcript_dir(get_workspace_root())


# --- it refuses off POSIX ----------------------------------------------------

_PROBE = """
import os, sys, importlib.util, shutil, pathlib
os.name = 'nt'
spec = importlib.util.spec_from_file_location('cp', {path!r})
m = importlib.util.module_from_spec(spec); sys.modules['cp'] = m
spec.loader.exec_module(m)
print('RESULT=' + repr(m.transcript_dir(pathlib.PurePosixPath('/home/x/w'))))
"""


def test_the_resolver_refuses_rather_than_guessing_off_posix():
    """Run in a SUBPROCESS. `os.name` cannot be patched in place: `pathlib`
    picks WindowsPath off it and `shutil` imports `nt` off it, so the patch has
    to land after both are loaded and die with the child."""
    probe = _PROBE.format(path=str(ROOT / "scripts" / "utils" / "checkpoint_paths.py"))
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, f"probe failed: {out.stderr[-800:]}"
    assert "RESULT=None" in out.stdout, (
        "the resolver produced a path on a non-POSIX platform. The backslashes "
        "and the drive colon are not handled, so the result names no real "
        f"directory. Got: {out.stdout.strip()!r}"
    )


# --- and the callers say so out loud -----------------------------------------

def test_status_marks_the_directory_unresolved(monkeypatch):
    """`status()` is a data function; it reports the fact, main() shouts it."""
    monkeypatch.setattr(ARCH, "transcript_dir", lambda: None)
    s = ARCH.status()
    assert s["unresolved"] is True
    assert s["live_count"] == 0


def test_the_cli_reports_the_refusal_instead_of_zero(monkeypatch, capsys,
                                                    unguard_main_clone):
    """The defect was not the wrong slug. It was that a wrong slug produced a
    clean `live 0 file(s)` and exit 0 on every run, forever."""
    # `archive-transcripts.main()` opens with `require_main_clone(__file__)`,
    # which exits 2 from a worktree before `--status` reports anything.
    # Neutralised on this loaded module, for this test only; the guard itself is
    # owned by tests/test_guarded_entry_points_refuse_from_a_worktree.py (the
    # call is main()'s first statement, passed `__file__`, pinned through the
    # AST) and tests/test_clone_guard.py (it fires).
    unguard_main_clone(ARCH)
    monkeypatch.setattr(ARCH, "transcript_dir", lambda: None)
    rc = ARCH.main(["--status"])
    out = capsys.readouterr()
    assert rc != 0, "--status exited 0 with no transcript directory"
    assert "could not be resolved" in (out.out + out.err).lower(), (
        f"nothing told the operator; got {out.out + out.err!r}"
    )
    assert "live " not in out.out, "it still printed a live count it could not know"


def test_archive_reports_the_refusal_too(monkeypatch, capsys):
    """ONE `readouterr()`. It drains both streams, so the second call in the
    old `capsys.readouterr().out + capsys.readouterr().err` always returned an
    empty `err` — and `archive()` prints its refusal to stderr, so the text
    half of this disjunction could never be true and only the `unresolved`
    flag was ever measured."""
    monkeypatch.setattr(ARCH, "transcript_dir", lambda: None)
    counts = ARCH.archive()
    captured = capsys.readouterr()
    text = (captured.out + captured.err).lower()
    assert counts.get("archived", 0) == 0
    assert "could not be resolved" in text, (
        f"archive() said nothing about an unresolvable directory; got {text!r}"
    )
    assert counts.get("unresolved"), (
        "archive() silently returned zero for an unresolvable directory"
    )


def test_the_memory_panel_reports_the_refusal_instead_of_no_files(monkeypatch,
                                                                 capsys):
    """A refusal that renders as `missing` is the defect one level down: the
    panel's own NON_FAILURE_STATUSES would let it through as an ordinary
    inactive-memory workspace, which is the silent green this file is about."""
    monkeypatch.setattr(PRIME, "transcript_dir", lambda _p: None)
    res = PRIME.run_memory_health(ROOT)
    capsys.readouterr()
    assert res["status"] not in PRIME.NON_FAILURE_STATUSES, (
        f"an unresolvable transcript store rendered as {res['status']!r}, "
        "which /prime treats as healthy")
    assert "could not be resolved" in res["output"].lower(), res["output"]
    assert "0 files" not in res["output"], (
        "it still reported a file count it could not have counted")


def test_calibrate_reports_the_refusal_instead_of_no_session(monkeypatch, capsys):
    """Exit 2 means 'I looked and there were none'. Off POSIX nothing looked."""
    monkeypatch.setattr(CAL, "DEFAULT_SESSIONS_DIR", None)
    rc = CAL.main([])
    out = capsys.readouterr()
    assert rc not in (0, 2), f"exit {rc} claims a completed search"
    assert "could not be resolved" in (out.out + out.err).lower(), (
        f"nothing told the operator; got {(out.out + out.err)!r}")
    assert not out.out.strip(), "it printed an envelope it could not build"


def test_chronicle_reports_the_refusal_instead_of_crashing(monkeypatch, capsys):
    """`args.sessions_dir or DEFAULT_SESSIONS_DIR` then `.is_dir()`: with the
    owner refusing, an unguarded None is an AttributeError traceback out of a
    nightly build, which says nothing about the platform."""
    monkeypatch.setattr(CHRON, "DEFAULT_SESSIONS_DIR", None)
    args = type("A", (), {"sessions_dir": None, "since": None, "backfill": False,
                          "limit": 1, "dry_run": True})()
    rc = CHRON.cmd_build(args)
    out = capsys.readouterr()
    assert rc != 0
    assert "could not be resolved" in (out.out + out.err).lower(), (
        f"nothing told the operator; got {(out.out + out.err)!r}")


# --- status() survives the deletion this script exists to outrun -------------

def test_status_skips_a_file_that_vanishes_between_glob_and_stat(monkeypatch, tmp_path):
    """`archive()` guards each file; `status()` did not, so the harness pruning
    a transcript mid-scan produced an uncaught traceback from a READ-ONLY
    command. That pruning is the whole reason this script exists."""
    live = tmp_path / "live"
    live.mkdir()
    (live / "a.jsonl").write_text("{}\n", encoding="utf-8")
    (live / "b.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(ARCH, "transcript_dir", lambda: live)
    monkeypatch.setattr(ARCH, "archive_root", lambda: tmp_path / "arch")

    real_stat = Path.stat
    gone = live / "a.jsonl"

    def flaky(self, *a, **kw):
        if self == gone:
            raise FileNotFoundError(str(self))
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", flaky)
    s = ARCH.status()
    assert s["live_count"] == 2, "the glob result should still list both"
    assert s["live_bytes"] > 0, "the surviving file's size was dropped too"


def test_status_reports_real_sizes_when_nothing_vanishes(monkeypatch, tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    (live / "a.jsonl").write_text("x" * 100, encoding="utf-8")
    monkeypatch.setattr(ARCH, "transcript_dir", lambda: live)
    monkeypatch.setattr(ARCH, "archive_root", lambda: tmp_path / "arch")
    assert ARCH.status()["live_bytes"] == 100
