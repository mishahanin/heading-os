r"""How many words is this? The engine had five answers.

`.claude/rules/hidden-chars.md` settles the question and is explicit about it:
"Both numbers come from the tool, never from an estimate ... Until 2026-08-23
nothing computed X and it was guessed - a made-up figure inside a validation
line, which .claude/rules/scope-claims.md forbids." That rule was written, and
exactly one counter was fixed to satisfy it. Four more kept their own
definitions, so the sentence "comes from the tool" named a tool that did not
agree with the tool beside it.

Measured on one ordinary sentence,
`"It's a well-known state-of-the-art system - see item 3. | --- | 50% of $347,850."`
the five answered 11, 12, 15, 15 and 17. A 55% spread.

The worst was `scripts/generate-newsletter-html.py`. Its `count_words` stripped
tags with `re.sub(r"<[^>]+>", " ", ...)`, which removes the `<style>` TAG and
leaves its BODY. `build_css()` inlines 17424 characters of CSS into every issue,
so a newsletter carrying three words of prose reported `Word count: ~1961`. The
figure is what the operator reads to judge whether a briefing runs the right
length, and it was inflated by about 1958 on every run.

`scripts/utils/html_text.strip_html` has removed `<style>` and `<script>` bodies
since it was written, and its own docstring asks new callers to import it rather
than copy the logic. It was there the whole time.

Second was `scripts/run-skill-eval.py`, where the count is not a display but a
GATE: `len(output.split())` decided `min_words` and `max_words`, so a bare `-`
bullet, a `|` table rule and a `---` separator each cleared one word of a length
floor. A floor a list of bullets satisfies on punctuation is not a floor.

Two counters were deliberately left alone. The operator reversed half of that on
2026-08-30 and folded `ste-check` into the shared counter as well, so only one
private definition survives. Both states are recorded here, because a file that
still described the old decision would send the next audit hunting a counter
that no longer exists:

- `scripts/ste-check.py:word_count` USED TO count `[\w'-]+`, which reads `.` `/`
  `=` `{` `}` as word boundaries and scored `outputs/.../{version}.md` as seven
  words. It now calls the shared counter. The swap can only lower a count, so it
  loosened the 20/25 sentence limits rather than tightening them: measured over
  the gated corpus, 8644 sentences, no verdict moved and 572 sentences gained
  margin. The convergence is pinned by
  `tests/test_two_private_counters_that_outvoted_the_shared_one.py`, which also
  holds the loosening at the exact boundary word.
- `scripts/humanization-check.py:word_count` counts `\b\w+\b`, so "well-known"
  is two and "state-of-the-art" is four. Every one of that tool's sentence and
  paragraph thresholds is calibrated against it; swapping the definition would
  move an entire rule's enforcement, which is a design change and not a defect
  fix. What WAS wrong is that it printed its figure as "Word count:", the exact
  shape of the validation line `hidden-chars.md` owns, so an operator could copy
  either one and satisfy the rule's wording with a number that differs by 55%.
  It is now labelled "Rhythm words" and names the tool the deliverable's count
  comes from.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.sanitize_text import word_count  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# The sentence that produced five answers.
SPREAD = "It's a well-known state-of-the-art system - see item 3. | --- | 50% of $347,850."


# ==========================================================================
# The one definition
# ==========================================================================

def test_a_bare_bullet_is_not_a_word():
    assert word_count("- alpha") == 1


def test_a_table_rule_is_not_a_word():
    assert word_count("| alpha | beta |") == 2


def test_a_separator_run_is_not_a_word():
    assert word_count("alpha\n---\nbeta") == 2


def test_a_lone_dash_is_not_a_word():
    assert word_count("alpha - beta") == 2


def test_a_number_is_a_word():
    assert word_count("$347,850") == 1


def test_a_hyphenated_compound_is_one_word():
    """It is one word to a reader, and `.split()` agrees."""
    assert word_count("well-known") == 1


def test_the_empty_string_counts_nothing():
    assert word_count("") == 0


def test_whitespace_alone_counts_nothing():
    assert word_count("  \t\n  ") == 0


# ==========================================================================
# The canonical counter is reachable, which is why the copies existed
# ==========================================================================

def test_the_definition_lives_in_an_importable_module():
    """It was `_word_count` inside a kebab-case CLI, which no module can import.

    That is the whole reason four more were written. A shared definition nobody
    can reach is not shared.
    """
    from scripts.utils import sanitize_text
    assert callable(sanitize_text.word_count)


def test_the_sanitize_cli_uses_the_shared_definition():
    cli = _load("sanitize_cli", "scripts/sanitize-text.py")
    assert cli._word_count is word_count, "the CLI kept a second copy"


# ==========================================================================
# The newsletter, which counted its own stylesheet
# ==========================================================================

nl = _load("newsletter_wc", "scripts/generate-newsletter-html.py")


# A stylesheet the test owns, not the workspace's own.
#
# These three read `nl.build_css()` at first. It calls `load_template`, which
# reads from the DATA overlay and RAISES FileNotFoundError when the overlay is
# absent - so all three passed on the operator's machine and failed on CI, which
# has no overlay. The property under test ("a <style> body is not prose") does
# not depend on which stylesheet it is, so the fixture is built here and the
# tests measure the same thing on any machine.
#
# Sized to the real defect. The measured inflation was about 1958 words from
# 17424 characters of CSS, and a guard fed two selectors would pass whether or
# not the fix were present.
_CSS_RULE = ".selector-{n} {{ margin: 0 auto; padding: 12px; color: #F5922B; }}\n"
FAT_CSS = "".join(_CSS_RULE.format(n=i) for i in range(400))


def test_the_fixture_stylesheet_is_large_enough_for_this_to_matter():
    """A guard over two selectors passes with or without the fix.

    Stated as a test rather than a comment, so a later edit that trims the
    fixture is told what it broke instead of leaving the two below hollow.
    """
    assert len(FAT_CSS) > 15000
    assert word_count(FAT_CSS) > 1000


def test_the_newsletter_does_not_count_a_stylesheet():
    """MEASURED before the fix: 3 words of prose reported as 1961."""
    doc = f"<html><head><style>{FAT_CSS}</style></head><body><p>Hello there friend</p></body></html>"
    assert nl.count_words(doc) == 3


def test_a_stylesheet_alone_counts_as_nothing():
    assert nl.count_words(f"<style>{FAT_CSS}</style>") == 0


def test_the_newsletter_inlines_its_stylesheet_into_the_document():
    """The link between the fixture above and the real risk.

    The two tests above use a stand-in. They only matter because the real
    generator puts a whole stylesheet inside a `<style>` element in the rendered
    document; if it ever stopped doing that, they would be guarding a shape the
    code no longer produces. Read from the source, so it holds with or without
    the DATA overlay that carries the stylesheet itself.
    """
    src = (ROOT / "scripts" / "generate-newsletter-html.py").read_text(encoding="utf-8")
    assert "<style>" in src
    assert "build_css()" in src


def test_the_newsletter_does_not_count_a_script_body_either():
    doc = "<script>var a = 1; var b = 2; var c = 3;</script><p>Hello there friend</p>"
    assert nl.count_words(doc) == 3


def test_an_html_entity_is_not_a_word():
    assert nl.count_words("<p>alpha &amp; beta</p>") == 2


def test_the_newsletter_counts_words_across_block_tags():
    """`strip_html` inserts a break at block boundaries, so two divs are not
    one fused word."""
    assert nl.count_words("<div>alpha</div><div>beta</div>") == 2


def test_the_newsletter_uses_the_shared_counter():
    assert nl.word_count is word_count


# ==========================================================================
# The eval gate, where the count decides pass or fail
# ==========================================================================

ev = _load("skill_eval_wc", "scripts/run-skill-eval.py")


def _checks(output, checks):
    return {r["check"]: r for r in ev.run_checks(output, checks)}


def test_a_floor_is_not_cleared_by_bullets():
    """Five bullet characters and three words used to count as eight."""
    output = "- \n- \n- \n- \n- \nalpha beta gamma"
    got = _checks(output, {"min_words": 8})
    assert got["min_words>=8"]["passed"] is False
    assert got["min_words>=8"]["detail"] == "got 3"


def test_a_floor_is_cleared_by_real_words():
    output = "alpha beta gamma delta epsilon zeta eta theta"
    assert _checks(output, {"min_words": 8})["min_words>=8"]["passed"] is True


def test_a_ceiling_is_not_breached_by_table_rules():
    """The stricter count cuts both ways: punctuation no longer pushes a
    reply over a `max_words` ceiling either."""
    output = "| alpha | beta |\n| --- | --- |\n| gamma | delta |"
    got = _checks(output, {"max_words": 4})
    assert got["max_words<=4"]["passed"] is True
    assert got["max_words<=4"]["detail"] == "got 4"


def test_the_eval_gate_uses_the_shared_counter():
    assert ev.word_count is word_count


# ==========================================================================
# The two counters deliberately left alone
# ==========================================================================

hc = _load("humanization_wc", "scripts/humanization-check.py")


def test_the_humanisation_counter_still_has_its_own_definition():
    """Its thresholds are calibrated against it. Swapping it moves a rule."""
    assert hc.word_count("well-known") == 2
    assert word_count("well-known") == 1


def test_the_humanisation_report_no_longer_says_word_count():
    """It printed `Word count: X`, the exact shape of the validation line
    `.claude/rules/hidden-chars.md` owns, from a different definition."""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        hc.print_report(hc.audit("Alpha beta gamma delta."), "probe")
    out = buf.getvalue()
    assert "Word count:" not in out
    assert "Rhythm words:" in out


def test_the_humanisation_report_names_where_the_real_count_comes_from():
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        hc.print_report(hc.audit("Alpha beta gamma delta."), "probe")
    assert "sanitize-text.py --scan" in buf.getvalue()


def test_the_humanisation_report_carries_the_label_when_it_has_findings():
    """Two print sites, and a fix that lands in one of them is this whole file."""
    import io
    import contextlib
    noisy = "It is important to note that this leverages a robust solution. " * 4
    result = hc.audit(noisy)
    assert result["findings"], "the probe text stopped producing findings"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        hc.print_report(result, "probe")
    out = buf.getvalue()
    assert "Rhythm words:" in out
    assert "Word count:" not in out


def test_the_ste_counter_no_longer_has_its_own_definition():
    """This file's subject is the 55% spread across five counters. One arm of
    that spread closed on 2026-08-30, so the assertion is inverted rather than
    deleted: the spread is what this file measures, and a closed arm is a
    result, not an absence.

    The old assertions (`well-known` is 1, `50%` is 1) are not proof of anything
    any more. Both hold under the shared counter too, so they would have gone on
    passing while the claim in their name became false.
    """
    ste = _load("ste_wc", "scripts/ste-check.py")
    assert ste.word_count(SPREAD) == word_count(SPREAD), (
        "ste-check disagrees with the shared counter again; the 2026-08-30 "
        "convergence has been undone")


def test_the_newsletter_cli_reports_an_exact_count_not_an_estimate():
    """It printed `Word count: ~N` where N was a regex estimate over the CSS.

    The tilde promised roughness and delivered a figure wrong by two thousand.
    """
    src = (ROOT / "scripts" / "generate-newsletter-html.py").read_text(encoding="utf-8")
    assert 'print(f"Word count: {words}")' in src
    assert 'Word count: ~' not in src.split('"""')[-1], "the estimate marker is still printed"


def test_the_newsletter_cli_does_not_shadow_the_imported_counter():
    """`word_count = count_words(...)` inside main() binds over the import.

    It happens to work, because `count_words` reads the module global rather
    than main's local. It is a trap for the next edit in that scope, and this
    file exists because of edits that did not notice a second copy.
    """
    src = (ROOT / "scripts" / "generate-newsletter-html.py").read_text(encoding="utf-8")
    assert "    word_count = count_words(" not in src
