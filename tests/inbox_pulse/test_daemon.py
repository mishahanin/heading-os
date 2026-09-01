"""Tests for scripts/inbox_pulse/daemon.py.

No real Exchange connection is made and no real state files are written to the
workspace: every test that can reach the state directory pins
``INBOX_PULSE_STATE_DIR`` at ``tmp_path``, and every test that can reach EWS
mocks ``EWSConnection``.

Corrected 2026-08-30. This used to read "All tests mock EWSConnection and state
helpers", which was false of three of them: ``test_check_mode_fails_on_missing_env``
mocked neither and ran the real ``health_check()`` against the live state
directory, and ``test_domain_of_extracts_domain_part`` /
``test_signal_handler_sets_shutdown_event`` mock nothing because they need
nothing. The guarantee that actually matters — no live Exchange, no live state
file — is now stated in the form the tests enforce, and the missing state-dir
pin has been added.

Sovereignty check: test_main_loop_writes_jsonl_per_event verifies that JSONL
entries contain only sender_domain (not full address) and subject_length (not
subject text). No body data ever appears.
"""

from __future__ import annotations

import importlib
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
pytest.importorskip("exchangelib")  # F-7.1: skip on a core-only clone (needs the email extra)

# ---------------------------------------------------------------------------
# Workspace on sys.path
# ---------------------------------------------------------------------------
_WORKSPACE = Path(__file__).resolve().parent.parent.parent
if str(_WORKSPACE) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE))


# ---------------------------------------------------------------------------
# Module import helpers
# ---------------------------------------------------------------------------


def _reload_paths(state_dir: Path | None = None, monkeypatch=None):
    """Reset module-level caches in paths.py and optionally redirect state dir."""
    import scripts.inbox_pulse.paths as mod
    mod._workspace_root_cache = None
    mod._state_dir_cache = None
    if state_dir is not None and monkeypatch is not None:
        monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(state_dir))
    return mod


def _import_daemon():
    """Import (or reimport) the daemon module with a clean shutdown event."""
    import scripts.inbox_pulse.daemon as mod
    importlib.reload(mod)
    return mod


def _make_fake_event(
    event_type="NewMail",
    item_id="AAAA111",
    parent_folder_id="INBOX-ID",
    datetime_received="2026-05-27T10:00:00+00:00",
):
    return {
        "event_type": event_type,
        "timestamp": "2026-05-27T10:00:00+04:00",
        "item_id": item_id,
        "parent_folder_id": parent_folder_id,
        "datetime_received": datetime_received,
    }


# ---------------------------------------------------------------------------
# Test 1: --check passes with all valid preconditions
# ---------------------------------------------------------------------------


def test_check_mode_passes_with_valid_env(monkeypatch, tmp_path):
    """health_check() returns 0 when env vars are set, state dir is writable,
    and EWSConnection connects successfully."""
    monkeypatch.setenv("EXCHANGE_EMAIL", "ceo@31c.io")
    monkeypatch.setenv("EXCHANGE_PASSWORD", "secret")  # pragma: allowlist secret
    monkeypatch.setenv("EXCHANGE_SERVER", "mail.31c.io")
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    _reload_paths(tmp_path, monkeypatch)

    # Reload FIRST, then patch. `_import_daemon` calls `importlib.reload`, which
    # re-executes `from .exchange import EWSConnection` and puts the REAL class
    # straight back over the mock. Proven 2026-08-27: inside the patch context,
    # `mod.EWSConnection` is a MagicMock before the reload and
    # `<class scripts.inbox_pulse.exchange.EWSConnection>` after it. So this test
    # ran the real connection object and passed anyway, because
    # `exchangelib.Account(autodiscover=False)` constructs without contacting the
    # server - which is also the reason `health_check` now touches `.root`.
    mod = _import_daemon()

    mock_ews = MagicMock()
    mock_ews.account = MagicMock()  # accessing .account.root triggers connect

    with patch.object(mod, "EWSConnection", return_value=mock_ews) as ctor:
        result = mod.health_check()

    assert result == 0
    # And the stub was the thing that answered. Without this the test passes
    # whether or not the patch survived, which is exactly how it spent its life.
    assert ctor.call_count == 1, "health_check never constructed an EWSConnection"
    mock_ews.disconnect.assert_called_once()


def test_check_mode_fails_when_the_server_cannot_be_reached(monkeypatch, tmp_path):
    """The probe must FAIL when the round trip fails, not merely when the
    object cannot be built.

    This is the test that makes `ews.account.root` measurable. A `MagicMock`
    answers `.account` and `.account.root` identically, so a mutation between
    the two is invisible to any test that only asserts success - reverting
    `.root` survived the harness on 2026-08-27 for exactly that reason. A stub
    whose `.account` resolves and whose `.account.root` RAISES separates them:
    with `.root` the probe returns 1, without it 0.

    It also pins the production defect. Measured against
    `no-such-host.invalid`: `EWSConnection().account` returned an
    `exchangelib.Account` in 0.25 s with no network, because `_connect` builds
    it with `autodiscover=False` and that constructor is lazy. The probe printed
    "OK: env vars present, state dir writable, EWS connectable".
    """
    monkeypatch.setenv("EXCHANGE_EMAIL", "ceo@31c.io")
    monkeypatch.setenv("EXCHANGE_PASSWORD", "secret")  # pragma: allowlist secret
    monkeypatch.setenv("EXCHANGE_SERVER", "mail.31c.io")
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    _reload_paths(tmp_path, monkeypatch)

    mod = _import_daemon()

    class _UnreachableAccount:
        @property
        def root(self):
            raise OSError("connection refused")

    mock_ews = MagicMock()
    mock_ews.account = _UnreachableAccount()

    with patch.object(mod, "EWSConnection", return_value=mock_ews):
        result = mod.health_check()

    assert result == 1, (
        "the probe reported healthy while the server refused the round trip"
    )


