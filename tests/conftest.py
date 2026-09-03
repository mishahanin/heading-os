"""Root test configuration.

Pin the per-instance timezone for the whole test session so tests that assert
local-time behaviour (calendar, scheduling, daemon heartbeats) validate the
real Etc/GMT-4 logic rather than the engine's UTC default. The production
value lives in the gitignored .env; here we set it deterministically.
See scripts.utils.workspace.get_default_tz().

This file also holds the re-exec guard for the whole suite; see below.
"""
import os
import shutil
import sys
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

# The same isolation, one directory wider, and the one that was missing.
#
# `.claude/state/` sits under the ENGINE root, not the data overlay, so the
# `HEADING_OS_DATA` pin every isolated test in this suite relies on does not
# redirect it and never did. The evidence was on disk on 2026-09-01, written by
# this suite into the operator's live tree: `checkpoint-True.json` and
# `checkpoint-3.json` from the malformed-payload sweep's BAD_FIELD_VALUES,
# `checkpoint-sweep-session.json` from that file's own base payload, and
# `checkpoint-session.json` from its `[]` and `{}` cases collapsing onto a
# shared bucket.
#
# `CLAUDE_PROJECT_DIR` looked like the seam and is not. `project_root()` reads
# `payload["cwd"]` BEFORE it reads any environment variable, and that sweep
# sends `"cwd": <the engine root>` in every payload, so the pin those tests
# thought they held was beaten before it was read. `HEADING_OS_STATE_DIR` is a
# separate question asked ahead of the payload; `checkpoint_paths.state_root()`
# carries the full reasoning.
#
# Assignment, not setdefault, for the reason given two guards up. The owner
# check is the same one `WS_RATE_LIMIT_STATE` needs and for the same measured
# reason: this suite spawns pytest CHILDREN, each imports this file, and an
# unconditional reset would wipe a parent's state mid-run.
#
# A directory under `.logs/_pytest/` rather than a per-test `tmp_path`, matching
# `WS_RATE_LIMIT_STATE`: checkpoint state is session-scoped and several tests
# drive a hook in one subprocess and read the result in another, so a store that
# emptied between tests would break them. It is inside the clone, so it must
# stay gitignored - `.logs/` is, and `tests/test_a_state_directory_the_data_pin_
# never_redirected.py` asserts that, because an untracked-and-not-ignored write
# here is what makes the push wall start refusing.
_TEST_STATE_DIR = _ENGINE_ROOT / ".logs" / "_pytest" / "claude-state"
os.environ["HEADING_OS_STATE_DIR"] = str(_TEST_STATE_DIR)


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


def _mute_telegram_targets() -> None:
    """Blank every notification target visible right now, plus the bot token.

    `_MUTED_TELEGRAM` is frozen at import from this machine's `.env` and its
    inherited environment, so it names only the targets this machine actually
    configures. On the operator's laptop that is ODIN_CADENCE_ and SENTINEL_ and
    nothing else - CHECKPOINT_TELEGRAM_TARGET and OPS_RADAR_TELEGRAM_TARGET, the
    first two names the stall fuse reads, are absent from it.

    A name absent from the frozen list is a name nothing re-blanks, and that is
    a leak the fixture below cannot see. A test that removes such a name with
    `monkeypatch.delenv(name, raising=False)` gets NO undo entry: pytest's
    `MonkeyPatch.delitem` returns early when the name is already absent, before
    it appends to `_setitem`. A `load_env()` inside that test then CREATES the
    name, and nothing ever takes it back. MEASURED 2026-08-31:
    tests/test_a_notice_that_could_not_see_its_own_target.py left
    CHECKPOINT_TELEGRAM_TARGET and OPS_RADAR_TELEGRAM_TARGET at
    'fixture-sink-9911' for the remainder of the session, and
    tests/test_telegram_send_containment.py failed on both in a full serial run
    while passing when collected alone.

    So the second loop sweeps by SUFFIX over the live environment rather than
    over the frozen list, which is what makes the containment independent of
    which names this particular machine happens to have configured. The frozen
    list still runs first: it carries the bot token, whose name does not end in
    _TELEGRAM_TARGET.
    """
    for _name in _MUTED_TELEGRAM:
        os.environ[_name] = ""
    # Materialised before the writes: os.environ is mutated in the loop body.
    for _name in [k for k in os.environ if k.endswith("_TELEGRAM_TARGET")]:
        os.environ[_name] = ""


