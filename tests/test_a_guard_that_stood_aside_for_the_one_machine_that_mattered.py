#!/usr/bin/env python3
"""The egress guard called another computer "local".

`tests/conftest.py::address_is_local` returns True for loopback, AF_UNIX, the
unspecified address, and the WSL2 host gateway. The first three are this
process. The fourth is a DIFFERENT MACHINE running a DIFFERENT daemon, and it
was on the list precisely because the embedder lives there.

So the one guard the suite has against "my verdict depends on the host" stood
aside for the exact traffic that makes a verdict depend on the host.

MEASURED 2026-09-03, by socket census over 109 candidate test files with the
daemon first down and then up:

    test_ops_signals::test_ollama_accel_state_fs                passed / FAILED
    test_five_fixes...::..._loads_with_accented_values          reached the host
    test_recall_cross_lingual (4 cases)                         skipped / passed

Four unit tests dialled `172.30.48.1` with nothing asking them to, and two
changed verdict when the daemon's state changed and no code did. The same day
the operator's main clone went red on a memory-health test that this worktree
ran green, on the same commit, because `config/ollama-hosts.yaml` is gitignored
and only one of the two machines carried the pin.

THE REPAIR. The gateway leg is now opt-in. `requires_ollama` was already
declared in `pyproject.toml`, deselected nothing and gated nothing; it is now
what opens that leg. Everything else is refused AT THE SOCKET with a message
naming the three ways out.

NOT CLAIMED. This closes the leg for connections opened inside the pytest
process. A test that reaches ollama through a CHILD process is not covered:
the guard is installed in this interpreter only. The census found none by
reading, and could not prove their absence.

Run: python3 -m pytest tests/test_a_guard_that_stood_aside_for_the_one_machine_that_mattered.py
"""
from __future__ import annotations

import ipaddress
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import conftest as C  # noqa: E402

GATEWAY = C.wsl_host_gateway()

not_on_wsl = pytest.mark.skipif(
    GATEWAY is None,
    reason="no WSL2 host gateway here, so this leg of the guard has no address "
           "to refuse; the classification tests below still run")


# ============================================================
# The refusal
# ============================================================

@not_on_wsl
def test_an_unmarked_test_cannot_reach_the_ollama_host():
    """The failing half. Against the version before this change this connect
    was permitted, and on a machine with the daemon up it SUCCEEDED."""
    with pytest.raises(C.NetworkAccessRefused) as refused:
        socket.create_connection((GATEWAY, 11434), timeout=1)

    message = str(refused.value)
    assert "foreign daemon" in message, message
    assert "requires_ollama" in message, (
        f"the refusal does not name the way out, so the next author's only "
        f"option is to delete the guard: {message}")


@not_on_wsl
@pytest.mark.requires_ollama
def test_a_test_that_asked_for_the_daemon_is_let_through():
    """The other direction, and the one that keeps the marker worth having.

    A guard that refuses everything satisfies every refusal test and breaks
    every honest caller. This does not require the daemon to ANSWER -- only
    that the guard steps aside; a refused connection is a fine outcome here and
    a `NetworkAccessRefused` is not.
    """
    try:
        socket.create_connection((GATEWAY, 11434), timeout=1).close()
    except C.NetworkAccessRefused:  # pragma: no cover - the failure being tested
        pytest.fail("`requires_ollama` did not open the gateway leg")
    except OSError:
        pass        # the daemon is down: the guard still stood aside, which is
                    # the whole assertion.


@not_on_wsl
def test_the_marker_closes_again_after_the_test_that_used_it():
    """A per-test switch that leaks is a guard that is off for the rest of the
    worker. Ordered after the marked test above, in the same file, on purpose."""
    with pytest.raises(C.NetworkAccessRefused):
        socket.create_connection((GATEWAY, 11434), timeout=1)


# ============================================================
# The classification underneath, driven without a socket
# ============================================================

@not_on_wsl
def test_the_gateway_is_recognised_by_address_not_by_string_shape():
    """`_is_wsl_host_gateway` must answer about THIS machine's gateway.

    A hard-coded `172.` prefix, or a substring match, would pass the tests
    above on this laptop and refuse an unrelated address on another.
    """
    assert C._is_wsl_host_gateway((GATEWAY, 11434)) is True
    assert C._is_wsl_host_gateway((GATEWAY.encode(), 11434)) is True
    assert C._is_wsl_host_gateway((f"{GATEWAY}%eth0", 11434)) is True
    # `ipaddress` rather than the literal, which ruff reads as a bind-to-all
    # (S104). Nothing is bound here; the string is only ever compared.
    unspecified = ipaddress.ip_address(0).compressed
    for other in ("127.0.0.1", unspecified, "8.8.8.8", "localhost"):
        assert C._is_wsl_host_gateway((other, 443)) is False, other


