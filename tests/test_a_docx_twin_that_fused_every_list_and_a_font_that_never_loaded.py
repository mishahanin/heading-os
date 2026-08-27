#!/usr/bin/env python3
"""Two defects in the one file that renders every external 31C document.

`scripts/utils/doctype_renderer.py` produces both halves of a corporate
document: the PDF, which is the locked-template render, and the DOCX, which is
the editable copy the counterparty actually receives and marks up.

**One.** `build_docx` carries a private four-line `strip_html` that converts
`<br>` and `</p><p>` to newlines, then deletes every remaining tag with NO
separator and no entity decoding. `scripts/utils/html_text.strip_html` is the
shared copy, was rewritten for exactly this failure, and its module docstring
says a new caller should import it rather than copy the logic. Measured
2026-08-27 on the same input:

    local : 'We propose:Module AModule BShared between Globex &amp; 31C.'
    shared: 'We propose:\\nModule A\\nModule B\\nShared between Globex & 31C.'

It is not letter-only. The same function feeds every DOCX body site, so
`/corporate-letter`, `/proposal`, `/partnership-doc` and `/official-doc` all
produced a DOCX whose list items, table cells and headings ran together into one
line, with `&amp;` left as four literal characters. The PDF was correct, because
`BODY_HTML` is substituted into the template raw, so nothing on screen showed
the operator what the recipient would open.

The shared stripper decodes entities, and `&nbsp;` decodes to U+00A0, which
`.claude/rules/hidden-chars.md` bans outright. The fix therefore runs
`sanitize_text.sanitize` over the result: adopting the shared parser without it
would trade a fused word for a banned character.

**Two.** `_resolve_brand_assets` looked the Cyrillic fallback fonts up under
`_fonts_dir(root) / "Inter"`, and `_fonts_dir` already ends in `GT Standard`.
Inter lives one level up, so the path named `.../fonts/GT Standard/Inter`, which
does not exist. `_embed_asset` returns `""` for a missing file without a word,
so both Inter faces embedded as `src: url("")` and the render exited 0. Measured
on the live workspace, 2026-08-27: GT Standard 246689 and 248853 bytes, both
Inter faces 0. `base.css` lists Inter first in the `[lang="ru"]` stack and GT
Standard has no Cyrillic glyphs, so every Russian run fell through to Segoe UI
or Arial at a heavier weight than the Latin column - which is verbatim the
outcome the comment above those two lines says the embed exists to prevent. The
operator writes in Russian daily.

Nothing under tests/ imported `doctype_renderer.build_docx` or
`_resolve_brand_assets` before this file.

Found by the engine defect hunt, 2026-08-27.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import doctype_renderer as DR  # noqa: E402

docx = pytest.importorskip("docx", reason="python-docx is an optional extra")


# ============================================================
# Fixtures: a synthetic brand tree, so CI needs no data overlay
# ============================================================

FONT_FILES = {
    "GT Standard/GT-Standard-M-Standard-Light.ttf": b"light-face",
    "GT Standard/GT-Standard-M-Standard-Medium.ttf": b"medium-face",
    "Inter/Inter-Light.ttf": b"inter-light-face",
    "Inter/Inter-Medium.ttf": b"inter-medium-face",
}

LOGO_FILES = (
    "31C_Logo_Palantinate_Blue_Color.png",
    "31C_Logo_White_Color.png",
    "31C_Logo_Black_Color.png",
)


@pytest.fixture()
def brand_root(tmp_path):
    """A workspace root whose `datastore/brand/` matches the real layout.

    `_resolve_under_corporate` tries `workspace_root / rel` first, so this tree
    wins over the machine's own overlay and the test measures the code rather
    than this laptop.
    """
    brand = tmp_path / "datastore" / "brand"
    for rel, payload in FONT_FILES.items():
        path = brand / "fonts" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    logos = brand / "assets" / "logos"
    logos.mkdir(parents=True, exist_ok=True)
    for name in LOGO_FILES:
        (logos / name).write_bytes(b"png-bytes")
    return tmp_path


def _paragraphs(path: Path) -> list[str]:
    from docx import Document

    return [p.text for p in Document(str(path)).paragraphs]


def _body(text: str) -> str:
    return text


LETTER = {
    "SENDER_NAME": "A Sender", "SENDER_TITLE": "CEO",
    "SENDER_EMAIL": "sender@example.test", "SENDER_PHONE": "+000",
    "RECIPIENT_NAME": "A Recipient", "RECIPIENT_TITLE": "CTO",
    "RECIPIENT_ORG": "Globex", "SUBJECT": "A subject",
    "DATE": "2026-08-27", "REF_ID": "REF-1", "SALUTATION": "<p>Dear A,</p>",
}

PARTNERSHIP = {
    "SUBTYPE": "MOU", "SUBJECT": "A partnership", "REF_ID": "REF-2",
    "EFFECTIVE_DATE": "2026-08-27", "TERM": "12 months",
    "PARTY_A_NAME": "31 Concept", "PARTY_A_SHORT": "31C",
    "PARTY_A_ENTITY_TYPE": "company", "PARTY_A_JURISDICTION": "Nowhere",
    "PARTY_A_ADDRESS": "1 Street", "PARTY_A_SIGNATORY_NAME": "A Sender",
    "PARTY_A_SIGNATORY_TITLE": "CEO",
    "PARTY_B_NAME": "Globex", "PARTY_B_SHORT": "Globex",
    "PARTY_B_ENTITY_TYPE": "company", "PARTY_B_JURISDICTION": "Elsewhere",
    "PARTY_B_ADDRESS": "2 Street", "PARTY_B_SIGNATORY_NAME": "A Recipient",
    "PARTY_B_SIGNATORY_TITLE": "CTO",
    "PURPOSE_HTML": "<p>Purpose.</p>", "SCOPE_HTML": "<p>Scope.</p>",
    "GOVERNANCE_HTML": "<p>Governance.</p>", "CLAUSES": [],
}

OFFICIAL = {
    "CLASS": "RESOLUTION", "REF_ID": "REF-3", "DATE": "2026-08-27",
    "PLACE": "Nowhere", "ISSUER_NAME": "A Sender", "ISSUER_TITLE": "CEO",
    "SUBJECT": "A resolution",
}


def _letter(tmp_path, body_html, **extra):
    data = dict(LETTER, BODY_HTML=body_html)
    data.update(extra)
    out = tmp_path / "letter.docx"
    DR.build_docx("letter", data, out, tmp_path)
    return _paragraphs(out)


# ============================================================
# The stripper: block elements must not fuse
# ============================================================

def test_list_items_do_not_fuse_into_one_line(tmp_path):
    """The headline defect. Three bullets became one unreadable run."""
    paras = _letter(tmp_path, "<p>We propose:</p><ul><li>Module A</li>"
                             "<li>Module B</li><li>Training</li></ul>")
    assert "Module A" in paras
    assert "Module B" in paras
    assert not any("Module AModule B" in p for p in paras), (
        f"the list items are still fused: {paras}")


def test_table_cells_do_not_fuse(tmp_path):
    paras = _letter(tmp_path, "<table><tr><td>Left</td><td>Right</td></tr></table>")
    assert not any("LeftRight" in p for p in paras), f"cells fused: {paras}"


def test_a_heading_does_not_run_into_the_text_below_it(tmp_path):
    paras = _letter(tmp_path, "<h3>Scope</h3><p>The first sentence.</p>")
    assert not any("ScopeThe first" in p for p in paras), f"fused: {paras}"


def test_an_entity_reaches_the_reader_as_its_character(tmp_path):
    """`&amp;` printed as four literal characters in a document sent to a client."""
    paras = _letter(tmp_path, "<p>Costs shared between Globex &amp; 31C.</p>")
    assert any("Globex & 31C" in p for p in paras), paras
    assert not any("&amp;" in p for p in paras), f"entity survived: {paras}"


def test_no_banned_hidden_character_enters_the_document(tmp_path):
    """The trap in adopting the shared parser.

    The shared stripper decodes entities, and `&nbsp;` decodes to U+00A0, which
    `.claude/rules/hidden-chars.md` bans in every generated artifact. Fixing the
    fusion without sanitising afterwards would swap one defect for another.
    """
    from scripts.utils.sanitize_text import SCANNED_CHARS

    paras = _letter(tmp_path, "<p>Costs rise 10&nbsp;percent&nbsp;this year.</p>")
    joined = "\n".join(paras)
    found = sorted({c for c in joined if c in SCANNED_CHARS})
    assert not found, f"hidden characters reached the DOCX: {[hex(ord(c)) for c in found]}"
    assert any("10 percent this year" in p for p in paras), paras


def test_paragraph_siblings_still_become_separate_paragraphs(tmp_path):
    """The regression guard: the one shape the old code did handle."""
    paras = _letter(tmp_path, "<p>First para.</p><p>Second para.</p>")
    assert "First para." in paras and "Second para." in paras


def test_a_br_still_breaks_the_line(tmp_path):
    paras = _letter(tmp_path, "<p>Line one<br>Line two</p>")
    assert not any("Line oneLine two" in p for p in paras), f"fused: {paras}"


def test_no_markup_survives_into_the_document(tmp_path):
    paras = _letter(tmp_path, "<p>Text with <strong>bold</strong> and "
                              "<a href='https://example.test'>a link</a>.</p>")
    joined = "\n".join(paras)
    assert "<" not in joined and ">" not in joined, f"markup survived: {joined!r}"
    assert any("bold" in p and "a link" in p for p in joined.split("\n")), paras


@pytest.mark.parametrize("doctype,data,field", [
    ("partnership", PARTNERSHIP, "PURPOSE_HTML"),
    ("partnership", PARTNERSHIP, "SCOPE_HTML"),
    ("partnership", PARTNERSHIP, "GOVERNANCE_HTML"),
    ("official", OFFICIAL, "PREAMBLE"),
    ("official", OFFICIAL, "CLOSING_HTML"),
])
def test_every_doctype_body_field_gets_the_same_treatment(tmp_path, doctype, data, field):
    """The defect was one shared helper, so the fix must be too.

    A per-call-site fix would leave whichever branch nobody thought to test.
    """
    payload = dict(data)
    payload[field] = "<ul><li>Alpha</li><li>Beta</li></ul>"
    out = tmp_path / f"{doctype}-{field}.docx"
    DR.build_docx(doctype, payload, out, tmp_path)
    joined = "\n".join(_paragraphs(out))
    assert "AlphaBeta" not in joined, f"{doctype}.{field} still fuses: {joined!r}"
    assert "Alpha" in joined and "Beta" in joined


def test_the_proposal_body_sections_do_not_fuse(tmp_path):
    proposal = {
        "SENDER_NAME": "A Sender", "SENDER_TITLE": "CEO",
        "SENDER_EMAIL": "sender@example.test", "SENDER_PHONE": "+000",
        "RECIPIENT_NAME": "A Recipient", "RECIPIENT_TITLE": "CTO",
        "RECIPIENT_ORG": "Globex", "RECIPIENT_COUNTRY": "Nowhere",
        "SUBJECT": "A proposal", "LEDE": "A lede", "DATE": "2026-08-27",
        "REF_ID": "REF-4",
        "EXECUTIVE_OPENING_HTML": "<ul><li>Alpha</li><li>Beta</li></ul>",
        "OPPORTUNITY_HTML": "<ul><li>Gamma</li><li>Delta</li></ul>",
        "SOLUTION_HTML": "<p>Solution.</p>", "PROOF_HTML": "<p>Proof.</p>",
        "COMMERCIAL_INTRO_HTML": "<p>Commercial.</p>",
        "NEXT_STEPS_HTML": "<ol><li>Sign</li><li>Deploy</li></ol>",
        "PRICING_LINES": [{"label": "Module A", "value": "EUR 412,000"}],
    }
    out = tmp_path / "proposal.docx"
    DR.build_docx("proposal", proposal, out, tmp_path)
    joined = "\n".join(_paragraphs(out))
    for fused in ("AlphaBeta", "GammaDelta", "SignDeploy"):
        assert fused not in joined, f"{fused} fused in the proposal: {joined!r}"


def test_a_clause_body_does_not_fuse(tmp_path):
    payload = dict(PARTNERSHIP, CLAUSES=[
        {"num": 1, "title": "Term", "body": "<ul><li>Alpha</li><li>Beta</li></ul>"}])
    out = tmp_path / "clauses.docx"
    DR.build_docx("partnership", payload, out, tmp_path)
    joined = "\n".join(_paragraphs(out))
    assert "AlphaBeta" not in joined, f"clause body fused: {joined!r}"


def test_a_whereas_clause_does_not_fuse(tmp_path):
    payload = dict(OFFICIAL, WHEREAS_CLAUSES=["<p>One</p><p>Two</p>"],
                   RESOLVED_BLOCKS=["<ul><li>Alpha</li><li>Beta</li></ul>"])
    out = tmp_path / "official.docx"
    DR.build_docx("official", payload, out, tmp_path)
    joined = "\n".join(_paragraphs(out))
    assert "OneTwo" not in joined and "AlphaBeta" not in joined, joined


def test_the_salutation_goes_through_the_same_helper(tmp_path):
    """The third copy, and the smallest.

    The salutation line stripped `<p>` and `</p>` by name with two `.replace`
    calls. That is the private stripper's defect in miniature: any other tag,
    and every entity, reached the page verbatim.
    """
    paras = _letter(tmp_path, "<p>Body.</p>",
                    SALUTATION="<p>Dear <strong>Globex &amp; Co</strong>,</p>")
    assert any("Dear Globex & Co," in p for p in paras), paras
    assert not any("&amp;" in p or "<strong>" in p for p in paras), paras


def test_no_tag_stripping_regex_is_hand_rolled_anywhere_in_the_module():
    """A fourth copy would not be a `def`, so the AST guard below cannot see it.

    Both copies this shard removed were expressions, not functions: a nested
    `def strip_html` and two `.replace("<p>", "")` calls on one line. What they
    share is the intent to turn markup into text without the shared parser.
    """
    source = (ROOT / "scripts" / "utils" / "doctype_renderer.py").read_text(encoding="utf-8")
    body = source.split('def _docx_text', 1)[1]
    for tell in ('re.sub(r"<', "re.sub(r'<", '.replace("<p>"', '.replace("</p>"'):
        assert tell not in body, (
            f"a hand-rolled markup strip is back in the module: {tell!r}. "
            f"Route it through _docx_text, which owns this conversion.")


def test_the_module_defines_no_second_html_stripper():
    """`html_text` says to import rather than copy. This asks whether anyone did.

    Asked of the AST: a `def strip_html` nested inside `build_docx` is invisible
    to a grep for a module-level definition, and that is exactly where this one
    hid for as long as it did.
    """
    tree = ast.parse((ROOT / "scripts" / "utils" / "doctype_renderer.py")
                     .read_text(encoding="utf-8"))
    defined = [n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    assert "strip_html" not in defined, (
        "doctype_renderer defines its own strip_html again. Import it from "
        "scripts.utils.html_text, which html_text's docstring asks callers to do.")


# ============================================================
# The fonts that never loaded
# ============================================================

def test_all_four_faces_embed(brand_root):
    assets = DR._resolve_brand_assets(brand_root)
    empty = sorted(k for k, v in assets.items() if k.startswith("FONT") and not v)
    assert not empty, f"these faces embedded as an empty data URI: {empty}"


def test_the_cyrillic_faces_carry_their_own_bytes(brand_root):
    """Non-empty is not enough: the two Inter faces must be the Inter files.

    A fix that pointed both Inter placeholders at a GT Standard face would pass
    the test above and still render Russian in a font with no Cyrillic glyphs.
    """
    import base64

    assets = DR._resolve_brand_assets(brand_root)
    for key, expected in (("FONT_INTER_LIGHT", b"inter-light-face"),
                          ("FONT_INTER_MEDIUM", b"inter-medium-face")):
        payload = base64.b64decode(assets[key].split("base64,", 1)[1])
        assert payload == expected, f"{key} embedded the wrong file: {payload!r}"


def test_the_inter_directory_is_not_looked_up_under_gt_standard(brand_root):
    """The defect in one line: `_fonts_dir` already ends in `GT Standard`."""
    gt = DR._fonts_dir(brand_root)
    assert gt.name == "GT Standard"
    assert not (gt / "Inter").exists(), "the fixture accidentally models the bug"
    inter = DR._inter_dir(brand_root)
    assert inter.is_dir(), f"{inter} does not exist"
    assert inter.parent == gt.parent, (
        f"Inter resolved outside the fonts root: {inter}")


def test_a_missing_asset_is_reported_rather_than_returned_as_silence(brand_root, capsys):
    """`_embed_asset` returning "" is how this hid for as long as it did.

    By the time assets resolve, `render_html` has already read the locked
    template from the same brand tree, so a file missing HERE is an anomaly, not
    a public clone with no overlay.
    """
    (brand_root / "datastore" / "brand" / "fonts" / "Inter" / "Inter-Light.ttf").unlink()
    assets = DR._resolve_brand_assets(brand_root)
    assert assets["FONT_INTER_LIGHT"] == ""
    err = capsys.readouterr().err
    assert "Inter-Light.ttf" in err, f"the miss was silent; stderr was {err!r}"


def test_a_complete_brand_tree_says_nothing(brand_root, capsys):
    """The other half: a warning printed on every render is noise, not a signal."""
    DR._resolve_brand_assets(brand_root)
    assert capsys.readouterr().err == ""
