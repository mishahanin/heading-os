<!-- version: 1.0.0 | last-updated: 2026-04-28 -->
# Prompt Refinement Protocol

Last Updated: 2026-05-13
Last Verified: 2026-05-13

Always-active rule. Governs how Claude interprets every user prompt before acting.

## Three-Phase Flow

For EVERY user prompt, Claude MUST follow this three-phase flow. No exceptions except the Escape Valves below.

### Phase 1 - Interpret & Expand

Restate the request as a fully expanded, execution-ready prompt. Open with:

> "It looks like you want me to do the following:"

Then present the expanded prompt with:

- Clear objective
- Concrete scope (what's in, what's out)
- Deliverables and file outputs
- Constraints, tone, and quality bar
- Any assumptions being made (flagged explicitly)

### Phase 2 - Clarify (only if needed)

If ambiguity blocks a confident expansion, ask focused questions BEFORE presenting the expanded prompt. Ask only what is necessary. Do not invent detail - flag gaps.

### Phase 3 - Await Approval, Then Execute

After presenting the expanded prompt, STOP. Do not execute. Wait for explicit approval: "approved", "proceed", "go", "execute", or "yes".

On approval, execute strictly against the approved prompt. Do not expand scope mid-execution - if new decisions arise, stop and ask.

### Explicit escalation: `/align N`

The three-phase flow above is always active and leaves "when to clarify"
to Claude's judgement. When the user knows up front that scope matters
and wants to force clarification with a specific number of questions,
they invoke `/align N` (default N=5, range 1-10). /align overrides
Phase 1's expansion length with a compact 2-5 sentence preamble, replaces
Phase 2 with exactly N numbered + lettered questions carrying
per-question recommendations, and preserves Phase 3's approval gate.
See `.claude/skills/align/SKILL.md`.

### Explicit critique escalation: `/devil N`

The default posture is to validate and proceed. When the user wants the
opposite - explicit contrarian critique of a recent decision or claim -
they invoke `/devil N` (default N=5, range 1-10). /devil produces N
severity-tagged critique points from distinct angles (correctness,
scope, cost, timing, alternatives, second-order effects), exits, and
lets the user reply with point numbers or move on freely. Honesty floor:
if fewer than N defensible angles exist, the skill stops early rather
than fabricate. See `.claude/skills/devil/SKILL.md`.

### Explicit variation escalation: `/burst N`

When the user wants the same content delivered N different ways - to
compare directions, escape a stuck draft, or run the convergence pattern
(produce N variants, pick one, /burst again from there) - they invoke
`/burst N` (default N=3, range 2-5). /burst produces N variants of the
latest assistant-produced content artifact: N-1 spread variants attacking
distinct axes (opener, tone, structure, lens, length, voice, metaphor)
plus one mandatory "swing-the-other-way" variant inverting a defining
property of the original. See `.claude/skills/burst/SKILL.md`.

## Escape Valves

Skip the protocol and act directly ONLY when:

1. The user prefixes the message with `!` (e.g., `!fix this typo`)
2. The message is a direct reply to a question YOU asked
3. The message is a trivial one-step correction to work just produced (a typo, a rename, a single-line tweak)

When in doubt, run the protocol. Over-refinement is cheaper than misaligned execution.

## Output Discipline

- Keep the expanded prompt tight. Brevity over verbosity.
- Do not pad with corporate language, hedging, or preamble beyond the required opener.
- Use plain prose and short lists. No ceremonial formatting.

## Interaction with Corporate-Docs Guardrail

The corporate-docs guardrail (`.claude/rules/corporate-docs.md`) requires immediate skill announcement when a request matches one of the five locked doctypes (letter, proposal, partnership-doc, official-doc, xpager). The two rules reconcile as follows:

- The skill announcement happens **inside** Phase 1, not before it. Open with the announcement, then present the expanded prompt.
- Example: `Using /proposal (commercial proposal template, locked typography, GT Standard, 31C letterhead). It looks like you want me to do the following: ...`
- Phase 3 approval gate still applies. Do not start drafting until the user approves the expanded prompt.
- Escape Valve 1 (`!` prefix) bypasses both rules and lets the skill execute directly.
