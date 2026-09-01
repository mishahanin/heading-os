#!/usr/bin/env python3
"""Async Action-Queue send executor (R2; R3+R14 honest failure classification).

Reads ``status: approved`` + ``action_type: email_send`` cards from
``queue.json`` and sends each via ``scripts/send-email.py`` (which loads
Exchange creds from ``.env`` in THIS child process - the daemon never holds
them, and auto-logs the send to CRM). Prints a JSON array of per-card results
to stdout:

    [{"action_id": "...", "result": "sent"},
     {"action_id": "...", "result": "send_failed",
      "error": "...", "classification": "transient",
      "attempt": 1, "next_attempt_at": "2026-06-04T12:34:56+00:00"}]

It does NOT write ``queue.json``. A caller captures this stdout and applies the
status changes in-process under the queue lock (keeps the file single-writer).
The executor reads ``attempt`` / ``next_attempt_at`` off each card to honour
backoff and emits the next ``attempt`` / ``next_attempt_at`` for transient
failures so the caller can persist them; the executor itself writes nothing.

**Nothing spawns ``main()`` today.** The daemon's send-executor spawn was
REMOVED on 2026-06-27 (``scripts/bridge-daemon.py``, ``_executor_job``): the
synchronous terminal ``scripts/action-queue.py approve`` is now the SOLE send
path, and the daemon never sends. This docstring claimed "Spawned every ~2 min
by a config-gated daemon job" until 2026-08-23, and an auditor reading it
reasoned correctly about a batch sender that has not run for two months. What
stays live here is ``send_card``, which ``action-queue.py`` imports and calls
for the one card the CEO approved. ``main()`` is retained as the batch entry
point should a caller ever want it back.

Failure classification (scrutiny M1 - honest within what is deterministic).
``send-email.py`` exits 1 for both connection blips and permanent config /
recipient errors with no distinct codes, so a clean transient/permanent split
from stderr would be brittle string-matching. Therefore:

- pre-send empty recipient / empty body (detected before spawning) -> permanent.
- ``TimeoutExpired`` -> transient.
- residual exit-code-1 + stderr -> default transient (gets bounded backoff);
  the ``max_attempts`` cap reclassifies it to permanent rather than guessing
  from stderr.

Backoff (R14). A transient failure carries ``attempt`` / ``next_attempt_at`` on
the card. The executor skips a card whose ``next_attempt_at`` is still in the
future; on a transient failure it computes the next window via
``dead_letter.backoff_schedule(attempt)``. After ``max_attempts`` (default 5) a
transient failure is reclassified ``permanent`` (the daemon writes it to the
DLQ on a permanent result) - bounded, no unbounded retry loop.

Usage: python scripts/action-queue-execute.py

Tests: tests/test_a_queue_that_read_corrupt_as_empty.py
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import dead_letter, tool_risk
from scripts.utils.timeparse import parse_iso
from scripts.utils.workspace import get_outputs_dir, get_workspace_root

SEND_TIMEOUT_S = 120
MAX_ATTEMPTS = 5


def send_card(engine_root: Path, card: dict, now: datetime | None = None) -> dict:
    """Send ONE card via send-email.py and return a result dict. Reusable by both
    the batch executor (this file's main) and the synchronous terminal approve
    (scripts/action-queue.py).

    ``engine_root`` is the ENGINE workspace root - it locates
    ``scripts/send-email.py`` (an engine path) and is the subprocess cwd. This
    function NEVER touches queue.json; the caller resolves the queue store
    separately (under the DATA root) and applies the status transition.

    Result shapes:
      {result: "sent",     classification: "sent",      attempt}
      {result: "send_failed", classification: transient|permanent, error, attempt[, next_attempt_at]}
      {result: "refused",  classification: "none", error}  (not gated - never send)
      {result: "skipped",  classification: "none", error}  (not a send type)
    The synchronous caller stamps its own classification on the queue (M2: None,
    no auto-DLQ); the batch caller honours transient/permanent for backoff + DLQ.
    """
    now = datetime.now(timezone.utc) if now is None else now
    aid = card.get("id")
    action_type = card.get("action_type")
    # Derived up here, not further down, because the first branch that returns
    # a send_failed result is the telegram one below.
    attempt = card.get("attempt") or 0
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 0:
        attempt = 0
    # THE GATE, and it is UNCONDITIONAL: it runs for every card that reaches
    # this function, above every branch on `action_type`. This function is the
    # only code in the tree that can send anything, and both callers - the
    # terminal `action-queue.py approve` and this file's batch `main()` - come
    # through it, so nothing below can send without `tier_for` having answered
    # `gated` for the card in hand.
    #
    # It used to sit BELOW the two type branches, inside the region keyed on the
    # literal `email_send`. A send-capable type registered in the ledger but
    # missing from those branches returned `skipped` before ever reaching it:
    # the guard was skipped, not failed. The exposure was latent (no executor
    # exists for any other type) and would have opened the day one was added,
    # because a new branch goes where the others are - above the old check.
    #
    # An emptied or tampered `send_capable` cannot switch this off. `tier_for`
    # is total over action_types and answers `gated` for one it does not know,
    # so the condition below is evaluated for every card whatever the ledger
    # says. `send_capable_types()` is consulted only to tell the two refusals
    # apart - never to decide whether to check.
    tier = tool_risk.tier_for(action_type)
    if tier != tool_risk.GATED:
        if action_type in tool_risk.send_capable_types():
            # The ledger calls it a sender and the resolver disagrees. Only a
            # tampered resolver can produce this; refuse loudly.
            return {"action_id": aid, "result": "refused",
                    "error": f"{action_type} does not resolve gated - refusing to send",
                    "classification": "none"}
        # Positively classified as a non-send type (autonomous / notify).
        return {"action_id": aid, "result": "skipped",
                "error": f"not a send type ({action_type})", "classification": "none"}
    # telegram_send is reserved-and-gated but has no executor yet (F-L6): explicit
    # 501, never a silent skip. A gated send that cannot send.
    if action_type == "telegram_send":
        # `attempt` too: every other send_failed path carries it, and the
        # docstring's result shape promises it, so a caller persisting
        # res["attempt"] uniformly hit KeyError on this one branch.
        return {"action_id": aid, "result": "send_failed",
                "error": "telegram executor not implemented (501)",
                "classification": "permanent", "attempt": attempt}
    if action_type != "email_send":
        # Gated, but no executor here knows how to send it - an unclassified
        # type, or a send type registered before its executor landed.
        return {"action_id": aid, "result": "skipped",
                "error": f"no executor for send type ({action_type})",
                "classification": "none"}

    to = (card.get("to") or "").strip()
    subject = card.get("subject") or ""
    body = card.get("draft_body") or ""
    if not to or not body:
        return {"action_id": aid, "result": "send_failed",
                "error": "draft not written (run /cold-sweep to fill the body)",
                "classification": "permanent", "attempt": attempt}
    send_script = engine_root / "scripts" / "send-email.py"
    # The body goes on STDIN, never in argv. Two reasons, both measured
    # 2026-08-23. An argv element is readable by any local account via `ps` for
    # the up-to-120-second life of the send, and outbound CRM content is the
    # most commercially sensitive text here. And Linux caps one argument at
    # MAX_ARG_STRLEN = 131072 bytes: a 100,000-byte body spawned, 131,072 raised
    # OSError [Errno 7]. See tests/test_send_body_never_reaches_argv.py.
    cmd = [sys.executable, str(send_script), "--to", to, "--subject", subject,
           "--body-stdin"]
    try:
        p = subprocess.run(cmd, cwd=str(engine_root), capture_output=True,
                           text=True, timeout=SEND_TIMEOUT_S, input=body)
    except subprocess.TimeoutExpired:
        return _transient_result(aid, "send timed out", attempt, now)
    except OSError as exc:
        # A spawn can still fail for reasons this code does not control:
        # ENOMEM, EMFILE, a missing interpreter. Returning a result keeps the
        # caller on the recorded path; raising made it a raw traceback out of
        # `action-queue.py approve`, the CEO's own send command.
        return _transient_result(aid, f"could not start the sender: {exc}", attempt, now)
    if p.returncode == 0:
        return {"action_id": aid, "result": "sent", "classification": "sent", "attempt": attempt}
    error = (p.stderr or p.stdout or "send failed")[-300:].strip()
    return _transient_result(aid, error, attempt, now)


def main() -> int:
    root = get_workspace_root()
    queue_path = get_outputs_dir() / "operations/action-queue/queue.json"
    # Absent and unreadable are different facts and must not share an answer --
    # the principle `load_fleet_registry` in `aggregate-crm.py` states in its
    # own docstring, and the one broken here. A queue file with one stray comma
    # printed `[]` and exited 0, and the documented caller contract is "capture
    # this stdout and apply the status changes". So the caller applied nothing,
    # reported success, and every approved send card was dropped from the run
    # with no diagnostic anywhere. An empty queue is a fact; an unreadable one
    # is a failure, and a send queue is the worst place to confuse them.
    if not queue_path.exists():
        print("[]")
        return 0
    #
    # `UnicodeDecodeError` sits in the tuple beside the other two because it is
    # a SIBLING of `json.JSONDecodeError` under `ValueError`, not a subclass of
    # it, and the decode happens inside `read_text` before `json.loads` is
    # handed anything at all. The queue store is appended to by a live sender,
    # so a torn write is the ordinary corruption here. MEASURED 2026-09-01
    # against a queue.json holding one 0xff byte: a raw UnicodeDecodeError
    # traceback out of `main`, no `[]` on stdout, and the documented caller
    # contract ("capture this stdout and apply the status changes") broken in
    # exactly the way the comment above says it must not be.
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"action-queue-execute: cannot read {queue_path}: {exc}",
              file=sys.stderr)
        print("[]")
        return 1

    # Parsing is not reading. The except above catches JSON that is not JSON;
    # JSON that PARSES into the wrong shape got through it and died on
    # `data.get` / `card.get` with a raw AttributeError, no stderr diagnostic
    # and no JSON array on stdout - which breaks the same contract the comment
    # above is about, just one layer later. `[1,2]`, `"hello"` and
    # `{"actions": {...}}` all reached that traceback.
    #
    # A non-object element is rejected with the document rather than skipped.
    # Skipping it would be the silent drop this file exists not to do: an
    # element that is not an object cannot be inspected for `status`, so
    # "it was not approved anyway" is an assumption, not a reading.
    cards = data.get("actions") if isinstance(data, dict) else None
    if not isinstance(cards, list) or not all(isinstance(c, dict) for c in cards):
        print(f"action-queue-execute: {queue_path} is not a queue document "
              f"(expected an object with a list of card objects under 'actions')",
              file=sys.stderr)
        print("[]")
        return 1

    now = datetime.now(timezone.utc)
    results: list[dict] = []
    for card in cards:
        if card.get("status") != "approved":
            continue
        # Backoff gate: skip a transient-failed card still inside its window.
        next_at = card.get("next_attempt_at")
        if next_at:
            when = parse_iso(next_at)
            if when is not None and when > now:
                continue
        # One card must never discard the batch. Results are printed ONCE,
        # after this loop, so an exception here loses the record of every card
        # already SENT in this run: those stay `approved`, and the next run
        # sends them again. Duplicate mail to an external counterparty is the
        # single failure a send-gated queue exists to prevent, so the guard is
        # deliberately broad.
        try:
            res = send_card(root, card, now=now)
        except Exception as exc:                        # noqa: BLE001 - see above
            res = {"action_id": card.get("id"), "result": "send_failed",
                   "error": f"executor raised: {exc!r}",
                   "classification": "permanent",
                   "attempt": (card.get("attempt") or 0) + 1}
        # Preserve batch behaviour: non-send / non-gated cards are silently
        # skipped (not surfaced as failures); everything else is reported.
        if res.get("result") in ("skipped", "refused"):
            continue
        results.append(res)

    print(json.dumps(results, ensure_ascii=False))
    return 0


def _transient_result(aid, error: str, attempt: int, now: datetime) -> dict:
    """Build a send_failed result for a default-transient failure.

    Bumps ``attempt``; once it reaches ``MAX_ATTEMPTS`` the failure is
    reclassified permanent (the daemon writes the DLQ entry on permanent), else
    it stays transient with a fresh ``next_attempt_at`` backoff window.
    """
    new_attempt = attempt + 1
    if new_attempt >= MAX_ATTEMPTS:
        return {"action_id": aid, "result": "send_failed", "error": error,
                "classification": "permanent", "attempt": new_attempt}
    delay = dead_letter.backoff_schedule(attempt)
    next_at = now.timestamp() + delay
    return {"action_id": aid, "result": "send_failed", "error": error,
            "classification": "transient", "attempt": new_attempt,
            "next_attempt_at": datetime.fromtimestamp(next_at, tz=timezone.utc).isoformat()}


if __name__ == "__main__":
    sys.exit(main())
