<!-- audit-skip-start -->
<!-- version: 3.0.0 | last-updated: 2026-08-20 -->
<!-- audit-skip-end -->

# Humanisation Rule - Write All Prose As Human

> Always-active rule. Applies to every natural-language prose output Claude produces here - posts, emails, proposals, Tribe messages, notes, plans, even casual chat replies. Does NOT apply to: code, JSON, machine logs, structured data tables, or quoted/cited third-party text.
>
> Last Updated: 2026-08-20 (eleventh revision - context-floor pass, 13,265 B -> 9,274 B; the moved sections are listed in the reference file below)
> Last Verified: 2026-08-20
>
> Background: `outputs/research/humanizing-ai-text-deep-research.md`. Mechanical audit: `scripts/humanization-check.py`. Empirical basis (9 datapoints) plus the sections moved off this floor: `reference/humanization-empirical-basis.md`. Full banned-vocabulary catalog: `reference/humanization-banned-vocabulary.md`.

## The frame

Override the LLM default register on every output: five fundamentals plus enforcement, producing prose a reader experiences as alive.

## Step 0 - Test before rewriting (calibration gate, mandatory)

Before applying any fundamental to EXISTING prose, **test the baseline** - run `scripts/humanization-check.py`, or (preferred for high-stakes outbound) an external detector (ZeroGPT / GPTZero / Originality.ai). Then act on the reading:

<!-- audit-skip-start -->

