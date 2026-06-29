---
name: align
description: >
  Force exactly N numbered clarifying questions before doing any work on the
  current request. Each question carries lettered options and a "Моя
  рекомендация" line so the user can answer rapidly ("1a, 2c, 3 - custom: ...").
  Manual escalation lever above the default prompt-refinement rule. Single-shot
  per invocation. Default N=5, valid range 1-10.
disable-model-invocation: true
argument-hint: "[N]"
allowed-tools: "Glob"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers: []
x-31c-capability:
  what: >
    Forces exactly N numbered clarifying questions - each with lettered
    options and a recommendation - before any work begins, so scope is
    locked up front instead of corrected after.
  how: >
    Type /align for the default 5 questions, or /align N for N (range 1-10).
    Answer compactly, e.g. "1a, 2c, 3 - custom: ...". Single-shot per call.
  when: >
    Use before a high-stakes or ambiguous task where a wrong assumption is
    expensive. Skip it for simple requests - the always-on prompt-refinement
    rule already handles those.
---
# /align - Force N numbered clarifying questions before doing any work

Manual escalation lever above the default `prompt-refinement.md` rule. When
the user invokes `/align N` they are explicitly summoning a clarification
gate with exactly N numbered + lettered questions, each carrying a single
committed recommendation. Single-shot per invocation - after Phase 3
approval the skill exits and normal posture resumes.

Design spec: `docs/superpowers/specs/2026-05-13-align-skill-design.md` (data overlay: `.heading-os-data/docs/superpowers/specs/2026-05-13-align-skill-design.md`).

## Phase 1 - Pre-flight + target identification

Three sub-steps, all silent on success.

### 1.1 Parse and validate N

Read `$ARGUMENTS`:
- Empty -> `N = 5` (default)
- Integer string `"1"` through `"10"` -> `N = int(arg)`
- Anything else (`"0"`, `"100"`, `"abc"`, `"5.5"`, `"-3"`) -> abort with the
  single-line error and stop. The error is the entire output:

  ```
  /align expects N between 1 and 10. Got: {arg}. Try `/align 5`.
  ```

### 1.2 Detect CEO vs exec context

One Glob call to set the recommendation label:

```
Glob("reference/misha-voice.md")
```

- Match found -> `voice_mode = "ceo"`, label = `Моя рекомендация`. Bilingual
  EN/RU per the user's preceding message language.
- No match -> `voice_mode = "exec"`, label = `My recommendation`. English-only.

The voice_mode flag controls only the label string, not the conversation
language. Conversation language always matches the user's last message.

### 1.3 Identify the substantive target request

Walk back through conversation context (in-LLM, no tool call) to find the
latest substantive user turn preceding the `/align` invocation. Rules:

- Skip the `/align` invocation itself.
- Skip bare command words (`ok`, `yes`, `approved`, `go`, `proceed`, `thanks`).
- Skip prior /align answer lines that look like `1a, 2c, 3 - custom: ...`.
- Accept the first prior user turn containing an actionable request -
  typically the longest recent turn, often with a question, instruction
  verb, or pasted context.

If nothing actionable is found in the last 10 user turns, abort with:

```
/align could not find a substantive request to clarify against. Type the
request and then /align N, or include the request in the same message.
```

## Phase 2 - Compact expansion + N questions

Produce a single assistant response containing four blocks in this order.

### Block A - Compact expansion (2-5 sentences)

Open with:

```
Aligning against your request. Here's what I think you want:

{2-5 sentences restating the request, surfacing the main assumptions
the skill is making and the success criteria as understood. Plain prose,
not bullet list. Specific and committed, not hedged.}
```

This is the Phase 1 of `prompt-refinement.md` collapsed to a short
paragraph. NOT the long "It looks like you want me to do the following:"
expansion - that comes in Phase 3 after answers.

### Block B - N numbered questions with lettered options + per-question recommendation

See [references/question-templates.md](references/question-templates.md) for the canonical lettered + open question forms, the option-count and ordering rules, and the committed-recommendation contract. Consult it before producing the N questions.

### Block C - Summary table

```
| Q | Topic | Pick |
|---|---|---|
| 1 | {topic} | {letter} |
| 2 | {topic} | {letter} |
...
| N | {topic} | {letter} |
```

### Block D - Closer (single line)

```
Your call? Answer "1a, 2c, 3 - custom: ..., 4b, 5a" or "all my picks" to take every recommendation.
```

That is the full Phase 2 output. Nothing else - no preamble, no
explanation of what /align is, no commentary.

## Phase 3 - Parse + finalize + execute

