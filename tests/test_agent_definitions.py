"""The agent roles, and the tool lists that are their whole point.

Four recurring orchestrator roles moved out of `.claude/rules/skill-orchestrator.md`
prose into `.claude/agents/` on 2026-08-09. The move was not about the prose. It
was about enforcement: a sentence saying "do NOT write to CRM" is interpreted,
and a breach shows up afterwards in a diff, whereas a missing `Write` in the tool
list is refused at the call.

So the assertions here are about capability, not wording. If someone adds `Bash`
to `draft-writer` for convenience, the lethal-trifecta control quietly stops
being structural for that agent and goes back to being a request, and these tests
are what says so.
"""
from __future__ import annotations

import ast
import mmap
import re
import shutil
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / ".claude" / "agents"

EXPECTED = {
    "crm-reader",
    "comms-scout",
    "datastore-validator",
    "draft-writer",
}

# The three read-only scouts. `low` is the setting Anthropic names for subagents,
# and on Opus 5 effort governs tool calls as well as output tokens, so it also
# bounds how far a reader wanders. It is a per-agent key because changing effort
# mid-conversation invalidates the prompt cache, and each dispatch is its own
# conversation (2026-08-20).
LOW_EFFORT_SCOUTS = {"crm-reader", "comms-scout", "datastore-validator"}


