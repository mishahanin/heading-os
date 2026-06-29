"""Embed TrueType fonts into a DOCX file per ECMA-376 Part 1, 17.8.1.

Word obfuscates embedded fonts: the first 32 bytes of the font file are XORed
with a 16-byte key derived from a GUID. The GUID is stored in
``word/fontTable.xml`` as the ``w:fontKey`` attribute.

GUID-to-key byte order: the GUID string ``{AABBCCDD-EEFF-0011-2233-445566778899}``
is parsed to 16 bytes in string order, then REVERSED. That reversed sequence is
the XOR key applied across the first 32 bytes (key wraps after 16).

This module patches the .docx zip *as strings* — not via XML parsing — to
preserve every original namespace prefix exactly. Round-tripping through
ElementTree mangles ``xmlns:mc`` to ``xmlns:ns1`` and breaks Word's strict
schema check, surfaced as the "unreadable content" recovery dialog.

Public API: :func:`embed_fonts` and :class:`FontWeights`.
"""
from __future__ import annotations

import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path


FONT_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/font"
)
OBFUSCATED_FONT_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.obfuscatedFont"
)


@dataclass
class FontWeights:
    """File paths for a font family's weight variants. Each is a TTF path
    or ``None``. Only ``regular`` is required."""
    regular: Path
    bold: Path | None = None
    italic: Path | None = None
    bold_italic: Path | None = None


def _obfuscate(font_bytes: bytes, guid_str: str) -> bytes:
    """ECMA-376 Part 1, 17.8.1 obfuscation. XOR is symmetric: same function
    de-obfuscates."""
    cleaned = guid_str.replace("{", "").replace("}", "").replace("-", "")
    raw = bytes.fromhex(cleaned)
    if len(raw) != 16:
        raise ValueError(f"GUID must yield 16 bytes, got {len(raw)}")
    key = raw[::-1]
    out = bytearray(font_bytes)
    for i in range(min(32, len(out))):
        out[i] ^= key[i % 16]
    return bytes(out)


def _patch_font_table(xml: str, embed_plan: list) -> str:
    """Append a ``<w:font name="...">`` block per family before
    ``</w:fonts>``. If the family already has an entry, drop our additions and
    overwrite via a regex; this method appends fresh and lets duplicates be
    resolved by Word (last entry wins in practice for our inserted families).
    """
    by_family: dict[str, list] = {}
    for family, weight_attr, guid, _, rel_id, _ in embed_plan:
        by_family.setdefault(family, []).append((weight_attr, rel_id, guid))

    inserts = []
    for family, entries in by_family.items():
        # Remove any pre-existing <w:font name="<family>"> block so Word does
        # not see duplicate declarations.
        # Match opens like <w:font w:name="GT Standard"> ... </w:font>
        pat = re.compile(
            r'<w:font\s+w:name="' + re.escape(family) + r'"\s*>.*?</w:font>',
            re.DOTALL,
        )
        xml = pat.sub("", xml)

        embeds = "".join(
            f'<w:{attr} r:id="{rel_id}" w:fontKey="{guid}"/>'
            for attr, rel_id, guid in entries
        )
        inserts.append(
            f'<w:font w:name="{family}">'
            '<w:altName w:val="Calibri"/>'
            '<w:charset w:val="00"/>'
            '<w:family w:val="swiss"/>'
            '<w:pitch w:val="variable"/>'
            f'{embeds}'
            '</w:font>'
        )

    if "</w:fonts>" not in xml:
        raise ValueError("fontTable.xml is malformed - missing closing </w:fonts>")
    return xml.replace("</w:fonts>", "".join(inserts) + "</w:fonts>")


def _build_font_rels(embed_plan: list, existing_xml: str | None) -> str:
    """Build word/_rels/fontTable.xml.rels. If the part already exists,
    preserve any non-font Relationships and append the new font rels."""
    new_rels = "".join(
        f'<Relationship Id="{rel_id}" Type="{FONT_REL_TYPE}" '
        f'Target="fonts/{fname}"/>'
        for _, _, _, _, rel_id, fname in embed_plan
    )

    if existing_xml is None:
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/'
            'package/2006/relationships">'
            f'{new_rels}'
            '</Relationships>'
        )

    # Strip prior font Relationships so we do not double-register
    cleaned = re.sub(
        r'<Relationship\s+[^/>]*Type="' + re.escape(FONT_REL_TYPE) + r'"\s*[^/>]*/>',
        "",
        existing_xml,
    )
    if "</Relationships>" not in cleaned:
        raise ValueError(
            "fontTable.xml.rels malformed - missing </Relationships>"
        )
    return cleaned.replace("</Relationships>", new_rels + "</Relationships>")


