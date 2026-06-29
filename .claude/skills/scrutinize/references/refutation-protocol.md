# Refutation Protocol - /scrutinize Phase 2.5

**Consumed by:** `.claude/skills/scrutinize/SKILL.md` (Phase 2.5)
**Last Updated:** 2026-05-27

The adversarial verification layer that filters false positives before findings reach the approval block. Closes R1 (disprove step) and R6 (two-agent debate on BLOCKER + HIGH) from the 2026-05-27 meta-review of /scrutinize.

This phase sits between Phase 2 (Identify) and Phase 3 (Approval Block). It runs in two sub-phases:

| Phase | Severity scope | Pattern | Cost shape |
|---|---|---|---|
| 2.5a | every BLOCKER, HIGH, MEDIUM | single-pass refutation | cheap |
| 2.5b | every BLOCKER, HIGH | two-agent debate + Meta-Judge | moderate |

LOW and NIT findings bypass Phase 2.5 entirely - the cost/value ratio is poor at those tiers and they rarely cause real harm if wrong.

## Why this exists

Both council members (Gemini and Grok) converged on the same first-move recommendation in the 2026-05-27 meta-review: insert an adversarial filter before findings post. Anthropic's own Code Review plugin (March 2026) uses an equivalent verification step and reports under 1% incorrect findings at scale. Khan et al. ICML 2024 Best Paper showed two-LLM debate lifts non-expert judge accuracy from 60% to 88% - the cost of running debate is small compared to the cost of a confidently-wrong BLOCKER halting forward progress on the CEO's work.

## Phase 2.5a - Single-pass refutation (every BLOCKER + HIGH + MEDIUM)

For each finding at severity BLOCKER, HIGH, or MEDIUM produced in Phase 2:

1. Dispatch one refutation agent. Model family is rotated per `references/bias-mitigation.md` (default rotation: Claude Opus 4.7, Gemini 3.5 Flash, Grok 4.3). The agent does NOT see prior reasoning from the finding-emitter - it gets only the finding statement, location, evidence, and read access to the workspace.

2. Agent brief (template):

   ```text
   Finding under review:
     ID:        {id}
     Severity:  {severity}
     Statement: {statement}
     Location:  {file}:{line}
     Evidence:  {evidence_quote}

   Your job: refute this finding. Look for:
   - File context the original reviewer missed (lines above/below the location)
   - Workspace rules that explicitly permit what was flagged
   - Existing tests, hooks, or scripts that already cover the case
   - Prior decisions in CLAUDE.md, commit history, or git blame
   - Whether the cited evidence actually supports the statement

   Return one of:
     REFUTATION_FAILED - no contradiction found; finding survives
     REFUTED: <one-sentence reason citing file/rule> - finding should be dropped
     REFUTE_PARTIAL: <reason> - finding is real but severity should be downgraded

   Adjust the confidence score by:
     +5 to +15 if your refutation failed (the finding looks more solid)
     N/A if REFUTED (the finding is dropped, score irrelevant)
     -10 to -25 if REFUTE_PARTIAL (the finding is weaker than originally graded)
   ```

3. Outcomes:
   - `REFUTATION_FAILED` -> finding proceeds to Phase 3 with confidence adjusted upward.
   - `REFUTED` -> finding is DROPPED from the approval block. Logged in a "Refuted" section of the saved report so the CEO can audit dropped findings if curious.
   - `REFUTE_PARTIAL` -> finding proceeds with confidence adjusted downward AND severity downgraded one tier (BLOCKER -> HIGH, HIGH -> MEDIUM, MEDIUM -> LOW).

4. Cost discipline: Phase 2.5a runs in parallel with any remaining Phase 2 finalization work via the Agent tool. Default agent model class is the "judge tier" defined in `references/bias-mitigation.md` (typically Sonnet-class or rotation thereof).

## Phase 2.5b - Two-agent debate (BLOCKER + HIGH only, after Phase 2.5a)

For findings that survived Phase 2.5a at severity BLOCKER or HIGH, run a Khan-style debate:

1. **Advocate** (model A from rotation). Brief: "Argue this finding is real. Cite specific workspace files, rules, or commit history. Refuse hand-waves. Maximum 200 words."

