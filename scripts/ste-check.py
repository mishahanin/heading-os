#!/usr/bin/env python3
"""
ste-check.py - Mechanical audit for the HEADING OS documentation style.

Implements the checkable part of .claude/rules/documentation-style.md, an
ASD-STE100 Part 1 subset applied to the engine's procedural documentation.
Companion to scripts/humanization-check.py, which audits the opposite surface:
that script checks prose written to be read, this one checks text written to be
executed. Neither is a gate.

Part 2 of ASD-STE100 (the ~900-word approved dictionary) is NOT implemented and
must not be claimed - see the rule file for why.

Usage:
  python scripts/ste-check.py <file>              # Audit one file
  python scripts/ste-check.py --all               # Audit the 14 gated pages
  python scripts/ste-check.py --skills --quiet    # Audit skill bodies (ungated)
  python scripts/ste-check.py --strict <file>     # Fail on warnings too
  python scripts/ste-check.py --json <file>       # JSON output for CI
  python scripts/ste-check.py --text "string"     # Inline text audit
  python scripts/ste-check.py --all --quiet       # Gate form: errors only

Checks performed:
  1. sentence_too_long   - >20 words in a numbered step, >25 in prose (rule 3)
  2. multi_action_step   - two actions joined in one step (rule 4)
  3. and_or              - the "and/or" construction (rule 9)
  4. banned_phrase       - padding and minimising words (rule 10)
  5. ing_opener          - a gerund opening a sentence or step (rule 7)
  6. warning_at_end      - a warning callout closing a procedure (rule 8)
  7. non_imperative_step - a numbered step opening as a description (rule 1)
  8. passive_voice       - heuristic, no POS tagger behind it (rule 2)
  9. weak_modal          - "you should" where an instruction belongs (rule 1)

Exit codes:
  0 - clean (or strict-mode pass)
  1 - findings present (errors or, in strict mode, warnings)
  2 - script error
"""

import sys
import re
import json
import argparse
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from scripts.utils.colors import GREEN, YELLOW, RED, GRAY, BOLD, RESET
except ImportError:
    GREEN = YELLOW = RED = GRAY = BOLD = RESET = ""

from scripts.utils.workspace import get_workspace_root
from scripts.utils.sanitize_text import word_count


# ============================================================
# Configuration
# ============================================================

# The mechanically checked subset of the rule's `paths:` frontmatter. Narrower
# than the rule on purpose: the rule also governs SKILL.md instruction bodies and
# CLI help strings, where prose and machine text interleave too closely for a
# line-based checker to stay honest. tests/test_ste_check.py asserts every glob
# here is covered by the rule, so this list can never widen past it.
CHECKED_GLOBS = [
    "docs/QUICKSTART.md",
    "docs/DEPLOYMENT.md",
    "docs/INTEGRATIONS-SETUP.md",
    "docs/MODELS-SETUP.md",
    "docs/CONFIGURATION.md",
    "docs/TROUBLESHOOTING.md",
    "docs/EMERGENCY-PROCEDURES.md",
    "docs/MAKE-IT-YOURS.md",
    "docs/PLUGINS.md",
    "docs/HOOKS-REFERENCE.md",
    "docs/DOCS-PIPELINE.md",
    "docs/GLOSSARY.md",
    # Added 2026-08-22. Both are pages a reader executes -- TELEGRAM-AND-ALERTS
    # says so in its own opening ("written for someone who has never touched an
    # API"), EXTENDING is the developer how-to and carries the commands you type.
    # They sat in neither this list nor the rule's explicit exclusion paragraph,
    # so nothing had ever decided about them; `test_every_docs_page_is_classified`
    # now makes that state unreachable.
    "docs/EXTENDING.md",
    "docs/TELEGRAM-AND-ALERTS.md",
]

STEP_WORD_LIMIT = 20      # ASD-STE100 procedural sentence limit
PROSE_WORD_LIMIT = 25     # ASD-STE100 descriptive sentence limit

# Padding and minimising vocabulary -> the shorter form that carries the same
# information. Errors: pure verbosity, always safe to replace. Warnings: words
# that occasionally carry meaning, so a human decides.
BANNED_PHRASES_ERROR = {
    "in order to": "to",
    "prior to": "before",
    "subsequent to": "after",
    "in the event that": "if",
    "at this point in time": "now",
    "a number of": "some, or the exact number",
    "utilize": "use",
    "utilise": "use",
    "commence": "start",
    "in the case where": "if",
    "with the exception of": "except",
    "is able to": "can",
    "are able to": "can",
}

