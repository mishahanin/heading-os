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

ROOT = Path(__file__).resolve().parent.parent
AGENTS = ROOT / ".claude" / "agents"

EXPECTED = {
    "crm-reader",
    "comms-scout",
    "datastore-validator",
    "draft-writer",
}


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), (
        f"{path.name} does not open with a frontmatter fence, so none of its "
        f"fields are read"
    )
    body = text[4:]
    end = body.find("\n---\n")
    assert end != -1, f"{path.name} has an unterminated frontmatter block"
    out = {}
    for line in body[:end].splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _tools(path: Path) -> set[str]:
    raw = _frontmatter(path).get("tools", "")
    return {t.strip() for t in raw.split(",") if t.strip()}


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
