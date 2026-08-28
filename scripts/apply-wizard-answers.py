#!/usr/bin/env python3
"""apply-wizard-answers.py -- sole writer for setup wizard state and output files.

See docs/superpowers/specs/2026-04-24-setup-wizard-design.md section 8 for the full contract.

Tests: tests/test_a_wizard_that_reached_outside_its_own_workspace.py
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import yaml
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The `.env` grammar, shared with every other reader and writer of that file.
# This module is a WRITER, and a writer that cannot recognise the line a reader
# reads appends a duplicate instead of replacing it. `parse_env_line` is pure
# (no path resolution, no I/O), so importing it resolves nothing outside the
# workspace this script was pointed at.
from scripts.utils.paths import parse_env_line  # noqa: E402

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


def _read_text(path: Path) -> str:
    """Read UTF-8, and turn a decode failure into this file's own error type.

    `UnicodeDecodeError` subclasses `ValueError`, NOT `OSError`. Every read site
    here sat under a handler chain of `SchemaError` / `StateWriteError` /
    `OSError` (`main`) or `(SchemaError, OSError, KeyError)` (`cmd_all`'s
    planner), so not one of them saw it: a `.env`, a template, a question bank
    or a rendered output holding a single non-UTF-8 byte produced a raw
    traceback, including out of the read-only `--all --check`. The file already
    stated the intended convention in three places -- `load_answers` catches
    `UnicodeDecodeError` explicitly, `_apply_placeholder_substitution` guards
    its read, and `main`'s comments call a traceback the thing they exist to
    prevent -- so the read sites were inconsistent with the script's own
    contract rather than expressing a different one.

    The path goes in the message. `UnicodeDecodeError` carries the codec, the
    byte and its offset and never the file it came from, so the bare error told
    the operator a byte was bad and not which of the workspace's files to open.
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise SchemaError(
            f"{path} is not valid UTF-8 ({e.reason} at byte {e.start}). "
            f"The wizard reads and writes UTF-8 text only."
        ) from e


