<!-- version: 1.2.0 | last-updated: 2026-09-03 -->
# Architecture

Last Updated: 2026-09-03. Consumed by: readers of the docs site, and
`.claude/rules/console-first.md`. That rule keeps the console-first obligations, and
points at § 5 below for the rationale and the scope boundary.

How the pieces fit. Claude Code is the agent; HEADING OS is the structure built around
it that turns a general assistant into an operations engine with durable memory, a
catalog of skills, and a hard line between shareable code and private data.

This page is the map. Each subsystem has its own guide, linked as it appears.

---

## 1. Two repositories, one seam

HEADING OS runs as two sibling git repositories:

- the **engine** (this repo): all logic, no data, shareable;
- your **data overlay** (private, yours): CRM, knowledge, outputs, threads, context.

The engine resolves your data at runtime through a single seam, `get_data_root()`, as
a sibling directory or via `HEADING_OS_DATA`. Every artifact a skill produces is
written through that seam into your overlay, never into the engine tree. The
separation is enforced mechanically, not by discipline: see the
**[Security model](SECURITY-MODEL.html)** and the
**[segregation contract](engine-data-segregation-contract.html)**, and the
**[data overlay structure](data-structure.html)** for what lives where.

---

## 2. What happens when you type

A turn flows through the engine like this:

1. **You send a message** (natural language or a `/slash-command`).
2. **The skill router** matches intent to a skill. One clear match invokes it; several
   plausible ones present a short menu; no match falls through to ordinary
   conversation. Compound requests (for example "prep me for this meeting" with depth
   signals) hand off to the **orchestrator**, which dispatches parallel read-only
   agents and serializes any writes.
3. **The skill runs** in phases: load context, execute, synthesize, output.
4. **Hooks guard the work.** `PreToolUse` hooks can block a write before it lands
   (secret detection, the engine/data boundary); `PostToolUse` hooks scan what was
   written (hidden characters, injection patterns); `SessionStart` hooks prime the
   session.
5. **Outputs land in your data overlay**, named by convention, and anything outbound
   is drafted and queued, never sent on its own.

---

## 3. The building blocks

| Block | What it is | Guide |
|---|---|---|
| **Skills** | Slash-command workflows for research, comms, content, CRM, strategy, operations | [Skills, MCP & plugins](skills-mcp-plugins.html) |
| **Rules** | Always-on or path-scoped behavior the agent follows every turn | [Extending the engine](EXTENDING.html) |
| **Hooks** | Pre / post / session guards that enforce the rules before a write lands | [Security model](SECURITY-MODEL.html) |
| **Agents** | Reusable subagent roles in `.claude/agents/`. Each carries its own model and, more to the point, its own tool list: `draft-writer` has no `Bash`, so it cannot reach the send path whatever a dispatch asks | [Extending the engine](EXTENDING.html) |
| **Scripts & daemons** | CLI tools and optional always-on services (dashboard, mail sync, monitors) | [Daemons](daemons.html) |
| **Memory** | Auto-memory, semantic recall, the ODIN brain, knowledge, threads | [Memory & ODIN](memory-odin.html) |
| **Models** | Local embeddings plus the Council voices | [AI models](MODELS-SETUP.html) |

The router and the orchestrator are themselves rules: the agent reads them every turn,
which is why adding a skill means registering it with the router (see
[Extending the engine](EXTENDING.html)).

---

## 4. Memory: state that outlives a session

HEADING OS does not rely on one-shot prompts. It keeps durable state in several layers,
each with a different job: file-based **auto-memory** for atomic facts, a local
**semantic recall** index over the whole workspace, the curated **ODIN brain** for
long-term advice, the **knowledge base** for notes, and **threads** for live
operational state. They are explained together in **[Memory & ODIN](memory-odin.html)**.
The recall index runs on a local embedder; connecting it is in
**[AI models](MODELS-SETUP.html)**.

**Every one of those layers is a file or a SQLite database, and that is a rule
rather than an accident** (`.claude/rules/persistence.md`). File formats are free —
Markdown, JSON, JSONL, YAML, whatever fits the data. What the rule refuses is a
server database.

The reason is durability, not taste. A database that runs as its own process stakes
its whole durability contract on `fsync` reaching physical media. On WSL2, where the
filesystem is ext4 inside a VHDX inside a Hyper-V guest, whether a guest flush is
passed through to the disk is undocumented by Microsoft. SQLite in the same VHDX
inherits exactly the same uncertainty — but it inherits nothing else: no daemon to
supervise, no port, no service account, no separate backup, no version upgrade, and
nothing left running when the writing process exits. On a laptop whose only sleep
state is hibernate, that difference is the whole argument.

