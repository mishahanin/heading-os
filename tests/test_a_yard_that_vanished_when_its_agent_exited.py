"""`exec` in the bootstrap's agent-start turned an ordinary exit into a loss.

`yard-bootstrap.sh` used to start the yard's agent as

    herdr pane run "$PANE_ID" "HEADING_OS_YARD=1 exec $AGENT_CMD"

`exec` replaces the pane's shell with the agent, so the pane holds ONE process
and no fallback. When Claude Code exits -- which a person is entitled to make it
do -- nothing is left in the pane, herdr hangs it up, and the workspace
disappears from the sidebar. The checkout and the branch are untouched on disk,
so what the operator sees is a YARD that deleted itself while he was looking at
it.

MEASURED 2026-09-06 in `~/.config/herdr/herdr-server.log`, three times in one
day, each within 5-14 s of the operator focusing the workspace:

    w5H  04:52:48 focus -> 04:52:55 pane child exited, code 0, signal None
    w5N  05:53:30 focus -> 05:53:44 pane child exited, code 0, signal None
    w5Q  06:05:01 focus -> 06:05:06 pane child exited, code 0, signal None

He reported the third himself: he opened the agents view and pressed Esc.

BOTH DIRECTIONS. The marker must still be exported (dropping `exec` must not
quietly cost the thing the line exists for), and `exec` must be gone from that
command. The first half passes against the previous version too, and is here
precisely so a later edit cannot buy a surviving shell by dropping the marker.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = ROOT / "scripts" / "herdr" / "heading-os-yard" / "yard-bootstrap.sh"

# The line that hands a command to herdr for the pane. Comments are stripped
# first: this file's own explanation says the word `exec` several times, and a
# grep over raw text would go red on the paragraph that documents the fix --
# which teaches the next author to stop explaining.
_START = re.compile(r'pane\s+run\s+"\$PANE_ID"')


def _code_lines() -> list[str]:
    """The script's lines with comment-only lines removed."""
    lines = BOOTSTRAP.read_text(encoding="utf-8").splitlines()
    return [ln for ln in lines if not ln.lstrip().startswith("#")]


def _agent_start_command() -> str:
    """The `pane run` invocation that starts the agent, joined across its
    continuations. Asserted non-empty by its callers, so a refactor that renames
    the call fails here rather than passing over nothing."""
    lines = _code_lines()
    for index, line in enumerate(lines):
        if not _START.search(line):
            continue
        chunk = [line]
        cursor = index
        while chunk[-1].rstrip().endswith("\\") and cursor + 1 < len(lines):
            cursor += 1
            chunk.append(lines[cursor])
        return " ".join(part.rstrip().rstrip("\\") for part in chunk)
    return ""


def test_the_agent_start_does_not_exec_over_the_pane_shell():
    """The half that fails against the previous version."""
    command = _agent_start_command()
    assert command, (
        "no `pane run \"$PANE_ID\"` line found in the bootstrap; this test now "
        "measures nothing and must be repointed at whatever starts the agent")
    assert " exec " not in f" {command} ", (
        f"the bootstrap starts the yard's agent with `exec`, so the pane holds "
        f"no shell and an ordinary agent exit destroys the workspace. MEASURED "
        f"three times on 2026-09-06.\ncommand: {command}")


def test_the_agent_still_receives_the_yard_marker():
    """The other direction, and the reason that line exists at all.

    `HEADING_OS_YARD` is what the yard-side guards read, and it reaches the
    agent by process inheritance. A future edit must not buy a surviving shell
    by dropping it.
    """
    command = _agent_start_command()
    assert command, "no agent-start line found; see the sibling test"
    assert "HEADING_OS_YARD=1" in command, (
        f"the agent is started without its marker, so the yard-side guards have "
        f"nothing to read.\ncommand: {command}")


def test_the_operator_is_told_the_yard_survived_the_exit():
    """An exit must land on a prompt that says what happened.

    Without this the fix is invisible: the pane goes quiet, the operator sees a
    bare shell where an agent was, and has no reason to believe the yard is
    intact or any idea how to bring the agent back.

    Asserted on the two things that must be TRUE of the message, not on its
    wording. The first version of this test required the literal word
    "Restart:", which made it a spell-checker: rewording the hint to something
    better failed it, and that teaches people to stop improving the words.
    """
    command = _agent_start_command()
    assert command, "no agent-start line found; see the sibling test"
    assert "intact" in command, (
        f"the pane never says the yard survived, so a surviving shell reads as "
        f"a broken yard.\ncommand: {command}")
    assert "--continue" in command, (
        f"the hint offers no way back to the SAME conversation. The exit the "
        f"operator actually performs is Esc in the agents view, and what he "
        f"wants back is the session he was in; without `--continue` in front of "
        f"him he starts a fresh one and loses it.\ncommand: {command}")


