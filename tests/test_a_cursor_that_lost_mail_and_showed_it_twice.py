"""The Inbox Pulse cursor dropped mail at one boundary and replayed it at another.

Two defects in `scripts/inbox_pulse/daemon.py::_main_loop`, both in how the
poll cursor moves, measured 2026-08-29 against the fake mailbox below:

1. The cursor advanced only after the generator drained. `poll_inbox` raises
   mid-iteration on a dropped connection, the handler swallows it and polls
   again from the unmoved cursor, so everything already appended in that cycle
   was appended a second time. The log came out
   ['MSG-A', 'MSG-B', 'MSG-A', 'MSG-B', 'MSG-C'], and the report pipeline
   counts those rows.

2. The cursor advanced to the newest item's timestamp PLUS one second, while
   the poll filter is strictly greater-than over stamps EWS truncates to whole
   seconds. Anything stamped in the second the cursor names is equal to it,
   never greater, so it was withheld from that poll and from every poll after
   it. MSG-NEXT-SECOND survived four cycles unlogged.

The fake mailbox reproduces both properties of the real one that the defects
depend on: whole-second `datetime_received`, and a strictly-greater window.
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

_WORKSPACE = Path(__file__).resolve().parent.parent
if str(_WORKSPACE) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE))

import scripts.inbox_pulse.daemon as daemon  # noqa: E402

# All stamps are whole seconds, which is the only shape on-prem EWS stores.
T0 = datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc)


class FakeMailbox:
    """Serves `datetime_received > since`, oldest first, like `poll_inbox`.

    `visible_from_poll` delays an item until a later cycle, which is how a
    message that lands between two polls is modelled.
    """

    def __init__(self, items, raise_after=None):
        # items: (item_id, received, visible_from_poll)
        self.items = items
        self.raise_after = raise_after
        self.polls = 0
        self.since_seen: list[datetime] = []

    def poll_inbox(self, since=None):
        self.polls += 1
        self.since_seen.append(since)
        served = 0
        for item_id, received, visible_from in self.items:
            if self.polls < visible_from:
                continue
            if since is not None and not received > since:
                continue
            if self.raise_after is not None and self.polls == 1 and served == self.raise_after:
                raise RuntimeError("simulated EWS transport failure mid-page")
            served += 1
            yield {
                "event_type": "NewMail",
                "timestamp": received.isoformat(),
                "item_id": item_id,
                "parent_folder_id": "INBOX",
                "datetime_received": received.isoformat(),
            }


class _BareItem:
    """Enrichment stub: no sender, no subject, no recipients."""

    sender = None
    subject = None
    to_recipients = None
    cc_recipients = None


def _run(mailbox, start_cursor, max_polls):
    """Drive `_main_loop` for `max_polls` cycles. Returns (logged ids, cursor)."""
    shutdown = threading.Event()
    logged: list[str] = []
    store = {"cursor": start_cursor}
    cycles = {"n": 0}

    def _no_sleep(timeout=None):
        # Every wait() is one cycle boundary; nothing actually sleeps.
        cycles["n"] += 1
        if cycles["n"] >= max_polls:
            shutdown.set()
        return shutdown.is_set()

    shutdown.wait = _no_sleep

    daemon._main_loop(
        shutdown_event=shutdown,
        ews=mailbox,
        write_log_fn=lambda filename, entry: logged.append(entry["message_id"]),
        fetch_item_fn=lambda item_id: _BareItem(),
        get_cursor_fn=lambda: store["cursor"],
        set_cursor_fn=lambda dt: store.__setitem__("cursor", dt),
        rules_engine=None,
        classifier=None,
    )
    return logged, store["cursor"]


def _no_healthcheck_ping(monkeypatch):
    """A clean cycle pings Healthchecks.io; keep the suite off the network."""
    monkeypatch.delenv("STEWARD_HC_EMAIL_TRIAGE", raising=False)


def test_a_poll_that_dies_mid_page_does_not_log_the_same_mail_twice(monkeypatch):
    _no_healthcheck_ping(monkeypatch)

    mailbox = FakeMailbox(
        [
            ("MSG-A", T0, 1),
            ("MSG-B", T0 + timedelta(seconds=30), 1),
            ("MSG-C", T0 + timedelta(seconds=60), 1),
        ],
        raise_after=2,  # dies after MSG-A and MSG-B are already on disk
    )

    logged, _ = _run(mailbox, T0 - timedelta(seconds=1), max_polls=3)

    assert logged, "the fake mailbox served nothing; the test proves nothing"
    assert mailbox.polls >= 2, "the loop never retried after the failure"
    repeated = sorted({m for m in logged if logged.count(m) > 1})
    assert repeated == [], (
        f"mail already written before the failure was written again: {logged}"
    )
    assert logged == ["MSG-A", "MSG-B", "MSG-C"], logged


def test_the_cursor_is_persisted_for_each_item_not_once_per_cycle(monkeypatch):
    """The advance must land before the failure, which is what stops the replay."""
    _no_healthcheck_ping(monkeypatch)

    shutdown = threading.Event()
    persisted: list[datetime] = []
    cycles = {"n": 0}

    def _no_sleep(timeout=None):
        cycles["n"] += 1
        if cycles["n"] >= 1:
            shutdown.set()
        return shutdown.is_set()

    shutdown.wait = _no_sleep

    mailbox = FakeMailbox(
        [("MSG-A", T0, 1), ("MSG-B", T0 + timedelta(seconds=30), 1)],
        raise_after=2,
    )

    daemon._main_loop(
        shutdown_event=shutdown,
        ews=mailbox,
        write_log_fn=lambda filename, entry: None,
        fetch_item_fn=lambda item_id: _BareItem(),
        get_cursor_fn=lambda: T0 - timedelta(seconds=1),
        set_cursor_fn=persisted.append,
        rules_engine=None,
        classifier=None,
    )

    assert len(persisted) == 2, (
        "expected one cursor write per item logged before the failure, "
        f"got {[d.isoformat() for d in persisted]}"
    )
    assert persisted[-1] == T0 + timedelta(seconds=31)


def test_mail_stamped_in_the_second_the_cursor_names_is_still_delivered(monkeypatch):
    _no_healthcheck_ping(monkeypatch)

    mailbox = FakeMailbox(
        [
            ("MSG-A", T0, 1),
            # Lands after the first poll, stamped in the very second the cursor
            # is about to name. This is the message the +1s advance lost forever.
            ("MSG-NEXT-SECOND", T0 + timedelta(seconds=1), 2),
        ]
    )

    logged, cursor = _run(mailbox, T0 - timedelta(seconds=1), max_polls=5)

    assert mailbox.polls >= 2, "only one poll ran; the boundary was never tested"
    assert "MSG-A" in logged
    assert "MSG-NEXT-SECOND" in logged, (
        "mail stamped in the cursor's own second was never delivered, "
        f"across {mailbox.polls} polls: logged={logged} cursor={cursor.isoformat()}"
    )
    assert logged.count("MSG-NEXT-SECOND") == 1, logged


def test_the_poll_window_opens_below_the_persisted_cursor(monkeypatch):
    """The strict `__gt` filter needs a floor under the cursor, not on it."""
    _no_healthcheck_ping(monkeypatch)

    mailbox = FakeMailbox([("MSG-A", T0, 1)])
    _run(mailbox, T0 - timedelta(seconds=1), max_polls=2)

    assert len(mailbox.since_seen) >= 2, "need two polls to see the advanced cursor"
    second_floor = mailbox.since_seen[1]
    # After MSG-A the cursor sits at T0+1s; the window must open strictly below
    # it so an item stamped exactly T0+1s compares greater.
    assert second_floor < T0 + timedelta(seconds=1), second_floor
    assert second_floor > T0, (
        "the floor dropped below the previous item's second, which would "
        f"replay it: {second_floor.isoformat()}"
    )
