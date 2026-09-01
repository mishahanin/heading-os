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


def pytest_sessionstart(session):
    global _RESTORE_SOCKET_GUARD
    _RESTORE_SOCKET_GUARD = _install_socket_guard()
    # MODE_REFUSE explicitly, never the environment's answer. `arm()` defaults to
    # `resolve_mode()`, which honours `HEADING_OS_OVERLAY_GUARD=record`, and a
    # test run must not be softened to logging by a variable someone exported for
    # a measurement and forgot. A suite that records instead of refusing is a
    # suite whose isolation failures all pass.
    _guard.arm(_guard.MODE_REFUSE)


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
