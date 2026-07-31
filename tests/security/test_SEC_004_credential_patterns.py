#!/usr/bin/env python3
"""SEC-004: Verify secret scanners detect all credential formats.

Vulnerability: Missing patterns allow specific credential types to slip through.
Expected safe behavior: Both scanners detect Firecrawl and Google OAuth tokens.
"""

import re
import subprocess
import sys

import pytest

from tests.security.conftest import read_file_content, extract_patterns_from_scanner


# Test credential strings that MUST be detected
TEST_CREDENTIALS = {
    "Firecrawl API Key (alphanumeric)": "fc-aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789",  # pragma: allowlist secret
    "Google OAuth Token": "ya29.a0ARrdaM_EXAMPLE_TOKEN_THAT_IS_LONG_ENOUGH_TO_MATCH_FIFTY_CHARS_EASILY",  # pragma: allowlist secret
}


def test_secret_scanner_detects_firecrawl(scripts_dir):
    """secret-scanner.py must detect Firecrawl API keys with alphanumeric chars."""
    # The vocabulary moved to scripts/utils/secret_patterns.py; the assertion is
    # unchanged, only its target. The _dispatch.py twins below are unaffected.
    content = read_file_content(scripts_dir / "utils" / "secret_patterns.py")
    # Must have a pattern that matches fc- followed by alphanumeric (not just hex)
    assert re.search(r'fc-\[.*[Aa].*[Zz]', content), (
        "secret_patterns.py Firecrawl pattern must match alphanumeric characters, not just hex"
    )


def test_secret_scanner_detects_google_oauth(scripts_dir):
    """secret-scanner.py must detect Google OAuth tokens."""
    # The vocabulary moved to scripts/utils/secret_patterns.py; the assertion is
    # unchanged, only its target. The _dispatch.py twins below are unaffected.
    content = read_file_content(scripts_dir / "utils" / "secret_patterns.py")
    assert "ya29" in content, (
        "secret_patterns.py must have a pattern for Google OAuth tokens (ya29.*)"
    )


def test_prevent_secrets_detects_firecrawl(hooks_dir):
    """PreToolUse secret patterns must detect Firecrawl API keys with alphanumeric chars.

    After the 2026-05-12 perf-v2 consolidation, the live patterns moved from
    prevent-secrets.py (now a shim) to _dispatch.py. We verify the patterns
    in their authoritative location.
    """
    content = read_file_content(hooks_dir / "_dispatch.py")
    assert re.search(r'fc-\[.*[Aa].*[Zz]', content), (
        "_dispatch.py Firecrawl pattern must match alphanumeric characters, not just hex"
    )


def test_prevent_secrets_detects_google_oauth(hooks_dir):
    """PreToolUse secret patterns must detect Google OAuth tokens.

    After the 2026-05-12 perf-v2 consolidation, the live patterns moved from
    prevent-secrets.py (now a shim) to _dispatch.py. We verify the patterns
    in their authoritative location.
    """
    content = read_file_content(hooks_dir / "_dispatch.py")
    assert "ya29" in content, (
        "_dispatch.py must have a pattern for Google OAuth tokens (ya29.*)"
    )


# ---------------------------------------------------------------------------
# Behavioural regression: env-password placeholder false positive (2026-05-31)
#
# Context: a hijacked core.hooksPath had been bypassing all pre-commit hooks.
# On restoration, secret-scanner.py flagged EXCHANGE_PASSWORD=your-email-password
# in docs/ZERO-TO-HERO-DEPLOYMENT.html -- a placeholder, not a secret -- which
# would block any commit touching that doc. The env-password pattern was given a
# placeholder negative-lookahead (mirroring the markdown-password pattern). These
# tests exercise the scanner through its CLI, not its regex source, so they hold
# even if the pattern is rewritten.
#
# Key/value strings are assembled from fragments so this test file carries no
# literal KEY=secret substring -- it is scanned by the same hooks it verifies.
# ---------------------------------------------------------------------------

_ENV_KEY = "EXCHANGE_" + "PASSWORD"


def _run_scanner(scripts_dir, tmp_path, filename, content):
    """Run secret-scanner.py on a temp file. Return exit code (0=clean, 1=secret)."""
    target = tmp_path / filename
    target.write_text(content, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(scripts_dir / "secret-scanner.py"), str(target)],
        capture_output=True,
        text=True,
    )
    return result.returncode


