#!/usr/bin/env python3
"""
council_models.py - single source of truth for the /council model pins.

The three council API wrappers (gemini-consult.py, grok-consult.py,
kimi-consult.py) resolve their DEFAULT_MODEL through get_model(provider) so the
three flagship pins live in ONE file, config/council-models.json, instead of
being hardcoded in three places. Staying on the latest models is then a
one-command bump via scripts/council-models.py --set, with no code edit.

Fail-safe: if config/council-models.json is missing, unreadable, or malformed,
each provider falls back to its FALLBACKS pin below so /council never
hard-fails on a bad or deleted config. The config can only *change* which model
is used; it can never break the resolver.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.workspace import get_workspace_root  # noqa: E402

# Known-good baseline. Also the canonical provider set: get_model / set_model
# reject any provider not listed here. Kept in sync with the values shipped in
# config/council-models.json so a missing config reproduces current behaviour.
FALLBACKS = {
    "gemini": "gemini-3.5-flash",
    "grok": "grok-4.5",
    # kimi is served through the local ollama daemon (cloud-routed), so the pin
    # must match a tag actually registered there (`ollama list`). Bump only after
    # `ollama pull kimi-<new>:cloud` on this machine.
    "kimi": "kimi-k2.6:cloud",
    # kimi-code: coding-specialised Kimi, the optional 4th /council voice that
    # joins on code tasks (and /scrutinize code reviews). Also ollama-served, so
    # the same pull-then-pin rule applies.
    "kimi-code": "kimi-k2.7-code:cloud",
}

PROVIDERS = tuple(FALLBACKS.keys())

CONFIG_RELPATH = "config/council-models.json"


def config_path() -> Path:
    """Absolute path to the council model config in the engine tree."""
    return get_workspace_root() / CONFIG_RELPATH


def _load_config() -> dict:
    """Read config/council-models.json, returning {} on any read/parse failure.

    A missing file is silent (first-run / not-yet-created is normal). A present
    but unreadable or malformed file warns to stderr and falls back, so a bad
    edit degrades to the baseline instead of crashing the council.
    """
    path = config_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        print(
            f"Warning: could not read {path} ({e}); using fallback council model pins.",
            file=sys.stderr,
        )
        return {}
    if not isinstance(data, dict):
        print(
            f"Warning: {path} is not a JSON object; using fallback council model pins.",
            file=sys.stderr,
        )
        return {}
    return data


def get_model(provider: str) -> str:
    """Resolve the model id for one provider (gemini|grok|kimi).

    Returns the configured value when present and non-empty, otherwise the
    FALLBACKS baseline. Raises ValueError on an unknown provider name.
    """
    if provider not in FALLBACKS:
        raise ValueError(
            f"Unknown council provider: {provider!r}. Known: {', '.join(PROVIDERS)}"
        )
    value = _load_config().get(provider)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return FALLBACKS[provider]


def load_all() -> dict:
    """Resolved {provider: model} for every known provider."""
    return {provider: get_model(provider) for provider in PROVIDERS}


def set_model(provider: str, model: str) -> None:
    """Set one provider's pin in config/council-models.json (atomic write).

    Preserves any other keys already in the file. Raises ValueError on an
    unknown provider or an empty model string.
    """
    if provider not in FALLBACKS:
        raise ValueError(
            f"Unknown council provider: {provider!r}. Known: {', '.join(PROVIDERS)}"
        )
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Model id must be a non-empty string.")

    path = config_path()
    data = _load_config()
    data[provider] = model.strip()

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
