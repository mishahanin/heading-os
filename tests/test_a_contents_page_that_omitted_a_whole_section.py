"""Shard 07-p1: the ODUN.ONE capability document generator.

k3 returned three findings against `scripts/generate-odunone-docx.py` and none
against `scripts/generate-partner-enablement.py`. All three confirmed.

1. THE CONTENTS PAGE OMITTED A WHOLE SECTION. The body writes seventeen
   Heading 1 paragraphs; the hardcoded `toc_items` listed fifteen entries plus
   the contents page itself. `LICENSING & SUPPORT` -- with its Support Packages
   and Professional Services subsections -- appeared nowhere in the contents.
   Entry 10 also misquoted its own heading: "AI Analytics for Telco & Law
   Enforcement: Training Scenarios" against a body heading reading "AI
   ANALYTICS: TELCO & LAW ENFORCEMENT TRAINING SCENARIOS". Close enough to look
   right; different enough that searching one against the other finds nothing.

   This is the SAME defect shape as shard 06-p3's phantom appendices, in a
   second document generator, and it gets the same structural fix: one
   `SECTIONS` tuple read by the contents page and by every heading, with `h1()`
   refusing a heading that is not in it. A hand-typed list drifts. A derived one
   cannot.

2. Editorial placeholders shipped in the deliverable. `[N]+`, `[$X]B`,
   `[HQ City 1]` and `[HQ City 2]` reach the recipient verbatim inside a file
   named "Complete Capability Document.docx" that generates its own
   "Confidential -- For Authorized Recipients Only" line. They are deliberate:
   this IS a template for a human editing pass. Nothing said so, and nothing
   checked whether the pass had happened. `report_placeholders` now names them
   after every build. It reports rather than refuses, because refusing would
   break the workflow that produces the template.

3. The `add_bullet` docstring said "orange bullet" and the header comment said
   "orange accent #FF9235" -- both naming the MARKER glyph, whose colour lives
   in the template's numbering.xml and is untouched by this code. What the code
   does is colour the bullet TEXT RUNS, so ~150 bulleted lines render orange
   rather than the brand's black Normal.

   Described, not changed. Whether a customer-facing document should carry
   orange bullet body text is the operator's brand call. The CONTRADICTION is
   what is fixed: the docstring no longer tells a reader the colour lands
   somewhere it does not.

   The colour question is CLOSED. #FF9235 matched neither `--orange` (#F5922B)
   nor `--orange-hi` (#FF8C00), which read as drift; it is in fact a third
   brand orange, documented in `reference/31c-typeface-usage.md` and taken from
   the 03-Jul-2026 Investor Deck. The operator ruled on 2026-08-25 that all
   three are legitimate, and it is now listed as `--orange-display` in the
   locked palette. See `test_every_orange_the_code_uses_is_in_the_locked_palette`.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SOURCE = ROOT / "scripts" / "generate-odunone-docx.py"


@pytest.fixture(scope="module")
def od():
    spec = importlib.util.spec_from_file_location("odunone_k3", str(SOURCE))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["odunone_k3"] = mod
    spec.loader.exec_module(mod)
    return mod


def _body_headings():
    """Every level-1 heading the builder emits, read from the syntax tree.

    Read as CODE, not as text: the comments in this file quote the old
    contents-page wording in order to record what was wrong with it, and a
    substring search over the source would call that a regression.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "add_heading"):
            continue
        if len(node.args) < 3:
            continue
        level = node.args[2]
        if not (isinstance(level, ast.Constant) and level.value == 1):
            continue
        arg = node.args[1]
        if isinstance(arg, ast.Constant):
            out.append(arg.value)                      # a bare literal
        elif (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name)
                and arg.func.id == "h1" and arg.args
                and isinstance(arg.args[0], ast.Constant)):
            out.append(arg.args[0].value)              # h1("...")
    return out


# ============================================================
# 1. The contents page and the body
# ============================================================

def test_every_body_section_is_on_the_contents_page(od):
    """The finding. LICENSING & SUPPORT was in the body and nowhere else."""
    listed = {heading for _, heading in od.SECTIONS}
    body = [h for h in _body_headings() if h != "TABLE OF CONTENTS"]
    assert set(body) - listed == set(), "a section the contents page omits"


