"""scripts/memory.py is a thin facade: prove each subcommand dispatches to the
correct existing script with the right args, that reconcile uses CLI mode (never a
bare no-op hook call), and that --help lists every shipped subcommand.

No backing script actually runs. `scripts.memory` shells out through the name
`subprocess` bound in its own module namespace, and every test here replaces that
NAME with a recorder; the test asserts the argv the facade WOULD have executed.

Two isolation properties are load-bearing, and both were absent until 2026-08-31.

1. The data root is pinned at tmp_path for EVERY test in this module, autouse, so
   a test added later inherits it without remembering to. `cmd_status` and
   `cmd_reconcile` both call `get_data_root()` at CALL time (scripts/memory.py:63
   and :99), which on this machine resolves `HEADING_OS_DATA` straight to the
   operator's live overlay. `cmd_reconcile` then hands that path to a CHILD
   process as `--canonical`, and .claude/hooks/memory-reconcile.py:84-89 does a
   bidirectional newest-wins sync that OVERWRITES `MEMORY.md`. Measured in a
   sandbox on 2026-08-31: one unfaked `cmd_reconcile` wrote 232 files into
   `<data-root>/auto-memory/`. The in-process overlay guard in tests/conftest.py
   cannot see a child process (its own comment says so, conftest.py:306-308), so
   the recorder was the only thing standing between this module and the
   operator's live memory index.

2. The recorder is installed on the module's NAME, never on the shared stdlib
   object. `monkeypatch.setattr(cli.subprocess, "run", fake)` reaches
   `sys.modules["subprocess"]` itself and poisons every other module in the
   interpreter for the duration of the test, which is invisible in file order and
   only shows up under random ordering.

`_RecordingSubprocess.run` additionally REFUSES an argv naming a live sink, so a
future test that installs a real runner, or a mutation that restores one, fails
loudly at the attempt instead of quietly rewriting the operator's memory.
"""
from __future__ import annotations

import os
import subprocess as _stdlib_subprocess
import sys
from pathlib import Path

import pytest

from scripts import memory as cli
from scripts.utils.paths import get_data_root, get_workspace_root

PY = cli.PY
ROOT = cli.ROOT

# Captured at import, before any fixture can repoint anything: the two real sinks
# a child of this module could reach. `HEADING_OS_DATA` is set on this machine, so
# an unpinned child inherits the operator's overlay verbatim.
#
# Only the overlay is AUTO-refused. The native harness store is what
# `cmd_reconcile` is supposed to resolve — refusing it would make the subcommand
# untestable — and it is protected by the stronger property instead: this module
# never spawns a child at all. Both sinks are still detectable, and the guard's
# negative case drives both, so the day a real runner is installed the detector
# already knows the second address.
LIVE_OVERLAY = get_data_root().resolve()
LIVE_NATIVE_STORE = (Path.home() / ".claude" / "projects").resolve()
LIVE_SINKS = (LIVE_OVERLAY, LIVE_NATIVE_STORE)
_AUTO_REFUSED = (LIVE_OVERLAY,)

# The stdlib function as it exists before any test runs. A test asserts this
# object is still bound to `subprocess.run` while a recorder is installed.
REAL_SUBPROCESS_RUN = _stdlib_subprocess.run


class LiveSinkRefused(AssertionError):
    """A test tried to hand a child process a path in the operator's live data."""


def live_sink_in(argv, sinks=_AUTO_REFUSED) -> str | None:
    """The first argv element that lands inside one of `sinks`, or None.

    Prefix comparison on a resolved path, not a substring: a tmp_path that merely
    happens to contain the overlay's name is not the overlay, and `..` inside an
    argument must not launder its way past the check.
    """
    for element in argv:
        try:
            candidate = Path(os.fspath(element)).expanduser().resolve()
        except (TypeError, ValueError, OSError):
            continue
        for sink in sinks:
            if candidate == sink or sink in candidate.parents:
                return f"{element} -> {sink}"
    return None


class _Result:
    returncode = 0


