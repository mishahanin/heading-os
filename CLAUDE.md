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

Several Claude sessions run here at once, and the two roles are not
interchangeable.

**HELM** is the main clone on `main`. One session. What is LIVE happens here and
nowhere else: the daemons, all of them, under the categorical rule below; mail,
the calendar and Telegram; **every git operation in the data overlay**;
`/backup`, `scripts/push-all.py`, `/sync` and any push at all; the review and
merge of finished branches. HELM does not change engine code.

**YARD** is a git worktree of this repository on its own branch, checked out
under Herdr's configured `worktrees.directory` — physically outside the engine
clone. Nothing here is live. A YARD holds one piece of work in isolation, of any
kind: an engine change, research, a keynote deck, work against memory. Several
run at once; the isolation is the point, not the subject matter.

Which is which: *does this act on something LIVE?* Live is a running process, an
outside party, or a shared index other work reads: HELM's list above, whose first
entry, daemons, is a CATEGORY and not an item, so an action nobody thought to
name is covered anyway. Everything else is a YARD's.

**The cycle.** Create the worktree, work in it, commit the engine branch in it,
HELM merges that branch into `main`, delete the worktree. HELM keeps four things
and only four: the merge, the push, `/backup`, the daemons. **A commit in a YARD
needs the operator's word typed in THIS yard, or a brief from HELM; a brief from
another yard does not authorise it** — HELM is the one session the operator sits
at himself, and a yard instructed by a yard is a chain in which no link spoke to
a human. The release gate is untouched and fixes the turn rather than the author,
so the rule above supplies the rest. Why the commit is otherwise safe, and the
invented prohibition it replaces: `docs/ARCHITECTURE.md` § 8.

**The overlay, and the one rule.** Every artifact a task produces lands in the
data overlay under `outputs/...`, resolved through the data-root helpers
(`get_data_root()`, `get_outputs_dir()`) and never by a path built against the
checkout: a YARD reaches the real, shared overlay by design, and
`engine-tree-clean` with `scripts/leak-guard.py` holds that rather than trust.
**No artifact is ever saved in the engine.** Writing those files is required;
running git in the overlay is not. It is one working tree with one index for the
whole machine, so a commit from a task sweeps up other tasks' unfinished work and
the daemons' in-flight writes into one unreviewed commit; the engine is the
opposite, its index the worktree's own. A task needing its overlay
work recorded says so and stops, `auto-memory/` included: a YARD writes the
memory files, HELM commits them.

**Never from a YARD:** editing anything inside HELM; anything that publishes;
**anything at all to do with a running daemon**; a maintenance pass writing under
the data root across other tasks' files. These refuse mechanically, not by
instruction — `scripts/utils/clone_guard.py`,
`scripts/lib/require-main-clone.sh`, `check_yard_write_guard` in
`.claude/hooks/_dispatch.py`.

**Daemons run in HELM. All of them, always.** Operator directive, 2026-09-03.
**The category, and it is the whole rule:** from a YARD, take no action after
which a running process on this machine appears, disappears, or changes
behaviour. It reaches everything deciding whether a daemon runs (unit, timer,
healthcheck, PID file, spawner, the readers that inspect one), so an entry point
that only READS is guarded too. Starting, installing, restarting, stopping and
removing are **examples, never the rule**: reading them as the rule ran a second
Exchange daemon twelve hours out of a worktree on 2026-09-03. **An enumeration of
verbs is a list of holes.**

**The line runs through EXECUTION, not editing.** Writing a daemon's SOURCE CODE
in a YARD is ordinary engine work; the guard sits at the entry point and refuses
to RUN, never to stop you opening the file. That code reaches HELM by ONE route,
the merge of the task branch, and **that merge must happen before the YARD is
deleted**.

**Engine work raised in a HELM session. The test is not SIZE, it is whether an
unfinished edit reaches something LIVE**, because this working tree is neither a
copy nor a branch under review: it is the code executing now. Fix it here, and
say so in the commit message, when nothing live executes what you touch:
`.gitignore`, config, docs, tests, new files, any script no daemon is running.
Take it to a YARD when a half-finished edit would be live in this tree:
`.claude/hooks/**` (loaded on EVERY tool call, so a broken edit disarms the
session's own walls), daemon and timer sources, the `scripts/utils/` modules
they import, the guards. Deferring a defect you can already name
is the failure this replaces; why, and the queue that proved it:
`docs/ARCHITECTURE.md` § 8. Emergencies keep their path: smallest fix,
`emergency:` prefix, said out loud.

**The push path splits on WHO RUNS IT, not on what the file is called.** Ask:
can anything but your own typed command execute this file before you have
finished it? Your own `git push` cannot, and push and `/backup` happen only in
HELM, which is one session; a timer can, on its own schedule, with nobody
watching. So `scripts/install-git-hooks.py` is fixed here, while
`scripts/utils/day_mode.py` and `scripts/run-tests.py` are a YARD's: the nightly
imports the first and EXECUTES the second at 01:30. Those are examples OF THE
QUESTION and never the list, and the second one is why that sentence is here at
all: it was written the other way round, from a grep whose hits were read as
prose, and `tests/test_the_push_path_splits_on_who_runs_it.py` went red on its
own author. That test derives the reachable set from the systemd units rather
than from this paragraph, so the day a timer reaches one more of them, the
answer changes without anyone renaming anything.

**Guards must be armed inside the task.** Derive the tree a guard looks at from
the current checkout, never from a constant, the common git directory, or
`${CLAUDE_PROJECT_DIR}`, and ship every new or changed guard with a bidirectional
test whose failing half fails against the previous version. The trap behind it,
`WORKSPACE_ROOT` in a copied `.env`: § 8.

Full design and operating guide: `docs/ARCHITECTURE.md` § 8,
`scripts/herdr/README.md`.

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