@pytest.mark.parametrize("placeholder", [
    "your-email-password",
    "your_password",
    "changeme123",
    "<your-password>",
    "ExampleValue",
    "placeholder-secret",
    "redacted-value",
])
def test_env_password_placeholder_not_flagged(scripts_dir, tmp_path, placeholder):
    """Placeholder values in .env-style password assignments must NOT be flagged."""
    content = f"{_ENV_KEY}={placeholder}\n"
    assert _run_scanner(scripts_dir, tmp_path, "placeholder.env", content) == 0, (
        f"placeholder {placeholder!r} should not be flagged as a secret"
    )


def test_env_password_real_value_still_flagged(scripts_dir, tmp_path):
    """Regression guard: the placeholder fix must not weaken real-value detection."""
    real_value = "Hunter2" + "!" + "xKQ9mZ"  # assembled from parts; not a real credential
    content = f"{_ENV_KEY}={real_value}\n"
    assert _run_scanner(scripts_dir, tmp_path, "real.env", content) == 1, (
        "a real env password value must still be flagged"
    )


# ---------------------------------------------------------------------------
# F-L4: secret-scanner.py threshold alignment with _dispatch.py ({16,} not {20,})
#
# The write-time hook (_dispatch.py) uses {16,} for 7 prefix patterns; the
# commit-time scanner (secret-scanner.py) used {20,}. A key of length 16-19
# chars after the prefix was caught at write time but slipped past the commit
# scanner. These tests assert both gates agree at the 16-char boundary.
#
# Test material is assembled at runtime (prefix + "A"*N) so no literal API-key
# string lives in this file, which is itself scanned by the same hooks.
# ---------------------------------------------------------------------------

import importlib.util  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

_ROOT = _Path(__file__).resolve().parent.parent.parent

# (prefix, key-material alphabet sample char) for the 7 aligned prefixes.
_ALIGNED_PREFIXES = ["sk-ant-", "pplx-", "r8_", "fc-", "ctx7sk-", "ghp_", "gho_"]


def _load_scanner_module(rel_path: str):
    """Import a scanner module by file path and return the live module.

    One loader, deliberately. An earlier revision of this file grew a second,
    subtly different one (no SystemExit guard) beside it, and two loaders in one
    file drift. The SystemExit guard is the safe superset: a module with a
    `__main__` guard never raises it, and one whose import parses argv would
    otherwise take the test session down with it.
    """
    spec = importlib.util.spec_from_file_location("_scanmod_" + rel_path.replace("/", "_"), str(_ROOT / rel_path))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


def _load_module_patterns(rel_path: str):
    """The runtime SECRET_PATTERNS list of a scanner module."""
    return getattr(_load_scanner_module(rel_path), "SECRET_PATTERNS", [])


@pytest.mark.parametrize("prefix", _ALIGNED_PREFIXES)
def test_scanner_catches_16_char_suffix(scripts_dir, tmp_path, prefix):
    """secret-scanner.py must flag a 16-char suffix via its CLI (threshold {16,})."""
    sample = prefix + ("A" * 16)
    assert _run_scanner(scripts_dir, tmp_path, "k16.txt", sample + "\n") == 1, (
        f"secret-scanner.py failed to flag {prefix!r} + 16 chars; threshold must be {{16,}} (F-L4)"
    )


@pytest.mark.parametrize("prefix", _ALIGNED_PREFIXES)
def test_dispatch_catches_16_char_suffix(prefix):
    """_dispatch.py must detect a 16-char suffix (baseline confirmation of {16,})."""
    sample = prefix + ("A" * 16)
    patterns = _load_module_patterns(".claude/hooks/_dispatch.py")
    assert any(pat.search(sample) for pat, _ in patterns), (
        f"_dispatch.py failed to detect {prefix!r} + 16 chars (baseline broken)"
    )


# ---------------------------------------------------------------------------
# F-L3: JWT, PEM private-key, and connection-string patterns in both scanners
#
# All sample tokens are assembled from fragments at runtime so this file (which
# is scanned by the same hooks) carries no literal JWT / PEM / credential URI.
# ---------------------------------------------------------------------------

