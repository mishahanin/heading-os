#!/usr/bin/env python3
"""The night batch: approve open findings by day, repair them while nobody works.

Usage:
    python scripts/night-repair.py --status
    python scripts/night-repair.py --approve            # everything open
    python scripts/night-repair.py --approve --max-minutes 180
    python scripts/night-repair.py --run                # the timer calls this

What runs at night, and what cannot
-----------------------------------
`--run` starts a headless Claude Code session over an approved batch. Three
properties bound it, and each is asserted by a test rather than promised here:

1. **It cannot commit and it cannot push.** The prompt it passes carries no word
   from the release gate's authorising lists, so `check_release_gate` in
   `.claude/hooks/_dispatch.py` refuses any commit, tag, push or publish the
   session attempts. That is deliberate, and it is why the night is safe: an
   authorising word placed in a machine-written prompt would let a program grant
   itself the operator's permission, which is the exact defect the gate was
   built for after a release went out on an approval nobody had given.
2. **It cannot run twice.** The batch is consumed before the session starts, so
   a crash, a reboot or a second timer fire finds nothing to do.
3. **It cannot approve itself.** `--approve` writes the batch, and only the
   operator runs it. `--run` refuses when no approved batch is waiting.

The morning is the acceptance
-----------------------------
The session leaves a working tree the operator reads. It does not mark anything
`fixed` in the rotation ledger either: a repair is recorded as fixed once its
evidence exists (a test that was red, mutations caught) and the operator accepts
it. An agent that both repairs and certifies its own repair is marking its own
homework, which is the shape this whole standard exists to remove.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import get_default_tz, get_workspace_root  # noqa: E402

ROOT = get_workspace_root()
BATCH_PATH = ROOT / ".claude" / "state" / "night-batch.json"
LOG_DIR = ROOT / ".claude" / "state" / "night-repair"
HOLD_PATH = ROOT / "config" / "automation-hold.json"

# The session's own budget. A night is long, but not unbounded, and a run that
# never returns is a run nobody notices failed.
DEFAULT_TIMEOUT_S = 6 * 3600


def _now() -> str:
    return datetime.now(get_default_tz()).isoformat(timespec="seconds")


def hold_reason(path: Path, today: str) -> str | None:
    """Why nothing may run today, or None when the freeze has lapsed.

    A dated freeze the operator sets by hand. It exists because a hold an
    assistant merely REMEMBERS is not a hold: the release that went out on
    2026-08-31 was a permission remembered rather than read, and this is the
    same shape pointed the other way. A date on disk survives a compaction, a
    new session, and a different assistant.

    An unreadable or malformed file HOLDS rather than lapses. Absent is the only
    state that means "no freeze", because absent is the only one that carries
    the operator's intent; a corrupt file carries no intent at all and the safe
    reading of no intent is to do nothing.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"the hold file is unreadable ({exc}); refusing rather than guessing"
    if not isinstance(data, dict):
        return "the hold file is malformed; refusing rather than guessing"
    until = data.get("hold_until")
    if not isinstance(until, str) or not until:
        return "the hold file names no date; refusing rather than guessing"
    if today >= until:
        return None
    return (f"held until {until} ({data.get('reason') or 'no reason recorded'}); "
            f"today is {today}")


