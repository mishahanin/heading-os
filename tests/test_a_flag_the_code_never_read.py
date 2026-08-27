"""Five flags that were declared, printed, documented, and never read.

A flag is a promise with three parts: the parser accepts it, the code reads it,
and the docs name the one the parser accepts. Each defect below breaks a
different part, and none of them raises anything a test could notice.

1. A SIGNAL THAT COULD NOT FIRE. `ops_signals.publish_state` shelled out to
   `publish-corporate.py --dry-run --json`. That parser has never defined either
   flag, so every run exited 2 on the argparse error, `pending` never left 0,
   and the /radar publish-to-fleet row was structurally incapable of firing.
   Measured 2026-08-27 against a tree with real corporate changes: `due=False`
   against a threshold of 1. `tests/test_ops_signals.py` exercises only the pure
   classifier, which takes an already-computed integer and therefore cannot
   notice that the integer is always zero.

2. A MEASUREMENT THAT DROPPED THE PATHS IT COULD NOT SPELL. `_repo_uncommitted`
   read `git status --porcelain` as text. Git C-QUOTES any path holding a space,
   a non-ASCII byte or a backslash, so `stat()` looked for a file literally
   named `"caf\303\251.md"`, raised, and the path fell out of the oldest-mtime
   scan - understating backup debt in exactly the way the function's own
   docstring says must not happen.

3. A PROFILE NAME PRINTED BACK BUT NEVER USED. `linkedin-activity.py --profile`
   was declared, defaulted and echoed (`Loaded N cookies from Floorp <profile>
   profile`) while the reader was hardcoded to "ClaudeCode".

4. A FLAG THE SUBPARSER SILENTLY ATE. `marp_render.py --verbose` is declared
   three times; a subparser's store_true default of False OVERWRITES the
   top-level value in the same namespace, so `--verbose render deck.md` parsed
   to verbose=False. The call site read `args.verbose or getattr(args,
   "verbose", False)`, which is `x or x`.

5. TWO FLAGS THAT NEVER EXISTED. `merge-contacts.py`'s usage line documented
   `[--repo PATH]`; `scrutinize-replay.py`'s docstring documented an
   `--import-rater-output` merge step; `docs/EMERGENCY-PROCEDURES.md` told the
   operator to recover a dead Sentinel with `scripts/sentinel.py --check`, whose
   parser defines `--test`. A runbook step that exits 2 is worse than no step.

Found by the third defect-class fan-out over `tests/`, 2026-08-27, lens
`cli-flag-that-does-nothing`.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import ops_signals  # noqa: E402


def _load(relpath: str, name: str):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / relpath))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# 1. The radar signal that shelled out to flags nobody defined
# ============================================================

def _ops_signal_invocations() -> list[tuple[str, str, list[str]]]:
    """Every `[sys.executable, str(script), "--flag", ...]` ops_signals builds.

    Derived from the AST, so a fourth signal added tomorrow is checked without
    anyone remembering to add it here. Each entry is
    (enclosing function, the script path literal, the flags).
    """
    import ast
    tree = ast.parse((ROOT / "scripts" / "utils" / "ops_signals.py").read_text(encoding="utf-8"))
    out = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # The script this function names: `engine_root / "scripts" / "<name>.py"`.
        target = None
        for node in ast.walk(fn):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                parts = []
                cur = node
                while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
                    if isinstance(cur.right, ast.Constant) and isinstance(cur.right.value, str):
                        parts.insert(0, cur.right.value)
                    cur = cur.left
                if parts and parts[0] == "scripts" and parts[-1].endswith(".py"):
                    target = "/".join(parts)
        if target is None:
            continue
        for node in ast.walk(fn):
            if not (isinstance(node, ast.List) and node.elts):
                continue
            head = node.elts[0]
            if not (isinstance(head, ast.Attribute) and head.attr == "executable"):
                continue
            flags = [e.value for e in node.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)
                     and e.value.startswith("--")]
            if flags:
                out.append((fn.name, target, flags))
    return out


def test_every_argv_ops_signals_builds_is_accepted_by_its_callee():
    """The guard that would have caught this on the day it was written.

    `verify_admin_identity()` runs inside `main`, which is why nobody could
    write this test before: `publish-corporate.py` grew a `build_parser` so an
    argv can be checked without being an admin. The other two signals are
    checked through `--help`, which argparse handles inside `parse_args`.
    """
    calls = _ops_signal_invocations()
    assert len(calls) >= 3, f"only {len(calls)} script invocations found in ops_signals"
    cache: dict = {}
    bad = []
    for fn_name, script, flags in calls:
        assert (ROOT / script).is_file(), f"{fn_name} shells out to a missing {script}"
        declared = _help_flags(cache, script, ())
        assert declared is not None, f"{script} --help exits non-zero"
        for flag in flags:
            if flag not in declared:
                bad.append(f"{fn_name} -> {script} {flag}")
    assert not bad, (
        "ops_signals builds an argv its callee's parser rejects, so the signal "
        "can never fire:\n  " + "\n  ".join(bad))


def test_the_publish_argv_selects_the_json_preview():
    """Named explicitly: the flags must be the RIGHT ones, not merely accepted."""
    pc = _load("scripts/publish-corporate.py", "publish_corporate_under_test")
    flags = next(f for fn, _s, f in _ops_signal_invocations() if fn == "publish_state")
    parsed = pc.build_parser().parse_args(flags)
    assert parsed.preview and parsed.json


def test_publish_preview_json_emits_a_pending_count(tmp_path, monkeypatch):
    """--preview --json prints ONE object on stdout and nothing else."""
    pc = _load("scripts/publish-corporate.py", "publish_corporate_json")
    monkeypatch.setattr(pc, "list_tracked_files", lambda: ["a.md", "b.md", "c.md"])
    monkeypatch.setattr(pc, "list_untracked_corporate_files", list)
    monkeypatch.setattr(pc, "get_routing_destination", lambda p: "corporate")
    monkeypatch.setattr(pc, "diff_corporate", lambda files: (["a.md"], ["b.md"], ["c.md"], []))
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert pc.mode_preview(as_json=True) == 0
    payload = json.loads(buf.getvalue())
    assert payload == {"pending": 2, "new": 1, "modified": 1, "unchanged": 1,
                       "missing": 0, "untracked_corporate": 0}


def test_json_without_preview_is_an_error():
    pc = _load("scripts/publish-corporate.py", "publish_corporate_json_guard")
    with pytest.raises(SystemExit) as exc:
        pc.main(["--copy", "--json"])
    assert exc.value.code == 2


def test_a_nonzero_pending_count_makes_the_publish_signal_due(tmp_path, monkeypatch):
    """End to end through the real subprocess call, against a stub script.

    This is the assertion the suite never had: it fails the moment the argv and
    the callee's parser disagree again, whatever the reason.
    """
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "publish-corporate.py").write_text(
        "import argparse, json, sys\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--preview', action='store_true')\n"
        "p.add_argument('--json', action='store_true')\n"
        "a = p.parse_args()\n"
        "sys.exit(2) if not (a.preview and a.json) else print(json.dumps({'pending': 3}))\n",
        encoding="utf-8")
    state = ops_signals.publish_state(tmp_path)
    assert state["value"] == 3
    assert state["due"] is True


def test_the_publish_signal_degrades_to_not_due_when_the_script_is_absent(tmp_path):
    state = ops_signals.publish_state(tmp_path)
    assert state["value"] == 0 and state["due"] is False


# ============================================================
# 2. The dirty paths git could spell and this reader could not
# ============================================================

def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (["init", "-q", "."], ["config", "user.email", "t@example.invalid"],
                ["config", "user.name", "Test"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.mark.parametrize("name", ["old notes.md", "café.md", "back\\slash.md"])
def test_a_path_git_quotes_still_counts_toward_the_oldest_change(tmp_path, name):
    """Every one of these prints C-quoted under plain --porcelain.

    The old reader stat'd the quotes and the octal escapes, missed, and dropped
    the entry: `git status` said the tree was dirty and the age came back None,
    which the classifier reads as "unknown", not as "sitting for a week".
    """
    repo = _repo(tmp_path)
    f = repo / name
    f.write_text("x", encoding="utf-8")
    old = time.time() - 72 * 3600
    os.utime(f, (old, old))
    count, age = ops_signals._repo_uncommitted(repo)
    assert count == 1, f"{name!r} was not counted"
    assert age is not None, f"{name!r} was dropped from the age scan"
    assert 71 < age < 73, age


def test_a_rename_is_one_entry_and_uses_the_new_path(tmp_path):
    """Under -z a rename is TWO NUL-separated fields for ONE entry.

    Counting the fields would double-count the change, and stat'ing the second
    would look for a path that no longer exists.
    """
    repo = _repo(tmp_path)
    (repo / "a file.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "mv", "a file.md", "b file.md"], cwd=repo, check=True,
                   capture_output=True)
    old = time.time() - 48 * 3600
    os.utime(repo / "b file.md", (old, old))
    count, age = ops_signals._repo_uncommitted(repo)
    assert count == 1, "the rename was counted twice"
    assert age is not None and 47 < age < 49, age


def test_a_clean_repo_reports_zero_and_zero(tmp_path):
    """The floor. The clean-tree short circuit is derived from the parsed
    fields, not from a separate string test: a `.strip("\0")` guard sat here
    and a mutation removing it SURVIVED, because git emits an empty body for a
    clean tree and never a lone NUL. Unreachable defence, deleted.
    """
    repo = _repo(tmp_path)
    assert ops_signals._repo_uncommitted(repo) == (0, 0.0)


def test_a_deleted_path_still_counts_but_reports_an_unknown_age(tmp_path):
    """Documented contract: reporting 0.0 hours for a deletion says "just now"."""
    repo = _repo(tmp_path)
    (repo / "gone.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "gone.md").unlink()
    count, age = ops_signals._repo_uncommitted(repo)
    assert count == 1 and age is None


# ============================================================
# 3. The Floorp profile that was printed but not read
# ============================================================

def test_the_profile_flag_reaches_the_cookie_reader(monkeypatch):
    la = _load("scripts/linkedin-activity.py", "linkedin_activity_under_test")
    seen = {}

    def _fake_get_cookies(domain, profile_name, browser):
        seen["profile"] = profile_name
        return {"li_at": "x", "JSESSIONID": "y"}

    monkeypatch.setattr(la, "get_cookies", _fake_get_cookies)
    la.floorp_cookies_for_playwright("Personal")
    assert seen["profile"] == "Personal"


def test_the_missing_cookie_message_names_the_profile_that_was_read(monkeypatch):
    """The message named "ClaudeCode" whatever was typed, so a wrong --profile
    produced a correct-looking failure about the wrong profile."""
    la = _load("scripts/linkedin-activity.py", "linkedin_activity_msg")
    monkeypatch.setattr(la, "get_cookies", lambda *a, **k: {"JSESSIONID": "y"})
    with pytest.raises(RuntimeError) as exc:
        la.floorp_cookies_for_playwright("Personal")
    assert "Personal" in str(exc.value)


def test_the_call_site_passes_the_parsed_profile():
    src = (ROOT / "scripts" / "linkedin-activity.py").read_text(encoding="utf-8")
    assert "floorp_cookies_for_playwright(args.profile)" in src


# ============================================================
# 4. The subparser that ate --verbose
# ============================================================

# BOTH subcommands. The first version of this test covered `render` only, and a
# mutation that put the plain `store_true` back on `from_p` SURVIVED: one
# subparser was pinned and its twin, three lines below it in the same file, was
# not. Every subcommand that redeclares a shared dest needs its own row.
@pytest.mark.parametrize("target, argv", [
    ("render", ["--verbose", "render", "deck.md"]),
    ("render", ["render", "deck.md", "--verbose"]),
    ("from", ["--verbose", "from", "notes.md"]),
    ("from", ["from", "notes.md", "--verbose"]),
])
def test_marp_verbose_is_honoured_in_either_position(target, argv, monkeypatch):
    """argparse overwrites a shared dest with the SUBPARSER's default.

    Proven directly on 2026-08-27: with a plain `store_true` on both levels,
    `--verbose render` parses to verbose=False. `default=argparse.SUPPRESS` on
    the sub-declaration leaves the top-level value alone.
    """
    mr = _load("scripts/marp_render.py", f"marp_render_{target}_{len(argv)}")
    captured = {}
    fn = "render" if target == "render" else "transform_workspace_md"
    monkeypatch.setattr(mr, fn, lambda **kw: captured.update(kw) or {"ok": True})
    monkeypatch.setattr(mr, "print_result", lambda r: None)
    monkeypatch.setattr(sys, "argv", ["marp_render.py", *argv])
    with pytest.raises(SystemExit) as exc:
        mr.main()
    assert exc.value.code == 0
    assert captured["verbose"] is True, f"--verbose was discarded for argv {argv}"


@pytest.mark.parametrize("target, argv", [
    ("render", ["render", "deck.md"]),
    ("from", ["from", "notes.md"]),
])
def test_marp_without_verbose_stays_quiet(target, argv, monkeypatch):
    """The negative case. SUPPRESS must not make the flag always-true."""
    mr = _load("scripts/marp_render.py", f"marp_quiet_{target}")
    captured = {}
    fn = "render" if target == "render" else "transform_workspace_md"
    monkeypatch.setattr(mr, fn, lambda **kw: captured.update(kw) or {"ok": True})
    monkeypatch.setattr(mr, "print_result", lambda r: None)
    monkeypatch.setattr(sys, "argv", ["marp_render.py", *argv])
    with pytest.raises(SystemExit):
        mr.main()
    assert captured["verbose"] is False


# ============================================================
# 5. Documented flags that no parser defines
# ============================================================

_H = r"[^\S\n]"  # horizontal space only: an invocation must not span lines
_INVOKE = re.compile(
    rf"python3?{_H}+(scripts/[A-Za-z0-9_./-]+\.py)"
    rf"((?:{_H}+(?:--?[A-Za-z0-9][A-Za-z0-9-]*|[^\s`<|&;\"']+))*)")
_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")


def _documented_calls() -> dict[tuple[str, tuple[str, ...]], set[str]]:
    """Every `python scripts/<x>.py [sub...] --flag` in a git-tracked doc.

    Line-bounded on purpose. `\\s` in the tail matched newlines, so one
    invocation swallowed the flags of the next paragraph and produced twelve
    false positives on the first run of this scan.
    """
    # `-z`, and split on NUL rather than whitespace: `.split()` broke any path
    # holding a space into pieces, and git C-quotes any non-ASCII path, so both
    # kinds fell out of this scan silently.
    tracked = [p for p in subprocess.run(
        ["git", "ls-files", "-z", "*.md", "*.html"],
        cwd=ROOT, capture_output=True, text=True).stdout.split("\0") if p]
    calls: dict[tuple[str, tuple[str, ...]], set[str]] = {}
    for rel in tracked:
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in _INVOKE.finditer(text):
            script, tail = m.group(1), m.group(2)
            subs: list[str] = []
            for tok in tail.split():
                if tok.startswith("-"):
                    break
                if re.fullmatch(r"[a-z][a-z0-9-]*", tok):
                    subs.append(tok)
                else:
                    break
            flags = set(_FLAG.findall(tail))
            if flags:
                calls.setdefault((script, tuple(subs)), set()).update(flags)
    return calls


def _uses_argparse(script: str) -> bool:
    """True when the script builds an argparse parser.

    This gate is not cosmetic, it is a SAFETY check. A script without argparse
    has no --help to intercept, so running it executes the whole program: an
    early version of this guard invoked
    `scripts/generate-partner-enablement.py --help` and overwrote a real
    deliverable in the operator's data overlay before writing 44 KB to stdout.
    Nothing here may run a script that does not stop at --help.
    """
    import ast
    try:
        tree = ast.parse((ROOT / script).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "ArgumentParser":
            return True
        if isinstance(node, ast.Name) and node.id == "ArgumentParser":
            return True
    return False


def _source_flags(script: str) -> set[str]:
    """Every `--flag` literal in a script's source.

    The fallback for the scripts that read `sys.argv` by hand:
    `generate-partner-enablement.py` sets `LIGHT_MODE = "--light" in sys.argv`,
    which is a real, working flag that no parser declares.
    """
    try:
        return set(_FLAG.findall((ROOT / script).read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError):
        return set()


def _help_flags(cache: dict, script: str, subs: tuple[str, ...]) -> set[str] | None:
    """The option strings `--help` prints. None when --help itself fails.

    `--help` rather than an AST walk, because a parser may build its flags in a
    loop: `scripts/scrutinize-dispatch.py` declares six modes as
    `mode.add_argument(f"--{flag}")`, and a scan for string constants calls all
    six undeclared. argparse handles --help inside `parse_args`, before any
    admin gate a script runs afterwards, so an argparse script does no real
    work here. A script WITHOUT argparse is never run; its source is read.
    """
    key = (script, subs)
    if key not in cache:
        if not _uses_argparse(script):
            cache[key] = _source_flags(script)
            return cache[key]
        proc = subprocess.run(
            [sys.executable, str(ROOT / script), *subs, "--help"],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
            env=dict(os.environ, COLUMNS="200"))
        cache[key] = set(_FLAG.findall(proc.stdout)) if proc.returncode == 0 else None
    return cache[key]


def test_every_documented_flag_exists_in_the_script_that_is_invoked():
    """A runbook step that exits 2 is worse than no step at all.

    `docs/EMERGENCY-PROCEDURES.md` told the operator to revive a dead Sentinel
    with `python scripts/sentinel.py --check`; the parser defines `--test`, so
    the recovery step exited 2 before doing anything. Derived from the tree
    rather than listed, so a phantom flag written tomorrow fails here.
    """
    calls = _documented_calls()
    assert len(calls) >= 60, (
        f"only {len(calls)} documented invocations found; the scan regex broke "
        "and this guard is passing over an almost-empty corpus")
    pairs = sum(len(v) for v in calls.values())
    assert pairs >= 120, f"only {pairs} documented flags found; scan is too narrow"

    cache: dict = {}
    undeclared, unresolved = [], []
    for (script, subs), flags in sorted(calls.items()):
        if not (ROOT / script).is_file():
            unresolved.append(f"{script}: not in the tree")
            continue
        declared = _help_flags(cache, script, subs)
        if declared is None:
            declared = _help_flags(cache, script, ())
            if declared is None:
                unresolved.append(f"{script}: --help exits non-zero")
                continue
        top = _help_flags(cache, script, ()) or set()
        for flag in sorted(flags):
            if flag not in declared and flag not in top:
                undeclared.append(f"{script} {' '.join(subs)} {flag}".strip())

    # Named, not swallowed: a script this scan could not resolve is coverage it
    # did not deliver, and silence about it reads as a clean pass.
    assert not unresolved, "scripts this guard could not check:\n  " + "\n  ".join(unresolved)
    assert not undeclared, (
        "documented invocations whose flag no parser defines:\n  "
        + "\n  ".join(undeclared))


def test_no_script_docstring_documents_a_flag_of_its_own_that_does_not_exist():
    """Narrower than the doc scan above, and deliberately so.

    A bare `--flag` anywhere in a module docstring is not a usable signal: 43 of
    them exist in this tree and most quote ANOTHER tool (`uv pip compile
    --all-extras`, `git merge-base --is-ancestor`, `yt-dlp
    --cookies-from-browser`, `pytest --maxfail`). Only a line that invokes the
    script ITSELF is unambiguous, so that is what this checks. The prose case is
    NOT mechanically covered; `scripts/scrutinize-replay.py`'s phantom
    `--import-rater-output` was in prose, and its absence is pinned by the
    explicit test below rather than by this scan.
    """
    scripts = [p for p in subprocess.run(
        ["git", "ls-files", "-z", "scripts/*.py", "scripts/**/*.py"],
        cwd=ROOT, capture_output=True, text=True).stdout.split("\0") if p]
    import ast
    cache: dict = {}
    checked, bad = 0, []
    for rel in scripts:
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        doc = ast.get_docstring(tree) or ""
        name = Path(rel).name
        for m in re.finditer(
                rf"python3?{_H}+(?:scripts/)?{re.escape(name)}((?:{_H}+\S+)*)", doc):
            tail = m.group(1)
            flags = set(_FLAG.findall(tail))
            if not flags:
                continue
            subs: list[str] = []
            for tok in tail.split():
                if tok.startswith("-"):
                    break
                if re.fullmatch(r"[a-z][a-z0-9-]*", tok):
                    subs.append(tok)
                else:
                    break
            declared = _help_flags(cache, rel, tuple(subs))
            if declared is None:
                declared = _help_flags(cache, rel, ())
                if declared is None:
                    continue
            top = _help_flags(cache, rel, ()) or set()
            checked += 1
            for flag in sorted(flags):
                if flag not in declared and flag not in top:
                    bad.append(f"{rel}: {' '.join(subs)} {flag}".replace("  ", " "))
    assert checked >= 20, f"only {checked} self-invocations checked; scan too narrow"
    assert not bad, "script docstrings naming a flag their own parser lacks:\n  " + "\n  ".join(bad)


def test_the_two_phantom_flags_are_no_longer_presented_as_usable():
    """The prose case the scan above states it does not cover.

    Asserted on the INSTRUCTION, not on the string: both docstrings now name
    the flag in order to say it does not exist, which is the record of why the
    line changed and must survive.
    """
    replay = (ROOT / "scripts" / "scrutinize-replay.py").read_text(encoding="utf-8")
    assert "merging into the scoring sheet via --import-rater-output" not in replay, (
        "the docstring still instructs the reader to use a merge step no parser "
        "implements")
    assert "There is no\n`--import-rater-output`" in replay, (
        "the correction that records why the line changed was removed")
    merge = (ROOT / "scripts" / "merge-contacts.py").read_text(encoding="utf-8")
    assert "--repo" not in merge, "the usage line documents a flag the parser lacks"
