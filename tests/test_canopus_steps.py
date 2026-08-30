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

import html
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SKILL = _ROOT / ".claude" / "skills" / "canopus" / "SKILL.md"

# Every operator-facing surface that names a `/canopus` subcommand. The skill and
# the two GENERATED router layers were already covered by the frontmatter test
# below; the catalogue page is the one that shipped a retired lifecycle to the
# published docs site, because it is hand-authored HTML with no `.md` source, so
# neither the frontmatter test nor the docs drift guard could see it.
_SUBCOMMAND_SURFACES = (
    _SKILL,
    _ROOT / ".claude" / "rules" / "skill-router.md",
    _ROOT / "reference" / "skill-router" / "operations.md",
    _ROOT / "docs" / "skills-operations-quality.html",
)

# The documents that state the vacuity criterion in prose to an operator.
_VACUITY_DOCUMENTS = (
    _SKILL,
    _ROOT / ".claude" / "skills" / "canopus" / "references" / "planning-gate.md",
    _ROOT / "docs" / "EXTENDING.md",
    # Added 2026-08-09 with the page itself: `docs/CANOPUS.md` is now the
    # canonical operator-facing description of the standard, so it is where a
    # reworded inversion of the criterion would do the most damage.
    _ROOT / "docs" / "CANOPUS.md",
)


def _real_subcommands() -> set:
    """The subcommand names `scripts/canopus.py` actually carries, from its parser.

    READ, never retyped. A test naming the set in its own source asserts that two
    lists agree at the moment somebody typed the second one; this one fails the
    next time a subcommand is added or removed while a document still names the
    old set, which is the whole defect it exists to catch.
    """
    import argparse

    from scripts.canopus import build_parser

    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError("canopus.py's parser carries no subparsers at all")


def _code_spans(text: str, path: Path) -> list:
    """Every literal command line on a surface, markup and entities removed.

    Only code spans are read, deliberately. Prose says "bare `/canopus` prints
    the seven steps", and a reader that took the next word after every `/canopus`
    would call `prints` an advertised subcommand. What an operator can COPY is
    what this test judges, and that is exactly the span the old catalogue card
    put `python scripts/canopus.py pack` inside.

    Split to ONE LINE PER COMMAND, because a fenced block is a span holding
    several. Reading the block whole takes the first `canopus` and the first word
    after it, which on a three-line block is a word from another line entirely.
    """
    if path.suffix == ".html":
        spans = [html.unescape(re.sub(r"<[^>]+>", "", span))
                 for span in re.findall(r"<code>(.*?)</code>", text, re.S)]
    else:
        # Markdown, including the two router tables, where a table cell escapes
        # the alternation's pipes as `\|`.
        spans = [span.replace(r"\|", "|")
                 for span in re.findall(r"`([^`]+)`", text)]
    return [line for span in spans for line in span.splitlines() if line.strip()]


def _advertised_subcommands(span: str) -> set:
    """The subcommand names one command span claims `/canopus` accepts.

    Two shapes, and both appear on these surfaces: an alternation
    (`/canopus [note | check | probe]`), and a concrete invocation
    (`python scripts/canopus.py probe tests/contract/...`). A span naming neither
    contributes nothing.
    """
    span = span.strip()
    if not re.search(r"(^|[/\s])canopus(\.py)?\b", span):
        return set()
    rest = re.split(r"canopus(?:\.py)?\b", span, maxsplit=1)[1].strip()
    alternation = re.match(r"\[([^\]]*)\]", rest)
    if alternation:
        return {word.strip() for word in alternation.group(1).split("|")
                if word.strip()}
    word = rest.split()[0] if rest.split() else ""
    # A flag, a path or an argument placeholder is not a subcommand claim.
    return {word} if re.fullmatch(r"[a-z][a-z-]*", word) else set()


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
# Property 1b - position is derived, and admits what it cannot see
#
# RENUMBERED 2026-08-30. This header said "Property 2", which the module
# docstring defines as the skill-may-never-renumber rule; the section below is
# about machine visibility. The docstring's own account of the 2026-08-07
# narrowing says the position ladder "no longer exists, so the properties they
# carried are gone with them", yet a position-derivation section survived it,
# unnumbered by the docstring's list. It is a corollary of Property 1 -- the
# agenda is one definition, so what can be OBSERVED of it is derived from that
# definition rather than guessed -- and is labelled 1b rather than given a
# number the docstring does not issue.
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
    """Read the set from the parser; do not retype it here.

    Found by the 2026-08-23 audit. This test pinned the set with a literal
    `("note", "check", "probe")` in a file whose own `_real_subcommands()`
    docstring says "READ, never retyped" - and the parser-derived set was used
    by exactly one other test. Add a fourth subcommand and the literal version
    stayed green while the skill's hint went stale, which is precisely the drift
    the file exists to catch.
    """
    import yaml

    real = _real_subcommands()
    assert real, "no subcommands were read from the parser, so nothing was checked"

    text = _SKILL.read_text(encoding="utf-8")
    front = yaml.safe_load(text.split("---")[1])
    hint = front.get("argument-hint", "")
    missing = sorted(name for name in real if name not in hint)
    assert missing == [], (
        f"argument-hint does not advertise {missing}; "
        f"the tool carries {sorted(real)} and the hint is {hint!r}"
    )


