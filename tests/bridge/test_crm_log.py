"""Unit tests for the CRM-log finalizer (POST /inbox/crm-log).

The calls pass no root. `log_to_crm` dropped its dead `workspace_root`
parameter on 2026-08-24 -- it was never read -- and the autouse fixture in
`conftest.py` points `HEADING_OS_DATA` at `tmp_path`, so `get_data_root()`
resolves to the same tree these helpers write into.
"""
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.bridge_daemon.finalizers.crm_log import log_to_crm
from scripts.utils.workspace import get_default_tz


def _write_fetch(workspace_root, conversations):
    d = workspace_root / "outputs" / "operations" / "email-intelligence"
    d.mkdir(parents=True, exist_ok=True)
    (d / "_latest-fetch.json").write_text(
        json.dumps({"run_info": {}, "conversations": conversations}),
        encoding="utf-8",
    )


def _write_contact(workspace_root, slug):
    d = workspace_root / "crm" / "contacts"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.md").write_text(
        "---\nlast_touch: 2026-01-01\n---\n\n# Contact\n\nNotes.\n\n## Interaction Log\n",
        encoding="utf-8",
    )


def _conv(conv_id, slug=None, topic="A thread"):
    c = {"id": conv_id, "topic": topic, "latest_datetime": "2026-05-20T09:00:00+00:00"}
    if slug:
        c["crm_context"] = {"contact_slug": slug, "name": "X"}
    return c


def test_log_to_crm_happy_path(tmp_path):
    _write_contact(tmp_path, "ada-lovelace")
    _write_fetch(tmp_path, [_conv("c1", "ada-lovelace", "Demo follow-up")])
    r = log_to_crm("c1")
    assert r["ok"] is True
    assert r["slug"] == "ada-lovelace"
    text = (tmp_path / "crm" / "contacts" / "ada-lovelace.md").read_text(encoding="utf-8")
    assert "Demo follow-up" in text
    assert "last_touch: 2026-05-20" in text
    assert "Logged from the Inbox dashboard." in text


def test_log_to_crm_is_idempotent(tmp_path):
    _write_contact(tmp_path, "ada-lovelace")
    _write_fetch(tmp_path, [_conv("c1", "ada-lovelace")])
    assert log_to_crm("c1")["ok"] is True
    second = log_to_crm("c1")
    assert second["ok"] is False
    assert "already logged" in second["error"]
    # Exactly one entry written, not two.
    text = (tmp_path / "crm" / "contacts" / "ada-lovelace.md").read_text(encoding="utf-8")
    assert text.count("Logged from the Inbox dashboard.") == 1


def test_log_to_crm_no_contact_link(tmp_path):
    _write_fetch(tmp_path, [_conv("c1", slug=None)])
    r = log_to_crm("c1")
    assert r["ok"] is False
    assert "no CRM contact" in r["error"]


def test_log_to_crm_conv_not_in_fetch(tmp_path):
    _write_fetch(tmp_path, [_conv("c1", "ada-lovelace")])
    r = log_to_crm("ghost")
    assert r["ok"] is False
    assert "not in latest fetch" in r["error"]


def test_log_to_crm_missing_contact_file(tmp_path):
    _write_fetch(tmp_path, [_conv("c1", "no-such-contact")])
    r = log_to_crm("c1")
    assert r["ok"] is False
    assert "not found" in r["error"]


def test_log_to_crm_rejects_bad_slug(tmp_path):
    """A traversal-shaped slug is rejected before any filesystem access."""
    _write_fetch(tmp_path, [_conv("c1", "../etc/passwd")])
    r = log_to_crm("c1")
    assert r["ok"] is False
    assert "invalid contact slug" in r["error"]


def test_log_to_crm_missing_conv_id(tmp_path):
    assert log_to_crm("")["ok"] is False
    assert log_to_crm("x" * 600)["ok"] is False


def test_the_two_conv_id_guards_are_refusing_for_their_own_reasons(tmp_path):
    """The test above is a straw man on its second line.

    `log_to_crm("x" * 600)` returns `ok: False` whether or not the length
    guard exists, because a 600-character id is also not in the latest fetch.
    Deleting `if len(conv_id) > 500` was measured on 2026-08-31:

        owner tests/bridge/test_crm_log.py: 7 passed in 0.91s
        tests/bridge                      : 1312 passed, 1 skipped in 52.12s
        VERDICT: SURVIVED

    measured over the owning file and all of `tests/bridge`. The guard exists
    so an oversized id never
    reaches `mark_crm_logged`, which appends it to the dedupe log as a key,
    so the fix is to assert the error each guard actually emits rather than
    the boolean they share. The 500-boundary is checked ON the line, since a
    `>=` typo is the way a length bound usually breaks.
    """
    _write_fetch(tmp_path, [_conv("c1", "ada-lovelace")])
    _write_contact(tmp_path, "ada-lovelace")

    assert log_to_crm("x" * 600)["error"] == "conv_id too long"
    assert log_to_crm("x" * 501)["error"] == "conv_id too long"
    # 500 is legal, so it must fall through to the NEXT check, not this one.
    assert log_to_crm("x" * 500)["error"] == "conversation not in latest fetch"
    # And the empty/blank cases are the other guard, not the length one.
    for blank in ("", "   ", "\t\n"):
        assert log_to_crm(blank)["error"] == "conv_id is required"
    for wrong_type in (None, 42, ["c1"], {"id": "c1"}):
        assert log_to_crm(wrong_type)["error"] == "conv_id is required"
    # A refused id must leave no trace in the dedupe log.
    from scripts.bridge_daemon.sources.inbox import CRM_LOGGED_FILE
    assert not (tmp_path / CRM_LOGGED_FILE).exists()