def _text_or_none(path: Path) -> str | None:
    """The file's text, or None when it cannot be compared as text.

    For the two "is the file already what we would write?" comparisons. An
    undecodable or unreadable file is not equal to the rendered text, so None
    is the honest answer and the caller proceeds to write. Mirrors the
    `(UnicodeDecodeError, PermissionError)` guard `_apply_placeholder_substitution`
    already uses, rather than inventing a second convention beside it.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return None


def _git_rel(workspace_root: Path, path: Path) -> str:
    """A workspace-relative path spelled the way git spells one.

    Two corrections to `str(path.relative_to(workspace_root))`, and `cmd_reset`
    used that form on both sides of its conversation with git.

    Forward slashes. `str()` on a `WindowsPath` gives backslashes; `git status
    --porcelain` reports forward slashes on every platform. The dirty check
    compared `docs\\setup.md` against git's `docs/setup.md`, so on Windows every
    file the wizard had just written failed the "is this ours?" filter and was
    reported as foreign uncommitted work. `--reset` without `--force` therefore
    refused in exactly the state the wizard creates -- the same always-failing
    gate the comment above that check says was fixed on 2026-08-23, reached
    again through the path separator. Windows is supported here; the `icacls`
    branch in `_upsert_env_line` exists for it.

    Resolved, not lexical. `_resolve_output_path` returns the UNRESOLVED join,
    so a bank output of `docs/../notes.md` stays `docs/../notes.md` through
    `relative_to`, on Linux too.

    The second consumer is why this matters beyond a refusal. The revert loop
    feeds `rel` to `git ls-files --error-unmatch` and treats a non-zero exit as
    "untracked", whose branch is `path.unlink()`. A spelling git will not match
    does not merely mis-report: it routes a TRACKED file, which `git checkout
    --` would have restored, into the delete branch instead, under a help line
    reading "Revert touched files to git-index state".
    """
    return path.resolve().relative_to(workspace_root.resolve()).as_posix()


def _require_inside(workspace_root: Path, candidate: Path, described: str) -> Path:
    """Return `candidate`, or raise SchemaError when it leaves the workspace.

    One definition for the invariant the file states twice in prose: "A question
    bank may not reach past the workspace root." It was enforced on the glob
    targets and on the rich OUTPUT path, and the two enforcements were separate
    copies of the same four lines, which is how the third site (the template
    READ) came to have none at all.
    """
    try:
        candidate.resolve().relative_to(workspace_root.resolve())
    except ValueError:
        raise SchemaError(
            f"{described} resolves outside the workspace at {workspace_root}. "
            f"A question bank may not reach past the workspace root."
        ) from None
    return candidate


def resolve_read_path(workspace_root: Path, rel_path) -> Path:
    """Resolve a read-only config/template path across workspace layouts.

    CEO workspace has config/ and wizard-templates/ at the root. Exec
    workspaces classify config/ as corporate, so the same files live under
    corporate/config/ and corporate/config/wizard-templates/. This helper
    checks the root layout first, then falls back to corporate/. Returns the
    primary (root) path if neither exists so error messages point to the
    expected location.

    Containment applies to the READ side too. `target.template` reaches this
    function straight from the bank, and both branches selected purely on
    `.exists()`, so `template: "../../etc/hostname"` was read and RENDERED INTO
    the output document -- an arbitrary host file copied into a workspace file,
    through the one path the sibling checks had been written for and did not
    cover. An absolute `rel_path` was the same hole by a shorter route:
    `workspace_root / "/etc/hostname"` discards the root entirely. Traversal
    also skipped the root-then-corporate resolution this function exists to do,
    since `..` climbs out before the fallback is ever consulted.
    """
    rel = Path(rel_path)
    primary = _require_inside(workspace_root, workspace_root / rel,
                              f"the path {str(rel_path)!r}")
    if primary.exists():
        return primary
    fallback = _require_inside(workspace_root, workspace_root / "corporate" / rel,
                               f"the path {str(rel_path)!r}")
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
        data = json.loads(_read_text(identity_path))
    except json.JSONDecodeError as e:
        raise SchemaError(f".workspace-identity.json is malformed: {e}") from e
    if not isinstance(data, dict):
        # json.loads accepts any JSON value. `[]`, `"x"` and `42` all parse,
        # and `data.get` then raised AttributeError -- a traceback, not the
        # SchemaError this docstring promises. load_answers already checks.
        raise SchemaError(
            f".workspace-identity.json holds a {type(data).__name__}, not an "
            f"object."
        )
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
# What each question type needs inside `target`. Validated at LOAD time, so a
# malformed bank fails once with the question id, instead of reaching
# `cmd_question` and raising a bare KeyError from an index expression. Only
# `target`'s EXISTENCE was checked until 2026-08-23; `cmd_all` already caught
# KeyError while planning, which is what showed the gap was unintended.
_TARGET_FIELDS = {
    "placeholder": ("files", "placeholder"),
    "list": ("files", "placeholders"),
    "rich": ("template", "output"),
    "secret": ("env_var",),
}


def _validate_target(q: dict) -> None:
    target = q["target"]
    if not isinstance(target, dict):
        raise SchemaError(
            f"Question {q['id']!r}: target must be a mapping, not "
            f"{type(target).__name__}")
    for field in _TARGET_FIELDS.get(q["type"], ()):
        if field not in target:
            raise SchemaError(
                f"Question {q['id']!r} is type {q['type']!r} and its target is "
                f"missing {field!r}")


def load_questions(workspace_root: Path) -> list[dict]:
    """Load and validate config/wizard-questions.yaml. Raise SchemaError on problems."""
    path = resolve_read_path(workspace_root, QUESTIONS_REL_PATH)
    if not path.exists():
        raise SchemaError(f"Question bank not found: {path}")
    try:
        data = yaml.safe_load(_read_text(path))
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
        _validate_target(q)
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
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        # The schema check below already raises SchemaError; this one did not,
        # so every subcommand died with a raw JSONDecodeError instead.
        raise SchemaError(f"cannot read answers.json at {path}: {e}") from e
    if not isinstance(data, dict):
        raise SchemaError(
            f"answers.json at {path} is a {type(data).__name__}, not an object")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise SchemaError(
            f"answers.json schema_version {data.get('schema_version')} "
            f"incompatible with expected {SCHEMA_VERSION}"
        )
    return data


class StateWriteError(RuntimeError):
    """The workspace was modified and the answer could not be recorded."""


def save_answers(workspace_root: Path, state: dict) -> None:
    """Atomic write of state dict to .setup/answers.json.

    Raises StateWriteError on any OS failure. Every caller in `cmd_question`
    modifies the workspace FIRST and saves afterwards, so a raw OSError here
    left the files changed, the answer unrecorded, and `--status` none the
    wiser -- reported as a traceback rather than as the divergence it is.
    """
    setup_dir = workspace_root / ".setup"
    setup_dir.mkdir(exist_ok=True)
    path = setup_dir / "answers.json"
    tmp = path.with_suffix(".json.tmp")
    state["last_updated"] = _now_iso()
    if state.get("started_at") is None:
        state["started_at"] = state["last_updated"]
    try:
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False),
                       encoding="utf-8")
    except OSError as e:
        raise StateWriteError(
            f"the workspace was modified but the answer could not be recorded "
            f"to {path}: {e}. Re-run with --status to see what is out of sync."
        ) from e
    # Mode BEFORE the replace, the same order `.env` uses: chmod after
    # os.replace leaves a window where the new file carries umask defaults.
    # This file holds the last four characters of every secret plus every other
    # answer, and it is exactly the state file that reaches a backup or a sync.
    # A filesystem without POSIX modes cannot honour this and that is not fatal;
    # the write itself below is.
    with contextlib.suppress(OSError):
        os.chmod(tmp, 0o600)
    try:
        os.replace(tmp, path)
    except OSError as e:
        raise StateWriteError(
            f"the workspace was modified but the answer could not be recorded "
            f"to {path}: {e}. Re-run with --status to see what is out of sync."
        ) from e


# ============================================================
# Helpers / Utilities
# ============================================================
def _now_iso() -> str:
    """ISO 8601 with timezone offset for timestamps in answers.json."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _iso_after(a: str, b: str) -> bool:
    """True when timestamp `a` is strictly later than `b`.

    Falls back to the string compare when either side will not parse, which is
    what the caller did for both sides before 2026-08-23. A stale reading is
    better than a crash in a status command.
    """
    try:
        return datetime.fromisoformat(a) > datetime.fromisoformat(b)
    except (TypeError, ValueError):
        return a > b