BANNED_PHRASES_WARNING = {
    "simply": "delete the word",
    "just": "delete the word",
    "easily": "delete the word",
    "obviously": "delete the word",
    "of course": "delete the phrase",
    "etc.": "name the remaining items",
    "please": "delete it; a step is an instruction",
}

# Gerunds that are ordinary nouns rather than verb forms.
ING_ALLOWLIST = {
    "nothing", "something", "anything", "everything", "thing", "things",
    "string", "strings", "during", "heading", "warning", "meaning",
    "engineering", "onboarding", "setting", "settings", "morning",
}

# A numbered step that opens this way is describing, not instructing.
NON_IMPERATIVE_OPENERS = (
    "the ", "this ", "that ", "there ", "these ", "those ",
    "it is ", "it will ", "it should ",
    "you can ", "you will ", "you may ", "we ",
)

WEAK_MODAL_RE = re.compile(
    r"\b(you should|it is recommended|it is advisable|it is suggested)\b",
    re.IGNORECASE,
)

# be-verb + past participle. No POS tagger behind it, so this is a warning.
PASSIVE_RE = re.compile(
    r"\b(is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?"
    r"(\w+ed|written|built|done|made|given|taken|shown|known|held|kept|sent|"
    r"put|found|left|read|run|set|lost|drawn|thrown|torn|shut)\b",
    re.IGNORECASE,
)

WARNING_CALLOUT_RE = re.compile(
    r"^\s*[>*_\-\s]*(\*{0,2})(warning|caution|danger|note)\b",
    re.IGNORECASE,
)

NUMBERED_ITEM_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
BULLET_ITEM_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
HEADING_RE = re.compile(r"^\s*#{1,6}\s")

# Markdown emphasis sits on BOTH sides of a sentence boundary in this corpus, and
# the original pattern saw neither side:
#
#   ... rather than two. **You decide.** No code reads them.
#                       ^ opener: the lookahead wanted a capital or a bracket
#                                    ^ closer: the lookbehind wanted the terminator
#                                      immediately before the space, not `.**`
#
# So one boundary in every bolded lead-in silently merged two sentences, and the
# joined pair then measured over the word limit. It reported 51 errors across the
# skill corpus against prose that was already correct.
#
# The closer is handled by a fixed-width lookbehind per shape rather than by
# consuming the markers: `re.split` drops what it matches, and consuming them
# would strip the emphasis out of the text the checker then reports back.
# `(?<![A-Z0-9])` keeps the abbreviation guard, so `SKILL.md file`, `v1.2 of` and
# an enumerated `1. ` stay unsplit.
#
# Emphasis was only the first closer found. A quote and a bracket sit in exactly
# the same place -- `... both work." If two variants ...`, `(see below.) Next` --
# so CLOSER is a character class, not a third enumerated shape. Enumerating cost
# two rounds of this bug; a class covers the next closer somebody writes.
# Inline code is not prose, but deleting it outright cost the fourth splitter
# defect of this same family, found 2026-08-22. A sentence that OPENS with a code
# span -- "`git diff` answers whether the contract moved." -- became " answers
# whether ..." once the span went, so the split lookahead saw a lowercase letter
# and merged the sentence into the one above it. Three correct sentences on
# docs/EXTENDING.md measured as one 26-word run.
#
# So the span leaves a MARK rather than a hole, and the mark is one word. Deleting
# it also under-counted every sentence that carries code, which is the wrong
# direction: the denser the code, the larger the discount, so the hardest
# sentences in the corpus got the biggest pass. One page read 21 words to this
# checker and 27 to a person, and reported clean.
#
# The mark counts ONE word per span, never the words inside it. `--base REF` and
# `scripts/crm-health.py` are each one thing the eye lands on, and counting their
# interior would penalise naming the exact flag or path -- pressure in the wrong
# direction for reference documentation. Measured on the fourteen gated pages
# 2026-08-22: zero-word (old) 0 errors, one-word (this) 15, interior words 32.
#
# "Code" satisfies both jobs at once: `[A-Z(\[\"'`*_]` accepts it as a sentence
# opener so the split fires, `\b[\w'-]+\b` counts it once, and it collides with
# no banned phrase, bullet character, CLOSER member or non-imperative opener.
CODE_MARK = "Code"

