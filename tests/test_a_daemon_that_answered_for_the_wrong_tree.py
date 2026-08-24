"""The bridge daemon, reading and writing the tree it was not handed.

Covers the k3 audit shards `scripts-01-p2`, `scripts-01-p3`, `scripts-01-p4`,
`scripts-02-p1`, `scripts-02-p2` and `scripts-02-p3`. The shard name is written
here on purpose: it is what lets a later pass tell a shard that was worked from
one that was never opened.

The shard behind this file (k3, 2026-08-23) found one shape repeated across
nine modules: a function is given a root, ignores it, and reaches for a
different one. `mark_conversation_read` was handed the DATA root and went
looking for an ENGINE script. `refreshers/mail.py` accepted a `workspace_root`
and resolved its producer from the module's own file location instead.
`crm_log.log_to_crm` declared a `workspace_root` it never read while the caller
passed the data root into it. All three worked only because the two roots still
point at the same directory on this machine.

The rest is the same family of quiet wrongness: a corrupt `queue.json` silently
overwritten with an empty one, a config typo refusing to let the daemon boot,
an empty token file bricking every endpoint with no log line, a `/health`
comment claiming a threat model the server next to it does not implement.
"""
from __future__ import annotations

import json
import logging
import socket
from pathlib import Path

import pytest

from scripts.bridge_daemon import auth, config as config_mod, error_tracker, sessions
from scripts.bridge_daemon import scheduler as scheduler_mod
from scripts.bridge_daemon._jsonl import read_jsonl_capped
from scripts.bridge_daemon.finalizers import crm_log
from scripts.bridge_daemon.refreshers import mail, pulse as pulse_ref
from scripts.bridge_daemon.sources import action_queue as aq
from scripts.bridge_daemon.sources import approvals

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "scripts" / "bridge_daemon"


def _code_only(path: Path) -> str:
    """The file minus whole-line comments, so a fix's own tombstone
    cannot satisfy a grep that is looking for the removed code."""
    return "\n".join(
        ln for ln in path.read_text(encoding="utf-8").split("\n")
        if not ln.lstrip().startswith("#")
    )


# ============================================================
# One root per function
# ============================================================

def test_the_mark_read_finalizer_is_given_the_engine_root():
    """It locates `scripts/email-intelligence.py` and cwd's there."""
    code = _code_only(PKG / "app.py")
    assert "_inbox_mark_conv_read(workspace_root," in code
    assert "_inbox_mark_conv_read(data_root," not in code, (
        "the data overlay has no scripts/ tree; every dismiss would 502"
    )


def test_the_mail_refresher_resolves_its_producer_under_the_given_root(tmp_path):
    assert mail.producer_script(tmp_path) == tmp_path / "scripts" / "email-intelligence.py"


def test_the_mail_refresher_default_root_is_this_engine_clone():
    assert mail.producer_script() == mail.PRODUCER_SCRIPT
    assert mail.producer_script().is_relative_to(ROOT)


def test_log_to_crm_declares_no_root_it_does_not_read():
    import inspect

    params = list(inspect.signature(crm_log.log_to_crm).parameters)
    assert params[0] == "conv_id"
    assert "workspace_root" not in params


# ============================================================
# A corrupt queue is preserved, not overwritten
# ============================================================

def _queue_path(root: Path) -> Path:
    return root / aq.QUEUE_FILE


def test_a_corrupt_queue_is_moved_aside_before_anything_writes(tmp_path, caplog):
    q = _queue_path(tmp_path)
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text('{"actions": [{"id": "card-1"', encoding="utf-8")  # torn write

    with caplog.at_level(logging.ERROR):
        loaded = aq._load_queue(tmp_path)

    assert loaded["actions"] == []
    assert not q.exists(), "the corrupt file must not still be in the live path"
    wrecks = list(q.parent.glob("queue.json.corrupt-*"))
    assert len(wrecks) == 1
    assert "card-1" in wrecks[0].read_text(encoding="utf-8"), (
        "the whole point is that the bytes survive"
    )
    assert "unreadable" in caplog.text


def test_a_queue_of_the_wrong_shape_is_also_quarantined(tmp_path):
    q = _queue_path(tmp_path)
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text('["not", "a", "queue"]', encoding="utf-8")
    aq._load_queue(tmp_path)
    assert list(q.parent.glob("queue.json.corrupt-*"))


