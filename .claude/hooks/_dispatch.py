#!/usr/bin/env python3
"""Consolidated CEO-machine PreToolUse hook dispatcher.

Replaces separate Python subprocess invocations (prevent-secrets.py,
protect-personal-threads.py, etc.) with one process running all checks
in-process. Preserves first-block-wins semantics and aggregated advisory
output. (The `_secure/` vault and its `protect-secure` check were removed in
Plan 5 — vault removal; sensitivity is now the fail-closed `SENSITIVE_MODE`
flag in `scripts/utils/sensitive.py`.)

There are NO delegating shims. `prevent-secrets.py`, `protect-corporate.py`,
`protect-docs.py` and `protect-personal-threads.py` were all deleted in commit
ba1affd; this docstring claimed until 2026-08-25 that they "remain as thin shims
that delegate here, so exec workspaces provisioned with the original filenames
keep working without re-provisioning". They do not exist, so a workspace whose
settings.local.json still names one of them runs with that wall entirely absent
and MUST be re-provisioned.

Matcher scope: settings.local.json registers this dispatcher under three
matchers — `Write|Edit|MultiEdit|NotebookEdit`, `Bash`, and `Read`. All three
payload shapes reach every check, so a new check has to answer what it does with
each of them rather than inherit a two-matcher assumption. Read carries a
`file_path` but no content, which is why `check_protect_corporate` and
`check_protect_docs` exclude it by name: `check_protect_docs` reaching a path
test on a Read payload is what policy-denied an ordinary operator Read at
2026-08-11T21:44:19, recorded in the denial log. The path-scoped checks return
None on a Bash payload for the plainer reason that it carries no `file_path` at
all.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import sys
from pathlib import Path

# `typing` is deliberately NOT imported. `from __future__ import annotations`
# above makes every annotation in this file a string that nothing evaluates, and
# `python3 -X importtime` charged the import 4.9 ms of the ~43 ms this hook costs
# on EVERY Write, Edit, MultiEdit, NotebookEdit, Bash and Read. Removed
# 2026-08-20. If you add a runtime typing use here (cast, TypeVar, a validated
# model), import it inside the function that needs it, not at module scope.

WORKSPACE = Path(__file__).resolve().parent.parent.parent

def _record_denial(mechanism: str, payload: dict, reason: str) -> None:
    """Count one refusal. Telemetry only — it can never change a decision.

    Called from main()'s terminal deny path and from nowhere else, so a check
    added tomorrow is counted by construction rather than by its author
    remembering to add a line. Lazy and guarded, because this runs inside the
    workspace's only synchronous wall between a model mistake and a written
    credential, and no failure here may take that wall down. It runs on denials
    only, which are rare, so the overwhelming majority of tool calls never pay
    for the import.
    """
    try:
        if str(WORKSPACE) not in sys.path:
            sys.path.insert(0, str(WORKSPACE))
        from scripts.utils.denial_log import log_denial

        tool_input = payload.get("tool_input") or {}
        log_denial(
            mechanism=mechanism,
            action=payload.get("tool_name") or "unknown",
            path=(tool_input.get("file_path")
                  or tool_input.get("notebook_path")
                  or None),
            reason=reason,
        )
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[_dispatch] denial counter unavailable ({type(exc).__name__}): {exc}",
              file=sys.stderr)

# ============================================================
# check_prevent_secrets — secret patterns in content or Bash commands
# ============================================================

SECRET_PATTERNS = [
    (re.compile(r'sk-ant-[a-zA-Z0-9_-]{16,}'), "Anthropic API key"),
    (re.compile(r'pplx-[a-zA-Z0-9]{16,}'), "Perplexity API key"),
    (re.compile(r'r8_[a-zA-Z0-9]{16,}'), "Replicate API token"),
    (re.compile(r'fc-[A-Za-z0-9]{16,}'), "Firecrawl API key"),
    (re.compile(r'ctx7sk-[a-zA-Z0-9-]{16,}'), "Context7 API key"),
    (re.compile(r'cpx-[a-zA-Z0-9]{16,}'), "CLIProxyAPI local proxy key"),
    (re.compile(r'ghp_[a-zA-Z0-9]{16,}'), "GitHub personal access token"),
    (re.compile(r'gho_[a-zA-Z0-9]{16,}'), "GitHub OAuth token"),
    (re.compile(r'AKIA[0-9A-Z]{16}'), "AWS access key"),
    (re.compile(r'xoxb-[0-9]+-[a-zA-Z0-9]+'), "Slack bot token"),
    (re.compile(r'xoxp-[0-9]+-[a-zA-Z0-9]+'), "Slack user token"),
    (re.compile(r'ya29\.[A-Za-z0-9._-]{50,}'), "Google OAuth token"),
    # JWT, PEM private keys, and credentialed connection strings (F-L3; mirror in secret-scanner.py)
    (re.compile(r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'), "JWT bearer token"),
    (re.compile(r'-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----'), "PEM private key"),
    (re.compile(r'[a-zA-Z][a-zA-Z0-9+.-]{0,31}://(?!user:pass(?:word)?@|username:password@)[^:@\s/?]{2,}:[^:@\s/?]{2,}@'), "connection string with inline credentials"),
    (re.compile(
        r'\*\*Password:\*\*\s+'
        r'(?!Stored|REDACTED|N/A|See |TBD|Change|Reset|Set |Use |Your )'
        r'[^\n]{8,}'
    ), "Plaintext password in markdown"),
    # Whole-value placeholder exclusion; the rationale, the dropped inert `<`
    # alternative, and the residual bound are documented at the mirror of this
    # entry in scripts/utils/secret_patterns.py. The regex text must stay
    # byte-identical to that mirror; test_SEC_004 holds the two in lockstep.
    (re.compile(
        r'(?:EXCHANGE_PASSWORD|DB_PASSWORD|SMTP_PASSWORD|AUTH_PASSWORD)'
        r'\s*=\s*'
        r'(?i:(?!'
        r'(?=[A-Za-z0-9_-]*(?:your|changeme|example|placeholder|redacted|dummy|x{3,}))'
        r'(?:[A-Za-z]+[0-9]{0,3}|[0-9]{1,4})'
        r'(?:[-_](?:[A-Za-z]+[0-9]{0,3}|[0-9]{1,4}))*'
        r'(?![A-Za-z0-9!@#$%^&*_+=-])'
        r'))'
        r'[A-Za-z0-9!@#$%^&*_+=-]{8,}'
    ), "Password in environment variable assignment"),
]

# Mirror of REQUIRED_SUBSTRING in scripts/utils/secret_patterns.py, embedded for
# the same reason the pattern list is. A pattern that cannot match without a
# literal substring records it; testing the substring first is O(n) and changes
# no verdict. Measured 2026-07-31: without it a 200 KB single-string scan costs
# 81.6s here, with it 0.00223s.
REQUIRED_SUBSTRING = {
    "connection string with inline credentials": "://",
}

# Every allowance below is PATH-SCOPED, with ONE deliberate exception named
# here rather than left for a reader to trip over: the `.env` / `.env.*` branch
# in _secrets_path_allowed is basename-wide by design, because `git check-ignore`
# confirms both repositories ignore those names at any depth, so such a file can
# never be committed and never reaches a wall.
#
# For everything else there is deliberately no basename-wide set any more: one
# existed until 2026-07-31 and allowed prevent-secrets.py, secret-scanner.py and
# .env.example anywhere in EITHER repository, which was measured against the
# live gate with a planted key in outputs/scratch/, knowledge/ and
# crm/contacts/ — all three written successfully. The scanner side (SKIP_PATHS in
# scripts/secret-scanner.py) already carried repo-relative paths for the same
# three files; the WORKSPACE_PATHS set is the hook's alignment with it. The
# directory set below has no scanner counterpart at all, so it is anchored on its
# own terms.
#
# Every file below is allowed WORKSPACE-EXACT, never by containing directory.
# `scripts/`, `.claude/hooks/` and `scripts/utils/` are all ordinary creatable
# directory names, so a segment match leaves a decoy — outputs/scratch/scripts/
# secret-scanner.py, outputs/scratch/.claude/hooks/_dispatch.py,
# outputs/scratch/scripts/utils/secret_patterns.py — unscanned, which is a
# smaller version of the basename-anywhere hole this file closed on the same
# day. The first narrowing reached only the `scripts/` entry; the other two
# stayed directory-scoped until the decoys were measured against the live gate
# and written successfully. The one case the wider scope also covered, a nested
# scripts/utils/nested/secret_patterns.py, exists nowhere in the tree.
#
# These are exact repo-relative paths, matching the scanner's own SKIP_PATHS
# rather than widening past it. _is_workspace_file honours the relative form and
# THIS workspace's absolute form; a foreign absolute root is not this file.
#
# Why each is exempt: secret_patterns.py holds the vocabulary by definition and
# would self-trigger on every edit, and _dispatch.py embeds a copy of it for the
# same reason. secret-scanner.py has held ZERO re.compile calls since the
# vocabulary moved out of it, so its "same pattern catalog" justification stopped
# being true; it is kept because the scanner skips it by path and the two walls
# agreeing is worth more than removing an allowance that costs nothing. The fourth
# entry, .claude/hooks/prevent-secrets.py, went with the shim itself on
# 2026-08-11 — an allowance for a file that cannot exist is a name waiting for
# someone to recreate it and inherit the exemption.
SECRETS_ALLOW_WORKSPACE_PATHS = {
    "scripts/secret-scanner.py",
    "scripts/utils/secret_patterns.py",
    ".claude/hooks/_dispatch.py",
}
# Directory allow-list, anchored to THIS workspace's own directory, not to any
# path that happens to contain the segment.
#
# Segment-anchoring alone was not enough, and this is the same hole one level up
# from the basename set above. `tests/security/` and `.sessions/` are ordinary
# creatable directory names, so while the old rule accepted any path merely
# CONTAINING the segment, a decoy at outputs/scratch/tests/security/planted.py,
# knowledge/tests/security/planted.md, outputs/scratch/.sessions/planted.json or
# crm/contacts/.sessions/planted.md went unscanned. All four were measured
# against the live gate and written successfully. The data-overlay case is the
# live one: `git check-ignore` returns 1 for outputs/reports/tests/security/, so
# such a decoy would be TRACKED, and unlike the basename hole the scanner's
# SKIP_PATHS carries no counterpart for either directory at all.
SECRETS_ALLOW_DIR_SEGMENTS = [
    ".sessions/",                      # OAuth tokens, Telegram sessions
    "tests/security/",                 # Security test fixtures
]
SECRETS_ALLOW_EXACT_PATHS = {
    "outputs/browser/cookies.json",    # Browser cookies for headless automation  # leak-guard: ok (allowlist exact-path key, not path construction)
}

def _under_dir(normalized: str, segment: str) -> bool:
    """True only for THIS workspace's own `segment` directory, relative or absolute.

    Anchored at the workspace root, not merely at a `/`. A look-alike like
    `mytests/security/x` was already excluded by the older segment anchoring;
    what that anchoring still admitted was a REAL `tests/security/` nested
    anywhere, e.g. outputs/scratch/tests/security/x.py. See the comment on
    SECRETS_ALLOW_DIR_SEGMENTS for the measurement.
    """
    return normalized.startswith((segment, WORKSPACE.as_posix() + "/" + segment))


def _is_workspace_file(normalized: str, rel: str) -> bool:
    """True only for THIS workspace's own copy of `rel`, absolute or relative."""
    return normalized == rel or normalized == (WORKSPACE / rel).as_posix()


