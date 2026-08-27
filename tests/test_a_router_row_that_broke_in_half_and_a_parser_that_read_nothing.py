#!/usr/bin/env python3
"""Three defects in the pair of scripts that own the always-on skill router.

`.claude/rules/skill-router.md` is injected into every session. Its registry is
generated from each SKILL.md's `x-heading-routing` block by
`scripts/generate-skill-router.py`, and `--check` regenerates and diffs to prove
the two agree.

**One: a newline inside a cell splits the row, and `--check` ratifies it.**
`_as_list` validates that every item is a string and stops there; `escape_pipes`
handles `|` and nothing else. A trigger written in the folded-scalar style this
workspace already uses for `x-heading-capability`:

    triggers:
      - >
        what's next
      - plain trigger

parses to `["what's next\\n", 'plain trigger']`, and `render_core_row` emits
``| `/demo` | what's next\\n, plain trigger |`` - a half row plus an orphan
``, plain trigger |`` line. In a detail file the Exclusions and Compound columns
then land on a different row than the skill they describe. Both gates stay green,
because the corruption is deterministic and `--check` compares a corrupt
generation against a corrupt file. Verified 2026-08-27.

This is the same shape as two defects the module has already fixed, and its own
`_as_list` docstring names the principle: "A coercion that can turn a structural
mistake into valid-looking output is a gate that reports on nothing." The item
TYPE was guarded, the container type was guarded, `compound: No` as a YAML
boolean was guarded. Cell CONTENT was not.

**Two: `label` is the field the `name` fix missed.** `load_routing_rows`
type-checks `name` and says why, in a comment about `name: 7` reaching `sorted()`
and raising an uncaught `TypeError` instead of the curated `{rel}: {err}` line
this gate exists to print. `label` sits three lines below, is read the same way,
and was never checked. Verified 2026-08-27: `render_core_row` with `label=7`
raises `TypeError: expected string or bytes-like object, got 'int'`.

**Three: the inverse parser reads a file that no longer holds its table.**
`scripts/dev/extract-router-rows.py` opens only `.claude/rules/skill-router.md`
and drops any row that does not split into exactly four cells. F-5.2 split the
generator's output into a two-column core index in that file plus four-column
detail tables under `reference/skill-router/`, which the parser never opens.
Measured on the live tree 2026-08-27: 94 rows warn-skipped, 0 parsed, and BOTH
exit paths print a green success line and return 0. Its docstring still promises
it "parses the seven registry tables" and that "the round-trip reproduces each
cell". A reader who runs it to re-derive a block gets a green line, exit 0, and
no work done.

Found by the engine defect hunt, 2026-08-27; the `label` half found while
reproducing the first.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gen():
    return _load("gsr_under_test", "scripts/generate-skill-router.py")


@pytest.fixture(scope="module")
def extractor():
    return _load("extract_rows_under_test", "scripts/dev/extract-router-rows.py")


def _skill(tmp_path, routing_yaml: str, name: str = "demo") -> Path:
    """Write one SKILL.md with the given x-heading-routing block."""
    d = tmp_path / ".claude" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: A demo skill.\n"
        + textwrap.dedent(routing_yaml)
        + "---\n\n# Demo\n",
        encoding="utf-8")
    return tmp_path


def _rows(gen, monkeypatch, tmp_path, routing_yaml, name="demo"):
    root = _skill(tmp_path, routing_yaml, name)
    monkeypatch.setattr(gen, "SKILLS_DIR", root / ".claude" / "skills")
    monkeypatch.setattr(gen, "ROOT", root)
    return gen.load_routing_rows()


CLEAN = """\
x-heading-routing:
  category: Operations
  triggers: ["alpha", "beta"]
  exclusions: ["N/A"]
  compound: "No"
  router: auto
"""


# ============================================================
# The control: a well-formed block still renders
# ============================================================

def test_a_clean_block_still_produces_a_row(gen, monkeypatch, tmp_path):
    rows, errors = _rows(gen, monkeypatch, tmp_path, CLEAN)
    assert errors == []
    assert gen.render_core_row(rows[0]) == "| `/demo` | alpha, beta |"


# ============================================================
# One: a newline must not reach a table cell
# ============================================================

FOLDED = """\
x-heading-routing:
  category: Operations
  triggers:
    - >
      what's next
    - plain trigger
  exclusions: ["N/A"]
  compound: "No"
  router: auto
