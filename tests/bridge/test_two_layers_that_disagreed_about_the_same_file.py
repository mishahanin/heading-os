"""Shard 01-p3: three readers where one layer promised what the next could not keep.

Each finding here is the same shape - two pieces of one module, each correct
read on its own, contradicting each other across the seam:

* ``inbox.read_inbox`` guarded every read of the hand-editable fetch file with
  ``as_mapping`` EXCEPT ``run_info``, where it used ``or {}``. ``or`` only
  substitutes for a FALSY value, so a ``"run_info"`` that arrived as a string
  passed straight through and the following ``.get`` raised ``AttributeError``
  - which no ``except (json.JSONDecodeError, OSError)`` catches, so the whole
  /inbox endpoint 500'd. Its own siblings ``_inbox_row`` and
  ``read_conversation`` already carry a comment explaining exactly this.

* ``conversations.list_conversations`` returned ``"total": len(raw)`` and three
  count maps accumulated before the row cap, next to a ``conversations`` list
  truncated at 100. A dashboard rendering ``total`` beside the rows showed a
  number the rows could never reach, and the sibling endpoint ``contacts.py``
  defines ``total`` as the RETURNED set - so the two endpoints gave one word
  two meanings. The cap was also silent: nothing anywhere said rows had been
  dropped.

* ``contacts._is_contact_file`` lowered the whole filename before testing
  ``.endswith(".md")``, i.e. it was written to ACCEPT ``Jane.MD``. Neither
  layer around it can honour that. ``glob("*.md")`` is case-sensitive on
  posix, so on Linux the file never reached the test and vanished with no log
  line; on Windows, where pathlib globs case-insensitively, it was listed with
  ``slug="Jane"`` and then ``read_one_contact`` - which only ever opens
  ``{slug}.md``, and whose ``CONTACT_SLUG_RE`` is lowercase-only - could never
  open the row.

Run: python3 -m pytest tests/bridge/test_two_layers_that_disagreed_about_the_same_file.py
"""
import json
import logging
from pathlib import Path

import pytest

import scripts.bridge_daemon.sources.contacts as contacts_src
import scripts.bridge_daemon.sources.conversations as conv_src
from scripts.bridge_daemon.sources.contacts import (
    _is_contact_file,
    list_contacts,
    read_one_contact,
)
from scripts.bridge_daemon.sources.conversations import (
    CONVERSATIONS_ROW_CAP,
    list_conversations,
)
from scripts.bridge_daemon.sources.inbox import read_inbox

FETCH_REL = "outputs/operations/email-intelligence/_latest-fetch.json"


def _write_fetch(root: Path, payload: dict) -> Path:
    p = root / FETCH_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _conv(cid: str, **over) -> dict:
    """One well-formed conversation the /inbox reader will band as needs-you."""
    base = {
        "id": cid,
        "topic": f"Topic {cid}",
        "priority": "P1",
        "direction": "inbound",
        "message_count": 2,
        "latest_datetime": "2026-08-24T09:00:00+00:00",
        "analysis": {"category": "deal", "summary": "Wants a call.",
                     "priority": "high"},
    }
    base.update(over)
    return base


# ============================================================
# Finding 1 - the one read of the fetch file that was not shape-guarded
# ============================================================

@pytest.mark.parametrize("bad", [
    "2026-08-24T10:00:00Z",   # a bare string: the reported reproduction
    ["2026-08-24T10:00:00Z"],
    12345,
    1.5,
    True,
])
def test_a_truthy_non_dict_run_info_does_not_take_the_endpoint_down(tmp_path, bad):
    _write_fetch(tmp_path, {"run_info": bad, "conversations": [_conv("c1")]})
    # Before the fix this raised AttributeError out of read_inbox.
    result = read_inbox(tmp_path)
    assert result["data_time"] is None
    assert [r["id"] for r in result["bands"]["needs-you"]] == ["c1"]


