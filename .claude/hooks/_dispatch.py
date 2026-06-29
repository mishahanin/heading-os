#!/usr/bin/env python3
"""Consolidated CEO-machine PreToolUse hook dispatcher.

Replaces separate Python subprocess invocations (prevent-secrets.py,
protect-personal-threads.py, etc.) with one process running all checks
in-process. Preserves first-block-wins semantics and aggregated advisory
output. (The `_secure/` vault and its `protect-secure` check were removed in
Plan 5 — vault removal; sensitivity is now the fail-closed `SENSITIVE_MODE`
flag in `scripts/utils/sensitive.py`.)

The original three scripts remain as thin shims that delegate here, so
exec workspaces whose settings.local.json was provisioned with the
original filenames keep working without re-provisioning.

Bash matcher scope: this dispatcher is registered for both Write|Edit|...
and Bash tool calls in settings.local.json. The Bash registration intentionally
omits `protect-corporate.py` and `protect-docs.py` — those two hooks operate
ONLY on file_path attributes, which Bash payloads do not carry. Both scripts
exit cleanly on empty file_path, so registering them for Bash would be a
zero-effect no-op subprocess cost. The path-scoped hooks remain registered
only for Write|Edit family tools where they have a target to inspect.
"""
from __future__ import annotations
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

WORKSPACE = Path(__file__).resolve().parent.parent.parent

# ============================================================
# check_prevent_secrets — secret patterns in content or Bash commands
# ============================================================

