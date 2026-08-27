"""An override hint must name a value ABOVE the cap it offers to raise.

The dispatcher's cap messages end with a ready-to-paste escape hatch:

    Override: `export WS_TOOL_BUDGET_HARD=2000` if intentional

The number in that hint was a literal, and the cap beside it was a separate
literal. On 2026-08-27 the tool-call cap was raised 1200 -> 4000 because
workflows are the standing working method here and a subagent's tool calls all
count against the operator's one 30-minute window: a 66-agent audit workflow made
1849 calls in 29 minutes and tripped a cap set when the main loop was the only
caller. The hint was not raised with it, so the blocked operator was handed a
command that would have LOWERED the cap from 4000 to 2000 and blocked them
harder, in a message whose whole purpose is to unblock them.

Nothing could have caught that, because nothing related the two numbers. This
does, and it does it for every cap in the file rather than the one that drifted:
the env name and its default are read out of the source, so a cap added later is
covered on the day it is written.

Both numbers are now derived (`{TOOL_BUDGET_HARD * 2}`), so this test also pins
that they stay derived. A future edit back to a literal is exactly the shape of
the original defect.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DISPATCH = ROOT / ".claude" / "hooks" / "_dispatch.py"

# `export WS_SOMETHING=1234` inside any string in the file.
HINT_RE = re.compile(r"export\s+(WS_[A-Z0-9_]+)\s*=\s*(\d+)")

# The same, but with the number left as an f-string expression rather than a
# literal. This is the shape the fix produced and the shape that cannot drift.
DERIVED_HINT_RE = re.compile(r"export\s+(WS_[A-Z0-9_]+)\s*=\s*\{")


def _env_defaults() -> dict[str, tuple[str, int]]:
    """Map each WS_* env override to (constant name, its numeric default).

    Read from the AST rather than by importing, so this test says something
    about the file on disk even if the module grows an import-time side effect.
    Only the `int(os.environ.get("WS_X", "N"))` shape is claimed; anything else
    is skipped rather than guessed at.
    """
    tree = ast.parse(DISPATCH.read_text(encoding="utf-8"))
    found: dict[str, tuple[str, int]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        for sub in ast.walk(node.value):
            if not (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "get"
                    and len(sub.args) == 2):
                continue
            name, default = sub.args
            if not (isinstance(name, ast.Constant) and isinstance(name.value, str)
                    and name.value.startswith("WS_")):
                continue
            if not (isinstance(default, ast.Constant) and isinstance(default.value, str)):
                continue
            try:
                found[name.value] = (target.id, int(default.value))
            except ValueError:
                continue
    return found


def test_the_source_still_declares_the_caps_this_test_reads():
    """Anchor. If the AST shape changes, every assertion below silently covers
    nothing, and a file with no recognised caps passes trivially."""
    defaults = _env_defaults()
    assert "WS_TOOL_BUDGET_HARD" in defaults, defaults
    assert "WS_RATE_LIMIT_HARD" in defaults, defaults


def test_the_hints_this_test_reads_are_actually_present():
    """Anchor for the other half: a regex that matches nothing passes anything."""
    source = DISPATCH.read_text(encoding="utf-8")
    hints = HINT_RE.findall(source) + DERIVED_HINT_RE.findall(source)
    assert len(hints) >= 2, (
        f"found {len(hints)} override hints in {DISPATCH.name}; the detector has "
        "stopped matching the message text it is supposed to police"
    )


def test_no_override_hint_names_a_value_at_or_below_its_own_cap():
    source = DISPATCH.read_text(encoding="utf-8")
    defaults = _env_defaults()
    bad = []
    for env_name, advised in HINT_RE.findall(source):
        if env_name not in defaults:
            bad.append(f"{env_name} is advised but never read as a cap")
            continue
        const, current = defaults[env_name]
        if int(advised) <= current:
            bad.append(
                f"{env_name} hint advises {advised} but {const} is already "
                f"{current}; pasting it makes the block worse"
            )
    assert not bad, (
        "an override hint would not unblock the operator it is printed for:\n  "
        + "\n  ".join(bad)
    )


@pytest.mark.parametrize("env_name", ["WS_TOOL_BUDGET_HARD", "WS_RATE_LIMIT_HARD"])
def test_the_cap_hints_are_derived_from_the_cap_not_typed_beside_it(env_name):
    """A literal cannot be wrong today and can always be wrong tomorrow.

    The test above catches the drift once it exists; this one removes the way it
    happens. Both hints now interpolate the live constant.
    """
    source = DISPATCH.read_text(encoding="utf-8")
    assert re.search(rf"export\s+{env_name}\s*=\s*\{{", source), (
        f"the {env_name} override hint carries a hand-typed number again. Derive "
        f"it from the constant, e.g. `export {env_name}={{CONST * 2}}`, so the two "
        f"cannot disagree."
    )


def test_the_tool_budget_cap_leaves_room_for_a_workflow():
    """Pin the reason the cap was raised, not just the number.

    A workflow's subagent calls land in the operator's own 30-minute window. The
    measured run that tripped the old cap made 1849 calls, so a cap at or below
    that number blocks the working method this workspace is built around. The
    guard still exists: a genuine runaway reaches any ceiling.
    """
    defaults = _env_defaults()
    _, cap = defaults["WS_TOOL_BUDGET_HARD"]
    assert cap > 1849, (
        f"the tool-call cap is {cap}, at or below the 1849 calls one measured "
        f"66-agent workflow made in 29 minutes. Deliberate fan-out would block."
    )


def test_the_retained_history_can_still_reach_the_raised_cap():
    """The cap is counted from the retained history, so the retention bound has
    to stay above it or the BLOCK branch is dead code. This bug has happened
    here before: when the cap went 300 -> 1200 the bound stayed at 500.
    """
    source = DISPATCH.read_text(encoding="utf-8")
    assert "tool_history[-(TOOL_BUDGET_HARD + 100):]" in source, (
        "the tool-history retention bound is no longer expressed in terms of the "
        "cap. If it is now a literal below the cap, the cap can never be reached "
        "and the block never fires. See tests/security/"
        "test_SEC_017_dispatch_check_branches.py for the behavioural proof."
    )
