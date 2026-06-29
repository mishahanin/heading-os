#!/usr/bin/env python3
"""apply-wizard-answers.py -- sole writer for setup wizard state and output files.

See docs/superpowers/specs/2026-04-24-setup-wizard-design.md section 8 for the full contract.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import yaml
from datetime import datetime, timezone
from pathlib import Path

__version__ = "0.1.0"

# ============================================================
# Constants / Exit Codes
# ============================================================
EXIT_OK = 0
EXIT_SCHEMA_ERROR = 1
EXIT_FILE_WRITE_ERROR = 2
# Exit code 3 is reserved for future audience-mismatch scenarios (spec section 8.1).
EXIT_CEO_MASTER_WITHOUT_FORCE = 4
EXIT_UNKNOWN_ID = 5  # user asked for a question id that's not in the audience-filtered bank


# ============================================================
# Schema & Path Resolution
# ============================================================
class SchemaError(Exception):
    """Raised when workspace identity or question bank is malformed."""


VALID_IDENTITY_TYPES = {"ceo-master", "exec-workspace"}

QUESTIONS_REL_PATH = Path("config") / "wizard-questions.yaml"
ANSWERS_REL_PATH = Path(".setup") / "answers.json"
LOG_REL_PATH = Path(".setup") / "wizard.log"


def resolve_read_path(workspace_root: Path, rel_path) -> Path:
    """Resolve a read-only config/template path across workspace layouts.

    CEO workspace has config/ and wizard-templates/ at the root. Exec
    workspaces classify config/ as corporate, so the same files live under
    corporate/config/ and corporate/config/wizard-templates/. This helper
    checks the root layout first, then falls back to corporate/. Returns the
    primary (root) path if neither exists so error messages point to the
    expected location.
    """
    rel = Path(rel_path)
    primary = workspace_root / rel
    if primary.exists():
        return primary
    fallback = workspace_root / "corporate" / rel
    if fallback.exists():
        return fallback
    return primary
SCHEMA_VERSION = 1
VALID_TYPES = {"placeholder", "rich", "secret", "list"}
VALID_AUDIENCES = {"public", "exec"}
# All fields the loader will accept silently. Fields outside this set trigger a
# stderr warning (not a failure) so typos like `audiance` surface early.
ALLOWED_QUESTION_FIELDS = {
    "id", "audience", "type", "required", "prompt", "example", "target",
    "help", "depends_on",
}

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules",
             ".sessions", ".setup", ".sentinel"}
PROCESSABLE_EXTENSIONS = {".md", ".py", ".yaml", ".yml", ".json", ".txt", ".html", ".tmpl"}

_PLACEHOLDER_TOKEN_RE = re.compile(r"\{[A-Z_][A-Z0-9_]*\}")

_VAR_RE = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
# Intentional subset: no nested {% if %}. Non-greedy match terminates at the
# FIRST {% endif %}, so `{% if a %}{% if b %}x{% endif %}{% endif %}` would close
# the inner if and leave the outer endif as literal text. For nested conditions,
# restructure the template. Current shipped templates (Task 3) do not use nesting.
_IF_BLOCK_RE = re.compile(
    r"\{%\s*if\s+([a-z_][a-z0-9_]*)\s*%\}(.*?)\{%\s*endif\s*%\}",
    re.DOTALL,
)


def detect_audience(workspace_root: Path) -> str:
    """Return 'ceo-master', 'exec', or 'public'.

    Reads .workspace-identity.json at workspace_root. Absent = 'public'.
    Raises SchemaError on malformed JSON or unknown 'type' value.
    """
    identity_path = workspace_root / ".workspace-identity.json"
    if not identity_path.exists():
        return "public"
    try:
        data = json.loads(identity_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SchemaError(f".workspace-identity.json is malformed: {e}") from e
    type_ = data.get("type")
    if type_ not in VALID_IDENTITY_TYPES:
        raise SchemaError(
            f".workspace-identity.json has unknown type {type_!r}. "
            f"Expected one of {VALID_IDENTITY_TYPES}."
        )
    return "exec" if type_ == "exec-workspace" else "ceo-master"


# ============================================================
# State / Answer Persistence
# ============================================================
def load_questions(workspace_root: Path) -> list[dict]:
    """Load and validate config/wizard-questions.yaml. Raise SchemaError on problems."""
    path = resolve_read_path(workspace_root, QUESTIONS_REL_PATH)
    if not path.exists():
        raise SchemaError(f"Question bank not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise SchemaError(f"Question bank YAML parse error: {e}") from e
    if not isinstance(data, list):
        raise SchemaError("Question bank must be a YAML list")
    ids_seen: set[str] = set()
    for i, q in enumerate(data):
        if not isinstance(q, dict):
            raise SchemaError(f"Question #{i} is not a mapping")
        for field in ("id", "audience", "type", "required", "prompt", "example", "target"):
            if field not in q:
                raise SchemaError(f"Question #{i} missing field {field!r}")
        if q["id"] in ids_seen:
            raise SchemaError(f"duplicate question id: {q['id']!r}")
        ids_seen.add(q["id"])
        if q["type"] not in VALID_TYPES:
            raise SchemaError(f"Question {q['id']!r} has invalid type {q['type']!r}")
        if not isinstance(q["audience"], list) or not q["audience"]:
            raise SchemaError(f"Question {q['id']!r} audience must be a non-empty list")
        for aud in q["audience"]:
            if aud not in VALID_AUDIENCES:
                raise SchemaError(f"Question {q['id']!r} has invalid audience {aud!r}")
        if "depends_on" in q:
            dep = q["depends_on"]
            if not isinstance(dep, dict) or "question" not in dep or "equals" not in dep:
                raise SchemaError(
                    f"Question {q['id']!r}: depends_on must be a dict with 'question' and 'equals'"
                )
            if not isinstance(dep["question"], str):
                raise SchemaError(
                    f"Question {q['id']!r}: depends_on.question must be a string"
                )
        # Warn on unknown top-level fields (catches typos like `audiance` vs `audience`).
        unknown = set(q.keys()) - ALLOWED_QUESTION_FIELDS
        if unknown:
            sys.stderr.write(
                f"WARNING: question {q['id']!r} has unknown field(s): {sorted(unknown)}\n"
            )
    # Second pass: verify every depends_on references an existing id (bank is fully loaded now).
    bank_ids = ids_seen
    for q in data:
        if "depends_on" in q and q["depends_on"]["question"] not in bank_ids:
            raise SchemaError(
                f"Question {q['id']!r}: depends_on parent {q['depends_on']['question']!r} not in bank"
            )
    return data


def filter_by_audience(questions: list[dict], audience: str) -> list[dict]:
    """Return the subset of questions relevant to the given audience."""
    return [q for q in questions if audience in q["audience"]]


def load_answers(workspace_root: Path) -> dict:
    """Return the answers.json state dict. Returns an empty skeleton if missing."""
    path = workspace_root / ANSWERS_REL_PATH
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "audience": None,
            "started_at": None,
            "last_updated": None,
            "applied_at": None,
            "answers": {},
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise SchemaError(
            f"answers.json schema_version {data.get('schema_version')} "
            f"incompatible with expected {SCHEMA_VERSION}"
        )
    return data


def save_answers(workspace_root: Path, state: dict) -> None:
    """Atomic write of state dict to .setup/answers.json."""
    setup_dir = workspace_root / ".setup"
    setup_dir.mkdir(exist_ok=True)
    path = setup_dir / "answers.json"
    tmp = path.with_suffix(".json.tmp")
    state["last_updated"] = _now_iso()
    if state.get("started_at") is None:
        state["started_at"] = state["last_updated"]
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ============================================================
# Helpers / Utilities
# ============================================================
def _now_iso() -> str:
    """ISO 8601 with timezone offset for timestamps in answers.json."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _is_valid_env_var_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Z_][A-Z0-9_]*", name))


