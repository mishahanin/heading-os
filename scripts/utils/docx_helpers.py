"""Shared DOCX generation utilities."""


def set_cell_shading(cell, color_hex: str) -> None:
    """Set background color for a table cell."""
    # Lazy docx import (F-2.1: this util must import pure so callers stay collectable).
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{color_hex}" w:val="clear"/>')
    cell._tc.get_or_add_tcPr().append(shading)
