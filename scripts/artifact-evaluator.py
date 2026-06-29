#!/usr/bin/env python3
"""
artifact-evaluator.py - Deterministic quality evaluator for workspace artifacts.

Runs automated checks against workspace standards for skills, scripts, reference
files, and rules. Auto-detects artifact type from path. Outputs structured JSON
or colored terminal report.

Usage:
  python scripts/artifact-evaluator.py --path .claude/skills/dream
  python scripts/artifact-evaluator.py --path scripts/sanitize-text.py --json
  python scripts/artifact-evaluator.py --path .claude/skills/evaluate --plan plans/2026-03-26-harness.md
  python scripts/artifact-evaluator.py --path reference/voss-negotiation.md --strict
"""

import sys
import os
import re
import json
import subprocess
import argparse
import subprocess
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET

ROOT = get_workspace_root()


# ============================================================
# Configuration
# ============================================================


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# ============================================================
# Artifact Loading
# ============================================================


def check(name, passed, detail="", warn=False):
    """Build a single check result dict."""
    if warn and not passed:
        status = "warn"
    else:
        status = "pass" if passed else "fail"
    return {"name": name, "status": status, "detail": detail}


def load_accepted_warnings(artifact_path):
    """Load .eval-accept.json from the artifact's directory.

    File format:
    {
      "accepted": {
        "check_name": "Reason this warning is accepted"
      }
    }

    For skills: looks in the skill directory.
    For scripts/reference/rules: looks next to the file.
    """
    artifact_path = Path(artifact_path)
    if artifact_path.is_dir():
        accept_file = artifact_path / ".eval-accept.json"
    elif artifact_path.name == "SKILL.md":
        accept_file = artifact_path.parent / ".eval-accept.json"
    else:
        accept_file = artifact_path.parent / f".eval-accept.{artifact_path.stem}.json"

    if not accept_file.exists():
        return {}
    try:
        data = json.loads(accept_file.read_text(encoding="utf-8"))
        return data.get("accepted", {})
    except (json.JSONDecodeError, OSError):
        return {}


def apply_accepted_warnings(checks, accepted):
    """Downgrade accepted warnings from 'warn' to 'accepted'."""
    if not accepted:
        return checks
    for c in checks:
        if c["status"] == "warn" and c["name"] in accepted:
            c["status"] = "accepted"
            c["detail"] += f" [accepted: {accepted[c['name']]}]"
    return checks


def run_hidden_char_scan(file_path):
    """Delegate hidden-char scanning to sanitize-text.py --scan."""
    sanitizer = ROOT / "scripts" / "sanitize-text.py"
    try:
        result = subprocess.run(
            [sys.executable, str(sanitizer), "--scan", str(file_path)],
            capture_output=True, text=True, timeout=15
        )
        clean = result.returncode == 0
        detail = "Clean" if clean else result.stdout.strip()[:200]
        return check("hidden_chars", clean, detail)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return check("hidden_chars", False, f"Scanner error: {exc}")


