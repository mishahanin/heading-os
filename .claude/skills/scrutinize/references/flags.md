# /scrutinize — target and flag catalog

Consumed by: `.claude/skills/scrutinize/SKILL.md` (loaded by Phase 0 step 1,
which loads every file in this directory).

Last Updated: 2026-08-20

Moved out of the SKILL.md body on 2026-08-20. The body sat 94 bytes under the
18432-byte hard budget, so a one-line frontmatter addition broke the CI gate.
This block is a catalog a reader consults, not a step the skill executes, which
makes it reference material by the same test every other file here passes.

## Target

`target` (optional) — one of:

| Value | Meaning |
|---|---|
| `plan` | the most recent plan text in the conversation |
| `execution` | git status plus this session's commits |
| `file:<path>` | one file |
| `dir:<path>` | a directory, with standard exclusions |
| `workspace` | the whole workspace; Phase 2 dispatches area specialists |
| `trajectory:<run_id>` | a recorded implementation trajectory |

If omitted, auto-detect per `references/target-detection.md`.

## Flags

| Flag | Effect |
|---|---|
| `--relentless` | Auto-apply and loop with adaptive termination per `references/relentless-adaptive.md`. Pre-approves every proposed fix, applies it, re-runs Phase 1 on the same target, and loops until terminated. Not compatible with `target=plan`. |
| `--no-refute` | Skip Phase 2.5 (refutation and debate). Findings emit straight to the approval block with scorer-emitted confidence only. Recorded in the saved report. |
| `--include-low-confidence` | Show findings below the confidence threshold (default 75) in the approval block. The default hides them and still logs them in the saved report. |
| `--include-ambiguous` | Surface AMBIGUOUS debate verdicts with an `[AMBIGUOUS]` tag for CEO adjudication. The default drops them. |
| `--no-code-review` | Skip the Kimi code-specialist voice on code targets (see Phase 1 and `references/code-review-voice.md`). The default runs it on code. |
