# Workspace Deep Audit - Inventory Streams

Consumed by: `.claude/skills/workspace-deep-audit/SKILL.md` Phase 1.
Last Updated: 2026-05-20

7 parallel inventory streams. Each is a self-contained agent prompt. Concurrency cap 5 per wave per `skill-orchestrator.md` Principle 5 — if running all 7, batch 5 + 2.

All streams are READ-ONLY. None modifies workspace files. Each returns an inline JSON-shaped summary that the synthesis phase consumes.

---

## Stream 1 - Skills Inventory

**Agent model:** Haiku (mechanical counting)

**Prompt:**

> Read-only inventory of `.claude/skills/`. Produce a structured summary covering:
>
> 1. Total active skill count (skip `archive/`)
> 2. Per-category breakdown using the categories from `.claude/rules/skill-router.md` (Content, Operations, Business/Strategy, Intelligence, Communication, CRM, Design, Other)
> 3. Per-skill line counts (use `wc -l` on each `SKILL.md`)
> 4. Frontmatter compliance: count skills with name / description / allowed-tools / metadata.author / metadata.version / x-31c-orchestration block
> 5. References coverage: count skills with `references/` directory and total reference file count
> 6. Evals coverage: count skills with `evals/cases/` and `evals/benchmark.json` files
> 7. Skills exceeding 300 inline lines (flag for refactor queue)
> 8. Skills exceeding 500 inline lines (Anthropic hard cap violation)
>
> Return inline summary - no file writes. Format as a markdown table the synthesis phase can paste into the audit body.

---

## Stream 2 - Rules Inventory

**Agent model:** Haiku

**Prompt:**

