<!-- version: 1.0.0 | last-updated: 2026-07-01 -->
# Architecture

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

---

## 5. Daemons: optional, console-first

Background services (a loopback dashboard, mail and calendar sync, comms monitors) are
optional. Every capability they offer is also operable from the terminal and from
chat: the dashboard is a convenience layer, never a dependency. A capability that only
worked through a browser would be a defect. Install and operation are in
**[Daemons & scheduled tasks](daemons.html)**.

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

*HEADING OS · Architecture · maintained by 31 Concept · the Navigation Principle in
software: durable state over one-shot prompts, verified completion over hopeful
timeouts.*