SECRET_PATTERNS = [
    (re.compile(r'sk-ant-[a-zA-Z0-9_-]{16,}'), "Anthropic API key"),
    (re.compile(r'pplx-[a-zA-Z0-9]{16,}'), "Perplexity API key"),
    (re.compile(r'r8_[a-zA-Z0-9]{16,}'), "Replicate API token"),
    (re.compile(r'fc-[A-Za-z0-9]{16,}'), "Firecrawl API key"),
    (re.compile(r'ctx7sk-[a-zA-Z0-9-]{16,}'), "Context7 API key"),
    (re.compile(r'ghp_[a-zA-Z0-9]{16,}'), "GitHub personal access token"),
    (re.compile(r'gho_[a-zA-Z0-9]{16,}'), "GitHub OAuth token"),
    (re.compile(r'AKIA[0-9A-Z]{16}'), "AWS access key"),
    (re.compile(r'xoxb-[0-9]+-[a-zA-Z0-9]+'), "Slack bot token"),
    (re.compile(r'xoxp-[0-9]+-[a-zA-Z0-9]+'), "Slack user token"),
    (re.compile(r'ya29\.[A-Za-z0-9._-]{50,}'), "Google OAuth token"),
    # JWT, PEM private keys, and credentialed connection strings (F-L3; mirror in secret-scanner.py)
    (re.compile(r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'), "JWT bearer token"),
    (re.compile(r'-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----'), "PEM private key"),
    (re.compile(r'[a-zA-Z][a-zA-Z0-9+.-]*://(?!user:pass(?:word)?@|username:password@)[^:@\s/?]{2,}:[^:@\s/?]{2,}@'), "connection string with inline credentials"),
    (re.compile(
        r'\*\*Password:\*\*\s+'
        r'(?!Stored|REDACTED|N/A|See |TBD|Change|Reset|Set |Use |Your )'
        r'[^\n]{8,}'
    ), "Plaintext password in markdown"),
    (re.compile(
        r'(?:EXCHANGE_PASSWORD|DB_PASSWORD|SMTP_PASSWORD|AUTH_PASSWORD)'
        r'\s*=\s*[A-Za-z0-9!@#$%^&*_+=-]{8,}'
    ), "Password in environment variable assignment"),
]

SECRETS_ALLOW_BASENAMES = {
    "prevent-secrets.py",   # Legacy hook with the same pattern catalog; self-trigger if scanned
    "secret-scanner.py",    # Git pre-commit secret-scanner (mirror of these patterns)
    ".env.example",         # Placeholder values only
}
# Path-scoped allow: only honoured when the file lives inside .claude/hooks/.
# A file named _dispatch.py anywhere else (outputs/, scripts/) must still be scanned.
SECRETS_ALLOW_HOOK_BASENAMES = {
    "_dispatch.py",
}
# Directory allow-list. Matched as path SEGMENTS, not raw substrings, so a
# look-alike like `mytests/security/` or `my.sessions/` does NOT slip past the
# secret scan. Anchoring added 2026-06-09 audit (hooks finding — substring match
# at SECRETS_ALLOW_PATHS bypassed the scan for any path merely CONTAINING the text).
SECRETS_ALLOW_DIR_SEGMENTS = [
    ".sessions/",                      # OAuth tokens, Telegram sessions
    "tests/security/",                 # Security test fixtures
]
SECRETS_ALLOW_EXACT_PATHS = {
    "outputs/browser/cookies.json",    # Browser cookies for headless automation  # leak-guard: ok (allowlist exact-path key, not path construction)
}

def _secrets_path_allowed(file_path: str) -> bool:
    # Normalize FIRST so a Windows-style backslash path resolves its basename
    # correctly even on Linux (os.path.basename does not split on "\" off Windows).
    normalized = file_path.replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1]
    if basename in SECRETS_ALLOW_BASENAMES:
        return True
    if basename in SECRETS_ALLOW_HOOK_BASENAMES and (
        "/.claude/hooks/" in normalized or normalized.startswith(".claude/hooks/")
    ):
        return True
    # Exact .env basename set only — `.env` and dotted variants (`.env.local`,
    # `.env.production`), but NOT look-alikes like `.envil` or `.environment`.
    if basename == ".env" or basename.startswith(".env."):
        return True
    if normalized in SECRETS_ALLOW_EXACT_PATHS:
        return True
    # Segment-anchored directory match: the segment must start at the path root
    # or be preceded by a `/`, so `tests/security/` matches `.../tests/security/x`
    # but never `.../mytests/security/x`.
    anchored = "/" + normalized
    for seg in SECRETS_ALLOW_DIR_SEGMENTS:
        if normalized.startswith(seg) or ("/" + seg) in anchored:
            return True
    return False

def _scan_for_secrets(text: str) -> Tuple[bool, Optional[str]]:
    if not text:
        return False, None
    for pattern, desc in SECRET_PATTERNS:
        if pattern.search(text):
            return True, desc
    return False, None

def check_prevent_secrets(payload: dict) -> Optional[dict]:
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    if tool_name == "Bash":
        # Bash payloads have no file_path; scan the command string only.
        command = tool_input.get("command", "")
        matched, desc = _scan_for_secrets(command)
        if matched:
            return {
                "decision": "block",
                "reason": (
                    f"BLOCKED: Detected {desc} in Bash command. "
                    f"Secrets must NEVER appear in commands. "
                    f"Use environment variables from .env instead."
                ),
            }
        return None
    file_path = tool_input.get("file_path", "")
    if not file_path or _secrets_path_allowed(file_path):
        return None
    content = tool_input.get("content", "") or ""
    new_string = tool_input.get("new_string", "") or ""
    matched, desc = _scan_for_secrets(content + "\n" + new_string)
    if matched:
        basename = os.path.basename(file_path)
        # NOTE: the reason text below references `.claude/hooks/prevent-secrets.py`,
        # NOT `.claude/hooks/_dispatch.py` where this code actually lives. This is
        # intentional: byte parity with the original prevent-secrets.py hook output
        # is preserved manually — regenerate fixtures via
        # `python outputs/operations/workspace/capture_hook_fixtures.py` and diff
        # against `tests/fixtures/expected/` before any change to this filename
        # reference. The actual allow-list is in SECRETS_ALLOW_BASENAMES /
        # SECRETS_ALLOW_HOOK_BASENAMES / SECRETS_ALLOW_PATHS above in this same
        # file (_dispatch.py).
        return {
            "decision": "block",
            "reason": (
                f"BLOCKED: Detected {desc} in content being written to {basename}. "
                f"Secrets must NEVER be written to workspace files. "
                f"Store API keys in .env (loaded via load_api_key() from scripts/utils/api.py). "
                f"Store passwords in a password manager. "
                f"If this is a false positive, the file may need to be added to the "
                f"allow-list in .claude/hooks/prevent-secrets.py."
            ),
        }
    return None

# ============================================================
# check_protect_personal_threads — block leaks of threads/personal/ content
# ============================================================

PERSONAL_PATH_RE = re.compile(r"threads[/\\]personal[/\\]", re.IGNORECASE)

# Order of patterns is irrelevant for correctness (any match blocks).
# The list follows the original protect-personal-threads.py order to
# preserve git blame lineage. Adding new patterns: append to the end
# or group with related shell-builtin / language-specific variants.
DANGEROUS_BASH_PATTERNS = [
    re.compile(r"\b(cp|mv|rsync|scp|xcopy|robocopy)\b.*threads[/\\]personal", re.IGNORECASE),
    re.compile(r"\b(tar|zip|7z|gzip)\b.*threads[/\\]personal", re.IGNORECASE),
    re.compile(r"\bcat\b.*threads[/\\]personal.*>", re.IGNORECASE),
    re.compile(r"\bgit\s+(add|stash\s+push)\b.*threads[/\\]personal", re.IGNORECASE),
    re.compile(r"<\s*threads[/\\]personal", re.IGNORECASE),
    re.compile(r"\btee\b.*threads[/\\]personal", re.IGNORECASE),
    re.compile(r"\bcat\b.*threads[/\\]personal.*\|\s*tee", re.IGNORECASE),
    re.compile(r"\bdd\b.*\bif=threads[/\\]personal", re.IGNORECASE),
    re.compile(r"\bcd\b.*threads[/\\]personal", re.IGNORECASE),
    re.compile(r"\b(cp|mv|rsync|scp|xcopy|robocopy)\b.*threads[/\\]archive.*[/\\]personal", re.IGNORECASE),
    re.compile(r"\bgit\s+(add|stash\s+push)\b.*threads[/\\]archive.*[/\\]personal", re.IGNORECASE),
    re.compile(r"\b(Copy-Item|Move-Item|Get-Content)\b.*threads[/\\]personal", re.IGNORECASE),
    re.compile(r"\bshutil\.(copy|copy2|move|copytree)\b.*threads[/\\]personal", re.IGNORECASE),
    re.compile(r"\bopen\s*\(\s*['\"]threads[/\\]personal", re.IGNORECASE),
    # Read-then-emit exfil: any plain read utility pointed at threads/personal/
    # dumps CEO-only content into the transcript (a leak by itself, no redirect
    # needed). Added 2026-06-09 audit (hooks finding 2 — guard was narrower than
    # secure-projects.md/security.md claim of technical enforcement).
    re.compile(r"\b(head|tail|sed|awk|base64|b64encode|xxd|od|strings|nl|fold|cut|less|more|grep|rg)\b.*threads[/\\]personal", re.IGNORECASE),
    re.compile(r"\bopen\s*\(\s*['\"][^'\"]*threads[/\\]personal", re.IGNORECASE),
]

ALLOWED_DOC_PATH_RE = re.compile(
    r"(?:^|/)("
    r"docs/superpowers/(plans|specs)/|"
    r"outputs/operations/scrutiny/|"  # leak-guard: ok (regex alternation branch)
    r"\.claude/(skills|rules|hooks)/|"
    r"reference/|"
    r"templates/|"
    r"tests/"
    r")",
)

def check_protect_personal_threads(payload: dict) -> Optional[dict]:
    """Block leaks of threads/personal/ content. Each block carries the
    `_policy_deny: True` flag — the dispatcher's main loop renders these as a
    PreToolUse permission deny (hookSpecificOutput / exit 0), so the CLI shows
    an intentional policy block with its reason, NOT a "hook error". The block
    is just as binding as the exit-2 path; only the presentation differs."""
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}

    if tool_name == "Bash":
        command = tool_input.get("command", "") or ""
        for pattern in DANGEROUS_BASH_PATTERNS:
            if pattern.search(command):
                cmd_display = command[:200] + ("..." if len(command) > 200 else "")
                return {
                    "decision": "block",
                    "reason": (
                        f"Personal-threads protection — intentional policy block, "
                        f"not an error. This shell command targets threads/personal/ "
                        f"(CEO-only content kept out of the transcript): {cmd_display}"  # leak-guard: ok (string in a message/log, not a path)
                    ),
                    "_policy_deny": True,
                }
        return None

    if tool_name == "Read":
        target = re.sub(r"\\+", "/", tool_input.get("file_path") or "")
        if PERSONAL_PATH_RE.search(target):
            return {
                "decision": "block",
                "reason": (
                    "Personal-threads protection — intentional policy block, not an "
                    "error. Reading a threads/personal/ file is not allowed: CEO-only "
                    "content must not enter the transcript."
                ),
                "_policy_deny": True,
            }
        return None

    if tool_name not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return None

    target = (tool_input.get("file_path") or tool_input.get("notebook_path") or "").replace("\\", "/")
    contents = []
    if tool_name == "Write":
        contents.append(tool_input.get("content") or "")
    elif tool_name == "Edit":
        contents.append(tool_input.get("new_string") or "")
    elif tool_name == "MultiEdit":
        for edit in (tool_input.get("edits") or []):
            contents.append(edit.get("new_string") or "")
    elif tool_name == "NotebookEdit":
        contents.append(tool_input.get("new_source") or "")

    if PERSONAL_PATH_RE.search(target):
        return None
    if ALLOWED_DOC_PATH_RE.search(target):
        return None
    for c in contents:
        if PERSONAL_PATH_RE.search(c):
            return {
                "decision": "block",
                "reason": (
                    f"Personal-threads protection — intentional policy block, not an "
                    f"error. Non-personal target {target!r} contains a "
                    f"threads/personal/ path reference."  # leak-guard: ok (string in a message/log, not a path)
                ),
                "_policy_deny": True,
            }
    return None