def test_the_omitted_section_is_now_listed(od):
    assert "LICENSING & SUPPORT" in {h for _, h in od.SECTIONS}


def test_no_contents_entry_names_a_section_that_does_not_exist(od):
    listed = {heading for _, heading in od.SECTIONS}
    body = {h for h in _body_headings() if h != "TABLE OF CONTENTS"}
    assert listed - body == set(), "the contents page promises what is not there"


def test_the_counts_match_exactly(od):
    body = [h for h in _body_headings() if h != "TABLE OF CONTENTS"]
    assert len(od.SECTIONS) == len(body)


def test_the_contents_text_and_the_heading_are_the_same_words(od):
    """Entry 10 read "...for Telco & Law Enforcement: Training Scenarios"
    against a heading reading "AI ANALYTICS: TELCO & ...". Case may differ;
    the words may not."""
    for toc, heading in od.SECTIONS:
        assert toc.upper() == heading.upper(), (toc, heading)


def test_the_misquoted_entry_is_corrected(od):
    toc = {h: t for t, h in od.SECTIONS}
    assert toc["AI ANALYTICS: TELCO & LAW ENFORCEMENT TRAINING SCENARIOS"] == (
        "AI Analytics: Telco & Law Enforcement Training Scenarios")


def test_a_heading_outside_the_list_is_refused(od):
    """The guarantee is mechanical or it is not a guarantee.

    A new section added to the body without an entry in SECTIONS must fail
    here, not ship a document whose contents page quietly omits it.
    """
    with pytest.raises(KeyError):
        od.h1("A SECTION NOBODY LISTED")


def test_a_listed_heading_passes_through_unchanged(od):
    assert od.h1("EXECUTIVE SUMMARY") == "EXECUTIVE SUMMARY"


