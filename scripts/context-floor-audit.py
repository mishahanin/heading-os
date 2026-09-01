#!/usr/bin/env python3
"""
context-floor-audit.py - measure the always-loaded context floor, repeatably.

Every session pays the floor before it does any work, and every compaction
re-injects it. Until 2026-08-19 the only figure this workspace had for it was
produced ad hoc in a shell session, and it was wrong in a way that set an
unreachable target: 45 974 was read as "the skill catalogue's name and
description entries" when it is in fact the ENTIRE YAML frontmatter of all 96
skills. The description fields alone are about a quarter of that. A plan then
set a 15 000-token reduction bar against a surface holding about 12 300 tokens.

So this script reports the two separately and never conflates them, reports
bytes as bytes and tokens as tokens, and says plainly what its method cannot
see.

WHAT IT CANNOT SEE, stated up front because the number is otherwise read as
complete: this measures WORKSPACE-OWNED content only. The system prompt, the
tool schemas, and the harness's own scaffolding are not on disk here and are not
counted. It also cannot observe WHICH parts of a SKILL.md the harness injects -
that is a harness behaviour, not a file property - so it reports the description
component and the remaining frontmatter as two candidate surfaces and leaves the
choice between them to whoever can measure the harness.

Usage:
    python scripts/context-floor-audit.py
    python scripts/context-floor-audit.py --json
    python scripts/context-floor-audit.py --write-baseline
    python scripts/context-floor-audit.py --baseline          # exit 1 on growth

THE BASELINE IS A RATCHET, AND SOMEONE HAS TO TURN IT. `--baseline` fails only
upward, so every byte the floor loses becomes permanent slack the floor may
regrow for free. Nothing re-tightens it automatically, by design: an
auto-shrinking baseline would silently redefine the target between two runs and
no diff would ever show it. Re-baselining is therefore a deliberate, reviewable
commit, in BOTH directions:

    - after a reduction, re-run `--write-baseline` to bank the win, or the next
      author inherits the slack rather than the gain;
    - after a legitimate addition (a new skill, a new always-on rule) that
      pushes the floor up, `--write-baseline` is how you raise it on purpose.
      Raising it is not cheating the gate; failing to say you raised it is. The
      committed diff to config/context-floor-baseline.json IS the disclosure.

NOT REPRODUCIBLE OFF THE OPERATOR WORKSPACE, which is why no CI job or
pre-commit hook consumes `--baseline` today. Two of the five components
(`memory_index`, and the operational half of `claude_md`) live in the private
data overlay, so a clone without one measures ~25 KB lower. A baseline written
here fails open in CI (always a shrink); one written in CI fires spuriously
here (always growth). Wiring a blocking gate needs an engine-only subtotal
first - the three components that ARE reproducible anywhere are
`skill_descriptions`, `skill_other_frontmatter` and `always_on_rules`.

Tests: tests/test_a_gate_that_shipped_what_it_never_read.py
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.markdown import FM_OK, split_frontmatter  # noqa: E402
from scripts.utils.workspace import get_workspace_root  # noqa: E402

# Bytes per token, as an ESTIMATE. English prose and YAML sit near four bytes per
# token for this tokenizer family. The ratio is stated wherever a token figure is
# printed, because the mistake this script exists to prevent was a byte count
# wearing a token label.
BYTES_PER_TOKEN = 4

# `description:` runs until the next top-level key. Skill descriptions are often
# folded scalars spanning many lines, so a single-line match undercounts badly.
# `|\Z` in the lookahead. Without it the pattern required a FOLLOWING top-level
# key, so a `description:` that is the last key in a frontmatter block matched
# nothing at all: that skill was counted at description_bytes 0, its whole
# frontmatter was attributed to `skill_other_frontmatter`, and both of the two
# totals this script exists to separate moved the wrong way. YAML key order is
# free, so description-last is an ordinary layout. The header says this script
# was written because that exact conflation once set an unreachable reduction
# target.
DESCRIPTION_RE = re.compile(r"^description:\s*(.*?)(?=^[A-Za-z_][\w-]*:|\Z)",
                            re.S | re.M)

BASELINE_PATH = "config/context-floor-baseline.json"
GROWTH_TOLERANCE = 0.05


def _tokens(byte_count: int) -> int:
    return byte_count // BYTES_PER_TOKEN


def measure_skills(root: Path) -> dict:
    """The catalogue, split into the surface a caller can edit and the rest.

    A SKILL.md this cannot read is NAMED, never skipped in silence. The number
    below feeds a growth gate with a 5% tolerance, and a dropped skill makes the
    measured floor SMALLER: the gate would then pass on a floor that grew, which
    is the one direction a gate must not fail in.
    """
    total_fm = total_desc = count = 0
    per_skill = []
    unreadable: list[str] = []
    for path in sorted((root / ".claude" / "skills").glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        frontmatter, _body, kind = split_frontmatter(text)
        if frontmatter is None or kind != FM_OK:
            unreadable.append(f"{path.parent.name} ({kind})")
            continue
        # The shared block KEEPS the newline before the closing fence; the
        # `\A---\n(.*?)\n---\n` regex that used to sit here put it outside
        # group 1. Dropping one terminator preserves the recorded byte count
        # EXACTLY (measured 2026-08-29: 94 skills, +94 bytes without this line),
        # so migrating the grammar moves no number in
        # config/context-floor-baseline.json. The grammar is the whole change:
        # a fence written `--- ` or `---\t` was read as no frontmatter at all.
        if frontmatter.endswith("\n"):
            frontmatter = frontmatter[:-1]
            if frontmatter.endswith("\r"):
                frontmatter = frontmatter[:-1]
        count += 1
        fm_bytes = len(frontmatter.encode("utf-8"))
        found = DESCRIPTION_RE.search(frontmatter)
        desc_bytes = len(found.group(1).encode("utf-8")) if found else 0
        total_fm += fm_bytes
        total_desc += desc_bytes
        per_skill.append({
            "skill": path.parent.name,
            "frontmatter_bytes": fm_bytes,
            "description_bytes": desc_bytes,
        })
    per_skill.sort(key=lambda row: row["description_bytes"], reverse=True)
    if unreadable:
        print(f"{YELLOW}warn:{RESET} {len(unreadable)} SKILL.md file(s) carry no "
              f"readable frontmatter and are NOT in the figures below: "
              f"{', '.join(unreadable)}", file=sys.stderr)
    return {
        "skills": count,
        "frontmatter_bytes": total_fm,
        "description_bytes": total_desc,
        "other_frontmatter_bytes": total_fm - total_desc,
        "per_skill": per_skill,
        "unreadable_skills": unreadable,
    }


def _is_always_on(text: str) -> bool:
    """A rule with no `paths:` key, or an empty one, loads in every session.

    Same grammar as `measure_skills`, and for a sharper reason: a rule that
    HAS `paths:` but whose fence carries a trailing space read here as "no
    frontmatter", which this function turns into always-on. A path-scoped rule
    was then counted in the always-on floor and reported to the operator as
    loading in every session when it does not.
    """
    body, _rest, kind = split_frontmatter(text)
    if body is None or kind != FM_OK:
        return True
    found = re.search(r"^paths:(.*)$", body, re.M)
    if not found:
        return True
    trailing = found.group(1).strip()
    if trailing in ("", "[]"):
        # An inline empty list is always-on; a bare `paths:` needs its block read.
        if trailing == "[]":
            return True
        return not re.search(r"^\s+-\s+\S", body[found.end():], re.M)
    return False


def measure_rules(root: Path) -> dict:
    always, scoped = [], []
    for path in sorted((root / ".claude" / "rules").glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        row = {"rule": path.name, "bytes": len(text.encode("utf-8"))}
        (always if _is_always_on(text) else scoped).append(row)
    return {
        "always_on": always,
        "path_scoped": scoped,
        "always_on_bytes": sum(r["bytes"] for r in always),
        "path_scoped_bytes": sum(r["bytes"] for r in scoped),
    }


def _file_bytes(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").encode("utf-8"))
    except OSError:
        return 0


def measure(root: Path) -> dict:
    skills = measure_skills(root)
    rules = measure_rules(root)

    from scripts.utils.workspace import get_data_root

    try:
        memory = _file_bytes(get_data_root() / "auto-memory" / "MEMORY.md")
    except Exception:  # noqa: BLE001 - a missing overlay is reported as zero, not fatal
        memory = 0
    claude_md = _file_bytes(root / "CLAUDE.md")
    try:
        claude_md += _file_bytes(get_data_root() / "CLAUDE.operational.md")
    except Exception as exc:  # noqa: BLE001 - a bare engine clone has no overlay
        # Said out loud rather than swallowed: on a public engine clone there is
        # no data overlay and the operational CLAUDE.md genuinely does not exist,
        # which is a smaller floor and not an error. Silence here would let a
        # real resolution failure read as that same benign case.
        print(f"context-floor-audit: no operational CLAUDE.md counted ({exc})",
              file=sys.stderr)

    components = {
        "skill_descriptions": skills["description_bytes"],
        "skill_other_frontmatter": skills["other_frontmatter_bytes"],
        "always_on_rules": rules["always_on_bytes"],
        "memory_index": memory,
        "claude_md": claude_md,
    }
    # Two totals, not one. `total_bytes` sums every surface this script can see on
    # disk. `observed_bytes` sums only the four whose injection into a live session
    # this workspace has actually confirmed by reading its own system prompt.
    #
    # The difference is `skill_other_frontmatter` — the x-heading-* blocks — at
    # ~136 KB, the single largest row here. The harness's skill listing carries a
    # name and a description; no x-heading content has ever been observed in a
    # session's available-skills block. But this script's method cannot settle
    # that either way, so it does not assert the opposite: the row keeps its place
    # in the on-disk total and is simply excluded from the observed one.
    #
    # Why it matters: config/context-floor-baseline.json commits a number, and a
    # later "we cut the floor by N" has to name WHICH of the two it moved. Added
    # 2026-08-20 per .claude/rules/scope-claims.md.
    observed_keys = (
        "skill_descriptions",
        "always_on_rules",
        "memory_index",
        "claude_md",
    )
    return {
        "bytes_per_token": BYTES_PER_TOKEN,
        "skill_count": skills["skills"],
        # What `skill_count` does NOT cover. A machine reader of this payload
        # would otherwise take the count as the whole catalogue; the stderr
        # warning it cannot see is not a substitute for the field.
        "unreadable_skills": skills["unreadable_skills"],
        "components": components,
        "total_bytes": sum(components.values()),
        "observed_bytes": sum(components[k] for k in observed_keys),
        "observed_components": list(observed_keys),
        "path_scoped_rules_bytes": rules["path_scoped_bytes"],
        "largest_descriptions": skills["per_skill"][:10],
        "always_on_rules": rules["always_on"],
    }


def render(result: dict) -> None:
    print(f"{BOLD}Always-loaded context floor{RESET}  "
          f"{GRAY}({result['skill_count']} skills){RESET}\n")
    print(f"{GRAY}{'component':<28}{'bytes':>12}{'~tokens':>10}{RESET}")
    for name, byte_count in result["components"].items():
        print(f"{name:<28}{byte_count:>12}{_tokens(byte_count):>10}")
    total = result["total_bytes"]
    observed = result["observed_bytes"]
    print(f"{BOLD}{'TOTAL (surfaces on disk)':<28}{total:>12}{_tokens(total):>10}{RESET}")
    print(f"{BOLD}{'OBSERVED FLOOR':<28}{observed:>12}{_tokens(observed):>10}{RESET}"
          f"  {GRAY}<- injection confirmed{RESET}")

    scoped = result["path_scoped_rules_bytes"]
    print(f"\n{GRAY}Path-scoped rules, NOT in the total (they load only on a "
          f"path match): {scoped} bytes, ~{_tokens(scoped)} tokens{RESET}")

    print(f"\n{BOLD}Largest skill descriptions{RESET} "
          f"{GRAY}(the only surface a description edit can shrink){RESET}")
    for row in result["largest_descriptions"]:
        print(f"  {row['skill']:<26}{row['description_bytes']:>7} bytes"
              f"{_tokens(row['description_bytes']):>7} tok")

    router = next(
        (r for r in result["always_on_rules"] if r["rule"] == "skill-router.md"),
        None,
    )
    if router:
        print(f"\n{YELLOW}Note:{RESET} .claude/rules/skill-router.md is counted "
              f"under always-on rules ({router['bytes']} bytes, "
              f"~{_tokens(router['bytes'])} tokens). It carries the trigger list "
              f"for every skill, generated from x-heading-routing.triggers - a "
              f"DIFFERENT field from description. Compressing descriptions does "
              f"not shrink it, so the two wins do not add up.")

    # scope-claims: name what the method cannot establish, rather than letting a
    # partial measurement read as a complete one.
    print(f"\n{GRAY}Method: workspace-owned files only. The system prompt and the "
          f"tool schemas are not on disk here and are NOT counted. Token figures "
          f"are bytes/{BYTES_PER_TOKEN}, an estimate, not a tokenizer result. "
          f"This script cannot observe which parts of a SKILL.md the harness "
          f"actually injects, so it prints two totals. TOTAL sums every surface "
          f"on disk. OBSERVED FLOOR sums only "
          f"{', '.join(result['observed_components'])} - the four whose presence "
          f"in a live system prompt has been read directly. The gap between them "
          f"is skill_other_frontmatter (the x-heading-* blocks), which is on disk "
          f"and whose injection is unconfirmed in either direction. Say which "
          f"total you moved.{RESET}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure the always-loaded context floor by component."
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="overwrite the committed baseline with the current measurement. "
             "Use it to bank a reduction, and to RAISE the floor deliberately "
             "when a new skill or always-on rule legitimately grows it. The "
             "diff to " + BASELINE_PATH + " is the disclosure that it moved.",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="compare against the committed baseline and exit 1 on growth "
             "beyond the tolerance. Never fails on a shrink, so the recorded "
             "number only ratchets when someone runs --write-baseline.",
    )
    args = parser.parse_args()

    root = get_workspace_root()
    result = measure(root)
    baseline_file = root / BASELINE_PATH

    # A corpus with a hole in it is not a floor, and `measure_skills` already
    # spells out why: "a dropped skill makes the measured floor SMALLER: the
    # gate would then pass on a floor that grew, which is the one direction a
    # gate must not fail in." Until 2026-09-01 that reasoning ended at a warning
    # on stderr and neither ratchet mode read `unreadable_skills`. MEASURED in
    # the ratchet sandbox: truncating one SKILL.md's closing fence took the
    # measured floor from 273 bytes to 96 and `--baseline` called the drop
    # "within tolerance" and exited 0.
    #
    # Both modes refuse, and banking is the worse of the two: a comparison that
    # passes wrongly is undone by the next run, while a baseline WRITTEN while a
    # skill was invisible holds the ratchet at a number that was never the floor.
    # A bare informational run still prints, because reporting is not asserting.
    if (args.baseline or args.write_baseline) and result["unreadable_skills"]:
        print(f"\n{RED}Refusing: {len(result['unreadable_skills'])} SKILL.md "
              f"file(s) carry no readable frontmatter, so this measurement is "
              f"smaller than the real floor and growth elsewhere would read as "
              f"a shrink: {', '.join(result['unreadable_skills'])}{RESET}",
              file=sys.stderr)
        return 1

    if args.write_baseline:
        baseline_file.parent.mkdir(parents=True, exist_ok=True)
        baseline_file.write_text(
            json.dumps({
                "components": result["components"],
                "total_bytes": result["total_bytes"],
                "skill_count": result["skill_count"],
                "bytes_per_token": BYTES_PER_TOKEN,
            }, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"{GREEN}Baseline written to {BASELINE_PATH}{RESET}")
        return 0

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        render(result)

    if args.baseline:
        # The verdict goes to stderr, never stdout. With `--json --baseline`
        # these three lines were printed AFTER the JSON document on the same
        # stream, so a machine caller got an unparseable mix. `crm-health.py`
        # already routes its warnings to stderr for exactly this reason, and
        # says so in a comment.
        def verdict(line: str) -> None:
            print(line, file=sys.stderr)

        if not baseline_file.is_file():
            verdict(f"\n{YELLOW}No baseline at {BASELINE_PATH}; "
                    f"write one with --write-baseline.{RESET}")
            return 1
        recorded = json.loads(baseline_file.read_text(encoding="utf-8"))
        was = recorded.get("total_bytes", 0)
        now = result["total_bytes"]
        ceiling = was * (1 + GROWTH_TOLERANCE)
        if now > ceiling:
            verdict(f"\n{RED}Floor grew: {was} -> {now} bytes "
                    f"(tolerance {int(GROWTH_TOLERANCE * 100)}%).{RESET}")
            return 1
        verdict(f"\n{GREEN}Floor within tolerance: {was} -> {now} bytes.{RESET}")
        # A pass is not the whole story. The gate only fails upward, so every
        # byte between the live figure and the ceiling is room the floor may
        # regrow unnoticed. Printing the slack is what makes a stale baseline
        # visible; without it "within tolerance" reads as "tight" and the
        # ratchet quietly stops ratcheting.
        slack = int(ceiling) - now
        if slack > 0:
            verdict(f"{YELLOW}Slack: {slack} bytes (~{_tokens(slack)} tokens) "
                    f"before this gate fires - the baseline is {was} and the "
                    f"ceiling is {int(ceiling)} at {int(GROWTH_TOLERANCE * 100)}% "
                    f"tolerance. Re-run with --write-baseline to bank the "
                    f"reduction, or the next author inherits the slack.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
