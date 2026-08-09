"""Tests for the operator identity seam (F-4.1).

Covers:
  - env-var precedence (highest tier)
  - overlay/file precedence over the generic default
  - generic default on an unconfigured clone
  - configured operator_identity.yaml resolves the real identity through the seam (the
    path every de-shimmed call site relies on as of v0.5.0)
  - a regression guard asserting no personal operator-identity literal survives
    in the load-bearing engine sites (the shim is gone as of v0.5.0; identity
    resolves through scripts/utils/operator_identity.py, so no personal literal may appear
    in engine code).
"""
import re
from pathlib import Path

import pytest

import scripts.utils.operator_identity as operator_identity

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_operator_env(monkeypatch):
    """Clear operator env vars and the module cache around every test."""
    for env in operator_identity._ENV_KEYS.values():
        monkeypatch.delenv(env, raising=False)
    operator_identity._reset_cache()
    yield
    operator_identity._reset_cache()


# --------------------------------------------------------------------------
# Precedence
# --------------------------------------------------------------------------

def test_generic_default_on_unconfigured_clone(monkeypatch):
    """No operator_identity.yaml, no env -> the neutral generic identity."""
    monkeypatch.setattr(operator_identity, "_resolve_file", lambda: (operator_identity._EXAMPLE_PATH, False))
    operator_identity._reset_cache()
    op = operator_identity.get_operator()
    assert op["slug"] == "operator"
    assert op["name"] == "Operator"
    assert operator_identity.operator_is_default() is True
    assert operator_identity.operator_org() == ""


def test_overlay_file_wins_over_generic(monkeypatch, tmp_path):
    """A real operator_identity.yaml supplies identity and marks the instance configured."""
    f = tmp_path / "operator_identity.yaml"
    f.write_text(
        "name: Ada Lovelace\nslug: ada-lovelace\ngithub_org: adalovelace\n"
        "voice_reference: reference/ada-voice.md\nemail: ada@example.com\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(operator_identity, "_resolve_file", lambda: (f, True))
    operator_identity._reset_cache()
    op = operator_identity.get_operator()
    assert op["slug"] == "ada-lovelace"
    assert op["github_org"] == "adalovelace"
    assert operator_identity.operator_is_default() is False


def test_env_wins_over_overlay(monkeypatch, tmp_path):
    """The HEADING_OS_OPERATOR_* env tier overrides the file tier."""
    f = tmp_path / "operator_identity.yaml"
    f.write_text("name: Ada\nslug: ada-lovelace\ngithub_org: adalovelace\n", encoding="utf-8")
    monkeypatch.setattr(operator_identity, "_resolve_file", lambda: (f, True))
    monkeypatch.setenv("HEADING_OS_OPERATOR_SLUG", "grace-hopper")
    monkeypatch.setenv("HEADING_OS_OPERATOR_GITHUB_ORG", "gracehopper")
    operator_identity._reset_cache()
    op = operator_identity.get_operator()
    assert op["slug"] == "grace-hopper"
    assert op["github_org"] == "gracehopper"
    # File still supplies the name (env did not override it).
    assert op["name"] == "Ada"


def test_never_raises_on_bad_yaml(monkeypatch, tmp_path):
    """A malformed operator_identity.yaml degrades to the generic default, never raises."""
    f = tmp_path / "operator_identity.yaml"
    f.write_text("this: : : not valid yaml\n  - broken", encoding="utf-8")
    monkeypatch.setattr(operator_identity, "_resolve_file", lambda: (f, True))
    operator_identity._reset_cache()
    op = operator_identity.get_operator()
    assert op["slug"] == "operator"


# --------------------------------------------------------------------------
# Configured-identity resolution (post-shim; v0.5.0)
# --------------------------------------------------------------------------

def test_configured_operator_yaml_resolves_identity(monkeypatch, tmp_path):
    """A written operator_identity.yaml resolves the real identity through the seam - the
    path every de-shimmed call site now relies on (no shim, no legacy literal)."""
    f = tmp_path / "operator_identity.yaml"
    f.write_text(
        "name: Ada Lovelace\nslug: ada-lovelace\ngithub_org: adalovelace\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(operator_identity, "_resolve_file", lambda: (f, True))
    operator_identity._reset_cache()
    assert operator_identity.operator_slug() == "ada-lovelace"
    assert operator_identity.operator_org() == "adalovelace"
    assert operator_identity.operator_is_default() is False


# --------------------------------------------------------------------------
# Regression guard: no personal operator-identity literal in engine code
# --------------------------------------------------------------------------

# The load-bearing engine sites de-personalized in F-4.1 and fully cut over to the
# operator seam in v0.5.0. No personal identity slug/org literal may appear in
# these files at all - identity resolves through scripts/utils/operator_identity.py.
FIXED_FILES = [
    "scripts/utils/workspace.py",
    "scripts/bridge_daemon/config.py",
    "scripts/bridge-daemon.py",
    "scripts/bridge_daemon/sources/contacts.py",
    "scripts/aggregate-crm.py",
    "scripts/setup.py",
    "scripts/publish-corporate.py",
    "scripts/merge-contacts.py",
    "scripts/transfer-contact.py",
    "scripts/bridge_daemon/terminal.py",
]

# Personal operator-identity slug / org literals (NOT public-author identity:
# "Misha Hanin", author emails, and github.com/mishahanin repo URLs use a
# different shape and are preserved verbatim).
_PERSONAL_RE = re.compile(r"\bmisha-hanin\b|\bmishahanin\b")


def _code_lines(text: str):
    """Yield (lineno, line) for code lines only: skips blank lines, full-line
    comments, and triple-quoted docstring/comment blocks (category-c prose)."""
    in_block = False
    fence = None
    for i, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if in_block:
            if fence in stripped:
                in_block = False
            continue
        # Enter a triple-quoted block that does not also close on the same line.
        for f in ('"""', "'''"):
            if stripped.count(f) == 1:
                in_block = True
                fence = f
                break
        if in_block or not stripped or stripped.startswith("#"):
            continue
        yield i, raw


def test_no_personal_identity_literal_in_engine():
    """No personal slug/org literal survives in any fixed engine file's code.

    The v0.5.0 close-out removed the operator-identity compatibility shim; identity
    now resolves through scripts/utils/operator_identity.py, so a bare personal literal in
    these files is a regression. Category-c prose (single-line docstrings,
    argparse/usage examples, trailing inline comments) is still allowed."""
    offenders = []
    for rel in FIXED_FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for lineno, line in _code_lines(text):
            if not _PERSONAL_RE.search(line):
                continue
            # Allowlisted category-c prose that lives on a code line: single-line
            # docstrings and argparse/usage examples.
            if '"""' in line or "'''" in line or "e.g." in line or "help=" in line:
                continue
            # Allowlisted: a trailing inline comment carrying the token (prose).
            code = line.split("#", 1)[0]
            if not _PERSONAL_RE.search(code):
                continue
            offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "personal operator-identity literal in engine code (identity must resolve "
        "through scripts/utils/operator_identity.py):\n" + "\n".join(offenders)
    )


def test_bare_user_slug_default_removed():
    """The bridge user_slug default is no longer the bare literal 'misha'."""
    cfg = (ROOT / "scripts/bridge_daemon/config.py").read_text(encoding="utf-8")
    assert '"user_slug": "misha"' not in cfg
    assert "'user_slug': 'misha'" not in cfg
