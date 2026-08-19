"""Documentation-style checker (.claude/rules/documentation-style.md).

Covers each check in scripts/ste-check.py against minimal inputs, the markdown
preparation that must run before any check (code fences, skip blocks), and the
scope contract: the checker's file list can never widen past the rule that
authorises it.

Run: python3 -m pytest tests/test_ste_check.py
"""
import fnmatch
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "ste-check.py"
RULE = ROOT / ".claude" / "rules" / "documentation-style.md"


@pytest.fixture(scope="module")
def ste():
    spec = importlib.util.spec_from_file_location("ste_check_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def types_in(result):
    return {f["type"] for f in result["findings"]}


# ============================================================
# Scope contract
# ============================================================

def rule_paths():
    text = RULE.read_text(encoding="utf-8")
    _, frontmatter, _ = text.split("---", 2)
    return yaml.safe_load(frontmatter)["paths"]


def test_checked_globs_are_authorised_by_the_rule(ste):
    """Every file the checker audits must be one the rule actually governs."""
    authorised = rule_paths()
    for glob in ste.CHECKED_GLOBS:
        assert any(fnmatch.fnmatch(glob, pattern) for pattern in authorised), (
            f"{glob} is checked but not listed in the rule's paths: frontmatter"
        )


def test_scope_resolves_to_existing_files(ste):
    resolved = ste.resolve_scope()
    assert resolved, "no in-scope documentation file resolved on disk"
    assert all(p.exists() for p in resolved)


def _hook(hook_id):
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    for repo in config["repos"]:
        for hook in repo.get("hooks", []):
            if hook.get("id") == hook_id:
                return hook
    return None


def _documentation_style_hook():
    return _hook("documentation-style")


def test_the_gate_is_armed_in_pre_commit_and_ci():
    """The checker earned a gate on 2026-08-11; assert it is still wired.

    Errors only. A `--strict` gate would fail on the passive-voice heuristic,
    which has no part-of-speech tagger behind it.
    """
    hook = _documentation_style_hook()
    assert hook, "the documentation-style pre-commit hook is gone"
    assert "--all" in hook["entry"] and "--strict" not in hook["entry"]
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "scripts/ste-check.py --all" in ci, "the CI step is gone"


def test_the_hook_fires_on_exactly_the_files_the_checker_audits(ste):
    """A `files:` pattern narrower than CHECKED_GLOBS is a silently unguarded page.

    The hook runs `--all` rather than the staged paths, so its pattern decides
    only WHEN the gate runs. A page missing from the pattern can be edited and
    committed without the gate firing once.
    """
    import re

    pattern = re.compile(_documentation_style_hook()["files"])
    uncovered = [g for g in ste.CHECKED_GLOBS if not pattern.search(g)]
    assert not uncovered, (
        f"these audited pages do not trigger the pre-commit gate: {uncovered}"
    )


def test_the_skill_corpus_is_gated_too(ste):
    """The other half of the rule's scope earned its gate on 2026-08-17.

    `--all` covered twelve pages while the rule also governs ninety-six skill
    bodies, so a green `--all` read as a green corpus for as long as the skill
    half went unmeasured. It measured 300, of which 83 were splitter defects and
    217 were real; the corpus is at zero, so the gate can hold it there.

    Errors only, for the same reason `--all` is errors only: the warning checks
    are heuristics without a part-of-speech tagger behind them.
    """
    hook = _hook("documentation-style-skills")
    assert hook, "the skill-corpus pre-commit hook is gone"
    assert "--skills" in hook["entry"] and "--strict" not in hook["entry"]
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "scripts/ste-check.py --skills" in ci, "the CI step is gone"


def test_the_skills_hook_fires_on_every_skill_body(ste):
    """A `files:` pattern narrower than the resolved corpus is an unguarded skill.

    The hook runs `--skills` rather than the staged paths, so its pattern decides
    only WHEN the gate runs. A skill outside the pattern can be edited and
    committed without the gate firing once.
    """
    import re

    pattern = re.compile(_hook("documentation-style-skills")["files"])
    uncovered = [
        p.relative_to(ROOT).as_posix()
        for p in ste.resolve_skill_scope()
        if not pattern.search(p.relative_to(ROOT).as_posix())
    ]
    assert not uncovered, f"these skills do not trigger the gate: {uncovered}"


def test_the_vendored_skill_is_gated_like_any_other(ste):
    """No exemption. The one vendored skill was fixed instead of carved out.

    An exemption would have hidden a vendored skill's style debt forever, and
    the whole point of arming this gate was that unmeasured is not clean. The
    in-repo copy is what `skills-lock.json` pins -- the lock protects the copy
    that ships, not upstream's bytes, and `--relock` is a supported operation --
    so adapting it is a re-lock, not a fork. The lock's `note` field carries the
    instruction to re-apply the adaptation after a re-vendor.
    """
    vendored = ROOT / ".claude" / "skills" / "ast-grep" / "SKILL.md"
    assert vendored in ste.resolve_skill_scope(), "the vendored skill fell out of scope"

    lock = json.loads((ROOT / "skills-lock.json").read_text(encoding="utf-8"))
    note = lock["skills"]["ast-grep"].get("note", "")
    assert "re-vendor" in note.lower(), (
        "the lock must tell a re-vendor to re-apply the style adaptation"
    )


def test_explanatory_docs_are_out_of_scope(ste):
    """The narrative pages must stay out - flattening them is the failure mode."""
    for excluded in ("docs/ARCHITECTURE.md", "docs/THREAT-MODEL.md",
                     "docs/DESIGN-CHECK.md", "docs/RELEASE-NOTES.md"):
        assert excluded not in ste.CHECKED_GLOBS


def test_a_sentence_boundary_before_emphasis_still_splits(ste):
    """A period followed by `**bold**` is a boundary like any other.

    The splitter's lookahead accepted a capital, a bracket or a quote and not an
    emphasis marker, so `... two. **You decide.** No code ...` measured as ONE
    sentence of 54 words and reported an error against prose that was already
    three clean sentences. Found 2026-08-17 while bringing the skill corpus
    down: the corpus was being rewritten to satisfy a broken measurement.
    """
    joined = (
        "Alpha bravo charlie delta echo foxtrot golf hotel india juliett kilo "
        "lima mike november. **Oscar papa quebec romeo sierra tango uniform "
        "victor whiskey xray yankee zulu one two three.**"
    )
    assert len(ste.split_sentences(joined)) == 2, "bold after a period blocks the split"

    for lead in ("*italic sentence here.*", "_underscored sentence here._"):
        text = f"Alpha bravo charlie delta echo foxtrot golf. {lead}"
        assert len(ste.split_sentences(text)) == 2, f"{lead!r} blocks the split"


def test_a_sentence_boundary_after_emphasis_still_splits(ste):
    """The mirror half: the CLOSING marker sits between the period and the space.

    `**You decide.** No code reads them.` put `.**` before the whitespace, so the
    lookbehind never saw the terminator and the bolded sentence merged with the
    one after it. Both halves of the bug had to go; fixing only the opener left
    every bolded lead-in still joined to its successor.
    """
    assert len(ste.split_sentences("Alpha bravo. **Charlie delta.** Echo foxtrot.")) == 3
    assert len(ste.split_sentences("Alpha bravo. *Charlie delta.* Echo foxtrot.")) == 3
    assert len(ste.split_sentences("Alpha bravo. _Charlie delta._ Echo foxtrot.")) == 3


def test_a_sentence_that_ends_inside_a_closer_still_splits(ste):
    """A quote or a bracket can sit between the terminator and the space.

    The third instance of the same bug. The lookbehind enumerated the emphasis
    markers and nothing else, so `... both work." If two variants ...` and
    `(see below.) Next` each measured as one sentence. Found 2026-08-17 in
    `.claude/skills/burst/SKILL.md`, reported as 42 words against four short
    ones. The fix is a closer CLASS rather than another enumerated shape, so
    the next closer somebody writes is already covered.
    """
    assert len(ste.split_sentences('Alpha bravo. "Charlie delta." Echo foxtrot.')) == 3
    assert len(ste.split_sentences("Alpha bravo (charlie delta.) Echo foxtrot.")) == 2
    assert len(ste.split_sentences("Alpha bravo [charlie delta.] Echo foxtrot.")) == 2
    assert len(ste.split_sentences("Alpha bravo. 'Charlie delta.' Echo foxtrot.")) == 3
    assert len(ste.split_sentences("Alpha bravo. **Charlie delta.** Echo foxtrot.")) == 3


def test_a_blockquote_marker_is_not_read_as_prose(ste):
    """`>` at the start of a wrapped blockquote line is structure, not a word.

    The splitter's lookahead accepts a capital, a bracket, a quote or an
    emphasis marker, and `>` is none of them. So every sentence that ENDED at a
    blockquote line break merged with the sentence on the next line, and the
    joined pair measured over the limit. Found 2026-08-17 in
    `.claude/skills/push-updates/SKILL.md`, where a four-sentence R16 callout
    measured as two sentences of 39 and 31 words. Same family as the emphasis
    boundary above: the corpus was being rewritten to satisfy a broken
    measurement.
    """
    quoted = (
        "> Alpha bravo charlie delta echo foxtrot golf hotel india.\n"
        "> Juliett kilo lima mike november oscar papa quebec romeo.\n"
        "> Sierra tango uniform victor whiskey xray yankee zulu.\n"
    )
    units = ste.parse_units(ste.strip_noise(quoted))
    assert len(units) == 1, "the callout should still read as one paragraph"
    assert len(ste.split_sentences(units[0]["text"])) == 3, (
        "the blockquote marker blocks the sentence split"
    )
    assert not [f for f in ste.audit(quoted)["findings"] if f["severity"] == "error"]


def test_the_warning_callout_check_survives_marker_stripping(ste):
    """Stripping `>` must not disarm rule 8, the one with a physical cost.

    `check_warning_at_end` reads the same prepared text, and its callout regex
    matched on the `>` prefix among others. A warning that closes a procedure is
    still a warning after the marker is gone.
    """
    text = (
        "## Procedure\n\n"
        "1. Open the valve.\n"
        "2. Close the valve.\n"
        "> **Warning:** the line is pressurised.\n"
    )
    assert "warning_at_end" in types_in(ste.audit(text))


def test_the_split_does_not_fire_on_an_abbreviation(ste):
    """The guard that was already there must survive the widened lookahead."""
    assert len(ste.split_sentences("Read the SKILL.md file for the spec.")) == 1
    assert len(ste.split_sentences("It runs on v1.2 of the API.")) == 1


def test_all_does_not_claim_the_coverage_it_does_not_have(ste):
    """scope-claims, turned on this checker itself.

    The rule governs the twelve pages AND the instruction bodies of every
    `.claude/skills/**/SKILL.md`. `--all` resolves the twelve pages only, and
    described itself as "every in-scope file", which reads as a clean corpus to
    anyone who runs it. Measured 2026-08-16: 74 of 96 skills carry 300 errors
    that this wording said did not exist.
    """
    help_text = ste.ALL_HELP.lower()
    assert "in-scope" not in help_text, (
        "--all says 'in-scope', but the rule's scope is larger than CHECKED_GLOBS"
    )
    assert "skill" in help_text, (
        "--all must name the part of the rule's scope it does NOT cover"
    )


def test_skills_scope_resolves_the_skill_corpus(ste):
    """The ungated half of the rule's scope must at least be measurable.

    A gap nobody can measure from the CLI is a gap that gets argued about from
    memory. `--skills` is the number, not the gate.
    """
    resolved = ste.resolve_skill_scope()
    assert len(resolved) > 50, f"only {len(resolved)} SKILL.md files resolved"
    assert all(p.name == "SKILL.md" for p in resolved)
    assert not set(resolved) & set(ste.resolve_scope()), (
        "the two scopes overlap; a file would be audited twice"
    )


def test_the_skill_scope_is_authorised_by_the_rule():
    """The same contract CHECKED_GLOBS answers to: audit only what the rule governs."""
    authorised = rule_paths()
    assert any(fnmatch.fnmatch(".claude/skills/checkpoint/SKILL.md", p) for p in authorised), (
        "the rule's paths: frontmatter does not govern SKILL.md bodies, so "
        "--skills would audit files no rule authorises"
    )


# ============================================================
# Text preparation
# ============================================================

def test_code_fence_is_not_prose(ste):
    """A long shell command must not read as an over-long sentence."""
    text = (
        "Run the installer.\n\n"
        "```bash\n"
        "uv run python scripts/install-bridge-service.sh --with-every-single-option "
        "--and-another-one --plus-more --keep-going --until-well-past-the-limit --done\n"
        "```\n"
    )
    assert types_in(ste.audit(text)) == set()


def test_inline_code_and_urls_are_stripped(ste):
    text = "1. Run `uv sync --all-extras --group dev` now.\n"
    assert ste.audit(text)["summary"]["errors"] == 0


def test_skip_block_is_exempt(ste):
    text = (
        "<!-- ste-skip-start -->\n"
        "1. In order to proceed and then continue, simply utilize the and/or form.\n"
        "<!-- ste-skip-end -->\n"
    )
    assert ste.audit(text)["findings"] == []


def test_frontmatter_is_out_of_scope_and_does_not_move_the_body(ste):
    """The rule scopes the checker to instruction BODIES, and a line number must land.

    Both halves in one fixture, because they failed as one. The frontmatter and
    the body carry the SAME passive construction: only the body may report, and
    the body's report must name line 10, the line the reader opens the file at.

    The third assertion is the one that pins the fix. Frontmatter was already
    stripped before 2026-08-19, but by DELETION, so every finding below it came
    back one frontmatter-height too high. Measured that day: 156 of 431 findings
    across the 96 skill bodies (36%) named a line at or above their own
    frontmatter, which reads as the checker auditing YAML the rule scopes out.
    It was auditing prose and misreporting where. Drop the offset and the count
    stays right while every excerpt points at the wrong line - the silent half.
    """
    text = (
        "---\n"                                             # 1
        "name: example-skill\n"                             # 2
        "description: The output is generated by it.\n"     # 3
        "metadata:\n"                                       # 4
        '  version: "1.0"\n'                                # 5
        "---\n"                                             # 6
        "\n"                                                # 7
        "# Example\n"                                       # 8
        "\n"                                                # 9
        "The output is generated by it.\n"                  # 10
    )
    passives = [f for f in ste.audit(text)["findings"] if f["type"] == "passive_voice"]
    assert len(passives) == 1, "the frontmatter copy was audited as prose"
    assert passives[0]["line"] == 10, (
        f"reported line {passives[0]['line']}, but the body sentence is on line 10"
    )


def test_a_code_fence_does_not_move_the_lines_below_it(ste):
    """Frontmatter was the biggest block that shifted the count, not the only one.

    A fence and a multi-line HTML comment displace everything after them by the
    same mechanism, so `_blank_out` is height-preserving for all of them rather
    than an offset applied once to the frontmatter.
    """
    text = (
        "# Install\n"                                       # 1
        "\n"                                                # 2
        "```bash\n"                                         # 3
        "uv sync --all-extras --group dev\n"                # 4
        "```\n"                                             # 5
        "\n"                                                # 6
        "<!-- a comment\n"                                  # 7
        "     spanning three\n"                             # 8
        "     lines -->\n"                                  # 9
        "\n"                                                # 10
        "The unit is created by the installer.\n"           # 11
    )
    passives = [f for f in ste.audit(text)["findings"] if f["type"] == "passive_voice"]
    assert len(passives) == 1
    assert passives[0]["line"] == 11, (
        f"reported line {passives[0]['line']}, but the sentence is on line 11"
    )


def test_a_mid_line_block_does_not_split_the_sentence_around_it(ste):
    """A comment opened mid-sentence must not hide the sentence's length.

    `_blank_out` empties the lines a removed block covered, and an empty line
    ends a paragraph. So an aside opened mid-line inside a paragraph split that
    paragraph in two, and a sentence running through the aside was measured in
    halves. Reproduced 2026-08-20: the 26-word sentence below measured 11 words
    and 15 words, and the prose limit of 25 reported nothing at all. The block
    is noise, but the prose on either side of it is one sentence.
    """
    text = (
        "# Heading\n"                                                    # 1
        "\n"                                                             # 2
        "Alpha bravo charlie delta echo foxtrot golf hotel india "       # 3
        "juliett kilo <!-- an aside\n"
        "     that spans\n"                                              # 4
        "     three lines --> lima mike november oscar papa quebec "     # 5
        "romeo sierra tango uniform victor whiskey xray yankee zulu.\n"
    )
    units = ste.parse_units(ste.strip_noise(text))
    assert len(units) == 1, "the aside split the paragraph into separate units"
    assert ste.word_count(units[0]["text"]) == 26

    long = [f for f in ste.audit(text)["findings"] if f["type"] == "sentence_too_long"]
    assert len(long) == 1, "the over-length sentence went unmeasured"
    assert long[0]["line"] == 3, (
        f"reported line {long[0]['line']}, but the paragraph starts on line 3"
    )


def test_a_mid_line_block_still_holds_the_lines_below_it_in_place(ste):
    """The property the mid-line fix must not cost: a line number still lands.

    `_blank_out` blanks rather than removes so that every finding names the line
    the reader opens the file at. A mid-line block is marked instead of left
    empty, and the marking has to be exactly as tall as what it replaced.
    """
    text = (
        "Alpha bravo charlie <!-- an aside\n"                # 1
        "     that spans\n"                                  # 2
        "     four lines\n"                                  # 3
        "     in total --> delta echo.\n"                    # 4
        "\n"                                                 # 5
        "The unit is created by the installer.\n"            # 6
    )
    passives = [f for f in ste.audit(text)["findings"] if f["type"] == "passive_voice"]
    assert len(passives) == 1
    assert passives[0]["line"] == 6, (
        f"reported line {passives[0]['line']}, but the sentence is on line 6"
    )


def test_a_mid_line_skip_block_behaves_like_a_mid_line_comment(ste):
    """The same shape through the other multi-line blanker.

    `ste-skip` and an HTML comment are blanked by the same helper, so an author
    who exempts a phrase mid-paragraph must not have the paragraph cut in two
    underneath them.
    """
    text = (
        "Alpha bravo charlie delta echo foxtrot golf hotel india juliett "  # 1
        "kilo <!-- ste-skip-start -->\n"
        "in order to\n"                                                     # 2
        "<!-- ste-skip-end --> lima mike november oscar papa quebec "       # 3
        "romeo sierra tango uniform victor whiskey xray yankee zulu.\n"
    )
    units = ste.parse_units(ste.strip_noise(text))
    assert len(units) == 1, "the skip block split the paragraph into separate units"
    assert "sentence_too_long" in types_in(ste.audit(text))
    assert "banned_phrase" not in types_in(ste.audit(text)), (
        "the skipped phrase was audited"
    )


def test_a_standalone_block_still_separates_the_paragraphs_around_it(ste):
    """The counterweight: a block on its own line keeps ending the paragraph.

    A fence between two paragraphs left blank lines behind, and those blank
    lines are what kept the two paragraphs apart. Only a block that opens
    mid-line is treated as interior to a paragraph.
    """
    text = (
        "Alpha bravo charlie delta echo.\n"     # 1
        "```bash\n"                             # 2
        "uv sync\n"                             # 3
        "```\n"                                 # 4
        "Foxtrot golf hotel india juliett.\n"   # 5
    )
    units = ste.parse_units(ste.strip_noise(text))
    assert [u["line"] for u in units] == [1, 5], (
        "the fence must still separate the two paragraphs"
    )


def test_a_mid_line_block_does_not_break_a_procedure_apart_for_rule_8(ste):
    """Rule 8 reads the same prepared text, so it needs the mark dropped too.

    `check_warning_at_end` walks the prepared lines itself rather than the units,
    and it holds a procedure open across an indented continuation line. A mid-line
    block leaves a MARKED line where plain blanking left an empty one, and the
    mark starts with `@`, which is neither leading whitespace nor a callout - so
    an unstripped mark closes the procedure early and the warning below it stops
    being the last line of anything.

    Measured 2026-08-20 by deleting the two-line strip in `check_warning_at_end`
    and running the suite: 45 of 45 tests still passed while this fixture went
    from one `warning_at_end` error to none. That is the shape of a silent
    regression, so the strip gets its own guard.
    """
    text = (
        "1. Do the thing.\n"                    # 1
        "   Alpha bravo <!-- an aside\n"        # 2
        "   that spans --> charlie.\n"          # 3
        "   **Warning:** be careful here.\n"    # 4
    )
    findings = [f for f in ste.audit(text)["findings"] if f["type"] == "warning_at_end"]
    assert len(findings) == 1, "the mark closed the procedure before the warning"
    assert findings[0]["line"] == 4, (
        f"reported line {findings[0]['line']}, but the callout is on line 4"
    )


def test_no_skill_corpus_finding_lands_in_its_own_frontmatter(ste):
    """The corpus-level guard for the same defect, so it cannot come back quietly.

    A unit test pins the fixture; this pins the 96 real files the gate reads. On
    2026-08-19 this counted 156; it counts zero.
    """
    import re

    misplaced = []
    for path in ste.resolve_skill_scope():
        text = path.read_text(encoding="utf-8")
        match = re.match(r"^---\n.*?\n---\n", text, re.DOTALL)
        if not match:
            continue
        height = match.group(0).count("\n")
        total = len(text.splitlines())
        for finding in ste.audit(text)["findings"]:
            if finding["line"] <= height or finding["line"] > total:
                misplaced.append((path.name, finding["line"], height))
    assert not misplaced, (
        f"{len(misplaced)} finding(s) report a line inside the frontmatter or past "
        f"the end of file: {misplaced[:5]}"
    )


def test_table_rows_and_headings_are_skipped(ste):
    text = (
        "## In order to configure\n\n"
        "| Term | Meaning |\n"
        "|------|---------|\n"
        "| `drift` | In order to describe unconscious movement out of a state. |\n"
    )
    assert ste.audit(text)["findings"] == []


# ============================================================
# Unit segmentation
# ============================================================

def test_numbered_item_is_a_step_and_bullet_is_prose(ste):
    units = ste.parse_units("1. Open the file.\n\n- Open the file.\n")
    assert [u["kind"] for u in units] == ["step", "prose"]


def test_step_limit_is_tighter_than_prose_limit(ste):
    """Twenty-two words: over the step limit, under the prose limit."""
    sentence = " ".join(["word"] * 21) + " end."
    assert "sentence_too_long" in types_in(ste.audit(f"1. {sentence}\n"))
    assert "sentence_too_long" not in types_in(ste.audit(f"{sentence}\n"))


def test_prose_over_twenty_five_words_is_flagged(ste):
    sentence = " ".join(["word"] * 27) + " end."
    assert "sentence_too_long" in types_in(ste.audit(sentence + "\n"))


# ============================================================
# Individual checks
# ============================================================

def test_multi_action_step(ste):
    result = ste.audit("1. Open the file and then restart the daemon.\n")
    assert "multi_action_step" in types_in(result)


def test_multi_action_does_not_fire_on_prose(ste):
    """Bulleted feature lists are not procedures; the imperative checks stay off."""
    result = ste.audit("- The daemon reads the file and then serves it.\n")
    assert "multi_action_step" not in types_in(result)


def test_and_or(ste):
    assert "and_or" in types_in(ste.audit("Set the token and/or the password.\n"))


def test_banned_phrase_error_and_warning(ste):
    result = ste.audit("1. In order to start, simply run the installer.\n")
    findings = {f["description"].split("'")[1] for f in result["findings"]
                if f["type"] == "banned_phrase"}
    assert "in order to" in findings
    assert "simply" in findings
    severities = {f["severity"] for f in result["findings"] if f["type"] == "banned_phrase"}
    assert severities == {"error", "warning"}


def test_ing_opener_fires_and_respects_allowlist(ste):
    assert "ing_opener" in types_in(ste.audit("1. Running the installer takes a minute.\n"))
    assert "ing_opener" not in types_in(ste.audit("1. Nothing happens until you run it.\n"))


def test_non_imperative_step(ste):
    assert "non_imperative_step" in types_in(ste.audit("1. The installer creates the unit.\n"))
    assert "non_imperative_step" not in types_in(ste.audit("1. Create the unit.\n"))


def test_weak_modal(ste):
    assert "weak_modal" in types_in(ste.audit("1. You should verify the checksum.\n"))


def test_passive_voice(ste):
    assert "passive_voice" in types_in(ste.audit("The unit is created by the installer.\n"))
    assert "passive_voice" not in types_in(ste.audit("The installer creates the unit.\n"))


# ============================================================
# Warning placement (the rule with a physical cost)
# ============================================================

def test_warning_closing_a_procedure_is_an_error(ste):
    text = (
        "1. Stop the daemon.\n"
        "2. Delete the state file.\n"
        "> Warning: deleting the state file discards the queue.\n"
    )
    assert "warning_at_end" in types_in(ste.audit(text))


def test_warning_before_the_step_is_clean(ste):
    text = (
        "> Warning: deleting the state file discards the queue.\n\n"
        "1. Stop the daemon.\n"
        "2. Delete the state file.\n"
    )
    assert "warning_at_end" not in types_in(ste.audit(text))


def test_warning_without_a_procedure_is_ignored(ste):
    assert types_in(ste.audit("> Warning: this host has no swap.\n")) == set()


# ============================================================
# Result contract
# ============================================================

def test_clean_procedure_passes(ste):
    text = (
        "# Install\n\n"
        "1. Clone the repository.\n"
        "2. Run the installer.\n"
        "3. Verify the health check reports zero failures.\n"
    )
    result = ste.audit(text)
    assert result["findings"] == []
    assert result["passed"] is True
    assert result["summary"]["steps"] == 3


def test_strict_mode_fails_on_warnings_only(ste):
    text = "1. You should verify the checksum.\n"
    assert ste.audit(text, strict=False)["passed"] is True
    assert ste.audit(text, strict=False)["summary"]["errors"] == 0
    assert ste.audit(text, strict=True)["passed"] is False


def test_findings_carry_line_numbers_in_order(ste):
    text = "1. In order to start, run it.\n\n2. Utilize the installer.\n"
    lines = [f["line"] for f in ste.audit(text)["findings"]]
    assert lines == sorted(lines)
    assert all(line > 0 for line in lines)