| Detector reading | Action |
| --- | --- |
| <15% AI / "Human written" | **Treat the text as a binary blob. Preserve byte-for-byte.** No rewriting, no punctuation normalisation, no typo "fix", no whitespace cleanup, no curly->straight quote conversion, no character-class substitution of any kind. Ship verbatim. Any modification regresses the score. The text's punctuation, quote style, dash type, ellipsis style, irregularities, and surface variance ARE the human signal at this score. **Em-dashes (`—` U+2014), en-dashes (`–` U+2013), curly apostrophes (`'` U+2019), and curly quotes (`"` U+201C / `"` U+201D) are NOT covered by the "no double dashes" rule** (canonical home: `.claude/rules/voice.md`, which carries this carve-out in full — the rule applies to `--`, two ASCII hyphens, only). Preserve all of these verbatim in detector-tested prose. **When saving sub-15% prose to a file, verify smart-quote presence after save** (`grep` for U+2019, U+201C, U+201D, U+2014, U+2013, U+2026); the Write tool can silently normalise curly characters to straight. |
| 15-40% AI / borderline | **Diagnose the dominant signal first, then intervene specifically.** Diagnose polished-versus-rough before touching anything: polished-and-dramatic = Signal A, strip the polish; rough-and-lived = Signal B, do NOT re-rhythm. Read `reference/humanization-empirical-basis.md` § Step 0a before touching rhythm. |
| >40% AI | **Content-first rewrite.** Add specificity and committed stance throughout FIRST; only then consider structural changes, and only if the content-additive pass alone did not move the score. Re-test after each change; revert any that worsens it. |

<!-- audit-skip-end -->

Signal A / Signal B detail, the nine datapoints behind this gate, and the byte-immutability evidence: `reference/humanization-empirical-basis.md`.

For FROM-SCRATCH prose (no existing baseline), apply the fundamentals as guidance, then test the output before delivery and revert any change that worsens the score.

## The fundamentals (mandatory from-scratch; conditional on rewrites)

Ordered by empirical strength: specificity dominates, structure is downstream.

### 1. Specificity density (the dominant signal)

**The most important fundamental.** Content-level signals dominate detection, not structural ones: dense specifics pass even under heavy anaphora; thin specifics fail even on perfect rhythm. **Every paragraph must contain at least one named, dated, or numbered specific** - proper noun, precise figure, named place/person/quarter/module/tool. "The 1997 Camry," not "an older sedan." "£347,850," not "approximately £350,000." The absence of these - the "verbal stock-photo" register - is the actual AI signature. If a specific isn't available, ask for it rather than invent; a fabricated specific is worse than a missing one. Density benchmark (from the 10.8% datapoint): roughly one specific per 30-50 words, distributed through the paragraph, not clustered.

### 2. Committed stance

Take a position. Refuse to balance every claim. End with an asymmetric closer that lands a position, not a summary. Strong declarative verbs over hedged ones ("I think this is wrong," not "it might be argued this approach has limitations"). State a personal stake. When a topic invites both-sides framing, take one side anyway and name it. AI hedges; human commits.

### 3. Burstiness on purpose (from-scratch only)

Mix short and long deliberately when generating from scratch. **Never re-rhythm existing prose** - fragmenting long sentences or smoothing fragments both homogenise variance and worsen the score. Full text: `reference/humanization-empirical-basis.md` § Fundamental 3.

### 4. Kill the AI vocabulary and phrase fingerprints

LLMs over-produce a catalog of surface tells - transitional and emphasis words, figurative abstract nouns, promotional adjectives, business jargon, dramatic metaphors, empty phrases, vague attributions. `scripts/humanization-check.py` catches many of them. **Consult the full per-category catalog before any outbound voice pass: `reference/humanization-banned-vocabulary.md`.** Two operative rules stay resident here:

- **Empty structural patterns are CONDITIONAL on a vacuous Y.** "Not only X, but also Y", "From X to Y", "It's not X, it's Y", "isn't/aren't just" are AI tells ONLY when the X-Y contrast is vacuous (you can swap Y for a synonym or drop it without changing the substantive claim → rewrite). When Y carries information X does not, it is legitimate rhetoric - leave it.
- **Title Case For All Headings is always banned.** Use sentence case; Title Case is print-magazine register RLHF picked up.

**The vacuum trap (CRITICAL):** scrubbing fingerprints without replacing them produces vapid, forgettable prose. Deletion is not the fix - replacement with specific, committed, opinionated prose is. Test every flagged word or pattern: "Am I adding real information here, or making this sound more important?" If the latter, delete and rewrite with concrete content.

### 5. Geometry over vocabulary

At least once per paragraph, take the second- or third-most-natural continuation - the word the model would not have picked. Largely overlaps fundamental 1. Full text: `reference/humanization-empirical-basis.md` § Fundamental 5.

### 6. Two-pass voice editing (mandatory on outbound prose)

For any prose that goes out, produce the content draft, then run a SEPARATE voice pass - never deliver in one pass. Order (specificity first, structure last):

1. **Specificity** - every paragraph has at least one named/dated/numbered specific; add one, or ask for the fact, never fabricate.
2. **Commitment** - does it take a position, or balance every claim? Commit somewhere.
3. **Vocabulary fingerprint scan** - remove or rewrite the banned vocabulary and phrases.
4. **Read aloud (mentally)** - fix stumbles in FROM-SCRATCH content only (never re-rhythm user-supplied prose - see Step 0).
5. **Mechanical audit** - `python scripts/humanization-check.py <file>`; treat findings as hints, not orders.
6. **External detector spot-check** (optional, recommended for high-stakes) - if borderline, intervene on content first per Step 0.

For very long outputs (>3,000 words): break into chunks and re-apply per chunk; voice degrades log-linearly with length (Levy et al., arXiv:2402.14848).

## Integration with the voice stack

This rule ADDS to, does not replace: `reference/misha-voice.md` (Misha's core voice - read first when drafting his voice), `.claude/rules/voice.md`, `.claude/rules/terminology.md`, `.claude/rules/hidden-chars.md`. Voice specifics → `misha-voice.md`; "does this sound like AI?" → this rule.

## Validation requirement

Every prose deliverable presented to Misha carries the confirmation line:

> Word count: X. Hidden characters: clean. Humanisation audit: clean / N findings (one-line summary of fixes if any).

The audit is `python scripts/humanization-check.py <file>`. Run it, report the result, fix findings before delivering.

**Documentation files** legitimately discuss the banned items they govern; wrap banned-list sections in `<!-- audit-skip-start -->` / `<!-- audit-skip-end -->` so the audit ignores them. Rule and reference prose also fails the burstiness check by design - the audit targets outbound prose, not rule or reference files.

## Scope carve-outs and failure modes

Three carve-outs stand. Quote sources verbatim, even where they violate the rule. Code, config and structured data are not prose. On a user-supplied draft, ask before changing voice; fix only obvious unintended issues. Never chase a detector score as the success metric, and never mandate one writer's voice.

Full scope sections, the failure-mode catalogue (vacuum trap, metronome, forced specifics, identity mimicry, "internal output does not count"), and change control: `reference/humanization-empirical-basis.md`.
