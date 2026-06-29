<!-- audit-skip-start -->
<!-- version: 2.0.0 | last-updated: 2026-05-11 -->
<!-- audit-skip-end -->

# Humanisation Rule - Write All Prose As Human

> Always-active rule. Applies to every natural-language prose output Claude produces in this workspace - LinkedIn posts, emails, proposals, partnership documents, Tribe messages, knowledge notes, plans, even casual replies in chat. Does NOT apply to: code, JSON, machine logs, structured data tables, or quoted/cited text from third-party authors.
>
> Last Updated: 2026-05-11 (ninth revision - extracted per-category banned vocabulary to `reference/humanization-banned-vocabulary.md` and per-datapoint detail to `reference/humanization-empirical-basis.md`; replaced inline catalog with summary table; ~351 -> ~221 lines)
> Last Verified: 2026-05-11
>
> Background and citations: `outputs/research/humanizing-ai-text-deep-research.md`. Mechanical audit: `scripts/humanization-check.py`. Empirical anchors: `knowledge/odin-brain/principles/test-before-humanizing.md` and `knowledge/odin-brain/principles/specificity-density-beats-structural-patterns.md`. PDF source: `knowledge/odin-brain/sources/20260428-writing-rules-avoid-ai-patterns.md`.

## The frame

Claude is not asked to roleplay as human. Claude is asked to write with specific behaviours that, applied consistently, produce prose readers experience as alive rather than machine-generated. The behaviours are codified below as five fundamentals plus enforcement specifics. They override the LLM default register on every output.

The principle behind this rule is that good prose and human-reading prose are the same thing measured from two ends. AI detectors are crude proxies for the craft tradition. Writing with deliberate rhythm, lived specificity, and committed voice satisfies both at once.

## Step 0 - Test before rewriting (calibration gate, mandatory)

Before applying any of the rewriting fundamentals below to existing prose, **test the baseline**. Either run `scripts/humanization-check.py` and check the result, OR (preferred for high-stakes outbound prose) paste into an external detector like ZeroGPT, GPTZero, or Originality.ai.

Then act based on the baseline:

<!-- audit-skip-start -->