_mute_telegram_targets()


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
    `_mute_telegram_targets` sweeps by suffix, so a target this machine does not
    configure is re-armed here too; its docstring records why that matters.
    """
    os.environ["WORKSPACE_LOG_DIR"] = _TEST_LOG_DIR
    os.environ["WS_RATE_LIMIT_STATE"] = str(_TEST_RATE_STATE)
    os.environ["HEADING_OS_STATE_DIR"] = str(_TEST_STATE_DIR)
    _mute_telegram_targets()
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
# Three primitives are guarded: `connect`, `connect_ex`, and `getaddrinfo`.
#
# This paragraph used to say the opposite. It read "`connect` is the choke point
# rather than `getaddrinfo`: a name lookup carries no payload", and on 2026-09-01
# a probe test in this suite measured that claim false: with the guard armed,
# `socket.getaddrinfo("example.com", 443)` reached a real resolver and came back
# with a real address. A hostname IS a payload, because
# `<secret>.attacker.example` leaves this machine through a resolver without
# ever touching `connect`. The wrapper landed the same day; see the measurement
# recorded at `_install_socket_guard`.
#
# The old paragraph's second half was a real cost, and it was accepted rather
# than refuted: refusing a lookup does turn "no network" into a refusal raised
# one call earlier than the connect that wanted it. `NetworkAccessRefused` names
# the host and the verb ("resolve" or "connect to"), so the message says which.

_NETWORK_MARKERS = ("network", "integration", "acceptance")
_NETWORK_ALLOWED = False        # flipped per test by _no_egress below
# `requires_ollama` is declared in `pyproject.toml` and, until 2026-09-03,
# deselected nothing and gated nothing. It is now what opens the gateway leg of
# the guard; see `_refuse_egress` for the four tests that measured a daemon.
_OLLAMA_MARKERS = _NETWORK_MARKERS + ("requires_ollama",)
_OLLAMA_ALLOWED = False         # flipped per test by _no_egress below
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


def _is_wsl_host_gateway(address) -> bool:
    """The one address `address_is_local` calls local that is another machine."""
    if not isinstance(address, tuple) or not address:
        return False
    host = address[0]
    if isinstance(host, (str, bytes)):
        try:
            text = os.fsdecode(host) if isinstance(host, bytes) else host
        except (TypeError, ValueError):
            return False
        return text.split("%", 1)[0] == wsl_host_gateway()
    return False


def _refuse_egress(address, verb):
    if _NETWORK_ALLOWED:
        return
    # THE HOLE THIS CLOSES. `address_is_local` calls the WSL host gateway local,
    # deliberately and with a comment saying it is where the embedder lives. It
    # is the one address on that list that is a DIFFERENT MACHINE running a
    # DIFFERENT daemon, so the guard stood aside for exactly the traffic whose
    # presence or absence decides a test's verdict.
    #
    # MEASURED 2026-09-03: four unit tests reached ollama on the gateway with no
    # marker asking for it, and two of them CHANGED VERDICT when the daemon's
    # state changed and nothing else did. `test_ollama_accel_state_fs` passed
    # with the daemon down and no pin, failed with a pin set; the four
    # `test_recall_cross_lingual` cases went from skipped to passed when the
    # daemon came up. The same day, `scan_redundancy`'s corruption warning was
    # found to be suppressed by the same outage, and the test that should have
    # caught it had been green by luck since it was written.
    #
    # `requires_ollama` already existed in `pyproject.toml` and deselected
    # nothing. It is now the opt-in: a test that genuinely needs the daemon says
    # so and gets through here; every other test is told, loudly, at the socket.
    if _is_wsl_host_gateway(address) and not _OLLAMA_ALLOWED:
        raise NetworkAccessRefused(
            f"a test tried to {verb} {address!r}, the ollama host on the other "
            f"side of the WSL boundary. Its verdict would then be decided by "
            f"whether a foreign daemon happens to be running, not by this "
            f"repository's code. Inject the embedder (`scan_redundancy` and "
            f"`memory-index` both take one), patch "
            f"`scripts.utils.ollama_host.probe`, or mark the test "
            f"`@pytest.mark.requires_ollama` if it genuinely needs the daemon."
        )
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

    # Name RESOLUTION is egress too, and wrapping only the two connect
    # primitives left it open. MEASURED 2026-09-01 with a probe test in this
    # suite: `socket.getaddrinfo("example.com", 443)` returned
    # `('104.20.23.154', 443)` with the guard armed, so a unit test asking for a
    # third-party host sent a real DNS query to a real resolver before anything
    # refused it. Two costs, and the second is the one that matters. The query
    # itself carries the hostname off this machine, and a hostname is a channel:
    # `<payload>.attacker.example` is exfiltration through a resolver that never
    # touches `connect`. The refusal now happens one call earlier, before the
    # packet, which is the same rule the rest of this guard already keeps.
    #
    # `address_is_local` answers this shape unchanged: `("localhost", 80)`,
    # `(None, port)` and every loopback literal are local, so binding helpers
    # and the WSL host gateway are untouched. MEASURED over the whole suite the
    # same day, one scratch tree, one run each: 157 failed / 20112 passed
    # without this wrapper and 157 failed / 20113 passed with it. No test in
    # this repository resolves a third-party name for a legitimate reason.
    real_getaddrinfo = socket.getaddrinfo

    def guarded_getaddrinfo(host, port, *args, **kwargs):
        _refuse_egress((host, port), "resolve")
        return real_getaddrinfo(host, port, *args, **kwargs)

    socket.getaddrinfo = guarded_getaddrinfo

    def restore():
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex
        socket.getaddrinfo = real_getaddrinfo

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


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Open the gate only for a test that has asked for it, then close it again.

    A HOOK, not an autouse fixture, and the difference is the whole repair.

    MEASURED 2026-09-04 in the operator's main clone: the four
    `tests/test_recall_cross_lingual.py` cases carry
    `pytestmark = pytest.mark.requires_ollama` -- they are marked, correctly,
    and they still hit `NetworkAccessRefused`. They arrive as ERRORS rather than
    failures, which is the tell: it is fixture SETUP that refuses, not a test
    body.

    The cause is fixture ordering. pytest instantiates higher-scoped fixtures
    FIRST, and that file resolves its embedder in two `scope="module"` fixtures.
    An autouse function-scoped fixture has not run when they do, so the flag it
    flips is still closed and the marker that would have opened it has not been
    read. A per-test switch cannot cover work that happens before the per-test
    phase begins.

    `pytest_runtest_protocol` brackets the ENTIRE protocol for an item -- setup
    (including every higher-scoped fixture instantiated on its behalf), call,
    and teardown -- so a marked test's module fixture is inside the window. The
    same window is why the restore in the `finally` matters here in a way it
    did not before: a module fixture is torn down during the last item of its
    module, and that item is the one whose markers opened the gate.

    This was visible only where the daemon is REACHABLE. In a worktree with no
    pin the module fixtures skip before they open a socket, so the same code
    reported four honest skips and the defect could not appear. Green here and
    red in HELM meant different visibility, not a regression -- the second time
    that happened in one evening.
    """
    global _NETWORK_ALLOWED, _OLLAMA_ALLOWED
    previous = _NETWORK_ALLOWED, _OLLAMA_ALLOWED
    wants_ollama = any(
        item.get_closest_marker(name) is not None for name in _OLLAMA_MARKERS
    )
    _NETWORK_ALLOWED = any(
        item.get_closest_marker(name) is not None for name in _NETWORK_MARKERS
    )
    _OLLAMA_ALLOWED = wants_ollama

    # THE INHERITED EMBEDDING PIN is contained in the SAME window, and for the
    # same reason. It lived in an autouse function-scoped fixture until
    # 2026-09-04, so a module-scoped fixture resolving
    # `embeddings.index_embed_preference` read the ambient value before the
    # containment existed.
    #
    # Third ring of the containment `_mute_telegram_targets` above performs for
    # the notification targets: a name in the ambient environment silently
    # decides a verdict. `HEADING_OS_OLLAMA_EMBED_HOST` is source 2 of that
    # resolution, so a shell that exports it repoints every embed caller in the
    # suite at a host the test never named. MEASURED 2026-09-03:
    # `tests/test_ops_signals.py::test_ollama_accel_state_fs` passes with the
    # name unset and FAILS with it set to `auto:11434,11436`, on its very first
    # assertion -- the test writes its own config into a `tmp_path` precisely to
    # control this, and the environment reached around it.
    #
    # Blanked rather than deleted: every reader uses
    # `os.environ.get(name, "")`, so an empty value is the documented no-op,
    # while a deleted name is recreated by the first `load_env()` any test
    # performs. A test that genuinely needs the operator's pin marks itself
    # `requires_ollama` and keeps it -- the same switch that opens the gateway
    # leg of the egress guard.
    pin = "HEADING_OS_OLLAMA_EMBED_HOST"
    had_pin = pin in os.environ
    pin_was = os.environ.get(pin)
    if not wants_ollama:
        os.environ[pin] = ""
    try:
        yield
    finally:
        _NETWORK_ALLOWED, _OLLAMA_ALLOWED = previous
        if not wants_ollama:
            if had_pin:
                os.environ[pin] = pin_was
            else:
                os.environ.pop(pin, None)