# ---------------------------------------------------------------------------
# Test 2: --check fails when env var is missing
# ---------------------------------------------------------------------------


def test_check_mode_fails_on_missing_env(monkeypatch, tmp_path):
    """health_check() returns 1 and prints diagnostic when EXCHANGE_EMAIL absent.

    Isolated 2026-08-30. This was the one `--check` test that set no
    `INBOX_PULSE_STATE_DIR`, so it ran `health_check()` against the REAL
    workspace state directory. It is safe only because `health_check` validates
    env vars before it probes the state dir for writability, and that ordering
    is nobody's invariant: swap the two checks — an entirely reasonable
    refactor — and this test starts writing a probe file into the live
    `state/email-triage/`, contradicting the module docstring's guarantee. The
    pin costs one line and removes the dependence on check order.
    """
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("EXCHANGE_PASSWORD", "secret")  # pragma: allowlist secret
    monkeypatch.setenv("EXCHANGE_SERVER", "mail.31c.io")
    monkeypatch.delenv("EXCHANGE_EMAIL", raising=False)

    # Stub load_env so it doesn't re-populate from the workspace .env file
    monkeypatch.setattr(
        "scripts.utils.workspace.load_env",
        lambda *a, **kw: None,
    )

    mod = _import_daemon()
    result = mod.health_check()

    assert result == 1


# ---------------------------------------------------------------------------
# Test 3: --check fails when state dir is unwritable
# ---------------------------------------------------------------------------


def test_check_mode_fails_on_unwritable_state_dir(monkeypatch, tmp_path):
    """health_check() returns 1 when the state dir write-check raises PermissionError."""
    monkeypatch.setenv("EXCHANGE_EMAIL", "ceo@31c.io")
    monkeypatch.setenv("EXCHANGE_PASSWORD", "secret")  # pragma: allowlist secret
    monkeypatch.setenv("EXCHANGE_SERVER", "mail.31c.io")
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    _reload_paths(tmp_path, monkeypatch)

    # Load the module first, then patch its get_state_dir binding
    mod = _import_daemon()

    # Build a mock Path whose child raises on write_text
    health_check_tmp = MagicMock(spec=Path)
    health_check_tmp.write_text = MagicMock(side_effect=PermissionError("read-only filesystem"))

    mock_path = MagicMock(spec=Path)
    mock_path.__truediv__ = MagicMock(return_value=health_check_tmp)

    monkeypatch.setattr(mod, "get_state_dir", lambda: mock_path)

    result = mod.health_check()

    assert result == 1


# ---------------------------------------------------------------------------
# Test 4: --check fails when EWS is unreachable
# ---------------------------------------------------------------------------


def test_check_mode_fails_on_ews_unreachable(monkeypatch, tmp_path):
    """health_check() returns 1 when EWSConnection().account raises."""
    monkeypatch.setenv("EXCHANGE_EMAIL", "ceo@31c.io")
    monkeypatch.setenv("EXCHANGE_PASSWORD", "secret")  # pragma: allowlist secret
    monkeypatch.setenv("EXCHANGE_SERVER", "mail.31c.io")
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    _reload_paths(tmp_path, monkeypatch)

    # Load the module first, then patch EWSConnection in its namespace
    mod = _import_daemon()

    # EWSConnection instance whose .account property raises
    mock_ews = MagicMock()
    type(mock_ews).account = property(
        fget=lambda self: (_ for _ in ()).throw(ConnectionError("server unreachable"))
    )

    monkeypatch.setattr(mod, "EWSConnection", lambda: mock_ews)

    result = mod.health_check()

    assert result == 1


# ---------------------------------------------------------------------------
# Test 5: _domain_of helper
# ---------------------------------------------------------------------------


def test_domain_of_extracts_domain_part():
    """_domain_of correctly extracts domain or returns empty string."""
    mod = _import_daemon()

    assert mod._domain_of("charlie@contoso.com") == "contoso.com"
    assert mod._domain_of("no-at-sign") == ""
    assert mod._domain_of("") == ""
    assert mod._domain_of("user@sub.example.org") == "sub.example.org"


# ---------------------------------------------------------------------------
# Test 6: signal handler sets shutdown event
# ---------------------------------------------------------------------------


def test_signal_handler_sets_shutdown_event():
    """Calling _handle_signal directly sets _shutdown_event."""
    mod = _import_daemon()

    # Clear first to ensure we're testing the set
    mod._shutdown_event.clear()
    assert not mod._shutdown_event.is_set()

    mod._handle_signal(signal.SIGTERM, None)

    assert mod._shutdown_event.is_set()

    # Restore for other tests
    mod._shutdown_event.clear()


# ---------------------------------------------------------------------------
# Shared helper: build a mock rules_engine + classifier for shadow-mode tests
# ---------------------------------------------------------------------------


def _make_mock_rules_engine(reload_return=False):
    """Return a MagicMock RulesEngine whose reload_if_changed returns reload_return."""
    mock_re = MagicMock()
    mock_re.reload_if_changed.return_value = reload_return
    return mock_re


def _make_mock_classifier(tier="LOW", weight=0, breakdown=None):
    """Return a MagicMock CheapClassifier.classify returning given values."""
    if breakdown is None:
        breakdown = {}
    mock_clf = MagicMock()
    mock_clf.classify.return_value = {
        "tier_guess": tier,
        "weight": weight,
        "reason_breakdown": breakdown,
    }
    return mock_clf


# ---------------------------------------------------------------------------
# Test 7: main loop writes JSONL with sovereignty discipline (polling version)
# ---------------------------------------------------------------------------


