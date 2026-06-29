#!/usr/bin/env python3
"""eval-flag.py - one-keystroke "this output was wrong" capture (R13).

Turns a bad output into a versioned DRAFT regression case, keyed by the R12
trace ID, in one command. Two paths:

  eval-flag.py <id-or-prefix>            resolve a live Action Queue card over the
                                          bridge loopback and stage a draft from it.
  eval-flag.py --skill NAME --note "..."  fully offline; stage a draft from a note
            [--input-file F] [--type ...]  (and optional input file). No daemon needed.
  eval-flag.py --list                     list staged drafts across all skills.

Drafts are written to ``.claude/skills/{name}/evals/outcomes/_staged/`` and are
INERT + gitignored (``**/evals/outcomes/_staged/``): no runner globs ``_staged/``
(run-skill-eval globs evals/cases/*.json, eval-outcomes globs
evals/outcomes/*.json, top-level only), and being untracked a draft can never be
shipped by publish-corporate (tracked-only) even if staged under a corporate
skill dir. Suite entry stays CEO-gated - the CEO edits the draft, then moves it
up into ``evals/outcomes/`` and ``git add``s it (plain ``mv`` + ``git add``, not
``git mv`` - the source is gitignored). This NEVER writes into live
``evals/outcomes/`` or ``evals/cases/`` (matches eval-case-template.md Phase 4.5).

Console-first and offline-first: the loopback is used ONLY to resolve a live
card's content; the offline path always works with the browser/daemon down.

Exit codes: 0 ok, 1 usage error, 2 daemon not reachable (only on the <id> path).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import trace
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET
from scripts.utils.workspace import get_workspace_root

ROOT = get_workspace_root()
SKILLS_DIR = ROOT / ".claude" / "skills"
# Live Action Queue cards come from the email/CRM/comms spine, which is CEO-only;
# drafts captured from a card default here so they land under the ceo-only
# .claude/skills/email-intel/evals/ subtree. Override with --skill.
DEFAULT_CARD_SKILL = "email-intel"


# ============================================================
# Loopback client (mirror scripts/action-queue.py)
# ============================================================

def _read_state(root: Path, name: str) -> str | None:
    p = root / ".daemon-state" / name
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _die_no_daemon() -> None:
    print(f"{RED}bridge daemon not reachable{RESET} - is it running? "
          f"(check .daemon-state/port, run scripts/bridge-daemon.py --start)", file=sys.stderr)
    sys.exit(2)


def _request(method: str, path: str, token: str, port: str, body: dict | None = None) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # subclass of URLError - catch first
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail", "")
        except Exception as exc:  # noqa: BLE001 - best-effort detail extraction
            print(f"eval-flag: could not parse error detail: {exc}", file=sys.stderr)
        print(f"{RED}HTTP {e.code}{RESET} {detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError:
        _die_no_daemon()


def _resolve_id(items: list[dict], prefix: str) -> str:
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


# ============================================================
# Draft staging
# ============================================================

def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:40] or "flagged")


def _staged_dir(skill: str) -> Path:
    return SKILLS_DIR / skill / "evals" / "outcomes" / "_staged"


def _stage_draft(skill: str, draft: dict) -> Path:
    """Atomically write a draft JSON into the skill's _staged/ dir."""
    staged = _staged_dir(skill)
    staged.mkdir(parents=True, exist_ok=True)
    path = staged / f"{draft['id']}.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _new_draft(description: str, input_text: str, trace_id: str, source: str,
               case_type: str) -> dict:
    ts = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    draft: dict = {
        "id": f"flag-{ts}-{_slugify(description)}",
        "description": description or "(untitled)",
        "input": input_text,
        "trace_id": trace_id,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "status": "draft",
    }
    if case_type == "prose":
        draft["checks"] = {"must_mention": [], "must_not_mention": []}
    else:
        draft["outcome"] = {
            "type": "TODO (crm_log | doctype_render)",
            "note": "Fill in the expected side-effect, then move this file up into evals/outcomes/ and git add it.",
        }
    return draft


# ============================================================
# Commands
# ============================================================