# ============================================================
# Model resolution is pinned, so the suite does not go red on the clock
# ============================================================
#
# MEASURED 2026-08-31: 40 tests across 10 files failed with
# `NetworkAccessRefused`, and the whole cause was a cache 18 minutes past its
# 24-hour TTL. `scripts/utils/claude_models.resolve()` asks the Anthropic Models
# API for the newest model per family and caches the answer for a day
# (`CACHE_TTL_SECONDS`). Inside the TTL every one of those tests passed; outside
# it, `fetch_from_api()` reached for the network and the egress guard refused.
#
# The suite therefore went red on wall-clock time rather than on code, which is
# the shape `auto-memory/a-test-that-reads-the-host-clock-is-not-a-test.md`
# names. Nobody had changed anything.
#
# `fetch_from_api()` is written to degrade: it catches `HTTPError`, `URLError`,
# `OSError`, `TimeoutError` and the decode errors, returns `{}`, and `resolve()`
# then falls through to the cache and finally to `BASELINE`, which is exactly
# what a public clone with no API key gets. The ONE thing it cannot catch is
# `NetworkAccessRefused`, a `RuntimeError` this file raises.
#
# Two fixes were possible and this is the second one.
#
# Rejected: make `NetworkAccessRefused` inherit `OSError`. It would fix all 40
# at a stroke, and it would also let every `except OSError` in the tree silently
# swallow a test's attempt to leave the machine. The egress guard's whole value
# is being loud, so widening what can catch it is the wrong direction.
#
# Chosen: pin `fetch_from_api` to `{}` for the whole suite, which is the same
# thing `tests/test_no_claude_model_pins.py` already does for itself
# (`monkeypatch.setattr(claude_models, "fetch_from_api", dict)`). Model
# resolution then always degrades to cache-or-BASELINE, deterministically, and a
# test that genuinely needs the live API marks itself `network` and gets the real
# function back. One seam, and the next test author cannot forget it.
_MODEL_PIN_MARKERS = _NETWORK_MARKERS


# Packages whose own conftest pins the DATA root autouse. Mirrored here as a
# safety net, NOT as a replacement: both directory fixtures stay, and both set
# byte-for-byte the same value this one does.
_DATA_ROOT_PINNED_PACKAGES = ("bridge", "integration")


@pytest.fixture(autouse=True)
def _pin_the_data_root_even_on_package_re_entry(request, tmp_path, monkeypatch):
    """Hold the DATA-root pin when pytest drops a directory conftest's fixtures.

    `tests/bridge/conftest.py::_isolate_data_root` and
    `tests/integration/conftest.py::_pin_the_data_root` each set
    `HEADING_OS_DATA` to the test's own `tmp_path`. On pytest 9.1.1 those
    autouse fixtures are DROPPED when collection leaves a package and re-enters
    it, which a hand-written interleaved command line does.

    MEASURED 2026-09-01 in the real tree:

        pytest tests/bridge/test_config.py tests/test_data_root.py \\
               tests/bridge/test_telemetry.py
        -> 74 passed, 6 errors      (fixture not found: _isolate_data_root)

        pytest tests/integration/test_aggregate_crm_per_exec.py \\
               tests/test_data_root.py \\
               tests/integration/test_workspace_helpers_per_exec.py
        -> 1 failed, 36 passed

    The bridge shape is loud. The integration shape is the dangerous one: ONE
    test failed, the guard shard 7 had just added, and the other 36 ran green
    against the operator's live overlay. Four of that directory's files spawn
    child processes, a child inherits the environment, and the in-process
    overlay write guard cannot see a child at all.

    A root conftest is loaded once for the session and is never re-entered, so
    a pin placed here cannot be dropped the same way. This fixture sets exactly
    `monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))`, which is character
    for character what both directory fixtures set, and `tmp_path` is the same
    object both receive for a given test. In the ordinary run it is therefore a
    no-op duplicate and cannot change any existing result; when the directory
    fixture is missing it is the only pin left standing.

    Deliberately NOT a replacement for the two directory fixtures. Each of them
    carries the reasoning for its own directory, one of them is pinned by its
    own guard test, and deleting them would move that reasoning away from the
    tests it governs. Belt and braces is the right shape when the failure mode
    is "the belt silently is not there".

    Found by the shard-7 auditor of the 2026-08-31 tests campaign, which
    measured the hole, recorded it, and correctly left the repo-wide fix alone
    as outside a single shard's remit.
    """
    node_path = Path(str(request.node.fspath)).resolve()
    try:
        parts = node_path.relative_to(Path(__file__).resolve().parent).parts
    except ValueError:
        # A test collected from outside tests/ entirely. Not ours to pin.
        return
    if not parts or parts[0] not in _DATA_ROOT_PINNED_PACKAGES:
        return
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))


@pytest.fixture(autouse=True)
def _pin_model_resolution(request):
    """No unit test resolves a model id over the network.

    The per-process memo and the failure latch are cleared on the way in AND on
    the way out. They are module globals: one test that resolved a real id would
    otherwise hand it to every later test, and a `_FETCH_FAILED` left standing
    would suppress the fetch for a `network`-marked test that wanted it.
    """
    if any(request.node.get_closest_marker(name) is not None
           for name in _MODEL_PIN_MARKERS):
        yield
        return
    try:
        from scripts.utils import claude_models
    except ImportError:
        yield
        return

    real_fetch = claude_models.fetch_from_api
    claude_models.fetch_from_api = dict
    claude_models._RESOLVED.clear()
    claude_models._FETCH_FAILED = False
    try:
        yield
    finally:
        claude_models.fetch_from_api = real_fetch
        claude_models._RESOLVED.clear()
        claude_models._FETCH_FAILED = False


