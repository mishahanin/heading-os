---
name: promote-corporate
disable-model-invocation: true
description: "CEO-only: gated promotion of the corporate `staging` branch to `main` (R16 Layer 2). Runs the canary soak/freshness/smoke gates, then fast-forward merges staging->main so all execs receive the canary-validated build. EXPLICIT INVOCATION ONLY - never auto-trigger."
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
argument-hint: "[--force] [--dry-run]"
allowed-tools: "Read, Bash(python3:*), Bash(git:*)"
model: haiku
x-31c-orchestration:
  parallel_safe: false
  shared_state:
    - ../heading-os-corporate/
  triggers: []
x-31c-capability:
  what: >
    CEO-only R16 Layer 2 gate that fast-forward merges the corporate staging
    branch to main after the canary soak, freshness, and smoke gates pass, so
    every exec pulls the canary-validated build. Never bumps BUILD.json.
  how: >
    Explicit only: type /promote-corporate [--force] [--dry-run]; not
    auto-triggered. Runs scripts/promote-corporate.py, shows the gate report,
    confirms, then git checkout main and merge --ff-only and push.
  when: >
    Use to promote a soaked staging build to all execs. For a routine publish
    use /push-updates; to undo a bad build use /rollback-corporate.
---
# Promote Corporate (staging -> main)

> CEO-only. The Layer 2 promotion gate of the two-stage corporate propagation
> (design: `plans/2026-05-15-corporate-staging-branch-and-canary-exec.md`).
> Promotes the canary-soaked `staging` branch to `main`; all execs then pull the
> validated build on their next hourly sync.

## Prerequisites

1. Read `.workspace-identity.json` - verify `role: admin`. If not, say "This skill is CEO-only." and stop.
2. Verify `../heading-os-corporate/` exists and is a git repo.
3. An exec must be flagged `canary: true` in `config/exec-registry.json` (else there is nothing to gate against).

## Workflow

### Phase 1 - Run the gate (read-only)

```bash
python scripts/promote-corporate.py --dry-run
```

This prints the gate report without merging: canary slug, staging tip, soak hours
(need 4), and the per-gate pass/fail for **soak** (>=4h since the latest staging
commit; resets on every new commit), **canary freshness** (the canary pulled the
latest staging commit), **smoke** (`smoke_status == healthy`), and **eval status**
(advisory WARNING only -- never blocks). Present this report to the CEO verbatim.

### Phase 2 - Decision

- If all gates pass: ask "Promote staging -> main? [Y/N]".
- If any gate is blocking (`soak-incomplete`, `canary-stale`, `smoke-blocked`):
  do NOT promote unless the CEO explicitly chooses to force. Forcing requires
  typing the failing flag name to confirm, and is logged to `BUILD.json.history`.

### Phase 3 - Execute (on CEO go-ahead)

```bash
python scripts/promote-corporate.py            # gated promote (interactive confirm)
python scripts/promote-corporate.py --force     # bypass blocking gates (typed risk-ack)
```

The script does `git checkout main && git merge origin/staging --ff-only && git push origin main`.
The fast-forward preserves the staging `BUILD.json` verbatim -- **promote never bumps the build**.

### Phase 4 - Report

Relay the script's PROMOTION COMPLETE line (build number, merge, push) to the CEO.
Note that execs receive the update on their next hourly sync. On any merge/push
failure, surface the error and stop; never retry automatically.

## NEVER

- NEVER promote without showing the gate report first.
- NEVER bump BUILD.json here (staging's build is canonical; `--ff-only` preserves it).
- NEVER use rebase/squash merges (would break `/rollback-corporate`'s HEAD-revert semantics).
- NEVER force-promote without the CEO typing the failing-gate confirmation.
- Console-first: every step is a CLI invocation; no browser path required.
