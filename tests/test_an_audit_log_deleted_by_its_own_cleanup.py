"""An audit log the normal end of a yard's life deleted.

`check_release_gate` appends every authorised commit and push to
`authorised.jsonl` so that, as `_record_release` puts it, "every commit and push
this workspace makes can be traced back to the exact words that authorised it".
The file lived at `_state_dir() / "release" / ...`, and `_state_dir()` resolves
under `WORKSPACE`, the CURRENT CHECKOUT. So every YARD kept its own copy, the
path is gitignored, and `git worktree remove` is the documented last step of a
yard's life. The record that answers "who authorised this commit" was guaranteed
to be destroyed for exactly the sessions the operator was not sitting at.

PRE-FIX MEASUREMENT, taken 2026-09-08 by restoring `.claude/hooks/_dispatch.py`
from HEAD (b5d982e) and running this file against it: **8 failed, 4 passed**.
The four that passed are the containment cases that assert an ABSENCE under a
redirected HOME, and pre-fix they pass for a reason that is not the one they
were written for: the old code wrote into the checkout, so `~/.heading-os` was
empty either way. They are honest anchors only against the fixed code, and
`test_the_same_run_outside_pytest_does_write_there` is the case that stops them
being vacuous by making the same path prove a PRESENCE.
The centre of it, driven through the real entry point in a throwaway second
checkout with `HEADING_OS_RELEASE_LOG` naming a file outside it:

    check_release_gate({"tool_name": "Bash",
                        "tool_input": {"command": "git commit -m x"}, ...})
    -> None                                       (the commit is authorised)
    <checkout>/.claude/state/release/authorised.jsonl   1 line   <- written here
    <durable path>                                     absent
    rm -rf <checkout>                             -> the only record is gone

After the fix the record lands at the durable path, carries the checkout and the
branch it happened in, and survives the removal.

THE OTHER HALF, and the reason it is half the file. A log outside the checkout
is a log a test run can reach, which is the defect class that committed the
engine at `ed7cee1`. Containment has two independent legs and both must fail
before a real record is poisoned: the `HEADING_OS_RELEASE_LOG` pin, and a
refusal to touch the default at all under pytest. Both legs are driven here in
both directions, against a redirected HOME so the positive direction never needs
the operator's real file to prove the negative one.

WHAT THIS FILE DOES NOT ESTABLISH. It says nothing about whether the gate's
DECISION is right; `tests/test_a_release_the_operator_never_asked_for.py` and
`tests/test_a_brief_from_another_session_that_authorised_a_release.py` own that.
It does not touch the operator's real log, and it does not migrate the records
written under the old path: that is an operator action in the main clone,
written out in `docs/HOOKS-REFERENCE.md`.

Run: .venv/bin/python -m pytest
     tests/test_an_audit_log_deleted_by_its_own_cleanup.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Every test below spawns children that inherit `os.environ`, and on the
# operator's machine `HEADING_OS_DATA` there names the live overlay. Nothing in
# this file has any business near it: the subject is a log OUTSIDE both
# repositories. The pin removes a reachability these tests never wanted, which
# is the criterion `scratch_data_root` states for opting in.
pytestmark = pytest.mark.usefixtures("scratch_data_root")

ROOT = Path(__file__).resolve().parent.parent
GATE_REL = ".claude/hooks/_dispatch.py"

# The support files a throwaway checkout needs before `_dispatch.py` imports.
_CLONE_FILES = (
    GATE_REL,
    "scripts/__init__.py",
    "scripts/utils/__init__.py",
    "scripts/utils/pathnorm.py",
)

# Every environment name that tells a child process it is under pytest. Spelled
# out rather than read from the source, so this is an independent statement of
# what the belt is and goes red when the source drops one of them.
_PYTEST_NAMES = ("PYTEST_CURRENT_TEST", "PYTEST_VERSION", "PYTEST_XDIST_WORKER")

# Floors, outside every loop below. MEASURED 2026-09-08 on this tree.
MIN_CLONE_FILES = 4
CONCURRENT_WRITERS = 6
RECORDS_EACH = 30


def _load_gate():
    spec = importlib.util.spec_from_file_location(
        "dispatch_release_log_probe", ROOT / GATE_REL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dispatch_release_log_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


D = _load_gate()


# ==========================================================================
# A throwaway second checkout, and a way to drive the gate inside it
# ==========================================================================

_DRIVER = """\
import importlib.util, json, sys
from pathlib import Path
here = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "gate", here / ".claude" / "hooks" / "_dispatch.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["gate"] = mod
spec.loader.exec_module(mod)
print(json.dumps({"verdict": mod.check_release_gate(json.loads(sys.argv[1]))}))
"""


def _transcript(path: Path, text: str) -> Path:
    """One turn in the shape the gate reads: the capped record and the full one."""
    flat = " ".join(text.split())
    capped = flat if len(flat) <= 200 else flat[:200] + "…"
    path.write_text("\n".join([
        json.dumps({"type": "last-prompt", "lastPrompt": capped}),
        json.dumps({"type": "user", "promptSource": "typed",
                    "message": {"content": text}}),
    ]) + "\n", encoding="utf-8")
    return path


def _checkout(base: Path, name: str = "checkout") -> Path:
    """A second checkout of the engine, holding only what the gate imports.

    `WORKSPACE` in `_dispatch.py` is `__file__`'s third parent, so a copy of the
    hook three levels down makes this directory the checkout as far as the gate
    is concerned. That is what makes it stand in for a YARD here without
    registering a worktree beside the live ones.
    """
    assert len(_CLONE_FILES) >= MIN_CLONE_FILES
    clone = base / name
    for rel in _CLONE_FILES:
        dest = clone / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dest)
    (clone / "CLAUDE.md").write_text("throwaway\n", encoding="utf-8")
    (clone / "_drive.py").write_text(_DRIVER, encoding="utf-8")
    return clone


def _drive(clone: Path, transcript: Path, *, home: Path, pin: str | None,
           under_pytest: bool = True, command: str = "git commit -m x"):
    """Run the real entry point in a child rooted at `clone`.

    `HEADING_OS_STATE_DIR` is REMOVED from the child, and that is load-bearing
    rather than tidiness. `tests/conftest.py` sets it for the whole suite, so a
    child that inherited it would send even the pre-fix code to the suite's
    scratch state directory, which is outside the checkout and survives its
    removal. The reproduction would then pass against the defect.
    """
    env = os.environ.copy()
    env.pop("HEADING_OS_STATE_DIR", None)
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(clone)
    if pin is None:
        env.pop("HEADING_OS_RELEASE_LOG", None)
    else:
        env["HEADING_OS_RELEASE_LOG"] = pin
    if not under_pytest:
        for name in _PYTEST_NAMES:
            env.pop(name, None)
    payload = json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": command},
                          "transcript_path": str(transcript)})
    proc = subprocess.run(
        [sys.executable, str(clone / "_drive.py"), payload],
        capture_output=True, text=True, env=env, cwd=str(clone))
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])["verdict"]


def _lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


# ==========================================================================
# The defect: the record died with the checkout that made it
# ==========================================================================

def test_a_record_written_from_a_second_checkout_survives_its_removal(tmp_path):
    """The reproduction, both halves of it, at the real entry point."""
    home = tmp_path / "home"
    home.mkdir()
    durable = tmp_path / "durable" / "authorised.jsonl"
    clone = _checkout(tmp_path)
    transcript = _transcript(tmp_path / "session.jsonl", "commit this")

    assert _drive(clone, transcript, home=home, pin=str(durable)) is None

    inside = clone / ".claude" / "state" / "release" / "authorised.jsonl"
    assert not inside.exists(), (
        "the release log was written inside the checkout, where the yard's "
        "removal destroys it")

    records = _lines(durable)
    assert len(records) == 1, f"expected one durable record, got {records}"
    assert records[0]["action"] == "commit"
    assert records[0]["authorised_by"] == "commit this"

    shutil.rmtree(clone)
    assert not clone.exists()
    assert len(_lines(durable)) == 1, (
        "removing the checkout took the audit record with it")


def test_the_record_names_the_checkout_and_the_branch_it_happened_in(tmp_path):
    """One shared file needs to say WHICH tree released, or it answers less.

    The checkout here is a real `git worktree`, of a scratch repository built
    under `tmp_path` and never of the live one, because a worktree's `.git` is a
    FILE holding `gitdir:` and that is the exact case a naive HEAD read misses.
    """
    home = tmp_path / "home"
    home.mkdir()
    durable = tmp_path / "durable" / "authorised.jsonl"

    origin = tmp_path / "origin"
    origin.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(origin), *a],  # noqa: E731
                                    check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    (origin / "seed").write_text("x\n", encoding="utf-8")
    run("add", "seed")
    run("-c", "user.email=t@t", "-c", "user.name=T", "commit", "-qm", "seed")
    linked = tmp_path / "linked"
    run("worktree", "add", "-q", "-b", "yard-example", str(linked))

    for rel in _CLONE_FILES:
        dest = linked / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dest)
    (linked / "CLAUDE.md").write_text("throwaway\n", encoding="utf-8")
    (linked / "_drive.py").write_text(_DRIVER, encoding="utf-8")

    assert (linked / ".git").is_file(), "a worktree's .git must be the file case"
    transcript = _transcript(tmp_path / "session.jsonl", "commit this")
    assert _drive(linked, transcript, home=home, pin=str(durable)) is None

    record = _lines(durable)[0]
    assert record["branch"] == "yard-example"
    assert Path(record["checkout"]).resolve() == linked.resolve()


def test_the_release_log_is_the_one_state_this_checkout_does_not_own(tmp_path):
    """The split, asserted: guard state stays per-checkout, the trail leaves.

    A later reader looking to "consolidate" the two helpers goes red here.
    """
    state = D._state_dir()
    assert D._GRAPH_STATE_DIR.is_relative_to(state)
    assert D._FANOUT_STATE_DIR.is_relative_to(state)
    assert not D._RELEASE_LOG_DEFAULT.is_relative_to(state)
    assert not D._RELEASE_LOG_DEFAULT.is_relative_to(D.WORKSPACE)


# ==========================================================================
# The trap: a path outside the checkout is a path a test run can reach
# ==========================================================================

def test_a_run_with_no_pin_writes_nothing_to_the_default_log(tmp_path):
    """Leg two of the containment, driven with the pin removed.

    HOME is redirected, so the default this asserts is empty is the same file
    the resolver would compute, without the operator's real one ever being in
    reach of the assertion or the write.
    """
    home = tmp_path / "home"
    home.mkdir()
    clone = _checkout(tmp_path)
    transcript = _transcript(tmp_path / "session.jsonl", "commit this")

    assert _drive(clone, transcript, home=home, pin=None) is None

    default = home / ".heading-os" / "release" / "authorised.jsonl"
    assert not default.exists(), (
        "a pytest run appended to the machine's authorised-release log")
    assert not (clone / ".claude" / "state" / "release").exists()


def test_the_same_run_outside_pytest_does_write_there(tmp_path):
    """The other direction, or the test above would pass over a wrong path."""
    home = tmp_path / "home"
    home.mkdir()
    clone = _checkout(tmp_path)
    transcript = _transcript(tmp_path / "session.jsonl", "commit this")

    assert _drive(clone, transcript, home=home, pin=None,
                  under_pytest=False) is None

    default = home / ".heading-os" / "release" / "authorised.jsonl"
    records = _lines(default)
    assert len(records) == 1, f"expected the default location to be used: {records}"
    assert records[0]["authorised_by"] == "commit this"


@pytest.mark.parametrize("name", _PYTEST_NAMES)
def test_any_one_pytest_marker_is_enough_to_hold_the_belt(tmp_path, name):
    """Each name alone contains the run, so a harness that drops one is covered."""
    home = tmp_path / "home"
    home.mkdir()
    clone = _checkout(tmp_path)
    transcript = _transcript(tmp_path / "session.jsonl", "commit this")

    env_names = [n for n in _PYTEST_NAMES if n != name]
    env = os.environ.copy()
    env.pop("HEADING_OS_STATE_DIR", None)
    env.pop("HEADING_OS_RELEASE_LOG", None)
    for other in env_names:
        env.pop(other, None)
    env[name] = "1"
    env["HOME"] = str(home)
    proc = subprocess.run(
        [sys.executable, str(clone / "_drive.py"),
         json.dumps({"tool_name": "Bash",
                     "tool_input": {"command": "git commit -m x"},
                     "transcript_path": str(transcript)})],
        capture_output=True, text=True, env=env, cwd=str(clone))
    assert proc.returncode == 0, proc.stderr
    assert not (home / ".heading-os" / "release" / "authorised.jsonl").exists()


def test_a_relative_pin_records_nothing_rather_than_falling_back(tmp_path):
    """Fail-closed, and deliberately unlike `_state_dir()`'s relative handling."""
    home = tmp_path / "home"
    home.mkdir()
    clone = _checkout(tmp_path)
    transcript = _transcript(tmp_path / "session.jsonl", "commit this")

    assert _drive(clone, transcript, home=home, pin="release/authorised.jsonl",
                  under_pytest=False) is None

    assert not (home / ".heading-os" / "release" / "authorised.jsonl").exists()
    assert not (clone / "release" / "authorised.jsonl").exists()


