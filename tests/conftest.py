"""Root test configuration.

Pin the per-instance timezone for the whole test session so tests that assert
local-time behaviour (calendar, scheduling, daemon heartbeats) validate the
real Etc/GMT-4 logic rather than the engine's UTC default. The production
value lives in the gitignored .env; here we set it deterministically.
See scripts.utils.workspace.get_default_tz().

This file also holds the re-exec guard for the whole suite; see below.
"""
import os
import time
from pathlib import Path

import pytest

from scripts.utils import venv_guard as _venv

# Assignment, not setdefault, for the reason spelled out at WORKSPACE_LOG_DIR
# fifty lines down: a pin a stray shell variable can switch off is not a pin.
# It was a `setdefault` until 2026-08-30, so `HEADING_OS_TZ=America/New_York
# pytest tests/` quietly re-pointed every calendar, scheduling and heartbeat
# assertion at New York offsets while this docstring still promised Etc/GMT-4,
# and nothing in the run said the substitution had happened. A test that needs
# a different zone sets it per case with monkeypatch, which applies inside the
# test body and still wins.
# tests/test_a_timezone_pin_a_stray_variable_could_switch_off.py fails without
# this line being an assignment.
os.environ["HEADING_OS_TZ"] = "Etc/GMT-4"

# The suite's re-exec guard, set ONCE, here, because this file is collected
# before any test module.
#
# About twenty scripts call `ensure_venv()` at module scope and about twenty test
# modules load a script by path. When the running interpreter is not
# .venv/bin/python, that call `os.execv`s the WHOLE pytest process, which inherits
# pytest's capture as file descriptor 1: the relaunched run writes every byte
# into a temp file nobody reads, so `pytest tests/` prints ZERO bytes while
# exiting 0 on a passing set and 1 on a failing one. Measured on this repository,
# 2026-07-26. A run that prints nothing is indistinguishable from one that never
# happened.
#
# It lived as three per-module copies until wire 2.2, and that shape could not
# hold. The variable is process-global, so each copy satisfied the other two
# modules' guard tests: deleting the line from one module left its own test
# passing. Worse, the defect was self-erasing, because a NEW unguarded module
# re-execs at collection, `ensure_venv` sets this same sentinel before
# `os.execv`, and in the relaunched silent run every guard test passes.
#
# The constant is referenced rather than spelled out: a duplicated literal drifts
# silently the day venv.py renames it.
os.environ.setdefault(_venv._SENTINEL, "1")

_TESTS_ROOT = Path(__file__).resolve().parent
_ENGINE_ROOT = _TESTS_ROOT.parent

# Runtime logs written during a test run go to their own directory, never to the
# operator's. Measured the day the denial counter landed (2026-08-01): a single
# suite run appended 13 refusals to the production `.logs/denials/denials.jsonl`
# from tests that legitimately exercise leak-guard and the push walls with
# fixtures. Left alone, the instrument built to decide which guards earn their
# cost would have counted its own test suite as the workspace's main offender,
# which is the same defect class as an instrument with a silently wrong
# denominator — the thing this counter exists to end, reproduced inside it.
#
# Assignment, not setdefault: isolation that a stray shell variable can switch
# off is not isolation. Individual tests still redirect per-case with monkeypatch
# or an explicit subprocess env, and both continue to win over this.
_TEST_LOG_DIR = str(_ENGINE_ROOT / ".logs" / "_pytest")
os.environ["WORKSPACE_LOG_DIR"] = _TEST_LOG_DIR

