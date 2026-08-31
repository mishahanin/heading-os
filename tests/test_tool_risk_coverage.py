"""Regression: tool-risk ledger must classify crm_write, knowledge_write, task_create (F-L10).

Also pins the POSITIVE direction of the send gate against the real on-disk
ledger: every ``send_capable`` name resolves ``gated`` through the resolver, a
name shaped like an outbound sender is registered in ``send_capable``, and the
``send_capable`` layer alone carries the floor without help from the redundant
``tiers`` entry beside it.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "config" / "tool-risk.json"

sys.path.insert(0, str(ROOT))

from scripts.utils import tool_risk  # noqa: E402


def _load_ledger():
    return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))


# Name fragments that mark an action_type as reaching a third party. Matched as
# whole `_`-delimited tokens, never as substrings, so `recall_note` does not
# match on "call". The set is deliberately conservative: a false positive here
# would push a future editor to weaken the test rather than register the type.
#
# SCOPE (per .claude/rules/scope-claims.md): this catches a sender that follows
# the ledger's own naming convention (`email_send`, `telegram_send`). It cannot
# catch one named without a recognised token, and it is a second line of
# defence, not the control. The control is the code-level floor in
# `tool_risk.tier_for`, and the unclassified-type fail-safe behind that.
SENDER_TOKENS = frozenset({
    "send", "sends", "sms", "email", "mail", "post", "publish", "reply",
    "forward", "dm", "message", "tweet", "telegram", "slack", "whatsapp",
})


def _looks_like_a_sender(action_type):
    return bool(SENDER_TOKENS & set(action_type.split("_")))


def test_crm_write_classified():
    ledger = _load_ledger()
    assert ledger["tiers"].get("crm_write", {}).get("tier") == "notify", (
        "crm_write must be classified tier=notify in config/tool-risk.json (F-L10)"
    )


def test_knowledge_write_classified():
    ledger = _load_ledger()
    assert ledger["tiers"].get("knowledge_write", {}).get("tier") == "notify", (
        "knowledge_write must be classified tier=notify in config/tool-risk.json (F-L10)"
    )


def test_task_create_classified():
    ledger = _load_ledger()
    assert ledger["tiers"].get("task_create", {}).get("tier") == "notify", (
        "task_create must be classified tier=notify in config/tool-risk.json (F-L10)"
    )


def test_new_entries_not_in_send_capable():
    """crm_write, knowledge_write, task_create are reversible edits, not outbound sends."""
    send_capable = set(_load_ledger().get("send_capable", []))
    for action_type in ("crm_write", "knowledge_write", "task_create"):
        assert action_type not in send_capable, (
            f"{action_type} is not an outbound send; it must not be in send_capable"
        )


def test_send_gate_invariant_intact():
    """email_send and telegram_send must remain in send_capable (invariant guard)."""
    send_capable = set(_load_ledger().get("send_capable", []))
    assert "email_send" in send_capable, "email_send must remain in send_capable"
    assert "telegram_send" in send_capable, "telegram_send must remain in send_capable"


# ============================================================
# The send gate, positive direction, against the REAL on-disk ledger
# ============================================================

def test_every_send_capable_type_resolves_gated_on_the_real_ledger():
    """The shipped ledger, read through the resolver, floors every sender.

    The test above asserts MEMBERSHIP by reading the JSON. This one asserts the
    RESOLUTION the executor actually consults, on the real file rather than a
    temp one, so a ledger and a resolver that each look right alone cannot
    disagree in production without a red test.
    """
    send_capable = list(_load_ledger().get("send_capable", []))
    # Empty-corpus guard: a renamed key yields [] and would pass the loop
    # vacuously, reporting a gate over nothing at all.
    assert len(send_capable) >= 2, (
        f"send_capable must list at least the two known senders; got {send_capable}"
    )
    tool_risk.load(force=True)
    try:
        for action_type in send_capable:
            assert tool_risk.tier_for(action_type) == "gated", (
                f"{action_type} is send_capable but resolves "
                f"{tool_risk.tier_for(action_type)!r}, not 'gated'"
            )
    finally:
        tool_risk._CACHE = None


def test_a_sender_shaped_action_type_is_registered_send_capable():
    """A new outbound sender may not be classified by its `tiers` entry alone.

    This is the gap the other tests leave open. Adding `slack_send` with
    `tier: autonomous` and forgetting `send_capable` resolved `autonomous` with
    the whole suite green, because nothing enumerated senders independently of
    the set being audited.
    """
    ledger = _load_ledger()
    tiers = ledger.get("tiers", {})
    send_capable = set(ledger.get("send_capable", []))
    # Empty-corpus guard, same reason as above.
    assert len(tiers) >= 5, f"tiers looks truncated: {sorted(tiers)}"

    unregistered = sorted(
        name for name in set(tiers) | send_capable
        if _looks_like_a_sender(name) and name not in send_capable
    )
    assert not unregistered, (
        f"action_type(s) named like an outbound sender but absent from "
        f"send_capable: {unregistered}. Add them to send_capable in "
        f"config/tool-risk.json so they floor at gated, per "
        f".claude/rules/tiered-risk.md. A `tiers` entry is not enough: it is "
        f"overridable data, and the send gate must be code."
    )


def test_the_sender_detector_still_matches_something():
    """A detector that matches nothing passes everything.

    Pins `_looks_like_a_sender` against decay: if the token set or the split
    ever stops recognising the ledger's own senders, the test above becomes a
    no-op that reports a clean pass over an unchecked ledger.
    """
    assert _looks_like_a_sender("email_send")
    assert _looks_like_a_sender("telegram_send")
    assert _looks_like_a_sender("slack_send")
    # And it must not fire on the reversible edits, or a future editor will
    # weaken it rather than register a type.
    assert not _looks_like_a_sender("crm_write")
    assert not _looks_like_a_sender("pipeline_update")
    assert not _looks_like_a_sender("recall_note")

    detected = [n for n in _load_ledger().get("tiers", {}) if _looks_like_a_sender(n)]
    assert detected, (
        "the detector matches no action_type in the live ledger; it has "
        "decayed into a guard that cannot fail"
    )


def _resolve_with_ledger(ledger, tmp_path, monkeypatch):
    """Resolve against a modified copy of the real ledger, then drop the cache."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "tool-risk.json").write_text(
        json.dumps(ledger), encoding="utf-8"
    )
    monkeypatch.setattr(tool_risk, "get_workspace_root", lambda: tmp_path)
    tool_risk.load(force=True)
    return {name: tool_risk.tier_for(name) for name in ledger["send_capable"]}


