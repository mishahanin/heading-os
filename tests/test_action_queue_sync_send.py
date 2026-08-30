#!/usr/bin/env python3
"""Tests for the terminal-native synchronous Action Queue (Step 1 + Step 2).

Standalone-runnable, plain asserts. Covers:
  - send_card: sent / failed / non-gated-refused / telegram-501 / empty-body
    (with send-email.py stubbed via subprocess.run)
  - the Success Signal: daemon-free list/show/approve drives the in-process
    helpers on a temp queue under a temp DATA root; approve transitions the card
    synchronously to sent (or send_failed) in the same call; the send-gate
    invariant holds (email_send -> gated; a non-gated type is refused).
"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import tool_risk

_spec_x = importlib.util.spec_from_file_location("aqx", ROOT / "scripts" / "action-queue-execute.py")
aqx = importlib.util.module_from_spec(_spec_x)
_spec_x.loader.exec_module(aqx)


def _check(name, cond):
    """Fail the test when `cond` is false. The name is the failure message.

    This used to `return bool(cond)`. Every caller accumulated the result into
    an `ok` flag and closed with `return ok`, which is how these files ran
    before they were renamed `test_*.py`: as standalone scripts, under a
    `main()` that read the return value.

    Under pytest a test that RETURNS False still PASSES. Pytest only emits
    `PytestReturnNotNoneWarning` and moves on. So the rename made the runner
    redundant and the conditions blind at the same time, and nothing said so.
    Measured 2026-08-20 across the three files that shared this helper: 25 test
    functions, 78 conditions, none able to fail the suite.

    An assert is the whole fix. The `main()` runner went with it, because its
    only job was to read a return value that no longer exists.
    """
    assert cond, name


class _FakeProc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _email_card(**over):
    c = {"id": "abc123", "action_type": "email_send", "status": "approved",
         "to": "x@example.com", "subject": "s", "draft_body": "b",
         "draft_status": "ready_for_review"}
    c.update(over)
    return c


def test_send_card():
    # gate invariant: email_send resolves gated
    _check("email_send resolves gated", tool_risk.tier_for("email_send") == tool_risk.GATED)

    # success
    orig = aqx.subprocess.run
    try:
        aqx.subprocess.run = lambda *a, **k: _FakeProc(0)
        r = aqx.send_card(ROOT, _email_card())
        _check("send success -> sent", r["result"] == "sent")
        # failure
        aqx.subprocess.run = lambda *a, **k: _FakeProc(1, stderr="smtp boom")
        r = aqx.send_card(ROOT, _email_card())
        _check("send failure -> send_failed + error", r["result"] == "send_failed" and "boom" in r["error"])
    finally:
        aqx.subprocess.run = orig

    # a non-send type is skipped (never sends)
    r = aqx.send_card(ROOT, _email_card(action_type="note"))
    _check("note (non-send type) -> skipped", r["result"] == "skipped")
    # gate-refusal: if email_send ever failed to resolve gated (tampered ledger),
    # the synchronous send path REFUSES rather than sends (defensive invariant).
    orig_tier = aqx.tool_risk.tier_for
    try:
        aqx.tool_risk.tier_for = lambda t: "autonomous"  # force non-gated
        r = aqx.send_card(ROOT, _email_card())
        _check("email_send not gated -> refused (no send)", r["result"] == "refused")
    finally:
        aqx.tool_risk.tier_for = orig_tier
    # telegram_send -> explicit 501 permanent
    r = aqx.send_card(ROOT, _email_card(action_type="telegram_send"))
    _check("telegram_send -> 501 permanent", r["result"] == "send_failed" and r["classification"] == "permanent")
    # empty body -> permanent, no subprocess
    r = aqx.send_card(ROOT, _email_card(draft_body=""))
    _check("empty body -> permanent", r["result"] == "send_failed" and r["classification"] == "permanent")


# ---- Success Signal: the only synchronous approve -> send end-to-end path ----

def _load_aq_cli():
    """Import scripts/action-queue.py by path. An import failure is a FAILURE.

    This used to be wrapped by the caller in `except Exception: print("[SKIP]");
    return True`, alongside `if not hasattr(aqcli, "approve_and_send"): ...
    return True`. Those two lines were left behind by a staged rollout ("filled
    in Step 2 once action-queue.py is rewritten") and Step 2 landed long ago.

    What they bought, measured 2026-08-30 by renaming `approve_and_send` in
    `scripts/action-queue.py`: the suite's ONLY synchronous approve-to-send test
    printed `[SKIP]` and reported nothing wrong. `pyproject.toml`'s
    `filterwarnings = ["error::pytest.PytestReturnNotNoneWarning"]` did turn the
    `return True` into a failure - but one that names the return value, not the
    missing send path, and the same run with `return True` softened to a bare
    `return` went green with the send path gone. A guard whose teeth are an
    unrelated warning promotion is not a guard.

    `tests/test_action_queue_endpoints.py` points HERE for this coverage, so a
    silent skip removes the coverage two files claim to have. There is nothing
    left to be conditional about: the import either works or this test is red.
    """
    spec = importlib.util.spec_from_file_location("aqcli", ROOT / "scripts" / "action-queue.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Sender:
    """Records every send subprocess. `refuse=True` makes any send a failure.

    Standing in for the transport is not optional here: `send_card` shells out
    to `scripts/send-email.py`, which is a real outbound path. Every test below
    installs one of these, and the refusal tests install one that raises, so a
    regression that sends where it must not fails loudly instead of mailing
    someone.
    """

    def __init__(self, returncode=0, stderr="", refuse=False):
        self.calls = []
        self.returncode = returncode
        self.stderr = stderr
        self.refuse = refuse

    def __call__(self, *a, **k):
        self.calls.append((a, k))
        if self.refuse:
            raise AssertionError(
                "the sender ran on a path that must never send: "
                f"argv={a[0] if a else None!r}")
        return _FakeProc(self.returncode, stderr=self.stderr)


def _queue(td, *cards):
    """Write a queue.json under a temp DATA root and return (data_root, path)."""
    data_root = Path(td)
    qdir = data_root / "outputs" / "operations" / "action-queue"
    qdir.mkdir(parents=True)
    path = qdir / "queue.json"
    path.write_text(json.dumps({"version": 1, "generated_at": None,
                                "actions": list(cards)}), encoding="utf-8")
    return data_root, path


def _status(path, action_id):
    q = json.loads(path.read_text(encoding="utf-8"))
    return [c["status"] for c in q["actions"] if c["id"] == action_id]


def test_the_cli_exposes_the_synchronous_send_entry_point():
    """The named seam exists. Renaming it must break this file, not skip it."""
    aqcli = _load_aq_cli()
    _check("scripts/action-queue.py defines approve_and_send",
           callable(getattr(aqcli, "approve_and_send", None)))


def test_success_signal():
    """Daemon-free approve on a temp DATA root; synchronous transition to sent."""
    aqcli = _load_aq_cli()
    sender = _Sender()
    with tempfile.TemporaryDirectory() as td:
        data_root, path = _queue(td, _email_card(status="pending"))
        # stub the send so no real email leaves (patch the CLI's own copy of the
        # executor module, _AQX, whose send_card runs the subprocess)
        orig = aqcli._AQX.subprocess.run
        try:
            aqcli._AQX.subprocess.run = sender
            res = aqcli.approve_and_send(ROOT, data_root, "abc123")
        finally:
            aqcli._AQX.subprocess.run = orig
        _check("approve transitions card to sent in one call", res.get("result") == "sent")
        _check("the send ran exactly once", len(sender.calls) == 1)
        # the queue file reflects sent
        _check("queue.json shows status sent", _status(path, "abc123") == ["sent"])


def test_a_draft_not_marked_ready_is_refused_and_nothing_is_sent():
    """The negative case: approve must REFUSE, not send, on an unready draft.

    The success test alone cannot tell a working gate from an absent one - a
    path that sends everything passes it. This one fails the moment the
    ready_for_review check stops running, and the sender raises rather than
    quietly mailing the half-written draft.
    """
    aqcli = _load_aq_cli()
    sender = _Sender(refuse=True)
    with tempfile.TemporaryDirectory() as td:
        data_root, path = _queue(
            td, _email_card(status="pending", draft_status="draft"))
        orig = aqcli._AQX.subprocess.run
        try:
            aqcli._AQX.subprocess.run = sender
            res = aqcli.approve_and_send(ROOT, data_root, "abc123")
        finally:
            aqcli._AQX.subprocess.run = orig
        _check("an unready draft is blocked", res.get("result") == "blocked")
        _check("the refusal names the draft state",
               "ready_for_review" in (res.get("error") or ""))
        _check("no send was attempted", sender.calls == [])
        _check("the claim was released back to pending",
               _status(path, "abc123") == ["pending"])


def test_a_type_that_does_not_resolve_gated_is_refused_and_nothing_is_sent():
    """The send-gate invariant on the synchronous path, driven to refusal.

    `.claude/rules/lethal-trifecta.md` makes this the load-bearing control:
    anything send-capable resolves `gated` or is not sent. Forcing the resolver
    to answer `autonomous` is the only way to observe the refusal, so the ledger
    itself is never edited - only the CLI module's view of it, and only inside
    this test.
    """
    aqcli = _load_aq_cli()
    sender = _Sender(refuse=True)
    with tempfile.TemporaryDirectory() as td:
        data_root, path = _queue(td, _email_card(status="pending"))
        orig_run = aqcli._AQX.subprocess.run
        orig_tier = aqcli.tool_risk.tier_for
        try:
            aqcli._AQX.subprocess.run = sender
            aqcli.tool_risk.tier_for = lambda t: "autonomous"
            res = aqcli.approve_and_send(ROOT, data_root, "abc123")
        finally:
            aqcli._AQX.subprocess.run = orig_run
            aqcli.tool_risk.tier_for = orig_tier
        _check("a non-gated send type is refused", res.get("result") == "refused")
        _check("no send was attempted", sender.calls == [])
        _check("the card was not moved", _status(path, "abc123") == ["pending"])


def test_approving_the_same_card_twice_sends_once():
    """A repeat keystroke must not mail the recipient a second time."""
    aqcli = _load_aq_cli()
    sender = _Sender()
    with tempfile.TemporaryDirectory() as td:
        data_root, path = _queue(td, _email_card(status="pending"))
        orig = aqcli._AQX.subprocess.run
        try:
            aqcli._AQX.subprocess.run = sender
            first = aqcli.approve_and_send(ROOT, data_root, "abc123")
            try:
                second = aqcli.approve_and_send(ROOT, data_root, "abc123")
            except SystemExit as exc:
                # `_resolve_id` exits when the id is no longer in the active
                # set. That is a refusal too; what must not happen is a send.
                second = {"result": f"exit:{exc.code}"}
        finally:
            aqcli._AQX.subprocess.run = orig
        _check("the first approve sent", first.get("result") == "sent")
        _check("the second approve did not send", second.get("result") != "sent")
        _check("the sender ran exactly once", len(sender.calls) == 1)
        _check("the card is sent, once", _status(path, "abc123") == ["sent"])
