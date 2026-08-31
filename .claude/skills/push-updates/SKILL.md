---
name: push-updates
disable-model-invocation: true
description: "CEO-only: Publish workspace updates for all executives. Single command that commits the engine clone and the data overlay, classifies, publishes corporate-routed content to the corporate repo, bumps BUILD.json, pushes both repos through scripts/push-all.py, refreshes the operator's own CRM aggregate, and reports. Each executive then pulls; this command drives no exec workspace. EXPLICIT INVOCATION ONLY - never auto-trigger."
argument-hint: "[optional summary of changes]"
allowed-tools: "Read, Write, Edit, Bash(python3:*), Bash(git:*), Glob, Grep"
model: haiku
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.2"
x-heading-orchestration:
  parallel_safe: false
  shared_state:
    - ../heading-os-corporate/
    - crm/
    - config/
  triggers:
    - push updates
    - update all executives
    - sync to everyone
x-heading-capability:
  what: >
    The one CEO command that ships workspace changes to every executive. Phase 1
    commits the engine clone and the data overlay, then Phase 2 classifies and
    publishes corporate-routed files to ../heading-os-corporate/ via
    publish-corporate.py. Phase 4 pushes the engine clone and the data overlay
    through scripts/push-all.py, then refreshes the operator's own CRM aggregate
    under <data-root>/crm/aggregated/. Each exec then pulls; there is no central
    sync driver and this command touches no exec workspace.
  how: >
    CEO-only, explicit invocation only - type /push-updates [summary]. Verifies
    admin role, shows a publish preview, and waits for explicit confirmation before
    publishing and bumping the build.
  when: >
    Use to propagate shared updates to the whole fleet. For a plain GitHub backup
    of the engine clone and the data overlay, with no corporate publish, use
    /backup; to publish corporate files without the build bump and the CRM
    aggregate tail, use /publish-corporate.
x-heading-routing:
  category: Operations
  triggers:
    - NEVER auto-trigger. Explicit `/push-updates` only.
  exclusions:
    - Personal backup -> /backup
  compound: 'No'
  router: manual
---
# Push Updates to All Executives

> CEO-only skill. Single command to publish all workspace changes to all executives via the corporate repo.

## Prerequisites

1. Read `.workspace-identity.json` - verify role is "admin". If not: "This skill is CEO-only." Stop.
2. Verify corporate repo exists at `../heading-os-corporate/`. If not: "Corporate repo not found. Run initial setup first." Stop.

## Variables

- `$ARGUMENTS` - Optional summary of changes (used in BUILD.json and CHANGELOG)

## Workflow

**Read `references/workflow.md` before you run each phase below.** It holds the
preview format, the BUILD.json and CHANGELOG templates, and the report format.
It also holds the v1.2 script mandate and the R16 staged rollout. The phases
below carry every command line and every approval gate.

**Working directory: run every command from the engine clone, which is the
workspace root.** No step changes it. A command that acts on another repository
names that repository with `git -C <path>`, so no phase inherits a directory
another phase moved to.

### Phase 0: Pre-flight

1. Run `python scripts/classification-health.py` - report classification stats
2. Check for unclassified files: `python scripts/classification-health.py --unclassified`
   - If any found: prompt CEO for classification of each file
   - Add a rule to `config/routing-map.yaml` for any newly classified files
3. Check both trees for uncommitted changes:

   ```bash
   git status --short
   git -C ../.heading-os-data status --short
   ```

4. If uncommitted changes exist, show a summary and ask: "Commit these changes before pushing? (yes/no)"
5. **Routing-regression gate (soft).** Run:

   ```bash
   python scripts/skill-trigger-test.py --changed --strict --threshold 0.85
   ```

   Handle the exit code:
   - **0** - proceed (no routing change, or all changed skills route correctly).
   - **1** - below threshold, OR a changed skill the judge never returned a verdict for. Read which. `MISS` lines are routing regressions. `NO VERDICT` and `Unmeasured` lines mean the judge failed, not the router. Re-run in that case, rather than redrawing a trigger. Surface either to the CEO and ask for an explicit "proceed anyway" before continuing. Do NOT auto-block - this is a soft gate.
   - **3** - no `ANTHROPIC_API_KEY`. Print a one-line warning that the routing check was skipped and proceed (never block publish on a missing key).
   - **2** - setup error. Surface it and pause.

### Phase 1: Commit the engine clone and the data overlay

1. Stage the relevant files in BOTH trees (respect .gitignore). The engine clone
   carries code, rules, skills and config. The data overlay carries content.
   - **DO NOT stage:** `.env`, `.workspace-identity.json`, `.sync/`, `.sentinel/`, `__pycache__/`
2. Commit each tree with message: "Workspace update: {summary from $ARGUMENTS or auto-generated}"
3. Check if any knowledge files are classified as corporate (via a corporate rule in `config/routing-map.yaml`)
   - If yes: run `python scripts/promote-knowledge.py --note "{path}" --type "{type}"` for each
   - Commit promotions: "Promote knowledge to shared for corporate distribution"

