"""Shard 08-p3: what the humanisation audit missed, and what it counted twice.

Eight findings against `scripts/humanization-check.py`, a pre-publish gate. Its
job is to be believed: a clean line means the prose ships. Four of the eight
made that line wrong in one direction and four in the other.

The hole: LLM output is full of U+2019, and only ONE of the fourteen checks
normalised it. "It's important to note" with a curly apostrophe matched nothing
in the banned-phrase list, nothing in the hedge list, and none of the `it'?s`
structure regexes -- while the identical sentence with a straight apostrophe was
a hard error. Whether the gate saw a banned phrase at all came down to which
apostrophe the model emitted.

The inflation: nothing de-duplicated across checks, so "a rich tapestry" was
three errors and "shaping the future" was two.

Plus a raw dict printed into the human report, a docstring that listed 11 of the
14 checks that run, two thresholds documented off by one boundary, frontmatter
left in place when a file ends without a newline, and a filter that threw out
the prose sitting under a heading along with the heading.
"""

import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load():
    path = PROJECT_ROOT / "scripts" / "humanization-check.py"
    spec = importlib.util.spec_from_file_location("humanization_check_08p3", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["humanization_check_08p3"] = mod
    spec.loader.exec_module(mod)
    return mod


hc = _load()

CURLY = "’"        # right single quotation mark
CURLY_OPEN = "‘"   # left single quotation mark


def types_of(text):
    return hc.audit(text)["summary"]["by_type"]


# ---------------------------------------------------------------------------
# Finding 1 -- the curly apostrophe walked past almost every check
# ---------------------------------------------------------------------------

def test_a_curly_apostrophe_no_longer_hides_a_banned_phrase():
    straight = "It's important to note that this matters."
    curly = straight.replace("'", CURLY)
    assert types_of(curly) == types_of(straight)
    assert types_of(curly).get("banned_phrase") == 1


def test_a_curly_apostrophe_no_longer_hides_a_banned_structure():
    straight = "It's not just a product, it's a promise to every operator."
    curly = straight.replace("'", CURLY)
    assert types_of(curly) == types_of(straight)


def test_a_curly_apostrophe_no_longer_hides_a_hedge():
    straight = ("It's worth noting that generally speaking the rollout went well. "
                "It's important to note that in some sense the numbers may suggest "
                "a degree of improvement. ") * 6
    curly = straight.replace("'", CURLY)
    assert types_of(curly) == types_of(straight)
    assert types_of(straight).get("hedge_density") == 1


def test_the_opening_curly_quote_is_folded_too():
    assert hc.strip_markdown_noise(CURLY_OPEN + "tis") == "'tis"


def test_folding_the_apostrophe_does_not_move_any_offset():
    """One character for one character, which is why the snippets stay right."""
    text = "We shipped it. It" + CURLY + "s important to note that."
    folded = hc.strip_markdown_noise(text)
    assert len(folded) == len(text)
    hit = [f for f in hc.audit(text)["findings"] if f["type"] == "banned_phrase"]
    assert hit and text[hit[0]["position"]:hit[0]["position"] + 2] == "It"


def test_the_file_on_disk_is_never_rewritten(tmp_path):
    """The fold is for matching only; the curly characters are human signal."""
    p = tmp_path / "note.md"
    original = "It" + CURLY + "s fine.\n"
    p.write_text(original, encoding="utf-8")
    hc.audit(p.read_text(encoding="utf-8"))
    assert p.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# Finding 2 -- one occurrence, two and three findings
# ---------------------------------------------------------------------------

def test_a_phrase_in_two_lists_is_reported_once():
    counts = types_of("We shipped the feature, shaping the future.")
    assert sum(counts.values()) == 1


def test_the_longest_match_is_the_one_kept():
    """"rich tapestry" beats "tapestry"; the author is told about the phrase."""
    findings = hc.audit("a rich tapestry")["findings"]
    assert len(findings) == 1
    kept = findings[0]
    assert kept["position"] == 2
    assert kept["end"] == len("a rich tapestry")


def test_a_word_outside_the_covering_span_still_reports():
    counts = types_of("a rich tapestry and a robust delve")
    assert counts.get("banned_vocab") == 2          # robust, delve
    assert sum(counts.values()) == 3                # + the rich-tapestry hit


def test_a_warning_never_retires_an_error_inside_its_span():
    """A generic -ing tail is a warning; `fostering` inside it is an error."""
    findings = hc.audit("We ran the pilot, fostering the growth.")["findings"]
    assert any(f["severity"] == "error" for f in findings)


def test_two_separate_occurrences_are_both_reported():
    """The dedupe collapses overlapping spans, never repeated text."""
    counts = types_of("A rich tapestry here. And a rich tapestry there.")
    assert sum(counts.values()) == 2


def test_the_dedupe_is_deterministic_on_an_exact_span_tie():
    first = hc.audit("a rich tapestry")["findings"][0]
    again = hc.audit("a rich tapestry")["findings"][0]
    assert first["type"] == again["type"]


def test_a_structural_pattern_cannot_swallow_a_banned_word():
    """Different claim about the same text; both are worth reporting."""
    text = "It's not just a robust idea, it's the whole plan for the quarter."
    counts = types_of(text)
    assert counts.get("banned_vocab") == 1
    assert counts.get("structural_pattern", 0) >= 1


def _lex(kind, start, end, severity="error"):
    return {"type": kind, "severity": severity, "position": start, "end": end}


def test_two_partly_overlapping_matches_are_both_kept():
    """CONTAINMENT, not overlap. Two matches that merely cross each other are
    two different stretches of text and two different things to fix; dropping
    one because it touched the other would hide a real finding."""
    given = [_lex("banned_phrase", 0, 10), _lex("banned_vocab", 5, 20)]
    assert hc._dedupe_lexical_overlaps(given) == given


def test_a_match_that_only_touches_the_edge_is_kept():
    given = [_lex("banned_phrase", 0, 10), _lex("banned_vocab", 10, 20)]
    assert hc._dedupe_lexical_overlaps(given) == given


def test_a_contained_match_is_dropped():
    outer = _lex("banned_phrase", 0, 20)
    inner = _lex("banned_vocab", 5, 10)
    assert hc._dedupe_lexical_overlaps([outer, inner]) == [outer]


def test_a_structural_pattern_is_never_dropped_by_a_lexical_match():
    """It is not a lexical hit, so it is outside the dedupe on both sides."""
    phrase = _lex("banned_phrase", 0, 50)
    structural = _lex("structural_pattern", 5, 15, severity="warning")
    kept = hc._dedupe_lexical_overlaps([phrase, structural])
    assert structural in kept


def test_a_structural_pattern_never_drops_a_lexical_match_either():
    structural = _lex("structural_pattern", 0, 50, severity="warning")
    vocab = _lex("banned_vocab", 5, 15)
    kept = hc._dedupe_lexical_overlaps([structural, vocab])
    assert vocab in kept and structural in kept


def test_findings_without_a_span_are_untouched():
    """Burstiness and hedge density are per-paragraph, not per-match."""
    text = ("It's worth noting that generally speaking this went well. "
            "It's important to note that in some sense it may suggest a degree "
            "of progress. ") * 6
    assert types_of(text).get("hedge_density") == 1


# ---------------------------------------------------------------------------
# Finding 3 -- a raw dict printed into the human report
# ---------------------------------------------------------------------------

def test_a_generic_ing_tail_warning_prints_as_a_line_not_a_dict(capsys):
    text = "We rewrote the pipeline last quarter, securing the future."
    hc.print_report(hc.audit(text), "inline text")
    out = capsys.readouterr().out
    assert "'type':" not in out
    assert "'severity':" not in out
    assert "securing the future" in out


def test_every_warning_type_has_its_own_report_branch(capsys):
    """No warning may fall into the `else` that dumps the whole finding."""
    samples = [
        "We rewrote the pipeline last quarter, securing the future.",
        "It's not just a product, it's a promise to the operator.",
        "# A Heading With Title Case Words Here\n\nBody text follows here.\n",
    ]
    for text in samples:
        hc.print_report(hc.audit(text), "inline text")
    out = capsys.readouterr().out
    assert "'position':" not in out


# ---------------------------------------------------------------------------
# Finding 4 -- the docstring listed 11 of the 14 checks that run
# ---------------------------------------------------------------------------

def test_the_docstring_lists_the_three_checks_it_used_to_omit():
    doc = hc.__doc__
    assert "Over-fragmentation" in doc
    assert "tail phrases" in doc
    assert "Additionally / Moreover" in doc


def test_the_docstring_names_the_cross_check_dedupe():
    assert "_dedupe_lexical_overlaps" in hc.__doc__


# ---------------------------------------------------------------------------
# Finding 5 -- two thresholds documented one boundary off
# ---------------------------------------------------------------------------

def test_the_burstiness_docstring_matches_the_long_sentence_boundary():
    doc = " ".join(hc.check_burstiness.__doc__.split())
    assert "MORE THAN 25 words" in doc
    assert "a 25+-word sentence AND" not in doc


def test_the_burstiness_docstring_matches_the_cv_boundary():
    doc = " ".join(hc.check_burstiness.__doc__.split())
    assert "30% OR MORE" in doc


def _sentence(n):
    """An n-word sentence that the sentence splitter will actually split on.

    It wants a capital after the full stop, so each one starts with `Word`.
    """
    return "Word " + " ".join(f"word{i}" for i in range(n - 1)) + "."


def test_a_sentence_of_exactly_25_words_does_not_satisfy_the_long_track():
    """The boundary the docstring used to describe the other way round."""
    para = f"{_sentence(2)} {_sentence(25)} {_sentence(14)}"
    found = hc.check_burstiness(para)
    assert [hc.word_count(s) for s in hc.get_sentences(para)] == [2, 25, 14]
    assert any(">25w" in f.get("missing", []) for f in found)


def test_a_sentence_of_twenty_six_words_does_satisfy_it():
    para = f"{_sentence(2)} {_sentence(26)} {_sentence(14)}"
    assert hc.check_burstiness(para) == []


# ---------------------------------------------------------------------------
# Finding 6 -- frontmatter survived when the file ended on its closing fence
# ---------------------------------------------------------------------------

def test_frontmatter_is_stripped_without_a_trailing_newline():
    text = "---\ntitle: leveraging robust systems\n---"
    assert hc.strip_markdown_noise(text).strip() == ""
    assert hc.audit(text)["summary"]["errors"] == 0


def test_frontmatter_is_still_stripped_with_a_trailing_newline():
    text = "---\ntitle: leveraging robust systems\n---\nReal prose here.\n"
    stripped = hc.strip_markdown_noise(text)
    assert "leveraging" not in stripped
    assert "Real prose here." in stripped


def test_a_horizontal_rule_mid_document_is_not_mistaken_for_frontmatter():
    text = "Opening line.\n\n---\n\nClosing line.\n"
    assert "Opening line." in hc.strip_markdown_noise(text)


def test_a_pair_of_rules_mid_document_does_not_eat_the_prose_between_them():
    """The `^` anchor is what keeps this a frontmatter strip and not a
    "delete everything between the first two horizontal rules" strip."""
    text = "Opening line.\n\n---\nMiddle words live here.\n---\n\nClosing line.\n"
    stripped = hc.strip_markdown_noise(text)
    assert "Middle words live here." in stripped
    assert "Opening line." in stripped
    assert "Closing line." in stripped


# ---------------------------------------------------------------------------
# Finding 7 -- prose under a heading was thrown out with the heading
# ---------------------------------------------------------------------------

def test_prose_directly_under_a_heading_is_still_a_paragraph():
    text = "## Intro\nThis paragraph is here and it is long enough to count.\n"
    paras = hc.get_paragraphs(text)
    assert len(paras) == 1
    assert paras[0].startswith("This paragraph")
    assert "## Intro" not in paras[0]


def test_a_heading_alone_is_still_not_a_paragraph():
    assert hc.get_paragraphs("## Intro\n") == []


def test_a_document_written_entirely_under_headings_is_not_zero_paragraphs():
    text = ("## One\nFirst body sentence sits here.\n\n"
            "## Two\nSecond body sentence sits here.\n")
    assert len(hc.get_paragraphs(text)) == 2


def test_a_heading_below_the_first_line_of_a_block_is_removed_too():
    text = "Body line one.\n### Sub\nBody line two.\n"
    paras = hc.get_paragraphs(text)
    assert len(paras) == 1
    assert "### Sub" not in paras[0]
    assert "Body line one." in paras[0] and "Body line two." in paras[0]


def test_an_indented_heading_is_removed_as_well():
    assert hc.get_paragraphs("  ## Indented\nReal prose line.\n") == ["Real prose line."]


# ---------------------------------------------------------------------------
# Finding 8 -- the reported word count included what nothing else counted
# ---------------------------------------------------------------------------

def test_the_reported_word_count_excludes_a_code_fence():
    prose = "This is the only real sentence in the document here.\n\n"
    fence = "```\n" + " ".join(f"token{i}" for i in range(80)) + "\n```\n"
    counted = hc.audit(prose + fence)["summary"]["word_count"]
    assert counted == hc.word_count(prose)


def test_the_reported_word_count_excludes_frontmatter():
    text = "---\ntitle: some metadata words here\n---\nJust five words follow.\n"
    assert hc.audit(text)["summary"]["word_count"] == hc.word_count("Just five words follow.")


def test_plain_prose_counts_the_same_as_before():
    text = "One two three four five."
    assert hc.audit(text)["summary"]["word_count"] == 5
