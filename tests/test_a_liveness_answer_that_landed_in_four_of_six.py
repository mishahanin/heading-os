#!/usr/bin/env python3
"""`except OSError` read "alive, and not yours to signal" as "dead".

On POSIX, `os.kill(pid, 0)` raising `PermissionError` means the process EXISTS
and belongs to another user. `ProcessLookupError` is the one that means it is
gone. A guard that catches the base `OSError` cannot tell them apart, so it
answers "dead" about a running daemon owned by a service account.

`scripts/utils/pid_liveness.py` was written for exactly this, and its docstring
records the outcome it prevents: a pulse script that spawns when it sees "dead"
started a SECOND daemon beside the first. The correct answer had landed in four
of the six places that ask the question. MEASURED 2026-08-29 against PID 1,
which is alive and unsignalable by this uid:

    True   scripts/utils/pid_liveness.py   pid_is_running    (canonical)
    True   scripts/fireside-pulse.py       posix branch
    True   scripts/fireside-bot-daemon.py  POSIX half
    False  scripts/sentinel.py             _is_pid_alive
    False  scripts/marp_render.py          _is_process_running

The two wrong ones then act on the verdict, and both act by DELETING. Measured
the same day in a scratch directory, with a live PID in the state file:

    sentinel   `--status` printed "NOT running (stale PID file)" and removed the
               PID file, which is the only handle `--stop` has.
    marp       `watch_status()` printed "no longer running. State cleaned up."
               and removed the watch state AND the generated theme file.

Both are read-only-sounding commands. Sentinel matters most, because it runs on
the Steward VM under a service account rather than the operator's shell, which
is precisely the case that raises `PermissionError`.

`scripts/fireside-bot-daemon.py` was wrong on the other platform: its Windows
branch read a NULL handle from `OpenProcess` as "no such process", when it is
also what access-denied returns, and it used `ctypes.windll`, which never
populates the ctypes error slot, so it could not have told the difference.

All three now call the shared function. The wrapper NAMES stay, because callers
and tests patch them by name, and a delegating wrapper with the reason written
above it is what stops the next reader restoring the copy.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import signal
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.pid_liveness import pid_is_running  # noqa: E402
from tests.repo_files import read_sources, tracked_python_files  # noqa: E402


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ============================================================
# The answer itself, on a real live process
# ============================================================

@pytest.fixture(scope="module")
def unsignalable_pid() -> int:
    """A PID that is ALIVE and that this uid may not signal.

    PID 1 on Linux. The whole defect is about this case, so a fixture that
    silently fell back to a signalable process would make every test below
    vacuous; it skips instead.
    """
    try:
        os.kill(1, 0)
    except PermissionError:
        return 1
    except ProcessLookupError:  # pragma: no cover - no init on this host
        pytest.skip("PID 1 absent")
    pytest.skip("this uid can signal PID 1, so the defect cannot be reproduced")


def test_the_fixture_supplies_a_pid_this_uid_cannot_signal(unsignalable_pid):
    """The fixture is the whole experiment. Were it to hand back a signalable
    PID instead of skipping, every test that takes it would still pass and none
    of them would touch `PermissionError`, which is the defect."""
    with pytest.raises(PermissionError):
        os.kill(unsignalable_pid, 0)


def test_the_shared_answer_calls_an_unsignalable_process_alive(unsignalable_pid):
    assert pid_is_running(unsignalable_pid) is True


def test_a_pid_that_is_really_gone_is_dead():
    """The mirror. An answer that said True for everything would pass the test
    above and make `stop` a permanent no-op."""
    assert pid_is_running(2 ** 22 - 1) is False
    assert pid_is_running(0) is False
    assert pid_is_running(-1) is False


def test_our_own_process_is_alive():
    assert pid_is_running(os.getpid()) is True


# ============================================================
# Every wrapper gives the shared answer
# ============================================================

WRAPPERS = [
    ("sentinel", "_is_pid_alive"),
    ("marp_render", "_is_process_running"),
    ("fireside-bot-daemon", "_pid_is_running"),
]


@pytest.mark.parametrize("stem, func", WRAPPERS, ids=[w[0] for w in WRAPPERS])
def test_the_wrapper_agrees_with_the_shared_answer(stem, func, unsignalable_pid):
    module = _load(stem, f"liveness_probe_{stem.replace('-', '_')}")
    answer = getattr(module, func)
    assert answer(unsignalable_pid) is True, (
        f"{stem}.{func} calls a live process dead, which is the defect")
    assert answer(2 ** 22 - 1) is False, (
        f"{stem}.{func} calls a dead process alive")


# ============================================================
# What the wrong answer destroyed
# ============================================================

def test_a_watch_status_over_a_live_pid_keeps_the_state_and_the_theme(tmp_path,
                                                                      monkeypatch):
    """`watch_status()` reports. It used to delete, on a verdict that was wrong
    for any process this uid cannot signal."""
    marp = _load("marp_render", "liveness_probe_marp_status")
    theme = tmp_path / "theme.css"
    theme.write_text("/* generated */\n", encoding="utf-8")
    state = tmp_path / "watch.json"
    state.write_text(json.dumps({"pid": os.getpid(), "theme_path": str(theme)}),
                     encoding="utf-8")
    monkeypatch.setattr(marp, "WATCH_STATE_FILE", state)

    result = marp.watch_status()

    assert result["running"] is True, result
    assert state.exists(), "the watch state of a LIVE session was deleted"
    assert theme.exists(), "the generated theme of a LIVE session was deleted"


def test_a_watch_status_over_a_dead_pid_still_cleans_up(tmp_path, monkeypatch):
    """The mirror. Never deleting would leave a stale session blocking the next
    `watch_start`, which is what the cleanup exists to prevent."""
    marp = _load("marp_render", "liveness_probe_marp_status_dead")
    theme = tmp_path / "theme.css"
    theme.write_text("/* generated */\n", encoding="utf-8")
    state = tmp_path / "watch.json"
    state.write_text(json.dumps({"pid": 2 ** 22 - 1, "theme_path": str(theme)}),
                     encoding="utf-8")
    monkeypatch.setattr(marp, "WATCH_STATE_FILE", state)

    result = marp.watch_status()

    assert result["running"] is False, result
    assert not state.exists()
    assert not theme.exists()


def test_a_sentinel_status_over_its_own_live_pid_keeps_the_pid_file(tmp_path,
                                                                    monkeypatch,
                                                                    capsys):
    """`--status` used to print "NOT running (stale PID file)" over a live
    daemon and remove the file `--stop` needs. The identity check is stubbed
    true here, so this measures the liveness verdict and nothing else."""
    sentinel = _load("sentinel", "liveness_probe_sentinel")
    pid_file = tmp_path / "sentinel.pid"
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    monkeypatch.setattr(sentinel, "PID_FILE", pid_file)
    monkeypatch.setattr(sentinel, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(sentinel, "_pid_is_sentinel", lambda pid: True)

    sentinel.check_status()

    assert "RUNNING" in capsys.readouterr().out
    assert pid_file.exists(), "a read-only --status deleted a live daemon's PID file"


def test_a_sentinel_status_over_a_dead_pid_still_clears_the_file(tmp_path,
                                                                 monkeypatch,
                                                                 capsys):
    """The mirror. A genuinely stale PID file should go."""
    sentinel = _load("sentinel", "liveness_probe_sentinel_dead")
    pid_file = tmp_path / "sentinel.pid"
    pid_file.write_text(f"{2 ** 22 - 1}\n", encoding="utf-8")
    monkeypatch.setattr(sentinel, "PID_FILE", pid_file)
    monkeypatch.setattr(sentinel, "STATE_FILE", tmp_path / "state.json")

    sentinel.check_status()

    assert "NOT running" in capsys.readouterr().out
    assert not pid_file.exists()


def _kill_recorder(calls):
    """Records every signal, and answers "gone" to any direct liveness probe.

    That second half is the point. A wrapper that asks the question inline,
    with `os.kill(pid, 0)` instead of the shared function, reads this recorder
    as a dead process and skips the escalation, so the private copy cannot come
    back without failing the test below.
    """
    def kill(pid, sig):
        calls.append((pid, sig))
        if sig == 0:
            raise ProcessLookupError(pid)
    return kill


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX escalation path")
def test_a_stop_escalates_to_sigkill_when_the_process_outlives_sigterm(tmp_path,
                                                                       monkeypatch):
    """`stop_daemon` asked the liveness question a fifth time, inline, and
    `except OSError` swallowed `PermissionError` there too. That is the one
    case where escalating matters most: a daemon that is alive, is not ours to
    signal, and ignored the SIGTERM. It reported the daemon stopped instead."""
    sentinel = _load("sentinel", "liveness_probe_sentinel_kill")
    pid_file = tmp_path / "sentinel.pid"
    pid_file.write_text("424242\n", encoding="utf-8")
    monkeypatch.setattr(sentinel, "PID_FILE", pid_file)
    monkeypatch.setattr(sentinel, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(sentinel, "_pid_is_sentinel", lambda pid: True)
    monkeypatch.setattr(sentinel, "pid_is_running", lambda pid: True)
    monkeypatch.setattr(sentinel.time, "sleep", lambda seconds: None)
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(sentinel.os, "kill", _kill_recorder(calls))

    sentinel.stop_daemon()

    assert (424242, signal.SIGTERM) in calls
    assert (424242, signal.SIGKILL) in calls, (
        "a daemon that survived SIGTERM was left running and reported stopped")


@pytest.mark.skipif(sys.platform == "win32", reason="the POSIX escalation path")
def test_a_stop_does_not_sigkill_a_process_that_already_went(tmp_path, monkeypatch):
    """The mirror. Escalating unconditionally would SIGKILL a PID that the OS
    has already freed and may have handed to somebody else."""
    sentinel = _load("sentinel", "liveness_probe_sentinel_nokill")
    pid_file = tmp_path / "sentinel.pid"
    pid_file.write_text("424242\n", encoding="utf-8")
    monkeypatch.setattr(sentinel, "PID_FILE", pid_file)
    monkeypatch.setattr(sentinel, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(sentinel, "_pid_is_sentinel", lambda pid: True)
    monkeypatch.setattr(sentinel, "pid_is_running", lambda pid: False)
    monkeypatch.setattr(sentinel.time, "sleep", lambda seconds: None)
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(sentinel.os, "kill", _kill_recorder(calls))

    sentinel.stop_daemon()

    assert (424242, signal.SIGTERM) in calls
    assert (424242, signal.SIGKILL) not in calls


# ============================================================
# And there is no sixth copy
# ============================================================

def _asks_liveness(node: ast.AST) -> bool:
    """`os.kill(pid, 0)`: signal zero is the liveness probe, not a signal."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "kill" and len(node.args) == 2):
        return False
    second = node.args[1]
    return isinstance(second, ast.Constant) and second.value == 0


