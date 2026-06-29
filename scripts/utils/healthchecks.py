"""Healthchecks.io ping helper for the Fireside daemon.

Each cron-like job pings a specific URL on success. Misconfig (env var missing)
or transient network failure (timeout, connection refused) MUST NOT crash the
job — the job's primary work already succeeded. Failures here are informational.
"""
from __future__ import annotations

import logging
import os

import requests

_logger = logging.getLogger(__name__)
# 10s matches HC.io's recommended client timeout; raise only if endpoint SLO changes.
_TIMEOUT_SEC = 10


def ping(env_var: str) -> bool:
    """Send a success ping to the HC.io URL stored in `env_var`.

    Returns True on HTTP 200, False on any failure (incl. missing env var).
    """
    url = os.environ.get(env_var)
    if not url:
        _logger.info("hc-ping skipped: env var %s not set", env_var)
        return False
    try:
        r = requests.get(url, timeout=_TIMEOUT_SEC)
        ok = r.status_code == 200
        if not ok:
            _logger.warning("hc-ping %s returned HTTP %s", env_var, r.status_code)
        return ok
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        _logger.warning("hc-ping %s network failure: %s", env_var, e)
        return False
    except Exception as e:
        _logger.exception("hc-ping %s unexpected failure: %s", env_var, e)
        return False
