"""A card is ACTIVE or TERMINAL, and `apply_status` wrote whatever it was handed.

Found by the 2026-08-24 campaign (shard `scripts-01-p2`, finding 1) on
`scripts/bridge_daemon/sources/action_queue.py`.

The comment above `TERMINAL_STATUSES` states the invariant in its own words:
"A card is either ACTIVE or TERMINAL; there is no third state, and every writer
of `status` has to land in one of these two tuples." Nothing held the writers
to it. `apply_status` ran `card["status"] = status` with no check, and it is
the writer four callers reach for: `scripts/bridge-daemon.py`'s tier sweep,
`scripts/action-queue.py`'s approve and its send-failure path, plus
`approve_card` and `dismiss_card` in the module itself.

One typo, `"approve"` for `"approved"`, put a card into a state the file says
cannot exist, and the call returned `{"ok": True}`. From there the card was:

- invisible to `list_action_queue`, which filters on ACTIVE_STATUSES, so the
  CEO could not see it, approve it, dismiss it or undo it;
- immune to the prune in `append_cards`, which filters on TERMINAL_STATUSES, so
  it sat in `queue.json` at every age under a comment promising bound growth;
- invisible to the dedup check in `append_cards`, which also reads
  ACTIVE_STATUSES, so re-depositing the same dedup key minted a SECOND live
  card. That is the duplicate outbound message the dedup exists to stop.

The three consequences are asserted separately rather than through the refusal
alone. A refusal test proves the call answered no; it does not prove the write
did not happen, and the write is what the queue is made of.
"""
from __future__ import annotations

import pytest

from scripts.bridge_daemon.sources import action_queue as aq


def _deposit(root, title: str = "hello") -> str:
    """One pending card in the queue, returning its id.

    The title is a parameter because `_dedup_key` derives the dedup identity
    from `contact_file`, else `to`, else `title`, and a plain note card has
    only the title. Reusing it is what asks `append_cards` to dedup; a
    dismissed card also holds its key for COOLDOWN_DAYS afterwards.
    """
    got = aq.append_cards(root, [{"action_type": "note", "title": title}])
    assert got["ok"] is True and got["added"] == 1, got
    return got["ids"][0]


@pytest.mark.parametrize("bogus", [
    "approve",        # the typo the audit named: `approved` minus a letter
    "APPROVED",       # right word, wrong case; the tuples are exact
    "",               # a caller that passed through an empty field
    "pending ",       # a trailing space from a hand-edited config or CLI arg
    "done",           # a plausible word that is in neither tuple
])
def test_a_status_outside_both_tuples_is_refused(tmp_path, bogus):
    aid = _deposit(tmp_path)
    got = aq.apply_status(tmp_path, aid, bogus)
    assert got["ok"] is False, (
        f"{bogus!r} was written into status, and the file says there is no "
        f"third state: {got}")
    assert "status" in got.get("error", ""), got


def test_a_refused_status_leaves_the_card_where_it_was(tmp_path):
    """The refusal must not be the only thing that happened.

    A guard that answers `ok: False` AFTER writing is worse than none: the
    caller believes nothing changed. The check runs before the lock and before
    the load, so there is no partial write to find.
    """
    aid = _deposit(tmp_path)
    aq.apply_status(tmp_path, aid, "approve")
    listing = aq.list_action_queue(tmp_path)
    assert listing["total"] == 1, (
        f"the card vanished from the queue the CEO drives: {listing}")
    assert listing["items"][0]["id"] == aid
    assert listing["items"][0]["status"] == "pending", listing["items"][0]


def test_a_refused_status_cannot_mint_a_second_live_card(tmp_path):
    """The duplicate-mail end of the same defect.

    `append_cards` dedups by asking whether any card under the key is in
    ACTIVE_STATUSES. A card parked in a third state answers no to that
    question, so the same dedup key deposited again became a second live card.
    """
    aid = _deposit(tmp_path)
    aq.apply_status(tmp_path, aid, "approve")
    again = aq.append_cards(tmp_path, [{"action_type": "note",
                                        "title": "hello"}])
    assert again["added"] == 0, (
        f"the dedup key was live and a second card was created anyway: {again}")
    assert again["skipped"] == 1, again


def test_every_real_status_still_writes(tmp_path):
    """The anchor. Without it the guard passes by refusing everything.

    Every member of both tuples is driven, because a guard written against a
    hand-copied subset is the one that starts refusing a status a later commit
    adds to the tuple. `sending` is reached through `claim_card_for_send` in
    production rather than through here, and it is still a legal argument.
    """
    for status in aq.ACTIVE_STATUSES + aq.TERMINAL_STATUSES:
        # A key per iteration: a dismissed card holds its key for COOLDOWN_DAYS
        # and an active one holds it outright, so reusing one measures the
        # dedup rather than the status write.
        aid = _deposit(tmp_path, title=f"card for {status}")
        got = aq.apply_status(tmp_path, aid, status)
        assert got["ok"] is True, (status, got)
        assert got["card"]["status"] == status, (status, got)


def test_the_guard_reads_the_tuples_rather_than_a_copy_of_them(tmp_path):
    """A hand-copied allowlist is the thing that falls behind.

    `applied` is the case that already happened once: it was in neither tuple
    until 2026-08-28 while the daemon's tier sweep stamped it on every
    auto-applied notify card. If the guard were written against a literal list
    instead of the tuples, the next status added to a tuple would be refused by
    the writer that is supposed to set it.
    """
    aid = _deposit(tmp_path)
    aq.ACTIVE_STATUSES  # noqa: B018  named so a rename fails here too
    assert "applied" in aq.TERMINAL_STATUSES
    assert aq.apply_status(tmp_path, aid, "applied",
                           event="auto_apply")["ok"] is True
