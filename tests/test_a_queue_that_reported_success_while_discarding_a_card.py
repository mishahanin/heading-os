"""A second, hand-maintained action_type allowlist that silently threw cards away.

``scripts/bridge_daemon/sources/action_queue.py`` carried its own tuple::

    ACTION_TYPES = ("email_send", "note", "pipeline_update", "alert")

``config/tool-risk.json`` is the single classification input for an
``action_type`` (`.claude/rules/tiered-risk.md`), and ``tool_risk.py`` is the one
module that reads it. The tuple above was a SECOND copy of that taxonomy, and it
had already fallen behind. MEASURED 2026-09-02 against the shipped ledger, four
classified types were absent from it:

    crm_write, knowledge_write, task_create, telegram_send

``append_cards`` skipped every card carrying one of those and then returned
``{"ok": True, "added": 0, "skipped": 1}``. Nothing in that shape separates
"your card was deduplicated against a live one", which is the documented and
correct behaviour, from "your card was discarded because I do not recognise its
type", which is a defect. Every caller reads exactly those keys:
``scripts/cold-sweep.py`` prints the count under the label
``(dedup/cooldown applied by append_cards)``, ``scripts/dead-letter.py`` reports
"likely deduped against an existing card", and ``scripts/utils/alert.py``
resolves ``ok and added`` to a bare False. All three would have described a
thrown-away card as a deduplicated one.

``telegram_send`` is the sharpest case. It is in the ledger's ``send_capable``
set, floors at ``gated``, is shown in the UI, and
``scripts/action-queue-execute.py`` carries an explicit 501 refusal branch for
it, described in `.claude/rules/tiered-risk.md` as "reserved-and-gated". It
could not be deposited at all: the tuple dropped it before a tier was ever
stamped.

What this file holds:

  - every type the ledger classifies is accepted, resolved through
    ``tool_risk.classified_types()`` rather than restated here, so the test
    cannot drift from the ledger the way the tuple did;
  - a type the ledger does NOT classify is refused loudly and atomically, and
    the refusal names the type;
  - the allowlist is DERIVED: a type added to a fixture ledger is accepted with
    no source edit, and a type removed from one is refused;
  - the lethal-trifecta floor is untouched by any of it - a ``send_capable``
    type is stamped ``gated`` on deposit even from a ledger that marks it
    autonomous, and an unclassified type fails safe by not entering the queue.

Run: .venv/bin/python -m pytest tests/test_a_queue_that_reported_success_while_discarding_a_card.py
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.bridge_daemon.sources import action_queue as aq
from scripts.utils import tool_risk


@pytest.fixture
def root(tmp_path):
    (tmp_path / "outputs" / "operations" / "action-queue").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Point tool_risk at a temp ledger, and reset its cache around the test.

    Cleared to None rather than re-read: ``get_workspace_root`` is still
    monkeypatched while this fixture unwinds, so a ``load(force=True)`` here
    would repopulate the cache from the fixture and leak it into later modules.
    """
    def _make(data: dict):
        cfg = tmp_path / "config"
        cfg.mkdir(exist_ok=True)
        (cfg / "tool-risk.json").write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(tool_risk, "get_workspace_root", lambda: tmp_path)
        tool_risk.load(force=True)
        return tmp_path
    yield _make
    tool_risk._CACHE = None


def _card(action_type: str, title: str) -> dict:
    return {"action_type": action_type, "title": title, "reasoning": "fixture"}


def _stored(root: Path) -> list[dict]:
    path = root / aq.QUEUE_FILE
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["actions"]


# ============================================================
# The dropped types
# ============================================================

def test_every_type_the_ledger_classifies_can_be_deposited(root):
    """The defect, stated as the whole set rather than the four names.

    Asking the ledger keeps this honest as the ledger grows: a type registered
    tomorrow is covered by this assertion the day it lands, which is exactly
    what a hand-maintained second copy cannot promise.
    """
    known = sorted(tool_risk.classified_types())
    assert known, "the shipped ledger classifies nothing; the fixture is wrong"

    result = aq.append_cards(root, [_card(t, f"card for {t}") for t in known])

    assert result["ok"] is True, result
    assert result["added"] == len(known), (
        f"{len(known) - result['added']} classified type(s) were discarded: "
        f"{sorted(set(known) - {c['action_type'] for c in _stored(root)})}"
    )
    assert {c["action_type"] for c in _stored(root)} == set(known)


def test_a_send_capable_type_reserved_but_unwired_can_still_be_deposited(root):
    """``telegram_send`` names itself, because it is the one that hurt.

    Reserved-and-gated with a 501 executor branch waiting for it, and not
    depositable at all.
    """
    result = aq.append_cards(root, [_card("telegram_send", "reserved send")])

    assert result["ok"] is True and result["added"] == 1, result
    assert _stored(root)[0]["tier"] == "gated"


# ============================================================
# A skipped card must not read as success
# ============================================================

