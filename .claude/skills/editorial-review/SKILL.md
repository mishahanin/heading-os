---
name: editorial-review
description: >
  Document-level STRUCTURAL editor for long deliverables. Reviews a draft's argument
  architecture -- section ordering, claim-to-evidence linkage, hierarchy, redundancy,
  buried lede, missing sections -- and proposes CUT / MERGE / MOVE / CONDENSE / SPLIT /
  ADD / PROMOTE / PRESERVE operations with word-savings estimates, BEFORE any sentence is
  touched. Content is sacrosanct: it reorganizes, it never argues with the ideas. Phase 2
  hands all sentence-level work (rhythm, specificity, vocabulary) to the always-on
  humanization.md prose rule -- it never duplicates that. Use when the user says "editorial
  pass", "structural review", "review the structure of this", "tighten this document",
  "restructure this draft", or wants a long proposal / brief / report's argument arc
  checked. Do NOT use for: sentence-level prose / "make this sound human" (that is
  humanization.md), typo or grammar fixes (sanitize-text + humanization-check), fact-checking
  (/validate), artifact grading (/evaluate), short chat replies, or atomic /zk notes.
argument-hint: "[file:<path> | pasted text]"
allowed-tools: "Read, Edit, Write, Bash(python3:*), Bash(python:*)"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: false
  shared_state: []
  triggers:
    - "editorial review"
    - "structural review"
    - "review the structure"
    - "editorial pass"
    - "tighten this document"
    - "restructure this draft"
x-31c-capability:
  what: >
    A document-level structural editor for long deliverables. It maps a draft's
    argument architecture and proposes CUT / MERGE / MOVE / CONDENSE / SPLIT /
    ADD / PROMOTE / PRESERVE operations with word-savings estimates, then applies
    only the approved ones. It reorganizes; it never rewrites sentences or argues
    with the ideas.
  how: >
    Run /editorial-review file:<path> (or paste the text). Phase 1 presents
    findings behind an approval gate; Phase 2 hands all sentence-level prose work
    to the humanization.md rule and runs the sanitize + humanization-check gates.
  when: >
    Use on a long proposal, brief, or report whose argument arc needs checking.
    For sentence-level "make this sound human" work use the humanization rule;
    for fact-checking use /validate; for artifact grading use /evaluate.
---
# Editorial Review (structural pass)

Critique and tighten a long deliverable's **argument architecture** — then hand the prose off to the humanization layer. This skill operates on structure only; it never rewrites sentences and never challenges the ideas.

The full method (document arcs, operation vocabulary, defect checklist, the hard boundary with `humanization.md`) lives in `reference/editorial-review.md`. Load it; do not restate it here.

---

## Phase 0: Load context

1. Read `reference/editorial-review.md` (the document-model arcs, the operation vocabulary, the defect checklist, the boundary table).
2. Read `.claude/rules/humanization.md` — confirm the boundary: this skill stops at the paragraph; everything below it (rhythm, specificity, banned vocabulary, the calibration gate) belongs to that rule.
3. Read the target document **in full** (the `file:<path>` argument, or the pasted text). Never review a draft you have only skimmed.

## Phase 1: Structural pass

1. **Select one document model** from `reference/editorial-review.md` (proposal arc, intel-brief arc, investor-narrative arc, argument/decision-note arc, or a generic model). State the choice and its primary rule in one sentence: "This deliverable should follow the {model}; its primary rule is {rule}."
2. **Map the structure** — list each major section with an approximate word count. This forces measurement before recommendation.
3. **Walk the defect checklist** (orphan section, claim-without-evidence, evidence-without-claim, buried lede, flat hierarchy, redundant paragraphs, missing scaffolding, no document-level stance, scope violation).
4. **Emit findings**, each tagged with exactly one operation (CUT / MERGE / MOVE / CONDENSE / SPLIT / ADD / PROMOTE / DEMOTE / PRESERVE / QUESTION), a one-sentence rationale, and a word-impact estimate. PRESERVE protects comprehension aids from over-cutting (summaries and examples are reinforcement, not redundancy). Close with the summary block: total recommendations, estimated reduction (and % of original), meets-length-target verdict, comprehension trade-offs.
5. **Optional — pull a critique method.** For a hard restructure, you may run `python scripts/elicit.py show "Critique and Refine"` or `"Red Team vs Blue Team"` from the shared elicitation catalog and apply it to the argument arc. Skip it when the checklist already bites.
6. **APPROVAL GATE.** Present the findings. Apply NO structural edit until the CEO approves (per `.claude/rules/voice.md`: no structural changes without approval). "No substantive changes recommended — the structure is sound" is a valid, explicit completion. On approval, apply only the approved operations, editing structure (move/cut/merge/condense), never wording.

## Phase 2: Hand off to the prose layer

1. State explicitly: "Structural pass complete. Sentence-level rhythm, specificity, and vocabulary are now governed by `.claude/rules/humanization.md` (two-pass voice edit)." Do not perform that work here.
2. Run the standard prose gates on the resulting document:
   - `python scripts/sanitize-text.py <path> --scan`
   - `python scripts/humanization-check.py <path>`
3. Report the validation line: `Structural ops applied: N. Hidden characters: clean. Humanisation audit: <result>.`

---

## Voice rules

- Hyphens, not em-dashes in the operation labels and prose; ODUN.ONE, DPI+, Tribe per `.claude/rules/terminology.md`.
- The skill proposes; the CEO decides. Frame every finding as a recommendation, never an executed change.
- Sentence case in any headings the skill itself emits.

## NEVER

- NEVER do sentence-level prose work — rhythm, specificity density, banned-vocabulary swaps, or the calibration gate. That is `humanization.md`'s job; duplicating it is a defect.
- NEVER challenge or rewrite the ideas. Content is sacrosanct; reorganize only.
- NEVER apply a structural edit before the CEO approves the findings.
- NEVER trigger a prose rewrite on already-human (sub-15%-AI) text — the structural pass operates on the argument layer only and leaves human prose byte-for-byte intact.
- NEVER run on an atomic `/zk` note or a short chat reply — the structural pass is a near no-op there.
- NEVER declare done without the Phase 2 sanitize + humanization-check gates and the validation line.
