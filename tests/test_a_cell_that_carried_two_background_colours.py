#!/usr/bin/env python3
"""`set_cell_shading` in `scripts/utils/docx_helpers.py` joined, it did not replace.

`CT_TcPr` allows at most one `w:shd`. Shading a cell twice appended a second
one, so the cell carried `<w:shd w:fill="FF0000"/><w:shd w:fill="00FF00"/>` in a
single `w:tcPr` - schema-invalid, with the winning colour left to whatever the
consumer decides. Putting the second element in the RIGHT position, which the
`insert_in_order` funnel already did, does not help: two are still two.

No current caller shades the same cell twice, so this is latent - the same shape
as the ordering defect the funnel above it was written for, and found the same
way. No audit reported it.
"""

import pytest

pytest.importorskip("docx")  # F-7.1: the documents extra, absent on a core clone
pytest.importorskip("lxml")

from lxml import etree  # noqa: E402

from scripts.utils.docx_helpers import TCSHD_SUCCESSORS, load_docx, set_cell_shading  # noqa: E402

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _cell():
    docx = load_docx()
    return docx.Document().add_table(rows=1, cols=1).cell(0, 0)


def _shd_fills(cell):
    tc_pr = cell._tc.get_or_add_tcPr()
    return [e.get(f"{W}fill") for e in tc_pr.findall(f"{W}shd")]


def test_shading_a_cell_twice_leaves_one_shd():
    cell = _cell()
    set_cell_shading(cell, "FF0000")
    assert _shd_fills(cell) == ["FF0000"], "first shading did not land - fixture broken"
    set_cell_shading(cell, "00FF00")
    assert _shd_fills(cell) == ["00FF00"], etree.tostring(cell._tc.get_or_add_tcPr())


def test_the_replacement_still_lands_before_its_successors():
    """Replacing must not cost the ordering the funnel exists to give."""
    docx = load_docx()
    cell = _cell()
    tc_pr = cell._tc.get_or_add_tcPr()
    # w:vAlign is a successor of w:shd; put it in first so appending would be wrong.
    v_align = docx.parse_xml(f'<w:vAlign {docx.nsdecls("w")} w:val="center"/>')
    tc_pr.append(v_align)
    assert "w:vAlign" in TCSHD_SUCCESSORS

    set_cell_shading(cell, "FF0000")
    set_cell_shading(cell, "00FF00")

    tags = [etree.QName(e).localname for e in tc_pr]
    assert tags.count("shd") == 1, tags
    assert tags.index("shd") < tags.index("vAlign"), tags


def test_a_single_shading_is_unchanged():
    cell = _cell()
    set_cell_shading(cell, "0A1F3C")
    assert _shd_fills(cell) == ["0A1F3C"]
