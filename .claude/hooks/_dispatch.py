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

Matcher scope: settings.local.json registers this dispatcher under five
matchers — `Write|Edit|MultiEdit|NotebookEdit`, `Bash`, `Read|Grep|Glob`,
`mcp__codegraph__.*`, and `Agent|Task|Workflow`.
Every payload shape reaches every check, so a new check has to answer what it
does with each of them rather than inherit a three-matcher assumption. The last
two arrived on 2026-08-29 with the walls that need them: a matcher is the only
thing that makes a door reachable, and `check_graph_first` and
`check_fanout_first` were each written with an unlock the dispatcher could not
see. A wall whose unlock is unreachable is a cage. Grep and
Glob joined the third matcher on 2026-08-28: `check_protect_personal_threads`
refused the Bash spellings of a personal-thread read (`grep`, `rg`) while the
native tools were not dispatched here at all, so the guard covered the harder
route and missed the plain one. Read carries a
`file_path` but no content, which is why `check_protect_corporate` and
`check_protect_docs` exclude it by name: `check_protect_docs` reaching a path
test on a Read payload is what policy-denied an ordinary operator Read at
2026-08-11T21:44:19, recorded in the denial log. The path-scoped checks return
None on a Bash payload for the plainer reason that it carries no `file_path` at
all.
"""
from __future__ import annotations
import fnmatch
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

# One lexical path collapse, shared with `data-path-redirect.py`. Measured cost
# of the import: inside the run-to-run noise of this hook (12 runs each way,
# 2026-08-29), so it is at module scope rather than deferred.
sys.path.insert(0, str(WORKSPACE))
from scripts.utils.pathnorm import normalize_path, normalize_segments  # noqa: E402

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
    return any(_under_dir(normalized, seg) for seg in SECRETS_ALLOW_DIR_SEGMENTS)

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

# Same subtree, but matching the DIRECTORY itself as well as a path inside it.
# `PERSONAL_PATH_RE` needs a separator after `personal`, which is right for a
# file path and wrong for a search root: `Grep(path="threads/personal")` is the
# natural spelling and carries no trailing slash. The alternation end-anchors
# instead, so `threads/personal-notes/` — a different directory — still passes.
_PERSONAL_DIR_RE = re.compile(r"threads[/\\]personal(?:[/\\]|$)", re.IGNORECASE)

# The archived copies of the same material, one directory deeper.
# `scripts/thread.py` writes a closed thread to `threads/archive/<year>/<type>/`
# and `VALID_TYPES` in scripts/utils/threads_lib.py includes `personal`, so the
# archive holds CEO-only bodies under a path that `_PERSONAL_DIR_RE` cannot see:
# it needs `personal` immediately after `threads`. Two of the Bash patterns
# below have carried the archive shape since they were written, which left the
# wall self-contradictory on ONE file - `cp threads/archive/2026/personal/x.md`
# was refused while `cat` of it, and `Read` of it, went through.
_PERSONAL_ARCHIVE_RE = re.compile(
    r"threads[/\\]archive[/\\].+[/\\]personal(?:[/\\]|$)", re.IGNORECASE)


# A path-like run inside free text: anything containing a separator, stopping at
# whitespace and the shell metacharacters that end a word. Used to canonicalise
# the paths in a Bash command line and in written content, so a `.`, `//` or
# `..` segment cannot hide the directory from a pattern that spells it plainly.
_PATH_TOKEN_RE = re.compile(r"[^\s'\"<>|;&()]*[/\\][^\s'\"<>|;&()]*")

# Cheap pre-filter. Every spelling of a CEO-only path has to name the threads
# root followed by a separator somewhere, so text without that cannot be made to
# match by canonicalising it, and the substitution pass is skipped. This keeps
# the write-content branch, which sees whole file bodies, at one extra search on
# the overwhelming majority of writes.
_THREADS_HINT_RE = re.compile(r"threads[/\\]", re.IGNORECASE)


def _canonicalise_paths(text: str) -> str:
    """Rewrite every path-like run in free text to its collapsed form.

    Returns `text` unchanged when it cannot name the threads root at all, which
    is the common case and the reason this is affordable on a Write payload.
    """
    if not _THREADS_HINT_RE.search(text):
        return text
    return _PATH_TOKEN_RE.sub(lambda m: normalize_path(m.group(0)), text)


def _names_personal_threads(text: str) -> bool:
    """True when `text` spells a path inside either CEO-only threads subtree.

    Asked of the raw text AND of its canonical form. The two differ exactly when
    the text carries a `.`, `//` or `..` segment, which changes the spelling and
    not the file. Until 2026-08-29 only the raw form was asked, and three
    ordinary spellings of one CEO-only file walked through the wall.
    """
    for haystack in (text, _canonicalise_paths(text)):
        if _PERSONAL_DIR_RE.search(haystack) or _PERSONAL_ARCHIVE_RE.search(haystack):
            return True
    return False


# A glob segment that is not a plain directory name. `*`, `?` and `[abc]` can
# each expand to `personal`, so a segment carrying one is treated as if it might.
_GLOB_META_RE = re.compile(r"[*?\[]")


def _segment_can_be(segment: str, name: str) -> bool:
    """Can ONE glob segment expand to the literal directory `name`?"""
    if segment.lower() == name:
        return True
    if not _GLOB_META_RE.search(segment):
        return False
    return fnmatch.fnmatch(name, segment.lower())


def _tail_reaches_personal(tail: list[str]) -> bool:
    """Can these segments, taken below the threads root, reach a CEO-only one?

    The two shapes are `<threads>/personal/...` and
    `<threads>/archive/<anything>/personal/...`; both are produced by
    `scripts/thread.py`. Anything else under `threads/` is business content and
    must stay searchable, which is why this asks about those two shapes rather
    than refusing every `**`.
    """
    if not tail:
        return True                       # the threads root itself, read whole
    head, rest = tail[0], tail[1:]
    if head == "**":
        return True                       # crosses any depth, so it crosses these
    if _segment_can_be(head, "personal"):
        return True
    if _segment_can_be(head, "archive"):
        if not rest or rest[0] == "**":
            return True                   # the whole archive, or any depth in it
        return not rest[1:] or _segment_can_be(rest[1], "personal")
    return False


def _threads_roots() -> list[Path]:
    """Every threads directory a sweep on this machine could descend into.

    Only directories that EXIST are returned. The engine clone ships no threads
    at all, which is what makes the unanchored-sweep carve-out below safe, and
    an entry for a directory that is not there would take that carve-out away
    for no gain.
    """
    global _THREADS_ROOTS_CACHE
    if _THREADS_ROOTS_CACHE is not None:
        return _THREADS_ROOTS_CACHE
    bases = [WORKSPACE]
    # `get_data_root()` already honours the `HEADING_OS_DATA` pin, so reading
    # the variable here as well was a second answer to a question that has one.
    # Mutation-checked 2026-08-29: removing the env branch changed no verdict.
    try:
        from scripts.utils.workspace import get_data_root
        bases.append(get_data_root())
    except Exception:  # pragma: no cover - resolver unavailable
        # Fail toward the wall, not away from it: guess the sibling layout
        # rather than conclude there is no threads directory anywhere.
        bases.append(WORKSPACE.parent / f"{WORKSPACE.name}-data")
    roots = []
    for base in bases:
        try:
            candidate = Path(base).resolve() / "threads"
        except OSError:  # pragma: no cover - unresolvable base
            continue
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)
    _THREADS_ROOTS_CACHE = roots
    return roots


_THREADS_ROOTS_CACHE: list[Path] | None = None


def _expression_can_reach(expression: list[str], rel: list[str]) -> bool:
    """Can these glob segments walk down `rel`, then into a CEO-only subtree?

    `rel` is the threads root stated relative to the search root. Once it is
    consumed the ordinary tail question applies.
    """
    if not rel:
        return _tail_reaches_personal(expression)
    if not expression:
        return False
    head, rest = expression[0], expression[1:]
    if head == "**":
        return True                       # crosses any depth, so it crosses rel
    if _segment_can_be(head, rel[0].lower()):
        return _expression_can_reach(rest, rel[1:])
    return False


def _sweep_descends_from_above(tool_name: str, fields: dict, cwd: str) -> bool:
    """Can a sweep rooted ABOVE the threads tree descend into the CEO-only part?

    The anchor test below only fires on a LITERAL `threads` segment, so a search
    rooted one directory higher carried no such segment and was allowed. That is
    the plainest way to sweep the private corpus, and the shape this workspace
    instructs, because agent threads reset cwd and absolute paths are the fix.
    Measured 2026-08-29: `Grep(path=<data-root>)`, `Glob("**/*.md",
    path=<data-root>)` and a Grep two levels up were all allowed while
    `Grep(path=<data-root>/threads)` was refused.

    The comment on the unanchored carve-out justified itself with "an unanchored
    sweep stays inside the engine clone, which holds no threads at all". That
    premise is now checked rather than assumed: the question asked here is
    whether the resolved search root is an ancestor of a threads directory that
    actually exists.
    """
    raw_path = fields["path"]
    try:
        root = Path(raw_path).resolve() if raw_path else Path(cwd or ".").resolve()
    except OSError:  # pragma: no cover - unresolvable root
        return False
    for threads_root in _threads_roots():
        try:
            rel = threads_root.relative_to(root).parts
        except ValueError:
            continue                      # not below this search root
        if not rel:
            continue                      # the root IS the threads dir: anchored
        if tool_name == "Grep":
            # ripgrep walks the whole tree under `path`, and applies `--glob` at
            # any depth rather than as a ceiling, so it descends either way.
            return True
        if _expression_can_reach(fields["pattern"].split("/"), list(rel)):
            return True
    return False


def _search_reaches_personal(segments: list[str]) -> bool:
    """Can a search described by these path segments reach the CEO-only threads?

    The wall used to ask "does this argument SPELL the directory", and a
    wildcard never spells it: a Glob that sweeps the whole threads tree with a
    double star walked straight through and put private thread filenames in the
    transcript. Measured 2026-08-29, 7 of 13 verdicts wrong. This asks the
    question that matters instead - can the expression EXPAND to something
    inside the subtree.

    Only a LITERAL `threads` segment anchors the search. A wildcard that could
    itself expand to `threads` is not treated as an anchor, because then
    `Glob("**/*.py")` is refused and that is every ordinary sweep in the engine.

    That limit is deliberate, and what makes it safe is `_sweep_descends_from_above`
    one screen up - NOT the sentence that used to stand here, which read: "only a
    `threads`-prefixed path is re-anchored at the data root by
    data-path-redirect.py, so an unanchored sweep stays inside the engine clone,
    which holds no threads at all." That premise was measured FALSE and retired.
    A Glob with no `path` resolves against `cwd`, the redirect says nothing about
    where `cwd` points, and with `cwd` at the data root the sweep descends
    straight into the threads tree.

    The sentence stayed here after the check that replaced it landed, which is
    the dangerous half of a stale comment: it told the next reader the carve-out
    was self-justifying, so the sibling rule carrying the actual load reads as
    redundant and invites deletion. `_sweep_descends_from_above` resolves the
    real search root and refuses when that root sits above a threads tree that
    exists. Do not remove it.
    """
    # `..` was left in place here while `""` and `.` were dropped, so
    # `Grep(path="threads/business/../personal")` found no `personal` segment
    # directly after `threads` and was allowed. The shared collapse pops it.
    parts = normalize_segments("/".join(segments))
    parts = [s for s in parts if s]
    for index, segment in enumerate(parts):
        if segment.lower() == "threads":
            return _tail_reaches_personal(parts[index + 1:])
    return False

# Order of patterns is irrelevant for correctness (any match blocks).
# The list follows the original protect-personal-threads.py order to
# preserve git blame lineage. Adding new patterns: append to the end
# or group with related shell-builtin / language-specific variants.
#
# One spelling of "either CEO-only threads directory", shared by every pattern
# below. Until 2026-08-29 the archive shape was pasted onto exactly two of them
# (`cp` and `git add`), so `cp threads/archive/2026/personal/x.md` was refused
# while `cat` of the same file was allowed - the wall gave two answers about one
# file. A fragment cannot drift the way a hand-copied clause did.
_BASH_CEO_THREADS = r"threads[/\\](?:personal|archive[/\\][^\s'\"]+[/\\]personal)"

DANGEROUS_BASH_PATTERNS = [
    re.compile(rf"\b(cp|mv|rsync|scp|xcopy|robocopy)\b.*{_BASH_CEO_THREADS}", re.IGNORECASE),
    re.compile(rf"\b(tar|zip|7z|gzip)\b.*{_BASH_CEO_THREADS}", re.IGNORECASE),
    re.compile(rf"\bcat\b.*{_BASH_CEO_THREADS}.*>", re.IGNORECASE),
    re.compile(rf"\bgit\s+(add|stash\s+push)\b.*{_BASH_CEO_THREADS}", re.IGNORECASE),
    re.compile(rf"<\s*{_BASH_CEO_THREADS}", re.IGNORECASE),
    re.compile(rf"\btee\b.*{_BASH_CEO_THREADS}", re.IGNORECASE),
    re.compile(rf"\bcat\b.*{_BASH_CEO_THREADS}.*\|\s*tee", re.IGNORECASE),
    re.compile(rf"\bdd\b.*\bif={_BASH_CEO_THREADS}", re.IGNORECASE),
    re.compile(rf"\bcd\b.*{_BASH_CEO_THREADS}", re.IGNORECASE),
    re.compile(rf"\b(Copy-Item|Move-Item|Get-Content)\b.*{_BASH_CEO_THREADS}", re.IGNORECASE),
    re.compile(rf"\bshutil\.(copy|copy2|move|copytree)\b.*{_BASH_CEO_THREADS}", re.IGNORECASE),
    re.compile(rf"\bopen\s*\(\s*['\"]{_BASH_CEO_THREADS}", re.IGNORECASE),
    # Read-then-emit exfil: a plain read utility pointed at the personal subtree
    # dumps CEO-only content into the transcript (a leak by itself, no redirect
    # needed). Added 2026-06-09 audit (hooks finding 2 — guard was narrower than
    # secure-projects.md/security.md claim of technical enforcement).
    #
    # THIS IS A DENY-LIST, and a deny-list of utility names can never be
    # complete. An unlisted reader passes: `busybox cat`, a shell function, a
    # compiled helper, a name invented after this line was written. Read the
    # alternation as "the common ones are refused", never as "reading is
    # impossible". The structural alternative is a default-deny on any Bash
    # command naming that directory, and it is the operator's decision, not this
    # file's.
    #
    # `cat` was missing from 2026-06-09 to 2026-08-27. The other two `cat`
    # patterns above both require a redirect or a pipe to tee, so `head` on a
    # personal thread was refused while the plainest read of the same file was
    # allowed. Added with its neighbours: tac, rev, sort, uniq, shuf, paste, pr,
    # fmt, expand, unexpand, column, tr, hexdump.
    re.compile(
        r"\b(cat|tac|head|tail|sed|awk|base64|b64encode|xxd|hexdump|od|strings|"
        r"nl|fold|cut|less|more|grep|rg|rev|sort|uniq|shuf|paste|pr|fmt|expand|"
        rf"unexpand|column|tr)\b.*{_BASH_CEO_THREADS}",
        re.IGNORECASE),
    re.compile(rf"\bopen\s*\(\s*['\"][^'\"]*{_BASH_CEO_THREADS}", re.IGNORECASE),
]

# Directories whose files legitimately QUOTE a CEO-only path: the specs and
# plans that designed the wall, the scrutiny reports that audit it, the skills
# and rules that name it, and the tests that drive it.
#
# Anchored at the workspace root, not at any `/`. A `(?:^|/)` regex matched the
# segment ANYWHERE, so a decoy directory exempted a write: MEASURED 2026-08-31,
# `outputs/scratch/reference/leak.md`, `knowledge/templates/leak.md` and
# `outputs/scratch/tests/leak.md` were all ALLOWED to carry a `threads/personal/`
# reference, while the control `outputs/reports/leak.md` was blocked. Creating a
# directory called `reference` is not a privilege.
#
# Same hole shape, and the same fix, as `SECRETS_ALLOW_DIR_SEGMENTS` on
# 2026-07-31. That one was measured and anchored; this allowlist, twelve lines
# away, was left matching a segment anywhere.
ALLOWED_DOC_DIRS = (
    "docs/superpowers/plans/",
    "docs/superpowers/specs/",
    "outputs/operations/scrutiny/",  # leak-guard: ok (relative prefix/match key)
    ".claude/skills/",
    ".claude/rules/",
    ".claude/hooks/",
    "reference/",
    "templates/",
    "tests/",
)


_DOC_BASES_CACHE: list[str] | None = None


def _doc_bases() -> list[str]:
    """The roots under which one of `ALLOWED_DOC_DIRS` is a real directory.

    BOTH repositories, because the two-part topology puts `reference/` and
    `templates/` in the private overlay and `tests/` and `.claude/` in the
    engine clone. Anchoring to the engine root alone would have refused a
    legitimate `<data-root>/reference/x.md`, which is the over-tightening a
    narrower fix would have shipped. Same base set, and the same reasoning, as
    `_threads_roots` above.
    """
    global _DOC_BASES_CACHE
    if _DOC_BASES_CACHE is not None:
        return _DOC_BASES_CACHE
    bases = [WORKSPACE.as_posix()]
    try:
        from scripts.utils.workspace import get_data_root
        bases.append(Path(get_data_root()).as_posix())
    except Exception as exc:  # noqa: BLE001 - reported, never raised, in a hook
        # Fail toward the WALL: an unresolvable overlay means one fewer exempt
        # root, so a doc write there is refused rather than silently allowed.
        print(f"[_dispatch:doc_paths] overlay root unresolvable, so only the "
              f"engine tree exempts a doc path: {exc}", file=sys.stderr)
    _DOC_BASES_CACHE = [b.rstrip("/") for b in bases if b]
    return _DOC_BASES_CACHE


def _is_allowed_doc_path(normalized: str) -> bool:
    """True for one of `ALLOWED_DOC_DIRS` inside a root this machine really has.

    Relative spellings are exempt as before. An ABSOLUTE spelling is exempt only
    under one of `_doc_bases()`, so a decoy `reference/` anywhere else is not a
    privilege, and an absolute path into some OTHER workspace entirely is not
    this wall's business to exempt.
    """
    for seg in ALLOWED_DOC_DIRS:
        if normalized.startswith(seg):
            return True
        if any(normalized.startswith(f"{base}/{seg}") for base in _doc_bases()):
            return True
    return False

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
        # Raw first, then with every path-like run collapsed. The patterns pair
        # a verb with a literal path shape, and a `.`, `//` or `..` segment
        # broke the path half while the verb half still matched: `cat` of a
        # CEO-only file was refused and `cat` of `<threads>/./<personal>/x.md`
        # was allowed. Canonicalising the command, rather than widening ten
        # regexes, keeps one answer for one file.
        canonical = _canonicalise_paths(command)
        haystacks = (command,) if canonical == command else (command, canonical)
        for pattern in DANGEROUS_BASH_PATTERNS:
            if any(pattern.search(h) for h in haystacks):
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
        # Collapsed, not just backslash-folded. `re.sub(r"\\+", "/")` answered
        # about the spelling: three ordinary spellings of one CEO-only file
        # opened it while this branch said nothing. Measured 2026-08-29.
        target = normalize_path(tool_input.get("file_path") or "")
        if PERSONAL_PATH_RE.search(target) or _PERSONAL_ARCHIVE_RE.search(target):
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

    if tool_name in ("Grep", "Glob"):
        # Grep returns MATCHING LINES, which is reading. Its Bash twin was
        # refused throughout (`grep` and `rg` are both in DANGEROUS_BASH_PATTERNS
        # above), so the native tool was the one spelling of the same read that
        # went through. Verified 2026-08-28: this function returned None for both
        # tools, AND `_dispatch.py` was not even registered for them —
        # `data-path-redirect.py` was, on the same two tools, which is how the
        # gap stayed invisible. Glob returns only paths, but a personal thread's
        # FILENAME is CEO-only too, and the pair is cheaper to reason about than
        # a carve-out.
        #
        # Every field that can point the tool at the subtree is checked: `path`
        # (both tools), `pattern` (a Glob pattern is a path, and a Grep pattern
        # can carry one), and `glob` (Grep's file filter).
        # Folded, NOT collapsed. The collapse has to happen after the fields are
        # joined, and doing it here as well is worse than redundant: with
        # `path="<threads>/business"` and `pattern="../personal/*.md"`, a
        # per-field collapse drops the `..` against nothing and composes
        # `<threads>/business/personal/*.md`, which reaches nowhere and is
        # allowed. Joined first, the same two fields collapse onto the CEO-only
        # directory. Found by mutation, 2026-08-29: reverting this line changed
        # no verdict, which is what a redundant guard looks like.
        fields = {key: re.sub(r"\\+", "/", tool_input.get(key) or "")
                  for key in ("path", "pattern", "glob")}
        for key in ("path", "pattern", "glob"):
            if _names_personal_threads(fields[key]):
                return {
                    "decision": "block",
                    "reason": (
                        f"Personal-threads protection — intentional policy block, "
                        f"not an error. {tool_name} targets threads/personal/ via "  # leak-guard: ok (string in a message/log, not a path)
                        f"its {key!r} argument: CEO-only content must not enter "
                        f"the transcript."
                    ),
                    "_policy_deny": True,
                }
        # The fields COMPOSE, and a wildcard names the subtree in none of them.
        # Testing each argument on its own let three ordinary spellings through:
        # `Glob("threads/**/*.md")`, `Grep(path="threads")` and
        # `Grep(path="threads", glob="personal/*.md")` all reached CEO-only
        # files while every field passed its own test. Build the expression the
        # tool will actually expand, then ask where it can land.
        if tool_name == "Glob":
            # A Glob pattern is relative to `path` when both are given.
            expression = fields["path"].split("/") + fields["pattern"].split("/")
        else:
            # ripgrep applies `--glob` at ANY depth below `path`, not only at the
            # top of it, which is what the `**` stands for here. With no filter,
            # the search is the whole tree under `path`.
            expression = fields["path"].split("/")
            if fields["glob"]:
                expression = expression + ["**"] + fields["glob"].split("/")
        if (_search_reaches_personal(expression)
                or _sweep_descends_from_above(tool_name, fields,
                                              payload.get("cwd") or "")):
            return {
                "decision": "block",
                "reason": (
                    f"Personal-threads protection — intentional policy block, "
                    f"not an error. {tool_name} can expand into the CEO-only "
                    f"threads subtree, so its result set is not safe for the "
                    f"transcript. Name the subtree you want, for example "
                    f"threads/business/, instead of searching the whole tree."  # leak-guard: ok (string in a message/log, not a path)
                ),
                "_policy_deny": True,
            }
        return None

    if tool_name not in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return None

    target = normalize_path(
        tool_input.get("file_path") or tool_input.get("notebook_path") or "")
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

    if PERSONAL_PATH_RE.search(target) or _PERSONAL_ARCHIVE_RE.search(target):
        return None
    if _is_allowed_doc_path(target):
        return None
    for c in contents:
        if _names_personal_threads(c):
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
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # `UnicodeDecodeError` is a `ValueError`, and a SIBLING of
        # `json.JSONDecodeError` rather than a subclass, because the decode
        # fails inside `read_text` before `json.loads` is ever called. So the
        # handler below caught the corrupt-JSON case the comment underneath
        # describes and walked straight past the corrupt-BYTES case.
        #
        # MEASURED 2026-09-01, one identity file in three states, same Write
        # into `corporate/`:
        #
        #     valid, role exec  -> allowed        (correct, the normal path)
        #     not JSON          -> BLOCKED        (correct, fails shut)
        #     not UTF-8         -> ALLOWED        (the defect, fails OPEN)
        #
        # The third printed the decode error to stderr and let the write
        # through, which is the exact outcome the paragraph below says was
        # fixed: one corrupt byte switched the corporate wall off for the whole
        # session and the executive's edit was silently overwritten on the next
        # sync. Only half of "corrupt" was covered. Fixing a handler means
        # asking which OTHER inputs reach it, not only the one that prompted
        # the fix.
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
    # `notebook_path` as well, because NotebookEdit carries the target there and
    # nowhere else. MEASURED 2026-08-31 with an exec-workspace identity file:
    # `Write` and `Edit` into `corporate/` both BLOCKED, `NotebookEdit` was
    # ALLOWED. The module docstring claims "every payload shape reaches every
    # check", and the two siblings `check_prevent_secrets` and
    # `check_protect_personal_threads` both already read this field.
    file_path = (tool_input.get("file_path", "")
                 or tool_input.get("notebook_path", "") or "")
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
# `.claude/hooks/sync-docs.py` — keep in sync if either side changes. The path
# read `scripts/sync-docs.py` until 2026-08-31 and no such file exists; the two
# sets do currently match, so this was a stale pointer, not a drift.

_DOCS_DIR_RE = re.compile(r"(?:^|/)docs/")

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
    # `notebook_path` too. Same gap, same date and same reason as the one in
    # `check_protect_corporate` above: NotebookEdit carries its target in this
    # field and nowhere else, so reading only `file_path` left the tool outside
    # a wall the module docstring says every payload shape reaches.
    file_path = (tool_input.get("file_path", "")
                 or tool_input.get("notebook_path", "") or "")
    if not file_path:
        return None

    norm_path = file_path.replace("\\", "/")
    # `(?:^|/)docs/`, not the substring `"/docs/"`. The substring needs a
    # separator BEFORE `docs`, so the plainest spelling of the very path this
    # guard exists for — a repo-relative `docs/GETTING-STARTED.md` — went
    # through, while `./docs/GETTING-STARTED.md` was blocked. Verified
    # 2026-08-28. The anchor also keeps `my-docs/GETTING-STARTED.md` out, which
    # the substring already did.
    if not _DOCS_DIR_RE.search(norm_path):
        return None

    # basename of the NORMALISED path. `os.path.basename` splits on `\` only on
    # Windows, so on the Linux/WSL host this hook runs on, a Windows-spelled
    # `docs\GETTING-STARTED.md` came back whole and matched nothing in
    # SYNCED_FILES — while the directory test two lines up had already accepted
    # it, because that one normalises. One guard, two spellings of the path, and
    # the write went through. This repo ships settings for Windows hosts
    # (.claude/settings.local.windows.json), so the spelling is not exotic.
    file_name = norm_path.rsplit("/", 1)[-1]
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

def _cds_to_workspace_root(command: str, norm_root: str) -> bool:
    """True when a segment of `command` changes directory to the workspace root.

    The second self-anchoring spelling, and the one an operator actually types.
    Only the literal `git rev-parse --show-toplevel` was recognised, so
    `cd <root> && .venv/bin/python scripts/run-tests.py` was refused from a
    drifted shell, with the message saying the command "would fail with ENOENT".
    It would not: the `cd` fixes the cwd before the script is reached. MEASURED
    2026-08-31 from `<root>/tests`, that exact command BLOCKED.

    Same false-cause claim as the 2026-08-25 absolute-path defect recorded above,
    reached by the other door. Exempting is the right direction for a friction
    guard: a wrong refusal is the documented harm, and it is what teaches the
    operator to reach for the escape.
    """
    for segment in _SEGMENT_SPLIT_RE.split(command):
        words = segment.split()
        if len(words) < 2 or words[0] != "cd":
            continue
        target = words[1].strip("\"'")
        if not target:
            continue
        try:
            if os.path.realpath(target) == norm_root:
                return True
        except (OSError, ValueError):
            continue
    return False


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
    # The second self-anchoring spelling. See `_cds_to_workspace_root`.
    if _cds_to_workspace_root(command, norm_root):
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
#
# Run this over `_unquoted_skeleton(command)`, never over the raw text. A shell
# keyword is only a keyword outside quotes and outside a comment, and this
# pattern reaches across the whole string by design (a real loop puts `while` and
# `sleep` in different segments, so `_shell_segments` cannot be used here). Over
# raw text that reach turned `echo "while you wait"; sleep 1` into a policy deny
# whose own message promises that short sleeps pass. Reproduced 2026-08-28.
_POLL_LOOP_RE = re.compile(r"\b(?:while|until)\b[\s\S]*?\bsleep\b")


def _unquoted_skeleton(command: str) -> str:
    """`command` with quoted spans and comments blanked out, structure preserved.

    Quote contents become empty, so a shell keyword written inside a string
    cannot be read as one; separators, redirections and unquoted words are kept
    exactly where they were, so a pattern that reaches across segments still
    does. Trailing `#` comments are dropped for the same reason: `sleep 1 # a
    while later` is not a poll loop.

    A HERE-DOCUMENT body is blanked for the same reason. It is data being fed to
    a program's stdin, not shell syntax, and this hook's own commit message was
    refused by it on 2026-08-28: the message describes the poll-loop defect, so
    it contains the words `while` and `sleep` in ordinary prose. Both spellings
    (`<<WORD` and `<<-WORD`, delimiter quoted or bare) end at a line holding the
    delimiter alone. An UNTERMINATED heredoc consumes the rest of the string,
    which matches what the shell would do with it.

    Deliberately shares the quote-walk shape of `_shell_segments` above rather
    than calling shlex: shlex drops the quotes AND keeps the words, which is the
    opposite of what this needs.
    """
    out = []
    quote = None
    pending_heredocs: list = []
    index = 0
    while index < len(command):
        char = command[index]
        if quote:
            if char == "\\" and quote == '"' and index + 1 < len(command):
                index += 2
                continue
            if char == quote:
                quote = None
                out.append(char)
            index += 1
            continue
        if char in "'\"":
            quote = char
            out.append(char)
            index += 1
            continue
        if char == "\\" and index + 1 < len(command):
            # An escaped character is data, never a keyword letter.
            out.append(" ")
            out.append(" " if command[index + 1] != "\n" else "\n")
            index += 2
            continue
        if char == "#" and (not out or out[-1].isspace()):
            # Comment to end of line. A newline is a shell separator, so keep it.
            while index < len(command) and command[index] != "\n":
                index += 1
            continue
        heredoc = _HEREDOC_START_RE.match(command, index)
        if heredoc:
            # The redirection itself stays; only the BODY is data. The body does
            # not start here, it starts after this line ends, so the delimiter is
            # queued and the walk carries on with the rest of the line.
            out.append("<<")
            pending_heredocs.append(heredoc)
            index = heredoc.end()
            continue
        if char == "\n" and pending_heredocs:
            out.append("\n")
            index += 1
            for opened in pending_heredocs:
                index = _skip_heredoc_body(command, index, opened)
            pending_heredocs = []
            continue
        out.append(char)
        index += 1
    return "".join(out)


# `<<` or `<<-`, optional space, then the delimiter word: bare, 'single' or
# "double" quoted. `<<<` is a here-STRING, one word on the same line, and is not
# matched here: the negative lookahead keeps it out so it falls through to the
# ordinary character walk.
# `(?<!<)` as well as `(?!<)`, and the lookBEHIND is the half that works.
#
# A here-STRING is `<<<word`, and the trailing `(?!<)` only stops the match that
# starts at its FIRST `<`. `re` then retries one character along, where `<<` is
# the second and third angle brackets, the lookahead sees `w`, and `word` is
# read as a heredoc delimiter. `_skip_heredoc_body` looks for a line equal to
# `word`, never finds one, and returns len(command) - so everything after the
# here-string is dropped from the skeleton.
#
# MEASURED 2026-09-01 against the shipped pattern:
#
#     grep x <<<needle\nwhile true; do sleep 1; done
#         skeleton  'grep x <<<\n'      _blocking_wait  False
#
# The poll loop the guard exists to refuse is invisible to it. That is the
# failure `test_a_real_loop_after_a_here_document_is_still_refused` names in its
# own docstring - "a heredoc reader that never recognises its terminator
# swallows the rest of the command" - reached through the one opener nobody
# tested. Every here-string case in that file is QUOTED, and the quote scanner
# empties those before this pattern is consulted, so the bare form went unseen.
_HEREDOC_START_RE = re.compile(
    r"(?<!<)<<(?!<)(?P<dash>-?)\s*(?P<quote>['\"]?)(?P<word>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)")


def _skip_heredoc_body(command: str, position: int, match) -> int:
    """Index just past the body `match` opened, body and terminator removed.

    `position` is the first character of the body (the walk has already consumed
    the newline that ends the opening line). Returns len(command) for an
    unterminated heredoc, which is what the shell does with it too. `<<-` strips
    leading TABS from the terminator line, per POSIX; the plain form requires the
    delimiter alone on its line.
    """
    delimiter = match.group("word")
    while position <= len(command):
        line_end = command.find("\n", position)
        line = command[position:line_end if line_end != -1 else len(command)]
        candidate = line.lstrip("\t") if match.group("dash") else line
        if candidate.rstrip("\r") == delimiter:
            return len(command) if line_end == -1 else line_end + 1
        if line_end == -1:
            return len(command)
        position = line_end + 1
    return len(command)

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


# Environment runners that execute their next non-flag word as a program:
# `uv run pytest ...` runs pytest exactly as a bare `pytest` would. `uv` is this
# repo's canonical toolchain, so its spelling of a serial suite run has to land
# inside the guard. Deliberately a short, named list: a runner is recognised by
# name, never guessed, so an ordinary program whose second word happens to be
# `run` cannot be mistaken for one.
_ENV_RUNNERS = ("uv", "uvx", "poetry", "pdm", "hatch", "rye", "pipenv")


def _is_runner_invocation(tokens: list, index: int) -> bool:
    """True when `tokens[index]` is the program a `<runner> run ...` executes.

    Flags between `run` and the program are allowed (`uv run --frozen pytest`),
    because they configure the runner, not the run. Anything that is not a flag
    ends the search: in `uv run python -m pytest`, the program is `python`, and
    the `-m` clause in the caller already recognises that shape.
    """
    if index < 2 or tokens[0] not in _ENV_RUNNERS or tokens[1] != "run":
        return False
    return all(word.startswith("-") for word in tokens[2:index])


def _pytest_argv(command: str) -> list | None:
    """The argv of a pytest invocation in `command`, or None if there is none.

    Judged positionally: `pytest` must be the first word of a shell segment,
    follow `-m` on an interpreter, or follow a named environment runner
    (`uv run pytest`). A `pytest` that is merely an argument to some other
    program — a grep pattern, a path being echoed — is not an invocation and
    must not be treated as one.

    The runner form was missing until 2026-08-28, and `uv` is this repo's own
    canonical toolchain (CLAUDE.md § Setup). `uv run python -m pytest tests/` was
    caught by the `-m` clause while `uv run pytest tests/` — the shorter spelling
    of the same serial full-suite run — went through untouched. A guard the
    prescribed tool walks around stops being a guard.
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
            if _is_runner_invocation(tokens, index):
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


