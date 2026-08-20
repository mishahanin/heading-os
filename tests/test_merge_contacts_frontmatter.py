#!/usr/bin/env python3
"""`merge-contacts.py` must not lose a multi-line YAML list when it writes back.

The parser reads the frontmatter line by line and skips any line with no colon
in it. A block list is exactly that shape:

    relevant_principles:
      - channel-is-structural-in-cybersecurity
      - brokers-open-the-door-principals-close-the-distance

The key line carries an empty value, the three item lines carry no colon, and
the paired serializer then writes `relevant_principles:` with nothing after it.
The items are gone, in a path that writes over a real CRM record.

Measured 2026-08-20 against the live overlay: 7 of 326 records hold a block
list, the largest carrying 3 items. Any merge onto one of them dropped the list
silently, because the merge reports which FIELDS changed and this field still
existed. The records are not named here - the engine is public and a CRM slug is
real-entity content the `content-guard-31c` gate refuses. The tests below find
them by shape instead, which is also what keeps them true after the data moves.

The parser and the serializer are a pair, which is why this is not fixed by
swapping in `scripts.utils.markdown.parse_frontmatter`: that one returns native
types the naive serializer cannot write back (see the docstring on
`parse_frontmatter` in the script). So the pair is repaired together, and the
round-trip is what these tests hold. Flow lists keep their flow bytes and block
lists keep their block bytes, because a merge that reformats a field it was not
asked to touch is its own defect.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "merge-contacts.py"


@pytest.fixture(scope="module")
def mc():
    spec = importlib.util.spec_from_file_location("merge_contacts", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BLOCK = (
    "---\n"
    "name: Dana Okonkwo\n"
    "relevant_principles:\n"
    "  - channel-is-structural-in-cybersecurity\n"
    "  - brokers-open-the-door-principals-close-the-distance\n"
    "  - partner-services-ratio-drives-investment\n"
    "tier: 2\n"
    "---\n"
    "\n## Notes\n"
)


def test_a_block_list_survives_the_parse(mc):
    fm, _body = mc.parse_frontmatter(BLOCK)
    assert fm["relevant_principles"] == [
        "channel-is-structural-in-cybersecurity",
        "brokers-open-the-door-principals-close-the-distance",
        "partner-services-ratio-drives-investment",
    ]


def test_the_keys_around_a_block_list_are_still_read(mc):
    """The item lines must be consumed, never treated as new keys."""
    fm, _body = mc.parse_frontmatter(BLOCK)
    assert fm["name"] == "Dana Okonkwo"
    assert fm["tier"] == "2"
    assert list(fm) == ["name", "relevant_principles", "tier"]


def test_a_block_list_round_trips_to_block_bytes(mc):
    """Round-trip identity is the property that matters: a merge that touches
    one field must not silently restyle another."""
    fm, _body = mc.parse_frontmatter(BLOCK)
    out = mc.serialize_frontmatter(fm)
    assert out == BLOCK[: BLOCK.index("---\n", 4) + 4]


def test_a_flow_list_still_round_trips_to_flow_bytes(mc):
    """The 165 contacts that use `tags: [a, b]` must not be reformatted."""
    flow = "---\nname: X\ntags: [ai, telco]\n---\n"
    fm, _body = mc.parse_frontmatter(flow)
    assert fm["tags"] == ["ai", "telco"]
    assert mc.serialize_frontmatter(fm) == flow


def test_an_empty_key_is_still_an_empty_string(mc):
    """A key with no value and no list under it keeps its old meaning."""
    fm, _body = mc.parse_frontmatter("---\nname: X\nnotes:\ntier: 2\n---\n")
    assert fm["notes"] == ""
    assert fm["tier"] == "2"


def test_a_block_list_at_its_key_s_own_column_round_trips(mc):
    """YAML allows the items at zero indent. One live record uses that form."""
    flat = "---\nstatus: active\ntags:\n- softlist\n- uzbekistan\ntimezone: Europe/Warsaw\n---\n"
    fm, _body = mc.parse_frontmatter(flat)
    assert fm["tags"] == ["softlist", "uzbekistan"]
    assert fm["timezone"] == "Europe/Warsaw"
    assert mc.serialize_frontmatter(fm) == flat


def test_quotes_survive_on_a_scalar_and_on_a_list_item(mc):
    """`x: "2026-10-01"` is a string; `x: 2026-10-01` is a date. Not the same
    document, and a merge must not change one into the other."""
    quoted = (
        "---\n"
        'freeze_until: "2026-10-01"\n'
        "other_emails:\n"
        '  - "seb.mueller@example.com"\n'
        'phone: ""\n'
        "---\n"
    )
    fm, _body = mc.parse_frontmatter(quoted)
    assert fm["freeze_until"] == "2026-10-01"
    assert fm["other_emails"] == ["seb.mueller@example.com"]
    assert fm["phone"] == ""
    assert mc.serialize_frontmatter(fm) == quoted


def test_every_live_crm_record_round_trips_byte_for_byte(mc):
    """The corpus proof. Parse then serialize must be the identity on all of
    it, because `merge-contacts` rewrites the whole file, not the one field it
    was asked to merge. Measured 2026-08-20: 182 of 326 records failed this
    before the repair, 0 after."""
    from scripts.utils.workspace import get_data_root

    crm = Path(get_data_root()) / "crm"
    if not crm.exists():
        pytest.skip("private data overlay not present")

    records = sorted(crm.glob("contacts/*.md")) + sorted(crm.glob("address-book/*.md"))
    if not records:
        pytest.skip("no CRM records on disk")

    damaged = []
    for f in records:
        text = f.read_text(encoding="utf-8")
        fm, body = mc.parse_frontmatter(text)
        if not fm:
            continue
        # Mirrors the production join exactly (merge-contacts.py, "Merge"
        # block). A test that reassembles differently from the writer measures
        # its own arithmetic, not the tool's.
        if mc.serialize_frontmatter(fm) + body != text:
            damaged.append(f.name)

    assert not damaged, f"{len(damaged)} of {len(records)} rewritten: {damaged[:8]}"


@pytest.mark.parametrize("gap,label", [
    ("", "no blank line after the frontmatter"),
    ("\n", "one blank line"),
    ("\n\n", "two blank lines"),
])
def test_the_gap_after_the_frontmatter_is_preserved(mc, gap, label):
    """The tool merges one field. Everything else must come back byte-identical,
    including whitespace it was never asked about.

    Until 2026-08-20 the closing `---\\s*\\n` swallowed every following blank
    line and the writer put exactly one back. A record with one blank line
    survived; a record with none gained one, and a record with two lost one.
    All 326 of the operator's records carry exactly one, so the corpus test
    above could not see it. `examples/crm/contacts/EXAMPLE-contact.md` carries
    none, which is how CI found it while every local run stayed green.
    """
    text = f"---\nname: Example\ntier: prospect\n---\n{gap}Body line one.\n"
    fm, body = mc.parse_frontmatter(text)
    assert fm, f"{label}: frontmatter did not parse"
    assert mc.serialize_frontmatter(fm) + body == text, (
        f"{label}: the round trip changed the file"
    )


def test_every_live_block_list_reads_back_as_a_list(mc):
    """Finds the records by SHAPE, never by slug.

    A slug is real-entity content and this repository is public, so the test
    looks for the pattern that broke rather than for the record that carried
    it. That also survives the data moving, which a hardcoded filename does
    not."""
    import re

    from scripts.utils.workspace import get_data_root

    crm = Path(get_data_root()) / "crm"
    if not crm.exists():
        pytest.skip("private data overlay not present")

    block_key = re.compile(r"^([\w-]+):[ \t]*$\n(?:^[ \t]*-[ \t].*$\n?)+", re.M)
    checked = 0
    for f in sorted(crm.glob("contacts/*.md")) + sorted(crm.glob("address-book/*.md")):
        text = f.read_text(encoding="utf-8")
        head = text.partition("\n---\n")[0]
        keys = [m.group(1) for m in block_key.finditer(head + "\n")]
        if not keys:
            continue
        fm, _body = mc.parse_frontmatter(text)
        for key in keys:
            assert isinstance(fm.get(key), list) and fm[key], (
                f"{f.name}: {key} parsed as {fm.get(key)!r}, not a list"
            )
            checked += 1

    if not checked:
        pytest.skip("no live record uses a block list")