# ============================================================
# check_protect_corporate — exec-only block on corporate/ writes
# ============================================================
# Folded in from .claude/hooks/protect-corporate.py during Phase 2.3 of the
# 2026-05-12 perf v2 sprint. The standalone script remains in place as a
# backward-compat shim for any exec workspace whose settings.local.json
# was provisioned before this fold-in.

def check_protect_corporate(payload: dict) -> Optional[dict]:
    """Block writes to corporate/ in exec workspaces.

    Only fires when the workspace identity is exec-workspace. The CEO
    workspace never blocks corporate/ writes (it is the source of truth).
    """
    tool_name = payload.get("tool_name", "")
    if tool_name == "Bash":
        return None  # Bash payloads have no file_path

    project_dir = payload.get("cwd") or str(WORKSPACE)
    identity_file = Path(project_dir) / ".workspace-identity.json"
    if not identity_file.is_file():
        return None
    try:
        identity = json.loads(identity_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if identity.get("type") != "exec-workspace":
        return None  # CEO workspace -- no restriction

    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path", "") or ""
    if not file_path:
        return None

    file_path_norm = os.path.normpath(file_path)
    corporate_dir = os.path.normpath(os.path.join(project_dir, "corporate"))
    if file_path_norm.startswith(corporate_dir + os.sep) or file_path_norm == corporate_dir:
        return {
            "decision": "block",
            "reason": (
                "BLOCKED: Cannot write to corporate/ directory. "
                "This folder is read-only and managed by the CEO. "
                "Your changes would be overwritten on the next sync. "
                "If you need something changed, use /request-skill to ask the CEO."
            ),
        }
    return None


# ============================================================
# check_protect_docs — block direct edits to auto-synced docs/ files
# ============================================================
# Folded in from .claude/hooks/protect-docs.py during Phase 2.3 of the
# 2026-05-12 perf v2 sprint. SYNCED_FILES must match SYNC_FILES in
# scripts/sync-docs.py — keep in sync if either side changes.

SYNCED_FILES = {
    "GETTING-STARTED.md",
    "GETTING-STARTED.html",
    "CEO-ADMIN-GUIDE.md",
    "CEO-ADMIN-GUIDE.html",
    "EMERGENCY-PROCEDURES.md",
    "EMERGENCY-PROCEDURES.html",
}


def check_protect_docs(payload: dict) -> Optional[dict]:
    """Block direct Write/Edit to auto-synced docs/ files.

    The 8 shared documentation files in docs/ are auto-synced from
    templates/ by sync-docs.py (PostToolUse). Direct edits get silently
    overwritten on the next template change. This check steers Claude
    to edit templates/ instead.
    """
    tool_name = payload.get("tool_name", "")
    if tool_name == "Bash":
        return None  # Bash payloads have no file_path

    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path", "") or ""
    if not file_path:
        return None

    norm_path = file_path.replace("\\", "/")
    if "/docs/" not in norm_path:
        return None

    file_name = os.path.basename(file_path)
    if file_name not in SYNCED_FILES:
        return None

    return {
        "decision": "block",
        "reason": (
            f"BLOCKED: docs/{file_name} is auto-synced from templates/{file_name}. "
            f"Direct edits to docs/ get silently overwritten on the next template change. "
            f"Edit templates/{file_name} instead -- sync-docs.py will propagate the change "
            f"to docs/ automatically, and re-render HTML if applicable."
        ),
    }


# ============================================================
# check_cwd_anchor — catch root-relative workspace scripts run from a drifted shell
# ============================================================
# The Bash tool persists its working directory across calls. A command that
# launches a workspace script by a root-relative path (.claude/skills/.../x.py,
# scripts/x.py, .claude/hooks/x.py) fails with a cryptic ENOENT when an earlier
# `cd` left the shell parked in a subdirectory — the failure that motivated this
# check (a /viraid run that inherited a shell sitting in knowledge/odin-brain/).
#
# It fires ONLY when all three hold: (a) the live shell cwd (from the hook
# payload, which reflects real drift) is a subdirectory of the workspace root,
# (b) the command runs a root-relative workspace .py path, and (c) that path
# resolves from root but NOT from the current cwd. Condition (c) is a filesystem
# check, so the block fires only when the command is genuinely about to fail —
# no false positives, and no change to the permission posture (it blocks with the
# anchored command to run instead, rather than force-allowing a rewrite).

WORKSPACE_REL_SCRIPT_RE = re.compile(
    r"""["']?((?:\.claude/(?:skills|hooks)|scripts)/[^\s"';|&)]+\.py)"""
)

def check_cwd_anchor(payload: dict) -> Optional[dict]:
    if payload.get("tool_name") != "Bash":
        return None
    command = (payload.get("tool_input", {}) or {}).get("command", "") or ""
    if not command:
        return None
    # Self-anchored commands already cd to root — leave them alone.
    if "git rev-parse --show-toplevel" in command:
        return None
    cwd = payload.get("cwd") or ""
    try:
        norm_cwd = os.path.realpath(cwd) if cwd else ""
        norm_root = os.path.realpath(str(WORKSPACE))
    except (OSError, ValueError):
        return None
    if not norm_cwd or norm_cwd == norm_root:
        return None  # at root (or cwd unknown) — nothing to anchor
    if not norm_cwd.startswith(norm_root + os.sep):
        return None  # shell is outside the workspace — do not interfere

    for match in WORKSPACE_REL_SCRIPT_RE.finditer(command):
        rel = match.group(1)
        if os.path.isabs(rel):
            continue
        # Only act when the path resolves from root but is unreachable from cwd —
        # i.e. the command is about to fail purely because of shell drift.
        if os.path.exists(os.path.join(norm_root, rel)) and not os.path.exists(
            os.path.join(norm_cwd, rel)
        ):
            anchored = f'cd "$(git rev-parse --show-toplevel)" && {command}'
            return {
                "decision": "block",
                "reason": (
                    f"BLOCKED: the shell is parked in {norm_cwd}, but this command runs "
                    f"the root-relative path '{rel}', which only resolves from the "
                    f"workspace root — it would fail with ENOENT. Re-run it anchored to "
                    f"root:\n\n{anchored}"
                ),
            }
    return None


# ============================================================
# check_rate_limit — daily Write/Edit cap + runaway-loop detection
# ============================================================
#
# Closes P2.6 from the 2026-05-14 workspace deep audit. The dispatcher previously
# had no rate-limit / loop-detection. A runaway skill writing 10,000 files would
# only be stopped by Claude's own context window. This check catches the pattern
# at the hook layer with file-based daily counters.
#
# State file is best-effort - concurrent hook invocations may race and miscount
# by a few; we are not banking on exact counts, only on catching runaway loops.

import time
from datetime import datetime

RATE_LIMIT_STATE_FILE = WORKSPACE / ".claude" / "state" / "dispatch-rate.json"
RATE_LIMIT_SOFT = int(os.environ.get("WS_RATE_LIMIT_SOFT", "200"))   # advisory at N writes/day
RATE_LIMIT_HARD = int(os.environ.get("WS_RATE_LIMIT_HARD", "1000"))  # block at N writes/day
RATE_LIMIT_LOOP_WINDOW = 20      # how many recent calls to inspect
RATE_LIMIT_LOOP_THRESHOLD = 6    # same (tool, path) >= N times in window → advisory


def _load_rate_state() -> dict:
    if not RATE_LIMIT_STATE_FILE.exists():
        return {"date": "", "count": 0, "recent": []}
    try:
        return json.loads(RATE_LIMIT_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"date": "", "count": 0, "recent": []}


def _save_rate_state(state: dict) -> None:
    # Atomic write (tmp + os.replace) per the global atomic-state-write rule: a torn
    # write would silently reset the runaway-loop counter. Added 2026-06-09 audit.
    try:
        RATE_LIMIT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = RATE_LIMIT_STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, RATE_LIMIT_STATE_FILE)
    except Exception as e:
        print(f"[_dispatch:rate_limit] state save failed: {e}", file=sys.stderr)


