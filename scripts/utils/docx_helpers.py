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


_BRAND_VERSION_RE = re.compile(r"v(\d+)\.(\d+)\)")


def brand_template_prefix(manifest=None) -> str:
    """The filename stem the master templates share, up to the version marker.

    Derived from the manifest entry rather than written here. It used to be the
    module constant `BRAND_TEMPLATE_PREFIX`, spelling the real filename in a
    public repository; the operator ruled on 2026-09-02 that a datastore filename
    is private, and `scripts/utils/brand_assets.py` carries the reasoning. Cut at
    the version match, so the one registered name yields the glob that finds every
    version of it, which is what made this a prefix in the first place.

    Raises `BrandAssetError` when the manifest is absent (a public clone) or the
    registered name carries no version marker, because a prefix guessed from
    either would glob a directory this workspace does not have.
    """
    from scripts.utils.brand_assets import BrandAssetError, brand_asset_name

    name = brand_asset_name("word_master_template", manifest)
    match = _BRAND_VERSION_RE.search(name)
    if not match:
        raise BrandAssetError(
            f"the manifest entry for 'word_master_template' names {name!r}, "
            "which carries no 'v<major>.<minor>)' version marker, so no prefix "
            "can be cut from it and the newest-version lookup has nothing to "
            "glob for"
        )
    return name[: match.start()]


def brand_master_template(suffix: str = ".dotx", *, templates_dir=None,
                          prefix: str | None = None) -> Path:
    """The NEWEST brand master template with `suffix`, read from the datastore.

    `prefix` defaults to `brand_template_prefix()`, which reads the private
    manifest. It is a parameter so a unit test can drive the version-sort logic
    against an invented name in a scratch directory, with no data overlay and no
    real filename anywhere in the test file.

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
    if prefix is None:
        prefix = brand_template_prefix()

    found: list[tuple[tuple[int, int], Path]] = []
    for candidate in templates_dir.glob(f"{prefix}*{suffix}"):
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
            f"'{prefix}*{suffix}' in {templates_dir}; "
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


# ============================================================
# OOXML child ordering
# ============================================================
#
# `w:pPr`, `w:tcPr` and `w:tblPr` each have a FIXED child sequence in ECMA-376,
# and a child in the wrong position is schema-invalid: the consumer is free to
# ignore the element, so the border or shading the code just wrote may simply
# not appear.
#
# python-docx places a child correctly whenever it declares one, through
# `get_or_add_<tag>()`. It declares none for `w:pBdr`, `w:tcBorders` or
# `w:tblBorders` - the three elements the generators here need most - so every
# generator hand-rolled the insertion, and every one of them appended. Appending
# is right only while the container is empty of later siblings, which it usually
# is not: setting `space_before` first puts `w:spacing` in, python-docx puts
# `w:tblLook` in by itself, and shading a cell puts `w:shd` in.
#
# `scripts/generate-odunone-docx.py` found this twice and fixed it twice, in
# `add_bullet` and `add_table`, each with a comment naming the symptom. The rule
# never crossed to its four sibling generators. This is that rule, written once.
#
# The tuples are the tail of each container's sequence, copied from ECMA-376.
# `tests/test_a_rule_that_reached_one_of_six_generators.py` re-derives them from
# the installed python-docx and fails if they ever disagree, so a version bump
# cannot leave a stale copy here.
PBDR_SUCCESSORS = (
    "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap",
    "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN",
    "w:bidi", "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind",
    "w:contextualSpacing", "w:mirrorIndents", "w:suppressOverlap", "w:jc",
    "w:textDirection", "w:textAlignment", "w:textboxTightWrap", "w:outlineLvl",
    "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
)
TCBORDERS_SUCCESSORS = (
    "w:shd", "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText",
    "w:vAlign", "w:hideMark", "w:headers", "w:cellIns", "w:cellDel",
    "w:cellMerge", "w:tcPrChange",
)
TCSHD_SUCCESSORS = (
    "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign",
    "w:hideMark", "w:headers", "w:cellIns", "w:cellDel", "w:cellMerge",
    "w:tcPrChange",
)
TBLBORDERS_SUCCESSORS = (
    "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption",
    "w:tblDescription", "w:tblPrChange",
)


def insert_in_order(parent, element, successors):
    """Put `element` into `parent` before the first of `successors` present.

    Falls back to appending when none of them is there, which is what the
    callers were doing unconditionally.

    Returns `element`, so a caller can keep building it in one expression.
    """
    # Lazy docx import (F-2.1: this util must import pure so callers stay collectable).
    from docx.oxml.ns import qn

    for tag in successors:
        found = parent.find(qn(tag))
        if found is not None:
            found.addprevious(element)
            return element
    parent.append(element)
    return element


def set_cell_shading(cell, color_hex: str) -> None:
    """Set background color for a table cell.

    Through the funnel above rather than a bare append. `w:shd` sits at index 6
    of the `w:tcPr` sequence with eleven tags after it, so appending was correct
    only for a cell that carried none of them. Every current caller shades
    before setting anything later, so nothing was wrong on this path today; a
    cell given a vertical alignment first would have been.

    Any `w:shd` already on the cell is REPLACED, not joined. `CT_TcPr` allows
    one, and ordering the second one correctly still leaves two: measured
    2026-08-30, shading a cell red then green produced
    `<w:shd w:fill="FF0000"/><w:shd w:fill="00FF00"/>` in one `w:tcPr`, which is
    schema-invalid and leaves the winning colour to the consumer. No current
    caller shades a cell twice, so this is the same shape of latent defect the
    ordering rule above was written for.
    """
    # Lazy docx import (F-2.1: this util must import pure so callers stay collectable).
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls, qn
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{color_hex}" w:val="clear"/>')
    tc_pr = cell._tc.get_or_add_tcPr()
    for stale in tc_pr.findall(qn("w:shd")):
        tc_pr.remove(stale)
    insert_in_order(tc_pr, shading, TCSHD_SUCCESSORS)