2. **Skeptic** (model B from rotation, DIFFERENT family from Advocate). Brief: "Argue this finding is wrong. Cite specific workspace files, rules, or commit history that contradict it. Refuse hand-waves. Maximum 200 words."

3. **Meta-Judge** (model C from rotation, DIFFERENT family from both Advocate and Skeptic). Brief: "Read both sides. Decide. Verdict: CORRECT (finding is real, keep it), INCORRECT (drop), or AMBIGUOUS (drop and surface to CEO). Score: 100 if certain CORRECT, 80 if lean-CORRECT, 60 if AMBIGUOUS, 0 if INCORRECT. Max 100 words explaining the decision."

4. Advocate and Skeptic run IN PARALLEL (single Agent tool call message). Meta-Judge runs AFTER both return. This keeps wall-clock low.

5. Outcomes:
   - `CORRECT` (score >= 75) -> finding proceeds to approval block with the Meta-Judge score replacing the original confidence.
   - `INCORRECT` (score < 60) -> finding DROPPED, logged in "Refuted" section.
   - `AMBIGUOUS` (60 <= score < 75) -> finding DROPPED from approval block by default. If `--include-ambiguous` is set, finding appears in the approval block flagged `[AMBIGUOUS]` for the CEO to manually adjudicate.

6. Family rotation enforces self-preference mitigation: Advocate-Skeptic-Judge always span three different families (cycle: Claude -> Gemini -> Grok). If only Claude is available (API outages, vault mode), fall back to Phase 2.5a single-pass refutation only and surface the degradation in the approval block.

## Why two phases not one

Phase 2.5a is cheap and catches the obvious false positives - typos, misread evidence, missed context. Phase 2.5b is more expensive and only fires on the survivors, where the BLOCKER/HIGH severity makes a wrong call most costly. The cost curve is right: most findings die in 2.5a, only the strong-looking ones reach 2.5b, and the workspace-target 5-specialist budget never gets blown.

## Refuted findings - what happens to them

Refuted findings are NOT lost. They are saved under a "Refuted Findings" section in the Phase 5 report with:

- Original ID, severity, statement, evidence
- Refutation reason (which agent refuted, what they cited)
- Whether 2.5a or 2.5b dropped it

This serves two purposes: (a) audit trail so the CEO can spot-check whether the refutation agent is over-aggressive, and (b) calibration data for the human-agreement benchmark (R11) - replays compare current-pass refutations against historical CEO `flag-as-fp` decisions.

## Skip conditions

Phase 2.5 is skipped entirely when:

- `target = plan` - plans are conversational, refutation has poor grip on prose
- `--no-refute` flag is set (CEO override for quick passes)
- Phase 2 emitted zero BLOCKER/HIGH/MEDIUM findings - nothing to refute
- API access to >= 2 distinct judge families is unavailable - falls back to 2.5a single-family only (degradation reported in approval block header)

In all skip cases, the approval block header must announce the skip explicitly: `"Note: Phase 2.5 refutation skipped because <reason>. Confidence scores are scorer-emitted only, not refutation-adjusted."`

## Cost expectations

Per the Anthropic plugin design (4 parallel agents at scale, sub-1% FP rate), the refutation layer typically adds:

- 2.5a: ~1 extra agent call per BLOCKER/HIGH/MEDIUM finding (parallelizable; wall-clock cost is one agent's worth)
- 2.5b: 3 extra agent calls per BLOCKER/HIGH finding (Advocate + Skeptic in parallel, then Judge)

For a typical execution-target run with 5 findings (1 BLOCKER, 2 HIGH, 2 MEDIUM), Phase 2.5 adds ~5 refutation calls + ~9 debate calls = ~14 agent calls. The Langfuse observability layer (`references/observability.md`) emits per-finding cost telemetry so the CEO can see actual spend.

## Calibration

The FP aggregator (`scripts/scrutinize-fp-aggregate.py`) reads the `_fp_log.jsonl` and reports actual FP rates per confidence band. Expected after Phase 2.5 ships:

- conf 0-24: ~80% actual FP rate (these are speculative findings)
- conf 25-49: ~55% actual FP rate
- conf 50-74: ~35% actual FP rate
- conf 75-100: ~15% actual FP rate

If actual rates drift far from these expectations after ~30 days of FP data, the refutation prompts or confidence-scoring rubric need tuning. This calibration check is run as part of the human-agreement benchmark (R11).
