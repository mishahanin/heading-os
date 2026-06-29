---
name: devil
description: >
  Force N contrarian critique points against a target position. Each point
  carries a severity tag (BLOCKER / HIGH / MEDIUM / LOW) and a committed
  paragraph attacking from a distinct angle. Target is either inline
  (`/devil 5: <claim>`) or walked back to the latest substantive
  decision/claim in conversation. Manual sycophancy-breaker. Cheap
  alternative to /council (external) and /scrutinize (multi-phase).
  Single-shot per invocation. Default N=5, valid range 1-10. Honesty
  floor: stops early rather than fabricate weak points to hit N.
disable-model-invocation: true
argument-hint: "[N] [: <inline claim>]"
allowed-tools: "Glob"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers: []
x-31c-capability:
  what: >
    Forces exactly N severity-tagged (BLOCKER/HIGH/MEDIUM/LOW) contrarian critique points against a target claim or decision, each from a distinct angle - a manual sycophancy-breaker, no fixes, no hedging.
  how: >
    Explicit-invocation only (disable-model-invocation). Type /devil for 5 points, /devil N for N (range 1-10), or /devil N: <inline claim>. Single-shot, conversational, nothing persisted.
  when: >
    Use to pressure-test a recent decision cheaply. For external Gemini+Grok views use /council; for a multi-phase principal-engineer review use /scrutinize.
---
# /devil - Force N contrarian critique points

Manual sycophancy-breaker. When the user invokes `/devil N` they are
explicitly summoning a contrarian critic to produce exactly N
severity-tagged adversarial points against a target position, from
distinct angles, in committed prose. Cheap alternative to /council
(external Gemini+Grok) and /scrutinize (multi-phase VIIA). Single-shot -
after Phase 2 the skill exits and normal posture resumes.

Design spec: `docs/superpowers/specs/2026-05-13-devil-skill-design.md` (data overlay: `.heading-os-data/docs/superpowers/specs/2026-05-13-devil-skill-design.md`).

## Phase 1 - Pre-flight + target identification

Three sub-steps, all silent on success.

**Optional angle priming.** Before identifying the target, you may consult the shared method catalog to widen the attack surface beyond the default angle pool - draw from the `risk`, `competitive`, and `framing` families: `python scripts/elicit.py list --category risk` (e.g. Pre-mortem Analysis, Failure Mode Analysis, Assumption Audit). Use a method only to sharpen a real cut; never let it manufacture a point that violates the honesty floor. Skip this entirely when the angles are already obvious. Catalog: `reference/elicitation-methods.md`.

### 1.1 Parse N + detect inline form

Read `$ARGUMENTS`:
- Empty -> `N = 5`, target = walk-back (1.3)
- Token 1 is `"1"` through `"10"`:
  - Only one token -> `N = int(token1)`, target = walk-back
  - Followed by `:` -> `N = int(token1)`, target = inline (everything
    after the `:`, stripped)
- Token 1 is not an integer in 1-10 (`"0"`, `"100"`, `"abc"`, `"5.5"`,
  `"-3"`) -> abort with the single-line error and stop:

  ```
  /devil expects N between 1 and 10. Got: {token1}. Try `/devil 5` or `/devil 5: <inline claim>`.
  ```

If the inline form is detected but the inline claim is empty (e.g.,
`/devil 5:` with nothing after the colon) -> abort with:

```
/devil 5: requires an inline claim after the colon, or use bare `/devil 5` to walk back to the latest claim in conversation.
```

### 1.2 Detect CEO vs exec context

One Glob call:

```
Glob("reference/misha-voice.md")
```

- Match -> `voice_mode = "ceo"`. Honesty-floor label = `Моя честная
  оценка`. Bilingual EN/RU per the user's preceding message language.
- No match -> `voice_mode = "exec"`. Honesty-floor label = `My honest
  assessment`. English-only.

Severity tags (BLOCKER / HIGH / MEDIUM / LOW) stay English in both modes -
they are technical labels, not voice text.

### 1.3 Identify the substantive target

If 1.1 captured an inline target, use it directly. Skip walk-back.

Otherwise walk back through conversation context (in-LLM, no tool call)
to find the latest substantive decision/claim preceding the `/devil`
invocation. Rules:

- Skip the `/devil` invocation itself.
- Skip bare command words (`ok`, `yes`, `approved`, `go`, `proceed`,
  `thanks`).
- Skip prior /devil outputs (turns with N severity-tagged points).
- Skip prior /align outputs (compact-expansion + N questions block).
- Accept the first prior turn (assistant OR user) containing an
  actionable decision, recommendation, claim, or position.

If nothing actionable in the last 10 turns, abort with:

```
/devil could not find a substantive decision or claim to critique. Type the claim and then /devil N, use `/devil N: <inline claim>`, or include the claim in the same message.
```

## Phase 2 - Produce N adversarial points + closer

Single assistant response with three blocks in this order.

### Block A - Brief framing (1 line)

```
Adversarial pass on: "{1-line summary of the target - claim, decision, or recommendation as the skill understood it}"
```

This one-line restate confirms what the skill is critiquing - prevents
mismatched-target failures.

### Block B - N numbered adversarial points

For each point 1..N:

```
## Point {i}: [SEVERITY] {short topic line, sentence case}

**{2-5 word bolded title phrase}.** {One or two sentences attacking the
target from a distinct angle. The angle is named implicitly by the topic
line. No proposed alternative. No fixes. No "you could solve this by". No
counter-offers. No silver lining. No hedging ("on the other hand", "some
would argue", "this might be a concern in certain cases"). Land the cut
and stop.}
```