def check_rate_limit(payload: dict) -> Optional[dict]:
    """Daily Write/Edit cap + runaway-loop detection.

    Soft limit emits advisory. Hard limit blocks. Loop detection (same tool + same
    file_path repeating in a short window) always emits advisory; never blocks
    (legitimate iterative refactors hit this too).

    Bash is excluded - it has its own surface and inflating Bash counts would mask
    the file-write loops this check exists to catch.
    """
    tool_name = payload.get("tool_name", "")
    if tool_name == "Bash":
        return None
    if tool_name not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return None

    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path", "") or tool_input.get("notebook_path", "")

    state = _load_rate_state()
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("date") != today:
        state = {"date": today, "count": 0, "recent": []}

    state["count"] = int(state.get("count", 0)) + 1
    state["recent"] = (state.get("recent", []) + [[tool_name, file_path, int(time.time())]])[-RATE_LIMIT_LOOP_WINDOW:]
    _save_rate_state(state)

    # Hard limit - block
    if state["count"] > RATE_LIMIT_HARD:
        return {
            "decision": "block",
            "reason": (
                f"BLOCKED: workspace daily write cap ({RATE_LIMIT_HARD}) exceeded "
                f"({state['count']} writes today). Pause and review what is producing "
                f"this volume. Override: `export WS_RATE_LIMIT_HARD=2000` if intentional, "
                f"or delete .claude/state/dispatch-rate.json to reset the counter."
            ),
        }

    # Soft limit - advisory
    if state["count"] == RATE_LIMIT_SOFT + 1:  # fire once per crossing
        return {
            "additionalContext": (
                f"NOTICE: {state['count']} file writes today (soft cap {RATE_LIMIT_SOFT}). "
                f"If this is an intentional batch operation, ignore. Hard cap is {RATE_LIMIT_HARD}."
            ),
        }

    # Loop detection - same (tool, file_path) repeating
    if file_path and len(state["recent"]) >= RATE_LIMIT_LOOP_THRESHOLD:
        signature = (tool_name, file_path)
        recent_signatures = [(r[0], r[1]) for r in state["recent"]]
        repeat_count = recent_signatures.count(signature)
        if repeat_count >= RATE_LIMIT_LOOP_THRESHOLD:
            return {
                "additionalContext": (
                    f"NOTICE: {tool_name} on '{os.path.basename(file_path)}' fired "
                    f"{repeat_count} times in the last {RATE_LIMIT_LOOP_WINDOW} writes. "
                    f"If this is iterative refactoring, continue. If it looks like a loop, stop."
                ),
            }

    return None


