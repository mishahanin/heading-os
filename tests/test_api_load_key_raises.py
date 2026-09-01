"""Regression: load_api_key() must raise ValueError (not sys.exit) when a required key is absent (F-L7)."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.api import load_api_key

ABSENT_KEY = "DEFINITELY_ABSENT_TEST_KEY_XYZ_31C"


def test_missing_required_key_raises_value_error():
    """A missing required key raises ValueError with the key name in the message."""
    env = {k: v for k, v in os.environ.items() if k != ABSENT_KEY}
    # Patch load_env so the .env file cannot supply the key during the test.
    with patch.dict(os.environ, env, clear=True), patch("scripts.utils.api.load_env"):
        with pytest.raises(ValueError, match=ABSENT_KEY):
            load_api_key(ABSENT_KEY)


def test_missing_required_key_does_not_sys_exit():
    """A missing required key must raise ValueError, not SystemExit."""
    env = {k: v for k, v in os.environ.items() if k != ABSENT_KEY}
    with patch.dict(os.environ, env, clear=True), patch("scripts.utils.api.load_env"), pytest.raises(ValueError):
        load_api_key(ABSENT_KEY)
        # If sys.exit were called, pytest would surface SystemExit, not ValueError.


def test_missing_optional_key_returns_empty():
    """required=False preserves the soft-fail contract: returns '' on a missing key."""
    env = {k: v for k, v in os.environ.items() if k != ABSENT_KEY}
    with patch.dict(os.environ, env, clear=True), patch("scripts.utils.api.load_env"):
        assert load_api_key(ABSENT_KEY, required=False) == ""


def test_present_key_returns_value():
    """A key that exists in the environment returns its value (regression guard)."""
    with patch.dict(os.environ, {"TEST_PRESENT_KEY_31C": "test-value-abc"}):
        assert load_api_key("TEST_PRESENT_KEY_31C") == "test-value-abc"


def test_key_that_lives_only_in_dotenv_is_loaded_and_returned():
    """The .env fallback must actually run, and its value must be what comes back.

    Every other test in this file patches ``load_env`` to a no-op, so nothing
    here witnessed that ``load_api_key`` calls it at all. MEASURED 2026-09-01:
    deleting the ``load_env()`` line from ``scripts/utils/api.py`` left this
    whole file green. That is the credential path for the entire workspace:
    almost every key (ANTHROPIC_API_KEY, HUNTER_API_KEY, the Exchange
    credentials) lives ONLY in the gitignored ``.env``, never exported, so the
    deletion would report every one of them missing while four tests said the
    loader was fine.

    The stub RECORDS its invocation rather than discarding it, and the assertion
    is on the returned value, so neither "load_env was never called" nor
    "load_env ran but its result was ignored" can pass.
    """
    calls = []

    def _fake_load_env(*args, **kwargs):
        calls.append((args, kwargs))
        os.environ[ABSENT_KEY] = "value-from-dotenv"

    env = {k: v for k, v in os.environ.items() if k != ABSENT_KEY}
    with patch.dict(os.environ, env, clear=True), \
            patch("scripts.utils.api.load_env", _fake_load_env):
        assert load_api_key(ABSENT_KEY) == "value-from-dotenv"
    assert len(calls) == 1, f"load_env called {len(calls)} time(s), expected exactly 1"


def test_dotenv_is_not_consulted_when_the_key_is_already_exported():
    """An exported key short-circuits before .env is read.

    The negative case on the same line as the test above: without it, a
    load_api_key that read .env unconditionally would satisfy the fallback test
    while quietly paying a file read on every call and, worse, letting a stale
    .env value be re-published into the environment on a key the caller had
    deliberately overridden.
    """
    calls = []

    def _fake_load_env(*args, **kwargs):
        calls.append((args, kwargs))

    with patch.dict(os.environ, {"TEST_PRESENT_KEY_31C": "exported-wins"}), \
            patch("scripts.utils.api.load_env", _fake_load_env):
        assert load_api_key("TEST_PRESENT_KEY_31C") == "exported-wins"
    assert calls == [], "load_env was consulted even though the key was already exported"


def test_the_error_message_names_the_key_but_never_a_value():
    """A missing-key error must not carry any credential material.

    The engine repo is public and this message reaches stderr, logs, and CI
    output. Naming the variable is the useful half; echoing anything from the
    environment would be a secret in a public repo.
    """
    env = {k: v for k, v in os.environ.items() if k != ABSENT_KEY}
    env["UNRELATED_31C_ENV_ENTRY"] = "sk-do-not-echo-me"
    with patch.dict(os.environ, env, clear=True), \
            patch("scripts.utils.api.load_env"), \
            pytest.raises(ValueError) as excinfo:
        load_api_key(ABSENT_KEY)
    message = str(excinfo.value)
    assert ABSENT_KEY in message
    assert "sk-do-not-echo-me" not in message
    assert "UNRELATED_31C_ENV_ENTRY" not in message
