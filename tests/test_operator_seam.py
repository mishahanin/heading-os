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


def _scan(text: str, rel: str):
    """(offending code lines, how many code lines were inspected).

    Split out of the test below so the detector can be run over text this file
    owns. A guard whose only case is "the tree is clean" reports the same green
    when it has stopped detecting anything; see the two tests under it.
    """
    offenders = []
    inspected = 0
    for lineno, line in _code_lines(text):
        inspected += 1
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
    return offenders, inspected


def test_no_personal_identity_literal_in_engine():
    """No personal slug/org literal survives in any fixed engine file's code.

    The v0.5.0 close-out removed the operator-identity compatibility shim; identity
    now resolves through scripts/utils/operator_identity.py, so a bare personal literal in
    these files is a regression. Category-c prose (single-line docstrings,
    argparse/usage examples, trailing inline comments) is still allowed."""
    offenders = []
    inspected = 0
    for rel in FIXED_FILES:
        found, n = _scan((ROOT / rel).read_text(encoding="utf-8"), rel)
        offenders.extend(found)
        inspected += n
    # Corpus floor: 3339 code lines reached the regex on 2026-08-26, so 2000 is a
    # safe floor that survives retiring a file or two. Without it, a drift in
    # _code_lines (say the triple-quote fence tracking never leaves in_block)
    # would yield zero lines, leave offenders empty, and pass while checking nothing.
    assert inspected >= 2000, f"only {inspected} engine code lines inspected"
    assert not offenders, (
        "personal operator-identity literal in engine code (identity must resolve "
        "through scripts/utils/operator_identity.py):\n" + "\n".join(offenders)
    )


# The corpus floor above proves the WALK still reaches code lines. It proves
# nothing about the DETECTOR: a mistyped `_PERSONAL_RE`, or an allowlist clause
# widened until it swallows every hit, leaves `offenders` empty over three
# thousand inspected lines and the assertion passes while checking nothing. The
# two cases below run the real scanner over text this file owns, so the detector
# has to keep both halves of its answer.

_PLANTED = (
    "import os\n"
    "\n"
    'DEFAULT_OWNER = "misha-hanin"\n'
    'GITHUB_ORG = "mishahanin"\n'
)

_ALLOWLISTED = (
    "import os\n"
    'PARSER.add_argument("--org", help="the org, e.g. mishahanin")\n'
    "SLUG = operator_slug()  # was hardcoded to misha-hanin before v0.5.0\n"
    '"""One-line docstring naming misha-hanin."""\n'
)


def test_the_seam_detector_flags_a_planted_literal():
    """The true negative. Without it the guard cannot fail for the right reason."""
    offenders, inspected = _scan(_PLANTED, "planted.py")
    assert inspected == 3, f"the scanner skipped a code line: {inspected}"
    assert len(offenders) == 2, f"the detector missed a planted literal: {offenders}"
    assert "misha-hanin" in offenders[0] and "mishahanin" in offenders[1]


def test_the_seam_detector_leaves_the_allowlisted_prose_alone():
    """And the other half: a detector that flags everything is switched off."""
    offenders, inspected = _scan(_ALLOWLISTED, "allowed.py")
    assert inspected == 4, f"the allowlisted lines never reached the regex: {inspected}"
    assert offenders == [], f"category-c prose was flagged as code: {offenders}"


def test_bare_user_slug_default_removed():
    """The bridge user_slug default is no longer the bare literal 'misha'."""
    cfg = (ROOT / "scripts/bridge_daemon/config.py").read_text(encoding="utf-8")
    assert '"user_slug": "misha"' not in cfg
    assert "'user_slug': 'misha'" not in cfg
