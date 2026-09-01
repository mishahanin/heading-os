#!/usr/bin/env python3
"""Two sessions synced one memory store with no lock and no atomic write.

`.claude/hooks/memory-reconcile.py` is a SessionStart hook, and this workspace
runs several sessions on one tree. BY DESIGN each session syncs a DIFFERENT
native harness store against the SAME `<data-root>/auto-memory`, so the canonical
side has two concurrent writers and the hook took no lock over it. Each entry was
copied with `shutil.copy2`, which TRUNCATES the destination and then streams into
it, so a concurrent reader can hold a short file or an empty one. This hook is
that reader: `reconcile` compares `fa.read_bytes()` against `fb.read_bytes()` and
resolves any difference by mtime, and copy2 stamps the source's mtime only after
the stream finishes, so a truncated read can win the comparison and be copied out
as content. All of it under a module docstring promising the design "fails safe
against accidental mass-loss".

MEASURED 2026-08-31 in a scratch sandbox, one writer process looping the copy
while another read the destination, on a 4000-byte file (the shape of a real
memory: 248 of them in the live store, largest 18713 bytes):

    shutil.copy2      224456 of 566664 reads a size other than 4000, smallest 0
    tmp + os.replace       0 of 420183

Two things this file does NOT establish, said plainly rather than left implied.
It does not reproduce the end-to-end loss between two live SessionStart hooks: a
forced-overlap harness running two real hook processes over 20 files of 480 KB
each, 12 rounds, corrupted nothing, because the mtime resolution usually happens
to pick the full side. The exposure window is measured; which side of a real
overlap loses is not. And it does not prove the lock produces mutual exclusion of
the read-compare-write span under a real overlap; it proves the lock is taken over
the SHARED store, that a second holder is detected and reported, and that its
absence degrades rather than blocks.

One more defect in the same file, found the same day:

  * A `transcript_path` of the wrong TYPE killed the hook.
    `Path(tp)` sat OUTSIDE main()'s try. MEASURED against the live hook:
    `{"transcript_path": 3}` exited 1 with `TypeError: expected str, bytes or
    os.PathLike object, not int`, and `[1]`, `{"a": 1}` and `true` each did the
    same. `tests/test_every_hook_survives_a_malformed_payload.py` feeds only
    top-level non-object payloads, so a wrong FIELD type was covered nowhere.
    `[]` and `{}` slipped past the old truthiness test only because they are
    falsy, which is luck rather than a guard.

A third defect lived next door and is now gone. `.claude/hooks/memory-inject.py`
claimed to be a live SessionStart hook while being named in NO settings file
(measured 2026-08-31), and of the 17 hooks then on disk it was the ONLY
unregistered one. The operator retired the file on 2026-09-01 rather than keep
wiring a hook `recall-inject.py` had superseded on 2026-08-07. The two checks it
motivated stay, and they are the reason it is worth naming here: every hook on
disk must be wired or declared, and a declaration must not outlive its hook.
Those two now run over 16 hooks and an EMPTY declared-unregistered registry, so
each carries its own anti-vacuity floor rather than leaning on the one exemption
that used to make them non-trivial.

Run: .venv/bin/python -m pytest tests/test_two_sessions_that_synced_one_memory_store.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HOOKS = ROOT / ".claude" / "hooks"
HOOK = HOOKS / "memory-reconcile.py"

# 4000 bytes: measured above, and inside the size range of the live store.
BODY = ("y" * 79 + "\n") * 50
FULL = len(BODY.encode())


def load_hook():
    spec = importlib.util.spec_from_file_location("memory_reconcile_race", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return load_hook()


# ============================================================
# The write window, measured with a real second process
# ============================================================

_WRITER = """
import importlib.util, os, shutil, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location('m', {hook!r})
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
src, dst = Path({src!r}), Path({dst!r})
copier = {copier}
deadline = time.monotonic() + {seconds!r}
while time.monotonic() < deadline:
    copier(src, dst)
