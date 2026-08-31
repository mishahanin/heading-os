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

    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        heading = _HEADING.match(line)
        if heading:
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

        if line.strip() and _PATH_TOKEN.search(line) and _BARE_DESTINATION.search(line):
            complaints.append(
                f"{where}: prose in a Classification section asserts a destination "
                f"for a backticked path without being a claim bullet: "
                f"{line.strip()[:160]!r}. Make it a bullet, or backtick the "
                f"destination word to mark it as a quoted term."
            )

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
