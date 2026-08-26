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
  python scripts/skill-trigger-test.py --skill osint --model haiku

A case the judge never returns a usable verdict for is UNMEASURED, not a routing miss:
it is retried once, then counted in `errored` and excluded from the pass rate, so an
API hiccup or a more verbose judge cannot masquerade as a router regression.

Exit codes: 0 completed (advisory, or strict-pass), 1 strict-threshold breached or a
skill left unmeasured, 2 setup error, 3 API/key error.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import claude_models  # noqa: E402
from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.router_payload import (  # noqa: E402
    load_skill_description,
    load_triggers,
    router_rules_text,
    system_text,
    user_text,
)
from scripts.utils.workspace import get_workspace_root, load_env  # noqa: E402

ROOT = get_workspace_root()
SKILLS_DIR = ROOT / ".claude" / "skills"
ROUTER_RULE = ROOT / ".claude" / "rules" / "skill-router.md"
# F-5.2: the per-category exclusions/compound tables the judge needs to reason about
# should_trigger:false cases live here, not in the always-on router rule.
CATEGORY_DETAIL_DIR = ROOT / "reference" / "skill-router"


def load_full_router_rules() -> str:
    """The exact rule text the judge is sent.

    Delegates to `scripts.utils.router_payload`, which is the single place the
    outbound payload is built, so the nightly egress check cannot drift from what
    this script actually sends. Kept as a name here because it is the harness's
    published surface.
    """
    return router_rules_text()


def list_skills_with_triggers() -> list[str]:
    out = []
    for child in sorted(SKILLS_DIR.iterdir()):
        if child.is_dir() and (child / "triggers.json").exists():
            out.append(child.name)
    return out


# The judge runs on a family, never a version, so a new Sonnet judges routing
# the day it ships. `claude_models.resolve` passes an explicit model id through
# untouched for a caller reproducing an older judge run.
DEFAULT_FAMILY = "sonnet"


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

    # Through `_run`, not around it. This probe had no timeout and no
    # FileNotFoundError guard, so a machine without git on PATH -- or a hung
    # repo -- crashed the --changed gate with a traceback, past the very
    # "degrades clearly, never an exception" contract `_run` implements.
    base_ok = bool(_run(["rev-parse", "--verify", "--quiet", base]))
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
    if ".claude/rules/skill-router.md" in changed or any(
        c.startswith("reference/skill-router/") and c.endswith(".md") for c in changed
    ):
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

def build_system(router_rules: str, skill_name: str, skill_desc: str) -> str:
    """The system prompt, byte for byte, from the shared payload module."""
    return system_text(skill_name, skill_desc, router_rules)


def build_user(query: str, target: str) -> str:
    """The user message for one case, byte for byte, from the same module."""
    return user_text(query, target)


# Measured 2026-08-10, the first night the judge family resolved to Sonnet 5: the
# previous 300-token ceiling truncated the verdict JSON mid-`reason` string, and two
# replies came back empty. 47 cases across 32 skills scored as routing misses when the
# router had not changed at all - no commit had touched .claude/skills or .claude/rules
# since 2026-08-08. The ceiling is a property of the judge's verbosity, which moves
# whenever the family resolves to a newer model, so it is set well clear of the
# ~120-token verdict rather than trimmed to it.
JUDGE_MAX_TOKENS = 1000

# One retry, because an empty reply is transient weather and a second call is cheap
# next to mislabelling a case. A verdict that is still absent after the retry is
# reported as unmeasured, never as a routing miss.
JUDGE_ATTEMPTS = 2


def _parse_verdict(text: str) -> dict:
    """Parse one judge reply. `routes_to_target: None` means the judge did not answer."""
    # Tolerate a fenced or chatty reply: extract the first {...} block.
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {"routes_to_target": None, "skill": "?", "reason": f"unparseable: {text[:80]}"}
    try:
        # The slice above runs from the FIRST `{` to the LAST `}`, so it always
        # begins with `{`. json.loads on such a string yields a dict or raises;
        # it cannot yield a list or a scalar. An audit asked for an
        # `isinstance(parsed, dict)` guard here for the `[{"routes_to_target":
        # true}]` case -- but the slicing already strips the brackets off that,
        # and the guard was measurably unreachable. Recorded rather than added,
        # so the next reader does not re-derive it.
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {"routes_to_target": None, "skill": "?", "reason": f"bad json: {text[:80]}"}


