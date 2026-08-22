<!-- version: 1.1.0 | last-updated: 2026-08-20 -->
# Architecture

Last Updated: 2026-08-20. Consumed by: readers of the docs site, and
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

---

*HEADING OS · Architecture · maintained by Misha Hanin · the Navigation Principle in
software: durable state over one-shot prompts, verified completion over hopeful
timeouts.*