def test_the_marker_is_exported_into_the_surviving_shell():
    """A `VAR=value cmd` prefix marks that ONE process and nothing else.

    Once the agent can exit without taking the pane with it, the shell it
    leaves behind is a place the operator types `claude --continue` from. If the
    marker was only prefixed onto the original command, that hand-started agent
    runs with no `HEADING_OS_YARD` and the yard-side guards have nothing to
    read. MEASURED that exact state on w5M on 2026-09-06, reached by a different
    route: `/proc/<pid>/environ` held no marker at all.
    """
    command = _agent_start_command()
    assert command, "no agent-start line found; see the sibling test"
    assert "export HEADING_OS_YARD=1" in command, (
        f"the marker is prefixed rather than exported, so an agent the operator "
        f"restarts from the surviving shell is unmarked.\ncommand: {command}")


# ============================================================
# The agent command itself: the operator's standing flag
# ============================================================

def test_the_agent_starts_with_permissions_skipped_by_default():
    """Operator instruction, 2026-09-06, verbatim: "когда запускается claude,
    не важно где и в каком Yard, пусть всегда запускается с
    --dangerously-skip-permission".

    Asserted on the DEFAULT of `HEADING_OS_AGENT_CMD` rather than on a literal
    somewhere in the file, because the default is what a yard created with no
    configuration actually runs. An override in the plugin's own `.env` still
    wins, which is why this reads the `:-` branch and not the whole assignment.

    Why the flag is not a hole here: what confines a YARD is the write guard in
    `.claude/hooks/_dispatch.py` and `HEADING_OS_YARD=1` in the agent's
    environment, and both run whatever this default says. Prompting only decided
    whether a human was asked first, and in a yard nobody is sitting at, the ask
    reached no one.
    """
    lines = [ln for ln in _code_lines() if ln.startswith("AGENT_CMD=")]
    assert len(lines) == 1, (
        f"expected exactly one AGENT_CMD assignment, found {len(lines)}: {lines}")
    assert "--dangerously-skip-permissions" in lines[0], (
        f"a yard's agent is started without the operator's standing flag, so "
        f"every new yard stalls on a permission dialog nobody is there to "
        f"answer.\n{lines[0]}")
    assert "HEADING_OS_AGENT_CMD:-" in lines[0], (
        f"the command is hard-coded rather than defaulted, so the operator can "
        f"no longer ask for prompts back without editing the script.\n{lines[0]}")


# ============================================================
# The sidebar label: findable, and derived rather than recorded
# ============================================================

def _label_block() -> str:
    """The lines that build the workspace label, joined."""
    lines = _code_lines()
    for index, line in enumerate(lines):
        if line.lstrip().startswith("LABEL="):
            return " ".join(lines[max(0, index - 4):index + 3])
    return ""


def test_the_sidebar_label_carries_the_workspace_id():
    """The operator asked (2026-09-06) to find in the sidebar the code he hears
    from the assistant. It goes in the LABEL."""
    block = _label_block()
    assert block, "no LABEL= assignment found; the rename no longer builds one"
    assert "$WS_ID" in block, (
        f"the label does not carry the workspace id, so a yard named in "
        f"conversation cannot be found in the sidebar.\n{block}")


def test_the_workspace_id_is_not_baked_into_the_branch_or_the_path():
    """The other direction, and the reason the id lives in the label alone.

    The id is EPHEMERAL: the slug yard was w5N, then w5Q, then w5R inside one
    hour on 2026-09-06, each time its pane died and the workspace was recreated.
    The branch, the checkout path and the transcript slug outlived all three. A
    branch or a directory carrying an id would have been wrong an hour later and
    wrong permanently, which is worse than not carrying it at all.
    """
    source = "\n".join(_code_lines())
    for forbidden in ("--branch \"$WS_ID", "--branch $WS_ID",
                      "$WS_ID-$BRANCH", "$WS_ID/$BRANCH"):
        assert forbidden not in source, (
            f"the workspace id is being written into something durable "
            f"({forbidden!r}); it is ephemeral and belongs only in the label")


def test_the_label_does_not_say_yard_twice():
    """`YARD/yard-slug-one-owner` spent its width on the word YARD, twice, and
    crowded out the part that identifies the work."""
    block = _label_block()
    assert block, "no LABEL= assignment found; see the sibling test"
    assert '"YARD/$BRANCH"' not in block and "'YARD/$BRANCH'" not in block, (
        f"the label prepends YARD/ to a branch that may already start with "
        f"`yard-`, producing YARD/yard-...\n{block}")