class _RecordingSubprocess:
    """Stands in for the `subprocess` NAME inside scripts.memory.

    A module object, not the stdlib module's `run` attribute. Rebinding the
    attribute would reach `sys.modules["subprocess"]`, which every other module
    in the interpreter shares.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, argv, *a, **k):
        offender = live_sink_in(argv)
        if offender is not None:
            raise LiveSinkRefused(
                "scripts/memory.py was about to spawn a child pointed at the "
                f"operator's live data ({offender}). Pin HEADING_OS_DATA at a "
                "tmp_path before anything that resolves the data root."
            )
        self.calls.append(list(argv))
        return _Result()


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch) -> _RecordingSubprocess:
    """Pin the data root at tmp_path and make a real child impossible.

    Autouse and module-wide on purpose. The per-test opt-in this replaced meant a
    new test in this file reached live data by default, and the default is the
    thing that has to be safe.

    The directory is created because `env_data_root()` RAISES on a
    `HEADING_OS_DATA` naming a path that does not exist (scripts/utils/paths.py)
    rather than falling through to the live overlay. Both properties are wanted:
    the pin holds, and a broken pin fails closed.
    """
    data_root = tmp_path / "data-root"
    (data_root / "auto-memory").mkdir(parents=True)
    monkeypatch.setenv("HEADING_OS_DATA", str(data_root))
    recorder = _RecordingSubprocess()
    monkeypatch.setattr(cli, "subprocess", recorder)
    return recorder


def _invoke(argv: list[str]) -> int:
    # Mirror main(): parse_known_args so passthrough flags land in extras.
    args, extras = cli.build_parser().parse_known_args(argv)
    args.extras = extras
    return args.func(args)


def _script(rel: str) -> str:
    return str(ROOT / rel)


# ------------------------------------------------------------------
# Isolation: the properties that keep this module off the operator's data
# ------------------------------------------------------------------

def test_the_pinned_data_root_is_not_the_operators_overlay(sandbox, tmp_path):
    """Fails without the autouse pin: get_data_root() resolves the live overlay."""
    resolved = get_data_root().resolve()
    assert resolved != LIVE_OVERLAY
    assert tmp_path.resolve() in resolved.parents or resolved == (tmp_path / "data-root").resolve()
    # The pin reaches a child too, which is the half the in-process conftest
    # guard cannot cover.
    assert Path(os.environ["HEADING_OS_DATA"]).resolve() == resolved


def test_the_recorder_does_not_rebind_the_shared_stdlib_module(sandbox):
    """Fails without the fix: the old stub set `subprocess.run` itself.

    Asserted on identity of the stdlib object rather than on the module reachable
    through `cli`, because the poisoning is interpreter-wide: an unrelated test
    importing `subprocess` for itself got the stub.
    """
    assert sys.modules["subprocess"].run is REAL_SUBPROCESS_RUN
    assert _stdlib_subprocess.run is REAL_SUBPROCESS_RUN
    assert cli.subprocess is not _stdlib_subprocess
    assert cli.subprocess is sandbox


def test_the_recorder_refuses_a_child_aimed_at_the_live_overlay(sandbox):
    """The guard's negative case, with an entry ON the line as well as inside it."""
    with pytest.raises(LiveSinkRefused):
        sandbox.run([PY, "x.py", "--canonical", str(LIVE_OVERLAY / "auto-memory")])
    with pytest.raises(LiveSinkRefused):
        sandbox.run([PY, "x.py", str(LIVE_OVERLAY)])
    assert sandbox.calls == []


def test_the_detector_knows_both_live_sinks(sandbox):
    """The native harness store is not auto-refused, but it IS detectable.

    Both addresses are covered so the day someone installs a real runner the
    detector does not have to be extended first.
    """
    for sink in LIVE_SINKS:
        assert live_sink_in([str(sink / "memory")], LIVE_SINKS) is not None
        assert live_sink_in([str(sink)], LIVE_SINKS) is not None
    assert len(LIVE_SINKS) == 2
    # Auto-refusal is deliberately narrower than detection.
    assert live_sink_in([str(LIVE_NATIVE_STORE / "memory")]) is None
    assert live_sink_in([str(LIVE_OVERLAY / "auto-memory")]) is not None


def test_the_recorder_allows_a_child_aimed_anywhere_else(sandbox, tmp_path):
    """The other direction: a guard that refuses everything measures nothing."""
    safe = [PY, "x.py", "--canonical", str(tmp_path / "data-root" / "auto-memory")]
    assert sandbox.run(safe).returncode == 0
    assert sandbox.calls == [safe]
    # A path that merely SPELLS the overlay's name is not the overlay.
    lookalike = [PY, "x.py", str(tmp_path / LIVE_OVERLAY.name / "auto-memory")]
    assert sandbox.run(lookalike).returncode == 0
    assert len(sandbox.calls) == 2


