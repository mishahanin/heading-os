---
name: sync
disable-model-invocation: true
description: "Manually trigger workspace sync - git pull of the code/data clones, then a push-all backup. EXPLICIT INVOCATION ONLY - never auto-trigger."
argument-hint: "[pull|backup]"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "2.0"
allowed-tools: "Bash(python3:*), Bash(git:*), Read"
model: haiku
x-31c-orchestration:
  parallel_safe: false
  shared_state: []
  triggers:
    - sync
    - full corp sync
    - pull updates
x-31c-capability:
  what: >
    Manually syncs the workspace with plain git: pulls the latest code (and data)
    with `git pull --ff-only`, then backs up via `scripts/push-all.py`. This
    replaced the retired workspace-sync.py copy-and-orphan-delete engine.
  how: >
    Run /sync (explicit-invocation-only, never auto-triggers) to pull then back up,
    or scope it with /sync pull | backup. pull = git pull --ff-only on the engine
    and data clones; backup = push-all.py.
  when: >
    Use to pull updates or back up between sessions. To publish corporate changes
    out to all execs use /push-updates; for a backup alone use /backup (push-all).
---
# Workspace Sync

Manually sync the workspace using plain git. This replaced the retired
`workspace-sync.py` mechanism (see
`plans/2026-06-26-retire-workspace-sync-disk-import.md`): there is no longer a
copy-and-orphan-delete engine, no scheduled sync task, and no risk of the old
"delete the engine tree" failure. Sync is now two simple, non-destructive
operations.

## What Gets Synced

1. **Pull (code + data)** - `git pull --ff-only` on the engine clone (`.heading-os`)
   and the data overlay (`.heading-os-data`). Fast-forward only, so a divergent
   local history surfaces as a plain error instead of an implicit merge.
1b. **Corporate content (execs)** - `python scripts/sync-corporate.py` refreshes the
   gitignored `.corporate-repo/` clone of `heading-os-corporate`, read in place via
   `get_corporate_root()`. It self-no-ops on the CEO workspace, so it runs
   unconditionally in the pull sequence (no skill-level branch).
2. **Backup (push)** - `python scripts/push-all.py` commits and pushes BOTH repos
   to their private `origin/main` with the pre-push secret scan and `[0 0]`
   ahead/behind verification.

> First-run record recovery after a clean deploy is a separate one-shot:
> `python scripts/import-legacy-records.py --from <old-records-path>` (local,
> non-destructive). It is NOT part of /sync.

## Options

- `/sync` - pull, then backup
- `/sync pull` - `git pull --ff-only` on the engine and data clones only
- `/sync backup` - `python scripts/push-all.py` only

## Steps

1. Resolve the engine root (the workspace you launched from) and the data root
   (the `.heading-os-data` sibling, or `HEADING_OS_DATA` if set).
2. For **pull**: run `git -C <engine-root> pull --ff-only` and
   `git -C <data-root> pull --ff-only`, then `python scripts/sync-corporate.py`
   (refreshes `.corporate-repo/` on execs; no-ops on the CEO workspace). Show each
   result.
3. For **backup**: run `python scripts/push-all.py` and show its output.
4. For bare `/sync`: pull first, then backup.
5. If a step fails, explain plainly and suggest a fix:
   - `git pull` non-fast-forward: "Local history diverged - reconcile manually
     (`git -C <root> status`) before syncing; /sync will not force or merge."
   - push-all secret-scan refusal: "A tracked file looks like it holds a secret -
     move it to `.env` and retry; never pass `--no-verify`."
   - Git auth: "GH_TOKEN missing or expired - check the engine `.env`."

## Never

- Never delete files as part of sync. There is no orphan-deletion step anymore.
- Never pass `--no-verify` or force-push to work around a failed gate.
