# /push-updates - full workflow detail

Consumed by: `.claude/skills/push-updates/SKILL.md`, section "Workflow". Holds
the complete phase-by-phase procedure and the preview and report output formats.
It also holds the BUILD.json and CHANGELOG templates, the v1.2 script mandate
with its history, and the R16 Layer 2 staged-rollout note. Read the phase you
are about to run, before you run it.

The command lines and the approval gates are duplicated in SKILL.md on purpose.
SKILL.md is authoritative for those. This file is authoritative for the formats
and the reasoning.

Last Updated: 2026-08-30

## Which tree is which

Three repositories are in play and this document used to name a fourth that no
longer exists.

- **Engine clone** (`.heading-os`, the workspace root). Code only, PUBLIC. Every
  command in this workflow runs from here.
- **Data overlay** (`../.heading-os-data`). All content, private forever. It is
  the publish SOURCE: `publish-corporate.py` sets `SOURCE_ROOT = get_data_root()`,
  so the corporate-routed files it enumerates, copies and verifies live here.
- **Corporate repo** (`../heading-os-corporate`). The publish DESTINATION, shared
  down to executives.

Until 2026-08-30 six lines of this workflow and four of the SKILL.md named
`ceo-main`, the single pre-cutover workspace retired at the 2026-06-15 two-part
cutover. It is absent from disk and must not be written to, so instructions like
"run `git status` in ceo-main" could not be carried out at all. The occurrences
did not all resolve to the same tree, which is why the fix was not a rename: the
Phase 0 check, the Phase 1 commit and the Phase 4 push mean both writable trees,
while the orphan and verify notes in Phases 2 and 3 mean the data overlay alone.

Two other rules follow from the list above. **No step changes the working
directory**; a command acting on another repository names it with `git -C
<path>`. And **no step hand-runs `git push` on the engine clone or the data
overlay**: that is `scripts/push-all.py`, for the reason Phase 4 gives.

---

### Phase 0: Pre-flight

1. Run `python scripts/classification-health.py` - report classification stats
2. Check for unclassified files: `python scripts/classification-health.py --unclassified`
   - If any found: prompt CEO for classification of each file
   - Add a rule to `config/routing-map.yaml` for any newly classified files
3. Check both writable trees for uncommitted changes:
   ```bash
   git status --short
   git -C ../.heading-os-data status --short
   ```
4. If uncommitted changes exist, show a summary and ask: "Commit these changes before pushing? (yes/no)"
5. **Routing-regression gate (soft).** Run:
   ```bash
   python scripts/skill-trigger-test.py --changed --strict --threshold 0.85
   ```
   This LLM-judge tests only the skills whose `SKILL.md`/`triggers.json` changed since `origin/main` (a `skill-router.md` change widens to all). Handle the exit code:
   - **0** - proceed (no routing change, or all changed skills route correctly).
   - **1** - below threshold, OR a changed skill the judge never returned a verdict for. Read which. `MISS` lines are routing regressions. `NO VERDICT` and `Unmeasured` lines mean the judge failed, not the router. Re-run in that case, rather than redrawing a trigger. Surface either to the CEO and ask for an explicit "proceed anyway" before continuing. Do NOT auto-block - this is a soft gate.
   - **3** - no `ANTHROPIC_API_KEY`. Print a one-line warning that the routing check was skipped and proceed (never block publish on a missing key).
   - **2** - setup error. Surface it and pause.

   > Soft gate (advisory + CEO override) per audit #63-2. The judge is non-deterministic; promote to a hard block only once its false-positive rate is characterized over several weeks of soft runs.

### Phase 1: Commit the engine clone and the data overlay

1. Stage the relevant files in BOTH trees (respect .gitignore): the engine clone
   for code, rules, skills and config; the data overlay for content.
   - **DO NOT stage:** `.env`, `.workspace-identity.json`, `.sync/`, `.sentinel/`, `__pycache__/`
2. Commit each tree with message: "Workspace update: {summary from $ARGUMENTS or auto-generated}"
3. Check if any knowledge files are classified as corporate (via a corporate rule in `config/routing-map.yaml`)
   - If yes: run `python scripts/promote-knowledge.py --note "{path}" --type "{type}"` for each
   - Commit promotions: "Promote knowledge to shared for corporate distribution"

