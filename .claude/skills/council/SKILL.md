---
name: council
description: |
  Second-opinion advisor. Consults Gemini, Grok, Kimi AND GLM in parallel for independent views on hard or high-stakes calls.
  Two modes: independent (all models see the problem only, reason fresh) and critique (all stress-test a draft).
  Distinct from /deep-think (Claude reasoning structured, alone) and /odin (Claude + the curated knowledge brain).
  Trigger when the user says: "council", "/council", "second opinion on", "consult the council",
  "what would Gemini/Grok/Kimi say about", "stress-test this with Gemini/Grok/Kimi", "council vote".
argument-hint: "[question] | --critique [draft]"
allowed-tools: "Read, Bash(python3:*), Write"
context: fork
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.3"
x-heading-orchestration:
  parallel_safe: partial
  shared_state: ["outputs/operations/council/"]
  triggers:
    - council
    - second opinion
    - consult the council
    - what would Gemini say
    - what would Grok say
    - what would Kimi say
    - stress-test with Gemini
    - stress-test with Grok
    - stress-test with Kimi
    - council vote
x-heading-capability:
  what: >
    Independent second opinions from Gemini, Grok, Kimi AND GLM in parallel, presented side-by-side with Claude's own view — no synthesized final answer, the CEO decides.
  how: >
    Run /council <question> for independent mode, or /council --critique <draft> to stress-test a draft. Transcript saved to outputs/operations/council/ unless --no-log. Flags --gemini-only / --grok-only / --kimi-only / --glm-only (run one) or --no-gemini / --no-grok / --no-kimi / --no-glm (skip one). A coding-specialised Kimi-Code voice joins additively on code tasks (auto-detected, or forced with --code / suppressed with --no-kimi-code).
  when: >
    Use for a hard or high-stakes call where cross-model disagreement is itself signal. For Claude reasoning alone use /deep-think; for Claude plus the curated knowledge brain use /odin.
x-heading-routing:
  category: Strategy
  triggers:
    - second opinion
    - consult the council
    - what would Gemini say
    - what would Grok say
    - what would Kimi say
    - stress-test with Gemini
    - stress-test with Grok
    - stress-test with Kimi
    - gemini council
    - kimi council
    - council vote
    - second opinion on
  exclusions:
    - Reasoning alone -> /deep-think
    - Claude + curated knowledge brain -> /odin
  compound: 'No'
  router: auto
---

# Council - Independent Second Opinions (Gemini + Grok + Kimi + GLM)

Independent second opinions from Gemini, Grok, Kimi AND GLM, dispatched in parallel by default. Use when:
- The user wants fresh views on a hard call (independent mode)
- The user wants a draft stress-tested before it ships (critique mode)

This skill is distinct from `/deep-think` (Claude reasoning harder, alone) and `/odin` (Claude + the curated knowledge brain). The unique value here is four models with different training pedigrees, different RLHF, and different failure modes — agreement is stronger evidence; disagreement is itself information.

---

## Phase 0 - Determine mode

Read the user's request.

CRITIQUE mode if any of:
- The user passed `--critique`
- The user said "stress-test this", "critique this draft", "find flaws in", "what's wrong with this", "review this draft"
- The user pasted a draft (proposal, message, claim) and asked for review

INDEPENDENT mode otherwise (default).

---

## Phase 1 - Gather inputs

For INDEPENDENT mode, prepare:
- `question`: the user's question, cleaned and concrete
- `context`: facts the user has shared in this conversation that bear on the question. Important: include the FACTS (numbers, names, dates, constraints), NOT Claude's reasoning or proposed answer. The whole point of independent mode is to give Gemini a clean slate.

For CRITIQUE mode, prepare:
- `draft`: the exact draft text to critique. If the user passed `--critique 'text'` inline, the draft is the quoted text immediately following the flag. If the user pasted a draft in the message body without the flag, use that body verbatim.
- `context`: why the draft was produced (audience, goal, constraints). In critique mode, including Claude's reasoning IS appropriate - Gemini is being asked to stress-test it.

**Optional** — to give Claude's view a distinct lens from the outside models, pull 2-5 methods from `reference/elicitation-methods.md` (`python scripts/elicit.py list --category collaboration`; categories `collaboration`/`research`/`framing`, e.g. Steelmanning, Reframe the Question). Skip when the question is already well-framed.

---

## Phase 2 - Call the model scripts

### Determine which models to call

Scan the user's invocation text for model-selection flags. Default = run Gemini + Grok + Kimi + GLM.

