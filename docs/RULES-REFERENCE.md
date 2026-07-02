# Rules reference

The always-on behavioural layer: what governs every Claude Code session before a single skill runs.

Skills are invoked. Rules are always in force. Every file in `.claude/rules/` loads into the agent's context automatically, so the engine behaves consistently across sessions without anyone asking for it. This page is the catalogue of that layer, the rule equivalent of the [skills catalogue](skills-mcp-plugins.html).

## How rules load

Rules live as markdown files in `.claude/rules/`. Two kinds:

- **Always-on.** No `paths:` frontmatter, so the rule is active in every session. Most rules are this kind.
- **Path-scoped.** A `paths:` list in the frontmatter (glob patterns). The rule activates only when the work touches a matching path. Example: the DataStore rule loads only when you edit content or deliverables.

You never invoke a rule. You change the engine's behaviour by editing or adding rule files, then the change is in force on the next session. A rule that changes voice, classification, or a safety control is load-bearing: read the rule's own "Change control" note before editing it.

## The rules, by function

### Voice and prose

Every piece of natural-language output the engine produces passes through this group.

| Rule | Scope | What it enforces |
|------|-------|------------------|
| `voice.md` | Always-on | Core communication style: truth first, draft-then-iterate, bilingual reply matching the user's language, no structural changes without approval, the canonical "no double dashes" rule. |
| `humanization.md` | Always-on | Writes all prose so it reads as written by a person, not a machine: specificity density, committed stance, deliberate rhythm, a banned-vocabulary list, and a test-before-rewriting calibration gate. |
| `hidden-chars.md` | Always-on | Zero invisible Unicode in any output (zero-width spaces, soft hyphens, non-breaking spaces, BOM). Validated with `scripts/sanitize-text.py`. |
| `terminology.md` | Always-on | House vocabulary: the Navigation Principle, operational states, and the correct product and philosophy terms. See the [glossary](GLOSSARY.html). |
| `voss.md` | Always-on | Chris Voss tactical-empathy overlay on all outgoing communication: labels, calibrated questions, precise numbers, never split the difference. |
| `output-naming.md` | Always-on | The `YYYY-MM-DD_{type}_{slug}.{ext}` naming standard for everything written under `outputs/`, so the tree stays searchable and sortable. |

### Security and safety

The controls with teeth. These are enforced by hooks and code, not by prose alone. See the [security model](SECURITY-MODEL.html) and the [hooks reference](HOOKS-REFERENCE.html).

| Rule | Scope | What it enforces |
|------|-------|------------------|
| `lethal-trifecta.md` | Always-on | The one control every send-capable path inherits: outbound send is always human-gated, never autonomous. An agent may draft and queue, a human clicks before anything leaves. |
| `tiered-risk.md` | Path-scoped | The three-tier risk gate (`autonomous` / `notify` / `gated`) for Action Queue cards, and the invariant that a send-capable action always floors to `gated` regardless of config. |
| `security.md` | Always-on | Where secrets belong (never in tracked files), the defence layers, credential rotation, and incident response. |
| `classification.md` | Always-on | Record classification (engine / private / corporate) driven by `config/routing-map.yaml`. The fail-closed direction that keeps real data out of the engine. |
| `vpn-preflight.md` | Always-on | A confirmation gate before browsing operations that public services block from datacenter IPs (YouTube, Google, LinkedIn, some OSINT). |

### Workflow and interaction

How the engine interprets a request and routes it to the right work.

| Rule | Scope | What it enforces |
|------|-------|------------------|
| `prompt-refinement.md` | Always-on | The interpret then clarify then await-approval flow before acting, plus the `/align`, `/devil`, and `/burst` escalations and the `!` escape valve. |
| `skill-router.md` | Always-on | Matches a natural-language message to the right skill, with a full skill registry, exclusions, and compound-trigger handoff. |
| `skill-orchestrator.md` | Always-on | Detects compound workflows and dispatches parallel research agents while serialising writes, bounded by a concurrency cap and approval gates. |
| `console-first.md` | Always-on | Every capability must be operable from the terminal and chat. The web dashboard is a convenience layer, never a dependency. |

### Corporate output and design

Loads mostly when producing external-facing or visual artifacts.

| Rule | Scope | What it enforces |
|------|-------|------------------|
| `corporate-docs.md` | Always-on | Five external doctypes (letter, proposal, partnership doc, official doc, one-pager) auto-route to their locked branded templates with an announced skill selection. |
| `datastore.md` | Path-scoped | Validate factual claims against the authoritative source documents in `datastore/` before stating them as fact. |
| `visual-design-discipline.md` | Always-on | Design every visual so it reads as intentional and human-made, not a templated default. |

### Engineering and operations

Governs development work on the engine itself.

| Rule | Scope | What it enforces |
|------|-------|------------------|
| `development-standards.md` | Path-scoped | Quality gates for every artifact: research first, restraint (simplicity and surgical changes), a debugging discipline, and the skill / script / reference standards. |
| `documentation.md` | Always-on | Keep documentation in step with the code: which docs to update when a skill, script, rule, or structure changes, and the version-marker convention. |
| `trace-id.md` | Path-scoped | One correlation ID per process tree, so a single grep answers "what happened in this run?" across the daemon logs. |

## Editing a rule

1. Rules are plain markdown. Edit the file in `.claude/rules/`; the change is in force next session.
2. A rule with `paths:` frontmatter is path-scoped. Omit `paths:` (or leave it empty and always-active) for an always-on rule.
3. If your change touches a load-bearing control (a security gate, the classification map, voice), read that rule's "Change control" note first. Several controls, notably the send-gate invariant, are enforced by code and tests, not by the rule text.
4. Keep the `Last Verified` date current, per the documentation rule.

## Related

- [Skills, MCP and plugins](skills-mcp-plugins.html): the invoked layer that sits on top of these rules.
- [Hooks reference](HOOKS-REFERENCE.html): the code that enforces several of these rules before a write lands.
- [Security model](SECURITY-MODEL.html): how the safety rules compose into a defended system.
- [Configuration](CONFIGURATION.html): the config files several rules read from.
