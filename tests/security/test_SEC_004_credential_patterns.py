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
    content = read_file_content(scripts_dir / "secret-scanner.py")
    # Must have a pattern that matches fc- followed by alphanumeric (not just hex)
    assert re.search(r'fc-\[.*[Aa].*[Zz]', content), (
        "secret-scanner.py Firecrawl pattern must match alphanumeric characters, not just hex"
    )


def test_secret_scanner_detects_google_oauth(scripts_dir):
    """secret-scanner.py must detect Google OAuth tokens."""
    content = read_file_content(scripts_dir / "secret-scanner.py")
    assert "ya29" in content, (
        "secret-scanner.py must have a pattern for Google OAuth tokens (ya29.*)"
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


def _load_module_patterns(rel_path: str):
    """Import a scanner module by file path and return its SECRET_PATTERNS list."""
    spec = importlib.util.spec_from_file_location("_scanmod_" + rel_path.replace("/", "_"), str(_ROOT / rel_path))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return getattr(mod, "SECRET_PATTERNS", [])


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
