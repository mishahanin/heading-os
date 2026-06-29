"""Tests for scripts/inbox_pulse/daemon.py.

All tests mock EWSConnection and state helpers -- no real Exchange connection
is made and no real state files are written to the workspace.

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

    # Patch EWSConnection so no real network call occurs
    mock_ews = MagicMock()
    mock_ews.account = MagicMock()  # accessing .account triggers connect

    with patch("scripts.inbox_pulse.daemon.EWSConnection", return_value=mock_ews):
        mod = _import_daemon()
        result = mod.health_check()

    assert result == 0


# ---------------------------------------------------------------------------
# Test 2: --check fails when env var is missing
# ---------------------------------------------------------------------------


def test_check_mode_fails_on_missing_env(monkeypatch):
    """health_check() returns 1 and prints diagnostic when EXCHANGE_EMAIL absent."""
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

    assert mod._domain_of("victor@northgate.com") == "northgate.com"
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
    fake_item.sender.email_address = "alice@northgate.com"
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
        assert entry["sender_domain"] == "northgate.com"
        assert "alice@northgate.com" not in json.dumps(entry), "Full sender address leaked"

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


def test_heartbeat_thread_writes_periodically(tmp_path, monkeypatch):
    """Heartbeat thread calls write_heartbeat at least once within a short window."""
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
