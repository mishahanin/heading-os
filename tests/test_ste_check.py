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


def test_explanatory_docs_are_out_of_scope(ste):
    """The narrative pages must stay out - flattening them is the failure mode."""
    for excluded in ("docs/ARCHITECTURE.md", "docs/THREAT-MODEL.md",
                     "docs/DESIGN-CHECK.md", "docs/RELEASE-NOTES.md"):
        assert excluded not in ste.CHECKED_GLOBS


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