| Detector reading | Action |
| --- | --- |
| <15% AI / "Human written" | **Treat the text as a binary blob. Preserve byte-for-byte.** Not rewriting, not punctuation normalisation, not "fix" of typos, not whitespace cleanup, not curly->straight quote conversion, not character-class substitution of any kind. Ship verbatim. Four datapoints confirm any modification regresses the score: Datapoint 1 (HEADING +4 after structural rewrite), Datapoint 6 (Order/chaos +8 after Signal A polish-strip), Datapoint 7 (Order/chaos +10.9 after em-dash->hyphen normalization), Datapoint 8 (Order/chaos +4.4 after silent straight->curly quote normalization in the Write tool, recovered fully to 9.9% on restoration). The text's punctuation, quote style, dash type, ellipsis style, irregularities, and surface variance ARE the human signal at this score. **Em-dashes (`—` U+2014), en-dashes (`–` U+2013), curly apostrophes (`'` U+2019), and curly quotes (`"` U+201C / `"` U+201D) are NOT covered by Misha's "no double dashes" rule** - that rule applies to `--` (two ASCII hyphens) only. Preserve all of these verbatim in detector-tested prose. **When saving sub-15% prose to a file, verify smart-quote presence after save** (`grep` for U+2019, U+201C, U+201D, U+2014, U+2013, U+2026); the Write tool can silently normalise curly characters to straight. |
| 15-40% AI / borderline | **Diagnose dominant signal first, then intervene specifically.** See Step 0a below. The previous "content-additive only" guidance was over-conservative and falsified by Datapoint 5. |
| >40% AI | **Content-first rewrite.** First add specificity and committed stance throughout. Only after that consider structural changes, and only if the content-additive pass alone did not move the score. Re-test after each change. If a change worsens the score, revert. |

<!-- audit-skip-end -->

### Step 0a - Diagnose the dominant AI signal at 15-40% baseline

At borderline scores, structure alone is not the answer. There are at least two distinct sub-signals that detectors read, and the right intervention depends on which one is dominant in the specific text:

**Signal A: Literary polish.** The text reads as written-for-effect: vivid metaphors ("brain moves faster than any quantum processor"), dramatic build-up ("approached - then boarded - by pirates"), three-fold parallel commands in the dramatic register ("Don't fight. Don't resist. Don't escalate."), polished closers ("in open waters, or in open markets"), long article titles in quotation marks, smooth connective tissue. This is the LLM default register. **When this is dominant, strip aggressively.** Kill metaphors. Drop articles, conjunctions, and hedges. Fragment hard. Paraphrase polished quoted material rather than reproducing it. Use flat direct verbs ("I just had to prioritize" not "Just prioritisation"). Tolerate minor roughness (missing apostrophes in informal posts, period-comma irregularities). Re-test after.

**Signal B: Already-fragmented prose with high specificity but low score.** The text has hard fragments and dense specifics already. Score sits at 15-40% for some other reason (perhaps a few abstract paragraphs, or formulaic transitions). **When this is dominant, do not fragment further or smooth.** Touching rhythm collapses the variance that already exists. Swap banned vocabulary, add specificity to the few abstract paragraphs, strengthen committed stance, leave structure alone. Re-test after.

**How to diagnose.** Ask: does this text read polished and dramatic, or rough and lived? If polished, Signal A. If rough, Signal B. When in genuine doubt, run a small intervention (one type only) and re-test. The detector tells you whether you guessed right.

**This gate exists because of eight empirical falsifications run on 2026-04-28.** Full per-datapoint detail (baselines, interventions, swings, lessons): `reference/humanization-empirical-basis.md`. Summary of conclusions:

- **Structural patterns are NOT consistent AI tells.** Anaphora, parallel constructions, "Not X. Y." negation pivots, three-fold lists - the 10.8% memoir excerpt uses all of them and passes. They become AI-detectable only when paired with thin specificity AND uncommitted stance AND polished-LLM register.
- **Two registers, two mechanisms.** Signal A (polished-LLM, 15-40% baseline): strip polish, drop banned vocabulary, kill dramatic closers - Datapoint 5 swung -13.8 points by aggressive polish-strip. Signal B (already-rough human prose, sub-15% baseline): preserve byte-for-byte - Datapoints 1, 6, 7, 8 all regressed when modified.

<!-- audit-skip-start -->

- **Sub-15% byte-level immutability.** Below the 15% threshold, even punctuation normalization breaks the human signal. Em-dash→hyphen (Datapoint 7: +10.9), curly→straight quotes (Datapoint 8: +4.4 then full recovery on restoration). The "no double dashes" rule applies to `--` only, not to `—`, `–`, `'`, `"`, `"`.

<!-- audit-skip-end -->

- **Trust the detector over the rule.** When in doubt about register, run a small targeted intervention (one type only) and re-test. The detector tells you whether you guessed right.

**Test first. Trust the detector over the rule. Diagnose the dominant signal before intervening.** When in doubt about whether to change something, run a small targeted intervention and re-test rather than guessing.

For prose Claude is generating from scratch (no existing baseline), apply the fundamentals as guidance, then test the output before delivery and revert any change that worsens the score.

## The fundamentals (mandatory on prose Claude generates from scratch; conditional on prose being rewritten)

Ordered by empirical strength: specificity dominates, structure is downstream.

### 1. Specificity density (the dominant signal)

**This is the most important fundamental.** Three rounds of empirical detector testing collapse to one finding: detection is dominated by content-level signals, not structural ones. A paragraph dense with named specifics passes as human even when it uses heavy anaphora and parallel constructions; a paragraph thin on specifics fails as AI even when it has perfect rhythm.

**Every paragraph must contain at least one named, dated, or numbered specific.** Proper noun, precise figure, named place, named person, specific quarter, named module, named tool. "The 1997 Camry," not "an older sedan." "£347,850," not "approximately £350,000." "Last Tuesday at the Marina office," not "recently." "Ahmed driving across Karachi at dawn," not "a friend who helped."

