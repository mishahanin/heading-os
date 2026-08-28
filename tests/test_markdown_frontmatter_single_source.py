#!/usr/bin/env python3
"""One frontmatter parser under scripts/, plus a named list of measured exceptions.

``scripts/utils/markdown.py`` is the canonical home: ``parse_frontmatter``
(yaml.safe_load with a regex fallback, native types preserved) and
``parse_frontmatter_str`` (every value coerced to str). Every other script that
needs frontmatter should be a THIN WRAPPER around one of those - read
``scripts/knowledge-health.py`` for the shape.

This test walks every module under ``scripts/`` and fails when a file that is
neither a wrapper nor a named survivor defines its own parser. A wrapper is
detected structurally: the module imports a name from ``scripts.utils.markdown``
and the function body references it. That means a survivor can be migrated at
any time without touching this test - only ADDING a new copy fails.

Counted 2026-08-20: 17 ``parse_frontmatter*`` definitions tree-wide, 2 of them
canonical. ``scripts/odin_pagerank.py`` became a wrapper on 2026-08-20 (proven
identical over all 522 Odin brain notes and over the built graph); the survivors
below each carry a measured reason at their own call site.
"""
from __future__ import annotations

import ast
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
CANONICAL = SCRIPTS / "utils" / "markdown.py"
CANONICAL_MODULE = "scripts.utils.markdown"

# Any function whose name claims to parse frontmatter, however it spells it.
PARSER_NAMES = {
    "parse_frontmatter",
    "_parse_frontmatter",
    "parse_frontmatter_str",
    "_parse_frontmatter_str",
    "parse_frontmatter_raw",
    "_parse_frontmatter_raw",
    # Added 2026-08-28. `scripts/artifact-evaluator.py` spelled its copy
    # `parse_yaml_frontmatter`, so this detector never saw it, and that copy
    # carried the `---`-inside-a-scalar defect the sweep exists to catch. A
    # detector keyed on names only sees the spellings it was told about, so a
    # new copy under a fourth spelling is still invisible; this is a ratchet,
    # not a proof.
    "parse_yaml_frontmatter",
    "_parse_yaml_frontmatter",
    "split_frontmatter",
    "parse_frontmatter_strict",
}

# Files allowed to define their own parser, with the reason it cannot be a
# wrapper. Removing an entry once its copy migrates is a strict improvement and
# needs no change here; the test only fails on a copy that is NOT listed.
ALLOWED_SURVIVORS = {
    # `scripts/generate-skill-router.py`, `scripts/skill-metadata-check.py` and
    # `scripts/artifact-evaluator.py` left this list on 2026-08-28. All three
    # were here because the shared parser collapsed every failure into
    # ({}, text) and the error string is a gate's whole output;
    # `markdown.parse_frontmatter_strict` and `markdown.split_frontmatter` now
    # return the classification and let each caller keep its own wording. The
    # copies had drifted apart in the meantime: two of the three cut the block
    # at a `---` inside a scalar and the third did not, so two CI gates
    # disagreed about the same SKILL.md.
    "scripts/validate-crm-schema.py":
        "Schema-aware coercion: its value types are what jsonschema then checks. "
        "Measured 326/326 records violate a declared type under "
        "parse_frontmatter, 170/326 under parse_frontmatter_str.",
    "scripts/merge-contacts.py":
        "Paired with serialize_frontmatter; measured 48 of 326 CRM files would "
        "be written back with different bytes under parse_frontmatter_str.",
    "scripts/promote-knowledge.py":
        "Returns the RAW yaml block, not a dict, so inject_frontmatter_fields "
        "can edit lines while preserving quoting, comments and order. The "
        "shared util has no raw-block accessor on its public surface.",
    "scripts/council-aggregate.py":
        "Not in the 2026-08-20 dedup sweep's scope; unmeasured.",
    "scripts/bridge_daemon/sources/capabilities.py":
        "Not in the 2026-08-20 dedup sweep's scope; unmeasured.",
    "scripts/bridge_daemon/sources/library.py":
        "Not in the 2026-08-20 dedup sweep's scope; unmeasured.",
    "scripts/bridge_daemon/sources/tribe.py":
        "Not in the 2026-08-20 dedup sweep's scope; unmeasured.",
}


def _imported_from_canonical(tree: ast.Module) -> set[str]:
    """Names this module pulled in from scripts.utils.markdown (alias-aware)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("utils.markdown"):
            names.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == CANONICAL_MODULE:
                    names.add(a.asname or a.name.split(".")[0])
    return names


def _references(func: ast.FunctionDef, names: set[str]) -> bool:
    """True when the function body mentions one of ``names``."""
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and node.id in names:
            return True
        if isinstance(node, ast.Attribute) and node.attr in names:
            return True
    return False


def _own_parsers() -> tuple[dict[str, list[str]], int]:
    """(rel_path -> parser names it defines itself, total definitions seen)."""
    offenders: dict[str, list[str]] = {}
    total = 0
    for path in sorted(SCRIPTS.rglob("*.py")):
        if path == CANONICAL:
            continue
        try:
            # Parsing the whole tree surfaces other files' warnings (2026-08-20:
            # scripts/pencil-export.py has an invalid escape sequence). Not this
            # test's business, so it is silenced rather than reported here.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):  # pragma: no cover - a broken file is another test's job
            continue
        canonical_names = _imported_from_canonical(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in PARSER_NAMES:
                continue
            total += 1
            if canonical_names and _references(node, canonical_names):
                continue  # thin wrapper around the canonical parser
            offenders.setdefault(str(path.relative_to(ROOT)), []).append(node.name)
    return offenders, total


def test_canonical_module_still_defines_the_parsers():
    """Pins the detector: wrapper detection is meaningless if the home moved."""
    tree = ast.parse(CANONICAL.read_text(encoding="utf-8"))
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert {"parse_frontmatter", "parse_frontmatter_str"} <= defined, (
        f"{CANONICAL_MODULE} no longer defines both canonical parsers; "
        "update this test and every wrapper that imports them."
    )


def test_detector_still_sees_the_corpus():
    """A detector that matches nothing passes everything."""
    _offenders, total = _own_parsers()
    assert total >= 10, (
        f"only {total} parse_frontmatter* definitions found under scripts/ - "
        "the name set or the walk is broken, not the tree."
    )


def test_no_new_frontmatter_parser():
    offenders, _total = _own_parsers()
    unlisted = {p: fns for p, fns in offenders.items() if p not in ALLOWED_SURVIVORS}
    if unlisted:
        listed = "\n".join(f"  {p}: {', '.join(fns)}" for p, fns in sorted(unlisted.items()))
        pytest.fail(
            "New frontmatter parser(s) defined outside scripts/utils/markdown.py:\n"
            f"{listed}\n\n"
            "Use `from scripts.utils.markdown import parse_frontmatter` (native "
            "types) or `parse_frontmatter_str` (all values str) and keep the local "
            "function as a thin wrapper - see scripts/knowledge-health.py. If the "
            "copy genuinely cannot be a wrapper, add it to ALLOWED_SURVIVORS in "
            "this file with the measured reason."
        )