def test_an_absent_queue_is_not_quarantined(tmp_path, caplog):
    """Absent and corrupt are different facts and must stay different.

    Also: cold start must be SILENT. Routing the absent case through the
    quarantine path leaves nothing on disk (there is no file to rename) but
    logs an ERROR on every first boot, which is how operators learn to skim
    past errors.
    """
    q = _queue_path(tmp_path)
    q.parent.mkdir(parents=True, exist_ok=True)
    with caplog.at_level(logging.ERROR):
        assert aq._load_queue(tmp_path)["actions"] == []
    assert not list(q.parent.glob("queue.json.corrupt-*"))
    assert caplog.text == "", f"cold start logged an error: {caplog.text}"


def test_a_healthy_queue_is_returned_untouched(tmp_path):
    q = _queue_path(tmp_path)
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text(json.dumps({"version": 1, "actions": [{"id": "a"}]}), encoding="utf-8")
    assert aq._load_queue(tmp_path)["actions"] == [{"id": "a"}]
    assert q.exists()


def test_two_corruptions_do_not_clobber_each_others_wreckage(tmp_path):
    q = _queue_path(tmp_path)
    q.parent.mkdir(parents=True, exist_ok=True)
    for body in ("{bad one", "{bad two"):
        q.write_text(body, encoding="utf-8")
        aq._load_queue(tmp_path)
    assert len(list(q.parent.glob("queue.json.corrupt-*"))) == 2


def test_the_disposition_log_is_appended_not_rewritten():
    code = _code_only(PKG / "sources" / "action_queue.py")
    assert "append_jsonl(workspace_root / DISPOSITION_LOG" in code
    assert "existing + json.dumps(event" not in code, (
        "the read-whole-file rewrite was O(size) per event, under the global lock"
    )


# ============================================================
# Truncation is never silent
# ============================================================

