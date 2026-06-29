# Scrutinize — Version History

Consumed by: `.claude/skills/scrutinize/SKILL.md`.
Last Updated: 2026-06-16

Historical changelog for the skill. Kept out of the SKILL body so the operational
instructions stay under the inline budget; not read at runtime.

## v2.2 (2026-06-16)

Inline-budget refactor: moved the saved-report section contracts to
`references/report-format.md` and this changelog out of the SKILL body; compressed the
Phase 2.5 dispatch detail (full protocol already in `references/refutation-protocol.md`)
and the Phase 4.5 step detail (full rules already in `references/eval-case-template.md`).
No behavioural change.

## v2.1 (2026-05-27)

R12 from the meta-review (P3) — new target type `trajectory:<run_id>` that reads
`outputs/operations/implement/_trajectory_<run_id>.jsonl` (emitted by `/implement` v1.3+)
and runs a sequential-decision VIIA lens. Audits the path the agent took through the
work, not just the end-state. Deterministic tool-call records win over rationale prose
(gaming-the-judge mitigation per arXiv 2601.14691). Calibration debt is documented in
`references/trajectory-evaluation.md` and tracked in
`threads/business/2026-05-27-r12-calibration-debt-clearance.md`.

## v2.0 (2026-05-27)

11 vectors landed from the meta-review. Phase 2.5 (refutation + two-agent debate)
inserted between Identify and Approval. Per-finding confidence scoring (0-100) with
default threshold 75. Cross-family judge rotation (Claude / Gemini 3.5 Flash / Grok 4.3)
for bias mitigation. Phase 4 gains `flag-as-fp` approval command + FP aggregator.
Phase 4.5 gains auto-scaffold for `evals/cases/` and broadens to scripts + rules.
`--relentless` gains adaptive termination + verbal memory ledger. Phase 0 + 5 emit
Langfuse trace telemetry. `scripts/scrutinize-replay.py` builds the human-agreement
scoring sheet.
