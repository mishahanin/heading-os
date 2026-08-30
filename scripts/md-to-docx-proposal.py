#!/usr/bin/env python3
"""
Convert the National Programme DPI Proposal from markdown to a professional Word document.
Usage: python scripts/md-to-docx-proposal.py
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.venv_guard import ensure_venv  # noqa: E402

ensure_venv()
from scripts.utils.docx_helpers import (
    TBLBORDERS_SUCCESSORS,
    insert_in_order,
    load_docx,
    save_docx,
    set_cell_shading,
)
from scripts.utils.workspace import get_outputs_dir

INPUT_PATH = str(get_outputs_dir() / 'proposals' / '31C-National-Programme-DPI-Proposal-v1.md')
OUTPUT_PATH = str(get_outputs_dir() / 'proposals' / '31C-National-Programme-DPI-Proposal-v1.docx')

# docx names + brand colours are bound lazily (F-2.1: import stays pure).
Document = Pt = Inches = Cm = RGBColor = None
WD_ALIGN_PARAGRAPH = WD_TABLE_ALIGNMENT = WD_ORIENT = qn = nsdecls = parse_xml = None
BRAND_DARK = BRAND_ACCENT = BRAND_LIGHT = WHITE = None


def _ensure_docx():
    global Document, Pt, Inches, Cm, RGBColor
    global WD_ALIGN_PARAGRAPH, WD_TABLE_ALIGNMENT, WD_ORIENT, qn, nsdecls, parse_xml
    global BRAND_DARK, BRAND_ACCENT, BRAND_LIGHT, WHITE
    if Document is not None:
        return
    d = load_docx()
    Document, Pt, Inches, Cm, RGBColor = d.Document, d.Pt, d.Inches, d.Cm, d.RGBColor
    WD_ALIGN_PARAGRAPH, WD_TABLE_ALIGNMENT, WD_ORIENT = (
        d.WD_ALIGN_PARAGRAPH, d.WD_TABLE_ALIGNMENT, d.WD_ORIENT)
    qn, nsdecls, parse_xml = d.qn, d.nsdecls, d.parse_xml
    BRAND_DARK = RGBColor(0x1A, 0x1A, 0x2E)   # Dark navy
    BRAND_ACCENT = RGBColor(0x00, 0x7A, 0xCC)  # Blue accent
    BRAND_LIGHT = RGBColor(0x4A, 0x4A, 0x5A)   # Body text grey
    WHITE = RGBColor(0xFF, 0xFF, 0xFF)
TABLE_HEADER_BG = "007ACC"
TABLE_ALT_BG = "F2F7FC"

# Markdown heading depth -> Word outline level, one to one. `setup_styles`
# brands Heading 1 through Heading 4, which is every level `parse_markdown`
# emits.
HEADING_LEVELS = {'h1': 1, 'h2': 2, 'h3': 3, 'h4': 4}


def setup_styles(doc):
    """Configure document styles."""
    style = doc.styles['Normal']
    font = style.font
    font.name = 'Calibri'
    font.size = Pt(10.5)
    font.color.rgb = BRAND_LIGHT
    pf = style.paragraph_format
    pf.space_after = Pt(6)
    pf.line_spacing = 1.15

    # Heading 1
    h1 = doc.styles['Heading 1']
    h1.font.name = 'Calibri'
    h1.font.size = Pt(20)
    h1.font.color.rgb = BRAND_DARK
    h1.font.bold = True
    h1.paragraph_format.space_before = Pt(24)
    h1.paragraph_format.space_after = Pt(12)
    h1.paragraph_format.keep_with_next = True

    # Heading 2
    h2 = doc.styles['Heading 2']
    h2.font.name = 'Calibri'
    h2.font.size = Pt(15)
    h2.font.color.rgb = BRAND_ACCENT
    h2.font.bold = True
    h2.paragraph_format.space_before = Pt(18)
    h2.paragraph_format.space_after = Pt(8)
    h2.paragraph_format.keep_with_next = True

    # Heading 3
    h3 = doc.styles['Heading 3']
    h3.font.name = 'Calibri'
    h3.font.size = Pt(12)
    h3.font.color.rgb = BRAND_DARK
    h3.font.bold = True
    h3.paragraph_format.space_before = Pt(12)
    h3.paragraph_format.space_after = Pt(6)
    h3.paragraph_format.keep_with_next = True

    # Heading 4. Added with the heading-level remap in `build_document`: `####`
    # used to render as Heading 3 (styled here), and mapping it to its true
    # level 4 would otherwise have dropped it onto Word's unbranded default --
    # 11pt Calibri Light in the theme's accent blue, next to nothing else in
    # this document. Fixing the outline must not cost the brand.
    h4 = doc.styles['Heading 4']
    h4.font.name = 'Calibri'
    h4.font.size = Pt(11)
    h4.font.color.rgb = BRAND_LIGHT
    h4.font.bold = True
    h4.font.italic = False
    h4.paragraph_format.space_before = Pt(10)
    h4.paragraph_format.space_after = Pt(4)
    h4.paragraph_format.keep_with_next = True

    return doc



def format_table(table):
    """Apply professional formatting to a table."""
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True

    # Style header row
    if len(table.rows) > 0:
        for cell in table.rows[0].cells:
            set_cell_shading(cell, TABLE_HEADER_BG)
            for paragraph in cell.paragraphs:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
                for run in paragraph.runs:
                    run.font.color.rgb = WHITE
                    run.font.bold = True
                    run.font.size = Pt(9.5)
                    run.font.name = 'Calibri'

    # Style data rows
    for i, row in enumerate(table.rows[1:], 1):
        for cell in row.cells:
            if i % 2 == 0:
                set_cell_shading(cell, TABLE_ALT_BG)
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(9.5)
                    run.font.name = 'Calibri'
                    run.font.color.rgb = BRAND_LIGHT

    # Set borders
    tbl = table._tbl
    # `find`, not the `tblPr` property. python-docx declares `tblPr` as
    # OneAndOnlyOne, so on a table that has none the property RAISES
    # InvalidXmlError -- it never returns None. The old line read
    # `tbl.tblPr if tbl.tblPr is not None else parse_xml(...)`, which therefore
    # could not take its own fallback: the condition raised first. And had it
    # been reachable, the fallback element was never attached to the table, so
    # `tblPr.append(borders)` decorated a detached node and the table came out
    # with no borders and no error.
    #
    # UNREACHABLE, and more deeply than the audit that found it said: the
    # `table.alignment` assignment at the top of this function also writes into
    # `tblPr` and raises first. So no caller can reach the branch below through
    # `format_table`. It is kept rather than deleted because it is now correct
    # if the shape ever changes, and named here so nobody mistakes it for a
    # live guard or writes a test that cannot pass.
    tblPr = tbl.find(qn('w:tblPr'))
    if tblPr is None:
        tblPr = parse_xml(f'<w:tblPr {nsdecls("w")}/>')
        tbl.insert(0, tblPr)
    borders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        '  <w:top w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        '  <w:left w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        '  <w:bottom w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        '  <w:right w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        '  <w:insideH w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        '  <w:insideV w:val="single" w:sz="4" w:space="0" w:color="CCCCCC"/>'
        '</w:tblBorders>'
    )
    # python-docx puts `w:tblLook` into `tblPr` by itself, and `w:tblBorders`
    # belongs four places before it. So this append landed the borders last on
    # EVERY table in the proposal. `scripts/generate-odunone-docx.py` fixed the
    # same line in its own `add_table` and named the symptom there: "a table
    # with no borders at all". The comment above this block records an earlier,
    # different bug on the same three lines, which is how the second one kept
    # its cover.
    insert_in_order(tblPr, borders, TBLBORDERS_SUCCESSORS)


def add_cover_page(doc):
    """Add a professional cover page."""
    # Spacer
    for _ in range(6):
        doc.add_paragraph()

    # Company name
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run('31 CONCEPT')
    run.font.size = Pt(36)
    run.font.color.rgb = BRAND_DARK
    run.font.bold = True
    run.font.name = 'Calibri'

    # Separator line
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run('_' * 60)
    run.font.color.rgb = BRAND_ACCENT
    run.font.size = Pt(10)

    doc.add_paragraph()

    # Title
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run('ODUN.ONE Platform Response')
    run.font.size = Pt(22)
    run.font.color.rgb = BRAND_ACCENT
    run.font.bold = True
    run.font.name = 'Calibri'

    doc.add_paragraph()

    # Subtitle
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run('National Programme Digital Infrastructure\n(Telecom and ICT)\nGovernance & Cyber Security Roadmap')
    run.font.size = Pt(14)
    run.font.color.rgb = BRAND_DARK
    run.font.name = 'Calibri'

    for _ in range(4):
        doc.add_paragraph()

    # Metadata
    meta_items = [
        ('Prepared by:', '31 Concept (31C) -- Platform Vendor'),
        ('Document Version:', '1.0'),
        ('Date:', 'March 2, 2026'),
        ('Classification:', 'Confidential -- Partner Use Only'),
    ]
    for label, value in meta_items:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(label + ' ')
        run.font.size = Pt(10)
        run.font.color.rgb = BRAND_LIGHT
        run.font.bold = True
        run.font.name = 'Calibri'
        run = p.add_run(value)
        run.font.size = Pt(10)
        run.font.color.rgb = BRAND_LIGHT
        run.font.name = 'Calibri'

    # Page break
    doc.add_page_break()


_SEPARATOR_CELL = re.compile(r':?-+:?')


def _split_row(line):
    """Split one markdown table row into cells on its UNESCAPED pipes.

    `line.split('|')[1:-1]` was wrong twice over. It treated `\\|` -- the only
    way GFM gives you of putting a literal pipe inside a cell -- as a
    delimiter, so `| cost model \\| fixed | 100 | 200 |` yielded FOUR cells
    against a three-column header, every value shifted one column left and the
    renderer dropped the overflow: a priced row reached the client missing a
    number. And it discarded the first and last field unconditionally, which is
    only right when the row carries outer pipes. On the legal borderless form
    (`Phase | Weeks`) it deleted both edge columns, and on a lone `a | b` it
    returned nothing at all.

    An outer empty is dropped only where an outer pipe actually stands.
    """
    text = line.strip()
    cells, buf = [], []
    escaped = False
    ends_on_delimiter = False
    for ch in text:
        if escaped:
            # A backslash before anything GFM defines no escape for is literal
            # text, so a cell reading `C:\reports` survives intact.
            buf.append(ch if ch in '|\\' else '\\' + ch)
            escaped = False
            ends_on_delimiter = False
        elif ch == '\\':
            escaped = True
        elif ch == '|':
            cells.append(''.join(buf).strip())
            buf = []
            ends_on_delimiter = True
        else:
            buf.append(ch)
            ends_on_delimiter = False
    if escaped:
        buf.append('\\')
    cells.append(''.join(buf).strip())

    if text.startswith('|'):
        cells = cells[1:]
    if ends_on_delimiter and cells and cells[-1] == '':
        cells = cells[:-1]
    return cells


def _is_separator_row(line, expected_cells=None):
    """True when `line` is a table's header separator, judged by SHAPE.

    The detector used to be `'---' in lines[i + 1]`, a substring search over
    the whole next line. Any body line holding a pipe whose successor carried
    three hyphens ANYWHERE -- an em dash typed as `---`, a horizontal rule, a
    phrase like `rehost --- replatform` -- was read as a table header, and the
    prose line was rewritten into a mangled one-cell table or dropped from the
    document with nothing said. A separator has a shape instead: between the
    pipes sit dashes, optional alignment colons, and nothing else.

    `expected_cells` applies GFM's other requirement, that the separator match
    the header's column count. Without it a bare `---` (one cell) would still
    capture a three-column prose line, since a horizontal rule and a
    single-column separator are spelled identically.
    """
    cells = _split_row(line)
    if not cells or not all(_SEPARATOR_CELL.fullmatch(c) for c in cells):
        return False
    return expected_cells is None or len(cells) == expected_cells


def parse_markdown(md_text):
    """Parse markdown into structured blocks."""
    blocks = []
    lines = md_text.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i]

        # Skip the cover page metadata (already handled)
        if line.startswith('# 31 Concept') or line.startswith('## National Programme') or line.startswith('**Prepared by') or line.startswith('**Document Version') or line.startswith('**Date:') or line.startswith('**Classification'):
            i += 1
            continue

        # Skip TOC
        if line.strip().startswith('[') and '](#' in line:
            i += 1
            continue

        # Skip horizontal rules
        if line.strip() == '---':
            i += 1
            continue

        # Skip the "Table of Contents" heading
        if line.strip() == '## Table of Contents':
            i += 1
            continue

        # Headings
        if line.startswith('# '):
            # Without this branch an H1 fell through to the paragraph handler
            # and rendered as 10.5pt body text WITH ITS `# ` STILL ATTACHED --
            # no heading style, no navigation-pane entry, no TOC. The cover
            # page's own H1 is skipped above, so only other H1s reach here.
            blocks.append(('h1', line[2:].strip()))
            i += 1
            continue
        if line.startswith('## '):
            blocks.append(('h2', line[3:].strip()))
            i += 1
            continue
        if line.startswith('### '):
            blocks.append(('h3', line[4:].strip()))
            i += 1
            continue
        if line.startswith('#### '):
            blocks.append(('h4', line[5:].strip()))
            i += 1
            continue

        # Tables
        if ('|' in line and i + 1 < len(lines)
                and _is_separator_row(lines[i + 1], len(_split_row(line)))):
            table_lines = []
            while i < len(lines) and '|' in lines[i]:
                table_lines.append(lines[i])
                i += 1
            # Parse table
            rows = []
            for tl in table_lines:
                # A separator row is one whose EVERY cell is dashes and colons.
                # The test used to be `'---' in tl` against the whole raw line,
                # so a data row holding an em dash written as `---`, or a value
                # like `rehost --- replatform`, was dropped from the proposal
                # without a word. No column count here: the block is already
                # established as a table, and a ragged separator is still a
                # separator.
                if _is_separator_row(tl):
                    continue
                cells = _split_row(tl)
                if cells:
                    rows.append(cells)
            if rows:
                blocks.append(('table', rows))
            continue

        # Bullet points
        if line.strip().startswith('- **') or line.strip().startswith('- '):
            bullet_lines = []
            # RAW lines, not stripped. `build_document` decides sub-bullet vs
            # top-level by leading spaces, and every line was handed to it
            # already stripped -- so `List Bullet 2` was unreachable and every
            # nested bullet in a client-facing proposal rendered flat.
            #
            # The second half of the condition tests the RAW line too. It used
            # to be `lines[i].strip().startswith('  ')`, which no stripped
            # string can ever satisfy, so an indented continuation line of a
            # wrapped bullet was never collected: it fell out to the main loop
            # and came back as a standalone body paragraph in the middle of the
            # list.
            while i < len(lines) and (
                    lines[i].lstrip().startswith('- ')
                    or (lines[i].startswith('  ') and lines[i].strip())):
                bullet_lines.append(lines[i].rstrip())
                i += 1
            blocks.append(('bullets', bullet_lines))
            continue

        # Regular paragraph
        if line.strip():
            para_lines = [line.strip()]
            i += 1
            # Collect continuation lines (but stop at headings, tables, bullets, blank lines)
            while i < len(lines):
                next_line = lines[i]
                if not next_line.strip():
                    break
                if next_line.startswith('#') or next_line.startswith('|') or next_line.strip().startswith('- '):
                    break
                para_lines.append(next_line.strip())
                i += 1
            blocks.append(('para', ' '.join(para_lines)))
            continue

        i += 1

    return blocks


_INLINE_TOKEN = re.compile(
    r'\[([^\]]+)\]\([^)]*\)'    # 1: link -> keep the text, drop the target
    r'|\*\*\*(.+?)\*\*\*'       # 2: bold italic -- MUST precede the `**` case
    r'|\*\*(.+?)\*\*'           # 3: bold
    r'|\*(.+?)\*'               # 4: italic
)


def _add_run(paragraph, text, bold, italic):
    """One run, with the emphasis its enclosing markers asked for."""
    if not text:
        return
    run = paragraph.add_run(text)
    if bold:
        run.bold = True
    if italic:
        run.italic = True


def add_rich_text(paragraph, text, bold=False, italic=False):
    """Render inline markdown into `paragraph` as formatted runs.

    ONE routine for every path that emits text -- headings, paragraphs,
    bullets and table cells. The four used to disagree about what "inline
    markdown" even means. Headings ran the link regex alone, so a heading
    written `## **Scope**` reached the client with its asterisks showing.
    Table cells stripped `**bold**` but not `*italic*`, and stripped rather
    than rendered it, so a cell lost its emphasis entirely. Only body
    paragraphs produced real runs. Every path was some author's idea of
    "clean up the markdown", and each new one arrived with a new subset;
    sharing the routine retires the whole class rather than the three
    instances of it that were measured.

    Ordering `***` ahead of `**` is the second fix. `re.split` on
    `(\\*\\*.*?\\*\\*)` matched `***critical***` as `***critical**` plus a
    stray `*`: `part[2:-2]` then bolded a LEADING ASTERISK, and the closing
    pair either migrated into the following text or, at the end of a line,
    became an empty italic run and was deleted outright.

    `bold` and `italic` carry an enclosing marker's state into the recursive
    call, which is what makes `**bold with *emphasis* inside**` and a link
    inside emphasis come out right. Each recursion consumes its delimiters,
    so the text strictly shrinks and the descent terminates.
    """
    pos = 0
    for match in _INLINE_TOKEN.finditer(text):
        if match.start() > pos:
            _add_run(paragraph, text[pos:match.start()], bold, italic)
        link, bold_italic, strong, emphasis = match.groups()
        if link is not None:
            add_rich_text(paragraph, link, bold, italic)
        elif bold_italic is not None:
            add_rich_text(paragraph, bold_italic, True, True)
        elif strong is not None:
            add_rich_text(paragraph, strong, True, italic)
        else:
            add_rich_text(paragraph, emphasis, bold, True)
        pos = match.end()
    if pos < len(text):
        _add_run(paragraph, text[pos:], bold, italic)


def build_document():
    """Build the Word document from the markdown source."""
    _ensure_docx()
    with open(INPUT_PATH, 'r', encoding='utf-8') as f:
        md_text = f.read()

    doc = Document()
    setup_styles(doc)

    # Page setup
    section = doc.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.5)
    section.bottom_margin = Cm(2.5)
    section.left_margin = Cm(2.5)
    section.right_margin = Cm(2.5)

    # Add cover page
    add_cover_page(doc)

    # Parse and render content
    blocks = parse_markdown(md_text)

    for block_type, content in blocks:
        if block_type in HEADING_LEVELS:
            # One markdown level, one Word outline level. The four branches
            # here used to map h1->1, h2->1, h3->2, h4->3, so `#` and `##`
            # rendered as the SAME Heading 1: two distinct structural levels
            # collapsed into one, and Word's navigation pane and any generated
            # TOC lost the distinction with nothing said. The skew came from
            # this document's own shape -- its `#` title and `## National
            # Programme` subtitle are consumed by the cover page and skipped in
            # `parse_markdown`, which left `##` as the de-facto top level -- but
            # encoding one document's accidents into the converter is what made
            # every other heading level wrong.
            # Empty text, then the shared inline routine: `add_heading(text)`
            # would have dropped the raw markdown in as one literal run, which
            # is how the asterisks in `## **Scope**` reached the client.
            heading = doc.add_heading('', level=HEADING_LEVELS[block_type])
            add_rich_text(heading, content)

        elif block_type == 'table':
            rows = content
            if len(rows) < 1:
                continue
            # WIDEN, never drop. `num_cols` was `len(rows[0])` and the loop
            # below guarded with `if c_idx < num_cols`, so any row with more
            # cells than the header declared lost its tail cells in silence --
            # the exact failure `_split_row` was mis-splitting rows into.
            # With the splitter fixed a wider row now means a genuinely
            # malformed table, and the two honest options are to widen or to
            # report. Widening wins: this converter runs unattended on a
            # document bound for a client, so an empty cell in a header row is
            # a visible defect the operator can see and fix, while a deleted
            # price is not visible at all.
            num_cols = max(len(r) for r in rows)
            table = doc.add_table(rows=len(rows), cols=num_cols)
            for r_idx, row in enumerate(rows):
                for c_idx, cell_text in enumerate(row):
                    cell = table.cell(r_idx, c_idx)
                    p = cell.paragraphs[0]
                    add_rich_text(p, cell_text)
                    p.paragraph_format.space_after = Pt(2)
                    p.paragraph_format.space_before = Pt(2)
            format_table(table)
            doc.add_paragraph()  # Space after table

        elif block_type == 'bullets':
            last_p = None
            for bullet in content:
                stripped = bullet.strip()
                # Sub-bullet: indented, and still a bullet. `parse_markdown`
                # now hands these over with their indentation intact, which is
                # what makes this branch reachable at all.
                if bullet.startswith(' ') and stripped.startswith('- '):
                    # `stripped[2:]`, not `.lstrip('- ')`. lstrip takes a SET of
                    # characters, so a child item reading `-5% margin` came out
                    # as `5% margin`.
                    last_p = doc.add_paragraph(style='List Bullet 2')
                    add_rich_text(last_p, stripped[2:])
                elif stripped.startswith('- '):
                    last_p = doc.add_paragraph(style='List Bullet')
                    add_rich_text(last_p, stripped[2:])
                elif last_p is not None and stripped:
                    # A wrapped bullet's continuation line. This was `pass`, and
                    # unreachable besides, so such a line escaped the list
                    # entirely and came back as a body paragraph mid-list.
                    add_rich_text(last_p, ' ' + stripped)

        elif block_type == 'para':
            p = doc.add_paragraph()
            add_rich_text(p, content)

    # Closing line, NOT a page footer. These are ordinary body paragraphs, so
    # the line appears once, wherever the content happens to end, and not on
    # every page. The comment used to say "Footer", which is what
    # md-to-docx-competitive.py actually does via `section.footer`. Whether
    # this document should switch to a real repeating footer is a formatting
    # decision for the operator, not a silent change here.
    doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run('31 Concept | [HQ City 1] | [HQ City 2] | 31c.io')
    run.font.size = Pt(9)
    run.font.color.rgb = BRAND_ACCENT
    run.font.italic = True

    save_docx(doc, OUTPUT_PATH)
    print(f"Word document saved to: {OUTPUT_PATH}")


if __name__ == '__main__':
    build_document()