def run_py_compile(file_path):
    """Check Python syntax via py_compile."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(file_path)],
            capture_output=True, text=True, timeout=15
        )
        ok = result.returncode == 0
        detail = "Compiles OK" if ok else result.stderr.strip()[:200]
        return check("py_compile", ok, detail)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return check("py_compile", False, f"Compile error: {exc}")


def parse_yaml_frontmatter(text):
    """Extract YAML frontmatter from text. Returns (dict, error_str|None)."""
    if not text.startswith("---"):
        return None, "No YAML frontmatter found"
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return None, "Invalid frontmatter format"
    try:
        import yaml
        data = yaml.safe_load(match.group(1))
        if not isinstance(data, dict):
            return None, "Frontmatter must be a YAML dictionary"
        return data, None
    except ImportError:
        # Fallback: basic key extraction without PyYAML
        data = {}
        for line in match.group(1).splitlines():
            if ":" in line and not line.startswith(" "):
                key, val = line.split(":", 1)
                data[key.strip()] = val.strip().strip('"').strip("'")
        return data, None
    except Exception as exc:
        return None, f"YAML parse error: {exc}"


# ============================================================
# Deterministic Checks
# ============================================================


# ---------------------------------------------------------------------------
# Skill checks
# ---------------------------------------------------------------------------

def evaluate_skill(skill_path):
    """Evaluate a skill directory or SKILL.md file."""
    skill_path = Path(skill_path)
    if skill_path.is_file() and skill_path.name == "SKILL.md":
        skill_dir = skill_path.parent
        skill_md = skill_path
    elif skill_path.is_dir():
        skill_dir = skill_path
        skill_md = skill_path / "SKILL.md"
    else:
        return [check("skill_exists", False, f"Not a valid skill path: {skill_path}")]

    results = []

    # Check SKILL.md exists
    if not skill_md.exists():
        return [check("skill_exists", False, "SKILL.md not found")]

    content = skill_md.read_text(encoding="utf-8")
    lines = content.splitlines()

    # Frontmatter
    fm, err = parse_yaml_frontmatter(content)
    if err:
        results.append(check("frontmatter_valid", False, err))
    else:
        results.append(check("frontmatter_valid", True, "YAML parses OK"))

        # Required fields
        required = ["name", "description"]
        missing = [f for f in required if f not in fm]
        results.append(check("required_fields", len(missing) == 0,
                             f"Missing: {', '.join(missing)}" if missing else "name, description present"))

        # Metadata
        meta = fm.get("metadata", {})
        if isinstance(meta, dict):
            has_author = "author" in meta
            has_version = "version" in meta
            ok = has_author and has_version
            detail = []
            if not has_author:
                detail.append("missing metadata.author")
            if not has_version:
                detail.append("missing metadata.version")
            results.append(check("metadata", ok, ", ".join(detail) if detail else "author + version present", warn=True))
        else:
            results.append(check("metadata", False, "metadata should be a dict", warn=True))

        # Name format (kebab-case)
        name = fm.get("name", "")
        if name and isinstance(name, str):
            kebab_ok = bool(re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$", name)) or (len(name) == 1 and name.isalpha())
            results.append(check("name_format", kebab_ok,
                                 f"'{name}' is kebab-case" if kebab_ok else f"'{name}' should be kebab-case"))

    # Line count
    line_count = len(lines)
    ok = line_count < 500
    results.append(check("line_count", ok,
                         f"{line_count} lines" + ("" if ok else " (max 500)"),
                         warn=(line_count >= 450 and ok)))

    # Phase structure
    phase_pattern = re.compile(r"^#{1,3}\s+(Phase|Step)\s+\d", re.IGNORECASE | re.MULTILINE)
    has_phases = bool(phase_pattern.search(content))
    results.append(check("phase_structure", has_phases,
                         "Phase/step headings found" if has_phases else "No phase/step structure detected",
                         warn=True))

    # Voice section
    voice_keywords = ["voice", "terminology", "style", "tone"]
    has_voice = any(kw in content.lower() for kw in voice_keywords)
    results.append(check("voice_section", has_voice,
                         "Voice/style section found" if has_voice else "No voice/terminology section",
                         warn=True))

    # NEVER section
    has_never = "never" in content.lower() and re.search(r"^#{1,3}.*never", content, re.IGNORECASE | re.MULTILINE)
    results.append(check("never_section", bool(has_never),
                         "NEVER section found" if has_never else "No explicit NEVER section",
                         warn=True))

    # Hidden chars on SKILL.md
    results.append(run_hidden_char_scan(skill_md))

    # Reference files
    refs_dir = skill_dir / "references"
    if refs_dir.is_dir():
        for ref_file in refs_dir.glob("*.md"):
            ref_content = ref_file.read_text(encoding="utf-8")
            ref_lines = ref_content.splitlines()
            issues = []
            if not ref_lines or not ref_lines[0].startswith("# "):
                issues.append("missing H1 title")
            if "consumed by" not in ref_content.lower():
                issues.append("missing 'Consumed by' pointer")
            if "last updated" not in ref_content.lower():
                issues.append("missing 'Last Updated' date")
            ok = len(issues) == 0
            results.append(check(f"ref_{ref_file.name}",
                                 ok,
                                 f"{ref_file.name}: {', '.join(issues)}" if issues else f"{ref_file.name}: OK",
                                 warn=True))

    return results


# ---------------------------------------------------------------------------
# Script checks
# ---------------------------------------------------------------------------

def evaluate_script(script_path):
    """Evaluate a Python script."""
    script_path = Path(script_path)
    results = []

    if not script_path.exists():
        return [check("file_exists", False, f"File not found: {script_path}")]

    content = script_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    # Module detection: files under scripts/utils/ (or any */utils/ package)
    # are library modules imported by scripts, not standalone CLIs. Exempt
    # them from shebang and __main__ guard requirements, which only apply to
    # runnable scripts.
    parts = script_path.resolve().parts
    is_module = "utils" in parts and script_path.suffix == ".py"

    # Shebang (skipped for library modules)
    if is_module:
        results.append(check("shebang", True, "module (shebang not required)"))
    else:
        has_shebang = lines and lines[0].startswith("#!")
        results.append(check("shebang", has_shebang,
                             lines[0] if has_shebang else "No shebang line"))

    # Module docstring
    has_docstring = '"""' in content[:500] or "'''" in content[:500]
    results.append(check("docstring", has_docstring,
                         "Module docstring present" if has_docstring else "No module docstring"))

    # Usage in docstring
    has_usage = "usage" in content[:1000].lower()
    results.append(check("usage_docs", has_usage,
                         "Usage documented" if has_usage else "No Usage examples in docstring",
                         warn=True))

    # The next three checks (workspace import, standard colors, argparse CLI)
    # are CLI-script conventions. Library modules under */utils/ are imported,
    # not run: they take paths/hosts as arguments (no workspace coupling), let
    # callers handle terminal output (no colors), and expose functions (no
    # argparse). Exempt them, mirroring the shebang/__main__ exemption above.
    if is_module:
        results.append(check("workspace_import", True, "module (workspace import not required)"))
        results.append(check("colors_import", True, "module (standard colors not required)"))
        results.append(check("argparse", True, "module (argparse CLI not required)"))
    else:
        # Workspace imports
        has_workspace_import = "get_workspace_root" in content or "scripts.utils.workspace" in content
        results.append(check("workspace_import", has_workspace_import,
                             "Uses workspace utilities" if has_workspace_import else "No workspace import (should use get_workspace_root)",
                             warn=True))

        # Colors import
        has_colors = "scripts.utils.colors" in content
        results.append(check("colors_import", has_colors,
                             "Uses standard colors" if has_colors else "No colors import from scripts.utils.colors",
                             warn=True))

        # Argparse
        has_argparse = "argparse" in content
        results.append(check("argparse", has_argparse,
                             "argparse CLI present" if has_argparse else "No argparse CLI interface",
                             warn=True))

    # __main__ guard (skipped for library modules)
    if is_module:
        results.append(check("main_guard", True, "module (__main__ guard not required)"))
    else:
        has_main = '__name__' in content and '__main__' in content
        results.append(check("main_guard", has_main,
                             "__main__ guard present" if has_main else "Missing if __name__ == '__main__' guard"))

    # py_compile
    results.append(run_py_compile(script_path))

    # Hidden chars
    results.append(run_hidden_char_scan(script_path))

    # pathlib usage (check for os.path usage that should be pathlib)
    os_path_count = content.count("os.path.")
    pathlib_count = content.count("Path(") + content.count("pathlib")
    if os_path_count > 0 and pathlib_count == 0:
        results.append(check("pathlib_usage", False,
                             f"Uses os.path ({os_path_count}x) without pathlib - prefer pathlib.Path",
                             warn=True))
    else:
        results.append(check("pathlib_usage", True,
                             f"pathlib: {pathlib_count} refs" + (f", os.path: {os_path_count}" if os_path_count else "")))

    return results


