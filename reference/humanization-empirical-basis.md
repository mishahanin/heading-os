<!-- version: 1.1.1 | last-updated: 2026-04-28 -->

# Humanisation rule - empirical basis

Consumed by: `.claude/rules/humanization.md`

Last Updated: 2026-04-28
Last Verified: 2026-06-08

> The nine datapoints below are a fixed historical record of detector-based
> falsifications run on 2026-04-28; they are not re-run. "Last Verified"
> advances when the record is re-confirmed accurate and still the basis the
> humanisation rule cites — not when the experiments are repeated.

The humanisation rule's calibration gate (Step 0) and structural-vs-content guidance are derived from nine detector-based falsifications run against ZeroGPT (datapoints 1-8) and a cross-detector observation against QuillBot + ZeroGPT (datapoint 9), all on 2026-04-28. This file captures the full datapoint detail. The rule itself summarises the conclusions; reach here when the conclusions need to be re-validated, when a new datapoint contradicts the rule, or when an executive-side reader wants to understand why the rule says what it does.

---

## The nine datapoints (2026-04-28)

### 1. HEADING memoir prologue (Signal B sub-15% baseline)

- Baseline: **8.2% AI** on ZeroGPT
- Intervention: I fragmented long-clause-rich sentences into staccato fragments "for rhythm"
- Result: **12.2% AI** (+4 points worse)
- Lesson: Fragmentation LOWERED the variance the detector measures. Already-fragmented human prose has irregular sentence-length variance; mechanical fragmentation homogenises it into a uniform short-sentence rhythm that reads as machine.

### 2. Cigarettes Chapter 1 (Signal B mid-band baseline)

- Baseline: **29.3% AI**
- Intervention: I smoothed staccato fragments into rolling long-clause sentences "for rhythm"
- Result: **34.8% AI** (+5.5 points worse)
- Lesson: Smoothing LOWERED variance the other way. Mirror image of Datapoint 1 - the author's existing rhythm IS the variance signal; any attempt to homogenise it in either direction worsens detection.

### 3. Tribe-and-fatherhood memoir excerpt - short version (anaphora-heavy human prose)

- Baseline: **10.8% AI** despite heavy anaphora ("The technology broke. The hardware broke. The weather models were wrong..."), parallel constructions ("X's skill was A. His value was B." repeated 4x), and fragmentary lists
- Result: Detector did not flag these as AI patterns
- Lesson: Structural patterns (anaphora, parallel construction, fragmentary lists) are NOT consistent AI tells. They are AI-detectable only when paired with thin specificity AND uncommitted stance AND polished-LLM register.

### 4. Tribe-and-fatherhood memoir excerpt - full version

- Baseline: **10.8% AI** with ~430 additional philosophical paragraphs (much longer than Datapoint 3)
- Intervention: None - Step 0 recommended "do not rewrite"
- Result: Detector confirmed at the same 10.8%
- Lesson: Step 0's "do not rewrite below 15%" calibration is correct. Length does not change the verdict if specificity density and stance commitment are consistent throughout.

### 5. LinkedIn task-saturation post (Signal A polish-strip - largest swing)

- Baseline: **21.9% AI** (borderline)
- My intervention (Step 0 borderline-guidance "content-additive only"): **28.3% AI** (+6.4 points worse)
- Misha's stripped-down version: aggressive fragments, dropped articles and connectives, killed the quantum-processor metaphor, killed the three-fold commands, killed the closing metaphor, paraphrased the quoted article title -> **8.1% AI** (-13.8 points better than baseline)
- Lesson: This is the single largest swing in the dataset and the cleanest refutation of the previous "do not touch structure" guidance at borderline scores. Signal A polish-strip works at 15-40% baseline when the polish is the AI tell. The "content-additive only" guidance was wrong for Signal A polished-LLM register.

### 6. Order/chaos LinkedIn post (Signal A mechanism applied below 15% - failed)

