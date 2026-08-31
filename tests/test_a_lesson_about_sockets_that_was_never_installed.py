"""The suite could reach the internet, and the rule saying it must not was prose.

The workspace's own record reads "block `socket.connect`; one POSTed a token to
Google". Swept on 2026-08-31: no conftest at any level touched `socket`,
`pytest-socket` was not in the dependency set, and the corpus carried 172
network-call sites and 29 `socket.connect` sites. So the lesson had been
written down and the place it had to be applied did not have it — the same shape
as the overlay guard in the same file, found the same week.

The guard added to `tests/conftest.py` refuses EGRESS: a connect to an address
that is not loopback. Loopback, AF_UNIX and the `integration` / `acceptance` /
`network` markers all stand outside it, and that boundary is asserted in both
directions below — a guard that refuses everything measures nothing, and one
that refuses nothing is prose again.

No test here opens a connection to anything off this machine. The refusal
happens BEFORE the syscall, which is the whole point: proving the guard works
must not be the thing that sends the packet.
"""
from __future__ import annotations

import pathlib
import socket
import sys

import pytest


@pytest.fixture
def cf():
    """The LIVE root conftest pytest loaded, not a fresh copy of it."""
    live = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    assert live is not None, (
        "the root conftest is not in sys.modules under either name pytest uses; "
        "this file cannot see the live guard and must not pass quietly")
    return live


# ============================================================
# 1. Egress is refused, in this session, through the real primitives
# ============================================================

# Documentation addresses (RFC 5737 / RFC 3849). Never routed, and never
# reached: the guard raises before the syscall.
#
# Every socket below still gets a short timeout, and that is not belt and braces.
# Mutation-testing this file on 2026-08-31 disabled the guard and the run HUNG:
# an unrouted address does not refuse a connection, it swallows it, so the tests
# written to prove the guard were the tests that blocked for minutes when it was
# gone. A guard whose absence looks like a hang instead of a failure is a guard
# nobody will trust the next time the suite is slow.
_FAIL_FAST = 0.05
_OFF_MACHINE = [
    ("192.0.2.1", 80),
    ("198.51.100.7", 443),
    ("2001:db8::1", 443, 0, 0),
]


@pytest.mark.parametrize("address", _OFF_MACHINE)
def test_connecting_off_this_machine_is_refused(cf, address):
    family = socket.AF_INET6 if len(address) == 4 else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(_FAIL_FAST)
        with pytest.raises(cf.NetworkAccessRefused):
            sock.connect(address)
        with pytest.raises(cf.NetworkAccessRefused):
            sock.connect_ex(address)


def test_create_connection_is_refused(cf):
    """The route http.client, urllib, requests and urllib3 all take."""
    with pytest.raises(cf.NetworkAccessRefused):
        socket.create_connection(("192.0.2.1", 80), timeout=_FAIL_FAST)


def test_an_ssl_socket_inherits_the_refusal(cf):
    """The wrapped class must not be a hole in the same wall."""
    import ssl

    context = ssl.create_default_context()
    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw.settimeout(_FAIL_FAST)
    wrapped = context.wrap_socket(raw, server_hostname="example.invalid",
                                  do_handshake_on_connect=False)
    with wrapped, pytest.raises(cf.NetworkAccessRefused):
        wrapped.connect(("192.0.2.1", 443))


def test_the_refusal_says_what_to_do(cf):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(_FAIL_FAST)
        with pytest.raises(cf.NetworkAccessRefused) as excinfo:
            sock.connect(("192.0.2.1", 80))
    message = str(excinfo.value)
    assert "192.0.2.1" in message
    assert "pytest.mark.network" in message, "the message has to name the opt-in"


# ============================================================
# 2. The other direction: what must keep working
# ============================================================

