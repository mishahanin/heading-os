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
    with patch.dict(os.environ, env, clear=True), patch("scripts.utils.api.load_env"):
        with pytest.raises(ValueError):
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
