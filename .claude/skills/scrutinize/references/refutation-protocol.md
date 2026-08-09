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

1. Dispatch one refutation agent. Family is assigned by `scripts/scrutinize-dispatch.py` per `references/bias-mitigation.md` - the running session's Claude alternating with the Kimi reasoning pin over the local proxy. Those two are the whole roster; the judge layer reaches nothing else. The agent does NOT see prior reasoning from the finding-emitter - it gets only the finding statement, location, evidence, and read access to the workspace.

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

4. Cost discipline: Phase 2.5a runs in parallel with any remaining Phase 2 finalization work via the Agent tool. The Claude side judges on the running session's model, which is the latest Opus by construction and is never pinned to a tier or a release here; the external side resolves through `get_model("kimi_reasoning")`. Naming either as a fixed class is how a judge layer quietly ends up a year behind.

## Phase 2.5b - Two-agent debate (BLOCKER + HIGH only, after Phase 2.5a)

For findings that survived Phase 2.5a at severity BLOCKER or HIGH, run a Khan-style debate:

1. **Advocate** (model A from rotation). Brief: "Argue this finding is real. Cite specific workspace files, rules, or commit history. Refuse hand-waves. Jordanum 200 words."

2. **Skeptic** (model B from rotation, DIFFERENT family from Advocate). Brief: "Argue this finding is wrong. Cite specific workspace files, rules, or commit history that contradict it. Refuse hand-waves. Jordanum 200 words."

3. **Meta-Judge** (model C from rotation, DIFFERENT family from both Advocate and Skeptic). Brief: "Read both sides. Decide. Verdict: CORRECT (finding is real at its stated severity, keep it), CORRECT_DOWNGRADE (finding is real but the stated severity is too high, keep it one tier lower), INCORRECT (drop), or AMBIGUOUS (drop and surface to CEO). Score: 100 if certain CORRECT, 80 if lean-CORRECT, 60 if AMBIGUOUS, 0 if INCORRECT. Max 100 words explaining the decision, and when downgrading, name the tier you are moving it to."

4. Advocate and Skeptic run IN PARALLEL (single Agent tool call message). Meta-Judge runs AFTER both return. This keeps wall-clock low.

5. Outcomes:
   - `CORRECT` (score >= 75) -> finding proceeds to approval block with the Meta-Judge score replacing the original confidence.
   - `CORRECT_DOWNGRADE` (score >= 75) -> finding proceeds with the Meta-Judge score AND severity lowered one tier (BLOCKER -> HIGH, HIGH -> MEDIUM), mirroring 2.5a's `REFUTE_PARTIAL`. Added 2026-08-09: the outcome set carried a verdict and a score but no severity channel, so a debate that concluded "real, but not at this severity" had nowhere to put the conclusion. On the impeccable trajectory audit both debates produced exactly that, and both judges had to be re-asked for severity out of band, which is a grading habit forming outside the protocol. Downgrading is a judgement the debate is well placed to make and the verdict channel simply could not carry.
   - `INCORRECT` (score < 60) -> finding DROPPED, logged in "Refuted" section.
   - `AMBIGUOUS` (60 <= score < 75) -> finding DROPPED from approval block by default. If `--include-ambiguous` is set, finding appears in the approval block flagged `[AMBIGUOUS]` for the CEO to manually adjudicate.

6. The adversarial split enforces self-preference mitigation. With the two-family roster the binding rule is narrower and stricter than "all three differ": the **Skeptic and the Meta-Judge must never be the same family**, because a Meta-Judge ruling on its own family's refusal to refute is the exact bias being mitigated. `scripts/scrutinize-dispatch.assign_families()` makes the assignment and the side swap is derived from the run id, so neither is a choice the reviewing model gets to make. If only Claude is reachable (proxy down, the pin absent from `cliproxy models`, or the session DECLARED sensitive), fall back to Phase 2.5a single-pass refutation only, and surface the degradation WITH ITS CAUSE in the approval block and in the `## Judge layer` section; the dispatcher also writes a `degraded` row, and `--validate` fails a report that claims a skip without one. Dropping a debate role and recording nothing but "not exercised" is a skipped mitigation dressed as a note.

## Reproduction outranks the jury

A finding reproduced by a command needs no debate. Since 2026-08-09 that is a
first-class outcome rather than a recorded degradation - four earlier passes had
already invented it, substituting deterministic falsification for the debate and
honestly logging it as a protocol deviation, when it was arguably the stronger
refutation all along.

| Outcome | When | Rank |
|---|---|---|
| `REPRODUCED` | Phase 2.5. The harness ran the command and observed a non-zero exit. | Outranks a 2.5b debate; the finding proceeds without one. |
| `FALSIFIED` | Phase 4. The harness re-ran the same command after the fix and observed zero. | Terminal. The finding is closed by evidence, not by opinion. |

**The harness runs the command, never the model.** The model proposes it;
`scripts/scrutinize-dispatch.py --reproduce` executes it and records the exit
code; `--promote` re-runs it after the fix and joins the two observations. This
is the whole excuse-prevention: a narrated reproduction is not one. Three refusals
enforce it - a command that already exits 0 reproduces nothing, a promotion with
no stored `exit_before` has nothing to join, and the record itself refuses a
`FALSIFIED` row whose exit codes do not show the fail-to-pass transition.

A finding whose fix is rejected or deferred stops at `REPRODUCED`. It is never
promoted, and that is the correct terminal state for it.

## Every verdict leaves a row

A verdict that exists only in prose did not happen, as far as anything downstream
can tell. Measured 2026-08-09 across 75 saved reports: the mandated `Refutation:`
header appears in 8, the mandated `## Judge layer` heading in 12. So every judge
call now writes a row through `scripts/utils/scrutinize_record.py`, and
`scripts/scrutinize-record.py --validate` reconciles the saved report against
those rows.

It cannot make omission impossible - the Claude judge IS the running session, so
that verdict is still supplied rather than captured. It makes omission visible,
which is a weaker claim and the true one. Do not write the stronger one.

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

Actual FP rates per confidence band are computed from the `fp_flag` rows in `runs.jsonl`, beside the verdicts they disagree with. The 327-line aggregator that used to render this was deleted on 2026-08-09 after 75 runs produced zero flags: a calibration pipeline with no data is decoration, and the table it drew was mistaken for a working loop. Expected once rows accumulate:

- conf 0-24: ~80% actual FP rate (these are speculative findings)
- conf 25-49: ~55% actual FP rate
- conf 50-74: ~35% actual FP rate
- conf 75-100: ~15% actual FP rate

If actual rates drift far from these expectations after ~30 days of FP data, the refutation prompts or confidence-scoring rubric need tuning. This calibration check is run as part of the human-agreement benchmark (R11).
