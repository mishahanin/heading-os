#!/usr/bin/env python3
"""skill-trigger-test.py - LLM-judge regression test for the skill router.

This workspace routes natural-language messages to skills via a markdown RULE the model
interprets (`.claude/rules/skill-router.md`), NOT a callable function. So routing cannot
be unit-tested deterministically. This harness tests the rule AS IT ACTUALLY EXECUTES:
it feeds the router rules plus the target skill's own trigger description to a judge model,
asks whether a given query routes to that skill, and compares the verdict to the
`should_trigger` expectation in the skill's `triggers.json`.

Because the judge is a model, results are NON-DETERMINISTIC. This is an ADVISORY signal —
a `/push-updates` pre-flight check and an on-demand `/evaluate` option — never a hard
blocking CI gate. `--strict` makes it exit non-zero when a skill's pass rate falls below
`--threshold`, for callers that want a gate; the default is advisory (always exit 0 on a
completed run).

triggers.json shape (array of cases), per skill at .claude/skills/{name}/triggers.json:

    [
      { "query": "investigate ExampleTelco's leadership", "should_trigger": true },
      { "query": "validate this claim",               "should_trigger": false }
    ]

Usage:
  python scripts/skill-trigger-test.py --skill osint
  python scripts/skill-trigger-test.py --all
  python scripts/skill-trigger-test.py --all --json
  python scripts/skill-trigger-test.py --all --strict --threshold 0.9
  python scripts/skill-trigger-test.py --skill osint --model claude-haiku-4-5-20251001

Exit codes: 0 completed (advisory, or strict-pass), 1 strict-threshold breached,
2 setup error, 3 API/key error.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import get_workspace_root, load_env  # noqa: E402

ROOT = get_workspace_root()
SKILLS_DIR = ROOT / ".claude" / "skills"
ROUTER_RULE = ROOT / ".claude" / "rules" / "skill-router.md"

DEFAULT_MODEL = "claude-sonnet-4-6"
MODEL_ALIAS = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-8",
}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_triggers(skill_dir: Path) -> list[dict]:
    """Return the list of trigger cases for a skill, or [] if it has none."""
    path = skill_dir / "triggers.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must be a JSON array of cases")
    return data


def load_skill_description(skill_dir: Path) -> str:
    """Return the `description` frontmatter field of a skill's SKILL.md (best-effort)."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return ""
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    fm = text[3:end]
    # Capture `description:` possibly spanning until the next top-level key.
    lines = fm.splitlines()
    desc_lines: list[str] = []
    capturing = False
    for line in lines:
        if line.startswith("description:"):
            capturing = True
            desc_lines.append(line.split(":", 1)[1].strip())
            continue
        if capturing:
            # Continuation lines are indented; a new top-level key ends the field.
            if line and not line[0].isspace():
                break
            desc_lines.append(line.strip())
    return " ".join(d for d in desc_lines if d).strip()


def list_skills_with_triggers() -> list[str]:
    out = []
    for child in sorted(SKILLS_DIR.iterdir()):
        if child.is_dir() and (child / "triggers.json").exists():
            out.append(child.name)
    return out


# ---------------------------------------------------------------------------
# Changed-scope selection (for the /push-updates soft gate)
# ---------------------------------------------------------------------------

def _git_changed_files(base: str = "origin/main") -> set[str]:
    """Union of changed paths (POSIX, repo-relative) in the engine tree: committed
    `base..HEAD` (only if `base` resolves), working-tree edits, and untracked files.

    Degrades clearly: a missing/unresolvable `base` drops the committed diff and
    prints a note; a git failure yields an empty contribution, never an exception.
    """
    files: set[str] = set()

    def _run(args: list[str]) -> list[str]:
        try:
            out = subprocess.run(
                ["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=30
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if out.returncode != 0:
            return []
        return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]

    base_ok = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", base],
        cwd=str(ROOT), capture_output=True, text=True,
    ).returncode == 0
    if base_ok:
        files.update(_run(["diff", "--name-only", f"{base}..HEAD"]))
    else:
        print(f"{GRAY}routing-gate: base '{base}' unresolved; using working-tree diff only{RESET}")
    files.update(_run(["diff", "--name-only", "HEAD"]))
    files.update(_run(["ls-files", "--others", "--exclude-standard"]))
    return files