**Exclusive flags** (run exactly one):
- `--gemini-only` — call only Gemini.
- `--grok-only` — call only Grok.
- `--kimi-only` — call only Kimi.
- `--glm-only` — call only GLM.

At most one `--*-only` flag is allowed.

**Skip flags** (combinable):
- `--no-gemini` — skip Gemini.
- `--no-grok` — skip Grok.
- `--no-kimi` — skip Kimi.
- `--no-glm` — skip GLM.

**Reject immediately** (one-line error, then stop — do not proceed to Phase 3) if:
- More than one `--*-only` flag is set.
- Any `--*-only` is combined with any `--no-*`.
- All base voices end up skipped (e.g. `--no-gemini --no-grok --no-kimi --no-glm`).

### The Kimi-Code voice (optional 4th, code tasks)

Kimi-Code (`kimi-k2.7-code:cloud`, resolved via `python scripts/council-models.py --get kimi-code`) is a coding-specialised voice that joins the panel **additively** — Gemini + Grok + Kimi + GLM + Kimi-Code — on code-related consultations. It never replaces the general Kimi voice; the two are distinct players.

Include Kimi-Code when EITHER:
- The user passed `--code`, OR
- **Auto-detect:** the question or draft is clearly about code — it contains a code block, a diff/patch, a stack trace or error output, file paths with code extensions, or its substance is implementation / architecture / a bug / an algorithm / an API / a data structure. When genuinely unsure, do NOT auto-include (a false include on a strategy question just adds noise).

Exclude it when `--no-kimi-code` is set (forces exclusion even under `--code` or a code question), or when there is no code signal and no `--code` flag (the default for strategy/business questions).

`--code` / `--no-kimi-code` are **orthogonal** to the `--*-only` exclusivity: those flags select among the four base voices; Kimi-Code adds on top when activated. So `--grok-only --code` runs Grok + Kimi-Code; a plain code question runs all five.

### Build the commands

Use `Bash` with single-quoted args (escape any single quotes in the inputs as `'\''`). Build a command for each SELECTED model:

For independent mode:
```bash
python scripts/gemini-consult.py --mode independent --question '...' --context '...'
python scripts/grok-consult.py   --mode independent --question '...' --context '...'
python scripts/kimi-consult.py   --mode independent --question '...' --context '...'
# GLM base voice (ollama wrapper):
python scripts/kimi-consult.py   --mode independent --question '...' --context '...' --model "$(python scripts/council-models.py --get glm)"
# plus, ONLY when the Kimi-Code voice is active (see above) — same wrapper, code model:
python scripts/kimi-consult.py   --mode independent --question '...' --context '...' --model "$(python scripts/council-models.py --get kimi-code)"
```

For critique mode:
```bash
python scripts/gemini-consult.py --mode critique --draft '...' --context '...'
python scripts/grok-consult.py   --mode critique --draft '...' --context '...'
python scripts/kimi-consult.py   --mode critique --draft '...' --context '...'
# GLM base voice (ollama wrapper):
python scripts/kimi-consult.py   --mode critique --draft '...' --context '...' --model "$(python scripts/council-models.py --get glm)"
# plus, ONLY when the Kimi-Code voice is active:
python scripts/kimi-consult.py   --mode critique --draft '...' --context '...' --model "$(python scripts/council-models.py --get kimi-code)"
```

The Kimi-Code and GLM calls are normal `kimi-consult.py` invocations pointed at their models (both ollama-served) — do NOT add a new script. Label their outputs **Kimi-Code** and **GLM** (distinct from the general **Kimi** call) so the three never merge.

Optional model overrides:
- `--gemini-model gemini-2.5-flash` — passed to the Gemini call as `--model gemini-2.5-flash`
- `--grok-model grok-3-mini` — passed to the Grok call as `--model grok-3-mini`
- `--kimi-model kimi-k2.6:cloud` — passed to the general Kimi call as `--model kimi-k2.6:cloud`
- `--glm-model <id>` — override the GLM voice's model (default: the `glm` pin)
- `--kimi-code-model <id>` — override the Kimi-Code voice's model (default: the `kimi-code` pin)

Other passthrough flags (apply to all calls): `--temperature`, `--max-tokens`.

### Dispatch IN PARALLEL

Fire all SELECTED model Bash calls in a SINGLE assistant message (parallel dispatch). Do NOT call them sequentially — that multiplies latency.

When only one model is being called (`--*-only`), fire just that one call.