The constraint governs this workspace. It says nothing about what a product
deployment may use.

---

## 5. Daemons: optional, console-first

Background services (a loopback dashboard, mail and calendar sync, comms monitors) are
optional. Every capability they offer is also operable from the terminal and from
chat: the dashboard is a convenience layer, never a dependency. A capability that only
worked through a browser would be a defect. Install and operation are in
**[Daemons & scheduled tasks](daemons.html)**.

The obligations themselves — ship a non-web path first, keep the backing store the
source of truth, never make a web view the only mutator, degrade clearly — and the
three checks that verify them live in `.claude/rules/console-first.md`, which the agent
reads every turn. What follows is the rationale and the boundary, moved here verbatim
from that rule on 2026-08-20.

### The principle

Misha operates from the terminal and from Claude Code chat. A capability that can only be exercised through a browser is, in practice, invisible to how he works and couples core function to a presentation layer. So the browser is additive — it visualises and accelerates — but it is never the only path to any action.

Daemons are fine. A headless always-on service (the bridge daemon, sync-exchange, sentinel, etc.) is acceptable as a dependency because it has no UI requirement — you drive it from a CLI or chat. A *web dashboard* dependency is not acceptable. The distinction: depending on a running process is allowed; depending on a rendered web page is a defect.

### Scope

**In scope (rule applies):** every script, skill, daemon, workflow, and feature that exposes an action or surfaces state — Action Queue, Cold-Sweep, recall, intel, CRM, comms, content, operations. New capability of any kind.

**Out of scope:** purely presentational polish of the dashboard itself (a chart, a colour, a layout) that adds no capability the CLI/chat lacks; third-party web tools the workspace merely calls (LinkedIn, Google); and the dashboard's role as a *viewer* of state that is already fully CLI/chat-operable.

### How this composes with the visual-design rule

`.claude/rules/visual-design-discipline.md` governs how the dashboard *looks* when it exists. This rule governs whether the dashboard is *required*. They do not conflict: build the CLI/chat path first (this rule), and when a web view is also built, design it well (that rule). A beautiful dashboard that is the only way to do something still violates this rule.

---

## 6. The control plane

Three controls are woven through everything above, not bolted on:

- **Engine and data stay apart**, enforced by layered guards and an unbypassable
  push-time wall.
- **Outbound send is always human-gated**, through the Action Queue.
- **Secrets never reach a remote**, through a commit-time warning and an authoritative
  push-time content scan.

All three are detailed in the **[Security model](SECURITY-MODEL.html)**.

---

## 7. The documentation map

| To... | Read |
|---|---|
| stand up a clone | [Prerequisites](prerequisites.html), [Deployment](DEPLOYMENT.html), [Quickstart](QUICKSTART.html) |
| make it yours | [Make it yours](MAKE-IT-YOURS.html) |
| connect models | [AI models](MODELS-SETUP.html) |
| connect services | [Integrations & credentials](INTEGRATIONS-SETUP.html) |
| run the background services | [Daemons](daemons.html) |
| understand memory | [Memory & ODIN](memory-odin.html) |
| understand the data layout | [Data overlay structure](data-structure.html) |
| understand the security model | [Security model](SECURITY-MODEL.html) |
| build on the engine | [Extending the engine](EXTENDING.html) |

## 8. HELM and YARD: working on the engine and the data at once

Two pieces of work that share one folder collide. Two Claude sessions in the same
clone edit the same files: one rewrites a module while the other writes tests for
what it just replaced. A terminal multiplexer gives each session its own window,
and windows are not walls.

The answer is two roles with no overlap.

**HELM** is the main clone on `main`. One session. What is live: the daemons,
mail and the calendar and Telegram, every git operation in the data overlay,
`/backup`, `/sync` and any push at all, and the review and merge of finished
branches.

**YARD** is a git worktree of the engine on its own branch, checked out under
Herdr's `worktrees.directory` — outside the engine clone, so a file created by
accident inside a task cannot land in the engine's working tree. Herdr lays the
checkouts out as `<directory>/<repo>/<branch-slug>`, and that middle segment is
its own, not configurable: `worktrees.directory` is the only key under
`worktrees.` in the 0.8.2 configuration reference. Nothing here is live. A YARD
holds one piece of work in isolation, whatever the work is: an engine change, a
research task, a keynote deck, a program being built, work against memory.
Several run at once, doing different kinds of work, and the engine can be taken
apart in one of them while HELM keeps running on the merged version.