def changed_routing_skills(base: str = "origin/main") -> list[str]:
    """Skills whose routing surface changed since `base`. A change to the router
    rule widens scope to every skill with a triggers.json (the rule affects all)."""
    changed = _git_changed_files(base)
    if ".claude/rules/skill-router.md" in changed:
        return list_skills_with_triggers()
    skills: set[str] = set()
    for path in changed:
        parts = path.split("/")
        if (
            len(parts) >= 4
            and parts[0] == ".claude"
            and parts[1] == "skills"
            and parts[3] in ("SKILL.md", "triggers.json")
            and (SKILLS_DIR / parts[2] / "triggers.json").exists()
        ):
            skills.add(parts[2])
    return sorted(skills)


# ---------------------------------------------------------------------------
# Judge
# ---------------------------------------------------------------------------

JUDGE_INSTRUCTION = (
    "You are a routing oracle for a Claude Code workspace. Below are the workspace's "
    "skill-routing rules. Given a user message and a TARGET skill, decide whether the "
    "rules would route that message to the TARGET skill (as its primary skill or compound "
    "entrypoint). Judge strictly by the rules and the target skill's own trigger description "
    "— not by what you personally think is reasonable.\n\n"
    "Reply with ONLY a compact JSON object, no prose:\n"
    '{"routes_to_target": true|false, "skill": "<skill you think fires, or none>", '
    '"reason": "<one short clause>"}'
)


def build_system(router_rules: str, skill_name: str, skill_desc: str) -> str:
    desc = skill_desc or "(no description frontmatter found)"
    return (
        f"{JUDGE_INSTRUCTION}\n\n"
        f"=== TARGET SKILL ===\n/{skill_name}\nDescription: {desc}\n\n"
        f"=== WORKSPACE SKILL-ROUTING RULES ===\n{router_rules}"
    )


