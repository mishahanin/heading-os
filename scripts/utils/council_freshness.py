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
    """One-line nudge; '' ONLY when every pin was checked and every one is ok.

    A run that could not probe at all -- the proxy down, or `CLIPROXY_API_KEY`
    unset -- returns `unknown` findings, not `broken` ones, so `is_actionable`
    counted none and this returned `''`. Both callers read `''` as good news and
    print it: `scripts/council-models-notify.py` logs "all council pins current"
    and the daily unit exits 0, and `scripts/council-models.py` prints "All
    council pins are current". Neither run had established anything about a pin.

    An unknown is not a nudge about a MODEL, so it does not read like one; it
    says the check did not happen, which is what the operator needs to know.
    """
    actionable = [f for f in findings if is_actionable(f)]
    if actionable:
        return "Council models: " + "; ".join(f["detail"] for f in actionable) + "."
    unknown = [f for f in findings if f["status"] == "unknown"]
    if unknown:
        reasons = sorted({f["detail"] for f in unknown})
        return ("Council pins NOT checked (" + "; ".join(reasons)
                + "); freshness is unknown, not confirmed.")
    return ""


def _http_json(url, headers=None, timeout=HTTP_TIMEOUT):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None


def probe_proxy():
    """GET the proxy /v1/models catalog; None on any failure.

    "Any failure" includes a 200 whose body is well-formed JSON of the wrong
    shape. This read `body.get("data", [])`, and `dict.get` substitutes its
    default only when the key is ABSENT: a body of `{"data": null}` - a proxy
    error page, a gateway that answers in its own envelope, a version skew -
    passed the `if not body` guard and then iterated `None`. MEASURED
    2026-08-30: `TypeError: 'NoneType' object is not iterable`, raised straight
    out of `assess()`, which the daily `scripts/council-models-notify.py` unit
    calls. `{"data": "abc"}` was worse than the crash: a string iterates its
    characters, so the catalog came back `[]` and every pin classified `broken`,
    turning a probe failure into three false alarms.

    The whole point of this function is that a probe failure reads as `unknown`
    downstream (`classify_proxy_model` -> `nudge_line`'s "NOT checked" line), so
    a non-list `data` has to return None like every other failure.
    """
    key = load_api_key("CLIPROXY_API_KEY", required=False)
    if not key:
        return None
    body = _http_json(PROXY_MODELS_URL, headers={"Authorization": f"Bearer {key}"})
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if not isinstance(data, list):
        return None
    return [m.get("id", "") for m in data
            if isinstance(m, dict) and m.get("id")]


def assess(probes=None):
    """Read-only assessment of the three council pins against the proxy catalog.

    `probes` injects the catalog for tests: {"proxy": [...ids...] | None}.
    """
    probes = probes or {}
    catalog = probes["proxy"] if "proxy" in probes else probe_proxy()
    return [classify_proxy_model(p, get_model(p), catalog) for p in PROVIDERS]
