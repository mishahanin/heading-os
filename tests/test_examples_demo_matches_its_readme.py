"""The demo in examples/README.md must produce the output it promises.

`examples/README.md` is the engine's only end-to-end reproduction promise to a
newcomer: point `HEADING_OS_DATA` at `examples/`, run one command, see this
exact block. Everything else in the repo asks the reader to believe a
description.

The 2026-08-23 engine audit flagged the promise as under-specified. Its argument:
`docs/skills-crm.html` says cadence defaults live in `crm/config.md`, no such
file ships in `examples/`, the bundled contact declares no `cadence` in its
frontmatter, and so the "(cadence: 14)" in the README depends on a built-in
default nobody documented. If the script instead errored on the missing config,
or defaulted differently, the block would be wrong for a first-run adopter.

Reproduced, and the output claim is refuted:

    $ HEADING_OS_DATA="$(pwd)/examples" .venv/bin/python scripts/crm-health.py
    31C Relationship Radar

    RED - Overdue
      Example Contact (Example Co) -  - no recorded touch (cadence: 14)

    Total: 1 contacts tracked | 1 red | 0 yellow | 0 green

Byte for byte what the README shows, exit 0. The 14 comes from
`scripts/utils/crm.py:365`, `type_cadence = 14`, the fallback used when no
`crm/config.md` supplies a table.

The reasoning behind the finding still stands even though its conclusion did
not: the promise rests on an undocumented literal in a file the README never
names, so changing that literal breaks the README with nothing to notice. This
test is what the finding should have asked for. It runs the documented command
and compares against the documented block, so the two cannot drift apart in
silence, whichever one moves.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "examples" / "README.md"
EXAMPLES = ROOT / "examples"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _promised_block() -> list[str]:
    """The fenced block under 'What you see', normalized to non-empty lines."""
    text = README.read_text(encoding="utf-8")
    start = text.index("**What you see**")
    fence = text.index("```", start)
    end = text.index("```", fence + 3)
    body = text[fence + 3:end]
    return [ln.rstrip() for ln in body.splitlines() if ln.strip()]


def _run_demo() -> subprocess.CompletedProcess:
    env = dict(os.environ, HEADING_OS_DATA=str(EXAMPLES))
    # The overlay pin must not leak in from the operator's own shell.
    env.pop("HEADING_OS_TZ", None)
    return subprocess.run(
        [sys.executable, "scripts/crm-health.py"],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120,
    )


@pytest.fixture(scope="module")
def demo() -> subprocess.CompletedProcess:
    return _run_demo()


def test_the_readme_still_carries_a_promised_block():
    lines = _promised_block()
    assert len(lines) >= 3, f"parsed only {lines} out of the README"
    assert any("Relationship Radar" in ln for ln in lines)


def test_the_documented_command_exits_clean(demo):
    assert demo.returncode == 0, (
        f"the demo command the README tells a newcomer to run failed:\n"
        f"stdout={demo.stdout!r}\nstderr={demo.stderr!r}"
    )


def test_the_demo_prints_exactly_what_the_readme_promises(demo):
    actual = [ln.rstrip() for ln in _ANSI.sub("", demo.stdout).splitlines() if ln.strip()]
    expected = _promised_block()
    assert actual == expected, (
        "the demo output no longer matches examples/README.md.\n"
        f"README promises: {expected}\n"
        f"the command prints: {actual}"
    )


def test_the_cadence_literal_the_promise_rests_on_is_still_there():
    """Names the coupling the audit was right to notice. If this default moves,
    the test above fails too, but this one says WHY in one line."""
    src = (ROOT / "scripts" / "utils" / "crm.py").read_text(encoding="utf-8")
    assert "type_cadence = 14" in src, (
        "scripts/utils/crm.py no longer defaults to a 14-day cadence; the "
        "'(cadence: 14)' in examples/README.md came from that literal"
    )


def test_the_demo_tree_still_ships_no_crm_config():
    """The demo exercises the no-config path on purpose. A `crm/config.md`
    appearing here would silently change what the promise proves."""
    assert not (EXAMPLES / "crm" / "config.md").exists(), (
        "examples/ now ships a crm/config.md, so the demo no longer exercises "
        "the missing-config fallback the README output depends on"
    )
