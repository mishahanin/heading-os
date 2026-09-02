#!/usr/bin/env python3
"""Audit SKILL.md frontmatter completeness across all workspace skills.

Checks every .claude/skills/{name}/SKILL.md for the required frontmatter fields
defined in .claude/rules/development-standards.md and consumed by the parallel
orchestrator in .claude/rules/skill-orchestrator.md:

Required (top-level):
  name, description, metadata.author, metadata.version
Required (under x-heading-orchestration:):
  parallel_safe, shared_state, triggers
Recommended:
  argument-hint, allowed-tools

The orchestration fields live under a namespaced x-heading-orchestration: block
in SKILL.md. This signals "workspace extension, not part of Anthropic's standard
SKILL.md spec" so future stricter validation does not strip them.

Skills lacking the orchestration block (or its fields) default to
parallel_safe=false per the orchestrator's safety model, which is invisible.
This audit surfaces the gap so frontmatter can be filled in deliberately.

Size budget (F-5.3): every SKILL.md is also checked against a mechanical size
budget - a hard cap of 500 lines and 18432 bytes (18 KB), with a warn threshold
at 16384 bytes (16 KB). A file over either hard cap is a HARD violation; a file
in the warn band prints a non-blocking advisory. The size gate is UNCONDITIONAL:
any HARD size violation makes this script exit 1 regardless of --fail-on-missing,
so the flagless CI invocation ("Skill metadata contract") enforces it with no
workflow-file change. Size WARN/HARD lines always print, even under --summary.
The frontmatter-completeness gate keeps its existing --fail-on-missing semantics;
the size gate and the coverage gate are unconditional.

Trigger coverage (F-6.1): every AUTO-ROUTABLE skill (x-heading-routing.router == auto
AND NOT disable-model-invocation: true) must carry a valid triggers.json corpus
(>= 6 cases, >= 4 positive, >= 2 negative). A MISSING corpus (auto-routable, not in the
grandfather baseline), a thin/malformed present corpus, or a stale baseline entry
(baselined but now covered) makes this script exit 1 UNCONDITIONALLY, so the flagless CI
invocation enforces coverage with no workflow-file change. Grandfathering is the committed,
only-shrinks config/triggers-coverage-baseline.json; --write-baseline regenerates it
shrink-only (removes now-covered skills, never adds a newly-shipped uncovered skill).

Usage:
  python scripts/skill-metadata-check.py              # full audit (+ unconditional size + coverage gates)
  python scripts/skill-metadata-check.py --summary    # counts only, no per-skill output (size + coverage lines still print)
  python scripts/skill-metadata-check.py --fail-on-missing  # ALSO exit 1 if any required field missing (for CI)
  python scripts/skill-metadata-check.py --json       # machine-readable JSON output (includes size + coverage fields)
  python scripts/skill-metadata-check.py --write-baseline   # shrink-only regenerate the grandfather baseline
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import get_workspace_root
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET
from scripts.utils import markdown as md
from scripts.utils.markdown import parse_frontmatter_strict


REQUIRED_TOP_FIELDS = ["name", "description"]
REQUIRED_ORCH_FIELDS = ["parallel_safe", "shared_state", "triggers"]
REQUIRED_METADATA = ["author", "version"]
RECOMMENDED_FIELDS = ["argument-hint", "allowed-tools"]
ORCHESTRATION_BLOCK = "x-heading-orchestration"

# Size budget (F-5.3). Hard caps fail the check; the warn threshold is advisory.
LINE_HARD_CAP = 500
BYTE_HARD_CAP = 18432  # 18 KB
BYTE_WARN = 16384      # 16 KB

# Trigger-coverage gate (F-6.1). The router is a markdown rule the model interprets,
# so a new skill's triggers can silently hijack another skill's queries; triggers.json
# corpora are the regression harness that catches it. Every auto-routable skill must
# carry a valid corpus (>= 6 cases, >= 4 positive, >= 2 hard negatives). "Auto-routable"
# = x-heading-routing.router == auto AND NOT disable-model-invocation: true (a
# disable-model-invocation skill never auto-routes, so a routing corpus is meaningless).
# Grandfathering is a committed, only-shrinks baseline (config/triggers-coverage-baseline.json),
# mirroring lint-ratchet / audit-skill-bash-paths - NOT a git-date lookup.
ROUTING_KEY = "x-heading-routing"
TRIGGERS_MIN_CASES = 6
TRIGGERS_MIN_POS = 4
TRIGGERS_MIN_NEG = 2
BASELINE_REL = ("config", "triggers-coverage-baseline.json")


def classify_size(size_lines: int, size_bytes: int) -> str:
    """Classify a SKILL.md's size against the budget: HARD | WARN | OK."""
    if size_lines > LINE_HARD_CAP or size_bytes > BYTE_HARD_CAP:
        return "HARD"
    if size_bytes >= BYTE_WARN:
        return "WARN"
    return "OK"


