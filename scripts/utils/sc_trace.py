#!/usr/bin/env python3
"""A12 — the binding between a stated criterion and the test that decides it.

Measured on the two slices shipped 2026-08-02: the `gate-yield` gate artifact
states seven success criteria, its contract carries 28 test functions, and the
string `SC-` appears in those tests three times, all three in prose. Five of the
seven criteria were traceable to nothing. Criteria and the tests that decide them
are written twice, by hand, with nothing detecting a divergence.

The binding is a test's DOCSTRING naming the criterion it decides. Cheapest thing
that works: no marker registration, no decorator parsing, and it puts the
criterion where the test's reason already belongs.

**What this proves, and what it does not.** It detects the ABSENCE of a binding.
It cannot detect a WRONG one: a test may claim SC-2 in its docstring and assert
something entirely unrelated, and this module will call the trace complete. A
green trace reads "every criterion has someone claiming to decide it", NOT "every
criterion is decided". An operator who reads it the second way and stops reading
the tests is worse off than before this existed, so the limitation is stated here
and in the report rather than left to be inferred.

PURE. Nothing here touches the filesystem except `gate_refusal`, which is the one
seam `scripts/canopus.py` calls and which is TOTAL by construction: see its
docstring for why a raise here would be a lockout rather than a refusal.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# A criterion is DEFINED by a line it opens, and merely MENTIONED anywhere else.
# Both false positives below are measured against the real corpus rather than
# imagined: an archived artifact opens a line with `SC-1 to SC-7, from the spec`
# outside the criteria section, and a critique table carries
# `| H1 | HIGH | SC-13 rewritten: ... |` inside one. A whole-file scan defines
# SC-7 and SC-13 out of prose and then refuses the slice for failing to test
# criteria nobody ever wrote.
#
# The leading class admits the two shapes the corpus actually uses and nothing
# else: `- **SC-1** ...` (the last two artifacts) and `SC-2 [failure-mode]: ...`
# (the planning gate's own format block). A table row opens with `|`, and a prose
# sentence opens with a word; neither is in the class.
CRITERION_LINE = re.compile(r"^[-*\s]*\**\s*(SC-\d+)\b")

# A CLAIM OPENS THE DOCSTRING. Everything after the leading run of identifiers
# is prose and claims nothing, which is the exact mirror of the line-start rule
# above and was earned the same way: by this module refusing its own contract.
#
# Measured 2026-08-02 at step 8. Three tests in this slice's contract describe
# the false positives the artifact parser exists to survive, so their docstrings
# say `SC-13`, `SC-1 to SC-7` and `SC-9` in prose. A reader taking any mention as
# a claim reported all three as orphan criteria and refused the slice. Without
# this rule the orphan check is not merely noisy, it is unusable: a docstring
# cannot explain what it is testing without accidentally binding to it.
CLAIM_PREFIX = re.compile(r"^((?:SC-\d+[\s,]*)+)")
CRITERION_ANY = re.compile(r"\bSC-\d+\b")

_SECTION = re.compile(r"^(#{1,6})\s+.*success\s+criteria", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,6})\s+")


def read_criteria(text: str) -> list[str]:
    """The criteria a gate artifact DEFINES, in the operator's own order.

    Scoped to the success-criteria section, because the same identifier appears
    in later phases as prose and in critique tables as a cell, and a parser that
    cannot tell a definition from a mention refuses slices for criteria that were
    never stated.

    Order is preserved rather than sorted: a report that renumbers the operator's
    own criteria is one he has to translate before he can act on it.
    """
    lines = text.splitlines()
    start = None
    level = 0
    for index, line in enumerate(lines):
        found = _SECTION.match(line)
        if found:
            start = index + 1
            level = len(found.group(1))
            break
    if start is None:
        return []

    seen: list[str] = []
    for line in lines[start:]:
        heading = _HEADING.match(line)
        if heading and len(heading.group(1)) <= level:
            break
        found = CRITERION_LINE.match(line)
        if found and found.group(1) not in seen:
            seen.append(found.group(1))
    return seen


def read_claims(sources: dict[str, str]) -> dict[str, set[str]]:
    """Which criteria each contract source claims, keyed by criterion.

    `sources` maps a display name to that file's text, so this stays pure and the
    caller owns every read.

    A file that will not parse contributes NOTHING and is not an error here: it
    cannot collect under pytest either, so the redness gate already owns it, and
    raising from this module would reach the shared builder that `approve` and
    `freeze` both call. Same for a test with no docstring, which is most tests in
    this workspace: `ast.get_docstring` answers None and a reader that assumes a
    string raises inside that same builder.
    """
    claims: dict[str, set[str]] = {}
    for name, text in sources.items():
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test"):
                continue
            doc = ast.get_docstring(node)
            if not doc:
                continue
            opening = CLAIM_PREFIX.match(doc.strip())
            if not opening:
                continue
            for found in CRITERION_ANY.findall(opening.group(1)):
                claims.setdefault(found, set()).add(name)
    return claims


def trace(criteria, claims: dict[str, set[str]]) -> dict:
    """Match the stated criteria against the claimed ones.

    Three outcomes, and the third is the one a naive implementation drops:
    `orphan` is a criterion a test claims and the artifact never defined, which
    is what a typo in a docstring looks like from the outside. Without it the
    author believes a criterion is covered, the trace reads clean, and the
    binding points at nothing.
    """
    stated = list(criteria)
    return {
        "criteria": stated,
        "bound": {name: sorted(claims[name]) for name in stated if name in claims},
        "unbound": [name for name in stated if name not in claims],
        "orphan": sorted(set(claims) - set(stated)),
    }


def refusal(result: dict) -> str:
    """The refusal sentence, or "" when the trace is clean.

    The empty-criteria case comes FIRST because it is the most misleading answer
    this module could give: zero criteria and zero unbound criteria is
    arithmetically a pass, and it is exactly the vacuity the slice exists to stop.

    Every refusal names BOTH exits and says which is wrong. An author facing an
    unbound criterion can write the missing test or delete the criterion; the
    second is one keystroke and passes immediately, and a refusal that stays
    silent about it trains the workspace to shrink its own contracts until they
    pass.
    """
    if not result["criteria"]:
        return ("the gate artifact states no success criteria, so a trace over it "
                "proves nothing. A contract checked against zero criteria passes "
                "by having nothing to satisfy, which is the one answer this check "
                "must never give. Write the criteria into the artifact's "
                "success-criteria section first.")

    # BOTH classes, never the first one found. The two arrive together far more
    # often than either arrives alone: retyping SC-2 as SC-9 in a docstring
    # creates an unbound SC-2 and an orphan SC-9 in one keystroke, and a message
    # naming only the unbound half sends the operator to write a test that
    # already exists. Measured on this slice's own contract at step 8.
    parts = []
    if result["unbound"]:
        names = ", ".join(result["unbound"])
        parts.append(
            f"{names} stated in the gate artifact and claimed by no contract "
            f"test. Write the test that decides each one and name the criterion "
            f"in its docstring. Deleting the criterion also clears this refusal "
            f"and is the wrong exit: the contract would then pass by covering "
            f"less than it was approved to cover.")
    if result["orphan"]:
        names = ", ".join(result["orphan"])
        parts.append(
            f"{names} claimed by a contract test docstring and defined nowhere "
            f"in the artifact's success criteria. Either the claim is a typo, in "
            f"which case fix the docstring, or the criterion was dropped from "
            f"the artifact after the test was written, in which case put it back "
            f"rather than deleting the test.")
    return " ".join(parts)


def contract_sources(paths) -> dict[str, str]:
    """Read every test module under *paths* into a display-name to text mapping.

    Display names are relative to the working directory when that is possible,
    so a report row names a path the operator can open, and fall back to the bare
    filename when the contract lives somewhere else entirely.
    """
    sources: dict[str, str] = {}
    for entry in paths:
        entry = Path(entry)
        found = sorted(entry.rglob("test_*.py")) if entry.is_dir() else [entry]
        for path in found:
            try:
                name = str(path.relative_to(Path.cwd()))
            except ValueError:
                name = path.name
            sources[name] = path.read_text(encoding="utf-8", errors="replace")
    return sources


def gate_refusal(anchor_path, contract_paths) -> str:
    """The one seam the lifecycle calls. TOTAL: it never raises, ever.

    This runs inside `_candidate_manifest`, the single builder `approve` and
    `freeze` share. A raise here refuses EVERY slice in the workspace, and the
    sanctioned repair for a wedged gate -- `/canopus back` -- itself begins with
    `approve --replace`, so the lockout would include its own escape. A check
    about the shape of prose is not permitted to do that.

    So: only a DEFINITE finding refuses. An unreadable artifact, an unparseable
    contract file, an unexpected exception of any kind -- all report on stderr
    and return no refusal at all. The same reasoning that makes `depth-gate`
    deliberately bypassable: process discipline is not a leak wall, and the
    push-time scans that ARE unbypassable exist for a different job.

    No contract means no trace. A slice freezing only enforcer content has no
    tests to bind to, and demanding one there would refuse every content-only
    freeze in the workspace.
    """
    if not contract_paths:
        return ""
    try:
        text = Path(anchor_path).read_text(encoding="utf-8", errors="replace")
        result = trace(read_criteria(text), read_claims(contract_sources(contract_paths)))
        return refusal(result)
    except Exception as exc:  # noqa: BLE001 — totality IS the requirement
        # Named, so this is a report and not a swallow. An operator seeing this
        # is looking at a check that could not establish an answer, which is not
        # the same claim as a criterion being unbound, and the sentence says so.
        print(f"canopus: the criteria trace could not be established, so it "
              f"refuses nothing: {type(exc).__name__}: {exc}", file=sys.stderr)
        return ""
