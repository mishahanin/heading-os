"""A commit guard that prints BLOCKED must exit non-zero.

Two of them did not. `vault-guard` and `runtime-state-guard` both ended in:

    (print('BLOCKED - ...') or [print(f'  {f}') for f in leaked] or sys.exit(1))
        if leaked else sys.exit(0)

`print()` returns None, so the chain moves to the list comprehension. A
comprehension over a non-empty list returns a non-empty list of Nones, which is
TRUTHY, so `or` short-circuits there and `sys.exit(1)` is never evaluated. The
script then runs off the end and the interpreter exits 0. Measured 2026-08-23:

    $ python3 -c "<vault-guard entry>" _secure/probe.txt
    BLOCKED - vault files staged for commit
      _secure/probe.txt
    EXIT=0

So both guards announced a block and allowed the commit, for as long as they
have existed. This is the failure the repo's own CI comments call the worst
kind: a step that can never be observed failing.

These tests run each local hook's REAL `entry` string out of
`.pre-commit-config.yaml`, rather than a copy. A copy is how the two got out of
sync with reality in the first place.

**The half this file could not see, until 2026-09-01.** `BLOCKING_GUARDS` and
the coverage ratchet below both read the ENTRY string, so they only ever saw the
two hooks that hold their whole refusal in a `python3 -c` one-liner. Three more
local hooks refuse from inside a SCRIPT the entry invokes:

    leak-guard-paths     scripts/leak-guard.py     "BLOCKED - hardcoded data-path ..."
    leak-guard-staged    scripts/leak-guard.py     "BLOCKED - non-engine content ..."
    content-guard-31c    scripts/content-guard.py  "BLOCKED - real-entity content ..."

None of them appeared in `printers`, because "BLOCKED" is not in their entry.
MEASURED with the mutation harness: adding a local hook whose entry runs a
script that prints BLOCKED and calls `sys.exit(0)` left this file GREEN. The
ratchet named "every hook that can print BLOCKED" was covering two of five.

All three were measured refusing correctly (exit 1) the day the gap was found,
so this is a coverage gap and not a live regression. It is closed by RUNNING
them, in `SCRIPT_BACKED_GUARDS`, rather than by naming a neighbour test: a name
is a claim that decays, and a claim about a blocking gate is the kind this file
exists to stop believing.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / ".pre-commit-config.yaml"


def _local_hooks() -> dict[str, str]:
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    hooks = {}
    for repo in data.get("repos", []):
        if repo.get("repo") != "local":
            continue
        for hook in repo.get("hooks", []):
            hooks[hook["id"]] = hook.get("entry", "")
    return hooks


HOOKS = _local_hooks()

# hook id -> (a path it must refuse, a path it must allow)
BLOCKING_GUARDS = {
    "vault-guard": ("_secure/probe.txt", "README.md"),
    "runtime-state-guard": (".claude/scheduled_tasks.json", "README.md"),
}


def _run(entry: str, *args: str,
         env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a hook's entry string the way pre-commit does: shell-split, then
    the staged filenames appended."""
    return subprocess.run(shlex.split(entry) + list(args),
                          capture_output=True, text=True, cwd=ROOT,
                          env=dict(os.environ, **(env or {})))


@pytest.mark.parametrize("hook_id", sorted(BLOCKING_GUARDS))
def test_the_guard_is_still_configured(hook_id):
    assert hook_id in HOOKS, (
        f"{hook_id} is gone from .pre-commit-config.yaml. If that was deliberate, "
        "remove its row from BLOCKING_GUARDS here too; if not, the gate is off."
    )


@pytest.mark.parametrize("hook_id", sorted(BLOCKING_GUARDS))
def test_the_guard_exits_non_zero_on_the_thing_it_names(hook_id):
    bad, _ = BLOCKING_GUARDS[hook_id]
    result = _run(HOOKS[hook_id], bad)
    assert "BLOCKED" in result.stdout, (
        f"{hook_id} did not even announce a block for {bad!r}"
    )
    assert result.returncode != 0, (
        f"{hook_id} printed BLOCKED for {bad!r} and then exited 0, so the commit "
        f"proceeds. stdout was: {result.stdout!r}"
    )


@pytest.mark.parametrize("hook_id", sorted(BLOCKING_GUARDS))
def test_the_guard_names_the_offending_file(hook_id):
    bad, _ = BLOCKING_GUARDS[hook_id]
    result = _run(HOOKS[hook_id], bad)
    assert bad in result.stdout, (
        f"{hook_id} blocked without saying which file, so the operator cannot act"
    )