# ============================================================
# Which checkout is this? Asked, never assumed
# ============================================================
#
# `Path(__file__).parents[1]` is the checkout the SUITE WAS LAUNCHED FROM. It is
# HELM only sometimes, and in this workspace it is a YARD whenever engine work is
# in progress, which is whenever anybody is running these tests on purpose.
#
# MEASURED 2026-09-03. The same commit reports `24377 passed, 1 skipped, 0
# failed` in HELM and 97 failures in the YARD at `.yard/.heading-os/test-123`.
# Not one of the 97 is a code regression. They are assumptions about where the
# suite runs, in two shapes:
#
#   25 treat that path AS the main clone -- feeding it to `is_main_clone()`
#      expecting True, contrasting a synthetic yard against it, or requiring a
#      write into it to be refused. Every one of those polarities inverts when
#      the launching checkout is itself a worktree.
#   72 drive a script whose `main()` calls `require_main_clone(__file__)`, which
#      exits 2 from a worktree before the behaviour under test runs at all.
#
# Three fixtures below, one per shape. They exist here rather than in each file
# because a fix that lands in one of N copies is this repository's dominant
# defect, and N is 27 here.


@pytest.fixture(scope="session")
def helm_root() -> Path:
    """Absolute path of the MAIN CLONE this checkout belongs to.

    For a test that needs HELM's path as DATA -- a substring in a message, an
    argument handed to a predicate but never executed. When the test needs to
    RUN the tree under test as a main clone, use `armed_main_clone`: this one
    points at the operator's real HELM, whose working tree is whatever they
    have checked out, not what this branch changed.
    """
    from scripts.utils.clone_guard import main_clone_path
    return main_clone_path(Path(__file__).resolve().parent.parent)


@pytest.fixture
def armed_main_clone(tmp_path, request) -> Path:
    """A real MAIN CLONE carrying THIS checkout's working tree.

    The exact mirror of `armed_worktree` below, and the reason it has to exist:
    a worktree's `.git` is a file and the main clone's is a directory, so
    `is_main_clone()` can only answer True for something shaped like this. A
    test that asserts the True side needs one, and pointing at the operator's
    HELM would test THEIR committed code rather than the change under test.

    `git clone --shared` because it is cheap: the object database is shared
    rather than copied. MEASURED 2026-09-03 on this repository: the clone's
    `.git` is a directory, `--git-dir` equals `--git-common-dir`,
    `is_main_clone()` answers True and `main_clone_path()` returns the clone
    itself. Uncommitted and untracked files are copied over afterwards, the
    same way `armed_worktree` does, so the code under test is the code in this
    working tree.
    """
    import shutil
    import subprocess

    root = Path(__file__).resolve().parent.parent
    target = tmp_path / "main-clone-under-test"
    created = subprocess.run(
        ["git", "clone", "--quiet", "--shared", str(root), str(target)],
        capture_output=True, text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"could not clone a main checkout: {created.stderr.strip()}")

    # THE DIRECTORY IS NOT THE WHOLE OF IT.
    #
    # `git clone` registers nothing, so on the happy path there is nothing here
    # to clean. But a teardown that only removes the directory is one that
    # cannot clean up if this fixture ever produces a LINKED worktree instead,
    # and on 2026-09-03 it did: a mutation run replacing this fixture's
    # `git clone` with `git worktree add` left 8 prunable registrations in the
    # shared git directory, one per mutant and per xdist worker. They outlive
    # the session, the branch and the machine's patience.
    #
    # So the registration is read from the target's OWN `.git` -- the only place
    # it can be read, because git appends a collision suffix and the name cannot
    # be predicted -- and read NOW, before the rmtree destroys the file that
    # names it. Only that one entry is removed.
    #
    # NEVER `git worktree prune`. It reaches every other worktree of this
    # repository, including ones other processes are using right now. The same
    # reasoning, and the incident behind it, is recorded at `temporary_worktree`
    # below; this is the second copy of the technique and deliberately so, since
    # the two fixtures create their checkouts by different commands.
    registration = own_worktree_registration(target)

    def _remove_only_ours():
        shutil.rmtree(target, ignore_errors=True)
        drop_worktree_registration(registration)

    request.addfinalizer(_remove_only_ours)

    # The working tree, not just the commit. A guard being changed on this
    # branch has to be the guard under test.
    listing = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "-z"],
        cwd=str(root), capture_output=True, check=True,
    )
    for entry in listing.stdout.decode("utf-8", "surrogateescape").split("\0"):
        if len(entry) < 4:
            continue
        rel = entry[3:]
        source = root / rel
        if not source.is_file():
            continue
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return target


@pytest.fixture
def unguard_main_clone(monkeypatch):
    """Neutralise `require_main_clone` on a module the test loaded itself.

    For the 45 failures that load a script with `spec_from_file_location` and
    call `main()`: the guard exits 2 before any of the behaviour under test
    runs. Patching the loaded module's own attribute reaches only that copy,
    for that test.

    This does NOT leave the guard untested, and that distinction is the whole
    argument for doing it this way rather than skipping.
    `tests/test_guarded_entry_points_refuse_from_a_worktree.py` pins, through
    the AST and with a floor over the corpus, that the call is the first
    statement of `main()` and is passed `__file__`;
    `tests/test_clone_guard.py` pins that it fires. Those files own the
    control. These files own the behaviour behind it, and could not reach it.
    """
    def apply(module):
        if not hasattr(module, "require_main_clone"):
            raise AssertionError(
                f"{getattr(module, '__name__', module)!r} does not import "
                f"require_main_clone; this fixture is patching nothing, which "
                f"is how a neutralised guard becomes an unnoticed one")
        monkeypatch.setattr(module, "require_main_clone", lambda *a, **k: None)
        return module

    return apply


# The reason string every clone-gated skip carries. One literal, so
# `tests/test_a_suite_that_could_not_run_where_the_work_happens.py` can count
# them and refuse a new silent one.
MAIN_CLONE_SKIP = (
    "runs a HELM-only entry point in a child process, which refuses from a "
    "worktree with no in-process seam to patch; see conftest MAIN_CLONE_SKIP"
)


