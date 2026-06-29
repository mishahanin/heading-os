#!/usr/bin/env python3
"""SEC-013: Verify analyze_batch separates JSONDecodeError from Exception."""

import pytest
from tests.security.conftest import read_file_content


def test_separate_exception_handlers_in_analyze_batch(scripts_dir):
    """analyze_batch must not catch JSONDecodeError and Exception in the same clause."""
    content = read_file_content(scripts_dir / "sentinel.py")
    # The problematic pattern is: except (json.JSONDecodeError, Exception)
    assert "(json.JSONDecodeError, Exception)" not in content, (
        "analyze_batch must separate json.JSONDecodeError and Exception into "
        "distinct except clauses. JSONDecodeError is a subclass of Exception, "
        "so catching both in the same clause is redundant and prevents "
        "differentiated error handling."
    )