def _pytest_marker_expression(argv: list) -> str | None:
    """The value of pytest's OWN `-m`, or None.

    Located after the pytest token, never by scanning the whole argv.
    `python -m pytest` puts an unrelated `-m` in the same list, so reading the
    first one would have made every `python -m pytest tests/` look narrow and
    deleted the Bash half of this guard.
    """
    start = next((i for i, w in enumerate(argv)
                  if w == "pytest" or w.endswith("/pytest")), None)
    if start is None:
        return None
    rest = argv[start + 1:]
    for i, word in enumerate(rest):
        if word == "-m":
            return rest[i + 1] if i + 1 < len(rest) else None
        if word.startswith("-m") and len(word) > 2 and not word.startswith("--"):
            return word[2:]
    return None


def _marker_selects_a_subset(expr: str) -> bool:
    """True when `-m <expr>` PICKS a subset, not when it merely deselects.

    `-k` was exempt here and `-m` was not, so `.venv/bin/python -m pytest -m
    acceptance` was policy-denied. That shape is not exotic: this repo defines
    the markers itself (`pyproject.toml`, `slow` and `acceptance`) and
    `scripts/run-tests.py` runs `-m acceptance` as one of its own lanes. MEASURED
    2026-08-31, before the fix: `-m acceptance` and `-m "not slow"` both BLOCKED
    while `-k test_foo` passed. A guard that refuses the exact shape its own
    refusal text exempts is the failure mode `_is_subdirectory_target` above was
    written about, and it teaches the operator to reach for the escape comment.

    The rule is per NAME, because the two directions are genuinely different.
    `-m acceptance` runs a couple of tests. `-m "not slow"` is the whole suite
    with a handful removed, so it stays blocked: a marker reached through `not`
    only deselects, and deselecting from six thousand tests is still six
    thousand tests. `-m "not slow and acceptance"` selects, so it passes.
    """
    tokens = expr.replace("(", " ").replace(")", " ").split()
    negate = False
    for tok in tokens:
        if tok == "not":
            negate = not negate
            continue
        if tok in ("and", "or"):
            negate = False
            continue
        if not negate:
            return True
        negate = False
    return False