@pytest.fixture
def disarm_clone_guard(monkeypatch):
    """Neutralise the clone gate for a test whose SUBJECT is not the gate.

    Daemons run in HELM only (operator directive, 2026-09-03), so on 2026-09-03
    nineteen daemon entry points gained `require_main_clone(__file__)` as the
    first statement of `main()`. That is correct in production and it turned 25
    tests red in this worktree at once -- every one of them calling `main()` to
    ask about something else entirely: whether the status subcommand is wired,
    whether an unknown flag is rejected, whether a malformed checkpoint raises.

    Those tests are not weakened by disarming the gate. The gate has its own
    tests, in `tests/test_a_prohibition_written_as_a_list_of_verbs.py`, which
    assert its presence from the AST and drive real entry points in a real
    worktree. Leaving it armed here would only mean the suite could not run
    where the engine work happens, which is the failure
    `tests/test_a_suite_that_could_not_run_where_the_work_happens.py` exists to
    name.

    Takes the MODULE, because each guarded script does
    `from scripts.utils.clone_guard import require_main_clone` and so holds its
    own reference. Patching `clone_guard.require_main_clone` would leave every
    one of those bindings pointing at the original, and the test would pass or
    fail for a reason unrelated to what it did.

        def test_x(disarm_clone_guard):
            module = _load("scripts/sentinel.py")
            disarm_clone_guard(module)
            ...

    NOT for a child process: a subprocess re-imports the real function and this
    fixture cannot reach it. Use `main_clone_only` there, which skips out loud.
    """
    def disarm(module) -> None:
        assert hasattr(module, "require_main_clone"), (
            f"{getattr(module, '__name__', module)} holds no reference to "
            f"require_main_clone, so this fixture would silently do nothing. "
            f"If the guard moved, point this at where it now lives.")
        monkeypatch.setattr(module, "require_main_clone", lambda *a, **k: None)

    return disarm


@pytest.fixture
def main_clone_only():
    """Skip, out loud, when the suite is not running in the main clone.

    Only for the 27 failures where the guard fires in a CHILD process. There is
    no environment escape hatch by design -- `clone_guard.py` rejected one
    because a variable "can be lost by a new shell" -- and adding one to make a
    test pass would weaken the control in production.

    A skip nobody counts is a green suite that checks nothing, so the count is
    asserted elsewhere against a measured number rather than left to grow.
    """
    from scripts.utils.clone_guard import is_main_clone
    if not is_main_clone(Path(__file__).resolve().parent.parent):
        pytest.skip(MAIN_CLONE_SKIP)


# ============================================================
# A stub `herdr`, because the real one is shared across every worktree
# ============================================================
#
# `yard-bootstrap.sh` talks to the herdr server at six places: `workspace get`,
# `workspace rename`, `pane report-metadata` (on every step, through `badge`),
# `pane run`, and two `notification show`. There is ONE server per machine, so
# every one of those reaches the operator's live session and any OTHER worktree
# open beside it.
#
# MEASURED 2026-09-03. A mutation run over the bootstrap tests put
# "YARD: the engine/data contour is broken - step 6: the PreToolUse walls are
# not registered in this copy" on the operator's screen, from a test. The
# notification is the visible half. The dangerous half is silent: the script
# reads `WS_ID="${HERDR_WORKSPACE_ID:-}"` and `PANE_ID="${HERDR_PANE_ID:-}"`
# from the environment, the test helpers build their env from `os.environ`, and
# inside a herdr-managed session both are SET and name the operator's real
# workspace and pane. `workspace rename "$WS_ID" "$LABEL"` and
# `pane run "$PANE_ID" ...` were therefore aimed at a live pane belonging to
# somebody else's work.
#
# This is the same shape as the `git worktree prune` defect described below,
# against a different shared resource: an operation scoped to "this test" that
# is in fact scoped to the whole machine.
#
# The seam already existed and nothing used it: line 41 of the bootstrap reads
# `HERDR="${HERDR_BIN_PATH:-herdr}"`. One fixture fills it, and every test that
# executes the script goes through this fixture rather than carrying its own
# copy.



# ============================================================
# One checkout's own worktree registration
# ============================================================
#
# Extracted 2026-09-03. Two fixtures below need the same three facts, and the
# second copy of a technique is the one that stops being fixed:
#
#   * a LINKED worktree's `.git` is a FILE reading `gitdir: <shared>/worktrees/<n>`;
#     a MAIN clone's `.git` is a DIRECTORY and has no registration at all;
#   * the name cannot be predicted, because git appends a collision suffix when
#     two checkouts share a basename -- four concurrent xdist workers produced
#     four different ones on 2026-09-03;
#   * so it must be READ WHILE THE CHECKOUT STILL EXISTS, before any teardown
#     deletes the file that names it.


def own_worktree_registration(checkout: Path) -> Path | None:
    """The shared git dir entry `checkout` is registered under, or None."""
    try:
        content = (checkout / ".git").read_text(encoding="utf-8").strip()
    except OSError:
        # IsADirectoryError for a main clone, FileNotFoundError for a path that
        # is not a checkout. Neither has a registration, and neither is an error.
        return None
    if not content.startswith("gitdir:"):
        return None
    return Path(content.split(":", 1)[1].strip())


def drop_worktree_registration(registration: Path | None) -> None:
    """Remove ONE registration, the caller's own.

    NEVER `git worktree prune`. Prune reaches every worktree of this repository,
    including ones other processes are holding open right now -- several Claude
    sessions run against this repo at once and each may hold one. A cleanup
    scoped to "this test" that is in fact scoped to the whole machine is the
    same defect as the leak it would be fixing.
    """
    if registration is not None and registration.is_dir():
        shutil.rmtree(registration, ignore_errors=True)


class HerdrStub:
    """An executable that records every call and never reaches the server."""

    def __init__(self, binary: Path, log: Path):
        self.binary = binary
        self.log = log

    @property
    def calls(self) -> list[list[str]]:
        """Each invocation's argv, in order."""
        if not self.log.exists():
            return []
        return [line.split("\x1f") for line in
                self.log.read_text(encoding="utf-8").splitlines() if line]

    def env(self, base: dict | None = None) -> dict:
        """`base` (default `os.environ`) with the bootstrap pointed at the stub.

        `HERDR_WORKSPACE_ID` and `HERDR_PANE_ID` are dropped as well. The stub
        alone makes the calls harmless, but a test that inherits them is still
        naming the operator's live pane in its arguments, and a future caller
        that spawns the real binary would aim at it. Removing the identifiers
        means there is nothing to aim.
        """
        env = dict(os.environ if base is None else base)
        env["HERDR_BIN_PATH"] = str(self.binary)
        for name in ("HERDR_WORKSPACE_ID", "HERDR_PANE_ID"):
            env.pop(name, None)
        return env


