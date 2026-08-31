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
import re
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


def excluded_page_names():
    """The basenames the rule's exclusion paragraph names, read from the rule.

    Parsed rather than duplicated here, because a second copy of the list is the
    copy that stops being updated.
    """
    text = RULE.read_text(encoding="utf-8")
    start = text.index("It does NOT apply to explanatory documentation")
    paragraph = text[start:text.index("\n", start)]
    return set(re.findall(r"`([A-Za-z0-9._-]+\.md)`", paragraph))


def hand_authored_html_names():
    """The `docs/*.html` basenames the rule names as hand-authored.

    Parsed from the rule for the reason `excluded_page_names` gives: a second
    copy of a list is the copy that stops being updated. That parser cannot
    serve here at all. Its regex accepts `([A-Za-z0-9._-]+\\.md)` and nothing
    else, so until 2026-08-31 the rule could not have named an HTML page as
    classified even if someone had wanted to. This is the missing half.
    """
    lines = RULE.read_text(encoding="utf-8").splitlines()
    anchor = "The rest are HAND-AUTHORED, with no `.md` behind them"
    holders = [i for i, line in enumerate(lines) if anchor in line]
    assert len(holders) == 1, (
        f"expected exactly one hand-authored-HTML lead-in in {RULE.name}, found "
        f"{len(holders)}. Re-anchor this parser rather than let it read the "
        f"wrong paragraph, or none at all."
    )
    listing = next(line for line in lines[holders[0] + 1:] if line.strip())

    # The list gets a line to itself, and this asserts that it kept one. The
    # first version of the rule carried the names inline in the reasoning
    # paragraph, and mutation M1 deleted `prerequisites.html` from them without
    # turning this test red: the same page is discussed twice further down the
    # same line, so the narrative kept re-declaring what the list had dropped. A
    # line that must contain nothing but names cannot absorb a passing mention.
    names = set(re.findall(r"`([A-Za-z0-9._-]+\.html)`", listing))
    residue = re.sub(r"`[A-Za-z0-9._-]+\.html`|[,\s]", "", listing)
    assert not residue, (
        f"the hand-authored list line in {RULE.name} carries prose as well as "
        f"names: {residue!r}. Keep the line to backticked `*.html` names and "
        f"commas, or a page mentioned in passing joins the list silently."
    )
    return names


# Floors against a vacuous walk. `docs/` held 23 `.md` and 37 `.html` on
# 2026-08-31, of which 14 were hand-authored and 23 generated. Each floor sits
# roughly a fifth below the count measured that day: low enough that retiring a
# few pages is ordinary maintenance, high enough that a glob returning nothing,
# a rename of `docs/`, or a broken sibling test cannot pass by reasoning over an
# empty or halved set. Pages are added here far more often than removed, so a
# drop past a floor is a structural change that deserves a human reading.
MIN_DOCS_MD = 18
MIN_DOCS_HTML = 30
MIN_GENERATED_HTML = 18
MIN_HAND_AUTHORED_HTML = 11


def test_every_docs_page_is_classified(ste):
    """No page under `docs/` may sit in neither the gate nor the exclusion list.

    The failure this closes has a shape no gate on the gated set can see. On
    2026-08-22 three pages -- EXTENDING.md, TELEGRAM-AND-ALERTS.md and
    RULES-REFERENCE.md -- were absent from `CHECKED_GLOBS` AND absent from the
    rule's "It does NOT apply to" sentence. `--all` was green over twelve pages
    and stayed green, because a page nobody classified is a page nobody audits;
    the three carried 32 errors between them. Two were pages a reader executes.

    So the contract is total, not partial: every `docs/*.md` is gated or is
    named as excluded, and a new page fails this test until someone decides.
    """
    checked = set(ste.CHECKED_GLOBS)
    excluded = excluded_page_names()
    root = ROOT / "docs"
    pages = sorted(root.glob("*.md"))
    assert len(pages) >= MIN_DOCS_MD, (
        f"only {len(pages)} `docs/*.md` pages walked, expected at least "
        f"{MIN_DOCS_MD}. A classification test over an empty or halved corpus "
        f"is green for the wrong reason."
    )
    unclassified = [
        p.name
        for p in pages
        if f"docs/{p.name}" not in checked and p.name not in excluded
    ]
    assert not unclassified, (
        f"these docs pages are in neither list: {unclassified}. Add each one to "
        f"CHECKED_GLOBS in scripts/ste-check.py plus the rule's paths: "
        f"frontmatter (a page a reader executes), or name it in the rule's "
        f"'It does NOT apply to explanatory documentation' sentence."
    )


def test_the_two_classifications_do_not_overlap(ste):
    """A page cannot be both gated and excluded; that pair reads as a decision."""
    both = {g.split("/")[-1] for g in ste.CHECKED_GLOBS} & excluded_page_names()
    assert not both, f"gated AND listed as out of scope: {sorted(both)}"


