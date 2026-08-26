"""Shard 05-p2: a truncation flag counted after the drops, stats that re-read
the payload past their own validation, and four contracts that misdescribed
themselves.

* ``email-intelligence.fetch_emails`` over-fetches ``limit + 1`` ROWS to learn
  whether more mail exists, then drops rows with no usable id, then computed
  ``truncated = len(results) > limit``. One dropped row inside the probe window
  made the count exactly ``limit``, the flag False, and the run stamped
  "complete" over messages it never fetched - the silent loss the flag exists
  to end.

* ``commit_state`` validates ``conversations`` is a list and then read the raw
  payload again for the stats line. ``.get(key, [])`` substitutes only when the
  KEY IS ABSENT, so ``"conversations": null`` came back as None and
  ``len(None)`` raised TypeError - not in ``main``'s handler tuple, so the
  deferred-commit path this function is hardened for died on a traceback.

* ``run_unread_mode``'s comment called the StateManager load read-only and said
  it fed the learned-ignore list. Construction can quarantine a corrupt state
  file with ``os.replace``, and this call passes ``mirror=True``, which skips
  the layer that list belongs to.

* ``email-sweep``'s exit-code line named one of three exit-2 conditions and
  assigned the date check to exit 1; ``_save``'s comment pointed at a note in
  ``cmd_propose`` that did not exist; ``cmd_propose`` caught only
  JSONDecodeError on a read that can also raise OSError; and ``approve 1 1``
  reported "2 action(s)" for one.

Run: python3 -m pytest tests/test_a_probe_that_counted_survivors.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
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


sweep = _load("email_sweep_under_test", "scripts/email-sweep.py")
intel = _load("email_intel_under_test", "scripts/email-intelligence.py")


# ============================================================
# The probe that counted survivors
# ============================================================

class _Item:
    """Every attribute the normalisation loop reads, and no more.

    `message_id` is the one that varies: a row with neither it nor `id` is the
    row the loop drops, which is the whole subject of this section.
    """

    _FIELDS = {
        "id": None, "text_body": "body", "body": "body", "subject": "s",
        "conversation_id": None, "conversation_topic": "t", "sender": None,
        "to_recipients": None, "cc_recipients": None,
        "datetime_received": None, "datetime_sent": None,
        "item_class": "IPM.Note", "importance": "Normal",
        "has_attachments": False, "in_reply_to": None,
    }

    def __init__(self, ident: str | None):
        self.message_id = ident
        for name, value in self._FIELDS.items():
            setattr(self, name, value)


class _Folder:
    def __init__(self, items):
        self._items = items

    def filter(self, **_kw):
        return self

    def only(self, *_a):
        return self

    def order_by(self, *_a):
        return self

    def __getitem__(self, sl):
        return self._items[sl]


class _Account:
    """`fetch_emails` takes an ACCOUNT and picks the folder off it."""

    def __init__(self, items):
        self.inbox = _Folder(items)
        self.sent = _Folder(items)


def _fetch(monkeypatch, items, limit):
    # `unread_only=True` so the cutoff branch (which needs real EWS types) is
    # not the thing under test here; the probe arithmetic is identical on both.
    return intel.fetch_emails(_Account(items), "inbox", None,
                              limit=limit, unread_only=True)


def test_a_dropped_row_inside_the_probe_still_reports_truncated(monkeypatch):
    """The reported reproduction: one id-less row hid a real overflow."""
    limit = 3
    items = [_Item("a"), _Item(None), _Item("c"), _Item("d"), _Item("e")]
    rows, truncated = _fetch(monkeypatch, items, limit)
    assert len(rows) == 3, "the id-less row is still dropped"
    assert truncated is True, "the probe fetched more than `limit` rows"


def test_a_full_page_with_no_overflow_is_not_truncated(monkeypatch):
    limit = 3
    rows, truncated = _fetch(monkeypatch, [_Item("a"), _Item("b"), _Item("c")],
                             limit)
    assert len(rows) == 3
    assert truncated is False


def test_a_short_page_is_not_truncated(monkeypatch):
    rows, truncated = _fetch(monkeypatch, [_Item("a")], 3)
    assert len(rows) == 1 and truncated is False


def test_every_row_id_less_still_reports_the_overflow(monkeypatch):
    """The extreme of the same defect: zero survivors, four rows fetched."""
    rows, truncated = _fetch(monkeypatch, [_Item(None)] * 4, 3)
    assert rows == []
    assert truncated is True


# ============================================================
# The stats line that read the payload again
# ============================================================

class _State:
    def __init__(self):
        self.data = {"stats": {}}
        self.marked = []

    def mark_processed(self, mid):
        self.marked.append(mid)

    def mark_conversation(self, cid, topic):
        pass


def _commit(payload):
    state = _State()
    intel.commit_state(state, payload)
    return state


def test_a_null_conversations_list_is_a_clean_refusal_not_a_typeerror():
    """`len(None)` is a TypeError, and main's handler tuple does not list it."""
    state = _State()
    payload = {"message_ids": ["m1"], "conversations": None,
               "noise_filtered": 0, "status": "complete"}
    # `or []` normalises it, so the run commits and the stats stay honest.
    intel.commit_state(state, payload)
    assert state.data["stats"]["total_conversations"] == 0


