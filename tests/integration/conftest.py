"""Shared fixtures for sentinel integration tests.

Design notes:
- One shared Exchange account mock, built here from `fixtures/sample_emails.json`.
  Telethon and Anthropic are mocked INSIDE the tests that need them, per test,
  because each one wants a different failure injected. Until 2026-08-27 this file
  also carried `mock_telegram_client` and `mock_anthropic_client`; no test had
  ever requested either, and neither had the meeting-invite corpus behind them.
  Unread scaffolding reads as active protection, so it came out.
- Synthetic fixtures only; no real data. Operator decision 2026-04-19, and since
  the engine went public it is the engine/data separation as well.
- The DATA ROOT is pinned to the test's tmp_path for every test in this
  directory, by `_pin_the_data_root` below. Everything else about filesystem
  isolation is OPT-IN: `tmp_state_dir` and `tmp_session_dir` are ordinary
  fixtures a test has to ask for, and nothing redirects a test that does not.

  This bullet read "File I/O redirected to tmp_path (pytest built-in) to avoid
  touching real state" until 2026-09-01, describing a blanket redirection that
  did not exist. Measured that day: with the fixture below removed, every test
  in this directory resolves
  `/home/administrator/ai/claude-workspaces/.heading-os-data`.

TWO THINGS THIS FILE DOES NOT CONTAIN, stated because their absence reads as
their presence.

- Importing `scripts.sentinel` (below) runs its module body, and that body ends
  with `load_env(WORKSPACE_ROOT)`. On the operator's machine that puts 70 real
  credential names into `os.environ` for the rest of the pytest session, and no
  fixture here can undo it: the same happens the moment any test anywhere
  imports the module. `tests/conftest.py` blanks the bot token and every
  `*_TELEGRAM_TARGET` before each test; nothing blanks the other 68.
- Every test collected here is auto-marked `integration` by
  `tests/conftest.py::pytest_collection_modifyitems`, and that marker stands
  BOTH the egress guard and the model-resolution pin down. A test in this
  directory can reach the network and can resolve a model id over it. That is
  the deliberate meaning of the marker, not an oversight, but it means a skip
  or a silent degradation hides more here than anywhere else in the suite.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Ensure scripts/ is importable
import sys
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

# sentinel.py has module-level side effects on Windows: the top-level
# `if sys.platform == "win32":` guard replaces sys.stdout/stderr with a
# TextIOWrapper, which destroys pytest's capture layer. Work around by
# pretending to be non-Windows at import time (skipping the branch), then
# restoring platform. No line numbers here on purpose - this comment said
# "lines 80-82" until 2026-08-27, by which time the guard had moved to 97-99.
# Subsequent imports get the cached module without re-running top-level code.
_orig_platform = sys.platform
sys.platform = "linux"
try:
    import scripts.sentinel  # noqa: F401 - triggers module init
finally:
    sys.platform = _orig_platform


# ---------------------------------------------------------------------------
# Filesystem isolation
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _pin_the_data_root(tmp_path: Path, monkeypatch) -> None:
    """Every test in this directory resolves the DATA root under its tmp_path.

    Copied from `tests/bridge/conftest.py`, which has pinned this autouse since
    the bridge sources grew a `get_data_root()` fallback. This directory had no
    pin at all while its docstring claimed file I/O was redirected, and it is
    the directory that needs one most: four of its seven test files already
    spawned child processes, a child inherits the environment, and the
    in-process overlay write guard in `scripts/utils/overlay_write_guard.py`
    cannot see a child at all. The session-finish report in `tests/conftest.py`
    counted 22 children from this directory running "with the live data root
    reachable".

    The pin is an ENVIRONMENT variable, not a patched attribute, because that
    is what crosses the process boundary. `get_data_root()` reads
    `HEADING_OS_DATA` on every call, so both halves follow it.

    A test that genuinely needs the operator's real overlay has to say so in its
    own body, where it is visible. Nothing here does, and the one live-fleet
    read that used to live in `test_workspace_helpers_per_exec.py` is covered
    outside this conftest by
    `tests/test_per_exec_contacts_dir.py::test_the_live_fleet_is_visible_through_the_helper`.

    Pinned by `test_workspace_helpers_per_exec.py::
    test_this_directory_resolves_a_scratch_data_root`, in-process and in a
    child, so removing this fixture fails rather than quietly re-arming the
    live overlay. That test also records the one invocation shape under which
    pytest 9.1.1 drops this fixture without saying so, which is worth reading
    before believing a failure from it.
    """
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))


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