def write_herdr_stub(directory: Path, *, exit_code: int = 0,
                     validate: bool = True) -> HerdrStub:
    """Create a `herdr` stub in `directory`. Used by the fixture and by tests
    that need a deliberately hostile one.

    IT VALIDATES. Until 2026-09-03 this stub recorded argv and exited 0 for
    anything at all, which made it a recorder rather than a check: it confirmed
    every shape it was handed, and three wrong ones reached the operator's
    machine behind a green suite. It now refuses argv that the CAPTURED herdr
    grammar rejects, with exit 2, which is what herdr 0.8.2 was measured to do.

    `validate=False` exists for the tests that drive the checker itself. It is
    not a way out of a failing call: a call this rejects is a call the real
    binary rejects, and the fix belongs in the caller.
    """
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / "herdr"
    log = directory / "herdr-calls.log"
    tests_dir = Path(__file__).resolve().parent
    # US (0x1f) between argv elements, not a space: a herdr argument can hold
    # spaces (a workspace label is one), and a space-joined log would not
    # round-trip into `HerdrStub.calls`.
    binary.write_text(
        f"#!{sys.executable}\n"
        "import sys, pathlib\n"
        f"sys.path.insert(0, {str(tests_dir)!r})\n"
        f"log = pathlib.Path({str(log)!r})\n"
        "argv = sys.argv[1:]\n"
        'with log.open("a", encoding="utf-8") as handle:\n'
        '    handle.write("\\x1f".join(argv) + "\\n")\n'
        f"if {validate!r}:\n"
        "    from herdr_contract import check\n"
        "    problem = check(argv)\n"
        "    if problem:\n"
        '        sys.stderr.write("herdr: " + problem + "\\n")\n'
        "        raise SystemExit(2)\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8")
    binary.chmod(0o755)
    return HerdrStub(binary, log)


@pytest.fixture
def herdr_stub(tmp_path) -> HerdrStub:
    """The one herdr stub. Every test that executes `yard-bootstrap.sh` uses it.

    Read `HerdrStub.env()` for what it neutralises and why it is not optional.
    """
    return write_herdr_stub(tmp_path / "herdr-stub")


# ============================================================
# A real git worktree, created and guaranteed removed
# ============================================================
#
# HELM/YARD work needs tests that run against a SEPARATE CHECKOUT of this
# repository, because the whole class of defect it guards against is a check
# that inspects the wrong tree and reports clean. A guard pointed at the
# untouched main clone produces exactly the result a healthy guard produces, so
# nothing short of a real worktree can tell the two apart.
#
# Teardown is doubled on purpose. `git worktree add` writes a registration into
# `.git/worktrees/<name>` in the SHARED git directory, so a leaked one outlives
# the test process and shows up in every later `git worktree list` on the
# operator's real repository. `try/finally` covers the ordinary failure; the
# finalizer covers an error raised inside another fixture's setup after this one
# yielded. `--force` because a test that dirtied the checkout is the normal case
# here.
#
# It does NOT call `git worktree prune`, and that is the point of this
# paragraph. `prune` operates on the whole SHARED registration directory, not on
# the worktree the fixture created, so it reaches every OTHER process holding a
# worktree of this repository at the same time. MEASURED 2026-09-03: with the
# full suite running under `-n auto` and a second `turn-check` lane started
# beside it, `git ls-files -z` inside a fixture worktree exited 128 and two
# guard tests failed, in a run whose 24318 other tests passed and which did not
# reproduce serially. `remove --force` already drops this worktree's own
# registration; the half-failed case it was there for is covered by deleting
# THIS worktree's registration directory, read from its own `.git` file, and
# nobody else's.


@pytest.fixture
def temporary_worktree(tmp_path, request):
    """Yield a Path to a throwaway git worktree of THIS repository.

    Detached HEAD, so the fixture never competes with a branch the operator has
    checked out and two tests can hold worktrees at once.
    """
    import shutil
    import subprocess

    # THE FIXTURE IS CLOSED ON ITS OWN CLONE, NOT ON THE OPERATOR'S HELM.
    #
    # This ran `git worktree add` with `cwd` set to THIS checkout until
    # 2026-09-03, and this checkout's `--git-common-dir` is HELM's `.git`. So
    # every worktree the test suite made was registered in the same directory as
    # the operator's live YARDs, alongside them.
    #
    # MEASURED 2026-09-03, and it is why this is isolation rather than tidier
    # deletion. A mutation planted against the cleanup helper below replaced
    # `shutil.rmtree(registration)` with
    #
    #     for entry in registration.parent.iterdir():
    #         shutil.rmtree(entry, ignore_errors=True)
    #
    # `registration.parent` IS `<helm>/.git/worktrees`, so `iterdir()` listed
    # every worktree of the repository and the loop removed all of them. The
    # directory was left empty and the live YARD the suite was running in lost
    # its own registration: `git rev-parse --git-common-dir` then exited 128 and
    # the session could not run another command until the operator rebuilt the
    # entry by hand from HELM.
    #
    # No amount of care in the deleting code fixes that, because the code being
    # careful is the code under test. The fix is that a test fixture must not be
    # able to name a live YARD at all. It clones first -- shared object database,
    # so it is cheap -- and adds its worktree to THAT.
    engine = Path(__file__).resolve().parent.parent
    root = tmp_path / "origin"
    cloned = subprocess.run(
        ["git", "clone", "--quiet", "--shared", str(engine), str(root)],
        capture_output=True, text=True,
    )
    if cloned.returncode != 0:
        pytest.skip(f"could not clone for a worktree: {cloned.stderr.strip()}")
    target = tmp_path / "yard-under-test"
    registration: list[Path] = []

    def _remove():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(target)],
            cwd=str(root), capture_output=True,
        )
        # Only ours. `git worktree remove` leaves the registration behind when
        # it half-fails, and pruning globally would reach other processes'.
        for path in registration:
            drop_worktree_registration(path)

    request.addfinalizer(_remove)
    created = subprocess.run(
        ["git", "worktree", "add", "--detach", str(target), "HEAD"],
        cwd=str(root), capture_output=True, text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"git worktree add failed: {created.stderr.strip()}")
    own = own_worktree_registration(target)
    if own is not None:
        registration.append(own)
    try:
        yield target
    finally:
        _remove()