def _rotation():
    """The rotation module, imported by path (its filename is kebab-case)."""
    import importlib.util

    path = ROOT / "scripts" / "audit-rotation.py"
    spec = importlib.util.spec_from_file_location("audit_rotation_for_night", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ============================================================
# The batch
# ============================================================

def build_batch(findings, max_minutes: int | None) -> dict:
    """The approved work, in severity order, cut at the time budget.

    The budget STOPS the batch, it does not pack it. Skipping an item that does
    not fit and taking a smaller one behind it uses the night better and makes
    the batch stop being the top of the list, which is the property the operator
    reads it for: what got approved is the most severe work, in order, cut where
    the time ran out.

    One carve-out. A finding whose own estimate exceeds the whole budget is
    still taken when it is first, because otherwise the largest defect can never
    be scheduled and quietly outlives every night.
    """
    items = []
    spent = 0
    for rel, finding in findings:
        minutes = int(finding.get("estimate_minutes") or 0)
        if max_minutes is not None and items and spent + minutes > max_minutes:
            break
        items.append({"path": rel, **finding})
        spent += minutes
    return {"approved_at": _now(), "consumed_at": None,
            "estimated_minutes": spent, "items": items}


def load_batch(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # Never swallow: an unreadable batch is a fault. Reading it as "no batch"
        # would turn a corrupt file into a silent no-op night after night.
        print(f"{RED}batch unreadable: {exc}{RESET}", file=sys.stderr)
        raise SystemExit(2) from exc
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        print(f"{RED}batch malformed: expected an object with an items list"
              f"{RESET}", file=sys.stderr)
        raise SystemExit(2)
    return data


def save_batch(path: Path, batch: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(batch, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def is_pending(batch: dict | None) -> bool:
    """An approved batch nobody has started yet."""
    return bool(batch) and batch.get("consumed_at") is None and bool(batch["items"])


# ============================================================
# The prompt
# ============================================================

def build_prompt(batch: dict) -> str:
    """The instruction the night session receives.

    Carries NO authorising word. `tests/` asserts that against the release
    gate's own lists, imported from the hook rather than copied, so a word added
    there tomorrow is checked here tonight.
    """
    lines = [
        "Unattended repair pass. The operator approved this batch during the "
        "day and is asleep. Work through it and stop.",
        "",
        "For each item: open the current file first and reproduce the defect. "
        "If it does not reproduce, say so and leave the file alone. If it does, "
        "write the test that fails on it BEFORE the fix, watch it fail, apply "
        "the fix, watch it pass, then mutation-verify with "
        "scripts/utils/mutation_harness.py and state the fraction caught.",
        "",
        "Follow .claude/rules/development-standards.md, section 'The Evidence "
        "Standard'. Every obligation in it applies to this pass.",
        "",
        "Leave the working tree dirty. Do not stage anything, do not create a "
        "branch, do not touch the rotation ledger. The operator reads the diff "
        "in the morning and decides.",
        "",
        f"The batch ({len(batch['items'])} item(s), "
        f"{batch['estimated_minutes']} minutes estimated):",
    ]
    for item in batch["items"]:
        lines.append(f"- {item['path']}: {item['summary']} "
                     f"[{item['severity']}, ~{item['estimate_minutes']}m]")
    lines += [
        "",
        "Write what you did to .claude/state/night-repair/<date>.md: per item, "
        "reproduced or not, the test file, the mutation fraction, and anything "
        "you left alone with the reason.",
    ]
    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

def cmd_status(batch: dict | None) -> int:
    if not batch:
        print(f"{GRAY}no batch approved{RESET}")
        return 0
    state = "pending" if is_pending(batch) else f"consumed {batch['consumed_at']}"
    print(f"{BOLD}night batch{RESET}  {len(batch['items'])} item(s), "
          f"{batch['estimated_minutes']}m estimated, approved "
          f"{batch['approved_at']}, {state}")
    for item in batch["items"]:
        print(f"  {CYAN}{item['path']}{RESET}  {item['summary']}")
    return 0


def cmd_approve(max_minutes: int | None) -> int:
    rot = _rotation()
    current = rot.inventory(ROOT)
    entries = rot.load_ledger(rot.LEDGER_PATH)
    findings = rot.open_findings(entries, current)
    if not findings:
        print(f"{GREEN}nothing open to approve{RESET}")
        return 0

    batch = build_batch(findings, max_minutes)
    save_batch(BATCH_PATH, batch)
    print(f"{GREEN}approved{RESET} {len(batch['items'])} item(s), "
          f"{batch['estimated_minutes']}m estimated")
    for item in batch["items"]:
        print(f"  {CYAN}{item['path']}{RESET}  {item['summary']}")
    print(f"\n{GRAY}The night run will not commit or push. The tree is dirty in "
          f"the morning and the decision is yours.{RESET}")
    return 0


def cmd_run(timeout_s: int) -> int:
    held = hold_reason(HOLD_PATH, _now()[:10])
    if held:
        print(f"{YELLOW}HELD{RESET} {held}", file=sys.stderr)
        return 0

    batch = load_batch(BATCH_PATH)
    if not is_pending(batch):
        print(f"{GRAY}no approved batch pending; nothing to do{RESET}")
        return 0

    claude = shutil.which("claude")
    if not claude:
        print(f"{RED}claude CLI not on PATH; the night run cannot start{RESET}",
              file=sys.stderr)
        return 2

    # Consumed BEFORE the session starts. A crash, a reboot or a second timer
    # fire then finds nothing pending, rather than repeating a half-done pass
    # over a tree the first one already changed.
    batch["consumed_at"] = _now()
    save_batch(BATCH_PATH, batch)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = LOG_DIR / f"{_now()[:10]}-session.log"
    prompt = build_prompt(batch)

    print(f"{BOLD}starting the night pass{RESET}  {len(batch['items'])} item(s)")
    try:
        proc = subprocess.run([claude, "-p", prompt], cwd=ROOT,
                              capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        log.write_text(f"TIMED OUT after {timeout_s}s\n", encoding="utf-8")
        print(f"{RED}the night session exceeded {timeout_s}s and was stopped"
              f"{RESET}", file=sys.stderr)
        return 2
    except OSError as exc:
        log.write_text(f"FAILED TO START: {exc}\n", encoding="utf-8")
        print(f"{RED}the night session did not start: {exc}{RESET}",
              file=sys.stderr)
        return 2

    log.write_text(f"exit {proc.returncode}\n\n{proc.stdout}\n\n"
                   f"--- stderr ---\n{proc.stderr}\n", encoding="utf-8")
    print(f"{GREEN}night pass finished{RESET} exit {proc.returncode}; "
          f"transcript at {log}")
    return 0 if proc.returncode == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--approve", action="store_true",
                    help="approve the open findings as tonight's batch")
    ap.add_argument("--max-minutes", type=int, default=None,
                    help="time budget for the batch")
    ap.add_argument("--run", action="store_true",
                    help="run the approved batch; the timer calls this")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    args = ap.parse_args()

    if args.approve:
        return cmd_approve(args.max_minutes)
    if args.run:
        return cmd_run(args.timeout)
    return cmd_status(load_batch(BATCH_PATH))


if __name__ == "__main__":
    sys.exit(main())
