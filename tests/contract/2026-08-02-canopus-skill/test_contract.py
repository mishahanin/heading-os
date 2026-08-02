"""Frozen contract - /canopus, the operator's surface onto the lifecycle.

The gap this closes, measured on 2026-08-02: there is no `/canopus` skill. The
entire lifecycle lives inside `/pre-impl` and its reference file, which are
instructions for the ASSISTANT. The only way the operator could reach the
standard from his own workspace was to be handed an absolute path to a script,
which happened in this session and which he correctly refused as a regression.

That is a violation of `.claude/rules/console-first.md`, whose own validation
list asks "Can it be driven from Claude chat?". For Canopus the answer was "only
if the assistant types the path".

Three properties carry the weight:

1. **The agenda is one definition, and it is complete.** Thirteen numbered
   moments, four acts, exactly two approvals, contiguous numbering, every step in
   exactly one act, act ranges that tile the sequence without gap or overlap. A
   process whose own step list can drift between two files is a process nobody
   can be at a known point in.
2. **Position is derived from what the machine can see, and admits what it
   cannot.** Six moments leave a durable trace; the rest are human work no file
   records. The display reports the known ones and says plainly where it is
   inferring, because invented precision about where you are is worse than
   silence.
3. **The bare command orients rather than reports.** `/canopus` with no argument
   answers where you are, what you just did, what is next, and prints the whole
   agenda. The operator asked for exactly this, and it is the difference between
   a status line and a place to stand.

Authoring rule: every import of the code under test happens INSIDE a test body.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_CLI = _ROOT / "scripts" / "canopus.py"
_SKILL = _ROOT / ".claude" / "skills" / "canopus" / "SKILL.md"


def _run(args, cwd=None):
    return subprocess.run([sys.executable, str(_CLI), *args], capture_output=True,
                          text=True, cwd=str(cwd or _ROOT), env=dict(os.environ),
                          timeout=180)


def _payload(proc):
    """The CLI's JSON as an ASSERTION, never an exception: a test that ERRORS is
    vacuous by the probe's rule, so every parse fails loudly instead."""
    assert proc.stdout.strip(), (
        f"no output (exit {proc.returncode})\n{proc.stderr[:600]}")
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise AssertionError(f"output was not JSON: {exc}\n{proc.stdout[:400]}") from exc


# ---------------------------------------------------------------------------
# Property 1 - the agenda is one definition, and it is complete
# ---------------------------------------------------------------------------

def test_there_are_thirteen_numbered_moments():
    from scripts.utils.canopus_steps import STEPS

    assert len(STEPS) == 13


def test_the_numbering_is_contiguous_from_one():
    from scripts.utils.canopus_steps import STEPS

    assert [s["number"] for s in STEPS] == list(range(1, 14))


def test_exactly_two_moments_are_the_operators_own():
    """The operator's decision, 2026-08-02: number the approvals so his own two
    moments are visible in the counter instead of sitting between the acts."""
    from scripts.utils.canopus_steps import approvals

    assert [a["number"] for a in approvals()] == [6, 12]


def test_every_act_the_operator_takes_part_in_ends_with_his_step():
    """The consequence that made 13 better than 11: acts 1 and 3 close on an
    approval, and act 2 is the one with no human in it."""
    from scripts.utils.canopus_steps import ACTS, step

    for entry in ACTS:
        last = step(entry["steps"][1])
        if entry["number"] in (1, 3):
            assert last["approval"], f"act {entry['number']} does not end on an approval"
        else:
            assert not last["approval"]


def test_the_acts_tile_the_sequence_with_no_gap_and_no_overlap():
    from scripts.utils.canopus_steps import ACTS

    covered = []
    for entry in sorted(ACTS, key=lambda a: a["number"]):
        first, last = entry["steps"]
        assert first <= last
        covered.extend(range(first, last + 1))
    assert covered == list(range(1, 14))


def test_every_step_belongs_to_exactly_one_act():
    from scripts.utils.canopus_steps import ACTS, STEPS, act_of

    numbers = {a["number"] for a in ACTS}
    for entry in STEPS:
        assert entry["act"] in numbers
        assert act_of(entry["number"])["number"] == entry["act"]


def test_every_step_carries_a_name_and_a_description():
    """An agenda entry nobody can read is a number, not an agenda."""
    from scripts.utils.canopus_steps import STEPS

    for entry in STEPS:
        assert entry["name"].strip()
        assert len(entry["what"].strip()) > 30, entry["number"]


def test_the_release_step_is_separate_from_the_approval_that_triggers_it():
    """They were quietly one thing. They are two: the operator's word, then the
    work that closes the slice."""
    from scripts.utils.canopus_steps import step

    assert step(12)["approval"] is True
    assert step(13)["approval"] is False
    assert step(12)["act"] != step(13)["act"]


# ---------------------------------------------------------------------------
# Property 2 - position is derived, and admits what it cannot see
# ---------------------------------------------------------------------------

def test_the_machine_visible_moments_are_marked_and_are_a_minority():
    """Most of this process is human work that leaves no file behind. Saying so
    is the difference between a position and a guess."""
    from scripts.utils.canopus_steps import STEPS

    visible = [s["number"] for s in STEPS if s["machine_visible"]]
    assert visible, "nothing is machine-visible, so no position could be derived"
    assert len(visible) < len(STEPS), "everything claims to be machine-visible"
    assert 7 in visible and 9 in visible, "the lock and the verdict must be visible"


