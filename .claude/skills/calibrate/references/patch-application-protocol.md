# Calibrate - Patch-application protocol templates

Consumed by: `.claude/skills/calibrate/SKILL.md` Phase 5 (Steps 5.4 and 5.5).
Last Updated: 2026-05-15

Static templates for the atomic commit message and the final state report.
The orchestration around them (staging order, ceo-only vs corporate routing,
sanitisation, rollback policy) stays in SKILL.md.

## Stage + commit (Step 5.4 template)

```bash
git add .claude/ outputs/operations/calibrate/ <any-other-modified-workspace-files>
```

Memory files at `~/.claude/projects/.../memory/` are outside the workspace
tree - NOT staged. Settings file at `.claude/settings.local.json` is gitignored
- staged but git will not include it.

Compose the commit message:

```
chore(calibrate): apply {N} session-end calibrations

- [memory] {item summary}
- [memory] {item summary}
- [settings.local] {item summary}
- [skills/{name}] {item summary}
- [rules/{name}] {item summary}
- [skills/{name}] (NONE - routed to corporate review)
- [rules/{name}] (NONE - routed to corporate review)

Corporate review queue: {C} items in outputs/operations/calibrate/{date}_corporate-review.md
Session source: {session_id}
Light mode: {true|false}
{Hidden-character cleanup note if any}
```

Run: `git commit -m "<message>"`. If commit fails (pre-commit hook rejects):
report state, do not auto-rollback. User decides recovery.

## Final state report (Step 5.5 template)

Print:

```
Applied {N} patches:
  - {M} memory: {paths}
  - {S} settings.local: {properties}
  - {K} skills: {paths}
  - {R} rules: {paths}

Routed to corporate review ({C} items): outputs/operations/calibrate/{date}_corporate-review.md

Single atomic commit: chore(calibrate): apply {N} session-end calibrations (HEAD = {sha})

Rollback:
  - Workspace files: git revert HEAD
  - Memory files: edit manually at ~/.claude/projects/.../memory/
  - settings.local.json: edit manually (gitignored)
```
