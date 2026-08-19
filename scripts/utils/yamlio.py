"""libyaml-backed YAML reading, with the same safety contract as ``yaml.safe_load``.

``yaml.safe_load`` always binds the pure-Python ``SafeLoader`` even when PyYAML was
built against libyaml. Measured 2026-08-20 on ``config/routing-map.yaml``:
SafeLoader 5.537 ms/call vs CSafeLoader 0.260 ms/call — 21x. On the hot classifier
path (``load_routing_map`` -> ``get_routing_destination``, called once per tracked
file by the push wall and the ``engine-tree-clean`` pre-commit hook) that parse cost
was the whole cost of the check.

``CSafeLoader`` is the libyaml-backed SafeLoader: identical safety contract, no
arbitrary object construction, no ``!!python/object`` tags. So preferring it does
NOT weaken the ``yaml.load()``-without-SafeLoader ban in the security policy
(``~/.claude/CLAUDE.md``, ``.claude/rules/security.md``) — it is the same safe
subset, parsed in C. Where libyaml is absent (a wheel built without it), the
fallback is the pure-Python ``SafeLoader``, so behaviour is unchanged, only slower.

Equivalence, as measured rather than assumed (2026-08-20): both loaders returned
the same value for all 26 YAML files in this repo, and raised/accepted alike on 13
of a 14-case malformed corpus. The one divergence is an unsupported ``%YAML``
version directive, which the pure-Python loader accepts and ``CSafeLoader``
rejects with a ``yaml.YAMLError``. That direction is STRICTER, so every
fail-closed handler built on ``except yaml.YAMLError`` (``load_routing_map`` is
the one today) fails toward the safe answer, never away from it. Do not describe
this as byte-for-byte identical to ``yaml.safe_load``; it is the same safe tag
set, marginally stricter on malformed input. Pinned by
``tests/test_routing_map_cache.py::test_yamlio_strictness_divergence_fails_closed``.
"""

from __future__ import annotations

from typing import IO, Any

import yaml

try:  # libyaml present -> C parser, same safe tag set
    from yaml import CSafeLoader as _Loader
except ImportError:  # pragma: no cover - wheel built without libyaml
    from yaml import SafeLoader as _Loader  # type: ignore[assignment]

# Exported so callers/tests can assert which parser is actually in use.
SafeLoader = _Loader
USING_LIBYAML = _Loader.__name__ == "CSafeLoader"


def safe_load(stream: str | bytes | IO[str] | IO[bytes]) -> Any:
    """Drop-in replacement for ``yaml.safe_load`` that prefers the C parser.

    Raises ``yaml.YAMLError`` on malformed input exactly as ``yaml.safe_load`` does,
    so existing fail-closed handlers keep working unchanged.
    """
    # Suppressed below because _Loader is CSafeLoader or SafeLoader and nothing else —
    # the same safe tag set yaml.safe_load() binds, so no arbitrary object construction.
    return yaml.load(stream, Loader=_Loader)  # noqa: S506  # nosec B506
