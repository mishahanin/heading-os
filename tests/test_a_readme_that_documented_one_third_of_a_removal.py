"""Removing a YARD is three commands; the README documented one, wrongly.

`scripts/herdr/README.md` said, as the whole of its removal procedure:

    herdr worktree remove --workspace fix-router

MEASURED 2026-09-03 by the operator. `herdr worktree remove --help` prints
`--workspace <ID>` and the wire schema (`WorktreeRemoveParams`) requires a field
literally named `workspace_id`. `herdr workspace list` prints IDs as `w46`,
`w3Q`; the branch name appears only inside `label` and `checkout_path`. So the
documented call passed a branch name where an ID belongs, AND IT SAID NOTHING:
exit code reported no error, the worktree stayed, the workspace stayed.

The visible cost, also the operator's, same day: two probe worktrees removed by
hand with `git worktree remove` plus `rm -rf` left `yard-verify (deleted)` and
`yard-probe2 (deleted)` in the herdr sidebar, one of them still holding a shell
in a directory that no longer existed. Clearing that needs
`herdr workspace close <ID>`, a command the README did not mention at all, and
the branch needs a third command after that.

WHY THAT ONE CALL WAS SILENT IS NOT ESTABLISHED. The first version of this file
asserted a README warning saying that a herdr command given a wrong-shaped
argument does nothing and reports success. That was one observation generalised
into a mechanism, and an audit of herdr 0.8.2 on 2026-09-03 refuted it: an
unknown ID returns `{"error":{"code":"..._not_found"}}` on stderr with exit 1, an
unknown flag or a missing value exits 2, a missing required positional prints
usage and exits 2. Every read-only command checked failed loudly. So the silent
run is an OUTLIER, the README now says so, and this file asserts that it keeps
saying so -- an invented explanation is worse here than an admitted gap, because
the next reader would stop looking for the real cause.

WHY THIS TEST EXISTS, since a docs test can easily be shape-checking for its own
sake. Three of its four assertions are about CONTENT, not form: that the removal
procedure still names all three commands, that they appear in the order in which
they work, and that the unexplained outlier is still labelled unexplained. Those
fail when somebody deletes a step or re-invents the mechanism, which are the two
defects that actually happened.

The fourth is a shape check, and it is worth its keep precisely because the
argument shapes are disjoint: a herdr workspace ID is `w` followed by
alphanumerics and nothing else, while every branch name in this repository
carries a `-`, a `/` or a `.`. A literal that cannot be an ID is the exact
mistake made here, and it is cheap to catch.

WHAT THIS TEST DOES NOT ESTABLISH, stated rather than implied: nothing here runs
a herdr command, so it cannot tell you the documented commands work. Verifying
that needs mutating runs against the operator's live terminal manager, which a
test must never do. It holds the doc to two measurements; it does not repeat
them.

Run: python3 -m pytest tests/test_a_readme_that_documented_one_third_of_a_removal.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.repo_files import read_sources  # noqa: E402

README = ROOT / "scripts" / "herdr" / "README.md"

# `--workspace` followed by its argument, wherever it is written.
WORKSPACE_ARG = re.compile(r"--workspace[= ]+(\S+)")

# What `herdr workspace list` actually prints for the ID field: `w3Q`, `w46`,
# `w48`. No separator of any kind.
AN_ID = re.compile(r"^w[0-9A-Za-z]+$")

# A documentation placeholder, which is the preferred thing to write.
A_PLACEHOLDER = re.compile(r"^(<[^>]+>|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|\"?\$.*)$")


def looks_like_an_id(argument: str) -> bool:
    """True when `argument` is a herdr workspace ID or a placeholder for one.

    The predicate is exported and tested directly below, both ways, so the
    corpus scan is not the only thing standing behind it.
    """
    # The same flag is written in prose as well as in a shell block, so the
    # captured token can carry markdown and sentence punctuation: `--workspace
    # <ID>`, ends the capture at "<ID>`,". Strip that, never the inside.
    argument = argument.strip().strip("`'\".,;:()")
    return bool(A_PLACEHOLDER.match(argument) or AN_ID.match(argument))


REMOVAL_HEADING = "### Removing one is THREE commands"


def _readme_text() -> str:
    vanished: list = []
    texts = dict(read_sources([README], vanished))
    assert not vanished, f"{README} is not readable: {vanished}"
    return texts[README]


def _removal_recipe() -> str:
    """The shell block under the removal heading, and nothing else.

    Scoped rather than whole-file on purpose. A bare substring search over the
    README is satisfied by the PROSE around the recipe: deleting
    `herdr workspace list` from the commands SURVIVED a mutation run (5/6
    caught) because the same words appear two paragraphs below explaining what
    the command prints. An assertion a fix's own explanation can satisfy is not
    an assertion.
    """
    text = _readme_text()
    assert REMOVAL_HEADING in text, (
        f"{README.name} no longer has a removal section headed "
        f"{REMOVAL_HEADING!r}; everything below measured nothing")
    section = text[text.index(REMOVAL_HEADING):]
    fence = section.index("```bash")
    body = section[fence + len("```bash"):]
    return body[:body.index("```")]


# ============================================================
# The predicate, both directions, on synthetic input
# ============================================================

def test_an_id_shaped_argument_is_accepted():
    for good in ("w47", "w3Q", "w46", "<ID>", "<workspace-id>", "$WS_ID", "${WS}"):
        assert looks_like_an_id(good), f"{good!r} is an ID and was rejected"


def test_a_branch_shaped_argument_is_rejected():
    """The failing half. Every one of these is a real branch name here."""
    for bad in ("fix-router", "test-123", "yard-probe2", "main",
                "YARD/test-123", "release/v0.14.0", "v0.14.0"):
        assert not looks_like_an_id(bad), (
            f"{bad!r} is a branch name and was accepted as an ID; the check "
            f"cannot catch the mistake it exists for")


# ============================================================
# The README itself
# ============================================================

def test_no_documented_call_passes_a_branch_name_where_an_id_belongs():
    # A markdown TABLE ROW is excluded, and only a table row. The README's
    # refuting measurement documents deliberately wrong calls -- `worktree list
    # --workspace YARD/not-an-id` is there precisely BECAUSE it fails -- and a
    # scan that cannot tell a recipe from a record of a failure would push the
    # evidence back out of the file. Prose and shell blocks stay in scope.
    arguments = [a
                 for line in _readme_text().splitlines()
                 if not line.lstrip().startswith("|")
                 for a in WORKSPACE_ARG.findall(line)]

    # The floor. With no occurrences the loop below asserts nothing at all,
    # and this file would pass over a README that had lost the command.
    assert len(arguments) >= 1, (
        "no `--workspace` call left in the README; either the procedure was "
        "removed or it was reworded past this check, and either way the scan "
        "below is now measuring an empty corpus")

    wrong = [a for a in arguments if not looks_like_an_id(a)]
    assert not wrong, (
        f"{README.name} passes {wrong} to --workspace. That flag reaches a wire "
        f"field named `workspace_id`; a branch name there does nothing and "
        f"reports success. Use the ID from `herdr workspace list`.")


def test_the_removal_procedure_still_names_all_three_steps():
    """The assertion that carries the real information.

    Each of these was absent on 2026-09-03. Deleting any one of them puts the
    operator back where they started: a removed checkout with a dead sidebar
    entry, or a branch nobody can delete.
    """
    recipe = _removal_recipe()
    for step, why in (
        ("herdr worktree remove", "removes the checkout"),
        ("herdr workspace close", "clears the sidebar entry, which step 1 leaves"),
        ("git branch -d", "deletes the branch, which neither of the others does"),
        ("herdr workspace list", "is how the ID is found in the first place"),
    ):
        assert step in recipe, (
            f"the removal recipe in {README.name} no longer runs `{step}`, "
            f"which {why}")


def test_the_three_steps_are_documented_in_the_order_they_work():
    """A branch cannot be deleted while a worktree still holds it."""
    recipe = _removal_recipe()
    remove = recipe.index("herdr worktree remove")
    close = recipe.index("herdr workspace close")
    branch = recipe.index("git branch -d")
    assert remove < close < branch, (
        "the removal steps are documented out of order: `git branch -d` fails "
        "while the worktree still holds the branch, so it cannot come first")


def test_the_unexplained_outlier_is_still_labelled_unexplained():
    """The correction, held in place.

    The temptation this guards against is a real one: the file already carried a
    tidy mechanism ("herdr fails silently") that explained the observation and
    was false. Re-inventing it would read like an improvement.
    """
    prose = " ".join(_readme_text().split())
    assert "Why that call was silent is NOT ESTABLISHED" in prose, (
        "the README no longer admits that the silent run is unexplained")
    assert "is an OUTLIER" in prose, (
        "the README no longer says the silent run was an outlier rather than "
        "the rule")
    assert "does nothing and reports success" not in prose or (
        "corrected it" in prose), (
        "the refuted generalisation is back in the README as a claim; MEASURED "
        "2026-09-03 on herdr 0.8.2, every read-only command checked failed "
        "loudly with exit 1 or exit 2")


def test_the_refuting_measurement_travels_with_the_correction():
    """A correction with no evidence beside it gets reverted by the next reader."""
    prose = " ".join(_readme_text().split())
    assert "herdr 0.8.2" in prose and "MEASURED" in prose, (
        "the README states the outlier without the measurement that refuted "
        "the tidy explanation, so nothing stops the explanation coming back")
    for evidence in ("_not_found", "unknown option", "missing value for"):
        assert evidence in prose, (
            f"the refuting measurement lost its {evidence!r} row")