def test_main_loop_writes_jsonl_per_event(monkeypatch, tmp_path):
    """Polling loop logs 2 events to JSONL; each has required keys; no sovereign data.

    Shadow-mode: entries contain mode='shadow', tier_guess, weight, reason_breakdown.
    Sovereignty: sender_domain only (not full address); subject_length only (not text).
    """
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    _reload_paths(tmp_path, monkeypatch)

    mod = _import_daemon()

    shutdown = threading.Event()

    base_cursor = datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc)

    fake_events = [
        {
            "event_type": "NewMail",
            "timestamp": "2026-05-27T10:00:00+04:00",
            "item_id": "AAAA111",
            "parent_folder_id": "INBOX-ID",
            "datetime_received": "2026-05-27T10:00:00+00:00",
        },
        {
            "event_type": "NewMail",
            "timestamp": "2026-05-27T10:01:00+04:00",
            "item_id": "BBBB222",
            "parent_folder_id": "INBOX-ID",
            "datetime_received": "2026-05-27T10:01:00+00:00",
        },
    ]

    mock_ews = MagicMock()

    # poll_inbox yields 2 events then we set shutdown so the loop exits
    call_count = {"n": 0}

    def _fake_poll(since=None):
        call_count["n"] += 1
        yield from fake_events
        shutdown.set()

    mock_ews.poll_inbox.side_effect = _fake_poll

    # fetch_item: returns item with sender + subject
    fake_item = MagicMock()
    fake_item.sender = MagicMock()
    fake_item.sender.email_address = "alice@contoso.com"
    fake_item.subject = "Proposal review"
    mock_ews.fetch_item.return_value = fake_item

    written: list[tuple[str, dict]] = []

    def _capture_write(filename: str, entry: dict) -> None:
        written.append((filename, entry))

    cursor_store = {"value": base_cursor}

    def _get_cursor():
        return cursor_store["value"]

    def _set_cursor(dt):
        cursor_store["value"] = dt

    mock_re = _make_mock_rules_engine()
    mock_clf = _make_mock_classifier(tier="LOW", weight=0)

    mod._main_loop(
        shutdown_event=shutdown,
        ews=mock_ews,
        write_log_fn=_capture_write,
        fetch_item_fn=mock_ews.fetch_item,
        get_cursor_fn=_get_cursor,
        set_cursor_fn=_set_cursor,
        rules_engine=mock_re,
        classifier=mock_clf,
    )

    # 2 events logged
    assert len(written) == 2, f"Expected 2 log entries, got {len(written)}"

    for filename, entry in written:
        # Required keys
        assert "ts" in entry
        assert "event_type" in entry
        assert "message_id" in entry
        assert "parent_folder_id" in entry
        assert "sender_domain" in entry
        assert "subject_length" in entry
        assert "mode" in entry
        # Shadow mode (not raw)
        assert entry["mode"] == "shadow"
        # Classifier output keys present
        assert "tier_guess" in entry
        assert "weight" in entry
        assert "reason_breakdown" in entry

        # Sovereignty: only domain, not full address
        assert entry["sender_domain"] == "contoso.com"
        assert "alice@contoso.com" not in json.dumps(entry), "Full sender address leaked"

        # Sovereignty: only length, not subject text
        assert entry["subject_length"] == len("Proposal review")
        assert "Proposal review" not in json.dumps(entry), "Subject text leaked"


# ---------------------------------------------------------------------------
# Test 8: main loop retries on poll error (was: reconnects on ConnectionError)
# ---------------------------------------------------------------------------


def test_main_loop_retries_on_poll_error(monkeypatch):
    """Poll cycle failure triggers backoff (shutdown_event.wait(60)) then retry."""
    mod = _import_daemon()

    shutdown = threading.Event()
    poll_call_count = {"n": 0}

    mock_ews = MagicMock()

    def _fake_poll(since=None):
        poll_call_count["n"] += 1
        if poll_call_count["n"] == 1:
            raise Exception("simulated Exchange error")
        # Second call: yield nothing and signal shutdown
        shutdown.set()
        return iter([])

    mock_ews.poll_inbox.side_effect = _fake_poll

    cursor_store = {"value": datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc)}

    # Capture wait() calls to verify backoff
    original_wait = shutdown.wait
    wait_calls = []

    def _recording_wait(timeout=None):
        wait_calls.append(timeout)
        # Don't actually sleep in tests
        return shutdown.is_set()

    shutdown.wait = _recording_wait

    mod._main_loop(
        shutdown_event=shutdown,
        ews=mock_ews,
        write_log_fn=MagicMock(),
        fetch_item_fn=MagicMock(),
        get_cursor_fn=lambda: cursor_store["value"],
        set_cursor_fn=lambda dt: None,
        rules_engine=_make_mock_rules_engine(),
        classifier=_make_mock_classifier(),
    )

    # poll_inbox called twice: first raises, second yields empty + sets shutdown
    assert poll_call_count["n"] == 2, f"Expected 2 poll calls, got {poll_call_count['n']}"
    # Backoff wait(60) must have been called after the error
    assert 60 in wait_calls, f"Expected backoff wait(60) in calls {wait_calls}"


# ---------------------------------------------------------------------------
# Test 9: heartbeat thread writes periodically
# ---------------------------------------------------------------------------


