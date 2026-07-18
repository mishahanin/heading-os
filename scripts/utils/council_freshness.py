#!/usr/bin/env python3
"""council_freshness.py - read-only freshness check for the /council pins.

After the proxy migration the three council voices (gemini/grok/kimi) are served
by the local CLIProxyAPI proxy. The only meaningful check is presence: is each
pinned model still in the proxy catalog (`/v1/models`)? A missing pin is 'broken'
(the model id was renamed or the auth was removed); otherwise 'ok'. The old
newer/auto-bump heuristic is gone — proxy variant names cannot be safely
version-ranked, and pins are deliberate. Never mutates anything.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.api import load_api_key  # noqa: E402
from scripts.utils.council_models import get_model  # noqa: E402

PROXY_MODELS_URL = "http://127.0.0.1:8317/v1/models"
HTTP_TIMEOUT = 8
PROVIDERS = ("gemini", "grok", "kimi")


def _finding(provider, pin, status, candidate, detail):
    return {"provider": provider, "pin": pin, "status": status,
            "candidate": candidate, "detail": detail}


def classify_proxy_model(provider, pin, catalog):
    """Finding for one pin against the proxy catalog (list of model ids, or None
    when the probe failed)."""
    if catalog is None:
        return _finding(provider, pin, "unknown", None,
                        f"{provider}: proxy /v1/models unreachable")
    if pin not in catalog:
        return _finding(provider, pin, "broken", None,
                        f"{provider}: pin {pin} not on the proxy (check `cliproxy models`)")
    return _finding(provider, pin, "ok", None, f"{provider}: {pin} present")


def is_actionable(finding):
    """A finding the CEO should see: a broken pin (only status that acts now)."""
    return finding["status"] == "broken"


def nudge_line(findings):
    """One-line Telegram nudge from broken pins; '' when all present/ok."""
    actionable = [f for f in findings if is_actionable(f)]
    if not actionable:
        return ""
    return "Council models: " + "; ".join(f["detail"] for f in actionable) + "."


def _http_json(url, headers=None, timeout=HTTP_TIMEOUT):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None


def probe_proxy():
    """GET the proxy /v1/models catalog; None on any failure."""
    key = load_api_key("CLIPROXY_API_KEY", required=False)
    if not key:
        return None
    body = _http_json(PROXY_MODELS_URL, headers={"Authorization": f"Bearer {key}"})
    if not body:
        return None
    return [m.get("id", "") for m in body.get("data", [])
            if isinstance(m, dict) and m.get("id")]


def assess(probes=None):
    """Read-only assessment of the three council pins against the proxy catalog.

    `probes` injects the catalog for tests: {"proxy": [...ids...] | None}.
    """
    probes = probes or {}
    catalog = probes["proxy"] if "proxy" in probes else probe_proxy()
    return [classify_proxy_model(p, get_model(p), catalog) for p in PROVIDERS]
