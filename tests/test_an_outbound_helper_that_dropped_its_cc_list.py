"""An outbound helper that accepted a cc list and never delivered it.

`_send_threaded_core` declares `cc` and `bcc` and reads neither. A direct
caller passing `cc=["q@example.invalid"]` got `"status": "sent"` with the CC
absent: silent loss on an irreversible outbound action, which this file's own
comments call the worst shape it can produce. `main` refuses the pair one layer
up, so the CLI was safe; the trap sat at the layer that actually drops them,
in a module whose lazy-import design invites other importers.

MEASURED 2026-08-30 with ast, over the current source:

    declared params                -> {'cc', 'bcc'}
    names READ in the body         -> set()

Two mis-stamped stages travel with it, same function, same shape. A forward
with no recipients and an unknown mode both returned `"stage": "attachments"`,
which `_STAGE_GUIDANCE` renders as "Fix the path and run it again." No path is
involved in either failure, so the stage table -- written so a caller can "tell
the operator something true about the draft and the wire" -- told them
something false.

NOTHING HERE SENDS. The stub original raises out of every create_* method, which
is the step BEFORE anything is persisted or transmitted: no draft object is
built, `save()` never runs, and `send()` never runs. Note the raise does not
propagate (`_send_threaded_core` catches it and returns `stage: "save_draft"`),
so a case that walks past the refusal shows up as a wrong STAGE, not as an
error. Every test below therefore asserts the stage, never merely the status.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "send_email_cc_probe", ROOT / "scripts" / "send-email.py")
se = importlib.util.module_from_spec(_spec)
sys.modules["send_email_cc_probe"] = se
_spec.loader.exec_module(se)


class _Mailbox:
    def __init__(self, address):
        self.email_address = address


class _Original:
    """A matched message that refuses to be turned into anything sendable."""

    subject = "Q3 numbers"
    author = _Mailbox("felix.leiter@example.invalid")
    sender = _Mailbox("moneypenny@example.invalid")

    def _refuse(self, *args, **kwargs):
        raise AssertionError(
            "the transport was reached; this test must never build a message")

    create_reply = _refuse
    create_reply_all = _refuse
    create_forward = _refuse


def _core(**kwargs):
    """Call the helper with the signature pinned and no signature file read."""
    kwargs.setdefault("account", None)
    kwargs.setdefault("original", _Original())
    kwargs.setdefault("body", "<p>x</p>")
    kwargs.setdefault("signature", "SIG")
    return se._send_threaded_core(**kwargs)


# --- cc / bcc are refused, not swallowed ---

@pytest.mark.parametrize("field", ["cc", "bcc"])
def test_a_threaded_send_refuses_a_recipient_list_it_cannot_deliver(field):
    result = _core(mode="reply", **{field: ["q@example.invalid"]})

    assert result["status"] == "failed", (
        f"{field} was accepted and the result claims success, while "
        f"create_reply is never given the address")
    assert result["stage"] == "validation"
    assert field in result["error"]


def test_the_refusal_happens_before_anything_is_built_or_saved():
    """The stub raises on create_*; reaching it would fail the call, not pass it."""
    result = _core(mode="forward", to=["m@example.invalid"],
                   cc=["q@example.invalid"])
    assert result["status"] == "failed"
    assert result["stage"] == "validation"
    assert "nothing was saved and nothing was sent" in result["error"].lower()


def test_an_empty_cc_list_is_not_treated_as_a_cc():
    """The other direction: `cc=[]` and `cc=None` must not trip the refusal."""
    for empty in (None, [], ()):
        result = _core(mode="bogus-mode", cc=empty, bcc=empty)
        assert result["stage"] != "validation" or "cc/bcc" not in result["error"], (
            f"cc={empty!r} was read as a populated recipient list")


# --- the mis-stamped stages ---

def test_a_forward_with_no_recipients_is_not_stamped_as_an_attachment_failure():
    result = _core(mode="forward")

    assert result["status"] == "failed"
    assert result["stage"] == "validation", (
        "stamped 'attachments', so _STAGE_GUIDANCE tells the operator to fix a "
        "path on a failure that involved no path")
    assert "requires --to" in result["error"]


def test_an_unknown_mode_is_not_stamped_as_an_attachment_failure():
    result = _core(mode="telepathy")

    assert result["status"] == "failed"
    assert result["stage"] == "validation"
    assert "unknown mode" in result["error"]


def test_the_guidance_for_a_refused_request_does_not_say_fix_the_path():
    """What the operator actually reads, resolved the way `main` resolves it."""
    result = _core(mode="forward")
    guidance = se._STAGE_GUIDANCE.get(result["stage"], se._STAGE_GUIDANCE_UNKNOWN)

    assert "path" not in guidance.lower(), guidance
    assert "nothing was saved and nothing was sent" in guidance.lower()


def test_a_genuine_attachment_failure_still_reads_attachments():
    """The other direction: the attachments stage must keep its own meaning."""
    result = _core(mode="reply", attach=["/nonexistent/does-not-exist.pdf"])

    assert result["status"] == "failed"
    assert result["stage"] == "attachments"
    assert "path" in se._STAGE_GUIDANCE["attachments"].lower()


# Every function in send-email.py that stamps a "stage" key, classified once.
# The walk below derives the stamping set from the AST and fails when it does not
# match these two registries, so a THIRD stamper cannot be added without someone
# deciding which of the two it is.
#
# `_GUIDANCE_CONSUMERS`: the result is handed to `_STAGE_GUIDANCE.get(...)` (at
# send-email.py:1010 via `send_email`, and at :1347 in main's threaded branch), so
# every stage it can stamp must have a guidance entry or the operator is told the
# stage was "unrecorded" when it was recorded.
#
# Until 2026-09-01 only `_send_threaded_core` was walked. MEASURED that day:
# adding an unguided `"stage": "quota"` return to `_send_email_core`, the other
# half of the same table's audience, left this file green at 9 passed.
_GUIDANCE_CONSUMERS = {"_send_threaded_core", "_send_email_core"}

# `_OWN_VOCABULARY`: stampers whose result never reaches that lookup. Each entry
# states why, because "it does not need guidance" is exactly the claim that rots.
_OWN_VOCABULARY = {
    "send_batch": (
        "main's batch branch (send-email.py:1301-1311) prints only r['error'] "
        "per failed message and never calls _STAGE_GUIDANCE.get, so the stage is "
        "an internal record. The irreversibility warning still reaches the "
        "operator: _send_email_core embeds _UNSURE_NOTE in the error string "
        "itself before returning, so the batch report carries it."
    ),
}

# The stage vocabulary the two guidance consumers can stamp, MEASURED 2026-09-01.
# A floor, not a description: a new stage has to be added here by hand, and that
# edit is the moment its guidance entry gets written.
_KNOWN_CONSUMER_STAGES = {"attach", "attachments", "save_draft", "send", "sent",
                          "validation"}


def _stage_stamps():
    """Every ``{"stage": ...}`` site in send-email.py, grouped by enclosing function.

    Returns ``(literals_by_function, opaque)``. ``opaque`` collects any stage
    value that is not a plain constant. The previous walk SKIPPED those in
    silence, so moving a literal behind a name removed it from the audit without
    removing it from the wire.
    """
    src = (ROOT / "scripts" / "send-email.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    def enclosing(node):
        while node is not None:
            if isinstance(node, ast.FunctionDef):
                return node.name
            node = parent.get(node)
        return "<module>"

    literals: dict[str, set] = {}
    opaque: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if not (isinstance(key, ast.Constant) and key.value == "stage"):
                continue
            where = enclosing(node)
            if isinstance(value, ast.Constant):
                literals.setdefault(where, set()).add(value.value)
            else:
                opaque.append(f"{where} (line {key.lineno}): "
                              f"{type(value).__name__}")
    return literals, opaque


def test_no_stage_value_is_hidden_behind_a_non_literal():
    """A computed stage is invisible to this audit, so it is refused outright."""
    _, opaque = _stage_stamps()
    assert opaque == [], (
        "these return a stage this file cannot read, so their coverage is "
        f"unmeasured: {opaque}. Stamp a string literal, or the audit below "
        "passes without having looked at them.")


def test_every_function_that_stamps_a_stage_is_classified():
    """A new stamper fails until someone decides whether it needs guidance."""
    literals, _ = _stage_stamps()
    found = set(literals)
    classified = _GUIDANCE_CONSUMERS | set(_OWN_VOCABULARY)
    assert found == classified, (
        f"unclassified stampers (add to _GUIDANCE_CONSUMERS or _OWN_VOCABULARY "
        f"with a reason): {sorted(found - classified)}; "
        f"registered but no longer stamping anything: {sorted(classified - found)}")


def test_every_stage_a_guidance_consumer_can_return_has_its_own_guidance():
    """A stage with no table entry silently falls to the unknown-state text."""
    literals, _ = _stage_stamps()
    returned = set().union(*(literals.get(name, set()) for name in _GUIDANCE_CONSUMERS))

    assert returned == _KNOWN_CONSUMER_STAGES, (
        f"the stage vocabulary moved: new {sorted(returned - _KNOWN_CONSUMER_STAGES)}, "
        f"gone {sorted(_KNOWN_CONSUMER_STAGES - returned)}. Update "
        "_KNOWN_CONSUMER_STAGES and write the guidance entry in the same change.")
    assert returned - {"sent"} <= set(se._STAGE_GUIDANCE), (
        f"stages with no guidance entry: "
        f"{sorted(returned - {'sent'} - set(se._STAGE_GUIDANCE))}")


def test_no_guidance_entry_guards_a_stage_nothing_can_return():
    """The other direction: a table entry no code path can reach is dead text.

    Without this, a typo'd or renamed key sits in _STAGE_GUIDANCE forever while
    the stage it was meant to describe silently resolves to
    _STAGE_GUIDANCE_UNKNOWN. Measured 2026-09-01: an invented key left the file
    green at 9 passed.
    """
    literals, _ = _stage_stamps()
    returned = set().union(*(literals.get(name, set()) for name in _GUIDANCE_CONSUMERS))
    orphans = set(se._STAGE_GUIDANCE) - returned
    assert orphans == set(), (
        f"guidance entries no stage can reach: {sorted(orphans)}")