# The same isolation, one guard along. `check_rate_limit` in the PreToolUse
# dispatcher counts Write and Edit calls per day and BLOCKS past 1000, and six
# test modules drive that hook in a subprocess exactly as production does.
# Measured 2026-08-07, before this line: the operator's counter stood at 1033
# and was blocking three tests, and the writes it had stored were fixtures —
# `threads/personal/foo.md`, a Windows path that cannot exist on this machine,
# a scratch probe file. One run of three of those modules added 12 more.
#
# UNLINKED at import, not merely redirected. The counter is keyed by date and
# resets itself tomorrow, but a run this hour would otherwise inherit every
# earlier run's fixtures today, and enough runs in one day would reproduce the
# same block in the new location. Starting from zero is what makes the
# redirection a fix rather than a move.
#
# Only the session that OWNS the variable resets it, and that distinction was
# measured rather than reasoned. This suite spawns pytest CHILDREN — the Canopus
# probe alone runs several per contract — and each child imports this file. With
# an unconditional unlink they wiped their parent's counter mid-run, so after a
# full run the file was simply gone and the reset the comment promised was not
# the reset the code performed. An inherited variable means a parent already did
# this; the child leaves it alone.
_TEST_RATE_STATE = _ENGINE_ROOT / ".logs" / "_pytest" / "dispatch-rate.json"
_OWNS_RATE_STATE = "WS_RATE_LIMIT_STATE" not in os.environ
os.environ["WS_RATE_LIMIT_STATE"] = str(_TEST_RATE_STATE)
if _OWNS_RATE_STATE:
    try:
        _TEST_RATE_STATE.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:  # pragma: no cover - reported, never fatal to the run
        print(f"[conftest] could not reset the test rate-limit state: {exc}")


# The same isolation, applied to the one instrument the suite could falsify from
# the outside. A Healthchecks deadman answers "is that daemon alive?", and this
# repository was answering for it. `_main_loop` in scripts/inbox_pulse/daemon.py
# pings STEWARD_HC_EMAIL_TRIAGE at the end of a clean cycle, eleven tests in
# tests/inbox_pulse/test_daemon.py drive that loop to completion, and any earlier
# test that calls the real load_env() puts the production ping URL in os.environ
# for the rest of the session. Measured 2026-08-18: one run of that one file sent
# 14 real success pings to the live check. The daemon behind it had been wedged
# since 2026-08-17 06:53 UTC and had not completed a single cycle in 33 hours,
# yet the operator saw the alert clear and re-fire nine times, because every push
# ran the suite and the suite pinged. The outage was found by reading a traceback,
# not by the monitor built to find it.
#
# Blanked, not deleted, and that distinction is the whole guard: load_env() uses
# os.environ.setdefault, so a name it finds already present is left alone, while a
# deleted one is restored the moment any test loads the environment. An empty
# value is also the documented no-op for ping(), which returns False on a falsy
# URL without opening a socket.
#
# Matched on the VALUE rather than the name, because the invariant is "this
# string is a live deadman", not "this key happens to be spelled _HC_". A ping URL
# added tomorrow under any name is contained on the day it is added.
# tests/test_deadman_ping_containment.py fails without these four lines.
_ENV_FILE = _ENGINE_ROOT / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _key, _, _value = _line.partition("=")
        if _ and "hc-ping.com" in _value:
            os.environ[_key.strip()] = ""

