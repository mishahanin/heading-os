# Council — Transcript Format

Consumed by: `.claude/skills/council/SKILL.md` (Phase 5 — Persist the transcript).
Last Updated: 2026-06-18

The exact transcript file content written to
`outputs/operations/council/{YYYY-MM-DD}_council_{HHMMSS}_{slug}.md`. Kept out of the
SKILL body so the phase logic stays under the inline budget. The SKILL body holds the
path, `{HHMMSS}` collision rule, slug rules, and the `--no-log` detection.

## Transcript content

```markdown
---
timestamp: {ISO 8601 with seconds}
mode: {independent|critique}
models_requested: [gemini, grok, kimi]   # or subset based on flags
models_succeeded: [gemini, grok, kimi]   # or subset based on runtime
gemini_model: {gemini model name}  # only if Gemini was requested (include even on failure — model name is still known from the command)
grok_model: {grok model name}      # only if Grok was requested (include even on failure — model name is still known from the command)
kimi_model: {kimi model name}      # only if Kimi was requested (include even on failure — model name is still known from the command)
---

# Council Consultation - {question excerpt, max 80 chars}

## Question / Draft

{question text or draft text}

## Context (sent to all models)

{context, or "_(none provided)_"}

## Gemini's full response (verbatim)

{Gemini's stdout, unmodified — OR "_(not requested)_" — OR "**FAILED:** {error message}"}

## Grok's full response (verbatim)

{Grok's stdout, unmodified — OR "_(not requested)_" — OR "**FAILED:** {error message}"}

## Kimi's full response (verbatim)

{Kimi's stdout, unmodified — OR "_(not requested)_" — OR "**FAILED:** {error message}"}

## Claude's view

{the bullets from Phase 3}

## Side-by-side (as presented to user)

{the rendered Phase 4 output — same content shown to the user}

## Decision (filled by Misha)

_(empty - fill manually after deciding)_
```

After writing, show the full absolute path of the transcript at the end of the chat
output, in the form `Transcript: <workspace_root>/outputs/operations/council/<filename>`.
Resolve `<workspace_root>` at runtime from the foundation helper
(`python3 scripts/utils/paths.py`, or `from scripts.utils.workspace import get_workspace_root`)
— never embed a drive letter or platform-specific literal. Example:
`Transcript: <workspace_root>/outputs/operations/council/2026-05-09_council_143201_tier1-vs-tier2.md`.
