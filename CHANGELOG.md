# Changelog

All notable changes to HEADING OS are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims at [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the project is pre-1.0, interfaces may change between minor versions; see [ROADMAP.md](ROADMAP.md).

## [Unreleased]

### Added
- Documentation: a **Rules reference** cataloguing all always-on and path-scoped behavioural rules (`docs/RULES-REFERENCE.md`).
- Documentation: a **Hooks reference** inventorying every `PreToolUse` / `PostToolUse` / `SessionStart` / lifecycle hook (`docs/HOOKS-REFERENCE.md`).
- Documentation: a **Configuration** reference for `config/` (`routing-map.yaml`, `tool-risk.json`, `memory-index.yaml`, `llm_fallback.yaml`, wizard files, schemas) (`docs/CONFIGURATION.md`).
- Documentation: a **Troubleshooting** guide and a **Glossary** (`docs/TROUBLESHOOTING.md`, `docs/GLOSSARY.md`).
- This changelog.

### Changed
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

[Unreleased]: https://github.com/mishahanin/heading-os/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mishahanin/heading-os/releases/tag/v0.1.0