@pytest.fixture
def worktree_origin(tmp_path) -> Path:
    """The main clone `temporary_worktree` was cut from.

    For a test asserting what a worktree POINTS BACK AT. That used to be
    `helm_root`, the operator's real HELM, because the fixture registered its
    worktrees there. It no longer does, and the property under test is unchanged
    -- a linked worktree resolves to the main clone it belongs to -- so only the
    address moved. Same `tmp_path`, so this is the very clone that worktree came
    from, not a second one.
    """
    return tmp_path / "origin"


@pytest.fixture
def armed_worktree(temporary_worktree):
    """A worktree carrying this checkout's UNCOMMITTED state, not just HEAD.

    `git worktree add` checks out a commit, so a test that drives a guard which
    is not committed yet would run against a copy that does not have it and
    fail for entirely the wrong reason. Copying the modified and
    untracked-not-ignored files makes the worktree reflect the tree under test.
    Once the work is committed this is a no-op for those files and the fixture
    stays correct either way, so no test needs to know which side of a commit
    it is on.

    Deletions are skipped: the worktree already lacks a file this checkout
    deleted from HEAD only if the deletion is committed, and copying cannot
    express a removal. A test that turns on a deleted file should say so.
    """
    import subprocess

    root = Path(__file__).resolve().parent.parent
    out = subprocess.run(
        ["git", "status", "--porcelain", "-z", "--untracked-files=all"],
        cwd=str(root), capture_output=True, check=True,
    ).stdout.decode("utf-8", "surrogateescape")
    for entry in out.split("\0"):
        if len(entry) < 4:
            continue
        status, rel = entry[:2], entry[3:]
        if "D" in status:
            continue
        source = root / rel
        if not source.is_file():
            continue
        destination = temporary_worktree / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        destination.chmod(source.stat().st_mode)
    return temporary_worktree


# Only the OUTERMOST pytest enforces the reachability ratchet. The same owner
# check `WS_RATE_LIMIT_STATE` needs sixty lines up, for the same measured
# reason: this suite spawns pytest CHILDREN and each imports this file. A child
# scoped to one directory would be judged against the whole suite's frozen
# number, which is both too generous for it and, at a baseline of 0, fatal.
# MEASURED 2026-09-03: three tests failed exactly that way, each because the
# child pytest it drives exited 1 on a budget written for the parent.
_OWNS_OVERLAY_WATCH = "WS_OVERLAY_WATCH_OWNER" not in os.environ
os.environ["WS_OVERLAY_WATCH_OWNER"] = "1"


# Reachable-child counts collected from xdist workers as each finishes. A list
# rather than an int so the hook can mutate it without a `global`, which is the
# shape that silently made a module name local and broke `arm()` earlier today.
_WORKER_REACHABLE_TOTAL = [0]


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    """Sum each worker's reachable-child count into the controller's total.

    `optionalhook=True` is not decoration. This hook belongs to xdist, and a
    run started with `-p no:xdist` has no such hookspec: pluggy then rejects
    the whole conftest with `PluginValidationError: unknown hook`, which
    surfaces as INTERNALERROR and takes the session down. MEASURED 2026-09-03 —
    and it also explains why the first aggregation attempt reported 0 under
    `-n auto`: the hook was never registered at all, so nothing was ever
    summed.

    `workeroutput` is xdist's own channel for exactly this. Without it the
    controller sees only its own process, which spawns no test children, and
    the total it enforces against is zero regardless of what the run did.
    """
    output = getattr(node, "workeroutput", None) or {}
    _WORKER_REACHABLE_TOTAL[0] += int(output.get("overlay_reachable", 0) or 0)


def pytest_sessionstart(session):
    global _RESTORE_SOCKET_GUARD
    _RESTORE_SOCKET_GUARD = _install_socket_guard()
    # MODE_REFUSE explicitly, never the environment's answer. `arm()` defaults to
    # `resolve_mode()`, which honours `HEADING_OS_OVERLAY_GUARD=record`, and a
    # test run must not be softened to logging by a variable someone exported for
    # a measurement and forgot. A suite that records instead of refusing is a
    # suite whose isolation failures all pass.
    _guard.arm(_guard.MODE_REFUSE)


# The frozen number of children this suite is known to spawn with the
# operator's live data root reachable. Read from a committed file, never
# hardcoded here, so lowering it is a visible diff and raising it is a
# deliberate act rather than an edit inside a 1000-line conftest.
_REACHABILITY_BASELINE = _ENGINE_ROOT / "config" / "overlay-reachability-baseline.json"


