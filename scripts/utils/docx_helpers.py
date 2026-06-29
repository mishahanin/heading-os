"""Shared DOCX generation utilities."""

from docx.oxml.ns import nsdecls
from docx.oxml import parse_xml


def set_cell_shading(cell, color_hex: str) -> None:
    """Set background color for a table cell."""
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{color_hex}" w:val="clear"/>')
    cell._tc.get_or_add_tcPr().append(shading)