@pytest.mark.parametrize("hook_id", sorted(BLOCKING_GUARDS))
def test_the_guard_passes_an_ordinary_file(hook_id):
    _, good = BLOCKING_GUARDS[hook_id]
    result = _run(HOOKS[hook_id], good)
    assert result.returncode == 0, (
        f"{hook_id} rejected the innocent file {good!r}: {result.stdout!r} "
        f"{result.stderr!r}"
    )


@pytest.mark.parametrize("hook_id", sorted(BLOCKING_GUARDS))
def test_the_guard_passes_when_nothing_is_staged(hook_id):
    assert _run(HOOKS[hook_id]).returncode == 0


# --- the guards whose refusal lives in a script, not in the entry -------------

# Every path in an entry that names a file in this repository. A regex rather
# than shlex.split, because two entries are `bash -c '<whole command>'` and
# shell-splitting those yields one opaque token with the script name inside it.
_PY_IN_ENTRY = re.compile(r"[\w./-]+\.py")

# An INVENTED company. The engine repo is public and ships no real entity, so
# the probe token is manufactured here and planted in a synthetic overlay that
# lives and dies inside tmp_path.
INVENTED_ENTITY = "Zarquon Telemetrics"


def _hook_scripts(entry: str) -> list[Path]:
    """The repository scripts a hook entry actually invokes."""
    seen = []
    for token in _PY_IN_ENTRY.findall(entry):
        path = ROOT / token
        if path.is_file() and path not in seen:
            seen.append(path)
    return seen


def _prints_blocked(hook_id: str) -> bool:
    """Whether this hook can print BLOCKED, from EITHER half of itself."""
    entry = HOOKS[hook_id]
    if "BLOCKED" in entry:
        return True
    return any("BLOCKED" in path.read_text(encoding="utf-8", errors="replace")
               for path in _hook_scripts(entry))


def _scratch_data(tmp_path: Path) -> Path:
    """A data root for the child, so a refusal's denial-log entry cannot land
    in the operator's live overlay. Per-child env, never a run-wide pin."""
    root = tmp_path / "scratch-data"
    root.mkdir(exist_ok=True)
    return root


def _leak_guard_paths_case(tmp_path: Path, *, refuse: bool):
    bad = tmp_path / ("bad.py" if refuse else "good.py")
    bad.write_text(
        'P = "crm/contacts/example.md"\n' if refuse
        else "P = get_crm_contacts_dir() / name\n",
        encoding="utf-8")
    return [str(bad)], {"HEADING_OS_DATA": str(_scratch_data(tmp_path))}


def _leak_guard_staged_case(tmp_path: Path, *, refuse: bool):
    # Path STRINGS: this hook classifies names, it does not open them.
    return ([("crm/contacts/example.md" if refuse else "README.md")],
            {"HEADING_OS_DATA": str(_scratch_data(tmp_path)),
             "HEADING_OS_ENGINE_REPO": "1"})


def _content_guard_case(tmp_path: Path, *, refuse: bool):
    """The probe must sit INSIDE the repository: content-guard classifies by
    routing destination and scans nothing outside the engine tree (measured -
    an absolute /dev/shm path reported `0 file(s)` and exited 0, which would
    have made this an assertion about nothing)."""
    overlay = tmp_path / "overlay"
    (overlay / "config").mkdir(parents=True, exist_ok=True)
    (overlay / "config" / "content-denylist.yaml").write_text(
        f"companies:\n  - {INVENTED_ENTITY}\n", encoding="utf-8")
    probe = ROOT / "tests" / f"_precommit_guard_probe-{os.getpid()}.md"
    probe.write_text(
        f"A deal with {INVENTED_ENTITY}.\n" if refuse else "Nothing to see.\n",
        encoding="utf-8")
    return ([probe.relative_to(ROOT).as_posix()],
            {"HEADING_OS_DATA": str(overlay)})


# hook id -> a builder returning (extra argv, extra env) for a refusal / a pass.
# Cleanup of anything written inside the repository happens in the test.
SCRIPT_BACKED_GUARDS = {
    "leak-guard-paths": _leak_guard_paths_case,
    "leak-guard-staged": _leak_guard_staged_case,
    "content-guard-31c": _content_guard_case,
}

COVERED = set(BLOCKING_GUARDS) | set(SCRIPT_BACKED_GUARDS)


def _cleanup_repo_probes() -> None:
    for stray in (ROOT / "tests").glob(f"_precommit_guard_probe-{os.getpid()}.*"):
        stray.unlink()


@pytest.mark.parametrize("hook_id", sorted(SCRIPT_BACKED_GUARDS))
def test_the_script_backed_guard_is_still_configured(hook_id):
    assert hook_id in HOOKS, (
        f"{hook_id} is gone from .pre-commit-config.yaml. If that was "
        "deliberate, remove its row from SCRIPT_BACKED_GUARDS; if not, a "
        "blocking gate is off."
    )