def test_heartbeat_thread_writes_state_json_to_the_state_dir(tmp_path, monkeypatch):
    """One real beat, through the real writer, landing in the real state dir.

    RENAMED 2026-09-01 from `test_heartbeat_thread_writes_periodically`. It
    asserts that `state.json` EXISTS after a short window, which one write
    satisfies, so it never measured the "periodically" in its own name. The
    periodicity claim is measured by
    `test_the_heartbeat_keeps_beating_until_shutdown` below, deterministically
    and without a sleep. What this test is genuinely worth keeping for is the
    other half: that the thread wiring, the real `write_heartbeat`, and
    `INBOX_PULSE_STATE_DIR` compose into a file on disk with the right fields.
    """
    monkeypatch.setenv("INBOX_PULSE_STATE_DIR", str(tmp_path))
    _reload_paths(tmp_path, monkeypatch)

    import scripts.inbox_pulse.state as state_mod
    importlib.reload(state_mod)

    mod = _import_daemon()

    shutdown = threading.Event()

    # Run the heartbeat loop with a very short tick (0.05s)
    thread = threading.Thread(
        target=mod._heartbeat_loop,
        args=(shutdown, lambda: 7),
        kwargs={"tick_seconds": 0.05},
        daemon=True,
    )
    thread.start()
    time.sleep(0.2)  # allow at least 3 ticks
    shutdown.set()
    thread.join(timeout=2)

    # Verify state.json was written with required fields
    state_file = tmp_path / "state.json"
    assert state_file.exists(), "state.json not created by heartbeat thread"
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert "last_heartbeat" in data
    assert "daemon_pid" in data
    assert "queue_depth" in data


# ---------------------------------------------------------------------------
# The heartbeat's periodicity, measured. NEW 2026-09-01.
#
# MEASURED that day by turning the loop into a single pass:
#
#     -   while not shutdown_event.is_set():
#     +   if not shutdown_event.is_set():
#
#     tests/inbox_pulse                     -> 226 passed (baseline: 226 passed)
#     the 45-file wide set + tests/contract -> 7 failed, 1199 passed, 3 skipped
#                                              (identical to baseline; those 7
#                                               are sandbox-environment
#                                               failures, present either way)
#
# A heartbeat that beats once and stops is what the fleet-health reader sees as
# a daemon that died seconds after boot, and nothing in the suite could tell the
# difference. No thread and no sleep here: the loop is driven in the calling
# thread with `tick_seconds=0`, and the spy ends it by setting the event, so the
# count is exact rather than a function of how loaded the machine is.
# ---------------------------------------------------------------------------


def test_the_heartbeat_keeps_beating_until_shutdown(monkeypatch):
    """Three beats, counted, not inferred from a file existing."""
    mod = _import_daemon()

    shutdown = threading.Event()
    beats: list[dict] = []

    def _spy_write_heartbeat(extra=None):
        beats.append(dict(extra or {}))
        if len(beats) == 3:
            shutdown.set()

    monkeypatch.setattr(mod, "write_heartbeat", _spy_write_heartbeat)

    mod._heartbeat_loop(shutdown, lambda: 7, tick_seconds=0)

    assert len(beats) == 3, (
        f"the heartbeat loop produced {len(beats)} beat(s); it must keep "
        f"beating until shutdown, not write once and stop")
    # The queue-depth callable is consulted on every beat, not once and cached.
    assert beats == [{"queue_depth": 7}] * 3


def test_the_heartbeat_stops_when_shutdown_is_already_set(monkeypatch):
    """Anchor. A loop that ignored the event would satisfy the count above by
    running forever, and would keep a shut-down daemon writing heartbeats that
    say it is alive."""
    mod = _import_daemon()

    shutdown = threading.Event()
    shutdown.set()
    beats: list[dict] = []
    monkeypatch.setattr(mod, "write_heartbeat",
                        lambda extra=None: beats.append(dict(extra or {})))

    mod._heartbeat_loop(shutdown, lambda: 0, tick_seconds=0)

    assert beats == [], "a heartbeat fired after shutdown was requested"


def test_one_failed_heartbeat_write_does_not_stop_the_beat(monkeypatch, caplog):
    """The `except` inside the loop, which had no case.

    A heartbeat write can fail transiently: the state directory is on the data
    overlay, and a remount or a full disk takes it away for a moment. The loop
    catches and carries on by design. Without that, one bad write kills the
    liveness signal of a daemon that is otherwise working perfectly.
    """
    import logging

    mod = _import_daemon()

    shutdown = threading.Event()
    calls = {"n": 0}

    def _flaky(extra=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("state dir vanished")
        if calls["n"] == 3:
            shutdown.set()

    monkeypatch.setattr(mod, "write_heartbeat", _flaky)

    with caplog.at_level(logging.WARNING, logger="inbox_pulse.daemon"):
        mod._heartbeat_loop(shutdown, lambda: 0, tick_seconds=0)

    assert calls["n"] == 3, "the loop stopped after the failed write"
    assert any("Heartbeat write failed" in r.getMessage() for r in caplog.records), (
        f"the failure was swallowed with no log line: "
        f"{[r.getMessage() for r in caplog.records]}")


# ---------------------------------------------------------------------------
# The Healthchecks deadman, both jaws. NEW 2026-09-01.
#
# `_main_loop` ends a CLEAN poll cycle with `hc_ping("STEWARD_HC_EMAIL_TRIAGE")`,
# and the comment beside it states the contract: "The `continue` above skips
# this on failure, so a wedged Exchange poll stops the pings and trips an
# external alert." Nothing measured either half. MEASURED 2026-09-01 with two
# mutations, each against the 45 test files anywhere in tests/ that name
# inbox_pulse, observability_safe, healthchecks or hc_ping, plus tests/contract:
#
#     the ping deleted outright             -> 7 failed, 1199 passed, 3 skipped
#     a ping added to the failure path too  -> 7 failed, 1199 passed, 3 skipped
#                                  (baseline: 7 failed, 1199 passed, 3 skipped;
#                                   those 7 are sandbox-environment failures)
#
# Adding the ping to the failure path also survived the WHOLE tests/ suite:
# 50 failed, 20042 passed, 104 skipped, and the same 50 fail with every
# mutation reverted, so not one test anywhere detected it.
#
# Both directions of the one property this daemon's monitoring rests on, and
# the suite was blind to each. It is not a hypothetical: on 2026-08-17 the poll
# loop wedged for 33 hours and the check went green anyway, which is the outage
# `tests/test_deadman_ping_containment.py` was written after. That file guards
# the opposite property, that the SUITE cannot ping the live check. Nothing
# guarded the daemon's own semantics.
#
# The spy replaces `hc_ping` in the daemon's namespace, which is where
# `_main_loop` reads it, so these tests also cannot reach the network whatever
# the environment holds. That is belt and braces over the conftest containment,
# not a substitute for it.
# ---------------------------------------------------------------------------


def _ping_spy(monkeypatch, mod) -> list[str]:
    pings: list[str] = []
    monkeypatch.setattr(mod, "hc_ping", lambda name: pings.append(name) or True)
    return pings


def test_a_clean_poll_cycle_pings_the_deadman_once(monkeypatch):
    """A live daemon has to keep the check green, or the alert is a false one."""
    mod = _import_daemon()
    pings = _ping_spy(monkeypatch, mod)

    shutdown = threading.Event()
    mock_ews = MagicMock()

    def _fake_poll(since=None):
        shutdown.set()
        return iter([])

    mock_ews.poll_inbox.side_effect = _fake_poll

    mod._main_loop(
        shutdown_event=shutdown,
        ews=mock_ews,
        write_log_fn=MagicMock(),
        fetch_item_fn=MagicMock(),
        get_cursor_fn=lambda: datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc),
        set_cursor_fn=lambda dt: None,
        rules_engine=_make_mock_rules_engine(),
        classifier=_make_mock_classifier(),
    )

    assert pings == ["STEWARD_HC_EMAIL_TRIAGE"], (
        f"a completed poll cycle produced {pings!r}; the deadman must be pinged "
        f"exactly once, by the name of the check that watches this daemon")


