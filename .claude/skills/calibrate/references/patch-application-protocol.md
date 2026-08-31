# Calibrate - Patch-application protocol templates

Consumed by: `.claude/skills/calibrate/SKILL.md` Phase 5 (Steps 5.4 and 5.5).
Last Updated: 2026-08-31

Static templates for the atomic commit message and the final state report.
The orchestration around them (the commit approval gate, staging order, ceo-only
vs corporate routing, sanitisation, rollback policy) stays in SKILL.md.

## Stage + commit (Step 5.4 template)

Step 5.4 asks for the commit separately from the Phase 4 patch approval. Do not
run either command below until that answer is an explicit yes.

Stage the files this run wrote, by name. No directory argument, no wildcard, no
`-A`, no `.` - any of those sweep in unrelated edits that are sitting in the
tree, which is what this template used to do:

```bash
git add -- <file-1> <file-2> <file-N>
```

Memory files under the canonical `auto-memory/` store live in the DATA
repository, a separate git repo - NOT staged. Settings file at
`.claude/settings.local.json` is gitignored - git will not include it.

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
  - Memory files: edit manually in the canonical auto-memory store
  - settings.local.json: edit manually (gitignored)
```
