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

It does NOT write ``queue.json``. The spawning daemon job captures this stdout
and applies the status changes in-process under the queue lock (keeps the file
single-writer). The executor reads ``attempt`` / ``next_attempt_at`` off each
card to honour backoff and emits the next ``attempt`` / ``next_attempt_at`` for
transient failures so the daemon can persist them; the executor itself writes
nothing. Spawned every ~2 min by a config-gated daemon job; idempotent because
once the daemon marks a card ``sent`` it is no longer ``approved`` and the next
run skips it (the daemon job is ``max_instances=1`` so runs never overlap).

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
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import dead_letter, tool_risk
from scripts.utils.workspace import get_outputs_dir, get_workspace_root

SEND_TIMEOUT_S = 120
MAX_ATTEMPTS = 5


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp; return None if unparseable."""
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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
    # telegram_send is reserved-and-gated but has no executor yet (F-L6): explicit
    # 501, never a silent skip. A gated send that cannot send.
    if action_type == "telegram_send":
        return {"action_id": aid, "result": "send_failed",
                "error": "telegram executor not implemented (501)",
                "classification": "permanent"}
    if action_type != "email_send":
        return {"action_id": aid, "result": "skipped",
                "error": f"not a send type ({action_type})", "classification": "none"}
    # Send-capable types always resolve gated; refuse anything that does not.
    if tool_risk.tier_for(action_type) != tool_risk.GATED:
        return {"action_id": aid, "result": "refused",
                "error": f"{action_type} does not resolve gated - refusing to send",
                "classification": "none"}

    attempt = card.get("attempt") or 0
    if not isinstance(attempt, int) or attempt < 0:
        attempt = 0
    to = (card.get("to") or "").strip()
    subject = card.get("subject") or ""
    body = card.get("draft_body") or ""
    if not to or not body:
        return {"action_id": aid, "result": "send_failed",
                "error": "draft not written (run /cold-sweep to fill the body)",
                "classification": "permanent", "attempt": attempt}
    send_script = engine_root / "scripts" / "send-email.py"
    cmd = [sys.executable, str(send_script), "--to", to, "--subject", subject, "--body", body]
    try:
        p = subprocess.run(cmd, cwd=str(engine_root), capture_output=True, text=True, timeout=SEND_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return _transient_result(aid, "send timed out", attempt, now)
    if p.returncode == 0:
        return {"action_id": aid, "result": "sent", "classification": "sent", "attempt": attempt}
    error = (p.stderr or p.stdout or "send failed")[-300:].strip()
    return _transient_result(aid, error, attempt, now)


def main() -> int:
    root = get_workspace_root()
    queue_path = get_outputs_dir() / "operations/action-queue/queue.json"
    if not queue_path.exists():
        print("[]")
        return 0
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("[]")
        return 0

    now = datetime.now(timezone.utc)
    results: list[dict] = []
    for card in data.get("actions", []):
        if card.get("status") != "approved":
            continue
        # Backoff gate: skip a transient-failed card still inside its window.
        next_at = card.get("next_attempt_at")
        if next_at:
            when = _parse_iso(next_at)
            if when is not None and when > now:
                continue
        res = send_card(root, card, now=now)
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