def liveness_probe_sites(corpus) -> list[str]:
    """(path:line) for every hand-rolled liveness probe in the corpus.

    Pure, so it can be measured on synthetic source. Over a corrected tree it
    reports only the declared sites, and deleting the line that appends a
    finding would change no live result, which is why the synthetic cases below
    exist at all.
    """
    out = []
    for rel, source in corpus:
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover - another test's job
            continue
        for node in ast.walk(tree):
            if _asks_liveness(node):
                out.append(f"{rel}:{node.lineno}")
    return out


_PROBE_FIXTURE = "def alive(p):\n    os.kill(p, 0)\n    return True\n"
_SIGNAL_FIXTURE = "os.kill(pid, signal.SIGTERM)\n"
_INNOCENT_FIXTURE = "proc.kill()\nos.killpg(g, 0)\n"


def test_the_detector_fires_on_a_hand_rolled_probe():
    assert liveness_probe_sites([("a.py", _PROBE_FIXTURE)]) == ["a.py:2"]


def test_the_detector_leaves_a_real_signal_alone():
    """Sending SIGTERM is not asking a question, and flagging it would put this
    rule in the way of every daemon that stops itself."""
    assert liveness_probe_sites([("a.py", _SIGNAL_FIXTURE)]) == []


def test_the_detector_leaves_a_different_call_alone():
    assert liveness_probe_sites([("a.py", _INNOCENT_FIXTURE)]) == []