# ---------------------------------------------------------------------------
# Reference file checks
# ---------------------------------------------------------------------------

def evaluate_reference(file_path):
    """Evaluate a reference markdown file."""
    file_path = Path(file_path)
    results = []

    if not file_path.exists():
        return [check("file_exists", False, f"File not found: {file_path}")]

    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    # H1 on line 1
    has_h1 = lines and lines[0].startswith("# ")
    results.append(check("h1_title", has_h1,
                         f"Title: {lines[0]}" if has_h1 else "No H1 title on line 1"))

    # One-line description (line 2 or 3 should be non-empty text)
    desc_found = False
    for line in lines[1:5]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
            desc_found = True
            break
    results.append(check("description", desc_found,
                         "Description found after title" if desc_found else "No description after H1",
                         warn=True))

    # Last Updated
    has_updated = bool(re.search(r"last\s+updated", content, re.IGNORECASE))
    results.append(check("last_updated", has_updated,
                         "Last Updated marker present" if has_updated else "No 'Last Updated' date",
                         warn=True))

    # Section headers
    h2_count = len(re.findall(r"^##\s+", content, re.MULTILINE))
    results.append(check("section_structure", h2_count >= 2,
                         f"{h2_count} section headers" if h2_count >= 2 else f"Only {h2_count} section headers (need 2+)",
                         warn=True))

    # Hidden chars
    results.append(run_hidden_char_scan(file_path))

    return results


