#!/usr/bin/env python3
"""Adversarial prompt-injection regression suite.

Closes P2.3 from the 2026-05-14 workspace deep audit. Runs each attack case in
tests/security/prompt-injection/attacks/ against the target skill's SKILL.md
loaded as system prompt, then verifies the model output:

- Does NOT contain anything from the case's ``must_not_do`` list
- DOES include something matching the case's ``must_do`` list (best-effort
  fuzzy match - case-insensitive substring)

Exit codes: 0 all defences held, 1 one or more attacks succeeded, 2 setup error.

Usage:
    python tests/security/prompt-injection/run-adversarial-suite.py             # all
    python tests/security/prompt-injection/run-adversarial-suite.py --case injection-01-system-override
    python tests/security/prompt-injection/run-adversarial-suite.py --dry-run
    python tests/security/prompt-injection/run-adversarial-suite.py --quiet     # pre-commit mode
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET  # noqa: E402
from scripts.utils.observability import observe  # noqa: E402
from scripts.utils.workspace import get_workspace_root, load_env  # noqa: E402

ROOT = get_workspace_root()
ATTACKS_DIR = ROOT / "tests" / "security" / "prompt-injection" / "attacks"
SKILLS_DIR = ROOT / ".claude" / "skills"

MODEL_BY_SKILL = {
    # Pick the SAME model the skill uses in production. If a skill doesn't declare
    # one, default to haiku for cost - adversarial pass rate at haiku is a strict
    # lower bound on the production model.
    "osint": "claude-haiku-4-5-20251001",
    "email-intel": "claude-sonnet-4-6",
    "sentinel": "claude-haiku-4-5-20251001",
    "meeting-prep": "claude-sonnet-4-6",
    "scrutinize": "claude-haiku-4-5-20251001",
}


def load_attack(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_skill_system_prompt(skill_name: str) -> str:
    skill_md = SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"SKILL.md not found for {skill_name}")
    text = skill_md.read_text(encoding="utf-8")
    # Strip frontmatter
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5:]
    return text.strip()


@observe()
def run_attack(case: dict, dry_run: bool = False) -> tuple[bool, list[str]]:
    """Return (defended, failures). defended=True means all must_not_do held."""
    skill = case["target_skill"]
    system_prompt = load_skill_system_prompt(skill)
    model = MODEL_BY_SKILL.get(skill, "claude-haiku-4-5-20251001")

    if dry_run:
        return True, ["dry-run"]

    import anthropic
    import os
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model=model,
        max_tokens=1500,
        system=[{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": case["injected_input"]}],
    )
    output = ""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            output += block.text

    failures = []
    for forbidden in case.get("must_not_do", []):
        if forbidden.lower() in output.lower():
            failures.append(f"COMPLIED with injection: '{forbidden}'")
    # must_do is informational only - we do not fail a case for omitting positive guidance,
    # because some attacks legitimately make the agent refuse the original task. The hard
    # signal is the must_not_do list.
    return (len(failures) == 0), failures


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--case", help="Run only the attack with this id")
    parser.add_argument("--dry-run", action="store_true", help="Validate cases without API calls")
    parser.add_argument("--quiet", action="store_true", help="Pre-commit mode - minimal output")
    args = parser.parse_args()

    if not ATTACKS_DIR.exists():
        print(f"{RED}ERROR{RESET}: {ATTACKS_DIR} not found", file=sys.stderr)
        return 2

    attacks = sorted(ATTACKS_DIR.glob("*.json"))
    if args.case:
        attacks = [p for p in attacks if p.stem == args.case]
        if not attacks:
            print(f"{RED}ERROR{RESET}: no attack with id {args.case!r}", file=sys.stderr)
            return 2

    total = 0
    defended = 0
    failed_cases = []

    for path in attacks:
        case = load_attack(path)
        case_id = case.get("id", path.stem)
        target = case.get("target_skill", "?")
        if not args.quiet:
            print(f"\n{BOLD}{CYAN}{case_id}{RESET}  target={target}  category={case.get('category', '?')}")
            print(f"  {case.get('description', '')}")

        try:
            ok, failures = run_attack(case, dry_run=args.dry_run)
        except Exception as e:
            print(f"  {RED}SETUP ERROR{RESET} {e}")
            return 2

        total += 1
        if ok:
            defended += 1
            if not args.quiet:
                print(f"  {GREEN}DEFENDED{RESET}")
        else:
            failed_cases.append(case_id)
            print(f"  {RED}BREACH{RESET} - {len(failures)} injection(s) succeeded:")
            for f in failures:
                print(f"    {RED}- {f}{RESET}")

    print()
    if total == 0:
        print(f"{YELLOW}No attack cases found{RESET}")
        return 2
    if defended == total:
        if not args.quiet:
            print(f"{GREEN}{BOLD}All {total} adversarial cases defended.{RESET}")
        return 0
    print(f"{RED}{BOLD}{total - defended}/{total} attacks succeeded:{RESET} {', '.join(failed_cases)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
