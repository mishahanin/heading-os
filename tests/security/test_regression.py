#!/usr/bin/env python3
"""Permanent security regression suite.

Imports all SEC finding tests. This file runs on every test execution
to prevent regressions. Tests are NEVER removed from this suite.
"""

# SEC-001: HTML injection in send-email.py
from tests.security.test_SEC_001_email_html_injection import *  # noqa: F401, F403

# SEC-002: Silent error swallowing in session-start hook
from tests.security.test_SEC_002_hook_silent_failure import *  # noqa: F401, F403

# SEC-003: Incomplete .gitignore coverage
from tests.security.test_SEC_003_gitignore_coverage import *  # noqa: F401, F403

# SEC-004: Missing credential patterns in scanners
from tests.security.test_SEC_004_credential_patterns import *  # noqa: F401, F403

# SEC-005: Missing Trojan Source chars in sanitizer
from tests.security.test_SEC_005_sanitizer_trojan_source import *  # noqa: F401, F403

# SEC-006: OAuth directory permissions
from tests.security.test_SEC_006_oauth_dir_permissions import *  # noqa: F401, F403

# SEC-007: Post-write-sanitize timeout logging
from tests.security.test_SEC_007_hook_timeout_logging import *  # noqa: F401, F403

# SEC-010: Sentinel atomic state writes
from tests.security.test_SEC_010_sentinel_atomic_state import *  # noqa: F401, F403

# SEC-011: Sentinel signal handler order
from tests.security.test_SEC_011_sentinel_signal_handler import *  # noqa: F401, F403

# SEC-012: Sentinel graceful shutdown
from tests.security.test_SEC_012_sentinel_graceful_shutdown import *  # noqa: F401, F403

# SEC-013: Sentinel batch error handling
from tests.security.test_SEC_013_sentinel_batch_error import *  # noqa: F401, F403

# SEC-014: Sentinel per-chat timeout
from tests.security.test_SEC_014_sentinel_chat_timeout import *  # noqa: F401, F403

# SEC-015: Sentinel state save in finally
from tests.security.test_SEC_015_sentinel_state_finally import *  # noqa: F401, F403

# SEC-016: Sentinel PID file locking
from tests.security.test_SEC_016_sentinel_pid_lock import *  # noqa: F401, F403
