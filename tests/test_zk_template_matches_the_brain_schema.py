"""What `/zk add` writes must satisfy what `odin-brain-health.py` requires.

The two lived in different files and drifted. `/zk` shipped ONE generic
frontmatter template for all eight note types, while
`scripts/odin-brain-health.py` holds a DIFFERENT `REQUIRED_FIELDS` list per
brain directory. Checked mechanically on 2026-08-23, the template satisfied
exactly one of the six:

    as a 'source'    note -> missing ['format', 'author', 'ingested']
    as a 'principle' note -> missing ['sources']
    as a 'position'  note -> missing ['principles', 'sources', 'revisit_when']
    as a 'episode'   note -> missing ['date']
    as a 'conflict'  note -> missing ['side_a', 'side_b']
    as a 'reference' note -> OK

So every note created through `/zk add` except a `technology` one was born
failing the brain's own health check, and `/zk stats` would then report the
schema violations it had just caused. The audit that surfaced this saw only the
tip of it: it noticed that step 6 prescribes a `format:` annotation the template
has no field for. `format` turned out to be a genuinely REQUIRED field for a
`sources/` note, and two more were missing beside it.

The fix is a per-destination extras table in the skill reference. This test is
the guard that keeps the table and `REQUIRED_FIELDS` from drifting apart again:
the whole defect was two lists in two files with nothing comparing them.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / ".claude" / "skills" / "zk" / "references" / "subcommands.md"
HEALTH = ROOT / "scripts" / "odin-brain-health.py"

# The base frontmatter every note gets, from the template block in the reference.
# Parsed rather than hardcoded, so editing the template moves this test with it.
BASE_BLOCK = re.compile(r"```markdown\n---\n(.*?)\n---\n", re.S)
EXTRAS_BLOCK = re.compile(
    r"<!-- zk-required-extras:start -->\n(.*?)<!-- zk-required-extras:end -->", re.S)


@pytest.fixture(scope="module")
def required_fields() -> dict:
    spec = importlib.util.spec_from_file_location("odin_brain_health_mod", HEALTH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["odin_brain_health_mod"] = mod
    spec.loader.exec_module(mod)
    return mod.REQUIRED_FIELDS


@pytest.fixture(scope="module")
def reference() -> str:
    return REF.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def base_fields(reference) -> set[str]:
    m = BASE_BLOCK.search(reference)
    assert m, "the /zk add note template block is no longer parseable"
    return {line.split(":", 1)[0].strip()
            for line in m.group(1).splitlines() if ":" in line}


@pytest.fixture(scope="module")
def extras(reference) -> dict[str, set[str]]:
    """Parse the per-destination extras table into {brain_kind: {fields}}."""
    m = EXTRAS_BLOCK.search(reference)
    assert m, (
        "the zk-required-extras table is missing from the /zk reference. It is "
        "what tells the skill which fields each brain directory demands."
    )
    parsed: dict[str, set[str]] = {}
    for row in m.group(1).splitlines():
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0].startswith("-") or cells[1] == "Brain kind":
            continue
        kind = cells[1]
        if cells[2].lower() == "none":
            parsed[kind] = set()
        else:
            parsed[kind] = set(re.findall(r"`([^`]+)`", cells[2]))
    return parsed


# --- the two lists must cover the same set of note kinds ----------------------

def test_the_table_covers_every_brain_kind(extras, required_fields):
    assert set(extras) == set(required_fields), (
        f"the /zk extras table covers {sorted(extras)} but the brain schema "
        f"defines {sorted(required_fields)}. A kind present in one and not the "
        "other is a note type that can never be written correctly."
    )


# --- base plus extras must satisfy every schema -------------------------------

@pytest.mark.parametrize("kind", ["source", "principle", "position",
                                  "episode", "conflict", "reference"])
def test_base_plus_extras_satisfies_the_schema(kind, base_fields, extras,
                                               required_fields):
    produced = base_fields | extras.get(kind, set())
    missing = [f for f in required_fields[kind] if f not in produced]
    assert not missing, (
        f"a '{kind}' note written from the /zk template would be missing "
        f"{missing}, so odin-brain-health.py flags it as a schema violation the "
        "moment it is created."
    )


# --- the extras must be needed, not decoration --------------------------------

@pytest.mark.parametrize("kind", ["source", "principle", "position",
                                  "episode", "conflict", "reference"])
def test_no_extra_field_is_invented(kind, base_fields, extras, required_fields):
    """An extra the schema does not ask for is drift in the other direction."""
    unnecessary = sorted(f for f in extras.get(kind, set())
                         if f not in required_fields[kind])
    assert not unnecessary, (
        f"the table asks a '{kind}' note for {unnecessary}, which "
        "odin-brain-health.py does not require. Either the schema changed and "
        "the table was not updated, or the field is decoration."
    )


def test_no_extra_duplicates_a_base_field(base_fields, extras):
    for kind, fields in extras.items():
        overlap = sorted(fields & base_fields)
        assert not overlap, (
            f"'{kind}' lists {overlap} as an extra, but the base template "
            "already writes it. A duplicated key in YAML frontmatter is a "
            "silent overwrite."
        )


# --- the step-6 annotation must land in a real field --------------------------

def test_the_format_annotation_step_6_prescribes_is_a_real_source_field(
        extras, required_fields):
    """Step 6 says to set `format: fleeting` and friends. That was the audit's
    finding: the field had nowhere to go. It has to be a genuine source field,
    or step 6 should stop prescribing it."""
    assert "format" in required_fields["source"]
    assert "format" in extras["source"]