def is_auto_routable(frontmatter: dict) -> bool:
    """A skill is auto-routable when the router can fire it from natural language.

    Requires x-heading-routing.router == "auto" AND NOT disable-model-invocation: true.
    A disable-model-invocation skill (even router: auto) can never auto-route, so it is
    EXEMPT from the corpus requirement. A skill with no routing block is treated as not
    auto-routable here (generate-skill-router.py --check flags the missing block
    separately); the coverage gate does not double-jeopardy it.
    """
    if frontmatter.get("disable-model-invocation") is True:
        return False
    routing = frontmatter.get(ROUTING_KEY)
    if not isinstance(routing, dict):
        return False
    return str(routing.get("router", "auto")).lower() == "auto"


def corpus_issues(corpus_path: Path) -> list[str]:
    """Validate a triggers.json corpus SHAPE. Empty list == valid.

    A JSON array of >= 6 {query, should_trigger} objects with >= 4 cases whose
    `should_trigger` is true and >= 2 whose `should_trigger` is false. That is
    the whole enforced rule, and the counting below is all of it.

    The parenthetical here read "hard negatives naming the neighbor skill they
    should route to" until 2026-09-02, which is the AUTHORING standard from
    `.claude/rules/development-standards.md`, not a property this function
    measures. Nothing in a `{query, should_trigger}` case records a neighbor
    skill, so two off-topic trivia queries satisfy the count and the F-6.1
    coverage gate passes a corpus that cannot catch a routing hijack. Claiming
    the stronger check made the gate read as coverage it never had, which is
    worse than the gap itself.

    Enforcing hard negatives would need a `routes_to` field on negative cases and
    a migration of every committed corpus; that is a schema decision for the
    F-6.1 owner, not something to imply in a docstring. Until then the standard
    stands as a convention the author upholds, and this stays a shape check.
    """
    try:
        data = json.loads(corpus_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return [f"unreadable or invalid JSON: {e}"]
    if not isinstance(data, list):
        return ["triggers.json must be a JSON array"]
    cases = [c for c in data
             if isinstance(c, dict) and "query" in c and "should_trigger" in c]
    issues = []
    if len(cases) != len(data):
        issues.append("every case must be an object with 'query' and 'should_trigger'")
    pos = sum(1 for c in cases if c.get("should_trigger") is True)
    neg = sum(1 for c in cases if c.get("should_trigger") is False)
    if len(cases) < TRIGGERS_MIN_CASES:
        issues.append(f"{len(cases)} cases < {TRIGGERS_MIN_CASES} required")
    if pos < TRIGGERS_MIN_POS:
        issues.append(f"{pos} positive < {TRIGGERS_MIN_POS} required")
    if neg < TRIGGERS_MIN_NEG:
        issues.append(f"{neg} negative < {TRIGGERS_MIN_NEG} required")
    return issues


def is_valid_corpus(corpus_path: Path) -> bool:
    """True when triggers.json exists and passes the shape rule."""
    return corpus_path.exists() and not corpus_issues(corpus_path)


def load_baseline(root: Path) -> set[str]:
    """Read the committed grandfather set (skills allowed to lack a corpus).

    Absent file -> empty set (first-seed state: every uncovered auto-routable skill
    classifies MISSING). A malformed file also -> empty set, which fails the gate
    loudly (many MISSING) rather than silently grandfathering everything.
    """
    p = root.joinpath(*BASELINE_REL)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return set(data) if isinstance(data, list) else set()


def _atomic_write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_baseline(root: Path, results: list[dict]) -> tuple[list[str], list[str]]:
    """Shrink-only regeneration of config/triggers-coverage-baseline.json.

    NEVER regenerates from scratch. On first seed (file absent) it writes the full
    still-uncovered auto-routable set. On any later run it writes
    ``existing_baseline INTERSECT still-uncovered`` - so it can only REMOVE now-covered
    skills, never ADD a newly-shipped uncovered skill (that skill stays MISSING and the
    gate fails). Returns (written_sorted, excluded_new) where excluded_new are new
    uncovered auto-routable skills deliberately NOT added to the frozen baseline.
    """
    p = root.joinpath(*BASELINE_REL)
    still_uncovered = {r["name"] for r in results
                       if r.get("is_auto_routable") and not r.get("has_valid_corpus")}
    if not p.exists():
        new_baseline = still_uncovered
        excluded_new: set[str] = set()
    else:
        existing = load_baseline(root)
        new_baseline = existing & still_uncovered  # subset of existing: never grows
        excluded_new = still_uncovered - existing
    _atomic_write_json(p, sorted(new_baseline))
    return sorted(new_baseline), sorted(excluded_new)


def parse_frontmatter(skill_md: Path) -> tuple[dict, str]:
    """Parse YAML frontmatter from a SKILL.md file.

    Returns (frontmatter_dict, error_message). error_message is empty on success.

    A thin wrapper over ``scripts.utils.markdown.parse_frontmatter_strict``,
    which keeps the failure REASON this audit exists to report. The audit kept a
    private copy because the plain shared parser collapses every failure into
    ``({}, text)``; that reason is gone now that the shared module classifies.

    Its own copy split on `text.split("---", 2)`, the three characters wherever
    they land. MEASURED 2026-08-28 on `description: drift --- check` in an
    otherwise ordinary SKILL.md: every key after the embedded dashes was
    dropped, this audit reported `metadata.author`, `metadata.version` and
    `x-heading-orchestration` as missing while all three sat in the file, and
    `triggers_status` flipped from MISSING to EXEMPT, so the coverage gate
    stopped asking for a corpus it requires. `generate-skill-router.py`, reading
    the same file with the same intent, read the whole mapping. Two CI gates,
    one corpus, two answers.

    The word "YAML parse error" is kept for that case, so the audit's own output
    is unchanged on every file it parses today (MEASURED: identical over all 94
    SKILL.md).
    """
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as e:
        return {}, f"read failed: {e}"

    data, kind, detail = parse_frontmatter_strict(text)
    if kind == md.FM_OK:
        return data, ""
    return {}, {
        md.FM_NO_OPENING: "no frontmatter (missing opening ---)",
        md.FM_NO_CLOSING: "malformed frontmatter (missing closing ---)",
        md.FM_INVALID_YAML: f"YAML parse error: {detail}",
        md.FM_EMPTY: "empty frontmatter",
        md.FM_NOT_MAPPING: f"frontmatter must be a mapping, got {detail}",
    }[kind]


def _classify_corpus(result: dict, skill_dir: Path, frontmatter: dict | None,
                     baseline: frozenset) -> None:
    """Fill the F-6.1 coverage fields on ``result``, in place.

    ``frontmatter=None`` means the SKILL.md could not be read or parsed, so
    routability is genuinely unknown. The corpus file is independent of the
    frontmatter and is still measured, but the status stays ``UNKNOWN`` and
    ``main`` treats UNKNOWN as a gate failure. Before this ran on the error
    paths, a skill with a malformed SKILL.md kept ``triggers_status`` at its
    initial UNKNOWN with an EMPTY ``corpus_issues`` -- its triggers.json was
    never opened -- and the "UNCONDITIONAL" coverage gate read that as clean.
    """
    corpus = skill_dir / "triggers.json"
    if corpus.exists():
        result["corpus_issues"] = corpus_issues(corpus)
    result["has_valid_corpus"] = corpus.exists() and not result["corpus_issues"]

    if frontmatter is None:
        result["triggers_status"] = "UNKNOWN"
        return

    result["is_auto_routable"] = is_auto_routable(frontmatter)
    if result["has_valid_corpus"]:
        result["triggers_status"] = "COVERED"
    elif not result["is_auto_routable"]:
        result["triggers_status"] = "EXEMPT"
    elif skill_dir.name in baseline:
        result["triggers_status"] = "GRANDFATHERED"
    else:
        result["triggers_status"] = "MISSING"


def check_skill(skill_dir: Path, baseline: frozenset = frozenset()) -> dict:
    """Check a single skill directory's SKILL.md for required frontmatter.

    ``baseline`` is the committed grandfather set (skill names allowed to lack a
    triggers.json corpus); it drives the MISSING-vs-GRANDFATHERED distinction for the
    F-6.1 coverage gate.
    """
    skill_md = skill_dir / "SKILL.md"
    result = {
        "name": skill_dir.name,
        "path": str(skill_md.relative_to(get_workspace_root())),
        "missing_required": [],
        "missing_recommended": [],
        "invalid_values": [],
        "error": "",
        "status": "UNKNOWN",
        "size_lines": 0,
        "size_bytes": 0,
        "size_status": "OK",
        "is_auto_routable": False,
        "has_valid_corpus": False,
        "corpus_issues": [],
        "triggers_status": "UNKNOWN",
    }

    if not skill_md.exists():
        result["error"] = "SKILL.md not found"
        result["status"] = "ERROR"
        _classify_corpus(result, skill_dir, None, baseline)
        return result

    # Size budget: measured independently of frontmatter status so a HARD size
    # violation is caught even on a skill that also fails frontmatter checks.
    try:
        raw = skill_md.read_text(encoding="utf-8")
        result["size_bytes"] = len(raw.encode("utf-8"))
        # splitlines(), not count("\n"): the latter counts line TERMINATORS, so a
        # file whose last line has no trailing newline was reported one line
        # short and a 501-line SKILL.md passed the 500-line hard cap.
        result["size_lines"] = len(raw.splitlines())
        result["size_status"] = classify_size(result["size_lines"], result["size_bytes"])
    except OSError as e:
        result["error"] = f"read failed: {e}"
        result["status"] = "ERROR"
        _classify_corpus(result, skill_dir, None, baseline)
        return result

    frontmatter, err = parse_frontmatter(skill_md)
    if err:
        result["error"] = err
        result["status"] = "ERROR"
        _classify_corpus(result, skill_dir, None, baseline)
        return result

    for field in REQUIRED_TOP_FIELDS:
        if field not in frontmatter:
            result["missing_required"].append(field)

    metadata = frontmatter.get("metadata", {})
    if not isinstance(metadata, dict):
        result["invalid_values"].append("metadata must be a mapping")
    else:
        for meta_field in REQUIRED_METADATA:
            if meta_field not in metadata:
                result["missing_required"].append(f"metadata.{meta_field}")

    for field in RECOMMENDED_FIELDS:
        if field not in frontmatter:
            result["missing_recommended"].append(field)

    # Orchestration block (x-heading-orchestration) - workspace extension,
    # namespaced to signal "not part of Anthropic's standard SKILL.md spec".
    orch = frontmatter.get(ORCHESTRATION_BLOCK)
    if orch is None:
        result["missing_required"].append(ORCHESTRATION_BLOCK)
    elif not isinstance(orch, dict):
        result["invalid_values"].append(f"{ORCHESTRATION_BLOCK} must be a mapping, got {type(orch).__name__}")
    else:
        for field in REQUIRED_ORCH_FIELDS:
            if field not in orch:
                result["missing_required"].append(f"{ORCHESTRATION_BLOCK}.{field}")

        if "parallel_safe" in orch:
            value = orch["parallel_safe"]
            if str(value).lower() not in {"true", "false", "partial"}:
                result["invalid_values"].append(f"{ORCHESTRATION_BLOCK}.parallel_safe={value!r} (must be true|false|partial)")

        if "shared_state" in orch:
            value = orch["shared_state"]
            if not isinstance(value, list):
                result["invalid_values"].append(f"{ORCHESTRATION_BLOCK}.shared_state must be a list, got {type(value).__name__}")
            else:
                # Each ENTRY, not just the container. The consumer is the
                # orchestrator's step-4 conflict detection
                # (.claude/rules/skill-orchestrator.md), which intersects these
                # lists by substring. A None, a mapping, or a blank string is a
                # list element that satisfies the container check above and can
                # never match a sibling's path, so it declares a conflict the
                # orchestrator will not see. That is the same failure as an
                # empty list, one layer down, and it became reachable the day
                # skills started filling this field.
                #
                # Deliberately NOT checked here: whether a non-empty list is
                # non-empty ENOUGH - that is, whether a skill that writes files
                # declared them. Frontmatter cannot answer it. `allowed-tools`
                # is a grant, not a limit, and most writing in this workspace
                # happens inside a script reached through `Bash(python3:*)`,
                # so any frontmatter-only heuristic either under-detects
                # (Write/Edit only: 5 of dozens) or over-detects (`Bash` counts:
                # /next, /state-check and /validate run read-only scripts).
                # A rule needing a hand-kept exemption list to avoid firing on
                # correct skills does not belong in the gate that runs on every
                # commit. It lives in
                # tests/test_two_skill_contracts_that_were_declared_and_never_measured.py,
                # which can carry a per-skill reason and shrink over time.
                for i, entry in enumerate(value):
                    if not isinstance(entry, str):
                        result["invalid_values"].append(
                            f"{ORCHESTRATION_BLOCK}.shared_state[{i}] must be a string, "
                            f"got {type(entry).__name__}")
                    elif not entry.strip():
                        result["invalid_values"].append(
                            f"{ORCHESTRATION_BLOCK}.shared_state[{i}] is blank; an entry "
                            f"that names no path never intersects a sibling's")

        if "triggers" in orch:
            value = orch["triggers"]
            if not isinstance(value, list):
                result["invalid_values"].append(f"{ORCHESTRATION_BLOCK}.triggers must be a list, got {type(value).__name__}")

    if result["missing_required"] or result["invalid_values"]:
        result["status"] = "FAIL"
    elif result["missing_recommended"]:
        result["status"] = "WARN"
    else:
        result["status"] = "PASS"

    # Trigger-coverage classification (F-6.1). Independent of the frontmatter status
    # above so a coverage gap is visible even on a skill that also fails other checks.
    _classify_corpus(result, skill_dir, frontmatter, baseline)

    return result


def audit_skills(skills_dir: Path, baseline: frozenset = frozenset()) -> list[dict]:
    """Walk skills directory and audit every SKILL.md."""
    results = []
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        if skill_dir.name.startswith("."):
            continue
        if skill_dir.name == "archive":
            continue
        results.append(check_skill(skill_dir, baseline))
    return results


def print_report(results: list[dict], summary_only: bool = False,
                 baseline: frozenset = frozenset()) -> dict:
    """Print human-readable audit report. Returns counts dict."""
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0, "ERROR": 0}
    for r in results:
        counts[r["status"]] += 1

    total = len(results)
    print(f"\n{BOLD}Skill Metadata Audit{RESET}")
    print(f"{GRAY}{'=' * 60}{RESET}")
    print(f"Total skills: {total}")
    print(f"  {GREEN}PASS:{RESET}  {counts['PASS']}")
    print(f"  {YELLOW}WARN:{RESET}  {counts['WARN']}  (missing recommended fields only)")
    print(f"  {RED}FAIL:{RESET}  {counts['FAIL']}  (missing required fields or invalid values)")
    print(f"  {RED}ERROR:{RESET} {counts['ERROR']} (no SKILL.md or malformed frontmatter)")

    # Size budget (F-5.3). Always printed, even under --summary, so the pre-commit
    # hook (which runs with --summary) still surfaces every WARN/HARD line.
    size_hard = [r for r in results if r.get("size_status") == "HARD"]
    size_warn = [r for r in results if r.get("size_status") == "WARN"]
    if size_hard or size_warn:
        print(f"\n{BOLD}SKILL.md size budget{RESET} "
              f"{GRAY}(hard: <= {LINE_HARD_CAP} lines and <= {BYTE_HARD_CAP} bytes; "
              f"warn: >= {BYTE_WARN} bytes){RESET}")
        for r in size_hard:
            reasons = []
            if r["size_lines"] > LINE_HARD_CAP:
                reasons.append(f"{r['size_lines']} > {LINE_HARD_CAP} lines")
            if r["size_bytes"] > BYTE_HARD_CAP:
                reasons.append(f"{r['size_bytes']} > {BYTE_HARD_CAP} bytes")
            print(f"  {RED}HARD{RESET} {BOLD}{r['name']}{RESET}: {'; '.join(reasons)}  ({r['path']})")
        for r in size_warn:
            print(f"  {YELLOW}WARN{RESET} {r['name']}: {r['size_bytes']} bytes, {r['size_lines']} lines")

    # Trigger-coverage gate (F-6.1). Always printed (like size), so the pre-commit hook
    # and flagless CI both surface every MISSING / thin corpus / stale-baseline entry.
    # UNKNOWN is counted and printed. It used to be dropped on the floor, so a
    # one-skill tree whose only skill was unreadable printed four zeros - a
    # tally that accounted for none of the skills it had just walked.
    cov = {"COVERED": 0, "GRANDFATHERED": 0, "EXEMPT": 0, "MISSING": 0, "UNKNOWN": 0}
    for r in results:
        ts = r.get("triggers_status", "UNKNOWN")
        if ts in cov:
            cov[ts] += 1
    missing = [r for r in results if r.get("triggers_status") == "MISSING"]
    unknown = [r for r in results if r.get("triggers_status") == "UNKNOWN"]
    thin = [r for r in results if r.get("corpus_issues")]
    stale = [r for r in results if r["name"] in baseline and r.get("has_valid_corpus")]
    print(f"\n{BOLD}triggers.json coverage{RESET} "
          f"{GRAY}(auto-routable skills need a corpus: >= {TRIGGERS_MIN_CASES} cases, "
          f">= {TRIGGERS_MIN_POS} pos, >= {TRIGGERS_MIN_NEG} neg){RESET}")
    print(f"  {GREEN}COVERED:{RESET} {cov['COVERED']}  "
          f"{GRAY}GRANDFATHERED:{RESET} {cov['GRANDFATHERED']}  "
          f"{GRAY}EXEMPT:{RESET} {cov['EXEMPT']}  "
          f"{RED}MISSING:{RESET} {cov['MISSING']}  "
          f"{RED}UNKNOWN:{RESET} {cov['UNKNOWN']}")
    for r in unknown:
        print(f"  {RED}UNKNOWN{RESET} {BOLD}{r['name']}{RESET}: SKILL.md unreadable, "
              f"so routability was never established  ({r['path']})")
    for r in missing:
        print(f"  {RED}MISSING{RESET} {BOLD}{r['name']}{RESET}: auto-routable, no valid "
              f"triggers.json, not grandfathered  ({r['path']})")
    for r in thin:
        print(f"  {RED}THIN{RESET} {BOLD}{r['name']}{RESET}: {'; '.join(r['corpus_issues'])}")
    for r in stale:
        print(f"  {RED}STALE-BASELINE{RESET} {BOLD}{r['name']}{RESET}: has a valid corpus but "
              f"is still in config/triggers-coverage-baseline.json (run --write-baseline to shrink)")

    if summary_only:
        return counts

    for r in results:
        if r["status"] == "PASS":
            continue
        color = {"WARN": YELLOW, "FAIL": RED, "ERROR": RED}.get(r["status"], GRAY)
        print(f"\n{color}[{r['status']}]{RESET} {BOLD}{r['name']}{RESET}  ({r['path']})")
        if r["error"]:
            print(f"  {RED}error:{RESET} {r['error']}")
        if r["missing_required"]:
            print(f"  {RED}missing required:{RESET} {', '.join(r['missing_required'])}")
        if r["invalid_values"]:
            print(f"  {RED}invalid:{RESET} {'; '.join(r['invalid_values'])}")
        if r["missing_recommended"]:
            print(f"  {YELLOW}missing recommended:{RESET} {', '.join(r['missing_recommended'])}")

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit SKILL.md frontmatter completeness and enforce the size budget "
                    "(hard: <=500 lines and <=18432 bytes; warn >=16384 bytes). A HARD size "
                    "violation always exits 1, independent of --fail-on-missing.")
    parser.add_argument("--summary", action="store_true", help="Counts only, no per-skill detail")
    parser.add_argument("--fail-on-missing", action="store_true", help="Exit 1 if any skill has missing required fields")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of human report")
    parser.add_argument("--write-baseline", action="store_true",
                        help="Shrink-only regenerate config/triggers-coverage-baseline.json "
                             "(removes now-covered skills; never adds a new uncovered skill)")
    args = parser.parse_args()

    root = get_workspace_root()
    skills_dir = root / ".claude" / "skills"
    if not skills_dir.exists():
        print(f"{RED}skills directory not found:{RESET} {skills_dir}")
        return 2

    baseline = frozenset(load_baseline(root))
    results = audit_skills(skills_dir, baseline)

    # A shrink-only ratchet goes blind at zero. Every gate below is a loop over
    # `results`: with no skills to walk, the size budget, the coverage gate and
    # the stale-baseline check all say nothing and this function returns 0.
    # MEASURED 2026-09-01 against a scratch root holding an empty
    # `.claude/skills`: "Total skills: 0", four zeroes under `triggers.json
    # coverage`, exit 0. The directory-exists check above does not reach it,
    # because the directory was there; only its contents were gone.
    #
    # `--write-baseline` is the sharper edge, which is why this stands ahead of
    # it: with `results` empty, `still_uncovered` is empty, `existing &
    # still_uncovered` is empty, and one run over a collapsed walk rewrites the
    # committed grandfather set to `[]`.
    if not results:
        print(f"{RED}no skills found under{RESET} {skills_dir}\n"
              f"{GRAY}Every check in this script is a loop over the skills it "
              f"walked, so an empty walk reports clean. Refusing rather than "
              f"passing.{RESET}")
        return 2

    if args.write_baseline:
        written, excluded = write_baseline(root, results)
        rel = "/".join(BASELINE_REL)
        print(f"{GREEN}wrote{RESET} {rel}: {len(written)} grandfathered skill(s)")
        if excluded:
            print(f"{YELLOW}note:{RESET} {len(excluded)} new uncovered auto-routable skill(s) "
                  f"NOT added to the frozen baseline (they must ship a corpus): "
                  f"{', '.join(excluded)}")
        return 0

    if args.json:
        print(json.dumps({
            "total": len(results),
            "skills": results,
        }, indent=2))
    else:
        print_report(results, summary_only=args.summary, baseline=baseline)

    if args.fail_on_missing:
        n_fail = sum(1 for r in results if r["status"] == "FAIL")
        n_error = sum(1 for r in results if r["status"] == "ERROR")
        if n_fail > 0 or n_error > 0:
            return 1

    # Size budget is an UNCONDITIONAL gate (F-5.3): any HARD size violation exits 1
    # regardless of flags, so the flagless CI invocation enforces it with no
    # workflow-file change.
    if any(r.get("size_status") == "HARD" for r in results):
        return 1

    # Trigger-coverage gate (F-6.1) is also UNCONDITIONAL: a MISSING corpus (auto-routable,
    # not grandfathered), a thin/malformed present corpus, or a stale baseline entry
    # (baselined but now covered) exits 1 regardless of flags, so the flagless CI
    # invocation enforces coverage with no workflow-file change.
    # UNKNOWN is a failure, not a pass: it means the SKILL.md could not be read
    # or parsed, so nothing about this skill's routing was established. Leaving
    # it out is what let a broken SKILL.md plus a 1-case corpus exit 0.
    coverage_fail = (
        any(r.get("triggers_status") in ("MISSING", "UNKNOWN") for r in results)
        or any(r.get("corpus_issues") for r in results)
        or any(r["name"] in baseline and r.get("has_valid_corpus") for r in results)
    )
    if coverage_fail:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