# ==========================================================================
# Concurrency, size, and the promise that this never refuses a release
# ==========================================================================

_APPENDER = """\
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("gate", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
sys.modules["gate"] = mod
spec.loader.exec_module(mod)
word = sys.argv[3] * 380
for i in range(int(sys.argv[2])):
    mod._record_release("commit", f"git commit -m {word}", f"{word} {i}")
"""


def test_concurrent_checkouts_never_interleave_into_a_corrupt_line(tmp_path):
    """One file, many writers: every line still parses, and none is lost."""
    durable = tmp_path / "authorised.jsonl"
    appender = tmp_path / "appender.py"
    appender.write_text(_APPENDER, encoding="utf-8")

    env = os.environ.copy()
    env.pop("HEADING_OS_STATE_DIR", None)
    env["HEADING_OS_RELEASE_LOG"] = str(durable)
    procs = [
        subprocess.Popen(
            [sys.executable, str(appender), str(ROOT / GATE_REL),
             str(RECORDS_EACH), chr(ord("a") + n)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for n in range(CONCURRENT_WRITERS)
    ]
    for proc in procs:
        assert proc.wait() == 0, proc.stderr.read().decode()

    raw = durable.read_text(encoding="utf-8").splitlines()
    expected = CONCURRENT_WRITERS * RECORDS_EACH
    assert len(raw) == expected, f"{len(raw)} lines, expected {expected}"
    for line in raw:
        json.loads(line)          # a spliced record raises here


def test_the_worst_case_record_is_one_bounded_line(tmp_path, monkeypatch):
    """The size behind the interleaving argument, measured rather than assumed.

    All FOUR capped fields filled with characters JSON must escape as `\\u0001`,
    six bytes for one, which is the expansion that dominates and is the true
    bound rather than the realistic one. A control character in a path is legal
    on Linux, so the checkout field gets the same treatment as the prompt.
    MEASURED 2026-09-08: 8517 bytes, one line. The four caps bound
    it at 8522; this construction is five short because the leading `/` of the
    checkout path is a character JSON does not escape.
    """
    durable = tmp_path / "authorised.jsonl"
    monkeypatch.setenv("HEADING_OS_RELEASE_LOG", str(durable))
    monkeypatch.setattr(D, "WORKSPACE", Path("/" + "\x01" * 500))
    monkeypatch.setattr(D, "_checkout_branch", lambda _root: "\x01" * 500)

    D._record_release("commit", "\x01" * 4000, "\x01" * 4000)

    blob = durable.read_bytes()
    assert blob.count(b"\n") == 1
    assert len(blob) == 8517, f"worst-case line is {len(blob)} bytes"
    record = json.loads(blob)
    assert len(record["command"]) == 400
    assert len(record["authorised_by"]) == 400
    assert len(record["checkout"]) == 400
    assert len(record["branch"]) == 200


def test_an_unusable_log_path_never_refuses_the_release(tmp_path, capsys):
    """Telemetry that cannot write is still telemetry, not a wall."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x\n", encoding="utf-8")
    transcript = _transcript(tmp_path / "session.jsonl", "commit this")

    os.environ["HEADING_OS_RELEASE_LOG"] = str(blocker / "release" / "log.jsonl")
    try:
        verdict = D.check_release_gate(
            {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"},
             "transcript_path": str(transcript)})
    finally:
        os.environ.pop("HEADING_OS_RELEASE_LOG", None)

    assert verdict is None, "an unwritable audit log refused an authorised commit"
    assert "release log unavailable" in capsys.readouterr().err