# The same containment, applied to the second instrument the suite could drive
# from the outside: the notifications bot. A deadman ping is believed; a Telegram
# message is READ, and the operator cannot tell one sent by a daemon from one
# sent by a test run.
#
# Measured 2026-08-19. `_notify_stall` in .claude/hooks/checkpoint-offer.py sends
# "HEADING OS: unattended run stopped. <reason>" when the no-progress fuse trips,
# and `test_the_pause_record_is_written_once` calls `_pause_unattended` directly.
# That test deleted HEADING_OS_TELEGRAM_CHAT_ID, a name `_notify_stall` never
# reads: the function walks CHECKPOINT_ / OPS_RADAR_ / ODIN_CADENCE_
# _TELEGRAM_TARGET, and any earlier test that called the real `load_env()` had
# already put ODIN_CADENCE_TELEGRAM_TARGET in os.environ for the rest of the
# session. Every run of the suite therefore sent the operator one real alert
# about an unattended run that had never started. He received nine of them in one
# afternoon and read them as a failing feature.
#
# Matched on the NAME here, not the value, because a chat id is a bare number
# with nothing distinctive to match on. Two rings: the targets, which the callers
# check first, and the bot token, without which `telegram_notify.notify()`
# returns False before it opens a socket whatever target survives.
#
# NOT contained here: TELEGRAM_API_ID / _API_HASH / _PHONE, the credentials of
# the operator's own Telegram ACCOUNT rather than the bot. No test in this suite
# drives that client, so blanking them would be a guard against nothing; a test
# that starts driving it needs its own containment, and it is named here so that
# day is not a discovery.
# tests/test_telegram_send_containment.py fails without these lines.
_MUTED_TELEGRAM = {"TELEGRAM_NOTIFY_BOT_TOKEN"}
_MUTED_TELEGRAM.update(k for k in os.environ if k.endswith("_TELEGRAM_TARGET"))
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _key, _, _value = _line.partition("=")
        _key = _key.strip()
        if _ and _key.endswith("_TELEGRAM_TARGET"):
            _MUTED_TELEGRAM.add(_key)
_MUTED_TELEGRAM = tuple(sorted(_MUTED_TELEGRAM))
for _key in _MUTED_TELEGRAM:
    os.environ[_key] = ""


@pytest.fixture(autouse=True)
def _isolate_runtime_logs():
    """Re-arm the redirection before every test.

    The module-scope assignment above covers import time and nothing else. A
    test that sets the variable by hand and pops it in a `finally` leaves it
    UNSET for every test that follows, and the rest of the session then writes
    to the operator's real log. That is not hypothetical: the denial counter's
    own contract does exactly that, and it is how this fixture was found.
    Restoring per test costs nothing and does not fight monkeypatch, which
    applies inside the test body, after this.

    The Telegram names are re-armed here for a sharper version of the same
    reason. Several notifier tests `monkeypatch.delenv` a target to exercise the
    unconfigured path, and a DELETED name is not a blanked one: the next
    `load_env()` anywhere in the session restores the operator's real chat id,
    because load_env uses setdefault. Blanking again before each test is what
    keeps the containment from lasting only until the first test that clears it.
    """
    os.environ["WORKSPACE_LOG_DIR"] = _TEST_LOG_DIR
    os.environ["WS_RATE_LIMIT_STATE"] = str(_TEST_RATE_STATE)
    for _name in _MUTED_TELEGRAM:
        os.environ[_name] = ""
    yield


# ============================================================
# The operator's overlay is not scratch space
# ============================================================
#
# The guard itself now lives in `scripts/utils/overlay_write_guard.py`. It was
# 409 lines of this file until 2026-08-31, and a conftest arms under pytest and
# nowhere else: a scratch probe run as a plain `.venv/bin/python` overwrote a
# real operator workbook while every one of its tests was green. Moving it out
# is what lets a later change arm it in any process.
#
# NOTHING is re-exported here on purpose. Two test files reach in and replace
# `_watched_roots` and `_OVERLAY_PREFIXES` to drive the guard over a pretend
# overlay; a copy of those names sitting in this module would let such a
# replacement bind something nobody reads, and the test would pass over
# nothing. With the names absent, `monkeypatch.setattr` raises instead.
from scripts.utils import overlay_write_guard as _guard  # noqa: E402


# ============================================================
# Egress: a unit test does not reach the internet
# ============================================================
#
# The lesson was written and the guard was never installed. The workspace's own
# record says "block `socket.connect`; one POSTed a token to Google", and on
# 2026-08-31 a sweep found no `socket` handling in any conftest at any level and
# no `pytest-socket` in the dependency set. So the rule existed in prose and the
# place it had to be applied did not have it — the same shape as the overlay
# guard above.
#
# Stdlib only, and deliberately narrow. What is refused is EGRESS: a connect to
# an address that is not loopback. What is not refused:
#
#   * loopback and AF_UNIX, always. That is local IPC — the bridge daemon, a
#     scratch HTTP server, a socketpair — and it carries nothing off the machine,
#     which is the thing this guard is about. Refusing it would break real tests
#     to buy nothing.
#   * anything under the `integration`, `acceptance` or `network` markers. The
#     first two are applied by path in `pytest_collection_modifyitems` and are
#     excluded from the per-push unit job; the third is the explicit opt-in for a
#     unit test that genuinely has to leave the machine.
#
# `connect` is the choke point rather than `getaddrinfo`: a name lookup carries
# no payload, and blocking it would turn "no network" into a DNS error a long way
# from the call that wanted the network.