Four sub-steps. Runs after the user replies to the Phase 2 questions.

### 3.1 Parse the user's answer (lenient)

Recognised shapes - try in order:

1. `all my picks` / `take all` / `all recommendations` / `all rec` ->
   adopt the recommendation letter for every question. Skip to 3.3.

2. Pick string with comma-separated entries:
   - `Na` - answer N is letter a/b/c/d
   - `N - custom: text` / `N: custom text` / `N custom text` - free-text
     override for question N
   - `N - skip` / `N skip` / `N pass` - use the recommendation for N
   - `N` alone -> ambiguous, surface in 3.2

3. Prose answer ("I'd say a for the first, c for the second, skip the
   third, custom on four - use Postgres, a for five") - best-effort parse.

For each question 1..N: cleanly parsed -> record the chosen letter or
custom text. Unparseable / missing -> mark as `ambiguous`.

### 3.2 Surface ambiguities (only if any)

If every question parsed cleanly, skip 3.2. Otherwise present once:

```
Parsed your answer. {N} items I need to confirm:

- Q{i}: I see "{i}" with no letter - use my recommendation {letter} ({option text})?
- Q{j}: did not see an answer - use my recommendation {letter} ({option text})?

Reply with the letters or "yes all" to take recommendations.
```

This is a single re-prompt round, not a loop. After the user responds,
parse those replies the same way. If still ambiguous after one round,
abort with:

```
Could not fully parse your answers. Type `/align {N}` again to restart, or
answer the request without /align if you would rather skip clarification.
```

### 3.3 Final expansion + Phase 3 approval gate

Compose the full expanded prompt:

```
It looks like you want me to do the following:

**Objective.** {one or two sentences from Block A, refined by answers}

**Scope.**
- In: {bullets, baked from answers}
- Out: {bullets if any answers excluded scope items}

**Deliverables.** {bullet list of concrete outputs}

**Constraints / assumptions** (from your answers):
- Q1 -> {letter and what it means in concrete terms}
- Q2 -> {letter and what it means}
- ...

**Tone / quality bar.** {one line, if relevant}

Proceed? (approved / revise / cancel)
```

Fulfils the Phase 3 awaiting-approval contract of `prompt-refinement.md`.

### 3.4 Branch on user response

- `approved` / `proceed` / `go` / `yes` / `да` -> exit /align. Execute the
  task using the locked context. Normal posture from this point.
- `revise` / `change` / `edit` -> re-enter Phase 2 with the prior answers
  shown as defaults. Ask which question(s) to revisit.
- `cancel` / `stop` / `no` -> exit /align cleanly. Print one line:
  `/align cancelled. Original request unchanged.` Do nothing else.
- Anything else (e.g., new request) -> treat as cancel and respond to the
  new request normally.

## NEVER

1. Never proceed to Phase 2 with invalid N. N must be an integer in 1-10.
   Anything else aborts in Phase 1.1 with a one-line error.
2. Never ask questions whose answers are already in the request. Each
   question must close real ambiguity, not perform clarification theatre.
3. Never present recommendations as "depends" or "either". Every
   recommendation names exactly one letter.
4. Never skip the summary table. Even when N=1 the table renders with one row.
5. Never persist alignment sessions to disk. Pure conversational. No writes
   to `outputs/operations/align/` or anywhere else.
6. Never enter persistent / always-on mode. Single-shot per invocation.
7. Never auto-route from natural language. Slash-only.
8. Never write to memory, settings, skills, or rules. /align is read-only.
9. Never run silently on unparseable input. Phase 3.2 surfaces parse
   ambiguities once, then aborts with restart instructions if still ambiguous.

## Voice rules

- Single hyphens `-` in prose, never `--`.
- No em-dashes in any prose this skill generates.
- Bilingual: if the user's preceding message is in Russian, respond in
  Russian and use "Моя рекомендация" as the label. If English, respond in
  English and use "My recommendation". The `voice_mode` flag from Phase 1.2
  controls only the label string, not the conversation language.
- Workspace terminology: ODUN.ONE when referencing the 31C platform, DPI+
  for deep packet intelligence, Tribe (never "team" / "family" / "crew").
- Recommendation prose must be committed. No "depends" / "either could
  work" - if the answer genuinely depends on something unsaid, that IS the
  question; ask for it instead.

## Examples

See [references/examples.md](references/examples.md) for two worked examples - Example A (all-lettered, `/align 3` on a TradeExpo launch email) and Example B (mixed open + lettered, `/align 2` on a meeting schedule). Both demonstrate the full Phase 2 four-block output with the canonical closer.