"""


def observe(tmp_path: Path, copier: str, seconds: float = 1.5) -> tuple[int, int, int]:
    """Read the destination while a SECOND PROCESS copies onto it, over and over.

    A second process, not a thread: the racers here are two `python3` hook
    invocations launched by two Claude sessions, and a thread would share this
    interpreter's GIL and measure something else.

    `copier` is an expression evaluated inside the child against the loaded hook
    module, so the same harness can drive the module's own primitive and the
    `shutil.copy2` it replaced. Returns (reads, short_reads, smallest_size).
    """
    src, dst = tmp_path / "src.md", tmp_path / "fact.md"
    src.write_text(BODY, encoding="utf-8")
    dst.write_text(BODY, encoding="utf-8")
    child = _WRITER.format(hook=str(HOOK), src=str(src), dst=str(dst),
                           copier=copier, seconds=seconds)
    writer = subprocess.Popen([sys.executable, "-c", child],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    reads = short = 0
    smallest = FULL
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            try:
                size = len(dst.read_bytes())
            except FileNotFoundError:
                short += 1
                smallest = 0
                continue
            reads += 1
            smallest = min(smallest, size)
            if size != FULL:
                short += 1
    finally:
        writer.wait(timeout=30)
    assert writer.returncode == 0, writer.stderr.read().decode()[-1500:]
    return reads, short, smallest


def test_the_harness_can_see_a_truncating_copy(tmp_path):
    """The control. Without it, "no short reads" could mean the harness reads
    nothing, or that this filesystem happens to make copy2 look indivisible, and
    the guard below would be green over a measurement that never happened.

    This asserts a property of `shutil.copy2` on this platform, deliberately. If
    it ever stops holding, the guard below has stopped measuring the fix and
    someone has to find out why before trusting it again.
    """
    reads, short, smallest = observe(tmp_path, "shutil.copy2")
    assert reads > 500, f"the harness managed only {reads} reads"
    assert short > 0, (
        "shutil.copy2 exposed no short destination in this run; the harness is "
        "no longer measuring the window the fix closes")
    assert smallest == 0, (
        f"expected an entirely empty destination to be observable, smallest was "
        f"{smallest}")


def test_the_hook_copier_never_exposes_a_truncated_destination(tmp_path):
    reads, short, smallest = observe(tmp_path, "m._copy_atomic")
    assert reads > 500, f"the harness managed only {reads} reads"
    assert (short, smallest) == (0, FULL), (
        f"{short} of {reads} reads saw a destination of the wrong size "
        f"(smallest {smallest}); a reader must hold either the whole old file or "
        "the whole new one")


# ============================================================
# The copy still has copy2's semantics
# ============================================================

def test_the_atomic_copy_keeps_the_source_mtime_and_mode(mod, tmp_path):
    """The mtime-tie rule in `reconcile` reads mtimes, and
    `tests/test_memory_reconcile_bump.py` pins what it decides. A copy that
    stamped a fresh mtime would silently make every pair look newly edited.

    Mode matters for the same reason a bare tempfile write would be wrong here:
    `mkstemp` creates 0600, so a naive tmp-then-replace would quietly narrow the
    permissions of every memory it touched. 0o640 is chosen because it is neither
    the tempfile default nor the umask default, so only a real copy of the
    source's mode produces it.
    """
    src, dst = tmp_path / "src.md", tmp_path / "fact.md"
    src.write_text(BODY, encoding="utf-8")
    os.chmod(src, 0o640)
    os.utime(src, (1_700_000_000, 1_700_000_000))
    dst.write_text("stale\n", encoding="utf-8")
    os.chmod(dst, 0o600)
    os.utime(dst, (1_600_000_000, 1_600_000_000))

    mod._copy_atomic(src, dst)

    assert dst.read_text(encoding="utf-8") == BODY
    assert dst.stat().st_mtime == src.stat().st_mtime == 1_700_000_000
    assert stat.S_IMODE(dst.stat().st_mode) == 0o640


def test_the_temporary_is_invisible_to_the_memory_glob(mod, tmp_path, monkeypatch):
    """`reconcile` enumerates `*.md`, and a temporary that matched it would be
    reconciled as a memory of its own, into the other store, under a name no
    memory has. Both directions: a live temporary is not globbed, and a completed
    copy leaves none behind at all.

    The live half is OBSERVED FROM INSIDE the real call, at the one instant the
    temporary exists: `os.replace` is the last statement of `_copy_atomic`, so a
    watcher standing in front of it sees the directory exactly as a concurrent
    reader would. The first version instead wrote a temporary of its own,
    spelled `.{name}.{pid}.tmp` in the test, and globbed for that -- which
    asserts a property of the literal in the test file and nothing about the
    module. MEASURED 2026-09-01 by renaming the module's temporary to
    `{name}.{pid}.tmp.md`: it matched `*.md` outright and this file stayed
    green.
    """
    src, dst = tmp_path / "src.md", tmp_path / "fact.md"
    src.write_text(BODY, encoding="utf-8")
    dst.write_text("previous\n", encoding="utf-8")

    seen: list[list[str]] = []
    real_replace = mod.os.replace

    def _watch(a, b):
        seen.append(sorted(p.name for p in tmp_path.glob("*.md")))
        return real_replace(a, b)

    monkeypatch.setattr(mod.os, "replace", _watch)
    mod._copy_atomic(src, dst)
    monkeypatch.undo()

    assert seen == [["fact.md", "src.md"]], (
        f"a live temporary was visible to the `*.md` glob: {seen}")
    assert dst.read_text(encoding="utf-8") == BODY, (
        "the watcher stood in for the replace instead of delegating to it")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fact.md", "src.md"], (
        "the copy left something behind in the store directory")


def test_a_failed_replace_leaves_neither_a_temporary_nor_a_damaged_destination(
        mod, tmp_path, monkeypatch):
    """The cleanup branch, which nothing would otherwise run.

    A rename that fails is the one case where the temporary outlives the call,
    and an orphan in the operator's memory directory is exactly the litter that
    makes a later `git status` unreadable.
    """
    src, dst = tmp_path / "src.md", tmp_path / "fact.md"
    src.write_text(BODY, encoding="utf-8")
    dst.write_text("previous\n", encoding="utf-8")

    def _refuse(a, b):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(mod.os, "replace", _refuse)
    with pytest.raises(OSError, match="simulated rename failure"):
        mod._copy_atomic(src, dst)
    monkeypatch.undo()

    assert dst.read_text(encoding="utf-8") == "previous\n", (
        "the destination was damaged by a copy that never completed")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["fact.md", "src.md"]


# ============================================================
# The lock over the shared store
# ============================================================

def test_the_lock_is_keyed_on_the_shared_canonical_store(mod, tmp_path, monkeypatch):
    """Both directions, because keying it on the native store is the plausible
    mistake and it would serialise nothing: the native store is precisely what
    differs between the two racing sessions."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)
    canonical = tmp_path / "data" / "auto-memory"
    other = tmp_path / "data-exec" / "auto-memory"

    assert mod._lock_path(canonical) == mod._lock_path(canonical), (
        "two sessions pointed at one canonical store must derive one lock")
    assert mod._lock_path(canonical) != mod._lock_path(other), (
        "two different canonical stores must not share a lock")
    assert str(tmp_path) in str(mod._lock_path(canonical)), (
        "the sidecar must live under the per-user runtime dir, not inside a "
        "git-tracked store")


