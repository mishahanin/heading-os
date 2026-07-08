# Scrutinize — approval-block format & strict semantics

The exact Phase 3 approval-block layout and the strict approval-command semantics for the `/scrutinize` skill, moved out of `SKILL.md` to keep it under the size budget. Phase 0 loads this file so it is in context when Phase 3 produces the block.

Consumed by: `.claude/skills/scrutinize/SKILL.md`

Last Updated: 2026-07-08

## Format

Produce the approval block inline. Do NOT apply any change before user approval.

```text
## /scrutinize - <target-label>
Grade: <PASS | PASS-WITH-NOTES | NEEDS-REWORK | BLOCKED>
Target: <plan | execution | file:path | dir:path | workspace | trajectory:<run_id>>
Findings: N BLOCKER, N HIGH, N MEDIUM, N LOW, N NIT (above threshold)
Refutation: <2.5a-only | 2.5a+2.5b | skipped:<reason>>
Judge rotation: <rotate | fixed-claude | overridden>

### Findings (severity-sorted; workspace target: also grouped by area, then cross-area)

[B1] (conf: 92) <one-line statement>
  Location: <file:line | plan step N | area>
  Evidence: <quote / reference>
  Proposed fix: <concrete patch or rewrite>

[H1] (conf: 88) ... [M1] (conf: 78) ...

### Approval

Reply with one of:
- "approve all"           apply every proposed fix
- "approve <ids>"         e.g., "approve B1, H1, H3" (comma-separated IDs)
- "reject all"            produce no changes, end pass
- "revise <id>: <note>"   rework a specific fix with the note, re-present, re-ask
- "skip <ids>"            approve everything except the listed IDs
- "flag-as-fp <ids>"      mark findings as false positives (logged for FP-rate
                          calibration via scripts/scrutinize-flag-fp.py; can be
                          combined with approve/skip on different IDs in same reply)
```

If Grade is `BLOCKED`: also print exactly one line after the approval block: `"Forward progress halted pending approval."`

If there are no findings, print the header, the Grade line, and `No findings. No approval required.` - skip the Findings section and the Approval section entirely.

**Confidence threshold:** by default, only findings with confidence >= 75 appear in the approval block. Findings below threshold are logged in the saved report under a `## Findings Below Threshold` section. Pass `--include-low-confidence` to show all.

## Approval semantics (strict)

- Only explicit `approve` / `reject` / `revise` / `skip` / `flag-as-fp` commands act. Silence, ambiguity, or "looks good" - WAIT.
- `approve all` on a workspace target still applies changes sequentially, one area at a time, with a one-line per-area confirmation.
- `revise <id>: <note>` - rework that single finding's fix using the note, re-present just the revised fix, re-ask for approval on it. No revise-cycle limit.
- `skip <ids>` / partial `approve` - findings not named are marked `deferred` in the saved report. Deferred = not applied, not lost.
- `flag-as-fp <ids>` - calls `python scripts/scrutinize-flag-fp.py --scrutiny-id <stem> --ids <ids> --notes "<optional CEO note>"`. The CEO can combine this with other commands in the same reply (e.g. `"approve B1, flag-as-fp H2, skip M1"`).
