"""Two ways the partner scorecard reported a result it had not produced.

`splice` checked that both markers were PRESENT, never that they were in order.
Measured on a partners.md whose two marker lines were swapped: `BEGIN[\\s\\S]*?END`
matches nothing, `pattern.sub` returns the input untouched, `--update` prints
"wrote N partnerships" and `--check` prints "in sync" over the stale table. That
is the silent no-op the function's own ValueError message says must never
happen, arriving through the one door the presence check left open.

`parse_partnerships` split rows on every `|`, ignoring the GitHub-flavoured
`\\|` escape. Measured on a topic cell reading `JV \\| licensing talks`: topic
came out `JV \\`, stage got `licensing talks`, priority got the real stage
`Active`, stage_date got the priority. Five cells survived, so nothing skipped
the row; it was rendered into partners.md as fact and `_health()` returned `--`
for a partner the pipeline calls Active.

Run: python3 -m pytest tests/test_a_scorecard_that_wrote_nothing_and_called_it_success.py
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "partner-scorecard.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("partner_scorecard_splice_mod", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ORDERED = """\
# Partner Ecosystem

<!-- BEGIN GENERATED SCORECARD -->
stale table nobody updated
<!-- END GENERATED SCORECARD -->

## Detailed Partner Profiles
"""

REVERSED = """\
# Partner Ecosystem

<!-- END GENERATED SCORECARD -->
stale table nobody updated
<!-- BEGIN GENERATED SCORECARD -->

## Detailed Partner Profiles
"""

PIPELINE_WITH_ESCAPED_PIPE = """\
## Partnership Discussions

| Partner | Topic | Stage | Priority | Stage Date | Next Action |
|---------|-------|-------|----------|------------|-------------|
| Globex | JV \\| licensing talks | Active | P0 | 2026-01-10 | Follow up |
| Acme Telecom | Reseller channel | Post-MWC | Medium | 2026-03-05 | Follow up |
"""


def test_markers_out_of_order_are_an_error_not_a_silent_no_op(mod):
    with pytest.raises(ValueError, match="order"):
        mod.splice(REVERSED, "| Acme Telecom | x | Active | P0 | 2026-01-10 | GREEN |\n")


def test_an_absent_marker_pair_is_named_as_absent_not_as_out_of_order(mod):
    """The two refusals are twins, and only this input tells them apart.

    Deleting the presence check entirely left the file green: with no markers
    at all `pattern.search` finds nothing, so the ORDER branch raises instead
    and something is still refused. What is lost is the diagnosis. An operator
    told his markers are "out of order" goes looking for a swap in a file that
    has no markers to swap.
    """
    with pytest.raises(ValueError, match="missing") as exc:
        mod.splice("# Partner Ecosystem\n\nno markers at all\n",
                   "| Acme Telecom | x | Active | P0 | 2026-01-10 | GREEN |\n")
    assert "out of order" not in str(exc.value)


def test_a_backslash_in_the_generated_table_survives_the_splice(mod):
    r"""`re.sub` reads a STRING replacement as a template; the lambda does not.

    A partner name or topic carrying a Windows path put `R:\new` through
    `pattern.sub`, where `\n` is a newline and `\1` is a group reference that
    raises `re.error`. Replacing the lambda with the bare string left every
    other case in this file green, because none of them contains a backslash.
    """
    table = (r"| Globex | R:\new drive | Active | P0 | 2026-01-10 | GREEN |" "\n"
             r"| Acme | \1x placeholder | Active | P0 | 2026-01-10 | GREEN |" "\n")

    out = mod.splice(ORDERED, table)

    assert r"R:\new drive" in out, "the backslash was read as an escape sequence"
    assert "\nnew drive" not in out
    assert r"\1x placeholder" in out, "the group reference was expanded, not inserted"


def test_an_alignment_colon_separator_row_is_still_a_separator(mod):
    """`startswith("---")` matched none of `|:---|`, `|---:|`, `|:---:|`.

    With the separator unrecognised `seen_sep` never flips, every data row is
    skipped, and `main` exits 2 with "no Partnership Discussions rows found"
    over a table that is right there. The live `context/pipeline.md` uses the
    plain form, so the regex that fixed this was measurable by nothing in this
    file until now.
    """
    aligned = ("## Partnership Discussions\n\n"
               "| Partner | Topic | Stage | Priority | Stage Date | Next Action |\n"
               "|:--------|:-----:|------:|:---------|---|---|\n"
               "| Globex | JV | Active | P0 | 2026-01-10 | Follow up |\n")

    rows = mod.parse_partnerships(aligned)

    assert len(rows) == 1, f"the aligned separator row was not recognised: {rows}"
    assert rows[0]["partner"] == "Globex"
    assert rows[0]["stage"] == "Active"


def test_the_ordered_pair_still_splices(mod):
    """The order check must refuse the reversed file and nothing else."""
    out = mod.splice(ORDERED, "| Acme Telecom | x | Active | P0 | 2026-01-10 | GREEN |\n")
    assert "stale table nobody updated" not in out
    assert "Acme Telecom" in out
    assert "## Detailed Partner Profiles" in out


def test_an_escaped_pipe_does_not_shift_the_columns(mod):
    rows = mod.parse_partnerships(PIPELINE_WITH_ESCAPED_PIPE)
    assert len(rows) == 2, "the escaped-pipe row was dropped, not parsed"
    globex = next(r for r in rows if r["partner"] == "Globex")
    assert globex["topic"] == r"JV \| licensing talks"
    assert globex["stage"] == "Active"
    assert globex["priority"] == "P0"
    assert globex["stage_date"] == "2026-01-10"
    assert mod._health(globex["stage"]) == "GREEN"


def test_the_escaped_pipe_round_trips_into_the_rendered_row(mod):
    """An unescaped pipe here would break the generated table open again."""
    rows = mod.parse_partnerships(PIPELINE_WITH_ESCAPED_PIPE)
    line = next(ln for ln in mod.render_scorecard(rows).split("\n")
                if ln.startswith("| Globex "))
    assert r"JV \| licensing talks" in line
    # Reparse the rendered row: if render had emitted a bare `|`, the columns
    # would shift again and `stage` would come back as the health colour.
    reparsed = mod.parse_partnerships(
        "## Partnership Discussions\n\n"
        "| Partner | Topic | Stage | Priority | Stage date | Health |\n"
        "|---|---|---|---|---|---|\n" + line + "\n")
    assert len(reparsed) == 1
    assert reparsed[0]["stage"] == "Active"
    assert reparsed[0]["stage_date"] == "2026-01-10"