_NETWORK_MARKERS = ("network", "integration", "acceptance")
_NETWORK_ALLOWED = False        # flipped per test by _no_egress below
_RESTORE_SOCKET_GUARD = None


class NetworkAccessRefused(RuntimeError):
    """A test tried to reach a non-loopback address."""


_WSL_GATEWAY = ...          # computed once, lazily; `...` means "not yet asked"


def wsl_host_gateway():
    """The Windows side of THIS machine on WSL2, or None anywhere else.

    Under WSL2 the host is reachable only through the default-route gateway, and
    this workspace's local models live there: no ollama runs inside WSL, every
    model is pinned to the Windows host, so the embedder is a connect to
    `<gateway>:11434`. That address is not loopback, but it is not egress
    either — it is the other half of the same physical machine, and nothing
    sent to it leaves the box.

    Found by the guard rather than reasoned in advance: installing the egress
    block turned tests/test_recall_cross_lingual.py red with
    `connect to ('172.30.48.1', 11434)`.

    Derived, never hardcoded. The gateway changes on every WSL boot, so a
    literal would rot within a day. Narrowed to the single gateway ADDRESS and
    not to its subnet, and not to RFC1918 in general: the machine sits on a real
    LAN, other hosts on it are third parties, and "private address" is not a
    synonym for "this computer".
    """
    global _WSL_GATEWAY
    if _WSL_GATEWAY is not ...:
        return _WSL_GATEWAY
    _WSL_GATEWAY = None
    try:
        release = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8")
    except OSError:
        return _WSL_GATEWAY
    if "microsoft" not in release.lower():
        return _WSL_GATEWAY
    import socket
    import struct
    try:
        rows = Path("/proc/net/route").read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return _WSL_GATEWAY
    for row in rows:
        fields = row.split()
        if len(fields) > 2 and fields[1] == "00000000":
            try:
                _WSL_GATEWAY = socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
            except (ValueError, struct.error, OSError):
                _WSL_GATEWAY = None
            break
    return _WSL_GATEWAY


def address_is_local(address) -> bool:
    """True for loopback, AF_UNIX, the unspecified address, and the WSL2 host.

    Public so a test can drive it without opening a socket. Unparseable is
    treated as NOT local: a guard that fails open on a shape it does not
    recognise is the kind that reports clean while a token leaves the machine.
    """
    import ipaddress

    if isinstance(address, (str, bytes, os.PathLike)):
        return True                              # AF_UNIX path
    if not isinstance(address, tuple) or not address:
        return False
    host = address[0]
    if host in (None, "", "localhost"):
        return True
    try:
        text = os.fsdecode(host) if isinstance(host, bytes) else str(host)
    except (TypeError, ValueError):
        return False
    bare = text.split("%", 1)[0]
    if bare == wsl_host_gateway():
        return True
    try:
        ip = ipaddress.ip_address(bare)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def _refuse_egress(address, verb):
    if _NETWORK_ALLOWED:
        return
    if address_is_local(address):
        return
    raise NetworkAccessRefused(
        f"a test tried to {verb} {address!r}, which is not loopback. A unit test "
        f"must not reach the network: mock the client, or mark the test "
        f"`@pytest.mark.network` if it genuinely has to leave this machine."
    )