def _run_cli(tmp_path, native: Path, canonical: Path, home: Path):
    env = dict(os.environ, HOME=str(home))
    env.pop("USERPROFILE", None)
    return subprocess.run(
        [sys.executable, str(HOOK), "--native", str(native),
         "--canonical", str(canonical)],
        capture_output=True, text=True, timeout=120, env=env, check=False)


@pytest.fixture
def two_stores(tmp_path):
    native = tmp_path / "native"
    canonical = tmp_path / "canonical"
    native.mkdir()
    canonical.mkdir()
    (native / "fact.md").write_text(BODY, encoding="utf-8")
    return native, canonical


def test_a_second_session_reports_the_canonical_store_busy(
        mod, tmp_path, two_stores, monkeypatch):
    """Hold the lock, then run the hook as the other session would.

    The line is `file_lock`'s bounded-degradation message, so seeing it proves
    three things at once: the hook asked for a lock, it asked for it at the path
    derived from the CANONICAL store, and it proceeded rather than hanging a
    SessionStart. The sync still has to complete, which is the whole reason the
    primitive degrades instead of blocking.
    """
    native, canonical = two_stores
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("USERPROFILE", raising=False)
    lock_path = mod._lock_path(canonical)

    from scripts.utils import checkpoint_paths as CP

    with CP.file_lock(lock_path, label="test-holder") as held:
        assert held, "the test could not take the lock it means to contend for"
        proc = _run_cli(tmp_path, native, canonical, home)

    assert proc.returncode == 0, proc.stderr[-1500:]
    assert "memory-reconcile" in proc.stderr and "busy" in proc.stderr, (
        "the hook reported no contention, so it never asked for the lock over "
        f"the canonical store.\nstderr:\n{proc.stderr[-1500:]}")
    assert (canonical / "fact.md").is_file(), (
        "the hook degraded to doing nothing; a bounded lock must proceed")