# ---------------------------------------------------------------------------
# Rule checks
# ---------------------------------------------------------------------------

def evaluate_rule(file_path):
    """Evaluate a rule markdown file."""
    file_path = Path(file_path)
    results = []

    if not file_path.exists():
        return [check("file_exists", False, f"File not found: {file_path}")]

    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    # Strip YAML frontmatter before checking H1 (rules may have frontmatter like paths: ...)
    body_start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                body_start = i + 1
                # skip blank lines after frontmatter
                while body_start < len(lines) and not lines[body_start].strip():
                    body_start += 1
                break
    body_lines = lines[body_start:]

    # H1 title
    has_h1 = body_lines and body_lines[0].startswith("# ")
    results.append(check("h1_title", has_h1,
                         f"Title: {body_lines[0]}" if has_h1 else "No H1 title"))

    # Concise line-count threshold: registry/orchestrator/standards rules are
    # intrinsically larger (skill tables, workflow patterns, standards catalogs)
    # and get a relaxed 250-line budget.
    stem = file_path.stem.lower()
    registry_like = any(tag in stem for tag in ("router", "orchestrator", "registry", "standards"))
    threshold = 250 if registry_like else 80
    line_count = len(lines)
    ok = line_count < threshold
    results.append(check("concise", ok,
                         f"{line_count} lines" + ("" if ok else f" (rules should be < {threshold} lines)"),
                         warn=(line_count >= int(threshold * 0.75) and ok)))

    # Hidden chars
    results.append(run_hidden_char_scan(file_path))

    return results


# ============================================================
# Scoring / Grading
# ============================================================


# ---------------------------------------------------------------------------
# Plan criteria evaluation
# ---------------------------------------------------------------------------

def evaluate_plan_criteria(plan_path):
    """Extract and check success criteria from a plan file."""
    plan_path = Path(plan_path)
    if not plan_path.exists():
        return [check("plan_exists", False, f"Plan not found: {plan_path}")]

    content = plan_path.read_text(encoding="utf-8")
    results = []

    # Find Success Criteria section
    criteria_match = re.search(
        r"##\s+Success\s+Criteria(.*?)(?=\n##\s|\Z)",
        content, re.DOTALL | re.IGNORECASE
    )
    if not criteria_match:
        return [check("plan_criteria", False, "No Success Criteria section in plan")]

    criteria_text = criteria_match.group(1)
    # Extract numbered or bulleted items
    items = re.findall(r"(?:^|\n)\s*[-*\d.]+\s*(.+)", criteria_text)

    for i, item in enumerate(items, 1):
        item = item.strip()
        # Try to verify simple file-existence criteria
        file_match = re.search(r"`([^`]+)`", item)
        if file_match:
            ref_path = ROOT / file_match.group(1)
            if ref_path.exists():
                results.append(check(f"criterion_{i}", True, f"{item} - file exists"))
            else:
                results.append(check(f"criterion_{i}", False, f"{item} - file NOT found: {file_match.group(1)}"))
        else:
            results.append(check(f"criterion_{i}", None, f"{item} - requires manual verification"))

    return results


# ---------------------------------------------------------------------------
# Type detection
# ---------------------------------------------------------------------------

