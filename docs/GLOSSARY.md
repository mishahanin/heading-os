# Glossary

The vocabulary the engine and its documentation use. Two kinds: the operating philosophy (which you can adopt or rename for your own house), and the engine mechanics (which are structural).

## Operating philosophy

HEADING OS is named after the way it frames work. This vocabulary runs through the skills, the rules, and the outputs. On a fresh clone you can keep it, or rename it to your own via [make it yours](MAKE-IT-YOURS.html); the [terminology rule](RULES-REFERENCE.html) enforces whichever set you choose.

| Term | Meaning |
|------|---------|
| **Navigation Principle** | The core idea: set a heading and hold it, correcting course as conditions change, rather than steering toward a fixed point and hoping. Durable state over one-shot prompts. |
| **Operational state** | A sustained condition you maintain, not a target you hit once. Priorities are framed as states to hold. |
| **Heading** | The direction of movement. Adjustable, unlike a fixed destination. |
| **Sea state** | The external conditions affecting operations. |
| **Course correction** | An adjustment that restores an operational state after drift. |
| **Drift** | Unconscious movement out of an operational state. |
| **State check** | A short diagnostic assessment that replaces the traditional long review. |
| **Crunch mode** | A heightened operational state: compressed decisions, tightened communication, zero tolerance for drift. |

## 31C product vocabulary

These are the author's company terms. They appear in the default data examples and some skills. On your own clone you would replace them with your own.

| Term | Meaning |
|------|---------|
| **31C / 31 Concept** | The company that builds and maintains HEADING OS. |
| **Tribe** | The company's people. The house word for the team. |
| **ODUN.ONE** | 31C's sovereign deep-packet-intelligence platform. |
| **DPI+** | Deep Packet Intelligence, the next generation beyond traditional deep packet inspection. |

## Engine mechanics

Structural terms. These are the same on every clone.

| Term | Meaning |
|------|---------|
| **Engine** | This repository (`.heading-os`): skills, scripts, rules, hooks, tests. Shareable, public, no real data. |
| **Data** | Your separate private repository (`.heading-os-data`): CRM, knowledge, outputs, threads, context. Never in the engine, never leaked. |
| **Data root** | The single seam (`get_data_root()`) through which the engine resolves your data repository at runtime, as a sibling directory or via `HEADING_OS_DATA`. |
| **Skill** | A slash-command workflow (in `.claude/skills/`) invoked from natural language by the router. See the [skills catalogue](skills-mcp-plugins.html). |
| **Rule** | An always-on behavioural file (in `.claude/rules/`) that governs the session without being invoked. See the [rules reference](RULES-REFERENCE.html). |
| **Hook** | A script the harness runs at a fixed event (before a tool call, after a write, at session start). See the [hooks reference](HOOKS-REFERENCE.html). |
| **Daemon** | An optional always-on background service (the bridge dashboard, mail and calendar sync), driven from the CLI, never required through a browser. See [daemons](daemons.html). |
| **Router** | The `skill-router` rule that maps a natural-language message to the right skill. |
| **Classification** | The engine / private / corporate destination of a record, resolved from `config/routing-map.yaml`. See [configuration](CONFIGURATION.html). |
| **Leak guard** | A code layer that keeps private data out of the engine tree. Part of the [security model](SECURITY-MODEL.html). |
| **Lethal trifecta** | The failure mode where private-data access, untrusted content, and outbound send meet in one execution. The engine keeps the send leg permanently human-gated. |
| **Send-gate** | The control that makes every outbound send require an explicit human click. Enforced in code, not just policy. |
| **Action Queue** | The single lane where proactive agents deposit drafted actions for a go / no-go, driven from the terminal. |
| **ODIN** | The persistent knowledge brain: ingests material, builds principles, gives referenced advice. See [memory and ODIN](memory-odin.html). |
| **Recall** | Workspace-wide semantic search over the local memory index, answered only from retrieved sources with citations. |
| **Console-first** | The rule that every capability works from the terminal and chat; the dashboard is convenience, never a dependency. |

## Related

- [Terminology rule](RULES-REFERENCE.html): enforces the vocabulary above.
- [Make it yours](MAKE-IT-YOURS.html): renaming the philosophy and product vocabulary for your own house.
- [Architecture](ARCHITECTURE.html): how the engine mechanics fit together.
