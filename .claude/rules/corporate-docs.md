<!-- version: 2.0.0 | last-updated: 2026-09-04 -->
# Corporate Document Guardrail

Last Updated: 2026-09-04
Last Verified: 2026-09-04

Always-active rule governing five external-facing document types. When any
executive requests one of these, the correct 31C-branded skill is applied
automatically - they never have to ask for branding, tone or format. Resident
because it fires on a MESSAGE, which no path can trigger on.

Everything that fires only AFTER a doctype is chosen - brand enforcement, the
rendering pipeline, output naming, classification, change control - is in the
path-scoped `.claude/rules/corporate-docs-authoring.md`, which loads when a skill
opens `reference/corporate-style-guide.md` (as this rule requires it to) or
touches the renderer, the templates or `outputs/documents/`.

## In-Scope Document Types

| # | Doctype | Skill | Render formats |
|---|---|---|---|
| 1 | External Letter | `/corporate-letter` | PDF + DOCX |
| 2 | Proposal | `/proposal` | PDF + DOCX |
| 3 | Partnership Document (MOU / LOI / term sheet) | `/partnership-doc` | PDF + DOCX |
| 4 | Official Document (resolution, formal notice, position letter) | `/official-doc` | PDF + DOCX |
| 5 | OnePager (xPager) | `/xpager` | PDF + HTML |

**Out of scope:** internal Tribe messages, LinkedIn posts, press releases,
marketing collateral. These have dedicated skills (`/tribe-message`,
`/linkedin-post`) and do not use the five locked templates.

## Trigger Protocol

When a message matches one of the five, auto-select the skill, announce the
selection, and apply the locked template. The executive does not need to name it.

**Classifier signals.** The per-doctype trigger phrases are the five skills' rows
in the always-on registry of `.claude/rules/skill-router.md`, generated from
their own `x-heading-routing.triggers`. They are deliberately not restated here:
a second hand-maintained copy of the same phrases in a second always-on rule is
paid for on every session and drifts from the generated one the first time a
skill is re-scoped.

**Ambiguity resolution.** If the message spans two categories ("partnership
proposal" could be commercial `/proposal` or legal `/partnership-doc`): look for
structural signals first - legal/MOU/LOI/term sheet goes to `/partnership-doc`,
commercial pricing/module activation goes to `/proposal`. If still ambiguous, ask
one targeted question: "Commercial proposal with pricing and modules, or MOU/LOI
defining mutual obligations?"

**Silent fall-through is forbidden.** If a request unambiguously falls into one
of the five types, Claude MUST announce the selection in the first response line:
`Using /{skill} (external letter template). Confidentiality footer applied, GT
Standard typography, 31C letterhead.` Announcement is non-negotiable - it gives
the executive an audit trail of what template is being applied.

**Read `reference/corporate-style-guide.md` before drafting any of the five.**
Required, no exceptions; opening it is also what loads
`.claude/rules/corporate-docs-authoring.md` with the rest of the obligations:
brand enforcement, rendering, output naming, classification, and the change
control on the locked templates. Change control moved there on 2026-09-04 because
it fires when someone EDITS a template, and that rule is already path-scoped to
the template directory, so the edit itself loads it.