@pytest.mark.parametrize("bad", ["12", None, [], {}, 1.5, True])
def test_a_non_int_noise_filtered_is_refused_as_a_value_error(bad):
    """`0 + "12"` is the same TypeError one field over."""
    state = _State()
    payload = {"message_ids": [], "conversations": [], "noise_filtered": bad}
    with pytest.raises(ValueError, match="noise_filtered"):
        intel.commit_state(state, payload)


def test_the_stats_count_the_validated_list():
    state = _commit({"message_ids": [], "noise_filtered": 2,
                     "conversations": [{"id": "c1"}, {"id": "c2"}]})
    assert state.data["stats"]["total_conversations"] == 2
    assert state.data["stats"]["total_filtered"] == 2
    assert state.data["stats"]["total_runs"] == 1


def test_a_wrong_typed_conversations_is_still_refused():
    """The older guard must survive the stats line being changed beside it."""
    with pytest.raises(ValueError, match="conversations"):
        intel.commit_state(_State(), {"conversations": "not a list"})


def test_the_unread_mode_comment_no_longer_claims_read_only():
    src = (ROOT / "scripts" / "email-intelligence.py").read_text(encoding="utf-8")
    assert "# read-only here - used only for learned-ignore senders" not in src
    assert "quarantines a corrupt state file" in src


# ============================================================
# The four contracts that misdescribed themselves
# ============================================================

def test_the_exit_code_line_names_every_exit_two_condition():
    doc = sweep.__doc__
    assert "missing or unreadable" in doc
    assert "--date is not an" in doc and "exact YYYY-MM-DD" in doc
    # The correction quotes the sentence it replaced, so pin the order.
    assert doc.index("exact YYYY-MM-DD") < doc.index(
        'used to read "2 state file missing for a mutate"')


def test_the_save_comment_points_at_a_note_that_exists():
    src = (ROOT / "scripts" / "email-sweep.py").read_text(encoding="utf-8")
    assert "see the note in cmd_propose" in src
    # And the note itself, in `cmd_propose`, saying what it promises to say.
    assert "does not cover the read-modify-write as a whole" in src


def test_an_unreadable_payload_is_a_clean_refusal(tmp_path, monkeypatch, capsys):
    payload = tmp_path / "proposed.json"
    payload.write_text("{}", encoding="utf-8")

    real_read = Path.read_text

    def _boom(self, *a, **kw):
        if self == payload:
            raise PermissionError("permission denied")
        return real_read(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", _boom)

    class _Args:
        file = str(payload)
        date = None

    code = sweep.cmd_propose(tmp_path, _Args())
    assert code == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err


def test_a_malformed_payload_is_still_a_clean_refusal(tmp_path, capsys):
    payload = tmp_path / "proposed.json"
    payload.write_text("{not json", encoding="utf-8")

    class _Args:
        file = str(payload)
        date = None

    assert sweep.cmd_propose(tmp_path, _Args()) == 1


def test_a_repeated_id_is_reported_once(tmp_path, monkeypatch, capsys):
    """`approve 1 1` moved one action and said it moved two."""
    saved = {}
    monkeypatch.setattr(sweep, "_load", lambda _r, _d: {
        "actions": [{"id": 1, "status": "proposed"}]})
    monkeypatch.setattr(sweep, "_save",
                        lambda _r, _d, data: saved.update(data))

    code = sweep._mutate_ids(tmp_path, "2026-08-25", [1, 1], "approved")
    assert code == 0
    out = capsys.readouterr().out
    assert "1 action(s)" in out
    assert "#1, #1" not in out


def test_two_distinct_ids_are_both_reported(tmp_path, monkeypatch, capsys):
    """Dedupe must not swallow a second, genuinely different action."""
    monkeypatch.setattr(sweep, "_load", lambda _r, _d: {
        "actions": [{"id": 1, "status": "proposed"},
                    {"id": 2, "status": "proposed"}]})
    monkeypatch.setattr(sweep, "_save", lambda *_a: None)

    assert sweep._mutate_ids(tmp_path, "2026-08-25", [2, 1], "approved") == 0
    out = capsys.readouterr().out
    assert "2 action(s)" in out
    assert "#2, #1" in out, "the operator's own order is preserved"