def test_a_failed_poll_cycle_does_not_ping_the_deadman(monkeypatch):
    """The jaw that matters, and the one the 33-hour outage turned on.

    A ping on the failure path makes the check green while the daemon is
    wedged, which is worse than having no check: the operator reads the green
    and stops looking. The `continue` in the exception handler is the entire
    mechanism, and it is one edit away from being lost.
    """
    mod = _import_daemon()
    pings = _ping_spy(monkeypatch, mod)

    shutdown = threading.Event()
    mock_ews = MagicMock()
    poll_calls = {"n": 0}

    def _fake_poll(since=None):
        poll_calls["n"] += 1
        raise RuntimeError("EWS refused the request")

    mock_ews.poll_inbox.side_effect = _fake_poll

    # The backoff ends the run, so the loop takes the failure path exactly once
    # and never reaches a clean cycle. Without this the retry would succeed and
    # ping legitimately, which would not test anything.
    waits: list = []

    def _recording_wait(timeout=None):
        waits.append(timeout)
        shutdown.set()
        return True

    shutdown.wait = _recording_wait

    mod._main_loop(
        shutdown_event=shutdown,
        ews=mock_ews,
        write_log_fn=MagicMock(),
        fetch_item_fn=MagicMock(),
        get_cursor_fn=lambda: datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc),
        set_cursor_fn=lambda dt: None,
        rules_engine=_make_mock_rules_engine(),
        classifier=_make_mock_classifier(),
    )

    assert poll_calls["n"] == 1, "the loop did not take the failure path"
    assert waits == [60], f"the error backoff did not run: {waits}"
    assert pings == [], (
        f"a poll cycle that raised still pinged the deadman ({pings!r}); a "
        f"wedged daemon would report itself healthy")


# ---------------------------------------------------------------------------
# Test 10: enrichment failure doesn't crash (polling version)
# ---------------------------------------------------------------------------


def test_main_loop_enrichment_failure_doesnt_crash(monkeypatch):
    """When fetch_item raises, the raw event is still logged with empty enrichment."""
    mod = _import_daemon()

    shutdown = threading.Event()
    written: list[dict] = []

    fake_event = {
        "event_type": "NewMail",
        "timestamp": "2026-05-27T10:00:00+04:00",
        "item_id": "MOVED-ITEM",
        "parent_folder_id": "INBOX-ID",
        "datetime_received": "2026-05-27T10:00:00+00:00",
    }

    mock_ews = MagicMock()

    def _fake_poll(since=None):
        yield fake_event
        shutdown.set()

    mock_ews.poll_inbox.side_effect = _fake_poll

    def _fetch_raises(item_id):
        raise Exception("DoesNotExist: item moved or deleted")

    def _capture_write(filename: str, entry: dict) -> None:
        written.append(entry)

    mod._main_loop(
        shutdown_event=shutdown,
        ews=mock_ews,
        write_log_fn=_capture_write,
        fetch_item_fn=_fetch_raises,
        get_cursor_fn=lambda: datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc),
        set_cursor_fn=lambda dt: None,
        rules_engine=_make_mock_rules_engine(),
        classifier=_make_mock_classifier(),
    )

    # Event still logged despite fetch failure
    assert len(written) == 1, f"Expected 1 logged event, got {len(written)}"

    entry = written[0]
    assert entry["message_id"] == "MOVED-ITEM"
    assert entry["sender_domain"] == ""     # enrichment skipped
    assert entry["subject_length"] == 0     # enrichment skipped
    assert entry["mode"] == "shadow"


# ---------------------------------------------------------------------------
# Test 11: bootstrap sets cursor to now when cursor is None
# ---------------------------------------------------------------------------


