#!/usr/bin/env python3
"""Three defects in `scripts/utils/docx_font_embed.py`, all on the re-embed path.

The module is built to be run twice over the same .docx: `_build_font_rels`
strips prior font Relationships, `_patch_font_table` removes prior family
blocks, `_patch_content_types` replaces an existing ttf Default. Every XML layer
was made idempotent. The three below were not.

1. The binary parts. `font_counter` restarts at 1 on every call, so the copy
   loop carried the previous run's `word/fonts/font1.ttf` forward and the plan
   loop then wrote a second entry under the identical name. `zipfile` allows
   duplicate names with only a `UserWarning`, so nothing failed - a consumer
   that resolves the first match simply read the STALE font.
2. The settings elements. `w:embedTrueTypeFonts`, `w:embedSystemFonts` and
   `w:saveSubsetFonts` were appended before `</w:settings>`, past the end of an
   xsd:sequence they belong near the head of.
3. The dedupe regex. It required `w:name` to be the opening tag's first and
   only attribute, so any other producer's spelling slipped past the removal and
   the family ended up declared twice.

Everything here is package surgery on real python-docx output. The embedded
bytes need not be a real font for the packaging to be checkable.
"""

import warnings
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("docx")  # F-7.1: the documents extra, absent on a core clone

from docx import Document  # noqa: E402

from scripts.utils.docx_font_embed import (  # noqa: E402
    FontWeights,
    _patch_font_table,
    _patch_settings,
    embed_fonts,
)

# Invented brand names. Engine law: no real entity may appear in this repo.
FAMILY = "Bond Sans"
OTHER_FAMILY = "Moneypenny Serif"

# The three flags, in their ECMA-376 CT_Settings sequence order. Spelled out
# here rather than imported: importing the module's own tuple would make the
# test agree with the code by construction, and would make this file
# uncollectable against a build that does not define it.
EMBED_SETTINGS = ("w:embedTrueTypeFonts", "w:embedSystemFonts", "w:saveSubsetFonts")


def _fake_ttf(path: Path, marker: bytes) -> Path:
    # 32+ bytes so the obfuscation pass has a full key window to chew on, and
    # the marker repeated PAST that window so two fixtures stay distinguishable
    # after the first 32 bytes are XORed.
    path.write_bytes(marker + bytes(range(64)) + marker)
    return path


def _blank_docx(path: Path) -> Path:
    Document().save(str(path))
    return path


# ------------------------------------------------------------------
# 1. duplicate + orphaned font parts on re-embed
# ------------------------------------------------------------------

def test_re_embedding_leaves_exactly_one_part_per_font(tmp_path):
    docx = _blank_docx(tmp_path / "brief.docx")
    old = _fake_ttf(tmp_path / "old.ttf", b"OLD-")
    new = _fake_ttf(tmp_path / "new.ttf", b"NEW-")

    embed_fonts(docx, {FAMILY: FontWeights(regular=old)})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        embed_fonts(docx, {FAMILY: FontWeights(regular=new)})

    names = zipfile.ZipFile(docx).namelist()
    font_parts = [n for n in names if n.startswith("word/fonts/")]
    assert font_parts, "no font part was written at all - the fixture is broken"
    assert len(font_parts) == len(set(font_parts)), (
        f"duplicate zip entries: {sorted(font_parts)}"
    )
    assert not [str(w.message) for w in caught if "Duplicate name" in str(w.message)]


def test_the_second_run_is_the_font_a_reader_gets(tmp_path):
    """The stale-bytes half of the defect, which the name count alone misses."""
    docx = _blank_docx(tmp_path / "brief.docx")
    old = _fake_ttf(tmp_path / "old.ttf", b"OLD-")
    new = _fake_ttf(tmp_path / "new.ttf", b"NEW-")

    embed_fonts(docx, {FAMILY: FontWeights(regular=old)})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        embed_fonts(docx, {FAMILY: FontWeights(regular=new)})

    with zipfile.ZipFile(docx) as zf:
        parts = [n for n in zf.namelist() if n.startswith("word/fonts/")]
        assert len(parts) == 1, parts
        # zipfile.read resolves the LAST entry of a duplicated name; a consumer
        # walking the central directory forward resolves the first. Assert on
        # the forward walk, which is where the stale bytes surfaced.
        infos = [i for i in zf.infolist() if i.filename == parts[0]]
        blob = zf.read(infos[0])
    # Obfuscation XORs only the first 32 bytes, so compare on the tail, which
    # survives verbatim and carries the fixture's marker.
    assert old.read_bytes()[32:] != new.read_bytes()[32:], "fixtures are indistinguishable"
    assert blob[32:] == new.read_bytes()[32:]