def test_a_malformed_run_info_does_not_empty_the_bands(tmp_path):
    """The degradation is scoped to data_time, not to the whole listing.

    A guard that returned the empty payload here would trade a 500 for a
    silently blank Inbox, which is the worse failure: the CEO reads an empty
    page as 'nothing needs me'.
    """
    _write_fetch(tmp_path, {"run_info": "nope",
                            "conversations": [_conv("a"), _conv("b"),
                                              _conv("c", priority="P3")]})
    result = read_inbox(tmp_path)
    assert result["counts"] == {"needs-you": 2, "fyi": 1, "noise": 0}


def test_a_well_formed_run_info_still_supplies_data_time(tmp_path):
    _write_fetch(tmp_path, {"run_info": {"timestamp": "2026-08-24T10:00:00Z"},
                            "conversations": []})
    assert read_inbox(tmp_path)["data_time"] == "2026-08-24T10:00:00Z"


def test_a_run_info_dict_without_a_timestamp_gives_none(tmp_path):
    _write_fetch(tmp_path, {"run_info": {"host": "laptop"}, "conversations": []})
    assert read_inbox(tmp_path)["data_time"] is None


@pytest.mark.parametrize("payload", [
    {"conversations": []},                 # key absent
    {"run_info": None, "conversations": []},   # explicit null
    {"run_info": {}, "conversations": []},     # empty dict
    {"run_info": "", "conversations": []},     # falsy string: the `or {}` case
])
def test_the_falsy_run_info_forms_keep_their_old_answer(tmp_path, payload):
    """The old `or {}` handled every falsy form. The fix must not regress them."""
    _write_fetch(tmp_path, payload)
    assert read_inbox(tmp_path)["data_time"] is None


# ============================================================
# Finding 2 - a total that counted rows the response never returned
# ============================================================

def test_total_equals_the_rows_returned(tmp_path):
    _write_fetch(tmp_path, {"conversations": [_conv("a"), _conv("b")]})
    got = list_conversations(tmp_path)
    assert got["total"] == len(got["conversations"]) == 2
    assert got["truncated"] is False


def test_skipped_non_dict_entries_do_not_inflate_the_total(tmp_path):
    _write_fetch(tmp_path, {"conversations": [
        _conv("a"), "not a conversation", None, 7, ["x"], _conv("b"),
    ]})
    got = list_conversations(tmp_path)
    assert got["total"] == 2, "four skipped entries used to be counted as rows"
    assert len(got["conversations"]) == 2


def test_over_the_cap_the_total_matches_the_capped_list(tmp_path):
    n = CONVERSATIONS_ROW_CAP + 5
    _write_fetch(tmp_path, {"conversations": [_conv(f"c{i:03d}") for i in range(n)]})
    got = list_conversations(tmp_path)
    assert len(got["conversations"]) == CONVERSATIONS_ROW_CAP
    assert got["total"] == CONVERSATIONS_ROW_CAP, "total used to report the raw fetch"
    assert got["truncated"] is True


def test_the_counts_are_measured_over_the_returned_rows_only(tmp_path):
    """counts must sum to total, at any fetch size.

    The maps used to accumulate inside the pre-cap loop, so a 105-row fetch
    gave by_priority summing to 105 beside 100 rows.
    """
    n = CONVERSATIONS_ROW_CAP + 5
    _write_fetch(tmp_path, {"conversations": [
        _conv(f"c{i:03d}", priority="high", direction="inbound",
              analysis={"category": "deal", "priority": "high"})
        for i in range(n)
    ]})
    got = list_conversations(tmp_path)
    assert sum(got["counts"]["by_priority"].values()) == got["total"]
    assert sum(got["counts"]["by_category"].values()) == got["total"]
    assert sum(got["counts"]["by_direction"].values()) == got["total"]
    assert got["counts"]["by_priority"] == {"high": CONVERSATIONS_ROW_CAP}