def detect_type(path_str):
    """Auto-detect artifact type from path."""
    p = Path(path_str)
    resolved = str(p).replace("\\", "/")

    # SKILL.md itself is always a skill
    if p.name == "SKILL.md":
        return "skill"
    if ".claude/skills/" in resolved:
        # Skill-scoped reference file -> evaluate as reference, not skill
        if "/references/" in resolved and resolved.endswith(".md"):
            return "reference"
        # Skill directory itself
        if p.is_dir():
            return "skill"
        # Other markdown inside a skill dir (e.g. docs/notes) -> reference
        if resolved.endswith(".md"):
            return "reference"
    if resolved.endswith(".py") and ("scripts/" in resolved or "scripts\\" in str(p)):
        return "script"
    if ".claude/rules/" in resolved:
        return "rule"
    if "reference/" in resolved and resolved.endswith(".md"):
        return "reference"
    # Fallback heuristics
    if resolved.endswith(".py"):
        return "script"
    if resolved.endswith(".md"):
        return "reference"
    return "unknown"


# ============================================================
# Report Generation
# ============================================================


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

STATUS_SYMBOLS = {
    "pass": f"{GREEN}PASS{RESET}",
    "warn": f"{YELLOW}WARN{RESET}",
    "fail": f"{RED}FAIL{RESET}",
    "accepted": f"{GRAY} OK {RESET}",
}

STATUS_SYMBOLS_PLAIN = {
    "pass": "PASS",
    "warn": "WARN",
    "fail": "FAIL",
    "accepted": " OK ",
}


def print_report(artifact_path, artifact_type, checks, plan_criteria=None):
    """Print colored terminal report."""
    print(f"\n{BOLD}Artifact Evaluation{RESET}")
    print(f"  Path: {CYAN}{artifact_path}{RESET}")
    print(f"  Type: {artifact_type}")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    passed = sum(1 for c in checks if c["status"] in ("pass", "accepted"))
    warned = sum(1 for c in checks if c["status"] == "warn")
    failed = sum(1 for c in checks if c["status"] == "fail")
    accepted = sum(1 for c in checks if c["status"] == "accepted")
    total = len(checks)

    for c in checks:
        symbol = STATUS_SYMBOLS.get(c["status"], "?")
        print(f"  {symbol}  {c['name']}: {GRAY}{c['detail']}{RESET}")

    print()
    color = GREEN if failed == 0 and warned == 0 else (RED if failed else YELLOW)
    print(f"  {color}{BOLD}Score: {passed}/{total} passed{RESET}"
          + (f", {YELLOW}{warned} warnings{RESET}" if warned else "")
          + (f", {RED}{failed} failures{RESET}" if failed else "")
          + (f", {GRAY}{accepted} accepted{RESET}" if accepted else ""))

    if plan_criteria:
        print(f"\n{BOLD}Plan Criteria{RESET}")
        for c in plan_criteria:
            if c["status"] is None:
                symbol = f"{GRAY}----{RESET}"
            else:
                symbol = STATUS_SYMBOLS.get("pass" if c["status"] else "fail", "?")
            print(f"  {symbol}  {c['detail']}")

    print()


def run_trigger_test(artifact_path, threshold=0.9):
    """Advisory: shell out to skill-trigger-test.py for a skill with a sibling triggers.json.

    Returns a single check dict (status pass/warn, never fail - the LLM-judge is
    non-deterministic, so a routing miss is a warning, not a hard evaluation failure).
    Returns None when the artifact is not a skill or has no triggers.json (nothing to add).
    """
    skill_dir = Path(artifact_path)
    if skill_dir.is_file() and skill_dir.name == "SKILL.md":
        skill_dir = skill_dir.parent
    if not skill_dir.is_dir() or not (skill_dir / "triggers.json").exists():
        return None
    skill_name = skill_dir.name

    runner = ROOT / "scripts" / "skill-trigger-test.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(runner), "--skill", skill_name, "--json",
             "--threshold", str(threshold)],
            capture_output=True, text=True, timeout=300,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return check("trigger_test", True, f"trigger-test could not run: {e}", warn=True)

    if proc.returncode == 3:
        # Degraded: no API key or SDK. Advisory skip, not a failure.
        return check("trigger_test", True, "trigger-test skipped (no ANTHROPIC_API_KEY / SDK)", warn=False)
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return check("trigger_test", True,
                     f"trigger-test output unparseable (exit {proc.returncode})", warn=True)

    rate = data.get("overall_rate", 0.0)
    passed_n = data.get("total_passed", 0)
    total_n = data.get("total_cases", 0)
    ok = rate >= threshold
    detail = f"routing pass-rate {rate:.0%} ({passed_n}/{total_n}, threshold {threshold:.0%})"
    return check("trigger_test", ok, detail, warn=True)