def _is_serial_full_suite(argv: list) -> bool:
    marker = _pytest_marker_expression(argv)
    if marker and _marker_selects_a_subset(marker):
        return False
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
    if _POLL_LOOP_RE.search(_unquoted_skeleton(command)):
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
# State file is best-effort, and the honest size of "best-effort" is bigger than
# this comment used to claim. It read "may race and miscount by a few", which is
# the arithmetic of two callers. `actor_id` below records the real rate: 36 hook
# calls in 25 seconds across six actors. An unlocked load-modify-save at that
# rate loses whole updates, not a few, and per-pid staging fixes tearing rather
# than lost updates. So the counter UNDER-counts hardest exactly when fan-out is
# heaviest, which is the runaway case the cap exists to catch.
#
# Not a new defect and not silently narrowed: this is the claim corrected to what
# the method establishes, per `.claude/rules/scope-claims.md`. The cap still
# catches a sustained runaway, because a loop that trips it does so over minutes
# rather than in one 25-second burst. A lock here is the fix if the cap is ever
# relied on for an exact number, and it is not today.

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
        data = json.loads(RATE_LIMIT_STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            # Valid JSON that is not an object reached `.get` in BOTH counter
            # checks and raised AttributeError, which `main()` then caught and
            # failed open on. MEASURED 2026-08-31 with the state file holding
            # `[]`: `check_rate_limit` and `check_tool_budget` both raised. The
            # sibling `check_protect_corporate` already learned this exact
            # lesson for the identity file; this is the second copy of the read.
            print(f"[_dispatch:rate_limit] state was a "
                  f"{type(data).__name__}, not an object; counters reset to zero",
                  file=sys.stderr)
            return {"date": "", "count": 0, "recent": []}
        return data
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
    #
    # The staging file is REMOVED when the write does not complete. Until
    # 2026-09-01 the handler below only printed, so a failed write left the
    # staging file behind for good. MEASURED that day: the filesystem filled to
    # 100% while eight hook processes were saving, and `.claude/state/` was left
    # holding eight zero-byte `dispatch-rate.json.<pid>.tmp` files, all stamped
    # 12:05. Nothing ever removed them, and nothing would have: the pid is in
    # the name, so no later run reuses that path on purpose. This directory is
    # read on every Write and Edit, so the litter is unbounded growth in a hot
    # path.
    #
    # `finally` rather than an `except` arm, because a KeyboardInterrupt between
    # the create and the replace leaves exactly the same orphan and does not
    # derive from `Exception`.
    tmp = RATE_LIMIT_STATE_FILE.with_suffix(f".json.{os.getpid()}.tmp")
    try:
        RATE_LIMIT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, RATE_LIMIT_STATE_FILE)
    except Exception as e:
        print(f"[_dispatch:rate_limit] state save failed: {e}", file=sys.stderr)
    finally:
        # A successful `os.replace` consumed the staging path, so this is a
        # no-op on the happy path. `missing_ok` rather than a check, because a
        # check would race with the replace it is meant to follow.
        try:
            tmp.unlink(missing_ok=True)
        except OSError as e:
            print(f"[_dispatch:rate_limit] could not remove {tmp.name}: {e}",
                  file=sys.stderr)


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
                f"this volume. Override: `export WS_RATE_LIMIT_HARD={RATE_LIMIT_HARD * 2}` "
                f"if intentional, or delete .claude/state/dispatch-rate.json to reset "
                f"the counter."
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
# Raised 1200 -> 4000 on 2026-08-27, on the operator's instruction, after the cap
# blocked a legitimate run. Workflows are the standing working method here, and a
# workflow's subagent tool calls all count against this one window: a 66-agent
# audit workflow made 1849 calls in 29 minutes and tripped a cap set when the main
# loop was the only caller. The cap exists to catch a runaway loop, and a runaway
# loop reaches 4000 in a window just as surely as it reaches 1200 - it just no
# longer catches deliberate fan-out on the way. Cost of the higher number: the
# rolling history is bounded at cap+100, so the state file this hook rewrites on
# every tool call grows from about 48 KB to about 152 KB at 37 bytes an entry.
TOOL_BUDGET_HARD = int(os.environ.get("WS_TOOL_BUDGET_HARD", "4000"))  # block at N
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

    Counts every tool invocation THIS DISPATCHER SEES, which is the matcher set
    named in the module docstring and nothing else. Do not re-list it here: this
    sentence spelled out "writes, Bash, Read, Grep and Glob" and went stale on
    2026-08-29, when the fourth and fifth matchers arrived and only the module
    docstring was updated. A count claimed against the wrong set reads as wider
    coverage than exists, which is the shape `.claude/rules/scope-claims.md`
    exists to stop — and the first version of this sentence said "counts every
    tool invocation" flat, which was wider still.

    It is NOT every tool call the session makes: WebFetch, Task, the MCP tools
    and the rest never reach this hook, so a runaway loop built out of those is
    invisible here.

    Soft cap warns; hard cap blocks.
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
                f"`export WS_TOOL_BUDGET_HARD={TOOL_BUDGET_HARD * 2}` if intentional, "
                f"or delete .claude/state/dispatch-rate.json to reset."
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
# check_graph_first — a code lookup asks the graph before it greps
# ============================================================
#
# The operator's standing instruction, given four times: 2026-08-27 twice (the
# second in capitals), then 2026-08-29 twice, the last one asking for a
# MECHANISM rather than another note. "сам реши как записать, чтобы не позволять
# самому себе нарушать правила."
#
# A written rule had already failed three times. There is also an always-on
# UserPromptSubmit hook that names matching indexed symbols on every code-shaped
# prompt, and the relapses happened anyway, with that text on screen. A reminder
# that is ignored while visible is not a weaker control than this one, it is a
# different kind of thing: advice. This is a wall.
#
# What it refuses: the FIRST code-shaped search of a session, when no
# `codegraph_explore` has been attempted yet. One explore unlocks the session.
# The bar is deliberately low, because the failure being fixed is skipping the
# graph ENTIRELY on a question, not under-using it later in a long session.
#
# What it never refuses, by construction:
#   - a repo with no `.codegraph/` index, matching the global instruction to
#     skip CodeGraph entirely where it is not indexed;
#   - a search over `.tmp/`, `/tmp`, logs, markdown or JSON, which is reading
#     output rather than locating code;
#   - anything at all once an explore has been ATTEMPTED. A failed or empty
#     explore still unlocks, because the rule is "ask the graph first", not
#     "the graph must answer". A graph that is down must not wedge the session.