def _install_socket_guard():
    """Wrap the two connect primitives. Returns a callable that puts them back."""
    import socket

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_connect(self, address, *args, **kwargs):
        _refuse_egress(address, "connect to")
        return real_connect(self, address, *args, **kwargs)

    def guarded_connect_ex(self, address, *args, **kwargs):
        _refuse_egress(address, "connect_ex to")
        return real_connect_ex(self, address, *args, **kwargs)

    # Set on `socket.socket`, the Python subclass, which shadows the inherited
    # `_socket.socket` methods for it and for every subclass — including
    # `ssl.SSLSocket`. `socket.create_connection`, and therefore http.client,
    # urllib, requests and urllib3, all call `sock.connect(sa)` and land here.
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex

    def restore():
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex

    return restore


def pytest_configure(config):
    """Register the opt-in marker here, not in pyproject.

    `--strict-markers` is on, so `network` has to be declared somewhere. It is
    declared beside the guard that honours it, so the two cannot drift apart.
    """
    config.addinivalue_line(
        "markers",
        "network: this test genuinely reaches a non-loopback address "
        "(the egress guard in tests/conftest.py stands aside for it)",
    )


@pytest.fixture(autouse=True)
def _no_egress(request):
    """Open the gate only for a test that has asked for it, then close it again."""
    global _NETWORK_ALLOWED
    previous = _NETWORK_ALLOWED
    _NETWORK_ALLOWED = any(
        request.node.get_closest_marker(name) is not None for name in _NETWORK_MARKERS
    )
    try:
        yield
    finally:
        _NETWORK_ALLOWED = previous


def pytest_sessionstart(session):
    global _RESTORE_SOCKET_GUARD
    _RESTORE_SOCKET_GUARD = _install_socket_guard()
    _guard.arm()


def pytest_sessionfinish(session, exitstatus):
    if not _guard._WATCH_BEFORE:
        return
    complaints = _guard.watch_complaints(_guard._WATCH_BEFORE, _guard._watch_snapshot())
    if not complaints:
        return
    # A child process writes outside this interpreter, so no wrapper in the guard
    # can see it and the snapshot alone cannot say who. `_CHILD_SPAWNS` is the
    # list of children that COULD have reached the live overlay, each with the
    # test that spawned it. It is a suspect list, never an accusation: on
    # 2026-08-31 the previous wording of this report sent an agent hunting a test
    # that had written nothing, when the writer was a concurrent agent.
    spawns = _guard._CHILD_SPAWNS
    if spawns:
        shown = spawns[:10]
        complaints.append(
            f"{len(spawns)} child process(es) ran with the live data root "
            f"reachable, any of which could be the writer: "
            + "; ".join(f"{nodeid} -> {cmd}" for nodeid, cmd in shown)
            + ("" if len(spawns) <= 10 else f" (+{len(spawns) - 10} more)")
        )
    # Reported through the reporter rather than an exception: a raise here is
    # attributed to no test and reads as a harness crash.
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    message = (
        "; ".join(complaints)
        + ". Pass HEADING_OS_DATA pointing at a tmp_path to anything that writes."
    )
    if reporter is not None:
        reporter.write_line("")
        reporter.write_line(f"ERROR: {message}", red=True)
    session.exitstatus = 1


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests by top-level directory so the per-push CI filter holds.

    The per-push unit job runs `-m "not integration and not acceptance"`. That
    filter is only honest if every test under tests/integration/ actually carries
    the `integration` marker (and likewise tests/acceptance/ -> `acceptance`).
    Marking by path removes the requirement that each file remember to declare it,
    and closes the gap where an unmarked integration suite (e.g. the LFS-fixture
    convert-to-md tests) silently ran in the unit job and failed on a fresh clone.
    Both markers are registered in [tool.pytest.ini_options]; --strict-markers is on.
    """
    for item in items:
        try:
            rel = item.path.resolve().relative_to(_TESTS_ROOT)
        except (ValueError, AttributeError):
            continue
        top = rel.parts[0] if rel.parts else ""
        if top in ("integration", "acceptance"):
            item.add_marker(top)
