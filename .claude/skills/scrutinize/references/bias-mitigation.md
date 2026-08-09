# Bias Mitigation - /scrutinize Judge Layer

**Consumed by:** `.claude/skills/scrutinize/SKILL.md` (Phase 2, 2.5)
**Last Updated:** 2026-07-25
**Last Verified:** 2026-07-25

Mechanical mitigations for documented LLM-as-judge biases. Closes R7 from the 2026-05-27 meta-review of /scrutinize.

The 2026 literature is unambiguous: prompt-level instructions ("be unbiased, don't prefer longer answers") reach about half of the bias they attempt to mitigate. Mechanical mitigations (shuffle on every pairwise call, rotate judge family across queries, length-controlled scoring) outperform prompt fixes consistently. This reference defines those mechanical layers and how /scrutinize wires them.

## The five documented biases

| Bias | Measured magnitude | Reference |
|---|---|---|
| Position bias | 10-15pt winrate swing depending on slot order | Zheng et al. 2024 (MT-Bench) |
| Verbosity bias | 15-30pt inflated preference for longer outputs | Wang et al. 2023 |
| Self-preference | ~5-10pt for own-family outputs | Panickssery et al. 2024 |
| Style / sycophancy | Variable (model + topic dependent) | Wu & Aji 2024 |
| Chain-of-thought bias | Variable, direction-dependent | Shankar et al. 2024 |

The /scrutinize architecture as of v1.2 had zero mechanical mitigations active. This reference adds three: judge-family rotation, position randomisation, and length-controlled scoring.

## Mitigation 1 - Fixed adversarial split

**The rule:** the LLM judge layer puts the adversarial roles in different model families. With two families this is a fixed split with a per-run side swap, not a rotation - calling it rotation was a description borrowed from the three-family era and it flattered the mechanism. The deterministic layer (`scripts/artifact-evaluator.py`) stays Claude-only - it is not a judge, it is a static-analysis pass.

**The roster (CEO decision, 2026-07-25, escapes removed 2026-08-09):**

| Slot | Model | Notes |
|---|---|---|
| 1 | Claude, the running session's model | primary Anthropic family; dispatched in-session. **Never pinned to a version.** The judge IS the session, so it is always the latest Opus the session runs on, and a new Opus reaches the judge layer the day it ships with no edit here. A version literal anywhere in this skill freezes it on the day someone typed it; `tests/test_scrutinize_no_model_pins.py` fails on one. |
| 2 | Kimi, reasoning pin | resolved at call time by `get_model("kimi_reasoning")` over the local CLIProxyAPI proxy, so a new flagship is `python scripts/council-models.py --set kimi_reasoning=<new>` and no code changes. Distinct training pedigree, distinct RLHF, a reasoning model that always thinks. The ONLY external voice in the roster. |

Two families, and that is the whole roster. The CEO cut the other families from the standing council on 2026-07-25 and kept Kimi k3; on 2026-08-09 the judge layer's escape hatches to them were removed too, on the reasoning that a knob nobody turns is not optionality, it is drift waiting to be mistaken for capability. `scripts/gemini-consult.py` and `scripts/grok-consult.py` still exist for `/council`; the judge layer does not reach them.

**How the split works:**

- For Phase 2.5a single-pass refutation: the Nth finding goes to family `slot[(N-1) mod 2]`, so judges alternate Claude, k3, Claude, k3.
- For Phase 2.5b two-agent debate: Advocate, Skeptic and Meta-Judge cannot all differ with two families, so the binding rule is narrower and stricter. **The Skeptic and the Meta-Judge must never be the same family**, because the Meta-Judge ruling on its own family's refusal to refute is the exact self-preference this mitigation exists to block. Assign the Skeptic to k3 and the Meta-Judge to Claude by default; swap per pass-start.
- For Phase 2 identification (initial finding emission): default Claude, since the primary reviewer is the running session. The split begins at Phase 2.5, and the side swap is derived from the run id by `scripts/scrutinize-dispatch.swap_for_run()` so it is a property of the run rather than a choice made per call.

**Config knobs (CEO overrides):**

| Env var or flag | Effect |
|---|---|
| `SCRUTINIZE_JUDGE_SPLIT=fixed-claude` | Collapse the split, use Claude for every judge call. Prose-only knob; no code reads it. |
| `SCRUTINIZE_JUDGE_SPLIT=split` (default) | Two-family adversarial split per the table above. |
| `--judge-family={claude\|kimi}` (one-shot) | Pin one family for this pass. The roster is the two families; there is nothing else to reach. |
| `python scripts/council-models.py --set kimi_reasoning=<id>` | Bump the Kimi judge pin. Distinct from the `kimi` fast pin other callers get; the judge layer must not run a coding pin. There is deliberately NO Claude equivalent - see slot 1. |

**Invocation pattern.** The k3 judge is dispatched with an explicit model pin:

```bash
python scripts/kimi-consult.py --mode independent --model k3 \
  --reasoning-effort high --max-tokens 12000 \
  --question '<the refutation brief>' --context '<finding, location, evidence>'
```

Never substitute `kimi-for-coding` when k3 is unavailable. A fast coding pin standing in for the reasoning voice produces a judge that looks external and reasons shallowly, which is worse than an honestly recorded absence. Claude judges run in-session, because the session is already Claude and calling out of process buys nothing. Every k3 call goes through `scripts/scrutinize-dispatch.py`, never by hand: the dispatcher owns the family assignment and writes the record row, so compliance is a property of the plumbing rather than of the reviewing model's intent.

