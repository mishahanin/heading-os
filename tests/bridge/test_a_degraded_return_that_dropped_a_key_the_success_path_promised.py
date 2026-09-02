"""Three shape claims that only held on the path anyone looks at.

Found by the 2026-08-24 engine audit campaign (shard `scripts-01-p4`), verified
still present and fixed 2026-09-02.

`list_investors` HAS TWO DEGRADED RETURNS AND THEY WERE A KEY SHORT. The success
path emits `sent_total`; the "no shortlist file" and "OSError reading it"
returns did not. MEASURED 2026-09-02 before the fix, all three paths on one tmp
tree: success gave `['counts', 'data_time', 'firms', 'raise_target',
'sent_total', 'total']` and both degraded paths gave the same list minus
`sent_total`. A Python consumer writing `payload["sent_total"]` therefore raised
KeyError on exactly the two paths that are hardest to notice, because both
already answer "nothing here" and an investor program that has not started looks
the same. The browser's own consumer (`web/app.js`, `d.sent_total ? ... : ''`)
happens to be truthiness-tolerant, which is why nothing surfaced this.

`list_investors` DID NOT DOCUMENT THREE KEYS IT EMITS. `sent_total` at the top
level and `sent_date` / `sent_note` on every firm row, all three added by the
Phase 1.36 send-log join, never reached the Returns block.

`dismiss_log_recent` STATED A ROW SHAPE ONE KEY SHORT. Its docstring said
`{conv_id, ts, date, note}`; the rows have carried `topic` since the "Recently
dismissed" footer was written, and the very next paragraph of the same docstring
explains where `topic` comes from.

The two docstring guards below are LIVE-KEY guards, not hand-written lists. Each
calls the function, takes the keys it actually emitted, and requires the
docstring to name each one. A hand-written list is a second thing to keep in
sync and would have gone stale beside the first; a live read cannot. They are
one-directional on purpose: a key emitted and not documented fails, a key
documented and no longer emitted does not, because a docstring may legitimately
name a retired key while explaining why it went.

Nothing here starts the daemon, binds a port, or reaches the network. Every path
is under `tmp_path`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.bridge_daemon.sources.inbox import dismiss_log_recent, mark_dismissed
from scripts.bridge_daemon.sources.investors import (
    PROGRAM_DIR,
    SEND_LOG_FILE,
    list_investors,
)

SHORTLIST = (
    "raise posture: $10-20M anchor\n\n"
    "## Europe (2)\n\n"
    "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
    "|---|------|------|----|--------|-----|-------|\n"
    "| 8 | Northwind Capital | Independent growth VC | Hamburg | EUR 20-60M | HIGH | warm path open |\n"
    "| 9 | Kestrel Ventures | Sovereign fund | Tallinn | EUR 10-30M | MED | cold |\n"
)


def _shortlist_path(root: Path) -> Path:
    return root / PROGRAM_DIR / "00-master-shortlist-v1.md"


def _write_program(root: Path, *, sent_firm_nums=()) -> None:
    """A real two-firm program, optionally with send-log entries joined in."""
    path = _shortlist_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SHORTLIST, encoding="utf-8")
    if sent_firm_nums:
        log = root / PROGRAM_DIR / SEND_LOG_FILE
        log.write_text("".join(
            json.dumps({"firm_num": n, "date": "2026-08-14",
                        "note": "first touch", "ts": f"2026-08-14T09:0{i}:00+00:00"}) + "\n"
            for i, n in enumerate(sent_firm_nums)
        ), encoding="utf-8")


def _shape_block(doc: str) -> str:
    """The brace-delimited SHAPE a docstring declares, and nothing else.

    Scoped rather than whole-docstring, and finding out why is worth the
    helper. Both docstrings now carry a paragraph naming the key that was
    missing and the date it was added, which is the house way of retiring a
    wrong claim. A whole-docstring match would be satisfied by that paragraph
    forever: deleting `"sent_total": int` from the Returns block would leave
    this guard green, so the guard would stop pinning the thing it exists for.
    MEASURED 2026-09-02, which is why the scoping is here and not a preference.

    First `{` to its matching `}`, so the nested `"counts": {...}` is included
    rather than ending the scan. Raises when the docstring declares no shape at
    all, because a reworded docstring must fail loudly rather than quietly
    match the empty string and pass everything.
    """
    doc = doc or ""
    start = doc.find("{")
    if start == -1:
        raise AssertionError(
            "the docstring declares no braced shape, so this guard has nothing "
            "to check; it was reworded and needs a human")
    depth = 0
    for i in range(start, len(doc)):
        if doc[i] == "{":
            depth += 1
        elif doc[i] == "}":
            depth -= 1
            if depth == 0:
                return doc[start:i + 1]
    raise AssertionError("unbalanced braces in the declared shape")


def _documents(shape: str, key: str) -> bool:
    """Is `key` named as a whole word in the declared shape?

    Word-boundary rather than substring, because `firm` is a substring of
    `firm_raw` and `total` of `sent_total`: a substring test would report the
    narrower key as documented on the strength of the wider one, which is the
    direction that hides a miss. `_` is a word character, so `\\bfirm\\b` does
    not match inside `firm_raw` and the two are told apart.
    """
    return re.search(rf"\b{re.escape(key)}\b", shape or "") is not None


# ============================================================
# 1. list_investors: the degraded returns and the success return agree
# ============================================================

def test_the_shortlist_missing_return_carries_every_key_the_success_return_does(tmp_path):
    """The measured defect, first of its two shapes: no shortlist file at all."""
    degraded = list_investors(tmp_path)
    _write_program(tmp_path)
    success = list_investors(tmp_path)

    missing = set(success) - set(degraded)
    assert not missing, (
        f"the no-shortlist return is {sorted(missing)} short of the success "
        f"return, so a consumer reading those keys raises KeyError on the path "
        f"that looks like an empty program")
    assert degraded["sent_total"] == 0


def test_the_unreadable_shortlist_return_carries_every_key_too(tmp_path):
    """The second shape: the file resolves but `read_text` raises OSError.

    A directory standing where the shortlist should be is the cheapest OSError
    that needs no permission games and no root, and `exists()` is true for it,
    so control reaches the second `return` and not the first.
    """
    path = _shortlist_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()
    degraded = list_investors(tmp_path)
    assert degraded["total"] == 0, "the OSError branch was not the one taken"

    path.rmdir()
    _write_program(tmp_path)
    success = list_investors(tmp_path)

    missing = set(success) - set(degraded)
    assert not missing, (
        f"the OSError return is {sorted(missing)} short of the success return")
    assert degraded["sent_total"] == 0


def test_sent_total_still_counts_the_firms_that_were_actually_sent(tmp_path):
    """The ANCHOR for both tests above.

    Without this, `sent_total = 0` hardcoded on the success path satisfies the
    key-set comparisons perfectly while destroying the number they exist to
    carry. Two firms in the program, one in the send log, so the answer is 1
    and neither 0 nor 2.
    """
    _write_program(tmp_path, sent_firm_nums=(8,))
    payload = list_investors(tmp_path)

    assert payload["total"] == 2, "the fixture stopped parsing both firms"
    assert payload["sent_total"] == 1
    sent = [f for f in payload["firms"] if f["sent_date"]]
    assert [f["num"] for f in sent] == [8]
    assert sent[0]["sent_note"] == "first touch"


# ============================================================
# 2 + 3. Live-key docstring guards
# ============================================================

def test_every_top_level_key_list_investors_emits_is_named_in_its_docstring(tmp_path):
    """`sent_total` was emitted and undocumented. Read the keys, not a list."""
    _write_program(tmp_path, sent_firm_nums=(8,))
    payload = list_investors(tmp_path)
    shape = _shape_block(list_investors.__doc__)

    assert "sent_total" in payload, (
        "the fixture no longer exercises the documented shape, so this guard "
        "would pass over a payload that never had the key")
    undocumented = sorted(k for k in payload if not _documents(shape, k))
    assert not undocumented, (
        f"list_investors emits {undocumented} and its docstring never names "
        f"them; a caller reading the Returns block does not know they exist")


def test_every_firm_row_key_list_investors_emits_is_named_in_its_docstring(tmp_path):
    """`sent_date` and `sent_note`, the other half of the same omission."""
    _write_program(tmp_path, sent_firm_nums=(8,))
    firms = list_investors(tmp_path)["firms"]
    shape = _shape_block(list_investors.__doc__)

    assert firms, "no firm rows, so this guard measured nothing"
    emitted = sorted({k for f in firms for k in f})
    assert "sent_date" in emitted and "sent_note" in emitted, (
        "the send-log join stopped running, so the keys this guard was written "
        "for are absent and it is now green over the wrong corpus")
    undocumented = [k for k in emitted if not _documents(shape, k)]
    assert not undocumented, (
        f"firm rows carry {undocumented} and the docstring's Returns block "
        f"never names them")


def test_every_key_dismiss_log_recent_emits_is_named_in_its_docstring(tmp_path):
    """`topic` was emitted, rendered in the footer, and left out of the shape."""
    mark_dismissed(tmp_path, "conv-northwind-01", note="handled on the call")
    rows = dismiss_log_recent(tmp_path)
    shape = _shape_block(dismiss_log_recent.__doc__)

    assert rows, "no dismiss rows, so this guard measured nothing"
    emitted = sorted({k for r in rows for k in r})
    assert "topic" in emitted, (
        "the row no longer carries `topic`, so this guard is green over a "
        "shape that never had the key it was written for")
    undocumented = [k for k in emitted if not _documents(shape, k)]
    assert not undocumented, (
        f"dismiss_log_recent rows carry {undocumented} and its stated row "
        f"shape names neither")


def test_the_docstring_guards_still_fail_a_key_that_is_not_documented(tmp_path):
    """The ANCHOR for the three guards above.

    A guard that reports "all documented" over a docstring is indistinguishable
    from one whose word-boundary match answers True for everything. Feed
    `_documents` a key no docstring could contain and a key both certainly do,
    so a helper degraded to `return True` fails here rather than passing
    silently everywhere.
    """
    shape = _shape_block(list_investors.__doc__)
    assert _documents(shape, "raise_target")
    assert not _documents(shape, "a_key_no_docstring_names")
    # And the scoping itself: a key named ONLY in the prose below the shape is
    # not documented shape. `KeyError` appears in the history paragraph and in
    # no braced shape, which is the exact trap a whole-docstring match falls in.
    assert "KeyError" in (list_investors.__doc__ or "")
    assert not _documents(shape, "KeyError")
    # `firm` must not be credited to `firm_raw`, which is what a substring
    # test would do and what would let a real miss through.
    assert not _documents("only firm_raw appears here", "firm")