CLOSER = r"[)\]\"'’”*_]"
SENTENCE_SPLIT_RE = re.compile(
    r"(?<![A-Z0-9])"
    rf"(?:(?<=[.!?])|(?<=[.!?]{CLOSER})|(?<=[.!?]{CLOSER}{CLOSER}))"
    r"\s+(?=[A-Z(\[\"'`*_/])"
)
# `/` is in the opener class because this corpus starts sentences with a slash
# command more often than most prose does: "... typical transients. /calibrate's
# auto-commit will include them." merged into one 22-word sentence and had to be
# reworded around the tool. The leading `(?<![A-Z0-9])` guard already stops a
# version number or an abbreviation from splitting before a path.


# ============================================================
# Text preparation
# ============================================================

# Marks a line a removed block emptied, so `parse_units` can tell it from a
# blank line the author wrote. Never reaches a report: the mark is stripped
# before any unit text is built. Carries no backtick, bracket, paren, angle
# bracket or leading `>`, so the preparation passes that run after the blanking
# cannot consume it.
BLANKED_LINE = "@ste-blanked@"


def _blank_out(match):
    """Replace a removed block with its own newlines, so line numbers survive.

    Every finding carries a line number the reader is expected to open the file
    at. `parse_units` numbers the lines of the PREPARED text, so a block deleted
    outright here silently shifts every finding below it by the block's height.

    Measured 2026-08-19 across the 96 skill bodies: 156 of 431 findings (36%)
    reported a line at or above the file's own frontmatter, which reads as the
    checker auditing YAML that `.claude/rules/documentation-style.md` scopes out.
    It was not - the frontmatter was already stripped on the line below. The
    findings were real body prose, reported one frontmatter-height too high.
    `.claude/skills/corporate-letter/SKILL.md` said line 21 and meant line 72,
    with its frontmatter closing on line 51.

    Frontmatter was the largest of these blocks, not the only one: a code fence
    or a multi-line HTML comment moves everything after it by the same
    mechanism. So the fix is height-preserving substitution for every block that
    spans lines, not an offset added at one call site - an offset is one more
    number to keep in step with the stripping, and the copy that stops being
    updated is the one somebody reads a line number from.

    A block opened MID-LINE needs one thing more than height. Plain blanking
    leaves the lines it covered EMPTY, and an empty line ends a paragraph, so an
    aside opened mid-sentence cut its own paragraph in two and the sentence
    running through it was measured in halves. Reproduced 2026-08-20: a 26-word
    sentence carrying a three-line aside measured 11 words and 15 words, and the
    prose limit of 25 reported nothing. So the lines a mid-line block covers are
    MARKED rather than left empty, and `parse_units` reads a marked line as
    interior to the paragraph. A block that opens a line keeps the plain
    blanking: the empty lines it leaves are what separated the paragraphs around
    it, exactly as the author's own line breaks did.
    """
    newlines = match.group(0).count("\n")
    line_start = match.string.rfind("\n", 0, match.start()) + 1
    opens_the_line = not match.string[line_start:match.start()].strip()
    if newlines == 0 or opens_the_line:
        return "\n" * newlines

    # One mark per covered line: the interior lines, plus the closing line,
    # whose own remainder lands right after the mark and is read as prose.
    return "\n" + f"{BLANKED_LINE}\n" * (newlines - 1) + BLANKED_LINE