"""


def test_the_folded_scalar_that_started_this_is_refused(gen, monkeypatch, tmp_path):
    """The realistic case: house style for capability blocks, applied to triggers."""
    rows, errors = _rows(gen, monkeypatch, tmp_path, FOLDED)
    assert rows == []
    assert len(errors) == 1
    assert "newline" in errors[0].lower(), errors[0]
    assert "triggers" in errors[0], errors[0]


@pytest.mark.parametrize("field,block", [
    ("triggers", 'triggers: ["one\\ntwo", "three"]\n  exclusions: ["N/A"]\n  compound: "No"'),
    ("exclusions", 'triggers: ["one"]\n  exclusions: ["a\\nb"]\n  compound: "No"'),
    ("compound", 'triggers: ["one"]\n  exclusions: ["N/A"]\n  compound: "Yes:\\nMeeting Prep"'),
    ("label", 'triggers: ["one"]\n  exclusions: ["N/A"]\n  compound: "No"\n  label: "/demo\\n[args]"'),
])
def test_every_cell_bearing_field_refuses_a_newline(gen, monkeypatch, tmp_path, field, block):
    """The fix belongs on every cell, not inside `_as_list` alone.

    `compound` is type-checked in a different place and `label` was not checked
    at all, so a guard added to `_as_list` would have left two fields open.
    """
    rows, errors = _rows(gen, monkeypatch, tmp_path,
                         "x-heading-routing:\n  category: Operations\n  " + block + "\n")
    assert rows == [], f"{field} rendered a row containing a newline"
    assert any(field in e and "newline" in e.lower() for e in errors), errors


def test_a_carriage_return_is_refused_too(gen, monkeypatch, tmp_path):
    """A file saved with CRLF line endings carries `\\r` into the cell."""
    rows, errors = _rows(gen, monkeypatch, tmp_path,
                         'x-heading-routing:\n  category: Operations\n'
                         '  triggers: ["one\\rtwo"]\n  exclusions: ["N/A"]\n'
                         '  compound: "No"\n')
    assert rows == []
    assert errors and "carriage return" in errors[0].lower(), errors


def test_the_error_names_the_file_so_the_author_can_find_it(gen, monkeypatch, tmp_path):
    _, errors = _rows(gen, monkeypatch, tmp_path, FOLDED)
    assert "SKILL.md" in errors[0], errors[0]


def test_a_tab_is_allowed(gen, monkeypatch, tmp_path):
    """Only what breaks a markdown row is refused. A tab renders as whitespace.

    Over-refusing is its own defect: it turns a working SKILL.md into a CI
    failure with no reader-visible symptom to point at.
    """
    rows, errors = _rows(gen, monkeypatch, tmp_path,
                         'x-heading-routing:\n  category: Operations\n'
                         '  triggers: ["one\\ttwo"]\n  exclusions: ["N/A"]\n'
                         '  compound: "No"\n')
    assert errors == []
    assert rows and "\t" in rows[0]["triggers"][0]


def test_every_rendered_row_of_the_live_corpus_is_exactly_one_line(gen):
    """The regression sweep over real data, beside the synthetic cases above.

    All 94 skills render newline-free today, so this passes now and is here to
    catch the SKILL.md that does not. It is not the only guard: on its own it
    would be green over whatever the corpus happens to contain.
    """
    rows, errors = gen.load_routing_rows()
    assert errors == [], errors
    assert len(rows) >= 50, f"only {len(rows)} rows loaded; the corpus moved"
    for row in rows:
        assert "\n" not in gen.render_core_row(row), row["name"]
        assert "\n" not in gen.render_row(row), row["name"]


# ============================================================
# Two: label, the field the name fix missed
# ============================================================

@pytest.mark.parametrize("literal", ["7", "no", "3.5", "[a, b]"])
def test_a_non_string_label_is_a_curated_error_not_a_traceback(
        gen, monkeypatch, tmp_path, literal):
    """`name` got this guard and the comment explaining why. `label` did not.

    Unquoted `no` is the YAML 1.1 boolean False, which is also falsy, so it fell
    through `routing.get("label") or f"/{name}"` and the author's value vanished
    without a word.
    """
    rows, errors = _rows(
        gen, monkeypatch, tmp_path,
        "x-heading-routing:\n  category: Operations\n"
        '  triggers: ["one"]\n  exclusions: ["N/A"]\n  compound: "No"\n'
        f"  label: {literal}\n")
    assert rows == [], f"label: {literal} produced a row"
    assert errors and "label" in errors[0], errors


def test_an_explicit_string_label_still_works(gen, monkeypatch, tmp_path):
    rows, errors = _rows(
        gen, monkeypatch, tmp_path,
        "x-heading-routing:\n  category: Operations\n"
        '  triggers: ["one"]\n  exclusions: ["N/A"]\n  compound: "No"\n'
        '  label: "/demo [args]"\n')
    assert errors == []
    assert rows[0]["label"] == "/demo [args]"


def test_an_absent_label_still_defaults_to_the_skill_name(gen, monkeypatch, tmp_path):
    rows, errors = _rows(gen, monkeypatch, tmp_path, CLEAN)
    assert errors == [] and rows[0]["label"] == "/demo"


# ============================================================
# Three: the inverse parser
# ============================================================

def test_the_parser_reads_the_file_that_actually_holds_the_table(extractor):
    """It parsed 0 of 94 rows and printed a green line over it."""
    rows, warnings = extractor.parse_registry()
    assert len(rows) >= 50, (
        f"parsed {len(rows)} rows; F-5.2 moved the four-column tables into "
        f"reference/skill-router/, which the parser must read")
    shape = [w for w in warnings if "expected 4" in w or "no detail file" in w]
    assert not shape, shape


def test_the_round_trip_reproduces_the_generator_cells(extractor, gen):
    """The docstring's own claim, asserted rather than described.

    A parser that reads the right file but splits on the wrong separator would
    still return rows; this asks whether the cells survive the trip.

    What is compared is the CELL, joined the way the generator joins it, not the
    list of items. That is the real property and the strongest one available:
    the trip loses and adds no character. It is not a loosened assertion, it is
    the honest one, because the rendered cell is genuinely ambiguous in two
    ways that no parser can undo. A trigger may itself contain the separator
    `, `, and then one item renders as two; and a pipe may be spelled bare or
    pre-escaped, and both render the same. Demanding list equality would demand
    the parser recover information the format does not carry.

    `unescape_pipes` is applied to the source side for the second ambiguity, so
    both spellings meet at the character the reader sees.
    """
    parsed, _ = extractor.parse_registry()
    generated, errors = gen.load_routing_rows()
    assert errors == [], errors
    by_name = {r["name"]: r for r in generated}
    u = gen.unescape_pipes
    checked = 0
    for name, routing in parsed.items():
        if name not in by_name:
            continue
        checked += 1
        source = by_name[name]
        assert routing["category"] == source["category"], name
        assert (gen.TRIGGER_SEP.join(routing["triggers"])
                == u(gen.TRIGGER_SEP.join(source["triggers"]))), name
        assert (gen.EXCL_SEP.join(routing["exclusions"])
                == u(gen.EXCL_SEP.join(source["exclusions"]))), name
        assert routing["compound"] == u(source["compound"]), name
    assert checked >= 50, f"only {checked} skills round-tripped"


def test_a_trigger_containing_the_separator_is_reported(extractor):
    """The ambiguity is not silent, because writing the block back would corrupt.

    This tool's purpose is to write an `x-heading-routing` block into a
    SKILL.md. A trigger holding `, ` splits into two on the way back, so the
    authoritative file would gain an item its author never wrote. The parser
    cannot tell the two apart, so it says so instead of pretending.
    """
    _, warnings = extractor.parse_registry()
    assert any("separator" in w for w in warnings), (
        f"the separator ambiguity is unreported; warnings were {warnings}")


@pytest.mark.parametrize("raw", [
    "plain text",
    "a | b",
    "/canopus [note | check | probe]",
    "||",
    r"\\",                   # backslashes with no pipe at all
    r"C:\ still fine",       # a backslash that is not next to a pipe
])
def test_escaping_a_cell_and_unescaping_it_returns_the_original(gen, raw):
    """The round trip at the character level.

    The parser split on unescaped pipes and never removed the escape, so a cell
    came back with a `\\|` the author never wrote - and that string would have
    been written into the authoritative SKILL.md.
    """
    assert gen.unescape_pipes(gen.escape_pipes(raw)) == raw


def test_a_cell_that_already_contains_an_escape_cannot_round_trip(gen):
    """The limit of the pair, recorded rather than papered over.

    `escape_pipes` treats an ODD backslash run before a pipe as "already
    escaped" and leaves it, so `a\\|b` and `a|b` written after a data backslash
    both come out as `a\\|b`. The forward function is not injective there, and
    no inverse can recover which one was meant. Asserting a perfect round trip
    for this input would be asserting something impossible, so what is asserted
    is the actual behaviour: the escape is consumed.

    The generator's OUTPUT is unaffected either way, which is why this has never
    shown: both spellings render the same table cell. Only the parser sees the
    difference, and what it recovers is the rendered character.
    """
    assert gen.escape_pipes(r"a\|b") == r"a\|b"
    assert gen.unescape_pipes(r"a\|b") == "a|b"


def test_the_corpus_really_does_hold_both_spellings(gen):
    """Measured, and the measurement contradicted the guess.

    Live skills write a pipe inside a trigger and they disagree: some write a
    bare `|` and let the generator escape it, others write `\\|` pre-escaped in
    their own frontmatter. Parity in `escape_pipes` makes the two render
    identically, so nothing has ever flagged it. Recorded here because the
    round-trip test below has to be honest about which of the two it can
    reproduce, and because a future reader will otherwise assume the corpus is
    uniform. Choosing one spelling is an edit to several SKILL.md files and the
    operator's call, not a night-shift one.

    Asserted as a PROPERTY, not as a census. A hand-listed pair of skill names
    was written here first and was wrong on the second run - it named one file
    and the corpus held two. An enumeration of what a corpus happens to contain
    is a test that breaks on every unrelated edit and teaches nothing.
    """
    rows, errors = gen.load_routing_rows()
    assert errors == [], errors
    cells = {r["name"]: " ".join(r["triggers"]) for r in rows}
    escaped = sorted(n for n, c in cells.items() if "\\|" in c)
    bare = sorted(n for n, c in cells.items() if "|" in c.replace("\\|", ""))
    assert escaped, "no skill spells a pipe pre-escaped; the ambiguity is gone"
    assert bare, "no skill spells a pipe bare; the ambiguity is gone"
    assert not set(escaped) & set(bare), (
        f"a single skill mixes both spellings, which no reader would expect: "
        f"{sorted(set(escaped) & set(bare))}")


def test_parsing_nothing_exits_non_zero(tmp_path, monkeypatch, extractor, capsys):
    """The shape this defect wore: success printed over an empty corpus.

    A tool whose whole job is to re-derive 94 blocks must not report OK after
    deriving none. The exit code is what a script or a CI step reads.
    """
    empty = tmp_path / "reference" / "skill-router"
    empty.mkdir(parents=True)
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    monkeypatch.setattr(extractor, "CATEGORY_FILE_DIR", empty)
    monkeypatch.setattr(extractor, "SKILLS_DIR", skills)
    monkeypatch.setattr(sys, "argv", ["extract-router-rows.py", "--dry-run"])

    rc = extractor.main()
    assert rc != 0, "parsing zero rows returned success"
    out = capsys.readouterr().out
    assert "0 router rows" in out


def test_the_script_reports_a_real_count_on_the_live_tree():
    """End to end, through the CLI, because the exit code is the deliverable."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "dev" / "extract-router-rows.py"),
         "--dry-run"],
        cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert "expected 4" not in result.stdout, (
        "rows are still being warn-skipped:\n" + result.stdout[:1500])
    assert "Parsed 0 router rows" not in result.stdout, result.stdout[:500]