def test_the_skill_no_longer_advertises_the_retired_subcommands():
    """The router row and the hint advertised `lock`, `release` and `back` after
    the machinery behind them was retired, and `where` after that. A command
    surface that names commands that do not exist is the same defect as a rule
    describing a deleted guard."""
    import yaml

    front = yaml.safe_load(_SKILL.read_text(encoding="utf-8").split("---")[1])
    surface = f"{front.get('argument-hint', '')} {front['x-heading-routing']['label']}"
    # WORD MATCH, not substring, since 2026-08-30. `retired not in surface` is a
    # raw substring test over ordinary English, so "block", "deadlock",
    # "background", "backfill", "backward", "wherever" and "elsewhere" each
    # failed it while advertising no retired subcommand at all. The sibling
    # `_advertised_subcommands` in this same file already does token-level
    # matching; this one did not.
    advertised = _words(surface)
    for retired in ("lock", "release", "back", "where"):
        assert retired not in advertised, (
            f"{retired} is still advertised: {surface!r}")


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


def _numbered_items(body: str) -> dict:
    """number -> [text of every numbered agenda item and `Step N` heading]."""
    items: dict = {}
    for match in re.finditer(r"^\s{0,3}(\d+)\.\s+(.*)$", body, re.M):
        items.setdefault(int(match.group(1)), []).append(match.group(2))
    for match in re.finditer(r"^#+\s*Step\s+(\d+)\s*[^\w]*\s*(.*)$", body, re.M):
        items.setdefault(int(match.group(1)), []).append(match.group(2))
    return items


def _words(text: str) -> set:
    """Lowercased alphabetic words, so `/scrutinize` matches `Scrutinize`."""
    return set(re.findall(r"[a-z]+", text.lower()))


def test_the_step_numbers_agree_between_the_module_and_the_skill():
    """Two files describing one process is how a standard drifts. The skill may
    summarise, but it may not renumber.

    TIGHTENED 2026-08-30. This asserted `entry["number"] in numbered`, where
    `numbered` is an unordered SET pooled from every numbered list and every
    `Step N` heading in the document. Membership says the integers 4 and 7
    appear somewhere; it says nothing about WHICH item they label. A skill that
    renumbered the operator's steps to 3 and 6 still passed, because any
    surviving seven-item list contributes 4 and 7 to the set -- and the byte
    budget list under "Step 3" is exactly such a list. The step's NAME is now
    required to sit on the line carrying its number, and to sit on no other
    number, which is what "may not renumber" means.
    """
    from scripts.utils.canopus_steps import STEPS

    body = _SKILL.read_text(encoding="utf-8")
    items = _numbered_items(body)
    for entry in STEPS:
        if not entry["approval"]:
            continue
        number, name = entry["number"], entry["name"]
        wanted = _words(name)
        assert number in items, (
            f"the skill does not carry the operator's step {number} as a "
            f"numbered step; found {sorted(items)}")
        assert any(wanted <= _words(text) for text in items[number]), (
            f"step {number} is numbered but not NAMED {name!r} there; the "
            f"skill's item {number} reads {items[number]}")
        elsewhere = sorted(other for other, texts in items.items()
                           if other != number
                           and any(wanted <= _words(t) for t in texts))
        assert not elsewhere, (
            f"the skill also numbers {name!r} as step(s) {elsewhere}; the "
            f"operator's step {number} has been renumbered")