def strip_noise(text):
    """Remove everything a style checker must not read as prose.

    Code fences and inline code go first: a shell command is not a sentence and
    would blow every word limit in the file.

    Blocks that span lines are blanked to their own height rather than deleted -
    see `_blank_out` for what that costs when they are not.
    """
    # CRLF first, or the frontmatter pattern below (which needs bare \n) misses
    # on a Windows-authored file and the YAML block gets style-checked as prose:
    # false findings on `description:` and `name:`, with every later line number
    # shifted. This repo advertises Windows support and gates docs on this file.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(
        r"<!--\s*ste-skip-start\s*-->[\s\S]*?<!--\s*ste-skip-end\s*-->",
        _blank_out, text, flags=re.IGNORECASE,
    )
    text = re.sub(r"^---\n.*?\n---\n", _blank_out, text, count=1, flags=re.DOTALL)
    text = re.sub(r"```[\s\S]*?```", _blank_out, text)
    text = re.sub(r"<!--[\s\S]*?-->", _blank_out, text)
    text = re.sub(r"`[^`\n]+`", f" {CODE_MARK} ", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    # A blockquote marker is structure, like a list bullet. Left in place it
    # lands between a period and the next capital, where the sentence splitter's
    # lookahead does not accept it, so every sentence ending at a wrapped
    # blockquote line merged with the one below and measured over the limit.
    text = re.sub(r"(?m)^[ \t]*>+[ \t]?", "", text)
    return text


# `word_count` is imported from `scripts.utils.sanitize_text`. It used to be a
# private copy here, `len(re.findall(r"\b[\w'-]+\b", text))`, and that regex
# reads `.` `,` `/` `=` `>` `{` `}` as word boundaries, so one token a reader
# parses as one thing inflated into several. MEASURED 2026-08-30 over the 108
# gated files (14 pages plus 94 skill bodies), 8644 sentences: 573 of them
# counted higher under the private regex, by 1 to 7 words. The worst was
# `.venv/bin/python scripts/canopus.py tests/contract/{date}-{slug}/`, seven
# words over. `e.g.,` scored 2, `1.0.0` scored 3, `outputs/.../{version}.md`
# scored 7.
#
# This is a THRESHOLD input: `check_sentence_length` compares it against
# STEP_WORD_LIMIT and PROSE_WORD_LIMIT, and the result gates CI and pre-commit.
# The shared counter is never HIGHER than the old one (every whitespace token
# holding an alnum yields at least one `[\w'-]+` match), so the swap can only
# LOOSEN this gate, never tighten it. On the corpus as it stands, no verdict
# moves: 0 errors before, 0 errors after, and zero sentences change side of
# their limit. What did change is the margin. Eight sentences sat exactly ON
# their limit and now sit 1 to 2 words under it, and a path-dense sentence can
# now carry up to 7 more counted words before the limit refuses it.
#
# That direction is the intended one. The 20/25 limits are ASD-STE100 reader-load
# limits, and a file path is one thing a reader parses, not seven words.


def split_sentences(text):
    return [s.strip() for s in SENTENCE_SPLIT_RE.split(text) if s.strip()]


def parse_units(text):
    """Segment a document into checkable units.

    A unit is a numbered step ("step") or a paragraph / bullet ("prose"). The
    distinction sets the word limit and decides whether the imperative checks
    run at all: bulleted lists in this engine's docs are overwhelmingly feature
    lists, not procedures, so running imperative checks on them is noise.
    """
    units = []
    para_lines, para_start = [], 0
    open_item = None

    def flush():
        nonlocal para_lines, para_start
        if para_lines:
            units.append({
                "kind": "prose",
                "text": " ".join(para_lines).strip(),
                "line": para_start,
            })
            para_lines = []

    for idx, raw in enumerate(text.splitlines(), start=1):
        blanked = raw.startswith(BLANKED_LINE)
        if blanked:
            raw = raw[len(BLANKED_LINE):]
        line = raw.rstrip()
        stripped = line.strip()

        # A line a mid-line block emptied ends no paragraph - the author wrote
        # prose straight through it. Every other empty line still ends one.
        if blanked and not stripped:
            continue

        if not stripped or HEADING_RE.match(line) or stripped.startswith("|"):
            flush()
            open_item = None
            continue

        m = NUMBERED_ITEM_RE.match(line)
        if m:
            flush()
            open_item = {"kind": "step", "text": m.group(1).strip(), "line": idx}
            units.append(open_item)
            continue

        m = BULLET_ITEM_RE.match(line)
        if m:
            flush()
            open_item = {"kind": "prose", "text": m.group(1).strip(), "line": idx}
            units.append(open_item)
            continue

        # A wrapped continuation belongs to the item above it, the way markdown
        # reads it. It used to start a paragraph of its own, so a step's sentence
        # was measured in halves and never against the 20-word step limit:
        # identical prose passed or failed purely on whether the author hard
        # wrapped it, and the gate then called the file clean.
        if open_item is not None and not para_lines:
            open_item["text"] = f"{open_item['text']} {stripped}".strip()
            continue

        if not para_lines:
            para_start = idx
        para_lines.append(stripped)

    flush()
    return units


# ============================================================
# Individual checks
# ============================================================

def check_sentence_length(units):
    findings = []
    for unit in units:
        limit = STEP_WORD_LIMIT if unit["kind"] == "step" else PROSE_WORD_LIMIT
        for sentence in split_sentences(unit["text"]):
            n = word_count(sentence)
            if n > limit:
                findings.append({
                    "type": "sentence_too_long",
                    "severity": "error",
                    "line": unit["line"],
                    "description": f"{n} words in a {unit['kind']} sentence (limit {limit})",
                    "context": _snippet(sentence),
                })
    return findings


def check_multi_action(units):
    findings = []
    pattern = re.compile(r"\b(and then|,\s*then)\b", re.IGNORECASE)
    for unit in units:
        if unit["kind"] != "step":
            continue
        m = pattern.search(unit["text"])
        if m:
            findings.append({
                "type": "multi_action_step",
                "severity": "error",
                "line": unit["line"],
                "description": f"'{m.group(1).strip()}' joins two actions - split into two steps",
                "context": _snippet(unit["text"]),
            })
    return findings


def check_and_or(units):
    findings = []
    for unit in units:
        for m in re.finditer(r"\band\s*/\s*or\b", unit["text"], re.IGNORECASE):
            findings.append({
                "type": "and_or",
                "severity": "error",
                "line": unit["line"],
                "description": "'and/or' is ambiguous - write 'A, B, or both'",
                "context": _snippet(unit["text"], m.start()),
            })
    return findings


def check_banned_phrases(units):
    findings = []
    for unit in units:
        for table, severity in ((BANNED_PHRASES_ERROR, "error"),
                                (BANNED_PHRASES_WARNING, "warning")):
            for phrase, fix in table.items():
                lead = r"\b" if phrase[0].isalnum() else ""
                tail = r"\b" if phrase[-1].isalnum() else ""
                pattern = re.compile(lead + re.escape(phrase) + tail, re.IGNORECASE)
                for m in pattern.finditer(unit["text"]):
                    findings.append({
                        "type": "banned_phrase",
                        "severity": severity,
                        "line": unit["line"],
                        "description": f"'{phrase}' -> {fix}",
                        "context": _snippet(unit["text"], m.start()),
                    })
    return findings


def check_ing_opener(units):
    findings = []
    for unit in units:
        for sentence in split_sentences(unit["text"]):
            m = re.match(r"([A-Za-z]+ing)\b", sentence)
            if not m or m.group(1).lower() in ING_ALLOWLIST:
                continue
            findings.append({
                "type": "ing_opener",
                "severity": "warning",
                "line": unit["line"],
                "description": f"'{m.group(1)}' opens with an -ing form - use the plain verb",
                "context": _snippet(sentence),
            })
    return findings


def check_non_imperative_step(units):
    findings = []
    for unit in units:
        if unit["kind"] != "step":
            continue
        low = unit["text"].lower()
        for opener in NON_IMPERATIVE_OPENERS:
            if low.startswith(opener):
                findings.append({
                    "type": "non_imperative_step",
                    "severity": "warning",
                    "line": unit["line"],
                    "description": f"step opens '{opener.strip()}' - give the reader a verb",
                    "context": _snippet(unit["text"]),
                })
                break
    return findings


def check_weak_modal(units):
    findings = []
    for unit in units:
        for m in WEAK_MODAL_RE.finditer(unit["text"]):
            findings.append({
                "type": "weak_modal",
                "severity": "warning",
                "line": unit["line"],
                "description": f"'{m.group(1)}' - state the instruction or the fact",
                "context": _snippet(unit["text"], m.start()),
            })
    return findings


def check_passive_voice(units):
    findings = []
    for unit in units:
        for m in PASSIVE_RE.finditer(unit["text"]):
            findings.append({
                "type": "passive_voice",
                "severity": "warning",
                "line": unit["line"],
                "description": f"'{m.group(0)}' reads passive - name the actor",
                "context": _snippet(unit["text"], m.start()),
            })
    return findings


def check_warning_at_end(text):
    """Flag a warning callout that closes a numbered procedure.

    ASD-STE100 puts a warning before the step it guards. A callout that is the
    last line of the procedure guards nothing that follows it.
    """
    findings = []
    lines = text.splitlines()
    block, block_has_step = [], False

    def close(block, has_step):
        if not has_step or not block:
            return None
        last_line_no, last_text = block[-1]
        if WARNING_CALLOUT_RE.match(last_text) and not NUMBERED_ITEM_RE.match(last_text):
            return {
                "type": "warning_at_end",
                "severity": "error",
                "line": last_line_no,
                "description": "warning closes the procedure - move it before the step it guards",
                "context": _snippet(last_text.strip()),
            }
        return None

    for idx, raw in enumerate(lines, start=1):
        # This check reads the same prepared text, where a mid-line block leaves
        # a mark instead of an empty line. Drop the mark so the line reads
        # exactly as plain blanking left it, and rule 8 decides on the prose.
        if raw.startswith(BLANKED_LINE):
            raw = raw[len(BLANKED_LINE):]
        stripped = raw.strip()
        if HEADING_RE.match(raw):
            found = close(block, block_has_step)
            if found:
                findings.append(found)
            block, block_has_step = [], False
            continue
        if not stripped:
            continue
        if NUMBERED_ITEM_RE.match(raw):
            block.append((idx, raw))
            block_has_step = True
            continue
        if block_has_step and (raw.startswith((" ", "\t")) or WARNING_CALLOUT_RE.match(raw)):
            block.append((idx, raw))
            continue
        found = close(block, block_has_step)
        if found:
            findings.append(found)
        block, block_has_step = [], False

    found = close(block, block_has_step)
    if found:
        findings.append(found)
    return findings


# ============================================================
# Helpers
# ============================================================

def _snippet(text, start=0, width=90):
    s = max(0, start - 15)
    out = text[s:s + width].replace("\n", " ")
    return ("..." if s > 0 else "") + out + ("..." if len(text) > s + width else "")


# ============================================================
# Aggregation and reporting
# ============================================================

def audit(text, strict=False):
    clean = strip_noise(text)
    units = parse_units(clean)

    findings = []
    findings += check_sentence_length(units)
    findings += check_multi_action(units)
    findings += check_and_or(units)
    findings += check_banned_phrases(units)
    findings += check_ing_opener(units)
    findings += check_non_imperative_step(units)
    findings += check_weak_modal(units)
    findings += check_passive_voice(units)
    findings += check_warning_at_end(clean)

    findings.sort(key=lambda f: (f.get("line", 0), f["type"]))

    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]

    return {
        "findings": findings,
        "summary": {
            "total_findings": len(findings),
            "errors": len(errors),
            "warnings": len(warnings),
            "steps": sum(1 for u in units if u["kind"] == "step"),
            "units": len(units),
            "by_type": dict(Counter(f["type"] for f in findings)),
        },
        "passed": not errors and (not strict or not warnings),
    }