def _docs_html_partition(root=None):
    """Split `docs/*.html` into GENERATED and HAND-AUTHORED, from disk.

    Derived, never typed. `scripts/regenerate-docs-html.py` renders md/html
    pairs, so a page with a sibling `.md` is generated by construction and a
    page without one was written by hand. Typing the second list into a test is
    the defect class this repository keeps finding; the rule carries the list
    because the rule is where a decision is recorded, and the test cross-checks
    it against what is actually on disk.
    """
    pages = sorted((root or ROOT / "docs").glob("*.html"))
    generated = {p.name for p in pages if p.with_suffix(".md").exists()}
    return pages, generated, {p.name for p in pages} - generated


def _html_classification_gaps(hand_authored, declared):
    """The two gaps, in both directions: unnamed pages and stale names.

    Pure, so the refusal can be proven on synthetic input instead of by
    littering the live `docs/` tree. The live test below feeds it the real
    partition; `test_an_unclassified_html_page_is_refused` feeds it inputs that
    must come back red.
    """
    return sorted(set(hand_authored) - set(declared)), sorted(set(declared) - set(hand_authored))


def test_every_docs_html_page_is_classified():
    """The `docs/` HTML pages fall in neither list, and 14 of them did.

    The md-only sibling above sat under a rule sentence that read "Every page
    under `docs/`". It globbed `*.md`. On 2026-08-31 `docs/` held 23 `.md` and
    37 `.html`, and 14 of the HTML pages had no `.md` source at all: written by
    hand, invisible to that walk, in NEITHER list. The blind spot was structural
    on both sides, because `excluded_page_names()` matches `.md` names only, so
    the rule had no way to name an HTML page even deliberately.

    `docs/prerequisites.html` is a from-scratch install page, exactly the "page
    a reader executes" class the rule exists to gate. It is out of the
    ASD-STE100 gate for a stated reason (the checker parses Markdown and would
    measure tag soup) and carries a content gate instead, in
    `tests/test_docs_pages_that_pointed_at_nothing.py`.

    This closes the neither-list hole without running ASD-STE100 over HTML. It
    reads the rule's list in BOTH directions: a hand-authored page the rule does
    not name is unclassified, and a name the rule carries that is no longer
    hand-authored is a stale decision. One direction alone lets the list rot.
    """
    pages, generated, hand_authored = _docs_html_partition()

    assert len(pages) >= MIN_DOCS_HTML, (
        f"only {len(pages)} `docs/*.html` pages walked, expected at least "
        f"{MIN_DOCS_HTML}. A broken glob would otherwise classify nothing and "
        f"report success."
    )
    assert len(generated) >= MIN_GENERATED_HTML, (
        f"only {len(generated)} generated HTML pages (sibling `.md` present), "
        f"expected at least {MIN_GENERATED_HTML}. Either `docs/` was "
        f"restructured or the sibling test broke."
    )
    assert len(hand_authored) >= MIN_HAND_AUTHORED_HTML, (
        f"only {len(hand_authored)} hand-authored HTML pages, expected at least "
        f"{MIN_HAND_AUTHORED_HTML}. If the sibling test wrongly calls every page "
        f"generated, this test has nothing left to check and passes vacuously."
    )

    declared = hand_authored_html_names()
    unclassified, stale = _html_classification_gaps(hand_authored, declared)

    assert not unclassified, (
        f"these hand-authored `docs/` HTML pages are in no list: {unclassified}. "
        f"Name each one in the rule's 'The rest are HAND-AUTHORED' sentence in "
        f"{RULE.name}, or give it a `.md` source so it becomes a generated page "
        f"and inherits that source's classification."
    )

    assert not stale, (
        f"{RULE.name} names these as hand-authored HTML, but on disk they are "
        f"generated or absent: {stale}. Remove each from the rule's sentence; a "
        f"decision about a page that no longer exists reads as coverage and is "
        f"not."
    )


def test_an_unclassified_html_page_is_refused(tmp_path):
    """Prove the contract REFUSES, on a tree built for the purpose.

    A gate is only a gate if something makes it say no. The live test above is
    green today, and a green test proves nothing about what it would reject, so
    the refusal is exercised here instead of by dropping a scratch page into the
    real `docs/` tree, where a crashed run would leave litter that fakes every
    later verdict.

    Three cases, one per way the contract can be broken: a hand-authored page
    nobody named, a name the rule keeps after the page gained a `.md` source,
    and a name for a page that is gone.
    """
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in ("QUICKSTART.md", "QUICKSTART.html", "index.html"):
        (docs / name).write_text("x", encoding="utf-8")

    pages, generated, hand_authored = _docs_html_partition(docs)
    assert [p.name for p in pages] == ["QUICKSTART.html", "index.html"]
    assert generated == {"QUICKSTART.html"}, "a page with a sibling `.md` is generated"
    assert hand_authored == {"index.html"}, "a page without one was written by hand"

    # 1. Named nowhere: the exact hole `*.md`-only globbing left open.
    unclassified, stale = _html_classification_gaps(hand_authored, set())
    assert unclassified == ["index.html"] and not stale

    # 2. Named, and correct: the only combination that may pass.
    unclassified, stale = _html_classification_gaps(hand_authored, {"index.html"})
    assert not unclassified and not stale

    # 3. Named, but the page is generated now, or deleted. Stale either way.
    unclassified, stale = _html_classification_gaps(
        hand_authored, {"index.html", "QUICKSTART.html", "retired.html"}
    )
    assert not unclassified
    assert stale == ["QUICKSTART.html", "retired.html"]


