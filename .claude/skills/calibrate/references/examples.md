# Calibrate - Worked examples

Consumed by: `.claude/skills/calibrate/SKILL.md` Examples section.
Last Updated: 2026-05-15

Two end-to-end examples showing the expected output shape. Use as visual
reference when implementing the skill; not part of execution logic.

## Example A - light mode with 2 candidates

User: `/calibrate light`

Response (after Phase 0 + Phase 1 light mode + Phase 2-3):

```
/calibrate found 2 candidates from session 2026-05-13-1623.
Light mode active. Categories 3 and 4 (repeated patterns, errors) skipped.

═══ MEMORY (2 items, low blast radius) ═══

1. [CORRECTION]  Save: skill files cannot use em-dashes
   -> ~/.claude/projects/.../memory/feedback_em_dash_rolling_clause.md
   You caught me using em-dashes in /devil and /burst SKILL.md today.
   The rule exists. Update the memory body so the next session avoids
   the same edit pass.

2. [PREFERENCE]  Save: full file reads on SKILL.md
   -> ~/.claude/projects/.../memory/reference_workspace_coding_style.md
   You asked for full file reads instead of partial reads when
   editing skill files. Worth saving so I default to that next time.

Options:
  apply all
  apply all except {comma-separated numbers}
  apply {comma-separated numbers}
  show diff {N}
  cancel
```

## Example B - clean session

User: `/calibrate light` after a short session of read-only lookups.

Response:

```
Session produced no actionable events. Nothing to calibrate.
```

That is the full output. No padding, no synthetic "areas for
improvement," no consolation patches.
