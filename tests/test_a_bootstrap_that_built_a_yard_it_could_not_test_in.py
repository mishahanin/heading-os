"""The YARD bootstrap synced core dependencies only, and the suite went noisy.

Step 4 of `scripts/herdr/heading-os-yard/yard-bootstrap.sh` ran a bare
`uv sync`. `CLAUDE.md` § Setup step 2 has prescribed
`uv sync --all-extras --group dev` for a fresh clone since the file existed, and
a YARD is a fresh clone in every sense that matters: its own checkout, its own
`.venv`, no inherited site-packages.

MEASURED 2026-09-03 in the YARD at `.yard/.heading-os/test-123`, against HELM:

    HELM  .venv:  import telethon      -> ok
    HELM  .venv:  import cryptography  -> ok
    YARD  .venv:  import telethon      -> ModuleNotFoundError
    YARD  .venv:  import cryptography  -> ModuleNotFoundError

and the full suite in that YARD reported **229 failures** which a HELM-shaped
environment does not produce. They were environment noise throughout: absent
optional extras, plus `pytest-xdist` and `pre-commit` from the `dev` group.

Why this is a defect and not a slow-start trade. A YARD exists so that engine
work can be judged in isolation, and a task cannot tell its own regression from
229 failures it did not cause. The bare sync did not make the YARD slower; it
removed the YARD's reason to exist. The preceding commit on this branch had to
spend a full baseline run in a throwaway worktree to establish that its 229
failures were pre-existing, which is the cost this defect imposes on every task.

What this file holds: the sync step still exists, and it carries BOTH flags.
Both directions are pinned -- a step missing either flag must be refused, and
the real script must pass. The script text is read through
`scripts/utils/repo_files.read_sources`, not `Path.read_text`, so a file that
vanished mid-walk is reported rather than silently skipped.

Run: python3 -m pytest tests/test_a_bootstrap_that_built_a_yard_it_could_not_test_in.py
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.repo_files import read_sources  # noqa: E402

BOOTSTRAP = ROOT / "scripts" / "herdr" / "heading-os-yard" / "yard-bootstrap.sh"

# The command CLAUDE.md § Setup step 2 prescribes. Parsed out of CLAUDE.md
# below rather than trusted from here; this is only the shape being looked for.
_SYNC_RE = re.compile(r"^\s*uv\s+sync\b(?P<flags>[^\n|&]*)", re.MULTILINE)


def _bootstrap_source() -> str:
    """The script text, through the shared reader.

    `read_sources` yields nothing for a path that vanished and records it in
    `vanished`, so an empty result is distinguishable from an empty file. A
    bare `read_text` here would raise on the same event with a message about
    the filesystem rather than about the corpus.
    """
    vanished: list[Path] = []
    texts = dict(read_sources([BOOTSTRAP], vanished))
    assert not vanished, f"the bootstrap vanished mid-read: {vanished}"
    assert texts, f"{BOOTSTRAP} produced no text"
    return texts[BOOTSTRAP]


def _sync_invocations(text: str) -> list[str]:
    """Every `uv sync ...` in the script, as its flag string."""
    return [m.group("flags") for m in _SYNC_RE.finditer(text)]


# ============================================================
# What CLAUDE.md actually prescribes, read rather than assumed
# ============================================================

def test_claude_md_still_prescribes_both_flags():
    """The anchor for every assertion below.

    Hardcoding `--all-extras --group dev` here would leave this file asserting
    a command the workspace had moved on from, and passing while the bootstrap
    diverged from the documented setup all over again.
    """
    claude_md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    m = re.search(r"`uv sync([^`]*)`", claude_md)
    assert m, "CLAUDE.md no longer documents a `uv sync` command"
    flags = m.group(1)
    assert "--all-extras" in flags, flags
    assert "--group dev" in flags, flags


def test_the_extras_and_group_the_flags_resolve_to_exist():
    """`--all-extras` and `--group dev` must name something in pyproject.

    A flag that resolves to an empty set installs nothing and would satisfy
    every string assertion in this file while restoring the defect.
    """
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    groups = data["dependency-groups"]
    # MEASURED 2026-09-03: 10 extras (nine real plus the `all` aggregate) and a
    # `dev` group of 9. Floors, not equalities: the point is that the flags
    # resolve to a non-trivial set, not that the set never grows.
    assert len(extras) >= 5, extras.keys()
    assert "dev" in groups
    assert len(groups["dev"]) >= 5, groups["dev"]
    assert "pytest-xdist" in " ".join(groups["dev"]), (
        "the suite is run with -n auto; xdist must come from the dev group")


# ============================================================
# The direction that must now be refused
# ============================================================

@pytest.mark.parametrize("flags", [
    "",                          # the exact defect: a bare `uv sync`
    " --quiet",                  # the exact defect as it was written
    " --all-extras",             # extras without the dev group: no pytest-xdist
    " --group dev",              # dev group without the extras: no telethon
    " --all-extras --group prod",
])
def test_a_sync_missing_either_flag_is_refused(flags):
    """The detector itself, both directions, before it is pointed at the file.

    Without this the sweep below is a claim about a regex nobody exercised: a
    detector that accepts everything passes over the defect it was written for.
    """
    assert not _flags_are_complete(flags), flags


@pytest.mark.parametrize("flags", [
    " --all-extras --group dev",
    " --all-extras --group dev --quiet",
    " --quiet --all-extras --group dev",
    " --group dev --all-extras",
])
def test_a_complete_sync_is_accepted(flags):
    """The honest caller. A rule that refused these would refuse the fix."""
    assert _flags_are_complete(flags), flags


def _flags_are_complete(flags: str) -> bool:
    return "--all-extras" in flags and "--group dev" in flags


# ============================================================
# The real script
# ============================================================

def test_the_bootstrap_still_syncs_at_all():
    """A floor. A script with no `uv sync` satisfies "every sync is complete"
    vacuously, which is how this whole class of test goes quietly green."""
    invocations = _sync_invocations(_bootstrap_source())
    assert len(invocations) >= 1, (
        "the bootstrap no longer runs `uv sync`; this file's premise is gone "
        "and the rule needs rewriting rather than deleting")


def test_every_sync_in_the_bootstrap_carries_both_flags():
    """The defect, asserted against the file that had it."""
    offenders = [f for f in _sync_invocations(_bootstrap_source())
                 if not _flags_are_complete(f)]
    assert not offenders, (
        f"these `uv sync` calls in {BOOTSTRAP.name} do not install what "
        f"CLAUDE.md prescribes, so the YARD they build cannot run the suite: "
        f"{offenders}")


def _step_four_block(text: str) -> str:
    """The comment block and command that belong to step 4, and nothing else.

    Scoping matters more than it looks. A first draft asserted
    `"MEASURED 2026-09-03" in text` over the WHOLE script, and passed while the
    step-4 comment carried no such literal at all: the file already held four
    of that phrase, at lines 19, 215, 264 and 327, none of them about this
    step. The assertion was green over an unrelated occurrence. Found
    2026-09-03 by a mutation whose anchor did not match, which is the only
    reason anyone looked.
    """
    start = text.index("# 4. Dependencies")
    end = text.index("# 5. Arm the PreToolUse walls", start)
    return text[start:end]


def _prose(block: str) -> str:
    """Comment text with markers stripped and whitespace collapsed.

    A shell comment is wrapped by hand, so a claim spans lines at whatever
    column the author stopped. Matching the raw text would make every phrase
    assertion below a hostage to re-wrapping, which is the "a substring match
    survives a rewrap" trap read from the other side: here it would FAIL on a
    harmless reflow while a real deletion elsewhere went unnoticed.
    """
    lines = [ln.strip().lstrip("#").strip() for ln in block.splitlines()]
    return re.sub(r"\s+", " ", " ".join(lines))


def test_the_step_four_comment_carries_its_own_measurement():
    """Obligation 2: the measurement travels with the code it explains.

    Each number is pinned to the CLAIM it belongs to, not merely asserted to
    exist somewhere in the block. `"229" in block` was the first draft, and a
    mutation that replaced one of the two occurrences with "many" SURVIVED it:
    the other occurrence kept the assertion green while a real measurement had
    been deleted. Found 2026-09-03.
    """
    prose = _prose(_step_four_block(_bootstrap_source()))
    assert "MEASURED 2026-09-03" in prose
    assert "reported 229 failures" in prose, (
        "the observed failure count must stay attached to the observation")
    assert "regression from 229 pre-existing failures" in prose, (
        "the same number carries the argument for why this is a defect")
    assert "telethon" in prose, "name what was missing, not just how many failed"


def test_the_scoping_helper_actually_excludes_the_rest_of_the_file():
    """The negative case for the helper above.

    A `_step_four_block` that returned the whole file would restore the vacuous
    assertion exactly, and the test above alone could not tell the difference.
    """
    text = _bootstrap_source()
    block = _step_four_block(text)
    assert len(block) < len(text) / 2, "the block is most of the script"
    # Literals that exist in the file but must not be inside step 4.
    assert "did not say which worktree" not in block
    assert "--doctor-only" not in block


def test_the_failure_message_names_the_flags():
    """`fail 4 \"uv sync failed\"` sends the next operator to the wrong question.

    The message is the only thing they see, so it has to name the command that
    actually ran.
    """
    text = _bootstrap_source()
    m = re.search(r'fail 4 "([^"]*sync[^"]*)"', text)
    assert m, "step 4 no longer reports a sync failure"
    assert "--all-extras" in m.group(1), m.group(1)
