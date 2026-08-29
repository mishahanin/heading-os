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

NOTHING HERE SENDS. The stub original raises out of every create_* method, so
any case that reaches the transport fails loudly rather than quietly putting
mail on the wire.
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


def test_every_stage_a_threaded_send_can_return_has_its_own_guidance():
    """A stage with no table entry silently falls to the unknown-state text."""
    returned = set()
    tree = ast.parse((ROOT / "scripts" / "send-email.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_send_threaded_core")
    for node in ast.walk(fn):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if (isinstance(key, ast.Constant) and key.value == "stage"
                    and isinstance(value, ast.Constant)):
                returned.add(value.value)

    assert returned, "found no stage literals at all; the AST walk is not binding"
    assert returned - {"sent"} <= set(se._STAGE_GUIDANCE), (
        f"stages with no guidance entry: "
        f"{sorted(returned - {'sent'} - set(se._STAGE_GUIDANCE))}")
