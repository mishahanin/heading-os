# Editorial Review — the structural pass

Document-level structural editing for long deliverables: the **argument architecture** — section ordering, claim-to-evidence linkage, hierarchy, redundancy, buried lede — judged *before* any sentence is touched. Adapted from and extends BMAD-Method `bmad-editorial-review-structure` (v6.8.0) for a CEO operations workspace. The source taxonomy is six operations (CUT / MERGE / MOVE / CONDENSE / QUESTION / PRESERVE); SPLIT, ADD, and PROMOTE/DEMOTE below are workspace additions.

Last Updated: 2026-06-04
Consumed by: `/editorial-review` (the standalone skill) and the long-doc skills that run it as an optional sub-phase (`/proposal`, `/intel-briefing-newsletter`, `/rfp-response`, `/data-room`, `/investor-pitch`).
Classification: corporate.

## The one inviolable rule

**CONTENT IS SACROSANCT.** The structural pass never challenges *what* the document says — only how it is organized. It moves, cuts, merges, and condenses; it does not argue with the ideas or rewrite the prose.

## The boundary with humanization.md (do not cross it)

This pass stops at the paragraph. Everything below the paragraph belongs to `.claude/rules/humanization.md` and must NOT be duplicated or contradicted here:

| This file (structural pass) owns | `humanization.md` owns |
|---|---|
| Argument arc, section ordering, hierarchy | Sentence rhythm / burstiness |
| Claim ↔ evidence linkage at the document level | Specificity density per paragraph |
| Redundant or orphaned sections | Banned AI vocabulary, phrase fingerprints |
| Buried lede, missing scaffolding | The test-before-rewriting calibration gate |
| Whether the *structure* lands one argument (a recommendation section that selects, a decision-asked-for section) | Whether the *prose* commits — phrasing, the closer, balance-of-claims |

Stance is the one fundamental that legitimately appears on both sides, split by level: this pass judges the section-architecture evidence of (non-)commitment; `humanization.md` fundamental 2 owns the phrasing and the closer. Expect both to mention commitment on a hedging document — that is the split working, not a conflict.

The structural pass operates on the argument layer, which is detector-orthogonal, so it MAY run on already-human (sub-15%-AI) prose — but it must never trigger a prose rewrite. After the structural pass, the prose pass is `humanization.md`'s two-pass voice edit, run as usual.

## Pick one document model first

Select the single model the deliverable should follow, then audit the draft against that model's primary rule.

**Ops document arcs (ordered section contracts):**

- **Proposal arc** — executive opening → opportunity framing → solution → proof → commercial/pricing → next steps. Primary rule: the opening states the value before the mechanics; pricing never leads.
- **Intel-brief arc** — bottom line up front → key findings (graded) → supporting detail → sources/freshness → 31C relevance + actions. Primary rule: the conclusion comes first; evidence supports, never leads.
- **Investor-narrative arc** — problem → why now → solution → traction/proof → market → ask. Primary rule: each section earns the next; the ask is set up, not sprung.
- **Argument/decision-note arc** — recommendation → reasoning grouped MECE → evidence → risks/non-goals → decision asked for. Primary rule: recommendation first (pyramid), most-critical argument first.

**Generic models (when no ops arc fits):** Tutorial/Guide (linear, dependency order), Reference (random-access, consistent schema per item, MECE), Explanation (abstract→concrete, scaffolding), Strategic/Pyramid (conclusion-first, evidence supports). Pick the closest and state its primary rule before analysing.

## The structural-operation vocabulary

Each finding is tagged with exactly one operation:

| Operation | When to apply | Word effect |
|---|---|---|
| **CUT** | Section delays understanding or serves no stated purpose | saves |
| **MERGE** | Two sections cover one idea | saves |
| **MOVE** | Right content, wrong place (buried lede, premature detail) | neutral |
| **CONDENSE** | Section earns its place but is over-long | saves |
| **SPLIT** | One section carries two distinct jobs | neutral |
| **ADD** | A load-bearing section is missing (e.g. no Non-Goals, no proof) | costs |
| **PROMOTE / DEMOTE** | A point sits at the wrong hierarchy level | neutral |
| **PRESERVE** | Looks cuttable but serves comprehension — explicitly keep | costs (a guard against over-cutting) |
| **QUESTION** | Needs an author decision before acting | neutral |

PRESERVE is the inverse move: it names a comprehension aid (a summary, an example, a callout) that an aggressive cut would remove, and protects it. Summaries and examples are *reinforcement*, not redundancy — only identical information repeated without purpose is true redundancy.

## The structural-defect checklist

Walk the draft once against each:

1. **Orphan section** — a section that serves no stated purpose. → CUT or MOVE.
2. **Claim without evidence** — an assertion the document never supports. → ADD evidence or soften the claim (QUESTION).
3. **Evidence without claim** — data that supports no stated point. → CUT or attach to a claim.
4. **Buried lede** — the most important thing is deep in the document. → MOVE up / PROMOTE.
5. **Flat hierarchy** — everything at one level; no signal of what matters most. → PROMOTE/DEMOTE.
6. **Redundant paragraphs** — the same point made twice without purpose. → MERGE / CUT.
7. **Missing scaffolding** — a complex idea with no setup. → ADD an overview, or MOVE context earlier.
8. **No structural commitment** — the *structure* never lands the argument: a recommendation/conclusion section that lists options without selecting one, or a missing decision-asked-for section. → flag (QUESTION). Phrasing-level hedging and the closer belong to humanization.md fundamental 2 (see the boundary note above); this pass judges only the section-architecture evidence.
9. **Scope violation** — content that belongs in a different document. → CUT or link.

## Word-savings estimate

For each finding, estimate the words it saves (or costs, for ADD/PRESERVE), grounded in a quick per-section word count taken during the walk. Close with a summary block: total recommendations, estimated reduction (and % of original), whether it meets any stated length target, and any comprehension trade-offs. This is a judgment estimate, not a deterministic diff.

## Completion

"No substantive changes recommended — the structure is sound" is a valid, explicit completion, not a failure. Every structural edit is a recommendation the CEO approves before it is applied (voice.md: no structural changes without approval); the pass proposes, the CEO decides.