def test_no_top_level_heading_is_typed_without_the_guard():
    """Drift is impossible only while every heading goes through `h1`."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    bare = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "add_heading" and len(node.args) >= 3):
            continue
        level = node.args[2]
        if not (isinstance(level, ast.Constant) and level.value == 1):
            continue
        arg = node.args[1]
        if isinstance(arg, ast.Constant) and arg.value != "TABLE OF CONTENTS":
            bare.append(arg.value)
    assert bare == [], bare


def test_the_contents_page_is_built_from_the_list(od):
    """Not re-typed. A second copy is the thing that drifts.

    TIGHTENED 2026-08-30. This asked `"SECTIONS" in {n.id for n in
    ast.walk(tree) if isinstance(n, ast.Name)}`, and the `SECTIONS = (...)`
    assignment target is itself an `ast.Name` in Store context that `ast.walk`
    visits -- so the definition alone satisfied it and every READ of the list
    could be deleted with the test still green. Both halves are now required:
    a Load-context read, and one inside `contents_lines`, which is the function
    whose output the contents page is made of.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    loads = {n.id for n in ast.walk(tree)
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    assert "SECTIONS" in loads, "SECTIONS is defined and never read"

    builder = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "contents_lines"),
                   None)
    assert builder is not None, (
        "contents_lines was renamed; this guard now measures nothing")
    inner = {n.id for n in ast.walk(builder)
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    assert "SECTIONS" in inner, (
        "contents_lines does not read SECTIONS, so the contents page is a "
        "second copy of the section list")


def test_the_numbering_lines_up_with_the_list_order(od):
    """CALLS the builder. The first version re-typed its comprehension here.

    That is a test which passes whatever the code does, and the mutation
    harness said so: reading the heading form instead of the contents form,
    and numbering from zero, both survived. `contents_lines` was extracted so
    there is something to call.
    """
    items = od.contents_lines()
    assert items[0] == "1.  Executive Summary"
    assert items[-1] == f"{len(od.SECTIONS)}. Licensing & Support"


def test_the_contents_lines_are_one_per_section(od):
    assert len(od.contents_lines()) == len(od.SECTIONS)


def test_every_contents_line_uses_the_contents_form(od):
    """Not the heading form. The two differ by case, and the page is Title Case."""
    # strict=True: a shorter contents page than SECTIONS is the exact defect
    # this file is about, and a lenient zip would stop at the shorter one and
    # call it a pass.
    for line, (toc, heading) in zip(od.contents_lines(), od.SECTIONS, strict=True):
        assert line.endswith(toc)
        if toc != heading:
            assert not line.endswith(heading)


def test_the_numbering_starts_at_one(od):
    assert od.contents_lines()[0].startswith("1.")
    assert not od.contents_lines()[0].startswith("0.")


def test_the_numbering_is_consecutive(od):
    numbers = [int(line.split(".", 1)[0]) for line in od.contents_lines()]
    assert numbers == list(range(1, len(od.SECTIONS) + 1))


def test_double_digit_entries_stay_aligned(od):
    """Sixteen sections now, so the alignment gap is load-bearing."""
    single = [ln for ln in od.contents_lines() if int(ln.split(".", 1)[0]) < 10]
    double = [ln for ln in od.contents_lines() if int(ln.split(".", 1)[0]) >= 10]
    assert all(ln.split(".", 1)[1].startswith("  ") for ln in single)
    assert double, "the omitted section is what pushed this past nine"
    assert all(ln.split(".", 1)[1].startswith(" ")
               and not ln.split(".", 1)[1].startswith("  ") for ln in double)


def test_the_builder_calls_the_helper():
    """A comprehension inlined again is a comprehension nothing can test."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "build_document")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "contents_lines" in called


# ============================================================
# 2. The placeholders that shipped
# ============================================================

class _FakePara:
    def __init__(self, text):
        self.text = text


class _FakeCell:
    def __init__(self, text):
        self.text = text


class _FakeRow:
    def __init__(self, cells):
        self.cells = [_FakeCell(c) for c in cells]


class _FakeTable:
    def __init__(self, rows):
        self.rows = [_FakeRow(r) for r in rows]


class _FakeDoc:
    def __init__(self, paras=(), tables=()):
        self.paragraphs = [_FakePara(p) for p in paras]
        self.tables = [_FakeTable(t) for t in tables]


def test_the_shipped_placeholders_are_found(od):
    doc = _FakeDoc(["removed the incumbent from [N]+ countries, creating a "
                    "[$X]B addressable market"])
    assert set(od.find_placeholders(doc)) == {"[N]", "[$X]"}


def test_a_placeholder_in_a_table_cell_is_found(od):
    doc = _FakeDoc(tables=[[["ok", "[HQ City 1]"]]])
    assert "[HQ City 1]" in od.find_placeholders(doc)


def test_a_clean_document_reports_nothing(od, capsys):
    assert od.report_placeholders(_FakeDoc(["all substituted"])) == []
    assert capsys.readouterr().err == ""


def test_the_report_names_every_distinct_token(od, capsys):
    doc = _FakeDoc(["[N] and [$X]"], tables=[[["[HQ City 1]", "[HQ City 2]"]]])
    assert od.report_placeholders(doc) == ["[$X]", "[HQ City 1]", "[HQ City 2]",
                                           "[N]"]
    err = capsys.readouterr().err
    for token in ("[N]", "[$X]", "[HQ City 1]", "[HQ City 2]"):
        assert token in err


def test_the_report_says_what_to_do_about_them(od, capsys):
    od.report_placeholders(_FakeDoc(["[N]"]))
    assert "BEFORE SENDING" in capsys.readouterr().err


def test_the_report_counts_occurrences_and_distinct_separately(od, capsys):
    od.report_placeholders(_FakeDoc(["[N] then [N] again"]))
    err = capsys.readouterr().err
    assert "2 editorial placeholder(s)" in err
    assert "1 distinct" in err


@pytest.mark.parametrize("text", [
    "ordinary prose", "a [lowercase] aside", "an [] empty bracket",
    "reference [1] to a source",
])
def test_ordinary_bracketed_text_is_not_a_placeholder(od, text):
    """The scan must not cry wolf, or it stops being read."""
    assert od.find_placeholders(_FakeDoc([text])) == []


def test_the_report_is_not_fatal(od, capsys):
    """It reports. Refusing would break the workflow that makes the template."""
    od.report_placeholders(_FakeDoc(["[N]"]))          # must not raise


def test_the_build_reports_after_saving():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    calls = [n.func.id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "report_placeholders" in calls


# ============================================================
# 3. The docstring that named the wrong thing
# ============================================================

def test_the_bullet_docstring_no_longer_claims_the_marker(od):
    doc = od.add_bullet.__doc__
    assert "orange bullet)" not in doc
    assert "TEXT RUNS" in doc or "BULLET TEXT" in doc


def test_the_docstring_says_where_the_colour_lands(od):
    doc = od.add_bullet.__doc__.lower()
    assert "run.font.color" in doc or "text runs" in doc


def test_the_docstring_names_the_open_brand_question(od):
    """A finding handed to the operator must stay visible in the source."""
    doc = od.add_bullet.__doc__
    assert "operator" in doc.lower()
    assert "FF9235" in doc


def test_the_header_comment_matches_the_docstring():
    """Checked on COMMENT LINES, not on the whole file.

    `add_bullet`'s docstring quotes the old wording in order to record what it
    was, so a plain substring search over the source flags the correction as
    the defect -- the third time this pattern has bitten in one night.
    """
    comments = [ln.strip() for ln in SOURCE.read_text(encoding="utf-8").splitlines()
                if ln.lstrip().startswith("#")]
    assert not any("orange accent #FF9235" in c for c in comments)
    assert any("TEXT RUNS" in c for c in comments)


def test_the_colour_itself_is_unchanged(od):
    """Nothing about the rendering was altered while describing it.

    Counted as ASSIGNMENTS in the syntax tree. Counting source lines counted
    the docstring's own mention of `run.font.color.rgb = ORANGE` as a fourth
    assignment, which is the documentation being read as behaviour again.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "add_bullet")
    targets = [ast.unparse(tgt) for node in ast.walk(fn)
               if isinstance(node, ast.Assign)
               and isinstance(node.value, ast.Name) and node.value.id == "ORANGE"
               for tgt in node.targets
               if isinstance(tgt, ast.Attribute) and tgt.attr == "rgb"]
    # Scoped to `add_bullet`. A module-wide count found FOUR, because the cover
    # page colours its own metadata block orange at line 395 -- a separate,
    # deliberate use that this test has no business pinning.
    assert sorted(targets) == ["run.font.color.rgb",
                               "run_b.font.color.rgb",
                               "run_n.font.color.rgb"]
    assert "RGBColor(0xFF, 0x92, 0x35)" in SOURCE.read_text(encoding="utf-8")