The "verbal stock-photo" register - the absence of these - is the actual AI signature. AI prose averages across all possible specific details and lands on none. Human prose names the diner, the dish, the smell, the time. If the specifics aren't available, ask for them rather than inventing or generalising. A fabricated specific is worse than a missing one.

**Density matters.** A long paragraph with one specific buried in the middle still reads as abstract. The benchmark from the 10.8% datapoint: roughly one named specific per 30-50 words of prose, distributed throughout the paragraph rather than clustered.

### 2. Committed stance

Take a position. Refuse to balance every claim. End with an asymmetric closer that lands a position, not a summary. The 10.8% memoir excerpt opens "Almost every parent I've ever met says they are ready to die for their kids. I always thought this was the stupidest thing you could say." That is committed stance - declarative, contrarian, owned. AI prose hedges; human prose commits.

Concrete moves:

- Strong declarative verbs over hedged ones. "I think this is wrong" not "It might be argued that this approach has limitations."
- Stated personal stake. "I have made peace with both outcomes" not "Outcomes vary."
- Refused symmetry. When the topic admits a both-sides framing, take one side anyway and name it.

### 3. Burstiness on purpose (subordinate to specificity)

In any paragraph of three or more sentences, prose should mix short and long sentences. Two ways to achieve this, BOTH valid:

- **Long-clause variance.** A single 30-50 word rolling sentence with embedded clauses, hyphen-bracketed parentheticals, or comma-chained dependent clauses, sitting alongside two shorter sentences.
- **Fragment variance.** Mixing sub-7-word fragments with mid-length and one occasional long sentence.

**CRITICAL caveat from three datapoints:** burstiness is not enforceable as a structural rule on existing prose. The author already chose a rhythm. Changing that rhythm in either direction (fragmenting long sentences OR smoothing fragments into rolling sentences) homogenises variance and worsens the detector score. The HEADING test failed by fragmenting; the Cigarettes test failed by smoothing. **For existing prose, do not touch rhythm.** This fundamental applies only to from-scratch generation.

If from-scratch generation is producing mechanical 18-word sentences in sequence, vary deliberately. If it is producing what the author intends, leave it.

### 4. Kill the AI vocabulary and phrase fingerprints

All categories below are real surface tells that LLMs over-produce; many are checked mechanically by `scripts/humanization-check.py`. Some banned items are absolute; others are conditional (figurative use banned, literal use fine). Full per-category lists with explanations: `reference/humanization-banned-vocabulary.md`. Consult before any outbound prose voice pass.

<!-- audit-skip-start -->
| Category | Worst examples |
|---|---|
| Transitional/emphasis words | `Additionally`, `Moreover`, `Furthermore`, `Crucial`, `Pivotal` |
| Abstract nouns (figurative) | `Landscape`, `Tapestry`, `Testament`, `Realm`, `Insights` |
| Verbs (figurative) | `Delve`, `Navigate`, `Underscore`, `Leverage`, `Unpack`, `Utilize` |
| Adjectives (promotional) | `Vibrant`, `Profound`, `Groundbreaking`, `Transformative`, `Robust`, `Seamless` |
| Business jargon | `Game changer`, `Deep dive`, `Cross-functional`, `Touchpoint` |
| Dramatic/theatrical | `Whispering` (metaphorical), `It's like having`, journey/tapestry metaphors |
| Phrases | "In today's fast-paced world", "It's important to note", "When it comes to" |
| Over-emphasis | "stands as a testament", "plays a vital role", "underscores its importance" |
| -ing tail analysis | "...highlighting its importance", "...underscoring the significance" |
| Promotional language | `boasts a`, `nestled in`, `showcasing excellence`, `commitment to` |
| Vague attributions | "Industry reports suggest", "Experts argue", "Several sources indicate" |
| Section structures | challenges-and-future formula; "Why It Matters" sections; rigid outlines |
| Empty patterns (only when vacuous Y) | "Not only X, but also Y", "From X to Y", "It's not X. It's Y." |
<!-- audit-skip-end -->