def test_a_missing_detail_file_is_named_rather_than_read_as_an_empty_category(
        tmp_path, monkeypatch, extractor):
    """A category with no file on disk is a broken tree, not a category of none.

    The loop walks `CATEGORY_ORDER` and opens one file per category. When a file
    is absent, skipping it silently makes seven-of-seven and six-of-seven look
    identical from the outside: the parser returns fewer rows and says nothing
    about why. The warning has to name the category AND the path, because the
    two failures behind it - a renamed slug and a directory pointed somewhere
    else - are told apart only by the path.
    """
    partial = tmp_path / "reference" / "skill-router"
    partial.mkdir(parents=True)
    present = extractor.CATEGORY_ORDER[0]
    absent = extractor.CATEGORY_ORDER[1]
    slug = extractor._gen.category_slug(present)
    (partial / f"{slug}.md").write_text(
        "| Skill | Triggers | Exclusions | Compound |\n"
        "|---|---|---|---|\n"
        "| `/demo` | one trigger | N/A | No |\n", encoding="utf-8")
    monkeypatch.setattr(extractor, "CATEGORY_FILE_DIR", partial)

    rows, warnings = extractor.parse_registry()
    assert "demo" in rows, "the one file that exists was not parsed"
    named = [w for w in warnings if absent in w and "no detail file" in w]
    assert named, (
        f"a missing detail file for {absent!r} was skipped silently; "
        f"warnings were {warnings}")
    assert str(partial) in named[0], (
        f"the warning does not say where it looked: {named[0]!r}")


