# Measurable Execution - Loop / Goal / Metrics Pre-flight

Last Updated: 2026-09-04
Last Verified: 2026-09-04

Always-active rule. Before any non-trivial task, agree the metric that defines
"done ideally" before the work, and assess whether the task fits the native
harnesses `/goal` and `/loop`.

**Entry point.** Attaches to Phase 1 of `prompt-refinement.md`, fires exactly
when that expansion fires, and inherits its escape valves verbatim. No separate
threshold. Rendered in the operator's working language (Russian: Замер /
Метрики / Loop / Goal); the field labels below are canonical English.

## The three checks

- **Metrics (always).** State the concrete, checkable signal that defines "done
  ideally": a test exit code, a count, a file that exists and passes the audit, a
  named deliverable state.
- **Goal-fit (on signal only).** When the task has a verifiable end condition,
  propose it as a ready-to-paste `/goal <condition>`. The condition is the metric.
- **Loop-fit (on signal only).** When the task is recurring or needs polling,
  propose `/loop [interval] <prompt>`.

Surface Goal-fit and Loop-fit ONLY when the task language signals a fit. When
neither helps, say so in one phrase rather than printing dead "no" lines.

## The metric is agreed, not asserted

The operator supplies the criteria when they have them; Claude adopts them. When
none are given, Claude proposes the metric it judges right as part of the block
and asks by what criteria "done" is measured; the operator accepts, sharpens or
replaces it at the approval STOP. Subjective bars (voice, "good enough to send")
are named as judgment calls or proxies, never dressed as hard metrics.

**The metric is a floor, not the quality bar.** A `/goal` can verify while the
output is still weak on voice or judgment. A passing metric never replaces the
normal voice and humanisation pass before the work is called done.

## Block format

The final part of the Phase 1 expansion, before the approval STOP. Plain text,
real newlines, no markdown tables.

    Measure:
    - Metrics: <how "done ideally" is checked> · /loop and /goal add nothing here

When a fit is signaled, surface only the relevant lines; both are proposals:

    Measure:
    - Goal: /goal <verifiable condition>   (proposed)
    - Loop: /loop 30m <prompt>             (proposed)
    - Metrics: <the criteria that decide the goal is met>

## Boundaries

Advisory: it feeds the Phase 1 expansion, which already carries the approval
STOP, so nothing here blocks. Skipping the block on a non-trivial task is a
protocol miss, like skipping Phase 1. Proposing `/loop` or `/goal` is a proposal
only — Claude never launches them autonomously (`lethal-trifecta.md`); the human
launches. For non-trivial code the metric seeds the test contract written at step
3 of a Canopus slice, which the operator's approval commit at step 4 carries; run
`/create-plan` then `/canopus` then `/implement` there rather than duplicating the
planning gate's heavy critique here.

Changes require the operator's explicit approval. Design history and council
trail: the workspace design-spec archive.
