#!/usr/bin/env python3
"""
humanization-check.py - Mechanical pre-publish audit for AI-text fingerprints.

Companion to scripts/sanitize-text.py. Where sanitize-text removes invisible
Unicode, this script checks for the structural and lexical signals that mark
prose as AI-generated. Implements the audit referenced by .claude/rules/humanization.md.

Usage:
  python scripts/humanization-check.py <file>              # Full audit, exits 0 if clean
  python scripts/humanization-check.py --strict <file>     # Strict mode: also fail on warnings
  python scripts/humanization-check.py --json <file>       # JSON output for CI/programmatic use
  python scripts/humanization-check.py --text "string"     # Inline text audit

Checks performed:
  1. Banned vocabulary (delve, tapestry, leverage, robust, etc.)
  2. Banned phrases (it's important to note, in today's world, etc.)
  3. Banned structures ("It's not just X - it's Y", "challenges pivot")
  4. Sentence-length burstiness (CV) per paragraph
  5. ASCII double-hyphen `--` (LLM artifact; em-dashes `—` and en-dashes `–` are fine)
  6. Paragraph-opener transition words (Therefore, Additionally, etc.)
  7. Per-paragraph specificity (proper noun / number / named entity)
  8. Title Case headings detection
  9. Hedge density

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

# ============================================================
# Workspace utility imports
# ============================================================
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET
except ImportError:
    GREEN = YELLOW = RED = CYAN = GRAY = BOLD = RESET = ""


# ============================================================
# Configuration - audit signal definitions
# ============================================================

# Banned vocabulary (case-insensitive whole-word match)
# Significantly expanded 2026-04-28 from comprehensive PDF source.
BANNED_VOCAB = [
    # Verbs
    "delve", "delving", "delved",
    "underscore", "underscores", "underscoring",
    "bolster", "bolsters", "bolstering", "bolstered",
    "foster", "fostering", "fosters",
    "harness", "harnessing", "harnesses",
    "leverage", "leverages", "leveraging", "leveraged",
    "unpack", "unpacks", "unpacking",
    "elevate", "elevates", "elevating", "elevated",
    "revolutionize", "revolutionizes", "revolutionizing", "revolutionized",
    "revolutionise", "revolutionises", "revolutionising", "revolutionised",
    "reimagine", "reimagines", "reimagining", "reimagined",
    "unleash", "unleashes", "unleashing", "unleashed",
    "garner", "garners", "garnering", "garnered",
    "cultivating", "cultivated",
    "boasts", "boasting",
    "enhance", "enhances", "enhancing", "enhanced",
    # Adjectives
    "pivotal",
    "crucial",
    "vital",
    "groundbreaking",
    "cutting-edge",
    "transformative",
    "game-changing",
    "innovative",
    "robust",
    "comprehensive",
    "seamless", "seamlessly",
    "intricate",
    "nuanced",
    "vibrant",
    "multifaceted",
    "holistic",
    "profound",
    "renowned",
    "meticulous", "meticulously",
    "enduring",
    # Abstract nouns
    "testament",
    "tapestry",
    "interplay",
    "intricacies",
    "paradigm",
    # Process nouns / vague filler
    "utilize", "utilizes", "utilizing", "utilization",
    "utilise", "utilises", "utilising", "utilisation",
    "synergy", "synergies", "synergistic",
    "showcase", "showcases", "showcasing",
    "enablement",
    # Transitional words (banned at sentence start; flagged anywhere)
    "additionally",
    "moreover",
    "furthermore",
    "subsequently",
]

# Banned vocabulary in figurative use only (require context check)
BANNED_FIGURATIVE = {
    "navigate": [r"\bnavigate\s+(the|this|these|complex|changing|evolving|landscape|world|terrain|waters|challenges|complexities)\b"],
    "landscape": [r"\b(the|this|that|complex|changing|evolving|competitive|business|technology|digital|market|evolving)\s+landscape\b"],
    "realm": [r"\b(in|within|the|this|that)\s+(the\s+)?realm\s+of\b"],
    "dive into": [r"\bdive\s+into\b"],
    "shed light": [r"\bshed\s+(\w+\s+)?light\b"],
    "pave the way": [r"\bpave\s+(the\s+)?way\b"],
    "rich": [r"\brich\s+(tapestry|heritage|history|tradition|culture|cultural|legacy|narrative|fabric|landscape)\b"],
    "ecosystem": [r"\b(rich|vibrant|thriving|complex|the|an)\s+ecosystem\b"],
    "cultivating": [r"\bcultivating\s+(community|relationships|connections|trust|engagement|talent)\b"],
    "to bridge": [r"\bto\s+bridge\s+(the\s+)?(gap|divide|difference|distance)\b"],
    "align with": [r"\balign\s+with\s+(your|our|their|the|a|an)\b"],
    "resonate with": [r"\bresonate\s+with\s+(your|our|their|the|a|an)\b"],
    "highlight": [r"\b(highlights|highlighting)\s+(the\s+)?(importance|significance|need|fact|value)\b"],
}

# Banned phrases (case-insensitive substring)
# Significantly expanded 2026-04-28 from comprehensive PDF source.
BANNED_PHRASES = [
    # "In today's X world" family
    "in today's fast-paced",
    "in today's rapidly evolving",
    "in today's digital",
    "in today's dynamic",
    "in today's world",
    "in today's modern",
    "in the modern era",
    # Hedge / over-emphasis
    "it's important to note",
    "it is important to note",
    "it's worth noting",
    "it is worth noting",
    "when it comes to",
    "at the end of the day",
    "a testament to",
    "exciting times lie ahead",
    "there's no denying",
    # Sycophancy / RLHF leakage
    "i hope this helps",
    "i hope this finds you well",
    "i hope this email finds you well",
    "great question",
    "i'd be happy to",
    "i'd love to",
    # Conclusion / transition filler
    "in conclusion",
    "to summarize",
    "to summarise",
    "going forward",
    "moving forward",
    # Over-emphasis "stands as / serves as / plays a role" family
    "stands as a testament",
    "serves as a testament",
    "is a testament to",
    "is a reminder of",
    "plays a vital role",
    "plays a significant role",
    "plays a crucial role",
    "plays a pivotal role",
    "plays a key role",
    "underscores its importance",
    "underscores its significance",
    "underscores the importance",
    "reflects broader trends",
    "contributing to the evolution",
    "setting the stage for",
    "marking the future",
    "shaping the future",
    "represents a shift",
    "key turning point",
    "evolving landscape",
    "focal point",
    "indelible mark",
    "deeply rooted",
    # Promotional / advertisement register
    "boasts a",
    "nestled in",
    "in the heart of",
    "natural beauty",
    "showcasing excellence",
    "exemplifies quality",
    "featuring a diverse array",
    "enhancing the experience",
    # Vague attribution
    "industry reports suggest",
    "observers have cited",
    "experts argue",
    "some critics argue",
    "researchers believe",
    "several sources indicate",
    # Generic heritage / ecosystem (without specifics)
    "rich heritage",
    "rich tapestry",
    "rich history",
    "cultural significance",
    # Other dramatic / theatrical
    "hustle and bustle",
    "it's like having",
]

# Structural patterns - flagged as ADVISORY warnings (NOT errors).
# These patterns become AI tells when the X-Y contrast is vacuous (Y adds nothing
# meaningful). They are legitimate rhetoric when the contrast carries real semantic
# weight. The audit flags them; human review decides whether the Y is substantive.
# Empirical anchor: 10.8% Tribe text uses "Not X. Y." patterns successfully because
# Y carries content X does not. See test-before-humanizing principle.
BANNED_STRUCTURES = [
    # "It's not just X - it's Y" / "It's not just X, it's Y"
    (r"\b(it'?s|this is|that is)\s+not\s+(just|merely|only|simply)\s+[\w\s,]{3,40}[-,.]\s*(it'?s|but)\b",
     "Empty-structure candidate: 'It's not just X - it's Y' (verify Y is substantive, not vacuous)"),
    # "This isn't about X. It's about Y"
    (r"\b(this|that|it)\s+isn'?t\s+about\s+[\w\s]{3,40}[.,]\s*(it'?s|this is)\s+about\b",
     "Empty-structure candidate: 'This isn't about X. It's about Y' (verify Y is substantive)"),
    # "Despite X challenges, Y" - challenges pivot
    (r"\bdespite\s+(these\s+)?(challenges|obstacles|difficulties|hurdles)\b.{0,40}\b(opportunit|future|prospect|path|way)",
     "Banned structure: challenges-and-future formula ('Despite challenges, [optimistic conclusion]')"),
    # "Not only X, but also Y" - flagged when Y likely vacuous
    (r"\bnot\s+only\s+[\w\s]{3,30}\s+but\s+also\b",
     "Empty-structure candidate: 'Not only X, but also Y' (verify Y adds meaningful content)"),
    # "From X to Y" generic - small businesses to large corporations etc.
    (r"\bfrom\s+(small|individual|local|emerging|startup)s?\s+(business|company|companies|firm|player)s?\s+to\s+(large|major|established|enterprise|global|multinational)\s+(corporation|company|firm|player)s?\b",
     "Generic 'From X to Y' scaffolding pattern"),
    # "stands as a testament to" / "serves as a testament to"
    (r"\b(stands?|serves?|acts?)\s+as\s+(a\s+)?testament\b",
     "Banned structure: 'stands/serves as a testament' inflation"),
    # "plays a vital/crucial/significant/key role"
    (r"\bplays\s+(a|an)\s+(vital|crucial|significant|pivotal|key|important)\s+role\b",
     "Banned structure: 'plays a [vital/crucial/etc] role' inflation"),
]


# -ing tail analytical phrases at sentence-end
# These are present-participle phrases tacked on to create false analytical depth.
# Strong LLM tic per PDF source. Detected by regex: comma-ing-VERB followed by
# abstract noun ending the sentence.
ING_TAIL_PHRASES = [
    "highlighting its importance",
    "highlighting the importance",
    "underscoring the significance",
    "underscoring its significance",
    "underscoring its importance",
    "emphasising the need",
    "emphasizing the need",
    "ensuring continued growth",
    "ensuring continued success",
    "reflecting broader trends",
    "symbolising progress",
    "symbolizing progress",
    "contributing to development",
    "contributing to growth",
    "fostering innovation",
    "fostering growth",
    "fostering community",
    "encompassing multiple aspects",
    "cultivating community",
    "cultivating engagement",
    "cultivating relationships",
    "shaping the future",
    "marking a turning point",
    "marking the future",
]

# Paragraph-opener transition words to flag
TRANSITION_OPENERS = [
    "Furthermore", "Moreover", "Additionally", "Therefore", "Thus", "Consequently",
    "However", "Nevertheless", "Nonetheless", "Hence", "Indeed",
]

# Hedge phrases (count density)
HEDGE_PHRASES = [
    "it's important to note", "it is important to note",
    "it's worth noting", "it is worth noting",
    "generally speaking", "broadly speaking",
    "in many ways", "in some ways",
    "to a large extent", "to some extent",
    "more or less", "for the most part",
]

# Title Case heading detection (line starting with # and most words capitalised)
TITLE_CASE_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

# Sentence boundary - simple heuristic, not perfect but adequate
SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

# Paragraph boundary
PARAGRAPH_BOUNDARY = re.compile(r"\n\s*\n")

# Proper-noun-ish detection (capitalised mid-sentence words, not at start)
PROPER_NOUN_RE = re.compile(r"(?<=[a-z]\s)([A-Z][a-zA-Z]{2,})")
# Number detection (any digit run, with or without punctuation)
NUMBER_RE = re.compile(r"\b\d[\d,.]*\b")
# Date / month detection
MONTH_RE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec|Q1|Q2|Q3|Q4)\b")


# ============================================================
# Markdown / YAML stripping for clean prose checks
# ============================================================

def strip_markdown_noise(text):
    """Remove markdown noise that would skew prose-level checks."""
    # Strip explicit audit-skip blocks (for documentation files that list banned items)
    text = re.sub(r"<!--\s*audit-skip-start\s*-->[\s\S]*?<!--\s*audit-skip-end\s*-->", "", text, flags=re.IGNORECASE)
    # Strip YAML frontmatter
    text = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL)
    # Strip code fences (```...```)
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Strip inline code (`...`)
    text = re.sub(r"`[^`\n]+`", "", text)
    # Strip URLs
    text = re.sub(r"https?://\S+", "", text)
    # Strip markdown link syntax but keep label
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Strip table rows (|...|...|)
    text = re.sub(r"^\|.*\|.*$", "", text, flags=re.MULTILINE)
    # Strip blockquote markers
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    # Strip bullet markers
    text = re.sub(r"^[\s]*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\s]*\d+\.\s+", "", text, flags=re.MULTILINE)
    return text


def get_paragraphs(text):
    """Split prose into paragraphs."""
    text = strip_markdown_noise(text)
    paras = [p.strip() for p in PARAGRAPH_BOUNDARY.split(text) if p.strip()]
    # Filter out lines that are only headings (start with # after stripping)
    paras = [p for p in paras if not re.match(r"^#{1,6}\s", p)]
    return paras


def get_sentences(paragraph):
    """Split paragraph into sentences."""
    sentences = SENTENCE_BOUNDARY.split(paragraph)
    sentences = [s.strip() for s in sentences if s.strip()]
    # Final sentence may end without ! ? . - keep it anyway
    return sentences


def word_count(text):
    return len(re.findall(r"\b\w+\b", text))


# ============================================================
# Individual checks
# ============================================================

def check_banned_vocab(text):
    findings = []
    lower = text.lower()
    for word in BANNED_VOCAB:
        # Whole-word match (handle hyphenation)
        pattern = re.compile(r"\b" + re.escape(word) + r"\b", re.IGNORECASE)
        for m in pattern.finditer(text):
            findings.append({
                "type": "banned_vocab",
                "severity": "error",
                "word": m.group(),
                "position": m.start(),
                "context": _snippet(text, m.start(), m.end()),
            })
    # Figurative-use checks
    for word, patterns in BANNED_FIGURATIVE.items():
        for pat in patterns:
            for m in re.finditer(pat, text, re.IGNORECASE):
                findings.append({
                    "type": "banned_vocab_figurative",
                    "severity": "error",
                    "word": word,
                    "position": m.start(),
                    "context": _snippet(text, m.start(), m.end()),
                })
    return findings


def check_banned_phrases(text):
    findings = []
    for phrase in BANNED_PHRASES:
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        for m in pattern.finditer(text):
            findings.append({
                "type": "banned_phrase",
                "severity": "error",
                "phrase": phrase,
                "position": m.start(),
                "context": _snippet(text, m.start(), m.end()),
            })
    return findings


def check_banned_structures(text):
    """Detect structural patterns historically called AI tells.

    NEUTRALISED 2026-04-28 to severity=warning following empirical falsification.
    The 10.8% memoir-excerpt datapoint showed that anaphora, parallel
    constructions, and "Not X. Y." patterns are NOT consistent AI tells when
    paired with dense specificity and committed stance. The detection signal is
    content-level (specificity density, committed stance), not structural.

    These patterns remain worth flagging as advisory hints - they correlate
    with AI register on average across LLM outputs - but they should not block
    delivery, and they should NOT trigger structural rewriting on borderline
    prose. The fix for borderline prose is content-additive (specificity), not
    structural-subtractive.
    """
    findings = []
    for pat, desc in BANNED_STRUCTURES:
        for m in re.finditer(pat, text, re.IGNORECASE):
            findings.append({
                "type": "structural_pattern",
                "severity": "warning",
                "description": desc + " (advisory only - neutralised 2026-04-28; see test-before-humanizing principle)",
                "position": m.start(),
                "context": _snippet(text, m.start(), m.end()),
            })
    return findings


def check_over_fragmentation(text):
    """Detect over-fragmentation: documents with many short sentences and no long-clause sentences.

    Calibrated against the 2026-04-28 HEADING-prologue empirical test where a fragmented
    rewrite (12.2% AI on ZeroGPT) scored worse than the long-clause-rich original (8.2%).

    Signal: if document has 200+ words and 8+ sentences but ZERO sentences over 25 words,
    the prose is over-fragmented and likely scores worse on detectors than longer-clause
    alternatives would. Long-clause-rich sentences (single sentence with 30+ words and 2+
    internal commas or hyphen-bracketed parentheticals) are a primary burstiness contributor.
    """
    findings = []
    prose = strip_markdown_noise(text)
    total_words = word_count(prose)
    if total_words < 200:
        return findings

    # Collect all sentences across all paragraphs
    paras = get_paragraphs(text)
    all_sentences = []
    for para in paras:
        all_sentences.extend(get_sentences(para))
    if len(all_sentences) < 8:
        return findings

    long_sentences = [s for s in all_sentences if word_count(s) > 25]
    long_clause_rich = [s for s in all_sentences
                        if word_count(s) >= 30 and (s.count(",") >= 2 or " - " in s)]

    if len(long_sentences) == 0:
        findings.append({
            "type": "over_fragmentation",
            "severity": "error",
            "description": f"Document has {total_words} words across {len(all_sentences)} sentences but ZERO sentences over 25 words. This is the over-fragmentation pattern that scored 12.2% AI on ZeroGPT in the 2026-04-28 calibration test (vs 8.2% for the long-clause original). Add at least one rolling long-clause sentence per major section.",
        })
    elif len(long_clause_rich) == 0 and len(all_sentences) >= 15:
        findings.append({
            "type": "long_clause_missing",
            "severity": "warning",
            "description": f"Document has {len(all_sentences)} sentences and {len(long_sentences)} long ones, but no long-clause-rich sentences (30+ words with 2+ commas or hyphen-bracketed parentheticals). These are major burstiness contributors. Consider adding one.",
        })
    return findings


def check_ing_tail_phrases(text):
    """Detect -ing tail analytical phrases tacked onto sentence ends.

    Flags both the explicit phrase list (ING_TAIL_PHRASES) and a generic regex
    for "[verb-ing] [the/its] [abstract noun]" at sentence end. Strong LLM tic
    per the comprehensive PDF source 2026-04-28.
    """
    findings = []
    # Explicit phrase list
    for phrase in ING_TAIL_PHRASES:
        pattern = re.compile(r",\s+" + re.escape(phrase), re.IGNORECASE)
        for m in pattern.finditer(text):
            findings.append({
                "type": "ing_tail_phrase",
                "severity": "error",
                "phrase": phrase,
                "position": m.start(),
                "context": _snippet(text, m.start(), m.end()),
            })
    # Generic regex: comma + verb-ing + the/its + abstract noun + sentence end
    # Match: ",\s+\w+ing\s+(the|its|their)\s+(importance|significance|need|growth|trends|...)\.?\s*$"
    abstract_targets = (r"importance|significance|relevance|need|necessity|growth|trends|"
                        r"future|impact|legacy|progress|development|community|innovation|"
                        r"engagement|effectiveness|success|value|role|potential|nature|essence")
    generic_pattern = re.compile(
        rf",\s+(\w+ing)\s+(the|its|their|a)\s+({abstract_targets})\b",
        re.IGNORECASE,
    )
    seen_positions = {f["position"] for f in findings}
    for m in generic_pattern.finditer(text):
        if m.start() in seen_positions:
            continue  # Already caught by explicit list
        # Filter out legitimate present participles (e.g., "...ensuring the report is correct")
        # by requiring the word to be at clause-end (followed by punctuation within ~20 chars)
        end_window = text[m.end():m.end() + 25]
        if re.search(r"^[\w\s]{0,20}[.!?]", end_window) or m.end() == len(text):
            findings.append({
                "type": "ing_tail_phrase",
                "severity": "warning",
                "phrase": m.group(1) + " " + m.group(2) + " " + m.group(3),
                "position": m.start(),
                "context": _snippet(text, m.start(), m.end()),
            })
    return findings


def check_sentence_start_additionally(text):
    """Flag sentences starting with 'Additionally' (PDF: critical rule)."""
    findings = []
    # After period/!/? + space (or paragraph start)
    pattern = re.compile(r"(^|[.!?]\s+)(Additionally|Moreover|Furthermore|Subsequently)\b")
    for m in pattern.finditer(text):
        findings.append({
            "type": "transition_at_sentence_start",
            "severity": "warning",
            "word": m.group(2),
            "position": m.start(2),
            "description": f"Sentence starts with transition word '{m.group(2)}' - PDF: never start sentences with Additionally; use sparingly",
            "context": _snippet(text, m.start(2), m.end(2)),
        })
    return findings


def check_double_hyphens(text):
    """Flag ASCII double-hyphen `--` as an LLM artifact.

    Misha's voice rule prohibits `--` in outbound prose (use single hyphen instead).
    Em-dashes (`—` U+2014) and en-dashes (`–` U+2013) are NOT covered by this rule -
    they are legitimate punctuation and preserve human signal at sub-15% detector
    baseline per humanization-empirical-basis.md Datapoint 7 (em-dash → hyphen swap
    pushed sub-15% prose +10.9 points worse).

    This check replaces the previous `check_em_dashes` (calibration fix 2026-04-28
    per Datapoint 9 audit-script finding). The previous check misapplied Misha's
    "no double dashes" rule to single-character em/en-dashes, treating them as
    AI fingerprints when they are in fact human-punctuation signals.

    Pattern: match `--` not surrounded by other dashes or word characters. Catches
    " -- " (the punctuation use Misha bans) but excludes "--strict" (CLI flag),
    "x--y" (compound), and "---" (markdown horizontal rule).
    """
    findings = []
    matches = re.findall(r"(?<![-\w])--(?![-\w])", text)
    if matches:
        findings.append({
            "type": "double_hyphen",
            "severity": "error",
            "count": len(matches),
            "description": f"Found {len(matches)} ASCII double-hyphen(s) `--`; use single hyphen `-` per Misha's voice rule. Em-dashes `—` are legitimate punctuation and preserve human signal.",
        })
    return findings


def check_burstiness(text):
    """Check sentence-length variance per paragraph.

    Two-track rule:
    - Track A (longer prose, mean sentence length > 12): require both a sub-7-word
      sentence AND a 25+-word sentence in any paragraph of 3+ sentences. This is the
      classic Provost/GPTZero burstiness signal for substantive prose.
    - Track B (punchy prose, mean sentence length <= 12): require coefficient of
      variation > 30%. Misha's LinkedIn voice achieves variance through extreme
      shortness (1-3 word sentences mixed with 15-word) - that IS bursty, just on a
      different range. CV catches this; the fixed-threshold rule does not.

    The systemic-burstiness error fires only when >50% of qualifying paragraphs fail
    AND the document is recognisably outbound prose (>200 words; not pure documentation).
    """
    findings = []
    paras = get_paragraphs(text)
    monotone_paras = 0
    total_qualifying = 0
    total_words = word_count(text)

    for i, para in enumerate(paras):
        sentences = get_sentences(para)
        if len(sentences) < 3:
            continue
        total_qualifying += 1
        lengths = [word_count(s) for s in sentences]
        if not lengths:
            continue
        mean_len = sum(lengths) / len(lengths)
        # CV calculation
        if mean_len > 0 and len(lengths) > 1:
            variance = sum((l - mean_len) ** 2 for l in lengths) / len(lengths)
            stdev = variance ** 0.5
            cv = (stdev / mean_len) * 100
        else:
            cv = 0

        if mean_len > 12:
            # Track A: long-form prose - require fixed-threshold spread
            has_short = any(l < 7 for l in lengths)
            has_long = any(l > 25 for l in lengths)
            failed = not (has_short and has_long)
            missing = []
            if not has_short: missing.append("<7w")
            if not has_long: missing.append(">25w")
        else:
            # Track B: punchy prose - require CV > 30
            failed = cv < 30
            missing = [f"CV={cv:.0f}% (<30%)"] if failed else []

        if failed:
            monotone_paras += 1
            findings.append({
                "type": "burstiness_violation",
                "severity": "warning",
                "paragraph_index": i,
                "sentence_lengths": lengths,
                "mean_length": round(mean_len, 1),
                "cv_percent": round(cv, 1),
                "missing": missing,
                "context": para[:120] + ("..." if len(para) > 120 else ""),
            })

    # Systemic flag - only for recognisable outbound prose, not short docs / docs in audit-skip mode
    if total_qualifying >= 4 and total_words >= 200:
        rate = monotone_paras / total_qualifying
        if rate > 0.6:
            findings.append({
                "type": "burstiness_systemic",
                "severity": "error",
                "rate": rate,
                "description": f"{monotone_paras}/{total_qualifying} multi-sentence paragraphs lack burstiness ({int(rate*100)}%)",
            })
    return findings


def check_specificity(text):
    """Check that paragraphs contain at least one named/numbered/dated specific."""
    findings = []
    paras = get_paragraphs(text)
    for i, para in enumerate(paras):
        if word_count(para) < 30:
            continue  # Skip very short paragraphs
        has_proper = bool(PROPER_NOUN_RE.search(para))
        has_number = bool(NUMBER_RE.search(para))
        has_month = bool(MONTH_RE.search(para))
        # Also accept any capitalised word at the start of a sentence that's not a common opener
        if not (has_proper or has_number or has_month):
            findings.append({
                "type": "specificity_missing",
                "severity": "warning",
                "paragraph_index": i,
                "description": "Paragraph has no proper noun, number, or named date - 'verbal stock-photo' risk",
                "context": para[:120] + ("..." if len(para) > 120 else ""),
            })
    return findings


def check_transition_openers(text):
    """Flag paragraphs that open with transition words."""
    findings = []
    paras = get_paragraphs(text)
    for i, para in enumerate(paras):
        first_word = para.split(maxsplit=1)[0] if para.split() else ""
        # Strip trailing punctuation
        first_word_clean = re.sub(r"[^\w]", "", first_word)
        if first_word_clean in TRANSITION_OPENERS:
            findings.append({
                "type": "transition_opener",
                "severity": "warning",
                "paragraph_index": i,
                "opener": first_word_clean,
                "description": f"Paragraph opens with transition word '{first_word_clean}'",
            })
    return findings


def check_hedge_density(text):
    """Count hedge phrases; flag if density > threshold."""
    findings = []
    total_words = word_count(text)
    if total_words < 100:
        return findings
    hedge_count = 0
    for phrase in HEDGE_PHRASES:
        hedge_count += len(re.findall(re.escape(phrase), text, re.IGNORECASE))
    if hedge_count == 0:
        return findings
    density = hedge_count / max(total_words, 1) * 1000  # per 1000 words
    if density > 3.0:
        findings.append({
            "type": "hedge_density",
            "severity": "warning",
            "count": hedge_count,
            "density_per_1000_words": round(density, 2),
            "description": f"{hedge_count} hedge phrases ({round(density,2)} per 1000 words) - LLM hedge over-use",
        })
    return findings


def check_title_case_headings(text):
    """Flag markdown headings that use Title Case For Most Words."""
    findings = []
    for m in TITLE_CASE_RE.finditer(text):
        heading = m.group(2).strip()
        words = re.findall(r"\b[A-Za-z][a-zA-Z]*\b", heading)
        if len(words) < 3:
            continue
        # Skip headings that are mostly proper nouns or all-caps acronyms
        cap_count = sum(1 for w in words if w[0].isupper() and not w.isupper() and len(w) > 3)
        # Common stop words that stay lowercase in title case
        stop_words = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "of", "on", "or", "the", "to", "via", "with"}
        non_stop = [w for w in words if w.lower() not in stop_words]
        if len(non_stop) < 2:
            continue
        cap_non_stop = sum(1 for w in non_stop if w[0].isupper())
        if cap_non_stop / len(non_stop) >= 0.8:
            findings.append({
                "type": "title_case_heading",
                "severity": "warning",
                "heading": heading,
                "description": f"Heading uses Title Case ('{heading}') - prefer sentence case",
            })
    return findings


# ============================================================
# Helpers
# ============================================================

def _snippet(text, start, end, before=20, after=30):
    s = max(0, start - before)
    e = min(len(text), end + after)
    return ("..." if s > 0 else "") + text[s:e].replace("\n", " ") + ("..." if e < len(text) else "")


# ============================================================
# Aggregation and reporting
# ============================================================

def audit(text, strict=False):
    """Run all checks; return a structured findings list and summary.

    Prose-level checks run on markdown-stripped text so inline-code spans
    (used to quote banned words) and code fences don't trigger false positives.
    Title-case-heading check uses the original text (headings live outside
    code blocks).
    """
    prose = strip_markdown_noise(text)
    findings = []
    findings += check_banned_vocab(prose)
    findings += check_banned_phrases(prose)
    findings += check_banned_structures(prose)
    findings += check_double_hyphens(prose)  # calibrated 2026-04-28 per Datapoint 9 - was check_em_dashes
    findings += check_ing_tail_phrases(prose)  # new 2026-04-28 from PDF source
    findings += check_sentence_start_additionally(prose)  # new 2026-04-28 from PDF source
    findings += check_burstiness(text)  # already strips internally
    findings += check_over_fragmentation(text)  # calibrated against HEADING test 2026-04-28
    findings += check_specificity(text)  # already strips internally
    findings += check_transition_openers(text)  # already strips internally
    findings += check_hedge_density(prose)
    findings += check_title_case_headings(text)

    errors = [f for f in findings if f.get("severity") == "error"]
    warnings = [f for f in findings if f.get("severity") == "warning"]

    summary = {
        "total_findings": len(findings),
        "errors": len(errors),
        "warnings": len(warnings),
        "word_count": word_count(text),
        "paragraph_count": len(get_paragraphs(text)),
        "by_type": dict(Counter(f["type"] for f in findings)),
    }

    return {
        "findings": findings,
        "summary": summary,
        "passed": len(errors) == 0 and (not strict or len(warnings) == 0),
    }


def print_report(result, source):
    """Pretty-print a human-readable report."""
    s = result["summary"]
    f = result["findings"]
    if not f:
        print(f"\n  {GREEN}{source}: clean - no humanisation findings.{RESET}")
        print(f"  Word count: {s['word_count']}. Paragraphs: {s['paragraph_count']}.")
        return

    print(f"\n  {BOLD}{source}: {s['errors']} error(s), {s['warnings']} warning(s).{RESET}")
    print(f"  Word count: {s['word_count']}. Paragraphs: {s['paragraph_count']}.\n")

    errors = [x for x in f if x.get("severity") == "error"]
    warnings = [x for x in f if x.get("severity") == "warning"]

    if errors:
        print(f"  {RED}Errors ({len(errors)}):{RESET}")
        for e in errors[:20]:
            t = e["type"]
            if t in ("banned_vocab", "banned_vocab_figurative"):
                print(f"    {RED}{t}{RESET}: '{e['word']}' - {e.get('context','')}")
            elif t == "banned_phrase":
                print(f"    {RED}{t}{RESET}: '{e['phrase']}' - {e.get('context','')}")
            elif t in ("banned_structure", "structural_pattern"):
                print(f"    {RED}{t}{RESET}: {e['description']} - {e.get('context','')}")
            elif t == "double_hyphen":
                print(f"    {RED}{t}{RESET}: {e['description']}")
            elif t == "ing_tail_phrase":
                print(f"    {RED}{t}{RESET}: '{e['phrase']}' - {e.get('context','')}")
            else:
                print(f"    {RED}{t}{RESET}: {e.get('description', e)}")
        if len(errors) > 20:
            print(f"    ...and {len(errors)-20} more")

    if warnings:
        print(f"\n  {YELLOW}Warnings ({len(warnings)}):{RESET}")
        for w in warnings[:15]:
            t = w["type"]
            if t == "burstiness_violation":
                missing = w.get("missing", [])
                cv = w.get("cv_percent", 0)
                mean = w.get("mean_length", 0)
                print(f"    {YELLOW}burstiness{RESET}: para {w['paragraph_index']} missing {','.join(missing)} - lengths {w['sentence_lengths']} (mean {mean}, CV {cv}%)")
            elif t == "specificity_missing":
                print(f"    {YELLOW}specificity{RESET}: para {w['paragraph_index']} - {w.get('context','')}")
            elif t == "transition_opener":
                print(f"    {YELLOW}transition{RESET}: para {w['paragraph_index']} opens with '{w['opener']}'")
            elif t == "hedge_density":
                print(f"    {YELLOW}hedges{RESET}: {w['description']}")
            elif t == "title_case_heading":
                print(f"    {YELLOW}title-case{RESET}: '{w['heading']}'")
            else:
                print(f"    {YELLOW}{t}{RESET}: {w.get('description', w)}")
        if len(warnings) > 15:
            print(f"    ...and {len(warnings)-15} more")

    print(f"\n  Type summary: {s['by_type']}")
    print()


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Mechanical pre-publish audit for AI-text fingerprints."
    )
    parser.add_argument("file", nargs="?", help="File to audit")
    parser.add_argument("--text", help="Inline text instead of a file")
    parser.add_argument("--strict", action="store_true", help="Fail on warnings as well as errors")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of human-readable")
    args = parser.parse_args()

    if not args.file and not args.text:
        parser.error("either a file or --text is required")

    if args.text:
        text = args.text
        source = "inline text"
    else:
        path = Path(args.file)
        if not path.exists():
            print(f"Error: {path} does not exist", file=sys.stderr)
            sys.exit(2)
        text = path.read_text(encoding="utf-8")
        source = str(path)

    result = audit(text, strict=args.strict)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_report(result, source)

    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
