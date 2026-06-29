# Observability Layer - /scrutinize Telemetry

**Consumed by:** `.claude/skills/scrutinize/SKILL.md` (Phase 0, all subsequent phases)
**Last Updated:** 2026-05-27

Langfuse Cloud telemetry contract for /scrutinize. Closes R9 from the 2026-05-27 meta-review. Uses the existing `scripts/utils/observability.py` `@observe` decorator and is automatically disabled in vault mode per `.claude/rules/secure-projects.md`.

## What gets traced

Every /scrutinize pass emits one Langfuse trace per phase invocation. Phase 0 opens the parent trace; subsequent phases attach as spans. Trace tags include:

| Tag | Value | Source |
|---|---|---|
| `skill` | `scrutinize` | static |
| `target_type` | `plan` / `execution` / `file` / `dir` / `workspace` | resolved in Phase 0 |
| `target_path` | path or `<conversation-plan>` for plan target | resolved in Phase 0 |
| `relentless` | bool | resolved in Phase 0 (`--relentless` flag present) |
| `iteration` | int (only during --relentless loop) | Phase 4 closure per iteration |
| `judge_family_rotation` | `rotate` / `fixed-claude` / etc | resolved per `bias-mitigation.md` |
| `gemini_model` | model id (e.g. `gemini-3.5-flash`) | env var or default |
| `grok_model` | model id (e.g. `grok-4.3`) | env var or default |
| `claude_model` | model id (e.g. `claude-opus-4-7`) | session model |

## Per-phase span metadata

Each phase emits a span with structured metadata. The metadata is the same data persisted to the saved report (Phase 5) - the observability layer is a parallel sink, not a different data shape.

### Phase 0 span

- `references_loaded`: count of reference files read
- `rules_loaded`: count of rule files read
- `target_resolution_path`: which detection rule fired (priority 1-4 per `target-detection.md`)
- `confirmation_required`: bool

### Phase 1 / 2 span

- `subchecks_run`: list of subcheck IDs (1-14) actually exercised
- `subchecks_na`: list of subcheck IDs logged as `N/A (out of scope)`
- `findings_count_by_severity`: `{BLOCKER: int, HIGH: int, MEDIUM: int, LOW: int, NIT: int}`
- `findings_count_by_confidence_band`: `{0-24: int, 25-49: int, 50-74: int, 75-100: int}`
- `artifact_evaluator_called`: bool (deterministic layer toggle)
- `artifact_evaluator_findings`: count of findings from `artifact-evaluator.py`

### Phase 2.5 span (when refutation runs)

- `refutation_layer`: `2.5a` / `2.5b` / `both` / `skipped`
- `refuted_count`: int (findings dropped by refutation)
- `refute_partial_count`: int (findings downgraded by refutation)
- `survived_count`: int (findings that reached Phase 3)
- `judge_families_used`: list (e.g. `['claude-opus-4-7', 'gemini-3.5-flash', 'grok-4.3']`)
- `debate_runs`: count of Phase 2.5b debates dispatched
- `position_swap_count`: int (number of Meta-Judge calls where Skeptic appeared first)
- `skip_reason`: string (only when skipped: `plan-target` / `no-refute-flag` / `no-findings` / `family-unavailable` / `vault-active`)

### Phase 3 span

- `findings_above_threshold`: count surfaced to CEO
- `findings_below_threshold`: count hidden by default (require `--include-low-confidence`)
- `grade`: `PASS` / `PASS-WITH-NOTES` / `NEEDS-REWORK` / `BLOCKED`

### Phase 4 span (per approval action)

- `approval_command`: `approve all` / `approve <ids>` / `reject all` / `revise` / `skip` / `flag-as-fp`
- `applied_count`: int
- `deferred_count`: int
- `flagged_as_fp_count`: int
- `post_apply_check_pass_count`: int
- `post_apply_check_fail_count`: int

### Phase 4.5 span (when promotion runs)

- `eligible_findings`: count
- `auto_scaffold_offered`: bool (R5)
- `auto_scaffold_accepted`: `scaffold-and-promote-all` / `scaffold-and-promote-subset` / `scaffold-only` / `skip` / `not-offered`
- `promoted_count`: int
- `skipped_count`: int

### Phase 5 span

- `report_path`: absolute path to saved report
- `report_word_count`: int
- `hidden_chars_clean`: bool (must be true)

## Cost telemetry

Each agent call (Phase 2.5 refutation, Phase 2.5b debate) carries Langfuse usage tokens via the wrapped client. The trace aggregates:

- `input_tokens_total`
- `output_tokens_total`
- `cache_creation_input_tokens` (if prompt caching applies)
- `cache_read_input_tokens`
- `wall_clock_seconds_per_phase`
- `wall_clock_seconds_total`

These are visible on the Langfuse dashboard and feed the cost-per-finding metric used to triage R6 debate vs R1 refutation cost-effectiveness over time.

## Trace ID in saved report

Every Phase 5 saved report includes a footer section:

```text
## Observability

Langfuse trace: {trace_id}
View at: https://cloud.langfuse.com/trace/{trace_id}
(blank when LANGFUSE_ENABLED=false or vault mode active)
```

When observability is disabled (vault, env var, or langfuse package missing), the trace_id field is `_disabled_` and the URL is omitted.

## Wiring pattern

The skill does NOT call Langfuse directly. The pattern follows the workspace standard:

1. Each agent dispatch (Phase 2.5 refutation, Phase 2.5b debate) uses the wrapped client from `scripts/utils/api.py` or the cross-family helpers (`scripts/gemini-consult.py`, `scripts/grok-consult.py`). Those helpers already emit traces.
2. Phase boundaries are marked with explicit `@observe(name="scrutinize-phase-N")` decorators on any helper Python invoked from within the skill.
3. The skill itself (markdown-driven) cannot emit Langfuse spans directly. It relies on the wrapped Python tooling to do so. Spans not covered by Python tooling are reported as structured fields in the saved Phase 5 report, where the dashboard's report-parser picks them up.

## Vault and disabled modes

When `_secure/.active-project` exists OR `LANGFUSE_ENABLED=false`:

- All `@observe` decorators degrade to no-op (already handled by `scripts/utils/observability.py`)
- The saved report still includes the "Observability" footer, with `trace_id: _disabled_` and a one-line reason

This protects the air-gap discipline required for vault sessions while keeping the artefact format consistent.

## Dashboard surfacing

The Bridge dashboard Pulse page reads the last N scrutiny runs from `outputs/operations/scrutiny/*.md` and renders a card showing:

- Findings per pass (severity stacked bar)
- FP rate trend (from `_fp_aggregate.md`)
- Cost per pass (from Langfuse trace metadata when available, else `_disabled_`)
- Average wall-clock per phase

The dashboard integration is opt-in via a config flag in the bridge daemon; if not enabled, the card is hidden. The data is always written; the visualization layer is the gate.