def judge_query(client, model: str, system: str, query: str, target: str) -> dict:
    """Ask the judge whether `query` routes to `target`. Returns the parsed verdict dict.

    Retries once when the reply does not carry a usable verdict. The caller reads a
    non-boolean `routes_to_target` as "not measured", so this function never has to
    invent one.
    """
    user = build_user(query, target)
    verdict: dict = {}
    for _ in range(JUDGE_ATTEMPTS):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=JUDGE_MAX_TOKENS,
                system=[{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:  # noqa: BLE001 - any transport fault, reported not raised
            # UNMEASURED, not fatal. This call had no handler at all, so one
            # transient 529 mid-sweep raised through run_skill and threw away a
            # 96-skill `--all` run -- dozens of paid judge calls already made.
            # The caller already reads a non-boolean `routes_to_target` as "not
            # measured", which is exactly the right meaning here.
            verdict = {"routes_to_target": None, "skill": "?",
                       "reason": f"api error: {type(exc).__name__}: {exc}"[:160]}
            continue
        text = "".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()
        verdict = _parse_verdict(text)
        if isinstance(verdict.get("routes_to_target"), bool):
            return verdict
    return verdict


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_skill(client, model: str, router_rules: str, skill_name: str) -> dict:
    """Run all trigger cases for one skill. Returns a result dict.

    `cases` counts the cases the judge actually answered, so the rate measures the
    ROUTER. A case the judge never returned a verdict for is counted in `errored`
    and kept out of both numerator and denominator: it is no evidence either way,
    and scoring it as a miss is what turned a judge-model upgrade into a phantom
    38-point drop on /voss on 2026-08-10.
    """
    skill_dir = SKILLS_DIR / skill_name
    cases = load_triggers(skill_dir)
    if not cases:
        # Say which of the two it is. One message covered both, and "no
        # triggers.json" was a false statement of what was measured for the
        # skill whose triggers.json is present and empty.
        reason = ("triggers.json holds no cases"
                  if (skill_dir / "triggers.json").exists()
                  else "no triggers.json")
        return {"skill": skill_name, "cases": 0, "passed": 0, "errored": 0,
                "results": [], "skipped": True, "skip_reason": reason}

    system = build_system(router_rules, skill_name, load_skill_description(skill_dir))
    results = []
    passed = 0
    judged = 0
    errored = 0
    for case in cases:
        query = case["query"]
        expected = bool(case["should_trigger"])
        verdict = judge_query(client, model, system, query, skill_name)
        got = verdict.get("routes_to_target")
        if isinstance(got, bool):
            judged += 1
            ok = got is expected
            passed += ok
        else:
            errored += 1
            ok = None  # unmeasured, distinct from False
        results.append({
            "query": query,
            "expected": expected,
            "got": got,
            "ok": ok,
            "judged_skill": verdict.get("skill", "?"),
            "reason": verdict.get("reason", ""),
        })
    return {"skill": skill_name, "cases": judged, "passed": passed, "errored": errored,
            "results": results, "skipped": False, "skip_reason": ""}


def print_skill_report(r: dict, threshold: float) -> None:
    if r["skipped"]:
        print(f"{YELLOW}skip{RESET}: {r['skill']} - "
              f"{r.get('skip_reason', 'no triggers.json')}; nothing was measured")
        return
    rate = r["passed"] / r["cases"] if r["cases"] else 0.0
    errored = r.get("errored", 0)
    if r["cases"]:
        color = GREEN if rate >= threshold else RED
        head = f"{color}{r['passed']}/{r['cases']}{RESET} ({rate:.0%})"
    else:
        head = f"{YELLOW}unmeasured{RESET}"
    tail = f"  {YELLOW}{errored} unmeasured{RESET}" if errored and r["cases"] else ""
    print(f"\n{BOLD}{CYAN}{r['skill']}{RESET}  {head}{tail}")
    for res in r["results"]:
        if res["ok"] is None:
            print(f"  {YELLOW}NO VERDICT{RESET} {res['query']!r}  ({res['reason']})")
        elif not res["ok"]:
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
    parser.add_argument("--model", help="Judge model (haiku/sonnet/opus/fable or full id)", default="sonnet")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON instead of text")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if a skill's pass rate < threshold")
    parser.add_argument("--threshold", type=float, default=0.9, help="Strict pass-rate threshold (default 0.9)")
    args = parser.parse_args(argv)

    if not ROUTER_RULE.exists():
        print(f"{RED}ERROR{RESET}: router rule not found at {ROUTER_RULE}", file=sys.stderr)
        return 2

    if args.skill:
        if not (SKILLS_DIR / args.skill).is_dir():
            # A typo used to print one skip line and return 0, including under
            # --strict. There is no such skill to measure, which is a setup
            # error (exit 2), not a passing routing check.
            print(f"{RED}ERROR{RESET}: no skill directory "
                  f"{SKILLS_DIR / args.skill}", file=sys.stderr)
            return 2
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
    model = claude_models.resolve(args.model, default_family=DEFAULT_FAMILY)

    router_rules = load_full_router_rules()
    t0 = time.time()
    reports = [run_skill(client, model, router_rules, name) for name in skills]
    elapsed = time.time() - t0

    active = [r for r in reports if not r["skipped"]]
    total_cases = sum(r["cases"] for r in active)
    total_passed = sum(r["passed"] for r in active)
    total_errored = sum(r.get("errored", 0) for r in active)
    overall = total_passed / total_cases if total_cases else 0.0
    # A skill with no answered case is UNMEASURED, not failing. Folding it into
    # `breached` would report a dead judge as a routing regression, which is the
    # confusion this whole split exists to end.
    breached = [r for r in active if r["cases"] and (r["passed"] / r["cases"]) < args.threshold]
    # Over `reports`, not `active`. A skipped skill measured nothing either, and
    # dropping it here is what let `--skill osnit-typo --strict` and an empty
    # triggers.json both exit 0 - a clean routing check that judged nothing.
    unmeasured = [r for r in reports if r["skipped"] or not r["cases"]]

    if args.json:
        print(json.dumps({
            "model": model,
            "elapsed_seconds": round(elapsed, 2),
            "overall_rate": round(overall, 4),
            "total_passed": total_passed,
            "total_cases": total_cases,
            "total_errored": total_errored,
            "threshold": args.threshold,
            "strict": args.strict,
            "below_threshold": [r["skill"] for r in breached],
            "unmeasured": [r["skill"] for r in unmeasured],
            "skills": reports,
        }, indent=2))
    else:
        for r in reports:
            print_skill_report(r, args.threshold)
        print(f"\n{BOLD}Overall: {total_passed}/{total_cases} ({overall:.0%})  "
              f"{GRAY}model={model} {elapsed:.1f}s{RESET}")
        if total_errored:
            print(f"{YELLOW}{total_errored} case(s) had no judge verdict and are "
                  f"excluded from the rate{RESET}")
        if breached:
            print(f"{YELLOW}Below {args.threshold:.0%}: {', '.join(r['skill'] for r in breached)}{RESET}")
        if unmeasured:
            print(f"{YELLOW}Unmeasured (no cases to judge, or no judge verdict): "
                  f"{', '.join(r['skill'] for r in unmeasured)}{RESET}")
        if not args.strict:
            print(f"{GRAY}advisory run (pass --strict to gate on the threshold){RESET}")

    # Strict means "gate on a clean measurement". A skill nobody could measure is
    # not a clean measurement, so it fails the gate too - loudly and by its own name.
    if args.strict and (breached or unmeasured):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