def test_with_no_freeze_held_the_position_is_before_the_lock():
    proc = _run(["where", "--json"])
    payload = _payload(proc)
    assert payload["slice"] is None
    assert payload["step"] <= 6, payload


def test_between_slices_it_names_the_first_step_as_next():
    proc = _run(["where", "--json"])
    payload = _payload(proc)
    assert payload["next"]["number"] == 1


def test_the_position_states_its_own_confidence():
    """Where the machine cannot tell, it must say so rather than pick."""
    proc = _run(["where", "--json"])
    payload = _payload(proc)
    assert "derived" in payload
    assert isinstance(payload["derived"], bool)
    assert payload["basis"].strip()


def test_the_position_carries_the_act_as_well_as_the_step():
    proc = _run(["where", "--json"])
    payload = _payload(proc)
    assert payload["act"]["number"] in (1, 2, 3, 4)
    assert payload["act"]["name"].strip()


def test_the_json_form_carries_the_whole_agenda():
    proc = _run(["where", "--json"])
    payload = _payload(proc)
    assert len(payload["agenda"]) == 13
    assert payload["agenda"][0]["number"] == 1
    assert payload["agenda"][-1]["number"] == 13


# ---------------------------------------------------------------------------
# Property 3 - the bare command orients
# ---------------------------------------------------------------------------

def test_the_human_form_names_the_step_number_out_of_thirteen():
    proc = _run(["where"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "13" in proc.stdout


def test_the_human_form_says_what_is_next():
    proc = _run(["where"])
    assert "next" in proc.stdout.lower()


def test_the_human_form_prints_every_one_of_the_thirteen():
    """The operator asked for the full agenda every time, not a status line."""
    from scripts.utils.canopus_steps import STEPS

    proc = _run(["where"])
    for entry in STEPS:
        assert entry["name"].split(" - ")[0] in proc.stdout, entry["number"]


def test_the_human_form_marks_the_two_approvals_as_the_operators_own():
    proc = _run(["where"])
    assert proc.stdout.count("Approval") >= 2


def test_the_agenda_shows_which_act_each_step_belongs_to():
    from scripts.utils.canopus_steps import ACTS

    proc = _run(["where"])
    for entry in ACTS:
        assert entry["name"] in proc.stdout


# ---------------------------------------------------------------------------
# The skill surface
# ---------------------------------------------------------------------------

def test_the_skill_exists():
    """The whole point: the operator reaches the standard from chat, not by
    being handed an absolute path to a script."""
    assert _SKILL.is_file(), f"no skill at {_SKILL}"


def test_the_skill_declares_every_subcommand_in_its_argument_hint():
    import yaml

    text = _SKILL.read_text(encoding="utf-8")
    front = yaml.safe_load(text.split("---")[1])
    hint = front.get("argument-hint", "")
    for word in ("plan", "lock", "check", "release", "back"):
        assert word in hint, f"{word} missing from argument-hint: {hint!r}"


def test_the_skill_carries_the_workspace_frontmatter_contract():
    import yaml

    front = yaml.safe_load(_SKILL.read_text(encoding="utf-8").split("---")[1])
    assert front["name"] == "canopus"
    assert front["description"].strip()
    assert front["metadata"]["version"]
    routing = front["x-heading-routing"]
    assert routing["category"]
    assert routing["router"] in ("auto", "manual")
    orchestration = front["x-heading-orchestration"]
    assert orchestration["parallel_safe"] is False, (
        "the lifecycle mutates one lock; it is never parallel-safe")


def test_the_skill_absorbed_the_planning_gate():
    """Full scope, the operator's call: `/pre-impl` was the worst-named thing in
    the standard, and it named a position relative to an act rather than an act.
    Its content becomes `/canopus plan`."""
    assert not (_ROOT / ".claude" / "skills" / "pre-impl").exists(), (
        "pre-impl still exists as a separate skill")
    body = _SKILL.read_text(encoding="utf-8")
    assert "plan" in body.lower()


def test_the_skill_never_takes_an_approval_on_the_operators_behalf():
    """Approval 1 and Approval 2 are steps 6 and 12 and they are HIS. A skill
    that can self-approve turns a two-approval standard into a zero-approval
    one."""
    body = _SKILL.read_text(encoding="utf-8").lower()
    assert "never" in body
    assert "approv" in body


def test_the_step_numbers_agree_between_the_module_and_the_skill():
    """Two files describing one process is how a standard drifts. The skill may
    summarise, but it may not renumber."""
    from scripts.utils.canopus_steps import STEPS

    body = _SKILL.read_text(encoding="utf-8")
    for entry in STEPS:
        if entry["approval"]:
            assert str(entry["number"]) in body, (
                f"the skill does not mention approval step {entry['number']}")


@pytest.mark.parametrize("stale", ["12 steps", "twelve steps", "eleven steps"])
def test_the_design_document_no_longer_contradicts_itself_on_the_count(stale):
    """The document said eleven in one place and twelve in three others. The
    count is now thirteen, and a standard that cannot state its own length is not
    one."""
    from scripts.utils.paths import get_data_root

    try:
        spec = (get_data_root() / "docs" / "superpowers" / "specs"
                / "2026-08-01-canopus-v2-design.md")
    except Exception:
        pytest.skip("no data overlay on this machine")
    if not spec.is_file():
        pytest.skip("design spec not present")
    assert stale not in spec.read_text(encoding="utf-8")
