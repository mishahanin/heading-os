"""Operator identity seam - one place a HEADING OS instance sets who runs it.

The engine ships generic defaults (name "Operator", slug "operator") so a fresh
public clone is operator-agnostic. A real deployment supplies its own identity in
one place; every load-bearing default in the codebase resolves through here.

Resolution precedence (highest wins):
    1. environment  HEADING_OS_OPERATOR_{NAME,SLUG,GITHUB_ORG,VOICE_REFERENCE,EMAIL}
    2. data overlay <data-root>/config/operator.yaml
    3. engine-local config/operator.yaml   (gitignored; for a data-less clone)
    4. the shipped example scripts/operator.example.yaml (generic defaults)

Never raises: on any read/parse error it returns the generic dict. Composes the
existing scripts.utils.workspace.resolve_config_with_example() helper for the
overlay->example decision and layers the engine-local + env tiers on top.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

OPERATOR_FILENAME = "operator.yaml"

# Shipped engine example (generic identity). scripts/operator.example.yaml.
_EXAMPLE_PATH = Path(__file__).resolve().parent.parent / "operator.example.yaml"

# The neutral identity a fresh clone resolves to.
_GENERIC: dict[str, str] = {
    "name": "Operator",
    "slug": "operator",
    "github_org": "",
    "voice_reference": "reference/voice.md",
    "email": "",
}

# field -> environment variable (highest-precedence tier).
_ENV_KEYS: dict[str, str] = {
    "name": "HEADING_OS_OPERATOR_NAME",
    "slug": "HEADING_OS_OPERATOR_SLUG",
    "github_org": "HEADING_OS_OPERATOR_GITHUB_ORG",
    "voice_reference": "HEADING_OS_OPERATOR_VOICE_REFERENCE",
    "email": "HEADING_OS_OPERATOR_EMAIL",
}


def _resolve_file() -> tuple[Path | None, bool]:
    """Return (path, is_real). is_real is False when the path is the generic example.

    Composes resolve_config_with_example() for the overlay->example decision, then
    inserts the engine-local config/operator.yaml tier between them: if the overlay
    file is absent (helper fell back to the example) but an engine-local file
    exists, prefer it.
    """
    from scripts.utils.workspace import resolve_config_with_example, get_workspace_root

    resolved = resolve_config_with_example(OPERATOR_FILENAME, _EXAMPLE_PATH)
    if resolved == _EXAMPLE_PATH:
        engine_local = get_workspace_root() / "config" / OPERATOR_FILENAME
        if engine_local.exists():
            return engine_local, True
        return (_EXAMPLE_PATH if _EXAMPLE_PATH.exists() else None), False
    return resolved, True


def _load() -> tuple[dict, bool]:
    """Return (operator_dict, configured). configured is True when a real
    operator.yaml (overlay or engine-local) or any env var supplied a value."""
    import yaml

    data = dict(_GENERIC)
    configured = False

    path, is_real = _resolve_file()
    if path is not None and path.exists():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            loaded = {}
        if isinstance(loaded, dict):
            for key in _GENERIC:
                val = loaded.get(key)
                if val not in (None, ""):
                    data[key] = str(val)
                    if is_real:
                        configured = True

    for key, env_name in _ENV_KEYS.items():
        val = os.environ.get(env_name)
        if val not in (None, ""):
            data[key] = val
            configured = True

    return data, configured


@lru_cache(maxsize=1)
def _cached() -> tuple[dict, bool]:
    return _load()


def get_operator() -> dict:
    """Resolved operator identity dict (name/slug/github_org/voice_reference/email).

    Never raises; returns generic defaults on a fresh clone. Cached; call
    _reset_cache() in tests after mutating env or files.
    """
    return dict(_cached()[0])


def operator_is_default() -> bool:
    """True when no operator.yaml or env var configured this instance's identity."""
    return not _cached()[1]


def operator_slug() -> str:
    """Operator short handle. 'operator' on an unconfigured clone."""
    return get_operator()["slug"]


def operator_org() -> str:
    """Operator GitHub org/owner. '' on an unconfigured clone."""
    return get_operator()["github_org"]


def _reset_cache() -> None:
    """Clear the identity cache. Intended for tests; not for production use."""
    _cached.cache_clear()
