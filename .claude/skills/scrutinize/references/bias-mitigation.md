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

## Mitigation 1 - Judge-family rotation

**The rule:** the LLM judge layer rotates across model families across queries within a single scrutinize pass. The deterministic layer (`scripts/artifact-evaluator.py`) stays Claude-only - it is not a judge, it is a static-analysis pass.

**The default rotation (CEO decision, 2026-07-25):**

| Slot | Model | Notes |
|---|---|---|
| 1 | Claude, the running session's model | primary Anthropic family; dispatched in-session via the Agent tool |
| 2 | Kimi k3 | `k3` over the local CLIProxyAPI proxy. Distinct training pedigree, distinct RLHF, a reasoning model that always thinks. This is the ONLY external voice in the default roster. |

Two families, not three. The CEO cut Gemini and Grok from the standing council roster on 2026-07-25 and kept Kimi k3, and the judge layer follows the same roster: running a family the operator has retired would be diversity on paper only. Gemini and Grok stay one flag away for a pass where breadth is worth the latency.

**How the rotation works:**

- For Phase 2.5a single-pass refutation: the Nth finding goes to family `slot[(N-1) mod 2]`, so judges alternate Claude, k3, Claude, k3.
- For Phase 2.5b two-agent debate: Advocate, Skeptic and Meta-Judge cannot all differ with two families, so the binding rule is narrower and stricter. **The Skeptic and the Meta-Judge must never be the same family**, because the Meta-Judge ruling on its own family's refusal to refute is the exact self-preference this mitigation exists to block. Assign the Skeptic to k3 and the Meta-Judge to Claude by default; swap per pass-start.
- For Phase 2 identification (initial finding emission): default Claude, since the primary reviewer is the running session. The rotation begins at Phase 2.5.

**Config knobs (CEO overrides):**

| Env var or flag | Effect |
|---|---|
| `SCRUTINIZE_JUDGE_ROTATION=fixed-claude` | Disable rotation, use Claude for every judge call (compatibility, or SENSITIVE_MODE). |
| `SCRUTINIZE_JUDGE_ROTATION=rotate` (default) | Two-family rotation per the table above. |
| `--judge-family={claude\|kimi\|gemini\|grok}` (one-shot) | Override rotation for this pass only. `gemini` and `grok` are reachable here even though they are out of the default roster. |
| `SCRUTINIZE_KIMI_MODEL=k3` | Override the Kimi side of the rotation. Default `k3`; it is the reasoning pin, distinct from the `kimi-for-coding` fast pin that `scripts/council-models.py --get kimi` serves to other callers. |
| `SCRUTINIZE_GEMINI_MODEL=<model-id>` | Override Gemini, for a pass that opts it back in. |
| `SCRUTINIZE_GROK_MODEL=<model-id>` | Same for Grok. |
| `SCRUTINIZE_CLAUDE_MODEL=<model-id>` | Same for Claude. Default is the running session's model. |

**Invocation pattern.** The k3 judge is dispatched with an explicit model pin:

```bash
python scripts/kimi-consult.py --mode independent --model k3 \
  --reasoning-effort high --max-tokens 12000 \
  --question '<the refutation brief>' --context '<finding, location, evidence>'
```

Never substitute `kimi-for-coding` when k3 is unavailable. A fast coding pin standing in for the reasoning voice produces a judge that looks external and reasons shallowly, which is worse than an honestly recorded absence. Gemini and Grok, when opted in, go through `scripts/gemini-consult.py` and `scripts/grok-consult.py`. Claude judges run as in-session Agent dispatches, because the session is already Claude and calling out of process buys nothing.

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

Cross-family rotation actually REDUCES judge-layer cost vs running every judge call on Claude Opus 4.7. Gemini 3.5 Flash ($1.50/M input, $9/M output) and Grok 4.3 ($1.25/M input) are an order of magnitude cheaper than Opus 4.7 (~$15/M input, ~$75/M output). On a three-family rotation, two of three judge calls land on the cheaper providers - the judge layer ends up roughly 40-60% CHEAPER than single-Opus while gaining the bias-mitigation properties. Estimated FP-rate reduction: 5-15 percentage points per the cited literature.

The ROI defends itself for BLOCKER/HIGH findings. For LOW/NIT, the rotation can be disabled via env var.

## Sensitive-session behaviour

When `SENSITIVE_MODE` is active (`scripts/utils/sensitive.py`), cross-family rotation is DISABLED. The external judge call would carry finding text, file paths and evidence to a third-party model, which is precisely what that flag exists to prevent. Fall back to `SCRUTINIZE_JUDGE_ROTATION=fixed-claude` for the duration of the session, and announce the degradation in the approval block header.

This supersedes the earlier vault rule. The `_secure/` vault was removed in Plan 5 and `_secure/.active-project` no longer exists, so a check for it would never fire and would leave the fallback documented but dead.

## Validation

Two checks must pass before a scrutiny pass closes:

1. **Family-rotation log present:** the saved report has a `## Judge layer` section listing the model used per phase. If absent, the pass is logged as `bias-mitigation: incomplete`.
2. **Position-swap log present** (only if Phase 2.5b ran): the report records the per-call swap bit. If absent, the pass is logged as `position-randomisation: incomplete`.

Incomplete bias-mitigation runs are valid outputs (the skill still produced findings) but are flagged in the saved report so the human-agreement benchmark can exclude them from calibration.
