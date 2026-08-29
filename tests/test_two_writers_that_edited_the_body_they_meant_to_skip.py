"""Frontmatter WRITERS that reached outside the frontmatter.

The reading side of this workspace learned twice that a fence is a LINE, not
three characters wherever they land: `scripts/utils/markdown.split_frontmatter`
and `split_frontmatter_raw` carry both post-mortems. The writing side never got
the lesson. Three functions rewrite a field in frontmatter and each spelled the
scope itself.

MEASURED 2026-08-29 (.tmp/audit/measure62.py, .tmp/audit/measure62b.py):

  `crm_autolog.bump_last_touch_in_text` ran `^last_touch:` MULTILINE over the
    WHOLE DOCUMENT before deciding whether to insert. A card with no
    `last_touch` in frontmatter and any body line beginning `last_touch:` took
    the replace branch, rewrote the BODY line and returned. The field stayed
    absent from frontmatter, the audit log recorded `matched: true`, and the
    health engine went on reading the contact as never touched. Its docstring
    said the write "lands inside the frontmatter block or nowhere".
  `transfer-contact.update_owner_in_frontmatter` scoped its edit correctly and
    spelled its own fences. The trailing newline in `\\n---\\s*\\n` is required,
    so a card whose file ENDS at the closing fence matched nothing, took the
    no-frontmatter branch, and had a SECOND block prepended: the card's real
    fields became body text.
  `crm-health.frontmatter_end` had both defects, was fixed on its own, and wrote
    the reason down in its docstring. The fix never reached the other two.

Both are now one function, `markdown.set_frontmatter_field`. The controls in
this file matter as much as the defect cases: a helper that refused to write
anything would satisfy every "did not touch the body" assertion here.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.crm_autolog import bump_last_touch_in_text  # noqa: E402
from scripts.utils.markdown import (  # noqa: E402
    FM_OK,
    parse_frontmatter,
    set_frontmatter_field,
    split_frontmatter_raw,
)

_spec = importlib.util.spec_from_file_location(
    "transfer_contact_shard62", ROOT / "scripts" / "transfer-contact.py")
transfer_contact = importlib.util.module_from_spec(_spec)
sys.modules["transfer_contact_shard62"] = transfer_contact
_spec.loader.exec_module(transfer_contact)

update_owner = transfer_contact.update_owner_in_frontmatter

DATE = "2026-08-29"


def _fm_value(text: str, key: str):
    """What a reader of FRONTMATTER sees. Not a substring search of the file."""
    data, _body = parse_frontmatter(text)
    return data.get(key)


# ============================================================
# set_frontmatter_field -- the shared writer
# ============================================================


def test_an_existing_field_is_replaced() -> None:
    out = set_frontmatter_field("---\na: 1\nk: old\n---\n\nbody\n", "k", "new")
    assert _fm_value(out, "k") == "new"
    assert _fm_value(out, "a") == 1


def test_a_missing_field_is_inserted() -> None:
    out = set_frontmatter_field("---\na: 1\n---\n\nbody\n", "k", "new")
    assert _fm_value(out, "k") == "new"
    assert _fm_value(out, "a") == 1


def test_a_body_line_with_the_same_key_is_not_touched() -> None:
    """THE DEFECT. The body line won and the frontmatter never got the field."""
    text = "---\na: 1\n---\n\n## Notes\nk: something a human wrote\n"
    out = set_frontmatter_field(text, "k", "new")
    assert _fm_value(out, "k") == "new"
    assert "k: something a human wrote" in out, (
        "the writer edited prose it was never asked to touch")


def test_the_body_line_survives_even_when_the_field_already_exists() -> None:
    text = "---\nk: old\n---\n\n## Notes\nk: something a human wrote\n"
    out = set_frontmatter_field(text, "k", "new")
    assert _fm_value(out, "k") == "new"
    assert "k: something a human wrote" in out


def test_a_nested_key_of_the_same_name_is_left_alone() -> None:
    """`  k:` under a mapping is not the top-level field a reader looks up."""
    text = "---\nmeta:\n  k: nested\n---\n\nbody\n"
    out = set_frontmatter_field(text, "k", "new")
    assert "  k: nested" in out
    assert _fm_value(out, "k") == "new"


def test_a_document_with_no_opening_fence_is_returned_unchanged() -> None:
    text = "# Title\n\nk: in the prose\n"
    assert set_frontmatter_field(text, "k", "new") == text


def test_a_document_with_no_closing_fence_is_returned_unchanged() -> None:
    text = "---\na: 1\n\nnever closed\n"
    assert set_frontmatter_field(text, "k", "new") == text


def test_the_blank_line_before_the_body_survives() -> None:
    """A field edit that reflows the file is a diff nobody asked for."""
    text = "---\na: 1\n---\n\n\nbody\n"
    out = set_frontmatter_field(text, "a", "2")
    assert out.endswith("---\n\n\nbody\n")


def test_crlf_is_preserved() -> None:
    text = "---\r\na: 1\r\n---\r\n\r\nbody\r\n"
    out = set_frontmatter_field(text, "a", "2")
    assert "\r\n" in out
    assert "\n\n" not in out.replace("\r\n", "<CRLF>"), "a bare LF was introduced"
    assert out.endswith("---\r\n\r\nbody\r\n")


def test_crlf_is_preserved_on_an_insert_too() -> None:
    text = "---\r\na: 1\r\n---\r\n\r\nbody\r\n"
    out = set_frontmatter_field(text, "k", "new")
    assert "k: new\r\n" in out
    assert _fm_value(out.replace("\r\n", "\n"), "k") == "new"


@pytest.mark.parametrize("fence", ["---", "--- ", "---\t"])
def test_a_fence_with_trailing_whitespace_is_still_a_fence(fence: str) -> None:
    text = f"{fence}\na: 1\n---\n\nbody\n"
    out = set_frontmatter_field(text, "k", "new")
    assert out != text, f"the opening fence {fence!r} was refused"
    assert _fm_value(out.replace("--- \n", "---\n").replace("---\t\n", "---\n"),
                     "k") == "new"


def test_three_dashes_inside_a_value_do_not_end_the_block() -> None:
    text = "---\nnotes: 2026-01-01---draft\nk: old\n---\n\nbody\n"
    out = set_frontmatter_field(text, "k", "new")
    assert _fm_value(out, "k") == "new"
    assert "notes: 2026-01-01---draft" in out


def test_a_backslash_in_the_value_is_written_literally() -> None:
    r"""`re.sub` reads `\1` in the REPLACEMENT as a group reference."""
    out = set_frontmatter_field("---\nk: old\n---\n\nbody\n", "k", r"C:\1\path")
    assert r"k: C:\1\path" in out


def test_a_key_holding_a_regex_metacharacter_is_escaped() -> None:
    """The lookalike key comes FIRST, on purpose.

    With `a.b: old` on the earlier line, an UNESCAPED `^a.b:` matches the right
    line anyway and `count=1` hides the bug -- the mutation that removes
    `re.escape` survived that ordering. Put `axb` first and the unescaped
    pattern rewrites the wrong field.
    """
    text = "---\naxb: other\na.b: old\n---\n\nbody\n"
    out = set_frontmatter_field(text, "a.b", "new")
    assert "axb: other" in out, "the unescaped `.` matched a different key"
    assert "a.b: new" in out
    assert "a.b: old" not in out


def test_an_insert_adds_exactly_one_line_to_the_block() -> None:
    """No blank line, no reflow. The block is YAML a human reads and diffs."""
    text = "---\na: 1\nb: 2\n---\n\nbody\n"
    out = set_frontmatter_field(text, "k", "new")
    before, _rest_in, _k1 = split_frontmatter_raw(text)
    after, _rest_out, _k2 = split_frontmatter_raw(out)
    assert len(after.splitlines()) == len(before.splitlines()) + 1
    assert "" not in [line.strip() for line in after.splitlines()], (
        f"a blank line appeared inside the block: {after!r}")


def test_an_empty_block_still_receives_the_field() -> None:
    out = set_frontmatter_field("---\n---\n\nbody\n", "k", "new")
    assert _fm_value(out, "k") == "new"
    assert out.endswith("---\n\nbody\n")


def test_only_the_first_occurrence_in_the_block_is_replaced() -> None:
    """A duplicated key is malformed YAML; the writer must not multiply it."""
    out = set_frontmatter_field("---\nk: one\nk: two\n---\n\nbody\n", "k", "new")
    assert out.count("k: new") == 1
    assert "k: two" in out


def test_a_file_that_ends_at_the_closing_fence_is_handled() -> None:
    text = "---\na: 1\nk: old\n---"
    out = set_frontmatter_field(text, "k", "new")
    assert out.count("---") == 2, f"a second block appeared: {out!r}"
    assert _fm_value(out + "\n", "k") == "new"


def test_everything_outside_the_block_is_byte_identical() -> None:
    text = "---\na: 1\n---\n\n## Notes\n\nsome prose\n\n---\n\nmore prose\n"
    out = set_frontmatter_field(text, "a", "2")
    _front_in, rest_in, kind_in = split_frontmatter_raw(text)
    _front_out, rest_out, kind_out = split_frontmatter_raw(out)
    assert kind_in == kind_out == FM_OK
    assert rest_in == rest_out


# ============================================================
# bump_last_touch_in_text
# ============================================================


def test_bump_replaces_an_existing_last_touch() -> None:
    out = bump_last_touch_in_text(
        "---\nname: Invented Person\nlast_touch: 2026-01-01\n---\n\nbody\n", DATE)
    assert str(_fm_value(out, "last_touch")) == DATE


def test_bump_inserts_when_the_field_is_absent() -> None:
    out = bump_last_touch_in_text("---\nname: Invented Person\n---\n\nbody\n", DATE)
    assert str(_fm_value(out, "last_touch")) == DATE


def test_bump_no_longer_rewrites_a_note_in_the_body() -> None:
    """THE DEFECT, measured: this wrote the date into the Notes line instead."""
    text = ("---\nname: Invented Person\n---\n\n## Notes\n"
            "last_touch: discussed the roadmap on the call\n")
    out = bump_last_touch_in_text(text, DATE)
    assert str(_fm_value(out, "last_touch")) == DATE
    assert "last_touch: discussed the roadmap on the call" in out


def test_bump_no_longer_rewrites_a_quoted_record_in_the_interaction_log() -> None:
    """The realistic route in. An email body is pasted into the log verbatim."""
    text = ("---\nname: Invented Person\n---\n\n## Interaction Log\n\n"
            "### 2026-08-01 Email\nThey pasted a record at us:\n"
            "last_touch: 2019-04-04\n")
    out = bump_last_touch_in_text(text, DATE)
    assert str(_fm_value(out, "last_touch")) == DATE
    assert "last_touch: 2019-04-04" in out, "the quoted record was rewritten"


def test_bump_leaves_a_document_with_no_frontmatter_alone() -> None:
    """The documented policy, and the old code broke it in the replace branch."""
    text = "# Invented Person\n\nlast_touch: never\n"
    assert bump_last_touch_in_text(text, DATE) == text


def test_the_field_the_health_engine_reads_is_the_one_that_gets_written() -> None:
    """Closes the loop the defect broke.

    The failure was not "a string was in the wrong place". It was that
    `calculate_health` parses FRONTMATTER, found no `last_touch`, and reported
    the contact red forever while every write reported success.
    """
    from scripts.utils.crm import parse_frontmatter as crm_parse

    text = ("---\nname: Invented Person\ntype: contact\n---\n\n## Notes\n"
            "last_touch: a sentence, not a date\n")
    out = bump_last_touch_in_text(text, DATE)
    assert str(crm_parse(out).get("last_touch")) == DATE


# ============================================================
# update_owner_in_frontmatter
# ============================================================


def test_owner_is_replaced() -> None:
    out = update_owner("---\nname: X\nowner: old\n---\n\nbody\n", "invented-owner")
    assert _fm_value(out, "owner") == "invented-owner"


def test_owner_is_inserted_when_absent() -> None:
    out = update_owner("---\nname: X\n---\n\nbody\n", "invented-owner")
    assert _fm_value(out, "owner") == "invented-owner"
    assert _fm_value(out, "name") == "X"


def test_a_card_ending_at_the_closing_fence_gets_one_block_not_two() -> None:
    """THE DEFECT, measured: the real fields became body text."""
    text = "---\nname: X\nowner: old\n---"
    out = update_owner(text, "invented-owner")
    assert out.count("---") == 2, f"a second frontmatter block appeared: {out!r}"
    assert _fm_value(out + "\n", "name") == "X", (
        "the card's own fields fell out of frontmatter")
    assert _fm_value(out + "\n", "owner") == "invented-owner"


def test_a_document_with_no_frontmatter_still_gets_one() -> None:
    """CONTROL. This caller's policy differs from crm_autolog's, on purpose."""
    out = update_owner("# X\n\nprose\n", "invented-owner")
    assert out.startswith("---\nowner: invented-owner\n---\n")
    assert "prose" in out


def test_owner_edit_keeps_the_blank_line_before_the_body() -> None:
    text = "---\nname: X\nowner: old\n---\n\nbody\n"
    out = update_owner(text, "invented-owner")
    assert out.endswith("---\n\nbody\n"), (
        "the transfer reflowed the file around the field it changed")


def test_owner_edit_keeps_crlf() -> None:
    text = "---\r\nname: X\r\nowner: old\r\n---\r\n\r\nbody\r\n"
    out = update_owner(text, "invented-owner")
    assert out.endswith("---\r\n\r\nbody\r\n")


def test_owner_edit_survives_three_dashes_in_a_value() -> None:
    text = "---\nnotes: a---b\nowner: old\n---\n\nbody\n"
    out = update_owner(text, "invented-owner")
    assert _fm_value(out, "owner") == "invented-owner"
    assert "notes: a---b" in out


# ============================================================
# The shape, so the third copy cannot come back
# ============================================================


_WRITERS = [
    ("scripts/utils/crm_autolog.py", "bump_last_touch_in_text"),
    ("scripts/transfer-contact.py", "update_owner_in_frontmatter"),
]


@pytest.mark.parametrize(("relpath", "func"), _WRITERS)
def test_each_writer_routes_through_the_shared_helper(relpath: str, func: str) -> None:
    """Asked of the CALL, not of the import.

    A file can import `set_frontmatter_field` and still rewrite the document
    with a local regex; the previous shard lost three mutations to exactly that
    distinction.
    """
    tree = ast.parse((ROOT / relpath).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == func)
    called = {ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert "set_frontmatter_field" in called, (
        f"{relpath}::{func} does not call the shared writer; it is spelling the "
        "scope itself again")


@pytest.mark.parametrize(("relpath", "func"), _WRITERS)
def test_no_writer_runs_a_multiline_substitution_of_its_own(
        relpath: str, func: str) -> None:
    """The mechanism of the defect, not just its symptom.

    `re.sub(..., flags=re.MULTILINE)` inside one of these functions is how both
    of them reached outside the block. The helper owns that call now.
    """
    tree = ast.parse((ROOT / relpath).read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == func)
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and ast.unparse(node.func).startswith("re."):
            pytest.fail(f"{relpath}::{func} calls {ast.unparse(node.func)} directly")


def test_the_sibling_that_was_already_right_is_still_right() -> None:
    """`crm-health.frontmatter_end` fixed this on its own and said why.

    Recorded here so a later tidy-up cannot quietly return it to a substring
    search: its docstring is the only place the earlier post-mortem lives.

    Asked of the PARSED function. The first version of this test grepped the
    file for `text.find("---", 3)` and went red on the docstring, which quotes
    the retired code as history -- the same substring-versus-citation trap that
    cost a test in the previous shard. A grep cannot tell a live call from a
    post-mortem about one.
    """
    tree = ast.parse((ROOT / "scripts" / "crm-health.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "frontmatter_end")
    called = {ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert "_FM_OPEN_RE.match" in called, "the guard stopped requiring an opening fence"
    assert "_FM_CLOSE_RE.search" in called, "the close is no longer an anchored line"
    assert not any(c.endswith(".find") for c in called), (
        f"a substring search came back into frontmatter_end: {sorted(called)}")


def test_the_helper_actually_writes_something() -> None:
    """CONTROL for every "did not touch the body" assertion above.

    A `set_frontmatter_field` that returned its input unchanged would satisfy
    all of them.
    """
    text = "---\na: 1\n---\n\nbody\n"
    assert set_frontmatter_field(text, "a", "2") != text
    assert set_frontmatter_field(text, "brand-new", "x") != text