_GRAPH_STATE_DIR = WORKSPACE / ".claude" / "state" / "graph-first"

# Bash utilities that reach source code. Two groups, one list.
#
# SEARCHING: grep and its kin, plus `find ... -name` over source. `find` was
# named in this comment and absent from the tuple until 2026-08-29, so the
# comment described a rule the code did not have.
#
# READING: sed, awk, cat, head, tail. These are not searches, and leaving them
# out was not an oversight in reasoning but a measured bypass: earlier in this
# same session, with the wall armed, `sed -n '1,40p' tests/<file>.py` opened a
# source file and the wall never saw it. A rule that stops `grep scripts/` and
# allows `cat scripts/x.py` is not a rule. Log and scratch uses of all five are
# filtered by `_NOT_CODE_HINTS` below, which is why they can be listed here at
# all.
_SEARCH_BINARIES = ("grep", "rg", "egrep", "fgrep", "ag", "ack", "ast-grep", "sg",
                    "find", "sed", "awk", "cat", "head", "tail")

# The shell form of the graph, documented in the global instructions as the one
# that "always works". A Bash call carrying it unlocks the session exactly as
# the MCP tool does, and that redundancy is what lets this wall be absolute:
# the MCP matcher lives in gitignored machine-local settings and can be absent,
# but the `Bash` matcher is present in every settings file in the repository.
# Two independent unlock doors mean a missing matcher can never cage a session,
# so the rule needs no escape hatch. See `check_graph_first`.
#
# Resolved in PROGRAM POSITION, not matched anywhere in the command line. A
# whitespace-delimited regex over the whole command made the word `codegraph`
# the key to its own wall: MEASURED 2026-08-31, `grep -rn codegraph scripts/`
# was allowed AND stamped the marker, so the session's first code grep both
# passed and disarmed the wall permanently. So did
# `echo codegraph ; grep -rn foo scripts/`. Heredoc bodies are blanked first,
# because a body is data the command writes, never a program it runs.
def _bash_asked_the_graph(command: str) -> bool:
    """True when a segment of `command` actually RUNS the graph CLI."""
    return any("codegraph" in _program_candidates(seg) for seg in _SEGMENT_SPLIT_RE.split(strip_heredocs(command)))

# Reading output, not locating code. Any of these in the target and the search
# is none of this check's business.
# `tmp/` covers `.tmp/`, `/tmp/` and any scratch directory under another name,
# in one token. Spelling the system path out drew a hardcoded-temp-path finding
# from both linters, and two suppression comments to say "this is a substring I
# match, not a path I open" is worse than one token that needs neither.
_NOT_CODE_HINTS = ("tmp/", ".log", ".json", ".jsonl", ".md",
                   ".txt", ".html", ".csv", "node_modules", ".venv",
                   "__pycache__", ".git/")

# The trees whose contents are the code this rule is about. Matched as whole
# path segments: `path="scripts"` carries no trailing slash and a substring test
# for "scripts/" missed it, which the parametrised case caught on the first run.
_CODE_TREE_RE = re.compile(r"(^|[\s/\\'\"])(scripts|tests)([\s/\\'\"]|$)")
_CODE_HINTS = (".py", ".claude/hooks", ".claude\\hooks")


def actor_id(payload: dict) -> str:
    """Who is making this call: the main session, or one dispatched agent.

    MEASURED on live payloads, 2026-08-29. A subagent's PreToolUse carries the
    SAME `session_id` AND the same `transcript_path` as the session that
    dispatched it. The only field separating them is `agent_id`, which the main
    session does not carry at all. Neither wall knew that. Both keyed their
    per-session state on `session_id` alone, so five dispatched agents and the
    session that dispatched them shared one budget and one stamp: 36 hook calls
    arrived in 25 seconds and 2 of them were the session's own.

    One shared key, two opposite failures:

    - the graph wall was UNLOCKED by an agent. A subagent calling
      `codegraph_explore` stamped the dispatching session's marker, so the
      session never had to ask the graph itself. The hole closed on the morning
      of 2026-08-29 reopened on every dispatch.
    - the fan-out wall was TRIPPED by an agent. The agents' own reading filled
      the parent's budget, so the wall refused the session for having done the
      exact thing the wall exists to demand.

    Pure and public, so both directions are measurable on synthetic input.
    """
    return str(payload.get("agent_id") or "").strip() or "main"