# ------------------------------------------------------------------
# Dispatch contract
# ------------------------------------------------------------------

def test_recall_dispatches_to_memory_index_query(sandbox):
    assert _invoke(["recall", "sovereign packet"]) == 0
    assert sandbox.calls == [[PY, _script("scripts/memory-index.py"), "query", "sovereign packet"]]


def test_recall_passes_through_extra_flags(sandbox):
    _invoke(["recall", "q", "--top-k", "3"])
    assert sandbox.calls[0] == [PY, _script("scripts/memory-index.py"), "query", "q", "--top-k", "3"]


def test_retire_dispatches_to_retire_memory(sandbox):
    _invoke(["retire", "feedback_foo.md", "bar.md"])
    assert sandbox.calls[0] == [PY, _script("scripts/retire-memory.py"), "feedback_foo.md", "bar.md"]


def test_promote_passes_through_flags(sandbox):
    _invoke(["promote", "--note", "knowledge/n.md", "--type", "signals"])
    assert sandbox.calls[0] == [PY, _script("scripts/promote-knowledge.py"), "--note", "knowledge/n.md", "--type", "signals"]


def test_hygiene_passes_through_flags(sandbox):
    _invoke(["hygiene", "--json"])
    assert sandbox.calls[0] == [PY, _script("scripts/memory-hygiene.py"), "--json"]


def test_status_aggregates_fast_read_only_signals(sandbox):
    assert _invoke(["status"]) == 0
    scripts_called = [c[1] for c in sandbox.calls]
    assert _script("scripts/memory-index.py") in scripts_called
    assert _script("scripts/knowledge-health.py") in scripts_called
    # hygiene is intentionally NOT run in status (it compiles the ODIN brain and is
    # slow); it stays a dedicated subcommand so status is responsive.
    assert _script("scripts/memory-hygiene.py") not in scripts_called


def test_reconcile_uses_cli_mode_not_bare_call(sandbox, tmp_path):
    assert _invoke(["reconcile"]) == 0
    argv = sandbox.calls[0]
    assert argv[1] == _script(cli.RECONCILE_HOOK)
    assert "--native" in argv and "--canonical" in argv
    # resolved dirs are non-empty (a bare hook call would carry neither flag).
    assert argv[argv.index("--native") + 1]
    canonical = Path(argv[argv.index("--canonical") + 1])
    assert canonical.name == "auto-memory"
    # and the canonical dir the child would sync into is the SANDBOX, not the
    # operator's. This is the assertion the module was missing: the old test
    # checked only the suffix, which the live path also satisfies.
    assert canonical.resolve() == (tmp_path / "data-root" / "auto-memory").resolve()
    assert live_sink_in(argv) is None
    # `--native` legitimately resolves the operator's REAL harness store: that is
    # what the subcommand is for. Nothing is written there because the recorder
    # never spawns a child.
    native = Path(argv[argv.index("--native") + 1])
    assert live_sink_in([str(native)], (LIVE_NATIVE_STORE,)) is not None


def test_missing_backing_script_degrades(sandbox):
    """_run returns 3 when the target script is absent.

    The name is asserted absent rather than made absent. This used to monkeypatch
    `cli.Path.exists` to a constant False, which is `pathlib.Path.exists` itself:
    every path in the interpreter reported missing for the duration of the test.
    """
    missing = "scripts/does-not-exist.py"
    assert not (ROOT / missing).exists(), "the fixture name must not be a real script"
    assert cli._run(missing) == 3
    assert sandbox.calls == [], "an absent script must not be spawned"


def test_present_backing_script_is_spawned(sandbox):
    """The other direction: _run's absence branch must not swallow a real script."""
    present = "scripts/memory.py"
    assert (ROOT / present).exists()
    assert cli._run(present, "--help") == 0
    assert sandbox.calls == [[PY, _script(present), "--help"]]


def test_help_lists_every_subcommand(capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--help"])
    out = capsys.readouterr().out
    for name in ("status", "recall", "promote", "retire", "reconcile", "hygiene"):
        assert name in out


def test_the_facade_resolves_the_workspace_root_not_a_data_root(sandbox):
    """ROOT is frozen at import (scripts/memory.py:32) and must stay the ENGINE tree.

    Freezing a data root that way would defeat the pin above, because a fixture
    cannot repoint a module constant evaluated before the fixture existed.
    """
    assert get_workspace_root() == ROOT
    assert ROOT.resolve() != LIVE_OVERLAY
