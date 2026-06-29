# CLAUDE.md — HEADING OS Engine

HEADING OS is an operations engine for an AI executive assistant: a library of
skills, always-on rules, automation scripts, and daemons that let Claude Code act
as a strategic assistant across sessions. This repository is the **engine** — code
only, no private data. It is built and maintained by Misha Hanin / 31 Concept (31C)
and shared as an open framework.

## Engine / data separation

The engine never contains real data, secrets, or PII. Anything
operator-specific — contacts, knowledge, outputs, threads, real identities,
credentials — lives outside this repo: in a separate private data overlay and in
gitignored runtime files (`.env`, `.sessions`). Routing is declared in
`config/routing-map.yaml` and enforced by `scripts/leak-guard.py`,
`scripts/utils/engine_guard.py`, and the unbypassable push-time content scan in
`scripts/push-all.py`.

If you run this as your own operator workspace, your private operational notes
load from a local overlay via the import at the bottom of this file; on a public
clone that overlay is simply absent and the engine runs on its defaults and
`*.example.*` templates.

## Layout

- `.claude/skills/` — skills (one folder per skill: `SKILL.md`, optional `references/`, `triggers.json`)
- `.claude/rules/` — always-on and path-scoped behavioral rules
- `scripts/` — CLI utilities and daemons; shared modules in `scripts/utils/`
- `tests/` — pytest suite
- `config/` — engine configuration plus `*.example.*` templates (real instance values stay private)
- `docs/`, `reference/`, `examples/` — documentation and scaffolding

## Setup

1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt` (dev tooling: `requirements-dev.txt`)
3. `cp .env.example .env`, then fill in your own credentials — never commit `.env`
4. `pytest -q` to verify the suite passes

## Contributing & security

Read `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, and `SECURITY.md` first. Never commit
secrets or real data — the commit hooks and the push-time scan are designed to
block them, but the first line of defense is you.

<!-- Operator-private operational context. Resolves only on an operator machine,
     loaded directly from the sibling private DATA overlay (no symlink, no copy —
     single source of truth in .heading-os-data). On a public clone that sibling
     is absent, so this import is a silent no-op. -->
@../.heading-os-data/CLAUDE.operational.md