def print_report(result, source):
    s = result["summary"]
    findings = result["findings"]

    if not findings:
        print(f"\n  {GREEN}{source}: clean - no documentation-style findings.{RESET}")
        print(f"  Units: {s['units']} ({s['steps']} numbered steps).")
        return

    print(f"\n  {BOLD}{source}: {s['errors']} error(s), {s['warnings']} warning(s).{RESET}")
    print(f"  Units: {s['units']} ({s['steps']} numbered steps).\n")

    for f in findings[:40]:
        colour = RED if f["severity"] == "error" else YELLOW
        print(f"    {colour}{f['type']}{RESET} (line {f['line']}): {f['description']}")
        if f.get("context"):
            print(f"      {GRAY}{f['context']}{RESET}")
    if len(findings) > 40:
        print(f"    ...and {len(findings) - 40} more")

    print(f"\n  Type summary: {s['by_type']}")
    print()


ALL_HELP = (
    "Audit the 14 gated documentation pages (CHECKED_GLOBS). This is one half of "
    "the rule's scope: skill instruction bodies are the other half, and they have "
    "their own gate - use --skills for those."
)

SKILLS_HELP = (
    "Audit every .claude/skills/*/SKILL.md. Gated since 2026-08-17, errors only, "
    "like --all: the corpus measured 300 errors on 2026-08-16, of which 83 were "
    "defects in this checker's own sentence splitter and 217 were real, and it "
    "now stands at zero."
)


