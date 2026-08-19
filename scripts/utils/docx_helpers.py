"""Shared DOCX generation utilities."""
from functools import lru_cache
from types import SimpleNamespace


@lru_cache(maxsize=1)
def load_docx() -> SimpleNamespace:
    """Bind the python-docx symbols the generator scripts use, on first call.

    Eight scripts under scripts/ carried a private ``_ensure_docx()`` doing
    exactly this - 143 duplicated lines, measured 2026-08-20 - each one lazily
    importing python-docx and stamping the names into module globals. The
    laziness is the point and is preserved here: python-docx is the optional
    ``documents`` extra (F-2.1), so importing it at module scope would break
    collection on a clone that never installs it.

    Returns a namespace rather than a tuple so a caller binds only the names it
    needs and a new symbol costs one line here instead of eight signatures.
    """
    from scripts.utils.optdeps import require
    require("docx", extra="documents")
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement, parse_xml
    from docx.oxml.ns import nsdecls, qn
    from docx.shared import Cm, Emu, Inches, Pt, RGBColor
    return SimpleNamespace(
        Document=Document,
        Pt=Pt,
        Cm=Cm,
        Inches=Inches,
        Emu=Emu,
        RGBColor=RGBColor,
        WD_ALIGN_PARAGRAPH=WD_ALIGN_PARAGRAPH,
        WD_TABLE_ALIGNMENT=WD_TABLE_ALIGNMENT,
        WD_ORIENT=WD_ORIENT,
        qn=qn,
        nsdecls=nsdecls,
        parse_xml=parse_xml,
        OxmlElement=OxmlElement,
    )


def set_cell_shading(cell, color_hex: str) -> None:
    """Set background color for a table cell."""
    # Lazy docx import (F-2.1: this util must import pure so callers stay collectable).
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{color_hex}" w:val="clear"/>')
    cell._tc.get_or_add_tcPr().append(shading)
