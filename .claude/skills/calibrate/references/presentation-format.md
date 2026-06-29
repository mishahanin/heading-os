# Calibrate - Phase 4 candidate presentation format

Consumed by: `.claude/skills/calibrate/SKILL.md` Phase 4.
Last Updated: 2026-05-15

Static display template and strict input grammar for the numbered candidate
presentation. The orchestration logic (sorting, grouping, auto-include,
classification routing) stays in SKILL.md - only the rendered display shape
and grammar table live here.

## Display format

```
/calibrate found {N} candidates from session {session_id}.
{light_mode_or_truncation_notice_if_any}

═══ MEMORY ({M} items, low blast radius) ═══
1. [{category}]  {one-line summary}
   → {target_path}
   {brief rationale (1 line)}

═══ SETTINGS ({S} items) ═══
{...}

═══ SKILLS ({K} items, ceo-only) ═══
{...}

═══ RULES ({R} items, ceo-only) ═══
{...}

═══ CORPORATE REVIEW QUEUE ({C} items, NOT auto-applied) ═══
   Will be written to outputs/operations/calibrate/{date}_corporate-review.md
   {list of corporate candidate titles only}

Options:
  apply all
  apply all except {comma-separated numbers}
  apply {comma-separated numbers}
  show diff {N}
  cancel
```

## Recognised input grammar

Strict - no fuzzy matching. Anything outside this table re-prompts with the
grammar reminder.

| Input | Action |
|---|---|
| `apply all` | apply every numbered item |
| `apply all except 3, 5` | apply all except those numbers |
| `apply 1, 3, 5` | apply only those numbers |
| `show diff 4` | display the proposed diff for item 4 inline, then loop back to this prompt |
| `cancel` | exit Phase 4 without applying, no file written, no commit |
| anything else | re-prompt with grammar reminder |
