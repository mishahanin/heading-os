"""A tool may not tell the operator more than its method established.

Two defects of one shape surfaced on 2026-08-12, hours apart:

`scripts/harness-audit.py` printed every `hooks.json` under the plugin cache
beneath the words "running in this session". The cache keeps superseded
versions, so it named `superpowers` 6.1.1 and 6.2.0 as two live SessionStart
hooks when the loader reads one. The method walked a directory; the sentence
claimed a session.

`scripts/turn-check.py` called `git diff` "the edits made in this turn" and the
Stop hook blocked a turn over a parallel session's deliberately-red TDD test.
The method read a working tree; the sentence claimed an author.

Neither was a logic bug. Both were a sentence wider than its evidence, and both
survived review because the sentence read as obviously true. Prose cannot guard
that, so this file does: a user-facing string that asserts session membership or
live execution has to come from a file that resolves it, and a NEW such string
has to be declared here on purpose. The registry is the point. Adding a claim
means answering "what establishes this?" while writing it, rather than after an
operator is misled by it.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Phrases that assert something a directory listing or a diff cannot show:
# that a thing is loaded RIGHT NOW, or that this session/turn is its author.
CLAIM_PHRASES = (
    "in this session",
    "in this turn",
    "this session",
    "running in",
)

SEARCHED = ("scripts", ".claude/hooks")

# path -> the identifier that must appear in it, naming what resolves the claim.
# A claim with no resolver is the defect this file exists to stop, so the value
# is never allowed to be empty.
DECLARED_CLAIMANTS = {
    "scripts/harness-audit.py": "active_install_paths",
    "scripts/turn-check.py": "session_scope",
    ".claude/hooks/turn-check.py": "transcript_path",
}

# The detector is deliberately wide, because a defect of this shape is written
# in whatever words the author reached for, not in a fixed phrase. Width costs
# false positives, and a false positive left unclassified rots the guard into
# noise people learn to override. So every match is classified exactly once:
# either it makes a coverage claim and names its resolver above, or it says why
# it is not a coverage claim here. Both answers are cheap; neither is silence.
NON_SCOPE_CLAIMS = {
    "scripts/fireside-bot.py":
        "'taking from this session' is the closing line of a fireside invitation; "
        "session there means the meeting, not a Claude Code session",
    "scripts/router-accuracy-nightly.py":
        "'declared for this session' reports the SENSITIVE_MODE flag, resolved by "
        "sensitivity_is_declared(); it describes a mode, not a set of files covered",
    "scripts/utils/observability.py":
        "'will NOT be recorded this session' reports this process's own degraded "
        "Langfuse state, which the process observed directly",
    ".claude/hooks/checkpoint-save.py":
        "'the work this session was doing' appears in a handoff the hook writes FOR "
        "the session whose transcript it was handed; the subject is its own caller",
}


def _docstrings(tree: ast.AST) -> set[int]:
    """Ids of the string nodes that are docstrings, which explain rather than
    assert: this file's own module docstring quotes both defects verbatim."""
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            found.add(id(first.value))
    return found


def _claim_strings(path: Path) -> list[str]:
    """User-facing literals in one file that make a scope claim."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return []
    skip = _docstrings(tree)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip:
            continue
        low = node.value.lower()
        if any(phrase in low for phrase in CLAIM_PHRASES):
            out.append(node.value)
    return out


def _claimants() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for tree_name in SEARCHED:
        base = ROOT / tree_name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            claims = _claim_strings(path)
            if claims:
                found[path.relative_to(ROOT).as_posix()] = claims
    return found


def test_every_scope_claim_is_declared_with_what_resolves_it():
    """A new tool that says "running in this session" must say how it knows.

    The fix when this fails is usually not to add a line here. It is to make the
    tool resolve the claim (`scripts/utils/session_scope.py` for authorship, the
    loader's own record for activation) and THEN declare it, or to reword the
    sentence down to what the method actually shows. Only a match that turns out
    not to be a coverage claim at all belongs in NON_SCOPE_CLAIMS, with the
    reason written out.
    """
    classified = set(DECLARED_CLAIMANTS) | set(NON_SCOPE_CLAIMS)
    undeclared = sorted(set(_claimants()) - classified)
    assert not undeclared, (
        "these files assert session membership or live execution in user-facing "
        f"text without being classified: {undeclared}"
    )


def test_no_file_is_classified_both_ways():
    """A file cannot both back a claim and disclaim making one; the overlap is
    how a real claimant hides behind an exemption written for a neighbour."""
    both = sorted(set(DECLARED_CLAIMANTS) & set(NON_SCOPE_CLAIMS))
    assert not both, both


@pytest.mark.parametrize("path,reason", sorted(NON_SCOPE_CLAIMS.items()))
def test_an_exemption_carries_a_real_reason(path, reason):
    """An exemption with a thin reason is an exemption nobody re-examines."""
    assert (ROOT / path).is_file(), f"{path} is exempted but is gone"
    assert len(reason) > 40, f"{path}: the reason has to say why, not just assert"


@pytest.mark.parametrize("path,resolver", sorted(DECLARED_CLAIMANTS.items()))
def test_a_declared_claimant_still_carries_its_resolver(path, resolver):
    """The registry entry has to stay true. Deleting the narrowing while leaving
    the sentence in place is exactly how both defects were written."""
    target = ROOT / path
    assert target.is_file(), f"{path} is declared as a claimant but is gone"
    assert resolver in target.read_text(encoding="utf-8"), (
        f"{path} still claims session scope but no longer mentions {resolver!r}, "
        f"so the claim is unbacked again"
    )


def test_the_registry_has_no_stale_entries():
    """A file that stopped claiming should leave the registry, or the next reader
    trusts a guard over something it no longer guards."""
    claiming = set(_claimants())
    stale = sorted((set(DECLARED_CLAIMANTS) | set(NON_SCOPE_CLAIMS)) - claiming)
    assert not stale, f"classified here but no longer claiming anything: {stale}"


def test_the_detector_is_not_vacuous():
    """A phrase list that matches nothing passes everything.

    This is the failure the workspace has hit before: a guard whose detector
    quietly stopped detecting looks identical to a clean tree.
    """
    found = _claimants()
    assert found, "the phrase scan found no claims at all, so it guards nothing"
    assert "scripts/harness-audit.py" in found, (
        "the audit's own claim is the reference case; if it stopped matching, "
        "the detector decayed rather than the code improving"
    )
