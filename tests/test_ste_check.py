"""Documentation-style checker (.claude/rules/documentation-style.md).

Covers each check in scripts/ste-check.py against minimal inputs, the markdown
preparation that must run before any check (code fences, skip blocks), and the
scope contract: the checker's file list can never widen past the rule that
authorises it.

Run: python3 -m pytest tests/test_ste_check.py
"""
import fnmatch
import importlib.util
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


def _documentation_style_hook():
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    for repo in config["repos"]:
        for hook in repo.get("hooks", []):
            if hook.get("id") == "documentation-style":
                return hook
    return None


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