# ============================================================
# check_tool_budget — total-tool-call cap + same-args repeat detection
# ============================================================
#
# Closes P2.5 from the 2026-05-14 workspace deep audit. Complements check_rate_limit
# (which only counts Write/Edit) by tracking ALL tool calls (including Bash, Read,
# Grep, etc.) in a rolling time window. Catches the "agent in a loop" pattern that
# check_rate_limit misses when the loop happens through Read+Bash rather than writes.
#
# Token-usage half of the audit P2.5 acceptance criteria (soft 100K / hard 500K) is
# handled by Langfuse Cloud dashboards rather than at the hook layer - hooks don't
# see Claude's token usage, but Langfuse captures every messages.create() call with
# full token data via the @observe decorator. Set a Langfuse alert on cost/tokens
# per dashboard for the proper signal.

TOOL_BUDGET_WINDOW_MINUTES = 30
TOOL_BUDGET_SOFT = int(os.environ.get("WS_TOOL_BUDGET_SOFT", "75"))    # advisory at N
TOOL_BUDGET_HARD = int(os.environ.get("WS_TOOL_BUDGET_HARD", "1200"))  # block at N
TOOL_REPEAT_THRESHOLD = 3  # same (tool, hash(args)) 3+ in a row → advisory


def _stable_args_signature(tool_name: str, tool_input: dict) -> str:
    """Build a stable signature for tool+args to detect identical repeats."""
    try:
        # Sort keys so dict ordering doesn't fool the hash
        canonical = json.dumps(tool_input, sort_keys=True, default=str)
    except Exception:
        canonical = str(tool_input)
    return f"{tool_name}:{hash(canonical)}"