def test_the_slug_guard_refuses_before_the_filesystem_is_touched(tmp_path):
    """`test_log_to_crm_rejects_bad_slug` says "before any filesystem access"
    and only reads the error string, so nothing observed the access.

    A traversal slug is refused by `_SLUG_RE`; that much the sibling test
    does pin (weakening the pattern to `^.{1,200}$` was CAUGHT). What was
    unobserved is the ordering claim in its own docstring, which is the part
    that matters if the regex is ever moved below the `exists()` call.
    """
    _write_fetch(tmp_path, [_conv("c1", "../../etc/passwd")])
    seen: list[str] = []
    real_exists = Path.exists

    def _recording(self, *a, **k):
        seen.append(str(self))
        return real_exists(self, *a, **k)

    with patch.object(Path, "exists", _recording):
        result = log_to_crm("c1")

    assert result["ok"] is False
    assert "invalid contact slug" in result["error"]
    assert not any("etc/passwd" in p or "contacts" in p for p in seen), (
        f"the slug reached a filesystem call before it was refused: {seen}")


# `_interaction_date` writes a date into a CRM contact file, and its whole
# reason for existing is that the old code measured LENGTH only, so
# `not-a-date-xx` was written verbatim as an interaction date. Nothing tested
# the replacement. Reverting it to the length-only form was measured
# 2026-08-31:
#
#     owner tests/bridge/test_crm_log.py: 7 passed in 1.00s
#     tests/bridge                      : 1312 passed, 1 skipped in 53.94s
#     VERDICT: SURVIVED
#
# measured over the owning file and all of `tests/bridge`. Every fixture in
# this file passes a clean
# `2026-05-20T09:00:00+00:00`, so the fallback branch was never entered by
# any test: the corpus had no malformed member.

@pytest.mark.parametrize("raw,reason", [
    ("not-a-date-xx", "ten characters, none of them a date"),
    ("20260824", "the compact ISO form date.fromisoformat accepts on 3.11"),
    ("2026082412", "ten digits, which slice to a plausible-looking string"),
    ("2026-13-01T00:00:00", "month 13"),
    ("2026-02-30T00:00:00", "February 30th, a shape-valid non-date"),
    ("2026-02-29T00:00:00", "February 29th in a non-leap year"),
    ("2026-00-10T00:00:00", "month zero"),
    ("2026-05-00T00:00:00", "day zero"),
    ("0000-00-00T00:00:00", "all zeros"),
    ("", "absent"),
    ("2026-05", "too short"),
    ("2026/05/20T00:00:00", "slashes, not hyphens"),
    ("yesterday!!", "prose"),
])
def test_a_malformed_timestamp_falls_back_to_today(tmp_path, raw, reason):
    """Not the raw head, and not a crash: today's date."""
    today = datetime.now(get_default_tz()).strftime("%Y-%m-%d")
    _write_contact(tmp_path, "ada-lovelace")
    conv = _conv("c1", "ada-lovelace")
    conv["latest_datetime"] = raw
    _write_fetch(tmp_path, [conv])

    result = log_to_crm("c1")

    assert result["ok"] is True
    assert result["date"] == today, f"{reason}: got {result['date']!r}"
    text = (tmp_path / "crm" / "contacts" / "ada-lovelace.md").read_text(
        encoding="utf-8")
    assert f"last_touch: {today}" in text
    head = raw[:10]
    assert not head or head == today or head not in text, (
        f"the malformed value {head!r} was written into the contact file")


@pytest.mark.parametrize("raw,expected", [
    ("2026-05-20T09:00:00+00:00", "2026-05-20"),
    ("2026-05-20", "2026-05-20"),
    ("2028-02-29T00:00:00", "2028-02-29"),   # 2028 is a leap year, 2026 is not
    ("2026-12-31T23:59:59Z", "2026-12-31"),
    ("2026-01-01T00:00:00", "2026-01-01"),
])
def test_a_well_formed_timestamp_is_used_verbatim(tmp_path, raw, expected):
    """The guard must not have been bought by rejecting real dates too.

    Both boundary days of the year and the leap day are here on purpose: a
    calendar check written with the wrong bounds refuses exactly these and
    would otherwise be invisible behind the fallback, which returns a
    perfectly valid date.
    """
    _write_contact(tmp_path, "ada-lovelace")
    conv = _conv("c1", "ada-lovelace")
    conv["latest_datetime"] = raw
    _write_fetch(tmp_path, [conv])
    assert log_to_crm("c1")["date"] == expected


def test_a_non_leap_year_february_29th_falls_back(tmp_path):
    """The one case that separates a real calendar check from a regex.

    `2027-02-29` matches `^\\d{4}-\\d{2}-\\d{2}$` perfectly and is not a day.
    A shape-only guard writes it into the contact file; the `date(...)`
    construction is what refuses it.
    """
    today = datetime.now(get_default_tz()).strftime("%Y-%m-%d")
    _write_contact(tmp_path, "ada-lovelace")
    conv = _conv("c1", "ada-lovelace")
    conv["latest_datetime"] = "2027-02-29T10:00:00"
    _write_fetch(tmp_path, [conv])
    assert log_to_crm("c1")["date"] == today