### Capture results

For each script call:
- If exit code 0: capture stdout as that model's verbatim response. Mark model as SUCCEEDED.
- If exit code non-zero: capture stderr as the error message. Mark model as FAILED.

If ALL called models FAILED: print `Error: all council models failed.` followed by each model's error, then stop. Do NOT write a transcript.

If at least one model SUCCEEDED, proceed to Phase 3.

---

## Phase 3 - Formulate Claude's view

After reading the verbatim responses captured in Phase 2 (whichever models succeeded — could be Gemini, Grok, Kimi, GLM, or any subset of them), write Claude's own view on the question or draft. Reach a real position independently of what any outside model said — don't just react to them.

Claude's view should be 3-5 bullets covering: position, key reasons, main risk Claude sees. (Tightened from 3-7 in Phase 1 — three views in one output need shorter bullets to stay readable.)

---

## Phase 4 - Present the side-by-side

Render exactly the sections below to the user. No more, no less. No synthesised final answer.

```
## Gemini's view
[3-5 bullets distilling Gemini's response. Preserve Gemini's actual conclusions and arguments — don't soften or rewrite them. If Gemini hedged, say so.]

## Grok's view
[3-5 bullets distilling Grok's response. Same rule — preserve Grok's actual conclusions and arguments.]

## Kimi's view
[3-5 bullets distilling Kimi's response. Same rule — preserve Kimi's actual conclusions and arguments.]

## GLM's view
[3-5 bullets distilling GLM's response. Same rule — preserve GLM's actual conclusions and arguments.]

## Kimi-Code's view
[ONLY when the Kimi-Code voice ran. 3-5 bullets distilling the code voice's response, framed as the code-specialist read (correctness, edge cases, implementation risk). Same rule — preserve its actual conclusions. Omit this whole section when the code voice was not active.]

## Claude's view
[3-5 bullets — Claude's own position, reached independently of any outside model.]

## Where we agree / disagree
[1 paragraph — make convergence and divergence explicit across all views present. If they agree, say that. If they disagree, name where. Don't fabricate disagreement.]

## Open questions for you
[1-3 things the user actually needs to weigh — not generic platitudes.]
```

### Conditional sections

Omit any model's section if that model was not called (`--*-only` / `--no-*`, or the Kimi-Code voice was not activated) or failed. When only one outside model ran, the output has four sections; when the Kimi-Code voice also ran, add its section. Replace a failed model's section with `## Failed: {Model}` and put the error message inside (one paragraph, plain text, no bullets). All-failed is caught in Phase 2 and never reaches Phase 4.

### Alignment check (mandatory, applies to whichever verbatims were captured)

Before writing the side-by-side:

1. If Gemini was requested AND succeeded: re-read Gemini's verbatim response from Phase 2 stdout. Verify every bullet under `## Gemini's view` is traceable to a specific sentence in that verbatim text. If the verbatim is ambiguous, truncated, or hedged, say so explicitly in the bullets rather than inferring a position.
2. If Grok was requested AND succeeded: repeat for Grok's verbatim and `## Grok's view`.
3. If Kimi was requested AND succeeded: repeat for Kimi's verbatim and `## Kimi's view`.
4. If GLM was requested AND succeeded: repeat for GLM's verbatim and `## GLM's view`.
5. If the Kimi-Code voice ran AND succeeded: repeat for its verbatim and `## Kimi-Code's view`.
6. Do NOT cross-feed: never use one model's verbatim to interpret another model's bullets. The general Kimi, GLM, and Kimi-Code verbatims are separate — never merge them.

---

## Phase 5 - Persist the transcript (default)

### Detect `--no-log`

Before writing anything, scan the user's original invocation text for any of:

- The literal flag `--no-log`
- Natural-language equivalents: `no transcript`, `don't log`, `do not save`, `skip the log`, `skip transcript`, `без записи` (Russian)

If any match, skip the entire Write step below and announce in the chat output: `Transcript skipped.` Then end normally.

### Write the transcript

Otherwise, write to:

```text
outputs/operations/council/{YYYY-MM-DD}_council_{HHMMSS}_{slug}.md
```

The `{HHMMSS}` segment (current time, 24-hour, no separators) prevents collisions when two consultations on the same date produce the same slug. Use the local clock at the moment of writing.

Slug rules (per `.claude/rules/output-naming.md`):