def _jwt_sample():
    return "eyJ" + ("A" * 14) + "." + ("B" * 14) + "." + ("C" * 14)


def _pem_samples():
    begin = "-----" + "BEGIN "
    end = " PRIVATE KEY" + "-----"
    return [
        begin + "RSA" + end,
        begin + "EC" + end,
        begin + "OPENSSH" + end,
        "-----" + "BEGIN " + "PRIVATE KEY" + "-----",  # bare PKCS#8
    ]


def _conn_string_samples():
    sep = "://"
    return [
        "postgresql" + sep + "dbuser" + ":" + "s3cr3tpass" + "@" + "db.example.com:5432/mydb",
        "mysql" + sep + "admin" + ":" + "hunter2val" + "@" + "127.0.0.1/prod",
    ]


def _fl3_samples():
    out = [("JWT bearer token", _jwt_sample())]
    out += [(f"PEM key header #{i}", s) for i, s in enumerate(_pem_samples())]
    out += [(f"connection string #{i}", s) for i, s in enumerate(_conn_string_samples())]
    return out


@pytest.mark.parametrize("desc,sample", _fl3_samples())
def test_secret_scanner_detects_new_pattern(scripts_dir, tmp_path, desc, sample):
    """secret-scanner.py must flag JWT / PEM / connection-string secrets (F-L3)."""
    assert _run_scanner(scripts_dir, tmp_path, "fl3.txt", sample + "\n") == 1, (
        f"secret-scanner.py missed: {desc} (F-L3)"
    )


@pytest.mark.parametrize("desc,sample", _fl3_samples())
def test_dispatch_detects_new_pattern(desc, sample):
    """_dispatch.py must detect JWT / PEM / connection-string secrets (F-L3)."""
    patterns = _load_module_patterns(".claude/hooks/_dispatch.py")
    assert any(pat.search(sample) for pat, _ in patterns), (
        f"_dispatch.py missed: {desc} (F-L3)"
    )


# Placeholder connection strings in docs/help text (user:pass@) must NOT be flagged.
_PLACEHOLDER_URIS = [
    "http" + "://" + "user" + ":" + "pass" + "@" + "host:port",
    "http" + "://" + "username" + ":" + "password" + "@" + "host",
    "https" + "://" + "user" + ":" + "password" + "@" + "proxy",
]


@pytest.mark.parametrize("sample", _PLACEHOLDER_URIS)
def test_scanner_ignores_placeholder_connection_string(scripts_dir, tmp_path, sample):
    """Documentation placeholder credential URIs (user/pass words) must NOT be flagged (F-L3)."""
    assert _run_scanner(scripts_dir, tmp_path, "ph.txt", sample + "\n") == 0, (
        f"placeholder URI {sample!r} should not be flagged as a real credential (F-L3)"
    )


# ---------------------------------------------------------------------------
# The anti-drift ratchet.
#
# _dispatch.py deliberately does NOT import the shared module: it is the
# blocking PreToolUse gate, and a guarded import that fell back to an empty
# pattern list would be a fail-open hole in a security control. The copy is the
# safe choice; the drift is what has to be made impossible to ship.
#
# Compared on the AST of each entry rather than on source text, so a reflowed
# line or a changed comment does not read as drift and a changed regex does.
#
# Not hypothetical. The copies drifted twice before this guard existed: the
# {16,} threshold (F-L4, above) and the placeholder lookahead on the
# environment-password entry, measured on 2026-07-31.
# ---------------------------------------------------------------------------

import ast  # noqa: E402


def _pattern_entries_from_source(rel_path: str):
    """[(description, regex_source, flags_dump)] parsed from a file's source.

    Parsed, never imported. _dispatch.py runs module-level code, and the
    property under test is a property of the source text either way.
    """
    path = _ROOT / rel_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "SECRET_PATTERNS":
                entries = []
                for elt in node.value.elts:
                    call, desc = elt.elts
                    flags = ast.dump(call.args[1]) if len(call.args) > 1 else ""
                    entries.append((desc.value, ast.dump(call.args[0]), flags))
                return entries
    raise AssertionError(f"SECRET_PATTERNS not found at module scope in {rel_path}")