def _is_valid_env_var_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Z_][A-Z0-9_]*", name))


def _upsert_env_line(env_path: Path, key: str, value: str) -> None:
    """Set `key=value` in `.env`, atomically and with no readable window.

    A control character in `value` is REFUSED. The value arrives as JSON on
    stdin, where `"\\n"` is an ordinary character, and this function wrote
    `f"{key}={value}"` verbatim until 2026-08-23: one paste with a trailing
    newline, or a multi-line PEM, split into extra `KEY=...` lines that silently
    defined variables nobody asked for and corrupted the file for every later
    reader. Refusing is right here rather than escaping, because a secret with
    an embedded newline is far more likely a paste accident than an intent.
    """
    if any(ch in value for ch in "\r\n\x00"):
        raise SchemaError(
            f"the value for {key} contains a newline or NUL. A .env line cannot "
            "carry one: it would split into extra KEY=... lines. Strip it and "
            "pass the value again."
        )
    lines = []
    if env_path.exists():
        lines = _read_text(env_path).splitlines()
    updated = False
    new_line = f"{key}={value}"
    for i, line in enumerate(lines):
        # `parse_env_line`, not `startswith`: the reader side strips the line
        # first, so `  KEY=old` assigns KEY to every reader in the workspace and
        # was invisible to this writer. The secret was appended as a second
        # line, and `load_env` (setdefault, FIRST line wins) then handed every
        # caller the OLD value while the wizard reported the new one written.
        pair = parse_env_line(line)
        if pair is not None and pair[0] == key:
            lines[i] = new_line
            updated = True
            break
    if not updated:
        lines.append(new_line)
    tmp = env_path.with_suffix(env_path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Mode BEFORE the rename. Setting it afterwards left the new .env at umask
    # defaults -- commonly 0644 -- for the gap between the two calls, on a file
    # that exists to hold credentials.
    if os.name == "posix":
        os.chmod(tmp, 0o600)
    os.replace(tmp, env_path)
    if os.name == "nt":
        # Best-effort ACL restriction on Windows: remove inheritance, grant the
        # current user only. Reported, not swallowed: the comment here promised
        # "failures are logged" above a bare `except: pass` and an unread exit
        # code, so an inherited ACL on .env left no signal anywhere.
        try:
            import getpass
            import subprocess as _subprocess
            acl = _subprocess.run(
                ["icacls", str(env_path), "/inheritance:r", "/grant:r",
                 f"{getpass.getuser()}:F"],
                check=False, capture_output=True, text=True,
            )
            if acl.returncode != 0:
                print(f"WARNING: could not restrict ACLs on {env_path}: "
                      f"{(acl.stderr or acl.stdout or '').strip()[:200]}",
                      file=sys.stderr)
        except OSError as e:
            print(f"WARNING: could not restrict ACLs on {env_path}: {e}",
                  file=sys.stderr)


def _mask_secret(value: str) -> str:
    """A display stub for `.setup/answers.json`. Keeps the last four and nothing else.

    It kept `value[:10]` as well until 2026-08-23, so fourteen characters of
    every real credential were persisted. `save_answers` chmods the file 0600
    now; this paragraph claimed it had "no mode of its own", which described
    the pre-2026-08-23 state as though it were current and would have misled
    the next reader weighing whether the mask is still needed. `_display_value` is the only reader and it renders
    `val[-4:]`, so the prefix served nobody; and a verified prefix plus suffix
    lets whoever steals this file confirm a candidate key.
    """
    if len(value) <= 8:
        return "****"
    return "*" * 12 + value[-4:]


def _log(workspace_root: Path, message: str) -> None:
    """Append one diary line. A failure here reports itself and nothing else.

    The single caller is the secret branch, which logs AFTER `.env` is written
    and the answer is durably saved, and one line before it prints its success
    JSON. An OSError out of here -- `.setup/wizard.log` existing as a directory
    is enough -- reached `main`'s OSError handler, which printed "Some workspace
    files may already have been changed while the answer went unrecorded" and
    returned EXIT_FILE_WRITE_ERROR. Every part of that was false: the `.env` was
    written, the answer WAS recorded, and the caller reads the exit code and the
    absent JSON as a failure, so it may prompt for the same secret again.

    A diary that cannot be written is worth a warning, never a verdict on the
    operation it was describing.
    """
    log_path = workspace_root / LOG_REL_PATH
    try:
        log_path.parent.mkdir(exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{_now_iso()} {message}\n")
    except OSError as e:
        print(f"WARNING: could not append to {log_path}: {e}", file=sys.stderr)


def _iter_matching_files(workspace_root: Path, globs: list[str]):
    """Yield files matching any glob, honoring SKIP_DIRS and PROCESSABLE_EXTENSIONS."""
    files, _ = _collect_matching_files(workspace_root, globs)
    yield from files


def _resolve_output_path(workspace_root: Path, out_rel: str) -> Path:
    """A rich question's output path, proven to stay inside the workspace.

    `_collect_matching_files` enforces containment for glob targets with a
    comment stating the invariant: "A question bank may not reach past the
    workspace root." The rich `output` / `output_exec` path had no equivalent,
    so `../../tmp/escape.md` was written to, and `--reset` then DELETED it.
    """
    return _require_inside(workspace_root, workspace_root / out_rel,
                           f"the rich output {out_rel!r}")


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
            # Containment, stated. A bank glob containing `..` matches outside
            # the workspace, and the only thing that stopped the substitution
            # engine rewriting those files was `relative_to` raising an
            # UNCAUGHT ValueError two lines down -- an accident, not a check.
            try:
                rel = path.resolve().relative_to(workspace_root.resolve())
            except ValueError:
                raise SchemaError(
                    f"the glob {pattern!r} matched {path}, which is outside the "
                    f"workspace at {workspace_root}. A question bank may not "
                    f"reach past the workspace root."
                ) from None
            if any(part in SKIP_DIRS for part in rel.parts):
                skipped += 1
                continue
            if path.suffix.lower() not in PROCESSABLE_EXTENSIONS:
                skipped += 1
                continue
            files.append(path)
    return files, skipped


def _reject_token_values(mapping: dict) -> None:
    """Refuse an answer that carries a placeholder token, anywhere in it.

    `search`, not `fullmatch`. The guard rejected an answer that IS a token and
    passed one that merely CONTAINS one, and the contained case is the one that
    diverges. `--question` applies a single question's mapping, so `"see {B} for
    details"` lands verbatim; `--all` merges every answered question's mapping
    per file and applies them in sequence, so question B's `str.replace` then
    rewrites the text question A's answer had just inserted. The same answers
    produce two different files depending on which command wrote them, and the
    re-apply path reports success either way.

    Refusing is the fix rather than one simultaneous pass, because the single-
    question path stays order-dependent whichever way `--all` behaves: answer A
    before B and the token is substituted later, answer B before A and it
    survives as literal text. There is no application order that makes an answer
    containing a live token mean one thing.

    `--all` also never ran this guard at all. It builds its merged mapping in
    `cmd_all` and applies it there, bypassing `_apply_placeholder_substitution`
    entirely -- so an answer recorded before this check existed, or written by
    hand into `answers.json`, reached the files unexamined.
    """
    for placeholder, value in mapping.items():
        if _PLACEHOLDER_TOKEN_RE.search(value if isinstance(value, str) else ""):
            raise SchemaError(
                f"value for {placeholder!r} contains a placeholder token "
                f"({value!r}); a later question would substitute into it. "
                f"Pick a literal string instead."
            )


def _apply_placeholder_substitution(path: Path, mapping: dict) -> bool:
    _reject_token_values(mapping)
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
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SchemaError(f"stdin payload not valid JSON: {e}") from e
    if not isinstance(payload, dict):
        # Only the SYNTAX was validated, so `[1]` and `42` passed and every
        # `payload.get(...)` downstream raised AttributeError -- a traceback,
        # bypassing this script's SchemaError -> clean exit-1 convention.
        raise SchemaError(
            f"stdin payload is a {type(payload).__name__}, not a JSON object"
        )
    return payload


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
                        help="Re-apply every answered question. Plans all writes first and aborts before writing if the plan fails, but the writes themselves are sequential and are NOT rolled back if one of them fails partway.")
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
    args.workspace_root = workspace_root

    # One handler at the dispatch, because the raisers are spread across the
    # file and their callers were not consistent. `load_answers` raises
    # SchemaError on a corrupt answers.json and NO subcommand wrapped it, so a
    # corrupt file still killed every subcommand with a traceback -- the exact
    # outcome its own inline comment claimed to have fixed. `save_answers`
    # raises StateWriteError after the workspace has already been modified, and
    # its docstring complained that this surfaced "as a traceback rather than
    # as the divergence it is"; nothing caught it either.
    try:
        # Inside the try, though resolve_audience currently catches its own
        # SchemaError and sys.exits. Placement, not a fix: it is a raiser, and
        # a raiser that stops exiting on its own should not become a traceback
        # because it sat outside the handler.
        args.resolved_audience = resolve_audience(args, workspace_root)
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
    except SchemaError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_SCHEMA_ERROR
    except StateWriteError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("       The workspace files may already have been changed while "
              "the answer went unrecorded; run --status to see the divergence.",
              file=sys.stderr)
        return EXIT_FILE_WRITE_ERROR
    except OSError as e:
        # The write side of every apply branch. `_apply_placeholder_substitution`
        # guards only its READ (UnicodeDecodeError / PermissionError); the
        # tmp-write and os.replace beneath it, the rich branch's template read,
        # and its output write were all unguarded, so a read-only directory, a
        # full disk or a missing template raised a traceback AFTER some target
        # files had been rewritten and BEFORE the answer was recorded. Same
        # divergence StateWriteError exists to name, reached a different way.
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        print("       Some workspace files may already have been changed while "
              "the answer went unrecorded; run --status to see the divergence.",
              file=sys.stderr)
        return EXIT_FILE_WRITE_ERROR

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
            # Parse, do not compare strings. `_now_iso()` writes local time
            # WITH an offset, so across a DST change the offsets differ and
            # lexicographic order stops matching chronological order: answered
            # at 01:30:00-04:00 (05:30 UTC) then applied at 01:00:00-05:00
            # (06:00 UTC) is applied LATER, and the string compare said
            # unapplied. Wrong for about an hour, twice a year.
            unapplied = _iso_after(state["last_updated"], state["applied_at"])

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

    if not args.value_from_stdin and not getattr(args, "check", False):
        # Refuse, do not default to {}. With an empty payload every branch below
        # read `payload.get("value", "")` and wrote the EMPTY STRING over the
        # placeholder in every file the question targets, then marked the
        # question answered and stamped applied_at. One forgotten flag silently
        # erased placeholders across a freshly cloned workspace -- the exact
        # corruption this script exists to prevent.
        print("ERROR: --question needs a value. Pass it on stdin with "
              "--value-from-stdin, or use --check for a dry run.", file=sys.stderr)
        return EXIT_SCHEMA_ERROR

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
            if not entry.get("draft"):
                # Nothing to archive. This used to fall through, write an empty
                # `{}` entry (which --status reads as pending with a blank
                # display), stamp last_updated, and print {"archived": id} --
                # reporting success for an operation that did nothing.
                print(f"ERROR: {q['id']} has no draft to archive",
                      file=sys.stderr)
                return EXIT_SCHEMA_ERROR
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

        template_text = _read_text(
            resolve_read_path(workspace_root, q["target"]["template"]))
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
        out_path = _resolve_output_path(workspace_root, out_rel)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if out_path.exists() and _text_or_none(out_path) == rendered:
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
        # The same guard `--question` applies, on the path that never had it.
        # Raised here rather than during planning because the merge is where the
        # cross-substitution becomes possible, and nothing has been written yet.
        _reject_token_values(mapping)
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
        # `_text_or_none`, so the DRY RUN cannot be the thing that crashes. A
        # rich question whose output file holds non-UTF-8 bytes raised
        # UnicodeDecodeError straight out of `--check`, past every handler,
        # from a command whose whole contract is that it writes nothing.
        would = sum(1 for p, nb in merged_journal
                    if not p.exists() or _text_or_none(p) != nb)
        print(json.dumps({"dry_run": True, "would_update": would,
                          "planned": [str(p.relative_to(workspace_root))
                                      for p, _ in merged_journal]}))
        return EXIT_OK

    files_changed = 0
    for path, new_bytes in merged_journal:
        try:
            if path.exists() and _text_or_none(path) == new_bytes:
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
        template_text = _read_text(
            resolve_read_path(workspace_root, q["target"]["template"]))
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
        # `_resolve_output_path`, not a bare join. `cmd_question` routed the
        # single-question path through the guard and this planning path did
        # not, so `output: "../../tmp/escape.md"` was REFUSED by `--question`
        # and silently written outside the workspace by `--all`. `cmd_reset`
        # does check, so it would then refuse to clean up what `--all` created.
        # An ABSOLUTE `out_rel` was worse still: `workspace_root / "/abs"`
        # discards the root entirely under pathlib, and `--all --check` then
        # raised an uncaught ValueError from `relative_to` -- a traceback out
        # of a dry run.
        plans.append(("write", _resolve_output_path(workspace_root, out_rel), rendered))
    elif q["type"] == "secret":
        # --all intentionally does NOT regenerate .env lines from masked state.
        # The real secret exists only in .env. If the user deletes .env and runs
        # --all, the masked value in answers.json is non-recoverable - silently
        # re-writing from the mask would be worse than surfacing the deletion.
        # Warn when env_written is True but the key is missing from .env.
        env_var = q["target"]["env_var"]
        env_path = workspace_root / ".env"
        if entry.get("env_written"):
            env_content = _read_text(env_path) if env_path.exists() else ""
            # Key-anchored, like the writer `_upsert_env_line`. A substring
            # test let `OTHER_API_KEY=x` satisfy a check for `API_KEY`, so the
            # "marked written but missing" warning never fired. It reads through
            # the shared grammar so this check, the writer, and `load_env` agree
            # about which lines assign the key.
            if not any((pair := parse_env_line(line)) is not None
                       and pair[0] == env_var
                       for line in env_content.splitlines()):
                _planner_warning(
                    f"{env_var} marked written but missing from .env. "
                    f"Re-run /setup-wizard and re-answer to restore."
                )
        # Intentionally return no plan entries for secrets.
    return plans


def cmd_reset(args) -> int:
    import subprocess
    workspace_root = args.workspace_root

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
            touched.add(_resolve_output_path(workspace_root, out_rel))

    # The dirty check runs HERE, after `touched` is known, and ignores it.
    #
    # It ran first until 2026-08-23 and refused on any dirty tracked file --
    # which is the state the wizard itself creates, so the non-force path was
    # reachable only when reset would do nothing. An always-failing gate is not
    # a safety check; it is training to pass --force, and --force discards the
    # unrelated hand edits this check exists to protect.
    if not args.force:
        try:
            status_out = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workspace_root, capture_output=True, text=True,
            )
        except FileNotFoundError:
            print("ERROR: git is not installed; cannot check for uncommitted "
                  "changes.", file=sys.stderr)
            return EXIT_SCHEMA_ERROR
        if status_out.returncode != 0:
            print(f"ERROR: git status failed: {status_out.stderr}", file=sys.stderr)
            return EXIT_SCHEMA_ERROR
        ours = {_git_rel(workspace_root, p) for p in touched}
        dirty = []
        for line in status_out.stdout.splitlines():
            if not line.strip() or line.startswith("??"):
                continue
            # porcelain v1: two status columns, a space, then the path.
            rel = line[3:].strip().strip('"')
            rel = rel.split(" -> ")[-1]          # a rename reports both sides
            if rel not in ours:
                dirty.append(line)
        if dirty:
            print("ERROR: uncommitted changes outside the wizard's own output. "
                  "Commit or stash them, or re-run with --force.", file=sys.stderr)
            for line in dirty[:10]:
                print(f"  {line}", file=sys.stderr)
            return EXIT_SCHEMA_ERROR

    # A git index must exist before anything is deleted.
    #
    # The loop below reverts a tracked file and DELETES an untracked one. In a
    # workspace that is not a git repository, `git ls-files --error-unmatch`
    # fails for every file, so every file took the delete branch: `--reset
    # --force` destroyed the operator's files outright, with no index to
    # restore them from, under a help line reading "Revert touched files to
    # git-index state". --force skipped the `git status` gate above, so nothing
    # else checked either. A missing `git` binary raised FileNotFoundError.
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=workspace_root, capture_output=True, text=True,
        )
    except FileNotFoundError:
        print("ERROR: git is not installed. --reset restores files from the "
              "git index and cannot run without it.", file=sys.stderr)
        return EXIT_SCHEMA_ERROR
    if inside.returncode != 0:
        # git ran and refused to answer. "not a git repository" is the common
        # cause, but "detected dubious ownership" exits 128 here too, and
        # reporting that one as "not a git work tree" sends the operator
        # looking for the wrong thing entirely. Quote git instead of guessing.
        print(f"ERROR: git refused to say whether {workspace_root} is a work "
              f"tree, so --reset will not DELETE the {len(touched)} touched "
              f"file(s). Refusing.", file=sys.stderr)
        for line in (inside.stderr or "").strip().splitlines()[:3]:
            print(f"  git: {line}", file=sys.stderr)
        return EXIT_SCHEMA_ERROR
    if inside.stdout.strip() != "true":
        print(f"ERROR: {workspace_root} is not a git work tree. --reset would "
              f"DELETE the {len(touched)} touched file(s) with no index to "
              f"restore them from. Refusing.", file=sys.stderr)
        return EXIT_SCHEMA_ERROR

    errors = []
    for path in touched:
        rel = _git_rel(workspace_root, path)
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
