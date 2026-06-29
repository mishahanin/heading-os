---
name: burst
description: >
  Force N variants of a target content artifact (default 3, range 2-5).
  Each variant is presented as an axis-header line followed by the full
  rewritten artifact. The N variants span deliberately diverse axes
  (length, opener, tone, structure, lens) with one mandatory
  "swing-the-other-way" variant taking the opposite approach. Target is
  either inline (`/burst 3: <seed>`) or walked back to the latest
  assistant-produced content turn. Convergence pattern: pick a variant,
  /burst again from there - works free under the walk-back rules.
  Single-shot per invocation.
disable-model-invocation: true
argument-hint: "[N] [: <inline seed>]"
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
    Produces N variants (default 3, range 2-5) of the latest content artifact - each attacking a distinct axis (opener, tone, structure, lens, length), with one mandatory "swing-the-other-way" variant that inverts a defining property.
  how: >
    Explicit invocation only - type /burst, /burst N, or /burst N: <inline seed>. Walks back to the latest assistant content turn (or uses the inline seed), returns the variants plus a recommended pick. Purely conversational, nothing written to disk.
  when: >
    Use to compare directions, escape a stuck draft, or run the convergence pattern (pick one, /burst again). For N clarifying questions use /align; for contrarian critique use /devil.
---
# /burst - Force N variants of a content artifact

Manual variation lever. When the user invokes `/burst N` they are
explicitly summoning N different versions of the latest content
artifact, each attacking a distinct axis, with one mandatory
"swing-the-other-way" variant inverting a defining property. Supports
the convergence pattern: pick a variant, /burst again, narrow to
target faster than one long prompt. Single-shot - after Phase 2 the
skill exits and normal posture resumes.

Design spec: `docs/superpowers/specs/2026-05-13-burst-skill-design.md` (data overlay: `.heading-os-data/docs/superpowers/specs/2026-05-13-burst-skill-design.md`).

## Phase 1 - Pre-flight + target identification

Three sub-steps, all silent on success.

### 1.1 Parse N + detect inline form

Read `$ARGUMENTS`:
- Empty -> `N = 3`, target = walk-back (1.3)
- Token 1 is `"2"` through `"5"`:
  - Only one token -> `N = int(token1)`, target = walk-back
  - Followed by `:` -> `N = int(token1)`, target = inline (everything
    after the `:`, stripped)
- Token 1 is not an integer in 2-5 (`"0"`, `"1"`, `"6"`, `"100"`,
  `"abc"`, `"3.5"`, `"-2"`) -> abort with the single-line error and stop:

  ```
  /burst expects N between 2 and 5. Got: {token1}. Try `/burst 3` or `/burst 3: <inline seed>`.
  ```

If the inline form is detected but the seed is empty (e.g., `/burst 3:`
with nothing after the colon) -> abort with:

```
/burst 3: requires a seed after the colon, or use bare `/burst 3` to walk back to the latest content in conversation.
```

### 1.2 Detect CEO vs exec context

One Glob call:

```
Glob("reference/misha-voice.md")
```

- Match -> `voice_mode = "ceo"`. Bilingual EN/RU per the user's
  preceding message language.
- No match -> `voice_mode = "exec"`. English-only.