def test_the_hook_and_the_shared_module_carry_the_same_patterns():
    """The one assertion that makes the embedded copy safe to keep."""
    shared = _pattern_entries_from_source("scripts/utils/secret_patterns.py")
    hook = _pattern_entries_from_source(".claude/hooks/_dispatch.py")

    assert len(shared) == len(hook), (
        f"pattern COUNT drifted: shared module has {len(shared)}, "
        f".claude/hooks/_dispatch.py has {len(hook)}")

    drifted = [
        (i, s[0], h[0]) for i, (s, h) in enumerate(zip(shared, hook, strict=True), 1) if s != h
    ]
    assert not drifted, "pattern entries drifted:\n  " + "\n  ".join(
        f"#{i}: shared={s!r} hook={h!r}" for i, s, h in drifted)


def test_the_ratchet_actually_compares_something():
    """A guard that silently parsed an empty list would pass forever."""
    shared = _pattern_entries_from_source("scripts/utils/secret_patterns.py")
    assert len(shared) >= 16
    assert all(desc and src for desc, src, _flags in shared)


def _required_substrings(rel_path: str) -> dict:
    """REQUIRED_SUBSTRING as a plain dict, parsed from source."""
    path = _ROOT / rel_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "REQUIRED_SUBSTRING":
                return {k.value: v.value for k, v in zip(node.value.keys, node.value.values, strict=True)}
    raise AssertionError(f"REQUIRED_SUBSTRING not found at module scope in {rel_path}")


def test_the_prefilter_is_identical_in_both_copies():
    """The prefilter is a second thing that must not drift, and it is more
    dangerous than the patterns: an entry that is not logically exact silently
    DISABLES a pattern rather than merely reporting a difference."""
    shared = _required_substrings("scripts/utils/secret_patterns.py")
    hook = _required_substrings(".claude/hooks/_dispatch.py")
    assert shared == hook, f"prefilter drifted: shared={shared} hook={hook}"


def test_every_prefilter_entry_names_a_real_pattern():
    """A typo in a description key would make the entry dead rather than wrong,
    which is the quiet failure. Keys must match a description that exists."""
    shared = _required_substrings("scripts/utils/secret_patterns.py")
    descriptions = {desc for desc, _src, _flags
                    in _pattern_entries_from_source("scripts/utils/secret_patterns.py")}
    unknown = set(shared) - descriptions
    assert not unknown, f"prefilter names patterns that do not exist: {unknown}"


@pytest.mark.parametrize("description,needle", [
    ("connection string with inline credentials", "://"),
])
def test_every_prefilter_entry_is_logically_exact(description, needle):
    """The entry is only safe if the pattern genuinely cannot match without the
    needle. Asserted against the real pattern rather than trusted, because a
    wrong entry turns a security control off without failing anything else."""
    patterns = {d: p for p, d in
                _load_module_patterns("scripts/utils/secret_patterns.py")}
    pattern = patterns[description]
    # Text that would otherwise be a match, with the needle removed.
    sample = "https" + "x-access-token" + ":" + "value" + "@" + "host"
    assert needle not in sample
    assert pattern.search(sample) is None, (
        f"{description!r} matched text containing no {needle!r}; the prefilter "
        f"entry would suppress a real finding")


@pytest.mark.parametrize("placeholder", [
    "your-email-password",
    "your_password",
    "changeme123",
    "<your-password>",
    "ExampleValue",
    "placeholder-secret",
    "redacted-value",
])
def test_dispatch_env_password_placeholder_not_flagged(placeholder):
    """The twin the suite was missing. Its absence is why the drift survived:
    the tests were asymmetric in exactly the place the code was."""
    patterns = _load_module_patterns(".claude/hooks/_dispatch.py")
    # Without this line the test is vacuously satisfiable: _load_module_patterns
    # ends in getattr(mod, "SECRET_PATTERNS", []), so a load failure yields an
    # empty list, no hits, and a green "nothing was flagged" that proves nothing.
    assert patterns, "no patterns loaded from _dispatch.py"
    line = _ENV_KEY + "=" + placeholder
    hits = [desc for pattern, desc in patterns if pattern.search(line)]
    assert not hits, f"placeholder {placeholder!r} flagged by the hook as {hits}"