def _state_key(session_id: str, actor: str) -> str:
    """One filesystem-safe name per (session, actor) pair."""
    return re.sub(r"[^A-Za-z0-9_-]", "_",
                  f"{session_id or 'unknown'}~{actor or 'main'}")[:96]


def _graph_marker(session_id: str, actor: str = "main") -> Path:
    return _GRAPH_STATE_DIR / f"{_state_key(session_id, actor)}.stamp"


def is_code_search(tool_name: str, tool_input: dict) -> bool:
    """True when this call is a code lookup the graph should have answered.

    Pure apart from its arguments, so both directions are measurable on
    synthetic input. That matters here more than usual: over a session where
    the marker already exists this predicate decides nothing, so deleting its
    body would change no live result and the rule would quietly stop existing.
    """
    if tool_name in ("Grep", "Glob"):
        target = " ".join(str(tool_input.get(k, ""))
                          for k in ("pattern", "path", "glob"))
    elif tool_name == "Read":
        # Opening a source file to learn what it does is the same lookup as
        # grepping for it, and the operator's instruction names both: "before
        # any grep or Read". The first version of this predicate answered False
        # for every Read, and `tests/..._four_times_and_obeyed_none.py` pinned
        # `Read scripts/sentinel.py` in the NOT-a-search list, so the hole was
        # asserted as correct. Measured 2026-08-29 on the live wall: a fresh
        # session refused a Grep of `scripts/` and allowed a Read of
        # `scripts/sentinel.py`, which is the whole rule walked around in one
        # tool call.
        target = str(tool_input.get("file_path", ""))
    elif tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        # Program position, per segment, AND the segment must carry something to
        # search. The variable here was named `first` while `re.split` returned
        # EVERY word, so a reader binary anywhere in the line marked the call a
        # code lookup, and the whole line then became the target that
        # `_CODE_HINTS` matched `.py` in. MEASURED 2026-08-31:
        # `python scripts/thread.py list | tail -5` answered True and was refused
        # as the session's first code lookup. It runs a CLI and reads its output.
        #
        # The non-flag argument is the second half of the rule. `tail -5` and
        # `head -20` at the end of a pipe read a stream, not a file, so a search
        # binary with nothing but flags is not a search. Only the matching
        # segments become the target, so a path elsewhere in the line no longer
        # decides the verdict.
        # `_SEGMENT_SPLIT`, not `_SEGMENT_SPLIT_RE`. The latter also splits on
        # parentheses, for the release wall's subshells, and an `ast-grep
        # --pattern 'os.kill($P, 0)' scripts/x.py` then lost `scripts/x.py` into
        # a third segment and stopped reading as a code search. Caught by the
        # existing fixture list on the first run.
        searching = []
        for seg in _SEGMENT_SPLIT.split(strip_heredocs(command)):
            progs = _program_candidates(seg)
            if not set(progs) & set(_SEARCH_BINARIES):
                continue
            words = seg.split()
            at = next((i for i, w in enumerate(words)
                       if w.rsplit("/", 1)[-1] in _SEARCH_BINARIES), None)
            if at is None:
                continue
            if not any(not w.startswith("-") for w in words[at + 1:]):
                continue  # flags only: reading a stream, not a corpus
            searching.append(seg)
        if not searching:
            return False
        target = " ".join(searching)
    else:
        return False

    lowered = target.lower()
    if any(hint in lowered for hint in _NOT_CODE_HINTS):
        return False
    if any(hint in lowered for hint in _CODE_HINTS):
        return True
    if _CODE_TREE_RE.search(lowered):
        return True
    # A Grep with no path at all sweeps the whole repo, which is the case the
    # graph answers best and the one most often reached for by reflex.
    return tool_name == "Grep" and not tool_input.get("path")


# THE RULE IS UNCONDITIONAL. It used to yield after three refusals in one
# session, and that hatch is gone as of 2026-08-29 on the operator's explicit
# instruction: "если есть жёсткое правило, оно ВСЕГДА выполнялось,
# безоговорочно". A control with a counter is a control the caller can wait out,
# and this one is a wall precisely because the written version was ignored three
# times with a reminder on screen.
#
# The hatch existed for one real reason, and the reason is now fixed instead of
# accommodated. A wall whose UNLOCK path is broken is a cage, and this one
# nearly shipped as one: the dispatcher's PreToolUse matchers are `Bash`,
# `Read|Grep|Glob` and the write family, so an MCP tool call never reached this
# check and no real `codegraph_explore` could stamp the marker. Measured
# 2026-08-29: the wall refused, the explore ran, the wall refused again.
#
# Two independent doors now open it, and a session can only be caged if BOTH are
# shut:
#   1. the MCP tool, via the `mcp__codegraph__.*` matcher. Added the same day to
#      all three TRACKED platform templates, so a fresh clone carries it; the
#      live `.claude/settings.local.json` is gitignored and cannot be relied on.
#   2. `codegraph explore` in a Bash command, which rides the `Bash` matcher.
#      That matcher is in every settings file in the repository, so this door
#      does not depend on anyone having copied a template.
# Either one stamps the marker BEFORE the call runs, so a graph that is down, an
# MCP server that is not connected, and a CLI that is not installed all still
# unlock. The rule is "ask the graph first", never "the graph must answer".


def check_graph_first(payload: dict):
    tool_name = payload.get("tool_name", "")
    session_id = str(payload.get("session_id", "")).strip()

    # No session, no rule. "The first code search of the SESSION" needs a
    # session to be the first of, and every real PreToolUse payload carries one.
    # A payload without it is another test driving a different wall, and keying
    # them all on one shared "unknown" marker would make those suites depend on
    # each other's order. That is not hypothetical: the rate limiter's single
    # shared state file did exactly this to a wall test earlier on 2026-08-29.
    if not session_id:
        return None

    # Per ACTOR, not per session. An agent must ask the graph for itself; its
    # stamp must not unlock the session that dispatched it. See `actor_id`.
    marker = _graph_marker(session_id, actor_id(payload))

    # Stamp on the explore ITSELF, at PreToolUse, before it runs. Stamping on
    # success instead would mean a graph outage locks the session out of grep
    # too, and a control that wedges a session gets switched off.
    #
    # Both doors are read here: the MCP tool by name, and the shell CLI by
    # command. The second is what makes the rule safe to enforce absolutely.
    asked_the_graph = "codegraph" in tool_name.lower() or (
        tool_name == "Bash"
        and _bash_asked_the_graph(str((payload.get("tool_input") or {})
                                      .get("command", "")))
    )
    if asked_the_graph:
        try:
            _GRAPH_STATE_DIR.mkdir(parents=True, exist_ok=True)
            marker.write_text("unlocked", encoding="utf-8")
        except OSError as exc:
            print(f"[_dispatch:graph_first] could not stamp: {exc}",
                  file=sys.stderr)
        return None

    if not (WORKSPACE / ".codegraph").is_dir():
        return None
    if marker.exists():
        return None
    if not is_code_search(tool_name, payload.get("tool_input") or {}):
        return None

    return {
        "decision": "block",
        "_policy_deny": True,
        "reason": (
            "Ask the graph first. This call reaches source code and no "
            "codegraph query has run in this session yet.\n\n"
            "One call to `codegraph_explore` returns the verbatim source of "
            "the relevant symbols PLUS who calls them, which grep cannot "
            "produce at any number of round trips. The operator gave this "
            "instruction four times before it became a wall, and made it "
            "unconditional on the fifth: there is no refusal count to wait "
            "out and no number of attempts that lets this through.\n\n"
            "TWO WAYS OUT, both of which unlock the whole session:\n"
            "  - call `codegraph_explore` with the symbol or file names in "
            "this lookup, or\n"
            "  - run `codegraph explore \"<names>\"` in Bash.\n"
            "Any ATTEMPT unlocks, including one that errors or returns "
            "nothing, so a graph that is down, an MCP server that is not "
            "connected and a missing CLI can none of them wedge you.\n\n"
            "Never refused at all: anything under .tmp/ or a scratch "
            "directory, logs, markdown, JSON, CSV and HTML. This wall is "
            "about locating and reading CODE."
        ),
    }


# ============================================================
# check_fanout_first — a wide stretch of work considers agents
# ============================================================
#
# The operator's standing instruction, escalated twice: "агенты и workflow
# разрешены всегда и везде", then "не только разрешены, они MUST BE USED, если
# это даёт скорость и оптимизацию". On 2026-08-29 he asked for the same kind of
# mechanism `check_graph_first` gives the graph rule, and for the same reason:
# he had said it three times and it kept lapsing.
#
# HOW THIS DIFFERS FROM THE GRAPH WALL, stated plainly because the difference
# decides the design. `check_graph_first` refuses one specific WRONG ACTION: a
# code search. "Did not use an agent" is not an action, it is an ABSENCE, and
# there is no single tool call to refuse. So this wall watches the SHAPE OF A
# STRETCH instead: how many distinct files this session has investigated by hand
# since it last considered fanning out.
#
# Distinct paths, not call count, and that choice is the whole precision of the
# rule. Thirty calls against one file is deep work and inherently serial;
# twelve calls against twelve files is a fan-out that was not dispatched. A
# counter keyed on calls would fire on the first kind, which is the sort of
# false refusal that gets a control switched off.
#
# TWO WAYS PAST, and the second is the honest part. Dispatching an Agent or a
# Workflow clears the budget, because the rule has been obeyed. Otherwise
# `python scripts/fanout-note.py "<why this is serial>"` clears it AND APPENDS
# THE REASON to a log the operator can read. Unlike a refusal counter, that
# escape cannot be taken silently: every use leaves a claim with a timestamp,
# so "I decided this was serial" becomes auditable rather than assumed. A wall
# whose only escape is invisible teaches nothing.

_FANOUT_STATE_DIR = WORKSPACE / ".claude" / "state" / "fanout"

# The tools that ARE fanning out. `Agent` is this harness's name; `Task` is the
# older one and `Workflow` the orchestrated form. All three clear the budget.
FANOUT_TOOLS = ("Agent", "Task", "Workflow")

# The tools whose paths `investigated_paths` charges for, and so the only tools
# this wall may refuse. Everything else -- a write, an edit, a notebook -- is
# waved through however far over budget the session is.
FANOUT_COUNTED_TOOLS = ("Read", "Grep", "Glob", "Bash")

# How many distinct files may be investigated by hand before the question is
# forced. A first setting, tunable by env rather than by editing this file: the
# right number is a property of how the operator works, and it should be moved
# on evidence rather than on argument.
FANOUT_PATH_BUDGET = int(os.environ.get("WS_FANOUT_BUDGET", "12"))

# Reading output, not investigating a corpus. Same list the graph wall uses for
# the same reason: a session must be able to read its own logs and scratch
# files without spending budget it did not mean to spend.
_FANOUT_IGNORE = ("tmp/", ".log", ".jsonl", "node_modules", ".venv",
                  "__pycache__", ".git/",
                  # Device and kernel nodes are plumbing in a command line, not
                  # a corpus: `2>/dev/null` on a real search charged a file.
                  "/dev/", "/proc/", "/sys/")

# A FORWARD slash, and only a forward slash. The separator class used to
# accept a backslash as well, for Windows paths, and the test below
# normalised backslashes to slashes before looking -- so every regex escape
# in a command (`\s`, `\n`, `\t`, `\d`) and every line continuation was
# counted as a distinct file. MEASURED 2026-08-29: one heredoc patching ONE
# file reported fourteen. This workspace is WSL-only and runs no Windows
# tooling, so nothing real is lost by dropping the backslash form.
#
# The GUARD in `investigated_paths` is the enforcement, not this class. Putting
# the backslash back here alone is a mutation that survives, because the guard
# tests for a literal forward slash and rejects `\s` whatever this matched. The
# narrower class only avoids building junk tokens to iterate.
_PATH_TOKEN = re.compile(r"[A-Za-z0-9_./-]*/[A-Za-z0-9_./-]*")


