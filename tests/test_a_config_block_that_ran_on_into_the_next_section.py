"""A `## Config` block that ran on into the next section, and a table row that ended the table.

Two defects in `scripts/utils/markdown.py`, both silent, both in the direction
where the caller believes an answer that came from somewhere else.

**`parse_config`.** The block was extracted by one regex ending at
`(?:\\n##|\\Z)`. That terminator needs a BLANK line before the next heading,
because every block line has already consumed its own `\\n`. When a `##`
heading follows the last config line directly, the block cannot end there and
runs on to the end of the file, so a key absent from the Config block but
present in a LATER section is returned as if it were configured. The docstring
says the block ends at the next `##` heading; it did not.

**`parse_md_table`.** A row that lost its leading pipe - what a hand edit or a
wrapped line produces - ended the loop with `break`, dropping that row and
every row after it with no warning. The same function's docstring says it was
written to end exactly this failure class for SHORT rows ("A short row is now
padded and reported; the row survives"), and the pipe-less variant sat one
column over. A line carrying no pipe at all is still prose and still ends the
table; that part was never the defect.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.markdown import parse_config, parse_md_table  # noqa: E402


# ============================================================
# parse_config: the block ends at the next heading
# ============================================================

def test_a_key_in_the_next_section_is_not_read_as_config():
    text = "## Config\ncadence: 14\n## Next\nsecret: 99\n"
    assert parse_config(text, "cadence") == "14"
    assert parse_config(text, "secret") is None


def test_the_heading_needs_no_blank_line_before_it_to_end_the_block():
    """The exact repro: the Config block carries `other`, `cadence` is elsewhere."""
    assert parse_config("## Config\nother: x\n## Next\ncadence: 99\n",
                        "cadence") is None


def test_a_blank_line_before_the_next_heading_still_ends_the_block():
    assert parse_config("## Config\nother: x\n\n## Next\ncadence: 99\n",
                        "cadence") is None


def test_the_block_still_reaches_end_of_file_when_no_heading_follows():
    text = "# Title\n\n## Configuration:\ncadence: 21\ntimezone: UTC\n"
    assert parse_config(text, "cadence") == "21"
    assert parse_config(text, "timezone") == "UTC"


def test_a_final_line_with_no_trailing_newline_is_still_read():
    assert parse_config("## Config\ncadence: 7", "cadence") == "7"


def test_a_document_with_no_config_block_returns_none():
    assert parse_config("# Title\n\nsome prose\ncadence: 5\n", "cadence") is None


def test_quoted_values_are_unquoted_and_comments_skipped():
    text = '## Config\n# a comment\nowner: "Jane Bellweather"\n## Other\n'
    assert parse_config(text, "owner") == "Jane Bellweather"


# ============================================================
# parse_md_table: a row missing its leading pipe survives, loudly
# ============================================================

_TABLE = ("| Deal | Value |\n"
          "|---|---|\n"
          "| Aurora Freight | 10 |\n"
          "Bellweather Mining | 20 |\n"
          "| Castellan Ports | 30 |")


def test_a_row_without_its_leading_pipe_does_not_delete_the_rows_after_it():
    warnings: list[str] = []
    rows = parse_md_table(_TABLE, warn=warnings.append)
    assert [r["Deal"] for r in rows] == [
        "Aurora Freight", "Bellweather Mining", "Castellan Ports"]
    assert [r["Value"] for r in rows] == ["10", "20", "30"]


def test_the_recovered_row_is_reported_not_swallowed():
    warnings: list[str] = []
    parse_md_table(_TABLE, warn=warnings.append)
    assert len(warnings) == 1, warnings
    assert "Bellweather Mining" in warnings[0]
    assert "line 4" in warnings[0]


def test_prose_under_the_table_still_ends_it():
    text = ("| Deal | Value |\n"
            "|---|---|\n"
            "| Aurora Freight | 10 |\n"
            "Notes follow below.\n"
            "| Castellan Ports | 30 |")
    warnings: list[str] = []
    rows = parse_md_table(text, warn=warnings.append)
    assert [r["Deal"] for r in rows] == ["Aurora Freight"]
    assert warnings == []


def test_a_blank_line_still_ends_the_table():
    text = ("| Deal | Value |\n"
            "|---|---|\n"
            "| Aurora Freight | 10 |\n"
            "\n"
            "| Other | Table |\n"
            "|---|---|\n"
            "| Castellan Ports | 30 |")
    warnings: list[str] = []
    rows = parse_md_table(text, warn=warnings.append)
    assert [r["Deal"] for r in rows] == ["Aurora Freight"]
    assert warnings == []