def _split(path: Path) -> tuple[str, str]:
    """Frontmatter block and body, as the harness itself divides them."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), (
        f"{path.name} does not open with a frontmatter fence, so none of its "
        f"fields are read"
    )
    end = text.find("\n---\n", 3)
    assert end != -1, f"{path.name} has an unterminated frontmatter block"
    return text[4 : end + 1], text[end + 5 :]


def _frontmatter(path: Path) -> dict:
    """Parse with a real YAML loader, not a line splitter.

    The hand-rolled `":" in line` parser this replaced (2026-08-20) read a YAML
    comment as a field and would have missed a value it could not split, so it
    agreed with the harness only by luck. yaml.safe_load is what the harness
    does, and it is the only way this file can claim the files are loadable.
    """
    block, _ = _split(path)
    loaded = yaml.safe_load(block)
    assert isinstance(loaded, dict), (
        f"{path.name} frontmatter is not a mapping: {loaded!r}"
    )
    return loaded


def _tools(path: Path) -> set[str]:
    raw = _frontmatter(path).get("tools", "")
    if isinstance(raw, str):
        raw = raw.split(",")
    return {str(t).strip() for t in raw if str(t).strip()}


def agent_files() -> list[Path]:
    return sorted(AGENTS.glob("*.md")) if AGENTS.is_dir() else []


def test_the_four_roles_exist():
    assert AGENTS.is_dir(), ".claude/agents/ is missing"
    assert {p.stem for p in agent_files()} >= EXPECTED


@pytest.mark.parametrize("path", agent_files(), ids=lambda p: p.stem)
def test_every_agent_declares_name_description_and_tools(path):
    fm = _frontmatter(path)
    for field in ("name", "description", "tools"):
        assert fm.get(field), f"{path.name} has no `{field}`"
    assert fm["name"] == path.stem, (
        f"{path.name} declares name={fm['name']!r}; a mismatch means the file is "
        f"dispatched under one name and documented under another"
    )


@pytest.mark.parametrize("path", agent_files(), ids=lambda p: p.stem)
def test_every_agent_is_yaml_plus_a_body(path):
    """The file has to survive a YAML loader and still say something.

    An agent whose frontmatter parses but whose body is empty is a name and a
    tool list with no contract in it, and every guarantee these roles carry
    beyond the tool list is written in that body.
    """
    _frontmatter(path)  # asserts the fences and that it loads as a mapping
    _, body = _split(path)
    assert len(body.strip()) > 200, f"{path.name} has no meaningful body"


@pytest.mark.parametrize("path", agent_files(), ids=lambda p: p.stem)
def test_no_agent_pins_a_model_release(path):
    """A family or `inherit`, never a release.

    Same reason as `tests/test_no_claude_model_pins.py`: every Claude model id is
    a pinned snapshot, so a literal here freezes the role on the day it was typed.
    """
    model = _frontmatter(path).get("model", "inherit")
    assert model in {"inherit", "opus", "sonnet", "haiku", "fable"}, (
        f"{path.name} pins model={model!r}; use a family or `inherit`"
    )


def test_draft_writer_cannot_reach_the_send_path():
    """The lethal-trifecta control, as a capability rather than a request.

    `draft-writer` reads untrusted inbound content and writes a reply. Give it
    `Bash` and it can run `scripts/send-email.py`, which puts all three legs of
    the trifecta in one agent.
    """
    tools = _tools(AGENTS / "draft-writer.md")
    assert "Bash" not in tools, tools
    assert "Write" in tools, "a draft writer that cannot write is useless"


def test_the_read_only_agents_hold_no_write_capability():
    for name in ("crm-reader", "datastore-validator"):
        tools = _tools(AGENTS / f"{name}.md")
        assert not (tools & {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"}), (
            f"{name} can mutate state; it is dispatched in parallel with others "
            f"and its read-only contract is what makes that safe: {tools}"
        )


@pytest.mark.parametrize("name", sorted(LOW_EFFORT_SCOUTS))
def test_the_read_only_scouts_run_at_low_effort(name):
    assert _frontmatter(AGENTS / f"{name}.md").get("effort") == "low", (
        f"{name} no longer declares `effort: low`; these three retrieve and "
        f"summarise, which is the case Anthropic names for the subagent setting, "
        f"and three of them are dispatched in parallel so their effort is the "
        f"pattern's latency"
    )


def test_draft_writer_takes_the_default_effort():
    """Absence is the decision here, so the absence needs a guard too.

    `high` IS the Opus 5 default. Writing it out would create a second place for
    the value to live and drift; leaving it out keeps one. But an untested
    absence is indistinguishable from an oversight, and the next person tidying
    these four files into a matching set would add the key without knowing
    drafting is the one role that spends the deliberation (2026-08-20).
    """
    assert "effort" not in _frontmatter(AGENTS / "draft-writer.md")


def test_comms_scout_is_bounded_by_turns_as_well_as_by_tools():
    """The one agent whose tool list is not the guarantee gets a second bound.

    `comms-scout` holds `Bash` — it has to, since reaching Exchange and Telegram
    means running the workspace readers — so unlike its siblings it cannot be
    stopped from sending by its capabilities alone. A turn cap is mechanical
    where its Never list is interpreted. Twelve is the ordinary path (locate the
    reader, run it, read the output, answer: about six) doubled, so a degraded
    path keeps its retry (2026-08-20).
    """
    fm = _frontmatter(AGENTS / "comms-scout.md")
    assert "Bash" in _tools(AGENTS / "comms-scout.md"), (
        "if comms-scout ever loses Bash, its tool list becomes the guarantee "
        "like its siblings' and this cap can be revisited"
    )
    cap = fm.get("maxTurns")
    assert isinstance(cap, int), f"maxTurns must be an int, got {cap!r}"
    assert 0 < cap <= 12, (
        f"maxTurns={cap} is not a bound worth having on a read-only scout; "
        f"split a sweep by channel rather than raise it"
    )


def test_the_orchestrator_rule_still_owns_the_guarantees():
    """Roles moved; the orchestration-level guarantees did not.

    An agent file cannot express "two agents never write the same contact file",
    because that is a property of the dispatch, not of any one agent.
    """
    rule = (ROOT / ".claude" / "rules" / "skill-orchestrator.md").read_text(encoding="utf-8")
    assert "Never skip approval gates" in rule
    assert "Two agents must never write the same CRM contact file" in rule
    for name in sorted(EXPECTED):
        assert name in rule, f"the rule does not name the `{name}` agent"


# ============================================================
# The claim, against the grant
#
# Added 2026-08-30. Everything above tests the GRANT -- which tools an agent
# holds -- and nothing tested whether the file's own account of that grant was
# true. `draft-writer` spent from 2026-08-09 to 2026-08-30 describing itself as
# "structurally unable to send, publish, or log to CRM" while holding `Write`
# and `Edit`, and a CRM contact file is an ordinary markdown file. Measured on
# 2026-08-30 against `.claude/hooks/_dispatch.py`: a Write to `crm/contacts/`
# and an Edit to `context/pipeline.md` both pass the whole hook chain with no
# denial, so nothing outside the agent made the claim true either. Two thirds of
# the sentence was right and the last third was an invitation to skip the check
# that actually protects those paths.
#
# The claim is machine-readable rather than prose ON PURPOSE. A guard that
# grepped the body for "unable to log to CRM" would fire on the paragraph that
# now explains the trap, which punishes a file for documenting itself. So each
# agent sorts its own Never list into `x-heading-enforcement.capability` (the
# grant refuses it) and `.instruction` (the grant allows it and only prose
# forbids it), and this section checks both directions against the parsed
# `tools` list. The `x-heading-*` namespace is the workspace convention for
# frontmatter the harness does not own; the CLI's agent schema is a
# non-strict object, so an unknown key is dropped rather than rejected.
# ============================================================

# What each named capability would take to perform, in tools. A claim is
# checkable only because this mapping is written down: `send` and `publish` mean
# `scripts/send-email.py` and the publish scripts, which are shell, so `Bash`
# alone confers them. The write family is conferred by any file-mutating tool
# AND by `Bash`, since a heredoc is a write.
_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash"}
CAPABILITY_TOOLS = {
    "send": {"Bash"},
    "publish": {"Bash"},
    "mark-read": {"Bash"},
    "crm-write": _WRITE_TOOLS,
    "pipeline-write": _WRITE_TOOLS,
    "state-write": _WRITE_TOOLS,
    "write-outside-the-given-path": _WRITE_TOOLS,
}


def _enforcement(path: Path) -> dict:
    block = _frontmatter(path).get("x-heading-enforcement")
    assert isinstance(block, dict), (
        f"{path.name} declares no `x-heading-enforcement` mapping. Every agent "
        f"has to sort its Never list into what the tool grant refuses and what "
        f"only the prose forbids, or the file can claim a guarantee it does not "
        f"have and nothing says so"
    )
    out = {}
    for key in ("capability", "instruction"):
        value = block.get(key, [])
        assert isinstance(value, list), (
            f"{path.name} x-heading-enforcement.{key} is a "
            f"{type(value).__name__}, not a list"
        )
        out[key] = [str(v).strip() for v in value]
    return out


@pytest.mark.parametrize("path", agent_files(), ids=lambda p: p.stem)
def test_every_claimed_capability_is_one_this_file_can_check(path):
    """An unknown term is a claim nothing verifies, so it fails here.

    Without this, a typo (`crm_write` for `crm-write`) would silently drop a
    claim out of both directions below and the file would read as guarded while
    being checked against nothing.
    """
    claims = _enforcement(path)
    unknown = sorted(
        set(claims["capability"] + claims["instruction"]) - set(CAPABILITY_TOOLS)
    )
    assert not unknown, (
        f"{path.name} names {unknown}, which CAPABILITY_TOOLS does not map to "
        f"any tool. Add the mapping here or fix the spelling in the agent file"
    )
    overlap = sorted(set(claims["capability"]) & set(claims["instruction"]))
    assert not overlap, (
        f"{path.name} files {overlap} as both capability-enforced and "
        f"instruction-enforced; it is one or the other"
    )


@pytest.mark.parametrize("path", agent_files(), ids=lambda p: p.stem)
def test_a_capability_claim_is_refused_by_the_tool_list(path):
    """Direction 1: nothing claimed structural may be reachable.

    This is the assertion that would have caught `draft-writer`. Its old
    description claimed CRM logging was structurally impossible; `Write` and
    `Edit` are both in its grant, so this reddens.
    """
    tools = _tools(path)
    for claim in _enforcement(path)["capability"]:
        conferring = tools & CAPABILITY_TOOLS[claim]
        assert not conferring, (
            f"{path.name} claims `{claim}` is refused by its tool list, but the "
            f"list grants {sorted(conferring)}, which confers it. Either drop "
            f"the tool or move `{claim}` to `instruction` and say in the body "
            f"that prose is all that holds it"
        )


@pytest.mark.parametrize("path", agent_files(), ids=lambda p: p.stem)
def test_an_instruction_claim_is_one_the_tool_list_really_allows(path):
    """Direction 2, and it is not symmetry for its own sake.

    A guard that only ever checks one direction lets the whole block decay into
    `instruction: [everything]`, which asserts nothing and passes forever. An
    entry here that the grant ALREADY refuses is a real guarantee filed as a
    request: the file undersells itself, a reader adds belt-and-braces prose
    instead of trusting the grant, and the next person to widen the tool list
    sees no test go red.
    """
    tools = _tools(path)
    for claim in _enforcement(path)["instruction"]:
        conferring = tools & CAPABILITY_TOOLS[claim]
        assert conferring, (
            f"{path.name} files `{claim}` as instruction-enforced, but its tool "
            f"list {sorted(tools)} grants nothing that confers it. That is a "
            f"capability guarantee written down as a request; move it to "
            f"`capability`"
        )


def test_the_two_agents_that_hold_bash_and_write_are_the_ones_that_say_so():
    """The split is derived from the grant, not restated from the files.

    A per-file check passes if every file is wrong the same way. This computes
    which agents CAN mutate a file, straight from the tool lists, and requires
    exactly those to carry a non-empty `instruction` list. `crm-reader` and
    `datastore-validator` hold neither `Bash` nor a write tool, so they have
    nothing to forbid by prose; `comms-scout` (Bash) and `draft-writer`
    (Write/Edit) both do.
    """
    can_mutate, claims_prose = set(), set()
    for path in agent_files():
        if _tools(path) & _WRITE_TOOLS:
            can_mutate.add(path.stem)
        if _enforcement(path)["instruction"]:
            claims_prose.add(path.stem)
    assert can_mutate == claims_prose, (
        f"agents whose grant can mutate a file: {sorted(can_mutate)}; agents "
        f"declaring an instruction-only prohibition: {sorted(claims_prose)}. "
        f"A file in the first set and not the second is forbidding nothing it "
        f"is actually able to do"
    )


def test_draft_writer_does_not_ask_for_a_number_it_cannot_measure():
    """Finding 3, bound where it can be bound.

    `.claude/rules/hidden-chars.md` owns the confirmation line and says both of
    its numbers come from `scripts/sanitize-text.py --scan`, never from an
    estimate. `draft-writer` has no `Bash`, so it cannot run that scan; asking
    it for a word count could only ever produce a guess inside a validation
    line. The obligation moved to the dispatching orchestrator, and the two
    patterns that dispatch this agent have to name the scan.

    Checked structurally, not by grepping the agent body: the agent's OWN prose
    now discusses the word count in order to explain why it does not report one,
    so a text guard would fire on the explanation.
    """
    assert "Bash" not in _tools(AGENTS / "draft-writer.md")
    patterns = (ROOT / "reference" / "orchestrator-patterns.md").read_text(encoding="utf-8")
    sections = {}
    current = None
    for line in patterns.splitlines():
        if line.startswith("## Pattern "):
            current = line
            sections[current] = []
        elif current is not None:
            sections[current].append(line)

    dispatching = [
        head for head, body in sections.items()
        if any("draft-writer" in line for line in body)
    ]
    assert dispatching, (
        "no pattern dispatches `draft-writer` any more; if the role was retired, "
        "retire this test with it rather than leaving it green over nothing"
    )
    for head in dispatching:
        body = "\n".join(sections[head])
        assert "sanitize-text.py" in body, (
            f"{head.strip()} dispatches `draft-writer`, which cannot run "
            f"scripts/sanitize-text.py --scan itself. The pattern has to say who "
            f"does, or the hidden-chars confirmation line has no owner"
        )
        assert "hidden-chars.md" in body, (
            f"{head.strip()} names the scan but not the rule that owns the "
            f"confirmation line, so the format is left to whoever is reading"
        )


# ============================================================
# The read-only invocation comms-scout is told to use
#
# Added 2026-08-30. `comms-scout` holds `Bash` and its Never list says "Never
# modify any state file, including a daemon's". The instruction above it used to
# read "the sync and reader scripts under `scripts/`" and name none, so the
# literal reading of one line broke the other: a bare
# `scripts/email-intelligence.py` run calls `commit_state` then `state.save()`
# and burns message ids into the dedupe set, and `scripts/sync-exchange.py
# --emails` rewrites `last_touch` in a CRM contact file through `bump_inbound`.
#
# The file now names the exact safe form per channel. That naming is only worth
# anything while the named things still exist and still mean what the file says,
# so this section asks the SOURCE, not the prose: the paths resolve, and the two
# facts the instruction leans on are still true in the code.
# ============================================================

# No leading backtick in this pattern, and that is a correction. It required
# one until a mutation showed the guard reading 2 of the 5 scripts the file
# names: the invocations are written as `python scripts/email-intelligence.py
# --json`, so the backtick sits before `python` and the anchored pattern
# skipped every path that carried an interpreter or a flag -- including both
# channels whose write paths are the reason this section exists. It was green
# over a subset and would have stayed green while the two paths that matter
# rotted.
_REFERENCED_PATH = re.compile(r"(?:scripts|\.claude)/[A-Za-z0-9_./-]+\.py")


def _ast(path: Path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_every_script_comms_scout_names_actually_exists():
    """A named invocation that points at nothing is worse than a vague one.

    The vague version at least sent the reader looking. A confident path that
    404s gets improvised around, which is how the agent ends up back at whatever
    reader it can find.
    """
    _, body = _split(AGENTS / "comms-scout.md")
    named = sorted({m.group(0) for m in _REFERENCED_PATH.finditer(body)})
    # A floor, not a count. Four channels are named plus `send-email.py` in the
    # honesty paragraph up top, so five is what the fixed file carries; the
    # assertion is >= 4 so ordinary rewording does not fail it, while a rewrite
    # that quietly drops back to "the reader scripts under scripts/" does.
    assert len(named) >= 4, (
        f"comms-scout names only {named}. The fix was to name the exact "
        f"read-only invocation for each channel it is sent to; a body that has "
        f"lost them has gone back to the vague instruction that broke its own "
        f"Never list"
    )
    missing = [p for p in named if not (ROOT / p).is_file()]
    assert not missing, f"comms-scout points at scripts that do not exist: {missing}"


def test_the_email_fetch_still_withholds_the_commit_for_both_flags():
    """`--json` is load-bearing, so it is checked in the code, not the prose.

    If someone drops `json` from this guard, the invocation comms-scout is told
    to use silently starts committing dedupe state, and every word of the
    instruction stays true-looking. Read off the AST: find the `if` that guards
    the `commit_state` call and require its condition to name both flags.
    """
    tree = _ast(ROOT / "scripts" / "email-intelligence.py")
    guards = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "commit_state"
                for stmt in node.body for c in ast.walk(stmt))
    ]
    assert guards, (
        "no `if` guards a `commit_state` call in scripts/email-intelligence.py. "
        "Either the fetch commits unconditionally now, or the shape moved; "
        "comms-scout is told `--json` makes the fetch read-only and that claim "
        "has to be re-derived before it is trusted"
    )
    for guard in guards:
        named = {n.attr for n in ast.walk(guard.test) if isinstance(n, ast.Attribute)}
        assert {"dry_run", "json"} <= named, (
            f"the commit guard tests {sorted(named)}; comms-scout is told that "
            f"`--json` alone suppresses the state write, which needs `json` in "
            f"this condition"
        )


def test_a_sentinel_dry_run_still_reaches_a_read_only_state_manager():
    """`--test` is the only cycle comms-scout may run, for exactly one reason.

    `dry_run` has to arrive at `StateManager(read_only=...)`. A refactor that
    keeps the flag and drops the wiring leaves `--test` writing state, with the
    agent file still calling it the safe one.
    """
    tree = _ast(ROOT / "scripts" / "sentinel.py")
    wired = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "StateManager"
        and any(kw.arg == "read_only" and isinstance(kw.value, ast.Name)
                and kw.value.id == "dry_run" for kw in node.keywords)
    ]
    assert wired, (
        "no `StateManager(read_only=dry_run)` in scripts/sentinel.py; a dry run "
        "no longer demonstrably withholds the state write"
    )


def test_the_telegram_read_commands_still_have_no_way_to_acknowledge():
    """One caller, and it is the subcommand comms-scout is told never to run.

    The agent file says the six read subcommands do not mark anything read. That
    holds only while `send_read_acknowledge` stays confined to `cmd_mark_read`;
    a second caller inside `cmd_read` or `cmd_unread` would make the sweep
    visible to the counterpart, which is the one thing this scout must not do.
    """
    client = ROOT / ".claude" / "skills" / "telegram" / "scripts" / "telegram_client.py"
    tree = _ast(client)
    callers = {
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(isinstance(c, ast.Call)
                and getattr(c.func, "attr", "") == "send_read_acknowledge"
                for c in ast.walk(fn))
    }
    assert callers == {"cmd_mark_read"}, (
        f"`send_read_acknowledge` is reached from {sorted(callers)}; comms-scout "
        f"is told the read subcommands cannot acknowledge, so anything beyond "
        f"cmd_mark_read makes that instruction false"
    )


# ============================================================
# The model an alias actually resolves to
#
# Added 2026-08-30. `datastore-validator.md` said `sonnet` resolves to
# `claude-sonnet-4-6`, under a header claiming it was measured. The installed
# CLI is 2.1.251, whose alias map reads
# `opus:"claude-opus-5", sonnet:"claude-sonnet-5", haiku:"claude-haiku-4-5"`,
# and `claude-sonnet-4-6` is now `PREV_SONNET_ID`. Every one of these files
# reasons about whether its `effort` key does anything, and that reasoning is
# built on the resolved id, so a stale id makes the conclusion stale with it.
# ============================================================

_MODEL_ID = re.compile(r"claude-(opus|sonnet|haiku|fable)-[0-9][0-9a-z.-]*")
_ALIAS_MAP = re.compile(
    rb'opus:"(claude-[a-z0-9-]+)",sonnet:"(claude-[a-z0-9-]+)",haiku:"(claude-[a-z0-9-]+)"'
)


def _installed_alias_map() -> dict[str, str] | None:
    """The alias table from the installed Claude Code binary, or None.

    None means no `claude` on PATH at all, which is an environment absence, not
    a pass: the one caller skips on it and says so. It does NOT return None for
    a binary that is present and unreadable, or present without the table --
    those are findings, and they raise.
    """
    found = shutil.which("claude")
    if found is None:
        return None
    binary = Path(found).resolve()
    with (
        binary.open("rb") as fh,
        mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as blob,
    ):
        hits = {m.groups() for m in _ALIAS_MAP.finditer(blob)}
    assert hits, (
        f"{binary} carries no recognisable model alias map. The CLI's minified "
        f"shape changed; re-derive the pattern before trusting this test again"
    )
    assert len(hits) == 1, f"{binary} carries conflicting alias maps: {hits}"
    opus, sonnet, haiku = next(iter(hits))
    return {"opus": opus.decode(), "sonnet": sonnet.decode(),
            "haiku": haiku.decode()}


@pytest.mark.parametrize("path", agent_files(), ids=lambda p: p.stem)
def test_a_body_never_names_a_stale_id_for_its_own_model_alias(path):
    """A file may discuss another family's id; it may not misstate its own.

    Scoped to the agent's OWN declared alias on purpose. `datastore-validator`
    legitimately explains what `haiku` resolves to for the two scouts beside it,
    and that sentence must not be dragged into this assertion. What it may not
    do is print a sonnet-family id that is not the one `sonnet` resolves to
    today, because its whole argument about the `effort` key rests on that.
    """
    aliases = _installed_alias_map()
    if aliases is None:
        pytest.skip(
            "no `claude` on PATH, so the alias map has no ground truth to read. "
            "This covers the model ids in the agent bodies and nothing else; a "
            "machine without the CLI leaves exactly that unchecked"
        )
    alias = _frontmatter(path).get("model", "inherit")
    if alias not in aliases:
        pytest.skip(f"{path.name} declares model={alias!r}, which is not an alias")
    current = aliases[alias]
    _, body = _split(path)
    named = {m.group(0) for m in _MODEL_ID.finditer(body)
             if m.group(1) == alias}
    stale = sorted(named - {current})
    assert not stale, (
        f"{path.name} runs `{alias}`, which the installed CLI resolves to "
        f"{current}, but its body still names {stale}. Re-measure and correct "
        f"the id and the version stamp together"
    )