def test_the_counts_still_omit_a_row_with_no_value_for_that_facet(tmp_path):
    """A blank facet is not counted under an empty-string key.

    Moving the accumulation past the cap must not change WHICH rows count.
    """
    _write_fetch(tmp_path, {"conversations": [
        _conv("a", priority="high", direction="inbound",
              analysis={"category": "deal"}),
        _conv("b", priority="", direction="", analysis={"category": ""}),
    ]})
    counts = list_conversations(tmp_path)["counts"]
    assert counts["by_priority"] == {"high": 1}
    assert counts["by_category"] == {"deal": 1}
    assert counts["by_direction"] == {"inbound": 1}
    assert "" not in counts["by_priority"]


def test_the_cap_keeps_the_newest_rows_not_an_arbitrary_hundred(tmp_path):
    """The sort must still run BEFORE the cap.

    Restructuring the counts moved code around the `out.sort(...)` /
    `out[:CAP]` pair; if the cap ever ran first, the page would drop the most
    recent conversations, which is the only thing it exists to show.
    """
    n = CONVERSATIONS_ROW_CAP + 3
    # One distinct minute each, so "newest" is a single unambiguous row.
    convs = [_conv(f"c{i:03d}",
                   latest_datetime=f"2026-01-01T{i // 60:02d}:{i % 60:02d}:00+00:00")
             for i in range(n)]
    # Oldest first on disk; the newest must survive the cap.
    convs.sort(key=lambda c: c["latest_datetime"])
    _write_fetch(tmp_path, {"conversations": convs})
    got = list_conversations(tmp_path)
    newest = convs[-1]["id"]
    oldest = convs[0]["id"]
    ids = [c["id"] for c in got["conversations"]]
    assert ids[0] == newest
    assert oldest not in ids


def test_a_dropped_row_is_named_in_the_log(tmp_path, caplog):
    """A silent cap reads as 'this is everything'."""
    n = CONVERSATIONS_ROW_CAP + 7
    _write_fetch(tmp_path, {"conversations": [_conv(f"c{i:03d}") for i in range(n)]})
    with caplog.at_level(logging.WARNING, logger=conv_src.__name__):
        list_conversations(tmp_path)
    assert any("7" in r.getMessage() for r in caplog.records), caplog.text


def test_a_fetch_at_exactly_the_cap_is_not_reported_truncated(tmp_path):
    _write_fetch(tmp_path, {"conversations": [
        _conv(f"c{i:03d}") for i in range(CONVERSATIONS_ROW_CAP)]})
    got = list_conversations(tmp_path)
    assert got["truncated"] is False
    assert got["total"] == CONVERSATIONS_ROW_CAP


@pytest.mark.parametrize("setup", ["missing", "unparseable", "wrong-shape"])
def test_every_degraded_return_carries_the_truncated_key(tmp_path, setup):
    """A consumer reading `payload["truncated"]` must never hit a KeyError."""
    if setup == "unparseable":
        p = tmp_path / FETCH_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", encoding="utf-8")
    elif setup == "wrong-shape":
        _write_fetch(tmp_path, {"conversations": "a string"})
    got = list_conversations(tmp_path)
    assert got["truncated"] is False
    assert got["total"] == 0


def test_both_endpoints_now_mean_the_same_thing_by_total(tmp_path):
    """Cross-endpoint: /conversations and /contacts agree on the word 'total'.

    This is the finding, not a nicety - a dashboard consumes both, and one of
    them counted the source while the other counted the response.
    """
    ws = tmp_path / "workspace"
    (ws / "crm" / "contacts").mkdir(parents=True)
    for slug in ("alice", "bob"):
        (ws / "crm" / "contacts" / f"{slug}.md").write_text(
            f"---\nrelationship_type: partner\n---\n\n# {slug}\n", encoding="utf-8")
    _write_fetch(ws, {"conversations": [_conv("a"), _conv("b"), _conv("c")]})

    contacts_payload = list_contacts(ws, data_root=ws)
    conv_payload = list_conversations(ws)
    assert contacts_payload["total"] == len(contacts_payload["contacts"])
    assert conv_payload["total"] == len(conv_payload["conversations"])


