---
name: partnership-doc
description: Produce a 31C partnership document (MOU, Letter of Intent, or term sheet) using the locked corporate template. Legal-adjacent document defining mutual obligations, governance, territory, exclusivity, and confidentiality between 31 Concept and a counterparty. Renders to PDF + DOCX. Trigger when the user says "MOU", "LOI", "memorandum of understanding", "letter of intent", "term sheet", "partnership agreement", "partnership document", or describes a collaboration framework with mutual obligations rather than a commercial proposal.
argument-hint: "[subtype] [counterparty]"
allowed-tools: "Read, Write, Bash(python3:*)"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: true
  shared_state:
   - outputs/documents/
  triggers:
   - MOU
   - LOI
   - memorandum of understanding
   - letter of intent
   - term sheet
   - partnership agreement
   - partnership document
x-31c-capability:
  what: >
    Produces a locked 31C partnership document (MOU, LOI, term sheet, or
    partnership agreement) defining mutual obligations, territory, exclusivity,
    governance, and confidentiality - rendered to PDF + DOCX.
  how: >
    Run /partnership-doc <subtype> <counterparty>. Renders via
    scripts/render-doctype.py to outputs/documents/; approval gate before
    render, and a counsel-review reminder on every output.
  when: >
    Use for a legal-adjacent collaboration framework. For a commercial offer
    with pricing use /proposal; for a formal letter use /corporate-letter.
---
# Partnership Document (MOU / LOI / Term Sheet)

Produce a locked 31C partnership document. Use for legal-adjacent collaboration frameworks. For commercial pricing and module activation, use `/proposal` instead.

## Variables

- `subtype`: one of `Memorandum of Understanding` / `Letter of Intent` / `Term Sheet` / `Partnership Agreement`
- `counterparty`: the other party's legal name, jurisdiction, and address
- `scope`: territory, duration, exclusivity posture
- `commercial_outline`: margin structure, payment terms (non-binding for MOU/LOI, binding for term sheet)
- `governance`: dispute resolution preference

## Phase 0: Context Loading

Read:
- `reference/corporate-style-guide.md` - locked template, checklist, terminology
- `.claude/rules/corporate-docs.md` - guardrail
- `.claude/rules/terminology.md`, `.claude/rules/voice.md`, `.claude/rules/voss.md`
- `context/partners.md`, `context/pipeline.md` - existing relationship state
- `reference/billion-growth-playbook.md` - channel economics where relevant

## Phase 1: Classify and Confirm

Announce:

> Using `/partnership-doc` ({subtype} template). PDF + DOCX will be rendered. Locked parties block, clause structure, signature grid, and confidentiality applied.

Clarify any of these before drafting:
- Subtype (MOU vs LOI vs term sheet) - they differ in binding scope.
- Counterparty's full legal name, entity type, jurisdiction, principal office address.
- Authorised signatory name and title on both sides.
- Territory and term.
- Exclusivity posture.
- Governance and dispute resolution jurisdiction preference.

## Phase 2: Draft Clauses

Voice: neutral legal-adjacent, not marketing prose. No Voss negotiation moves inside the document - those belong in the cover letter and negotiation playbook, not the executed text.

Default clause set:
1. Territory
2. Exclusivity (grant, carve-outs, minimum performance)
3. Commercial terms (margin, payment days, term length)
4. Training and certification
5. Marketing and brand use
6. Data protection and sovereignty

Supplement with domain-specific clauses as needed. Every clause: `num`, `title`, `body`.

Mutual obligations must be symmetrical where possible. Non-symmetrical clauses (e.g., IP ownership, product control) should be explicitly flagged in the Governance section.

## Phase 3: Present for Approval

Show the full document draft inline with all clauses. Run the authoring checklist from `reference/corporate-style-guide.md` (Partnership Document section).

Wait for explicit approval before rendering. Hard stop.

## Phase 4: Render

Assemble the JSON payload matching the `partnership` schema. Write to `outputs/documents/{sender-slug}/partnership/_work/data.json`. Invoke:

```bash
python scripts/render-doctype.py --type partnership \
  --data outputs/documents/{sender-slug}/partnership/_work/data.json \
  --out outputs/documents/{sender-slug}/partnership/
```

Outputs: PDF + DOCX.

## Phase 5: Report

Full absolute paths. "Word count: X. Hidden characters: clean."

## Reference Numbering

`31C-{SUBTYPE-CODE}-{YYYY}-{NNNN}` where SUBTYPE-CODE is `MOU`, `LOI`, `TS`, or `PA`. Sequence stored at `outputs/documents/_partnership-sequence.txt`.

## NEVER

- Sign or send without explicit approval.
- Include Voss negotiation moves inside the executed text (they belong in the cover letter).
- Use "31 Concept" as anything other than exactly "31 Concept".
- Create binding commercial terms in an MOU (use LOI or term sheet instead).
- Skip legal review flag: at the end of every output, remind the user that counsel review is required before execution.
