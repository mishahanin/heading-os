#!/usr/bin/env python3
"""run-skill-eval.py - Skill eval runner for workspace .claude/skills/{name}/evals/.

Closes the eval-coverage gap identified by the 2026-05-14 workspace deep audit (P1.1):
detects regression when a model update or skill edit silently degrades a skill's output.

Eval structure for each covered skill:

    .claude/skills/{name}/evals/
      README.md           - pattern documentation
      cases/              - one .json file per test case (3-7 cases per skill)
        case-1-*.json
        case-2-*.json
        ...
      benchmark.json      - baseline + most recent run result

Case file format (each case is a self-contained test):

    {
      "id": "case-1-short-slug",
      "description": "What this case tests, one sentence",
      "input": "User prompt that triggers the skill output",
      "checks": {
        "must_mention": ["substring", "another"],
        "must_not_mention": ["banned-term"],
        "min_words": 80,
        "max_words": 250,
        "hidden_chars_clean": true
      }
    }

Usage:
    python scripts/run-skill-eval.py --skill linkedin-post
    python scripts/run-skill-eval.py --skill linkedin-post --case case-1-sovereign-data
    python scripts/run-skill-eval.py --skill linkedin-post --dry-run
    python scripts/run-skill-eval.py --skill linkedin-post --no-write
    python scripts/run-skill-eval.py --all                   # every skill with evals/

Exit codes: 0 all checks passed, 1 one or more failed, 2 setup error, 3 API error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import claude_models  # noqa: E402
from scripts.utils.atomic import atomic_write_text  # noqa: E402
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET  # noqa: E402
from scripts.utils.observability import observe  # noqa: E402
from scripts.utils.workspace import get_workspace_root, load_env  # noqa: E402

ROOT = get_workspace_root()
SKILLS_DIR = ROOT / ".claude" / "skills"


# ---------------------------------------------------------------------------
# Case loading
# ---------------------------------------------------------------------------

def load_cases(skill_dir: Path, case_filter: str | None = None) -> list[dict]:
    """Return a list of case dicts from skill's evals/cases/ directory."""
    cases_dir = skill_dir / "evals" / "cases"
    if not cases_dir.exists():
        return []
    cases = []
    for path in sorted(cases_dir.glob("*.json")):
        with path.open("r", encoding="utf-8") as fh:
            case = json.load(fh)
        case["_path"] = str(path.relative_to(ROOT))
        if case_filter and case.get("id") != case_filter:
            continue
        cases.append(case)
    return cases


