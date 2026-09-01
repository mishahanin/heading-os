#!/usr/bin/env python3
"""Two rules told the reader their own files were `corporate`. Both were `engine`.

`config/routing-map.yaml`, resolved by `get_routing_destination()`, is the SINGLE
classification input (`.claude/rules/classification.md`). Some rules also spell their
own files' destinations out in prose, in a `## Classification` section, so a reader
does not have to go and resolve them. Nothing checked that prose against the map.

On 2026-08-31 it had rotted in both places it exists:

  `.claude/rules/corporate-docs.md`  five of six lines wrong, all claiming
                                     `corporate` for paths that resolve `engine`,
                                     plus an invented "`reference/` directory
                                     default" of `corporate` that never existed
  `.claude/rules/tiered-risk.md`     the rule itself, `scripts/utils/tool_risk.py`
                                     and `config/tool-risk.json`, all claimed
                                     `corporate`, all resolving `engine`

The root cause is recorded in the map's own header: code directories that were
`corporate` (shared DOWN to executives) became `engine` (shared to everyone),
because code is not data. The rules were never updated. On a public repo that is
wrong in the dangerous direction, because it tells an agent a `scripts/` file is
withheld from the public when it already ships in the public clone.

`scripts/classification-health.py` did not catch this and structurally cannot: it
classifies FILES against the map and never reads a word of rule prose. This module
is the missing half. It reads the prose and asks the resolver.

The contract this enforces, derived from the shape the two real sections already
use rather than invented here:

  1. Inside a `Classification` section of a `.claude/rules/*.md`, EVERY bullet is a
     claim of the canonical shape `- ` + one backticked path + a separator + one
     destination word. A bullet that does not parse is a failure, not a skip, so a
     claim cannot hide behind a shape the parser does not know.
  2. Every parsed destination is one of `engine` / `private` / `corporate` and
     equals what `get_routing_destination()` returns for that path. `ceo-only` is
     the two-value exec-sync collapse, not a routing destination, and fails here.
  3. Prose inside a `Classification` section may not assert a destination for a
     backticked path. A destination word written inside backticks is a quoted term
     and is fine (`.claude/rules/classification.md` already writes them that way);
     a BARE one next to a backticked path is an unparsed claim and fails. This is
     the exact shape `tiered-risk.md` used to smuggle three wrong claims through.
     The unit is the PARAGRAPH, not the line. Until 2026-09-01 it was the line,
     and every rule file in this tree is hard-wrapped at roughly 85 columns, so a
     claim that happened to wrap between its path and its destination word - the
     single most likely place for a wrap to land in a sentence of that shape -
     produced zero claims AND zero complaints. Measured on the pre-fix parser:
     "`scripts/utils/tool_risk.py` is\ncorporate." returned `(claims=0,
     complaints=0)`, byte-identical to a clean file, while the same sentence
     unwrapped returned one complaint. The smuggling shape this rule exists to
     stop was one newline away from being invisible.
  4. A canonical claim bullet may not live outside a `Classification` section,
     where nothing would be looking for it.

Every assertion runs through the resolver. A grep over `config/routing-map.yaml`
would pass on a destination that only appears in one of its comments, which is a
sibling of the defect above.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# The repo ROOT, never `ROOT / "scripts"`. A module-level insert is never undone,
# so it holds for the rest of the xdist worker; putting `scripts/` there makes
# every module beside it a top-level name, and `scripts/firecrawl.py` then
# shadows the installed `firecrawl` SDK for every test that runs afterwards.
# `tests/test_impeccable_engine.py` asserts that entry is absent, and this line
# used to fail it whenever the two landed on the same worker.
sys.path.insert(0, str(ROOT))

from scripts.utils.workspace import get_routing_destination  # noqa: E402

RULES_DIR = ROOT / ".claude" / "rules"

#: The three routing destinations. Anything else in a claim is a defect, including
#: `ceo-only`, which is the collapsed exec-sync label rather than a destination.
DESTINATIONS = ("engine", "private", "corporate")

#: Destination-ish words a rotting claim is likely to reach for. Used ONLY by the
#: outside-a-section sweep, so that `- \`outputs/\` — CEO deliverables` (a plain
#: descriptive bullet, of which the rules hold many) is not mistaken for a claim.
CLAIMISH = DESTINATIONS + ("ceo-only", "public", "shared")

_SEPARATOR = r"[-—–:]"

#: A claim bullet with any word in the destination slot. Used INSIDE a
#: Classification section, where every bullet must be a claim, so that an
#: unrecognised destination word is reported rather than skipped.
CLAIM_ANY_DEST = re.compile(
    r"^\s*[-*]\s+`(?P<path>[^`]+)`\s*(?:\([^)`]*\)\s*)?"
    rf"{_SEPARATOR}\s+(?P<dest>[A-Za-z][\w-]*)\b"
)

#: The same bullet restricted to destination-ish words. Used OUTSIDE a
#: Classification section to find a claim that wandered off.
CLAIM_KNOWN_DEST = re.compile(
    r"^\s*[-*]\s+`(?P<path>[^`]+)`\s*(?:\([^)`]*\)\s*)?"
    rf"{_SEPARATOR}\s+(?P<dest>{'|'.join(CLAIMISH)})\b"
)

_HEADING = re.compile(r"^(?P<hashes>#+)\s+(?P<title>.*?)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+\S")

#: A backticked token that looks like a path: it has a slash or a file extension.
#: `send_capable` and `gated` are backticked identifiers, not paths, and must not
#: turn ordinary prose into a suspected claim.
_PATH_TOKEN = re.compile(
    r"`(?:[A-Za-z0-9_.*-]*/[A-Za-z0-9_./*-]*|[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,6})`"
)

#: A destination word NOT inside backticks. The negative look-behind/ahead on
#: backtick and hyphen keeps `/publish-corporate` and `` `engine` `` out.
_BARE_DESTINATION = re.compile(
    rf"(?<![\w/`-])(?:{'|'.join(DESTINATIONS)})(?![\w`-])"
)

#: A bare destination in PREDICATE position - the shape an assertion takes.
#:
#: Bare-ness alone is not enough once the prose check reads a whole paragraph
#: rather than a single line, because two of the three destination words are also
#: ordinary English nouns in this workspace's vocabulary. Both real sections
#: contain the sentence "the engine repo is public", and joining a paragraph put
#: that bare "engine" beside a backticked `scripts/` path from the NEXT line.
#: Measured 2026-09-01: bare-ness alone produced two false positives on the live
#: tree, at `corporate-docs.md:121` and `tiered-risk.md:48`, both on that phrase.
#:
#: A claim puts the destination in the predicate - "`x.py` is corporate", "these
#: stayed corporate", "`y` -> private". A noun-modifier does not: "the engine
#: repo", "the private overlay". Requiring a copula or an arrow immediately
#: before the word separates the two without a denylist of noun phrases, which
#: would rot exactly the way the claims did.
_CLAIM_COPULA = (
    r"(?:is|are|was|were|be|stay|stays|stayed|remain|remains|remained|"
    r"resolve|resolves|resolved|become|becomes|became|reads|read|says|"
    r"classified as|routed to|routes to|treated as|marked|->|=>|→|:=)"
)
_PREDICATE_DESTINATION = re.compile(
    rf"(?:\b{_CLAIM_COPULA}\b|->|=>|→)"
    r"\s+(?:all\s+|both\s+|now\s+|still\s+|already\s+|therefore\s+|"
    r"genuinely\s+|simply\s+|of course\s+)*"
    rf"(?<![\w/`-])(?:{'|'.join(DESTINATIONS)})(?![\w`-])"
)

#: The canonical claim shape with the leading `- ` taken off: a backticked PATH,
#: a separator, a destination. Dropping the bullet marker is the cheapest way to
#: move a claim out of the parser's reach while leaving it just as readable, and
#: `_PREDICATE_DESTINATION` cannot see it because a dash or a colon is not a
#: copula. Measured 2026-09-01 against the predicate rule alone:
#: "`scripts/utils/tool_risk.py`: corporate" and the em-dash spelling both
#: returned zero claims and zero complaints.
#:
#: Safe to anchor this loosely because the separator must sit IMMEDIATELY after
#: the closing backtick of a path-shaped token. "The engine repo is public, so a
#: `scripts/` file ships" has no separator there, and every descriptive bullet in
#: the rules ("- `outputs/` - CEO deliverables") is followed by a word that is
#: not a destination.
_INLINE_CLAIM = re.compile(
    r"`[^`]*(?:/|\.[A-Za-z0-9]{1,6})[^`]*`\s*(?:\([^)`]*\)\s*)?"
    rf"{_SEPARATOR}\s+(?<![\w/`-])(?:{'|'.join(DESTINATIONS)})(?![\w`-])"
)


def _asserts_a_destination(text: str) -> bool:
    """True when `text` puts a destination word in a claiming position."""
    return bool(_PREDICATE_DESTINATION.search(text) or _INLINE_CLAIM.search(text))


def _display(path: Path) -> str:
    """Repo-relative when the file is in the repo; absolute for a tmp fixture."""
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


class Claim:
    """One `<path> -> <destination>` assertion made in rule prose."""

    __slots__ = ("file", "line", "path", "destination", "raw")

    def __init__(self, file: Path, line: int, path: str, destination: str, raw: str):
        self.file = file
        self.line = line
        self.path = path
        self.destination = destination
        self.raw = raw

    @property
    def where(self) -> str:
        return f"{_display(self.file)}:{self.line}"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Claim {self.where} {self.path} -> {self.destination}>"


def _is_classification_heading(title: str) -> bool:
    return title.strip().lower().rstrip(":").strip("*_ ") == "classification"


def audit_rule_file(path: Path) -> tuple[list[Claim], list[str]]:
    """Parse one rule file. Returns (claims, complaints).

    A complaint is a fully-formed failure message about a line the contract
    rejects: a bullet inside a Classification section that is not a canonical
    claim, prose inside one that asserts a destination for a backticked path, or a
    canonical claim bullet found outside one. Resolution is NOT done here, so the
    parser can be exercised on a fixture whose paths need not exist.
    """
    claims: list[Claim] = []
    complaints: list[str] = []
    inside = False
    section_depth = 0

    # The prose check's unit is a paragraph: a run of consecutive non-blank lines
    # that is not a heading and not a bullet. A blank line, a bullet, a heading,
    # leaving the section, or EOF ends it. Joining the run before testing it is
    # what makes the check survive the hard wrap every rule file in this tree
    # uses; testing line by line does not.
    para: list[str] = []
    para_start = 0

    def _flush_paragraph() -> None:
        nonlocal para, para_start
        if not para:
            return
        joined = " ".join(chunk.strip() for chunk in para)
        if _PATH_TOKEN.search(joined) and _asserts_a_destination(joined):
            complaints.append(
                f"{_display(path)}:{para_start}: prose in a Classification section "
                f"asserts a destination for a backticked path without being a "
                f"claim bullet: {joined[:160]!r}. Make it a bullet, or backtick "
                f"the destination word to mark it as a quoted term."
            )
        para = []

    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        heading = _HEADING.match(line)
        if heading:
            _flush_paragraph()
            depth = len(heading.group("hashes"))
            if _is_classification_heading(heading.group("title")):
                inside, section_depth = True, depth
            elif inside and depth <= section_depth:
                inside = False
            continue

        where = f"{_display(path)}:{lineno}"

        if not inside:
            wandered = CLAIM_KNOWN_DEST.match(line)
            if wandered:
                complaints.append(
                    f"{where}: a classification claim bullet outside a "
                    f"'Classification' section, where nothing audits it: "
                    f"{line.strip()!r}. Move it into that section."
                )
            continue

        if _BULLET.match(line):
            _flush_paragraph()
            parsed = CLAIM_ANY_DEST.match(line)
            if not parsed:
                complaints.append(
                    f"{where}: bullet in a Classification section does not parse as a "
                    f"claim: {line.strip()!r}. Required shape: "
                    f"- `path/to/thing` - engine|private|corporate."
                )
                continue
            claims.append(
                Claim(path, lineno, parsed.group("path"),
                      parsed.group("dest"), line.strip())
            )
            continue

        if not line.strip():
            _flush_paragraph()
            continue

        if not para:
            para_start = lineno
        para.append(line)

    _flush_paragraph()
    return claims, complaints


def audit_rules(paths) -> tuple[list[Claim], list[str]]:
    claims: list[Claim] = []
    complaints: list[str] = []
    for path in paths:
        file_claims, file_complaints = audit_rule_file(path)
        claims.extend(file_claims)
        complaints.extend(file_complaints)
    return claims, complaints


def resolve_complaints(claims) -> list[str]:
    """Ask the resolver about every claim. One message per disagreement."""
    out: list[str] = []
    for claim in claims:
        if claim.destination not in DESTINATIONS:
            out.append(
                f"{claim.where}: claims destination {claim.destination!r} for "
                f"{claim.path!r}, which is not a routing destination. The three are "
                f"{', '.join(DESTINATIONS)}. ('ceo-only' is the two-value exec-sync "
                f"collapse of 'private', not a destination.)"
            )
            continue
        actual = get_routing_destination(claim.path)
        if actual != claim.destination:
            out.append(
                f"{claim.where}: claims {claim.path!r} is {claim.destination!r}; "
                f"get_routing_destination() returns {actual!r}. "
                f"Claim line: {claim.raw!r}"
            )
    return out


RULE_FILES = sorted(RULES_DIR.glob("*.md"))
LIVE_CLAIMS, LIVE_COMPLAINTS = audit_rules(RULE_FILES)


# --------------------------------------------------------------------------
# The live tree
# --------------------------------------------------------------------------

def test_the_rules_directory_is_where_we_think_it_is():
    """A path list that resolves to nothing would make every check below green."""
    assert RULES_DIR.is_dir(), f"no rules directory at {RULES_DIR}"
    assert len(RULE_FILES) >= 20, (
        f"only {len(RULE_FILES)} rule files found; the sweep is looking in the "
        f"wrong place or the glob has narrowed"
    )


def test_the_parser_still_finds_the_claims_it_is_meant_to_police():
    """The floor. A parser that silently matches nothing is green forever.

    That failure mode is the entire reason the defect above survived: nothing was
    reading the prose, so nothing disagreed with it, so it looked verified. A bare
    count is the weakest possible floor, so this asserts two things.

    Ten is the floor, against 13 claims present on 2026-08-31 (10 in
    corporate-docs.md, 3 in tiered-risk.md). It sits below the live count so that
    deliberately dropping a bullet or two is not a test failure, and far enough
    above zero that a regex that stops matching, a heading that gets renamed, or a
    section that gets deleted is red immediately.

    The second assertion is the one that actually matters: the claims must come
    from at least two distinct rule files. A parser that still matched one section
    while going blind to the other would clear a raw count of ten on
    corporate-docs.md alone, and tiered-risk.md carried three wrong claims of its
    own. Coverage is not a total.
    """
    assert len(LIVE_CLAIMS) >= 10, (
        f"parsed only {len(LIVE_CLAIMS)} classification claims from "
        f"{len(RULE_FILES)} rule files. Either the claims were removed (fine, lower "
        f"this floor deliberately) or the parser stopped matching them (not fine)."
    )
    sources = {claim.file.name for claim in LIVE_CLAIMS}
    assert len(sources) >= 2, (
        f"every parsed claim came from {sources}. The parser must see more than one "
        f"Classification section; both known sections carried wrong claims."
    )


def test_no_rule_claims_a_destination_the_resolver_disagrees_with():
    """The check itself: rule prose against `config/routing-map.yaml`."""
    disagreements = resolve_complaints(LIVE_CLAIMS)
    assert not disagreements, (
        "rule prose disagrees with get_routing_destination():\n  "
        + "\n  ".join(disagreements)
    )


def test_no_classification_claim_escapes_the_parser():
    """Bullets that do not parse, prose claims, and claims outside a section."""
    assert not LIVE_COMPLAINTS, (
        "classification claims that the resolver check cannot see:\n  "
        + "\n  ".join(LIVE_COMPLAINTS)
    )


@pytest.mark.parametrize(
    "rel, expected",
    [
        # The two rules this module was written for, asserted directly so a
        # regression is named rather than merely counted.
        (".claude/rules/corporate-docs.md", "engine"),
        (".claude/rules/tiered-risk.md", "engine"),
        ("scripts/render-doctype.py", "engine"),
        ("scripts/utils/tool_risk.py", "engine"),
        ("config/tool-risk.json", "engine"),
        ("reference/corporate-style-guide.md", "engine"),
        (".claude/skills/xpager/SKILL.md", "engine"),
        # Both other destinations, so a resolver stuck on 'engine' is caught.
        ("datastore/brand/templates/doctypes/letter.html", "corporate"),
        ("outputs/documents/misha-hanin/letter/x.pdf", "private"),
    ],
)
def test_the_paths_the_two_rules_name_resolve_where_the_rules_now_say(rel, expected):
    assert get_routing_destination(rel) == expected


def test_reference_has_no_corporate_directory_default():
    """corporate-docs.md invented one and used it to justify a wrong claim.

    The claim read "corporate (shared with all execs via `reference/` directory
    default)". `reference/` has no such default: it falls through to the map's
    `engine` default, and the CEO files inside it are per-file `private` carve-outs.
    """
    assert get_routing_destination("reference/") == "engine"
    assert get_routing_destination("reference/corporate-style-guide.md") == "engine"


# --------------------------------------------------------------------------
# Negative cases. Each fails if the corresponding check is removed.
# --------------------------------------------------------------------------

def _fixture(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "fake-rule.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_wrong_claim_is_caught(tmp_path):
    """The defect, reconstructed. Without the resolver check this passes."""
    fake = _fixture(tmp_path, "# Fake\n\n## Classification\n\n"
                              "- `scripts/render-doctype.py` - corporate.\n")
    claims, complaints = audit_rule_file(fake)
    assert not complaints, complaints
    assert len(claims) == 1
    problems = resolve_complaints(claims)
    assert len(problems) == 1
    assert "scripts/render-doctype.py" in problems[0]
    assert "'corporate'" in problems[0] and "'engine'" in problems[0]


def test_a_correct_claim_is_not_caught(tmp_path):
    """The other direction, so the check is not simply always-red."""
    fake = _fixture(tmp_path, "# Fake\n\n## Classification\n\n"
                              "- `scripts/render-doctype.py` - engine.\n"
                              "- `outputs/documents/` - private.\n"
                              "- `datastore/brand/templates/doctypes/` - corporate.\n")
    claims, complaints = audit_rule_file(fake)
    assert not complaints, complaints
    assert len(claims) == 3
    assert resolve_complaints(claims) == []


def test_a_claim_smuggled_into_prose_is_caught(tmp_path):
    """tiered-risk.md's actual pre-fix shape: a claim in a sentence, not a bullet.

    Without the prose check the parser finds zero claims here and reports nothing,
    which is precisely how three wrong claims survived.
    """
    fake = _fixture(tmp_path, "# Fake\n\n## Classification\n\n"
                              "The fleet-safe primitives (`scripts/utils/tool_risk.py`,"
                              " `config/tool-risk.json`) are corporate.\n")
    claims, complaints = audit_rule_file(fake)
    assert claims == []
    assert len(complaints) == 1
    assert "prose in a Classification section" in complaints[0]


@pytest.mark.parametrize("body", [
    # The wrap lands between the path and its destination word.
    "The fleet-safe primitive `scripts/utils/tool_risk.py` is\ncorporate.\n",
    # ...and the other way round, destination first.
    "These are corporate:\n`scripts/utils/tool_risk.py` and its config.\n",
    # Three lines, with the two halves not even adjacent.
    "`scripts/utils/tool_risk.py` decides the tier for every\naction the queue "
    "carries, and the file itself is\ncorporate.\n",
])
def test_a_prose_claim_split_by_a_line_wrap_is_still_caught(tmp_path, body):
    """The gap that made the prose check one newline from useless.

    Until 2026-09-01 this check ran per LINE, so it needed the backticked path
    and the bare destination word to land on the same one. Every rule file in
    `.claude/rules/` is hard-wrapped at roughly 85 columns, and the join between
    a path and the word that classifies it is exactly where a wrap falls in a
    sentence of this shape.

    Measured on the pre-fix parser, all three bodies below returned
    `(claims=0, complaints=0)` - indistinguishable from a file that makes no
    claim at all - while the same sentences on one line returned one complaint
    each. `tiered-risk.md` smuggled three wrong claims through as prose; one
    newline in the right place and this module would have reported nothing about
    them either.
    """
    fake = _fixture(tmp_path, "# Fake\n\n## Classification\n\n" + body)
    claims, complaints = audit_rule_file(fake)
    assert claims == []
    assert len(complaints) == 1, (
        f"a wrapped prose claim escaped the parser entirely: {complaints}")
    assert "prose in a Classification section" in complaints[0]
    assert "tool_risk.py" in complaints[0], (
        "the complaint must quote the joined paragraph, not just the line the "
        "destination word happened to land on")


def test_the_engine_repo_phrase_is_not_a_claim_but_is_corporate_still_is(tmp_path):
    """Both directions of the predicate rule, on the real sentence that forced it.

    "the engine repo is public" appears in BOTH live Classification sections, and
    once the prose check reads a paragraph it sits beside a backticked `scripts/`
    path from the next line. Suppressing it must not suppress a real claim, so
    this pins the discrimination rather than the outcome: the noun-modifier is
    not a claim, the predicate on the very same destination word is.

    Derived both ways on purpose. Asserting only that the live tree is clean
    would be satisfied by a regex that matches nothing at all.
    """
    noun = "The engine repo is public, so a `scripts/` file ships to everyone."
    predicate = "The renderer `scripts/render-doctype.py` is engine."
    assert _BARE_DESTINATION.search(noun), (
        "the phrase must still contain a BARE destination word; if it does not, "
        "this test has stopped exercising the false positive it was written for")
    assert not _PREDICATE_DESTINATION.search(noun), (
        f"noun-modifier 'engine' read as a claim: {noun!r}")
    assert _PREDICATE_DESTINATION.search(predicate), (
        f"a real predicate claim on the SAME word was suppressed: {predicate!r}")

    # And end to end, through the parser, in the wrapped shape that produced the
    # false positive: path and phrase on different lines of one paragraph.
    fake = _fixture(tmp_path, "# Fake\n\n## Classification\n\n"
                              "`engine` is the WIDER destination. The engine repo is\n"
                              "public, so a `scripts/` file already ships in the\n"
                              "public clone.\n")
    claims, complaints = audit_rule_file(fake)
    assert claims == []
    assert complaints == [], f"live-tree prose flagged as a claim: {complaints}"


@pytest.mark.parametrize("sentence", [
    "`scripts/utils/tool_risk.py`: corporate",
    "`scripts/utils/tool_risk.py` - corporate",
    "`scripts/utils/tool_risk.py` — corporate",
    "`config/tool-risk.json` (the tier table) - corporate",
])
def test_a_claim_with_the_bullet_marker_removed_is_still_caught(tmp_path, sentence):
    """Dropping `- ` is the cheapest way out of the parser, so it must not work.

    The canonical bullet is audited; the identical sentence without its marker is
    prose, and a dash or a colon is not a copula, so the predicate rule alone
    could not see it. Measured 2026-09-01 before `_INLINE_CLAIM` existed: the
    colon and em-dash spellings both returned zero claims AND zero complaints,
    which is the same output as a file that says nothing.
    """
    fake = _fixture(tmp_path, "# Fake\n\n## Classification\n\n" + sentence + "\n")
    claims, complaints = audit_rule_file(fake)
    assert claims == []
    assert len(complaints) == 1, f"an unbulleted claim escaped: {complaints}"


@pytest.mark.parametrize("sentence", [
    # A path, a separator, and an ordinary noun. Real shapes: the rules describe
    # paths this way constantly.
    "`config/routing-map.yaml`: the single classification input.",
    "The renderer `scripts/render-doctype.py` - loaded once per doctype.",
    "`datastore/brand/templates/doctypes/` (the locked set) — content, not code.",
])
def test_a_path_followed_by_a_non_destination_word_is_not_a_claim(tmp_path, sentence):
    """The false-positive direction for `_INLINE_CLAIM`, and it must be INSIDE.

    Anchoring on "backticked path, separator, word" without checking that the
    word is a destination would flag every descriptive line in the rules, and an
    unusable check gets deleted rather than fixed.

    The fixture sits inside a `## Classification` section deliberately. Placed
    under a `## Notes` heading, as this test was first written, it proved
    nothing: the prose check only runs while `inside` is true, so widening
    `_INLINE_CLAIM` to accept any trailing word left it green. Measured
    2026-09-01 - that mutation SURVIVED the first version of this test and is
    killed by this one. A negative case has to be on the code path it is
    negating.
    """
    fake = _fixture(tmp_path, "# Fake\n\n## Classification\n\n"
                              "- `scripts/render-doctype.py` - engine.\n\n"
                              + sentence + "\n")
    claims, complaints = audit_rule_file(fake)
    assert len(claims) == 1
    assert complaints == [], (
        f"an ordinary descriptive line was read as a classification claim: "
        f"{complaints}")


def test_a_backticked_destination_after_a_copula_is_still_allowed(tmp_path):
    """`became `engine`` is the map header's own wording, quoted, not asserted."""
    fake = _fixture(tmp_path, "# Fake\n\n## Classification\n\n"
                              "- `scripts/render-doctype.py` - engine.\n\n"
                              "Code directories in `scripts/` that were `corporate`\n"
                              "became `engine`, because code is not data.\n")
    claims, complaints = audit_rule_file(fake)
    assert len(claims) == 1
    assert complaints == [], complaints


