"""The guard that keeps ollama answering.

Operator directive, 2026-08-23: *"сделай так, что бы Windows не спал и ollama
ВСЕГДА была доступна"*. Three things had to be true for that, and only one of
them was:

1. The machine must stay awake while it is running. It cannot suspend - this
   laptop's firmware offers no S0/S1/S2/S3 standby at all - and on AC it never
   hibernates. Hibernation after 30 minutes on BATTERY is deliberate and was
   left alone: the operator's rule is that a dormant Windows means a dormant
   WSL, which is correct behaviour and not an outage.
2. Ollama must start with the session. It already did, from
   `Startup\\Ollama.lnk`; nothing to fix.
3. Something must notice when it dies and start it again. Nothing did. That is
   this guard.

It runs inside WSL and starts the Windows-side application through interop,
because that is where every model now lives. Its decision logic is pure and
tested here; the launch itself is one injected callable, so no test starts a
process or touches the network.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_SRC = ROOT / "scripts" / "ollama-guard.py"
_spec = importlib.util.spec_from_file_location("ollama_guard", _SRC)
guard = importlib.util.module_from_spec(_spec)
# Registered BEFORE exec: `@dataclass` resolves annotations through
# `sys.modules[cls.__module__]`, so a module loaded by spec alone raises
# AttributeError on its own decorator.
sys.modules["ollama_guard"] = guard
_spec.loader.exec_module(guard)


class _Probe:
    """A probe that answers from a script, and remembers what it was asked."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[str] = []

    def __call__(self, host, **kw):
        self.calls.append(host)
        return self.answers.pop(0) if self.answers else False


# --- the decision ------------------------------------------------------------

def test_an_answering_host_is_left_alone():
    probe = _Probe(True)
    launched = []
    result = guard.ensure_up(
        ["http://gw:11434"], probe=probe, launch=lambda: launched.append(1), settle=0
    )
    assert result.up is True
    assert result.launched is False
    assert launched == [], "a healthy daemon must never be restarted"


def test_the_second_host_answering_is_still_up():
    """The pin names two ports on one machine; either one is a working ollama."""
    probe = _Probe(False, True)
    result = guard.ensure_up(
        ["http://gw:11434", "http://gw:11436"], probe=probe, launch=lambda: None, settle=0
    )
    assert result.up is True and result.launched is False


def test_a_dead_host_is_launched_and_re_probed():
    probe = _Probe(False, False, True)          # down, down, then up after launch
    launched = []
    result = guard.ensure_up(
        ["http://gw:11434", "http://gw:11436"],
        probe=probe, launch=lambda: launched.append(1), settle=0, attempts=1,
    )
    assert launched == [1]
    assert result.up is True and result.launched is True


def test_a_launch_that_does_not_help_reports_failure():
    probe = _Probe(False, False, False, False)
    result = guard.ensure_up(
        ["http://gw:11434"], probe=probe, launch=lambda: None, settle=0, attempts=2
    )
    assert result.up is False and result.launched is True
    assert "http://gw:11434" in result.detail, "say which address stayed dead"


def test_a_launcher_that_cannot_run_is_reported_not_swallowed():
    def _explode():
        raise OSError("cmd.exe not found")

    result = guard.ensure_up(
        ["http://gw:11434"], probe=_Probe(False), launch=_explode, settle=0
    )
    assert result.up is False
    assert "cmd.exe not found" in result.detail


def test_no_hosts_is_a_refusal_not_a_silent_pass():
    """An empty host list means the config was misread. Reporting 'up' there
    would be a monitor that passes by knowing nothing."""
    with pytest.raises(ValueError):
        guard.ensure_up([], probe=_Probe(True), launch=lambda: None, settle=0)


# --- the launcher ------------------------------------------------------------

def test_the_launch_command_expands_the_windows_user_path():
    """Never a hardcoded C:\\Users\\<name>: this engine is public, and the path
    differs per machine. cmd.exe expands %LOCALAPPDATA% in the session that owns
    the desktop, which is the session the app has to appear in."""
    argv = guard.launch_command()
    assert argv[0].endswith("cmd.exe")
    joined = " ".join(argv)
    assert "%LOCALAPPDATA%" in joined
    assert "ollama app.exe" in joined
    assert not any("Users" in part for part in argv)


def test_the_launcher_is_a_list_never_a_shell_string():
    """`shell=True` is forbidden workspace-wide; the shape is the guarantee."""
    argv = guard.launch_command()
    assert isinstance(argv, list) and all(isinstance(p, str) for p in argv)