def load_skill_system_prompt(skill_dir: Path) -> tuple[str, dict]:
    """Return (skill body as system prompt, frontmatter dict).

    Strip YAML frontmatter and use the rest of SKILL.md as the system context.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"SKILL.md not found in {skill_dir}")
    text = skill_md.read_text(encoding="utf-8")
    frontmatter: dict = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            fm_raw = text[4:end]
            body = text[end + 5:]
            # Cheap line-based parse - good enough for `model:` and `metadata.version`
            for line in fm_raw.splitlines():
                if ":" in line and not line.startswith((" ", "-")):
                    k, v = line.split(":", 1)
                    frontmatter[k.strip()] = v.strip().strip('"').strip("'")
    return body.strip(), frontmatter


# ---------------------------------------------------------------------------
# Deterministic checks
# ---------------------------------------------------------------------------

def term_pattern(term: str) -> re.Pattern:
    """Match a check term as a WORD, not as a substring.

    Plain `term.lower() in output.lower()` made short terms meaningless. The
    2026-08-23 audit caught the worst case: `brain-audit/case-3-boundaries`
    asserts `must_mention: ["no"]`, and "no" is a substring of "not", "know",
    "cannot", "note" and "another". Any answer of the required 30 words passed
    it, including one that got the boundaries exactly wrong. Twenty-two more
    terms of four characters or fewer sit in the corpus behind the same
    weakness ("add" in "address", "log" in "login", "new" in "renew").

    A boundary is added only where the term's own edge is a word character, so
    the twelve terms that start or end with punctuation or an emoji still match:
    `$350,000`, `.workspace-identity.json`, `.jsonl`, `Hi there!`, `\U0001F680`.
    """
    escaped = re.escape(term)
    left = r"\b" if re.match(r"\w", term) else ""
    right = r"\b" if re.search(r"\w$", term) else ""
    return re.compile(left + escaped + right, re.IGNORECASE)


def any_match(output: str, term) -> bool:
    """True when `term` is present. A LIST of terms means "any of these".

    Word-boundary matching is strict about inflection: `\\bpersist\\b` does not
    find "persistence". Rather than loosen the matcher back into substring
    mush, a check term may be a list of accepted spellings::

        "must_mention": [["persist", "persistence", "persisting"], "daemon"]

    A bare string still behaves exactly as before.
    """
    terms = term if isinstance(term, list) else [term]
    return any(term_pattern(t).search(output) for t in terms)


def run_checks(output: str, checks: dict) -> list[dict]:
    """Apply check specifications against the model output. Returns list of results."""
    results = []

    must_mention = checks.get("must_mention", [])
    for term in must_mention:
        passed = any_match(output, term)
        results.append({
            "check": f"must_mention[{term!r}]",
            "passed": passed,
            "detail": "" if passed else f"missing {term!r}",
        })

    must_not_mention = checks.get("must_not_mention", [])
    for term in must_not_mention:
        passed = not any_match(output, term)
        results.append({
            "check": f"must_not_mention[{term!r}]",
            "passed": passed,
            "detail": "" if passed else f"contains banned {term!r}",
        })

    word_count = len(output.split())
    if "min_words" in checks:
        passed = word_count >= checks["min_words"]
        results.append({
            "check": f"min_words>={checks['min_words']}",
            "passed": passed,
            "detail": f"got {word_count}",
        })
    if "max_words" in checks:
        passed = word_count <= checks["max_words"]
        results.append({
            "check": f"max_words<={checks['max_words']}",
            "passed": passed,
            "detail": f"got {word_count}",
        })

    if checks.get("hidden_chars_clean"):
        # Quick check for the most common offenders
        banned = ["\u200b", "\u200c", "\u200d", "\u00ad", "\u00a0", "\u2060", "\ufeff"]
        found = [hex(ord(c)) for c in output if c in banned]
        passed = not found
        results.append({
            "check": "hidden_chars_clean",
            "passed": passed,
            "detail": "" if passed else f"found {found[:3]}",
        })

    return results


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

@observe()
def call_skill(system_prompt: str, user_input: str, model: str) -> tuple[str, dict, float]:
    """Invoke the skill via Anthropic API. Returns (output_text, usage_dict, elapsed_seconds)."""
    import anthropic  # lazy import - keeps --dry-run runnable without SDK installed

    import os
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set (load .env via scripts.utils.workspace.load_env())")
    client = anthropic.Anthropic(api_key=api_key)

    t0 = time.time()
    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=[{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_input}],
    )
    elapsed = time.time() - t0

    output = ""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            output += block.text

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0),
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
    }
    return output, usage, elapsed


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

# Family used when a skill does not declare its own (e.g. content skills). A
# family, never a version: `claude_models.latest` turns it into today's newest
# release, so a new Haiku reaches the eval harness without a code edit.
DEFAULT_FAMILY = "haiku"


def resolve_model(frontmatter: dict, override: str | None) -> str:
    """Concrete model id for this run: override, then the skill's declared family.

    A value that is not a known family (`opus`/`sonnet`/`haiku`/`fable`) passes
    through untouched, so an explicit `--model <id>` still reproduces an old run.
    """
    declared = override or frontmatter.get("model", "")
    return claude_models.resolve(declared, default_family=DEFAULT_FAMILY)


# Why an outcome, not a (passed, total) pair: `(0, 0)` meant three different
# things - "nothing to run", "the skill does not exist", and "the model call
# raised" - and `main` summed them all into `overall_total == 0` and returned 0.
# The harness that exists to notice silent degradation degraded silently.
# Found 2026-08-23. Exit codes 2 and 3 were in the docstring from the start and
# nothing ever emitted them.
OUTCOME_OK = "ok"
OUTCOME_SKIPPED = "skipped"       # no cases, or --dry-run: not an error
OUTCOME_NOT_FOUND = "not-found"   # setup error -> exit 2
OUTCOME_API_ERROR = "api-error"   # exit 3


def run_one_skill(skill_name: str, case_filter: str | None, model_override: str | None,
                  dry_run: bool, write_benchmark: bool) -> tuple[int, int, str]:
    """Run all (or one) case for a skill.

    Returns (passed_count, total_count, outcome), where outcome is one of the
    OUTCOME_* constants above. The counts alone cannot distinguish a clean skip
    from a failed API call, and treating them as if they could is what let a
    missing API key report success.
    """
    skill_dir = SKILLS_DIR / skill_name
    if not (skill_dir / "SKILL.md").exists():
        print(f"{RED}ERROR{RESET}: skill {skill_name!r} not found", file=sys.stderr)
        return (0, 0, OUTCOME_NOT_FOUND)

    cases = load_cases(skill_dir, case_filter)
    if not cases:
        msg = f"no cases in {skill_dir / 'evals' / 'cases'}"
        if case_filter:
            msg += f" matching id={case_filter!r}"
        print(f"{YELLOW}skip{RESET}: {skill_name} - {msg}")
        return (0, 0, OUTCOME_SKIPPED)

    system_prompt, frontmatter = load_skill_system_prompt(skill_dir)
    model = resolve_model(frontmatter, model_override)

    print(f"\n{BOLD}{CYAN}{skill_name}{RESET}  model={model}  cases={len(cases)}")

    passed_total = 0
    check_total = 0
    case_results = []

    for case in cases:
        case_id = case.get("id", case["_path"])
        print(f"  {case_id}: ", end="", flush=True)

        if dry_run:
            print(f"{YELLOW}DRY{RESET}  (input_len={len(case.get('input', ''))})")
            continue

        try:
            output, usage, elapsed = call_skill(system_prompt, case["input"], model)
        except Exception as e:
            print(f"{RED}API ERROR{RESET} {e}")
            return (passed_total, check_total, OUTCOME_API_ERROR)

        results = run_checks(output, case.get("checks", {}))
        passed = sum(1 for r in results if r["passed"])
        total = len(results)
        passed_total += passed
        check_total += total

        status = f"{GREEN}{passed}/{total}{RESET}" if passed == total else f"{RED}{passed}/{total}{RESET}"
        cache_hit = "cache-hit" if usage["cache_read_input_tokens"] > 0 else ""
        print(f"{status}  {elapsed:.1f}s  in={usage['input_tokens']} out={usage['output_tokens']} {cache_hit}")
        for r in results:
            if not r["passed"]:
                print(f"    {RED}FAIL{RESET} {r['check']} - {r['detail']}")

        case_results.append({
            "id": case_id,
            "passed": passed,
            "total": total,
            "failures": [r for r in results if not r["passed"]],
            "usage": usage,
            "elapsed_seconds": round(elapsed, 2),
        })

    # A --case run grades ONE case. Writing the sidecar from it replaced
    # `last_run` with a partial record wearing a whole run's shape, and nothing
    # in the file said the other cases were never run. The sidecar's contract is
    # "the last full run", so a filtered run leaves it alone.
    if write_benchmark and not dry_run and case_results and not case_filter:
        benchmark_path = skill_dir / "evals" / "benchmark.json"
        existing = {}
        if benchmark_path.exists():
            try:
                existing = json.loads(benchmark_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # Keep the corrupt file and SAY SO. Silently resetting to {}
                # deleted the baseline -- the one artefact that makes future runs
                # comparable -- and the run that did it looked entirely normal.
                backup = benchmark_path.with_suffix(".json.corrupt")
                benchmark_path.replace(backup)
                print(f"{YELLOW}benchmark.json was unparseable; kept it at "
                      f"{backup.name} and starting a fresh baseline{RESET}",
                      file=sys.stderr)
                existing = {}
        existing["last_run"] = {
            # A SERIALIZED timestamp, so UTC with an offset
            # (dtz-datetime-convention). `time.strftime` wrote naive local time
            # and carries no tzinfo for ruff's DTZ ruleset to catch, so the one
            # stamp that makes two runs comparable was the one stamp that did
            # not say which clock it came from.
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": model,
            "passed_total": passed_total,
            "check_total": check_total,
            "cases": case_results,
        }
        if "baseline" not in existing:
            # A first run has nothing to compare against, so the baseline is
            # seeded from it. LABEL THAT. Twelve of sixteen benchmarks carried
            # a baseline byte-identical to last_run, timestamps included, and
            # nothing in the file said it was a self-seed: a structurally-zero
            # delta read exactly like "no regression detected". The audit of
            # 2026-08-23 read them as fabricated greens, which is the wrong
            # diagnosis but the right alarm.
            existing["baseline"] = existing["last_run"].copy()
            existing["baseline"]["source"] = "seeded-from-first-run"
        existing["baseline_is_self_seed"] = (
            existing["baseline"].get("source") == "seeded-from-first-run"
        )
        # Atomic: an interrupt here left unparseable JSON, which the branch
        # above then had to deal with.
        atomic_write_text(benchmark_path, json.dumps(existing, indent=2))
        print(f"  {GREEN}benchmark.json updated{RESET} -> {benchmark_path.relative_to(ROOT)}")
        if existing["baseline_is_self_seed"]:
            print(f"  {YELLOW}baseline is a self-seed{RESET} - this run was compared "
                  "against itself, so the delta detects nothing. Promote a real "
                  "baseline before reading it as a regression check.")

    return (passed_total, check_total,
            OUTCOME_SKIPPED if dry_run else OUTCOME_OK)


def list_skills_with_evals() -> list[str]:
    """Return sorted list of skill names that have an evals/cases/ directory."""
    out = []
    for child in SKILLS_DIR.iterdir():
        if child.is_dir() and (child / "evals" / "cases").exists():
            cases = list((child / "evals" / "cases").glob("*.json"))
            if cases:
                out.append(child.name)
    return sorted(out)


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--skill", help="Skill name (directory under .claude/skills/)")
    g.add_argument("--all", action="store_true", help="Run every skill with an evals/cases/ dir")
    parser.add_argument("--case", help="Run only the case with this id")
    parser.add_argument("--model", help="Override the model (haiku/sonnet/opus/fable or full id)")
    parser.add_argument("--dry-run", action="store_true", help="Parse cases without calling the API")
    parser.add_argument("--no-write", action="store_true", help="Do not update benchmark.json")
    args = parser.parse_args()

    skills = [args.skill] if args.skill else list_skills_with_evals()
    if not skills:
        print(f"{YELLOW}No skills with evals/ directory found{RESET}")
        return 2

    overall_passed = 0
    overall_total = 0
    outcomes = []
    for name in skills:
        p, t, outcome = run_one_skill(
            name, args.case, args.model, args.dry_run,
            write_benchmark=not args.no_write)
        overall_passed += p
        overall_total += t
        outcomes.append(outcome)

    print()
    # Order matters, and it is worst-first. An API error outranks a check
    # failure because a failed call measured nothing, and "nothing measured"
    # must never be reported as either pass or fail.
    if OUTCOME_API_ERROR in outcomes:
        print(f"{RED}API error: {outcomes.count(OUTCOME_API_ERROR)} of "
              f"{len(skills)} skill(s) could not be measured{RESET}", file=sys.stderr)
        return 3
    if OUTCOME_NOT_FOUND in outcomes:
        return 2
    # A --case typo matches nothing, every skill is skipped, and zero checks
    # then took the branch below and exited 0 - so a targeted regression run
    # that measured NOTHING reported green, which is the one thing a targeted
    # run must never do. Judged over the whole run: under --all a named case
    # lives in exactly one skill, so "skipped here" is the ordinary state of
    # every other skill and only "ran nowhere" is the error.
    if args.case and overall_total == 0:
        scope = "any skill" if args.all else repr(skills[0])
        print(f"{RED}--case {args.case!r} matched no case in {scope}{RESET}",
              file=sys.stderr)
        return 2
    if overall_total == 0:
        print(f"{YELLOW}No checks run{RESET}")
        return 0
    print(f"{BOLD}Overall: {overall_passed}/{overall_total} checks passed{RESET}")
    return 0 if overall_passed == overall_total else 1


if __name__ == "__main__":
    sys.exit(main())
