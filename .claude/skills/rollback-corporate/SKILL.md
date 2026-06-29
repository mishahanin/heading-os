---
name: rollback-corporate
disable-model-invocation: true
description: "CEO-only: one-command rollback of the corporate `main` branch to the previous BUILD (R16 Layer 2). Forward-revert (no force-push) so GitHub branch protection is respected; execs pull the reverted state on next sync. EXPLICIT INVOCATION ONLY - never auto-trigger."
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
argument-hint: "[--dry-run]"
allowed-tools: "Read, Bash(python3:*), Bash(git:*)"
model: haiku
x-31c-orchestration:
  parallel_safe: false
  shared_state:
    - ../heading-os-corporate/
  triggers: []
x-31c-capability:
  what: >
    Rolls the corporate main branch back to the previous BUILD via a forward revert (no force-push),
    so branch protection holds and execs pull the reverted state on their next sync. The R16 Layer 2 safety net.
  how: >
    Run /rollback-corporate (CEO-only, explicit-invocation-only, never auto-triggers). Preview with
    --dry-run first; then runs git revert HEAD + push origin main via scripts/rollback-corporate.py.
  when: >
    Use only when a published corporate build broke something the canary missed. For a routine publish
    use /push-updates; for the promote-to-main gate use /promote-corporate.
---
# Rollback Corporate (main -> previous BUILD)

> CEO-only. The safety net of the two-stage propagation: if something broke that
> the canary's smoke + eval missed, revert `main` to the previous BUILD. The bad
> commit stays on `staging` for investigation, never re-propagated.

## Prerequisites

1. Read `.workspace-identity.json` - verify `role: admin`. If not, say "This skill is CEO-only." and stop.
2. Verify `../heading-os-corporate/` exists and is on the `main` branch.

## Workflow

### Phase 1 - Preview (read-only)

```bash
python scripts/rollback-corporate.py --dry-run
```

Shows the current build and the target (`HEAD~1`) build. The rollback refuses
(fail-closed) when `HEAD~1` still carries the current build -- that means the
latest publish landed as multiple commits and a simple HEAD revert would not
restore the previous build, so recover manually instead.

### Phase 2 - Execute (on CEO go-ahead)

```bash
python scripts/rollback-corporate.py
```

Does `git revert --no-edit HEAD && git push origin main` -- a FORWARD revert (not
a hard reset), so no history is rewritten and branch protection on `main` is
respected. Assumes the promote was `--ff-only` (so `HEAD~1` is the previous build).

### Phase 3 - Report

Relay the ROLLBACK COMPLETE line (reverted to build N-1, pushed origin/main).
Execs pull the reverted state on their next hourly sync. On revert conflict or
push failure, surface the error and stop.

## NEVER

- NEVER hard-reset + force-push `main` (breaks branch protection and exec `git pull --ff-only`).
- NEVER roll back when `HEAD~1` is not the previous build (the script refuses; respect it).
- Console-first: every step is a CLI invocation; no browser path required.