def test_a_paragraph_break_stops_the_join(tmp_path):
    """The boundary, so the join is a paragraph and not the whole section.

    Without a case ON this line, `_flush_paragraph` could join every prose line
    in the section and pair a path in one paragraph with a destination word four
    paragraphs later. That would flag the real `corporate-docs.md` section, and
    an unusable check gets deleted rather than fixed.
    """
    fake = _fixture(tmp_path, "# Fake\n\n## Classification\n\n"
                              "- `scripts/render-doctype.py` - engine.\n\n"
                              "The path above is the renderer.\n\n"
                              "Templates are content, so they stayed corporate.\n")
    claims, complaints = audit_rule_file(fake)
    assert len(claims) == 1
    assert complaints == [], (
        f"the join crossed a blank line and manufactured a claim: {complaints}")


def test_a_bullet_ends_the_paragraph_too(tmp_path):
    """The other boundary: prose, then a bullet, then a bare destination word."""
    fake = _fixture(tmp_path, "# Fake\n\n## Classification\n\n"
                              "Resolved for `scripts/render-doctype.py` on 2026-08-31.\n"
                              "- `outputs/documents/` - private.\n"
                              "Everything else stayed corporate.\n")
    claims, complaints = audit_rule_file(fake)
    assert len(claims) == 1
    assert complaints == [], (
        f"the join swallowed the bullet between two prose runs: {complaints}")