def resolve_scope():
    """Return the fourteen documentation pages that exist on disk.

    Deliberately narrower than the rule's scope. The rule also governs skill
    instruction bodies, which `resolve_skill_scope` answers for. Both halves
    carry their own gate since 2026-08-17; the split stays because the two
    scopes resolve from different roots and report separate totals.
    """
    root = get_workspace_root()
    return [root / g for g in CHECKED_GLOBS if (root / g).exists()]


def resolve_skill_scope():
    """Return every skill instruction body, the other half of the rule's scope.

    Separate from `resolve_scope` so the gap has a number instead of an
    assumption. Before this existed, `--all` called itself "every in-scope
    file" and reported a clean corpus, while 74 of 96 skills carried errors it
    never opened.
    """
    root = get_workspace_root()
    return sorted((root / ".claude" / "skills").glob("*/SKILL.md"))


def main():
    parser = argparse.ArgumentParser(
        description="Mechanical audit for the HEADING OS documentation style (ASD-STE100 subset)."
    )
    parser.add_argument("file", nargs="?", help="File to audit")
    parser.add_argument("--all", action="store_true", help=ALL_HELP)
    parser.add_argument("--skills", action="store_true", help=SKILLS_HELP)
    parser.add_argument("--text", help="Inline text instead of a file")
    parser.add_argument("--strict", action="store_true", help="Fail on warnings as well as errors")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of a report")
    parser.add_argument("--quiet", action="store_true",
                        help="Print only files that carry an error, plus the totals. "
                             "The gate form: warnings are heuristic and do not fail, "
                             "so printing all of them on every commit trains the "
                             "reader to skip the output that does matter.")
    args = parser.parse_args()

    if not (args.file or args.text or args.all or args.skills):
        parser.error("a file, --text, --all, or --skills is required")

    # The three reads below were unguarded. An unreadable or non-UTF-8 page threw
    # out of `main`, and the traceback exited 1 - the code this script's own
    # docstring defines as "findings present", not the 2 it defines as "script
    # error". A gate that tells the two apart therefore reported a crash as a
    # style problem. Worse on `--all` and `--skills`: the loop died on the first
    # bad page, so every page after it went unread and nothing said so, which is
    # the silent-truncation shape `.claude/rules/scope-claims.md` forbids.
    targets = []
    unreadable: list[str] = []

    def _read(path):
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Error: cannot read {path}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            unreadable.append(str(path))
            return None

    if args.text:
        targets.append(("inline text", args.text))
    if args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"Error: {path} does not exist", file=sys.stderr)
            sys.exit(2)
        text = _read(path)
        if text is None:
            sys.exit(2)
        targets.append((str(path), text))
    for scope in (resolve_scope() if args.all else [],
                  resolve_skill_scope() if args.skills else []):
        for path in scope:
            text = _read(path)
            if text is not None:
                targets.append((str(path), text))

    if (args.all or args.skills) and not targets:
        # A gate that read nothing must not exit 0. MEASURED 2026-08-29
        # with the documented WORKSPACE_ROOT override: `--all --quiet`
        # printed nothing and exited 0 over zero pages, and `--all --json`
        # raised StopIteration out of `next(iter(results.values()))` and
        # exited 1 -- the code this file's own table reserves for findings
        # present, so a crash was indistinguishable from a style failure to
        # every machine consumer. This script gates: pre-commit and CI both
        # run it. One guard closes both halves, because `targets` can only
        # be empty when the requested scope resolved to nothing.
        print("Error: the requested scope resolved to 0 files, so nothing "
              "was checked. A pass over an empty corpus is not a pass.",
              file=sys.stderr)
        # `sys.exit`, not `return`: `main()` is called bare at the bottom of
        # this file, so a returned code is discarded and the process still
        # exits 0. Every other exit here is spelled the same way.
        sys.exit(2)

    results, passed = {}, True
    for source, text in targets:
        result = audit(text, strict=args.strict)
        results[source] = result
        passed = passed and result["passed"]
        if not args.json and not (args.quiet and result["summary"]["errors"] == 0):
            print_report(result, source)

    if args.json:
        print(json.dumps(results if len(results) > 1 else next(iter(results.values())), indent=2))
    elif len(results) > 1:
        errors = sum(r["summary"]["errors"] for r in results.values())
        warnings = sum(r["summary"]["warnings"] for r in results.values())
        print(f"  {BOLD}{len(results)} files: {errors} error(s), {warnings} warning(s).{RESET}\n")

    # Name what was left out. A sweep that read fewer pages than it was asked to
    # must not report like one that read them all, and the exit code says which
    # kind of failure happened: 2 is "this run did not do its job", 1 is "the
    # pages it read carry errors".
    if unreadable:
        print(f"  {BOLD}{len(unreadable)} file(s) could not be read and were NOT "
              f"checked:{RESET}", file=sys.stderr)
        for name in unreadable:
            print(f"    {name}", file=sys.stderr)
        sys.exit(2)

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
