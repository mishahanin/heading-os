#!/usr/bin/env python3
"""The wall read quoted TEXT as commands, and missed the one that really ran.

MEASURED 2026-09-08 against the version this fix replaced, in-process against
the real dispatcher. `_YARD_CHAIN_RE` split on every `|` and every newline
regardless of quoting, which is one defect with two opposite faces:

    herdr agent prompt w47 "...\\n  rm -rf <yard>\\n..."     REFUSED (wrongly)
    sh -c 'rm -rf <yard>'                                   PERMITTED (wrongly)

The first face is the reported one. A command that merely QUOTES a refused
command was refused as though the quoted line were a link of the chain, so any
`grep`, `rg`, `echo` or agent prompt carrying one of these strings was
refusable. The brief that reported this defect was itself refused on its first
send, by the wall it describes.

The second face was found while fixing the first and is the reason the fix is
not simply "respect quotes". A single-quoted payload contains no chain operator
to split on, so `sh -c 'rm -rf <yard>'` was ONE link whose verb is `sh`, and no
wall in this file ever saw the `rm`. The double-quoted spelling was refused
only by accident of where the blunt split happened to cut it. Respecting quotes
without also descending into a shell payload would have turned that accident
into a permanent hole.

So the fix is both halves: quoting is respected when it can be established, and
the argument of a `sh -c` / `eval` is read as the command it is.

WHAT IS DELIBERATELY UNCHANGED, both driven below, because a narrowed splitter
that quietly dropped them would be the same defect wearing the other hat:

* a heredoc keeps the blunt split. Its body is prose, not shell quoting, and an
  apostrophe in it would otherwise open a quoted region that swallows the lines
  after it;
* quoting that never closes keeps the blunt split. The shell would reject the
  command outright, so there is no correct reading to find, and the blunt one
  splits MORE rather than less.

NOT CLAIMED: the payload's `cd` is followed for the outer links that come after
it, where a real shell would have discarded it. `_yard_segments` states that
limit; it is the price of descending at all, and the alternative was the hole
above.

Run: .venv/bin/python -m pytest \\
     tests/test_a_chain_splitter_that_read_quoted_text_as_a_command.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "_dispatch.py"


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("dispatch_chain_split", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def yard(guard, monkeypatch, tmp_path):
    """One unfinished yard and nothing else.

    Hermetic on purpose: which worktrees this machine has registered today must
    not decide whether a case about SPLITTING passes.
    """
    path = tmp_path / "yards" / "a-task"
    path.mkdir(parents=True)
    monkeypatch.setattr(guard, "_yard_guarded_checkouts", lambda: [path])
    monkeypatch.setattr(guard, "_yard_main_clone", lambda: None)
    monkeypatch.setattr(
        guard, "_yard_unfinished",
        lambda root, closes=None: [("tree", "2 uncommitted change(s)")])
    return path


def _decide(guard, command: str):
    """The real entry point, with the payload the harness would hand it."""
    return guard.check_yard_deletion_guard({
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(guard.WORKSPACE),
    })


def _reason(decision) -> str:
    return (decision or {}).get("reason", "")


def _refused(decision) -> bool:
    return decision is not None and decision.get("decision") == "block"


# The verbs are assembled rather than written out. This file is read by the
# very wall it tests whenever anything greps it, and before this fix a literal
# here was itself a refusable line.
RM = "rm -rf"


# ============================================================
# Face one: text that is quoted is not a command
# ============================================================

def test_a_quoted_command_on_its_own_line_is_not_a_command(guard, yard):
    """The reported defect: an agent prompt whose TEXT quotes a deletion."""
    command = (f'herdr agent prompt w47 "THE DEFECT\n'
               f'  {RM} {yard}\n'
               f'WHY IT HAPPENS. the splitter."')
    assert _decide(guard, command) is None, _reason(_decide(guard, command))


@pytest.mark.parametrize("template", [
    'grep -n "{rm} {yard}" notes.md',
    "echo '{rm} {yard}'",
    'rg --fixed-strings "{rm} {yard}"',
])
def test_searching_or_printing_the_phrase_is_not_running_it(guard, yard,
                                                            template):
    command = template.format(rm=RM, yard=yard)
    assert _decide(guard, command) is None, _reason(_decide(guard, command))


def test_a_quoted_pipe_does_not_cut_the_link_in_two(guard, yard):
    """The `|` face of the same defect, with no newline anywhere in it."""
    command = f'grep -n "list | {RM} {yard}" notes.md'
    assert _decide(guard, command) is None, _reason(_decide(guard, command))


# ============================================================
# Face two: an argument a shell EXECUTES is a command
# ============================================================

@pytest.mark.parametrize("template", [
    "sh -c '{rm} {yard}'",
    'bash -c "{rm} {yard}"',
    'bash -lc "{rm} {yard}"',
    'zsh -c "cd /tmp && {rm} {yard}"',
    "eval '{rm} {yard}'",
    "sudo bash -c '{rm} {yard}'",
    # A long option that happens to contain a `c`. Found by mutation: reading
    # the payload as "the word after the first flag holding a c" matched
    # `--norc` and returned the literal `-c`, missing the payload entirely.
    "bash --norc -c '{rm} {yard}'",
    "bash --login --norc -c '{rm} {yard}'",
    "bash --rcfile /dev/null -c '{rm} {yard}'",
    """sh -c 'sh -c "{rm} {yard}"'""",
])
def test_a_shell_payload_is_read_as_the_command_it_is(guard, yard, template):
    """`sh -c '<payload>'` was PERMITTED by every wall in this file until now."""
    command = template.format(rm=RM, yard=yard)
    decision = _decide(guard, command)
    assert _refused(decision), f"{command} was permitted"
    assert "uncommitted change" in _reason(decision), _reason(decision)


def test_an_argument_that_is_not_executed_is_still_only_text(guard, yard):
    """The pair for the case above, and the whole reason it is per verb.

    `herdr agent prompt` and `sh -c` are handed the same characters. One runs
    them. A fix that read every quoted argument as a command would refuse the
    first, which is the defect this file opens with.
    """
    assert _decide(guard, f"sh -c '{RM} {yard}'") is not None
    assert _decide(guard, f"herdr agent prompt w47 '{RM} {yard}'") is None


# ============================================================
# What the narrowing must NOT drop
# ============================================================

def test_a_heredoc_body_keeps_the_blunt_split(guard, yard):
    """An apostrophe in prose is not shell quoting.

    Two of them span the dangerous line here, so a splitter that respected
    "quotes" inside a heredoc would mask the newlines around it and let the
    command through. The carve-out is what keeps this refused.
    """
    command = (f"cat <<EOF > plan.md\n"
               f"# it's the last step\n"
               f"{RM} {yard}   # don't\n"
               f"EOF")
    decision = _decide(guard, command)
    assert _refused(decision), "the heredoc body stopped being read"


def test_quoting_that_never_closes_keeps_the_blunt_split(guard, yard):
    """The shell would reject this, so there is no correct reading to find.

    Without the fallback the unterminated quote masks everything after it and
    the deletion becomes invisible, which is a hole opened by a typo.
    """
    command = f'echo "unclosed\n{RM} {yard}'
    decision = _decide(guard, command)
    assert _refused(decision), "an unterminated quote hid the deletion"


@pytest.mark.parametrize("template", [
    "cd /tmp && {rm} {yard}",
    "cd /tmp; {rm} {yard}",
    "true || {rm} {yard}",
    "ls | xargs echo && {rm} {yard}",
    "{rm} {yard}",
])
def test_an_ordinary_chain_is_split_exactly_as_before(guard, yard, template):
    """The unquoted operators are the ones that were always the point."""
    command = template.format(rm=RM, yard=yard)
    assert _refused(_decide(guard, command)), f"{command} was permitted"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