def _upsert_env_line(env_path: Path, key: str, value: str) -> None:
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    updated = False
    new_line = f"{key}={value}"
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = new_line
            updated = True
            break
    if not updated:
        lines.append(new_line)
    tmp = env_path.with_suffix(env_path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, env_path)
    if os.name == "posix":
        os.chmod(env_path, 0o600)
    elif os.name == "nt":
        # Best-effort ACL restriction on Windows: remove inheritance, grant current user only.
        # Failures are logged but non-fatal (icacls may be absent in mingw/WSL-on-Windows paths).
        try:
            import getpass
            import subprocess as _subprocess
            _subprocess.run(
                ["icacls", str(env_path), "/inheritance:r", "/grant:r",
                 f"{getpass.getuser()}:F"],
                check=False, capture_output=True,
            )
        except (OSError, FileNotFoundError):
            pass


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "****"
    return value[:10] + "-REDACTED-" + value[-4:]


def _log(workspace_root: Path, message: str) -> None:
    log_path = workspace_root / LOG_REL_PATH
    log_path.parent.mkdir(exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"{_now_iso()} {message}\n")


def _iter_matching_files(workspace_root: Path, globs: list[str]):
    """Yield files matching any glob, honoring SKIP_DIRS and PROCESSABLE_EXTENSIONS."""
    files, _ = _collect_matching_files(workspace_root, globs)
    yield from files


