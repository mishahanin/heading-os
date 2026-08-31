"""Three structural invariants over skill documentation, from the 2026-08-29 audit.

Each of these is a real structural property of a document (a list agrees with the
count that announces it; a version claim does not outrun the declared version; a
TEMPLATE pins no value that expires). None of them is a grep for a known bug
string, which is the shape this workspace refuses: a source-text grep punishes a
file for documenting its own trap, and this tree has three files that legitimately
name a removed rule, a forbidden punctuation mark, and a superseded model.

What is deliberately NOT bound here, and why - measured, not assumed:

* **"a skill instructs the punctuation the rule forbids"** (`/email-intel` told the
  writer to `Use hyphens (--) not em dashes`). Six sibling skills legitimately
  write the same `(--)` token as the named-forbidden item
  (`evaluate/SKILL.md:174`, `playwright`, `create-plan`, `ast-grep`, `scrutinize`,
  `queue-draft`). The only difference is which side of the word "never" it sits
  on. A matcher narrow enough to catch the one and spare the six is a hardcoded
  match on one phrasing.
* **"a skill cites a rule that does not exist"** (`/x-pulse` cited an
  `always-show-full-paths rule`). The citation carried no path and no `.md`, and
  the shape `<slug> rule` matches 30+ innocent English phrases in this corpus
  ("wave-grouping rule", "walk-back rule", "ast-grep rule"). The precise variant -
  every `.claude/rules/*.md` PATH cited under `.claude/skills/` must exist - was
  built and then discarded: it goes red on
  `workspace-deep-audit/references/inventory-streams.md:156`, which correctly
  records that `.claude/rules/secure-projects.md` is GONE. Exactly the file the
  guard exists to protect.
* **"a skill attributes a rule to the wrong file"** (`/mullvad` credited the
  double-dash rule to `hidden-chars.md`; the canonical home is `voice.md`). Both
  files exist, so nothing mechanical separates a right citation from a wrong one.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

IMPLEMENT = ROOT / ".claude" / "skills" / "implement" / "SKILL.md"
IMPLEMENT_CHANGELOG = (ROOT / ".claude" / "skills" / "implement"
                       / "references" / "implement-details.md")
PITCH = ROOT / ".claude" / "skills" / "investor-pitch" / "SKILL.md"
AUDIT_TEMPLATE = (ROOT / ".claude" / "skills" / "workspace-deep-audit"
                  / "references" / "output-template.md")


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split("."))


# --------------------------------------------------------------------------
# Finding F - a skill cited a version of itself that did not exist.
# --------------------------------------------------------------------------

def test_implement_declares_a_version_no_older_than_the_features_it_cites():
    """`--verify checks all three since v1.8` sat in a file declaring v1.7.

    The feature was real (the v1.8 changelog entry describes it); the frontmatter
    had simply not been bumped. A reader checking the version to decide whether
    the behaviour is available got the wrong answer.
    """
    body = IMPLEMENT.read_text(encoding="utf-8")

    declared = re.search(r'^\s*version:\s*"([0-9]+(?:\.[0-9]+)+)"\s*$',
                         body, re.MULTILINE)
    assert declared, "implement/SKILL.md frontmatter has no `version:` field"
    declared_v = _version(declared.group(1))

    cited = re.findall(r"\bv([0-9]+\.[0-9]+)\b", body)
    assert cited, "the sweep found no version citations at all in the body"
    for c in cited:
        assert _version(c) <= declared_v, (
            f"implement/SKILL.md declares version {declared.group(1)} but cites "
            f"v{c} in its own body"
        )

    # The other direction: the declared version must exist in the changelog, so
    # a bump that invented a version would not pass either.
    changelog = IMPLEMENT_CHANGELOG.read_text(encoding="utf-8")
    entries = re.findall(r"^\*\*v([0-9]+\.[0-9]+)", changelog, re.MULTILINE)
    assert entries, "no changelog entries parsed out of implement-details.md"
    assert declared.group(1) in entries, (
        f"implement/SKILL.md declares v{declared.group(1)}, which has no entry in "
        f"references/implement-details.md (found {sorted(set(entries))})"
    )


# --------------------------------------------------------------------------
# Finding I - the slide list did not match the count announcing it.
# --------------------------------------------------------------------------

def _pitch_slides() -> list[int]:
    lines = PITCH.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines)
                 if l.startswith("## Phase 2: Slide-by-Slide"))
    end = next(i for i, l in enumerate(lines[start + 1:], start + 1)
               if l.startswith("## "))
    return [int(m.group(1)) for l in lines[start:end]
            if (m := re.match(r"^([0-9]+)\.\s+\*\*", l))]


def test_investor_pitch_slide_list_is_contiguous_and_matches_its_declared_count():
    """Two `10.` entries made a 15-item list read as the 14 the blurb promised."""
    slides = _pitch_slides()
    assert len(slides) >= 10, f"only {len(slides)} slides parsed; the walk is broken"
    assert slides == list(range(1, len(slides) + 1)), (
        f"investor-pitch Phase 2 numbering is not contiguous 1..N: {slides}"
    )

    declared = re.findall(r"\b([0-9]+)-slide\b", PITCH.read_text(encoding="utf-8"))
    assert declared, "investor-pitch declares no N-slide structure anywhere"
    for d in declared:
        assert int(d) == len(slides), (
            f"investor-pitch promises a {d}-slide structure but its Phase 2 list "
            f"has {len(slides)} entries"
        )


# --------------------------------------------------------------------------
# Finding K - a template hardcoded a model generation that had moved on.
# --------------------------------------------------------------------------

# Tier words alone ("Agent model: Haiku") are routing instructions and stay
# legal. What a template may not carry is a GENERATION - a number that dates.
GENERATION_PINNED = re.compile(
    r"claude-(?:opus|sonnet|haiku)-[0-9]"
    r"|(?:opus|sonnet|haiku)[ \-][0-9]",
    re.IGNORECASE,
)


def test_the_deep_audit_output_template_pins_no_model_generation():
    """It read `Claude Opus 4.7 (1M context)` while the CLI shipped Opus 5.

    A template is copied verbatim into every audit it produces, so a hardcoded
    generation does not go stale in one place - it is stamped onto each new
    report as a fact about who wrote it.
    """
    lines = AUDIT_TEMPLATE.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 50, f"template is {len(lines)} lines; the read is wrong"
    offenders = [f"{n}: {l.strip()}" for n, l in enumerate(lines, 1)
                 if GENERATION_PINNED.search(l)]
    assert not offenders, (
        "the audit output template hardcodes a model generation; make it a "
        "placeholder read at run time instead: " + "; ".join(offenders)
    )


def test_the_generation_matcher_would_catch_the_original():
    """Positive control: an empty regex would pass the test above forever."""
    for sample in ("**Автор:** Claude Opus 4.7 (1M context)",
                   "claude-opus-5", "Sonnet 4.5", "Haiku-3"):
        assert GENERATION_PINNED.search(sample), f"matcher missed {sample!r}"
    for sample in ("**Agent model:** Haiku (mechanical counting)",
                   "**Agent model:** Sonnet (judgment needed)",
                   "**Автор:** {the model that ran the audit}"):
        assert not GENERATION_PINNED.search(sample), (
            f"matcher punishes a bare tier word: {sample!r}"
        )
