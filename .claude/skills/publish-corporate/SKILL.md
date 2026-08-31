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
x-heading-orchestration:
  parallel_safe: false
  shared_state:
    - ../heading-os-corporate/
  triggers:
    - publish corporate
    - publish to executives
    - push to corporate
x-heading-capability:
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
x-heading-routing:
  category: Operations
  triggers:
    - NEVER auto-trigger. Explicit `/publish-corporate` only.
  exclusions:
    - Full push with CRM -> /push-updates
  compound: 'No'
  router: manual
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

Read `$ARGUMENTS` and identify which files in the DATA overlay (`.heading-os-data`) belong in the corporate repo. The overlay is the publish source: `publish-corporate.py` sets `SOURCE_ROOT = get_data_root()`, and every corporate-classified path lives there rather than in the engine clone.

**Classification-driven publishing:** classification resolves from `config/routing-map.yaml` (the single input; `classification.json` was removed in HEADING OS step 7). Post-cutover (step 8, 2026-06-14) publish ships ONLY files whose three-value routing destination is `corporate` — content, not code. Engine code is NOT published here; execs receive it by cloning the engine repo (`.heading-os`). The corporate set is content-only: `datastore/`, `knowledge/shared/`, the two `context/` carve-outs, `crm/` config/aliases/address-book, `corporate/` daemon config. Use `python scripts/classification-health.py --corporate-only` to list it.

A file named in `$ARGUMENTS` may be classified `ceo-only` today, which routes `private`. Warn the CEO, and ask whether to reclassify it before publishing by adding a rule to `config/routing-map.yaml`.

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

Run the canonical script in preview mode. It changes nothing:

```bash
python scripts/publish-corporate.py --preview
```

The script derives the file set from `config/routing-map.yaml` and compares the data overlay against the corporate repo. It prints NEW, MODIFIED, UNCHANGED, and MISSING IN SOURCE.

Show that output to the CEO, then add this line:

```
This will be available to executives the next time they run `git pull` on their corporate clone (git-native; no scheduled task).
```

Ask for confirmation: "Publish these files? (yes/no)"

> **`$ARGUMENTS` does not filter the file set.** The script has no per-file selection flag. `--copy` publishes every changed corporate-classified file, so "targeted" here means a targeted intent and commit message, never a targeted subset. Read the preview and confirm it holds what you expected. To exclude a file, change its rule in `config/routing-map.yaml` instead.

### Step 2.5: Critical-leak scan (safety gate)

Before copying, run the shared critical-leak scanner on every file in the publish set:

```
python scripts/sanitize-check.py <file1> <file2> ...
```

The scanner uses the primitives in `scripts/utils/sanitize.py`. It flags the narrow set of terms that must never land in corporate (vault codenames, ceo-only paths, private contact markers). On exit code 1, stop - surface the findings, ask the CEO to fix the source files, do not proceed to Step 3.

### Step 3: Copy and verify

Never hand-type the list of files to copy, and never write ad-hoc Python inline to do it. A hand-typed list shipped the functionally broken build 77 on 2026-05-27. Run the script:

```bash
python scripts/publish-corporate.py --copy
```

`--copy` re-derives the same set `--preview` showed, copies each file into `../heading-os-corporate/`, and then re-compares every copy byte-for-byte. Halt on any non-zero exit and report it.

| Exit | Meaning | What to do |
|---|---|---|
| 0 | Copied and verified. | Continue to step 4. |
| 3 | `.workspace-identity.json` role is not `admin`. | Stop. This skill is CEO-only. |
| 4 | Corporate repo missing, or not a git repo. | Stop. Tell the CEO to run initial setup. |
| 6 | Filesystem error during the copy. | Stop. Report the named file. |
| 7 | Post-copy verify found a mismatch. | Stop. Report the named files. |
| 8 | Corporate `.gitattributes` lacks `* text=auto`. | Stop. Fix that file first, then re-run. |
| 9 | Untracked corporate-classified files in the data overlay. | Commit them in the overlay, then re-run. |

### Step 4: Commit and push the corporate repo

The script copies files. It stages nothing, commits nothing, and pushes nothing. Do that here.

Every command below names its repository with `git -C`. Stay on the engine clone and never `cd` into another tree, because a later command would then run in the wrong repository.

1. Re-verify immediately before the commit:

   ```bash
   python scripts/publish-corporate.py --verify
   ```

   Exit 0 means clean. Exit 7 means a file differs, or never reached the corporate repo. Halt on exit 7 and surface the named files.

2. Stage the copied files:

   ```bash
   git -C ../heading-os-corporate add -A
   ```

3. Show the CEO what changed:

   ```bash
   git -C ../heading-os-corporate diff --cached --stat
   ```

4. Commit with a message an executive can read:

   ```bash
   git -C ../heading-os-corporate commit -m "Publish: {description from $ARGUMENTS}"
   ```

5. Push:

   ```bash
   git -C ../heading-os-corporate push origin main
   ```

   This push is correct here, and this is the only repository it is correct for. The corporate repo is a private third repository outside the engine leak wall. `scripts/push-all.py` and `scripts/safe-push.py` both cover the engine clone and the data overlay only, so neither covers this one. Never point either script at the corporate repo, and never hand-run `git push` on the engine clone.

### Step 5: Versioning is out of scope here

This skill writes no version marker, and it must not invent one. Measured on 2026-08-30, the corporate repo carries no `VERSION` file, no `CHANGELOG.md`, and no `BUILD.json`. Revisions of this skill before that date told you to increment a `VERSION` file there. They also told you to append to a `CHANGELOG.md` there. Neither file has ever existed in that repo, so this revision drops both steps.

Build numbering belongs to `/push-updates`, which runs `python scripts/publish-corporate.py --bump-build` and writes `BUILD.json`. `scripts/check-build.py` reads that number to show per-exec sync drift. Use `/push-updates` when the CEO wants a numbered release.

### Step 6: Confirm

"Published to the corporate repo. Executives will receive these updates the next time they run `git pull` on their corporate clone."

## Rules

- ALWAYS verify admin role before proceeding
- NEVER publish personal content (CRM contacts, personal knowledge, personal-info.md)
- Show preview and get confirmation before copying
- If the corporate repo doesn't exist yet, tell the user to run initial setup
- ALWAYS drive the copy through `scripts/publish-corporate.py --copy`
- NEVER hand-type the list of files to copy; `config/routing-map.yaml` is the source of truth, not human memory
- NEVER write ad-hoc Python inline to do the copy step
- ALWAYS run `scripts/publish-corporate.py --verify` immediately before the corporate commit
- NEVER `cd` into another repository; name it with `git -C` instead
- Use descriptive commit messages that executives can understand

## Two names this skill used to get wrong

Kept as prose, because a reader who finds the old wording elsewhere needs to know what it meant.

**`ceo-main`.** Two steps named it as the live copy source. That single workspace was retired at the 2026-06-15 two-part cutover, it is absent from disk, and the operator's standing instruction forbids writing to it. Both occurrences meant the same tree, and it is the DATA overlay (`.heading-os-data`), never the engine clone. Corporate-classified content is content, so it lives in the overlay.

**A hand-rolled copy loop.** Steps 3 and 4 described copying each file by hand, then `git add -A`, commit, and push, and they named `scripts/publish-corporate.py` nowhere. The script above is the same mechanism `/push-updates` already mandates. The reason is also the same. On 2026-05-27 a hand-typed list missed one helper file and shipped a functionally broken build 77.