def _collect_matching_files(workspace_root: Path, globs: list[str]) -> tuple[list[Path], int]:
    """Return (files, skipped_count).

    skipped_count counts files that matched the glob but were excluded by
    SKIP_DIRS or PROCESSABLE_EXTENSIONS. Reported as `files_skipped` in the
    apply-script result JSON per spec section 8.2 step 10.
    """
    files: list[Path] = []
    skipped = 0
    seen: set = set()
    for pattern in globs:
        for path in workspace_root.glob(pattern):
            if not path.is_file():
                continue
            if path in seen:
                continue
            seen.add(path)
            if any(part in SKIP_DIRS for part in path.relative_to(workspace_root).parts):
                skipped += 1
                continue
            if path.suffix.lower() not in PROCESSABLE_EXTENSIONS:
                skipped += 1
                continue
            files.append(path)
    return files, skipped


def _apply_placeholder_substitution(path: Path, mapping: dict) -> bool:
    # Reject values that themselves look like a placeholder token. Prevents
    # feedback loops if a user answers "{COMPANY}" as their company name.
    for placeholder, value in mapping.items():
        if _PLACEHOLDER_TOKEN_RE.fullmatch(value.strip() if isinstance(value, str) else ""):
            raise SchemaError(
                f"value for {placeholder!r} looks like a placeholder token "
                f"({value!r}); pick a literal string instead"
            )
    try:
        original = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return False
    new = original
    for placeholder, value in mapping.items():
        new = new.replace(placeholder, value)
    if new == original:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, path)
    return True


def _read_stdin_payload() -> dict:
    raw = sys.stdin.read().strip()
    if not raw:
        raise SchemaError("--value-from-stdin requires JSON on stdin")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise SchemaError(f"stdin payload not valid JSON: {e}") from e


def render_template(template: str, context: dict) -> str:
    """Minimal Jinja subset: {{ var }} and {% if var %}...{% endif %}.

    Missing variables render as empty. Unsupported syntax (filters, loops)
    is tolerated: unmatched `{{ ... }}` blocks with filter syntax don't match
    the _VAR_RE pattern, so they pass through or are handled gracefully.
    Conditionals treat any truthy string/list/dict as 'true'.
    """
    def _replace_if(match):
        varname, body = match.group(1), match.group(2)
        return body if bool(context.get(varname)) else ""

    out = _IF_BLOCK_RE.sub(_replace_if, template)

    def _replace_var(match):
        varname = match.group(1)
        val = context.get(varname, "")
        return "" if val is None else str(val)

    out = _VAR_RE.sub(_replace_var, out)
    return out


def resolve_audience(args, workspace_root: Path) -> str:
    """Compute the effective audience, honoring --audience and --force-ceo-master.

    Returns 'public' or 'exec'. Never returns 'ceo-master' - if detected
    without --force-ceo-master, exits via sys.exit(EXIT_CEO_MASTER_WITHOUT_FORCE).
    """
    try:
        detected = detect_audience(workspace_root)
    except SchemaError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(EXIT_SCHEMA_ERROR)

    if detected == "ceo-master" and not args.force_ceo_master:
        print(
            "Detected workspace type 'ceo-master'. The setup wizard is only for "
            "HEADING OS clones and 31C exec workspaces. "
            "Pass --force-ceo-master to override. Aborting.",
            file=sys.stderr,
        )
        sys.exit(EXIT_CEO_MASTER_WITHOUT_FORCE)

    if args.audience:
        return args.audience
    return detected if detected != "ceo-master" else "public"