def test_the_plan_byte_budget_agrees_between_the_module_and_the_skill():
    """The plan's own byte budget, held in lockstep between code and prose.

    Promoted here from `tests/contract/2026-08-07-canopus-gap-and-skip/`, ahead
    of that contract retiring, because step 7 removes the contract directory and
    this was the only thing asserting the numbers the skill advertises. A test
    that disappears with the scaffolding it was written on leaves a claim in the
    prose with nothing behind it, which is the same shape as never having
    checked.

    Derived in the slice's plan from 99 real plans. `PLAN_BYTE_WARN` mirrors the
    SKILL.md warn `skill-metadata-check.py` already enforces, so the workspace
    carries one number rather than two. `PLAN_BYTE_HARD` is 24 KiB, the first
    binary-round number above the measured median of 23,704, clearing it by 872
    bytes. The contract's own docstring said "one byte above" the median until
    2026-08-07; that is the same false arithmetic `c0547cb` removed from the code
    comment, and it is corrected here rather than carried forward.

    The needles are FORMATTED FROM THE CONSTANTS rather than written out, so a
    change to either number fails this test instead of quietly leaving the prose
    behind. The slice's own after-build reading measured why that matters: seven
    of this file's tests survived all three wrong implementations, and every one
    of them greps for a phrase hardcoded in the test file, so nothing about the
    code can move them.
    """
    from scripts.utils.canopus_steps import PLAN_BYTE_HARD, PLAN_BYTE_WARN

    body = _SKILL.read_text(encoding="utf-8")

    assert (PLAN_BYTE_WARN, PLAN_BYTE_HARD) == (16384, 24576)
    assert [f"{PLAN_BYTE_WARN:,}" in body, f"{PLAN_BYTE_HARD:,}" in body] == [True, True]


def test_no_operator_facing_surface_advertises_a_subcommand_the_tool_lacks():
    """The hole the frontmatter test above could not see.

    `test_the_skill_no_longer_advertises_the_retired_subcommands` reads the
    skill's frontmatter, and the skill is one of four surfaces. Measured
    2026-08-07: `docs/skills-operations-quality.html`, which is LIVE on the
    published docs site, still advertised `/canopus [plan | lock | check |
    release | back]` and told the operator to run `python scripts/canopus.py
    pack` — five commands that do not exist — for a full day after the machinery
    behind them was deleted, because the page is hand-authored HTML with no
    `.md` source and neither that test nor the docs drift guard reads it.

    The real set is READ FROM THE PARSER, so the next subcommand change fails
    here instead of shipping.
    """
    real = _real_subcommands()
    assert real, "no subcommands were read from the parser, so nothing was checked"
    for path in _SUBCOMMAND_SURFACES:
        assert path.is_file(), f"a named surface is missing: {path}"
        text = path.read_text(encoding="utf-8")
        for span in _code_spans(text, path):
            for name in _advertised_subcommands(span):
                assert name in real, (
                    f"{path.relative_to(_ROOT)} advertises `canopus {name}`, "
                    f"which the tool does not carry: {sorted(real)}. "
                    f"The span was: {span.strip()!r}"
                )


def test_a_test_is_vacuous_when_it_never_fails_under_the_stub(tmp_path):
    """The behaviour the three documents describe, measured rather than asserted.

    Three tests, one per outcome the probe can read on a red contract:
      * `test_vacuous` PASSES under both stub runs and asserts nothing.
      * `test_reads_the_value` FAILS under both, because the stub resolved its
        import and the assertion then compared against a stub value. Failing is
        the ONLY outcome that proves a test read what the stub carried.
      * `test_errors_in_a_fixture` ERRORS under both, because the probe's own
        stand-in reached `json.loads`, which type-checks its argument. Not
        measured is not proved innocent, so it is named vacuous too.

    The third is the case the documents got backwards: they named erroring as
    the criterion and left the passing case, which is the dominant one,
    undescribed.
    """
    import json

    from scripts.utils.canopus_contract import run_null_stub

    contract = tmp_path / "c"
    contract.mkdir()
    (contract / "test_one.py").write_text(
        "import json\n"
        "\n"
        "import pytest\n"
        "\n"
        "\n"
        "@pytest.fixture\n"
        "def subject():\n"
        "    from absent_thing import raw\n"
        "    return json.loads(raw())\n"
        "\n"
        "\n"
        "def test_vacuous():\n"
        "    from absent_thing import answer\n"
        "    answer()\n"
        "    assert True\n"
        "\n"
        "\n"
        "def test_reads_the_value():\n"
        "    from absent_thing import answer\n"
        "    assert answer() == 42\n"
        "\n"
        "\n"
        "def test_errors_in_a_fixture(subject):\n"
        "    assert subject['k'] == 1\n",
        encoding="utf-8",
    )

    vacuous = run_null_stub([contract], tmp_path)

    assert ("c/test_one.py", "test_vacuous") in vacuous
    assert ("c/test_one.py", "test_errors_in_a_fixture") in vacuous
    assert ("c/test_one.py", "test_reads_the_value") not in vacuous
    assert json  # the import above is what makes the fixture error under the stub


