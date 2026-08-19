"""Loader for the HTML/CSS templates the standalone HTML generators render.

Consumed by: scripts/generate-dashboard.py, scripts/generate-newsletter-html.py,
scripts/generate-crm-dashboard.py, scripts/generate-partner-enablement.py.

Same seam as scripts/utils/doctype_renderer.py, one directory over: the locked
markup lives under the brand templates directory in the DATA overlay
(`datastore/brand/templates/generators/`), resolved through get_datastore_dir()
so it is never a hardcoded path and never a path into the public engine tree.
These are brand assets; the engine carries the code that renders them.

Placeholder syntax matches the doctype templates: `{{UPPER_SNAKE}}`.

WHY THIS RAISES INSTEAD OF DEGRADING: every one of these generators emits a
self-contained single-file HTML with the stylesheet inlined. A template that
silently resolved to "" would render a complete, plausible-looking, entirely
UNSTYLED document — a failure nobody notices until the artifact is already in
front of a counterpart. So a missing template file, an unfilled placeholder,
and an unused value are all hard errors here.

Public API:
    templates_dir() -> Path
    load_template(name) -> str
    render_template(name, **values) -> str
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.utils.workspace import get_datastore_dir

_VAR_RE = re.compile(r"\{\{([A-Z_][A-Z0-9_]*)\}\}")


def templates_dir() -> Path:
    """Directory holding the generator templates (DATA overlay, not the engine)."""
    return get_datastore_dir() / "brand" / "templates" / "generators"


def load_template(name: str) -> str:
    """Read a template verbatim. Raises FileNotFoundError when it is absent."""
    path = templates_dir() / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(
            f"Generator template '{name}' could not be read from {path}: {exc}. "
            "Refusing to render an unstyled document."
        ) from exc


def render_template(name: str, **values: str) -> str:
    """Substitute every `{{PLACEHOLDER}}` in a template, in one pass.

    One pass, not sequential str.replace, so a substituted value that happens to
    contain `{{...}}` is never re-substituted.
    """
    template = load_template(name)

    found: set[str] = set()

    def replace(match: re.Match) -> str:
        key = match.group(1)
        found.add(key)
        if key not in values:
            raise KeyError(
                f"Generator template '{name}' has placeholder {{{{{key}}}}} with no value supplied."
            )
        return values[key]

    rendered = _VAR_RE.sub(replace, template)

    unused = sorted(set(values) - found)
    if unused:
        raise KeyError(
            f"Values supplied to generator template '{name}' match no placeholder: {', '.join(unused)}."
        )

    return rendered
