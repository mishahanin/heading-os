"""Both documents describing /prime's health block must match the registry.

Found by the 2026-08-23 engine audit. Three artifacts described the same list and
all three disagreed:

  * `scripts/prime-health-parallel.py` — `CHECKS`, the real registry: 12 entries.
  * `reference/orchestrator-patterns.md` Pattern 6 — "Checks (11 ...)", missing
    `updates`.
  * `docs/skills-operations-daily.html` — "seven read-only health checks",
    missing five and calling the block read-only.

The orchestrator file carried its own excuse: "this list mirrors it and can lag
it." It had lagged. A disclaimer is not a control; it converts a defect into a
documented defect and stops anyone fixing it. This is the fifth instance in one
night of the same shape, two lists in two files with nothing reading both, so
the fix is the same: derive one side from the other.

The audit's own count was 11, taken from the orchestrator file rather than the
registry. The real number was 12. That is the failure mode this test exists to
prevent, and the auditor fell into it too.

Second defect, same block. The floor said "All checks are read-only. Do NOT
write to any workspace file." Two of the twelve are not: `fireside_health` and
`sync_exchange_health` shell out to pulse scripts whose docstrings say
"includes auto-start" / "includes auto-spawn", and those spawn a DETACHED daemon
that survives the shell. No workspace file is written, which is exactly why the
floor as worded did not catch it. The behaviour is intended; the silence was not.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "prime-health-parallel.py"
PATTERNS = ROOT / "reference" / "orchestrator-patterns.md"
SKILL_PAGE = ROOT / "docs" / "skills-operations-daily.html"

# Checks that start a process. Derived from the pulse scripts, not asserted here
# on faith: test_the_spawning_checks_really_spawn pins it to their source.
SPAWNING = {"fireside_health", "sync_exchange_health"}


def _registry_keys() -> list[str]:
    """Parse `CHECKS = {...}` with ast, so a comment mentioning a check name
    cannot inflate the count and a renamed key cannot hide behind a grep."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CHECKS":
                    assert isinstance(node.value, ast.Dict)
                    return [k.value for k in node.value.keys]
    raise AssertionError("no CHECKS assignment found in prime-health-parallel.py")


@pytest.fixture(scope="module")
def keys() -> list[str]:
    return _registry_keys()


def test_the_registry_parses_and_is_not_empty(keys):
    assert len(keys) >= 10, f"parsed only {keys} out of the registry"
    assert all(isinstance(k, str) for k in keys)


# --- the orchestrator pattern -------------------------------------------------

def test_pattern_6_states_the_registry_count(keys):
    text = PATTERNS.read_text(encoding="utf-8")
    m = re.search(r"\*\*Checks \((\d+), defined in the `CHECKS` registry\)", text)
    assert m, "Pattern 6 no longer states a check count in the expected form"
    assert int(m.group(1)) == len(keys), (
        f"Pattern 6 says {m.group(1)} checks; the CHECKS registry holds "
        f"{len(keys)}: {keys}"
    )


def _checks_block() -> str:
    """The list between Pattern 6's count line and its safety floor. Both
    markers must be searched FORWARD from the count: other patterns in this file
    carry their own '**Safety floor' heading earlier in the document, and the
    first draft of this test sliced backwards onto Pattern 1 and reported all
    twelve keys missing."""
    text = PATTERNS.read_text(encoding="utf-8")
    start = text.index("**Checks (")
    end = text.index("**Safety floor", start)
    return text[start:end]


def test_pattern_6_lists_every_registry_key(keys):
    listed = set(re.findall(r"^- `([a-z_]+)`", _checks_block(), re.M))
    missing = sorted(set(keys) - listed)
    extra = sorted(listed - set(keys))
    assert not missing, f"Pattern 6 does not list: {missing}"
    assert not extra, f"Pattern 6 lists checks that do not exist: {extra}"


def test_the_safety_floor_admits_the_two_that_spawn():
    """The floor read 'All checks are read-only'. Two are not, and the reader of
    a mandatory-read safety section has no other place to learn it."""
    text = PATTERNS.read_text(encoding="utf-8")
    # Anchor forward from Pattern 6's count line: other patterns have their own
    # safety-floor heading earlier in the file.
    floor = text[text.index("**Safety floor", text.index("**Checks (")):]
    floor = floor[:floor.index("\nAGGREGATION")] if "\nAGGREGATION" in floor else floor
    assert "All checks are read-only." not in floor, (
        "the safety floor claims every check is read-only again; two spawn a "
        "detached daemon"
    )
    for name in SPAWNING:
        assert name in floor, f"the floor does not name {name} as an exception"


# --- the public docs page -----------------------------------------------------

def test_the_docs_page_states_the_registry_count(keys):
    """Written as a word, so this checks the word. Twelve entries, 'twelve'."""
    words = {7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
             12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen"}
    text = SKILL_PAGE.read_text(encoding="utf-8")
    section = text[text.index('id="s-prime"'):]
    section = section[:section.index("</section>")]
    expected = words.get(len(keys), str(len(keys)))
    assert expected in section, (
        f"the /prime card does not state {expected!r} checks; the registry holds "
        f"{len(keys)}"
    )
    stale = {w for n, w in words.items() if n != len(keys) and w in section}
    assert not stale, f"the /prime card still states a stale count: {sorted(stale)}"


def test_the_docs_page_does_not_call_the_block_read_only():
    text = SKILL_PAGE.read_text(encoding="utf-8")
    section = text[text.index('id="s-prime"'):]
    section = section[:section.index("</section>")]
    assert "read-only health checks" not in section, (
        "the /prime card calls the health block read-only; two of its checks "
        "start a detached daemon"
    )


# --- the claim about spawning must stay true ----------------------------------

@pytest.mark.parametrize("check,script", [
    ("fireside_health", "fireside-pulse.py"),
    ("sync_exchange_health", "sync-exchange-pulse.py"),
])
def test_the_spawning_checks_really_spawn(check, script, keys):
    """If a pulse script stops auto-starting, the exception written into the
    safety floor becomes a lie in the other direction."""
    assert check in keys
    text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
    assert "Popen" in text, f"{script} no longer spawns anything"
    assert "auto-s" in text, f"{script} no longer documents an auto-start"