def test_a_thinner_re_embed_drops_the_surplus_parts(tmp_path):
    """Two weights in, one weight out: the second part must not be orphaned."""
    docx = _blank_docx(tmp_path / "brief.docx")
    reg = _fake_ttf(tmp_path / "reg.ttf", b"REG-")
    bold = _fake_ttf(tmp_path / "bold.ttf", b"BLD-")

    embed_fonts(docx, {FAMILY: FontWeights(regular=reg, bold=bold)})
    first = [n for n in zipfile.ZipFile(docx).namelist() if n.startswith("word/fonts/")]
    assert len(first) == 2, first

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        embed_fonts(docx, {FAMILY: FontWeights(regular=reg)})

    second = [n for n in zipfile.ZipFile(docx).namelist() if n.startswith("word/fonts/")]
    assert len(second) == 1, f"orphaned font part left behind: {sorted(second)}"


# ------------------------------------------------------------------
# 2. settings.xml sequence position
# ------------------------------------------------------------------

def _tail_of_ct_settings(xml: str) -> list[str]:
    """Tags in a stock settings.xml that must FOLLOW the three embed flags."""
    return [t for t in ("w:proofState", "w:defaultTabStop", "w:compat",
                        "w:decimalSymbol", "w:listSeparator") if f"<{t}" in xml]


def test_the_embed_flags_land_before_the_tail_of_the_settings_sequence(tmp_path):
    docx = _blank_docx(tmp_path / "brief.docx")
    embed_fonts(docx, {FAMILY: FontWeights(regular=_fake_ttf(tmp_path / "f.ttf", b"F---"))})

    xml = zipfile.ZipFile(docx).read("word/settings.xml").decode("utf-8")
    successors = _tail_of_ct_settings(xml)
    assert successors, "stock settings.xml carried none of the tail tags - fixture broken"

    for tag in EMBED_SETTINGS:
        at = xml.find(f"<{tag}")
        assert at != -1, f"{tag} was never inserted"
        for later in successors:
            assert at < xml.find(f"<{later}"), (
                f"{tag} was emitted after {later}, which must follow it in CT_Settings"
            )


def test_a_partially_patched_settings_block_keeps_the_three_in_order():
    """`w:embedSystemFonts` already present: the other two still sort around it."""
    xml = (
        '<w:settings xmlns:w="ns"><w:zoom w:percent="100"/>'
        '<w:embedSystemFonts/><w:listSeparator w:val=","/></w:settings>'
    )
    out = _patch_settings(xml)
    positions = [out.find(f"<{tag}") for tag in EMBED_SETTINGS]
    assert all(p != -1 for p in positions), out
    assert positions == sorted(positions), out
    assert out.find("<w:zoom") < positions[0] < out.find("<w:listSeparator")
    assert out.count("<w:embedSystemFonts") == 1


def test_patching_settings_twice_changes_nothing():
    xml = (
        '<w:settings xmlns:w="ns"><w:zoom w:percent="100"/>'
        '<w:listSeparator w:val=","/></w:settings>'
    )
    once = _patch_settings(xml)
    assert _patch_settings(once) == once


def test_a_settings_part_with_no_closing_tag_is_refused():
    with pytest.raises(ValueError):
        _patch_settings('<w:settings xmlns:w="ns"><w:zoom w:percent="100"/>')


# ------------------------------------------------------------------
# 3. the family-dedupe regex
# ------------------------------------------------------------------

PLAN = [(FAMILY, "embedRegular", "{GUID}", b"", "rIdFont100", "font1.ttf")]


@pytest.mark.parametrize("existing", [
    f'<w:font w:name="{FAMILY}"><w:altName w:val="X"/></w:font>',
    f'<w:font w:name="{FAMILY}" w:charset="00"><w:altName w:val="X"/></w:font>',
    f'<w:font w:panose1="0" w:name="{FAMILY}"><w:altName w:val="X"/></w:font>',
    f'<w:font w:name="{FAMILY}" w:charset="00"/>',
    f'<w:font w:name="{FAMILY}"/>',
])
def test_every_spelling_of_a_prior_family_block_is_removed(existing):
    xml = f"<w:fonts>{existing}</w:fonts>"
    out = _patch_font_table(xml, PLAN)
    assert out.count(f'w:name="{FAMILY}"') == 1, out


def test_removing_one_family_leaves_its_neighbours_intact():
    xml = (
        "<w:fonts>"
        f'<w:font w:name="{FAMILY}" w:charset="00"/>'
        f'<w:font w:name="{OTHER_FAMILY}"><w:charset w:val="00"/></w:font>'
        "</w:fonts>"
    )
    out = _patch_font_table(xml, PLAN)
    assert out.count(f'w:name="{OTHER_FAMILY}"') == 1, out
    assert "<w:charset w:val=\"00\"/></w:font>" in out
    assert out.count(f'w:name="{FAMILY}"') == 1, out


def test_a_font_table_with_no_closing_tag_is_refused():
    with pytest.raises(ValueError):
        _patch_font_table("<w:fonts>", PLAN)
