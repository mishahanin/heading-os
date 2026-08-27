"""Shard 35: the bridge and queue surfaces, where a slice was published as the
whole set and a second record silently replaced the first.

Every defect here was verified by reading the decisive line and, where it could
be driven, by running it.

* ``approvals.list_approvals`` sliced ``items`` to APPROVALS_ROW_CAP and then
  reported ``len(items)`` as ``total``. ``pulse.py`` reads that into the
  ``approvals_total`` KPI the dashboard labels "drafts waiting for approval", so
  a backlog of 35 read as exactly the cap and then stopped moving.

* ``threads.list_active_threads`` measured ``total`` BEFORE the slice and the
  per-bucket ``counts`` AFTER it, so the two disagreed above THREADS_ROW_CAP.
  Half of one function measured the set and half measured the page.

* ``pipeline.list_pipeline`` stopped PARSING at PIPELINE_ROW_CAP, so
  ``total_value_usd``, ``counts`` and ``overdue_count`` were sums over the first
  N rows of the markdown, published with nothing saying so - and ``pulse.py``
  compares that value against the file's own summary line, so past the cap the
  dashboard raised a drift warning about a discrepancy it had created.

* ``action_queue.append_cards`` de-duplicated against the literal
  ``("pending", "approved")`` while ACTIVE_STATUSES is those two plus
  ``send_failed``. A contact whose send failed got a SECOND live card, and
  approving both mails them twice.

* The same function's prune read the literal ``("sent", "dismissed")``, but the
  daemon's tier sweep writes the terminal status ``applied``. Those cards were
  in no status tuple at all: never pruned, and counted on no surface, under a
  comment promising bound growth and a rule promising a one-click undo.

* ``dead_letter.record`` named entries ``<trace_id>__<kind>.json``. One deposit
  stamps every card with one trace_id and ``kind`` is the action_type, so two
  failed sends from one deposit collided and ``os.replace`` clobbered the first
  while ``record`` returned a path for both.

* ``investors._read_send_log`` returned ``{}`` for a log over its size cap, so
  one byte past 1 MB made every firm read as never-sent. The WRITE half of the
  same log had already moved to the shared O_APPEND primitive.

* ``bridge-daemon.check_health`` printed any readable JSON on the port named in
  ``.daemon-state/port`` as this daemon's health, exit 0. Its own comment
  described that scenario for an unreadable body; a readable one went through.

* ``contacts._owner_label`` returned a hardcoded operator name in the public
  engine while ``operator_identity`` was already resolving the same question
  three functions above it.

Run: python3 -m pytest tests/test_eight_counters_that_reported_a_slice_as_the_whole.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon.sources import action_queue as aq  # noqa: E402
from scripts.bridge_daemon.sources import approvals as ap  # noqa: E402
from scripts.bridge_daemon.sources import contacts as ct  # noqa: E402
from scripts.bridge_daemon.sources import investors as inv  # noqa: E402
from scripts.bridge_daemon.sources import pipeline as pl  # noqa: E402
from scripts.bridge_daemon.sources import threads as th  # noqa: E402
from scripts.utils import dead_letter as dl  # noqa: E402


# ============================================================
# 1. approvals: a capped page reported as a count
# ============================================================

def _draft(directory: Path, index: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"draft-{index:03d}.md").write_text(
        f"To: person{index}@example.invalid\n"
        f"Subject: Draft number {index}\n\n"
        f"Body {index}\n",
        encoding="utf-8")


def test_the_approval_total_counts_the_backlog_not_the_page(tmp_path):
    directory = tmp_path / ap.EMAIL_DRAFTS_DIR
    over = ap.APPROVALS_ROW_CAP + 15
    for i in range(over):
        _draft(directory, i)

    payload = ap.list_approvals(tmp_path)
    assert len(payload["items"]) == ap.APPROVALS_ROW_CAP, "the page is still capped"
    assert payload["total"] == over, (
        f"total reported {payload['total']} for {over} pending drafts; a "
        "truncated list was published as a complete count")
    assert payload["truncated"] is True
    assert payload["row_cap"] == ap.APPROVALS_ROW_CAP


def test_an_uncapped_approval_set_is_not_flagged_truncated(tmp_path):
    directory = tmp_path / ap.EMAIL_DRAFTS_DIR
    for i in range(3):
        _draft(directory, i)
    payload = ap.list_approvals(tmp_path)
    assert payload["total"] == 3
    assert payload["truncated"] is False


def test_an_empty_approval_set_still_carries_the_shape(tmp_path):
    """The degraded path must not return a narrower dict than the parsed one.

    It did, on the day the keys were added, and this test is what found it.
    `pipeline.py` had already been through this and pulled its zero payload
    into a single `_empty_pipeline` writer for exactly this reason.
    """
    absent = ap.list_approvals(tmp_path)
    _draft(tmp_path / ap.EMAIL_DRAFTS_DIR, 0)
    parsed = ap.list_approvals(tmp_path)
    assert set(absent) == set(parsed)
    assert absent["total"] == 0
    assert absent["truncated"] is False
    assert absent["row_cap"] == ap.APPROVALS_ROW_CAP


def test_an_empty_thread_set_still_carries_the_shape(tmp_path):
    absent = th.list_active_threads(tmp_path)
    _thread_file(tmp_path / "threads" / "business", 0, days_ago=1)
    parsed = th.list_active_threads(tmp_path)
    assert set(absent) == set(parsed)
    assert absent["truncated"] is False
    assert absent["row_cap"] == th.THREADS_ROW_CAP


# ============================================================
# 2. threads: half the function measured the set, half the page
# ============================================================

def _thread_file(directory: Path, index: int, days_ago: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_ago)).date().isoformat()
    (directory / f"2026-01-01-thread-{index:03d}.md").write_text(
        "---\n"
        "status: active\n"
        f"title: Thread number {index}\n"
        f"last_activity: {stamp}\n"
        "---\n\nbody\n",
        encoding="utf-8")


def test_the_bucket_counts_and_the_total_agree_past_the_cap(tmp_path):
    directory = tmp_path / "threads" / "business"
    over = th.THREADS_ROW_CAP + 12
    for i in range(over):
        _thread_file(directory, i, days_ago=i % 40)

    payload = th.list_active_threads(tmp_path)
    assert payload["total"] == over
    assert len(payload["threads"]) == th.THREADS_ROW_CAP, "the page is still capped"
    assert sum(payload["counts"].values()) == payload["total"], (
        f"counts sum to {sum(payload['counts'].values())} while total says "
        f"{payload['total']}; one of the two measured the page")
    assert payload["truncated"] is True


def test_the_thread_counts_are_unchanged_below_the_cap(tmp_path):
    directory = tmp_path / "threads" / "business"
    for i in range(5):
        _thread_file(directory, i, days_ago=i)
    payload = th.list_active_threads(tmp_path)
    assert payload["total"] == 5
    assert sum(payload["counts"].values()) == 5
    assert payload["truncated"] is False


# ============================================================
# 3. pipeline: aggregates over the first N rows of the file
# ============================================================

def _pipeline_file(tmp_path: Path, rows: int, value_each: int) -> Path:
    path = tmp_path / pl.PIPELINE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Pipeline", "", "## Active Deals", "",
        "| Company | Country | Stage | Value | Stage Date | Owner | Next Action | Due |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i in range(rows):
        lines.append(
            f"| Company {i:03d} | Freedonia | Discovery | ${value_each:,} | "
            f"2026-01-01 | Operator | Follow up | 2027-01-01 |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_the_pipeline_value_sums_every_row_not_the_first_page(tmp_path):
    rows = pl.PIPELINE_ROW_CAP + 23
    _pipeline_file(tmp_path, rows, value_each=1000)

    payload = pl.list_pipeline(tmp_path)
    assert payload["total"] == rows
    assert len(payload["deals"]) == pl.PIPELINE_ROW_CAP, "the page is still capped"
    assert payload["total_value_usd"] == rows * 1000, (
        f"summed {payload['total_value_usd']} over {rows} deals of 1000; the "
        "parse stopped at the row cap and the shortfall was published as the "
        "pipeline value")
    assert sum(payload["counts"].values()) == rows
    assert payload["truncated"] is True
    assert payload["row_cap"] == pl.PIPELINE_ROW_CAP


def test_a_small_pipeline_is_unchanged(tmp_path):
    _pipeline_file(tmp_path, 4, value_each=250)
    payload = pl.list_pipeline(tmp_path)
    assert payload["total"] == 4
    assert payload["total_value_usd"] == 1000
    assert payload["truncated"] is False


def test_the_degraded_pipeline_payload_carries_the_same_keys(tmp_path):
    """A missing file must not return a narrower dict than a parsed one.

    The repo already has a test for this contract; it is repeated here against
    the three keys this change added, because the contract is what stopped the
    LAST key from going missing on the degraded path only.
    """
    absent = pl.list_pipeline(tmp_path)
    _pipeline_file(tmp_path, 2, value_each=1)
    parsed = pl.list_pipeline(tmp_path)
    assert set(absent) == set(parsed)
    for key in ("total", "truncated", "row_cap"):
        assert key in absent, key


# ============================================================
# 4. action queue: a live card that dedup did not consider live
# ============================================================

def _card(company: str) -> dict:
    return {
        "action_type": "email_send",
        "company": company,
        "contact_slug": company.lower(),
        "title": f"Follow up with {company}",
    }


def _statuses(workspace_root: Path) -> list:
    data = json.loads((workspace_root / aq.QUEUE_FILE).read_text(encoding="utf-8"))
    return [c.get("status") for c in data["actions"]]


@pytest.mark.parametrize("status", ["pending", "approved", "send_failed"])
def test_a_card_in_any_active_status_blocks_a_duplicate(tmp_path, status):
    """`send_failed` is in ACTIVE_STATUSES, and dedup used to skip it.

    A send_failed card is shown by every lister and `SENDABLE_STATUSES` in
    scripts/action-queue.py includes it, so the CEO can approve it. A second
    live card for the same contact means the person can be mailed twice.
    """
    first = aq.append_cards(tmp_path, [_card("Acme")])
    assert first["added"] == 1
    if status != "pending":
        aq.apply_status(tmp_path, first["ids"][0], status)

    second = aq.append_cards(tmp_path, [_card("Acme")])
    assert second["added"] == 0, (
        f"a second live card was created beside a {status} one: {second}")
    assert second["skipped"] == 1
    assert len(_statuses(tmp_path)) == 1


def test_the_dedup_reads_the_shared_status_tuple_not_a_copy():
    """The literal that caused this. A second copy is the one that rots."""
    source = (ROOT / "scripts/bridge_daemon/sources/action_queue.py").read_text(
        encoding="utf-8")
    body = source[source.index("def append_cards("):source.index("def _find(")]
    assert 'c.get("status") in ACTIVE_STATUSES' in body
    assert '("pending", "approved")' not in body, (
        "the dedup is testing a hand-written status list again")


def test_a_terminal_card_does_not_block_a_new_proposal(tmp_path):
    """Vacuity guard: widening dedup must not freeze the queue.

    A sent card is finished, and the same contact must be proposable again.
    """
    first = aq.append_cards(tmp_path, [_card("Acme")])
    aq.apply_status(tmp_path, first["ids"][0], "sent")
    second = aq.append_cards(tmp_path, [_card("Acme")])
    assert second["added"] == 1, second


# ============================================================
# 5. action queue: the status in no tuple at all
# ============================================================

def test_applied_is_terminal_and_is_pruned(tmp_path):
    first = aq.append_cards(tmp_path, [{"action_type": "pipeline_update",
                                        "company": "Acme"}])
    card_id = first["ids"][0]
    aq.apply_status(tmp_path, card_id, "applied", event="auto_apply")

    # Age it past the prune window, the way the sent/dismissed cases are aged.
    queue_path = tmp_path / aq.QUEUE_FILE
    data = json.loads(queue_path.read_text(encoding="utf-8"))
    old = (datetime.now(timezone.utc)
           - timedelta(days=aq.PRUNE_TERMINAL_DAYS + 5)).isoformat()
    data["actions"][0]["applied_at"] = old
    data["actions"][0]["created_at"] = old
    queue_path.write_text(json.dumps(data), encoding="utf-8")

    aq.append_cards(tmp_path, [])
    assert _statuses(tmp_path) == [], (
        "an auto-applied card survived the prune, so the queue file grows "
        "without bound under a comment promising it does not")


def test_a_recent_applied_card_is_not_pruned(tmp_path):
    """The bound is an age, not a status blocklist."""
    first = aq.append_cards(tmp_path, [{"action_type": "pipeline_update",
                                        "company": "Acme"}])
    aq.apply_status(tmp_path, first["ids"][0], "applied", event="auto_apply")
    aq.append_cards(tmp_path, [])
    assert _statuses(tmp_path) == ["applied"]


def _stamp_the_only_card(tmp_path, **stamps):
    """Overwrite timestamps on the single queued card, in place."""
    queue_path = tmp_path / aq.QUEUE_FILE
    data = json.loads(queue_path.read_text(encoding="utf-8"))
    data["actions"][0].update(stamps)
    queue_path.write_text(json.dumps(data), encoding="utf-8")


def _days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_an_old_card_applied_today_keeps_its_prune_window(tmp_path):
    """The two stamps must DISAGREE for this to measure anything.

    `test_applied_is_terminal_and_is_pruned` above sets `applied_at` and
    `created_at` to the same old date, so dropping the `applied_at` read from
    the prune's stamp lookup changes no verdict there and the mutation survived
    it. This is the case that separates them, and it is the real one: a card
    deposited months ago and applied this morning is fresh, and pruning it on
    its deposit date deletes a record the CEO has had one day to undo.
    """
    first = aq.append_cards(tmp_path, [{"action_type": "pipeline_update",
                                        "company": "Acme"}])
    aq.apply_status(tmp_path, first["ids"][0], "applied", event="auto_apply")
    _stamp_the_only_card(
        tmp_path,
        created_at=_days_ago(aq.PRUNE_TERMINAL_DAYS + 5),
        applied_at=_days_ago(0),
    )

    aq.append_cards(tmp_path, [])
    assert _statuses(tmp_path) == ["applied"], (
        "the prune aged an applied card by its deposit date, not by the day it "
        "was applied, so a long-open card was deleted the moment it finished")


def test_a_freshly_deposited_card_applied_long_ago_is_still_pruned(tmp_path):
    """The mirror, so the pair pins the stamp that is read rather than one side.

    An impossible ordering on the wall clock, and that is the point: only the
    `applied_at` read can produce this verdict, so a fallback to `created_at`
    cannot pass both this test and the one above.
    """
    first = aq.append_cards(tmp_path, [{"action_type": "pipeline_update",
                                        "company": "Acme"}])
    aq.apply_status(tmp_path, first["ids"][0], "applied", event="auto_apply")
    _stamp_the_only_card(
        tmp_path,
        created_at=_days_ago(0),
        applied_at=_days_ago(aq.PRUNE_TERMINAL_DAYS + 5),
    )

    aq.append_cards(tmp_path, [])
    assert _statuses(tmp_path) == [], (
        "the prune fell back to the deposit date, so an applied card outlives "
        "its window whenever the card was deposited recently")


def test_apply_status_dates_every_terminal_status(tmp_path):
    """Without its own stamp, `applied` fell back to `created_at`, so a card
    deposited months ago was prunable the instant the daemon applied it."""
    first = aq.append_cards(tmp_path, [{"action_type": "pipeline_update",
                                        "company": "Acme"}])
    aq.apply_status(tmp_path, first["ids"][0], "applied", event="auto_apply")
    data = json.loads((tmp_path / aq.QUEUE_FILE).read_text(encoding="utf-8"))
    assert data["actions"][0].get("applied_at"), data["actions"][0]


def test_every_status_the_code_writes_is_active_or_terminal():
    """The invariant that made `applied` findable: there is no third state.

    Read from the source, so a new status added to `apply_status`'s stamp map
    without a home in either tuple fails here rather than in production.
    """
    known = set(aq.ACTIVE_STATUSES) | set(aq.TERMINAL_STATUSES)
    source = (ROOT / "scripts/bridge_daemon/sources/action_queue.py").read_text(
        encoding="utf-8")
    stamp_block = source[source.index("stamp_field = {"):]
    stamp_block = stamp_block[:stamp_block.index("}.get(status)")]
    written = {line.split('"')[1] for line in stamp_block.splitlines()
               if line.strip().startswith('"')}
    assert written, "the stamp map could not be read; this test measures nothing"
    assert written <= known, f"statuses in neither tuple: {sorted(written - known)}"


def test_the_applied_count_reaches_a_surface(tmp_path):
    first = aq.append_cards(tmp_path, [{"action_type": "pipeline_update",
                                        "company": "Acme"}])
    aq.apply_status(tmp_path, first["ids"][0], "applied", event="auto_apply")
    envelope = aq.list_action_queue(tmp_path)
    assert envelope["applied_count"] == 1, (
        "a card the daemon applied by itself appears on no surface the CEO "
        "reads, so the one-click undo has no id to act on")
    assert envelope["total"] == 0, "an applied card is terminal, not active"


# ============================================================
# 6. dead letter: the second record replaced the first
# ============================================================

def test_two_failures_in_one_trace_produce_two_records(tmp_path):
    made = [
        dl.record(trace_id="-", kind="email_send", payload={"n": n},
                  classification="permanent", error=f"boom {n}",
                  workspace_root=tmp_path)
        for n in range(3)
    ]
    assert all(p is not None for p in made)
    assert len(set(made)) == 3, f"records collided: {made}"
    payloads = sorted(dl.load(p)["payload"]["n"] for p in made)
    assert payloads == [0, 1, 2], "a record was overwritten by the next one"


def test_the_first_record_keeps_the_plain_name(tmp_path):
    """The suffix is for collisions only; the ordinary case is unchanged."""
    path = dl.record(trace_id="abc123", kind="email_send", payload={},
                     classification="permanent", error="x",
                     workspace_root=tmp_path)
    assert path.name == "abc123__email_send.json"


def test_different_kinds_never_needed_a_suffix(tmp_path):
    first = dl.record(trace_id="abc123", kind="email_send", payload={},
                      classification="permanent", error="x",
                      workspace_root=tmp_path)
    second = dl.record(trace_id="abc123", kind="pipeline_update", payload={},
                       classification="permanent", error="x",
                       workspace_root=tmp_path)
    assert "__2" not in second.name
    assert first != second


def test_every_record_is_listed(tmp_path):
    for n in range(4):
        dl.record(trace_id="-", kind="email_send", payload={"n": n},
                  classification="permanent", error="x", workspace_root=tmp_path)
    assert len(dl.list_entries(workspace_root=tmp_path)) == 4


# ============================================================
# 7. investors: one byte past the cap erased every mark
# ============================================================

def _send_log(tmp_path: Path, entries: list) -> Path:
    path = tmp_path / inv.PROGRAM_DIR / inv.SEND_LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries),
                    encoding="utf-8")
    return path


def test_an_oversized_send_log_still_reports_the_recent_marks(tmp_path,
                                                              monkeypatch):
    entries = [{"firm_num": n, "date": "2026-05-01", "ts": "2026-05-01T00:00:00",
                "note": "x" * 200} for n in range(200)]
    _send_log(tmp_path, entries)
    monkeypatch.setattr(inv, "SEND_LOG_MAX_BYTES", 4000)

    log = inv._read_send_log(tmp_path)
    assert log, (
        "a send log past its size cap read as empty, so every firm showed as "
        "never contacted and invited a second first-touch")
    assert len(log) < 200, "the tail read is still bounded"


def test_a_small_send_log_is_read_whole(tmp_path):
    _send_log(tmp_path, [
        {"firm_num": 1, "date": "2026-05-01", "ts": "t1", "note": "a"},
        {"firm_num": 2, "date": "2026-05-02", "ts": "t2", "note": "b"},
    ])
    log = inv._read_send_log(tmp_path)
    assert set(log) == {1, 2}
    assert log[2]["note"] == "b"


def test_a_tombstone_still_cancels_a_mark(tmp_path):
    _send_log(tmp_path, [
        {"firm_num": 1, "date": "2026-05-01", "ts": "t1", "note": "a"},
        {"firm_num": 1, "undo": True},
    ])
    assert inv._read_send_log(tmp_path) == {}


def test_a_missing_send_log_is_empty_not_an_error(tmp_path):
    assert inv._read_send_log(tmp_path) == {}


# ============================================================
# 8. the health probe that could not tell one server from another
# ============================================================

@pytest.fixture(scope="module")
def daemon_entry():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "shard35_bridge_daemon", ROOT / "scripts" / "bridge-daemon.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("payload,expected", [
    ({"pid": 1, "version": "1.0.0", "uptime_s": 0, "ok": True}, True),
    ({"status": "ok"}, False),
    ({"ok": True}, False),
    ({"ok": True, "pid": 1}, False),
    ({"ok": True, "pid": "1", "version": "1.0"}, False),
    ({"ok": 1, "pid": 1, "version": "1.0"}, False),
    ([], False),
    ("ok", False),
    (None, False),
])
def test_only_this_daemons_health_shape_is_accepted(daemon_entry, payload,
                                                     expected):
    assert daemon_entry._is_bridge_health_payload(payload) is expected, payload


def test_the_shape_matches_what_the_route_actually_returns():
    """The check is worthless if it describes a payload the app never sends.

    A previous test in this repo asserted on `{"status": "ok"}`, which
    `build_app`'s /health route has never returned.
    """
    source = (ROOT / "scripts/bridge_daemon/app.py").read_text(encoding="utf-8")
    route = source[source.index('@app.get("/health")'):]
    route = route[:route.index('@app.get("/version")')]
    for key in ('"pid"', '"version"', '"ok"'):
        assert key in route, f"/health no longer returns {key}"


# ============================================================
# 9. the operator's name written into the public engine
# ============================================================

def test_the_owner_label_comes_from_the_operator_seam(monkeypatch):
    from scripts.utils import operator_identity
    monkeypatch.setattr(operator_identity, "get_operator",
                        lambda: {"name": "Jane Roe", "slug": "jane-roe"})
    monkeypatch.setattr(ct, "get_operator", lambda: {"name": "Jane Roe"})
    assert ct._owner_label(ct.CEO_OWNER) == "Jane Roe"


def test_an_exec_slug_is_still_titled_from_the_slug():
    assert ct._owner_label("ada-lovelace") == "Ada Lovelace"


def test_no_person_name_is_hardcoded_in_the_contacts_source():
    """The engine repo is public and ships generic defaults.

    `_crm_central_self_dir` in this same file already resolved the operator
    through `operator_identity`; `_owner_label` twenty lines below carried a
    literal instead.
    """
    source = (ROOT / "scripts/bridge_daemon/sources/contacts.py").read_text(
        encoding="utf-8")
    assert "CEO_OWNER_LABEL" not in source
    assert 'get_operator()["name"]' in source
