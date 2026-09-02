# CLAUDE.md — HEADING OS Engine

HEADING OS is an operations engine for an AI executive assistant: a library of
skills, always-on rules, automation scripts, and daemons that let Claude Code act
as a strategic assistant across sessions. This repository is the **engine** — code
only, no private data. It is a personal project built and maintained by Misha
Hanin and shared as an open framework. Misha is the Founder & CEO of 31 Concept
(31C) and uses HEADING OS himself, but 31 Concept is not involved in it and bears
no responsibility for it; any 31C-like vocabulary here is illustrative example
data only.

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
- `config/` — engine configuration (real instance values stay private); the
  `*.example.*` per-instance templates live beside the scripts that read them, in
  `scripts/`
- `docs/`, `reference/`, `examples/` — documentation and scaffolding

## Setup

Canonical toolchain is `uv`; `pyproject.toml` is the source of truth and `uv.lock`
pins the resolved set.

1. `git lfs install && git lfs pull` — brand assets and the binary test fixtures
   are Git LFS objects; without this they check out as pointer files
2. `uv sync --all-extras --group dev`
3. `cp .env.example .env`, then fill in your own credentials — never commit `.env`
4. `pre-commit install` — once per fresh clone, or the commit gates are not armed
5. `bash scripts/setup-platform.sh` — once per fresh clone, or the SESSION hooks
   are not armed. It writes the gitignored `.claude/settings.local.json`, which
   registers 16 of the 17 hooks; the tracked `.claude/settings.json` registers
   the one remaining. Among the 16 is `_dispatch.py`, the single entry point for
   eleven PreToolUse walls including the release gate and the secret scanner. A
   clone that skips this runs with those walls down and nothing says so. Re-run
   it any time: it merges, keeping every local key. `bash
   scripts/setup-platform.sh --check` reports the state and exits non-zero when
   a registration is missing, and `/prime` runs the same check at session boot.
6. `.venv/bin/python -m pytest tests/ -q` to verify the suite passes

The `venv` + `pip` path still works for tooling that cannot run `uv`
(`python -m venv .venv`, then `pip install -r requirements.txt`, dev tooling in
`requirements-dev.txt`); `requirements.txt` is a generated export, never
hand-edited. Invoke `.venv/bin/python` explicitly rather than a bare `python`, so
a machine-wide interpreter without the pinned dependencies cannot silently run
the suite.

## Contributing & security

Read `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, and `SECURITY.md` first. Never commit
secrets or real data — the commit hooks and the push-time scan are designed to
block them, but the first line of defense is you.

<!-- Operator-private operational context. Resolves only on an operator machine,
     loaded directly from the sibling private DATA overlay (no symlink, no copy —
     single source of truth in .heading-os-data). On a public clone that sibling
     is absent, so this import is a silent no-op. -->
@../.heading-os-data/CLAUDE.operational.md

<!-- Deliberately the LAST thing in the rendered system prompt. Anthropic's Opus 5
     prompt-engineering guidance: effort does not reliably shorten a visible
     response, so length is controlled by an explicit instruction, and a
     late-position reminder carries more weight than an early one. Added
     2026-08-20. It governs the SHAPE of the reply and nothing else — the
     paragraph says so in its own words, because the previous attempt at this
     (two clauses in the ELI5 output style) read as permission to do less work
     and Opus 5 followed it literally. -->
<tone_preference>Keep the written reply as short as the content allows. Match the
length of a document to what the task needs; no filler sections, no restating
what was just said, no boilerplate summary. This governs the length of the
OUTPUT, never the depth of the work: run every check the task needs, read every
file it needs, and finish the whole thing — then report it briefly.</tone_preference>