# ============================================================
# 4. The save that no longer makes its own directory twice
# ============================================================

def test_the_generator_saves_through_the_shared_helper():
    src = SOURCE.read_text(encoding="utf-8")
    assert "save_docx(doc, out)" in src
    assert "doc.save(" not in src


def test_the_redundant_makedirs_is_gone():
    """`save_docx` creates the parent; a second call is a second thing to keep
    in step, and it orphaned the `os` import when it went."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    calls = [ast.unparse(n.func) for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert "os.makedirs" not in calls
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names}
    assert "os" not in imported


# ==========================================================================
# The palette that did not list every orange in use
# ==========================================================================

def _palette_hexes() -> set[str]:
    """Every hex the LOCKED palette table declares."""
    guide = (ROOT / "reference" / "corporate-style-guide.md").read_text(encoding="utf-8")
    table = guide.split("## Colors (locked)", 1)[1].split("\n## ", 1)[0]
    return {m.upper() for m in re.findall(r"#([0-9A-Fa-f]{6})", table)}


def _colours_from_calls(source: str) -> set[str]:
    """Colours written as a three-byte constructor call, e.g.
    `RGBColor(0xFF, 0x92, 0x35)` -- the form `generate-odunone-docx.py` uses and
    the `#RRGGBB` regex can never match. Any callee taking exactly three int
    constants in 0-255 counts, so `RGBColor` being renamed does not blind this.
    """
    out: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or len(node.args) != 3 or node.keywords:
            continue
        vals = [a.value for a in node.args
                if isinstance(a, ast.Constant) and isinstance(a.value, int)
                and not isinstance(a.value, bool) and 0 <= a.value <= 255]
        if len(vals) == 3:
            out.add("".join(f"{v:02X}" for v in vals))
    return out


def _colours_used(path: Path) -> set[str]:
    """Every colour a source file names, in BOTH spellings.

    WIDENED 2026-08-30. Only `#RRGGBB` was read, so the generator's real
    assignments were invisible and the guard rested on an incidental comment.
    """
    text = path.read_text(encoding="utf-8")
    found = {m.upper() for m in re.findall(r"#([0-9A-Fa-f]{6})", text)}
    if path.suffix == ".py":
        found |= _colours_from_calls(text)
    return found


def test_every_orange_the_code_uses_is_in_the_locked_palette():
    """A generator using a hex the palette does not list is brand drift.

    #FF9235 was used by four places and listed by none, so which orange was
    correct depended on which reference file you opened. It is now
    `--orange-display`. This test is the thing that keeps the next one from
    going unlisted for as long.
    """
    palette = _palette_hexes()
    used: dict[str, list[str]] = {}
    for rel in ("scripts/generate-odunone-docx.py", "scripts/marp_render.py"):
        for hexval in _colours_used(ROOT / rel):
            # Oranges only: red high, green mid, blue low.
            r, g, b = (int(hexval[i:i + 2], 16) for i in (0, 2, 4))
            if r > 200 and 100 < g < 190 and b < 100:
                used.setdefault(hexval, []).append(rel)
    assert used, "the orange detector matched nothing; it can no longer fail"
    missing = {h: v for h, v in used.items() if h not in palette}
    assert not missing, f"orange(s) used but not in the locked palette: {missing}"


def test_the_orange_detector_reads_the_form_the_generator_actually_uses():
    """The negative case for the widening above. NEW 2026-08-30.

    `generate-odunone-docx.py` writes its colours as `RGBColor(0xFF, 0x92,
    0x35)`. The detector matched `#([0-9A-Fa-f]{6})` and nothing else, so it
    could never see that assignment: whether the generator's orange was policed
    at all depended on an incidental `#FF9235` surviving in a COMMENT -- and a
    sibling test in this same file pins that the old comment spelling was
    removed. This asserts the detector sees the call form with every comment
    and docstring stripped away, so the coverage cannot go back to resting on
    prose.
    """
    text = SOURCE.read_text(encoding="utf-8")
    assert "FF9235" in _colours_from_calls(text), (
        "the detector cannot see RGBColor(0xFF, 0x92, 0x35), which is how the "
        "generator spells its orange")

    # And the half that shows why the AST half is needed: tokenize the file and
    # ask where the `#RRGGBB` spelling actually occurs. Every occurrence is a
    # COMMENT or a STRING -- prose. The regex-only detector therefore policed
    # this generator's orange through documentation and nothing else, which a
    # sibling test in this file is actively removing.
    with tokenize.open(SOURCE) as fh:
        toks = list(tokenize.generate_tokens(fh.readline))
    in_code = [t for t in toks
               if "FF9235" in t.string.upper()
               and t.type not in (tokenize.COMMENT, tokenize.STRING)]
    prose = [t for t in toks
             if "FF9235" in t.string.upper()
             and t.type in (tokenize.COMMENT, tokenize.STRING)]
    assert prose, "the premise is stale: no `#FF9235` prose remains to rely on"
    assert not in_code, (
        f"`#FF9235` now appears in code at lines "
        f"{[t.start[0] for t in in_code]}; re-derive this test")


def test_the_palette_lists_all_three_oranges():
    palette = _palette_hexes()
    for hexval in ("F5922B", "FF8C00", "FF9235"):
        assert hexval in palette, f"#{hexval} left the locked palette"


def test_the_display_orange_names_where_it_came_from():
    """A third near-identical orange with no provenance reads as a mistake."""
    guide = (ROOT / "reference" / "corporate-style-guide.md").read_text(encoding="utf-8")
    row = [ln for ln in guide.splitlines() if "FF9235" in ln]
    assert row, "the display orange row is gone"
    assert "31c-typeface-usage" in row[0], "the row does not point at the per-element detail"


def test_the_typeface_reference_points_back_at_the_palette():
    """Two files disagreeing is the defect; each must name the other."""
    usage = (ROOT / "reference" / "31c-typeface-usage.md").read_text(encoding="utf-8")
    assert "corporate-style-guide.md" in usage
    assert "orange-display" in usage
