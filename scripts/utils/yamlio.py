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

Equivalence, as measured rather than assumed (2026-08-20, corrected 2026-08-26):
both loaders returned the same value for all 26 YAML files in this repo. On
malformed input they diverge in THREE known ways, and they do NOT all run in the
same direction. The 2026-08-20 sentence said "the one divergence ... is STRICTER
... never away from it", which was a claim the 14-case corpus behind it could not
support: the corpus contained no tab.

  1. ``%YAML 1.3`` (an unsupported MINOR version): pure-Python accepts,
     ``CSafeLoader`` raises. STRICTER. Note the narrowing: ``%YAML 2.0`` and
     ``%YAML 9.9`` are rejected by BOTH, so this is not "any unsupported
     version" as the old sentence had it.
  2. A tab between a key and its value (``crm/:\tprivate``): pure-Python raises
     a ScannerError, ``CSafeLoader`` parses it. LOOSER.
  3. A tab inside a scalar value (``crm/: pri\tvate``): same split, and the tab
     survives into the parsed string. LOOSER.

Cases 2 and 3 mean a fail-closed handler built on ``except yaml.YAMLError``
CANNOT rely on this loader's strictness for its safety. ``load_routing_map`` is
the one such handler today and it does not: it validates every destination
against ``{"engine", "private", "corporate"}`` and coerces anything else to
``private``, so case 3's ``pri\tvate`` fails closed on the VALUE check rather
than on the parse, and case 2 yields exactly the destination the file's author
wrote. Neither widens what is shareable. A new fail-closed handler must carry
its own value check for the same reason; do not add one that leans on the parse.

Do not describe this as byte-for-byte identical to ``yaml.safe_load``. Pinned by
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
# Identity against the class we would have bound, NOT a comparison of
# ``__name__`` to the string "CSafeLoader". A name is not a capability: an
# alias, a subclass or a rename in a future PyYAML would leave this flag
# reporting the wrong parser with nothing raising. ``yaml.__with_libyaml__``
# answers "was PyYAML built with libyaml", which is close but not the question
# either -- this flag has to say whether THIS module bound the C loader, and
# the import above can fail for reasons the build flag knows nothing about.
USING_LIBYAML = _Loader is getattr(yaml, "CSafeLoader", None)


def safe_load(stream: str | bytes | IO[str] | IO[bytes]) -> Any:
    """Drop-in replacement for ``yaml.safe_load`` that prefers the C parser.

    Raises ``yaml.YAMLError`` on malformed input exactly as ``yaml.safe_load`` does,
    so existing fail-closed handlers keep working unchanged.
    """
    # Suppressed below because _Loader is CSafeLoader or SafeLoader and nothing else —
    # the same safe tag set yaml.safe_load() binds, so no arbitrary object construction.
    return yaml.load(stream, Loader=_Loader)  # noqa: S506  # nosec B506