@pytest.mark.parametrize("hook_id", sorted(SCRIPT_BACKED_GUARDS))
def test_a_script_backed_guard_exits_non_zero_when_it_prints_blocked(
        hook_id, tmp_path):
    """Finding 28's shape, one layer down: the refusal text is in the script.

    `.githooks/pre-push-data` printed "push blocked" and returned 0 in this same
    repository, so the message is never the measurement. Both are asserted, and
    the exit status is the one that decides whether a commit happens.
    """
    args, env = SCRIPT_BACKED_GUARDS[hook_id](tmp_path, refuse=True)
    try:
        result = _run(HOOKS[hook_id], *args, env=env)
    finally:
        _cleanup_repo_probes()
    combined = result.stdout + result.stderr
    assert "BLOCKED" in combined, (
        f"{hook_id} did not announce a block for {args}: {combined!r}")
    assert result.returncode != 0, (
        f"{hook_id} printed BLOCKED and then exited 0, so the commit proceeds. "
        f"Output was: {combined!r}"
    )


@pytest.mark.parametrize("hook_id", sorted(SCRIPT_BACKED_GUARDS))
def test_a_script_backed_guard_passes_the_innocent_case(hook_id, tmp_path):
    """The other jaw. A guard that refuses everything blocks nothing useful and
    would satisfy the test above for the wrong reason."""
    args, env = SCRIPT_BACKED_GUARDS[hook_id](tmp_path, refuse=False)
    try:
        result = _run(HOOKS[hook_id], *args, env=env)
    finally:
        _cleanup_repo_probes()
    assert result.returncode == 0, (
        f"{hook_id} rejected the innocent case {args}: "
        f"{result.stdout!r} {result.stderr!r}"
    )


# --- the shape that caused it must not reappear anywhere in the config --------

def test_no_local_hook_puts_sys_exit_1_behind_an_or_chain():
    """The generic form of the bug: `... or [<comprehension>] or sys.exit(1)`.
    Any truthy value earlier in the chain silently swallows the exit."""
    offenders = []
    for hook_id, entry in HOOKS.items():
        if "or sys.exit(1)" in entry:
            offenders.append(hook_id)
    assert not offenders, (
        f"these hooks reach sys.exit(1) through an `or` chain: {offenders}. "
        "Whether the exit runs then depends on the truthiness of everything "
        "before it. Use a standalone `sys.exit(1 if leaked else 0)`."
    )


def test_every_hook_that_can_print_blocked_is_covered_here():
    """Stops the next guard of this shape shipping untested.

    Reads BOTH halves of a hook: its entry, and the source of any repository
    script that entry invokes. Reading only the entry is how three of the five
    BLOCKED printers stayed outside this ratchet.
    """
    printers = {hook_id for hook_id in HOOKS if _prints_blocked(hook_id)}
    # Non-vacuous: an empty set satisfies the assertion below without measuring
    # anything, which is exactly what a broken `_hook_scripts` would produce.
    # Measured 5 on 2026-09-01: vault-guard, runtime-state-guard,
    # leak-guard-paths, leak-guard-staged, content-guard-31c.
    assert len(printers) >= 5, (
        f"only {sorted(printers)} were detected as able to print BLOCKED. The "
        "detector collapsed; every assertion in this file scans that set."
    )
    uncovered = printers - COVERED
    assert not uncovered, (
        f"these hooks print BLOCKED but nothing here checks they exit non-zero: "
        f"{sorted(uncovered)}. Add an entry-only guard to BLOCKING_GUARDS, or a "
        "script-backed one to SCRIPT_BACKED_GUARDS with a recipe that drives it "
        "into its refusal."
    )


def test_the_blocked_detector_reads_inside_an_invoked_script():
    """The negative case for `_prints_blocked`, and the realistic near-miss.

    `leak-guard-staged` carries no "BLOCKED" anywhere in its entry string; the
    text lives in `scripts/leak-guard.py`. A detector that looks only at the
    entry answers False here, which is the state this file shipped in.
    """
    assert "BLOCKED" not in HOOKS["leak-guard-staged"]
    assert _hook_scripts(HOOKS["leak-guard-staged"]), (
        "the entry no longer resolves to a script in this repository")
    assert _prints_blocked("leak-guard-staged")
    # And the detector must still say no to something, or the ratchet above is
    # satisfied by a predicate that matches everything. Not pinned to a named
    # hook: a hook that GAINS a BLOCKED message should fail the ratchet with the
    # ratchet's own message, not this one.
    quiet = sorted(h for h in HOOKS if not _prints_blocked(h))
    assert quiet, "every local hook was classed as a BLOCKED printer"
