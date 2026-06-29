#!/usr/bin/env python3
"""crm_next.py -- Daily top-3 follow-up queue with drafts ready for approval.

Reads crm-health.py --json output, ranks RED contacts by pipeline stage tier
then days overdue, generates a checking-in email draft per candidate, saves
the queue to outputs/operations/crm/next-YYYY-MM-DD.md for CEO batch approval.

v0: drafts are presented for manual review + send via send-email.py.
Auto-send-on-approval is a Phase 3 follow-up.

Usage:
  python3 scripts/crm_next.py             # generate today's queue
  python3 scripts/crm_next.py --send 1 3  # (stub - v0 prints send instructions only)
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root, get_outputs_dir, get_crm_contacts_dir
from scripts.utils.colors import GREEN, YELLOW, RED, BOLD, RESET

_ENTRY_RE = re.compile(r"^(?:###\s+|-\s+)(\d{4}-\d{2}-\d{2}\b.*)$", re.MULTILINE)


STAGE_TIER = {
    "Negotiation": 1,
    "Proposal": 2,
    "Demo": 3,
    "Demo/POC": 3,    # accept both spellings
    "Qualified": 4,
    "Lead": 5,
    "": 6,
}


def rank_candidates(contacts: list, top_n: int = 3, today=None) -> list:
    """Rank RED contacts by (stage_tier, -days_overdue). Filters frozen contacts and non-REDs."""
    if today is None:
        today_date = date.today()
    else:
        today_date = date.fromisoformat(today)
    filtered = []
    for c in contacts:
        if c.get("health") != "red":
            continue
        freeze = c.get("radar_freeze_until", "") or ""
        if freeze:
            try:
                if date.fromisoformat(freeze) > today_date:
                    continue
            except (ValueError, TypeError):
                pass
        filtered.append(c)
    filtered.sort(key=lambda c: (
        STAGE_TIER.get(c.get("stage", ""), 6),
        -int(c.get("days_overdue", 0)),
    ))
    return filtered[:top_n]


def last_interaction_excerpt(contact_file_path: Path) -> str:
    """Read the most recent Interaction Log entry from a relationship record.

    Supports both heading-style (`### YYYY-MM-DD | ...`) and bullet-style
    (`- YYYY-MM-DD | ...`) entry formats. Returns the matched entry plus up
    to 3 following lines, capped at 4 lines total. Falls back to
    "(no prior interaction)" when no entry is found.
    """
    if not contact_file_path.exists():
        return "(no prior interaction)"
    text = contact_file_path.read_text(encoding="utf-8")
    if "## Interaction Log" not in text:
        return "(no prior interaction)"
    log = text.split("## Interaction Log", 1)[1]
    m = _ENTRY_RE.search(log)
    if not m:
        return "(no prior interaction)"
    start = m.start()
    rest = log[m.end():]
    next_m = _ENTRY_RE.search(rest)
    end = m.end() + (next_m.start() if next_m else len(rest))
    entry = log[start:end]
    lines = entry.strip().split("\n")[:4]
    return "\n".join(lines).strip()


def render_draft(contact: dict, last_excerpt: str) -> str:
    """Render a checking-in email draft using the /follow-up template shape.

    No manual sign-off in the body - the branded auto-signature (loaded by
    send-email.py from reference/email-signature.html) carries the sender's
    name and title. A sign-off here would double with that block.
    """
    name = contact.get("name", "there")
    days_overdue = contact.get("days_overdue", 0) or 0
    cadence = contact.get("cadence", 14) or 14
    # Total elapsed since last contact = cadence threshold + overdue beyond it
    days_since = days_overdue + cadence
    subject = "Quick check-in"

    body_lines = [
        f"Hey {name.split()[0] if name else 'there'},",
        "",
        f"Wanted to check back in - it's been {days_since} days since our last exchange.",
        "",
    ]
    # Only include the "most recent thread" block if we have real context
    has_prior = last_excerpt and not last_excerpt.startswith("(no prior")
    if has_prior:
        first_line = last_excerpt.split("\n")[0]
        body_lines.extend([
            "Most recent thread on my end:",
            f"> {first_line}",
            "",
        ])
    body_lines.append(
        "What's the right next step from here? Happy to push the conversation forward whenever the timing works."
    )
    # No sign-off in the body: the branded auto-signature carries the name +
    # title. Adding "Best, <Name>" here would double with the signature.
    return f"Subject: {subject}\n\n" + "\n".join(body_lines)


def generate_queue(today=None) -> Path:
    """Run crm-health.py --json, rank, generate drafts, save queue file."""
    ws = get_workspace_root()
    health_json = subprocess.run(
        ["python3", "scripts/crm-health.py", "--json"],
        capture_output=True, text=True, cwd=str(ws),
    )
    if health_json.returncode != 0:
        print(f"{RED}crm-health.py --json failed:{RESET}\n{health_json.stderr}", file=sys.stderr)
        sys.exit(1)
    contacts = json.loads(health_json.stdout)

    candidates = rank_candidates(contacts, top_n=3, today=today)

    today_str = today or date.today().isoformat()
    out_dir = get_outputs_dir() / "operations" / "crm"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"next-{today_str}.md"

    lines = [
        f"# CRM Next - {today_str}",
        "",
        f"Top {len(candidates)} priority follow-ups, drafts ready for manual review.",
        "",
        "**Note:** this file is regenerated on every `/crm next` invocation. If you edit it, the edits will be lost on the next run. Copy any edited drafts to a separate location before re-running.",
        "",
        "**v0 workflow (this build):** read the drafts below, copy the body of any you want to send, and run:",
        "",
        "```bash",
        "python3 scripts/send-email.py --to <recipient> --subject \"<subject>\" --body \"<body>\"",
        "```",
        "",
        "Auto-log fires on the send (Phase 1), so last_touch + interaction log update without further action.",
        "",
    ]

    for i, c in enumerate(candidates, start=1):
        contact_file = get_crm_contacts_dir() / c["file"]
        last_excerpt = last_interaction_excerpt(contact_file)
        draft = render_draft(c, last_excerpt)
        lines.append(f"## {i}. {c.get('name')} - {c.get('company', '')}")
        lines.append("")
        lines.append(f"- Stage: **{c.get('stage', '(no pipeline link)')}**")
        lines.append(f"- Days overdue: {c.get('days_overdue', '?')}")
        lines.append(f"- Last touch: {c.get('last_touch', '?')}")
        lines.append(f"- Email: `{c.get('email', '(missing)')}`")
        lines.append("")
        lines.append("### Most recent interaction")
        lines.append("```")
        lines.append(last_excerpt)
        lines.append("```")
        lines.append("")
        lines.append("### Draft")
        lines.append("```")
        lines.append(draft)
        lines.append("```")
        lines.append("")

    out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_file


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--send", nargs="+", type=int, help="Send drafts by index (after approval review)")
    args = parser.parse_args()

    if args.send:
        # v0: send is a manual step - print instructions
        print("To send approved drafts, copy the draft body from the queue file into send-email.py:")
        print("  python3 scripts/send-email.py --to <addr> --subject <subj> --body \"<body>\"")
        print("Auto-send wiring is a Phase 3 follow-up (separate task).")
        return

    out_file = generate_queue()
    print(f"{GREEN}Queue written: {out_file}{RESET}")
    print(f"Review the file and reply with approve/revise/skip commands.")


if __name__ == "__main__":
    main()