@pytest.mark.parametrize("field,block", [
    ("triggers", 'triggers: ["", "one"]\n  exclusions: ["N/A"]\n  compound: "No"'),
    ("exclusions", 'triggers: ["one"]\n  exclusions: ["  "]\n  compound: "No"'),
    ("compound", 'triggers: ["one"]\n  exclusions: ["N/A"]\n  compound: ""'),
    ("label", 'triggers: ["one"]\n  exclusions: ["N/A"]\n  compound: "No"\n'
              '  label: ""'),
])
def test_an_empty_cell_is_refused_rather_than_quietly_defaulted(
        gen, monkeypatch, tmp_path, field, block):
    """Empty is a third case, and it used to be handled three different ways.

    `label: ""` fell through `or f"/{name}"` and became `/demo`, so the author
    who deliberately blanked it was never told - the same silent-ignore that
    `label: no` suffered, wearing the one spelling the type check cannot catch.
    An empty trigger or compound had no default at all and rendered a blank
    column. One rule now covers all four fields: an empty cell is an error that
    names the field.
    """
    rows, errors = _rows(gen, monkeypatch, tmp_path,
                         "x-heading-routing:\n  category: Operations\n  " + block + "\n")
    assert rows == [], f"an empty {field} still produced a row: {rows}"
    assert any("empty" in e and field in e for e in errors), errors