### Phase 2: Publish to Corporate

**v1.2 (2026-05-27):** The file-copy step is now mandated to go through `scripts/publish-corporate.py`. Hand-typed file lists are forbidden - build 77 shipped a functionally broken release because the hand-typed list missed `scripts/implement-trajectory-log.py`. The script derives the canonical "files to publish" set from `config/routing-map.yaml` + git-tracked files vs corporate-repo content.

1. **Preview** what would be copied (no changes):

   ```bash
   python scripts/publish-corporate.py --preview
   ```

   The script enumerates all git-tracked workspace files and resolves each per `config/routing-map.yaml`, where the most-specific rule wins, else the `engine` default. It publishes ONLY files whose three-value routing destination is `corporate` (content, not code — post-cutover, step 8, 2026-06-14). Engine code is NOT published; execs receive it by cloning the engine repo (`.heading-os`). It groups the corporate-routed files into NEW / MODIFIED / UNCHANGED / MISSING-IN-SOURCE buckets. Untracked corporate-routed files trigger a hard warning.

2. **Show preview to CEO** in the standard format:

   ```text
   Push Preview (v{next_version}, build {next_build}):

   NEW FILES ({count}):
     <list from --preview output>

   MODIFIED FILES ({count}):
     <list from --preview output>

   SYSTEM COMPONENTS:
     {count} skills, {count} rules, {count} scripts, {count} hooks

   Publish to all executives? (yes/no)
   ```

3. **Get explicit CEO confirmation.**

4. **Copy + verify** atomically with the script:

   ```bash
   python scripts/publish-corporate.py --copy
   ```

   The script:
   - Refuses to proceed if untracked corporate-classified files exist (exit 6) - commit or .gitignore first.
   - Copies every NEW + MODIFIED corporate file via `shutil.copy2` preserving metadata.
   - Runs a post-copy `filecmp.cmp` verify on every copied file.
   - Exits non-zero with diagnostic on any mismatch (exit 7).
   - Surfaces orphan files (corporate-classified files missing from the data overlay, which is the publish source) as a warning - never auto-deletes from corporate.

5. **NEVER hand-type the file list** or write ad-hoc Python inline. Use the script as the single source of truth. If the script's classification logic is wrong for a specific case, add a rule to `config/routing-map.yaml`. Never work around the script.

> **Publish targets `main` directly. There is one stage, not two.** A
> staging-branch-plus-canary rollout was written in 2026-05 and removed on
> 2026-08-23: measured, it had no entry point (publish never wrote to
> `staging`), no scheduled smoke run to open its gate, and no live canary
> install, against a repo published three times in three months. The rationale
> is in `docs/EXTENDING.md`; the code is recoverable from git history.
> `scripts/publish-corporate.py --bump-build` still increments BUILD.json when
> you want a build number, and it stays opt-in.

### Phase 3: Build & Release

1. Read `../heading-os-corporate/BUILD.json` (create if missing, starting at build 1)
2. Increment build number
3. Determine version bump:
   - **PATCH** (x.x.+1): Content updates only (modified context, reference, knowledge)
   - **MINOR** (x.+1.0): New skills, rules, scripts, or structural changes
   - Suggest the appropriate bump and confirm with CEO
4. Write `BUILD.json`:
   ```json
   {
     "version": "{new_version}",
     "build": {new_build},
     "timestamp": "{ISO 8601 in the configured local timezone}",
     "publisher": "misha-hanin",
     "summary": "{from $ARGUMENTS or auto-generated}",
     "files_changed": {count}
   }
   ```
5. Update `VERSION` file with new version string (backward compatibility)
6. Update `CHANGELOG.md`:
   ```
   ## [{version}] - {YYYY-MM-DD}
   - {summary}
   - Files: {count} new, {count} modified
   - Build: {build_number}
   ```
7. **Final verify before the corporate commit** (v1.2 gate):

   ```bash
   python scripts/publish-corporate.py --verify
   ```

   This re-runs `filecmp.cmp` between every git-tracked corporate-classified file in the data overlay and its corporate-repo counterpart. Exit 0 = all clean. Exit 7 = mismatches detected (list printed). If the verify fails, halt before the corporate commit, surface the mismatched files to the CEO, and fix before proceeding.