def test_loopback_is_allowed_end_to_end(cf):
    """A real local connection, opened and accepted. Local IPC carries nothing
    off the machine, so refusing it would break real tests to buy nothing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(server.getsockname())      # must not raise
            conn, _addr = server.accept()
            with conn:
                client.sendall(b"ping")
                assert conn.recv(4) == b"ping"


def test_a_unix_socket_is_allowed(cf, tmp_path):
    path = str(tmp_path / "s.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(path)
        server.listen(1)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(path)                      # must not raise
            conn, _ = server.accept()
            conn.close()


@pytest.mark.parametrize("address,expected", [
    (("127.0.0.1", 8000), True),
    (("127.53.1.9", 1), True),
    (("::1", 443, 0, 0), True),
    (("0.0.0.0", 80), True),        # noqa: S104 - an address CLASSIFIED, not bound
    (("localhost", 80), True),
    ((None, 80), True),
    ("/run/some.sock", True),
    (b"/run/some.sock", True),
    (("192.0.2.1", 80), False),
    (("2001:db8::1", 443, 0, 0), False),
    (("example.com", 443), False),
    (("10.0.0.5", 22), False),
    ((object(), 80), False),
    (None, False),
    ((), False),
])
def test_the_local_test_answers_both_ways(cf, address, expected):
    """Both lists, derived case by case. A hostname that is not `localhost`
    reads as NOT local, because a guard that fails open on a shape it cannot
    parse is how a token leaves the machine while the report says clean."""
    assert cf.address_is_local(address) is expected


# ============================================================
# 2b. The WSL2 host is this machine, and only the gateway is
# ============================================================

def test_the_wsl_host_gateway_is_local_but_its_neighbours_are_not(cf):
    """Found by the guard, not predicted: installing it turned
    tests/test_recall_cross_lingual.py red on `('172.30.48.1', 11434)`, the
    Ollama embedder pinned to the Windows side of this same box.

    The carve-out is the gateway ADDRESS, derived from /proc/net/route. The
    assertion that matters is the second one: a different host on the same
    private subnet is a third party and stays refused, so this is not a
    "private addresses are fine" hole wearing a narrower name.
    """
    gateway = cf.wsl_host_gateway()
    if gateway is None:
        pytest.skip("not WSL2, so there is no host gateway to carve out")

    assert cf.address_is_local((gateway, 11434)) is True

    octets = gateway.split(".")
    neighbour = ".".join(octets[:3] + [str((int(octets[3]) + 7) % 254 + 1)])
    assert neighbour != gateway
    assert cf.address_is_local((neighbour, 11434)) is False, (
        "the carve-out widened from one address to a whole subnet")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(_FAIL_FAST)
        with pytest.raises(cf.NetworkAccessRefused):
            sock.connect((neighbour, 11434))


def test_off_wsl_there_is_no_gateway_carve_out(cf, monkeypatch):
    """A Linux server or a mac must not inherit a WSL-shaped hole.

    Only /proc/sys/kernel/osrelease is faked; /proc/net/route is left REAL and
    still carries a default route. Written the obvious way first — stubbing
    `Path.read_text` for every path — this passed against a mutant that deleted
    the WSL check entirely, because the same stub also emptied the route file,
    so the resolver returned None for the wrong reason. Mutation S10 caught it.
    The isolation is the test.
    """
    real_read_text = pathlib.Path.read_text

    def fake_read_text(self, *a, **k):
        if self.as_posix() == "/proc/sys/kernel/osrelease":
            return "6.1.0-generic-linux\n"
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(cf, "_WSL_GATEWAY", ...)
    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)

    # The precondition: the real route file DOES name a gateway, so a resolver
    # that skipped the WSL check would find one and this test would see it.
    route = real_read_text(pathlib.Path("/proc/net/route"), encoding="utf-8")
    assert any(r.split()[1:2] == ["00000000"] for r in route.splitlines()[1:]), (
        "no default route on this host; the mutant and the fix are "
        "indistinguishable here and this test must not claim otherwise")

    assert cf.wsl_host_gateway() is None
    assert cf.address_is_local(("172.30.48.1", 11434)) is False


def test_the_gateway_is_derived_from_the_kernel_not_hardcoded(cf):
    """A literal would rot at the next WSL boot."""
    gateway = cf.wsl_host_gateway()
    if gateway is None:
        pytest.skip("not WSL2")
    import struct as _struct
    expected = None
    for row in pathlib.Path("/proc/net/route").read_text().splitlines()[1:]:
        f = row.split()
        if len(f) > 2 and f[1] == "00000000":
            expected = socket.inet_ntoa(_struct.pack("<L", int(f[2], 16)))
            break
    assert gateway == expected


def test_the_marker_opens_the_gate_and_only_for_the_marked_test(cf):
    """Drive the gate the way the fixture does, without needing real egress."""
    assert cf._NETWORK_ALLOWED is False, (
        "an unmarked test must run with the gate shut")
    assert "network" in cf._NETWORK_MARKERS
    assert "integration" in cf._NETWORK_MARKERS
    assert "acceptance" in cf._NETWORK_MARKERS


@pytest.mark.network
def test_a_marked_test_is_let_through(cf):
    """The opt-in, exercised. The gate is open; nothing is actually sent,
    because a test that reaches the real internet in the unit job is precisely
    what the guard exists to prevent from happening by accident."""
    assert cf._NETWORK_ALLOWED is True
    cf._refuse_egress(("192.0.2.1", 80), "connect to")   # must not raise


# ============================================================
# 3. The guard is installed, not merely defined
# ============================================================

def test_the_session_actually_installed_the_socket_guard(cf):
    """A wrapper nobody applied refuses nothing, which is the shape this whole
    file is about."""
    assert callable(cf._RESTORE_SOCKET_GUARD), (
        "the session never installed the egress guard")
    assert socket.socket.connect.__qualname__.startswith("_install_socket_guard"), (
        f"socket.socket.connect is {socket.socket.connect!r}, not the guarded one")
    assert socket.socket.connect_ex.__qualname__.startswith("_install_socket_guard")


def test_the_guard_puts_the_primitives_back(cf):
    before = (socket.socket.connect, socket.socket.connect_ex)
    restore = cf._install_socket_guard()
    try:
        assert socket.socket.connect is not before[0], "the guard did not arm"
    finally:
        restore()
    assert (socket.socket.connect, socket.socket.connect_ex) == before


def test_the_marker_is_registered_so_strict_markers_accepts_it(pytestconfig):
    """`--strict-markers` is on and pyproject does not declare `network`;
    conftest's `pytest_configure` does, beside the guard that honours it."""
    declared = pytestconfig.getini("markers")
    assert any(m.startswith("network:") for m in declared), declared