> Read-only inventory of `.claude/rules/*.md`. For each rule file:
>
> 1. Filename
> 2. Scope: always-active / path-scoped (has `paths:` frontmatter) / contextual
> 3. `Last Verified:` date (extract from body, report `none` if missing)
> 4. Days since Last Verified (today's date - Last Verified date)
> 5. Total line count
>
> Then produce a table summary:
> - Total rule count
> - Count by scope (always-active / path-scoped / contextual)
> - Count of rules with Last Verified ≤90 days (fresh)
> - Count of rules with Last Verified >90 days (stale, drift risk)
> - Count of rules with no Last Verified date
>
> Return inline. No file writes.

---

## Stream 3 - Hooks Inventory

**Agent model:** Haiku

**Prompt:**

> Read-only inventory of the hook system:
>
> 1. Count `.py` files in `.claude/hooks/`
> 2. Open `.claude/hooks/_dispatch.py` and count `def check_*` functions (the PreToolUse checks)
> 3. List each check_* function name
> 4. Open `.claude/settings.local.json` and report registered hooks per event (SessionStart, PreToolUse, PostToolUse). For each event, list matcher + hook count.
> 5. Open `.pre-commit-config.yaml` and list every `- id:` value (the registered pre-commit hooks).
> 6. Report total hook gate count (PreToolUse + PostToolUse + pre-commit + sentinel-integration-tests).
>
> Return inline. No file writes.

---

## Stream 4 - Scripts Inventory

**Agent model:** Haiku

**Prompt:**

> Read-only inventory of `scripts/`:
>
> 1. Count `.py` files at `scripts/` top level (excluding `scripts/utils/`, `scripts/archive/`)
> 2. Count utility modules in `scripts/utils/`
> 3. Top-10 longest scripts (by `wc -l`) with line counts
> 4. Naming convention compliance: list scripts that violate `kebab-case.py` for CLI scripts or `snake_case.py` for `scripts/utils/`
> 5. Scripts > 500 lines without `# === Section ===` banners (grep for `# ===` in each)
> 6. Count scripts importing `from scripts.utils.workspace import` (use of identity oracle)
> 7. Count scripts decorated with `@observe()` from `scripts.utils.observability` (Anthropic SDK callers covered by Langfuse)
>
> Return inline. No file writes.

---

## Stream 5 - Dependencies + Context7 Validation

**Agent model:** Sonnet (judgment needed for staleness severity)

**Prompt:**

> Read `requirements.txt`, `requirements-dev.txt`, and any `daemons/*/requirements.txt`. For every pinned package:
>
> 1. Resolve the Context7 library ID via `mcp__plugin_context7_context7__resolve-library-id` with the package name
> 2. Fetch latest version via `mcp__plugin_context7_context7__query-docs` (look for version metadata in the docs response)
> 3. Compare pinned version vs latest. Categorize:
>    - **CRITICAL** - outdated 10+ minor versions OR has known security advisory OR enables a major workspace capability we're not using (e.g., anthropic prompt caching pre-0.97)
>    - **HIGH** - 1+ major version behind, OR enables a meaningful new feature
>    - **MEDIUM** - 1+ minor version behind, routine bump
>    - **CURRENT** - at latest within a patch
> 4. For each CRITICAL or HIGH item, write 1-2 sentences on what the bump unlocks and what migration cost looks like
> 5. Cross-reference main vs fireside venv pins - flag any version mismatches
> 6. Run `pip-audit` if available (Bash: `pip-audit --requirement requirements.txt --format json 2>&1 || echo "pip-audit not available"`). Report any HIGH/CRITICAL CVEs surfaced.
>
> Return as 4 categorized tables (CRITICAL / HIGH / MEDIUM / CURRENT) plus a Mismatch section.
>
> If Context7 returns no results for a package, report it as `UNVALIDATED - Context7 no match` and proceed.

---

## Stream 6 - Knowledge / Memory / CRM / DataStore Inventory

**Agent model:** Haiku

**Prompt:**

> Read-only inventory of the persistent state layers:
>
> 1. **Memory:** count files in `~/.claude/projects/{slug}/memory/` excluding `MEMORY.md`. Read `MEMORY.md` and verify line count under 200 (truncation threshold).
> 2. **Knowledge:** count `.md` files in `knowledge/` (recursive). Count notes in `knowledge/odin-brain/principles/` and `knowledge/odin-brain/positions/`.
> 3. **CRM:** count `.md` files in `crm/contacts/`. Run `python scripts/validate-crm-schema.py --quiet` and report pass/fail count.
> 4. **DataStore:** count all files under `datastore/` (recursive, all extensions). Report size with `du -sh datastore`.
> 5. **Plans:** count active plans in `plans/` (top-level `.md`) and archived plans in `plans/archive/`. Flag any active plan older than 30 days (stale).
> 6. **Threads:** count active threads in `threads/business/` and `threads/personal/`. List the 3 most recently touched.
> 7. **Outputs:** report total size of `outputs/` with `du -sh outputs`. Report size of `outputs/operations/` and `outputs/intel/` separately.
> 8. **Embedding-index threshold check:** compute `sum = knowledge_count + memory_count + odin_sources_count` (where odin_sources_count = number of files under `knowledge/odin-brain/sources/`). If `sum >= 1000`, surface this in the return summary as a flagged item: `[REMINDER] Knowledge corpus is {sum} entries (threshold 1000 crossed). CEO requested reminder about ChromaDB embedding index at this scale. See memory: project_embedding_index_threshold.md`. If `sum < 1000`, do not surface — silent pass.
>
> Return inline. No file writes.

---

## Stream 7 - Security Posture Audit

**Agent model:** Sonnet (judgment needed)

**Prompt:**

> Audit the workspace's 7-layer defense-in-depth posture. For each layer, verify it is present and operational:
>
> 1. **PreToolUse hooks** - read `.claude/hooks/_dispatch.py`. Confirm dispatcher pattern, count check_* functions, verify each is in the `CHECKS` registration block at module bottom.
> 2. **PostToolUse advisors** - confirm `post-write-sanitize.py`, `prompt-guard.py`, `context-monitor.py`, `sync-docs.py` exist and are registered in `.claude/settings.local.json`.
> 3. **Pre-commit hooks** - read `.pre-commit-config.yaml`, count `- id:` entries, list workspace-specific (non-external) hooks.
> 4. **Classification system** - verify `config/routing-map.yaml` parses. Count rule keys by destination (engine/private/corporate). Sample 3 random rules and verify the paths exist.
> 5. **Read-only chmod enforcement** - RETIRED. This was `_set_readonly` in the now-deleted `scripts/workspace-sync.py` (the corporate-pull copied code into exec trees and chmod-ed it read-only). The destructive sync engine is gone (see `plans/2026-06-26-retire-workspace-sync-disk-import.md`); execs now `git pull` the engine clone, so there is no copied corporate/ tree to chmod. Skip this check.
> 6. **Air-gapped vault** - check if `_secure/.active-project` exists (vault active?). Read `.claude/rules/secure-projects.md` and verify rule #6 (observability disable) is present.
> 7. **Adversarial regression suite** - count `.json` files in `tests/security/prompt-injection/attacks/`. Confirm `run-adversarial-suite.py` exists and is executable.
>
> Also run a workspace-wide secret scan summary: `detect-secrets scan --baseline .secrets.baseline 2>&1 | tail -5` (if detect-secrets is installed). Report any new findings.
>
> Run hidden-char scan on a sample of critical files: `python scripts/sanitize-text.py CLAUDE.md .claude/rules/*.md --scan 2>&1 | tail -10`. Report clean/findings.
>
> Return as a 7-row table (one per layer) with status: OPERATIONAL / DEGRADED / ABSENT, plus the secret scan + hidden char scan summary.

---

## Synthesis Phase

After all 7 streams return, the audit synthesis phase:

1. Validates that each stream returned non-empty output (re-dispatch if blank)
2. Cross-references stream outputs (e.g., Stream 1's skill count should match Stream 4's "skills referenced from scripts" if any)
3. Pastes the inventory tables into Section 2 of the output template per `references/output-template.md`
4. Flags any cross-stream inconsistencies in the "Anomalies" subsection