def test_an_oversized_jsonl_log_says_so(tmp_path, caplog):
    log = tmp_path / "big.jsonl"
    log.write_text("".join(json.dumps({"n": i}) + "\n" for i in range(400)),
                   encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        entries, truncated = read_jsonl_capped(log, max_bytes=200)
    assert truncated
    assert entries, "the newest entries are still returned"
    assert "over the" in caplog.text
    assert "never-actioned" in caplog.text


def test_a_small_jsonl_log_warns_about_nothing(tmp_path, caplog):
    log = tmp_path / "small.jsonl"
    log.write_text(json.dumps({"n": 1}) + "\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        entries, truncated = read_jsonl_capped(log, max_bytes=1_000_000)
    assert entries == [{"n": 1}]
    assert not truncated
    assert caplog.text == ""


# ============================================================
# Config and token: degrade, do not brick
# ============================================================

def test_a_malformed_user_config_layer_is_skipped_not_fatal(tmp_path, caplog):
    user = tmp_path / ".daemon-state" / "config.yaml"
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text("refresh: [unclosed\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        cfg = config_mod.load_config(tmp_path)
    assert cfg["port_range_start"] == config_mod.DEFAULTS["port_range_start"]
    assert "unreadable" in caplog.text


def test_a_config_layer_that_is_a_list_is_skipped(tmp_path, caplog):
    user = tmp_path / ".daemon-state" / "config.yaml"
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text("- a\n- b\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        cfg = config_mod.load_config(tmp_path)
    assert cfg["port_range_start"] == config_mod.DEFAULTS["port_range_start"]
    assert "not a mapping" in caplog.text


def test_a_good_user_config_layer_still_wins(tmp_path):
    user = tmp_path / ".daemon-state" / "config.yaml"
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text("port_range_start: 40404\n", encoding="utf-8")
    assert config_mod.load_config(tmp_path)["port_range_start"] == 40404


def test_an_empty_token_file_is_regenerated(tmp_path, caplog):
    token_file = tmp_path / ".daemon-state" / "token"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text("   \n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        token = auth.get_or_create_token(tmp_path)
    assert token, "an empty token means 401 on every request, forever"
    assert auth.validate(token, token)
    assert "empty" in caplog.text


def test_an_existing_token_is_left_alone(tmp_path):
    token_file = tmp_path / ".daemon-state" / "token"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text("a-real-token", encoding="utf-8")
    assert auth.get_or_create_token(tmp_path) == "a-real-token"


def test_the_auth_header_no_longer_claims_a_binding_it_does_not_enforce():
    head = (PKG / "auth.py").read_text(encoding="utf-8").split('"""')[1]
    assert "NOT binding" in head
    assert not head.startswith("Workspace-fingerprinted"), (
        "validate() is a plain compare_digest; a copied token works anywhere"
    )


def test_a_revert_names_the_corporate_keys_it_is_about_to_freeze(tmp_path, caplog):
    corp = tmp_path / "corporate" / "daemon" / "config.yaml"
    corp.parent.mkdir(parents=True, exist_ok=True)
    corp.write_text("port_range_start: 32000\nrefresh:\n  pulse: 30\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        shadowed = config_mod._warn_about_shadowed_corporate_keys(
            tmp_path, "port_range_start: 31415\nrefresh:\n  pulse: 60\n", "snap.yaml")
    assert shadowed == ["port_range_start", "refresh"]
    assert "frozen user overrides" in caplog.text


def test_no_corporate_layer_means_nothing_to_shadow(tmp_path):
    assert config_mod._warn_about_shadowed_corporate_keys(
        tmp_path, "port_range_start: 31415\n", "snap.yaml") == []


# ============================================================
# Nonces, handlers, intervals
# ============================================================

def test_expired_nonces_are_pruned_on_the_next_mint(monkeypatch):
    auth._image_nonces.clear()
    clock = {"t": 1000.0}
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock["t"])
    auth.mint_image_nonce()
    assert len(auth._image_nonces) == 1
    clock["t"] += auth.NONCE_TTL + 1
    auth.mint_image_nonce()
    assert len(auth._image_nonces) == 1, (
        "an unconsumed nonce used to live for the life of the process"
    )
    auth._image_nonces.clear()


def test_a_live_nonce_survives_a_later_mint(monkeypatch):
    auth._image_nonces.clear()
    clock = {"t": 1000.0}
    monkeypatch.setattr(auth.time, "monotonic", lambda: clock["t"])
    first = auth.mint_image_nonce()
    clock["t"] += 1
    auth.mint_image_nonce()
    assert first in auth._image_nonces
    auth._image_nonces.clear()


def test_the_test_reset_actually_detaches_the_tracker_handler():
    log = logging.getLogger("bridge_error_tracker_probe")
    error_tracker._reset_for_tests()
    assert error_tracker.install_handler(log) is True
    before = len(log.handlers)
    error_tracker._reset_for_tests()
    assert len(log.handlers) == before - 1, (
        "leaving it attached made the NEXT install double-count every record"
    )
    error_tracker._reset_for_tests()


def test_two_install_reset_cycles_do_not_stack_handlers():
    log = logging.getLogger("bridge_error_tracker_probe2")
    for _ in range(3):
        error_tracker._reset_for_tests()
        error_tracker.install_handler(log)
    error_tracker._reset_for_tests()
    assert not log.handlers


@pytest.mark.parametrize("bad", ["60", None, "", "auto", [], {}])
def test_a_non_numeric_refresh_interval_falls_back_instead_of_killing_boot(bad, caplog):
    with caplog.at_level(logging.WARNING):
        got = scheduler_mod._coerce_interval(bad, "pulse", 60)
    if bad == "60":
        assert got == 60.0          # a numeric string is coercible, not a fault
    else:
        assert got == 60
        assert "not a number" in caplog.text


def test_a_zero_interval_is_raised_to_the_floor(caplog):
    with caplog.at_level(logging.WARNING):
        assert scheduler_mod._coerce_interval(0, "pulse", 60) == scheduler_mod.MIN_INTERVAL_S
    assert "below the" in caplog.text


def test_a_sane_interval_passes_through():
    assert scheduler_mod._coerce_interval(120, "pulse", 60) == 120.0


# ============================================================
# Readers that must degrade, not raise
# ============================================================

def test_a_session_registry_holding_a_list_reads_as_empty(tmp_path):
    reg = tmp_path / "active-sessions.json"
    reg.write_text('["not", "a", "map"]', encoding="utf-8")
    assert sessions.read_registry(reg) == {}
    assert sessions.session_for_cwd(reg, "/anywhere") is None


def test_a_valid_session_registry_still_reads(tmp_path):
    reg = tmp_path / "active-sessions.json"
    reg.write_text(json.dumps({"/w": {"session_id": "s1"}}), encoding="utf-8")
    assert sessions.session_for_cwd(reg, "/w") == "s1"


def test_a_pulse_snapshot_that_is_a_list_is_a_miss(tmp_path, monkeypatch):
    snap = pulse_ref.snapshot_path(tmp_path)
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text("[1, 2, 3]", encoding="utf-8")
    assert pulse_ref.read_snapshot(tmp_path) is None


def test_a_pulse_snapshot_missing_computed_at_is_a_miss(tmp_path, caplog):
    snap = pulse_ref.snapshot_path(tmp_path)
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(json.dumps({"data": {"x": 1}}), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert pulse_ref.read_snapshot(tmp_path) is None
    assert "unexpected shape" in caplog.text


def test_a_well_formed_pulse_snapshot_is_returned(tmp_path):
    snap = pulse_ref.snapshot_path(tmp_path)
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(json.dumps({"data": {"x": 1}, "computed_at": "2026-08-24T00:00:00+00:00"}),
                    encoding="utf-8")
    got = pulse_ref.read_snapshot(tmp_path)
    assert got is not None and got["data"] == {"x": 1}


# ============================================================
# One path validator for the drafts tree
# ============================================================

@pytest.mark.parametrize("bad", [
    "",
    "outputs/elsewhere/x.md",
    approvals.EMAIL_DRAFTS_DIR + "/../secrets.md",
    approvals.EMAIL_DRAFTS_DIR + "/.hidden.md",
    approvals.EMAIL_DRAFTS_DIR + "/notes.txt",
])
def test_the_writers_reject_what_the_reader_rejects(bad, tmp_path):
    assert approvals.validate_draft_rel_path(bad) is not None
    assert approvals.mark_sent(tmp_path, bad)["ok"] is False
    assert approvals.undo_sent(tmp_path, bad)["ok"] is False


def test_a_traversal_path_never_reaches_the_sent_log(tmp_path):
    """It used to: mark_sent checked only the prefix, so a `..` string was
    written into the log even though read_draft would never open it."""
    approvals.mark_sent(tmp_path, approvals.EMAIL_DRAFTS_DIR + "/../evil.md")
    log = tmp_path / approvals.SENT_LOG_FILE
    assert not log.exists() or "evil.md" not in log.read_text(encoding="utf-8")


def test_a_legitimate_draft_path_is_accepted(tmp_path):
    good = approvals.EMAIL_DRAFTS_DIR + "/2026-08-24-a-draft.md"
    assert approvals.validate_draft_rel_path(good) is None
    assert approvals.mark_sent(tmp_path, good)["ok"] is True


# ============================================================
# The CRM interaction date is a date
# ============================================================

@pytest.mark.parametrize("raw", [
    "", "not-a-date-xx", "2026-13-45", "20260824", "x" * 30,
    # A SHORT one. The digits parse and the calendar accepts them, so only the
    # `YYYY-MM-DD` shape check rejects it -- without that, "2026-08-2" was
    # written into a contact file as an interaction date.
    "2026-08-2",
])
def test_a_malformed_timestamp_does_not_become_a_crm_date(raw):
    from datetime import date as _date

    got = crm_log._interaction_date(raw)
    assert crm_log._ISO_DATE_RE.match(got), got
    _date.fromisoformat(got)          # raises if the fallback is not a real date
    assert got != raw[:10]


def test_a_real_timestamp_keeps_its_date():
    assert crm_log._interaction_date("2026-05-20T09:00:00+00:00") == "2026-05-20"


def test_the_contact_write_is_locked():
    code = _code_only(PKG / "finalizers" / "crm_log.py")
    assert "with _CONTACT_WRITE_LOCK:" in code


# ============================================================
# Host parsing and the threat model
# ============================================================

def test_an_unbracketed_ipv6_loopback_host_is_not_mangled():
    from scripts.bridge_daemon.app import build_app

    code = _code_only(PKG / "app.py")
    assert 'if raw.count(":") > 1:' in code, (
        'rsplit(":", 1) turned "::1" into "::" and the loopback check then 421ed'
    )
    assert callable(build_app)


def test_the_health_comment_no_longer_claims_a_control_it_is_not():
    text = (PKG / "app.py").read_text(encoding="utf-8")
    assert "they leak workflow cadence to any local process" not in text
    assert "hands\n        # the full bearer token to any loopback caller" in text


def test_the_terminal_title_default_comes_from_the_operator_seam():
    code = _code_only(PKG / "app.py")
    assert 'title: str = _DEFAULT_TERMINAL_TITLE' in code
    assert 'title: str = "31C"' not in code


# ============================================================
# The port is held, not just probed (bridge-daemon entry)
# ============================================================

def test_the_daemon_binds_the_port_it_reports(tmp_path):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "bridge_entry_probe", ROOT / "scripts" / "bridge-daemon.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bridge_entry_probe"] = mod
    spec.loader.exec_module(mod)

    port, sock = mod._pick_port(45000)
    try:
        with (socket.socket(socket.AF_INET, socket.SOCK_STREAM) as thief,
              pytest.raises(OSError)):
            thief.bind(("127.0.0.1", port))
    finally:
        sock.close()


# ============================================================
# One bad record must not take down a whole page
# ============================================================
#
# The 01-p4 shard's own synthesis, and it is the right one: every file in
# `sources/` validates carefully at its drill-down boundary and then the LIST
# path reads the same data with weaker guards. Seventeen of its eighteen
# findings were already repaired by 2026-08-23; the size cap was the survivor.

def test_the_contact_list_scan_honours_the_same_size_cap_as_the_drill_down(tmp_path):
    from scripts.bridge_daemon.sources import contacts

    big = tmp_path / "huge-contact.md"
    big.write_text("x" * (contacts.CONTACT_FILE_MAX_BYTES + 1), encoding="utf-8")
    assert contacts._contact_record(big, "ceo", None) is None, (
        "the list scan read every byte of a file the drill-down refuses to open"
    )


def test_a_normal_contact_still_parses(tmp_path):
    from scripts.bridge_daemon.sources import contacts

    ok = tmp_path / "ada-lovelace.md"
    ok.write_text("---\nlast_touch: 2026-01-01\n---\n\n# Ada\n", encoding="utf-8")
    row = contacts._contact_record(ok, "ceo", None)
    assert row is not None and row["slug"] == "ada-lovelace"


def test_an_undecodable_contact_is_skipped_not_raised(tmp_path):
    from scripts.bridge_daemon.sources import contacts

    bad = tmp_path / "mojibake.md"
    bad.write_bytes(b"\xff\xfe\x00bad")
    assert contacts._contact_record(bad, "ceo", None) is None


def test_a_conversation_with_a_word_where_a_count_belongs_does_not_raise():
    from scripts.bridge_daemon.sources import conversations

    assert conversations._as_count("three") == 0
    assert conversations._as_count(None) == 0
    assert conversations._as_count([1, 2]) == 0
    assert conversations._as_count("7") == 7
    # `isinstance(True, int)` is True in Python, so without the bool guard a
    # JSON `"message_count": true` renders as the number 1 -- a fabricated
    # count, which is worse than the 0 that says "the field was unusable".
    assert conversations._as_count(True) == 0
    assert conversations._as_count(False) == 0


def test_a_conversation_whose_analysis_is_a_string_does_not_raise():
    from scripts.bridge_daemon.sources import conversations

    assert conversations._as_mapping("oops") == {}
    assert conversations._as_mapping(None) == {}
    assert conversations._as_mapping({"priority": "high"}) == {"priority": "high"}


def test_a_numeric_priority_does_not_reach_lower():
    from scripts.bridge_daemon.sources import conversations

    assert conversations._as_text(5) == ""
    assert conversations._as_text("High") == "High"


def test_a_critical_entry_with_no_timestamp_does_not_poison_the_sort():
    """Reads `critical.entry_ts`, not a private `_entry_ts`.

    The module held its own copy of this guard while eight siblings with the
    identical read had none. On 2026-08-24 the copy was deleted and the module
    took `_shapes.entry_ts`, which is the same function under the name the
    whole package now shares.
    """
    from scripts.bridge_daemon.sources import critical

    assert critical.entry_ts({"id": "abc"}) == ""
    assert critical.entry_ts({"id": "abc", "ts": None}) == ""
    assert critical.entry_ts({"id": "abc", "ts": "2026-08-24T00:00:00Z"}) == "2026-08-24T00:00:00Z"
    # The point: these are all comparable with each other.
    sorted([critical.entry_ts(e) for e in
            ({"id": "a"}, {"id": "b", "ts": None}, {"id": "c", "ts": "2026-01-01"})])


def test_a_hand_edited_undo_of_one_is_still_a_tombstone():
    """`entry.get("undo") is True` resurrected anything not serialised by
    Python's json.dumps, handing the CEO back an item they had unmarked."""
    code = _code_only(PKG / "sources" / "critical.py")
    assert 'entry.get("undo") is True' not in code
    # `is_undo`, since 2026-08-24. The truthiness test that fixed this module
    # was one of three guards written once and applied to one of the nine
    # readers with the identical bug; it now lives in `_shapes` and every
    # reader calls it.
    assert "if is_undo(entry):" in code


def test_naive_and_offset_conversation_stamps_order_together():
    from scripts.bridge_daemon.sources import conversations

    naive = conversations._parse_ts("2026-06-01T10:00:00")
    aware = conversations._parse_ts("2026-06-01T10:00:00Z")
    assert naive == aware, (
        "a naive stamp read in local time sorted hours away from the same instant"
    )


def test_an_unparseable_conversation_stamp_sorts_last_instead_of_raising():
    from scripts.bridge_daemon.sources import conversations

    assert conversations._parse_ts("not a time") == 0.0
    assert conversations._parse_ts(None) == 0.0


def test_the_telemetry_vocabulary_constant_matches_what_the_daemon_writes():
    """`TELEMETRY_EVENT_TYPES` is read by nothing, and that is deliberate: the
    counter is open-ended so a writer-side change SHOWS instead of hiding.

    But documentation nobody verifies rots. A fifth `tel.event(...)` type would
    leave the constant quietly wrong, and the docstring above it quietly wrong
    with it. This test is what makes the constant a checked claim rather than a
    comment: it reads the actual call sites out of `app.py`.
    """
    import ast

    from scripts.bridge_daemon.sources.ops import TELEMETRY_EVENT_TYPES

    tree = ast.parse((PKG / "app.py").read_text(encoding="utf-8"))
    emitted = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "event"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            emitted.add(node.args[0].value)

    assert emitted, "found no tel.event() call sites; the detector broke"
    assert emitted == set(TELEMETRY_EVENT_TYPES), (
        f"the daemon writes {sorted(emitted)} but the constant names "
        f"{sorted(TELEMETRY_EVENT_TYPES)}"
    )


# ============================================================
# 02-p2: analytics must never be able to stop the daemon
# ============================================================

def test_telemetry_construction_survives_an_unwritable_tree(tmp_path, monkeypatch, caplog):
    """A read-only filesystem killed daemon STARTUP over usage analytics.

    `event()` was hardened to swallow OSError; the constructor's mkdir was not,
    so the exact failure the class absorbs at write time was fatal at build
    time.
    """
    from scripts.bridge_daemon.telemetry import Telemetry

    real_mkdir = Path.mkdir

    def refuse(self, *a, **kw):
        if ".daemon-state" in str(self):
            raise OSError("read-only file system")
        return real_mkdir(self, *a, **kw)

    monkeypatch.setattr(Path, "mkdir", refuse)
    with caplog.at_level(logging.WARNING):
        tel = Telemetry(tmp_path)
    assert tel.path.name == "usage.jsonl"
    assert "not writable" in caplog.text


def test_a_telemetry_event_on_an_unwritable_tree_does_not_raise(tmp_path, monkeypatch):
    from scripts.bridge_daemon.telemetry import Telemetry

    tel = Telemetry(tmp_path)

    def refuse(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", refuse)
    tel.event("page_view", page="pulse")     # must not raise


def test_telemetry_retries_the_directory_on_write(tmp_path, monkeypatch):
    """The constructor's mkdir is best-effort, so a tree that becomes writable
    later must start logging. Without the retry the guard above would convert a
    transient fault into a permanent one."""
    from scripts.bridge_daemon.telemetry import Telemetry

    real_mkdir = Path.mkdir
    monkeypatch.setattr(Path, "mkdir",
                        lambda self, *a, **kw: (_ for _ in ()).throw(OSError("ro")))
    tel = Telemetry(tmp_path)
    assert not tel.path.parent.exists()

    monkeypatch.setattr(Path, "mkdir", real_mkdir)
    tel.event("launch", action="x")
    assert tel.path.is_file(), "the write never recreated the directory"


def test_a_non_serialisable_telemetry_field_does_not_reach_the_caller(tmp_path):
    from datetime import timedelta

    from scripts.bridge_daemon.telemetry import Telemetry

    tel = Telemetry(tmp_path)
    tel.event("finalize", elapsed=timedelta(seconds=3))   # must not raise
    assert tel.path.is_file()


def test_one_unreadable_artifact_folder_does_not_fail_the_studio_listing(tmp_path, monkeypatch, caplog):
    from scripts.bridge_daemon.sources import studio

    folder = tmp_path / "a-post"
    folder.mkdir()

    def refuse(self):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "iterdir", refuse)
    with caplog.at_level(logging.WARNING):
        assert studio._artifact_images(folder, tmp_path) == []
    assert "unreadable artifact folder" in caplog.text


def test_a_readable_artifact_folder_still_lists_its_images(tmp_path):
    from scripts.bridge_daemon.sources import studio

    folder = tmp_path / "a-post"
    folder.mkdir()
    (folder / "hero.png").write_bytes(b"x")
    (folder / "a-post.md").write_text("# post", encoding="utf-8")
    assert studio._artifact_images(folder, tmp_path) == ["a-post/hero.png"]


def test_the_tribe_preview_annotation_admits_the_none_it_returns():
    import inspect

    from scripts.bridge_daemon.sources import pulse

    sig = inspect.signature(pulse.tribe_state_preview)
    assert "None" in str(sig.return_annotation), (
        "it returns None on an unavailable tribe source; the annotation said dict"
    )


def test_the_raise_progress_docstring_lists_every_key_it_returns():
    """Two keys were returned and undocumented, so a consumer written against
    the docstring silently missed them."""
    from scripts.bridge_daemon.sources import pulse

    doc = pulse.raise_progress.__doc__ or ""
    for key in ("target", "total", "sendable_total", "sendable_drafts",
                "first_5_total", "first_5_drafts", "sendable_sent", "first_5_sent"):
        assert f'"{key}"' in doc, f"{key} is returned but undocumented"


def test_the_task_list_docstring_names_the_key_mark_done_needs():
    from scripts.bridge_daemon.sources import tasks

    doc = tasks.list_active_tasks.__doc__ or ""
    for key in ("task_key", "done_filtered", "done_log_count"):
        assert f'"{key}"' in doc, f"{key} is returned but undocumented"


# ============================================================
# 02-p3: the terminal flag follows the real binary, not the symlink
# ============================================================

def test_a_wrapper_symlink_dispatches_on_what_it_points_at(tmp_path):
    """`x-terminal-emulator` is an alternatives symlink with no flag semantics.

    Dispatching on THAT name always fell through to `-e`, which gnome-terminal
    rejects. The launch then reported success while no window opened.
    """
    from scripts.bridge_daemon import terminal

    real = tmp_path / "gnome-terminal"
    real.write_text("#!/bin/sh\n", encoding="utf-8")
    link = tmp_path / "x-terminal-emulator"
    link.symlink_to(real)

    cmd = terminal.build_linux_attach_command(str(link), "ceo")
    assert cmd[1] == "--", f"gnome-terminal needs `--`, got {cmd[1]!r}"


def test_the_legacy_wrapper_still_gets_the_flag_it_accepts(tmp_path):
    """`gnome-terminal.wrapper` exists to translate `-e`; it must NOT collapse
    into the same name as the binary it wraps."""
    from scripts.bridge_daemon import terminal

    real = tmp_path / "gnome-terminal.wrapper"
    real.write_text("#!/bin/sh\n", encoding="utf-8")
    link = tmp_path / "x-terminal-emulator"
    link.symlink_to(real)

    assert terminal.build_linux_attach_command(str(link), "ceo")[1] == "-e"


def test_kitty_takes_the_command_positionally(tmp_path):
    from scripts.bridge_daemon import terminal

    real = tmp_path / "kitty"
    real.write_text("#!/bin/sh\n", encoding="utf-8")
    cmd = terminal.build_linux_attach_command(str(real), "ceo")
    assert cmd[1] == "tmux", cmd


def test_an_unresolvable_terminal_path_falls_back_to_its_own_name():
    from scripts.bridge_daemon import terminal

    assert terminal._resolved_terminal_name("/no/such/kitty") == "kitty"


def test_the_attach_target_is_still_allowlist_validated(tmp_path):
    """The module header claims validation covers every builder. It did not
    cover this one, and an exported builder is exactly the future caller the
    claim names."""
    from scripts.bridge_daemon import terminal

    real = tmp_path / "xterm"
    real.write_text("#!/bin/sh\n", encoding="utf-8")
    with pytest.raises(ValueError):
        terminal.build_linux_attach_command(str(real), "bad slug; rm -rf /")
