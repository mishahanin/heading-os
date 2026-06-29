# Calibrate - Output-prose plain-English mandate

Consumed by: `.claude/skills/calibrate/SKILL.md` Voice rules section.
Last Updated: 2026-05-15

Every candidate's one-line summary, body explanation, and rationale must be
written in plain language a smart non-expert reader can scan in one pass.
Short words, short sentences, one idea per sentence. The user reads the
candidate list and picks `apply 1, 3, 5` in seconds; dense tech-writing
forces them to translate before they can decide.

## Banned vocabulary in candidate prose

Replace with the plain forms in parentheses:

- `encode` -> save, write down
- `canonical` -> right, agreed
- `deprecated` -> old, retired
- `leverage` -> use
- `surface` -> show, raise
- `propagate` -> spread, push out
- `paradigm`, `paradigm shift` -> approach, change
- `optimal` / `suboptimal` -> best / not great
- `articulation` / `instantiation` -> wording / example
- `methodology` -> method, way
- `utilize` -> use

## Examples

- Bad: "Encode this preference as a reference memory so future sessions
  inherit the editable-textarea behavior by default."
- Good: "User wants editable textareas every time. Save this so next
  session does it from the start."

- Bad: "Update Step 7 to point to Tool Y as the canonical provider,
  replacing the deprecated Tool X reference."
- Good: "The skill says use Tool X. That is wrong. Change it to Tool Y."

If a 12-year-old reading the candidate out loud would not roughly
understand it, rewrite it.
