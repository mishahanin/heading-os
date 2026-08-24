#!/usr/bin/env python3
"""email-sweep.py -- persisted state machine for /email-intel recommended actions.

Replaces the old per-conversation code notation (P1-A: a,b) with one flat,
numbered action list across all triaged emails. The list is the source of
truth and survives a session crash: each action carries a status, so a half-
finished sweep is resumable (``pending`` shows what is left).

This is the console-first backing store for the /email-intel approval UX. The
skill calls ``propose`` once after building the digest, renders with ``list``,
moves actions on approval (``approve`` / ``skip`` / ``edit``), and stamps
execution outcomes (``set``). Everything the skill does in chat is equally
doable from the terminal against the same file.

State file: outputs/operations/email-intelligence/sweep-actions-YYYY-MM-DD.json
(one per day; multiple runs in a day append to the same file with continuing ids).

Action tiers (display + friction):
  local   local workspace write, applied on approval   -> [crm] / [task] / [contact] / [know]
  notify  reversible Action-Queue notify card           -> [notify]    (pipeline_update)
  gated   irreversible outbound send, human-gated        -> [send-gated] (reply/reply-all/forward/new)

Usage:
    python scripts/email-sweep.py propose --file proposed.json [--date D]
    python scripts/email-sweep.py list [--date D] [--json]
    python scripts/email-sweep.py pending [--date D] [--json]
    python scripts/email-sweep.py approve 1 3 5 [--date D]
    python scripts/email-sweep.py skip 4 [--date D]
    python scripts/email-sweep.py edit 2 --note "drop second paragraph" [--date D]
    python scripts/email-sweep.py set 2 --status done [--note "sent msgid ..."] [--date D]

`propose --file` payload: a JSON array of action dicts. Each needs at least
``type`` and ``title``; optional ``priority`` (P1/P2/P3), ``target``,
``detail`` (free-form object the executor consumes). ``id``, ``tier``, and
``status`` are assigned here.

Exit codes: 0 ok, 1 usage/validation error, 2 state file missing for a mutate.
"""
import argparse
import json
import os
import re
import tempfile
import sys
from datetime import date as _date_cls
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import get_data_root, get_default_tz

# ============================================================
# Configuration
# ============================================================

STATE_DIR = "outputs/operations/email-intelligence"  # leak-guard: ok (relative suffix; rooted on get_data_root() in main(), lands in the DATA overlay, never the engine tree)

# type -> (tier, short display tag). Unknown types floor at gated (friction-max,
# matching the workspace "missing metadata -> most friction" convention and the
# lethal-trifecta default: anything that might send is gated until classified).
#
# This is a SEPARATE namespace from scripts/utils/tool_risk.py (the R3 ledger).
# tool_risk classifies Action-Queue action_types (email_send, pipeline_update,
# note, alert) for the daemon executor's code-enforced gate. These are the
# /email-intel sweep action types (crm_log, send_reply, ...), gated procedurally
# by the skill via CEO number-approval (see digest-format.md). The two tables
# share the convention "send-capable / unknown -> gated" but not code; the test
# below (test_unknown_and_send_types_floor_at_gated) pins that invariant for this
# table the way test_tool_risk.py pins it for the ledger.
TYPE_TIERS = {
    "crm_log":      ("local",  "crm"),
    "task":         ("local",  "task"),
    "new_contact":  ("local",  "contact"),
    "knowledge":    ("local",  "know"),
    "pipeline":     ("notify", "notify"),
    "send_reply":     ("gated", "send-gated"),
    "send_reply_all": ("gated", "send-gated"),
    "send_forward":   ("gated", "send-gated"),
    "send_new":       ("gated", "send-gated"),
}

# status transitions the state machine allows (target <- {valid froms}).
# No edge returns to `proposed` once an action has moved: to un-approve a
# mistakenly-approved action, `skip` it (the intended decline escape hatch).
#
# `failed` used to be a DEAD END, and that broke the promise in this file's own
# docstring. It is not in TERMINAL, so `pending` keeps listing it as work that
# is left; and no target accepted it as a source, so nothing could move it. A
# send that failed could be neither retried nor abandoned, and the resume set
# never emptied again. The escape hatch existed and did not reach the one state
# an operator actually needs to escape.
#
# So two edges: `failed -> approved` is the retry, and `failed -> skipped` is
# giving up on it. Neither weakens the outbound gate. This file records
# decisions; the code-enforced send gate is `scripts/utils/tool_risk.py` on the
# Action Queue, and `approve` here IS a human typing the approval.
_ALLOWED_FROM = {
    "approved":  {"proposed", "approved", "failed"},
    "skipped":   {"proposed", "skipped", "approved", "failed"},
    "executing": {"approved", "executing"},
    "done":      {"approved", "executing", "done"},
    "failed":    {"approved", "executing", "failed"},
    "proposed":  {"proposed"},
}
TERMINAL = {"done", "skipped"}