def test_main_loop_bootstrap_sets_cursor_when_none(monkeypatch):
    """When get_cursor returns None, set_cursor is called with a datetime approx now."""
    mod = _import_daemon()

    shutdown = threading.Event()
    set_cursor_calls: list[datetime] = []

    mock_ews = MagicMock()

    # poll_inbox yields nothing; set shutdown immediately
    def _fake_poll(since=None):
        shutdown.set()
        return iter([])

    mock_ews.poll_inbox.side_effect = _fake_poll

    before = datetime.now(timezone.utc)

    mod._main_loop(
        shutdown_event=shutdown,
        ews=mock_ews,
        write_log_fn=MagicMock(),
        fetch_item_fn=MagicMock(),
        get_cursor_fn=lambda: None,
        set_cursor_fn=set_cursor_calls.append,
        rules_engine=_make_mock_rules_engine(),
        classifier=_make_mock_classifier(),
    )

    after = datetime.now(timezone.utc)

    # set_cursor must have been called at least once (for bootstrap)
    assert len(set_cursor_calls) >= 1, "set_cursor not called during bootstrap"
    bootstrap_dt = set_cursor_calls[0]
    # The bootstrap timestamp must be between before and after
    assert before <= bootstrap_dt <= after, (
        f"Bootstrap cursor {bootstrap_dt} not in [{before}, {after}]"
    )


# ---------------------------------------------------------------------------
# Test 12: cursor advances to latest datetime_received after processing items
# ---------------------------------------------------------------------------


def test_main_loop_advances_cursor_after_processing_items(monkeypatch):
    """After processing 2 items, set_cursor is called with the latest datetime_received."""
    mod = _import_daemon()

    shutdown = threading.Event()

    older_dt = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    newer_dt = datetime(2026, 5, 27, 10, 5, 0, tzinfo=timezone.utc)
    initial_cursor = datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc)

    fake_events = [
        {
            "event_type": "NewMail",
            "timestamp": "2026-05-27T10:00:00+04:00",
            "item_id": "ITEM-A",
            "parent_folder_id": "INBOX",
            "datetime_received": older_dt.isoformat(),
        },
        {
            "event_type": "NewMail",
            "timestamp": "2026-05-27T10:05:00+04:00",
            "item_id": "ITEM-B",
            "parent_folder_id": "INBOX",
            "datetime_received": newer_dt.isoformat(),
        },
    ]

    mock_ews = MagicMock()

    def _fake_poll(since=None):
        yield from fake_events
        shutdown.set()

    mock_ews.poll_inbox.side_effect = _fake_poll

    # fetch_item returns a simple mock item (no subject, no sender)
    mock_item = MagicMock()
    mock_item.sender = None
    mock_item.subject = None
    mock_ews.fetch_item.return_value = mock_item

    set_cursor_calls: list[datetime] = []

    mod._main_loop(
        shutdown_event=shutdown,
        ews=mock_ews,
        write_log_fn=MagicMock(),
        fetch_item_fn=mock_ews.fetch_item,
        get_cursor_fn=lambda: initial_cursor,
        set_cursor_fn=set_cursor_calls.append,
        rules_engine=_make_mock_rules_engine(),
        classifier=_make_mock_classifier(),
    )

    # set_cursor must have been called with newer_dt + 1s (fence-post fix to prevent
    # re-fetching the boundary item on the next poll cycle).
    from datetime import timedelta
    assert len(set_cursor_calls) >= 1, "set_cursor not called after processing items"
    final_cursor = set_cursor_calls[-1]
    expected_cursor = newer_dt + timedelta(seconds=1)
    assert final_cursor == expected_cursor, (
        f"Expected cursor advanced to {expected_cursor} (newer_dt+1s), got {final_cursor}"
    )


# ---------------------------------------------------------------------------
# Test 13: cursor NOT advanced when no items are processed
# ---------------------------------------------------------------------------


def test_main_loop_does_not_advance_cursor_when_no_items(monkeypatch):
    """When poll_inbox yields nothing, set_cursor is not called."""
    mod = _import_daemon()

    shutdown = threading.Event()

    initial_cursor = datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc)
    set_cursor_calls: list[datetime] = []

    mock_ews = MagicMock()

    def _fake_poll(since=None):
        shutdown.set()
        return iter([])

    mock_ews.poll_inbox.side_effect = _fake_poll

    mod._main_loop(
        shutdown_event=shutdown,
        ews=mock_ews,
        write_log_fn=MagicMock(),
        fetch_item_fn=MagicMock(),
        get_cursor_fn=lambda: initial_cursor,
        set_cursor_fn=set_cursor_calls.append,
        rules_engine=_make_mock_rules_engine(),
        classifier=_make_mock_classifier(),
    )

    # No items means cursor should not change
    assert len(set_cursor_calls) == 0, (
        f"set_cursor should not be called when no items processed, but was called with {set_cursor_calls}"
    )


# ---------------------------------------------------------------------------
# Test 14: classifier tier_guess merged into JSONL entry
# ---------------------------------------------------------------------------


def test_main_loop_classifies_with_tier_guess(monkeypatch):
    """classifier.classify result (MAYBE / weight=3) is merged into log entry."""
    mod = _import_daemon()

    shutdown = threading.Event()

    fake_event = _make_fake_event()
    mock_ews = MagicMock()

    def _fake_poll(since=None):
        yield fake_event
        shutdown.set()

    mock_ews.poll_inbox.side_effect = _fake_poll

    fake_item = MagicMock()
    fake_item.sender = MagicMock()
    fake_item.sender.email_address = "cto@partner.io"
    fake_item.subject = "Urgent partnership proposal"
    mock_ews.fetch_item.return_value = fake_item

    written: list[dict] = []

    breakdown = {"sender_override": None, "keyword_override": "promote_to_important", "crm_contact": 1}
    mock_clf = _make_mock_classifier(tier="MAYBE", weight=3, breakdown=breakdown)

    mod._main_loop(
        shutdown_event=shutdown,
        ews=mock_ews,
        write_log_fn=lambda fn, e: written.append(e),
        fetch_item_fn=mock_ews.fetch_item,
        get_cursor_fn=lambda: datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc),
        set_cursor_fn=lambda dt: None,
        rules_engine=_make_mock_rules_engine(),
        classifier=mock_clf,
    )

    assert len(written) == 1
    entry = written[0]
    assert entry["tier_guess"] == "MAYBE"
    assert entry["weight"] == 3
    assert entry["reason_breakdown"] == breakdown
    assert entry["mode"] == "shadow"