def test_a_quoted_destination_word_in_prose_is_allowed(tmp_path):
    """The escape that keeps the prose check usable, and its boundary.

    A backticked destination is a quoted term, the convention
    `.claude/rules/classification.md` already uses. If this were rejected, authors
    could not explain a classification at all, and the pressure would be to delete
    the explanation rather than fix the claim.
    """
    fake = _fixture(tmp_path, "# Fake\n\n## Classification\n\n"
                              "- `scripts/render-doctype.py` - engine.\n\n"
                              "It read `corporate` until 2026-08-31; the map's header "
                              "records why code moved to `engine`.\n")
    claims, complaints = audit_rule_file(fake)
    assert not complaints, complaints
    assert len(claims) == 1


def test_an_unparseable_bullet_is_a_failure_not_a_skip(tmp_path):
    """A bullet the parser does not understand must be loud.

    The old corporate-docs.md packed four skill paths into one bullet. A parser
    that just skipped what it could not read would have audited none of them.
    """
    fake = _fixture(tmp_path, "# Fake\n\n## Classification\n\n"
                              "- `.claude/skills/xpager/`, `.claude/skills/proposal/`"
                              " - corporate.\n")
    claims, complaints = audit_rule_file(fake)
    assert claims == []
    assert len(complaints) == 1
    assert "does not parse as a claim" in complaints[0]