*Does this act on something live?* Live means a running process, an outside
party, or a shared index that other work reads, which is the list HELM holds
above. Everything else runs in a YARD.

### The one rule

**Every task writes its files into the data overlay. Only HELM may record the
OVERLAY's history.** The engine branch is a different question, answered in the
subsection below: the task commits that itself.

Files differ per task, so they do not collide. What collides is the moment of
recording: the overlay is one working tree with one git index for the whole
machine, so a commit from a task sweeps up a neighbour's half-finished draft and
whatever the mail sync is writing at that instant into a single commit nobody
reviewed.

The first half is an obligation, not a permission. No artifact is ever saved in
the engine: a research result, a document, a deck, a plan, a note all land in
the overlay under `outputs/...`, resolved through `get_data_root()` and the
`get_*_dir()` helpers rather than a path assembled against the checkout. A YARD
reaches the real, shared overlay by design, which is what makes the obligation
possible: the bootstrap copies `.env` from HELM, strips `WORKSPACE_ROOT`, writes
an absolute `HEADING_OS_DATA`, and asserts at step 7 that the resolved root sits
outside the checkout. The `engine-tree-clean` pre-commit hook fails a commit
that leaves a data artifact in the engine clone, and `scripts/leak-guard.py`
refuses a hardcoded data path in engine code.

### The cycle, and why a task commits its own branch

Create the worktree, work in it, commit the engine branch in it, HELM merges the
branch into `main`, delete the worktree. HELM keeps the merge, the push,
`/backup` and the daemons; the rest is the task's. The commit needs the
operator's word typed in that yard, or a brief from HELM, and nothing else
authorises it: HELM is the one session the operator sits at himself, so a yard
instructed by another yard is a chain in which no link spoke to a human. The
release gate is unchanged, and it is not that authorisation: it asks for a typed
word in the current turn of the committing session, which fixes the turn and
never the author.

This is written out because the place where it belonged said nothing, and on
2026-09-04 and 05 the silence was filled with an invented prohibition: task
branches were committed from HELM by hand with `git -C <yard> commit` and the
practice was recorded in commit messages as policy. No rule said it, in
`CLAUDE.md` or in `.claude/hooks/_dispatch.py`. Four properties make the commit
safe, each MEASURED 2026-09-05 on this repository from a YARD:

| Property | How it was checked | Result |
|---|---|---|
| A worktree has its own index, and a branch belongs to one worktree | `git rev-parse --git-path index`; `git worktree list` | `<HELM>/.git/worktrees/<id>/index`, one per checkout, six live worktrees on six distinct branches |
| Hooks are the shared clone's, so the gates are the same ones | `git rev-parse --git-path hooks`; `scripts/install-hooks.py --check` | resolves to `<HELM>/.git/hooks`; the framework gate reports ARMED |
| The release gate does not soften in a worktree | read `check_release_gate` in `.claude/hooks/_dispatch.py` | no clone-type branch in it: approval of the WORK is never approval of the commit |
| Publishing is off by machinery | `git remote -v` | push URL is `DISABLED://use-helm-to-publish`, set per worktree at step 3 of `yard-bootstrap.sh` |

The first row is git's own behaviour and the guard in `_dispatch.py` refuses
`git worktree add` from a YARD, so the two-worktrees-one-branch case cannot be
demonstrated from inside a task; the registry listing is the evidence available
there.

What the third row does NOT say: the gate confirms that the session's most
recent prompt asked for a commit in this turn, not that a human at this machine
typed it. A brief arriving from a neighbouring session is recorded the same way,
so in a YARD driven by another session the gate holds the turn boundary and
nothing about authorship. That is why the authorisation above names HELM rather
than "a neighbouring session": no machinery can tell the two apart, so the rule
has to, and it is written in `CLAUDE.md` where the committing session reads it.

### Daemons are HELM's alone

Operator directive, 2026-09-03. From a YARD, take no action after which a
running process on this machine appears, disappears, or changes behaviour. That
category is the rule; starting, installing and restarting are examples of it and
not a list to implement. It reaches the daemon and everything deciding whether
it runs: unit, timer, healthcheck, PID file, spawner, and the readers that
inspect one in order to decide. Every daemon entry point in the tree therefore
carries the clone guard, including the ones that only read.

