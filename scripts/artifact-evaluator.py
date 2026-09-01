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
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_default_tz, get_workspace_root
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET
from scripts.utils import markdown as md
from scripts.utils.markdown import split_frontmatter

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
    """Build a single check result dict.

    `passed=None` means UNVERIFIABLE and is passed through as `status=None`.
    It used to be folded into the falsy branch, so every plan criterion that
    "requires manual verification" was stamped `"fail"` in `--json` -- an
    unverified item reported as a verified failure, which any pipeline reading
    that field would rightly block on.
    """
    if passed is None:
        return {"name": name, "status": None, "detail": detail}
    status = "warn" if (warn and not passed) else ("pass" if passed else "fail")
    return {"name": name, "status": status, "detail": detail}


def read_artifact(path):
    """(text, None) when the artifact could be read, (None, failed check) when not.

    Every `evaluate_*` below opened its artifact with a bare `read_text`. The
    DECODE half of that was guarded on 2026-09-01 by adding
    `errors="replace"`; the OPEN half was not, so a path the evaluator was
    pointed at and could not open - mode 000, a directory, a file that vanished
    between the `exists()` above it and the read - ended the whole run on a
    traceback before a single check existed.

    MEASURED 2026-09-01 on a chmod 000 reference file: `--json` printed a
    `PermissionError` stack trace and exited 1, so a caller parsing the JSON got
    nothing at all to parse. An artifact that cannot be read is a FAILED CHECK
    naming the path, never a crash and never a silent pass.
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace"), None
    except OSError as exc:
        return None, check("file_readable", False, f"cannot read {path}: {exc}")


def has_consumed_by_pointer(text):
    """True when the text carries a "Consumed by:" pointer as a LINE LABEL.

    `.claude/rules/development-standards.md` § Reference File Standards requires
    a skill reference file to name the skill that reads it. The requirement is a
    labelled pointer, so this splits each line on its first colon and compares
    the label. It used to be `"consumed by" not in text.lower()`, a substring
    scan that any prose sentence satisfies: MEASURED 2026-08-31 on a fixture
    whose only occurrence was "This data is consumed by downstream tooling
    somewhere", and this evaluator stamped the file `pass`. That is worse than
    the missing warning it sits beside, because a false OK is read as coverage.

    The corpus-wide gate for this rule is
    tests/test_two_skill_contracts_that_were_declared_and_never_measured.py,
    which runs in CI over every reference file. This function exists so the
    single-artifact advisory report agrees with that gate instead of
    contradicting it; `test_the_evaluator_agrees_with_the_corpus_gate`, in that
    same file, pins the two together on the live corpus. That name was
    `test_artifact_evaluator_consumed_by.py` until 2026-08-31, a file which has
    never existed in this repository -- a citation to a nonexistent proof reads
    as coverage exactly like the substring scan this function replaced.

    `lstrip("-*")` below is a character SET on purpose, and is declared as such
    in `tests/test_a_quote_marker_that_ate_the_claim.py`.
    """
    body = text
    if body.startswith("---\n"):
        end = body.find("\n---", 4)
        if end != -1:
            body = body[end + 4:]
    for raw in body.splitlines():
        line = raw.strip().lstrip(">").strip().lstrip("-*").strip()
        if ":" not in line:
            continue
        label = line.split(":", 1)[0]
        label = label.replace("*", "").replace("_", "").replace("`", "").strip()
        if label.lower() == "consumed by":
            return True
    return False


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
        # stderr too. The scanner reports an unreadable path there, and until
        # 2026-08-25 that path exited 0 and was reported here as "Clean"; now
        # that it exits 2, reading stdout alone would report a failure with an
        # empty reason.
        detail = "Clean" if clean else (
            result.stdout.strip() or result.stderr.strip())[:200]
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
    """Extract YAML frontmatter from text. Returns (dict, error_str|None).

    The FENCES come from ``scripts.utils.markdown.split_frontmatter``; the YAML
    policy stays here, because this evaluator must keep working on a host with
    no PyYAML and its ``_frontmatter_without_pyyaml`` fallback is better than the
    shared module's generic one.

    Its own match was `re.match(r"^---\\r?\\n(.*?)\\r?\\n---", text, re.DOTALL)`,
    which ends the block at the three characters wherever they land. MEASURED
    2026-08-28 on `description: drift --- check`: the block was cut at the
    embedded dashes, so the mapping came back truncated and every later check
    here -- required fields, metadata shape -- judged a file it had only half
    read. The CRLF tolerance the previous comment describes is preserved by the
    shared splitter's fence-line regex.
    """
    block, _body, kind = split_frontmatter(text)
    if kind == md.FM_NO_OPENING:
        return None, "No YAML frontmatter found"
    if kind == md.FM_NO_CLOSING:
        return None, "Invalid frontmatter format"
    try:
        import yaml
        data = yaml.safe_load(block)
        if not isinstance(data, dict):
            return None, "Frontmatter must be a YAML dictionary"
        return data, None
    except ImportError:
        return _frontmatter_without_pyyaml(block), None
    except Exception as exc:
        return None, f"YAML parse error: {exc}"


def _frontmatter_without_pyyaml(block):
    """One level of nesting, for the host that has no PyYAML.

    The fallback skipped every indented line, so a nested block collapsed to its
    own header: the standard skill shape

        metadata:
          author: x
          version: "1.0"

    parsed as ``{"metadata": ""}``. `evaluate_skill` then asks
    ``isinstance(fm.get("metadata", {}), dict)``, which a string fails, so on a
    machine without PyYAML EVERY skill drew a `metadata should be a dict`
    warning no matter how correct its frontmatter was. The fallback could not
    satisfy the evaluator's own requirement -- the check was not measuring the
    artifact, it was measuring whether PyYAML was installed.

    Deliberately a subset of YAML: one level of nesting, scalars and simple
    lists, which is the whole shape this evaluator reads. Anything deeper needs
    PyYAML, which is a core dependency here; this path exists for a bare
    interpreter, not as a second parser to maintain.
    """
    def _scalar(raw):
        return raw.strip().strip('"').strip("'")

    data = {}
    top = None       # the top-level key whose nested block is open
    sub = None       # the second-level key whose list is open, inside `top`
    for line in block.splitlines():
        body = line.strip()
        if not body or body.startswith("#"):
            continue
        if line[0] not in " \t":
            sub = None
            if ":" not in body:
                top = None
                continue
            key, val = body.split(":", 1)
            key, val = key.strip(), _scalar(val)
            data[key] = val
            # An empty value opens a nested block; a scalar closes any open one.
            top = key if val == "" else None
            continue
        if top is None:
            continue
        if body.startswith("- "):
            item = _scalar(body[2:])
            if sub is None:
                # A list directly under the top-level key.
                if not isinstance(data.get(top), list):
                    data[top] = []
                data[top].append(item)
            else:
                # A list under a second-level key. Appending to `data[top]`
                # here REPLACED the mapping that key's siblings live in, so
                # `shared_state:` wiped `parallel_safe` off the block above it.
                container = data[top]
                if not isinstance(container.get(sub), list):
                    container[sub] = []
                container[sub].append(item)
        elif ":" in body:
            if not isinstance(data.get(top), dict):
                data[top] = {}
            sub_key, sub_val = body.split(":", 1)
            sub_key, sub_val = sub_key.strip(), _scalar(sub_val)
            data[top][sub_key] = sub_val
            sub = sub_key if sub_val == "" else None
    return data


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

    # `errors="replace"`. Every read in this file feeds a list of NAMED
    # checks with pass/warn/fail statuses, and a bare utf-8 decode raised
    # UnicodeDecodeError - a ValueError, caught nowhere between here and
    # `main` - so one stray byte in the artifact under review replaced the
    # whole report with a traceback. Replacing the byte lets the evaluator
    # say what it found; the artifact is corrupt either way, and that shows
    # up in the checks rather than in a stack trace. The OPEN half is
    # `read_artifact`, for the reason in its docstring.
    content, unreadable = read_artifact(skill_md)
    if unreadable:
        return [unreadable]
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
            # `errors="replace"` for the reason given at the first read above.
            ref_content, unreadable = read_artifact(ref_file)
            if unreadable:
                results.append(unreadable)
                continue
            ref_lines = ref_content.splitlines()
            issues = []
            if not ref_lines or not ref_lines[0].startswith("# "):
                issues.append("missing H1 title")
            if not has_consumed_by_pointer(ref_content):
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

    # `errors="replace"` for the reason given at the first read above.
    content, unreadable = read_artifact(script_path)
    if unreadable:
        return [unreadable]
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

    # `errors="replace"` for the reason given at the first read above.
    content, unreadable = read_artifact(file_path)
    if unreadable:
        return [unreadable]
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

    # `errors="replace"` for the reason given at the first read above.
    content, unreadable = read_artifact(file_path)
    if unreadable:
        return [unreadable]
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

    # `errors="replace"` for the reason given at the first read above.
    content, unreadable = read_artifact(plan_path)
    if unreadable:
        return [unreadable]
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



def print_report(artifact_path, artifact_type, checks, plan_criteria=None):
    """Print colored terminal report."""
    print(f"\n{BOLD}Artifact Evaluation{RESET}")
    print(f"  Path: {CYAN}{artifact_path}{RESET}")
    print(f"  Type: {artifact_type}")
    print(f"  Time: {datetime.now(get_default_tz()).strftime('%Y-%m-%d %H:%M:%S')}")
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
            # Look the status up directly. This read `"pass" if c["status"]
            # else "fail"`, and `c["status"]` is a STRING -- so `"fail"` is
            # truthy and every failed criterion printed as PASS, while the
            # `is None` arm above was dead because `check` could not emit None.
            # The terminal said everything passed; --json said the manual items
            # failed. Two opposite lies about the same list.
            symbol = (f"{GRAY}----{RESET}" if c["status"] is None
                      else STATUS_SYMBOLS.get(c["status"], "?"))
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
        # passed=False, or `warn=True` does nothing. `check` computes
        # `"warn" if (warn and not passed) else ("pass" if passed else "fail")`,
        # so warn is inert whenever passed is True: this returned
        # `"status": "pass"` carrying the detail "trigger-test could not run".
        # A check that never ran was reported as a check that passed, to the
        # terminal report and to any pipeline reading the JSON. The skip two
        # branches down passes `warn=False` for a genuine clean pass, which is
        # what shows the two cases were meant to differ.
        return check("trigger_test", False, f"trigger-test could not run: {e}", warn=True)

    if proc.returncode == 3:
        # Degraded: no API key or SDK. Advisory skip, not a failure.
        return check("trigger_test", True, "trigger-test skipped (no ANTHROPIC_API_KEY / SDK)", warn=False)
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return check("trigger_test", False,
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
        "timestamp": datetime.now(timezone.utc).isoformat(),
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

    # The ROOT-anchored path, not the raw argument. `detect_type` tests
    # `p.is_dir()`, which resolves against the process cwd, while main()
    # evaluates `ROOT / args.path`. Run from anywhere else with a relative
    # skill directory, detection said "unknown" and exited 1 on a skill
    # the evaluator was about to read successfully.
    artifact_type = args.type or detect_type(artifact_path)

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
    #
    # `plan_criteria` counts. It used to be built, printed, put in the JSON, and
    # then left out of this line entirely: a plan whose success criteria named a
    # file that does not exist produced `"status": "fail"` and exit 0, so every
    # gate keyed on the exit code (the normal contract here) waved it through.
    # An unverifiable criterion carries status None, not "fail", so a criterion
    # reading "requires manual verification" still does not fail the run.
    failed = [c for c in [*checks, *(plan_criteria or [])] if c["status"] == "fail"]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