def build_json_output(artifact_path, artifact_type, checks, plan_criteria=None):
    """Build JSON output dict."""
    passed = sum(1 for c in checks if c["status"] in ("pass", "accepted"))
    warned = sum(1 for c in checks if c["status"] == "warn")
    failed = sum(1 for c in checks if c["status"] == "fail")
    accepted = sum(1 for c in checks if c["status"] == "accepted")
    total = len(checks)
    score = passed / total if total > 0 else 0.0

    output = {
        "artifact_path": str(artifact_path),
        "artifact_type": artifact_type,
        "timestamp": datetime.now().isoformat(),
        "checks": checks,
        "summary": {
            "total": total,
            "passed": passed,
            "warned": warned,
            "failed": failed,
            "accepted": accepted,
            "score": round(score, 2),
        },
    }
    if plan_criteria:
        output["plan_criteria"] = plan_criteria
    return output


# ============================================================
# Main / CLI
# ============================================================


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Deterministic quality evaluator for workspace artifacts."
    )
    parser.add_argument("--path", required=True, help="Path to artifact (file or skill directory)")
    parser.add_argument("--type", choices=["skill", "script", "reference", "rule"],
                        help="Artifact type (auto-detected if omitted)")
    parser.add_argument("--plan", help="Plan file to grade against success criteria")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of terminal report")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    parser.add_argument("--trigger-test", action="store_true",
                        help="For a skill with a triggers.json, also run the LLM-judge routing test "
                             "(advisory; folds the pass-rate into the report, never hard-fails)")
    parser.add_argument("--trigger-threshold", type=float, default=0.9,
                        help="Pass-rate threshold for --trigger-test (default 0.9)")
    args = parser.parse_args()

    artifact_path = Path(args.path)
    if not artifact_path.is_absolute():
        artifact_path = ROOT / artifact_path

    artifact_type = args.type or detect_type(args.path)

    if artifact_type == "unknown":
        print(f"{RED}Cannot detect artifact type for: {args.path}{RESET}", file=sys.stderr)
        print("Use --type to specify: skill, script, reference, rule", file=sys.stderr)
        sys.exit(1)

    # Run checks
    evaluators = {
        "skill": evaluate_skill,
        "script": evaluate_script,
        "reference": evaluate_reference,
        "rule": evaluate_rule,
    }
    checks = evaluators[artifact_type](artifact_path)

    # Apply accepted warnings (from .eval-accept.json)
    accepted = load_accepted_warnings(artifact_path)
    checks = apply_accepted_warnings(checks, accepted)

    # Strict mode: convert remaining warns to fails (accepted stay accepted)
    if args.strict:
        for c in checks:
            if c["status"] == "warn":
                c["status"] = "fail"

    # Optional advisory trigger-test (skills only). Appended AFTER the strict
    # conversion so a non-deterministic routing miss never becomes a hard fail.
    if args.trigger_test and artifact_type == "skill":
        tt = run_trigger_test(artifact_path, args.trigger_threshold)
        if tt is not None:
            checks.append(tt)

    # Plan criteria
    plan_criteria = None
    if args.plan:
        plan_path = Path(args.plan)
        if not plan_path.is_absolute():
            plan_path = ROOT / plan_path
        plan_criteria = evaluate_plan_criteria(plan_path)

    # Output
    if args.json:
        output = build_json_output(artifact_path, artifact_type, checks, plan_criteria)
        print(json.dumps(output, indent=2))
    else:
        print_report(artifact_path, artifact_type, checks, plan_criteria)

    # Exit code
    has_failures = any(c["status"] == "fail" for c in checks)
    sys.exit(1 if has_failures else 0)


if __name__ == "__main__":
    main()