def test_the_rule_can_name_an_html_page_at_all(ste):
    """The `.md`-only regex was half the blind spot; assert the other half exists.

    `excluded_page_names()` parses `([A-Za-z0-9._-]+\\.md)`, so no HTML name it
    reads can ever be a match. Before 2026-08-31 that was the only parser over
    this rule, which made "name the page as excluded" impossible to carry out
    for an HTML page. Both halves are asserted together here: the md parser
    still refuses HTML, and the html parser returns real names.
    """
    assert not {n for n in excluded_page_names() if n.endswith(".html")}, (
        "excluded_page_names() now yields HTML names; the two parsers overlap "
        "and a page could be read into both lists"
    )
    declared = hand_authored_html_names()
    assert declared, "the rule names no hand-authored HTML page; the parser is dead"
    assert all(n.endswith(".html") for n in declared)
    assert "prerequisites.html" in declared, (
        "the from-scratch install page is the one hand-authored page a reader "
        "executes; it must be a named decision, not an omission"
    )


def test_a_generated_html_page_inherits_a_classified_source(ste):
    """Nothing reaches `docs/` through the generated half unclassified either.

    A page is generated because a sibling `.md` exists, and that `.md` is itself
    under `docs/`, so the md walk already decides it. Asserting it here states
    the total: every one of the 37 HTML pages is covered by a decision, through
    its source or through the hand-authored list, with no third case.
    """
    checked = set(ste.CHECKED_GLOBS)
    excluded = excluded_page_names()
    _, generated, _ = _docs_html_partition()
    assert len(generated) >= MIN_GENERATED_HTML, "empty generated set proves nothing"

    orphans = sorted(
        name for name in generated
        if f"docs/{Path(name).stem}.md" not in checked
        and f"{Path(name).stem}.md" not in excluded
    )
    assert not orphans, (
        f"these generated HTML pages render a `.md` that is in neither list: "
        f"{orphans}. Classify the source, not the render."
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


def test_a_sentence_opening_with_inline_code_still_splits(ste):
    """The fourth defect of the splitter family, found 2026-08-22.

    Inline code was deleted outright, so a sentence that OPENS with a span began
    with whitespace and a lowercase letter. The lookahead wants a capital, so the
    sentence merged into the one above and the pair measured over the limit.
    Three correct sentences on docs/EXTENDING.md reported as one 26-word run,
    and the prose was on its way to being rewritten to satisfy the wrong number.
    """
    text = (
        "The approval is a commit, not a lock file. `git show <sha>` reads the "
        "frozen bytes. `git diff` answers whether the contract moved."
    )
    assert len(ste.split_sentences(ste.strip_noise(text))) == 3


def test_a_code_span_costs_exactly_one_word(ste):
    """A span is one thing the eye lands on, so it counts once.

    Deleting it counted ZERO, which discounted every sentence in proportion to
    how much code it carried -- the densest sentences got the largest pass. One
    QUICKSTART line read 21 words to the checker and 27 to a person, and
    reported clean. Adopted 2026-08-22 at a measured cost of 37 rewrites.
    """
    assert ste.word_count(ste.strip_noise("Run `--strict` on the file.")) == 5


def test_a_multi_word_span_still_costs_one(ste):
    """Never the words INSIDE the span.

    Counting the interior would penalise naming the exact flag or path, which is
    pressure in the wrong direction for reference documentation. Measured on the
    fourteen gated pages: interior-words scores 32 errors against this rule's 15.
    """
    one = ste.word_count(ste.strip_noise("Pass `--base REF` here."))
    other = ste.word_count(ste.strip_noise("Pass `scripts/crm-health.py` here."))
    assert one == other == 3


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
    inspected = 0
    for path in ste.resolve_skill_scope():
        text = path.read_text(encoding="utf-8")
        match = re.match(r"^---\n.*?\n---\n", text, re.DOTALL)
        if not match:
            continue
        inspected += 1
        height = match.group(0).count("\n")
        total = len(text.splitlines())
        for finding in ste.audit(text)["findings"]:
            if finding["line"] <= height or finding["line"] > total:
                misplaced.append((path.name, finding["line"], height))
    # Measured 94 skill bodies on 2026-08-26. Floored well below that so
    # retiring a skill cannot fail this test. If the frontmatter regex stops
    # matching (a corpus-wide format change, or a widened pattern), every file
    # takes the `continue` and the misplaced list is empty over nothing.
    assert inspected >= 60, f"only {inspected} skill bodies reached the check"
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