def _fanout_marker(session_id: str, actor: str = "main") -> Path:
    return _FANOUT_STATE_DIR / f"{_state_key(session_id, actor)}.json"


# The second of three copies of the heredoc opener in this file, and it had
# neither guard against a here-string. See `_HEREDOC_START_RE` for the
# measurement: `<<<word` is read as a heredoc opening on `word`, the terminator
# is never found, and `strip_heredocs` drops every line after it. Here that
# means the fan-out counter stops seeing the paths those lines name, so the
# wall under-counts a session's hand-reads - a guard weakened by a typo the
# operator did not make.
_HEREDOC_OPEN = re.compile(r"(?<!<)<<(?!<)-?\s*[\"\']?([A-Za-z_][A-Za-z0-9_]*)[\"\']?")


def strip_heredocs(command: str) -> str:
    """The command line with every heredoc BODY removed.

    A heredoc body is content the command writes; it is not a corpus the caller
    is reading. MEASURED 2026-08-29: a `cat > msg.txt <<'EOF' ... EOF` holding a
    commit message spent the whole fan-out budget on the file names the message
    NAMED, and the wall then refused the commit. The same shape charges a patch
    script for the paths it edits, twice: once in the redirect and once in the
    body.

    Pure, so both directions are measurable on synthetic input. The delimiter is
    matched at the start of a line, per the shell, and an unterminated heredoc
    runs to the end, which is also what the shell does.
    """
    lines = command.split("\n")
    kept, i = [], 0
    while i < len(lines):
        line = lines[i]
        kept.append(line)
        markers = _HEREDOC_OPEN.findall(line)
        i += 1
        for marker in markers:
            while i < len(lines) and lines[i].strip() != marker:
                i += 1
            i += 1        # drop the terminator line too
    return "\n".join(kept)


# The commands that READ a file so a human can look at it. An allowlist, not a
# denylist, and that is the whole design: the wall exists to notice a session
# opening file after file by hand, and a denylist has to anticipate every
# command that is not that. MEASURED 2026-08-29, twice in one hour: `pytest a b
# c` and `ruff a b` were charged as five hand-reads and the wall refused the
# next step, and a `git commit` was charged for the files its message named.
# Running a suite, linting a set, committing a change: each names paths, none
# is a person reading them.
_READER_BINARIES = (
    "grep", "rg", "egrep", "fgrep", "ag", "ack", "ast-grep", "sg",
    "find", "sed", "awk", "cat", "head", "tail", "less", "more",
    "nl", "wc", "diff", "od", "xxd", "strings", "jq", "yq",
)

# Where one command ends and the next begins. Only the FIRST word of a segment
# is the binary, so `git commit -F msg` and `grep -n x a.py` split cleanly.
_SEGMENT_SPLIT = re.compile(r"(?:\|\||&&|[;|&\n])")


def reader_path_tokens(command: str) -> list[str]:
    """Path-looking tokens from the READER segments of a shell command.

    Pure, so both directions are measurable on synthetic input, which matters
    here because the allowlist decides everything: a body that returned every
    token would restore the false refusals, and one that returned none would
    delete the Bash half of the rule without failing anything else.
    """
    found: list[str] = []
    for segment in _SEGMENT_SPLIT.split(strip_heredocs(command)):
        words = segment.split()
        if not words:
            continue
        # Skip a leading `sudo`, `time`, or `VAR=value` assignment.
        #
        # `.split()[0]` used to sit on the end of the basename read. A word
        # coming out of `segment.split()` holds no whitespace, so it was
        # redundant on every input except one: a word ENDING in `/` has the
        # empty string as its basename, `"".split()` is `[]`, and `[0]` raised
        # IndexError. MEASURED 2026-08-31 on `grep -rn foo \` + newline +
        # `  scripts/`, which is an ordinary backslash-continued grep: the
        # continuation segment is a bare directory. `check_fanout_first` died,
        # `main()` caught it, printed one stderr line, allowed the tool, and the
        # path was never charged to the budget the wall exists to keep.
        i = 0
        while i < len(words):
            word = words[i]
            if word in ("sudo", "time", "command", "nohup"):
                i += 1
                continue
            if "=" in word.rsplit("/", 1)[-1] and not word.startswith("-"):
                i += 1
                continue
            break
        if i >= len(words):
            continue
        binary = words[i].rsplit("/", 1)[-1]
        if binary in _READER_BINARIES:
            # The ARGUMENTS, not the command word. Scanning the whole segment
            # charged `/bin/cat` and `/usr/bin/grep` as files being read, which
            # is how a reader invoked by absolute path spent two budget slots
            # for one file.
            found.extend(_PATH_TOKEN.findall(" ".join(words[i + 1:])))
    return found


def _runs_fanout_note(command: str) -> bool:
    """True when a segment of `command` actually RUNS `scripts/fanout-note.py`.

    Program position, so a reader binary in the same segment is not a door. See
    the measurement at the call site in `check_fanout_first`.
    """
    return any("fanout-note.py" in _program_candidates(seg) for seg in _SEGMENT_SPLIT_RE.split(strip_heredocs(command)))


def investigated_paths(tool_name: str, tool_input: dict) -> set[str]:
    """The distinct files this one call investigates by hand.

    Pure, so both directions are measurable on synthetic input. That matters
    more than usual here: over a session already under budget this function
    decides nothing, so a body that returned an empty set would change no live
    result and the rule would quietly stop existing.
    """
    if tool_name in ("Read", "Grep", "Glob"):
        candidates = [str(tool_input.get(k, ""))
                      for k in ("file_path", "path", "glob", "pattern")]
    elif tool_name == "Bash":
        candidates = reader_path_tokens(str(tool_input.get("command", "")))
    else:
        return set()

    found = set()
    for raw in candidates:
        token = raw.strip().strip("'\"")
        # `alnum` as well as the slash: a bare `/`, or `../`, is a separator
        # someone typed, not a file that was opened.
        if not token or "/" not in token or not any(c.isalnum() for c in token):
            continue
        lowered = token.lower()
        if any(hint in lowered for hint in _FANOUT_IGNORE):
            continue
        found.add(token.replace("\\", "/"))
    return found


def _fanout_state(marker: Path) -> set[str]:
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return set(data) if isinstance(data, list) else set()


def check_fanout_first(payload: dict):
    tool_name = payload.get("tool_name", "")
    session_id = str(payload.get("session_id", "")).strip()
    if not session_id:
        return None          # same reason as check_graph_first: no session, no rule

    # A subagent IS the fan-out. Nudging one to fan out again would ask for the
    # nested orchestration `.claude/rules/skill-orchestrator.md` principle 8
    # forbids, and its reading is not the session's hand-work. The marker is
    # keyed by actor as well, so removing this early return still cannot let an
    # agent spend the session's budget.
    actor = actor_id(payload)
    if actor != "main":
        return None

    marker = _fanout_marker(session_id, actor)

    # Clearing the budget. Both doors write the same empty state, so the rule
    # cannot tell them apart afterwards and does not need to: what it enforces
    # is that ONE of them happened.
    command = str((payload.get("tool_input") or {}).get("command", ""))
    # Resolved in PROGRAM POSITION. A substring test made every command that
    # merely NAMED the script a door, and the refusal text asserts the door is
    # audited: "It appends the reason with a timestamp to a log the operator
    # reads." MEASURED 2026-08-31 with the budget at 12 and the state seeded at
    # 20 paths, `grep -n fanout-note.py scripts/*.py` and
    # `ls -la scripts/fanout-note.py` each reset the count to zero and appended
    # nothing. The first of those is itself the hand-reading the wall bounds.
    cleared = tool_name in FANOUT_TOOLS or _runs_fanout_note(command)
    if cleared:
        try:
            _FANOUT_STATE_DIR.mkdir(parents=True, exist_ok=True)
            marker.write_text("[]", encoding="utf-8")
        except OSError as exc:
            print(f"[_dispatch:fanout] could not clear: {exc}", file=sys.stderr)
        return None

    seen = _fanout_state(marker) | investigated_paths(
        tool_name, payload.get("tool_input") or {})
    if not seen:
        return None

    try:
        _FANOUT_STATE_DIR.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(sorted(seen)), encoding="utf-8")
    except OSError as exc:
        print(f"[_dispatch:fanout] could not count: {exc}", file=sys.stderr)

    # Refuse only the tools this wall actually charges for. A wall that also
    # refused Write and Edit would, the moment the budget ran out, block the
    # repair as well as the investigation: that is a cage, not a wall, and it
    # happened while this very function was being fixed.
    if tool_name not in FANOUT_COUNTED_TOOLS:
        return None

    if len(seen) <= FANOUT_PATH_BUDGET:
        return None

    return {
        "decision": "block",
        "_policy_deny": True,
        "reason": (
            f"Consider fanning out. This session has investigated "
            f"{len(seen)} distinct files by hand without dispatching a single "
            f"agent or workflow.\n\n"
            f"The operator's standing instruction is that agents and workflows "
            f"are not merely permitted but MUST BE USED wherever they buy speed "
            f"or better coverage. Work this wide is usually several independent "
            f"questions being answered one after another.\n\n"
            f"TWO WAYS PAST:\n"
            f"  - dispatch an Agent or a Workflow. Give each one a complete, "
            f"self-contained brief: what to find, what NOT to do, and the shape "
            f"of the answer.\n"
            f"  - if this stretch really is serial (one dependency chain, one "
            f"file being edited repeatedly, a measurement that must be taken in "
            f"order), say so and continue:\n"
            f"        python scripts/fanout-note.py \"<why this is serial>\"\n\n"
            f"That second option is NOT a silent bypass. It appends the reason "
            f"with a timestamp to a log the operator reads, so the judgement is "
            f"recorded rather than assumed. Either way the budget resets and the "
            f"count starts again.\n\n"
            f"Never counted: .tmp/ and scratch directories, logs, and JSONL. "
            f"The budget is {FANOUT_PATH_BUDGET} distinct files "
            f"(WS_FANOUT_BUDGET)."
        ),
    }


# ============================================================
# Release gate: no commit and no push without a fresh operator word
# ============================================================
#
# WHY THIS IS A WALL AND NOT A RULE.
#
# "Commit or push only when the user asks" has been written down twice: in
# `~/.claude/CLAUDE.md` and in the operator's auto-memory as
# `commit-and-push-only-when-asked`, whose own text records the first breach on
# 2026-08-20. It happened again on 2026-08-30, and the mechanism is worth
# stating exactly, because it is not forgetfulness and a third copy of the
# sentence would not have stopped it:
#
#   The operator authorised ONE push. That authorisation was then written into
#   a handoff summary as "the operator's approval is already given", survived a
#   context compaction, and was read back on the other side as a STANDING FACT
#   about the world rather than as a spent event. Every later decision cited the
#   summary, not the operator. An approval had been promoted from an event into
#   a state.
#
# A rule cannot defend against that, because the rule was never contradicted in
# the moment: at the point of the second push the model sincerely believed
# permission existed. What is needed is a check that re-reads the operator's
# ACTUAL most recent words at the instant of the action, so belief is replaced
# by evidence and a stale belief cannot survive a single turn.
#
# `last-prompt` records in the session transcript carry `lastPrompt`, the
# operator's typed text verbatim, re-emitted at every leaf. Task notifications,
# Stop-hook feedback and tool results are NOT last-prompts, so a synthetic
# message cannot authorise anything. The model does not write the transcript.
#
# Fail-closed everywhere: an unreadable transcript, a missing field or an
# unrecognised command shape all refuse. A gate that opens when it cannot see is
# not a gate.

_RELEASE_STATE_DIR = WORKSPACE / ".claude" / "state" / "release"