The voice_mode flag affects only the rewritten artifact's language
(matches user's last message). Axis-header lines stay English in both
modes - they are structural labels, not voice text.

### 1.3 Identify the substantive target

If 1.1 captured an inline seed, use it directly. Skip walk-back.

Otherwise walk back through conversation context (in-LLM, no tool
call) to find the latest **assistant-produced content turn** preceding
the `/burst` invocation. Rules:

- Skip the `/burst` invocation itself.
- Skip user turns (user requests are not content artifacts; variations
  need a concrete artifact to vary).
- Skip bare command/status words (`ok`, `yes`, `approved`, `go`,
  `proceed`, `thanks`, `done`, single-letter answers).
- Skip prior /burst outputs (turns with N axis-header variants).
- Skip prior /align outputs (compact-expansion + N questions block).
- Skip prior /devil outputs (1-line framing + N severity-tagged points).
- Skip prior /calibrate outputs (grouped numbered patch list).
- Skip pure status/acknowledgment turns.
- Accept the first prior assistant turn that contains substantive
  content - a draft, an analysis, a recommendation, a rewritten passage,
  a decision summary, a code block, a translated passage. The full
  assistant turn IS the target; the skill varies it as a single artifact.

If nothing actionable in the last 10 assistant turns, abort with:

```
/burst could not find substantive assistant content to vary. Use `/burst N: <inline seed>` to provide content directly, or produce something first and then invoke /burst.
```

### 1.4 Cost + time pre-flight gate

Before generating, classify the target:

**Cost-incurring trigger.** If the target involves a paid-API call per variant
(image generation via /flux-image, video generation, audio generation,
paid-API research calls, Perplexity deep-research), STOP and emit:

```
Heads up: producing N variants of this would run N {model} generations at
~${per_unit} each = ~${total} total. Reply "go" to proceed, or revise the
brief first.
```

Wait for explicit "go" / "proceed" / "yes" before producing variants. Auto
mode does NOT override this. Re-quote per `/burst` invocation - approval
does not persist across calls.

**Long-task trigger.** If the target is multi-step per variant (e.g.,
`/burst 5 storyboards`, `/burst 3 long-form articles`, `/burst 4 detailed
proposal sections`), STOP and emit:

```
Heads up: this is N items x ~M min each = ~T min total. Reply "go" to
proceed.
```

Wait for explicit "go" before producing.

**Skip the gate** for text-only fast variants (LinkedIn posts, hooks,
subject lines, email drafts, taglines, single-paragraph rewrites, image
*prompts* without image generation). These are the default /burst payload
and need no warning.

When in doubt, gate it - 5 seconds of confirmation is cheaper than burning
$5+ on an unwanted image batch.

## Phase 2 - Produce N variants + closer

Single assistant response with three blocks in this order.

### Block A - Brief framing (1 line)

```
Varying: "{1-line summary of the target - what content type it is and the core direction the skill understood, e.g., 'a 150-word LinkedIn post about DPI sovereignty'}"
```

This one-line restate confirms what the skill is varying - prevents
mismatched-target failures.

### Block B - N variants

Generate exactly N variants. N-1 are spread variants attacking distinct
axes; 1 is the mandatory swing variant inverting a defining property.

**Lock the seed; vary the wrapper.** Hold the core message constant across
all variants. Diversify the wrapper (opener, tone, structure, lens, length,
voice, metaphor) - not the meaning. The user wants different ways to say
the same thing, not different things to say. Only diversify the message
itself when the user explicitly asks for "different directions",
"different angles", or "different takes" on the topic.

**Self-check before sending.** Before emitting Block B, scan it:
- Could V1 and V2 swap axis labels with no other change? If yes, the
  variants are too close - regenerate one further apart.
- Do all N variants sound like the same writer with cosmetic differences?
  If yes, the wrapper isn't varying enough - push voice or structure harder.
- Articulate "V1 is X, V2 is Y, V3 is Z" in three distinct words each. If
  you cannot, the variants are not distinct enough.

For each variant:

```
## Variant {i} - {short axis-header, sentence case}

{Full rewritten artifact. Same content type and rough length as the
original unless the axis itself is "length" or "length-reduction". The
rewritten artifact must be drop-in replaceable - the user should be
able to paste it where the original sat.}
```

Axis pool, swing-variant rules, and sort order: `references/variation-axes.md`. Read it before generating Block B. Refactored 2026-05-15 to close P2.2 from the workspace deep audit.

### Block C - My pick + closer

```
My pick: Variant {X} - {one-line reason in 8-15 words, committed prose,
naming the axis or property that makes it the strongest fit for the
target context}.

Reply "use variant N" to converge (next /burst will walk back to your choice), or any other message to continue normally.
```

The recommendation is one specific variant, not a hedge. "Variant 2 - the
operator-voice lens lands the deal pipeline angle harder" beats "Variants
2 or 4 both work." If two variants are genuinely tied, pick the one
closer to the original on the axis that matters most for the target use
case; the swing variant rarely wins this tiebreak.

That is the full Phase 2 output. Nothing else - no preamble, no
explanation of what /burst is, no commentary.

## Iteration patterns to expect

When the user is in a writing-iteration session (drafting a LinkedIn
post, line-rewriting an opener, picking descriptors), expect repeated
`/burst` calls in the same conversation. Common rapid-fire patterns:

- Opener variations -> descriptor swaps inside the opener -> mid-sentence
  word swaps -> closer rewrites
- Hook directions -> benefit phrasings -> CTA wording
- Title options -> subtitle options -> tagline options

Match the rhythm. When the user ran `/burst 5` four times in a row,
they are likely to keep wanting N=5; do not silently drop to the N=3
default. Treat each `/burst` as a fresh invocation - distinct variants,
labelled, with a pick - but read the cadence and pace with it.

## Post-output behaviour (non-Phase, by design)

After Phase 2 the skill EXITS. No Phase 3 gate.

The convergence pattern works free under Q1's walk-back rules:

- User replies `use variant N` (or `take variant N`, `go with N`, `use 2`)
  -> assistant uses variant N as the new draft in a normal turn (e.g.,
  "Using variant 2 as the new draft: <variant 2 text>"). The next
  `/burst` invocation walks back to that turn and varies variant 2.
- Unrelated new request -> respond normally; variants sit in conversation
  history but do not constrain future behaviour.
- Modify-a-specific-variant ("variant 2, but with a shorter opener") ->
  respond normally; produce the requested modification as a regular
  assistant turn. Next /burst walks back to that modified turn.

No special parsing in SKILL.md for any of these - they are all normal
posture.

## NEVER

1. Never proceed to Phase 2 with invalid N. N must be an integer in 2-5.
   Anything else aborts in Phase 1.1.
2. Never produce variants that are near-duplicates of the original.
   Surface variations (one word, one comma) are contract failures.
3. Never skip the swing variant. Even when all N variants could plausibly
   be spread, one must invert the original's defining property. Variant
   N is always the swing.
4. Never produce two variants on the same axis. If only K-1 spread axes
   are defensible, pick the next-best axis rather than repeating an axis
   with different framing.
5. Never describe variants as deltas. Each variant is a full rewritten
   artifact, drop-in replaceable.
6. Never persist variants to disk. Pure conversational.
7. Never enter persistent / always-on mode. Single-shot per invocation.
8. Never auto-route from natural language. Slash-only.
9. Never write to memory, settings, skills, or rules. /burst is read-only.
10. Never block on user response after Phase 2. The closer line is
    informational, not a gate.

## Voice rules

- Single hyphens `-` in prose, never `--`.
- No em-dashes in any prose this skill generates.
- Bilingual: if the user's preceding message is in Russian, respond in
  Russian and produce Russian-language variants. If English, respond
  in English. Axis-header lines stay English in both modes (they are
  structural labels).
- Workspace terminology: ODUN.ONE when referencing the 31C platform,
  DPI+ for deep packet intelligence, Tribe (never "team" / "family" /
  "crew").
- Each variant must be a real variation, not a near-duplicate. Surface
  variations are failures of the contract.
- The swing variant must genuinely invert a defining property of the
  original. A "swing" that lands one step away is not a swing.

## Examples

Worked examples for text-only burst (no gate) and cost-incurring burst (gate fires): `references/examples.md`.
