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
| `measurable-execution.md` | Always-on | Before any non-trivial task, agree the metric that defines "done ideally" and, when the task signals a fit, propose `/goal` (verifiable end-condition) or `/loop` (recurrence). Attaches to prompt-refinement Phase 1; advisory. |
| `skill-router.md` | Always-on | Matches a natural-language message to the right skill, with a full skill registry, exclusions, and compound-trigger handoff. |
| `skill-orchestrator.md` | Always-on | Detects compound workflows and dispatches parallel research agents while serialising writes, bounded by a concurrency cap and approval gates. |
| `console-first.md` | Always-on | Every capability must be operable from the terminal and chat. The web dashboard is a convenience layer, never a dependency. |
| `memory-discipline.md` | Always-on | Before any consequential action, open the authoritative record, not the pointer that surfaced it (an index hook, recalled snippet, or summary); and keep the always-loaded pointer layer lean - hooks carry topic + pointer, never volatile state. Enforced advisory by `scripts/memory-hygiene.py`. |

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
| `documentation-style.md` | Path-scoped | A subset of ASD-STE100 Simplified Technical English for the procedural pages and skill instruction bodies: imperative steps, active voice, a word limit per sentence, one action per step, and a warning that arrives before the step it guards. Advisory audit by `scripts/ste-check.py`. Explanatory pages and all outbound prose stay out of scope, where `humanization.md` governs instead. |
| `trace-id.md` | Path-scoped | One correlation ID per process tree, so a single grep answers "what happened in this run?" across the daemon logs. |

## Prompt refinement in depth

`prompt-refinement.md` is the rule that sits between your message and the engine's first action. Where the [skill router](skills-mcp-plugins.html) decides *which* capability runs, prompt refinement decides *whether the engine has understood you* before it runs anything. The premise is plain: re-asking is cheap and redoing is expensive, so ambiguity is resolved up front rather than discovered in a wrong deliverable.

### The three-phase flow

Every substantive request passes through three phases before work begins.

**Phase 1 — Interpret and expand.** The engine restates your request as an execution-ready brief. It opens with the line *"It looks like you want me to do the following:"* and then sets out the objective, the concrete scope (what is in, what is out), the deliverables and file outputs, the constraints and quality bar, and any assumptions it is making. Each assumption is flagged explicitly, so you can correct a misread before it costs anything. The brief closes with a measurable-execution line — how the result will be judged — so success is defined before work starts, not asserted after.

**Phase 2 — Clarify, only when needed.** If a genuine ambiguity blocks a confident expansion, the engine asks focused questions first, in a single block, and asks only what it actually needs. It does not invent the missing detail; it names the gap.

**Phase 3 — Await approval, then execute.** After presenting the brief the engine stops and waits for an explicit go: "approved", "proceed", "go", "execute", or "yes". On approval it executes strictly against the agreed brief. If a new decision surfaces mid-execution, it stops and asks again rather than widening scope on its own.

### Escape valves

The flow is calibrated for ambiguous or large-scope work, not for trivia. The engine acts immediately, skipping the ritual, in exactly three cases:

1. **`!` prefix.** A message that starts with `!` (for example `!fix this typo`) runs directly. This is the fastest way to say "just do it".
2. **A direct reply** to a question the engine itself asked.
3. **A trivial one-step correction** to work just produced: a typo, a rename, a single-line tweak.

When in doubt, the engine runs the protocol. Over-refinement is cheaper than misaligned execution.

### Forcing the behaviour: /align, /devil, /burst

When you know up front that a decision carries weight, three explicit escalations override the default posture. Each keeps the Phase 3 approval gate.

| Command | Default · range | What it does |
|---|---|---|
| `/align N` | 5 · 1–10 | Replaces Phase 1's long expansion with a compact preamble and asks exactly N numbered clarifying questions, each carrying a recommendation. Use it before wide-scope or branching work when you want the engine to interrogate the request first. |
| `/devil N` | 5 · 1–10 | Inverts the default validate-and-proceed posture into contrarian critique: N severity-tagged objections to a recent decision or claim, from distinct angles (correctness, scope, cost, timing, alternatives, second-order effects). It stops early rather than fabricate weak points. |
| `/burst N` | 3 · 2–5 | Produces N variants of the latest content artifact — N-1 attacking distinct axes (opener, tone, structure, lens, length, voice) plus one deliberate swing-the-other-way inversion — to compare directions or break a stuck draft. |

### Interplay with the corporate-docs guardrail

When a request matches one of the five locked doctypes (letter, proposal, partnership doc, official doc, one-pager), the corporate-docs guardrail requires the engine to announce the chosen skill immediately. The two rules reconcile cleanly: the announcement happens *inside* Phase 1, on the opening line, then the expanded brief follows and the Phase 3 approval gate still holds. The `!` escape valve bypasses both.

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
