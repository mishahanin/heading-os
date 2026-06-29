# Bias Mitigation - /scrutinize Judge Layer

**Consumed by:** `.claude/skills/scrutinize/SKILL.md` (Phase 2, 2.5)
**Last Updated:** 2026-05-27

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

**The default rotation:**

| Slot | Model | Notes |
|---|---|---|
| 1 | Claude Opus 4.7 | `claude-opus-4-7` - strongest reasoning, primary Anthropic family |
| 2 | Gemini 3.5 Flash | `gemini-3.5-flash` - Google DeepMind, GA 2026-05-19. Flagship intelligence at Flash speed (4x faster than other frontier models per Google's own benchmarks), Dynamic Thinking on by default, optimised for agentic workflows and sub-agent deployment. Knowledge cutoff January 2026. |
| 3 | Grok 4.3 | `grok-4.3` - xAI, launched 2026-05-04. Built-in reasoning, 1M-token context, distinct training pedigree (most divergent from Claude). Intelligence Index 53 (vs 35 median). |

**How the rotation works:**

- For Phase 2.5a single-pass refutation: agent index `i mod 3` selects the model. The Nth finding goes to family `slot[(N-1) mod 3]`.
- For Phase 2.5b two-agent debate: Advocate, Skeptic, Meta-Judge always get three DIFFERENT families. Cycle: Claude/Gemini/Grok rotated per pass-start to avoid the same family always being Advocate.
- For Phase 2 identification (initial finding emission): default Claude (primary reviewer is the running session). The rotation kicks in at Phase 2.5.

**Config knobs (CEO overrides):**

| Env var or flag | Effect |
|---|---|
| `SCRUTINIZE_JUDGE_ROTATION=fixed-claude` | Disable rotation, use Claude for every judge call (compatibility / vault mode). |
| `SCRUTINIZE_JUDGE_ROTATION=rotate` (default) | Three-family rotation per the table above. |
| `--judge-family={claude\|gemini\|grok}` (one-shot) | Override rotation for this pass only. |
| `SCRUTINIZE_GEMINI_MODEL=<model-id>` | Override the Gemini side of the rotation (e.g. flip to `gemini-3.5-pro` when it ships, or to a newer `4.x` line later). Default tracks the latest GA model. |
| `SCRUTINIZE_GROK_MODEL=grok-4.3` | Same for Grok. |
| `SCRUTINIZE_CLAUDE_MODEL=claude-opus-4-7` | Same for Claude. |

**Invocation pattern:** the cross-family agents are dispatched via `scripts/gemini-consult.py` and `scripts/grok-consult.py` for Gemini and Grok respectively. Both scripts accept `--model` and structured I/O. Claude refutation agents run as in-session Agent tool dispatches (the running session is already Claude, so calling out-of-process makes no sense).

**Logging:** every scrutiny pass logs which family was used per phase to the saved report under a "Judge layer" section:

```text
## Judge layer
- Phase 2 identifier: claude-opus-4-7 (session model)
- Phase 2.5a refutations: gemini-3.5-flash x2, grok-4.3 x1, claude-opus-4-7 x2
- Phase 2.5b debate (B1): Advocate=grok-4.3, Skeptic=gemini-3.5-flash, Judge=claude-opus-4-7
- Phase 2.5b debate (H2): Advocate=claude-opus-4-7, Skeptic=grok-4.3, Judge=gemini-3.5-flash
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

## Vault behaviour

When `_secure/.active-project` exists (vault mode active), cross-family rotation is DISABLED. Gemini and Grok calls would leak project context outside the Claude pipeline. Fall back to `SCRUTINIZE_JUDGE_ROTATION=fixed-claude` for the duration of vault session. The skill announces the degradation in the approval block header.

## Validation

Two checks must pass before a scrutiny pass closes:

1. **Family-rotation log present:** the saved report has a `## Judge layer` section listing the model used per phase. If absent, the pass is logged as `bias-mitigation: incomplete`.
2. **Position-swap log present** (only if Phase 2.5b ran): the report records the per-call swap bit. If absent, the pass is logged as `position-randomisation: incomplete`.

Incomplete bias-mitigation runs are valid outputs (the skill still produced findings) but are flagged in the saved report so the human-agreement benchmark can exclude them from calibration.