The line runs through EXECUTION, not through editing. Writing the SOURCE CODE of
a daemon in a YARD is ordinary engine work; that is what a YARD is for, and HELM
does not change engine code at all. The guard refuses to RUN from a non-main
clone and does not touch the file. That code reaches HELM by one route, the
merge of the task branch, and that merge has to happen before the worktree is
deleted.

The categorical form is a repair, not a preference, and the repair is the reason
the paragraph above refuses to be a list. `CLAUDE.md` used to forbid "daemon
install, restart or uninstall", and exactly the three scripts carrying those
words in their filenames were guarded. The prohibition had been implemented as a
match against a list of verbs, "start" was not on the list, and so a YARD could
not install the mail daemon but could start one. MEASURED
2026-09-03: a second Exchange daemon ran twelve hours out of a worktree beside
the operator's real one, auto-spawned because liveness is read from a PID file
INSIDE the checkout and a fresh worktree has none — so the HELM service was
invisible to the check that decided to "helpfully" start another.

### The gap parallel work opens, and how it is closed

The engine's layers that keep private data out of the code all run on this
machine. The strongest of them stands on the sanctioned upload path in
`scripts/push-all.py` and has no off switch — but "no off switch" means on that
path. A finished task pushing its branch with an ordinary `git push` would go
around it entirely, and the engine repository is public, so any branch pushed is
visible to everyone immediately.

The repair is not another wall on the road out. It is removing the second road:
**a task sends nothing.** Every worktree shares one repository, so HELM can
already see a task's branch with nothing transferred. Three independent
mechanisms hold it:

| Mechanism | Where | What gets past it |
|---|---|---|
| `remote.origin.pushurl` disabled per worktree | git config | nothing but manual reconfiguration |
| `require_main_clone()` in publishing and maintenance entry points | the scripts | nothing; exit 2 naming HELM |
| `check_yard_write_guard` | `.claude/hooks/_dispatch.py` | nothing; refused before the command runs |

### The predicate, and why it is not a variable

Telling a worktree from the main clone is a property of git, not an agreement.
MEASURED 2026-09-03 on this repository: `<root>/.git` is a DIRECTORY in the main
clone and a FILE in a worktree, and `rev-parse --git-dir` equals
`--git-common-dir` only in the main clone. That cannot be faked, forgotten, or
lost by a new shell. `scripts/utils/clone_guard.py` is the one implementation;
`scripts/lib/require-main-clone.sh` mirrors it in bash builtins for callers that
run with a pinned PATH, and a test asserts the two agree.

Earlier designs used a marker file and `CLAUDE_PROJECT_DIR`. Both answer "this is
the main clone, allow it" when the thing they read is simply missing, which is
fail-open in the one place that must fail closed.

### Proving a guard can fire

A guard pointed at the wrong tree reports clean, and a clean report is what a
healthy guard produces. The two states are indistinguishable by their result.

So provisioning a YARD ends with a canary: a decoy file is written to a path the
engine itself classifies as private, and the tree-clean wall is REQUIRED to fail
on it. If it passes, the wall is looking at another tree and the task does not
start. The probe path is chosen rather than assumed — the wall reads
`git ls-files --others --exclude-standard`, so a decoy git ignores is invisible
to it, and MEASURED 2026-09-03 six of the eight obvious candidates are gitignored.

Every guard added or changed from here ships with a bidirectional test: one case
proving it stays quiet on a clean tree, one proving it fires on a real violation
in the checkout under test. The failing case must fail against the previous
version of the guard; if it passes there too, it proves nothing.

### What a fresh worktree does NOT have

MEASURED 2026-09-03, and each of these looks healthy:

- `.claude/settings.local.json` — absent, so eleven PreToolUse walls including
  the release gate and the secret scanner are unregistered.
- `.env` — absent, so `get_data_root()` falls through to the bundled `examples`
  tree. Every guard gated on "the data root differs from the workspace root"
  stays armed, against example data.
- `.venv` — absent, which is why the bootstrap writes its status with `printf`
  and calls no pinned interpreter before `uv sync`.

`WORKSPACE_ROOT` is the mirror hazard: `get_workspace_root()` reads it first, so
one line copied from HELM's `.env` points every guard at the untouched main
clone. The bootstrap strips it and then verifies the resolved root, because
checking the intent is not checking the result.

Operating guide, the eleven bootstrap steps, and diagnosis:
`scripts/herdr/README.md`.

---

*HEADING OS · Architecture · maintained by Misha Hanin · the Navigation Principle in
software: durable state over one-shot prompts, verified completion over hopeful
timeouts.*
