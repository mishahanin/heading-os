"""A Healthchecks deadman may only ever be pinged by the daemon it watches.

The check `steward-email-triage` reports whether the inbox-pulse poll loop on
the Steward host is alive. On 2026-08-17 that loop wedged and stayed wedged for
33 hours, and the check did NOT stay red: it flapped, going green for 15 minutes
at a time. The green came from this repository. `_main_loop` calls
`hc_ping("STEWARD_HC_EMAIL_TRIAGE")` at the end of a clean cycle, eleven tests in
tests/inbox_pulse/test_daemon.py drive that loop to completion, and any earlier
test that called the real `load_env()` leaves the production ping URL in
os.environ for the rest of the session. Measured 2026-08-18: one run of that one
file sent 14 real success pings to the live check.

A monitor that a test run can turn green is worse than no monitor, because it is
believed. These two tests hold the containment in tests/conftest.py.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import healthchecks  # noqa: E402
from scripts.utils.paths import load_env  # noqa: E402

_PING_HOST = "hc-ping.com"


def _live_ping_urls() -> list[str]:
    return sorted(k for k, v in os.environ.items() if _PING_HOST in v)


def test_load_env_cannot_reintroduce_a_production_ping_url():
    """The real load_env() must not hand a live deadman URL to the suite.

    This is the exact path that produced the false greens: load_env() uses
    os.environ.setdefault, so blanking the names at conftest import (rather than
    deleting them) is what makes the containment survive a later load_env().
    On a clone with no .env there is nothing to reintroduce and this passes
    trivially -- it is the operator workspace, where .env is real, that it
    guards.
    """
    load_env()
    leaked = _live_ping_urls()
    assert not leaked, (
        f"production Healthchecks ping URL(s) live in the test environment: {leaked}. "
        "A test that reaches hc_ping would mark a wedged daemon healthy."
    )


def test_ping_is_a_no_op_for_the_steward_checks():
    """With the URL blanked, ping() takes its documented missing-var path.

    No network call, returns False. Asserted through the public function rather
    than by reading os.environ, so the guard is verified at the seam that
    actually sends.
    """
    load_env()
    for env_key in ("STEWARD_HC_EMAIL_TRIAGE", "STEWARD_HC_SENTINEL", "FIRESIDE_HC_POLL"):
        assert healthchecks.ping(env_key) is False, (
            f"{env_key} still resolves to a pingable URL inside the test suite"
        )