def test_an_unclassified_type_is_refused_loudly_not_counted_as_skipped(root):
    """The success-shaped discard. ``ok`` is False and the type is named."""
    result = aq.append_cards(root, [_card("emial_send", "typo'd type")])

    assert result["ok"] is False, (
        "an unrecognised action_type returned a success-shaped result; the "
        "caller cannot tell a discard from a no-op"
    )
    assert "emial_send" in json.dumps(result), result
    assert result["added"] == 0
    assert _stored(root) == []


def test_a_refusal_names_which_card_and_why(root):
    """Naming the type is not enough when a batch carries several."""
    result = aq.append_cards(root, [
        _card("note", "fine"),
        _card("not_a_type", "bad"),
        _card("also_not_a_type", "worse"),
    ])

    rejected = result.get("rejected")
    assert isinstance(rejected, list) and len(rejected) == 2, result
    assert {r["action_type"] for r in rejected} == {"not_a_type", "also_not_a_type"}
    assert {r["index"] for r in rejected} == {1, 2}
    assert all(r.get("reason") for r in rejected), rejected


def test_a_refused_batch_writes_nothing_at_all(root):
    """Refusal is atomic. A partial write reported as a failure is its own trap."""
    result = aq.append_cards(root, [_card("note", "good"), _card("nonsense", "bad")])

    assert result["ok"] is False
    assert _stored(root) == [], (
        "the good card in a refused batch was written while the call reported "
        "failure; the caller now cannot tell what state the queue is in"
    )


def test_a_non_dict_card_is_refused_rather_than_silently_counted(root):
    """The other arm of the old ``skipped += 1``, same dishonesty."""
    result = aq.append_cards(root, ["not a card"])

    assert result["ok"] is False, result
    assert result.get("rejected"), result
    assert _stored(root) == []


def test_skipped_now_means_dedup_and_only_dedup(root):
    """The count callers already print keeps its documented meaning.

    ``scripts/cold-sweep.py`` labels this number "(dedup/cooldown applied by
    append_cards)". That label was false whenever a type was dropped; it is now
    the only thing the number can mean.
    """
    first = aq.append_cards(root, [{"action_type": "note", "title": "Acme",
                                    "contact_file": "crm/contacts/acme.md"}])
    assert first["added"] == 1 and first["skipped"] == 0

    second = aq.append_cards(root, [{"action_type": "note", "title": "Acme",
                                     "contact_file": "crm/contacts/acme.md"}])
    assert second["ok"] is True
    assert second["added"] == 0 and second["skipped"] == 1
    assert not second.get("rejected"), second


def test_a_list_that_is_not_a_list_still_fails_the_old_way(root):
    """Anchor on the pre-existing contract this change must not disturb."""
    result = aq.append_cards(root, {"action_type": "note"})
    assert result == {"ok": False, "error": "cards must be a list"}


def test_an_empty_batch_is_a_no_op_success(root):
    """Nothing to do is not a refusal."""
    result = aq.append_cards(root, [])
    assert result["ok"] is True and result["added"] == 0 and not result.get("rejected")


# ============================================================
# Derived, not hand-maintained
# ============================================================

def test_a_type_added_to_the_ledger_is_accepted_with_no_source_edit(root, ledger):
    """The property the fix exists for.

    A hand-maintained list falls behind the moment the ledger moves. This is
    the assertion the old tuple could not pass at any point in its life.
    """
    ledger({
        "version": 1,
        "tiers": {"a_type_invented_by_this_test": {"tier": "notify", "reason": "fixture"}},
        "send_capable": [],
    })

    result = aq.append_cards(root, [_card("a_type_invented_by_this_test", "new")])

    assert result["ok"] is True and result["added"] == 1, result
    assert _stored(root)[0]["tier"] == "notify"


def test_a_type_absent_from_the_ledger_is_refused_even_though_it_ships_today(root, ledger):
    """The other direction: the allowlist follows the ledger down as well as up.

    ``pipeline_update`` is classified in the shipped ledger, so a source-held
    tuple would accept it here. Read from a ledger that omits it, it is not a
    known type and does not enter the queue.
    """
    ledger({
        "version": 1,
        "tiers": {"note": {"tier": "autonomous", "reason": "fixture"}},
        "send_capable": [],
    })

    result = aq.append_cards(root, [_card("pipeline_update", "stale type")])

    assert result["ok"] is False, (
        "the accepted set did not narrow with the ledger, so it is still being "
        "read from a copy somewhere"
    )
    assert _stored(root) == []


def test_a_send_capable_type_with_no_tiers_entry_is_still_accepted(root, ledger):
    """``send_capable`` alone is a classification.

    ``telegram_send`` ships exactly this way - listed as send-capable, absent
    from ``tiers`` - so a derivation that read only the ``tiers`` keys would
    reproduce the original defect for the very type that exposed it.
    """
    ledger({
        "version": 1,
        "tiers": {"note": {"tier": "autonomous", "reason": "fixture"}},
        "send_capable": ["some_future_send"],
    })

    result = aq.append_cards(root, [_card("some_future_send", "future")])

    assert result["ok"] is True and result["added"] == 1, result
    assert _stored(root)[0]["tier"] == "gated"