def test_send_gate_holds_without_the_redundant_tiers_entry(tmp_path, monkeypatch):
    """`send_capable` alone carries the floor; the `tiers: gated` row is spare.

    `email_send` is protected twice over on disk, so a test reading only the
    resolved tier cannot tell which layer did the work. Two edits to a copy of
    the REAL ledger separate them.

    Removing the `tiers` row is the weaker half: the unclassified-type
    fail-safe also answers `gated`, so it shows the floor survives, not which
    layer held it. TAMPERING the row to `autonomous` is the half that binds,
    because `send_capable` membership is then the only thing that can produce
    `gated` at all.
    """
    removed = _load_ledger()
    assert removed["tiers"].pop("email_send", None) is not None, (
        "expected a redundant tiers entry for email_send to remove"
    )
    assert len(removed["send_capable"]) >= 2
    assert set(_resolve_with_ledger(removed, tmp_path, monkeypatch).values()) == {"gated"}
    tool_risk._CACHE = None

    tampered = _load_ledger()
    tampered["tiers"]["email_send"] = {"tier": "autonomous", "reason": "tampered"}
    for name in tampered["send_capable"]:
        tampered["tiers"].setdefault(name, {"tier": "autonomous", "reason": "tampered"})
    try:
        resolved = _resolve_with_ledger(tampered, tmp_path, monkeypatch)
        assert set(resolved.values()) == {"gated"}, (
            f"a tampered ledger lowered a real send_capable type below gated: "
            f"{resolved}"
        )
    finally:
        # Clear rather than reload: get_workspace_root is still patched here,
        # so a force-load would leak the temp ledger into later suites.
        tool_risk._CACHE = None
