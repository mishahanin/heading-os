---
name: official-doc
description: Produce a 31C official document (board resolution, formal notice, letter of position, certificate of authority) using the locked corporate template. Authoritative voice, declarative language, reference numbering, and official seal block. Renders to PDF + DOCX. Trigger when the user says "board resolution", "formal notice", "letter of position", "certificate of authority", "official document", "official letter", or describes a document that must be issued under corporate authority.
argument-hint: "[class] [subject]"
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
   - board resolution
   - formal notice
   - letter of position
   - official document
   - official letter
   - certificate of authority
   - corporate resolution
x-31c-capability:
  what: >
    Produces a 31C official document - board resolution, formal notice, letter of
    position, certificate of authority, or corporate directive - using the locked
    corporate template with reference numbering and seal block. Renders to PDF +
    DOCX.
  how: >
    Type /official-doc [class] [subject]. It drafts in declarative authoritative
    voice, presents for approval, then renders through render-doctype.py to
    outputs/documents/{sender}/official/.
  when: >
    Use for documents issued under corporate authority. For an external partner
    letter use /corporate-letter; for a commercial proposal with pricing use
    /proposal; for an MOU/LOI/term sheet use /partnership-doc.
---
# Official Document

Produce an authoritative corporate document issued under 31 Concept's corporate authority. Renders to PDF + DOCX.

## Variables

- `class`: one of `Board Resolution` / `Formal Notice` / `Letter of Position` / `Certificate of Authority` / `Corporate Directive`
- `subject`: the resolution/notice subject
- `issuer`: authorised issuing officer (CEO, Board Secretary, legal representative)
- `place`: place of issue (city, jurisdiction)
- `whereas`: list of whereas clauses (for resolutions)
- `resolved`: list of resolution blocks (for resolutions)

## Phase 0: Context Loading

Read:
- `reference/corporate-style-guide.md` - locked template, checklist, terminology
- `.claude/rules/corporate-docs.md` - guardrail
- `.claude/rules/terminology.md`, `.claude/rules/voice.md`

For board resolutions, confirm quorum and governance authority. For formal notices, confirm the notice meets the jurisdictional requirements for the receiving party. For letters of position, confirm the position has been approved by appropriate governance.

## Phase 1: Classify and Confirm

Announce:

> Using `/official-doc` ({class} template). PDF + DOCX will be rendered. Locked header, reference number, seal block, and authoritative voice applied.

Clarify:
- Exact document class.
- Issuing officer's full legal name and title.
- Place and date of issue.
- Whether counsel review has occurred or is required before execution.

## Phase 2: Draft

Voice constraints:
- Declarative and authoritative. No hedging, no marketing language.
- Third-person for the corporate entity: "the Board", "31 Concept", "the Chief Executive Officer".
- Use "Whereas" / "Now, therefore, resolved" structure for resolutions.
- Use "The undersigned, in their capacity as..." for certificates.
- No Tribe references or Five Principles inside official documents - those are internal cultural references, not formal corporate voice.

Reference numbering follows `31C-{CLASS-CODE}-{YYYY}-{QN}-{NNN}`:
- BR: Board Resolution
- FN: Formal Notice
- LP: Letter of Position
- CA: Certificate of Authority
- CD: Corporate Directive

Sequence stored at `outputs/documents/_official-sequence.txt` (separate counter per class-code-year-quarter).

## Phase 3: Present for Approval

Show full draft inline. Run the authoring checklist from `reference/corporate-style-guide.md` (Official Document section).

Wait for explicit approval before rendering. Hard stop.

## Phase 4: Render

Assemble the JSON payload for the `official` schema. Write to `outputs/documents/{sender-slug}/official/_work/data.json`. Invoke:

```bash
python scripts/render-doctype.py --type official \
  --data outputs/documents/{sender-slug}/official/_work/data.json \
  --out outputs/documents/{sender-slug}/official/
```

Outputs: PDF + DOCX.

## Phase 5: Report

Full absolute paths. "Word count: X. Hidden characters: clean." Reminder that counsel countersign or corporate seal application may be required before this document takes legal effect.

## NEVER

- Sign or release without explicit approval.
- Use hedging language ("we think", "probably", "should").
- Use marketing language or Tribe vocabulary inside official documents.
- Issue a board resolution without confirming quorum in the preamble.
- Skip the hidden-character scan.