8. Commit and push the corporate repo. Each command names the repository with
   `git -C`, so the working directory stays on the engine clone and Phase 4
   inherits no directory this phase moved to:

   ```bash
   git -C ../heading-os-corporate add -A
   git -C ../heading-os-corporate commit -m "Release v{version} (build {build}): {summary}"
   git -C ../heading-os-corporate push origin main
   ```

   The corporate repo is a private third repository, outside the engine leak
   wall, so `push-all.py` neither covers it nor should be pointed at it. Until
   2026-08-30 this step read "In the corporate repo:" with no `git -C` and no
   `cd`, and Phase 4 step 1 below then ran a bare `git push` with the corporate
   repo as the plausible working directory. `/publish-corporate` step 3 has
   always carried an explicit `cd`; this workflow did not.

### Phase 4: Ancillary

1. Push the engine clone and the data overlay through the supervised primitive:

   ```bash
   python scripts/push-all.py -m "Workspace update: {summary from $ARGUMENTS}"
   ```

   **NEVER hand-run a bare `git push` on the engine clone.** The engine repo is
   public and `push-all.py` is the only path that carries its unbypassable
   walls: `engine_clean_scan()` refuses the push when any file in the engine
   clone routes private or corporate, `content_scan()` reads the working tree
   AND the unpushed commits for secrets, `engine_content_scan()` reads them for
   real-entity tokens, and `supervised_push()` verifies ahead/behind `[0 0]`
   rather than trusting an exit code. None of them has a skip flag, and a
   hand-run `git push` reaches none of them. The layer-6 row of
   `docs/engine-data-segregation-contract.md` names exactly this: "Someone
   hand-running `git push` outside `push-all` bypasses it." That was the
   instruction this file carried from its first version until 2026-08-30.

   `scripts/safe-push.py --repo engine` is NOT the substitute. It is the
   supervised-push primitive alone: it drives the push through a watchdog and
   verifies `[0 0]`, and it runs no content scan and no routing scan. It solves
   the hang problem, not the leak problem.

   One run covers both repositories, so `crm/contacts/` rides the data overlay
   here and needs no push of its own. That also removes the old step 2, which
   pushed the data overlay only when CRM contacts happened to change; a push
   that is conditional on one directory leaves every other content change of the
   session sitting unpushed. The DATA overlay goes FIRST inside `push-all.py`,
   because the engine's pre-push hook runs the full suite inside its push and
   the overlay is the half that cannot be reconstructed.

   Exit `3` is a skip, not a failure: read its headline (`Partial: N of M` vs
   `NOTHING PUSHED: all M`) and report that shape per `/backup` SKILL.md.

2. Refresh the operator's own CRM aggregate:
   ```bash
   python scripts/aggregate-crm.py
   ```
   This reads each active exec's own data overlay at
   `../.heading-os-data-{slug}/crm/contacts/` and regenerates the company-wide
   radar, ownership map, shared contacts, and by-company views into
   `<data-root>/crm/aggregated/`. That output is the operator's own derived view,
   not a shared repo, so nothing is pushed. Pass `--skip-clone` to skip cloning
   any missing exec overlay.

3. Executive workspaces:
   - Central CEO-driven exec sync is **retired** as of 2026-06-26 (the destructive
     `workspace-sync.py` and the `sync-all-execs.py` driver are gone). The no-op stub that
     stood in for `sync-all-execs.py` was deleted on 2026-08-20; four of its five
     flags were already documented as ignored, and a file that exists only to do
     nothing is a file someone will eventually call.
   - In the HEADING OS three-repo model each exec pulls engine code with a plain
     `git pull` and refreshes corporate content via `scripts/sync-corporate.py`
     (the consumption seam, LIVE 2026-06-26; deferral lifted after CEO cutover).
     There is still NO central CEO-driven driver — distribution stays per-machine.

### Phase 5: Report

Present a summary:
```
PUSH COMPLETE
  Version: {version} (build {build})
  Published: {new_count} new, {modified_count} modified files
  Categories: {skills} skills, {rules} rules, {scripts} scripts, {context} context files
  Corporate repo: pushed to origin/main
  Engine clone + data overlay: pushed via push-all.py, verified [0 0]
  CRM aggregate: {refreshed|no changes}

  Each executive receives this update when they next pull.
  Active executives: {list from admin/executives.json under the DATA root}
```
