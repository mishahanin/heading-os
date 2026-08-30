"""Operator identity seam - one place a HEADING OS instance sets who runs it.

The engine ships generic defaults (name "Operator", slug "operator") so a fresh
public clone is operator-agnostic. A real deployment supplies its own identity in
one place; every load-bearing default in the codebase resolves through here.

Resolution precedence (highest wins):
    1. environment  HEADING_OS_OPERATOR_{NAME,SLUG,GITHUB_ORG,VOICE_REFERENCE,EMAIL}
    2. data overlay <data-root>/config/operator.yaml
    3. engine-local config/operator.yaml   (gitignored; for a data-less clone)
    4. the shipped example scripts/operator.example.yaml (generic defaults)

Never raises, and that is now true. Every read/parse error, and every failure to
RESOLVE tier 2 at all, degrades to the next tier down and finally to the generic
dict at tier 4. Composes the existing
scripts.utils.workspace.resolve_config_with_example() helper for the
overlay->example decision and layers the engine-local + env tiers on top.

The documented sentinel. When nothing above tier 4 could be read, `get_operator()`
returns a copy of `_GENERIC`: name "Operator", slug "operator", github_org "",
email "". `operator_org()` and `operator_email_domain()` therefore answer `""`,
`operator_slug()` answers `"operator"`, and `operator_is_default()` answers True.
A caller that needs a REAL org must test the empty string; it must not assume the
value is real merely because no exception arrived.

Why this is the read that degrades rather than the read that refuses. Until
2026-08-30 the promise above was false through tier 2: `get_data_config_dir()`
reaches `get_data_root()`, which raises `DataRootError` when `HEADING_OS_DATA`
names a path that is not a directory, and `get_workspace_identity()` raises
`ValueError` on a corrupt `.workspace-identity.json`. Five modules call into this
seam at MODULE scope, so the exception arrived during import, before argparse:
`scripts/emergency-revoke.py` (the incident tool) died at its import line with a
traceback and exit 1 on `--help`, on a machine whose overlay is missing, which is
the same machine state the incident it serves produces. The `DataRootError` guard
exists to stop a WRITE landing on the live overlay by accident. Nothing here
writes; it reads an identity file, and the resolution chain already documents two
lower tiers to read instead. Falling to them costs nothing and unblocks every
caller at once, where correcting the docstring instead would have needed a
handler at each of the twenty-odd call sites. The degradation is announced once
per process on stderr, so it is never silent.
"""
from __future__ import annotations

import os
import sys
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

    A tier 2 that cannot even be RESOLVED (no usable data root, corrupt workspace
    identity) is treated exactly like a tier 2 that is absent: skip it and carry
    on down. That is what makes the module docstring's "never raises" true. The
    reason is stated there and the fall is announced on stderr, never taken in
    silence.
    """
    from scripts.utils.paths import DataRootError
    from scripts.utils.workspace import resolve_config_with_example, get_workspace_root

    try:
        resolved = resolve_config_with_example(OPERATOR_FILENAME, _EXAMPLE_PATH)
    except (DataRootError, ValueError, OSError) as exc:
        # ValueError as well as DataRootError, for the same reason
        # scripts.utils.workspace.display_path() catches both: the resolution
        # chain runs through get_workspace_identity(), which raises ValueError by
        # design on a corrupt .workspace-identity.json.
        print(f"[operator-identity] the private data overlay could not be "
              f"resolved ({type(exc).__name__}: {exc}); reading the operator "
              f"identity from the engine-local config/operator.yaml or the "
              f"shipped example instead. github_org may be empty.",
              file=sys.stderr)
        resolved = _EXAMPLE_PATH
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

    Never raises. Returns the documented sentinel -- a copy of _GENERIC, so
    github_org "" and slug "operator" -- on a fresh clone AND whenever the data
    overlay cannot be resolved or read (see the module docstring). Cached; call
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
    """Operator GitHub org/owner. '' on an unconfigured clone, and '' when the
    data overlay is unreachable. Never raises. A caller that needs a real org
    must test for the empty string rather than trust that no exception arrived."""
    return get_operator()["github_org"]


def operator_email_domain() -> str:
    """The domain half of the operator's email, or '' when unconfigured.

    Added 2026-08-23. Two engine scripts had a tenant domain compiled in --
    `scripts/gal-export.py` defaulted `--domain` to a real company's domain and
    `scripts/bootcamp-roster.py` read `gal-<that domain>.json` -- so on any other
    deployment the pair silently disagreed about which file to write and read.
    The domain is instance identity like the slug and the org, and it belongs
    beside them rather than in two callers.
    """
    email = (get_operator().get("email") or "").strip()
    _, _, domain = email.partition("@")
    return domain.lower()


def _reset_cache() -> None:
    """Clear the identity cache. Intended for tests; not for production use."""
    _cached.cache_clear()