def check_tool_budget(payload: dict) -> Optional[dict]:
    """Total-tool-call cap in 30-min rolling window + same-args repeat detection.

    Counts every tool invocation (not just writes). Soft cap warns; hard cap blocks.
    Three identical calls in a row (same tool, same args) → advisory only - the
    pattern signals a stuck loop but legitimately re-running the same `python script
    --check` is not a bug.
    """
    tool_name = payload.get("tool_name", "")
    if not tool_name:
        return None
    tool_input = payload.get("tool_input", {}) or {}

    state = _load_rate_state()
    now_ts = int(time.time())
    window_seconds = TOOL_BUDGET_WINDOW_MINUTES * 60
    cutoff = now_ts - window_seconds

    # Keep tool-call history separately from write history (check_rate_limit owns "recent")
    tool_history = [
        entry for entry in state.get("tool_history", [])
        if isinstance(entry, list) and len(entry) >= 2 and entry[1] >= cutoff
    ]
    signature = _stable_args_signature(tool_name, tool_input)
    tool_history.append([signature, now_ts])
    # Keep history bounded, but the bound MUST stay above the hard cap or the cap
    # can never fire: count is len(tool_history), and truncating storage below the
    # cap means a reloaded window can never reach it. Margin of +100 over the cap.
    state["tool_history"] = tool_history[-(TOOL_BUDGET_HARD + 100):]
    _save_rate_state(state)

    count = len(tool_history)

    if count > TOOL_BUDGET_HARD:
        return {
            "decision": "block",
            "reason": (
                f"BLOCKED: {count} tool calls in the last {TOOL_BUDGET_WINDOW_MINUTES} "
                f"minutes exceeded hard cap ({TOOL_BUDGET_HARD}). The agent loop looks "
                f"runaway. Pause, review what's driving this volume. Override: "
                f"`export WS_TOOL_BUDGET_HARD=2000` if intentional, or delete "
                f".claude/state/dispatch-rate.json to reset."
            ),
        }

    if count == TOOL_BUDGET_SOFT + 1:  # fire once per crossing
        return {
            "additionalContext": (
                f"NOTICE: {count} tool calls in the last {TOOL_BUDGET_WINDOW_MINUTES} min "
                f"(soft cap {TOOL_BUDGET_SOFT}). Hard cap {TOOL_BUDGET_HARD}. If this is a "
                f"large batch operation, ignore; if the agent feels stuck, stop and reset."
            ),
        }

    # Same-args repeat detection - last N entries with this signature
    recent_sigs = [entry[0] for entry in tool_history[-TOOL_REPEAT_THRESHOLD:]]
    if len(recent_sigs) >= TOOL_REPEAT_THRESHOLD and all(s == signature for s in recent_sigs):
        return {
            "additionalContext": (
                f"NOTICE: {tool_name} fired {TOOL_REPEAT_THRESHOLD} times in a row with "
                f"identical args. If this is a deliberate retry, continue. If you're "
                f"stuck on the same operation, change approach."
            ),
        }

    return None