def cmd_list(as_json: bool) -> int:
    drafts: list[dict] = []
    for staged in SKILLS_DIR.glob("*/evals/outcomes/_staged"):
        skill = staged.parent.parent.parent.name
        for f in sorted(staged.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                d = {}
            drafts.append({
                "skill": skill,
                "path": str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f),
                "id": d.get("id", f.stem),
                "description": d.get("description", ""),
                "trace_id": d.get("trace_id", "-"),
            })
    if as_json:
        print(json.dumps(drafts, indent=2, ensure_ascii=False))
        return 0
    if not drafts:
        print(f"{GRAY}no staged eval drafts{RESET}")
        return 0
    print(f"{BOLD}{len(drafts)} staged eval draft(s){RESET}")
    for d in drafts:
        print(f"  {CYAN}{d['skill']}{RESET}  {d['id']}  {GRAY}trace={d['trace_id']}{RESET}")
        print(f"      {d['description']}")
        print(f"      {GRAY}{d['path']}{RESET}")
    return 0


def cmd_from_card(card_id: str, skill: str | None, case_type: str, as_json: bool) -> int:
    token = _read_state(ROOT, "token")
    port = _read_state(ROOT, "port")
    if not token or not port:
        _die_no_daemon()
    d = _request("GET", "/action-queue", token, port)
    items = d.get("items", [])
    aid = _resolve_id(items, card_id)
    card = next(c for c in items if c.get("id") == aid)

    # Defensive field mapping: title is the only guaranteed human field; the
    # rest are email_send-only and absent on note/alert/pipeline_update cards.
    description = card.get("title") or "(untitled)"
    parts = []
    for key in ("subject", "draft_body", "note"):
        val = card.get(key)
        if val:
            parts.append(f"{key}: {val}")
    input_text = "\n".join(parts) if parts else description
    recipient = card.get("to")
    if recipient:
        input_text = f"to: {recipient}\n{input_text}"
    trace_id = card.get("trace_id") or trace.get() or "-"
    target_skill = skill or DEFAULT_CARD_SKILL

    draft = _new_draft(description, input_text, trace_id,
                       source=f"action-queue-card:{aid}", case_type=case_type)
    draft["card_action_type"] = card.get("action_type", "")
    path = _stage_draft(target_skill, draft)
    if as_json:
        print(json.dumps({"staged": str(path.relative_to(ROOT)), "skill": target_skill,
                          "id": draft["id"], "trace_id": trace_id}, indent=2))
    else:
        print(f"{GREEN}staged{RESET} draft from card {aid[:8]} -> "
              f"{GRAY}{path.relative_to(ROOT)}{RESET}  (trace {trace_id})")
        print(f"  edit it, then mv into evals/outcomes/ + git add to promote (CEO-gated).")
    return 0


def cmd_offline(skill: str, note: str, input_file: str | None, case_type: str,
                as_json: bool) -> int:
    input_text = note
    if input_file:
        try:
            input_text = Path(input_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"{RED}cannot read --input-file{RESET}: {e}", file=sys.stderr)
            return 1
    trace_id = trace.get() or "-"
    draft = _new_draft(note, input_text, trace_id, source="manual", case_type=case_type)
    path = _stage_draft(skill, draft)
    if as_json:
        print(json.dumps({"staged": str(path.relative_to(ROOT)), "skill": skill,
                          "id": draft["id"], "trace_id": trace_id}, indent=2))
    else:
        print(f"{GREEN}staged{RESET} draft -> {GRAY}{path.relative_to(ROOT)}{RESET}  "
              f"(trace {trace_id})")
        print(f"  edit it, then mv into evals/outcomes/ + git add to promote (CEO-gated).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Capture a flagged bad output as a draft regression case (R13).")
    ap.add_argument("id", nargs="?", help="Action Queue card id-or-prefix (live-card path)")
    ap.add_argument("--skill", help="target skill dir (required for the offline path; "
                                    "overrides the default for the card path)")
    ap.add_argument("--note", help="what was wrong (offline path)")
    ap.add_argument("--input-file", help="file whose contents are the case input (offline path)")
    ap.add_argument("--type", choices=["outcome", "prose"], default="outcome",
                    help="draft case shape (default: outcome)")
    ap.add_argument("--list", action="store_true", help="list staged drafts")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if args.list:
        return cmd_list(args.json)
    if args.id:
        return cmd_from_card(args.id, args.skill, args.type, args.json)
    if args.skill and args.note:
        return cmd_offline(args.skill, args.note, args.input_file, args.type, args.json)

    print(f"{RED}usage error{RESET}: give a card <id>, or --skill NAME --note \"...\", "
          f"or --list", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