def _secrets_path_allowed(file_path: str) -> bool:
    # Normalize FIRST so a Windows-style backslash path resolves its basename
    # correctly even on Linux (os.path.basename does not split on "\" off Windows).
    normalized = file_path.replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1]
    if any(_is_workspace_file(normalized, rel) for rel in SECRETS_ALLOW_WORKSPACE_PATHS):
        return True
    # Exact .env basename set only — `.env` and dotted variants (`.env.local`,
    # `.env.production`), but NOT look-alikes like `.envil` or `.environment`.
    #
    # `.env.example` is carved OUT, and it is the one exception worth stating.
    # The rest of this branch exempts the gitignored files that legitimately
    # hold live credentials; a `.example` template holds placeholders by
    # definition, so it needs no exemption, and the old basename-wide entry let
    # a planted key be written to a `.env.example` in any directory. Scanning it
    # costs nothing (measured: the real template is clean) and a real credential
    # appearing in one is a finding rather than a false positive. This is
    # STRICTER than the scanner's SKIP_PATHS, which still skips the repo-root
    # `.env.example`; the difference is deliberate and in the safe direction.
    if basename != ".env.example" and (basename == ".env" or basename.startswith(".env.")):
        return True
    if normalized in SECRETS_ALLOW_EXACT_PATHS:
        return True
    for seg in SECRETS_ALLOW_DIR_SEGMENTS:
        if _under_dir(normalized, seg):
            return True
    return False

