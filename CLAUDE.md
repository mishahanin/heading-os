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
   registers 15 of the 17 hooks; the tracked `.claude/settings.json` registers
   the other 2. Among the 15 is `_dispatch.py`, the single entry point for
   eleven PreToolUse walls including the release gate and the secret scanner. A
   clone that skips this runs with those walls down and nothing says so. The 2
   tracked ones are the data-path redirect and, since 2026-09-03, the
   session-start brief: it carries the YARD-NOT-PROVISIONED warning, so leaving
   it in the gitignored file meant the warning was installed by the very
   provisioning whose absence it exists to report. Re-run
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

## HELM and YARD

Several Claude sessions may run against this repository at once, and the roles
are not interchangeable.

**HELM** is the main clone on `main`. One session. Everything live happens here:
the data overlay, the daemons, mail and calendar, memory, document generation,
`/backup`, `/sync`, **every git operation in the data overlay**, and the review
and merge of finished task branches. HELM does not change engine code.

**YARD** is a git worktree of this repository on its own branch, checked out
under Herdr's configured `worktrees.directory` — physically outside the engine
clone. Nothing here is live. Engine code is changed here.

Which is which: *will this end in a commit to the engine repository?* Then YARD.
Everything else is HELM.

**The one rule.** Writing FILES into the data overlay from a YARD is allowed and
expected. Running git in it is not. Files differ per task; the git index is
shared, so a commit from a task sweeps up other tasks' unfinished work and the
daemons' in-flight writes into one unreviewed commit. A task that needs its work
recorded says so and stops.

**Never from a YARD:** editing anything inside HELM; `git push` (this repository
is public, so any branch pushed is visible immediately, not only `main`);
**anything at all to do with a running daemon** (see below); `scripts/push-all.py`,
`/backup`; maintenance passes that write under the data root. These refuse
mechanically rather than by instruction — see `scripts/utils/clone_guard.py`,
`scripts/lib/require-main-clone.sh`, and `check_yard_write_guard` in
`.claude/hooks/_dispatch.py`.

**Daemons run in HELM. All of them, always.** Operator directive, 2026-09-03.

**The category, and it is the whole rule:** from a YARD, take no action after
which a running process on this machine appears, disappears, or changes
behaviour. That is the test to apply — not a list. It covers the daemon itself
and everything that decides whether it runs: its unit, its timer, its
healthcheck, its PID file, anything that spawns it, and anything that inspects
it in order to decide. The rule is categorical rather than proportional to
risk, so a daemon entry point that only READS is guarded too. Examples, and
this list is deliberately **not exhaustive and must never be read as the
rule**: starting, installing, restarting, stopping, removing.

**The line runs through EXECUTION, not through editing.** Writing the SOURCE
CODE of a daemon in a YARD is ordinary engine work and is exactly what a YARD is
for — HELM does not change engine code at all. The guard sits at the entry
point and refuses to RUN from a non-main clone; it does not and must not stop
you opening that same file in an editor.

That code reaches HELM by ONE route, the merge of the task branch into `main`,
and **that merge must happen before the YARD is deleted** — a daemon change left
in a deleted worktree is lost, and one arriving in HELM by any other route
bypassed review.

Why the previous wording failed, recorded here because the fix is prose as much
as code. This list used to say "daemon install, restart or uninstall", and
exactly three files carried those words in their names:
`install-daemon-service.sh`, `restart-daemon-service.sh`,
`uninstall-daemon-service.sh`. Those three were guarded and nothing else was.
The prohibition had been implemented as a literal match against a list of verbs,
and "start" was not on the list — so from a YARD you could not INSTALL the mail
daemon and could freely START one. That is what happened on 2026-09-03, when a
second Exchange daemon ran twelve hours out of a worktree beside the operator's
real one. **An enumeration of verbs is a list of holes.** Whoever edits this
next: do not turn the examples above back into the rule.

**When engine work comes up during a HELM session:** name what you would change
and in which files, then stop, and say it belongs in a YARD task. This holds for
one-line changes and typos as much as for large work. The exception is an
emergency: if the live workspace is broken and a task cannot be created, make
the smallest possible fix, commit it immediately with an `emergency:` prefix,
and say plainly that the emergency path was used.

**Guards must be armed inside the task.** A worktree is a separate checkout, so
every guard has to be asked which tree it is actually looking at. Derive it from
the current checkout, never from a constant, the common git directory, or
`${CLAUDE_PROJECT_DIR}`. In particular, `WORKSPACE_ROOT` in a copied `.env`
repoints `get_workspace_root()` and with it every guard downstream; the YARD
bootstrap strips it and then verifies the resolved root. Every guard added or
changed ships with a bidirectional test, the failing half of which must fail
against the previous version.

Full design, the measurements behind it, and the operating guide:
`docs/ARCHITECTURE.md` § 8 and `scripts/herdr/README.md`.

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
