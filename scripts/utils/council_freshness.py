#!/usr/bin/env python3
"""
council_freshness.py - read-only freshness check for the /council model pins.

Answers one question per provider: is the pinned model still the right one?
Two kinds of finding:

  - broken : the pinned model is not actually available (the concrete failure
             mode we hit when kimi-k2.7:cloud was pinned but never pulled into
             the local ollama). Detected only where the availability list is
             authoritative -- ollama's /api/tags -- so a cloud API that simply
             does not enumerate a dated snapshot never yields a false "broken".
  - newer  : a same-tier, higher-version flagship exists that the pin has not
             adopted yet. Conservative on purpose: variants (mini, lite, code,
             vision, preview, dated snapshots) are excluded so the check never
             suggests downgrading /council to a cheaper or preview model.

The check NEVER mutates anything. It returns structured findings; the CLI
(scripts/council-models.py --check) renders them and the notify entrypoint
pushes a one-line nudge to Telegram. Adoption stays a manual one-command bump.

Pure comparison functions (parse_version / newer_flagship / classify_*) are
separated from the network probes so they can be unit-tested with injected
model lists and no live API calls.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.api import load_api_key  # noqa: E402
from scripts.utils.council_models import get_model  # noqa: E402

# Tokens that mark a NON-flagship variant. Matched against the id's delimiter-
# split tokens (NOT raw substrings), so "mini" flags "grok-4-mini" but not the
# "mini" inside "geMINI". A candidate whose tokens intersect this set is never
# proposed as a bump target, even if its version number is higher.
VARIANT_MARKERS = frozenset({
    "mini", "lite", "nano", "8b", "code", "vision", "image",
    "preview", "exp", "beta", "latest", "thinking",
})

_TOKEN_SPLIT = re.compile(r"[-.:]+")

# Provider model-list endpoints (read-only GET). Keys come from .env.
XAI_MODELS_URL = "https://api.x.ai/v1/models"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"

HTTP_TIMEOUT = 8


# ============================================================
# Pure version logic (unit-tested)
# ============================================================

def parse_version(model_id: str) -> tuple[int, ...] | None:
    """Extract the first dotted-or-single numeric version from a model id.

    'grok-4.5' -> (4, 5); 'gemini-3.5-flash' -> (3, 5); 'kimi-k2.6:cloud' -> (2, 6);
    'bge-m3' -> (3,). Returns None when no numeric version is present.
    """
    m = re.search(r"(\d+(?:\.\d+)*)", model_id)
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def _variant_markers(model_id: str) -> set[str]:
    """The variant-marker tokens present in an id (token-based, not substring)."""
    return set(_TOKEN_SPLIT.split(model_id.lower())) & VARIANT_MARKERS


def is_variant(model_id: str) -> bool:
    """True when the id names a non-flagship variant (in absolute terms).

    Token-based, not substring: 'grok-4-mini' is a variant, but 'gemini-3.5-flash'
    is not (the 'mini' inside 'gemini' is not a standalone token). Note: 'code'
    counts here, so 'kimi-k2.7-code' is a variant in the ABSOLUTE sense used by a
    general pin. Comparisons are pin-relative (see newer_flagship) so a code pin
    still tracks newer code models.
    """
    return bool(_variant_markers(model_id))


def _family_stem(model_id: str) -> str:
    """The comparable stem of a model id: lowercase, version digits and the
    ollama ':cloud' tag stripped, so same-family ids compare equal.

    'grok-4.5' -> 'grok-'; 'gemini-3.5-flash' -> 'gemini--flash';
    'kimi-k2.6:cloud' -> 'kimi-k'.
    """
    stem = model_id.lower().split(":", 1)[0]
    stem = re.sub(r"\d+(?:\.\d+)*", "", stem)
    return stem


def newer_flagship(pin: str, available: list[str]) -> str | None:
    """The highest same-family, non-variant model id in `available` whose version
    strictly exceeds the pin's. None when the pin is already newest (or nothing
    comparable is available).

    Same-family is decided by matching the version-stripped stem, so a pin of
    'grok-4.5' only ever compares against other 'grok-<n>' ids, never against
    'grok-code' variants (those are excluded) or an unrelated family.
    """
    pin_ver = parse_version(pin)
    if pin_ver is None:
        return None
    pin_stem = _family_stem(pin)
    pin_markers = _variant_markers(pin)
    best_id: str | None = None
    best_ver: tuple[int, ...] = pin_ver
    for cand in available:
        # Pin-relative: a candidate is a disqualifying variant only if it carries
        # a marker the PIN lacks. So a general pin (no markers) never adopts a
        # '-code' model, but a 'kimi-k2.7-code' pin still tracks 'kimi-k2.8-code'.
        if _variant_markers(cand) - pin_markers:
            continue
        if _family_stem(cand) != pin_stem:
            continue
        ver = parse_version(cand)
        if ver is None:
            continue
        if ver > best_ver:
            best_ver = ver
            best_id = cand
    return best_id


def classify_direct_api(provider: str, pin: str, available: list[str] | None) -> dict:
    """Finding for a direct cloud API (grok, gemini). Never 'broken' -- a cloud
    API may not enumerate the exact pinned snapshot, so absence is not proof of
    breakage. Only 'newer' or 'ok' (or 'unknown' when the probe failed)."""
    if available is None:
        return _finding(provider, pin, "unknown", None,
                        f"{provider}: could not reach model list")
    cand = newer_flagship(pin, available)
    if cand:
        return _finding(provider, pin, "newer", cand,
                        f"{provider}: {cand} available (pin {pin})")
    return _finding(provider, pin, "ok", None, f"{provider}: {pin} current")


def classify_ollama_model(provider: str, pin: str, tags: list[str] | None) -> dict:
    """Finding for an ollama-served model (kimi). ollama's tag list IS
    authoritative for local availability, so a missing pin is a real 'broken'."""
    if tags is None:
        return _finding(provider, pin, "unknown", None,
                        f"{provider}: ollama unreachable")
    if pin not in tags:
        return _finding(provider, pin, "broken", None,
                        f"{provider}: pin {pin} not in ollama (pull it or revert the pin)")
    cand = newer_flagship(pin, tags)
    if cand:
        return _finding(provider, pin, "newer", cand,
                        f"{provider}: {cand} pulled but not pinned (pin {pin})")
    return _finding(provider, pin, "ok", None, f"{provider}: {pin} current")


def _finding(provider: str, pin: str, status: str, candidate: str | None, detail: str) -> dict:
    return {"provider": provider, "pin": pin, "status": status,
            "candidate": candidate, "detail": detail}


def is_actionable(finding: dict) -> bool:
    """A finding the CEO should see: a broken pin or an available newer model."""
    return finding["status"] in ("broken", "newer")


def nudge_line(findings: list[dict]) -> str:
    """One-line Telegram nudge built from actionable findings; '' when all OK.

    Appends the exact bump command when at least one provider has a concrete
    candidate, so the message is directly actionable.
    """
    actionable = [f for f in findings if is_actionable(f)]
    if not actionable:
        return ""
    parts = [f["detail"] for f in actionable]
    sets = " ".join(
        f"{f['provider']}={f['candidate']}" for f in actionable if f.get("candidate")
    )
    line = "Council models: " + "; ".join(parts) + "."
    if sets:
        line += f" Apply: python scripts/council-models.py --set {sets}"
    return line


# ============================================================
# Network probes (best-effort, read-only)
# ============================================================

def _http_json(url: str, headers: dict | None = None, timeout: int = HTTP_TIMEOUT) -> dict | None:
    """GET a JSON body, or None on any network/parse failure (caller degrades)."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None


