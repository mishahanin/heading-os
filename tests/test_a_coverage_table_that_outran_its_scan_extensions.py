"""A stated coverage that outran the suffix list the walk actually uses.

`.claude/rules/visual-design-discipline.md` said "PPTX, DOCX and PDF stay
regex-only", which reads as three formats on the regex path. The regex walk
visits exactly:

    SCAN_EXTENSIONS = (".html", ".htm", ".svg", ".pptx")

so PDF and DOCX were read by NEITHER engine. The rule's own frontmatter listed
`outputs/**/*.pdf`, and its carve-out puts all five locked corporate doctypes in
scope, every one of which renders to PDF. Those renders were audited by nothing
while the rule described them as covered.

This binder reads the truth from the code (`SCAN_EXTENSIONS`, imported, never
retyped here) and parses the coverage claims out of the two places that state
them: the table in the rule, and the block in the checker's module docstring.
A disagreement in either direction fails.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RULE = REPO / ".claude" / "rules" / "visual-design-discipline.md"
CHECKER = REPO / "scripts" / "visual-discipline-check.py"


def _load_checker():
    """Import the hyphenated script by path; its name is not a Python ident."""
    spec = importlib.util.spec_from_file_location("_vdc_under_test", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scan_extensions() -> set[str]:
    exts = set(_load_checker().SCAN_EXTENSIONS)
    assert exts, "SCAN_EXTENSIONS is empty; nothing below would mean anything"
    assert all(e.startswith(".") for e in exts), f"malformed suffixes: {exts}"
    return exts


# ------------------------------------------------------------------
# Parsing the rule's coverage table
# ------------------------------------------------------------------

_SUFFIXABLE = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def parse_coverage_table(text: str) -> dict[str, dict[str, bool]]:
    """Return {suffix: {"regex": bool, "deep": bool}} from a markdown table.

    The suffixes are derived from the format-label cell, not from a map typed
    into this test: every alphabetic token in the label becomes a candidate
    suffix. `HTML / HTM` yields `.html` and `.htm`; `PDF` yields `.pdf`.
    """
    rows: dict[str, dict[str, bool]] = {}
    header_seen = False
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip().replace("*", "") for c in line.strip("|").split("|")]
        if len(cells) != 3:
            continue
        low = [c.lower() for c in cells]
        if low[0] == "format" and "regex" in low[1] and "deep" in low[2]:
            header_seen = True
            continue
        if not header_seen:
            continue
        if set(cells[0]) <= set("-: "):  # the |---|---|---| separator
            continue
        verdicts = []
        for cell in low[1:]:
            if cell.startswith("yes"):
                verdicts.append(True)
            elif cell.startswith("no"):
                verdicts.append(False)
            else:
                verdicts.append(None)
        if None in verdicts:
            continue
        for token in _SUFFIXABLE.findall(cells[0]):
            rows["." + token.lower()] = {"regex": verdicts[0], "deep": verdicts[1]}
    return rows


def test_the_rule_carries_a_coverage_table():
    table = parse_coverage_table(RULE.read_text(encoding="utf-8"))
    assert len(table) >= 6, (
        f"parsed only {len(table)} format rows from the rule's coverage table "
        f"({sorted(table)}); either the table is gone or the parser drifted, "
        f"and every assertion below would pass vacuously"
    )
    for required in (".html", ".svg", ".pptx", ".pdf", ".docx"):
        assert required in table, f"the coverage table says nothing about {required}"


def test_rule_table_agrees_with_scan_extensions(scan_extensions):
    table = parse_coverage_table(RULE.read_text(encoding="utf-8"))
    claimed_scanned = {ext for ext, v in table.items() if v["regex"]}
    assert claimed_scanned == scan_extensions, (
        f"the rule's coverage table claims the regex engine reads "
        f"{sorted(claimed_scanned)}, but SCAN_EXTENSIONS is "
        f"{sorted(scan_extensions)}.\n"
        f"  claimed and not scanned: {sorted(claimed_scanned - scan_extensions)}\n"
        f"  scanned and not claimed: {sorted(scan_extensions - claimed_scanned)}"
    )


def test_pdf_and_docx_are_stated_as_covered_by_nothing(scan_extensions):
    """The specific pair the old wording hid. Asserted only while it is true."""
    table = parse_coverage_table(RULE.read_text(encoding="utf-8"))
    for ext in (".pdf", ".docx"):
        if ext in scan_extensions:
            pytest.skip(f"{ext} is now scanned; the rule should say so")
        assert table[ext] == {"regex": False, "deep": False}, (
            f"the rule marks {ext} as covered, but it is absent from "
            f"SCAN_EXTENSIONS and the deep engine reads HTML and SVG only"
        )


def test_the_parser_rejects_a_table_that_overclaims(scan_extensions):
    """Negative case. Without it, a parser that returned {} would pass above.

    A table identical to the real one except that PDF claims a regex scan must
    be caught by exactly the comparison the previous test performs.
    """
    good = RULE.read_text(encoding="utf-8")
    mutated = re.sub(
        r"\|\s*\*\*PDF\*\*\s*\|\s*\*\*no\*\*\s*\|",
        "| **PDF** | **yes** |",
        good,
    )
    assert mutated != good, "could not mutate the PDF row; the table shape changed"

    table = parse_coverage_table(mutated)
    assert table[".pdf"]["regex"] is True, "the mutation did not take"
    claimed = {ext for ext, v in table.items() if v["regex"]}
    assert claimed != scan_extensions, (
        "an overclaiming table compared EQUAL to SCAN_EXTENSIONS; the "
        "comparison in test_rule_table_agrees_with_scan_extensions cannot "
        "detect an overclaim and is not a guard"
    )
    assert ".pdf" in claimed - scan_extensions


def test_the_parser_rejects_a_table_that_underclaims(scan_extensions):
    """The other direction: a format that IS scanned but marked no."""
    good = RULE.read_text(encoding="utf-8")
    mutated = re.sub(r"\|\s*SVG\s*\|\s*yes\s*\|", "| SVG | no |", good)
    assert mutated != good, "could not mutate the SVG row; the table shape changed"
    claimed = {ext for ext, v in parse_coverage_table(mutated).items() if v["regex"]}
    assert claimed != scan_extensions, "an underclaiming table compared EQUAL"
    assert ".svg" in scan_extensions - claimed


# ------------------------------------------------------------------
# Parsing the checker's own docstring
# ------------------------------------------------------------------

def parse_docstring_coverage(text: str) -> dict[str, bool]:
    """Return {suffix: scanned_by_regex} from the docstring coverage block.

    Lines look like `      .html .htm   both engines`. A line is a coverage
    line when it starts with a dotted suffix.
    """
    rows: dict[str, bool] = {}
    for line in text.splitlines():
        stripped = line.strip()
        match = re.match(r"^((?:\.\w+[ ,]*)+)\s\s+(\S.*)$", stripped)
        if not match:
            continue
        suffixes = re.findall(r"\.\w+", match.group(1))
        verdict = match.group(2).lower()
        if verdict.startswith(("both engines", "regex only")):
            scanned = True
        elif verdict.startswith("neither"):
            scanned = False
        else:
            continue
        for suffix in suffixes:
            rows[suffix.lower()] = scanned
    return rows


def test_checker_docstring_agrees_with_scan_extensions(scan_extensions):
    doc = _load_checker().__doc__
    assert doc, "the checker lost its module docstring"
    rows = parse_docstring_coverage(doc)
    assert len(rows) >= 6, (
        f"parsed only {len(rows)} coverage lines from the checker docstring "
        f"({sorted(rows)}); the block or the parser drifted"
    )
    claimed = {ext for ext, scanned in rows.items() if scanned}
    assert claimed == scan_extensions, (
        f"the checker's docstring claims the regex engine reads "
        f"{sorted(claimed)}, SCAN_EXTENSIONS says {sorted(scan_extensions)}"
    )


def test_docstring_parser_rejects_an_overclaim(scan_extensions):
    """Negative case for the docstring parser."""
    doc = _load_checker().__doc__
    mutated = re.sub(r"(\.pdf\s+)neither", r"\1regex only", doc)
    assert mutated != doc, "could not mutate the .pdf docstring line"
    claimed = {e for e, s in parse_docstring_coverage(mutated).items() if s}
    assert claimed != scan_extensions, "an overclaiming docstring compared EQUAL"
    assert ".pdf" in claimed


def test_the_two_statements_of_coverage_agree_with_each_other():
    """Rule table and script docstring are two copies; hold them together."""
    table = parse_coverage_table(RULE.read_text(encoding="utf-8"))
    rows = parse_docstring_coverage(_load_checker().__doc__)
    shared = set(table) & set(rows)
    assert len(shared) >= 5, f"only {len(shared)} formats stated in both places"
    for ext in sorted(shared):
        assert table[ext]["regex"] == rows[ext], (
            f"the rule and the checker docstring disagree about {ext}: "
            f"rule regex={table[ext]['regex']}, docstring scanned={rows[ext]}"
        )


# ------------------------------------------------------------------
# The frontmatter globs (V5)
# ------------------------------------------------------------------

def _frontmatter_paths(text: str) -> list[str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "the rule lost its frontmatter"
    body = match.group(1)
    return re.findall(r"^\s*-\s*\"([^\"]+)\"\s*$", body, re.M)


def _measure_rules():
    """Run the workspace's OWN rule classifier, not a reimplementation of it."""
    path = REPO / "scripts" / "context-floor-audit.py"
    spec = importlib.util.spec_from_file_location("_cfa_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.measure_rules(REPO)


def test_the_workspace_classifier_calls_this_rule_path_scoped():
    """The prose said "Always-active rule" over a non-empty `paths:` list.

    `measure_rules` in `scripts/context-floor-audit.py` is the classifier the
    workspace actually measures its context floor with. Ask it rather than
    reading the frontmatter a second way here.
    """
    measured = _measure_rules()
    always = {r["rule"] for r in measured["always_on"]}
    scoped = {r["rule"] for r in measured["path_scoped"]}
    assert len(always) + len(scoped) >= 20, (
        f"the classifier saw only {len(always) + len(scoped)} rules; it is not "
        f"looking at .claude/rules/ and nothing below would mean anything"
    )
    assert "skill-router.md" in always, (
        "sanity check failed: skill-router.md (paths: [] + always_active: true) "
        "should classify always-on, so a 'path_scoped' verdict below would be "
        "the classifier misreading, not a real finding"
    )
    assert RULE.name in scoped, (
        f"{RULE.name} classifies as always-on. Either give it `paths: []` and "
        f"accept ~{RULE.stat().st_size} bytes on the always-on floor, or keep "
        f"the globs and keep the prose path-scoped."
    )
    assert RULE.name not in always


def test_classifier_agrees_with_prose():
    text = RULE.read_text(encoding="utf-8")
    globs = _frontmatter_paths(text)
    assert globs, "the rule has an empty paths: list, which means always-on"

    flat = " ".join(text.split())
    assert "Path-scoped rule, not always-on" in flat, (
        "the rule carries a non-empty paths: list, so the workspace's own "
        "classifier (context-floor-audit.py measure_rules) counts it as "
        "path-scoped. The prose must not call itself always-active."
    )
    # The old wording, in any re-wrapped form, asserted the opposite.
    assert not re.search(r"^\s*>\s*Always-active rule\.", text, re.M), (
        "the 'Always-active rule.' opener is back on a path-scoped rule"
    )


_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _overlay_rooted(globs: list[str]) -> list[str]:
    """Globs whose first path segment does not exist in the engine tree.

    `outputs/` and `datastore/` live only in the operator's private data
    overlay, so a glob rooted at either cannot match an engine-relative path.
    Derived by asking the filesystem, never from a list typed here.
    """
    dead = []
    for glob in globs:
        root = glob.split("/")[0].split("*")[0]
        if root and not (REPO / root).exists():
            dead.append(glob)
    return dead


def test_the_rule_states_how_many_globs_cannot_fire_here():
    """The rule must carry the count, and the count must be measured.

    A glob rooted in the overlay is not automatically a defect - the sibling
    rule `.claude/rules/datastore.md` is scoped the same way. Silently leaving
    it unstated IS the defect: a reader takes eight globs for eight triggers.
    """
    text = RULE.read_text(encoding="utf-8")
    globs = _frontmatter_paths(text)
    assert len(globs) >= 5, f"only {len(globs)} globs parsed; parser drifted"

    dead = _overlay_rooted(globs)
    live = [g for g in globs if g not in dead]
    assert live, "no glob in the rule can fire from this repo at all"

    flat = " ".join(text.split())
    match = re.search(
        r"\*\*(\w+) of the (\w+) globs are rooted in the private data overlay",
        flat,
    )
    assert match, (
        "the rule does not state how many of its globs are overlay-rooted. "
        f"Measured: {len(dead)} of {len(globs)} ({dead})."
    )
    stated_dead = _NUMBER_WORDS.get(match.group(1).lower())
    stated_total = _NUMBER_WORDS.get(match.group(2).lower())
    assert stated_dead == len(dead), (
        f"the rule says {match.group(1)} globs are overlay-rooted; "
        f"{len(dead)} are: {dead}"
    )
    assert stated_total == len(globs), (
        f"the rule says {match.group(2)} globs; the frontmatter has {len(globs)}"
    )


def test_the_overlay_detector_is_not_vacuous(tmp_path, monkeypatch):
    """Negative case: a glob rooted somewhere that DOES exist must not be
    flagged, and one rooted at a missing directory must be."""
    assert _overlay_rooted(["docs/**"]) == [], "docs/ exists; must not be flagged"
    assert _overlay_rooted(["scripts/**"]) == [], "scripts/ exists"
    missing = "definitely-not-a-directory-here/**"
    assert _overlay_rooted([missing]) == [missing], (
        "the detector failed to flag a glob rooted at a path that is absent; "
        "it would return [] for every input and prove nothing"
    )


def test_the_two_surfaces_the_prose_governs_are_in_the_globs():
    """docs/ is the only surface CI gates; the bridge dashboard is named in the
    carve-out as the canonical implementation. Both were missing."""
    globs = _frontmatter_paths(RULE.read_text(encoding="utf-8"))
    joined = "\n".join(globs)
    assert "docs/" in joined, (
        "docs/** is the ONLY surface the CI ratchet gates and the entire "
        "content of .visual-baseline.json, and no glob matches it"
    )
    bridge = REPO / "scripts" / "bridge_daemon" / "web" / "index.html"
    assert bridge.exists(), f"{bridge} moved; update the glob"
    assert "bridge_daemon" in joined, (
        "the rule names the bridge dashboard as the canonical 31C dashboard "
        "implementation but no glob loads the rule when it is edited"
    )