**Degradation is recorded, never assumed.** If the proxy is down or `cliproxy models` does not list `k3`, run the judge on Claude and write the REASON into the `## Judge layer` section: which call was intended for k3, and what made it unavailable. A pass that quietly runs every judge on Claude and reports "fixed-claude applies" without saying why has skipped the mitigation and hidden it in the same breath. Measured on 2026-07-25: a `--relentless` pass on a plan target ran every judge on Claude, dropped the Advocate role entirely, and recorded only that the rotation "was not exercised". That is the failure this paragraph exists to prevent.

**Logging:** every scrutiny pass logs which family was used per phase to the saved report under a "Judge layer" section:

```text
## Judge layer
- Phase 2 identifier: claude (session model)
- Phase 2.5a refutations: k3 x3, claude x2
- Phase 2.5b debate (B1): Advocate=claude, Skeptic=k3, Judge=claude
- Phase 2.5b debate (H2): Advocate=k3, Skeptic=claude, Judge=k3
```

This is part of the audit trail that supports the human-agreement benchmark (R11).

## Mitigation 2 - Position randomisation

**The rule:** when /scrutinize compares two alternatives - for example, when Phase 2.5b's Meta-Judge sees Advocate's argument vs Skeptic's argument - the order in which they are presented is shuffled on every call.

**Why:** position bias swings winrate 10-15 points in pairwise judging. Shuffling on every call removes the bias mechanically. Prompt-level "ignore order" instructions are documented to fail.

**Mechanics:**

- Generate a per-call random bit `swap = random.random() < 0.5`.
- If `swap` is true, present Skeptic first, Advocate second; otherwise the natural order.
- Log the swap in the saved report so a human auditor can trace per-call ordering.
- The Meta-Judge prompt always says "Argument A: ... Argument B: ..." without revealing which is Advocate vs Skeptic. The skill maps A/B back to roles when computing the verdict.

**Out of scope:** position randomisation does not apply to Phase 2 identification (single-output finding emission) or Phase 2.5a single-pass refutation (single-output judgement). It applies only to pairwise comparisons.

## Mitigation 3 - Length-controlled scoring

**The rule:** when /scrutinize compares two outputs by quality - again, primarily Phase 2.5b's Advocate vs Skeptic - apply a verbosity regression. Subtract the length contribution from each output's score before the Meta-Judge sees them.

**Why:** verbosity bias is 15-30pt of inflated preference for length. Mechanical correction is Dubois et al. 2024's length-controlled win rates: estimate the length-coefficient on a calibration set, then subtract `coef * (length_diff)` from the longer output's apparent quality.

**Mechanics (lightweight implementation):**

- Cap both Advocate and Skeptic outputs at 200 words (the brief already requires this). This is a "soft" length cap that mostly eliminates the bias by construction.
- If outputs exceed 200 words anyway, truncate before passing to Meta-Judge.
- Defer formal length-controlled scoring (regression coefficient on a calibration set) to v2 - the simple word cap captures most of the gain.

## Cost shape

The k3 side draws on the Vivace subscription quota rather than per-token billing, so the marginal cost of the split is latency, not spend. The earlier arithmetic here priced a three-family rotation on providers this roster no longer runs and has been removed rather than restated: an unmeasured saving quoted to one decimal place is worse than no number.

The split defends itself on BLOCKER/HIGH findings, where a confidently wrong verdict halts real work. For LOW/NIT it can be pinned to one family.

## Sensitive-session behaviour

When a session is DECLARED sensitive, the k3 side is not dispatched: the external
judge call would carry finding text, file paths and evidence to a third-party
model, which is precisely what the flag exists to prevent. The pass falls back to
Phase 2.5a on Claude alone (`SCRUTINIZE_JUDGE_SPLIT=fixed-claude`) and announces
the degradation in the approval block header.

**Declared, not defaulted, and the distinction is load-bearing.** The gate lives
in `scripts/scrutinize-dispatch.py` and calls `sensitivity_is_declared()`. It must
never call `is_sensitive()`, which is fail-closed: an unset `SENSITIVE_MODE`
resolves sensitive, so a gate asking it would refuse every proxy call on an
ordinary machine and disable half the roster permanently. That is not a
hypothetical - the 2026-08-09 scrutiny pass ran without its k3 side for exactly
that reason, on a machine where nobody had declared anything. A person who typed
`SENSITIVE_MODE=on` knows something no default knows; an unset variable is the
machine's default and must not be read as a declaration.

Either way the refusal is recorded, not merely obeyed: the dispatcher writes a
`degraded` row naming the cause and exits non-zero, and `--validate` fails a
report that declares a skipped refutation with no such row.

This supersedes the earlier vault rule. The `_secure/` vault was removed in Plan 5
and `_secure/.active-project` no longer exists, so a check for it would never fire
and would leave the fallback documented but dead.

## Validation

Two checks must pass before a scrutiny pass closes:

1. **Judge-layer log present:** the saved report has a `## Judge layer` section listing the family used per phase. Measured 2026-08-09: that heading appears in 12 of 75 saved reports, which is why the mandate now has a machine-checkable sibling - every judge call also writes a `verdict` row through the dispatcher, and `scripts/scrutinize-record.py --validate` reconciles the report's `Refutation:` header against those rows.
2. **Position-swap log present** (only if Phase 2.5b ran): the report records the per-call swap bit. If absent, the pass is logged as `position-randomisation: incomplete`.

Incomplete bias-mitigation runs are valid outputs (the skill still produced findings) but are flagged in the saved report so the human-agreement benchmark can exclude them from calibration.