def judge_query(client, model: str, system: str, query: str, target: str) -> dict:
    """Ask the judge whether `query` routes to `target`. Returns the parsed verdict dict."""
    user = (
        f"User message: {query!r}\n"
        f"Does this route to /{target}? Answer with the JSON object only."
    )
    response = client.messages.create(
        model=model,
        max_tokens=300,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
    # Tolerate a fenced or chatty reply: extract the first {...} block.
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {"routes_to_target": None, "skill": "?", "reason": f"unparseable: {text[:80]}"}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {"routes_to_target": None, "skill": "?", "reason": f"bad json: {text[:80]}"}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_skill(client, model: str, router_rules: str, skill_name: str) -> dict:
    """Run all trigger cases for one skill. Returns a result dict."""
    skill_dir = SKILLS_DIR / skill_name
    cases = load_triggers(skill_dir)
    if not cases:
        return {"skill": skill_name, "cases": 0, "passed": 0, "results": [], "skipped": True}

    system = build_system(router_rules, skill_name, load_skill_description(skill_dir))
    results = []
    passed = 0
    for case in cases:
        query = case["query"]
        expected = bool(case["should_trigger"])
        verdict = judge_query(client, model, system, query, skill_name)
        got = verdict.get("routes_to_target")
        ok = (got is expected) if isinstance(got, bool) else False
        passed += ok
        results.append({
            "query": query,
            "expected": expected,
            "got": got,
            "ok": ok,
            "judged_skill": verdict.get("skill", "?"),
            "reason": verdict.get("reason", ""),
        })
    return {"skill": skill_name, "cases": len(cases), "passed": passed, "results": results, "skipped": False}


def print_skill_report(r: dict, threshold: float) -> None:
    if r["skipped"]:
        print(f"{YELLOW}skip{RESET}: {r['skill']} - no triggers.json")
        return
    rate = r["passed"] / r["cases"] if r["cases"] else 0.0
    color = GREEN if rate >= threshold else RED
    print(f"\n{BOLD}{CYAN}{r['skill']}{RESET}  {color}{r['passed']}/{r['cases']}{RESET} ({rate:.0%})")
    for res in r["results"]:
        if not res["ok"]:
            exp = "trigger" if res["expected"] else "NOT trigger"
            print(f"  {RED}MISS{RESET} {res['query']!r}")
            print(f"       expected {exp}; judge said skill={res['judged_skill']!r} ({res['reason']})")


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--skill", help="Skill name (directory under .claude/skills/)")
    g.add_argument("--all", action="store_true", help="Run every skill with a triggers.json")
    g.add_argument("--changed", action="store_true",
                   help="Run only skills whose SKILL.md/triggers.json changed since --base "
                        "(a skill-router.md change widens to all)")
    parser.add_argument("--base", default="origin/main",
                        help="Diff base for --changed (default origin/main)")
    parser.add_argument("--model", help="Judge model (haiku/sonnet/opus or full id)", default="sonnet")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON instead of text")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if a skill's pass rate < threshold")
    parser.add_argument("--threshold", type=float, default=0.9, help="Strict pass-rate threshold (default 0.9)")
    args = parser.parse_args(argv)

    if not ROUTER_RULE.exists():
        print(f"{RED}ERROR{RESET}: router rule not found at {ROUTER_RULE}", file=sys.stderr)
        return 2

    if args.skill:
        skills = [args.skill]
    elif args.changed:
        skills = changed_routing_skills(args.base)
        if not skills:
            # Empty scope returns BEFORE the key check / client build, so the gate
            # is cost-free (no API key required) on the common no-routing-change push.
            print(f"{GREEN}no routing-sensitive changes since {args.base}{RESET}")
            return 0
    else:
        skills = list_skills_with_triggers()
    if not skills:
        print(f"{YELLOW}No skills with triggers.json found{RESET}", file=sys.stderr)
        return 2

    # Degrade clearly per console-first rule: no key → plain message, non-zero, no hang.
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(f"{RED}ERROR{RESET}: ANTHROPIC_API_KEY not set; cannot run the LLM judge.", file=sys.stderr)
        return 3
    try:
        import anthropic
    except ImportError:
        print(f"{RED}ERROR{RESET}: anthropic SDK not installed.", file=sys.stderr)
        return 3
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = MODEL_ALIAS.get(args.model, args.model)

    router_rules = ROUTER_RULE.read_text(encoding="utf-8")
    t0 = time.time()
    reports = [run_skill(client, model, router_rules, name) for name in skills]
    elapsed = time.time() - t0

    active = [r for r in reports if not r["skipped"]]
    total_cases = sum(r["cases"] for r in active)
    total_passed = sum(r["passed"] for r in active)
    overall = total_passed / total_cases if total_cases else 0.0
    breached = [r for r in active if (r["passed"] / r["cases"] if r["cases"] else 0) < args.threshold]

    if args.json:
        print(json.dumps({
            "model": model,
            "elapsed_seconds": round(elapsed, 2),
            "overall_rate": round(overall, 4),
            "total_passed": total_passed,
            "total_cases": total_cases,
            "threshold": args.threshold,
            "strict": args.strict,
            "below_threshold": [r["skill"] for r in breached],
            "skills": reports,
        }, indent=2))
    else:
        for r in reports:
            print_skill_report(r, args.threshold)
        print(f"\n{BOLD}Overall: {total_passed}/{total_cases} ({overall:.0%})  "
              f"{GRAY}model={model} {elapsed:.1f}s{RESET}")
        if breached:
            print(f"{YELLOW}Below {args.threshold:.0%}: {', '.join(r['skill'] for r in breached)}{RESET}")
        if not args.strict:
            print(f"{GRAY}advisory run (pass --strict to gate on the threshold){RESET}")

    if args.strict and breached:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