- Lowercase, kebab-case
- First 5 meaningful words of the question (or draft summary)
- Strip articles (a, an, the) and common stop words (is, are, was, what, how, why, do, does, should, would, could, will)
- Max 40 characters total
- Fallback to `untitled` if no meaningful words remain

Use the `Write` tool. The exact transcript content (YAML frontmatter + body sections) and the post-write absolute-path announcement are specified in `references/transcript-format.md`.

---

## Phase 6 - Capture CEO verdict (Track C of LLM-fit logging)

Skip this phase entirely if `--no-log` was set (no transcript = nothing to record against).

After the transcript path is reported, ask the CEO **one short question** as the final line of the chat output:

> Which answer landed best - `claude`, `gemini`, `grok`, `kimi`, `glm`, `mix`, `reject`, or `skip`? (one word + optional sentence on why)

(When the Kimi-Code voice ran, it folds under `kimi` for this verdict — a "Kimi-Code was best" reply is recorded as `kimi`. GLM is a base voice with its own `glm` verdict choice.)

That is all. Do NOT re-summarise, push for a decision, or explain the choice values — the CEO knows them. Keep the question to one line so the CEO can reply in 5 seconds.

When the CEO replies, parse their next message:

- First token (case-insensitive) is the choice. Accept `claude` / `gemini` / `grok` / `kimi` / `glm` / `mix` / `reject` / `skip` / Russian equivalents (`пропустить`, `мix`, etc — normalise to the English choice).
- Everything after the first token is the optional `notes` string. Trim whitespace.

If choice is **`skip`** (or any non-recognised first token without an explicit `claude/gemini/grok/kimi/glm/mix/reject` keyword anywhere in the reply): do NOT record. Print one line: `Verdict skipped (left pending).` Do not nag.

Otherwise, run:

```bash
python scripts/council-record-verdict.py \
  --id {transcript filename stem, NO .md extension} \
  --choice {claude|gemini|grok|kimi|glm|mix|reject} \
  --notes "{notes string, or omit the flag if empty}"
```

The script prints `recorded: ... tally: N recorded - claude=X, gemini=Y, grok=Z, kimi=K, glm=G, mix=A, reject=B`. Echo only the tally line back to the CEO so they see the running count, plus one final line:

`Recorded. Aggregate refreshed.`

Then run `python scripts/council-aggregate.py` (no flags) to rebuild `outputs/operations/council/_aggregate.md` from the updated JSONL. This is the ONLY supported way verdicts enter the system - the CEO never opens the aggregate or the JSONL.

If the CEO has not yet replied when you would otherwise close out (e.g., they went silent or moved to another task), do not record anything. The verdict stays pending until they answer or another /council run prompts them again. Pending verdicts are reflected in the aggregate as `_(pending CEO verdict)_`.

---

## NEVER

- Synthesise a single "final answer" combining Gemini, Grok, Kimi, and Claude. The user decides.
- Show any outside model Claude's reasoning in INDEPENDENT mode. (In critique mode it's fine.)
- Cross-feed one model's response to another. Each model reasons independently.
- Re-run a failed model silently. If a model fails, render `## Failed: {Model}` with the error and continue.
- Forget the 31C system block — `gemini-consult.py`, `grok-consult.py`, and `kimi-consult.py` inject it automatically; if you ever bypass any script, inject it yourself.
- Run without the relevant API key set in `.env` (`GEMINI_API_KEY` for Gemini, `XAI_API_KEY` for Grok, `OLLAMA_API_KEY` for Kimi and GLM).
- Modify `scripts/gemini-consult.py`, `scripts/grok-consult.py`, or `scripts/kimi-consult.py` from inside this skill — those are code changes, not skill behaviour.
- Dispatch the model scripts sequentially when multiple are requested. Always parallel (single assistant message, multiple Bash tool calls).
- Ask the CEO to open `_aggregate.md` or `_verdicts.jsonl` directly. The CEO never edits those files; Phase 6 + `scripts/council-record-verdict.py` are the only writing path.

---

## Voice rules (apply to Claude's view and to the side-by-side prose)

- `.claude/rules/voice.md` - workspace voice
- `.claude/rules/humanization.md` - five fundamentals on Claude's own prose
- `.claude/rules/terminology.md` - Tribe, ODUN.ONE, DPI+, Five Principles
- `.claude/rules/hidden-chars.md` - zero invisible Unicode in the transcript

Validation before declaring done: run `python scripts/sanitize-text.py {transcript-path} --scan`. Confirmation line in chat: `Word count: X. Hidden characters: clean.`
