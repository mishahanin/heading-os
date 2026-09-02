"""The commit gate ran a different Python than the suite that validates it.

Every `repo: local` hook in `.pre-commit-config.yaml` is `language: system`, which
means pre-commit resolves the entry's argv[0] against PATH. Until 2026-09-02 all
but two of them opened with a bare `python3`, and on the operator's machine PATH
`python3` is `/usr/bin/python3` (3.12.3) while the pinned environment is `.venv`
(3.11.15), built by `uv sync` from `uv.lock`.

MEASURED 2026-09-02 with a temporary probe hook printing `sys.executable`:

    PROBE-A sys.executable=/usr/bin/python3
    PROBE-A sys.version=3.12.3

Twenty-one of the twenty-three local hooks ran under that interpreter, the 31C
secret scanner and both leak guards among them. The commit gate was therefore a
different program from the one the test suite exercises. The same probe found
`/usr/bin/python3` importing pytest from `/home/administrator/.local/lib/python3.12/
site-packages/pytest`, which is the exact failure `scripts/turn-check.py` records
in its module docstring: a lane collecting under the system interpreter against a
`~/.local` copy of a package the pinned environment does not use.

The remaining two hooks went through `uv run python`, which did resolve the venv,
but `uv run` BUILDS a fresh environment at any version satisfying
`requires-python` (`>=3.11`) when `.venv` is absent, and both carried a
`command -v uv || exit 0` guard that skipped the gate entirely. A gate that can
silently run somewhere else, or not run at all, is the defect this file guards.

What this test asserts, all of it derived from the YAML and never hand-listed:

1. Every Python interpreter token in a local hook's `entry` is the pinned
   `.venv/bin/python`. A hand-maintained roster of hook ids would fall behind the
   day someone adds the twenty-fourth hook, so the roster is read from the file.
2. A local hook's `language` is one that this test knows how to reason about. An
   unrecognised value fails rather than passing unexamined.
3. A local hook that wraps its work in a shell carries an explicit
   `.venv/bin/python` existence check, so a clone that has not run `uv sync`
   refuses by name instead of falling through to whatever the shell finds.
4. A floor: a run that inspected no hooks, or implausibly few, refuses. A green
   assertion loop over an empty corpus proves nothing.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / ".pre-commit-config.yaml"

# The one interpreter a local hook may name. Relative on purpose: pre-commit sets
# the repository root as the working directory for every hook it runs, and an
# absolute path would be wrong on every machine but one.
PINNED_INTERPRETER = ".venv/bin/python"

# Matches anything that would run a Python interpreter as a program: `python`,
# `python3`, `python3.11`, `/usr/bin/python3`, `.venv/bin/python`. It deliberately
# also matches the bare forms, because a bare form is precisely the defect.
INTERPRETER_TOKEN = re.compile(r"(?:[\w./+-]*/)?python[0-9.]*$")

# Languages whose hooks run no interpreter of their own and so have nothing for
# rule 1 to inspect. Kept tiny on purpose: an unknown language is a failure, not
# a silent skip, so this set cannot quietly grow stale.
NON_INTERPRETER_LANGUAGES = frozenset({"fail", "pygrep"})

# `language: system` is the only value under which the entry's own argv[0] decides
# the interpreter, which is what rules 1 and 3 reason about.
INTERPRETER_LANGUAGES = frozenset({"system"})

# Shells a hook may legitimately open with before reaching Python.
SHELL_PROGRAMS = frozenset({"bash", "sh"})

# Floor. Measured 2026-09-02: 23 local hooks. The floor sits below that so normal
# churn does not trip it, and far enough above zero that an empty or mis-parsed
# corpus cannot pass this file green.
MINIMUM_LOCAL_HOOKS = 15


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        pytest.fail(f"{CONFIG_PATH} is missing; the commit gate cannot be verified")
    loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or "repos" not in loaded:
        pytest.fail(f"{CONFIG_PATH} did not parse into a pre-commit config")
    return loaded


def _local_hooks() -> list[dict]:
    """Every hook under a `repo: local` block, read from the file.

    Derived, never hand-listed. `.claude/rules/` records the shape of the bug this
    avoids: a hand-maintained security list falls behind silently.
    """
    hooks: list[dict] = []
    for repo in _load_config()["repos"]:
        if repo.get("repo") != "local":
            continue
        for hook in repo.get("hooks", []):
            hooks.append(hook)
    return hooks


def _entry_tokens(entry: str) -> list[str]:
    """Every shell word in an entry, including the words inside a `bash -c` body.

    A `bash -c '...'` entry hides its real interpreter one quoting level down, so
    splitting once and reading argv[0] would report `bash` and see nothing else.
    """
    try:
        tokens = shlex.split(entry)
    except ValueError as exc:
        pytest.fail(f"entry is not parseable as a shell command: {entry!r} ({exc})")
    expanded: list[str] = []
    for token in tokens:
        expanded.append(token)
        # A shell body is itself a command line; descend one level so a
        # `python3` buried inside `bash -c '...'` is not invisible to rule 1.
        if any(ch in token for ch in " \t") and (
            "python" in token or "uv run" in token
        ):
            try:
                expanded.extend(shlex.split(token))
            except ValueError:
                expanded.extend(token.split())
    return expanded


def _interpreter_tokens(entry: str) -> list[str]:
    return [t for t in _entry_tokens(entry) if INTERPRETER_TOKEN.fullmatch(t)]


def test_the_two_language_sets_cannot_contradict_each_other() -> None:
    """A language cannot both run an interpreter and run none.

    Found by mutation on 2026-09-02: adding "system" to NON_INTERPRETER_LANGUAGES
    left every assertion in this file green, because the two sets are consumed at
    separate call sites and nothing compared them. The surviving mutant was a gap,
    not dead code: a later edit that parks a language in both sets would silently
    excuse it from the interpreter rule while still appearing to be covered.
    """
    overlap = INTERPRETER_LANGUAGES & NON_INTERPRETER_LANGUAGES
    assert not overlap, (
        f"languages claimed as both interpreter-bearing and interpreter-free: "
        f"{sorted(overlap)}. One of the two sets is wrong, and until it is fixed "
        "the hooks using that language are excused from the interpreter rule."
    )
    assert INTERPRETER_LANGUAGES, (
        "INTERPRETER_LANGUAGES is empty, so no hook can ever be interpreter-checked."
    )


def test_the_local_hook_corpus_is_large_enough_to_mean_anything() -> None:
    """Floor. An empty or mis-parsed corpus refuses instead of passing."""
    hooks = _local_hooks()
    assert len(hooks) >= MINIMUM_LOCAL_HOOKS, (
        f"only {len(hooks)} local hooks parsed out of {CONFIG_PATH.name}; "
        f"expected at least {MINIMUM_LOCAL_HOOKS}. Either the config lost most of "
        "its gate or this test stopped reading it, and both mean the assertions "
        "below are measuring nothing."
    )
    ids = [h.get("id") for h in hooks]
    assert all(ids), f"a local hook has no id, so it cannot be reported: {ids}"
    assert len(set(ids)) == len(ids), f"duplicate local hook ids: {ids}"


def test_every_local_hook_declares_a_language_this_test_understands() -> None:
    """An unrecognised language fails rather than slipping past rule 1 unexamined."""
    known = INTERPRETER_LANGUAGES | NON_INTERPRETER_LANGUAGES
    offenders = [
        (h["id"], h.get("language"))
        for h in _local_hooks()
        if h.get("language") not in known
    ]
    assert not offenders, (
        "local hooks declare a language this test cannot reason about, so their "
        f"interpreter is unverified: {offenders}. Either use `language: system` "
        "with an entry naming " + PINNED_INTERPRETER + ", or extend this test "
        "deliberately after measuring what the new language actually runs."
    )


def test_no_local_hook_can_resolve_an_interpreter_other_than_the_pinned_one() -> None:
    """The defect itself: a gate hook running a Python the suite never validated."""
    inspected = 0
    offenders: list[str] = []
    for hook in _local_hooks():
        if hook.get("language") not in INTERPRETER_LANGUAGES:
            continue
        entry = hook.get("entry", "")
        assert entry, f"hook {hook['id']} has no entry"
        inspected += 1
        found = _interpreter_tokens(entry)
        if not found:
            offenders.append(
                f"{hook['id']}: entry names no Python interpreter at all, so what "
                f"it runs is unknown -> {entry!r}"
            )
            continue
        for token in found:
            if token != PINNED_INTERPRETER:
                offenders.append(
                    f"{hook['id']}: resolves {token!r}, not {PINNED_INTERPRETER!r}"
                )

    assert inspected >= MINIMUM_LOCAL_HOOKS, (
        f"inspected only {inspected} interpreter-bearing local hooks; expected at "
        f"least {MINIMUM_LOCAL_HOOKS}. A pass over an empty set is not a pass."
    )
    assert not offenders, (
        "the commit gate would run an interpreter other than the pinned "
        f"{PINNED_INTERPRETER} (3.11.15 from `uv sync`). PATH `python3` here is "
        "/usr/bin/python3 (3.12.3) and carries a ~/.local package set the pinned "
        "environment does not use, so these hooks execute a different program "
        "from the one the test suite validates:\n  " + "\n  ".join(offenders)
    )


def test_a_shell_wrapped_hook_refuses_by_name_when_the_venv_is_absent() -> None:
    """A clone that has not run `uv sync` must fail loudly, never fall through.

    pre-commit gives that for free when the entry's argv[0] IS the interpreter:
    it reports "Executable `.venv/bin/python` not found" and exits 1 (measured
    2026-09-02 in a scratch repository). A hook that opens with a shell bypasses
    that check, so it has to make the same refusal itself.
    """
    inspected = 0
    offenders: list[str] = []
    for hook in _local_hooks():
        if hook.get("language") not in INTERPRETER_LANGUAGES:
            continue
        entry = hook.get("entry", "")
        tokens = shlex.split(entry)
        if not tokens or Path(tokens[0]).name not in SHELL_PROGRAMS:
            continue
        inspected += 1
        if f"[ -x {PINNED_INTERPRETER} ]" not in entry:
            offenders.append(
                f"{hook['id']}: shell-wrapped and carries no "
                f"`[ -x {PINNED_INTERPRETER} ]` refusal -> {entry!r}"
            )

    assert inspected >= 1, (
        "no shell-wrapped local hook was inspected. Two exist (docs-html-drift, "
        "readme-numbers); if both were rewritten to invoke the interpreter "
        "directly, delete this test rather than leave it green over nothing."
    )
    assert not offenders, (
        "a shell-wrapped hook would fall through to whatever the shell finds "
        "instead of refusing when the pinned interpreter is missing:\n  "
        + "\n  ".join(offenders)
    )


def test_no_local_entry_reaches_the_interpreter_through_uv_run() -> None:
    """`uv run` is not a pin.

    It resolves an existing `.venv` correctly, but with `.venv` absent it BUILDS
    one at any version satisfying `requires-python`, which this project sets to
    `>=3.11`. Measured 2026-09-02 in a scratch project: `uv run python` created a
    venv and ran it without being told which version to use. That is a silent
    choice of interpreter, which is the defect, not the fix.
    """
    offenders = [
        h["id"]
        for h in _local_hooks()
        if h.get("language") in INTERPRETER_LANGUAGES
        and re.search(r"\buv\s+run\b", h.get("entry", ""))
    ]
    assert not offenders, (
        "local hooks reach Python through `uv run`, which picks an interpreter "
        f"version by resolution rather than by pin: {offenders}. Name "
        f"{PINNED_INTERPRETER} directly."
    )