### Phase 2: Publish to Corporate

1. **Preview** what would be copied (no changes):

   ```bash
   python scripts/publish-corporate.py --preview
   ```

2. **Show preview to CEO** in the standard format. That format is in
   `references/workflow.md`, Phase 2, step 2.

3. **Get explicit CEO confirmation.** Do not continue without it.

4. **Copy + verify** atomically with the script:

   ```bash
   python scripts/publish-corporate.py --copy
   ```

5. **NEVER hand-type the file list** or write ad-hoc Python inline. Use the script as the single source of truth. If the script's classification logic is wrong for a specific case, add a rule to `config/routing-map.yaml`. Never work around the script.

### Phase 3: Build & Release

1. Read `../heading-os-corporate/BUILD.json` (create if missing, starting at build 1)
2. Increment build number
3. Determine version bump:
   - **PATCH** (x.x.+1): Content updates only (modified context, reference, knowledge)
   - **MINOR** (x.+1.0): New skills, rules, scripts, or structural changes
   - Suggest the appropriate bump and confirm with CEO
4. Write `BUILD.json` from the template in `references/workflow.md`, Phase 3, step 4
5. Update `VERSION` file with new version string (backward compatibility)
6. Update `CHANGELOG.md` from the template in `references/workflow.md`, Phase 3, step 6
7. **Final verify before the corporate commit** (v1.2 gate):

   ```bash
   python scripts/publish-corporate.py --verify
   ```

   Exit 0 = all clean. Exit 7 = mismatches detected (list printed). If the verify fails, halt before the corporate commit, surface the mismatched files to the CEO, and fix before proceeding.

8. Commit and push the corporate repo. Each command names the repository with
   `git -C`, so the working directory stays on the engine clone:

   ```bash
   git -C ../heading-os-corporate add -A
   git -C ../heading-os-corporate commit -m "Release v{version} (build {build}): {summary}"
   git -C ../heading-os-corporate push origin main
   ```

   The corporate repo is a private third repository. It is outside the engine
   leak wall, so `push-all.py` neither covers it nor should be pointed at it.

### Phase 4: Ancillary

1. Push the engine clone and the data overlay through the supervised primitive:

   ```bash
   python scripts/push-all.py -m "Workspace update: {summary from $ARGUMENTS}"
   ```

   **NEVER hand-run a bare `git push` on the engine clone.** The engine repo is
   public. `push-all.py` is the only path that carries its unbypassable walls.
   Those walls are `engine_clean_scan`, the secret content scan, and the
   ahead/behind `[0 0]` verification. A hand-run push reaches none of them, which
   `docs/engine-data-segregation-contract.md` names as THE bypass of layer 6.

   One run covers both repos, so `crm/contacts/` rides the data overlay here and
   needs no push of its own. Exit `3` is a skip, not a failure: read its headline
   (`Partial: N of M` vs `NOTHING PUSHED: all M`) and report that shape per
   `/backup` SKILL.md.

2. Refresh the operator's own CRM aggregate at `<data-root>/crm/aggregated/`.
   It reads each exec's own data overlay, and its output is a local derived view
   with no remote:
   ```bash
   python scripts/aggregate-crm.py
   ```
3. Executive workspaces: there is NO central CEO-driven driver. Each exec pulls
   engine code with a plain `git pull` and refreshes corporate content with
   `python scripts/sync-corporate.py`. Detail and history:
   `references/workflow.md`, Phase 4, step 3.

### Phase 5: Report

Present a summary in the format in `references/workflow.md`, Phase 5.

## Rules

- ALWAYS verify admin role before proceeding
- ALWAYS show preview and get confirmation before publishing
- ALWAYS use `scripts/publish-corporate.py --copy` for the file-copy step (v1.2 mandate)
- ALWAYS run `scripts/publish-corporate.py --verify` immediately before the corporate commit (v1.2 gate)
- NEVER hand-type the list of files to copy to the corporate repo; the classification logic is the source of truth, not human memory
- NEVER write ad-hoc Python inline to do the copy step; always go through the script
- NEVER hand-run `git push` on the engine clone or the data overlay. `scripts/push-all.py` is the only sanctioned path, and it carries the unbypassable leak wall
- NEVER tell an executive workspace to do anything from here; this command drives no exec machine
- NEVER publish files classified as ceo-only
- NEVER publish `.env`, `.workspace-identity.json`, `crm/contacts/`, `context/personal-info.md`, `context/people.md`, `datastore/books/*`, `datastore/investment/ceo-only/*`, `threads/`, `threads/personal/**`
- Use descriptive commit messages that executives can understand
- If BUILD.json doesn't exist yet, create it with build: 1
- The `build` number always increments by 1 (never decrements, never skips)
- If no changes are detected (all corporate files identical), report "Nothing to push - corporate repo is up to date"
