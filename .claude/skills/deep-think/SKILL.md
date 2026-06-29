---
name: deep-think
description: >
  Structured sequential thinking engine for complex reasoning, multi-variable decisions,
  and strategy under uncertainty. Breaks problems into numbered thought steps with revision
  and branching support. Replaces the Sequential Thinking MCP server with visible,
  challengeable reasoning tailored to CEO decision-making.

  Engage this skill PROACTIVELY -- without waiting for the user to ask -- whenever a problem
  involves multi-variable decisions, contradictory signals, high-stakes reasoning, strategy
  under uncertainty, or when you catch yourself making unexamined assumptions.

  Use when the user says "think through this", "break this down", "reason through",
  "what are we missing", "think step by step", "structured thinking", "analyze this
  carefully", "help me think about", "deep think", or any request requiring deliberate
  multi-step reasoning before action. This is the thinking engine that precedes and
  sharpens every other skill.
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.3"
argument-hint: "[problem]"
allowed-tools: "Read"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - think through this
    - break this down
    - reason through
    - what are we missing
    - analyze carefully
x-31c-capability:
  what: >
    Breaks a complex decision into visible, numbered thought steps - surfacing assumptions, exploring paths, and ending with a maritime-framed Course recommendation with confidence, key risk, and course correction.
  how: >
    Run /deep-think <problem>, or it engages proactively on multi-variable or high-stakes reasoning. Depth is quick/standard/deep. Strategic sessions save to outputs/thinking/.
  when: >
    Use before generating a deliverable when reasoning quality matters (feeds /deal-strategy, /create-plan, /meeting-prep). For a simple question just answer it; for a second opinion from other models use /council.
---
# Deep Think

Structured sequential reasoning engine. Breaks complex problems into visible, numbered thought steps -- with revision, branching, and maritime-framed recommendations. Every assumption surfaced. Every path explored. Every recommendation backed by the chain of reasoning that produced it.

This skill replaces the Sequential Thinking MCP server. The key improvement: reasoning is visible and challengeable, not hidden inside tool calls.

---

## Variables

- `problem` (required) -- The question, decision, or problem to think through
- `depth` (optional) -- `quick` (3-5 steps), `standard` (6-10 steps, default), `deep` (10-15+ steps)
- `context` (optional) -- Additional context, constraints, or files to reference

---

## Customization (optional, Phase 0)

This skill is customization-aware (pilot). Before reasoning, resolve any per-exec overrides: `python scripts/resolve_customization.py --skill .claude/skills/deep-think`. Apply any `activation_steps_prepend`, `persistent_facts` (facts to always honour, e.g. a preferred default depth or currency), and output-path overrides from the merged result. On any failure, proceed with the defaults below -- never block. Layout + authoring guide: `config/skill-custom/README.md`.

---

## When to Engage Proactively

Activate this skill WITHOUT being asked when you detect any of these conditions:

1. **Multi-variable decisions** -- 3+ competing factors with no obvious weighting. Example: "Should we enter [a new market] through PartnerCo or direct? There's a tender deadline, partner margin question, and a competitive threat."

2. **Strategy under uncertainty** -- The answer depends on unknowns or contested assumptions. Example: "How should we price the [target-market] deal given competitive pressure from a state-aligned vendor?"

3. **Contradictory signals** -- Information contains tension or paradox. Example: Pipeline shows strong momentum but CRM health shows RED contacts on key relationships.

4. **High-stakes reasoning** -- Being wrong is costly: investor positioning, partnership terms, competitive response, market entry timing. Example: "How much technical detail should we share with a potential investor who sits on a competitor's board?"

5. **Complex skill chaining** -- Determining which skills to invoke and in what order for a novel, ambiguous request that could go multiple directions.

6. **Assumption surfacing** -- You catch yourself making an assumption that could meaningfully change the output. Stop. Engage deep-think. Surface it.

**Do NOT engage for:** Simple follow-up emails, translations, straightforward CRM updates, content that doesn't involve strategic decisions.

---

## Depth Levels

| Level | Steps | When to Use |
|-------|-------|-------------|
| **Quick** | 3-5 | Time-sensitive decisions. Problem is clear, options are few, stakes are moderate. Skip Research stage. |
| **Standard** | 6-10 | Default. Most strategic decisions, deal positioning, partnership structuring. Full stage set. |
| **Deep** | 10-15+ | Investor positioning, market entry strategy, competitive response, anything where being wrong costs more than thinking costs. Multiple analysis paths, extensive research, revisions expected. |

---

## Thinking Stages

Use these stages in order. Not every stage is required for every depth level. Multiple Analysis steps are expected for Standard and Deep.

| Stage | Purpose | Required? |
|-------|---------|-----------|
| **Problem Definition** | Restate what we're actually deciding. Separate the real question from the presented question. | Always |
| **Assumptions Surfaced** | List explicit and implicit assumptions. Mark each as TESTED or UNTESTED. | Always |
| **Research / Evidence** | What do workspace context files, DataStore, or known facts say? Cite sources. | Standard, Deep |
| **Analysis** | Reason through an option, scenario, or hypothesis. Use Path A/B/C labels for alternatives. | Always (multiple allowed) |
| **Revision** | Revisit and update an earlier step. Must reference the step number and explain why. | When reasoning changes a prior conclusion |
| **Synthesis** | Combine threads into a coherent picture. Resolve tensions between paths. | Standard, Deep |
| **Heading Recommendation** | The recommended course with confidence, risk, and course correction. | Always last |

