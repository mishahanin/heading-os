# Scrutinize — Saved Report Format

Consumed by: `.claude/skills/scrutinize/SKILL.md` (Phase 5 — Report Persistence).
Last Updated: 2026-06-16

Defines the persisted-report section layout and the `--relentless` consolidated-report
shape. The SKILL body holds the target→path table and slug derivation; this file holds
the section contracts so the body stays under the inline budget.

## Saved report sections (single-pass, v2.0)

Emit these sections in order in the saved Markdown report:

1. **Target + grade + findings summary** — counts by severity + counts by confidence band.
2. **Full findings list (above threshold)** — every finding with ID, severity, confidence,
   location, statement, evidence, proposed fix.
3. **Findings Below Threshold** (only if any exist) — findings with confidence < 75 hidden
   from the approval block by default.
4. **Refuted Findings** (Phase 2.5 drops) — findings dropped by 2.5a or 2.5b with the
   refutation reason and which judge family refuted them.
5. **Judge Layer** (when Phase 2.5 ran) — family used per phase, position-swap bits for
   Phase 2.5b.
6. **Approval outcome** per finding — `approved` | `rejected` | `revised: <note>` |
   `skipped` | `deferred` | `flagged-as-fp`.
7. **Post-apply check results** per edited file — pass/fail with detail.
8. **Failed checks** (if any) and recovery action taken.
9. **Eval-case promotions** (Phase 4.5 outcomes, single-pass only).
10. **Observability** — Langfuse trace ID and dashboard URL (or `_disabled_` reason).

The `Write` tool auto-creates `outputs/operations/scrutiny/` on first save.

## Consolidated report (`--relentless` mode)

One consolidated report at the target's standard path with:

- **Termination reason:** `two-zero` / `marginal-twice` / `hard-cap` / `check-failure` /
  `oscillation`.
- **Iteration count.**
- **Per-iteration summary table** — findings counts by severity + confidence band, fixes
  applied, fixes deferred.
- **Verbal memory ledger** — cumulative fix history per file, recurring findings,
  oscillation events.
- **Cumulative applied-fix log.**
- **Remaining open findings** — only if terminated on `hard-cap`, `check-failure`, or
  `oscillation`.