def test_an_unlocked_store_reconciles_with_no_busy_line(tmp_path, two_stores):
    """The mirror. Without it, a hook that printed "busy" unconditionally, or
    one whose lock was never releasable, would satisfy the test above."""
    native, canonical = two_stores
    home = tmp_path / "home"
    proc = _run_cli(tmp_path, native, canonical, home)
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert "busy" not in proc.stderr, (
        f"an uncontended run reported contention:\n{proc.stderr[-1500:]}")
    assert (canonical / "fact.md").read_text(encoding="utf-8") == BODY


def test_the_reconcile_still_runs_when_the_lock_helper_is_absent(mod, monkeypatch):
    """A public engine clone may not carry `scripts/utils/checkpoint_paths.py`.
    Booting a session on a stale memory store is the loss this hook exists to
    prevent, so a missing helper degrades to unserialised, never to no sync."""
    monkeypatch.setattr(mod, "_CP", None)
    with mod._canonical_lock(Path("/nonexistent/auto-memory")) as held:
        assert held is False
    monkeypatch.undo()


def test_the_lock_is_really_held_when_the_helper_is_present(mod, tmp_path, monkeypatch):
    """The mirror of the degradation above: a context manager that always
    yielded False would pass it while locking nothing, ever."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)
    with mod._canonical_lock(tmp_path / "canonical") as held:
        assert held is True
    assert mod._lock_path(tmp_path / "canonical").is_file()


# ============================================================
# A payload field of the wrong type
# ============================================================

WRONG_TYPES = ["3", "[1]", '{"a": 1}', "true", "1.5"]


@pytest.mark.parametrize("value", WRONG_TYPES)
def test_a_transcript_path_of_the_wrong_type_does_not_crash_the_hook(
        value, tmp_path):
    """The field, not the payload. Driven through the real hook in its real
    process, with the data root and HOME pointed at scratch so nothing here can
    reach the operator's overlay."""
    overlay = tmp_path / "data-root"
    overlay.mkdir()
    env = dict(os.environ, HEADING_OS_DATA=str(overlay), HOME=str(tmp_path))
    env.pop("USERPROFILE", None)
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input='{"transcript_path": %s}' % value,
        capture_output=True, text=True, timeout=120, env=env, check=False)
    assert "Traceback" not in proc.stderr, (
        f"transcript_path={value} crashed the hook:\n{proc.stderr[-1500:]}")
    assert proc.returncode == 0, (
        f"transcript_path={value} exited {proc.returncode}:\n"
        f"{proc.stderr[-1500:]}")


def test_a_transcript_path_that_raises_on_expansion_does_not_crash_the_hook(tmp_path):
    """The other half of the same defect, and the half a type check cannot reach.

    `~nosuchuser42/...` IS a string, so `isinstance` passes it, and then
    `Path.expanduser()` raises `RuntimeError: Could not determine home directory.`
    The resolve therefore has to sit INSIDE main()'s try, not merely be
    type-guarded: the two fixes cover each other for an int and only the try
    covers this. Measured 2026-08-31 with the try in place: exit 0 and
    `[memory-reconcile] store resolve failed: Could not determine home
    directory.`
    """
    overlay = tmp_path / "data-root"
    overlay.mkdir()
    env = dict(os.environ, HEADING_OS_DATA=str(overlay), HOME=str(tmp_path))
    env.pop("USERPROFILE", None)
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input='{"transcript_path": "~nosuchuser42/.claude/projects/p/s.jsonl"}',
        capture_output=True, text=True, timeout=120, env=env, check=False)
    assert "Traceback" not in proc.stderr, (
        f"an unexpandable transcript_path crashed the hook:\n{proc.stderr[-1500:]}")
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert "store resolve failed" in proc.stderr, (
        "the hook neither crashed nor reported the failed resolve; a silent "
        f"skip hides a broken payload.\nstderr:\n{proc.stderr[-1500:]}")


