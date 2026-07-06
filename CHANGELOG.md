# Changelog

All notable changes to HEADING OS are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims at [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the project is pre-1.0, interfaces may change between minor versions; see [ROADMAP.md](ROADMAP.md).

## [Unreleased]

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
