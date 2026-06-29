---
name: publish-corporate
disable-model-invocation: true
description: "CEO-only: publish workspace content to all executives via the corporate repo. EXPLICIT INVOCATION ONLY - never auto-trigger."
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
argument-hint: "[description of what to publish]"
allowed-tools: "Read, Write, Edit, Bash(python3:*), Bash(git:*), Glob, Grep"
model: haiku
x-31c-orchestration:
  parallel_safe: false
  shared_state:
    - ../heading-os-corporate/
  triggers:
    - publish corporate
    - publish to executives
    - push to corporate
x-31c-capability:
  what: >
    CEO-only: copies corporate-classified content from the DATA overlay
    (.heading-os-data) to the ../heading-os-corporate/ repo and pushes, so execs
    pull it with plain `git pull` (git-native; no scheduled hourly sync).
  how: >
    Explicit invocation only - type /publish-corporate [what to publish]; never
    auto-triggers. It verifies admin role, runs a critical-leak scan, previews
    the file set for confirmation, then copies, commits, and pushes.
  when: >
    Use for targeted selective publishing to executives. For a full versioned
    push including BUILD.json and CRM aggregate use /push-updates; for a personal
    workspace backup use /backup.
---
# Publish Corporate Content

> CEO-only skill. Copies corporate-classified content from the DATA overlay (.heading-os-data) to the heading-os-corporate repo for distribution to all executives. Canonical mechanism: `python scripts/publish-corporate.py --preview|--copy|--verify` (reads the source from the data overlay, writes to ../heading-os-corporate/). Execs pull with plain `git pull` — there is no scheduled sync.

## Prerequisites

1. Read `.workspace-identity.json` - verify role is "admin". If not, say "This skill is CEO-only." and stop.
2. Verify the corporate repo exists at the parent directory: `../heading-os-corporate/`. If not, say "Corporate repo not found. Run initial setup first." and stop.

## Variables

- `$ARGUMENTS` - Description of what to publish (e.g., "Updated strategy for Q2", "New competitor-intel skill", "Updated a competing vendor competitive document")

## Workflow

### Step 1: Identify Files

Based on `$ARGUMENTS`, identify which files in ceo-main should be copied to the corporate repo.

**Classification-driven publishing:** classification resolves from `config/routing-map.yaml` (the single input; `classification.json` was removed in HEADING OS step 7). Post-cutover (step 8, 2026-06-14) publish ships ONLY files whose three-value routing destination is `corporate` — content, not code. Engine code is NOT published here; execs receive it by cloning the engine repo (`.heading-os`). The corporate set is content-only: `datastore/`, `knowledge/shared/`, the two `context/` carve-outs, `crm/` config/aliases/address-book, `corporate/` daemon config. Use `python scripts/classification-health.py --corporate-only` to list it.

If `$ARGUMENTS` references a specific file currently classified as `ceo-only` (routed `private`), warn the CEO and ask if they want to reclassify it before publishing (add a rule to `config/routing-map.yaml`).

**NEVER publish (safety check - overrides classification):**
- `context/personal-info.md` (CEO personal)
- `context/people.md` (CEO contacts)
- `crm/contacts/*` (CEO CRM data)
- `datastore/books/*` (CEO personal library)
- `knowledge/fleeting/*`, `knowledge/meetings/*`, `knowledge/people/*` (personal knowledge)
- `knowledge/technology/*osint-api-credentials*` (API keys)
- `.env` (secrets)
- `.workspace-identity.json` (per-workspace)
- `outputs/*` (CEO deliverables)
- `threads/` (entire directory) - operational thread registry, ceo-only on every machine
- `threads/personal/**` - explicitly listed even though covered by parent (defence in depth)
- Any script routed `private` in `config/routing-map.yaml` (CEO-personal tooling — e.g. `modem-tune.py`, the fireside/service-host scripts, the one-off CEO-instance scripts, plus anything added later). Single source of truth: `scripts.utils.workspace.get_ceo_only_scripts()`.

> **Note:** For full end-to-end push with BUILD.json versioning, use `/push-updates` instead. This skill is for targeted selective publishing.

### Step 2: Preview

Show the user a clear list:
```
Files to publish to all executives:
1. context/strategy.md (modified 2026-03-19)
2. .claude/skills/competitor-intel/SKILL.md (new)
...

This will be available to executives the next time they run `git pull` on their corporate clone (git-native; no scheduled task).
```

Ask for confirmation: "Publish these files? (yes/no)"

### Step 2.5: Critical-leak scan (safety gate)

Before copying, run the shared critical-leak scanner on every file in the publish set:

```
python scripts/sanitize-check.py <file1> <file2> ...
```

The scanner uses the primitives in `scripts/utils/sanitize.py`. It flags the narrow set of terms that must never land in corporate (vault codenames, ceo-only paths, private contact markers). On exit code 1, stop - surface the findings, ask the CEO to fix the source files, do not proceed to Step 3.

### Step 3: Copy & Commit

1. Copy each file from ceo-main to the corporate repo, preserving directory structure
2. `cd` to corporate repo directory
3. `git add -A`
4. `git diff --cached --stat` to show what changed
5. Commit with descriptive message: `"Publish: {description from arguments}"`
6. Push to origin main

### Step 4: Update VERSION

1. Read `VERSION` file in corporate repo
2. Increment PATCH version (e.g., 1.0.0 -> 1.0.1)
3. Write updated VERSION
4. Add entry to `CHANGELOG.md`:
   ```
   ## [1.0.1] - 2026-03-19
   - {description from arguments}
   - Files: {list of changed files}
   ```
5. Commit: `"Bump version to 1.0.1"`
6. Push

### Step 5: Confirm

"Published to the corporate repo. Executives will receive these updates the next time they run `git pull` on their corporate clone."

## Rules

- ALWAYS verify admin role before proceeding
- NEVER publish personal content (CRM contacts, personal knowledge, personal-info.md)
- Show preview and get confirmation before copying
- If the corporate repo doesn't exist yet, tell the user to run initial setup
- Use descriptive commit messages that executives can understand in CHANGELOG.md
