# /push-updates - full workflow detail

Consumed by: `.claude/skills/push-updates/SKILL.md`, section "Workflow". Holds
the complete phase-by-phase procedure and the preview and report output formats.
It also holds the BUILD.json and CHANGELOG templates, the v1.2 script mandate
with its history, and the R16 Layer 2 staged-rollout note. Read the phase you
are about to run, before you run it.

The command lines and the approval gates are duplicated in SKILL.md on purpose.
SKILL.md is authoritative for those. This file is authoritative for the formats
and the reasoning.

Last Updated: 2026-08-20

---

### Phase 0: Pre-flight

1. Run `python scripts/classification-health.py` - report classification stats
2. Check for unclassified files: `python scripts/classification-health.py --unclassified`
   - If any found: prompt CEO for classification of each file
   - Add a rule to `config/routing-map.yaml` for any newly classified files
3. Run `git status` in ceo-main - check for uncommitted changes
4. If uncommitted changes exist, show summary and ask: "Commit these changes before pushing? (yes/no)"
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

### Phase 1: Commit ceo-main

1. Stage all relevant workspace files (respect .gitignore)
   - **DO NOT stage:** `.env`, `.workspace-identity.json`, `.sync/`, `.sentinel/`, `__pycache__/`
2. Commit with message: "Workspace update: {summary from $ARGUMENTS or auto-generated}"
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
   - Surfaces orphan files (corporate-classified files missing from ceo-main) as a warning - never auto-deletes from corporate.

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

   This re-runs `filecmp.cmp` between every git-tracked corporate-classified file in ceo-main and its corporate-repo counterpart. Exit 0 = all clean. Exit 7 = mismatches detected (list printed). If the verify fails, halt before the corporate commit, surface the mismatched files to the CEO, and fix before proceeding.

8. In the corporate repo:

   ```bash
   git add -A
   git commit -m "Release v{version} (build {build}): {summary}"
   git push origin main
   ```

### Phase 4: Ancillary

1. Push ceo-main to GitHub:
   ```bash
   git push origin main
   ```
2. Check if CRM contacts were modified (any changes in `crm/contacts/`):
   - If yes, they ride the data repo: `python scripts/push-all.py` commits and
     pushes the data overlay (which holds `crm/contacts/`) to its private origin.
     Exit `3` is a skip, not a failure: read its headline (`Partial: N of M` vs
     `NOTHING PUSHED: all M`) and report that shape per `/backup` SKILL.md.
   - `aggregate-crm.py` (next step) reads each exec's data repo directly.
3. Optionally refresh CRM aggregation: `python scripts/aggregate-crm.py` (if crm-central exists)
4. Refresh CRM aggregation (if crm-central exists):
   ```bash
   python scripts/aggregate-crm.py
   ```
   This regenerates the company-wide radar, ownership map, shared contacts, and by-company views.

5. Executive workspaces:
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
  ceo-main: backed up to GitHub
  CRM Central: {synced|no changes}

  Executives will receive this update on their next hourly sync.
  Active executives: {list from exec-registry.json}
```