def test_unescape_leaves_an_even_backslash_run_alone(gen):
    """The parity rule stated directly, because no round trip can reach it.

    `escape_pipes` maps an even run to odd and leaves an odd run odd, so its
    output NEVER carries an even, non-zero run of backslashes before a pipe. A
    naive inverse - "if there is any backslash, drop one" - therefore agrees
    with the parity-aware one on every string the escaper can produce, and a
    round-trip test cannot tell them apart. What is asserted here is the
    contract itself: this function is the exact inverse of `escape_pipes`, and
    the inverse of "an odd run means escaped" is "an even run does not". Written
    as a direct property rather than dressed up as a round trip, because a test
    that cannot fail for the reason it names is worse than no test.
    """
    assert gen.unescape_pipes("a" + "\\" * 2 + "|b") == "a" + "\\" * 2 + "|b"
    assert gen.unescape_pipes("a" + "\\" * 4 + "|b") == "a" + "\\" * 4 + "|b"
    assert gen.unescape_pipes("a" + "\\" * 3 + "|b") == "a" + "\\" * 2 + "|b"


def test_the_docstring_no_longer_names_the_file_it_does_not_read():
    """The claim that made the silence misleading.

    `.claude/rules/skill-router.md` holds a two-column core index now. A
    docstring that says the parser reads its "seven registry tables" sends the
    next reader to the wrong file to explain a zero count.
    """
    doc = (ROOT / "scripts" / "dev" / "extract-router-rows.py").read_text(
        encoding="utf-8").split('"""')[1]
    assert "reference/skill-router" in doc, (
        "the docstring does not name the files the parser reads")