---

## Elicitation Methods (optional)

After Problem Definition, you may pull 2-5 named reasoning/critique methods from the shared catalog and run them as labelled Analysis paths -- useful when the default stages are not biting on a hard, multi-angle problem. This is optional; skip it when the standard flow already does the work.

- `python scripts/elicit.py categories` -- the cheap map of method families
- `python scripts/elicit.py list --category <c>` -- methods in a family (e.g. risk, framing, core)
- `python scripts/elicit.py show "<Method Name>"` -- full gist + output pattern

Pick methods matched to the problem (Pre-mortem Analysis and Inversion Analysis for a risky launch; First Principles Analysis and Reframe the Question when the framing feels wrong; Second-Order Thinking for cascading effects), name the ones you chose, then apply each as its own Analysis step. Catalog reference: `reference/elicitation-methods.md`.

---

## Step Format

Each step is a numbered heading with its stage label:

```
### [N] Stage Label
[Content of this thinking step]
```

**Revision notation:** When revising an earlier step, use:
```
### [N] << Revision of [earlier step number]: [reason for revision]
[Updated reasoning]
```
The original step is NOT deleted or modified. The revision chain stays visible.

**Branching notation:** When exploring alternative paths, use:
```
### [N] Analysis -- Path A: [Label]
[Reasoning for this option]

### [N+1] Analysis -- Path B: [Label]
[Reasoning for alternative]
```
A subsequent Synthesis step compares and resolves the paths.

---

## Output Format

```markdown
## Deep Think: [Problem Statement]

**Heading:** [1-sentence framing of what we're navigating]
**Sea State:** [External conditions affecting this decision -- market, timing, competitive, relational]
**Depth:** Quick / Standard / Deep

---

### [1] Problem Definition
[The problem restated precisely. What are we actually deciding? What is NOT the question?]

### [2] Assumptions Surfaced
- [Assumption 1] -- TESTED / UNTESTED
- [Assumption 2] -- TESTED / UNTESTED
- [Assumption N] -- TESTED / UNTESTED

### [3] Research / Evidence
[What do we know from context files, DataStore, prior interactions? Cite file paths.]

### [4] Analysis -- Path A: [Label]
[Reasoning through one option]

### [5] Analysis -- Path B: [Label]
[Reasoning through alternative]

### [6] << Revision of [2]: [new information changed an assumption]
[Updated assumption list]

### [7] Synthesis
[Bringing the threads together. Resolving tension between paths.]

### [8] Heading Recommendation
[The recommended course of action and the reasoning that supports it.]

---

**Course:** [One-sentence recommendation]
**Confidence:** HIGH / MEDIUM / LOW
**Key risk:** [The single thing most likely to make this wrong]
**If wrong, adjust by:** [Specific course correction -- what to do, not "reassess"]
```

Step count is dynamic. Continue until you reach Synthesis and Heading Recommendation. Do not predict total steps upfront.

---

## Integration with Other Skills

Deep-think is a **thinking layer** that feeds downstream skills. Use it BEFORE generating deliverables when reasoning quality matters.

**Deep-think feeds these skills:**
- `/deal-strategy` -- Think through positioning logic before generating the strategy
- `/create-plan` -- Think through architectural decisions before writing the plan
- `/meeting-prep` -- Think through counterpart motivations and information asymmetry before talking points
- `/proposal` -- Think through why THIS buyer cares before filling the template
- `/investor-pitch` -- Think through which assumptions investors will attack before building the narrative

**Chaining pattern:**
1. Run deep-think to establish reasoning
2. Reference the Course recommendation when executing the downstream skill
3. If the downstream skill surfaces new information that contradicts the thinking, add Revision steps rather than starting over

---

## Session Persistence

**When to save:** Standard and Deep thinks that produce strategic decisions -- deal positioning, market entry, investor structuring, partnership terms.

**Where to save:** `outputs/thinking/YYYY-MM-DD-[topic-slug].md`

**Format:** Include the full thinking session with a header:
```markdown
# Deep Think: [Problem Statement]
> Date: YYYY-MM-DD
> Depth: Standard / Deep
> Course: [recommendation summary]
> Confidence: HIGH / MEDIUM / LOW
> Outcome: [to be updated when the decision plays out]

[Full thinking session below]
```

**When to skip persistence:** Quick thinks consumed immediately by another skill. Reasoning that is tactical, not strategic.

---

## Rules

1. **Never skip to conclusions.** The value is in the visible chain of reasoning, not the recommendation alone.
2. **Never fabricate certainty.** If confidence is LOW, say so. If an assumption is UNTESTED, mark it.
3. **Always surface assumptions.** This is the single most important stage. Assumptions that stay hidden shape everything downstream.
4. **Maritime vocabulary for framing, not for every step.** Heading, Sea State, Course, Course Correction in the bookends. Plain analytical language in the thinking steps. Natural, not forced.
5. **Cite workspace sources.** When referencing facts from context files, DataStore, or CRM, include the file path.
6. **Revision is strength, not weakness.** Revising an earlier step mid-think shows the reasoning is working. Never hide it.
7. **The Course must be actionable.** Not "consider options" or "evaluate further." A specific recommended action with a specific next step.

## Knowledge Base

After a thinking session with persistence, offer: "Want me to capture the conclusions? `/odin log` records them as an episode in Odin's brain (CEO-only); `/zk distill` extracts the strategic insights into the knowledge base."