def test_the_detector_reports_both_ways_over_one_corpus():
    corpus = [("probe.py", _PROBE_FIXTURE), ("signal.py", _SIGNAL_FIXTURE)]
    assert liveness_probe_sites(corpus) == ["probe.py:2"]
    assert liveness_probe_sites(corpus[1:]) == []


# `scripts/utils/pid_liveness.py` owns the question. Every other entry states
# why that module asks it again for itself.
DECLARED_LIVENESS_SITES = {
    "scripts/utils/pid_liveness.py":
        "the owner; this is the one implementation and the one probe",
    "scripts/fireside-pulse.py":
        "a deliberately STRICTER contract, kept and not merged: its Windows half "
        "answers alive for ANY OpenProcess failure, where the shared one answers "
        "alive only for access-denied. Pulse spawns on a 'dead' verdict, so it "
        "refuses to guess. Merging the two would change behaviour with no defect "
        "measured against it, so the divergence is written down instead of hidden",
    "tests/test_a_liveness_answer_that_landed_in_four_of_six.py":
        "this file's own detector fixtures, which must keep spelling the shape",
    "tests/test_a_liveness_answer_that_raised_instead_of_answering.py":
        "two probes that pin `pid_liveness.PID_CEILING` to the number the kernel "
        "really takes: os.kill must ACCEPT the ceiling and raise OverflowError "
        "one above it. Asking `pid_is_running` there would ask the constant "
        "about itself, and it does - measured 2026-09-01, a ceiling of 2**30 "
        "passed every other test in that file",
}