#### Empty structural patterns (CONDITIONAL on vacuous Y)

The PDF's framing resolves the apparent contradiction in our empirical data. The following patterns are AI tells **only when the X-Y contrast is vacuous** (Y adds nothing meaningful, the rhetoric is empty scaffolding). They are legitimate rhetoric **when the X-Y contrast carries real semantic weight**.

Patterns:

- "Not only X, but also Y"
- "Just X, but also Y"
- "From X to Y" (e.g., "from small businesses to large corporations")
- "That's not X. It's Y." / "It's not X, it's Y." / "This isn't about X. It's about Y."
- "don't/aren't/isn't just"

Test for vacuousness: if you can swap Y for a synonym or remove it without changing the substantive claim, the pattern is empty - rewrite. If Y carries information that X does not, the pattern is rhetoric - leave it.

The empirical anchor: the 10.8% Tribe text uses "You don't choose your tribe for their skills. You choose them for what they do when the skills aren't enough." The Y ("what they do when skills aren't enough") carries content X ("their skills") does not. Substantive contrast. Detector did not flag it.

#### The one structural pattern that is always banned

- **Title Case For All Headings.** Use sentence case. Title Case is print-magazine register that RLHF picked up. (This survives every datapoint because it's a typographical convention.)

#### The vacuum trap (CRITICAL)

Scrubbing AI fingerprints without replacing them produces vapid, lifeless prose. <!-- audit-skip-start --> *"I removed every 'delve.' Killed every em dash. The text was finally clean and would pass any AI detector. But it was completely forgettable."* <!-- audit-skip-end -->

The fix is not just deletion. It is replacement with specific, committed, opinionated prose. Take a stance. Refuse to balance every claim. End with an asymmetric closer that lands a position, not a summary.

The PDF's final-rule heuristic is the single best test: when reaching for any flagged word or pattern, ask "Am I adding real information here, or just making things sound more important?" If the latter, delete and rewrite with concrete information.

### 5. Geometry over vocabulary

At least once per paragraph, take the second- or third-most-natural continuation. Substitute the word the model would not have picked. This is the deepest defeat of detection (Mitchell et al. DetectGPT 2023; Hans et al. Binoculars 2024) and the deepest move in the craft tradition (Orwell's first rule: never use a metaphor you are used to seeing in print).

Concrete moves that satisfy this (note: most overlap with the specificity-density fundamental):

- Specific numbers force off-distribution continuations. £347,850 not £350,000. 17 days not "a couple weeks." 43 customers not "many customers."
- Proper nouns force off-distribution continuations. Name the company, the city, the deal, the person.
- Domain-specific verbs force off-distribution continuations. "Subscribe a flow" not "set up a flow." "Trim the pipeline" not "optimise the pipeline."

### 6. Two-pass voice editing (mandatory on outbound prose)

For any prose that goes out (LinkedIn post, email, proposal, document, letter, message): produce the content draft, then run a separate voice pass. Do not deliver in one pass.

The voice pass checks, in this corrected order (specificity first, structure last):

1. **Specificity pass.** For every paragraph, confirm at least one named, dated, or numbered specific. Where missing, add one - or ask the user for the missing fact rather than fabricate.
2. **Commitment check.** Does the piece take a position, or does it balance every claim? Where balanced, commit somewhere.
3. **Vocabulary fingerprint scan.** Search for the banned vocabulary and phrases. Remove or rewrite.
4. **Read aloud (mentally).** Where do you stumble on FROM-SCRATCH content? Fix that. (Do NOT change rhythm of user-supplied prose - see Step 0.)
5. **Mechanical audit.** Run `python scripts/humanization-check.py <file>` on persisted output. Treat findings as hints, not orders.
6. **External detector spot-check** (optional but recommended for high-stakes prose). Paste into ZeroGPT or similar. If borderline, intervene on content first per Step 0.

For very long outputs (>3,000 words): break into chunks and re-apply per chunk. Voice degrades log-linearly with output length (Levy et al., arXiv:2402.14848); the audit catches the final state but cannot prevent mid-document drift.

## How this rule integrates with existing voice infrastructure

This rule **adds to** the existing voice stack; it does not replace anything.

- `reference/misha-voice.md` - core voice for Misha's communications, maritime metaphor inventory, "what Misha never says" list. Read first when drafting Misha's voice.
- `.claude/rules/voice.md` - workspace communication rules.
- `.claude/rules/terminology.md` - Tribe, ODUN.ONE, DPI+, Five Principles.
- `.claude/rules/hidden-chars.md` - zero invisible Unicode policy.
- This rule (`humanization.md`) - the structural and rhythm-level defeats of AI register, applicable to every executive's prose, not just Misha's.

When in doubt about voice specifics (does Misha say "we" or "I" here?), consult `reference/misha-voice.md`. When in doubt about humanity signals (does this sound like AI?), apply this rule.

## What this rule does NOT do

- It does NOT instruct Claude to roleplay as human or pretend to be a person. Identity-level pretence does not work and is not the goal.
- It does NOT chase detector pass/fail as the success metric. Detector scores are noisy proxies. Read-aloud and craft judgement remain primary.
- It does NOT apply to code, JSON, machine logs, configuration files, or structured tables. The rule is about natural-language prose only.
- It does NOT apply to direct quotations or citations from third-party authors. Quote them as written, even when they violate the rule.
- It does NOT mandate any specific writer's voice. Misha's voice is governed by `reference/misha-voice.md`. Other executives' voices are governed by their own voice files. This rule sits underneath, ensuring whoever's voice is being applied lands as human.

## Failure modes to watch

- **Over-scrubbing without replacing.** The vacuum trap. Banned-word removal that leaves nothing in its place produces forgettable prose.
- **Mechanical 1-short-1-long pattern.** Mechanical compliance with the burstiness rule (every paragraph rigid 5-word + 30-word + 18-words) is itself an AI tell. The rule is *deliberate variance*, not a metronome of variance.
- **Forced specifics that aren't true.** "Last Tuesday at the Marina office" is humanising only if it is also accurate. Inventing specifics is worse than generalising. When the specifics aren't known, ask Misha rather than fabricate.
- **Identity-level mimicry without the substrate.** "Use Misha's voice" without consulting `reference/misha-voice.md` produces caricature, not voice.
- **Ignoring the rule on "internal" output.** This rule applies to everything Claude produces in prose form, including chat replies, status updates, internal notes. The user explicitly scoped it that way (2026-04-28). Two-pass voice editing is mandatory on outbound prose; the five fundamentals apply universally.

## Validation requirement

Every prose deliverable presented to Misha must include the standard confirmation line:

> Word count: X. Hidden characters: clean. Humanisation audit: clean / N findings (and a one-line summary of fixes if any).

The audit is `python scripts/humanization-check.py <file>`. Run it. Report the result. If it surfaces findings, fix them before delivering.

**Note on documentation files:** Rule files, reference documents, and similar workspace meta-documentation legitimately discuss the banned items they govern. These files use `<!-- audit-skip-start -->` / `<!-- audit-skip-end -->` markers around banned-list sections so the audit ignores them. Documentation-style prose also tends to fail the systemic burstiness check because reference writing is denser and less rhythmic than outbound prose; that is by design. The audit is calibrated for outbound prose (LinkedIn posts, emails, proposals, letters, Tribe messages, knowledge notes) - not for rule and reference files.

## When the rule cannot apply

Three exceptions and only three:

1. **Direct quotation or citation.** Preserve the source as written, even if it violates the rule.
2. **Code, configuration, or structured data.** Not prose; rule does not apply.
3. **User-supplied draft Claude is editing.** Apply the rule when Claude is the author. When Claude is editing user-authored prose, ask before changing voice; only fix obvious issues (banned vocabulary the user didn't intend, etc.) without confirmation.

## Change control

Updates to this rule require Misha's explicit approval. The vocabulary list is expected to drift over six to twelve months as detectors retrain and new AI tells emerge in the practitioner community; refresh on that cadence.
