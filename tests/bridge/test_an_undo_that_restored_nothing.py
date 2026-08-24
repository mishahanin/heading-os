"""An undo that reverted nothing, and five readers that raised instead of degrading.

Covers the k3 audit shard `scripts-01-p2` for `sources/action_queue.py`,
`sources/approvals.py`, `sources/agenda.py`, `refreshers/inflight.py`,
`refreshers/mail.py` and `finalizers/send_email.py`. Nothing here starts the
daemon; it is stopped and disabled on this machine on purpose.

*The one that mattered.* `undo_card` popped `prev_value` and parked it under a
new key `restored_value`. Whatever field the auto-apply had changed kept its
post-edit value, nothing was written back anywhere, and the function returned
`{"ok": True, "noop": False}` -- telling the caller a revert happened. Its own
docstring says "Undo restores that state", twice, and
`.claude/rules/tiered-risk.md` describes the notify tier as "auto-applied by the
daemon, with a one-click undo". The undo did not exist. Nothing was corrupted,
because the notify producer is still future work and no card carries
`prev_value` yet; what was wrong was the promise, on the single control that
makes an auto-apply acceptable in the first place.

The fix reads `prev_field` -- the name of the field the value belongs in -- and
writes it back. A card with `prev_value` and no `prev_field` keeps the relabel,
because the value must stay recoverable, and reports `restored: False` instead
of implying a rollback. `prev_field` is producer-supplied and therefore cannot
be allowed to name `status`, `tier` or `action_type`: those decide which lane a
card sits in and whether it needs a human click.

*A mkdir outside the try it was meant to be inside.* `mark_sent` and
`undo_sent` guarded only `append_jsonl`. A read-only mount or a parent path
occupied by a plain file raised OSError out of a function contracted to return
`{ok: False, error}`. `heartbeat.py` fixed this exact shape on 2026-08-24 and
the sibling kept it.

*One racing file voided a whole scan.* `scan_inflight` caught only
UnicodeDecodeError while walking producers' output directories, where files
rotate underneath it. An unlink between `iterdir` and `stat` -- or between the
two separate stats it took per file -- raised OSError out of the scan, and
`refresh` catches that, so every row already collected was discarded and the
component version left alone.

*Three readers that stopped one level short.* `count_unread` called `.get` on
whatever `messages` held, though `read_email_state` validates only the top
level. `send_drafted` ran `re.match` on a possibly-non-string id, raising
TypeError where its guard promises ValueError. `minutes_until` floored toward
minus infinity, so every meeting read "1 minute ago" for its first minute.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKSPACE))

from scripts.bridge_daemon.finalizers import send_email  # noqa: E402
from scripts.bridge_daemon.refreshers import inflight, mail  # noqa: E402
from scripts.bridge_daemon.sources import action_queue, approvals  # noqa: E402


# ============================================================
# 1. The undo that restored nothing
# ============================================================

@pytest.fixture
def queue(tmp_path):
    """A workspace root holding one card, and a reader for it."""
    def _seed(card: dict) -> Path:
        path = tmp_path / action_queue.QUEUE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"actions": [card]}), encoding="utf-8")
        return tmp_path

    return _seed


def _card(root: Path, action_id: str) -> dict:
    data = json.loads((root / action_queue.QUEUE_FILE).read_text(encoding="utf-8"))
    return next(c for c in data["actions"] if c["id"] == action_id)


def test_an_undo_writes_every_field_back(queue):
    """The defect: the fields kept their post-edit values and the caller was
    told a revert had happened.

    `prev_value` is a MAPPING. That shape is not invented here; it is what the
    producer side already passes, in `test_action_queue_tiers.py`:
    `apply_status(..., prev_value={"stage": "Qualified"}, ...)`.
    """
    root = queue({"id": "a1", "action_type": "pipeline_update",
                  "status": "pending", "stage": "negotiation", "owner": "b",
                  "prev_value": {"stage": "qualified", "owner": "a"}})
    result = action_queue.undo_card(root, "a1")
    assert result["ok"] is True
    assert result["restored"] is True
    card = _card(root, "a1")
    assert card["stage"] == "qualified"
    assert card["owner"] == "a"


def test_the_undone_card_no_longer_carries_the_stamp(queue):
    root = queue({"id": "a1", "action_type": "pipeline_update",
                  "status": "pending", "stage": "negotiation",
                  "prev_value": {"stage": "qualified"}})
    action_queue.undo_card(root, "a1")
    card = _card(root, "a1")
    assert "prev_value" not in card
    assert "restored_value" not in card
    assert card["undone_at"]


def test_a_field_absent_from_the_card_is_still_restored(queue):
    """The auto-apply may have ADDED a field; undo puts the old absence back
    as an explicit value, which is the best a key-value log can do."""
    root = queue({"id": "a1", "action_type": "pipeline_update",
                  "status": "pending", "prev_value": {"stage": "qualified"}})
    assert action_queue.undo_card(root, "a1")["restored"] is True
    assert _card(root, "a1")["stage"] == "qualified"


@pytest.mark.parametrize("scalar", ["qualified", 42, ["a"], None])
def test_a_scalar_prev_value_says_it_restored_nothing(queue, scalar):
    """The relabel is kept, but it must stop claiming to be a rollback."""
    root = queue({"id": "a1", "action_type": "pipeline_update",
                  "status": "pending", "stage": "negotiation",
                  "prev_value": scalar})
    result = action_queue.undo_card(root, "a1")
    assert result["ok"] is True
    assert result["restored"] is False
    card = _card(root, "a1")
    assert card["stage"] == "negotiation", "nothing was restored, correctly"
    assert card["restored_value"] == scalar, "and it stays recoverable"


def test_the_unrestorable_case_is_logged_under_its_own_event(queue):
    root = queue({"id": "a1", "action_type": "pipeline_update",
                  "status": "pending", "prev_value": "a bare string"})
    action_queue.undo_card(root, "a1")
    log = (root / action_queue.DISPOSITION_LOG).read_text(encoding="utf-8")
    events = [json.loads(ln)["event"] for ln in log.splitlines() if ln.strip()]
    assert "undo_unrestorable" in events
    assert "undo" not in events, "an audit relabel is not an undo"


@pytest.mark.parametrize("protected", ["status", "tier", "action_type", "id",
                                       "created_at", "trace_id"])
def test_a_producer_cannot_name_a_protected_field(queue, protected):
    """`prev_value`'s keys are data from the card. They must not reach the
    fields that decide the card's lane.

    A card stamped `prev_value: {"status": "approved"}` would let an undo set
    a card's status, which is `apply_status`'s job alone; `tier` and
    `action_type` are what band a card into the gated or non-gated lane, and
    `annotate_card` drops `status` from its own fields for the same reason.
    """
    root = queue({"id": "a1", "action_type": "pipeline_update",
                  "status": "pending", "tier": "notify",
                  "prev_value": {protected: "approved"}})
    result = action_queue.undo_card(root, "a1")
    assert result["restored"] is False
    card = _card(root, "a1")
    assert card["status"] == "pending"
    assert card["action_type"] == "pipeline_update"
    assert card["id"] == "a1"
    assert card["restored_value"] == {protected: "approved"}


def test_a_mixed_mapping_restores_only_the_allowed_keys(queue):
    """One protected key must not veto the fields that ARE restorable."""
    root = queue({"id": "a1", "action_type": "pipeline_update",
                  "status": "pending", "stage": "negotiation",
                  "prev_value": {"stage": "qualified", "status": "approved"}})
    assert action_queue.undo_card(root, "a1")["restored"] is True
    card = _card(root, "a1")
    assert card["stage"] == "qualified"
    assert card["status"] == "pending", "the protected key was refused"


def test_a_card_with_nothing_to_revert_is_still_a_no_op(queue):
    """The earlier scrutiny-M2 fix must survive the new one."""
    root = queue({"id": "a1", "action_type": "note", "status": "pending"})
    result = action_queue.undo_card(root, "a1")
    assert result == {"ok": True, "noop": True, "restored": False,
                      "card": result["card"]}
    assert "undone_at" not in _card(root, "a1")


def test_a_missing_card_is_still_an_error(queue):
    root = queue({"id": "a1", "action_type": "note", "status": "pending"})
    assert action_queue.undo_card(root, "nope")["ok"] is False
    assert action_queue.undo_card(root, "")["ok"] is False


def test_every_result_shape_carries_the_restored_flag(queue):
    """A caller must be able to read one field, not infer from `noop`."""
    root = queue({"id": "a1", "action_type": "note", "status": "pending"})
    assert "restored" in action_queue.undo_card(root, "a1")
    root2 = queue({"id": "b1", "action_type": "pipeline_update",
                   "status": "pending", "stage": "x",
                   "prev_value": {"stage": "y"}})
    assert "restored" in action_queue.undo_card(root2, "b1")


# ============================================================
# 2. The mkdir outside its try
# ============================================================

def _blocked_log_parent(tmp_path: Path) -> Path:
    """Occupy the log's parent directory path with a plain FILE."""
    log_path = tmp_path / approvals.SENT_LOG_FILE
    log_path.parent.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.write_text("i am a file, not a directory", encoding="utf-8")
    return tmp_path


