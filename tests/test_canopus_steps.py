"""/canopus, the operator's surface onto the engineering standard.

Retired from `tests/contract/2026-08-02-canopus-skill/` into the ordinary suite at
the release step, 2026-08-02. A contract left in place binds every later slice to
this one's behaviour; the coverage is worth keeping, the lock on it is not.

Re-pointed 2026-08-06 from thirteen moments to seven steps. The freeze is now
the approval COMMIT and the separation is a subagent dispatch, so the acts of
ceremony that carried those two facts are gone and the steps that remain are the
ones a human actually performs.

Narrowed again 2026-08-07, when the freeze lifecycle and the `where` display
were deleted. The position ladder and the orientation page they were asserted
through no longer exist, so the properties they carried are gone with them. What
is left is the pair that never depended on the machinery: the agenda is one
definition, and the skill describing it may not renumber it.

The gap this closes, measured on 2026-08-02: there is no `/canopus` skill. The
entire lifecycle lives inside `/pre-impl` and its reference file, which are
instructions for the ASSISTANT. The only way the operator could reach the
standard from his own workspace was to be handed an absolute path to a script,
which happened in this session and which he correctly refused as a regression.

That is a violation of `.claude/rules/console-first.md`, whose own validation
list asks "Can it be driven from Claude chat?". For Canopus the answer was "only
if the assistant types the path".

Two properties carry the weight:

1. **The agenda is one definition, and it is complete.** Seven numbered steps,
   four acts, exactly two of them the operator's own, contiguous numbering, every
   step in exactly one act, act ranges that tile the sequence without gap or
   overlap. A process whose own step list can drift between two files is a
   process nobody can be at a known point in.
2. **The skill may summarise the agenda and may never renumber it.** Two files
   describing one process is how a standard drifts, and the surface the operator
   types is the one that must not advertise a subcommand the tool does not
   carry.

Authoring rule: every import of the code under test happens INSIDE a test body.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SKILL = _ROOT / ".claude" / "skills" / "canopus" / "SKILL.md"


# ---------------------------------------------------------------------------
# Property 1 - the agenda is one definition, and it is complete
# ---------------------------------------------------------------------------

def test_there_are_seven_numbered_steps():
    from scripts.utils.canopus_steps import STEPS

    assert len(STEPS) == 7


def test_the_numbering_is_contiguous_from_one():
    from scripts.utils.canopus_steps import STEPS

    assert [s["number"] for s in STEPS] == list(range(1, 8))


def test_exactly_two_steps_are_the_operators_own():
    """The approval is his commit of the plan and the RED contract (step 4), and
    his word to ship (step 7). Nothing between them is his to give."""
    from scripts.utils.canopus_steps import approvals

    assert [a["number"] for a in approvals()] == [4, 7]


def test_every_act_the_operator_takes_part_in_ends_with_his_step():
    """Acts 1 and 4 close on the operator. Acts 2 and 3 are the build and the
    adversarial pass, and neither ends on his word."""
    from scripts.utils.canopus_steps import ACTS, step

    for entry in ACTS:
        last = step(entry["steps"][1])
        if entry["number"] in (1, 4):
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
    assert covered == list(range(1, 8))


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


def test_the_build_step_names_the_separation_that_is_the_whole_point():
    """Step 5 is a subagent dispatch, not an inline build. That IS the second of
    the two gaps this standard exists to close: the entity that decides what
    'done' means must not be the entity that decides it is done."""
    from scripts.utils.canopus_steps import step

    assert "subagent-driven-development" in step(5)["what"]


def test_the_adversarial_step_is_separate_from_the_shipping_that_follows_it():
    """They were quietly one thing. They are two: the attack that keeps finding
    things, then the operator's word and the work that closes the slice."""
    from scripts.utils.canopus_steps import step

    assert step(6)["approval"] is False
    assert step(7)["approval"] is True
    assert step(6)["act"] != step(7)["act"]


# ---------------------------------------------------------------------------
# Property 2 - position is derived, and admits what it cannot see
# ---------------------------------------------------------------------------

def test_the_machine_visible_steps_are_marked_and_are_a_minority():
    """Most of this process is human work that leaves no file behind. Saying so
    is the difference between a position and a guess."""
    from scripts.utils.canopus_steps import STEPS

    visible = [s["number"] for s in STEPS if s["machine_visible"]]
    assert visible, "nothing is machine-visible, so no position could be derived"
    assert len(visible) < len(STEPS), "everything claims to be machine-visible"
    assert 4 in visible and 7 in visible, (
        "the approval commit and the note are the two traces that exist here")


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
    for word in ("note", "check", "probe"):
        assert word in hint, f"{word} missing from argument-hint: {hint!r}"


def test_the_skill_no_longer_advertises_the_retired_subcommands():
    """The router row and the hint advertised `lock`, `release` and `back` after
    the machinery behind them was retired, and `where` after that. A command
    surface that names commands that do not exist is the same defect as a rule
    describing a deleted guard."""
    import yaml

    front = yaml.safe_load(_SKILL.read_text(encoding="utf-8").split("---")[1])
    surface = f"{front.get('argument-hint', '')} {front['x-heading-routing']['label']}"
    for retired in ("lock", "release", "back", "where"):
        assert retired not in surface, f"{retired} is still advertised: {surface!r}"


def test_the_skill_carries_the_workspace_frontmatter_contract():
    import yaml

    front = yaml.safe_load(_SKILL.read_text(encoding="utf-8").split("---")[1])
    assert front["name"] == "canopus"
    assert front["description"].strip()
    assert front["metadata"]["version"]
    routing = front["x-heading-routing"]
    assert routing["category"]
    assert routing["router"] in ("auto", "manual")
    capability = front["x-heading-capability"]
    for field in ("what", "how", "when"):
        assert capability[field].strip(), field
    orchestration = front["x-heading-orchestration"]
    assert orchestration["parallel_safe"] is False, (
        "`note` writes one slice's record; it is never parallel-safe")


def test_the_skill_absorbed_the_planning_gate():
    """Full scope, the operator's call: `/pre-impl` was the worst-named thing in
    the standard, and it named a position relative to an act rather than an act.
    Its content becomes the planning steps of the agenda."""
    assert not (_ROOT / ".claude" / "skills" / "pre-impl").exists(), (
        "pre-impl still exists as a separate skill")
    body = _SKILL.read_text(encoding="utf-8")
    assert "plan" in body.lower()


def test_the_skill_never_takes_an_approval_on_the_operators_behalf():
    """Steps 4 and 7 are HIS. A skill that can self-approve turns a two-approval
    standard into a zero-approval one."""
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
                f"the skill does not mention the operator's step {entry['number']}")


def test_the_skill_carries_the_four_rules_the_measurements_bought():
    """Each was learned expensively and a reader who does not know why will
    delete them. Named here so a rewrite cannot quietly drop one."""
    body = _SKILL.read_text(encoding="utf-8").lower()
    for phrase in ("topology", "timestamp", "executed suite", "demonstrated red",
                   "alive"):
        assert phrase in body, f"the skill no longer carries: {phrase}"