def test_a_string_transcript_path_still_resolves_the_native_store(mod):
    """The mirror. A guard that refused every value would pass every case above
    and quietly stop the hook from ever finding a native store."""
    got = mod._native_from_hook(
        {"transcript_path": "/home/x/.claude/projects/p/s.jsonl"})
    assert got == Path("/home/x/.claude/projects/p/memory")


@pytest.mark.parametrize("value", [3, [1], {"a": 1}, True, "", None])
def test_an_unusable_transcript_path_falls_through_to_the_slug_resolver(mod, value):
    """Not a crash and not a refusal: the cwd-slug fallback still answers, so a
    session whose payload carries a junk field is reconciled rather than skipped."""
    got = mod._native_from_hook({"transcript_path": value, "cwd": "/home/x/work"})
    assert got is not None and got.name == "memory"
    assert "-home-x-work" in str(got)


def test_the_hook_refuses_rather_than_guessing_when_the_slug_resolver_is_absent(
        mod, monkeypatch, capsys):
    """The other import that can fail in a bundled clone. Returning None is the
    right answer; main() already treats it as nothing to reconcile."""
    monkeypatch.setattr(mod, "_CP", None)
    assert mod._native_from_hook({"cwd": "/home/x/work"}) is None
    monkeypatch.undo()
    assert "no slug resolver" in capsys.readouterr().err


# ============================================================
# A hook that said it was registered
# ============================================================

SETTINGS_FILES = [
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/settings.local.linux.json",
    ".claude/settings.local.macos.json",
    ".claude/settings.local.windows.json",
]

# Same extraction shape as tests/test_settings_hook_targets.py: the hook filename
# inside a self-locating launcher command.
HOOK_NAME_RE = re.compile(r"'([A-Za-z0-9_][A-Za-z0-9_.-]*\.py)'")

# Hooks on disk that are deliberately wired nowhere. An entry here is a claim
# that the file's own docstring explains why it ships dead.
#
# EMPTY since 2026-09-01, and that is the intended steady state. It held exactly
# one name, `memory-inject.py`, from 2026-08-31 until the operator retired that
# file; there were 17 hooks on disk and 16 named across the settings files, and
# it was the difference. MEASURED 2026-09-01 after the deletion: 16 on disk, 16
# named, difference zero.
#
# An empty registry makes `test_the_declaration_does_not_outlive_its_hook` below
# unfalsifiable — it loops over this dict, so with nothing in it there is nothing
# it can report. That is stated rather than hidden: it is a dormant guard that
# arms itself the instant a name is added here, and the check that still MEASURES
# something every run is `test_every_hook_on_disk_is_wired_or_declared`, which
# carries its own floor on the corpus it reads.
DECLARED_UNREGISTERED: dict[str, str] = {}


def hooks_named_in(rel: str) -> set[str]:
    """The hook filenames ONE settings file wires."""
    path = ROOT / rel
    if not path.is_file():
        return set()
    settings = json.loads(path.read_text(encoding="utf-8"))
    commands = []
    for entries in (settings.get("hooks") or {}).values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                if hook.get("command"):
                    commands.append(hook["command"])
    status_line = settings.get("statusLine") or {}
    if status_line.get("command"):
        commands.append(status_line["command"])
    names: set[str] = set()
    for command in commands:
        if ".claude" in command and "hooks" in command:
            names.update(HOOK_NAME_RE.findall(command))
    return names


def named_hooks() -> set[str]:
    names: set[str] = set()
    for rel in SETTINGS_FILES:
        names |= hooks_named_in(rel)
    return names


def test_the_extraction_reaches_a_real_registry():
    """Green over an empty corpus otherwise: an extractor that found nothing
    would report every hook as unregistered, and the assertion below would then
    fail for the wrong reason while looking right."""
    named = named_hooks()
    assert len(named) >= 12, f"only extracted {sorted(named)}"
    for expected in ("memory-reconcile.py", "session-start.py", "bridge-hook.py"):
        assert expected in named