# Quoted spans are stripped before matching, so `grep "git commit" file` is not
# read as a commit. Without this the wall blocks ordinary searches, and a wall
# that blocks ordinary work is a wall somebody routes around.
# Escape-aware. A quote character preceded by a backslash is a literal, not the
# start of a quoted span, and reading it as one let a real push hide between two
# of them: MEASURED 2026-08-31, `echo can\'t; git push; echo won\'t` is valid
# shell, the two escaped apostrophes paired as a span, and `release_action`
# returned None for a command that pushes. Contrived, and still a hole in a wall.
#
# The lookbehind is the conservative direction for a WALL: it blanks LESS, so a
# missed span costs a false refusal (visible, annoying) rather than a missed push
# (invisible, and the whole point). `\\'` (an escaped backslash then a real
# quote) is still read as a literal, which errs the same safe way.
_QUOTED_RE = re.compile(r"(?<!\\)'[^']*'|(?<!\\)\"(?:\\.|[^\"\\])*\"")

# The push-capable scripts of this workspace, by BASENAME.
#
# This list is not hand-curated and must not be extended by guesswork.
# `tests/test_a_release_the_operator_never_asked_for.py` DERIVES the set from
# the source by AST -- every `scripts/**.py` with a `__main__` guard that
# reaches `supervised_push()` or builds a literal `git`/`push` argv, following
# imports and subprocess fan-out transitively -- and fails if any derived entry
# point is missing from here. A new push script therefore reddens the suite
# until it is added. A hand-maintained security list falls behind; a derived one
# cannot.
#
# MEASURED 2026-08-30, before the derivation existed, calling `release_action`
# directly: `python scripts/safe-push.py --repo engine` returned None. That
# script IS the workspace's deterministic supervised push. Four more real push
# paths were open the same way. The list had been written by hand and had fallen
# behind the code, which is the failure mode this whole comment exists to close.
#
# `publish-corporate.py` is the one entry the derivation does NOT produce: it
# pushes nothing (its only subprocess is `git ls-files`). It is kept because
# `--copy` writes into ../heading-os-corporate/, the repository the fleet is
# published from, and because the unit of this wall is the SCRIPT, never the
# flag. Carving out `--preview` on the grounds that that one mode is read-only
# would put flag parsing inside a release wall, and a flag can be reordered,
# aliased, or defaulted differently by a later edit. The measured cost of
# keeping it is one refused read-only preview; the cost of the carve-out is a
# permanent bypass shape. Do not "fix" this by adding a flag exception.
_PUSH_SCRIPTS = frozenset({
    "push-all.py",
    "safe-push.py",
    "publish-service.py",
    "publish-marketplace.py",
    "create-data-repo.py",
    "provision-exec.py",
    "offboard-exec.py",
    "promote-knowledge.py",
    "emergency-revoke.py",
    "memory.py",           # `memory.py promote` shells out to promote-knowledge.py
    "publish-corporate.py",  # not derived; see the paragraph above
})

# `push-updates` was in this set as a bare token until 2026-08-31 and guarded
# nothing reachable. There is no executable of that name anywhere in the repo --
# only the directory `.claude/skills/push-updates/` -- and `/push-updates` is a
# SKILL, invoked through the Skill tool, which `check_release_gate` never sees
# (it returns None for every non-Bash payload, and it is the only caller of
# `release_action`). The Bash commands that skill issues are `publish-corporate.py`
# and `push-all.py`, both covered above. All the token ever did was refuse reads
# of files whose path spelled it.

# Programs that stand IN FRONT of the program actually being run. Skipping them
# is what lets the wall ask "what is this segment RUNNING" instead of "does this
# string contain a name somewhere".
_WRAPPERS = frozenset({
    "env", "sudo", "doas", "nohup", "command", "exec", "time", "nice",
    "stdbuf", "timeout", "xargs", "uv", "uvx", "poetry", "pipenv", "pdm",
    "hatch", "run", "npx",
    "python", "python2", "python3", "py", "pypy", "pypy3",
    "bash", "sh", "zsh", "dash", "ksh", "fish", "perl", "ruby", "node",
})

# A heredoc body is a command line only when a SHELL is what reads it.
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "fish"})

# Authorising words. A PUSH word also authorises the commit that carries it:
# asking for the push is asking for the work to leave the machine, and refusing
# the commit underneath it would only teach the operator to type two words.
_PUSH_WORDS = (
    "push", "пуш", "запуш", "отправь", "backup", "бэкап", "бекап",
    "publish", "опубликуй", "выложи", "залей",
)
_COMMIT_WORDS = (
    "commit", "коммит", "закоммить", "зафиксируй",
)

# Any of these anywhere in the prompt refuses, whatever else it says. Deliberately
# blunt: "не пушь пока" and "push" differ by one token, and a wall that has to
# parse intent is a wall that gets it wrong in the expensive direction.
_NEGATIONS = (
    "не пуш", "не коммить", "не комить", "не отправляй", "не заливай",
    "не публикуй", "без пуша", "без коммита",
    "don't push", "dont push", "do not push", "no push",
    "don't commit", "dont commit", "do not commit", "no commit",
)


def _strip_quoted(cmd: str) -> str:
    """The command with quoted spans blanked, for matching only."""
    return _QUOTED_RE.sub(" ", cmd or "")


def _loads_or_none(raw: bytes) -> dict | None:
    """One transcript line as a dict, or None.

    A partly-written or truncated line is ORDINARY here: the transcript is
    appended to by the harness while this hook reads it, so the last line is
    routinely half a record. It is skipped, not logged, because this runs on
    every release command and a warning per torn line would be noise the
    operator learns to scroll past. The consequence of skipping is covered: if
    no `last-prompt` is found at all, the caller REFUSES.
    """
    try:
        rec = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return rec if isinstance(rec, dict) else None


# Commands that only LOOK at bytes. A segment led by one of these cannot
# release anything, whatever the rest of it spells.
_INSPECTORS = frozenset({
    "grep", "egrep", "fgrep", "rg", "ag", "ack", "sed", "awk", "cat", "head",
    "tail", "less", "more", "wc", "ls", "find", "file", "stat", "diff", "cmp",
    "cut", "sort", "uniq", "tr", "nl", "basename", "dirname", "realpath",
    "readlink", "echo", "printf", "md5sum", "sha256sum", "test", "which",
})

# A NEWLINE separates commands exactly as `;` does, and it was missing here
# until 2026-08-31. MEASURED with the old splitter: the two-line command
# `cd /repo` then `git push origin main` returned None, because the anchors were
# `^` without re.MULTILINE and no operator preceded the push. A multi-line Bash
# call is an ordinary shape, so that was the wall failing open on an ordinary
# push. `)` is here so `(cd x && git push)` yields a clean final word.
_SEGMENT_SPLIT_RE = re.compile(r"(?:\|\||&&|[;&|\n\r()]|\$\()")

# `<<EOF`, `<< EOF`, `<<'EOF'`, `<<\"EOF\"`, `<<-EOF`.
#
# The name carries a `_RELEASE_` prefix because a plain `_HEREDOC_START_RE`
# already exists further up, and this file is one module. MEASURED 2026-08-31:
# the second binding won, `_unquoted_skeleton` at line 1179 matched with THIS
# pattern, and `_skip_heredoc_body` then asked the match for a group named
# `word` that only the FIRST pattern defines. Every heredoc reaching that walk
# raised `IndexError: no such group`, taking 11 tests down with it. The two
# patterns are near-identical to read and differ only in whether their groups
# are named, which is exactly why the collision was invisible in review.
# The third copy, with the same here-string hole as the other two. See
# `_HEREDOC_START_RE`. `_heredoc_body_spans` blanks from the newline after the
# match to the terminator, or to the end when there is none, so a `<<<word`
# blanked the remainder of the command out of whatever this pattern feeds.
_RELEASE_HEREDOC_RE = re.compile(
    r"(?<!<)<<(?!<)-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

# Shell punctuation clinging to a word. `git push)` must compare equal to
# `push`, or the wall fails open on a subshell.
_WORD_TRIM = "()[]{};`\"'"

# Flags whose remaining words are a command line, not arguments.
_NESTED_CMD_FLAGS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})


def _heredoc_body_spans(cmd: str) -> list[tuple[int, int]]:
    """(start, end) of every heredoc BODY in `cmd`."""
    spans = []
    for m in _RELEASE_HEREDOC_RE.finditer(cmd):
        nl = cmd.find("\n", m.end())
        if nl == -1:
            continue
        start = nl + 1
        term = re.compile(rf"^[ \t]*{re.escape(m.group(2))}[ \t]*$", re.MULTILINE)
        t = term.search(cmd, start)
        spans.append((start, t.start() if t else len(cmd)))
    return spans


def _blank_spans(cmd: str, spans: list[tuple[int, int]]) -> str:
    """`cmd` with those spans replaced by spaces, newlines preserved."""
    buf = list(cmd)
    for start, end in spans:
        for i in range(start, min(end, len(buf))):
            if buf[i] not in "\r\n":
                buf[i] = " "
    return "".join(buf)


def _program_candidates(seg: str) -> list[str]:
    """The basenames that could be the program this segment RUNS.

    Walks from the left, skipping flags, `VAR=value` prefixes and bare numbers
    (`timeout 30 ...`, `nice 10 ...`), and keeps walking through wrappers, so
    `uv run python scripts/push-all.py` yields `[uv, run, python, push-all.py]`
    and `cp a.md .claude/skills/push-updates/b.md` yields just `[cp]`.

    A later ARGUMENT never appears here, and that is the whole point. MEASURED
    2026-08-30, when the script names were matched anywhere in a segment:
    `.venv/bin/python scripts/ste-check.py .claude/skills/push-updates/SKILL.md`
    and `cp a.md .claude/skills/push-updates/b.md` were both refused as pushes.
    Neither releases anything, and the first cost a real lint run that day. That
    is how a wall teaches agents a detour, after which it guards nothing.
    """
    out: list[str] = []
    for raw in seg.split():
        word = raw.strip(_WORD_TRIM)
        if not word or word.startswith("-"):
            continue
        if "=" in word:
            # `env GIT_DIR=/r/.git git push`. The scan only ever reaches here
            # while everything seen so far is a wrapper (it breaks on the first
            # program), so an `=` token at this point is an assignment prefix,
            # not a program. Restricting the skip to the FIRST word let
            # `env VAR=v git push` resolve to `.git` and return None.
            continue
        if word.replace(".", "", 1).isdigit():
            continue
        base = word.rsplit("/", 1)[-1]
        out.append(base)
        if "." in base and not base.endswith(".py"):
            # `python -m scripts.memory promote` runs memory.py, and a basename
            # match never saw it. MEASURED 2026-08-31: None, for a real push
            # path. Only one push-capable entry point has a hyphen-free name
            # and is therefore importable this way today, but that is an
            # accident of naming, not a guarantee.
            out.append(base.rsplit(".", 1)[-1] + ".py")
        if base not in _WRAPPERS:
            break
    return out


def _strip_inert_heredocs(cmd: str) -> str:
    """Blank heredoc BODIES, unless a shell is what reads them.

    A heredoc feeds data to the program in front of it. For `python3 - <<EOF`,
    `tee f.py <<EOF` or `cat > f <<EOF` that data is a file being written, and
    matching release patterns inside it is pure over-friction: MEASURED
    2026-08-31, an agent writing a test file for THIS wall was refused twice
    because the strings "git push" and "git commit" appeared in its cases, and
    it routed around the wall with the Edit tool and a scratch file.

    For a shell the body IS a command line, so `bash <<'EOF' ... git push ...
    EOF` must stay caught -- and MEASURED at the same time, it was NOT: the old
    splitter never split on a newline, so that real push returned None. When any
    segment outside the bodies runs a shell, the bodies are returned untouched
    and the ordinary machinery reads them. Over-approximating "a shell runs
    here" is the safe direction and is chosen deliberately.

    Bound, stated rather than implied: this wall reads SHELL command lines. Code
    inside an interpreter payload that pushes (`subprocess.run(["git","push"])`
    in a `python3 -` heredoc) is not covered here and never was -- quoting
    already blanked it before this function existed. The gate for that is the
    same one that covers every script: the program being run.
    """
    spans = _heredoc_body_spans(cmd)
    if not spans:
        return cmd
    outside = _blank_spans(cmd, spans)
    for seg in _SEGMENT_SPLIT_RE.split(outside):
        if any(c in _SHELLS for c in _program_candidates(seg)):
            return cmd
    return outside