# ============================================================
# The lethal-trifecta floor is untouched
# ============================================================

def test_a_tampered_ledger_cannot_deposit_a_send_below_gated(root, ledger):
    """Widening the accepted set must not widen the send gate.

    The ledger is data and the send-gate is code (`.claude/rules/tiered-risk.md`).
    A ledger that marks a send-capable type autonomous still stamps ``gated``.
    """
    ledger({
        "version": 1,
        "tiers": {"email_send": {"tier": "autonomous", "reason": "tampered"},
                  "telegram_send": {"tier": "autonomous", "reason": "tampered"}},
        "send_capable": ["email_send", "telegram_send"],
    })

    result = aq.append_cards(root, [_card("email_send", "e"), _card("telegram_send", "t")])

    assert result["added"] == 2, result
    assert {c["tier"] for c in _stored(root)} == {"gated"}


def test_an_unclassified_type_fails_safe_by_not_entering_the_queue(root, ledger):
    """Fail-safe means gated, and a card that is never deposited is stronger.

    ``tier_for`` answers ``gated`` for a type it does not know, so a deposit of
    one would at worst sit behind the human gate. Refusing the deposit outright
    is the stricter of the two safe answers, and it is the one taken here.
    """
    ledger({"version": 1, "tiers": {}, "send_capable": []})

    assert tool_risk.tier_for("anything_at_all") == "gated"

    result = aq.append_cards(root, [_card("anything_at_all", "unknown")])

    assert result["ok"] is False
    assert _stored(root) == []


def test_a_corrupt_ledger_refuses_every_deposit_rather_than_admitting_everything(root, ledger, tmp_path):
    """The direction a broken config must fail in.

    ``tool_risk.load`` answers an empty dict for a malformed ledger, so the
    derived set is empty and every deposit is refused. The opposite - treating
    "I could not read the classification" as "everything is classified" - would
    put an unclassified card in the queue at the moment the tier data is least
    trustworthy.
    """
    ledger({"version": 1, "tiers": {}, "send_capable": []})
    (tmp_path / "config" / "tool-risk.json").write_text("{ this is not json",
                                                        encoding="utf-8")
    tool_risk.load(force=True)

    assert tool_risk.classified_types() == frozenset()

    result = aq.append_cards(root, [_card("email_send", "e")])

    assert result["ok"] is False
    assert _stored(root) == []


# ============================================================
# The last mile: the CLI must not print the refusal as a success
# ============================================================
#
# `append_cards` refuses atomically and honestly, and `cmd_deposit` then read
# only `added` and `skipped` from that answer. So a total refusal printed a
# GREEN "deposited added=0 skipped=0" and returned 0. MEASURED 2026-09-02 by
# running it: the operator was told the deposit succeeded with nothing in it,
# while the whole batch had been thrown away. The `logger.error` inside
# `append_cards` did reach stderr through `logging.lastResort`, but a warning
# beside a contradicting green line and a zero exit is not a report, and a
# calling script reads the exit code and nothing else.

def _run_deposit(tmp_path, monkeypatch, cards):
    """Drive `cmd_deposit` exactly as `main` does, on a throwaway data root."""
    import importlib.util
    import types

    cli_path = Path(__file__).resolve().parent.parent / "scripts" / "action-queue.py"
    spec = importlib.util.spec_from_file_location("action_queue_cli", cli_path)
    cli = importlib.util.module_from_spec(spec)
    sys.modules["action_queue_cli"] = cli
    spec.loader.exec_module(cli)

    cards_file = tmp_path / "cards.json"
    cards_file.write_text(json.dumps(cards), encoding="utf-8")
    data_root = tmp_path / "data"
    (data_root / "outputs" / "operations" / "action-queue").mkdir(parents=True)

    args = types.SimpleNamespace(file=str(cards_file))
    code = cli.cmd_deposit(tmp_path, data_root, args)
    return code, _stored(data_root)


def test_the_cli_exits_non_zero_when_the_whole_batch_was_refused(tmp_path, monkeypatch, capsys):
    code, stored = _run_deposit(tmp_path, monkeypatch,
                                [{"action_type": "emial_send", "title": "typo",
                                  "reasoning": "fixture"}])
    out = capsys.readouterr()

    assert code == 1, "a refused deposit must not exit 0"
    assert stored == []
    assert "deposited" not in out.out, \
        "the green success line printed over a refusal"
    assert "emial_send" in out.err, \
        "the refusal must name the card that caused it"


def test_the_cli_still_reports_a_real_deposit_as_success(tmp_path, monkeypatch, capsys):
    """The negative case for the branch above: it must not refuse everything."""
    code, stored = _run_deposit(tmp_path, monkeypatch,
                                [_card("note", "a real note")])
    out = capsys.readouterr()

    assert code == 0, out.err
    assert len(stored) == 1
    assert "deposited" in out.out