- Baseline: **9.9% AI** (already in human zone)
- Hypothesis: Test whether Signal A polish-strip extends below 15%
- Intervention: Stripped polish targets (banned phrase, dramatic closer, "It's not X. It's Y" patterns) and added one rolling long-clause sentence as insurance
- Result: **17.9% AI** (+8 points worse than baseline)
- Lesson: This is a Datapoint 1 echo - the sub-15% rewriting penalty applies even when the move is Signal A polish-strip, not structural fragmentation. **The Signal A mechanism cuts off at the 15% threshold.** Below 15%, the text's natural variance and lived qualities are themselves the human signal; touching any part of them homogenises in the wrong direction.

### 7. Order/chaos post (em-dash to hyphen normalization)

- Baseline: **9.9% AI**
- Intervention: After "ship as is" was confirmed, I saved the canonical version and applied what I described as "the only mechanical fix - not a rewrite": converted em-dashes (`—`) to single hyphens (`-`) per Misha's "no double dashes" preference
- Result: **20.8% AI** (+10.9 points worse than baseline)
- Lesson: At sub-15% baseline, even surgical "non-rewrites" like punctuation normalization can break the human signal. The "no double dashes" rule applies to `--` (two ASCII hyphens, an LLM artifact), not to `—` (single Unicode em-dash, legitimate punctuation). Em-dashes contribute to punctuation variance and rolling sentence structure that detectors read as human; converting them homogenises both signals in the AI direction.

### 8. Order/chaos post (silent straight↔curly quote normalization - cleanest datapoint)

- Baseline: **9.9% AI**
- Intervention 1: After Datapoint 7's failed em-dash conversion, I "restored" em-dashes in the saved file
- Misha re-tested: **14.3% AI** (+4.4 points worse than baseline)
- Inspection: 32 straight ASCII apostrophes and 16 straight ASCII double quotes where Misha's pasted text had used curly Unicode equivalents (`’`, `“`, `”`). The em-dashes had been correctly preserved during my "restoration" but I had not noticed the silent curly→straight normalisation
- Intervention 2: Ran a contextual smart-quote conversion pass to restore curly characters
- Misha re-tested: **9.9% AI** - full recovery to baseline
- Lesson: This is the cleanest sub-15% datapoint in the dataset. A single character-class substitution, semantically null to a human reader, broke the score; restoration of the same characters recovered it exactly. **At sub-15% baseline, the rule is byte-level immutability** - punctuation, quote style, dash type, ellipsis style, all matter. The Write tool can silently normalise curly characters to straight; verify smart-quote presence after any save of sub-15% prose.

### 9. LinkedIn thought-leadership essay (cross-detector blind spot, register-conditional)