SHIPPED_TEMPLATES = [
    ".claude/settings.local.linux.json",
    ".claude/settings.local.macos.json",
    ".claude/settings.local.windows.json",
]


@pytest.mark.parametrize("rel", SHIPPED_TEMPLATES)
def test_each_shipped_template_wires_its_own_hooks(rel):
    """A floor PER SOURCE, because the floor above is over the union.

    `named_hooks()` unions five files, so one template contributing NOTHING
    still clears a floor of 12 on the strength of the other four, and
    `test_every_hook_on_disk_is_wired_or_declared` then reports every hook as
    wired while a whole platform ships them dead. MEASURED 2026-09-01 by
    renaming the linux template's `hooks` key to `hooksXX`: both union tests
    stayed green, and this one goes red.

    What it does NOT catch, said rather than implied: one hook renamed inside a
    template. The count is unchanged, because a wrong name is still a name.
    `tests/test_unattended_resume_on_prompt.py::test_the_hook_is_registered_in_every_shipped_template`
    is the per-hook form of that question, and it exists for one hook.

    Only the three SHIPPED templates are floored. `.claude/settings.json` wires
    one hook by design, and `settings.local.json` is gitignored and
    machine-local, so neither can carry a floor a fresh clone would meet.
    Measured 2026-09-01: 15 hooks in each template. The floor sits below that so
    retiring one does not fail an unrelated test.
    """
    named = hooks_named_in(rel)
    assert len(named) >= 12, (
        f"{rel} wires only {sorted(named)}; a platform template that stops "
        f"naming its hooks ships them dead and the union floor cannot see it")


def test_every_hook_on_disk_is_wired_or_declared():
    """The direction `tests/test_settings_hook_targets.py` does not check.

    It asks that every hook NAMED in settings exists on disk. The reverse
    question is the one that went unanswered for memory-inject.py until it was
    retired on 2026-09-01: a file that called itself a live hook, that nothing
    ran, whose flag the operator could set with no effect and no explanation.

    The floor on `on_disk` is load-bearing now that DECLARED_UNREGISTERED is
    empty. Every exemption this test grants comes from `named_hooks()`, which
    `test_the_extraction_reaches_a_real_registry` floors at 12; the other side
    of the subtraction was floored by nothing. A renamed hooks directory, a glob
    that stopped matching, or a `HOOKS` constant pointed at the wrong tree would
    make `on_disk` empty and `unwired` trivially `[]`, and this test would pass
    having read no hooks at all. MEASURED 2026-09-01: 16 hooks on disk, all 16
    named across the settings files. Floored at 14 so retiring one or two more
    does not fail an unrelated test.
    """
    on_disk = {p.name for p in HOOKS.glob("*.py")}
    assert len(on_disk) >= 14, (
        f"only {len(on_disk)} hook files found under {HOOKS}: {sorted(on_disk)}. "
        "The corpus this test subtracts from is empty or nearly so, which makes "
        "the assertion below green without reading anything.")
    unwired = sorted(on_disk - named_hooks() - set(DECLARED_UNREGISTERED))
    assert unwired == [], (
        f"hooks on disk that no settings file wires: {unwired}. Either register "
        "the hook, or add it to DECLARED_UNREGISTERED with the reason and say so "
        "in its own docstring.")


def test_the_declaration_does_not_outlive_its_hook():
    """A registry entry guarding nothing is the same stale claim wearing the
    other hat: a declared-unregistered hook that is later wired, or deleted,
    fails here instead of silently exempting a file that no longer needs it.

    DORMANT while DECLARED_UNREGISTERED is empty, which it has been since
    2026-09-01. It loops over that dict, so an empty registry means it reads
    nothing and cannot fail; it is kept because the registry is expected to take
    entries again, and it arms itself the moment one is added. It is deliberately
    NOT given an anti-vacuity floor: a floor here would assert that some hook
    ships dead, which is the opposite of what this workspace wants to be true.
    The measuring guard over this corpus is the test above.
    """
    on_disk = {p.name for p in HOOKS.glob("*.py")}
    named = named_hooks()
    stale = sorted(n for n in DECLARED_UNREGISTERED
                   if n not in on_disk or n in named)
    assert stale == [], f"declared-unregistered hooks that are gone or wired: {stale}"
