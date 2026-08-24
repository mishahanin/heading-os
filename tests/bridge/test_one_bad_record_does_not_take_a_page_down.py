"""The list paths trusted their inputs; the readers beside them did not.

Found by the 2026-08-23 engine audit, which called this its systemic finding
and was right. Every one of these four modules validates carefully at its
single-item boundary -- ``read_skill``, ``read_one_contact``, ``mark_critical``
-- and then the LIST path consumes the same bytes with weaker guards. One
malformed file or record took down a whole dashboard page:

* ``capabilities.list_capabilities`` caught ``OSError`` around
  ``read_text(encoding="utf-8")``. ``UnicodeDecodeError`` is a ``ValueError``.
  ``read_skill``, twenty lines below, catches both -- so the author knew the
  failure mode and the list copy missed it.
* ``contacts._contact_record``: the same handler, the same gap.
* ``conversations.list_conversations``: ``int(c.get("message_count") or 0)``
  on ``"three"``, ``analysis.get(...)`` when ``analysis`` is a string,
  ``(5).lower()`` when a priority arrives as a number. The file guarded
  against invalid JSON and not against valid JSON of the wrong shape.
* ``critical._active_entries``: no schema check, so ``{"id": "x", "ts": null}``
  reached ``sort(key=lambda e: e.get("ts", ""))`` and raised
  ``'<' not supported between instances of 'NoneType' and 'str'``.

Two more of the same family, both silent rather than loud:

* ``contacts`` swallowed a failing exec registry with a bare
  ``except Exception: registry_slugs = []``, and every exec whose contacts live
  only in a per-exec mirror disappeared with no trace. The workspace's own
  rule: no handler swallows without logging or re-raising.
* ``conversations._parse_ts`` read a stamp with no offset in LOCAL time and one
  with a ``Z`` as absolute, so a fetch mixing both forms sorted wrong, silently.

The shared rule these encode: a scan over many records isolates per record. One
bad row costs its own row and nothing else.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon.sources import capabilities as CAP  # noqa: E402
from scripts.bridge_daemon.sources import contacts as CON      # noqa: E402
from scripts.bridge_daemon.sources import conversations as CNV  # noqa: E402
from scripts.bridge_daemon.sources import critical as CRIT     # noqa: E402


# ============================================================
# capabilities
# ============================================================

def _skill(root: Path, slug: str, body: bytes) -> None:
    d = root / ".claude" / "skills" / slug
    d.mkdir(parents=True)
    (d / "SKILL.md").write_bytes(body)


def test_one_undecodable_skill_does_not_hide_the_catalog(tmp_path):
    _skill(tmp_path, "good", b"---\nname: good\ndescription: A good skill.\n---\nbody\n")
    _skill(tmp_path, "bad", b"\xff\xfe---\nname: bad\n---\n")     # not UTF-8
    got = CAP.list_capabilities(tmp_path)
    assert [s["slug"] for s in got["skills"]] == ["good"], got
    assert got["count"] == 1


def test_a_skill_md_with_no_trailing_newline_still_carries_its_metadata(tmp_path):
    """Without it the skill listed under its directory name with a blank
    description -- a plausible row with nothing behind it, no error anywhere."""
    _skill(tmp_path, "terse", b"---\nname: Terse Skill\ndescription: Ends at the fence.\n---")
    got = CAP.list_capabilities(tmp_path)
    assert got["skills"][0]["name"] == "Terse Skill", got["skills"][0]
    assert got["skills"][0]["description"] == "Ends at the fence", got["skills"][0]


def test_the_trailing_newline_case_still_works(tmp_path):
    _skill(tmp_path, "normal", b"---\nname: Normal\ndescription: Has one.\n---\nbody\n")
    assert CAP.list_capabilities(tmp_path)["skills"][0]["name"] == "Normal"


def test_the_closing_fence_may_still_carry_trailing_content(tmp_path):
    """The reason this parser is not the canonical one; do not regress it."""
    _skill(tmp_path, "gen", b"---\nname: Gen\ndescription: d\n---<!-- AUTO-GENERATED -->\nbody\n")
    assert CAP.list_capabilities(tmp_path)["skills"][0]["name"] == "Gen"


# ============================================================
# contacts
# ============================================================

def _contact(dirpath: Path, slug: str, body: bytes) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / f"{slug}.md").write_bytes(body)


def test_one_undecodable_contact_does_not_hide_the_roster(tmp_path):
    data_root = tmp_path / "data"
    c = data_root / "crm" / "contacts"
    _contact(c, "ada-lovelace", b"---\ncompany: Analytical\n---\n# Ada Lovelace\n")
    _contact(c, "mangled", b"---\ncompany: \xe9\xe9\xe9\n---\n# Mangled\n")   # latin-1
    got = CON.list_contacts(tmp_path, data_root=data_root)
    assert [r["slug"] for r in got["contacts"]] == ["ada-lovelace"], got
    assert got["total"] == 1


def test_an_unreadable_exec_registry_is_logged_not_swallowed(tmp_path, monkeypatch, caplog):
    data_root = tmp_path / "data"
    _contact(data_root / "crm" / "contacts", "ada-lovelace", b"# Ada Lovelace\n")

    def boom():
        raise RuntimeError("registry file is corrupt")

    monkeypatch.setattr(CON, "get_all_active_exec_slugs", boom)
    with caplog.at_level("WARNING"):
        got = CON.list_contacts(tmp_path, data_root=data_root)
    assert got["total"] == 1, "the CEO's own contacts must survive"
    assert any("registry" in r.message for r in caplog.records), (
        "the exec registry failed and the page reported a smaller fleet in "
        "silence; nothing was logged"
    )


def test_an_invalid_owner_and_a_missing_mirror_read_differently(tmp_path):
    """Both used to be 'invalid owner', sending anyone debugging a contact
    whose repo is simply not cloned here after a validation bug."""
    data_root = tmp_path / "data"
    (data_root / "crm" / "contacts").mkdir(parents=True)
    bad = CON.read_one_contact(tmp_path, "Not A Slug!", "ada-lovelace",
                               data_root=data_root)
    absent = CON.read_one_contact(tmp_path, "example-exec", "ada-lovelace",
                                  data_root=data_root)
    assert bad["error"] == "invalid owner", bad
    assert absent["error"] != "invalid owner", absent
    assert "machine" in absent["error"], absent


def test_the_list_docstring_names_the_source_that_wins():
    """Only the SUMMARY line is scanned. The body legitimately quotes the old
    wording to explain the correction, and a whole-docstring match would flag
    that -- the same self-matching trap a guard test hit earlier in this
    campaign."""
    doc = CON.list_contacts.__doc__ or ""
    summary = doc.strip().splitlines()[0]
    assert "crm-central" not in summary, (
        f"the summary line credits the FALLBACK source as the one scanned: "
        f"{summary!r}"
    )
    assert "mirror" in doc, "the body no longer states the real resolution order"


# ============================================================
# conversations
# ============================================================

def _fetch(tmp_path: Path, conversations: list) -> Path:
    p = tmp_path / CNV.LATEST_FETCH_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"conversations": conversations}), encoding="utf-8")
    return tmp_path


def test_valid_json_of_the_wrong_shape_does_not_raise(tmp_path):
    root = _fetch(tmp_path, [
        {"id": "a", "message_count": "three"},
        {"id": "b", "message_count": ["not", "a", "number"]},
        {"id": "c", "analysis": "oops"},
        {"id": "d", "priority": 5},
        {"id": "e", "analysis": {"category": 7}},
        {"id": "f", "direction": None, "topic": 12},
        {"id": "g", "crm_context": "a string"},
        {"id": "h", "message_count": 4, "priority": "high"},
    ])
    got = CNV.list_conversations(root)
    assert got["total"] == 8
    by_id = {c["id"]: c for c in got["conversations"]}
    assert by_id["a"]["message_count"] == 0
    assert by_id["b"]["message_count"] == 0
    assert by_id["c"]["priority"] == ""
    assert by_id["d"]["priority"] == "", "a numeric priority must not become '5'"
    assert by_id["e"]["category"] == ""
    assert by_id["f"]["topic"] == "(no subject)"
    assert by_id["g"]["contact_name"] is None
    assert by_id["h"]["message_count"] == 4 and by_id["h"]["priority"] == "high"


def test_a_negative_or_boolean_count_reads_as_zero(tmp_path):
    root = _fetch(tmp_path, [{"id": "a", "message_count": -3},
                             {"id": "b", "message_count": True}])
    got = {c["id"]: c["message_count"] for c in CNV.list_conversations(root)["conversations"]}
    assert got == {"a": 0, "b": 0}


def test_a_naive_stamp_is_read_as_utc_not_local():
    """Same instant, two spellings: they must sort as equal, not four hours
    apart on a +04:00 host."""
    assert CNV._parse_ts("2026-06-01T10:00:00") == CNV._parse_ts("2026-06-01T10:00:00Z")


def test_conversations_sort_newest_first_across_both_stamp_forms(tmp_path):
    root = _fetch(tmp_path, [
        {"id": "older", "latest_datetime": "2026-06-01T09:00:00"},
        {"id": "newer", "latest_datetime": "2026-06-01T11:00:00Z"},
    ])
    order = [c["id"] for c in CNV.list_conversations(root)["conversations"]]
    assert order == ["newer", "older"], order


def test_the_dead_priority_map_is_gone():
    assert not hasattr(CNV, "PRIORITY_ORDER"), (
        "PRIORITY_ORDER is back and unread; either use it or drop it"
    )
    assert CNV.CONVERSATION_PRIORITIES, "the expected vocabulary is still documented"


def test_an_unexpected_priority_is_shown_not_dropped(tmp_path):
    """Passing it through is the choice: a filter would hide a pipeline change."""
    root = _fetch(tmp_path, [{"id": "a", "priority": "CRITICAL"}])
    got = CNV.list_conversations(root)
    assert got["conversations"][0]["priority"] == "critical"
    assert got["counts"]["by_priority"] == {"critical": 1}


# ============================================================
# critical
# ============================================================

def _append_raw(root: Path, obj: dict) -> None:
    p = root / CRIT.CRITICAL_LOG_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj) + "\n")


def test_a_null_timestamp_does_not_500_the_page(tmp_path):
    CRIT.mark_critical(tmp_path, "task", "ref-1", "A real item")
    _append_raw(tmp_path, {"id": "handedited", "kind": "task", "ts": None})
    got = CRIT.list_critical(tmp_path)
    assert got["total"] == 2, got
    assert got["items"][0]["label"] == "A real item", "real items sort above junk"


def test_recent_unmarked_survives_the_same_row(tmp_path):
    CRIT.mark_critical(tmp_path, "task", "ref-1", "A real item")
    item_id = CRIT.list_critical(tmp_path)["items"][0]["id"]
    CRIT.unmark_critical(tmp_path, item_id)
    _append_raw(tmp_path, {"id": "handedited", "undo": True, "ts": None})
    _append_raw(tmp_path, {"id": "handedited", "kind": "task", "label": "x", "ts": None})
    _append_raw(tmp_path, {"id": "handedited", "undo": True, "ts": None})
    rows = CRIT.recent_unmarked(tmp_path)
    assert any(r["id"] == item_id for r in rows), rows


def test_a_hand_edited_tombstone_still_tombstones(tmp_path):
    """`entry.get("undo") is True` resurrected an item unmarked by anything
    that does not serialise Python's True -- and handed it a junk payload."""
    CRIT.mark_critical(tmp_path, "task", "ref-1", "Unmark me")
    item_id = CRIT.list_critical(tmp_path)["items"][0]["id"]
    _append_raw(tmp_path, {"id": item_id, "undo": 1, "ts": "2026-08-24T00:00:00+00:00"})
    assert CRIT.list_critical(tmp_path)["total"] == 0