# ============================================================
# Finding 3 - the uppercase extension that fell between two layers
# ============================================================

@pytest.fixture(autouse=True)
def _stub_registry(monkeypatch):
    """No executives, so only the CEO's own crm/contacts/ is scanned."""
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs", list)


def _ws_with(tmp_path: Path, *filenames: str) -> Path:
    ws = tmp_path / "workspace"
    d = ws / "crm" / "contacts"
    d.mkdir(parents=True)
    for fn in filenames:
        (d / fn).write_text("---\nrelationship_type: partner\n---\n\n# Jane\n",
                            encoding="utf-8")
    return ws


@pytest.mark.parametrize("name", ["jane.MD", "jane.Md", "jane.mD"])
def test_a_case_variant_extension_is_not_listed(tmp_path, name):
    ws = _ws_with(tmp_path, name)
    assert list_contacts(ws, data_root=ws)["contacts"] == []


@pytest.mark.parametrize("name", ["jane.MD", "jane.Md", "jane.mD"])
def test_a_case_variant_extension_is_named_in_the_log(tmp_path, caplog, name):
    """It used to disappear with no log line at all: the glob never saw it."""
    ws = _ws_with(tmp_path, name)
    with caplog.at_level(logging.WARNING, logger=contacts_src.__name__):
        list_contacts(ws, data_root=ws)
    assert any(name in r.getMessage() for r in caplog.records), caplog.text


def test_the_row_the_drill_down_could_never_open_is_no_longer_listed(tmp_path):
    """The whole point: listing and drill-down now agree.

    On Windows this file WAS listed (pathlib globs case-insensitively there)
    and then `read_one_contact` looked for `jane.md`, which does not exist.
    """
    ws = _ws_with(tmp_path, "jane.MD")
    assert list_contacts(ws, data_root=ws)["contacts"] == []
    got = read_one_contact(ws, "ceo", "jane", data_root=ws)
    assert got["ok"] is False


def test_a_lowercase_contact_beside_a_case_variant_still_lists(tmp_path):
    ws = _ws_with(tmp_path, "jane.MD", "bob.md")
    slugs = [c["slug"] for c in list_contacts(ws, data_root=ws)["contacts"]]
    assert slugs == ["bob"]


def test_readme_and_underscore_files_are_not_reported_as_renamable(tmp_path, caplog):
    """The warning names a file the operator should rename, and nothing else.

    A README.MD is excluded on its own merits; telling the operator to rename
    it would be advice that changes nothing.
    """
    ws = _ws_with(tmp_path, "README.MD", "_scratch.MD")
    with caplog.at_level(logging.WARNING, logger=contacts_src.__name__):
        assert list_contacts(ws, data_root=ws)["contacts"] == []
    assert caplog.records == [], caplog.text


@pytest.mark.parametrize("name,expected", [
    ("jane.md", True),
    ("jane-smith.md", True),
    ("Jane.MD", False),      # the acceptance neither neighbouring layer honours
    ("jane.MD", False),
    ("readme.md", False),
    ("README.md", False),    # the readme test still folds case
    ("_scratch.md", False),
    ("_Scratch.md", False),  # the underscore test still folds case
    ("notes.txt", False),
])
def test_the_extension_test_is_case_exact_and_the_rest_is_not(name, expected):
    assert _is_contact_file(Path(name)) is expected


def test_a_lowercase_md_directory_scan_is_unchanged(tmp_path):
    """The wider glob must not start admitting anything new."""
    ws = _ws_with(tmp_path, "alice.md", "bob.md", "readme.md", "_draft.md")
    (ws / "crm" / "contacts" / "notes.txt").write_text("x", encoding="utf-8")
    slugs = sorted(c["slug"] for c in list_contacts(ws, data_root=ws)["contacts"])
    assert slugs == ["alice", "bob"]
