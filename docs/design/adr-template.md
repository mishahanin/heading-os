# ADR <id>: <one-line title>

<One sentence: what this record decides and why it exists. Delete the angle-bracket prompts as you fill each section.>

Last Updated: <YYYY-MM-DD>
Status: proposed

## Context

<The situation that forces a decision. What exists today, what is missing or wrong, and the constraint that makes this non-trivial. State the problem so a reader who has never seen the code understands why a decision is needed. Keep it factual and grounded in the real tree.>

## Decisions

<The choices taken, numbered. Each is a claim plus its rationale, not a description of the code.>

1. **<Decision>:** <why this and not the obvious alternative>.
2. **<Decision>:** <rationale>.

## Alternatives considered

<The paths not taken and why each was rejected. This is the part a future reader values most: it stops the same rejected idea being re-proposed.>

- **<Alternative>:** rejected because <reason>.

## Resolved open questions

<Questions that were genuinely open during design, and how each resolved. Omit the section if there were none.>

1. **<Question>:** <resolution and the reasoning>.

## Engine and data boundary

<For anything that touches the engine/data split: state which artifacts are engine-side (public, code) and which are DATA-side (private, real content), and confirm nothing private leaks into a public artifact. Omit if the change does not touch the boundary.>

## Relations

<Links to related records or playbook items this decision depends on, precedes, or supersedes. Omit if none.>