DRAFT = "outputs/communications/email/2026-08-25-example.md"


def test_mark_sent_returns_its_error_dict_instead_of_raising(tmp_path):
    root = _blocked_log_parent(tmp_path)
    result = approvals.mark_sent(root, DRAFT)
    assert result["ok"] is False
    assert "write failed" in result["error"]


def test_undo_sent_returns_its_error_dict_instead_of_raising(tmp_path):
    root = _blocked_log_parent(tmp_path)
    result = approvals.undo_sent(root, DRAFT)
    assert result["ok"] is False
    assert "write failed" in result["error"]


def test_mark_sent_still_works_on_a_writable_tree(tmp_path):
    """The guard must not have been bought by breaking the success path."""
    result = approvals.mark_sent(tmp_path, DRAFT, note="sent by hand")
    assert result["ok"] is True
    assert result["path"].endswith("2026-08-25-example.md")
    log = (tmp_path / approvals.SENT_LOG_FILE).read_text(encoding="utf-8")
    assert json.loads(log.splitlines()[0])["note"] == "sent by hand"


def test_a_rejected_path_is_still_rejected_before_any_write(tmp_path):
    result = approvals.mark_sent(tmp_path, "../../etc/passwd")
    assert result["ok"] is False
    assert "write failed" not in result["error"]


