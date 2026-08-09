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

---

## The structured run record

Every judged finding also leaves a row in `outputs/operations/scrutiny/runs.jsonl`,
written by `scripts/utils/scrutinize_record.py` rather than by the model. The
report stays the human artifact; the record is what anything downstream can count.

```
{"run_id", "ts", "target",
 "kind": "pass_start|verdict|reproduction|role|currency|fp_flag|degraded",
 "finding_id", "pass": "2.5a|2.5b|null", "judge_family": "claude|kimi|null",
 "verdict": "REFUTED|REFUTE_PARTIAL|REFUTATION_FAILED|CORRECT|CORRECT_DOWNGRADE|
             INCORRECT|AMBIGUOUS|REPRODUCED|FALSIFIED|null",
 "confidence_before", "confidence_after",
 "reproduction": null | {"cmd", "exit_before", "exit_after"},
 "role": "ops|scheduler|boundary|null",
 "currency": null | {"import", "distribution", "pinned", "latest",
                     "result": "ok|mismatch|inconclusive"},
 "degraded": null | "<cause>", "writer": "dispatch|flag-fp"}
```

**Do not assemble this report from the rows and then validate it against them.**
That tests generation, not compliance: a report generated from rows agrees with
them by construction. The non-circular signal is the `Refutation:` header the
approval block already mandates, written for a human, reconciled against the row
count by `scripts/scrutinize-record.py --validate`. Three things fail it: a run
with no `pass_start` row, a header claiming a pass over more judged findings than
there are verdict rows, and a header declaring a skip with no `degraded` row
naming its cause.

Why the header and not the prose: measured 2026-08-09 across 75 saved reports,
the mandated `Refutation:` line appears in 8 of them and the mandated
`## Judge layer` heading in 12. Prose mandates did not survive contact with 75
runs, which is the entire reason the record exists.
