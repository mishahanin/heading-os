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
    # Proxy pins (CLIProxyAPI, 127.0.0.1:8317). Bump with:
    #   python scripts/council-models.py --set gemini=<new>
    # Model ids are the proxy's own catalog names (`cliproxy models`), NOT the
    # vendor-direct names — the proxy fronts the subscriptions.
    "gemini": "gemini-3-flash",
    "grok": "grok-4.5",
    "kimi": "kimi-for-coding",
    # The /scrutinize judge voice, deliberately NOT the same pin as "kimi".
    # `kimi-for-coding` is the fast coding pin; the judge layer needs the
    # reasoning model, and a fast pin standing in for it produces a judge that
    # looks external and reasons shallowly. Registered here so a new Kimi
    # flagship is `--set kimi_reasoning=<new>` rather than a code edit.
    "kimi_reasoning": "k3",
}

# Claude is deliberately absent from this table, and that absence is the design.
# The Claude judge IS the running session, so pinning a version here would freeze
# /scrutinize on whatever was current the day someone typed it, and a newer Opus
# would need a code or config edit to reach the judge layer. There is nothing to
# bump: the session's model is authoritative, always. `tests/test_scrutinize_no_model_pins.py`
# fails if a Claude version literal ever appears in the skill.

# The three voices /council dispatches. NOT the same set as PROVIDERS: the table
# also carries pins used by one caller at one reasoning tier (kimi_reasoning, the
# /scrutinize judge), which is a second pin for an existing voice rather than a
# fourth voice. Keeping the two names apart is what lets a new non-council pin
# land without loosening the assertion that the council roster is these three.
COUNCIL_PROVIDERS = ("gemini", "grok", "kimi")

PROVIDERS = tuple(FALLBACKS.keys())

CONFIG_RELPATH = "config/council-models.json"


def config_path() -> Path:
    """Absolute path to the council model config in the engine tree."""
    return get_workspace_root() / CONFIG_RELPATH


def _load_config() -> dict:
    """Read config/council-models.json, returning {} on any read/parse failure.

    A missing file is silent (first-run / not-yet-created is normal). A present
    but unreadable or malformed file warns to stderr and falls back, so a bad
    edit degrades to the baseline instead of crashing the council. "Unreadable"
    includes a file that will not DECODE, which is a separate failure from a
    file that will not parse.
    """
    path = config_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    # `UnicodeDecodeError` subclasses `ValueError`, making it a SIBLING of
    # `json.JSONDecodeError`, and it is not an `OSError`. `json.load` over a
    # text handle decodes inside its own `f.read()`, before parsing, so a
    # config carrying one non-UTF-8 byte raised past both names and crashed the
    # council -- the outcome the sentence above promises it degrades from.
    # MEASURED 2026-09-01 with one 0xe9 byte: `UnicodeDecodeError: invalid
    # continuation byte` out of `_load_config`. `set_model` below reads the
    # same file and carried the same gap.
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
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
    """Resolve the model id for one provider named in `PROVIDERS`.

    Returns the configured value when present and non-empty, otherwise the
    FALLBACKS baseline. Raises ValueError on an unknown provider name.

    The accepted set is `FALLBACKS`, which is FOUR keys, not the three council
    voices: `kimi_reasoning` resolves here like any other. This line used to
    enumerate `(gemini|grok|kimi)`, so a reader of the contract concluded
    `get_model("kimi_reasoning")` raised - the /scrutinize judge pin, the one
    caller most likely to be reading it. Deferring to `PROVIDERS` is what stops
    a fifth pin re-opening the same gap.
    """
    if provider not in FALLBACKS:
        raise ValueError(
            f"Unknown council provider: {provider!r}. Known: {', '.join(PROVIDERS)}"
        )
    value = _load_config().get(provider)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return FALLBACKS[provider]


def is_fallback(provider: str) -> bool:
    """True when this provider resolved to the baseline, not to a pin.

    `council-models.py --show` used to infer this from `config_path().exists()`,
    which answers a different question: whether the FILE is there, not whether
    it names THIS provider. A config holding only `{"grok": ...}` — a partial
    file, a hand edit, one written by an older version — made gemini and kimi
    resolve to FALLBACKS and print with no `(fallback)` marker at all, so the
    operator read a baseline as a deliberate pin. Resolution is per provider,
    so the question has to be asked per provider.
    """
    if provider not in FALLBACKS:
        raise ValueError(
            f"Unknown council provider: {provider!r}. Known: {', '.join(PROVIDERS)}"
        )
    value = _load_config().get(provider)
    return not (isinstance(value, str) and value.strip())


def load_all() -> dict:
    """Resolved {provider: model} for every known provider."""
    return {provider: get_model(provider) for provider in PROVIDERS}


def set_model(provider: str, model: str) -> None:
    """Set one provider's pin in config/council-models.json (atomic write).

    Preserves any other keys already in the file, and REFUSES rather than
    rewrite when it cannot read them. `_load_config()` returns `{}` for a
    malformed file, which is right for a reader falling back to the baseline and
    wrong for a writer: bumping one pin against a hand-broken config rebuilt the
    file from that `{}` and erased every other operator-chosen pin, silently
    reverting them to fallbacks. The read-side warning went to stderr and the
    write still happened.

    Raises ValueError on an unknown provider or an empty model string, and
    RuntimeError when the existing config is present but unreadable.
    """
    if provider not in FALLBACKS:
        raise ValueError(
            f"Unknown council provider: {provider!r}. Known: {', '.join(PROVIDERS)}"
        )
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Model id must be a non-empty string.")

    path = config_path()
    if path.exists():
        # Read it here rather than through `_load_config`, whose `{}` cannot be
        # told apart from an empty file. A writer needs that difference.
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        # `UnicodeDecodeError` was missing here for the same reason as in
        # `_load_config`: it is a `ValueError`, a sibling of
        # `json.JSONDecodeError`, and not an `OSError`. The write still did not
        # happen -- the raw decode error propagated -- but it propagated as a
        # bare `UnicodeDecodeError` naming no path and offering no remedy,
        # instead of the RuntimeError this docstring promises. MEASURED
        # 2026-09-01 with one 0xe9 byte in the config.
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            raise RuntimeError(
                f"refusing to rewrite {path}: it exists but cannot be read "
                f"({e}). Writing would drop every pin already in it. Fix or "
                f"delete the file, then set the pin again."
            ) from e
        if not isinstance(data, dict):
            raise RuntimeError(
                f"refusing to rewrite {path}: it holds a "
                f"{type(data).__name__}, not an object of pins."
            )
    else:
        data = {}
    data[provider] = model.strip()

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
