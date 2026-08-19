#!/usr/bin/env python3
"""`validate-crm-schema.py` must read a block list that sits at its key's column.

YAML accepts both of these, and they mean the same array:

    tags:
      - softlist          # indented
    tags:
    - softlist            # at the key's own column

The parser's look-ahead was `^\\s+-\\s+`, which requires at least one space, so
the second form matched nothing. The key kept its empty value and the record was
reported as `tags: '' is not of type 'array'` — a schema failure invented by the
reader, against a file that is correct on disk.

Measured 2026-08-20 with `jsonschema` staged into the run: 2 of 326 live records
failed, and this was one of them. It is the same defect that was deleting block
lists in `merge-contacts.py`, in a second hand-rolled parser, which is the cost
this corpus pays for six of them.

The other failure that measurement found is real data, not a parser artefact:
one record carries `status: inactive`, which is not one of the six values
`crm-relationship.schema.json` allows. It is left alone here - it is a record to
correct or a schema to widen, and neither is a parser's decision. The record is
not named, because a CRM slug is real-entity content and this repository is
public.

These tests do not need `jsonschema`. They exercise the parser, which is the
part that was wrong; the gate itself cannot run in this venv at all, which is
its own finding and is recorded in `main()`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "validate-crm-schema.py"


@pytest.fixture(scope="module")
def vcs():
    spec = importlib.util.spec_from_file_location("validate_crm_schema", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(tmp_path: Path, frontmatter: str) -> Path:
    p = tmp_path / "record.md"
    p.write_text(f"---\n{frontmatter}---\n\n## Notes\n", encoding="utf-8")
    return p


def test_a_block_list_at_the_key_s_own_column_is_an_array(vcs, tmp_path):
    fm = vcs.parse_frontmatter(_write(tmp_path, (
        "status: active\n"
        "tags:\n"
        "- softlist\n"
        "- uzbekistan\n"
        "- compliance-hold\n"
        "timezone: Europe/Warsaw\n"
    )))
    assert fm["tags"] == ["softlist", "uzbekistan", "compliance-hold"]
    assert fm["timezone"] == "Europe/Warsaw"


def test_an_indented_block_list_still_works(vcs, tmp_path):
    fm = vcs.parse_frontmatter(_write(tmp_path, "tags:\n  - a\n  - b\nnext: x\n"))
    assert fm["tags"] == ["a", "b"]
    assert fm["next"] == "x"


def test_a_flow_list_still_works(vcs, tmp_path):
    fm = vcs.parse_frontmatter(_write(tmp_path, "tags: [a, b]\nempty: []\n"))
    assert fm["tags"] == ["a", "b"]
    assert fm["empty"] == []


def test_a_key_with_no_value_and_no_items_is_not_an_array(vcs, tmp_path):
    """`notes:` alone must keep whatever it meant before, not become []."""
    fm = vcs.parse_frontmatter(_write(tmp_path, "notes:\nnext: x\n"))
    assert fm["notes"] in ("", [])
    assert fm["next"] == "x"


def test_every_live_block_list_reads_as_an_array(vcs):
    """Finds the records by SHAPE, never by slug: a slug is real-entity content
    and this repository is public."""
    import re

    from scripts.utils.workspace import get_data_root

    crm = Path(get_data_root()) / "crm"
    if not crm.exists():
        pytest.skip("private data overlay not present")

    block_key = re.compile(r"^([\w-]+):[ \t]*$\n(?:^[ \t]*-[ \t].*$\n?)+", re.M)
    checked = 0
    for f in sorted(crm.glob("contacts/*.md")) + sorted(crm.glob("address-book/*.md")):
        head = f.read_text(encoding="utf-8").partition("\n---\n")[0]
        keys = [m.group(1) for m in block_key.finditer(head + "\n")]
        if not keys:
            continue
        fm = vcs.parse_frontmatter(f) or {}
        for key in keys:
            assert isinstance(fm.get(key), list) and fm[key], (
                f"{f.name}: {key} parsed as {fm.get(key)!r}, not an array"
            )
            checked += 1

    if not checked:
        pytest.skip("no live record uses a block list")
