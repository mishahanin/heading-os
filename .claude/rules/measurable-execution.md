# Measurable Execution - Loop / Goal / Metrics Pre-flight

Last Updated: 2026-07-11
Last Verified: 2026-07-11

Always-active rule. Before starting any non-trivial task, assess whether it fits the native Claude Code harnesses `/goal` and `/loop`, and agree the metric that defines "done ideally" before the work, so results are consistently excellent and verifiable.

**Entry point.** This attaches to Phase 1 of `prompt-refinement.md`. It fires exactly when the Phase 1 expansion fires and inherits that rule's escape valves verbatim (`!` prefix, a direct reply to a question Claude asked, a trivial one-step correction). No separate threshold.

**Render language.** The block is shown in the operator's working language. The field labels below are canonical English; render them localized (for a Russian-speaking operator: Замер / Метрики / Loop / Goal).

## The three checks

- **Metrics (always).** State the concrete, checkable signal that defines "done ideally": a test exit code, a count, a file that exists and passes the audit, a named deliverable state. Present on every non-trivial task.
- **Goal-fit (on signal).** When the task has a verifiable end condition, propose it as a ready-to-paste `/goal <condition>` string (native, v2.1.139+; keeps working until an evaluator verifies the condition). The condition is the metric.
- **Loop-fit (on signal).** When the task is recurring or needs polling, propose `/loop [interval] <prompt>` (native, v2.1.72+; fixed or dynamic interval).

Surface Goal-fit and Loop-fit ONLY when the task language signals a fit. When neither helps, say so in one phrase instead of printing dead "no" lines.

## The metric is agreed, not asserted

- The operator supplies the criteria when they have them; Claude adopts them.
- When none are given, Claude proposes the metric it judges right, as part of the block, and asks by what criteria "done" is measured. The operator accepts, sharpens, or replaces it at the approval STOP.
- Subjective bars (voice, "good enough to send") are named as judgment calls or proxies, not dressed as hard metrics.
- **The metric is a floor, not the quality bar.** A `/goal` can verify while the output is still weak on voice or judgment. A passing metric never replaces the normal voice and humanisation pass before the work is called done.

## Block format

The final part of the Phase 1 expansion, before the approval STOP. Plain text, real newlines, no markdown tables.

Common path, no fit signaled:

    Measure:
    - Metrics: <how "done ideally" is checked> · /loop and /goal add nothing here

When a fit is signaled, surface only the relevant lines; both are proposals:

    Measure:
    - Goal: /goal <verifiable condition>   (proposed)
    - Loop: /loop 30m <prompt>             (proposed)
    - Metrics: <the criteria that decide the goal is met>

## Boundaries

- Advisory. It feeds the Phase 1 expansion, which already carries the approval STOP; nothing blocks. Skipping the block on a non-trivial task is a protocol miss, like skipping Phase 1.
- Proposing `/loop` or `/goal` is a proposal only. Claude never launches them autonomously; the human launches (see `lethal-trifecta.md`).
- For non-trivial code, the metric seeds the test contract written at step 3 of a Canopus slice, which the operator's approval commit at step 4 then carries; run the full chain `/create-plan` then `/canopus` then `/implement` there. Do not duplicate the planning gate's heavy critique here.

## Change control

Changes require the operator's explicit approval. Design history and council trail live in the workspace design-spec archive.