# ============================================================
# Dispatcher main
# ============================================================
CHECKS = [
    check_prevent_secrets,
    check_protect_personal_threads,
    check_protect_corporate,
    check_protect_docs,
    check_cwd_anchor,
    check_rate_limit,
    check_tool_budget,
]

def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception as e:
        # Deliberate fail-open ONLY for unparseable stdin: Claude Code always sends
        # well-formed JSON, so a parse failure means the harness contract is broken,
        # not an exfil attempt. Blocking here would wedge every tool call. Logged.
        print(f"[_dispatch] failed to parse input: {e}", file=sys.stderr)
        sys.exit(0)
    # Coerce a malformed payload into a safe shape so every check sees a dict and
    # none crash into the advisory-and-continue path (which would let the tool run
    # unchecked). Added 2026-06-09 audit (hooks finding — non-dict tool_input).
    if not isinstance(payload, dict):
        print("[_dispatch] payload is not a dict; treating as empty", file=sys.stderr)
        payload = {}
    if not isinstance(payload.get("tool_input"), dict):
        payload["tool_input"] = {}
    advisory = []
    for check in CHECKS:
        try:
            decision = check(payload)
        except Exception as e:
            # M2: don't silently swallow. Log to stderr (visible in hook errors)
            # AND emit an advisory to Claude so the model knows a check crashed
            # and can prompt the user to verify safety. Per CLAUDE.md global
            # rule: "All exception handlers must log or re-raise - never
            # silently swallow." The continue is acceptable here because the
            # advisory carries the signal.
            # NOTE: if a subsequent check fires a block, the advisory built
            # here is discarded (block path is terminal). The stderr log still
            # carries the error, so the signal is not lost — just delivered
            # via a different channel.
            print(f"[_dispatch:{check.__name__}] {e}", file=sys.stderr)
            advisory.append(
                f"HOOK INTERNAL ERROR in {check.__name__}: {e}. "
                f"Verify this operation is safe before proceeding."
            )
            continue
        if decision is None:
            continue
        if decision.get("decision") == "block":
            # protect-personal-threads blocks are rendered as a PreToolUse
            # permission deny (hookSpecificOutput / exit 0) so the CLI shows an
            # intentional policy block with its reason, NOT a "hook error" — the
            # exit-2 + stderr path the harness labels as an error. The deny is
            # just as binding as exit 2 (claude-code-guide confirmed both paths
            # block with 100% reliability); only the presentation changes.
            if decision.get("_policy_deny"):
                reason = decision.get("reason", "")
                json.dump(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": reason,
                        }
                    },
                    sys.stdout,
                )
                sys.exit(0)
            # Strip internal flags (defensive — shouldn't be present here).
            public = {k: v for k, v in decision.items() if not k.startswith("_")}
            json.dump(public, sys.stdout)
            sys.exit(0)
        if decision.get("additionalContext"):
            advisory.append(decision["additionalContext"])
    if advisory:
        json.dump({"additionalContext": "\n".join(advisory)}, sys.stdout)
    sys.exit(0)

if __name__ == "__main__":
    main()