def test_an_oversized_log_shows_the_newest_marks_and_says_it_truncated(tmp_path, monkeypatch):
    """It used to show nothing, forever, while writes kept succeeding."""
    for i in range(60):
        CRIT.mark_critical(tmp_path, "task", f"ref-{i}", f"Item {i}",
                           note="padding " * 20)
    monkeypatch.setattr(CRIT, "CRITICAL_LOG_MAX_BYTES", 3_000)
    got = CRIT.list_critical(tmp_path)
    assert got["truncated"] is True
    assert got["total"] > 0, "the page is empty again"
    assert got["items"][0]["label"] == "Item 59", "the newest mark must survive"


def test_an_ordinary_log_is_not_flagged_truncated(tmp_path):
    CRIT.mark_critical(tmp_path, "task", "ref-1", "Only item")
    got = CRIT.list_critical(tmp_path)
    assert got["truncated"] is False and got["total"] == 1


def test_a_non_string_note_is_a_validation_error_not_a_crash(tmp_path):
    got = CRIT.mark_critical(tmp_path, "task", "ref-1", "Label", note=5)
    assert got["ok"] is False and "note" in got["error"], got


def test_the_ordinary_mark_and_unmark_round_trip_still_works(tmp_path):
    """Anchor: every guard above is worthless if the happy path broke."""
    r = CRIT.mark_critical(tmp_path, "deal", "acme", "Acme renewal",
                           source_page="#/pipeline", note="chase Monday")
    assert r["ok"] is True
    listed = CRIT.list_critical(tmp_path)
    assert listed["total"] == 1
    assert listed["items"][0]["note"] == "chase Monday"
    assert CRIT.unmark_critical(tmp_path, r["id"])["ok"] is True
    assert CRIT.list_critical(tmp_path)["total"] == 0
    assert [x["id"] for x in CRIT.recent_unmarked(tmp_path)] == [r["id"]]