# ---------------------------------------------------------------------------
# Test 15: no sender email -> classification skipped, defaults kept
# ---------------------------------------------------------------------------


def test_main_loop_skips_classification_if_no_sender_email(monkeypatch):
    """When fetch_item returns item with no sender, classifier.classify is NOT called.

    log_entry keeps tier_guess='LOW', weight=0, reason_breakdown={}.
    """
    mod = _import_daemon()

    shutdown = threading.Event()

    fake_event = _make_fake_event()
    mock_ews = MagicMock()

    def _fake_poll(since=None):
        yield fake_event
        shutdown.set()

    mock_ews.poll_inbox.side_effect = _fake_poll

    # Item has no sender at all
    fake_item = MagicMock()
    fake_item.sender = None
    fake_item.subject = "Some subject"
    mock_ews.fetch_item.return_value = fake_item

    written: list[dict] = []
    mock_clf = _make_mock_classifier(tier="HIGH_LIKELY", weight=99)

    mod._main_loop(
        shutdown_event=shutdown,
        ews=mock_ews,
        write_log_fn=lambda fn, e: written.append(e),
        fetch_item_fn=mock_ews.fetch_item,
        get_cursor_fn=lambda: datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc),
        set_cursor_fn=lambda dt: None,
        rules_engine=_make_mock_rules_engine(),
        classifier=mock_clf,
    )

    assert len(written) == 1
    entry = written[0]
    # classify must NOT have been called
    mock_clf.classify.assert_not_called()
    # defaults preserved
    assert entry["tier_guess"] == "LOW"
    assert entry["weight"] == 0
    assert entry["reason_breakdown"] == {}


# ---------------------------------------------------------------------------
# Test 16: classification exception -> warning logged, defaults kept, no crash
# ---------------------------------------------------------------------------


def test_main_loop_classification_failure_logs_warning_keeps_defaults(monkeypatch, caplog):
    """When classifier.classify raises, log_entry keeps defaults (LOW/0/{}).

    The loop continues (no crash), and a WARNING is emitted.
    """
    import logging

    mod = _import_daemon()

    shutdown = threading.Event()

    fake_event = _make_fake_event()
    mock_ews = MagicMock()

    def _fake_poll(since=None):
        yield fake_event
        shutdown.set()

    mock_ews.poll_inbox.side_effect = _fake_poll

    fake_item = MagicMock()
    fake_item.sender = MagicMock()
    fake_item.sender.email_address = "sender@example.com"
    fake_item.subject = "Hello"
    mock_ews.fetch_item.return_value = fake_item

    written: list[dict] = []

    mock_clf = MagicMock()
    mock_clf.classify.side_effect = RuntimeError("classifier internal error")

    with caplog.at_level(logging.WARNING, logger="inbox_pulse.daemon"):
        mod._main_loop(
            shutdown_event=shutdown,
            ews=mock_ews,
            write_log_fn=lambda fn, e: written.append(e),
            fetch_item_fn=mock_ews.fetch_item,
            get_cursor_fn=lambda: datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc),
            set_cursor_fn=lambda dt: None,
            rules_engine=_make_mock_rules_engine(),
            classifier=mock_clf,
        )

    # Event still logged
    assert len(written) == 1
    entry = written[0]
    # Defaults kept after classify() raised
    assert entry["tier_guess"] == "LOW"
    assert entry["weight"] == 0
    assert entry["reason_breakdown"] == {}
    # A WARNING was logged
    assert any("Classification failed" in r.message for r in caplog.records), (
        f"Expected 'Classification failed' warning, got: {[r.message for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# Test 17: rules_engine.reload_if_changed True -> logger.info logged
# ---------------------------------------------------------------------------


def test_main_loop_reloads_rules_yaml_when_changed(monkeypatch, caplog):
    """When rules_engine.reload_if_changed returns True, a reload INFO is logged."""
    import logging

    mod = _import_daemon()

    shutdown = threading.Event()
    mock_ews = MagicMock()

    def _fake_poll(since=None):
        shutdown.set()
        return iter([])

    mock_ews.poll_inbox.side_effect = _fake_poll

    mock_re = _make_mock_rules_engine(reload_return=True)

    with caplog.at_level(logging.INFO, logger="inbox_pulse.daemon"):
        mod._main_loop(
            shutdown_event=shutdown,
            ews=mock_ews,
            write_log_fn=MagicMock(),
            fetch_item_fn=MagicMock(),
            get_cursor_fn=lambda: datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc),
            set_cursor_fn=lambda dt: None,
            rules_engine=mock_re,
            classifier=_make_mock_classifier(),
        )

    assert any("Rules YAML reloaded" in r.message for r in caplog.records), (
        f"Expected reload log message, got: {[r.message for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# Test 18: rules_engine.reload_if_changed False -> no reload log
# ---------------------------------------------------------------------------


def test_main_loop_does_not_log_reload_when_unchanged(monkeypatch, caplog):
    """When rules_engine.reload_if_changed returns False, no reload message is logged."""
    import logging

    mod = _import_daemon()

    shutdown = threading.Event()
    mock_ews = MagicMock()

    def _fake_poll(since=None):
        shutdown.set()
        return iter([])

    mock_ews.poll_inbox.side_effect = _fake_poll

    mock_re = _make_mock_rules_engine(reload_return=False)

    with caplog.at_level(logging.INFO, logger="inbox_pulse.daemon"):
        mod._main_loop(
            shutdown_event=shutdown,
            ews=mock_ews,
            write_log_fn=MagicMock(),
            fetch_item_fn=MagicMock(),
            get_cursor_fn=lambda: datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc),
            set_cursor_fn=lambda dt: None,
            rules_engine=mock_re,
            classifier=_make_mock_classifier(),
        )

    assert not any("Rules YAML reloaded" in r.message for r in caplog.records), (
        "Unexpected reload log emitted when reload_if_changed returned False"
    )