def _tier_for(action_type: str) -> tuple[str, str]:
    return TYPE_TIERS.get(action_type, ("gated", "send-gated"))


# ============================================================
# State IO (atomic: tmp + os.replace)
# ============================================================

def _valid_date(date: str) -> str:
    """`date` as an exact YYYY-MM-DD, or a clean refusal.

    The value went straight into a filename, so a `--date` carrying separators
    and `..` escaped `outputs/operations/email-intelligence/` entirely and
    `propose` created directories and wrote JSON wherever it pointed. Nothing
    validated it. `strptime` accepts only the shape this file names its state
    after, which closes the traversal and the typo in one check.
    """
    # A shape check plus a calendar check, and no `strptime`: this is a
    # date-only value used as a filename, and `strptime` would build a naive
    # datetime that the workspace's DTZ ruleset (at zero) correctly refuses.
    # `fromisoformat` alone is not enough either — it accepts `20260801`.
    text = str(date or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError(f"--date must be an exact YYYY-MM-DD, got {date!r}")
    try:
        _date_cls.fromisoformat(text)
    except ValueError:
        raise ValueError(f"--date is not a real calendar date: {date!r}") from None
    return text


def _state_path(root: Path, date: str) -> Path:
    return root / STATE_DIR / f"sweep-actions-{_valid_date(date)}.json"


def _today() -> str:
    # the configured timezone is the workspace default; the skill passes --date explicitly
    # on every call, so this fallback only matters for ad-hoc CLI use.
    return datetime.now(get_default_tz()).strftime("%Y-%m-%d")


class SweepUnreadable(Exception):
    """The sweep file exists and cannot be used. NOT the same as absent.

    `_load` returned None for both, and `cmd_propose` reads
    `_load(...) or {new sweep}` — so a half-written or hand-corrupted approval
    queue was treated as "no sweep yet" and REPLACED by `_save`. An approval
    queue is the one file in this flow that must never be silently discarded.
    """


def _load(root: Path, date: str) -> dict | None:
    """The sweep for `date`, or None when there is no file yet.

    Raises SweepUnreadable when a file IS there and cannot be parsed or has the
    wrong shape, so a caller that mutates can refuse rather than overwrite.
    """
    p = _state_path(root, date)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise SweepUnreadable(f"{p}: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
        raise SweepUnreadable(f"{p}: not a sweep object with an actions list")
    return data


def _save(root: Path, date: str, data: dict) -> None:
    p = _state_path(root, date)
    p.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    # A unique tmp name per writer. Every writer used the SAME
    # `sweep-actions-<date>.json.tmp`, so two concurrent commands could both
    # write it and one `os.replace` moved the other's file out from under it —
    # losing an update, or raising FileNotFoundError. This does not serialize
    # the load-modify-save transaction (see the note in cmd_propose); it stops
    # the two writers from fighting over one scratch path.
    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ============================================================
# Commands
# ============================================================

def cmd_propose(root: Path, args) -> int:
    payload_path = Path(args.file)
    if not payload_path.exists():
        print(f"{RED}payload not found: {payload_path}{RESET}", file=sys.stderr)
        return 1
    try:
        proposed = json.loads(payload_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"{RED}payload is not valid JSON: {e}{RESET}", file=sys.stderr)
        return 1
    if not isinstance(proposed, list):
        print(f"{RED}payload must be a JSON array of action dicts{RESET}", file=sys.stderr)
        return 1

    date = args.date or _today()
    try:
        existing = _load(root, date)
    except SweepUnreadable as e:
        print(f"{RED}refusing to overwrite an unreadable sweep: {e}{RESET}",
              file=sys.stderr)
        print(f"{YELLOW}Move or repair the file, then re-run. A corrupt "
              f"approval queue is recoverable; a replaced one is not.{RESET}",
              file=sys.stderr)
        return 1
    data = existing or {
        "date": date,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "actions": [],
    }
    # Only NUMERIC ids count toward the next one. `_load` validates that
    # `actions` is a list and nothing more, so a persisted `{"id": "7"}` made
    # `max` return a string and `+ 1` a TypeError — a crash where a clean
    # state error belongs.
    bad_ids = [a.get("id") for a in data["actions"]
               if isinstance(a, dict) and not isinstance(a.get("id"), int)]
    if bad_ids:
        print(f"{RED}sweep state has non-numeric action id(s): {bad_ids}. "
              f"Repair the file before proposing.{RESET}", file=sys.stderr)
        return 1
    next_id = max((a.get("id", 0) for a in data["actions"]
                   if isinstance(a, dict)), default=0) + 1

    added = 0
    for raw in proposed:
        if not isinstance(raw, dict) or not raw.get("type") or not raw.get("title"):
            print(f"{YELLOW}skipping malformed action (need type+title): {raw}{RESET}", file=sys.stderr)
            continue
        tier, _tag = _tier_for(raw["type"])
        action = {
            "id": next_id,
            "type": raw["type"],
            "tier": tier,
            "priority": raw.get("priority", "P3"),
            "title": raw["title"],
            "target": raw.get("target", ""),
            "detail": raw.get("detail", {}),
            "status": "proposed",
            "note": "",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        data["actions"].append(action)
        next_id += 1
        added += 1

    _save(root, date, data)
    print(f"{GREEN}proposed {added} action(s){RESET} -> {_state_path(root, date)}")
    return 0


def _tag(action: dict) -> str:
    _tier, tag = _tier_for(action.get("type", ""))
    return tag


def _render(data: dict) -> None:
    actions = data["actions"]
    print(f"{BOLD}RECOMMENDED ACTIONS{RESET} -- sweep {data.get('date', '?')} "
          f"{GRAY}({len(actions)} action(s)){RESET}")
    for a in actions:
        # `list` is the first command run against a sweep, and the one an
        # operator reaches for when something looks wrong. A malformed entry
        # used to meet `a['id']` as a KeyError, so the ONE command that could
        # show which entry is broken was the command that could not run.
        if not isinstance(a, dict) or not isinstance(a.get("id"), int):
            print(f"  {RED}[?] malformed entry (needs an object with an integer "
                  f"id): {str(a)[:80]}{RESET}")
            continue
        st = a.get("status", "proposed")
        scol = {
            "proposed": CYAN, "approved": GREEN, "executing": YELLOW,
            "done": GREEN, "failed": RED, "skipped": GRAY,
        }.get(st, CYAN)
        tag = _tag(a)
        tagcol = RED if a.get("tier") == "gated" else (YELLOW if a.get("tier") == "notify" else GRAY)
        stmark = {"done": "[x]", "skipped": "[-]", "failed": "[!]"}.get(st, "[ ]")
        line = (f"  {stmark} {BOLD}{a['id']:>2}{RESET}. {a.get('title', '(untitled)')}"
                f"  {tagcol}[{tag}]{RESET}")
        if st not in ("proposed",):
            line += f"  {scol}{st}{RESET}"
        if a.get("note"):
            line += f"  {GRAY}({a['note']}){RESET}"
        print(line)
    print(f"\n{GRAY}> approve: 1,3,5 | 2 edit: <change> | 4 go | rest skip{RESET}")


def cmd_list(root: Path, args) -> int:
    date = args.date or _today()
    data = _load(root, date)
    if data is None:
        print(f"{GRAY}no sweep for {date}{RESET}")
        return 0
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    _render(data)
    return 0


def cmd_pending(root: Path, args) -> int:
    """Actions not yet in a terminal state -- the resume set after a crash."""
    date = args.date or _today()
    data = _load(root, date)
    if data is None:
        print(f"{GRAY}no sweep for {date}{RESET}")
        return 0
    # A malformed entry is PENDING, not skipped over: it is unresolved work by
    # definition, and dropping it would let the resume set read as empty while
    # the file still holds something nobody has dealt with.
    pending = [a for a in data["actions"]
               if not isinstance(a, dict) or a.get("status") not in TERMINAL]
    if args.json:
        print(json.dumps(pending, indent=2, ensure_ascii=False))
        return 0
    if not pending:
        print(f"{GREEN}sweep {date}: nothing pending (all done or skipped){RESET}")
        return 0
    print(f"{BOLD}{len(pending)} pending{RESET} in sweep {date}:")
    for a in pending:
        if not isinstance(a, dict) or not isinstance(a.get("id"), int):
            print(f"  {RED} ?. [malformed] {str(a)[:80]}{RESET}")
            continue
        print(f"  {a['id']:>2}. [{a.get('status')}] {a.get('title')}  [{_tag(a)}]")
    return 0


def _mutate_ids(root: Path, date: str, ids: list[int], target_status: str,
                note: str | None = None) -> int:
    data = _load(root, date)
    if data is None:
        print(f"{RED}no sweep file for {date} -- run propose first{RESET}", file=sys.stderr)
        return 2
    # Shape-guarded, the way `cmd_propose` already is. `_load` validates that
    # `actions` is a LIST and nothing about its members, so a hand-edited entry
    # with no `id`, or one that is not an object at all, met `a["id"]` here as a
    # KeyError or a TypeError - a traceback out of the command an operator runs
    # to approve a send, where this file has a clean exit code for everything
    # else. `cmd_propose` grew this check and the mutate path never did.
    malformed = [a for a in data["actions"]
                 if not isinstance(a, dict) or not isinstance(a.get("id"), int)]
    if malformed:
        print(f"{RED}sweep {date} has {len(malformed)} malformed action entr(ies) "
              f"(each needs an object with an integer id). Repair the file "
              f"before mutating it.{RESET}", file=sys.stderr)
        return 1
    by_id = {a["id"]: a for a in data["actions"]}
    changed = 0
    for i in ids:
        a = by_id.get(i)
        # Both refusals say NOTHING WAS APPLIED, because nothing was: `_save`
        # sits after the loop, so a mid-batch refusal discards the earlier
        # mutations too. That is the right behaviour and it was invisible -
        # `approve 1 2 3` refusing on #2 left an operator with no way to know
        # whether #1 had gone through, and re-running the whole batch is only
        # safe if it did not.
        if a is None:
            print(f"{RED}no action #{i} in sweep {date}{RESET} "
                  f"{GRAY}(nothing was changed){RESET}", file=sys.stderr)
            return 1
        cur = a.get("status", "proposed")
        allowed = _ALLOWED_FROM.get(target_status, set())
        if cur not in allowed:
            print(f"{RED}action #{i}: cannot move {cur} -> {target_status}{RESET} "
                  f"{GRAY}(nothing was changed){RESET}", file=sys.stderr)
            return 1
        a["status"] = target_status
        if note is not None:
            a["note"] = note
        a["updated_at"] = datetime.now(timezone.utc).isoformat()
        changed += 1
    _save(root, date, data)
    print(f"{GREEN}{target_status}{RESET} {changed} action(s): {', '.join(f'#{i}' for i in ids)}")
    return 0


def cmd_approve(root: Path, args) -> int:
    return _mutate_ids(root, args.date or _today(), args.ids, "approved")


def cmd_skip(root: Path, args) -> int:
    return _mutate_ids(root, args.date or _today(), args.ids, "skipped", note=args.note)


def cmd_edit(root: Path, args) -> int:
    # An edit records the requested change as a note and approves the action;
    # the skill applies the actual content change when it executes.
    return _mutate_ids(root, args.date or _today(), [args.id], "approved", note=args.note)


def cmd_set(root: Path, args) -> int:
    return _mutate_ids(root, args.date or _today(), [args.id], args.status, note=args.note)


# ============================================================
# Main / CLI
# ============================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="Persisted state machine for /email-intel recommended actions.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_prop = sub.add_parser("propose", help="seed the sweep from a JSON array of proposed actions")
    p_prop.add_argument("--file", required=True, help="path to JSON array of action dicts")
    p_prop.add_argument("--date", default=None)

    p_list = sub.add_parser("list", help="render the numbered action list")
    p_list.add_argument("--date", default=None)
    p_list.add_argument("--json", action="store_true")

    p_pend = sub.add_parser("pending", help="actions not yet done/skipped (resume set)")
    p_pend.add_argument("--date", default=None)
    p_pend.add_argument("--json", action="store_true")

    p_app = sub.add_parser("approve", help="approve action ids")
    p_app.add_argument("ids", nargs="+", type=int)
    p_app.add_argument("--date", default=None)

    p_skip = sub.add_parser("skip", help="skip action ids")
    p_skip.add_argument("ids", nargs="+", type=int)
    p_skip.add_argument("--date", default=None)
    p_skip.add_argument("--note", default=None)

    p_edit = sub.add_parser("edit", help="record an inline edit note + approve one action")
    p_edit.add_argument("id", type=int)
    p_edit.add_argument("--note", required=True)
    p_edit.add_argument("--date", default=None)

    p_set = sub.add_parser("set", help="stamp an execution outcome on one action")
    p_set.add_argument("id", type=int)
    p_set.add_argument("--status", required=True,
                       choices=["approved", "executing", "done", "failed", "skipped"])
    p_set.add_argument("--note", default=None)
    p_set.add_argument("--date", default=None)

    args = ap.parse_args()
    # Root state on the DATA overlay (not the engine clone) so sweep-actions-*.json
    # lands beside the rest of the email-intelligence data, never inside the engine
    # tree. get_data_root() resolves the private data repo; STATE_DIR is "outputs/...".
    root = get_data_root()
    dispatch = {
        "propose": cmd_propose, "list": cmd_list, "pending": cmd_pending,
        "approve": cmd_approve, "skip": cmd_skip, "edit": cmd_edit, "set": cmd_set,
    }
    try:
        return dispatch[args.cmd](root, args)
    except SweepUnreadable as e:
        # Read-only commands surface it here rather than each repeating the
        # handler; `propose` catches it itself, because it is the one command
        # that would otherwise WRITE over the file.
        print(f"{RED}sweep file unusable: {e}{RESET}", file=sys.stderr)
        return 2
    except ValueError as e:
        # `_valid_date` refuses anything that is not an exact YYYY-MM-DD.
        print(f"{RED}{e}{RESET}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
