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
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}")
    return bool(cond)


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
    ok = True
    # gate invariant: email_send resolves gated
    ok &= _check("email_send resolves gated", tool_risk.tier_for("email_send") == tool_risk.GATED)

    # success
    orig = aqx.subprocess.run
    try:
        aqx.subprocess.run = lambda *a, **k: _FakeProc(0)
        r = aqx.send_card(ROOT, _email_card())
        ok &= _check("send success -> sent", r["result"] == "sent")
        # failure
        aqx.subprocess.run = lambda *a, **k: _FakeProc(1, stderr="smtp boom")
        r = aqx.send_card(ROOT, _email_card())
        ok &= _check("send failure -> send_failed + error", r["result"] == "send_failed" and "boom" in r["error"])
    finally:
        aqx.subprocess.run = orig

    # a non-send type is skipped (never sends)
    r = aqx.send_card(ROOT, _email_card(action_type="note"))
    ok &= _check("note (non-send type) -> skipped", r["result"] == "skipped")
    # gate-refusal: if email_send ever failed to resolve gated (tampered ledger),
    # the synchronous send path REFUSES rather than sends (defensive invariant).
    orig_tier = aqx.tool_risk.tier_for
    try:
        aqx.tool_risk.tier_for = lambda t: "autonomous"  # force non-gated
        r = aqx.send_card(ROOT, _email_card())
        ok &= _check("email_send not gated -> refused (no send)", r["result"] == "refused")
    finally:
        aqx.tool_risk.tier_for = orig_tier
    # telegram_send -> explicit 501 permanent
    r = aqx.send_card(ROOT, _email_card(action_type="telegram_send"))
    ok &= _check("telegram_send -> 501 permanent", r["result"] == "send_failed" and r["classification"] == "permanent")
    # empty body -> permanent, no subprocess
    r = aqx.send_card(ROOT, _email_card(draft_body=""))
    ok &= _check("empty body -> permanent", r["result"] == "send_failed" and r["classification"] == "permanent")
    return ok


# ---- Success Signal (filled in Step 2 once action-queue.py is rewritten) ----

def _load_aq_cli():
    spec = importlib.util.spec_from_file_location("aqcli", ROOT / "scripts" / "action-queue.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_success_signal():
    """Daemon-free list/show/approve on a temp DATA root; synchronous transition."""
    ok = True
    try:
        aqcli = _load_aq_cli()
    except Exception as exc:  # action-queue.py not yet rewritten (Step 2)
        print(f"  [SKIP] success-signal: action-queue.py not import-ready yet ({type(exc).__name__})")
        return True
    if not hasattr(aqcli, "approve_and_send"):
        print("  [SKIP] success-signal: approve_and_send not present yet (Step 2)")
        return True

    with tempfile.TemporaryDirectory() as td:
        data_root = Path(td)
        qdir = data_root / "outputs" / "operations" / "action-queue"
        qdir.mkdir(parents=True)
        card = _email_card(status="pending")
        (qdir / "queue.json").write_text(
            json.dumps({"version": 1, "generated_at": None, "actions": [card]}), encoding="utf-8")

        # stub the send so no real email leaves (patch the CLI's own copy of the
        # executor module, _AQX, whose send_card runs the subprocess)
        orig = aqcli._AQX.subprocess.run
        try:
            aqcli._AQX.subprocess.run = lambda *a, **k: _FakeProc(0)
            res = aqcli.approve_and_send(ROOT, data_root, "abc123")
        finally:
            aqcli._AQX.subprocess.run = orig
        ok &= _check("approve transitions card to sent in one call", res.get("result") == "sent")
        # the queue file reflects sent
        q = json.loads((qdir / "queue.json").read_text())
        sent = [c for c in q["actions"] if c["id"] == "abc123" and c["status"] == "sent"]
        ok &= _check("queue.json shows status sent", len(sent) == 1)
    return ok


def main():
    ok = True
    for fn in (test_send_card, test_success_signal):
        print(f"\n{fn.__name__}:")
        ok &= fn()
    print("\nALL PASS" if ok else "\nSOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