def test_the_documents_state_the_vacuity_direction_the_code_implements():
    """Bind the prose to the code by the PROPERTY, not by an exact sentence.

    Three operator-facing documents said "a test that ERRORS against the stub is
    vacuous". Read literally that sends a builder to strengthen the tests that
    already assert something and leave the vacuous ones alone, which inverts the
    gate's whole purpose.

    The binding is deliberately not a string comparison against a blessed
    sentence, which would fail on every rewording and pass on a reworded
    inversion. It derives the discriminating outcome from `UNPROVED_OUTCOMES`,
    the tuple `run_null_stub` actually classifies with, and asks of each
    document's vacuity prose only that it name that outcome. A future change to
    the code's rule therefore fails this test until the prose follows it.
    """
    from xml.etree import ElementTree

    from scripts.utils.canopus_contract import UNPROVED_OUTCOMES, _outcome

    # Every token the reader can emit, EXERCISED rather than retyped and
    # deliberately not derived from `UNPROVED_OUTCOMES` itself. Taking the
    # universe from the constant under test makes the complement below `failure`
    # for any value of it, so the tripwire could never fire: measured, a
    # `UNPROVED_OUTCOMES` cut to `("error",)` left this assertion green.
    every_outcome = set()
    for tag in ("failure", "error", "skipped", None):
        case = ElementTree.Element("testcase")
        if tag:
            # `SubElement` CREATES the child and appends it. The outer
            # `case.append(...)` here until 2026-08-30 appended the same element
            # a second time, so every tagged synthetic case carried two
            # identical children -- a shape no real JUnit producer emits, in a
            # test whose docstring says the tokens are "EXERCISED rather than
            # retyped" precisely so they stand in for parser output.
            ElementTree.SubElement(case, tag)
            assert len(case) == 1, "the synthetic case is not the shape git emits"
        every_outcome.add(_outcome(case))
    assert len(every_outcome) == 4, every_outcome
    proving = every_outcome - set(UNPROVED_OUTCOMES)
    assert proving == {"failure"}, (
        f"the code's vacuity rule changed: {sorted(proving)} now proves a test "
        f"asserts something, so the three operator-facing documents need "
        f"rewording before this test is updated")

    for path in _VACUITY_DOCUMENTS:
        text = path.read_text(encoding="utf-8")
        sentences = [s for s in re.split(r"(?<=[.:])\s+", text)
                     if "vacuous" in s.lower()]
        assert sentences, f"{path.relative_to(_ROOT)} no longer states the criterion"
        criterion = " ".join(sentences).lower()
        assert "fail" in criterion, (
            f"{path.relative_to(_ROOT)} states the vacuity criterion without "
            f"naming the one outcome that clears a test ({sorted(proving)[0]}): "
            f"{criterion!r}")
        for unproved in UNPROVED_OUTCOMES:
            # An unproved outcome may be MENTIONED, and all three are, but never
            # as the criterion on its own: that is the exact inverted sentence
            # this test was written for.
            assert not re.search(
                rf"\b{unproved[:-2] if unproved.endswith('ed') else unproved}"
                rf"\w*\b[^.]{{0,60}}\bis vacuous\b", criterion), (
                f"{path.relative_to(_ROOT)} names {unproved!r} as the vacuity "
                f"criterion; only failing under the stub clears a test")


def test_the_skill_carries_the_four_rules_the_measurements_bought():
    """Each was learned expensively and a reader who does not know why will
    delete them. Named here so a rewrite cannot quietly drop one."""
    body = _SKILL.read_text(encoding="utf-8").lower()
    for phrase in ("topology", "timestamp", "executed suite", "demonstrated red",
                   "alive"):
        assert phrase in body, f"the skill no longer carries: {phrase}"