def test_dispatch_env_password_real_value_still_flagged():
    """The reconciliation must not cost the hook its real-value detection."""
    patterns = _load_module_patterns(".claude/hooks/_dispatch.py")
    line = _ENV_KEY + "=" + "Hunter2" + "!" + "xKQ9mZ"
    hits = [desc for pattern, desc in patterns if pattern.search(line)]
    assert "Password in environment variable assignment" in hits


def test_pattern_descriptions_are_unique():
    """REQUIRED_SUBSTRING is keyed by description, so a duplicate description
    would apply one pattern's prefilter to another and disable it silently."""
    descriptions = [desc for desc, _src, _flags
                    in _pattern_entries_from_source("scripts/utils/secret_patterns.py")]
    duplicates = {d for d in descriptions if descriptions.count(d) > 1}
    assert not duplicates, f"duplicate pattern descriptions: {duplicates}"


# ---------------------------------------------------------------------------
# The runtime companion to the AST ratchet.
#
# The AST test above (test_the_hook_and_the_shared_module_carry_the_same_patterns)
# parses tree.body, matches only ast.Assign, and returns on the FIRST
# SECRET_PATTERNS binding. Three ordinary constructs defeat it while the
# runtime behaviour genuinely differs:
#
#   a) a second `SECRET_PATTERNS = [...]` (or `= []`) assigned later at module
#      scope. The AST walk returns on the first match and never sees the
#      rebind; the interpreter executes both assignments in order and keeps
#      the LAST one. Ratchet green, runtime list wrong (or empty).
#   b) `SECRET_PATTERNS += [...]` is an ast.AugAssign, not an ast.Assign, so
#      the walk's `isinstance(node, ast.Assign)` check never matches it at
#      all -- the entries it adds or drops are invisible to the AST test.
#   c) `re.compile(pattern, flags=re.IGNORECASE)` passes flags as a keyword
#      argument. The AST test reads flags positionally from `call.args[1]`,
#      so a keyword-only flags argument never appears in what it compares.
#
# This test instead imports (executes) both files via the existing
# _load_module_patterns helper and compares the actual compiled regex
# objects the interpreter produced -- pattern text, compiled flags bitmask,
# and description, in order. All three bypasses above change what this
# comparison sees, because it observes the runtime result, not the source
# shape.
# ---------------------------------------------------------------------------

def test_the_hook_and_the_shared_module_match_at_runtime():
    """Runtime companion to the AST ratchet. Closes bypasses (a) trailing
    rebind, (b) AugAssign, and (c) keyword-argument flags -- see module
    docstring above for how each defeats the AST-only comparison."""
    shared = _load_module_patterns("scripts/utils/secret_patterns.py")
    hook = _load_module_patterns(".claude/hooks/_dispatch.py")

    # Non-empty first, or a load failure on either side (getattr(..., [])
    # default) would make the equality below vacuously true.
    assert shared, "no patterns loaded from scripts/utils/secret_patterns.py"
    assert hook, "no patterns loaded from .claude/hooks/_dispatch.py"

    shared_sig = [(pat.pattern, pat.flags, desc) for pat, desc in shared]
    hook_sig = [(pat.pattern, pat.flags, desc) for pat, desc in hook]
    assert shared_sig == hook_sig, (
        "runtime SECRET_PATTERNS diverged between the shared module and the "
        "hook's embedded copy:\n"
        f"  shared={shared_sig!r}\n"
        f"  hook={hook_sig!r}"
    )


def test_the_prefilter_matches_at_runtime():
    """The same runtime companion for REQUIRED_SUBSTRING, which had only the
    AST guard and so carried all three bypasses the test above just closed.

    This structure is the more dangerous of the two: an entry whose needle is
    not logically exact does not merely mis-describe a pattern, it silently
    DISABLES it. One trailing rebind or one item-assignment after the literal
    is enough to add a needle that appears in no text, switching that pattern
    off inside the blocking gate while every source-level guard stays green.
    """
    shared = getattr(_load_scanner_module("scripts/utils/secret_patterns.py"),
                     "REQUIRED_SUBSTRING", {})
    hook = getattr(_load_scanner_module(".claude/hooks/_dispatch.py"),
                   "REQUIRED_SUBSTRING", {})

    # Non-empty first: an empty dict on both sides compares equal and proves
    # nothing, which is exactly the vacuous-green shape being guarded against.
    assert shared, "no REQUIRED_SUBSTRING loaded from scripts/utils/secret_patterns.py"
    assert hook, "no REQUIRED_SUBSTRING loaded from .claude/hooks/_dispatch.py"
    assert shared == hook, (
        "runtime REQUIRED_SUBSTRING diverged between the shared module and the "
        f"hook's embedded copy:\n  shared={shared!r}\n  hook={hook!r}"
    )