# ---------------------------------------------------------------------------
# Test 19: backward compat -- _main_loop still works with no rules_engine/classifier
# ---------------------------------------------------------------------------


def test_main_loop_no_rules_engine_no_classifier_still_works(monkeypatch):
    """_main_loop with rules_engine=None + classifier=None runs without error.

    Entries get mode='shadow', tier_guess='LOW' (defaults), no crash.
    This ensures backward compat with any test that calls _main_loop without
    the new kwargs.
    """
    mod = _import_daemon()

    shutdown = threading.Event()
    fake_event = _make_fake_event()
    mock_ews = MagicMock()

    def _fake_poll(since=None):
        yield fake_event
        shutdown.set()

    mock_ews.poll_inbox.side_effect = _fake_poll

    fake_item = MagicMock()
    fake_item.sender = MagicMock()
    fake_item.sender.email_address = "someone@example.com"
    fake_item.subject = "Test"
    mock_ews.fetch_item.return_value = fake_item

    written: list[dict] = []

    mod._main_loop(
        shutdown_event=shutdown,
        ews=mock_ews,
        write_log_fn=lambda fn, e: written.append(e),
        fetch_item_fn=mock_ews.fetch_item,
        get_cursor_fn=lambda: datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc),
        set_cursor_fn=lambda dt: None,
        # rules_engine and classifier intentionally omitted (defaults to None)
    )

    assert len(written) == 1
    entry = written[0]
    assert entry["mode"] == "shadow"
    assert entry["tier_guess"] == "LOW"
    assert entry["weight"] == 0


# ---------------------------------------------------------------------------
# Tests for TL+To/CC recipient extraction and sovereignty (3 new tests)
# ---------------------------------------------------------------------------


def test_main_loop_extracts_recipients_for_classifier(monkeypatch):
    """Loop extracts to_recipients and cc_recipients and passes them to classifier.classify."""
    monkeypatch.setenv("EXCHANGE_EMAIL", "ceo@31c.io")
    mod = _import_daemon()

    shutdown = threading.Event()
    fake_event = _make_fake_event()
    mock_ews = MagicMock()

    def _fake_poll(since=None):
        yield fake_event
        shutdown.set()

    mock_ews.poll_inbox.side_effect = _fake_poll

    # Build a mock item with to_recipients and cc_recipients
    fake_item = MagicMock()
    fake_item.sender = MagicMock()
    fake_item.sender.email_address = "alice@31c.io"
    fake_item.subject = "Important update"

    to_r = MagicMock()
    to_r.email_address = "alice@example.com"
    cc_r = MagicMock()
    cc_r.email_address = "ceo@31c.io"

    fake_item.to_recipients = [to_r]
    fake_item.cc_recipients = [cc_r]
    mock_ews.fetch_item.return_value = fake_item

    captured_kwargs: list[dict] = []

    mock_clf = MagicMock()
    mock_clf.classify.side_effect = lambda **kwargs: (
        captured_kwargs.append(kwargs) or {
            "tier_guess": "LOW",
            "weight": 0,
            "reason_breakdown": {},
        }
    )

    mod._main_loop(
        shutdown_event=shutdown,
        ews=mock_ews,
        write_log_fn=MagicMock(),
        fetch_item_fn=mock_ews.fetch_item,
        get_cursor_fn=lambda: datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc),
        set_cursor_fn=lambda dt: None,
        rules_engine=_make_mock_rules_engine(),
        classifier=mock_clf,
    )

    assert len(captured_kwargs) == 1, "classifier.classify was not called"
    kwargs = captured_kwargs[0]
    assert kwargs["recipients_to"] == ["alice@example.com"]
    assert kwargs["recipients_cc"] == ["ceo@31c.io"]


def test_main_loop_does_not_log_full_recipients_in_jsonl(monkeypatch):
    """Sovereignty audit: full recipient addresses must never appear in the JSONL entry."""
    monkeypatch.setenv("EXCHANGE_EMAIL", "ceo@31c.io")
    mod = _import_daemon()

    shutdown = threading.Event()
    fake_event = _make_fake_event()
    mock_ews = MagicMock()

    def _fake_poll(since=None):
        yield fake_event
        shutdown.set()

    mock_ews.poll_inbox.side_effect = _fake_poll

    fake_item = MagicMock()
    fake_item.sender = MagicMock()
    fake_item.sender.email_address = "alice@31c.io"
    fake_item.subject = "Thread"

    to_r = MagicMock()
    to_r.email_address = "alice@example.com"
    cc_r = MagicMock()
    cc_r.email_address = "ceo@31c.io"

    fake_item.to_recipients = [to_r]
    fake_item.cc_recipients = [cc_r]
    mock_ews.fetch_item.return_value = fake_item

    written: list[dict] = []
    mock_clf = _make_mock_classifier(tier="LOW", weight=0)

    mod._main_loop(
        shutdown_event=shutdown,
        ews=mock_ews,
        write_log_fn=lambda fn, e: written.append(e),
        fetch_item_fn=mock_ews.fetch_item,
        get_cursor_fn=lambda: datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc),
        set_cursor_fn=lambda dt: None,
        rules_engine=_make_mock_rules_engine(),
        classifier=mock_clf,
    )

    assert len(written) == 1
    serialized = json.dumps(written[0])
    # Neither recipient address may appear in the serialized JSONL entry
    assert "alice@example.com" not in serialized, "To recipient address leaked into JSONL"
    assert "ceo@31c.io" not in serialized, "CC recipient address leaked into JSONL"
