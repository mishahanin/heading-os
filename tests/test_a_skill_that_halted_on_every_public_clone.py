#!/usr/bin/env python3
"""A skill gate that stopped dead on a file no public clone can have.

`/dream` Phase 1C read two security files and then said, verbatim:

    2. **If either file is not found:** STOP. Report the missing file and do not
       proceed to Phase 2. The dream is incomplete but safe.

One of the two was `docs/security/SECURITY-CONSTITUTION.md`. Measured 2026-09-02,
`get_routing_destination("docs/security/SECURITY-CONSTITUTION.md")` returns
`private`, the path does not exist in the engine tree, and `git ls-files
--error-unmatch` does not know it. So the file ships in the operator's data
overlay and in no public clone at all. Every adopter who typed `/dream` got a
gate that could never pass, on a skill whose whole job is safe to run without an
overlay: the four hard constraints in the same phase are the actual gate, and
they are engine text.

"Incomplete but safe" is what makes this worth a test rather than a one-line
edit. The sentence reads as a considered fail-closed decision, so a reviewer
skims past it. It is not one. It fails closed against a file whose absence is
the NORMAL state of the repository the skill ships in.

What this module holds:

  1. A mechanical sweep of every `.claude/skills/*/SKILL.md` and
     `.claude/rules/*.md` for the shape "absence of a thing" plus "therefore
     stop", where a path in scope resolves `private` through the real resolver
     and no continuation is stated. New skills inherit the guard for free.
  2. A positive control that runs the detector over the pre-fix text, so the
     detector cannot rot into a function that fires on nothing and passes.
  3. A floor: a run that inspected no skills fails instead of reporting clean.
  4. The specific fix, pinned, so the sweep and the repair cannot both be
     deleted in one edit and still look green.

Deliberately NOT held here: that a skill may reference a `private` path at all.
It may, and 300-odd blocks do. Reading `context/pipeline.md` when it is there and
saying nothing when it is not is correct behaviour and costs an adopter nothing.
The defect is the halt, not the reference.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.repo_files import read_sources, tracked_paths  # noqa: E402
from scripts.utils.workspace import get_routing_destination  # noqa: E402

# --- the shape ------------------------------------------------------------

# "the thing is not there"
ABSENCE = re.compile(
    r"not found|\babsent\b|not present|does not exist|do not exist|"
    r"missing|doesn'?t exist|no such file|unreadable|is not there",
    re.I,
)

# "...so stop"
HALT = re.compile(
    r"\bSTOP\b|do not proceed|must not proceed|cannot proceed|\babort\b|"
    r"\bhalts?\b|\bBLOCKED\b|do not continue",
    re.I,
)

# "...but here is what to do instead". A stated fallback is the fix, so a window
# that carries one is not a finding. This is the escape hatch by design: the
# repair for a finding is to write the fallback down, and the detector must
# recognise the repair.
CONTINUATION = re.compile(
    r"\bcontinue\b|\bskip\b|\bproceed without\b|\bdegrade|\bfall ?back|"
    r"\bomit\b|\bcarry on\b|\bno-?op\b|\bwarn\b",
    re.I,
)

# A negated fallback verb is not a fallback. Found by mutation, not by design:
# `/dream`'s NEVER list carried "NEVER skip the security gate - if protocol files
# are not found, stop and report", which is a halt, and the bare `\bskip\b` above
# read its own prohibition as the escape hatch. The window scored absence, halt,
# AND continuation, so the sweep passed over the second copy of the very defect
# it was written for.
_NEGATED = re.compile(r"(?:never|not|don'?t|cannot|can'?t|no)\s+(?:\w+\s+){0,2}$", re.I)

_TOKEN = re.compile(r"[A-Za-z0-9_./{}<>*+-]+")

# Workspace-relative path prefixes. A token that starts with one of these is a
# path the resolver can answer about; anything else (a bare filename, a URL, an
# env var) is not.
_PREFIXES = (
    "docs/", "scripts/", "config/", ".claude/", "reference/", "context/",
    "crm/", "knowledge/", "outputs/", "threads/", "plans/", "templates/",
    "datastore/", "admin/", "tests/", "deploy/", "auto-memory/", "chronicle/",
    "examples/", "_archive/", "personal/",
)

# Window height, in lines. An instruction and its consequence are written close
# together; four lines covers "read these files" / blank / "if not found, stop"
# without reaching into the next numbered step.
_WINDOW = 4

# Lookback, in lines, for a window that names no path itself. Bounded by the
# nearest markdown heading as well, so it never crosses a section.
#
# Paragraph-bounded would have missed the real defect: it put the paths in step
# 1 and the halt in step 2, with a blank line between them. Section-bounded
# alone over-reaches in the other direction, and did: at `--all` scope it
# attributed `/calibrate`'s "parser script missing, abort" to three private
# paths thirteen lines up its Phase 0 section, none of which the sentence is
# about. Eight lines covers "read these files" / blank / "if not found, stop"
# and stops short of the previous numbered step.
#
# Named limit: a halt whose subject path sits more than eight lines above it is
# invisible to this sweep. Widening it costs false attributions of exactly the
# `/calibrate` shape, which land in ACCEPTED and dull the guard.
_LOOKBACK = 8


# --- accepted, with the reason and the cost written out --------------------

# Key: (file, the private path the finding attributes). Value: why it stands.
# An entry here is a decision, not a silencer, so each one states what it costs
# to keep.
ACCEPTED: dict[tuple[str, str], str] = {
    (".claude/skills/modem-tune/SKILL.md", "config/modem.json"): (
        "ATTRIBUTION ARTIFACT, and a real limit of this detector. The halting "
        "sentence in that section is about a missing `MODEM_SSH_PASSWORD` "
        "credential in `.env`, not about `config/modem.json`. The credential is "
        "not a workspace path, so the window names no path, so the "
        "section-bounded lookback reaches up and finds the nearest one, which "
        "is the private config. The skill already degrades correctly two lines "
        "above: an unconfigured device exits cleanly on `generate`, `apply` and "
        "`revert`, and `detect` and `status` still run. "
        "COST: the detector cannot tell a halt on a credential from a halt on a "
        "private file when the two sit in one section, so every such "
        "co-location has to be read by a person once and landed here. That is "
        "the price of the section-bounded lookback, which is also the only "
        "reason the original `/dream` defect was visible at all."
    ),
}


# --- detector --------------------------------------------------------------


def _trim(raw: str) -> str:
    """Strip surrounding markdown and sentence punctuation off a token.

    Asymmetric on purpose. A symmetric `.strip(".,;:)('\\"`")` ate the leading dot
    of every `.claude/...` path, so `.claude/rules/voss.md` arrived as
    `claude/rules/voss.md`, matched no prefix, and vanished. The whole `.claude/`
    tree was invisible to this sweep, including `.claude/projects/` and
    `.claude/settings.local.json`, both of which route private. Found by
    mutation: two mutants that moved a `.claude/rules/` path into a checked
    position both survived, and neither test was wrong.
    """
    return raw.lstrip("('\"`").rstrip(".,;:)('\"`")


def _paths_in(text: str) -> set[str]:
    """Workspace-relative literal paths named in `text`.

    Templated and globbed tokens are dropped: `outputs/{slug}/x.md` names no
    file that can be present or absent, so it cannot be the subject of a halt.
    """
    out = set()
    for raw in _TOKEN.findall(text):
        tok = _trim(raw)
        if tok.startswith(_PREFIXES) and not any(c in tok for c in "{}<>*"):
            out.add(tok)
    return out


def _has_continuation(seg: str) -> bool:
    """True when `seg` states a fallback, ignoring negated fallback verbs."""
    for m in CONTINUATION.finditer(seg):
        if _NEGATED.search(seg[max(0, m.start() - 30):m.start()]):
            continue
        return True
    return False


def _is_accepted(rel: str, private: tuple[str, ...]) -> bool:
    """A finding is exempt only when EVERY private path it names is accepted.

    `all`, not `any`: a window naming one accepted path and one unreviewed path
    is an unreviewed finding, and `any` would swallow it whole.
    """
    return bool(private) and all((rel, p) in ACCEPTED for p in private)


def _section_start(lines: list[str], idx: int) -> int:
    """Index of the nearest markdown heading at or above `idx`, else 0."""
    for j in range(idx, -1, -1):
        if lines[j].startswith("#"):
            return j
    return 0


def corpus() -> list[Path]:
    """Every TRACKED skill body and every tracked rule, in a stable order.

    Through git, not through a bare glob. A bare `ROOT.glob` counts whatever is
    on disk, and this workspace runs agents in worktrees under `.claude/`, so a
    sweep that never asks git doubles its corpus the moment one is live and then
    reports findings against a copy nobody is editing.
    """
    return sorted(tracked_paths([".claude/skills/*/SKILL.md", ".claude/rules/*.md"],
                                ROOT))


def scan_text(rel: str, text: str) -> list[tuple[str, int, tuple[str, ...]]]:
    """Findings in one document: (file, 1-based line, private paths in scope).

    A finding is a window of up to `_WINDOW` lines that says a thing is absent,
    says to stop, states no continuation, and has at least one `private` path in
    scope. Overlapping windows collapse to one finding per (section, path set).
    """
    lines = text.splitlines()
    seen: dict[tuple[int, tuple[str, ...]], int] = {}
    for width in range(1, _WINDOW + 1):
        for i in range(len(lines) - width + 1):
            seg = "\n".join(lines[i:i + width])
            if not (ABSENCE.search(seg) and HALT.search(seg)):
                continue
            if _has_continuation(seg):
                continue
            start = _section_start(lines, i)
            paths = _paths_in(seg)
            if not paths:
                back = max(start, i - _LOOKBACK)
                paths = _paths_in("\n".join(lines[back:i + width]))
            private = tuple(
                sorted(p for p in paths if get_routing_destination(p) == "private")
            )
            if not private:
                continue
            key = (start, private)
            if key not in seen or i + 1 < seen[key]:
                seen[key] = i + 1
    return [(rel, line, private) for (_, private), line in sorted(seen.items())]


def scan_tree(apply_accepted: bool = True) -> list[tuple[str, int, tuple[str, ...]]]:
    """Findings across the whole corpus. `apply_accepted=False` returns them raw."""
    out = []
    # `read_sources`, not `read_text`: agents work against this one checkout, so
    # a skill or rule file can be written and removed between the walk above and
    # the read here, and `read_text` then raises FileNotFoundError from inside
    # the guard. This sweep hunts offenders, and a file that is gone cannot be
    # one, so skipping it narrows nothing that matters. The skip still warns by
    # name, so the narrowing is stated rather than swallowed.
    for path, text in read_sources(corpus()):
        rel = path.relative_to(ROOT).as_posix()
        for finding in scan_text(rel, text):
            if apply_accepted and _is_accepted(rel, finding[2]):
                continue
            out.append(finding)
    return out


# --- the floor -------------------------------------------------------------


def test_the_sweep_refuses_to_report_clean_over_an_empty_corpus():
    """A run that inspected nothing is a broken run, not a passing one.

    The whole guard is a glob over a directory layout. If that layout moves, the
    glob matches zero files and every assertion below passes for free, which is
    the exact failure this repository keeps writing down: a guard is green over
    an empty corpus.
    """
    files = corpus()
    skills = [p for p in files if "/skills/" in p.as_posix()]
    rules = [p for p in files if "/rules/" in p.as_posix()]
    assert skills, "no SKILL.md matched .claude/skills/*/SKILL.md; the sweep saw nothing"
    assert rules, "no rule matched .claude/rules/*.md; the sweep saw nothing"
    assert any(p.parent.name == "dream" for p in skills), (
        "the skill this module is named after is not in the corpus, so a green "
        "run proves nothing about the defect it guards"
    )


def test_the_detector_fires_on_the_text_it_was_written_against():
    """Positive control. A detector that can no longer fire is not a detector.

    This is the pre-fix Phase 1C, verbatim. It must produce exactly one finding
    naming the constitution. Without this, loosening ABSENCE or HALT, or
    widening CONTINUATION until it swallows everything, leaves the sweep green
    and silent.
    """
    pre_fix = (
        "### Phase 1C - Security Gate\n"
        "\n"
        "**This gate is mandatory. Do not proceed to Phase 2 without passing it.**\n"
        "\n"
        "1. Read the security rules:\n"
        "   - `.claude/rules/security.md`\n"
        "   - `docs/security/SECURITY-CONSTITUTION.md`\n"
        "\n"
        "2. **If either file is not found:** STOP. Report the missing file and "
        "do not proceed to Phase 2. The dream is incomplete but safe.\n"
    )
    findings = scan_text("fixture/PRE-FIX.md", pre_fix)
    assert len(findings) == 1, f"expected one finding, got {findings}"
    assert findings[0][2] == ("docs/security/SECURITY-CONSTITUTION.md",)


@pytest.mark.parametrize("phrase", [
    "is not found", "is absent", "absent", "is not present", "does not exist",
    "is missing", "doesn't exist", "is unreadable", "is not there",
])
def test_every_absence_phrase_fires_on_its_own(phrase):
    """Each alternative in ABSENCE must be load-bearing.

    Written after mutation: dropping `not found` from ABSENCE left the sweep
    green, because the single pre-fix fixture happened to also say "missing".
    A vocabulary tested only through one dense example is a vocabulary where
    any one term can rot away unseen.
    """
    doc = f"### Gate\n1. Read `context/pipeline.md`. If it {phrase}, STOP.\n"
    assert scan_text("fixture/ABSENCE.md", doc), f"ABSENCE misses {phrase!r}"


@pytest.mark.parametrize("phrase", [
    "STOP", "do not proceed", "must not proceed", "cannot proceed",
    "abort", "halt", "BLOCKED", "do not continue",
])
def test_every_halt_phrase_fires_on_its_own(phrase):
    """Same argument for HALT. Dropping `STOP` alone left the sweep green."""
    doc = f"### Gate\n1. Read `context/pipeline.md`. If it is not found, {phrase}.\n"
    assert scan_text("fixture/HALT.md", doc), f"HALT misses {phrase!r}"


def test_the_window_spans_more_than_one_line():
    """An absence on one line and its halt three lines down is one instruction.

    The pre-fix defect was exactly this shape and the sweep found it through the
    lookback instead, so shrinking the window to a single line changed nothing.
    Two redundant routes to one finding means neither route is under test.
    """
    doc = (
        "### Gate\n"
        "1. Read `context/pipeline.md`. If that file is not found\n"
        "   on this clone,\n"
        "   do not proceed.\n"
    )
    assert scan_text("fixture/WINDOW.md", doc), "the window no longer spans lines"


def test_the_lookback_reaches_a_path_the_window_cannot():
    """The subject of a halt is often several numbered steps above it.

    `/modem-tune` is the live instance and the only reason ACCEPTED has an
    entry. Nothing else exercised the lookback, so zeroing it passed.
    """
    doc = (
        "### Gate\n"
        "1. Read these:\n"
        "   - `context/pipeline.md`\n"
        "\n"
        "2. Note the focus area.\n"
        "\n"
        "3. If it is not found, STOP.\n"
    )
    findings = scan_text("fixture/LOOKBACK.md", doc)
    assert findings, "the lookback no longer reaches out of the window"
    assert findings[0][2] == ("context/pipeline.md",)


def test_a_dot_prefixed_path_survives_tokenising():
    """`.claude/...` is a path, not a sentence ending in a dot.

    A symmetric punctuation strip removed the leading dot and the whole tree
    dropped out of the sweep silently. Pinned as a unit, because the two
    mutations that exposed it both looked like test gaps at first.
    """
    assert _trim("`.claude/rules/voss.md`,") == ".claude/rules/voss.md"
    assert _paths_in("see `.claude/projects/` for it") == {".claude/projects/"}
    doc = "### Gate\n1. Read `.claude/settings.local.json`. If absent, abort.\n"
    assert scan_text("fixture/DOTTED.md", doc), "a .claude/ halt is invisible"


def test_a_templated_path_is_not_a_file_that_can_be_absent():
    """`outputs/{slug}/report.md` names no file, so it cannot be the subject.

    Counting templates would flag every skill that writes a dated artifact, and
    the sweep would drown in its own noise.
    """
    doc = "### Gate\n1. Read `outputs/{slug}/report.md`. If not found, STOP.\n"
    assert scan_text("fixture/TEMPLATED.md", doc) == []


def test_an_accepted_path_does_not_excuse_an_unreviewed_one():
    """The acceptance rule is `all`, and only a synthetic case can prove it.

    Every live finding names exactly one private path, so `any` and `all` agree
    on the whole corpus and the distinction is untested by the tree.
    """
    rel, path = next(iter(ACCEPTED))
    assert _is_accepted(rel, (path,))
    assert not _is_accepted(rel, (path, "crm/contacts/"))
    assert not _is_accepted(rel, ())
    assert not _is_accepted("some/other/file.md", (path,))


def test_every_accepted_entry_is_still_a_live_finding():
    """A stale exemption is a hole at a key nobody is looking at.

    It also keeps the registry honest: an entry that no longer corresponds to
    anything in the tree has to be deleted, not left as decoration.
    """
    raw = {(rel, p) for rel, _, paths in scan_tree(apply_accepted=False) for p in paths}
    stale = sorted(set(ACCEPTED) - raw)
    assert not stale, f"ACCEPTED entries that flag nothing any more: {stale}"


def test_a_negated_fallback_verb_is_not_a_fallback():
    """"NEVER skip the gate" is a prohibition, not a degradation path."""
    doc = (
        "### NEVER\n"
        "- NEVER skip the security gate - if `context/pipeline.md` is not found, "
        "stop and report\n"
    )
    assert scan_text("fixture/NEGATED.md", doc), (
        "a negated fallback verb is being read as a stated fallback"
    )


def test_a_stated_fallback_clears_the_finding():
    """Negative control: the repair must be recognised as a repair.

    Same shape as above, with the continuation written down. If this fires, the
    detector is unsatisfiable and every future author is stuck.
    """
    repaired = (
        "### Phase 1C - Security Gate\n"
        "\n"
        "1. Read `docs/security/SECURITY-CONSTITUTION.md`.\n"
        "\n"
        "2. If it is not found, continue without it and record that it was "
        "absent. Do not stop.\n"
    )
    assert scan_text("fixture/REPAIRED.md", repaired) == []


def test_the_accepted_registry_carries_a_reason_and_a_cost():
    """An exemption with no written price is a silencer."""
    for key, reason in ACCEPTED.items():
        assert "COST:" in reason, f"{key} states no cost"
        assert len(reason) > 200, f"{key} states no real reason"
    for rel, _ in ACCEPTED:
        assert (ROOT / rel).is_file(), (
            f"ACCEPTED names {rel}, which is not in the tree; a stale exemption "
            f"hides a real finding at the same key"
        )


# --- the guard -------------------------------------------------------------


def test_no_skill_or_rule_halts_on_a_path_that_routes_private():
    """The sweep. A public clone has no data overlay, so a gate that stops on a
    `private` file stops for every adopter, forever."""
    findings = scan_tree()
    assert not findings, "\n".join(
        f"{rel}:{line} halts on absence of {list(paths)} "
        f"(routes private, so absent in every public clone). "
        f"State what to do when it is missing, and continue."
        for rel, line, paths in findings
    )


# --- the specific repair ---------------------------------------------------


DREAM = ROOT / ".claude" / "skills" / "dream" / "SKILL.md"


def _phase_1c() -> str:
    """The Phase 1C section body of the dream skill.

    Scoped, not whole-file: the constitution path also appears in the skill's
    `## Paths` header, so a whole-file `in text` assertion passed even after the
    gate step that reads it had been rewritten away. Found by mutation.
    """
    lines = DREAM.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("### Phase 1C"))
    end = next(
        (i for i in range(start + 1, len(lines))
         if lines[i].startswith("### ") or lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_the_phase_1c_slice_is_a_slice():
    """The scoping must actually exclude the rest of the file.

    Without this the narrowing is untestable on its own: it only changes an
    outcome in combination with a second edit that moves the reference, so a
    mutation returning the whole file passed.
    """
    gate = _phase_1c()
    whole = DREAM.read_text(encoding="utf-8")
    assert gate in whole and len(gate) < len(whole) / 2
    assert "## Paths" not in gate, "the slice reaches the header section"
    assert "Phase 2 - Consolidate" not in gate, "the slice runs past its section"


def test_the_dream_security_gate_survives_a_missing_constitution():
    """Pin the fix itself, in case the sweep is deleted alongside it."""
    gate = _phase_1c()
    assert "docs/security/SECURITY-CONSTITUTION.md" in gate, (
        "Phase 1C no longer reads the constitution by name; the reference was "
        "deleted rather than degraded, and the operator's own clone should "
        "still read it when the overlay has one"
    )
    assert "If either file is not found:** STOP" not in gate, (
        "the original blocking sentence is back"
    )
    assert "rules only, constitution absent" in gate, (
        "Phase 1C no longer names the outcome it records when the overlay is "
        "absent, so the gate has no stated pass on a public clone"
    )


def test_the_constitution_is_still_the_private_overlay_file_this_assumes():
    """The fix rests on one measured fact. If the fact moves, revisit the fix.

    Were the constitution ever routed `engine` and committed, degrading would be
    the wrong answer and the halt would be right again.
    """
    dest = get_routing_destination("docs/security/SECURITY-CONSTITUTION.md")
    assert dest == "private", (
        f"the constitution now routes {dest!r}; this module's whole premise was "
        f"that it routes private and therefore cannot be in a public clone"
    )


# --- the same defect in a rule, not a skill --------------------------------
#
# `.claude/rules/corporate-docs.md` told five skills to load six files "before
# drafting". Measured 2026-09-02: four of the six are `.claude/rules/` files with
# no `paths:` frontmatter, which in this workspace means always-on, so no skill
# can fail to load them and the duty is unfalsifiable. Of the two that are real
# on-demand reads, one routes `private` and is absent from the engine tree, with
# no fallback stated anywhere. Same shape as `/dream`, different surface: an
# always-on rule in a public repository naming an overlay-only file as a hard
# prerequisite.

# The brand-enforcement section moved on 2026-09-04, in the context diet that cut
# the always-on rule set roughly in half. It fires only once a doctype has already
# been chosen, so it went to the PATH-SCOPED sibling
# `corporate-docs-authoring.md`, which loads when a skill opens
# `reference/corporate-style-guide.md` -- the read the always-on guardrail orders
# it to make. The section is unchanged; only the file holding it moved, so this
# test follows it rather than being relaxed.
#
# Asserted, not assumed: the sibling must exist and must be the path-scoped one.
# Pointing this constant at a file that had quietly become always-on again would
# make the whole section resident while this test still passed.
CORPORATE_DOCS = ROOT / ".claude" / "rules" / "corporate-docs-authoring.md"


def test_the_brand_section_lives_in_the_path_scoped_sibling():
    """The section is on-demand, and the guardrail still points at its trigger.

    Both halves matter. If `corporate-docs-authoring.md` lost its `paths:` block
    it would be always-on again and the diet would have silently reverted; if the
    always-on guardrail stopped ordering the style-guide read, nothing would load
    the sibling and the brand obligations would go unenforced.
    """
    assert CORPORATE_DOCS.is_file(), f"missing: {CORPORATE_DOCS}"
    scoped = CORPORATE_DOCS.read_text(encoding="utf-8")
    assert scoped.startswith("---\n"), "the sibling must open with frontmatter"
    front = scoped.split("---", 2)[1]
    assert "paths:" in front, "the sibling must be path-scoped, not always-on"
    assert "reference/corporate-style-guide.md" in front, (
        "the style guide must be one of the globs; it is the read that loads this rule")

    guardrail = (ROOT / ".claude" / "rules" / "corporate-docs.md").read_text(encoding="utf-8")
    assert "paths:" not in guardrail.split("\n## ", 1)[0], (
        "the trigger guardrail must stay always-on: it fires on a message, not a path")
    assert "reference/corporate-style-guide.md" in guardrail, (
        "the always-on guardrail must still order the read that loads the sibling")
    assert "corporate-docs-authoring.md" in guardrail, (
        "the always-on guardrail must name where the rest of the obligations went")

DOCTYPE_SKILLS = (
    "corporate-letter", "proposal", "partnership-doc", "official-doc", "xpager",
)

ALWAYS_ON = (
    ".claude/rules/terminology.md",
    ".claude/rules/voice.md",
    ".claude/rules/voss.md",
    ".claude/rules/hidden-chars.md",
)


def _on_demand_items() -> list[str]:
    """The numbered items under "Read before drafting" in the brand section.

    Parsed rather than hardcoded, so adding a seventh reference to the rule
    brings it under these checks without anyone remembering to come here.
    """
    lines = CORPORATE_DOCS.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if "**Read before drafting.**" in ln)
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("#")),
        len(lines),
    )
    items, cur = [], None
    for ln in lines[start + 1:end]:
        if re.match(r"^\d+\. ", ln):
            if cur:
                items.append("\n".join(cur))
            cur = [ln]
        elif cur is not None:
            if ln.strip() == "" and cur:
                items.append("\n".join(cur))
                cur = None
            else:
                cur.append(ln)
    if cur:
        items.append("\n".join(cur))
    return items


def _item_subject(item: str) -> str | None:
    """The path a numbered item REQUIRES, which is the first one it names.

    A later path in the same item is the fallback, not the requirement. Item 2
    names `.claude/rules/voice.md` as what to fall back to when the private
    voice file is absent, and a check that looked at every mention read that
    fallback as a fifth obligation.
    """
    for raw in _TOKEN.findall(item):
        tok = _trim(raw)
        if tok.startswith(_PREFIXES) and not any(c in tok for c in "{}<>*"):
            return tok
    return None


def _always_on_paragraph() -> str:
    """The prose between the two bold markers in the brand section."""
    text = CORPORATE_DOCS.read_text(encoding="utf-8")
    start = text.index("**Already in context; nothing to load.**")
    return text[start:text.index("**Read before drafting.**", start)]


def test_the_always_on_paragraph_names_every_rule_it_speaks_for():
    """A rule dropped from the paragraph is a rule the reader stops hearing about.

    The paragraph is the only place the brand section still accounts for these
    four, now that they are out of the numbered list. Losing one silently would
    undo half the repair: the reader would be left with two accounted-for files
    and four that no longer appear anywhere.
    """
    para = _always_on_paragraph()
    assert "**Read before drafting.**" not in para, (
        "the slice runs past its paragraph into the on-demand list, where "
        "`.claude/rules/voice.md` is named as a fallback; a paragraph that "
        "swallows the list would find these names there and pass for free"
    )
    missing = [rel for rel in ALWAYS_ON if rel not in para]
    assert not missing, f"the always-on paragraph no longer names {missing}"


def test_the_always_on_rules_really_are_always_on():
    """The premise. A `paths:` block would make one of them path-scoped, and
    then it WOULD be a per-skill duty and belongs back in the numbered list."""
    for rel in ALWAYS_ON:
        path = ROOT / rel
        assert path.is_file(), f"{rel} is gone; the brand section names it"
        head = path.read_text(encoding="utf-8").lstrip()
        assert not head.startswith("---"), (
            f"{rel} now carries frontmatter, so it may be path-scoped rather "
            f"than always-on; re-decide whether it is a per-skill obligation"
        )


def test_an_always_on_rule_is_not_listed_as_an_on_demand_read():
    """An obligation that cannot be broken is not an obligation.

    Four of the six original entries could never be violated, which made the
    other two look enforced when nothing enforced them either.
    """
    subjects = [_item_subject(item) for item in _on_demand_items()]
    listed = [rel for rel in ALWAYS_ON if rel in subjects]
    assert not listed, (
        f"{listed} are always-on and load themselves; requiring them as a read "
        f"before drafting is unfalsifiable"
    )


def test_the_on_demand_list_is_not_empty():
    """Floor. A parser that finds no items would pass the two checks below."""
    items = _on_demand_items()
    assert len(items) >= 2, f"parsed {len(items)} on-demand items from the rule"


def test_a_private_on_demand_reference_states_a_fallback():
    """`reference/misha-voice.md` routes private and no public clone has it.

    The five doctype skills are engine content and ship public, so the rule that
    drives them must say what to do without it.
    """
    for item in _on_demand_items():
        subject = _item_subject(item)
        if subject is None or get_routing_destination(subject) != "private":
            continue
        assert _has_continuation(item), (
            f"the brand section requires {subject} before drafting and states no "
            f"fallback; it routes private and is absent in every public clone"
        )


def test_every_doctype_skill_names_the_engine_reference_it_must_read():
    """The one real, checkable obligation in that section.

    Derived from the rule rather than hardcoded: whatever engine reference the
    rule lists as an on-demand read, all five skills must name.
    """
    required = sorted({
        s for s in (_item_subject(item) for item in _on_demand_items())
        if s and get_routing_destination(s) == "engine" and (ROOT / s).is_file()
    })
    assert required, "the brand section lists no engine reference at all"
    for slug in DOCTYPE_SKILLS:
        skill = ROOT / ".claude" / "skills" / slug / "SKILL.md"
        assert skill.is_file(), f"{slug} is not in the tree"
        text = skill.read_text(encoding="utf-8")
        missing = [p for p in required if p not in text]
        assert not missing, f"/{slug} never names {missing}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
