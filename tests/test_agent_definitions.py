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
