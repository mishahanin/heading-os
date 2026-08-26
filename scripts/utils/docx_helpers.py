"""Shared DOCX generation utilities.

Tests: tests/test_a_data_root_override_that_was_silently_ignored.py, tests/test_docx_helpers.py
"""
import re
from functools import lru_cache
from pathlib import Path
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


BRAND_TEMPLATE_PREFIX = "31C - Master Template (New Identity "
_BRAND_VERSION_RE = re.compile(r"v(\d+)\.(\d+)\)")


def brand_master_template(suffix: str = ".dotx", *, templates_dir=None) -> Path:
    """The NEWEST brand master template with `suffix`, read from the datastore.

    A version number written into a filename and then spelled out at each call
    site is a trap that springs quietly. The master went from v1.00 to v1.01,
    and afterwards three places disagreed: `reference/corporate-style-guide.md`
    and `scripts/generate-odunone-docx.py` moved, `scripts/generate-usecases-
    docx.py` kept v1.00 and has raised `FileNotFoundError` on its first
    `shutil.copy2` every run since, and the one test that would have said so
    copied the same dead name, found nothing, and SKIPPED. Measured 2026-08-26,
    with only v1.01 present on this clone.

    Sorted on the PARSED version, never the string: `v1.10` must beat `v1.9`.

    Raises:
        FileNotFoundError: no template matches. The message lists what the
            directory does hold, because "template not found" sends the reader
            to the wrong question.
    """
    if templates_dir is None:
        from scripts.utils.workspace import get_datastore_dir

        templates_dir = get_datastore_dir() / "brand" / "templates"
    templates_dir = Path(templates_dir)

    found: list[tuple[tuple[int, int], Path]] = []
    for candidate in templates_dir.glob(f"{BRAND_TEMPLATE_PREFIX}*{suffix}"):
        match = _BRAND_VERSION_RE.search(candidate.name)
        if match:
            found.append(((int(match.group(1)), int(match.group(2))), candidate))
    if not found:
        try:
            present = sorted(p.name for p in templates_dir.iterdir())
        except OSError as exc:
            present = [f"<unreadable: {exc}>"]
        raise FileNotFoundError(
            f"no brand master template matching "
            f"'{BRAND_TEMPLATE_PREFIX}*{suffix}' in {templates_dir}; "
            f"it holds: {', '.join(present) or '(nothing)'}"
        )
    return max(found)[1]


def save_docx(doc, path) -> Path:
    """Write `doc` to `path`, creating the directories it needs first.

    `python-docx`'s `Document.save()` does not create missing parents, and
    seven generators under scripts/ called it on a path assembled from
    `get_outputs_dir()` with no `mkdir` anywhere. On a workspace where
    `outputs/documents/` happened to exist that is invisible; on a fresh
    clone, a new data overlay, or a leaf nobody has written yet, each one
    did the whole render and then died on its last line with
    `FileNotFoundError`, having produced nothing and having printed nothing
    about what it had built.

    It was invisible in the suite too, because `tests/test_docx_helpers.py`
    pre-created the leaves in its sandbox to match the assumption. That
    scaffolding is gone now: the generators create their own output
    directory, so the golden run proves the behaviour instead of standing
    in for it.

    Returns the resolved path so a caller can report what it wrote.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(target))
    return target


def set_cell_shading(cell, color_hex: str) -> None:
    """Set background color for a table cell."""
    # Lazy docx import (F-2.1: this util must import pure so callers stay collectable).
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{color_hex}" w:val="clear"/>')
    cell._tc.get_or_add_tcPr().append(shading)