def _scan_for_secrets(text: str) -> tuple[bool, str | None]:
    if not text:
        return False, None
    for pattern, desc in SECRET_PATTERNS:
        needle = REQUIRED_SUBSTRING.get(desc)
        if needle is not None and needle not in text:
            continue
        if pattern.search(text):
            return True, desc
    return False, None

def check_prevent_secrets(payload: dict) -> dict | None:
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
    # All four write shapes the dispatcher is registered for, not just the two
    # that carry their text at the top level. MultiEdit keeps every replacement
    # in edits[i]["new_string"] and NotebookEdit names its target notebook_path
    # and its text new_source, so both walked past this gate untouched until
    # 2026-07-31: the notebook returned at the empty-path guard below, the
    # MultiEdit scanned an empty string. check_protect_personal_threads
    # destructures the same four correctly, and it is the fix for a prior
    # instance of this bug — the shape is copied from it deliberately.
    file_path = tool_input.get("file_path", "") or tool_input.get("notebook_path", "") or ""
    if not file_path or _secrets_path_allowed(file_path):
        return None
    parts = [
        tool_input.get("content") or "",
        tool_input.get("new_string") or "",
        tool_input.get("new_source") or "",
    ]
    for edit in (tool_input.get("edits") or []):
        if isinstance(edit, dict):
            parts.append(edit.get("new_string") or "")
    matched, desc = _scan_for_secrets("\n".join(parts))
    if matched:
        basename = os.path.basename(file_path)
        # The reason text used to name `.claude/hooks/prevent-secrets.py` for byte
        # parity with the original per-hook script. That script was a runpy shim
        # and was removed on 2026-08-11, so the parity had nothing left to match
        # and the message pointed an operator at a file that does not exist. It
        # now names this file, which is where the four SECRETS_ALLOW_* sets above
        # actually live. No fixture in tests/fixtures/expected/ carries the string.
        return {
            "decision": "block",
            "reason": (
                f"BLOCKED: Detected {desc} in content being written to {basename}. "
                f"Secrets must NEVER be written to workspace files. "
                f"Store API keys in .env (loaded via load_api_key() from scripts/utils/api.py). "
                f"Store passwords in a password manager. "
                f"If this is a false positive, the file may need to be added to the "
                f"allow-list in .claude/hooks/_dispatch.py."
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

def check_protect_personal_threads(payload: dict) -> dict | None:
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
# 2026-05-12 perf v2 sprint. That standalone script was deleted in ba1affd,
# together with protect-docs.py, protect-personal-threads.py and
# prevent-secrets.py, so there is no backward-compat shim: a workspace whose
# settings.local.json still names one of those files has NO PreToolUse wall at
# all and must be re-provisioned.

_IDENTITY_FILE = ".workspace-identity.json"


def _identity_root(cwd: str) -> Path | None:
    """The nearest directory at or above `cwd` that carries the identity file.

    Falls back to the workspace root, then answers None. `cwd` is the live shell
    directory, so it is a starting point and never an answer on its own.
    """
    if cwd:
        try:
            start = Path(cwd).resolve()
        except (OSError, ValueError):
            start = None
        if start is not None:
            for directory in (start, *start.parents):
                if (directory / _IDENTITY_FILE).is_file():
                    return directory
    if (WORKSPACE / _IDENTITY_FILE).is_file():
        return WORKSPACE
    return None


def check_protect_corporate(payload: dict) -> dict | None:
    """Block writes to corporate/ in exec workspaces.

    Fires when the workspace identity reads exec-workspace, and also when an
    identity file is present but unreadable, because then the type is unknown
    and "allow" is the wrong default for a wall. The CEO workspace never blocks
    corporate/ writes (it is the source of truth).
    """
    tool_name = payload.get("tool_name", "")
    # Bash carries no file_path; Read carries one but writes nothing, and this
    # check blocks WRITES to corporate/. Before 2026-08-20 only Bash was excluded,
    # so every Read stat'ed and JSON-parsed .workspace-identity.json to reach a
    # verdict it could never return.
    #
    # Excluded by name rather than gated on a write allow-list, deliberately: a
    # tool shape added to this hook's matcher later must arrive INSIDE the check,
    # not silently outside it.
    if tool_name in ("Bash", "Read"):
        return None

    # `payload["cwd"]` is the LIVE shell cwd and it drifts - check_cwd_anchor one
    # screen down is built entirely on that fact, and the denial log holds 14
    # refusals whose cwd was a subdirectory. Reading the identity file only at
    # that exact directory therefore meant: exec workspace, shell cd'd anywhere
    # below root, file absent, return None - allow. The wall switched off for the
    # whole session on the first `cd`, and the executive's corporate/ edit was
    # then silently overwritten on the next sync, which is the outcome the block
    # message describes preventing. Walk up instead, the way the hook launcher in
    # settings.local.json already locates this very file.
    project_dir = _identity_root(payload.get("cwd") or "")
    if project_dir is None:
        # No identity file anywhere above the cwd or at the workspace root. That
        # is an ordinary CEO or public clone, which has no corporate/ layer to
        # protect - a correct no-op now rather than an accidental one.
        return None
    identity_file = project_dir / ".workspace-identity.json"
    unreadable = ""
    try:
        identity = json.loads(identity_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        identity, unreadable = {}, f"{type(exc).__name__}: {exc}"
    if not isinstance(identity, dict):
        identity, unreadable = {}, "the file is not a JSON object"
    # A PRESENT identity file this hook cannot parse used to return None, which
    # is "allow" - so one corrupt byte switched the corporate wall off for the
    # whole session, and the executive's edit was silently overwritten on the
    # next sync. It cannot be read as a CEO workspace either: `_identity_root`
    # only answers a directory that HAS the file.
    #
    # The refusal is scoped to the WRITE, not to the environment. An unreadable
    # identity blocks corporate/ and nothing else; every other path in the
    # workspace keeps working, so a broken file costs one clear message instead
    # of a dead session.
    if not unreadable and identity.get("type") != "exec-workspace":
        return None  # CEO workspace -- no restriction

    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path", "") or ""
    if not file_path:
        return None

    file_path_norm = os.path.normpath(file_path)
    corporate_dir = os.path.normpath(os.path.join(str(project_dir), "corporate"))
    if file_path_norm.startswith(corporate_dir + os.sep) or file_path_norm == corporate_dir:
        if unreadable:
            return {
                "decision": "block",
                "reason": (
                    f"BLOCKED: cannot read {identity_file} ({unreadable}), so this "
                    "workspace's type is unknown and the corporate/ wall cannot be "
                    "resolved. Repair that file, then retry. Only corporate/ is "
                    "blocked; the rest of the workspace is unaffected."
                ),
            }
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


def check_protect_docs(payload: dict) -> dict | None:
    """Block direct Write/Edit to auto-synced docs/ files.

    The 6 shared documentation files in SYNCED_FILES are auto-synced from
    templates/ by sync-docs.py (PostToolUse). The count is stated because a
    coverage claim on a wall should be checkable; it said 8 against a set of 6
    until 2026-08-23, and tests/test_dispatch_docstrings_match_the_code.py now
    reads both. Direct edits get silently
    overwritten on the next template change. This check steers Claude
    to edit templates/ instead.
    """
    tool_name = payload.get("tool_name", "")
    # This one had teeth. Until 2026-08-20 only Bash was excluded, so a READ of
    # docs/EMERGENCY-PROCEDURES.md fell through to the path test and returned
    # decision:block, which the harness renders as a permission deny.
    # `.logs/denials/denials.jsonl` records a real operator Read refused this way
    # at 2026-08-11T21:44:19. The check steers EDITS to templates/; reading the
    # synced copy was never what it guards.
    #
    # Excluded by name, not gated on a write allow-list, for the same reason as
    # check_protect_corporate above: a new tool shape must land inside the check.
    if tool_name in ("Bash", "Read"):
        return None

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
# check on the exact token the shell will resolve, so the block fires only on a
# path that really is unreachable from that cwd, and no permission posture
# changes (it blocks with the anchored command to run instead, rather than
# force-allowing a rewrite).
#
# The left anchor is load-bearing and was missing until 2026-08-25. Without it
# the pattern matched the `scripts/...py` TAIL inside a fully-qualified absolute
# path, so `.venv/bin/python /home/.../heading-os/scripts/run-tests.py` extracted
# `scripts/run-tests.py`, found it under root and not under the drifted cwd, and
# refused a command that would have run perfectly - while telling the operator it
# "would fail with ENOENT", a cause the code had not established, since an
# absolute path resolves from any directory. `.logs/denials/denials.jsonl` records
# a real refusal of this shape at ts 1785739896.95. The lookbehind admits only a
# separator, so a match can no longer begin in the middle of a path.

WORKSPACE_REL_SCRIPT_RE = re.compile(
    r"""(?:^|(?<=[\s"'=(|&;]))["']?((?:\./)?(?:\.claude/(?:skills|hooks)|scripts)/[^\s"';|&)]+\.py)"""
)

def check_cwd_anchor(payload: dict) -> dict | None:
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
        # `norm_cwd == norm_root` changes no verdict and is kept for cost: when
        # the shell IS at root, condition (c) below resolves the path from the
        # same directory twice and always answers "reachable", so the outcome is
        # this same None after a regex scan of the whole command. This hook runs
        # on every Bash call, so the scan is worth skipping. Noted because a
        # mutation sweep will keep surfacing the line as untested - it is
        # untestable through behaviour, being an equivalent short-circuit.
        return None  # at root (or cwd unknown) — nothing to anchor
    if not norm_cwd.startswith(norm_root + os.sep):
        return None  # shell is outside the workspace — do not interfere

    for match in WORKSPACE_REL_SCRIPT_RE.finditer(command):
        rel = match.group(1)
        # There was an `if os.path.isabs(rel): continue` here. It never fired and
        # never could: the group can only begin at `.claude/`, `./` or `scripts`,
        # so `rel` is relative by construction. It read as the guard against the
        # absolute-path false positive while doing nothing about it — the left
        # anchor above is what actually enforces this now, and the test named
        # below fails if that anchor is loosened.
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
# check_slow_shell — the two Bash shapes that spend the operator's wall clock
# ============================================================
#
# Measured over the six sessions ending 2026-08-22, from the session transcripts:
# the Bash tool held 4.85 h of wall time, median call 0.4 s, and two shapes owned
# 65% of the total.
#
#   blocking waiters      2.07 h / 19 calls / avg 393 s
#   the suite run serially 1.05 h / 107 calls, the long ones 434-601 s each
#
# Both have a ready replacement that was already in the tree. `run_in_background:
# true` returns at once and wakes the turn on exit, so a wait costs nothing.
# `scripts/run-tests.py` has passed `-n auto` since the push gate was
# parallelized; measured 2026-08-22 on 16 cores, the full suite finishes in
# 88.88 s there against 434-601 s for a bare serial `pytest`.
#
# This is a habit guard, so it is written as a wall rather than a note: recall
# across sessions is the thing that failed, and a rule that depends on the same
# recall would fail with it. The `slow-shell-ok` marker keeps a deliberate
# exception possible and greppable — a wall with no door gets torn down.

SLOW_SHELL_ESCAPE = "slow-shell-ok"

_SHELL_OPERATORS = ";|&\n"


def _shell_segments(command: str) -> list:
    """Split a compound command into the parts a shell would run separately.

    Quote-aware, because a regex is not. The first cut here split on `|`
    unconditionally, and on 2026-08-22 it refused
    `ls auto-memory/ | grep -iE "test|pytest|shell"` — the alternation inside the
    quoted pattern broke into a segment whose only word was `pytest`, so a
    directory listing read as a suite run. A metacharacter inside quotes is data.

    Only the separators matter here, so `>` and `<` are left in place; `2>&1`
    splitting at the `&` is harmless, since every caller looks at where a word
    sits, not at redirection.
    """
    segments, buf = [], []
    quote = None
    index = 0
    while index < len(command):
        char = command[index]
        if quote:
            buf.append(char)
            if char == "\\" and quote == '"' and index + 1 < len(command):
                buf.append(command[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            buf.append(char)
            index += 1
            continue
        if char == "\\" and index + 1 < len(command):
            buf.append(char)
            buf.append(command[index + 1])
            index += 2
            continue
        if char in _SHELL_OPERATORS:
            segments.append("".join(buf))
            buf = []
            index += 1
            continue
        buf.append(char)
        index += 1
    segments.append("".join(buf))
    return [segment for segment in (part.strip() for part in segments) if segment]

# A polling loop: any while/until that sleeps inside its body. Duration is not the
# question here — the loop holds the turn for as long as the watched thing runs.
_POLL_LOOP_RE = re.compile(r"\b(?:while|until)\b[\s\S]*?\bsleep\b")

# A bare wait long enough that the operator feels it. Short sleeps (a daemon
# socket coming up) are ordinary and stay allowed.
_BLOCKING_SLEEP_SECONDS = 30

# Flags that narrow a pytest run to something that finishes in seconds, or that
# already distribute it. Either way there is nothing to correct.
_PYTEST_PARALLEL_FLAGS = ("-n", "--numprocesses")
_PYTEST_NARROWING_FLAGS = (
    "-k", "--collect-only", "--co", "--lf", "--last-failed", "--ff", "--failed-first",
)

_SERIAL_SUITE_REASON = """\
BLOCKED: this runs the suite in one process. Measured 2026-08-22 on this machine, \
the same 6164 tests finish in 88.88 s across 16 workers and 434-601 s serially — \
the serial shape spent 1.05 h of Bash wall time over the six sessions ending \
2026-08-22.

Use the runner, which has passed `-n auto` since the push gate was parallelized:

    .venv/bin/python scripts/run-tests.py

Or add the flag to the command you had: `-n auto`.

Narrow runs are untouched — a file path, a directory below the suite root such \
as `tests/security`, a node id, `-k`, or `--collect-only` all pass. An \
UNEXPANDED shell variable (`pytest $T`) is not one of them: this hook reads the \
command text, not the shell's environment, so it cannot tell `$T` from the whole \
suite and refuses. Write the path out. \
For a deliberate serial run (a baseline measurement, a plugin that will not \
distribute), append `# {escape}` and it goes through."""

_WAITER_REASON = """\
BLOCKED: this command waits, and the wait holds the turn. Waiters of this shape \
cost 2.07 h of Bash wall time over 19 calls (avg 393 s) in the six sessions \
ending 2026-08-22, and none of that time did anything.

Run the long command itself with `run_in_background: true` instead. The tool \
returns immediately and the turn is re-invoked when the command exits, so the \
wait is free and the output is still delivered.

Short sleeps are untouched — under {threshold} s with no polling loop passes. \
For a deliberate blocking wait, append `# {escape}` and it goes through."""


def _pytest_argv(command: str) -> list | None:
    """The argv of a pytest invocation in `command`, or None if there is none.

    Judged positionally: `pytest` must be the first word of a shell segment, or
    follow `-m` on an interpreter. A `pytest` that is merely an argument to some
    other program — a grep pattern, a path being echoed — is not an invocation
    and must not be treated as one.
    """
    import shlex  # local: this hook runs on every Bash, Read and write call

    for segment in _shell_segments(command):
        if "pytest" not in segment:
            continue
        try:
            tokens = shlex.split(segment, comments=True)
        except ValueError:
            continue  # unbalanced quotes — not something to reason about
        if not tokens:
            continue
        # `word`, not `token`: ruff's S105 reads a variable called `token` as a
        # credential and flags the comparison as a hardcoded password.
        for index, word in enumerate(tokens):
            if word != "pytest" and not word.endswith("/pytest"):
                continue
            if index == 0:
                return tokens
            if tokens[index - 1] == "-m":
                return tokens
        # `python scripts/run-tests.py` reaches pytest through the runner, which
        # already distributes; it is the prescribed form, not a finding.
    return None


# Flags whose NEXT argv word is a value, not a test target. Without this, the
# value was read as a named target: `pytest --rootdir /a/b` counted `/a/b` as a
# narrow run and the whole suite went through serially, having named no target
# at all. The attached spelling (`--rootdir=/a/b`) was already handled, because
# it starts with `-`.
_PYTEST_VALUE_FLAGS = ("--rootdir", "-p", "-k", "-o", "--basetemp",
                       "-c", "--override-ini", "--junitxml", "-W")


def _is_serial_full_suite(argv: list) -> bool:
    skip_next = False
    for index, token in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if token.startswith(_PYTEST_PARALLEL_FLAGS):
            return False  # -n, -n8, -nauto, --numprocesses=auto
        if token in _PYTEST_NARROWING_FLAGS or token.startswith("--collect-only"):
            return False
        if token in _PYTEST_VALUE_FLAGS:
            skip_next = True
            continue
        if index == 0:
            continue
        # A named target — a file, a directory below tests/, a node id — is a
        # narrow run whose duration is not the problem this guard was built for.
        if token.endswith(".py") or "::" in token:
            return False
        if _is_subdirectory_target(token):
            return False
    return True


def _is_subdirectory_target(token: str) -> bool:
    """True for a directory BELOW the suite root, e.g. `tests/security`.

    The comment above has claimed since this guard shipped that a directory
    below tests/ counts as narrow. The code did not implement it: only `.py`
    and `::` were accepted, so `pytest tests/security` was policy-denied with a
    message promising narrow runs are untouched. Found by the 2026-08-23 audit
    and reproduced. A guard that refuses the exact shape its own text exempts
    teaches the operator to reach for `# slow-shell-ok`, which is the one
    outcome a habit guard must never produce.

    The distinction that matters is depth, not existence: `tests/` IS the full
    suite and must stay blocked, while `tests/security` is a fast subset.

    Depth is measured AFTER stripping the workspace root, because counting raw
    segments made the spelling decide the verdict:
    `pytest /home/.../heading-os/tests` has six segments and read as narrow,
    so the identical 6000-test serial run passed by absolute path while
    `pytest tests/` was blocked. Agent threads reset cwd between calls and this
    workspace instructs absolute paths, so that was the likely spelling, not an
    exotic one. Stripping the root is string work; the filesystem is still not
    touched.
    """
    if token.startswith("-"):
        return False  # `--rootdir=/x` is a flag, not a target
    candidate = token
    root = str(WORKSPACE)
    if os.path.isabs(candidate):
        normalized = os.path.normpath(candidate)
        if normalized == root or normalized.startswith(root + os.sep):
            candidate = normalized[len(root):]
        else:
            # Outside this workspace entirely; not the suite this guard bounds.
            return True
    segments = [s for s in candidate.split("/") if s not in ("", ".")]
    return len(segments) > 1


def _blocking_wait(command: str) -> bool:
    if _POLL_LOOP_RE.search(command):
        return True

    import shlex  # local, same reason as above

    for segment in _shell_segments(command):
        if not segment.startswith("sleep"):
            continue
        try:
            tokens = shlex.split(segment, comments=True)
        except ValueError:
            continue
        if len(tokens) < 2 or tokens[0] != "sleep":
            continue
        try:
            if float(tokens[1]) >= _BLOCKING_SLEEP_SECONDS:
                return True
        except ValueError:
            continue
    return False


def check_slow_shell(payload: dict) -> dict | None:
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input", {}) or {}
    command = tool_input.get("command", "") or ""
    if not command or SLOW_SHELL_ESCAPE in command:
        return None

    argv = _pytest_argv(command)
    if argv is not None and _is_serial_full_suite(argv):
        return {
            "decision": "block",
            "_policy_deny": True,
            "reason": _SERIAL_SUITE_REASON.format(escape=SLOW_SHELL_ESCAPE),
        }

    # A backgrounded waiter is the prescribed fix, not the defect: it returns at
    # once and holds nothing. Only a foreground wait is worth refusing.
    if not tool_input.get("run_in_background") and _blocking_wait(command):
        return {
            "decision": "block",
            "_policy_deny": True,
            "reason": _WAITER_REASON.format(
                threshold=_BLOCKING_SLEEP_SECONDS, escape=SLOW_SHELL_ESCAPE
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

# `WS_RATE_LIMIT_STATE` redirects the counter, and the test suite is the only
# caller that sets it. Six test modules drive this hook in a subprocess exactly
# as production does, so before this seam existed every fixture write they made
# was counted against the operator's real daily allowance. Measured 2026-08-07:
# the production counter stood at 1033 and was BLOCKING, and the writes it had
# stored were fixtures — a thread file that does not exist, a Windows path that
# cannot exist on this machine, a scratch probe. The suite went red on volume
# nobody produced, and the runaway-loop guard's own numerator was filling with
# work nobody did, which is the worse half: a real runaway would arrive
# indistinguishable from a week of testing.
#
# The same shape as `WORKSPACE_LOG_DIR` one guard along, and for the same reason
# the denial log needed it on 2026-08-01. It weakens nothing that was not already
# open: the block message this check prints already tells the operator to delete
# the state file, so a redirected path is not a new way to reset the count.
RATE_LIMIT_STATE_FILE = Path(
    os.environ.get("WS_RATE_LIMIT_STATE")
    or WORKSPACE / ".claude" / "state" / "dispatch-rate.json"
)
RATE_LIMIT_SOFT = int(os.environ.get("WS_RATE_LIMIT_SOFT", "200"))   # advisory at N writes/day
RATE_LIMIT_HARD = int(os.environ.get("WS_RATE_LIMIT_HARD", "1000"))  # block at N writes/day
RATE_LIMIT_LOOP_WINDOW = 20      # how many recent calls to inspect
RATE_LIMIT_LOOP_THRESHOLD = 6    # same (tool, path) >= N times in window → advisory


def _load_rate_state() -> dict:
    if not RATE_LIMIT_STATE_FILE.exists():
        return {"date": "", "count": 0, "recent": []}
    try:
        return json.loads(RATE_LIMIT_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - a hook must not break the tool call
        # Say it. This reset the daily write cap, the runaway-loop window AND
        # check_tool_budget's rolling history to empty, and printed nothing at
        # all, so the one event these counters exist to catch would arrive
        # looking like a fresh day. `_save_rate_state` has always reported its
        # own failure; this side did not.
        print(f"[_dispatch:rate_limit] state unreadable, counters reset to zero: {e}",
              file=sys.stderr)
        return {"date": "", "count": 0, "recent": []}


def _save_rate_state(state: dict) -> None:
    # Atomic write (tmp + os.replace) per the global atomic-state-write rule: a torn
    # write would silently reset the runaway-loop counter. Added 2026-06-09 audit.
    #
    # The staging name carries the pid. `os.replace` is atomic for ONE writer;
    # the fixed `.json.tmp` was shared, so two hook processes - which this file's
    # own comments say do race - each opened the same staging path with mode "w"
    # and interleaved their writes, and whichever replaced second promoted torn
    # JSON to the live file. Per-process staging makes the atomicity claim above
    # true for the concurrency that actually happens here.
    try:
        RATE_LIMIT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = RATE_LIMIT_STATE_FILE.with_suffix(f".json.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, RATE_LIMIT_STATE_FILE)
    except Exception as e:
        print(f"[_dispatch:rate_limit] state save failed: {e}", file=sys.stderr)


def check_rate_limit(payload: dict) -> dict | None:
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
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    if state.get("date") != today:
        # Reset only the keys THIS check owns. Rebinding `state` to a fresh
        # three-key literal also dropped `tool_history`, which check_tool_budget
        # owns and which is a rolling 30-minute window, not a daily one. The
        # first write after local midnight therefore emptied that window, so a
        # runaway loop straddling midnight restarted its count at 1 and neither
        # the soft nor the hard tool cap could fire on the calls already made.
        state.update({"date": today, "count": 0, "recent": []})

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
# Raised 3 -> 4 on 2026-08-20, in the same change that made the signature stable.
# The detector had never fired, so 3 was never tested against real traffic; a
# legitimate edit-then-recheck cycle produces three identical calls easily. This
# branch is advisory-only and can never block, so the cost of it being slightly
# loose is a missed notice, and the cost of it being tight is noise on every
# ordinary retry. Lower it again only against a measured false-negative.
TOOL_REPEAT_THRESHOLD = 4  # same (tool, args-digest) N in a row → advisory


def _stable_args_signature(tool_name: str, tool_input: dict) -> str:
    """Build a stable signature for tool+args to detect identical repeats.

    sha256, not the builtin `hash`. Every hook invocation is a fresh interpreter
    and PYTHONHASHSEED is randomised per process, so `hash` gave a different
    value for identical input every time and no two stored signatures could ever
    match. Measured 2026-08-20: `.claude/state/dispatch-rate.json` held 344
    tool_history entries and 344 DISTINCT signatures, and the denial log records
    zero firings of this check across its whole history. The function name
    already promised stability; now it holds. The digest is a dedupe key here, never a
    security boundary — and sha256 rather than sha1 because the repo's ruff
    profile runs flake8-bandit, which flags sha1 (S324). A pragma to silence a
    gate is the move this workspace forbids, and the cost of the wider digest
    here is nothing: it is truncated to 16 hex characters either way.
    """
    try:
        # Sort keys so dict ordering doesn't fool the digest
        canonical = json.dumps(tool_input, sort_keys=True, default=str)
    except Exception:
        canonical = str(tool_input)
    digest = hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()[:16]
    return f"{tool_name}:{digest}"


def check_tool_budget(payload: dict) -> dict | None:
    """Total-tool-call cap in 30-min rolling window + same-args repeat detection.

    Counts every tool invocation (not just writes). Soft cap warns; hard cap blocks.
    TOOL_REPEAT_THRESHOLD identical calls in a row (same tool, same args) →
    advisory only, which is 4. The prose said three, from before the threshold
    was raised on 2026-08-20, so the docstring described behaviour the code had
    stopped having. The
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
    check_slow_shell,
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
            # One call site, ahead of both terminal renderings below, so every
            # check's refusal is counted the same way and an eighth check inherits
            # the counter without touching this file. A1 of the v2 design.
            _record_denial(check.__name__, payload, decision.get("reason", ""))
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