def test_a_shape_that_is_not_an_address_is_not_the_gateway():
    """AF_UNIX paths and malformed tuples arrive here too. None of them is a
    remote host, and none may crash the guard on the way past."""
    for shape in (None, (), "/run/some.sock", b"/run/some.sock", 42,
                  (None, 11434), ((), 11434)):
        assert C._is_wsl_host_gateway(shape) is False, shape


def test_loopback_is_still_permitted_without_any_marker():
    """The anchor. Most of this suite talks to itself over loopback, and a
    guard that took that away would be reverted within a day."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        socket.create_connection(listener.getsockname(), timeout=5).close()
    finally:
        listener.close()


# ============================================================
# The marker is real, and the switch is wired to it
# ============================================================

# ============================================================
# The second way the host reached in: an inherited pin
# ============================================================

def test_an_ambient_embed_pin_does_not_reach_an_unmarked_test():
    """The failing half of the containment, and it needs no daemon at all.

    MEASURED 2026-09-03: with `HEADING_OS_OLLAMA_EMBED_HOST=auto:11434,11436`
    exported, `test_ops_signals.py::test_ollama_accel_state_fs` failed on its
    FIRST assertion, and passed with the name unset. The test writes its own
    config into a `tmp_path` to control exactly this, and the environment
    reached around it.
    """
    import os

    assert os.environ.get("HEADING_OS_OLLAMA_EMBED_HOST", "") == "", (
        "this test inherited an embedding pin from the shell that launched "
        "pytest, so anything resolving an embed host below is answering about "
        "the operator's machine rather than about the code")


@pytest.mark.requires_ollama
def test_a_marked_test_keeps_the_operators_pin():
    """The other direction. Blanking it for everyone would silently repoint the
    four `requires_ollama` tests at the local daemon and skip them for good."""
    import os

    value = os.environ.get("HEADING_OS_OLLAMA_EMBED_HOST")
    assert value != "", (
        "the containment blanked the pin for a test that asked for the daemon")


def test_the_switch_is_restored_rather_than_only_reassigned():
    """Closes a mutation that survived every test above.

    `_no_egress` re-derives both flags at the START of every test, so dropping
    the restore in its `finally` is invisible BETWEEN two ordinary tests -- the
    next setup overwrites the leak. It is not invisible to anything that runs
    after the last per-test teardown: a module- or session-scoped fixture
    tearing down, which is ordinary cleanup code and is exactly where an
    unnoticed open gate would let a connection out unrefused.

    Driving the fixture function directly is what makes that window reachable
    from inside a test at all. MEASURED 2026-09-03: with the restore reduced to
    `_NETWORK_ALLOWED = previous[0]`, the six tests above all passed and this
    one fails.
    """
    class _Node:
        def get_closest_marker(self, name):
            return object() if name == "requires_ollama" else None

    class _Request:
        node = _Node()

    # `__wrapped__`, because pytest refuses a direct call on the decorated name.
    # The attribute is the undecorated function, so this drives the real body
    # rather than a copy of it.
    body = C._no_egress.__wrapped__
    before_network, before_ollama = C._NETWORK_ALLOWED, C._OLLAMA_ALLOWED
    generator = body(_Request())
    try:
        next(generator)
        assert C._OLLAMA_ALLOWED is True, (
            "the marker did not open the gateway leg during the test")
        with pytest.raises(StopIteration):
            next(generator)
        assert C._OLLAMA_ALLOWED is before_ollama, (
            "the gateway leg stayed open after the test that opened it, so "
            "every later teardown runs with the guard down")
        assert C._NETWORK_ALLOWED is before_network
    finally:
        C._NETWORK_ALLOWED, C._OLLAMA_ALLOWED = before_network, before_ollama


def test_requires_ollama_is_a_registered_marker():
    """An unregistered marker is a typo pytest warns about and then ignores,
    which would leave the opt-in silently closed for the tests that need it."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "requires_ollama:" in text, (
        "`requires_ollama` is no longer declared in pyproject.toml, so the "
        "opt-in this guard reads is an unregistered marker")


def test_the_ollama_leg_is_opened_by_more_than_one_marker():
    """A floor, and a statement of what is trusted.

    `network`, `integration` and `acceptance` already mean "this test leaves
    the machine", so they carry the gateway too. Four names on 2026-09-03; an
    empty tuple here would close the leg for everyone and pass every refusal
    test in this file.
    """
    assert len(C._OLLAMA_MARKERS) == 4, C._OLLAMA_MARKERS
    assert "requires_ollama" in C._OLLAMA_MARKERS
    for inherited in C._NETWORK_MARKERS:
        assert inherited in C._OLLAMA_MARKERS, inherited