def _patch_settings(xml: str) -> str:
    """Insert embedTrueTypeFonts / embedSystemFonts / saveSubsetFonts before
    </w:settings> if absent. The schema permits these at the end of the
    settings block."""
    insertions = []
    if "<w:embedTrueTypeFonts" not in xml:
        insertions.append("<w:embedTrueTypeFonts/>")
    if "<w:embedSystemFonts" not in xml:
        insertions.append("<w:embedSystemFonts/>")
    if "<w:saveSubsetFonts" not in xml:
        insertions.append("<w:saveSubsetFonts/>")
    if not insertions:
        return xml
    if "</w:settings>" not in xml:
        raise ValueError("settings.xml malformed - missing </w:settings>")
    return xml.replace("</w:settings>", "".join(insertions) + "</w:settings>")


def _patch_content_types(xml: str) -> str:
    """Ensure ``ttf`` extension is registered with the obfuscatedFont content
    type. Either replace an existing Default for ttf, or insert a new one
    after the opening ``<Types>`` tag."""
    new_default = (
        f'<Default Extension="ttf" ContentType="{OBFUSCATED_FONT_CONTENT_TYPE}"/>'
    )

    # Existing default for ttf - replace it
    existing_re = re.compile(
        r'<Default\s+Extension="ttf"[^/>]*/>',
        re.IGNORECASE,
    )
    if existing_re.search(xml):
        return existing_re.sub(new_default, xml, count=1)

    # No existing default - insert after the opening <Types ...> tag
    open_re = re.compile(r'(<Types\b[^>]*>)')
    if not open_re.search(xml):
        raise ValueError("[Content_Types].xml malformed - missing <Types>")
    return open_re.sub(r"\1" + new_default, xml, count=1)


def embed_fonts(docx_path: Path | str, fonts: dict[str, FontWeights]) -> None:
    """Embed font families into a DOCX in place.

    Parameters
    ----------
    docx_path
        Path to the .docx file. Modified in place.
    fonts
        Mapping of *font family name* (e.g. ``"GT Standard"``) to a
        :class:`FontWeights` describing TTF binaries for the regular / bold /
        italic / bold-italic variants.
    """
    docx_path = Path(docx_path)
    if not docx_path.exists():
        raise FileNotFoundError(docx_path)

    embed_plan: list[tuple] = []
    rel_counter = 100
    font_counter = 1
    for family, weights in fonts.items():
        for weight_attr, src in (
            ("embedRegular", weights.regular),
            ("embedBold", weights.bold),
            ("embedItalic", weights.italic),
            ("embedBoldItalic", weights.bold_italic),
        ):
            if src is None:
                continue
            ttf = Path(src).read_bytes()
            guid = "{" + str(uuid.uuid4()).upper() + "}"
            obf = _obfuscate(ttf, guid)
            rel_id = f"rIdFont{rel_counter}"
            fname = f"font{font_counter}.ttf"
            rel_counter += 1
            font_counter += 1
            embed_plan.append((family, weight_attr, guid, obf, rel_id, fname))

    tmp = docx_path.with_suffix(".docx.embed-tmp")

    with zipfile.ZipFile(docx_path, "r") as zin:
        names = zin.namelist()
        font_table_xml = zin.read("word/fontTable.xml").decode("utf-8")
        try:
            font_rels_xml = zin.read(
                "word/_rels/fontTable.xml.rels"
            ).decode("utf-8")
        except KeyError:
            font_rels_xml = None
        settings_xml = zin.read("word/settings.xml").decode("utf-8")
        ctypes_xml = zin.read("[Content_Types].xml").decode("utf-8")

        new_font_table = _patch_font_table(font_table_xml, embed_plan)
        new_font_rels = _build_font_rels(embed_plan, font_rels_xml)
        new_settings = _patch_settings(settings_xml)
        new_ctypes = _patch_content_types(ctypes_xml)

        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for n in names:
                if n == "word/fontTable.xml":
                    zout.writestr(n, new_font_table)
                elif n == "word/_rels/fontTable.xml.rels":
                    zout.writestr(n, new_font_rels)
                elif n == "word/settings.xml":
                    zout.writestr(n, new_settings)
                elif n == "[Content_Types].xml":
                    zout.writestr(n, new_ctypes)
                else:
                    zout.writestr(n, zin.read(n))

            if "word/_rels/fontTable.xml.rels" not in names:
                zout.writestr("word/_rels/fontTable.xml.rels", new_font_rels)

            for _, _, _, obf, _, fname in embed_plan:
                zout.writestr(f"word/fonts/{fname}", obf)

    shutil.move(str(tmp), str(docx_path))