`SEVERITY` is one of: `BLOCKER`, `HIGH`, `MEDIUM`, `LOW`.

**Length contract.** Default is one sentence to name the weakness, plus
optionally one more sentence to make the case. Two sentences is the
ceiling, not the floor. Drifting into 3+ sentence paragraphs is critique
theatre; if a single point needs a paragraph, it is probably two points
fused together - split them.

**Bolded title phrase.** The 2-5 word phrase opening the paragraph is the
scannable headline. Examples: **Buried verb.**, **Wrong tool.**, **One-note
feed.**, **Untested repo.**, **Sanctions exposure.**, **Wrong stakeholder.**.
Sentence case. Period at the end. The phrase names the weakness; the rest
of the sentence makes the case.

**Plain English by default.** Write at a register a smart non-expert
reader can scan in one pass. Short words, short sentences, direct verbs.
Technical vocabulary is allowed only when the target itself is technical
and the term is the right one. "This is the weakest line" beats "this
represents a suboptimal articulation."

Severity assignment:
- `BLOCKER` - if true, makes the target unworkable (hard constraint
  violation, regulatory failure, contradicts locked decision).
- `HIGH` - serious failure mode that should change target's shape
  (scales poorly, hits a known anti-pattern, misses a major stakeholder).
- `MEDIUM` - real concern the owner should weigh (cost/timing tradeoff,
  partial coverage, second-order side effect).
- `LOW` - defensible-but-minor objection (visible but probably will not
  change the decision).

Angle diversity:
- Each point attacks from a distinct angle. Angle pool: correctness,
  scope/coverage, cost/effort, timing/sequencing, alternatives/
  opportunity-cost, second-order effects, stakeholder/political risk,
  technical-debt accumulation, security/compliance, observability/
  maintenance.
- No two points should attack the same angle with different framing.
- If fewer than N distinct defensible angles exist, invoke the honesty
  floor (Block C).
- Sort by severity descending. Within same severity, sort by angle
  priority (correctness > scope > cost > timing > others).

### Block C (conditional) - Honesty floor message

Only if M < N (fewer points produced than requested):

```
/devil honesty floor: I could only produce {M} substantive points from
distinct angles. The remaining {N-M} would be strawmen or repetition.
Padding to N would defeat the purpose. If you want me to stretch
anyway, reply "force N" and I'll surface the weakest cuts with explicit
"weak: " prefixes.
```

If M == N, this block is absent. Block D follows Block B directly.

### Block D - Closer (single line)

```
Reply with point numbers to address, or any other message to continue normally.
```

That is the full Phase 2 output. Nothing else - no preamble, no
explanation of what /devil is, no commentary.

## Post-output behaviour (non-Phase, by design)

After Phase 2 the skill EXITS. No Phase 3 gate.

Whatever the user replies next is normal-posture conversation:

- Point numbers (e.g., `address 1, 3`, `fix point 2`, `fold the BLOCKERs
  into the current draft`) -> handle as a normal request using the
  critique context still in conversation history.
- Unrelated new request -> respond to the new request normally.
- `force N` (the honesty-floor escape hatch) -> produce the additional
  N-M weak points with explicit `weak: ` prefixes in the topic line and
  `LOW` severity tags. This is the only post-output reply that maps to a
  specific re-engagement contract.

## NEVER

1. Never proceed to Phase 2 with invalid N. N must be an integer in 1-10.
   Anything else aborts in Phase 1.1.
2. Never fabricate weak points to hit N. The honesty floor exists
   precisely to prevent this - padding-for-severity-theatre is the
   reverse failure mode of sycophancy.
3. Never invent constraints, stakeholders, or technical limitations that
   are not in the target's actual context. Adversarial honesty over
   adversarial creativity.
4. Never produce two points attacking the same angle with different
   framing. That is repetition, not breadth.
5. Never propose an alternative as part of the critique paragraph. The
   user's job is synthesis; /devil's job is the cut.
6. Never persist the adversarial pass to disk. Pure conversational.
7. Never enter persistent / always-on mode. Single-shot per invocation.
8. Never auto-route from natural language. Slash-only.
9. Never write to memory, settings, skills, or rules. /devil is read-only.
10. Never block on user response after Phase 2. The closer line is
    informational, not a gate.

## Voice rules

- Single hyphens `-` in prose, never `--`.
- No em-dashes in any prose this skill generates.
- Bilingual: if the user's preceding message is in Russian, respond in
  Russian. If English, respond in English. The `voice_mode` flag from
  Phase 1.2 controls only the honesty-floor label (`Моя честная оценка`
  vs `My honest assessment`); severity tags stay English in both modes.
- Workspace terminology: ODUN.ONE when referencing the 31C platform,
  DPI+ for deep packet intelligence, Tribe (never "team" / "family" /
  "crew").
- Adversarial prose must be committed and concrete. No "on the other
  hand", no "some would argue", no "this might be a concern in certain
  cases". Name the failure mode, name the impact, land the cut.
- Adversarial prose must be honest. If a critique requires inventing a
  constraint that is not true, drop the point and let M < N.

## Examples

Two worked examples (clean N=M pass + honesty-floor M<N pass): `.claude/skills/devil/references/examples.md`. Read for output-shape and honesty-floor wording templates.