def _corpus() -> list[tuple[str, str]]:
    # Read through `read_sources`. The walk and the read are two moments, and
    # two tests in this suite write a temporary `.py` INTO `tests/` and remove it
    # moments later; on 2026-09-01 exactly that killed a sibling sweep with
    # FileNotFoundError and blocked a push on a tree where nothing was wrong.
    # This is a scan: a module that is gone asks liveness of nothing, so it is
    # skipped and named in a warning. The decode branch is not carried over -
    # a tracked `.py` that is not UTF-8 is a real fault and must still raise.
    return [(path.relative_to(ROOT).as_posix(), text)
            for path, text in read_sources(
                tracked_python_files(("scripts", ".claude", "tests")))]


@pytest.mark.corpus
def test_the_sweep_reaches_a_real_corpus():
    corpus = _corpus()
    assert len(corpus) > 500, f"only {len(corpus)} sources read"
    assert any(rel.endswith("pid_liveness.py") for rel, _ in corpus)


@pytest.mark.corpus
def test_no_module_asks_liveness_for_itself():
    undeclared = sorted({site.split(":")[0] for site in liveness_probe_sites(_corpus())}
                        - set(DECLARED_LIVENESS_SITES))
    assert undeclared == [], (
        "`os.kill(pid, 0)` raising PermissionError means the process EXISTS. A "
        "private copy of this question has been wrong in four modules. Call "
        "scripts/utils/pid_liveness.pid_is_running, or add an entry to "
        f"DECLARED_LIVENESS_SITES saying why this module asks for itself: "
        f"{undeclared}")


# The three checks below are pure functions for one reason: over a corrected
# tree each of them returns empty, so deleting the line that COLLECTS a
# violation changes no live result and the rule silently stops existing. The
# synthetic cases are what prove each still discriminates.

def stale_declarations(declared, live_sites) -> list[str]:
    """Declared paths that no longer hold a probe. A declaration is a standing
    exemption; one that outlives its site exempts whatever is written there
    next."""
    live = {site.split(":")[0] for site in live_sites}
    return sorted(k for k in declared if k not in live)


def declarations_without_a_reason(declared) -> list[str]:
    return sorted(k for k, v in declared.items() if not v.strip())


def delegates_to_shared_answer(source: str) -> bool:
    """A wrapper is a delegation only while the import is there."""
    return "from scripts.utils.pid_liveness import pid_is_running" in source


def test_staleness_fires_on_a_declaration_with_no_site():
    assert stale_declarations({"gone.py": "why"}, ["kept.py:3"]) == ["gone.py"]


def test_staleness_stays_quiet_when_every_declaration_has_a_site():
    assert stale_declarations({"kept.py": "why"}, ["kept.py:3"]) == []


def test_the_reason_check_fires_on_a_blank_reason():
    assert declarations_without_a_reason({"a.py": "  \n", "b.py": "why"}) == ["a.py"]


def test_the_reason_check_stays_quiet_when_every_reason_is_written():
    assert declarations_without_a_reason({"a.py": "why", "b.py": "also why"}) == []


def test_the_import_check_fires_on_a_module_that_stopped_delegating():
    assert delegates_to_shared_answer("import os\n\ndef alive(p):\n    pass\n") is False


def test_the_import_check_passes_a_module_that_delegates():
    assert delegates_to_shared_answer(
        "from scripts.utils.pid_liveness import pid_is_running\n") is True


@pytest.mark.corpus
def test_the_declaration_list_does_not_outlive_its_sites():
    stale = stale_declarations(DECLARED_LIVENESS_SITES, liveness_probe_sites(_corpus()))
    assert stale == [], f"declared liveness sites that no longer exist: {stale}"


def test_every_declaration_carries_a_reason():
    empty = declarations_without_a_reason(DECLARED_LIVENESS_SITES)
    assert empty == [], f"declared with no reason written down: {empty}"


def test_the_three_repaired_modules_import_the_shared_answer():
    for rel in ("scripts/sentinel.py", "scripts/marp_render.py",
                "scripts/fireside-bot-daemon.py"):
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert delegates_to_shared_answer(source), rel