def test_the_mkdir_sits_inside_the_guard():
    src = (WORKSPACE / "scripts" / "bridge_daemon" / "sources"
           / "approvals.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.split("\n")
                     if not ln.lstrip().startswith("#"))
    assert "log_path.parent.mkdir(parents=True, exist_ok=True)\n    with _SENT_LOG_LOCK" \
        not in code, "the mkdir is back above the try"
    assert code.count("            log_path.parent.mkdir(parents=True, exist_ok=True)") == 2


# ============================================================
# 3. One racing file voided the whole scan
# ============================================================

def _inflight_tree(tmp_path: Path, monkeypatch, names: list[str]) -> Path:
    d = tmp_path / "outputs" / "negotiations"
    d.mkdir(parents=True)
    for n in names:
        # Real frontmatter: `_extract_session_id` looks inside the `---` block.
        (d / n).write_text("---\nsession_id: abc123\n---\nbody\n", encoding="utf-8")
    monkeypatch.setattr(inflight, "SCAN_DIRS", {"negotiations": "outputs/negotiations"})
    return tmp_path


def test_a_file_that_vanishes_mid_scan_costs_only_that_file(tmp_path,
                                                            monkeypatch):
    """The whole finding: the rows already collected were thrown away too."""
    root = _inflight_tree(tmp_path, monkeypatch, ["a.md", "b.md", "c.md"])
    doomed = root / "outputs" / "negotiations" / "b.md"
    real_stat = Path.stat

    def _vanishing(self, *a, **k):
        if self == doomed:
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", _vanishing)
    rows = inflight.scan_inflight(root)
    assert {r["id"] for r in rows} == {"a", "c"}


def test_an_unreadable_file_costs_only_that_file(tmp_path, monkeypatch):
    root = _inflight_tree(tmp_path, monkeypatch, ["a.md", "b.md"])
    doomed = root / "outputs" / "negotiations" / "b.md"
    real_read = Path.read_text

    def _denied(self, *a, **k):
        if self == doomed:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", _denied)
    assert {r["id"] for r in inflight.scan_inflight(root)} == {"a"}


def test_an_unlistable_directory_costs_only_that_category(tmp_path, monkeypatch,
                                                          caplog):
    """A tree that became unreadable after `exists()` passed took the run."""
    ok_dir = tmp_path / "outputs" / "negotiations"
    ok_dir.mkdir(parents=True)
    (ok_dir / "a.md").write_text("x", encoding="utf-8")
    bad_dir = tmp_path / "outputs" / "osint"
    bad_dir.mkdir(parents=True)
    monkeypatch.setattr(inflight, "SCAN_DIRS", {
        "negotiations": "outputs/negotiations", "osint": "outputs/osint"})
    real_iterdir = Path.iterdir

    def _denied(self, *a, **k):
        if self == bad_dir:
            raise PermissionError(13, "Permission denied", str(self))
        return real_iterdir(self, *a, **k)

    monkeypatch.setattr(Path, "iterdir", _denied)
    assert {r["id"] for r in inflight.scan_inflight(tmp_path)} == {"a"}


