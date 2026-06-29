---
name: proposal
description: Produce a branded 31C proposal (PDF + DOCX) for a commercial deal or partnership using the locked corporate template. Covers sales, partnership, channel, and government proposal types with executive opening, opportunity framing, solution structure, pricing with Voss precision, and next steps. Trigger when the user says "write a proposal", "draft a proposal", "partnership proposal", "sales proposal", "commercial proposal", or asks to prepare a formal business proposal. For MOU / LOI / term sheet structures use /partnership-doc instead.
argument-hint: "[type] [recipient]"
allowed-tools: "Read, Write, Bash(python3:*)"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "2.1"
x-31c-orchestration:
  parallel_safe: true
  shared_state:
   - outputs/documents/
  triggers:
   - write a proposal
   - draft a proposal
   - partnership proposal
   - sales proposal
   - commercial proposal
x-31c-capability:
  what: >
    Produces a branded 31C commercial proposal (PDF + DOCX) from the locked
    corporate template - executive opening, opportunity framing, solution
    structure, Voss-precise pricing, and next steps.
  how: >
    Run /proposal <type> <recipient>. Renders through the doctype pipeline to
    outputs/documents/. Covers sales, partnership, channel, and government
    types.
  when: >
    Use for a commercial offer with pricing. For an MOU/LOI/term sheet use
    /partnership-doc; for a formal letter without pricing use
    /corporate-letter; for an RFP or tender use /rfp-response.
---
# Proposal (31C Branded)

Produce a formal commercial proposal using the locked 31C corporate template. Four-page deliverable: dark cover, executive opening + opportunity, solution + proof, commercial terms + next steps. Renders to PDF + DOCX.

## Variables

- `type`: sales | partnership | channel | government
- `recipient`: organisation name, country, and named sponsor contact
- `objective`: what this proposal is asking for or proposing
- `context`: background on the relationship and deal status

## Phase 0: Context Loading

Read (in order):
- `reference/corporate-style-guide.md` - locked template, checklist, terminology
- `reference/templates/spec-core.md` - the Non-Goals + Success Signal discipline folded into the draft
- `.claude/rules/corporate-docs.md` - guardrail and pipeline
- `.claude/rules/terminology.md`, `.claude/rules/voice.md`, `.claude/rules/voss.md`
- `context/business-info.md` - ODUN.ONE modules, credentials, partner ecosystem
- `context/people.md` - recipient relationship context
- `context/pipeline.md` - current pipeline status
- `reference/billion-growth-playbook.md` - channel economics and partnership structure
- `context/strategy.md` - competitive positioning
- `reference/misha-voice.md` - voice
- `datastore/INDEX.md` - validate any cited facts or numbers

## Phase 1: Classify and Confirm

Announce:

> Using `/proposal` (31C commercial proposal template). PDF + DOCX will be rendered. Locked dark cover, executive opening, solution + proof page, commercial terms with Voss precision, and confidentiality footer applied.

Confirm: proposal type, recipient, sponsor contact, and the specific asking. If this looks like an MOU / LOI / term sheet, recommend `/partnership-doc` instead.

## Phase 2: Draft

### Cover page

- `SUBJECT`: under 80 characters, captures the deal.
- `LEDE`: 1-2 sentences. What is being proposed, who benefits, what timeframe.

### Executive Opening (`EXECUTIVE_OPENING_HTML`)

- Misha's (or sender's) personal note establishing the relationship.
- First-person singular.
- Peer-to-peer, not vendor.
- Under 150 words.

### Opportunity (`OPPORTUNITY_HTML`)

- Frame to the recipient's specific pain (sovereignty, 5G, regulatory, the legacy incumbent exit, quantum readiness).
- Name the exact pain, not generic "digital transformation".
- One paragraph.

### Solution (`SOLUTION_HTML`)

- Name ODUN.ONE modules explicitly: DataONE, ControlONE, TrustONE, VisibilityONE, SecurityONE.
- Deployment model: on-premises, sovereign architecture.
- Call out specific capabilities relevant to the recipient.
- State at least one explicit **Non-Goal** — what this proposal is NOT offering in this engagement — folded into the prose so scope is unambiguous. Frame as scope clarity, never a concession or weakness (Voss: never split the difference). Fields: `reference/templates/spec-core.md`.

### Proof (`PROOF_HTML`)

- Flagship production deployment ([region], [date]) as the anchor.
- Tribe size and hiring standard.
- Partner ecosystem if relevant.
- One paragraph, data-dense.

### Commercial (`COMMERCIAL_INTRO_HTML` + `PRICING_LINES`)

- Intro paragraph frames the commercial structure (Proof of Value pricing, perpetual licence model).
- Pricing lines use Voss precision: no round numbers. EUR 287,450 not EUR 290,000. 47 days not 6 weeks.
- Pricing bundle per `reference/billion-growth-playbook.md`.

### Next Steps (`NEXT_STEPS_HTML`)

- Numbered list (1., 2., 3., ...).
- Each step: action + owner + date.
- No later than 90 days for the final step.
- Name one testable **Success Signal**: the measurable observable that defines the PoV/engagement as succeeded (e.g. classification accuracy on N live flows by day 47), aligned to Principle 1 Proof of Value over PoC.

## Phase 2.5: Structural pass (optional)

For a long or high-stakes proposal, you may run the `/editorial-review` structural checklist over the assembled draft before approval — verify the proposal arc (opening -> opportunity -> solution -> proof -> commercial -> next steps), claim-to-evidence linkage, and that the lede is not buried. Reference: `reference/editorial-review.md`. The prose-level voice pass (`humanization.md`) runs as usual after. Skip when the draft is short or already tight.

## Phase 3: Present for Approval

Show the full draft inline. Run the authoring checklist from `reference/corporate-style-guide.md` (Proposal section) with each box explicitly checked or flagged.

Wait for explicit approval before rendering. Hard stop.

## Phase 4: Render

Assemble the JSON payload matching the `proposal` schema in `scripts/utils/doctype_renderer.py`. Write to `outputs/documents/{sender-slug}/proposal/_work/data.json`. Invoke:

```bash
python scripts/render-doctype.py --type proposal \
  --data outputs/documents/{sender-slug}/proposal/_work/data.json \
  --out outputs/documents/{sender-slug}/proposal/
```

Outputs:
- `{date}_proposal_{recipient-slug}_{subject-slug}.pdf`
- `{date}_proposal_{recipient-slug}_{subject-slug}.docx`

## Phase 5: Report

Full absolute paths. "Word count: X. Hidden characters: clean."

## Reference Numbering

`31C-PROP-{YYYY}-{NNNN}`. Sequence at `outputs/documents/_proposal-sequence.txt`.

## NEVER

- Send without explicit approval.
- Use round-number pricing or timelines.
- Refer to the Tribe as "team", "family", or "crew".
- Write "31 Concept GmbH" or any legal suffix variant.
- Use double dashes in any output.
- Claim ODUN.ONE decrypts encrypted traffic - it classifies via metadata and AI.
- Mention presence in sanctioned countries.
- Skip the hidden-character scan.
