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
- The measurable-execution block as the final element, per `measurable-execution.md` (metric always; `/loop` and `/goal` proposals only on signal)

### Phase 2 - Clarify (only if needed)

If ambiguity blocks a confident expansion, ask focused questions BEFORE presenting the expanded prompt. Ask only what is necessary. Do not invent detail - flag gaps.

### Phase 3 - Await Approval, Then Execute

After presenting the expanded prompt, STOP. Do not execute. Wait for explicit approval: "approved", "proceed", "go", "execute", or "yes".

On approval, execute strictly against the approved prompt. Do not expand scope mid-execution - if new decisions arise, stop and ask.

### Explicit escalations

Three commands override the default judgement above. Each is typed by the
operator, which loads the skill that defines it, so only their names are
resident. What each does to the three phases:
`reference/prompt-refinement-escalations.md`.

- `/align N` — force exactly N clarifying questions (default 5, range 1-10).
- `/devil N` — N severity-tagged contrarian critique points (default 5, 1-10).
- `/burst N` — the same content delivered N ways (default 3, range 2-5).

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

When a request matches one of the five locked doctypes,
`.claude/rules/corporate-docs.md` requires an immediate skill announcement. It
happens **inside** Phase 1, not before it: announce, then present the expanded
prompt. Phase 3's approval gate still applies, and Escape Valve 1 bypasses both
rules. Worked example:
`reference/prompt-refinement-escalations.md` § Interaction with the corporate-docs
guardrail.