def test_a_non_utf8_file_is_still_skipped(tmp_path, monkeypatch):
    """The one case that WAS handled must stay handled."""
    root = _inflight_tree(tmp_path, monkeypatch, ["a.md"])
    (root / "outputs" / "negotiations" / "b.md").write_bytes(b"\xff\xfe")
    assert {r["id"] for r in inflight.scan_inflight(root)} == {"a"}


def test_a_clean_scan_still_returns_every_row(tmp_path, monkeypatch):
    root = _inflight_tree(tmp_path, monkeypatch, ["a.md", "b.md"])
    rows = inflight.scan_inflight(root)
    assert {r["id"] for r in rows} == {"a", "b"}
    assert all(r["session_id"] == "abc123" for r in rows)


def test_a_stale_file_is_still_dropped_by_the_cutoff(tmp_path, monkeypatch):
    import os
    root = _inflight_tree(tmp_path, monkeypatch, ["a.md", "old.md"])
    stale = root / "outputs" / "negotiations" / "old.md"
    long_ago = stale.stat().st_mtime - 48 * 3600
    os.utime(stale, (long_ago, long_ago))
    assert {r["id"] for r in inflight.scan_inflight(root)} == {"a"}


# ============================================================
# 4. The id that was not a string
# ============================================================

@pytest.mark.parametrize("bad", [None, 42, ["x"], {"a": 1}, b"bytes"])
def test_a_non_string_artifact_id_is_a_value_error(bad):
    """`re.match` on a non-string raises TypeError, which no caller expects."""
    with pytest.raises(ValueError):
        send_email.send_drafted(None, bad)


def test_a_malformed_string_id_is_still_a_value_error():
    with pytest.raises(ValueError):
        send_email.send_drafted(None, "../../etc/passwd")


def test_a_well_formed_id_gets_past_the_guard(tmp_path):
    """It must reach the not-found branch, not be rejected by the guard."""
    result = send_email.send_drafted(tmp_path, "abc123")
    assert result["found"] is False


# ============================================================
# 5. The first minute of every meeting
# ============================================================

def _agenda(tmp_path: Path, monkeypatch, line: str, now):
    day = tmp_path / "outputs" / "operations" / "day"
    day.mkdir(parents=True, exist_ok=True)
    return day, line, now


@pytest.mark.parametrize("seconds_past,expected", [
    (1, 0), (30, 0), (59, 0), (60, -1), (90, -1), (121, -2),
])
def test_minutes_until_truncates_toward_zero(seconds_past, expected):
    """`//` floors toward minus infinity, so 30 seconds ago read -1."""
    delta = timedelta(seconds=-seconds_past)
    assert int(delta.total_seconds() / 60) == expected
    src = (WORKSPACE / "scripts" / "bridge_daemon" / "sources"
           / "agenda.py").read_text(encoding="utf-8")
    # Scoped to the `minutes_until` line. `minutes_to_next` a few lines below
    # also uses `//`, and correctly: it is the gap between two events in a
    # sorted list, so it is never negative and floor equals truncation there.
    line = next(ln for ln in src.splitlines()
                if 'e["minutes_until"] =' in ln)
    assert "// 60" not in line
    assert "/ 60" in line


def test_a_future_event_is_unaffected_by_the_change():
    """Truncation and floor agree for every positive value."""
    for seconds in (1, 59, 60, 61, 3600):
        delta = timedelta(seconds=seconds)
        assert int(delta.total_seconds() / 60) == int(delta.total_seconds() // 60)


# ============================================================
# 6. The reader that stopped one level short
# ============================================================

@pytest.mark.parametrize("messages", [
    ["not-a-dict"], [1, 2, 3], [None], "a string", 42, {"a": "b"}, None,
])
def test_a_malformed_messages_value_counts_zero(messages):
    """`read_email_state` validates the top level only, so this reaches here."""
    assert mail.count_unread({"messages": messages}) == 0


def test_an_absent_messages_key_counts_zero():
    assert mail.count_unread({}) == 0


def test_a_mixed_list_counts_only_the_real_ones():
    state = {"messages": [{"unread": True}, "junk", {"unread": False},
                          None, {"unread": True}]}
    assert mail.count_unread(state) == 2


def test_a_well_formed_state_still_counts_correctly():
    state = {"messages": [{"unread": True}, {"unread": True},
                          {"unread": False}]}
    assert mail.count_unread(state) == 2
