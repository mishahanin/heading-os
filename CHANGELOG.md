# Changelog

All notable changes to HEADING OS are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims at [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the project is pre-1.0, interfaces may change between minor versions; see [ROADMAP.md](ROADMAP.md).

## [Unreleased]

### Added
- **Nightly router-accuracy trend (F-6.2):** `scripts/router-accuracy-nightly.py` runs `skill-trigger-test.py --all --json` once a night and persists a dated raw artifact plus an append-only `trend.jsonl` (`{date, overall_rate, total_passed, total_cases, per_skill}`) under the DATA overlay (`get_datastore_dir()/operations/router-accuracy/`, the F-6.2-designed data-overlay exception), guarded by `require_writable_data_root()` and skipped under `SENSITIVE_MODE` (judge traffic traverses Anthropic). A systemd-user timer installer `scripts/install-router-accuracy-timer.sh` (+ `scripts/templates/systemd/router-accuracy.{service,timer}`, `OnCalendar` 03:00, after eval-drift's 02:00) mirrors the ops-radar installer and carries a `--uninstall` path. A new `router_accuracy` ops-radar signal producer (`classify_router_accuracy` + `router_accuracy_state` in `scripts/utils/ops_signals.py`, registered in `ops-radar.py`) reads the trend against a rolling 7-record baseline and raises a **Tier-B** `warn`/`high` flag when any skill drops > 10 points (point-scaled like eval-drift), so the alert rides the existing ops-radar -> Telegram path with no new channel. `datastore/operations/router-accuracy/` routed private. New `tests/test_router_accuracy_nightly.py`.
- **/implement typed trajectory flags + self-check:** `scripts/implement-trajectory-log.py --event` now accepts typed flags (`--step`, `--title`, `--file`, `--status`, `--wave`, `--successes`, `--check`, `--passed`/`--failed`, and the rest), so each event emits in one `Bash` call with no temp file. A new `--verify --run-id` mode self-checks a trajectory's structural invariants (step and wave pairing, bracketed `successes`, literal `files_affected`, `run_start` first and `run_end` last) and exits non-zero on a defect. New `tests/test_implement_trajectory_log.py`.
- **Operator identity seam (F-4.1):** one place names who runs an instance, so the engine ships operator-agnostic (a fresh clone resolves to a neutral "operator"). New `scripts/utils/operator.py` (`get_operator()` / `operator_slug()` / `operator_org()`) resolves identity with precedence env `HEADING_OS_OPERATOR_*` → data-overlay `operator.yaml` → engine-local `config/operator.yaml` → shipped `scripts/operator.example.yaml`. Every load-bearing identity default (bridge user slug, GitHub org, corporate publisher, the email-reply voice clause, admin-slug fallbacks) routes through the seam via `workspace.operator_identity_default()`. `config/operator.yaml` is routed private and gitignored. Regression guard in `tests/test_operator_seam.py`.

### Changed
- **SKILL.md size budget (F-5.3):** `scripts/skill-metadata-check.py` now enforces a mechanical size budget on every `SKILL.md` — a hard cap of 500 lines AND 18432 bytes (18 KB) with a 16384-byte (16 KB) warn threshold. The size gate is UNCONDITIONAL: any hard violation exits 1 regardless of `--fail-on-missing`, so the existing flagless CI invocation enforces it with no workflow-file change; a new `skill-size-budget` pre-commit hook runs the same audit on `SKILL.md` edits. The two byte outliers were slimmed under the cap by moving overflow into `references/`: `implement` (20725 → 16467 bytes) relocated its wave-execution mechanics and version history to `references/implement-details.md`; `scrutinize` (21452 → 17915 bytes) relocated its approval-block format + strict semantics to `references/approval-block.md` and tightened several inline sections. Behavior preserved (procedures reachable via pointers; scrutinize Phase 0 eager-loads all references). `development-standards.md` prose mechanized. New `tests/test_skill_metadata_check.py`.
- **Skill router progressive disclosure (F-5.2):** the generated router is split into two layers — a compact always-on core index (Skill + Triggers only) between the sentinel markers in `.claude/rules/skill-router.md`, and full per-category detail tables (Skill \| Triggers \| Exclusions \| Compound) in new `reference/skill-router/<category>.md` files read on demand for disambiguation. `scripts/generate-skill-router.py --split-by-category` (formerly a stub) is now the default write and generates both layers; `--check` verifies both (drift / missing / orphan); `--flat` prints the legacy flat monolith to stdout for the semantics-preservation proof (the union of category rows byte-equals it). The always-on marked region shrinks ~36% with every skill's triggers still in-core. `scripts/skill-trigger-test.py` now concatenates the category files onto its judge context so the routing regression harness still sees the relocated exclusions; the pre-commit `skill-router-sync` filter widens to `reference/skill-router/`. No routing-content change; no schema change; CI command unchanged.
- **Skill router generated from SKILL.md frontmatter (F-5.1):** each skill now owns its router row in its own `SKILL.md` under a new `x-heading-routing` block (category, triggers[], exclusions[], compound, router, optional label), and the seven registry tables in `.claude/rules/skill-router.md` are generated from those blocks between sentinel markers by `scripts/generate-skill-router.py` (`--write` / `--check` / `--flat`). The presence-only `check-skill-router-sync.py` is replaced by `generate-skill-router.py --check` (content idempotency, wired into CI, pre-commit, and the canary smoke set); a skill missing the block fails with the file path and a fix-it snippet. One-shot migration `scripts/dev/extract-router-rows.py` populated the 94 blocks; the initial regeneration is a semantics-neutral reorder (deterministic category, then name). New `tests/test_generate_skill_router.py`. (F-5.0: the rules-loading mechanism is the native Claude Code `.claude/rules/` auto-load, not a hook or import chain — documented so Phase 5 rests on fact.)
- **/implement trajectory emission (v1.6):** the skill drives emission through the typed flags, runs `--verify` after `run_end` (advisory), and consolidates the v1.3-v1.5 wave contract into one statement. `--data-file`/`--data-stdin`/`--data-json` stay as the arbitrary-payload escape hatch; the event schema and the `/scrutinize` lens are unchanged.
- **/implement emission discipline (v1.7):** `scripts/implement-trajectory-log.py` now enforces the sequencing invariant at emit time — a `step_start` opened while another step is open (outside a parallel wave), or a `step_end` for an unopened step, is rejected with a new exit code `5` instead of landing silently. `--verify` gains a run-level files reconciliation: any engine file changed since `run_start.git_head` but recorded in no step's `files_affected` is flagged as an advisory defect (git-degrades gracefully; meaningful only immediately after the run). The `/implement` SKILL (v1.7) now mandates verbatim surfacing of a non-zero `--verify` in the Report Deviations while still never hard-failing a completed run. Event schema and `/scrutinize` lens unchanged.
- **Frontmatter namespace `x-31c-*` → `x-heading-*` (F-4.2):** all 94 skills renamed from the brand-specific namespace to the neutral `x-heading-orchestration` / `x-heading-capability`. Both parsers are dual-key (prefer `x-heading-*`, accept the legacy key with a deprecation notice). New one-shot dev tool `scripts/dev/rename-x31c-namespace.py`.

### Deprecated
- The legacy `x-31c-*` frontmatter key and the operator-identity compatibility shim (`operator_identity_default()` legacy fallbacks) are accepted through a transition window and **removed in v0.5.0**. Write `config/operator.yaml` and re-stamp any skill still on `x-31c-*` before then.

### Security
- **Leak-path matrix (F-6.3):** `tests/security/test_leak_path_matrix.py` attacks every headless-testable engine/data segregation layer on purpose (write-vectors by data-class targets), asserting each leak is blocked by the expected layer with its distinctive message, all inside a sandboxed throwaway repo that never touches the real tree. 31 executable blocker cells; each of the tree-clean guard, leak-guard, content-guard, push wall, and data-root seam is the blocker in `>= 2` cells. The hook-mediated `data-path-redirect` vector is a documented manual drill in the security model. Closes the untested `engine_content_scan` real-entity wall and consolidates coverage that was previously per-layer.
- **Dashboard Host/Origin guard (F-9.2):** a FastAPI middleware on the bridge daemon rejects non-loopback `Host` (421) and cross-origin `Origin` (403), a belt-and-suspenders defense against DNS-rebinding and localhost-CSRF on the unauthenticated surface. `workspace-health.py` gains a `daemon-token` check asserting the bearer-token file is 0600. Proven by `tests/bridge/test_host_origin_guard.py`.
- **Threat model published (F-9.1):** `docs/THREAT-MODEL.md` maps every threat to its control and the exact test or CI guard that proves it, with an honest gap list. Linked from the Security model reference.
- **Vendored-skill hash verifier (F-9.5):** `scripts/verify-skills-lock.py` recomputes the `skills-lock.json` hashes (recipe `sha256-tree-v1`, LF-normalized) and fails on drift; wired into CI guards and pre-commit. `frontend-design` is marked plugin-managed (not vendored in-repo).
- **CI hardening (F-9.3, F-9.6):** the `audit-skill-bash-paths` and `classification-health` audits now run in the guards job; a CycloneDX SBOM is generated on push and tags; an OpenSSF Scorecard workflow runs weekly. detect-secrets baseline drift (F-9.4) confirmed in place.
- **Data-overlay migration framework (F-9.7):** `scripts/migrations/` + `scripts/migrate-data.py` (`--status` / `--apply` / `--stamp` / `--dry-run`); `require_writable_data_root()` refuses writes to an overlay behind the engine schema. Proven by `tests/test_data_migrations.py`.

## [0.3.0] - 2026-07-06

### Added
- **Memory metamemory (Phase 1):** an advisory near-duplicate detector over the auto-memory store, surfaced in the weekly memory-hygiene report as merge candidates. It never enters the hygiene exit-code gate and never auto-applies — a human resolves each candidate via `/dream`. New `scan_redundancy()` in `scripts/utils/memory_health.py`, wired through `scripts/memory-hygiene.py`, tunable via `audit.near_dup_threshold` in `config/memory-index.yaml`. Degrades gracefully when the local embedder is unavailable.
- **Recall-op log:** `scripts/utils/memory_ops_log.py` appends one local-only JSONL record per recall query (from `scripts/memory-index.py query`), redacting the query text under `SENSITIVE_MODE` while keeping the numeric metrics. It accumulates a baseline for deferred recall-quality metrics and never leaves the machine.
- **Both-store memory retirement:** `scripts/retire-memory.py` and `retire_memory()` in `scripts/utils/memory_stores.py`. A memory removed on one store alone is resurrected by the SessionStart reconcile (which never propagates deletions); `retire_memory` clears the canonical store and every native harness store so a delete sticks.

### Changed
- **`/dream` now operates on the canonical data-overlay `auto-memory/`** instead of the per-launch native harness store, and retires superseded or merged files via `scripts/retire-memory.py`. It also applies human-approved merge proposals from the hygiene report and appends a consolidation trace. This fixes a latent issue where `/dream` deletes were silently resurrected at the next session.
- `scripts/memory-index.py`: `cmd_query` now records each recall to the recall-op log (file-only; the stdout JSON contract is unchanged).

### Fixed
- `.gitignore`: ephemeral per-machine runtime directories (`.logs/`, `.state/`, `.data/`) are now ignored, so the local recall-op log can never reach the repo.

## [0.2.0] - 2026-07-05

### Added
- `/pencil-export`: **automatic brand-font embedding** in the editable PPTX. The typefaces used on the slides are embedded into the `.pptx` itself (the PowerPoint "Embed fonts in the file" structures, written directly at the OpenXML package layer — a `fntdata` content-type, one font part and relationship per typeface, and a schema-ordered `<p:embeddedFontLst>` with `embedTrueTypeFonts`), so the deck opens identically on a machine without the fonts installed. Only TTF/OTF embed (PowerPoint cannot use woff/woff2); the layout is never round-tripped through LibreOffice, which would drift it. See `.claude/skills/pencil-export/SKILL.md`.
- `/pencil-export`: a **portable "ready to be shared" flat PPTX**, opt-in via `--formats pptx-flat` (alias `pptx-image`). It is an image-per-slide deck (like the PDF, not editable) that needs no fonts installed and renders identically anywhere, written as `<name> (ready to be shared with the world).pptx`.
- Documentation: a **Rules reference** cataloguing all always-on and path-scoped behavioural rules (`docs/RULES-REFERENCE.md`).
- Documentation: a **Hooks reference** inventorying every `PreToolUse` / `PostToolUse` / `SessionStart` / lifecycle hook (`docs/HOOKS-REFERENCE.md`).
- Documentation: a **Configuration** reference for `config/` (`routing-map.yaml`, `tool-risk.json`, `memory-index.yaml`, `llm_fallback.yaml`, wizard files, schemas) (`docs/CONFIGURATION.md`).
- Documentation: a **Troubleshooting** guide and a **Glossary** (`docs/TROUBLESHOOTING.md`, `docs/GLOSSARY.md`).
- This changelog.

### Changed
- `/pencil-export`: **PPTX now defaults to the editable twin** (`--formats pptx`) — native, editable text boxes laid over a text-less background render, with the brand fonts embedded — instead of an image-per-slide deck. Editability is the point of a PPTX; the frozen image deck is still available via the new `pptx-flat` format, and `editable` is kept as an alias of `pptx`. `pdf` remains image-per-slide.
- Docs site: the sidebar navigation is now generated from a single source of truth for every page, including the hand-authored HTML pages, so nav stays consistent across the site.

### Fixed
- Docs site: `EMERGENCY-PROCEDURES` is now linked in the site navigation (it was generated but orphaned).
- Docs site: the engine/data segregation contract now has a rendered page in the navigation, fixing two broken inline links to it from the architecture and security pages.
- Docs site: the `ROADMAP` is now linked from the README and the site navigation.

## [0.1.0] (2026-07-01)

Initial public release.

### Added
- The **engine / data separation**: a shareable engine repository and a separate private data repository, wired at runtime through a single seam (`get_data_root()`), with the guarantee proven by multiple enforcement layers rather than policy alone.
- The **security model**: the lethal-trifecta control (outbound send is always human-gated), the engine/data enforcement layers, the secret gates, and the progress-watchdog on must-complete steps. See `docs/SECURITY-MODEL.md` and `docs/engine-data-segregation-contract.md`.
- **Skills**: slash-command workflows for research, communications, content, CRM, strategy, and operations, routed from natural language by a single router rule.
- **Rules**: an always-on behavioural layer governing voice, humanization, classification, and the safety controls.
- **Hooks**: `PreToolUse` / `PostToolUse` / `SessionStart` guards that enforce the rules before a write lands.
- **Daemons**: optional always-on background services (a loopback dashboard, mail and calendar sync), driven from the CLI, never required through a browser.
- **Memory and ODIN**: a local associative-memory index behind `/recall` and a persistent knowledge brain.
- The published documentation site at [mishahanin.github.io/heading-os](https://mishahanin.github.io/heading-os/), the deployment guide, and the focused setup guides for models, integrations, and personalization.

[Unreleased]: https://github.com/mishahanin/heading-os/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/mishahanin/heading-os/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/mishahanin/heading-os/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/mishahanin/heading-os/releases/tag/v0.1.0