def _overlay_reachability_baseline() -> int:
    """The frozen count, or 0 when the file is missing or unreadable.

    Fails toward STRICT: an absent or corrupt baseline means every reachable
    child is over budget and the run goes red. The opposite default would turn
    a deleted file into a silently disabled guard, which is the shape this
    whole repair is about.
    """
    import json
    try:
        data = json.loads(_REACHABILITY_BASELINE.read_text(encoding="utf-8"))
        return int(data["reachable_children"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def pytest_sessionfinish(session, exitstatus):
    if not _guard._WATCH_BEFORE:
        return
    # A collect-only session imports modules and runs no test body, so it cannot
    # be the writer, and every import-time write it could have seen is seen again
    # by the ordinary run that imports the same modules. What it CAN do is fail
    # over someone else's write: its window is under a second, and anything on
    # the machine that touches the overlay inside that second lands on it.
    #
    # MEASURED 2026-09-02, and this is why the exemption exists. The full suite
    # went red on ONE test, `test_the_guard_still_passes_on_this_repository`.
    # `scripts/dev/check-readme-numbers.py` derives the security-test count by
    # spawning `pytest tests/security --collect-only`, that child loaded this
    # conftest, and the compaction hook wrote a handoff file into the operator's
    # overlay while it collected. The child printed "556 tests collected", then
    # exited 1. Nothing was wrong with the count, the guard, or the tree.
    if session.config.getoption("collectonly", False):
        return
    # NOT `if not complaints: return`. The ratchet below is about this run's
    # own children, not about the tree, and a run that reached the live root
    # while writing nothing is exactly the case a diff cannot see. Checking it
    # only when a file happened to move would make the guard's coverage depend
    # on whether a daemon fired.
    complaints = _guard.watch_complaints(_guard._WATCH_BEFORE, _guard._watch_snapshot())
    # A child process writes outside this interpreter, so no wrapper in the guard
    # can see it and the snapshot alone cannot say who. `_CHILD_SPAWNS` is the
    # list of children that COULD have reached the live overlay, each with the
    # test that spawned it. It is a suspect list, never an accusation: on
    # 2026-08-31 the previous wording of this report sent an agent hunting a test
    # that had written nothing, when the writer was a concurrent agent.
    # WHAT FAILS THE SESSION, and why it is no longer the diff.
    #
    # The diff is a whole-session before/after walk of a LIVE tree. On this
    # machine the overlay has permanent competing writers: MEASURED 2026-09-03,
    # `sync-exchange-daemon.service` was active, its journal showed
    # `job-start 15:53:08` / `job-ok 15:53:29`, and the two files this guard
    # named had mtimes of 15:53:29.1418 and 15:53:29.1425 -- inside that window,
    # 0.0007 s apart. The same sweep rewrote eight more files the guard did not
    # name, including a week of calendar. A test run cannot write next week's
    # calendar. It was the daemon, and it was the SECOND time: the same
    # accusation is already recorded in CHANGELOG for 2026-09-02.
    #
    # So "the run changed these files" is a claim this method can never
    # establish, in any run, and `scope-claims.md` obligation 1 says resolve the
    # claim rather than narrow it. The invariant the method CAN establish is a
    # different one, and it is the one that actually matters: no child of this
    # run had the operator's live data root reachable. `_CHILD_SPAWN_COUNT`
    # measures exactly that, with no race against anybody.
    #
    # It is a RATCHET rather than a flat zero because zero is not today's tree:
    # measured the same day, this suite spawns hundreds of such children. A hard
    # flip would paint every run red and teach people to ignore it. The baseline
    # only ever shrinks, the same shape as `config/test-vacuity-baseline.json`.
    #
    # No filename exclusions, deliberately. A list of "files daemons touch"
    # would go quiet the first time a new daemon wrote something, which is
    # precisely when it should shout.
    reachable = _guard._CHILD_SPAWN_COUNT
    baseline = _overlay_reachability_baseline()
    # SHARDED RUNS DO NOT ENFORCE, and this is not a convenience.
    #
    # `_CHILD_SPAWN_COUNT` is per PROCESS. Under `-n auto` every xdist worker is
    # its own process with its own counter, so each reports a shard of the
    # total and none of them can see the run. MEASURED 2026-09-03, the same
    # tree twice: `-n auto` reported 16, and a single-process run over a subset
    # reported 654. A budget frozen from the first and enforced against the
    # second is a number about nothing -- the "baseline frozen from a truncated
    # view" trap, and it took a turn-check to notice because the sharded figure
    # looked plausible.
    #
    # So the ratchet binds only where one process sees everything. A sharded
    # run still REPORTS its shard, labelled as one.
    worker = hasattr(session.config, "workerinput")
    if worker:
        # A worker cannot judge, but it can REPORT UPWARD. Without this the
        # ratchet binds only to single-process runs, and MEASURED 2026-09-03 a
        # serial full run takes 37 minutes against 8 under `-n auto` -- so the
        # gate would be dormant in every run anybody actually performs. A guard
        # that only fires where nobody looks is decoration.
        output = getattr(session.config, "workeroutput", None)
        if output is not None:
            output["overlay_reachable"] = reachable
    else:
        reachable += _WORKER_REACHABLE_TOTAL[0]

    # DID THE AGGREGATION ACTUALLY ARRIVE? MEASURED 2026-09-03: it did not.
    # Under `-n auto` the controller reported 0 while a serial run over the same
    # tree counted 9460, because `pytest_testnodedown` and the controller's
    # `pytest_sessionfinish` do not order the way this assumed. Enforcing
    # against a total of 0 would pass every sharded run while printing a number
    # and a baseline, which is a gate that looks armed and is not -- the exact
    # shape this whole repair exists to remove. So a sharded run whose workers
    # reported nothing SAYS SO and enforces nothing.
    controller_sharded = (not worker) and bool(
        session.config.getoption("numprocesses", None))
    aggregation_lost = controller_sharded and _WORKER_REACHABLE_TOTAL[0] == 0

    enforced = _OWNS_OVERLAY_WATCH and not worker and not aggregation_lost
    over = enforced and reachable > baseline

    established = [
        f"{len(complaints)} observation(s) about the overlay tree: "
        + "; ".join(complaints),
        f"{reachable} child process(es) of this run had the operator's live "
        f"data root reachable (frozen baseline {baseline}"
        + ("" if enforced else ", not enforced: " + (
            "sharded run, this is one worker's shard reported upward" if worker
            else "sharded run and no worker total arrived, so this number is "
                 "NOT the run's" if aggregation_lost
            else "nested pytest"))
        + ")",
    ]
    spawns = _guard._CHILD_SPAWNS
    if spawns:
        shown = spawns[:10]
        established.append(
            "examples: "
            + "; ".join(f"{nodeid} -> {cmd}" for nodeid, cmd in shown)
            + ("" if len(spawns) <= 10 else f" (+{len(spawns) - 10} more kept)")
        )

    not_established = (
        "NOT established: which process performed those writes. This is a "
        "before/after diff of a live tree, and daemons write it on their own "
        "schedule. The half that CAN name a culprit is the in-process wrapper, "
        "which refuses at the moment of the write and puts the test in the "
        "traceback; it did not fire, so no test in this interpreter wrote there."
    )

    if not over and not complaints:
        return   # nothing observed and nothing new reached: say nothing

    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("")
        label = "ERROR" if over else "NOTE"
        reporter.write_line(f"{label}: overlay watch. " + " | ".join(established),
                            red=over, yellow=not over)
        reporter.write_line(not_established, yellow=True)
        if over:
            reporter.write_line(
                f"More children reached the live data root than the frozen "
                f"{baseline}. Pass HEADING_OS_DATA pointing at a tmp_path to "
                f"whatever this run added.", red=True)
    if over:
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