- Baselines: **0.0% AI on QuillBot, 4.1% AI on ZeroGPT** - the first cross-detector reading in the dataset
- Workspace audit (`scripts/humanization-check.py`): 1 error (em-dash flag, false positive per Datapoint 7) + 6 warnings (4 specificity_missing, 2 burstiness violations)
- Intervention: None - this is an observation datapoint, not an intervention test
- Result: A 742-word anonymous LinkedIn essay on product-startup MVPs and CI/CD scored in the human zone on both detectors despite breaking multiple fundamentals - zero specificity density (no proper nouns, dates, numbers, named places, named people), Title Case headings throughout, "Not X – It's Y" vacuous-contrast pattern, banned phrases ("Let's be honest", "Final Thought", "Let's build something great"), banned over-emphasis vocabulary ("real" appears 7+ times), polished marketing closer ("Let's build something great – together.")
- Surface features the text retains: heavy contractions (multiple per sentence), direct second-person address ("you", "your", "we"), em/en-dash frequency (12 instances), hard short fragments as standalone paragraphs ("It's a journey. A very real one." / "Fast."), question headings ("Why Choose an Actively Developed Product?"), one personal first-person insert ("I've seen too many roadmaps treated like rigid calendars")
- Lesson: **The specificity-dominant fundamental is register-conditional, not universal.** Datapoints 1-8 came from memoir, Tribe messages, and longer-form posts where specificity dominated. Datapoint 9 establishes that LinkedIn thought-leadership marketing register can pass detection without specificity. The rule's full fundamentals still apply to high-stakes prose where both human readers and detectors must be defeated. For LinkedIn marketing copy where the register itself is acceptable as generic and only the detector is the adversary, the rule appears over-conservative.
- Caveats: N=1 in this register. Detector blind spots evolve as detectors retrain. Feature isolation not done - we cannot say which feature drives the score (contractions, em-dashes, second-person, fragments, or some combination). Provenance unclear - the text may be AI-generated, human-written, or human-edited from AI; detector reading is the same regardless. Source preserved at `outputs/research/2026-04-28_humanization-datapoint-9_linkedin-source.md` for re-testing.
- Audit script calibration finding: the em-dash flag in `scripts/humanization-check.py` is a false positive in light of Datapoint 7's lesson (em-dashes preserve human signal at sub-15%). The script flagged em-dashes as errors universally; the rule says they should be preserved. **Resolved 2026-04-28**: replaced `check_em_dashes` with `check_double_hyphens`, which flags ASCII `--` (the actual LLM artifact and the target of Misha's "no double dashes" voice rule) while leaving em-dashes `—` and en-dashes `–` alone. Re-audit of this datapoint dropped from 1 error + 6 warnings to 0 errors + 6 warnings, with the rule-aligned findings (4 specificity_missing, 2 burstiness violations) preserved.

---

## Cross-datapoint patterns

**Structure is not the dominant signal.** Datapoints 3 and 4 confirm structural patterns (anaphora, parallel constructions, "Not X. Y." negation pivots, three-fold lists) are NOT consistent AI tells. They become AI-detectable only when paired with thin specificity, uncommitted stance, AND polished-LLM register.

**Two registers, two mechanisms.**

- **Signal A (polished-LLM register, 15-40% baseline):** Strip the polish - banned vocabulary, dramatic closers, vacuous parallelism, "It's not X. It's Y" templates. Datapoint 5 confirms this works (-13.8 swing).
- **Signal B (already-rough human prose, sub-15% baseline):** Preserve byte-for-byte. Datapoints 1, 6, 7, 8 confirm any modification regresses the score.

**Sub-15% byte-level immutability.** Datapoints 1, 6, 7, 8 establish that below the 15% threshold, even punctuation normalization (em-dash→hyphen, curly→straight quote) breaks the human signal. The "no double dashes" rule applies to `--` only, not to `—`, `–`, `’`, `“`, `”`.

**Specificity dominance is register-conditional.** Datapoint 9 establishes a third register: LinkedIn thought-leadership marketing. In this register, two independent detectors agreed the text reads as human (0% / 4.1%) despite zero specificity density and the full set of structural and vocabulary tells. The lesson is not that specificity does not matter, but that it appears optional in registers detectors do not score well in. For high-stakes prose where the human reader is also an adversary, specificity remains dominant. For Claude-generated marketing copy where only the detector matters, the fundamentals can be relaxed - but verify with cross-detector testing first, since N=1 is not a generalisation.

**The detector is calibrated against the dominant signal.** When in doubt about which register a piece of prose is in, run a small targeted intervention (one type only) and re-test. The detector tells you whether you guessed right.

---

## When to update this file

Add a new datapoint when:

1. A detector run produces a result that contradicts one of the conclusions above
2. A new intervention type (not just fragmentation, smoothing, polish-strip, punctuation) produces a measurable effect
3. A different detector (not ZeroGPT) confirms or contradicts the ZeroGPT-derived rules
4. A new register (not memoir, Tribe message, long-form LinkedIn post, short-form LinkedIn marketing) is tested

Format: numbered datapoint, baseline score(s), intervention description, result score, lesson. One-line cross-reference back to `.claude/rules/humanization.md` Step 0 if the new datapoint changes the rule's guidance.