def _executable_segments(bare: str) -> list[str]:
    """The parts of a command line that could actually run something.

    A segment whose FIRST word is a pure inspector is dropped. Since the script
    names became program-position matches, that drop is belt-and-braces rather
    than load-bearing: `cat scripts/push-all.py` resolves to `cat` and would be
    silent anyway. It is kept because it costs nothing and says the intent out
    loud.

    The `-exec` extraction below is NOT belt-and-braces. It is the one place
    where a dropped inspector was hiding a real command.

    Splitting first means a compound line is still caught: `ls && push-all.py`
    keeps its second segment.
    """
    out = []
    for seg in _SEGMENT_SPLIT_RE.split(bare):
        words_raw = seg.split()
        # `find . -name x -exec git push \;` runs a real push, and `find` is an
        # inspector, so the whole segment used to be dropped. Whatever the outer
        # program is, the words after `-exec` are a command line of their own
        # and get judged as one. Extracted BEFORE the inspector drop, or the
        # nested command goes down with its host.
        for i, word in enumerate(words_raw):
            if word in _NESTED_CMD_FLAGS:
                out.append(" ".join(words_raw[i + 1:]))
                break
        # Strip before returning. A segment carved out of `a && git push` keeps
        # its leading space, and the first version of this function returned it
        # unstripped, so `^` matched nothing and a real push read as None.
        # Program resolution no longer depends on the anchor, but the strip
        # stays: it is what makes the segments readable in a failing test.
        seg = seg.strip()
        words = seg.split()
        if words and words[0].rsplit("/", 1)[-1] in _INSPECTORS:
            continue
        out.append(seg)
    return out


def _segment_words(seg: str) -> list[str]:
    return [w.strip(_WORD_TRIM) for w in seg.split()]


def _segment_pushes(seg: str) -> bool:
    """Whether this one segment releases work to a remote."""
    candidates = _program_candidates(seg)
    if any(c in _PUSH_SCRIPTS for c in candidates):
        return True
    words = _segment_words(seg)
    if "git" in candidates and "push" in words:
        # Deliberately option-blind. The old regex enumerated the global-option
        # shapes it knew (`-x`, `-c k=v`) and MEASURED 2026-08-31 it missed
        # `git -C ../x push`, which is how this workspace pushes the second
        # repository. Asking "is git the program, and is `push` one of its
        # words" cannot be evaded by an option shape nobody thought of.
        return True
    if "gh" in candidates:
        # An index walk, not `zip(words, words[1:])`: a strict zip over a
        # sliced pair can never raise, so the lint that asks for one would be
        # satisfied by a check that measures nothing.
        for i in range(len(words) - 1):
            if (words[i], words[i + 1]) in (("release", "create"), ("pr", "merge")):
                return True
    return False


def _segment_commits(seg: str) -> bool:
    if "git" not in _program_candidates(seg):
        return False
    words = _segment_words(seg)
    if "commit" in words:
        return True
    if "tag" in words:
        # `git tag -a v1`. A bare `git tag` lists tags and is left alone, which
        # is what the regex this replaced did too.
        return any(w.startswith("-") for w in words[words.index("tag") + 1:])
    return False


def release_action(command: str) -> str | None:
    """`"push"`, `"commit"`, or None. Pure, so both directions are measurable.

    Coverage this establishes, and no more: SHELL command lines. It reads what
    each segment RUNS -- git, gh, or a push-capable script -- through wrappers,
    interpreters, subshells, newlines, shell heredocs and `find -exec`.

    It does NOT read code inside an interpreter payload. `python3 -c 'from
    scripts.utils.git_push import supervised_push; supervised_push(".")'`
    returns None, and always has: quoting blanks the payload before anything
    looks at it. That is a deliberate boundary, not an oversight -- parsing
    Python out of a shell string would be a second language to get wrong -- but
    it is the shape to remember when reasoning about what this wall promises.
    """
    bare = _strip_quoted(_strip_inert_heredocs(command or ""))
    segments = _executable_segments(bare)
    for seg in segments:
        if _segment_pushes(seg):
            return "push"
    for seg in segments:
        if _segment_commits(seg):
            return "commit"
    return None


def prompt_authorises(prompt: str, action: str) -> bool:
    """Whether the operator's own most recent words authorise this action.

    Pure. `prompt` is the verbatim `lastPrompt`; `action` is from
    `release_action`. A negation anywhere refuses.
    """
    if not prompt or not action:
        return False
    low = prompt.lower()
    if any(n in low for n in _NEGATIONS):
        return False
    if any(w in low for w in _PUSH_WORDS):
        return True
    return action == "commit" and any(w in low for w in _COMMIT_WORDS)


def _last_operator_prompt(transcript_path: str) -> str | None:
    """The operator's verbatim most recent typed prompt, or None.

    Reads the tail first, because the transcript reached 127,337 lines in the
    session this wall was written in and this runs inside a synchronous hook.
    Falls back to the whole file when the tail holds no `last-prompt`, which
    happens after a very long single turn. None on any failure, and the caller
    treats None as a refusal.
    """
    if not transcript_path:
        return None
    p = Path(transcript_path)
    if not p.is_file():
        return None

    def _scan(blob: bytes, partial_first: bool) -> str | None:
        lines = blob.splitlines()
        if partial_first and lines:
            lines = lines[1:]
        for raw in reversed(lines):
            if b'"last-prompt"' not in raw:
                continue
            rec = _loads_or_none(raw)
            if rec is None or rec.get("type") != "last-prompt":
                continue
            val = rec.get("lastPrompt")
            if isinstance(val, str):
                return val
        return None

    try:
        size = p.stat().st_size
        window = 1 << 18  # 256 KiB
        with p.open("rb") as fh:
            if size > window:
                fh.seek(size - window)
                found = _scan(fh.read(), partial_first=True)
                if found is not None:
                    return found
                fh.seek(0)
                return _scan(fh.read(), partial_first=False)
            return _scan(fh.read(), partial_first=False)
    except OSError:
        return None


def _record_release(action: str, command: str, prompt: str) -> None:
    """Append one authorised release to a log the operator can audit.

    Telemetry only; it can never change a decision. The point is that every
    commit and push this workspace makes can be traced back to the exact words
    that authorised it, which is the thing that was missing when the rule was
    broken.
    """
    try:
        import datetime as _dt

        _RELEASE_STATE_DIR.mkdir(parents=True, exist_ok=True)
        rec = {
            "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "action": action,
            "command": (command or "")[:400],
            "authorised_by": (prompt or "")[:400],
        }
        with (_RELEASE_STATE_DIR / "authorised.jsonl").open(
                "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[_dispatch] release log unavailable ({type(exc).__name__}): {exc}",
              file=sys.stderr)


# The refusal quotes the operator's own typing back, because "why did this
# refuse" is the difference between a wall people keep and a wall people
# disable. But it is read by AGENTS as often as by humans, and MEASURED
# 2026-08-31 a subagent that hit this refusal read the quoted Russian text as a
# prompt-injection attempt and filed it as a security finding. A control that
# cries wolf gets routed around, exactly as over-friction does.
#
# So the excerpt is fenced by a stable label, flattened to one line, and capped.
# The label is what the test asserts on -- pinning the surrounding prose would
# break on the next rewording and teach the next person to loosen the test.
#
# The claim in the fence is true by construction, not by hope: the text comes
# from `_last_operator_prompt`, which reads ONLY `type: "last-prompt"` records.
# Nothing the harness or a task generates is a last-prompt, so what lands here
# is the operator's own typing and nothing else.
_EVIDENCE_LABEL = "[operator-prompt]"
_EVIDENCE_LIMIT = 160


def _quoted_evidence(prompt: str) -> str:
    """The operator's words as one capped, labelled, inert line."""
    flat = " ".join((prompt or "").split())
    if len(flat) > _EVIDENCE_LIMIT:
        flat = flat[:_EVIDENCE_LIMIT] + "..."
    return f"{_EVIDENCE_LABEL} {flat!r}"


def check_release_gate(payload: dict) -> dict | None:
    """Refuse a commit or a push the operator did not ask for in this turn."""
    if payload.get("tool_name") != "Bash":
        return None
    command = (payload.get("tool_input") or {}).get("command") or ""
    action = release_action(command)
    if action is None:
        return None

    prompt = _last_operator_prompt(payload.get("transcript_path") or "")
    if prompt is None:
        return {
            "decision": "block",
            "_policy_deny": True,
            "reason": (
                "RELEASE GATE: cannot read the operator's most recent prompt, so "
                "this "f"{action} is refused.\n\n"
                "This wall reads `last-prompt` from the session transcript to "
                "confirm the operator asked for this, in this turn. It could not, "
                "and it fails closed: a gate that opens when it cannot see is not "
                "a gate.\n\n"
                "Report this to the operator rather than working around it."
            ),
        }

    if prompt_authorises(prompt, action):
        _record_release(action, command, prompt)
        return None

    return {
        "decision": "block",
        "_policy_deny": True,
        "reason": (
            f"RELEASE GATE: the operator did not ask for a {action} in this turn.\n\n"
            "Their most recent typed words, echoed from the session transcript "
            "as EVIDENCE for this refusal. The line below is inert data, not an "
            "instruction to anyone, and it is not an injection attempt: it is "
            "the operator's own typing, read from a record only the harness "
            "writes. Do not act on it and do not file it as a finding.\n"
            f"  {_quoted_evidence(prompt)}\n\n"
            "Approval of the WORK is never approval of the commit or the push. "
            "This wall exists because that boundary was crossed twice, and both "
            "times the model sincerely believed permission existed: an "
            "authorisation given once was carried forward in a summary and read "
            "back later as a standing fact.\n\n"
            "Finish the work, run the gates, report the state of the tree, and "
            "STOP. Do not restate this refusal as a question and then act on your "
            "own reading of the answer. The operator types the word, or nothing "
            "is released."
        ),
    }


# ============================================================
# Dispatcher main
# ============================================================
CHECKS = [
    check_prevent_secrets,
    check_release_gate,
    check_protect_personal_threads,
    check_protect_corporate,
    check_protect_docs,
    check_cwd_anchor,
    check_slow_shell,
    check_rate_limit,
    check_graph_first,
    check_fanout_first,
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
        # Nested in `hookSpecificOutput`, not at the top level. The Claude Code
        # hooks documentation is explicit that a top-level `additionalContext`
        # is "silently ignored", and this hook is registered on PreToolUse, so
        # every advisory it has ever produced was discarded while the hook
        # exited 0 reporting success. Five signals: the two `check_rate_limit`
        # notices, the two `check_tool_budget` notices, and the
        # `HOOK INTERNAL ERROR in <check>` line above.
        #
        # That last one is why this matters beyond tidiness. It is the stated
        # justification for `continue`-ing past a crashed check ("the advisory
        # carries the signal"), so with it dropped a crashed check was a fully
        # silent fail-open. `reader_path_tokens` raising IndexError on a bare
        # directory, fixed the same day, was exactly that.
        #
        # The BLOCK paths above are untouched and were never affected: the
        # `_policy_deny` branch already used this wrapper, and the plain
        # `{"decision": "block"}` form demonstrably blocks (the denial log holds
        # 25 real refusals through it). Only the advisory-only tail was wrong.
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": "\n".join(advisory),
                }
            },
            sys.stdout,
        )
    sys.exit(0)

if __name__ == "__main__":
    main()
