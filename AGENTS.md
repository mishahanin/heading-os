# AGENTS.md

Instructions for coding agents (Codex, Cursor, and any AGENTS.md-aware tool)
working in this repository. Claude Code agents should read `CLAUDE.md` instead
(or in addition) — it is the Claude-Code-specific superset of what is here,
carrying the always-on rules under `.claude/rules/` that this file does not
duplicate.

No `AGENTS.md` existed in this repository before this file; it is new, not a
revision of a prior version.

## What this repository is

HEADING OS is an operations engine for an AI executive assistant: a library of
skills, always-on rules, automation scripts, and daemons that let an agent act
as a strategic assistant across sessions, with Claude Code as the reference
agent it is built for. This repository is the **engine** — code only, no
private data, intended to be public. A companion private "data" repository
(not part of this repo, and not present in a bare clone) holds the operator's
actual contacts, knowledge, outputs, and credentials.

## Setup

The canonical toolchain is [uv](https://docs.astral.sh/uv/); `pyproject.toml`
is the source of truth for dependencies and `uv.lock` pins the resolved set.

```bash
uv sync --all-extras --group dev   # core + all integration extras + dev tools
cp .env.example .env               # fill in only the credentials you use
pre-commit install                 # arm the commit-time gates (see below)
```

A plain `venv` + `pip` path also works and is kept for tooling that cannot run
`uv`. `requirements.txt` is a generated export of the full dependency graph
(`uv export --all-extras`) for exactly this case — do not hand-edit it.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # add requirements-dev.txt for dev tooling
cp .env.example .env
pre-commit install
```

## Running tests

Always invoke the interpreter inside `.venv` explicitly — `.venv/bin/python`,
never a bare `python` — so you are not silently running against a different
interpreter or a machine-wide install that lacks the pinned dependencies.

```bash
.venv/bin/python -m pytest tests/ -q
```

The suite lives in `tests/`, with the security-focused subset under
`tests/security/`. The actual gate used by CI and by the push path is
`scripts/run-tests.py` (it re-execs itself into `.venv` if not already running
there, then applies a coverage floor and excludes the acceptance-only markers
that are meant to run separately):

```bash
python scripts/run-tests.py            # regression gate (what CI / pre-push run)
python scripts/run-tests.py --acceptance   # sign-off gates only
```

Lint and security static analysis run through `pre-commit` (ruff, bandit,
detect-secrets, and this repo's own guard hooks) and are armed by
`pre-commit install` above. Do not commit with `--no-verify` (see Security
below for why that matters here specifically).

## Engine / data separation — the load-bearing rule

This repository is **code only**. It must never contain real operator data:
contacts, private knowledge, generated outputs, operational threads, real
identities, or credentials. All of that lives in a separate, private "data"
repository that this engine resolves at runtime through a single seam,
`get_data_root()` (in `scripts/utils/paths.py`, re-exported from
`scripts/utils/workspace.py` for backward compatibility) — as a sibling
directory or via the `HEADING_OS_DATA` environment variable. When you write
any output, contact record, log, or generated artifact from code in this
repo, route it through the `get_*_dir()` helpers in `scripts/utils/`, never
by constructing a path into the workspace tree by hand.

Which destination a given path belongs to (engine vs. private data vs.
shared "corporate" content) is declared in `config/routing-map.yaml` and
resolved by `get_routing_destination()` / `get_classification()` in
`scripts/utils/workspace.py`. This routing is enforced mechanically, not just
by convention: `scripts/leak-guard.py`, `scripts/utils/engine_guard.py`, and
an unbypassable push-time content scan in `scripts/push-all.py` all check
that nothing private is about to leave the machine before a push succeeds.

If you are working in a bare clone of this engine with no sibling data
repository present, that is expected — the engine runs on its defaults and
the `*.example.*` / `examples/` templates rather than failing.

## Security constraints (non-negotiable)

These are enforced by tooling in this repo, not just stated as policy:

- **Secrets never go into a tracked file.** No API keys, tokens, passwords,
  or credentials in any file git can track — not `.claude/`, not `scripts/`,
  not `docs/`, not a test fixture. They belong in `.env` (gitignored,
  templated by `.env.example`) or `.sessions/` (gitignored), loaded via
  `scripts/utils/api.py` (`load_api_key()`) or `load_env()`.
- **No forbidden patterns.** Per `CONTRIBUTING.md`: no `eval`/`exec` on
  external input, no `pickle.loads()` on untrusted data, no
  `subprocess(..., shell=True)` / `os.system()`, no `yaml.load()` without
  `SafeLoader`, no disabled TLS verification. `pyproject.toml`'s ruff
  configuration runs the `S` (flake8-bandit) rule set project-wide, which
  also flags a bare `except: pass` (rule `S110`) — do not silently swallow an
  exception; log it or re-raise.
- **`git commit --no-verify` is forbidden.** The pre-commit hook (secret
  scanner, detect-secrets, bandit, and this repo's own guard hooks) is a
  local convenience check and is bypassable by design (git offers no way to
  make a commit hook unbypassable). The real, unbypassable guarantee is the
  content scan in `scripts/push-all.py`, which scans every file about to
  leave the machine and refuses the push on any hit, with no skip flag. Treat
  the commit hook as an early warning, never as the wall, and never route
  around it with `--no-verify`.
- **No new dependency without justification, pinned exactly.** Add it to
  `pyproject.toml` with an exact version pin (`==`), not a range, and say why
  it is needed — see `CONTRIBUTING.md`. Do not silently widen or merge an
  automated dependency-bump PR; refresh `uv.lock` deliberately (`uv lock`).
- **Anything touching authentication, authorization, or cryptography gets
  extra scrutiny** before it is considered done, per `CONTRIBUTING.md`.
- **Every refusal by these layers is logged**, redacted, to
  `.logs/denials/denials.jsonl` (`scripts/utils/denial_log.py`) — that log is
  telemetry only; it changes no decision and never blocks anything itself.
  Read it with `python scripts/denials.py [--days N] [--detail] [--json]`.
- **A commit touching the enforcement surface is guarded by the ordinary
  layers, and by nothing more.** The pre-commit gates, the unbypassable
  push-time content scan, and the `sovereignty guards` CI job are what stand
  behind such a change. There is no per-commit ceremony gate: the Canopus
  freeze machinery that once refused these commits was retired on 2026-08-07.

**Which files are security-critical here.** The enforcement surface named
above is this set, and a wrong edit to any of it costs more than a bug: the
PreToolUse hooks under `.claude/hooks/`; the shared credential vocabulary
`scripts/utils/secret_patterns.py` and the scanner built on it,
`scripts/secret-scanner.py`; the push wall `scripts/push-all.py` and its
detectors `scripts/utils/engine_guard.py` and
`scripts/utils/content_denylist.py`; the commit-time guards
`scripts/leak-guard.py` and `scripts/content-guard.py`; the send gate
`scripts/utils/tool_risk.py` with its ledger `config/tool-risk.json`; the two
egress controls, `scripts/utils/sensitive.py` (the fail-closed flag deciding
whether anything leaves for a third party) and `scripts/utils/egress_proof.py`
(the only sanctioned per-payload exemption from it); the classifier input
`config/routing-map.yaml`, which decides what counts as private; the test gate
`scripts/run-tests.py` and `tests/conftest.py`; and the rules those controls
implement in prose — `.claude/rules/security.md`,
`.claude/rules/lethal-trifecta.md`, `.claude/rules/tiered-risk.md` — because
the prose is what an agent reads before it acts.

What actually guards a change to them, and nothing else does: the pre-commit
gates (`31C secret scanner`, detect-secrets, bandit, ruff, and this repo's own
guard hooks), the unbypassable push-time content scan in
`scripts/push-all.py`, and the `sovereignty guards` CI job. This list is a
reading instruction for a human, not a mechanism. Nothing computes it, nothing
refuses a commit for being on it, and the classifier that once did was deleted
on 2026-08-07 with the lifecycle it served. Treat a change to one of these
files as work that earns a second read and a test, not as work some tool will
stop you getting wrong.

Full detail: `.claude/rules/security.md`, `SECURITY.md`,
`docs/SECURITY-MODEL.md`, `docs/engine-data-segregation-contract.md`.

## Console-first: no web-dashboard dependency

Every capability built in or for this workspace must be fully operable from a
terminal, a CLI, or Claude Code chat. An optional web dashboard (the bridge
daemon) may exist as a convenience layer on top of a capability, but it can
never be the *only* way to drive that capability. A background daemon is
fine (it has no UI requirement); a feature that only works through a rendered
web page is a defect. If you add a new capability, ship the CLI (an
argparse-based `scripts/<name>.py`) or chat path (a skill) first; a web view,
if any, comes after and stays optional. Full rule:
`.claude/rules/console-first.md`.

## Project layout

| Path | What it holds |
|---|---|
| `.claude/skills/` | One folder per skill: `SKILL.md`, optional `references/`, `triggers.json`. Slash-command workflows for research, communications, content, CRM, strategy, and operations. |
| `.claude/rules/` | Always-on and path-scoped behavioral rules the agent follows every turn (security, console-first, development standards, skill routing, and more). |
| `scripts/` | CLI utilities and daemons, invoked as `python scripts/<name>.py ...`. Kebab-case filenames for CLI entry points. |
| `scripts/utils/` | Shared library modules (snake_case filenames — hyphens are not legal in a Python module name). Path resolution, colors, API-key loading, workspace/data-root helpers, and similar. |
| `tests/` | The pytest suite; security-focused tests live under `tests/security/`. |
| `config/` | Engine configuration, including `routing-map.yaml` (the engine/data/corporate classifier), plus `*.example.*` templates — real instance values stay in the private data overlay, never here. |
| `docs/` | The published documentation source (rendered to a static site; see `docs/DOCS-PIPELINE.md` for the Markdown-to-HTML pipeline). |
| `reference/` | Engine reference material consumed by skills and rules. |
| `examples/` | A read-only demo data tree so a bare clone (no private data repo) still has something to run against. |

## Claude-Code-specific behavior

`CLAUDE.md` at the repository root is the Claude-Code-specific entry point.
It covers the same engine/data separation and security posture as this file,
plus Claude-Code-only mechanics this file does not repeat: the always-on
rules under `.claude/rules/` (skill routing, orchestration, voice,
humanization, prompt-refinement, and more), the hook system under
`.claude/hooks/`, and how a private operational data overlay loads on an
operator's own machine (absent, and harmless to be absent, on a public
clone). Read it if you are working as or alongside a Claude Code session in
this repository.
