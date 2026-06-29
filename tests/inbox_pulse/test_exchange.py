"""Tests for scripts/inbox_pulse/exchange.py.

All tests mock exchangelib.Account -- no real Exchange connection is made.
Real connection testing occurs in Phase 1-E (local smoke test against 31C Exchange).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Module import helper
# ---------------------------------------------------------------------------


def _import_exchange():
    """Return the exchange module (importlib.reload ensures a clean state)."""
    import importlib
    import scripts.inbox_pulse.exchange as mod
    importlib.reload(mod)
    return mod


# ---------------------------------------------------------------------------
# Test 1: init loads credentials from environment
# ---------------------------------------------------------------------------


def test_init_loads_from_env(monkeypatch):
    """EWSConnection() reads credentials from env vars without raising."""
    monkeypatch.setenv("EXCHANGE_EMAIL", "ceo@31c.io")
    monkeypatch.setenv("EXCHANGE_PASSWORD", "secret")
    monkeypatch.setenv("EXCHANGE_SERVER", "mail.31c.io")

    mod = _import_exchange()
    conn = mod.EWSConnection()

    assert conn._email == "ceo@31c.io"
    assert conn._password == "secret"  # pragma: allowlist secret
    assert conn._server == "mail.31c.io"
    assert conn._account is None  # lazy -- not yet connected


# ---------------------------------------------------------------------------
# Test 2: ValueError on missing credentials (raised at first .account access)
# ---------------------------------------------------------------------------


def test_init_raises_on_missing_credentials(monkeypatch):
    """Missing env vars cause ValueError when .account is first accessed."""
    monkeypatch.delenv("EXCHANGE_EMAIL", raising=False)
    monkeypatch.delenv("EXCHANGE_PASSWORD", raising=False)
    monkeypatch.delenv("EXCHANGE_SERVER", raising=False)

    mod = _import_exchange()
    conn = mod.EWSConnection()  # no error yet (lazy)

    with pytest.raises(ValueError, match="Missing Exchange credentials"):
        _ = conn.account  # triggers _connect()


# ---------------------------------------------------------------------------
# Test 3: account property creates the Account lazily and caches it
# ---------------------------------------------------------------------------


def test_account_property_creates_lazily(monkeypatch):
    """Account constructor is called on first .account access, then cached."""
    monkeypatch.setenv("EXCHANGE_EMAIL", "ceo@31c.io")
    monkeypatch.setenv("EXCHANGE_PASSWORD", "secret")
    monkeypatch.setenv("EXCHANGE_SERVER", "mail.31c.io")

    mock_account_instance = MagicMock()

    mod = _import_exchange()
    conn = mod.EWSConnection()

    with patch("exchangelib.Account", return_value=mock_account_instance) as mock_cls, \
         patch("exchangelib.Credentials"), \
         patch("exchangelib.Configuration"):

        # Constructor must NOT have been called yet
        mock_cls.assert_not_called()

        # First access -- should create the Account
        result1 = conn.account
        mock_cls.assert_called_once()

        # Second access -- must return the same instance, no new call
        result2 = conn.account
        mock_cls.assert_called_once()  # still only once

    assert result1 is mock_account_instance
    assert result2 is mock_account_instance


# ---------------------------------------------------------------------------
# Test 4: build_owa_link URL-encodes the item ID
# ---------------------------------------------------------------------------


def test_build_owa_link_url_encodes_item_id(monkeypatch):
    """build_owa_link() produces a correct OWA URL with the item ID URL-encoded."""
    monkeypatch.setenv("EXCHANGE_SERVER", "mail.31c.io")
    monkeypatch.setenv("EXCHANGE_EMAIL", "ceo@31c.io")
    monkeypatch.setenv("EXCHANGE_PASSWORD", "secret")

    mod = _import_exchange()
    conn = mod.EWSConnection()

    raw_id = "AAMkAGI3+/="
    url = conn.build_owa_link(raw_id)

    assert "mail.31c.io" in url
    # '+' -> %2B, '/' -> %2F, '=' -> %3D
    assert "ItemID=AAMkAGI3%2B%2F%3D" in url
    assert "exvsurl=1" in url
    assert "viewmodel=ReadMessageItem" in url


# ---------------------------------------------------------------------------
# Test 5: disconnect is idempotent
# ---------------------------------------------------------------------------


def test_disconnect_is_idempotent(monkeypatch):
    """disconnect() can be called multiple times without error."""
    monkeypatch.setenv("EXCHANGE_EMAIL", "ceo@31c.io")
    monkeypatch.setenv("EXCHANGE_PASSWORD", "secret")
    monkeypatch.setenv("EXCHANGE_SERVER", "mail.31c.io")

    mod = _import_exchange()
    conn = mod.EWSConnection()

    # Not connected -- first call is a no-op
    conn.disconnect()
    assert conn._account is None

    # Set a mock account and disconnect again
    conn._account = MagicMock()
    conn.disconnect()
    assert conn._account is None

    # Third call -- still no error
    conn.disconnect()
    assert conn._account is None


# ---------------------------------------------------------------------------
# Test 6: poll_inbox filters by since
# ---------------------------------------------------------------------------


def test_poll_inbox_filters_by_since(monkeypatch):
    """poll_inbox(since=...) calls folder.filter(datetime_received__gt=since)."""
    monkeypatch.setenv("EXCHANGE_EMAIL", "ceo@31c.io")
    monkeypatch.setenv("EXCHANGE_PASSWORD", "secret")
    monkeypatch.setenv("EXCHANGE_SERVER", "mail.31c.io")

    since_dt = datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc)

    def _make_fake_item(idx):
        item = MagicMock()
        item.id = f"item-{idx}"
        item.parent_folder_id = "inbox-folder"
        item.datetime_received = datetime(2026, 5, 26, 10, idx, 0, tzinfo=timezone.utc)
        return item

    fake_items = [_make_fake_item(i) for i in range(1, 4)]  # 3 items

    mock_account = MagicMock()
    # filter().order_by()[:200] chain returns fake_items
    mock_account.inbox.filter.return_value.order_by.return_value.__getitem__.return_value = fake_items

    mod = _import_exchange()
    conn = mod.EWSConnection()
    conn._account = mock_account

    results = list(conn.poll_inbox(since=since_dt))

    # filter called with the right kwarg
    mock_account.inbox.filter.assert_called_once_with(datetime_received__gt=since_dt)
    assert len(results) == 3


# ---------------------------------------------------------------------------
# Test 7: poll_inbox yields dicts with correct keys
# ---------------------------------------------------------------------------


def test_poll_inbox_yields_dicts_with_correct_keys(monkeypatch):
    """Each yielded dict has event_type='NewMail', timestamp, item_id, parent_folder_id, datetime_received."""
    monkeypatch.setenv("EXCHANGE_EMAIL", "ceo@31c.io")
    monkeypatch.setenv("EXCHANGE_PASSWORD", "secret")
    monkeypatch.setenv("EXCHANGE_SERVER", "mail.31c.io")

    since_dt = datetime(2026, 5, 26, 9, 0, 0, tzinfo=timezone.utc)
    item = MagicMock()
    item.id = "AAAA-0001"
    item.parent_folder_id = "INBOX-FOLDER-ID"
    item.datetime_received = datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc)

    mock_account = MagicMock()
    mock_account.inbox.filter.return_value.order_by.return_value.__getitem__.return_value = [item]

    mod = _import_exchange()
    conn = mod.EWSConnection()
    conn._account = mock_account

    results = list(conn.poll_inbox(since=since_dt))

    assert len(results) == 1
    ev = results[0]
    assert set(ev.keys()) == {"event_type", "timestamp", "item_id", "parent_folder_id", "datetime_received"}
    assert ev["event_type"] == "NewMail"
    # timestamp must parse as ISO-8601 with timezone
    parsed = datetime.fromisoformat(ev["timestamp"])
    assert parsed.tzinfo is not None
    assert ev["item_id"] == "AAAA-0001"
    assert ev["parent_folder_id"] == "INBOX-FOLDER-ID"
    assert ev["datetime_received"] is not None


# ---------------------------------------------------------------------------
# Test 8: poll_inbox without since uses all() not filter()
# ---------------------------------------------------------------------------


def test_poll_inbox_without_since_returns_recent_max_items(monkeypatch):
    """When since=None, calls folder.all() and sorts by -datetime_received."""
    monkeypatch.setenv("EXCHANGE_EMAIL", "ceo@31c.io")
    monkeypatch.setenv("EXCHANGE_PASSWORD", "secret")
    monkeypatch.setenv("EXCHANGE_SERVER", "mail.31c.io")

    def _make_item(idx):
        item = MagicMock()
        item.id = f"item-{idx}"
        item.parent_folder_id = "inbox-folder"
        item.datetime_received = datetime(2026, 5, 26, idx, 0, 0, tzinfo=timezone.utc)
        return item

    # Two items returned newest-first by -datetime_received sort
    fake_items = [_make_item(2), _make_item(1)]

    mock_account = MagicMock()
    mock_account.inbox.all.return_value.order_by.return_value.__getitem__.return_value = fake_items

    mod = _import_exchange()
    conn = mod.EWSConnection()
    conn._account = mock_account

    results = list(conn.poll_inbox(since=None))

    mock_account.inbox.all.assert_called_once()
    mock_account.inbox.filter.assert_not_called()
    # order_by called with "-datetime_received"
    mock_account.inbox.all.return_value.order_by.assert_called_once_with("-datetime_received")
    # Results reversed to oldest-first
    assert results[0]["item_id"] == "item-1"
    assert results[1]["item_id"] == "item-2"


# ---------------------------------------------------------------------------
# Test 9: poll_inbox respects max_items cap
# ---------------------------------------------------------------------------


def test_poll_inbox_respects_max_items_cap(monkeypatch):
    """max_items is passed as the slice limit to the queryset."""
    monkeypatch.setenv("EXCHANGE_EMAIL", "ceo@31c.io")
    monkeypatch.setenv("EXCHANGE_PASSWORD", "secret")
    monkeypatch.setenv("EXCHANGE_SERVER", "mail.31c.io")

    since_dt = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)

    mock_account = MagicMock()
    mock_account.inbox.filter.return_value.order_by.return_value.__getitem__.return_value = []

    mod = _import_exchange()
    conn = mod.EWSConnection()
    conn._account = mock_account

    list(conn.poll_inbox(since=since_dt, max_items=10))

    # Verify the slice [:10] was applied
    mock_account.inbox.filter.return_value.order_by.return_value.__getitem__.assert_called_once_with(
        slice(None, 10)
    )


# ---------------------------------------------------------------------------
# Test 10: poll_inbox yields oldest-first when since is provided
# ---------------------------------------------------------------------------


def test_poll_inbox_yields_oldest_first_when_since_provided(monkeypatch):
    """When since is provided, items are yielded in chronological (oldest-first) order."""
    monkeypatch.setenv("EXCHANGE_EMAIL", "ceo@31c.io")
    monkeypatch.setenv("EXCHANGE_PASSWORD", "secret")
    monkeypatch.setenv("EXCHANGE_SERVER", "mail.31c.io")

    since_dt = datetime(2026, 5, 26, 9, 0, 0, tzinfo=timezone.utc)

    def _make_item(hour):
        item = MagicMock()
        item.id = f"item-hour-{hour}"
        item.parent_folder_id = "inbox-folder"
        item.datetime_received = datetime(2026, 5, 26, hour, 0, 0, tzinfo=timezone.utc)
        return item

    # Exchange returns oldest-first when sorted by datetime_received (ascending)
    fake_items = [_make_item(10), _make_item(11), _make_item(12)]

    mock_account = MagicMock()
    mock_account.inbox.filter.return_value.order_by.return_value.__getitem__.return_value = fake_items

    mod = _import_exchange()
    conn = mod.EWSConnection()
    conn._account = mock_account

    results = list(conn.poll_inbox(since=since_dt))

    # order_by must use ascending datetime_received (no leading '-')
    mock_account.inbox.filter.return_value.order_by.assert_called_once_with("datetime_received")
    # Oldest first
    assert results[0]["item_id"] == "item-hour-10"
    assert results[1]["item_id"] == "item-hour-11"
    assert results[2]["item_id"] == "item-hour-12"