# ============================================================
# CLI / Subcommand Dispatch
# ============================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apply-wizard-answers.py",
        description="Sole writer for setup wizard state and output files.",
    )
    parser.add_argument("--question", metavar="ID",
                        help="Apply a single question (with --value-from-stdin or --skip)")
    parser.add_argument("--value-from-stdin", action="store_true",
                        help="Read JSON payload from stdin for --question")
    parser.add_argument("--skip", metavar="ID", help="Mark a question skipped")
    parser.add_argument("--all", action="store_true",
                        help="Re-apply every answered question transactionally")
    parser.add_argument("--check", action="store_true", help="Dry run")
    parser.add_argument("--audience", choices=["public", "exec"],
                        help="Override detected audience")
    parser.add_argument("--force-ceo-master", action="store_true",
                        help="Required companion when overriding ceo-master detection")
    parser.add_argument("--status", action="store_true", help="Print status JSON and exit")
    parser.add_argument("--reset", action="store_true",
                        help="Revert touched files to git-index state; preserve answers.json")
    parser.add_argument("--force", action="store_true",
                        help="Bypass safety checks on --reset (uncommitted changes)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    workspace_root = Path.cwd()
    args.resolved_audience = resolve_audience(args, workspace_root)
    args.workspace_root = workspace_root

    if args.status:
        return cmd_status(args)
    if args.question:
        return cmd_question(args)
    if args.skip:
        return cmd_skip(args)
    if args.all:
        return cmd_all(args)
    if args.reset:
        return cmd_reset(args)

    parser.print_help()
    return EXIT_OK


def _depends_on_satisfied(q, all_answers, bank):
    """Return True if q has no depends_on, or its dependency is met."""
    dep = q.get("depends_on")
    if not dep:
        return True
    parent_id = dep["question"]
    expected = dep.get("equals")
    if not any(pb["id"] == parent_id for pb in bank):
        raise SchemaError(f"depends_on: parent {parent_id!r} not in bank (for {q['id']!r})")
    parent_entry = all_answers.get(parent_id, {})
    if parent_entry.get("status") != "answered":
        return False
    return parent_entry.get("value") == expected


# ============================================================
# Subcommands (status / question / skip / all / reset)
# ============================================================
def cmd_status(args) -> int:
    workspace_root = args.workspace_root
    audience = args.resolved_audience
    try:
        bank = load_questions(workspace_root)
    except SchemaError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_SCHEMA_ERROR
    filtered = filter_by_audience(bank, audience)
    state = load_answers(workspace_root)
    answers = state.get("answers", {})

    try:
        visible = [q for q in filtered if _depends_on_satisfied(q, answers, bank)]
    except SchemaError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_SCHEMA_ERROR

    required_q = [q for q in visible if q["required"]]
    optional_q = [q for q in visible if not q["required"]]

    def _count(qs: list[dict], status: str) -> int:
        return sum(1 for q in qs if answers.get(q["id"], {}).get("status") == status)

    req_ans = _count(required_q, "answered")
    req_skip = _count(required_q, "skipped")
    req_pending = len(required_q) - req_ans - req_skip
    opt_ans = _count(optional_q, "answered")
    opt_skip = _count(optional_q, "skipped")
    opt_pending = len(optional_q) - opt_ans - opt_skip

    completion_pct = int((req_ans / len(required_q) * 100)) if required_q else 100

    rows = []
    for i, q in enumerate(visible, 1):
        entry = answers.get(q["id"], {})
        status = entry.get("status", "pending")
        rows.append({
            "id": q["id"],
            "label": _short_label(q["prompt"]),
            "status": status,
            "display_value": _display_value(q, entry),
            "required": bool(q["required"]),
            "number": i,
            "section": "required" if q["required"] else "optional",
            "type": q["type"],
            "prompt": q["prompt"],
            "example": q["example"],
            "help": q.get("help", ""),
        })

    unapplied = False
    if state.get("last_updated"):
        if not state.get("applied_at"):
            unapplied = True
        else:
            unapplied = state["last_updated"] > state["applied_at"]

    payload = {
        "audience": audience,
        "completion_pct": completion_pct,
        "required": {"total": len(required_q), "answered": req_ans,
                     "skipped": req_skip, "pending": req_pending},
        "optional": {"total": len(optional_q), "answered": opt_ans,
                     "skipped": opt_skip, "pending": opt_pending},
        "rows": rows,
        "applied_at": state.get("applied_at"),
        "last_updated": state.get("last_updated"),
        "unapplied": unapplied,
    }
    print(json.dumps(payload, indent=2))
    return EXIT_OK


def _short_label(prompt: str, max_len: int = 40) -> str:
    """Turn a long question prompt into a compact row label."""
    s = prompt.strip().rstrip("?").rstrip(".")
    if len(s) > max_len:
        s = s[:max_len - 1] + "..."
    return s


def _display_value(q: dict, entry: dict) -> str:
    """Safe-to-print rendering for the dashboard row."""
    status = entry.get("status", "pending")
    if status == "pending":
        return "(not answered)"
    if status == "skipped":
        return "(skipped)"
    if q["type"] == "secret":
        val = entry.get("value", "")
        if len(val) > 4:
            return "************" + val[-4:]
        return "****"
    if q["type"] == "rich":
        draft = entry.get("draft", "")
        word_count = len(draft.split())
        return f"[approved draft, ~{word_count} words]"
    if q["type"] == "list":
        items = entry.get("value", [])
        if isinstance(items, list):
            return ", ".join(items)
    val = entry.get("value", "")
    if isinstance(val, str) and len(val) > 40:
        return val[:39] + "..."
    return str(val)


def cmd_question(args) -> int:
    workspace_root = args.workspace_root
    audience = args.resolved_audience
    try:
        bank = load_questions(workspace_root)
    except SchemaError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_SCHEMA_ERROR
    filtered = filter_by_audience(bank, audience)
    q = next((x for x in filtered if x["id"] == args.question), None)
    if q is None:
        print(f"ERROR: unknown question id {args.question!r} for audience {audience}",
              file=sys.stderr)
        return EXIT_UNKNOWN_ID

    try:
        payload = _read_stdin_payload() if args.value_from_stdin else {}
    except SchemaError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_SCHEMA_ERROR

    state = load_answers(workspace_root)
    state["audience"] = audience
    answers = state.setdefault("answers", {})

    if q["type"] == "placeholder":
        if getattr(args, "check", False):
            print(json.dumps({"dry_run": True, "applied": []}))
            return EXIT_OK
        value = payload.get("value", "")
        if not isinstance(value, str):
            print("ERROR: placeholder value must be a string", file=sys.stderr)
            return EXIT_SCHEMA_ERROR
        files_changed = 0
        try:
            matching, files_skipped = _collect_matching_files(workspace_root, q["target"]["files"])
            for path in matching:
                if _apply_placeholder_substitution(path, {q["target"]["placeholder"]: value}):
                    files_changed += 1
        except SchemaError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return EXIT_SCHEMA_ERROR
        answers[q["id"]] = {"value": value, "status": "answered",
                             "answered_at": _now_iso()}
        state["applied_at"] = _now_iso()
        save_answers(workspace_root, state)
        print(json.dumps({"files_updated": files_changed, "files_skipped": files_skipped,
                          "errors": [], "applied": [q["id"]]}))
        return EXIT_OK

    if q["type"] == "list":
        if getattr(args, "check", False):
            print(json.dumps({"dry_run": True, "applied": []}))
            return EXIT_OK
        value = payload.get("value", [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            print("ERROR: list value must be an array of strings", file=sys.stderr)
            return EXIT_SCHEMA_ERROR
        placeholders = q["target"]["placeholders"]
        warnings = []
        if len(value) > len(placeholders):
            warnings.append(
                f"list overflow: {len(value)} items given, only {len(placeholders)} slots - extras dropped"
            )
            value = value[:len(placeholders)]
        mapping = {}
        for i, ph in enumerate(placeholders):
            mapping[ph] = value[i] if i < len(value) else ""
        files_changed = 0
        try:
            matching, files_skipped = _collect_matching_files(workspace_root, q["target"]["files"])
            for path in matching:
                if _apply_placeholder_substitution(path, mapping):
                    files_changed += 1
        except SchemaError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return EXIT_SCHEMA_ERROR
        answers[q["id"]] = {"value": value, "status": "answered",
                             "answered_at": _now_iso()}
        state["applied_at"] = _now_iso()
        save_answers(workspace_root, state)
        print(json.dumps({
            "files_updated": files_changed, "files_skipped": files_skipped,
            "errors": [], "warnings": warnings,
            "applied": [q["id"]],
        }))
        return EXIT_OK

    if q["type"] == "rich":
        if getattr(args, "check", False):
            print(json.dumps({"dry_run": True, "applied": []}))
            return EXIT_OK
        if payload.get("archive_draft"):
            entry = answers.get(q["id"], {})
            if entry.get("draft"):
                prev = entry.setdefault("draft_previous", [])
                prev.insert(0, {"draft": entry["draft"], "archived_at": _now_iso()})
                entry["draft_previous"] = prev[:3]
            answers[q["id"]] = entry
            state["audience"] = audience
            # Archiving means the rendered file no longer reflects the current canonical draft.
            # Clear applied_at so --status reports `unapplied: true` until the user re-runs
            # this rich question.
            state["applied_at"] = None
            save_answers(workspace_root, state)
            print(json.dumps({"archived": q["id"]}))
            return EXIT_OK

        value = payload.get("value", "")
        draft = payload.get("draft", "")
        draft_approved = bool(payload.get("draft_approved"))
        if not draft_approved:
            print("ERROR: rich question requires draft_approved=true to write output",
                  file=sys.stderr)
            return EXIT_SCHEMA_ERROR

        template_text = resolve_read_path(workspace_root, q["target"]["template"]).read_text(encoding="utf-8")
        ctx = {"generated_date": datetime.now().astimezone().date().isoformat()}
        for aid, aentry in answers.items():
            if isinstance(aentry.get("value"), str):
                ctx[aid] = aentry["value"]
            if aentry.get("draft"):
                ctx[f"{aid}_draft"] = aentry["draft"]
        ctx[q["id"]] = value
        ctx[f"{q['id']}_draft"] = draft

        rendered = render_template(template_text, ctx)

        if audience == "exec" and q["target"].get("output_exec"):
            out_rel = q["target"]["output_exec"]
        else:
            out_rel = q["target"]["output"]
        out_path = workspace_root / out_rel
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if out_path.exists() and out_path.read_text(encoding="utf-8") == rendered:
            files_changed = 0
        else:
            tmp = out_path.with_suffix(out_path.suffix + ".tmp")
            tmp.write_text(rendered, encoding="utf-8")
            os.replace(tmp, out_path)
            files_changed = 1

        prev_entry = answers.get(q["id"], {})
        new_entry = {
            "value": value, "draft": draft, "draft_approved": True,
            "status": "answered", "answered_at": _now_iso(),
        }
        if prev_entry.get("draft_previous"):
            new_entry["draft_previous"] = prev_entry["draft_previous"]
        answers[q["id"]] = new_entry
        state["applied_at"] = _now_iso()
        save_answers(workspace_root, state)
        print(json.dumps({"files_updated": files_changed, "errors": [], "applied": [q["id"]]}))
        return EXIT_OK

    if q["type"] == "secret":
        if getattr(args, "check", False):
            print(json.dumps({"dry_run": True, "applied": []}))
            return EXIT_OK
        value = payload.get("value", "")
        if not isinstance(value, str) or not value:
            print("ERROR: secret value must be a non-empty string", file=sys.stderr)
            return EXIT_SCHEMA_ERROR
        env_var = q["target"]["env_var"]
        if not _is_valid_env_var_name(env_var):
            print(f"ERROR: invalid env_var name {env_var!r}", file=sys.stderr)
            return EXIT_SCHEMA_ERROR
        env_path = workspace_root / ".env"
        try:
            _upsert_env_line(env_path, env_var, value)
        except OSError as e:
            print(f"ERROR: cannot write .env: {e}", file=sys.stderr)
            return EXIT_FILE_WRITE_ERROR
        masked = _mask_secret(value)
        answers[q["id"]] = {
            "value": masked, "env_written": True,
            "status": "answered", "answered_at": _now_iso(),
        }
        state["applied_at"] = _now_iso()
        save_answers(workspace_root, state)
        _log(workspace_root, f"{env_var}: [written, len={len(value)}]")
        print(json.dumps({"files_updated": 1, "errors": [], "applied": [q["id"]]}))
        return EXIT_OK

    # Defensive fallback: load_questions validates type is in VALID_TYPES, so
    # this branch is unreachable under normal operation. Treat as an internal error.
    print(f"INTERNAL ERROR: unhandled type {q['type']!r} (this should not be reachable)",
          file=sys.stderr)
    return EXIT_SCHEMA_ERROR


def cmd_skip(args) -> int:
    workspace_root = args.workspace_root
    audience = args.resolved_audience
    try:
        bank = load_questions(workspace_root)
    except SchemaError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_SCHEMA_ERROR
    filtered = filter_by_audience(bank, audience)
    if not any(q["id"] == args.skip for q in filtered):
        print(f"ERROR: unknown question id {args.skip!r} for audience {audience}", file=sys.stderr)
        return EXIT_UNKNOWN_ID
    state = load_answers(workspace_root)
    state.setdefault("answers", {})[args.skip] = {
        "value": None,
        "status": "skipped",
        "skipped_at": _now_iso(),
    }
    state["audience"] = audience
    save_answers(workspace_root, state)
    print(json.dumps({"applied": [args.skip], "status": "skipped"}))
    return EXIT_OK


def cmd_all(args) -> int:
    workspace_root = args.workspace_root
    audience = args.resolved_audience
    try:
        bank = load_questions(workspace_root)
    except SchemaError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_SCHEMA_ERROR
    filtered = filter_by_audience(bank, audience)
    state = load_answers(workspace_root)
    answers = state.get("answers", {})

    try:
        visible = [q for q in filtered if _depends_on_satisfied(q, answers, bank)]
    except SchemaError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_SCHEMA_ERROR

    _PLANNER_WARNINGS.clear()

    # journal entries are either:
    #   ("subst", path, mapping_dict)  -- for placeholder/list questions
    #   ("write", path, new_bytes)     -- for rich questions (self-contained rendered content)
    journal: list[tuple] = []
    warnings = []
    for q in visible:
        entry = answers.get(q["id"])
        if not entry or entry.get("status") != "answered":
            continue
        try:
            plans = _plan_question(workspace_root, q, entry, answers, audience)
            journal.extend(plans)
        except (SchemaError, OSError, KeyError) as e:
            print(f"ERROR: planning failed for {q['id']!r}: {e}", file=sys.stderr)
            return EXIT_SCHEMA_ERROR

    warnings.extend(_PLANNER_WARNINGS)

    # Merge all substitution mappings per file so multi-question replacements
    # on the same file are applied in a single read-modify-write pass.
    # "write" entries (rich) are kept as-is (they produce their own output paths).
    from collections import OrderedDict
    subst_by_file: dict = OrderedDict()  # path -> merged mapping dict
    write_entries: list[tuple] = []  # (path, new_bytes) for rich

    for entry_tuple in journal:
        kind = entry_tuple[0]
        if kind == "subst":
            _, path, mapping = entry_tuple
            if path not in subst_by_file:
                subst_by_file[path] = {}
            subst_by_file[path].update(mapping)
        else:  # "write"
            _, path, new_bytes = entry_tuple
            write_entries.append((path, new_bytes))

    # Build the final list of (path, new_bytes) by applying merged mappings.
    merged_journal: list[tuple] = []
    for path, mapping in subst_by_file.items():
        try:
            original = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue
        new = original
        for ph, v in mapping.items():
            new = new.replace(ph, v)
        merged_journal.append((path, new))
    merged_journal.extend(write_entries)

    if args.check:
        would = sum(1 for p, nb in merged_journal
                    if not p.exists() or p.read_text(encoding="utf-8") != nb)
        print(json.dumps({"dry_run": True, "would_update": would,
                          "planned": [str(p.relative_to(workspace_root))
                                      for p, _ in merged_journal]}))
        return EXIT_OK

    files_changed = 0
    for path, new_bytes in merged_journal:
        try:
            if path.exists() and path.read_text(encoding="utf-8") == new_bytes:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(new_bytes, encoding="utf-8")
            os.replace(tmp, path)
            files_changed += 1
        except OSError as e:
            print(f"ERROR: write failed for {path}: {e}", file=sys.stderr)
            print(json.dumps({
                "files_updated": files_changed,
                "errors": [{"file": str(path), "reason": str(e)}],
                "warnings": warnings,
            }))
            return EXIT_FILE_WRITE_ERROR

    state["applied_at"] = _now_iso()
    state["audience"] = audience
    save_answers(workspace_root, state)
    print(json.dumps({"files_updated": files_changed, "errors": [], "warnings": warnings}))
    return EXIT_OK


# Shared warnings list populated during --all planning. cmd_all consumes and emits.
_PLANNER_WARNINGS: list[str] = []


def _planner_warning(msg: str) -> None:
    _PLANNER_WARNINGS.append(msg)


def _plan_question(workspace_root, q, entry, all_answers, audience):
    """Return a list of journal entries for a single answered question.

    Each entry is a 3-tuple:
      ("subst", path, mapping)  -- placeholder/list: apply str.replace for each k->v
      ("write", path, new_str)  -- rich: write rendered content verbatim
    Secrets produce no entries (intentional - masked value is not recoverable).
    """
    plans = []
    if q["type"] == "placeholder":
        value = entry["value"]
        mapping = {q["target"]["placeholder"]: value}
        for path in _iter_matching_files(workspace_root, q["target"]["files"]):
            plans.append(("subst", path, mapping))
    elif q["type"] == "list":
        placeholders = q["target"]["placeholders"]
        value = entry["value"] or []
        value = value[:len(placeholders)]
        mapping = {ph: (value[i] if i < len(value) else "") for i, ph in enumerate(placeholders)}
        for path in _iter_matching_files(workspace_root, q["target"]["files"]):
            plans.append(("subst", path, mapping))
    elif q["type"] == "rich":
        template_text = resolve_read_path(workspace_root, q["target"]["template"]).read_text(encoding="utf-8")
        ctx = {"generated_date": datetime.now().astimezone().date().isoformat()}
        for aid, aentry in all_answers.items():
            if isinstance(aentry.get("value"), str):
                ctx[aid] = aentry["value"]
            if aentry.get("draft"):
                ctx[f"{aid}_draft"] = aentry["draft"]
        rendered = render_template(template_text, ctx)
        out_rel = (q["target"].get("output_exec")
                   if audience == "exec" and q["target"].get("output_exec")
                   else q["target"]["output"])
        plans.append(("write", workspace_root / out_rel, rendered))
    elif q["type"] == "secret":
        # --all intentionally does NOT regenerate .env lines from masked state.
        # The real secret exists only in .env. If the user deletes .env and runs
        # --all, the masked value in answers.json is non-recoverable - silently
        # re-writing from the mask would be worse than surfacing the deletion.
        # Warn when env_written is True but the key is missing from .env.
        env_var = q["target"]["env_var"]
        env_path = workspace_root / ".env"
        if entry.get("env_written"):
            env_content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
            if f"{env_var}=" not in env_content:
                _planner_warning(
                    f"{env_var} marked written but missing from .env. "
                    f"Re-run /setup-wizard and re-answer to restore."
                )
        # Intentionally return no plan entries for secrets.
    return plans


def cmd_reset(args) -> int:
    import subprocess
    workspace_root = args.workspace_root
    if not args.force:
        status_out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace_root, capture_output=True, text=True,
        )
        if status_out.returncode != 0:
            print(f"ERROR: git status failed: {status_out.stderr}", file=sys.stderr)
            return EXIT_SCHEMA_ERROR
        dirty = [line for line in status_out.stdout.splitlines()
                 if line.strip() and not line.startswith("??")]
        if dirty:
            print("ERROR: uncommitted changes detected. Commit or stash them, or re-run with --force.",
                  file=sys.stderr)
            for line in dirty[:10]:
                print(f"  {line}", file=sys.stderr)
            return EXIT_SCHEMA_ERROR

    state = load_answers(workspace_root)
    try:
        bank = load_questions(workspace_root)
    except SchemaError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_SCHEMA_ERROR
    filtered = filter_by_audience(bank, args.resolved_audience)

    touched = set()
    for q in filtered:
        entry = state.get("answers", {}).get(q["id"])
        if not entry or entry.get("status") != "answered":
            continue
        if q["type"] in ("placeholder", "list"):
            for p in _iter_matching_files(workspace_root, q["target"]["files"]):
                touched.add(p)
        elif q["type"] == "rich":
            out_rel = (q["target"].get("output_exec")
                       if args.resolved_audience == "exec" and q["target"].get("output_exec")
                       else q["target"]["output"])
            touched.add(workspace_root / out_rel)

    errors = []
    for path in touched:
        rel = str(path.relative_to(workspace_root))
        check = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel],
            cwd=workspace_root, capture_output=True, text=True,
        )
        if check.returncode == 0:
            revert = subprocess.run(
                ["git", "checkout", "--", rel],
                cwd=workspace_root, capture_output=True, text=True,
            )
            if revert.returncode != 0:
                errors.append({"file": rel, "reason": revert.stderr.strip()})
        else:
            try:
                if path.exists():
                    path.unlink()
            except OSError as e:
                errors.append({"file": rel, "reason": str(e)})

    state["applied_at"] = None
    save_answers(workspace_root, state)

    if errors:
        print(json.dumps({"reset": True, "errors": errors}))
        return EXIT_FILE_WRITE_ERROR
    print(json.dumps({"reset": True, "files_reverted": len(touched), "errors": []}))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