# ---------------------------------------------------------------------------
# The path-scoped allowance itself.
#
# _secrets_path_allowed decides which files the write-time gate does not scan
# at all, so a loosened anchor there is a hole no pattern test would see. The
# comment above SECRETS_ALLOW_DIR_SEGMENTS records that a substring match once
# bypassed the scan for any path merely CONTAINING the allowed text; nothing in
# the suite would have caught a regression back to it. The `myscripts/utils/`
# and backslash cases below are what makes that comment enforceable.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel_path,allowed", [
    ("scripts/utils/secret_patterns.py", True),
    ("/abs/root/scripts/utils/secret_patterns.py", True),
    ("scripts/utils/nested/secret_patterns.py", True),
    (".claude/hooks/_dispatch.py", True),
    ("myscripts/utils/secret_patterns.py", False),      # segment anchor
    ("outputs/scratch/secret_patterns.py", False),
    ("knowledge/notes/secret_patterns.py", False),
    ("outputs/scratch/_dispatch.py", False),
    ("mytests/security/planted.py", False),             # segment anchor
    ("tests/security/fixture.py", True),
])
def test_the_path_allowance_is_segment_anchored(rel_path, allowed):
    mod = _load_dispatch_module()
    assert mod._secrets_path_allowed(rel_path) is allowed, (
        f"{rel_path} was {'blocked' if allowed else 'allowed'}, "
        f"expected the opposite"
    )


def test_the_path_allowance_normalizes_backslashes():
    """A Windows-style path must resolve its basename off Windows too, or the
    allowance silently stops applying to the files it is meant to cover."""
    mod = _load_dispatch_module()
    assert mod._secrets_path_allowed(r"scripts\utils\secret_patterns.py") is True
    assert mod._secrets_path_allowed(r"outputs\scratch\secret_patterns.py") is False


# ---------------------------------------------------------------------------
# Control-flow tests for the mirrored prefilter, through check_prevent_secrets
# itself.
#
# All the prefilter tests above compare DATA -- the REQUIRED_SUBSTRING dicts,
# and needle exactness against a bare compiled pattern. None of them calls
# _scan_for_secrets or check_prevent_secrets, so none exercises the hook's own
# branch: `if needle is not None and needle not in text: continue`. Inverting
# that condition to `needle in text` silently turns OFF connection-string
# detection for every real connection string (the needle "://" IS present in
# every real one, so the inverted branch skips the pattern precisely when it
# should fire) while every existing test in this file stays green, because
# none of them drives a Write or Bash payload through the function that owns
# the branch.
# ---------------------------------------------------------------------------

def _load_dispatch_module():
    """The live hook module, so its functions can be called directly."""
    return _load_scanner_module(".claude/hooks/_dispatch.py")


def _assembled_connection_string():
    sep = "://"
    return "postgresql" + sep + "dbuser" + ":" + "s3cr3tpass" + "@" + "db.example.com:5432/mydb"


def test_check_prevent_secrets_blocks_write_with_connection_string():
    """Write path: a connection-string credential in file content must be
    blocked, with a reason naming the finding."""
    mod = _load_dispatch_module()
    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": "outputs/scratch/notes.txt",
            "content": "conn = " + repr(_assembled_connection_string()),
        },
    }
    result = mod.check_prevent_secrets(payload)
    assert result is not None, "connection string in Write content was not blocked"
    assert result["decision"] == "block"
    assert "connection string with inline credentials" in result["reason"]


def test_check_prevent_secrets_blocks_bash_with_connection_string():
    """Bash path (the `command` key, no file_path): same credential, same
    requirement."""
    mod = _load_dispatch_module()
    payload = {
        "tool_name": "Bash",
        "tool_input": {
            "command": "psql " + _assembled_connection_string(),
        },
    }
    result = mod.check_prevent_secrets(payload)
    assert result is not None, "connection string in Bash command was not blocked"
    assert result["decision"] == "block"
    assert "connection string with inline credentials" in result["reason"]
