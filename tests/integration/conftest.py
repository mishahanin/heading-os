"""Shared fixtures for sentinel integration tests.

Design notes:
- Full mocks for Exchange/Telethon/Anthropic (see plan 2026-04-19-sentinel-integration-tests.md).
- Synthetic fixtures only; no real data per CEO decision 2026-04-19.
- File I/O redirected to tmp_path (pytest built-in) to avoid touching real state.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure scripts/ is importable
import sys
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

# sentinel.py has module-level side effects on Windows:
# lines 80-82 replace sys.stdout/stderr with TextIOWrapper, which destroys
# pytest's capture layer. Work around by pretending to be non-Windows at
# import time (skipping the branch), then restoring platform.
# Subsequent imports get the cached module without re-running top-level code.
#
# The fake is applied ONLY on Windows, because it is not free elsewhere. While
# sys.platform is a lie, the first sysconfig init in the process computes its
# data-module name as _sysconfigdata_{abiflags}_{sys.platform}_{multiarch} --
# on macOS that resolves to the non-existent _sysconfigdata__linux_darwin, and
# sentinel.py's own `from zoneinfo import ZoneInfo` triggers exactly that init.
# The result was ModuleNotFoundError at conftest import, i.e. every test under
# tests/integration/ failing to collect on macOS. Linux needs no fake at all
# (the branch is already skipped), so Windows is the only platform that does.
_orig_platform = sys.platform
if _orig_platform.startswith("win"):
    sys.platform = "linux"
try:
    import scripts.sentinel  # noqa: F401 - triggers module init
finally:
    sys.platform = _orig_platform


# ---------------------------------------------------------------------------
# Filesystem isolation
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_state_dir(tmp_path: Path) -> Path:
    """Unique runtime directory per test (replaces .sentinel/)."""
    d = tmp_path / "sentinel_runtime"
    d.mkdir()
    return d


@pytest.fixture
def tmp_session_dir(tmp_path: Path) -> Path:
    """Unique telegram session directory per test."""
    d = tmp_path / "telegram_session"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Fixture data loaders
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict | list:
    with open(FIXTURES_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def fixture_emails() -> list[SimpleNamespace]:
    """Synthetic Exchange email items (5 items, mix of urgent/normal)."""
    raw = _load_fixture("sample_emails.json")
    return [SimpleNamespace(**item) for item in raw]


@pytest.fixture
def fixture_tg_messages() -> list[SimpleNamespace]:
    """Synthetic Telegram messages (5 items)."""
    raw = _load_fixture("sample_tg_messages.json")
    return [SimpleNamespace(**item) for item in raw]


@pytest.fixture
def fixture_meeting_invites() -> list[SimpleNamespace]:
    """Synthetic meeting invites (3 items, including one with bad datetime)."""
    raw = _load_fixture("sample_meeting_invites.json")
    return [SimpleNamespace(**item) for item in raw]


@pytest.fixture
def fixture_analyzer_responses() -> list[MagicMock]:
    """Synthetic Anthropic Message-shaped responses."""
    raw = _load_fixture("sample_analyzer_responses.json")
    responses = []
    for item in raw:
        msg = MagicMock()
        msg.content = [MagicMock(text=item["text"], type="text")]
        msg.stop_reason = item.get("stop_reason", "end_turn")
        responses.append(msg)
    return responses


# ---------------------------------------------------------------------------
# SentinelConfig mock
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_config() -> SimpleNamespace:
    """Minimal SentinelConfig-shaped object for tests."""
    from zoneinfo import ZoneInfo
    return SimpleNamespace(
        check_interval=900,
        urgency_threshold=7,
        timezone=ZoneInfo("Etc/GMT-4"),
        log_level="DEBUG",
        email={
            "enabled": True,
            "account": "test@example.com",
            "urgent_senders": ["vip@example.com"],
            "ignore_patterns": [],
        },
        telegram={
            "enabled": True,
            "api_id": 12345,
            "api_hash": "test_hash",
            "monitored_chats": [],
            "notification_channel": -1001234567890,
        },
        digest={"enabled": False},
        notification={"channel_id": -1001234567890},
        llm={"model": "claude-sonnet-4-6", "max_tokens": 256},
        calendar={
            "enabled": False,
            "auto_accept_domains": ["trusted.example.com"],
            "daily_themes": {0: "Tribe", 1: "Product"},
        },
    )


# ---------------------------------------------------------------------------
# External service mocks
# ---------------------------------------------------------------------------

async def _async_gen(items):
    """Yield items as an async generator (for Telethon iter_messages)."""
    for item in items:
        yield item


@pytest.fixture
def mock_exchange_account(fixture_emails):
    """MagicMock of exchangelib.Account with inbox returning fixture emails."""
    account = MagicMock()
    inbox_filter = MagicMock()
    inbox_filter.order_by.return_value = fixture_emails
    inbox_filter.__iter__ = lambda self: iter(fixture_emails)
    account.inbox.filter.return_value = inbox_filter
    account.inbox.all.return_value = fixture_emails
    return account


@pytest.fixture
def mock_telegram_client(fixture_tg_messages):
    """AsyncMock of telethon.TelegramClient with iter_messages as async gen."""
    client = AsyncMock()
    client.iter_messages = lambda *a, **kw: _async_gen(fixture_tg_messages)
    client.is_connected = MagicMock(return_value=True)
    client.disconnect = AsyncMock()
    client.connect = AsyncMock()
    client.get_me = AsyncMock(return_value=SimpleNamespace(
        first_name="TestUser", username="testuser"
    ))
    # session.save is called in sentinel; provide a dummy
    client.session = MagicMock()
    client.session._conn = MagicMock()
    return client


@pytest.fixture
def mock_anthropic_client(fixture_analyzer_responses):
    """MagicMock of anthropic.Anthropic client."""
    client = MagicMock()
    client.messages.create.side_effect = fixture_analyzer_responses
    return client


# ---------------------------------------------------------------------------
# Logger mock
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_logger() -> MagicMock:
    """MagicMock spec'd to logging.Logger for assert-on-call-args pattern."""
    return MagicMock(spec=logging.Logger)


# ---------------------------------------------------------------------------
# StateManager fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def state_manager(tmp_state_dir):
    """Fresh StateManager pointed at an empty tmp dir."""
    from scripts.sentinel import StateManager
    return StateManager(tmp_state_dir / "state.json")
