"""Tests for the operator identity seam (F-4.1).

Covers:
  - env-var precedence (highest tier)
  - overlay/file precedence over the generic default
  - generic default on an unconfigured clone
  - the workspace.py compatibility shim (established instance -> legacy literal;
    fresh clone -> generic), scheduled for removal in v0.5.0
  - a regression guard asserting no operator-identity default literal survives
    in the load-bearing engine sites, outside the operator_identity_default()
    shim and allowlisted public-author identity.
"""
import re
from pathlib import Path

import pytest

import scripts.utils.operator as operator
import scripts.utils.workspace as workspace

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_operator_env(monkeypatch):
    """Clear operator env vars and the module cache around every test."""
    for env in operator._ENV_KEYS.values():
        monkeypatch.delenv(env, raising=False)
    operator._reset_cache()
    yield
    operator._reset_cache()


# --------------------------------------------------------------------------
# Precedence
# --------------------------------------------------------------------------

def test_generic_default_on_unconfigured_clone(monkeypatch):
    """No operator.yaml, no env -> the neutral generic identity."""
    monkeypatch.setattr(operator, "_resolve_file", lambda: (operator._EXAMPLE_PATH, False))
    operator._reset_cache()
    op = operator.get_operator()
    assert op["slug"] == "operator"
    assert op["name"] == "Operator"
    assert operator.operator_is_default() is True
    assert operator.operator_org() == ""


def test_overlay_file_wins_over_generic(monkeypatch, tmp_path):
    """A real operator.yaml supplies identity and marks the instance configured."""
    f = tmp_path / "operator.yaml"
    f.write_text(
        "name: Ada Lovelace\nslug: ada-lovelace\ngithub_org: adalovelace\n"
        "voice_reference: reference/ada-voice.md\nemail: ada@example.com\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(operator, "_resolve_file", lambda: (f, True))
    operator._reset_cache()
    op = operator.get_operator()
    assert op["slug"] == "ada-lovelace"
    assert op["github_org"] == "adalovelace"
    assert operator.operator_is_default() is False


def test_env_wins_over_overlay(monkeypatch, tmp_path):
    """The HEADING_OS_OPERATOR_* env tier overrides the file tier."""
    f = tmp_path / "operator.yaml"
    f.write_text("name: Ada\nslug: ada-lovelace\ngithub_org: adalovelace\n", encoding="utf-8")
    monkeypatch.setattr(operator, "_resolve_file", lambda: (f, True))
    monkeypatch.setenv("HEADING_OS_OPERATOR_SLUG", "grace-hopper")
    monkeypatch.setenv("HEADING_OS_OPERATOR_GITHUB_ORG", "gracehopper")
    operator._reset_cache()
    op = operator.get_operator()
    assert op["slug"] == "grace-hopper"
    assert op["github_org"] == "gracehopper"
    # File still supplies the name (env did not override it).
    assert op["name"] == "Ada"


def test_never_raises_on_bad_yaml(monkeypatch, tmp_path):
    """A malformed operator.yaml degrades to the generic default, never raises."""
    f = tmp_path / "operator.yaml"
    f.write_text("this: : : not valid yaml\n  - broken", encoding="utf-8")
    monkeypatch.setattr(operator, "_resolve_file", lambda: (f, True))
    operator._reset_cache()
    op = operator.get_operator()
    assert op["slug"] == "operator"


# --------------------------------------------------------------------------
# Compatibility shim (workspace.operator_identity_default)
# --------------------------------------------------------------------------

def test_shim_established_instance_returns_legacy(monkeypatch):
    """Unconfigured operator + established instance -> legacy literal + warning."""
    monkeypatch.setattr(operator, "_resolve_file", lambda: (operator._EXAMPLE_PATH, False))
    operator._reset_cache()
    monkeypatch.setattr(workspace, "_is_established_instance", lambda: True)
    workspace._SHIM_WARNED.clear()
    with pytest.warns(DeprecationWarning):
        assert workspace.operator_identity_default("slug", "misha-hanin") == "misha-hanin"


def test_shim_fresh_clone_returns_generic(monkeypatch):
    """Unconfigured operator + fresh clone (no admin.json) -> generic value."""
    monkeypatch.setattr(operator, "_resolve_file", lambda: (operator._EXAMPLE_PATH, False))
    operator._reset_cache()
    monkeypatch.setattr(workspace, "_is_established_instance", lambda: False)
    workspace._SHIM_WARNED.clear()
    assert workspace.operator_identity_default("slug", "misha-hanin") == "operator"


def test_shim_configured_operator_wins(monkeypatch, tmp_path):
    """A configured operator.yaml bypasses the shim entirely (no legacy, no warn)."""
    f = tmp_path / "operator.yaml"
    f.write_text("slug: ada-lovelace\n", encoding="utf-8")
    monkeypatch.setattr(operator, "_resolve_file", lambda: (f, True))
    operator._reset_cache()
    monkeypatch.setattr(workspace, "_is_established_instance", lambda: True)
    workspace._SHIM_WARNED.clear()
    assert workspace.operator_identity_default("slug", "misha-hanin") == "ada-lovelace"


# --------------------------------------------------------------------------
# Regression guard: no operator-identity default literal outside the shim
# --------------------------------------------------------------------------

# The load-bearing engine sites de-personalized in F-4.1. Every personal
# identity slug/org literal in these files must now be an argument to
# operator_identity_default() (the guarded, v0.5.0-timeboxed compatibility shim),
# never a bare default.
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


def test_no_personal_identity_default_outside_shim():
    """Every personal slug/org literal in a fixed file's code is a shim arg."""
    offenders = []
    for rel in FIXED_FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for lineno, line in _code_lines(text):
            if not _PERSONAL_RE.search(line):
                continue
            # Allowlisted: the compatibility-shim call carrying the legacy literal.
            if "operator_identity_default(" in line:
                continue
            # Allowlisted category-c prose that lives on a code line: single-line
            # docstrings and argparse/usage examples (the plan preserves these).
            if '"""' in line or "'''" in line or "e.g." in line or "help=" in line:
                continue
            # Allowlisted: a trailing inline comment carrying the token (prose).
            code = line.split("#", 1)[0]
            if not _PERSONAL_RE.search(code):
                continue
            offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "operator-identity default literal outside operator_identity_default():\n"
        + "\n".join(offenders)
    )


def test_bare_user_slug_default_removed():
    """The bridge user_slug default is no longer the bare literal 'misha'."""
    cfg = (ROOT / "scripts/bridge_daemon/config.py").read_text(encoding="utf-8")
    assert '"user_slug": "misha"' not in cfg
    assert "'user_slug': 'misha'" not in cfg