def test_ceo_only_is_rejected_as_a_destination(tmp_path):
    """The old corporate-docs.md wrote `ceo-only`, the collapsed exec-sync label.

    It is not one of the three destinations, and accepting it would let a rule
    describe a file in a vocabulary the resolver cannot answer in.
    """
    fake = _fixture(tmp_path, "# Fake\n\n## Classification\n\n"
                              "- `outputs/documents/` - ceo-only.\n")
    claims, complaints = audit_rule_file(fake)
    assert not complaints, complaints
    problems = resolve_complaints(claims)
    assert len(problems) == 1
    assert "not a routing destination" in problems[0]


def test_a_claim_outside_a_classification_section_is_caught(tmp_path):
    """Moving the bullet out of the section must not buy silence."""
    fake = _fixture(tmp_path, "# Fake\n\n## Notes\n\n"
                              "- `scripts/render-doctype.py` - corporate.\n")
    claims, complaints = audit_rule_file(fake)
    assert claims == []
    assert len(complaints) == 1
    assert "outside a 'Classification' section" in complaints[0]


def test_an_ordinary_descriptive_bullet_is_not_mistaken_for_a_claim(tmp_path):
    """The false-positive direction, on real lines from `classification.md`.

    The rules are full of `- \\`path\\` - description` bullets. Flagging those would
    make the outside-a-section sweep unusable, and the usual response to an
    unusable check is to delete it.
    """
    fake = _fixture(tmp_path, "# Fake\n\n## Never ask\n\n"
                              "- `outputs/` - CEO deliverables\n"
                              "- `crm/contacts/` - personal CRM data\n"
                              "- `.claude/rules/hidden-chars.md` - zero invisible "
                              "Unicode policy.\n")
    claims, complaints = audit_rule_file(fake)
    assert claims == []
    assert complaints == [], complaints


def test_the_section_ends_at_the_next_heading_of_equal_or_higher_rank(tmp_path):
    """Otherwise a Classification section would swallow the rest of the file."""
    fake = _fixture(tmp_path, "# Fake\n\n## Classification\n\n"
                              "- `scripts/render-doctype.py` - engine.\n\n"
                              "## Change control\n\n"
                              "Rewriting this needs approval; `scripts/x.py` is "
                              "corporate business.\n")
    claims, complaints = audit_rule_file(fake)
    assert len(claims) == 1
    assert complaints == [], complaints
