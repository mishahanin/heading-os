#!/usr/bin/env python3
"""Terminal-native Action Queue driver (console-first, daemon-free).

Drives the queue entirely IN-PROCESS via the mutate helpers in
``scripts/bridge_daemon/sources/action_queue.py`` - no bridge daemon, no HTTP,
no ``.daemon-state``. The queue works with the browser closed and the daemon
down. ``approve`` is SYNCHRONOUS: it sends right then via ``send_card`` and shows
``sent`` / ``send_failed (reason)`` in the same command - there is no async
background send.

Root discipline (the two-repo topology):
  - the queue store lives under the DATA root  -> get_data_root()  (passed to
    every action_queue helper, matching the depositors and the daemon).
  - scripts/send-email.py lives under the ENGINE root -> get_workspace_root()
    (passed to send_card). These roots DIFFER on the CEO MAIN topology and must
    not be conflated.

The send-gate is untouched: send_card refuses anything that does not resolve
``gated``; the human ``approve`` IS the explicit click; nothing auto-sends.

Usage:
    python scripts/action-queue.py list
    python scripts/action-queue.py show <id-or-prefix>
    python scripts/action-queue.py approve <id-or-prefix>     # synchronous send
    python scripts/action-queue.py retry <id-or-prefix>       # re-send a failed card
    python scripts/action-queue.py edit <id-or-prefix> [--subject S] [--body-file F]
    python scripts/action-queue.py dismiss <id-or-prefix> [--reason R]
    python scripts/action-queue.py deposit --file <cards.json>

Exit codes: 0 ok, 1 request/usage error.
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import tool_risk
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import get_data_root, get_workspace_root
from scripts.bridge_daemon.sources.action_queue import (
    append_cards,
    apply_status,
    approve_card,
    dismiss_card,
    edit_card,
    list_action_queue,
)

# send_card lives in the hyphenated scripts/action-queue-execute.py, which is not
# importable by dotted path; load it by file. Exposed as _AQX so the per-card
# subprocess can be patched in tests (the gate + send logic stays one copy).
_spec = importlib.util.spec_from_file_location(
    "action_queue_execute", Path(__file__).resolve().parent / "action-queue-execute.py")
_AQX = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_AQX)
send_card = _AQX.send_card


def _resolve_id(items: list[dict], prefix: str) -> str:
    """Resolve a full id or unique prefix against the active queue."""
    exact = [c for c in items if c.get("id") == prefix]
    if exact:
        return exact[0]["id"]
    matches = [c for c in items if str(c.get("id", "")).startswith(prefix)]
    if not matches:
        print(f"{RED}no active card matches '{prefix}'{RESET}", file=sys.stderr)
        sys.exit(1)
    if len(matches) > 1:
        print(f"{RED}ambiguous prefix '{prefix}' ({len(matches)} matches){RESET}", file=sys.stderr)
        sys.exit(1)
    return matches[0]["id"]


def _print_aq_row(c: dict) -> None:
    prio = c.get("priority", "P3")
    pcol = {"P1": RED, "P2": YELLOW, "P3": GRAY}.get(prio, GRAY)
    status = c.get("status", "pending")
    scol = GREEN if status == "approved" else (RED if status == "send_failed" else CYAN)
    draft = c.get("draft_status")
    dsuffix = f"  {GRAY}{draft}{RESET}" if draft else ""
    print(f"  {pcol}{prio}{RESET} {c.get('id', '')[:8]} "
          f"{scol}{status:<11}{RESET} {GRAY}{c.get('source', '-'):<11}{RESET} "
          f"{c.get('title', '(untitled)')}{dsuffix}")


# ============================================================
# Core: synchronous approve-and-send (reusable / testable)
# ============================================================

def approve_and_send(engine_root: Path, data_root: Path, id_or_prefix: str) -> dict:
    """Approve a card and, for a gated send card, SEND it synchronously.

    Resolves the id against the active set, then:
      - email_send/telegram_send (gated): refuse if not gated; require
        draft_status == ready_for_review (email_send); call send_card(engine_root)
        and route the transition through apply_status(data_root, sent|send_failed,
        classification=None) - audit via the helper, NO auto-DLQ (the CEO retries).
      - note/pipeline_update/alert: record the non-send disposition (approve_card).

    Returns a small result dict for the caller to print.
    """
    env = list_action_queue(data_root)
    items = env.get("items", [])
    aid = _resolve_id(items, id_or_prefix)
    card = next(c for c in items if c["id"] == aid)
    atype = card.get("action_type")

    if atype in ("email_send", "telegram_send"):
        if tool_risk.tier_for(atype) != tool_risk.GATED:
            return {"result": "refused", "action_id": aid,
                    "error": f"{atype} does not resolve gated - refusing to send"}
        if atype == "email_send" and card.get("draft_status") != "ready_for_review":
            return {"result": "blocked", "action_id": aid,
                    "error": "draft not ready_for_review - edit it first"}
        res = send_card(engine_root, card)
        if res.get("result") == "sent":
            apply_status(data_root, aid, "sent", event="approved")
            return {"result": "sent", "action_id": aid}
        # M2: stamp classification=None -> apply_status writes NO dead-letter entry.
        apply_status(data_root, aid, "send_failed", event="send_failed",
                     error=res.get("error", ""), classification=None)
        return {"result": "send_failed", "action_id": aid, "error": res.get("error", "")}

    # Non-send disposition (note / pipeline_update / alert).
    approve_card(data_root, aid)
    return {"result": "approved", "action_id": aid}


# ============================================================
# Subcommands
# ============================================================

def cmd_list(engine_root: Path, data_root: Path, args) -> int:
    d = list_action_queue(data_root)
    actionable = d.get("actionable", d.get("items", []))
    fyi = d.get("fyi", [])
    counts = f"{GRAY}sent {d.get('sent_count', 0)} · dismissed {d.get('dismissed_count', 0)}{RESET}"
    if not actionable and not fyi:
        print(f"{GRAY}queue clear{RESET} "
              f"(sent {d.get('sent_count', 0)}, dismissed {d.get('dismissed_count', 0)})")
        return 0
    act_total = d.get("actionable_total", len(actionable))
    print(f"{BOLD}{act_total} action(s) waiting{RESET}  {counts}")
    for c in actionable:
        _print_aq_row(c)
    if fyi:
        print(f"{GRAY}FYI - read-only context ({len(fyi)}){RESET}")
        for c in fyi:
            _print_aq_row(c)
    return 0


def cmd_show(engine_root: Path, data_root: Path, args) -> int:
    items = list_action_queue(data_root).get("items", [])
    aid = _resolve_id(items, args.id)
    card = next(c for c in items if c["id"] == aid)
    print(json.dumps(card, indent=2, ensure_ascii=False))
    return 0


def cmd_approve(engine_root: Path, data_root: Path, args) -> int:
    res = approve_and_send(engine_root, data_root, args.id)
    r = res.get("result")
    aid = (res.get("action_id") or "")[:8]
    if r == "sent":
        print(f"{GREEN}sent{RESET} {aid} (delivered now - watched, not async)")
        return 0
    if r == "approved":
        print(f"{GREEN}approved{RESET} {aid} (non-send disposition recorded)")
        return 0
    if r == "send_failed":
        print(f"{RED}send failed{RESET} {aid}: {res.get('error', '')}\n"
              f"{GRAY}card kept as send_failed - fix and `retry {aid}`{RESET}", file=sys.stderr)
        return 1
    print(f"{YELLOW}{r}{RESET} {aid}: {res.get('error', '')}", file=sys.stderr)
    return 1


def cmd_retry(engine_root: Path, data_root: Path, args) -> int:
    items = list_action_queue(data_root).get("items", [])
    aid = _resolve_id(items, args.id)
    card = next(c for c in items if c["id"] == aid)
    if card.get("status") != "send_failed":
        print(f"{YELLOW}retry only applies to send_failed cards{RESET} "
              f"({aid[:8]} is {card.get('status')})", file=sys.stderr)
        return 1
    return cmd_approve(engine_root, data_root, args)


def cmd_dismiss(engine_root: Path, data_root: Path, args) -> int:
    items = list_action_queue(data_root).get("items", [])
    aid = _resolve_id(items, args.id)
    dismiss_card(data_root, aid, reason=args.reason or "")
    print(f"{YELLOW}dismissed{RESET} {aid[:8]} (suppressed from re-proposal for the cooldown window)")
    return 0


def cmd_edit(engine_root: Path, data_root: Path, args) -> int:
    items = list_action_queue(data_root).get("items", [])
    aid = _resolve_id(items, args.id)
    if args.subject is None and not args.body_file:
        print(f"{RED}nothing to edit - pass --subject and/or --body-file{RESET}", file=sys.stderr)
        return 1
    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else None
    edit_card(data_root, aid, subject=args.subject, draft_body=body,
              draft_status="ready_for_review")
    print(f"{GREEN}edited{RESET} {aid[:8]} (draft_status -> ready_for_review)")
    return 0


def cmd_deposit(engine_root: Path, data_root: Path, args) -> int:
    try:
        cards = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"{RED}cannot read cards file{RESET}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(cards, list):
        print(f"{RED}cards file must be a JSON array of card objects{RESET}", file=sys.stderr)
        return 1
    res = append_cards(data_root, cards)
    print(f"{GREEN}deposited{RESET} added={res.get('added', 0)} skipped={res.get('skipped', 0)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Terminal-native Action Queue driver (daemon-free).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list active cards")
    p_show = sub.add_parser("show", help="print one card's full JSON")
    p_show.add_argument("id")
    p_app = sub.add_parser("approve", help="approve + send synchronously (gated cards)")
    p_app.add_argument("id")
    p_ret = sub.add_parser("retry", help="re-send a send_failed card")
    p_ret.add_argument("id")
    p_dis = sub.add_parser("dismiss", help="tombstone a card")
    p_dis.add_argument("id")
    p_dis.add_argument("--reason", default="")
    p_edit = sub.add_parser("edit", help="rewrite an email card's subject/body")
    p_edit.add_argument("id")
    p_edit.add_argument("--subject", default=None)
    p_edit.add_argument("--body-file", default=None)
    p_dep = sub.add_parser("deposit", help="append cards from a JSON array file (daemon-free)")
    p_dep.add_argument("--file", required=True)
    args = ap.parse_args(argv)

    engine_root = get_workspace_root()
    data_root = get_data_root()

    dispatch = {
        "list": cmd_list, "show": cmd_show, "approve": cmd_approve, "retry": cmd_retry,
        "dismiss": cmd_dismiss, "edit": cmd_edit, "deposit": cmd_deposit,
    }
    return dispatch[args.cmd](engine_root, data_root, args)


if __name__ == "__main__":
    sys.exit(main())