def probe_xai() -> list[str] | None:
    key = load_api_key("XAI_API_KEY", required=False)
    if not key:
        return None
    body = _http_json(XAI_MODELS_URL, headers={"Authorization": f"Bearer {key}"})
    if not body:
        return None
    return [m.get("id", "") for m in body.get("data", []) if isinstance(m, dict) and m.get("id")]


def probe_gemini() -> list[str] | None:
    key = load_api_key("GEMINI_API_KEY", required=False)
    if not key:
        return None
    body = _http_json(f"{GEMINI_MODELS_URL}?key={key}&pageSize=200")
    if not body:
        return None
    ids: list[str] = []
    for m in body.get("models", []):
        if isinstance(m, dict) and m.get("name"):
            ids.append(m["name"].split("/", 1)[-1])  # 'models/gemini-x' -> 'gemini-x'
    return ids


def probe_ollama() -> list[str] | None:
    body = _http_json(OLLAMA_TAGS_URL)
    if body is None:
        return None
    tags: list[str] = []
    for m in body.get("models", []) or []:
        if isinstance(m, dict):
            name = m.get("name") or m.get("model") or ""
            if name:
                tags.append(name)
    return tags


# ============================================================
# Orchestration
# ============================================================

def assess(probes: dict | None = None) -> list[dict]:
    """Full read-only assessment across the three providers.

    `probes` injects model lists for tests: a dict with any of the keys
    'xai' / 'gemini' / 'ollama' mapping to a list[str] (or None to simulate a
    failed probe). Missing keys are probed live.
    """
    probes = probes or {}
    xai = probes["xai"] if "xai" in probes else probe_xai()
    gemini = probes["gemini"] if "gemini" in probes else probe_gemini()
    ollama = probes["ollama"] if "ollama" in probes else probe_ollama()

    return [
        classify_direct_api("grok", get_model("grok"), xai),
        classify_direct_api("gemini", get_model("gemini"), gemini),
        classify_ollama_model("kimi", get_model("kimi"), ollama),
        classify_ollama_model("kimi-code", get_model("kimi-code"), ollama),
    ]
