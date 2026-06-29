---
name: corporate-letter
description: Produce a branded 31C external letter (PDF + DOCX) using the locked corporate template. Use for formal correspondence to customers, partners, regulators, investors, counsel, and institutional contacts. Applies the locked letterhead, signature block, footer, and confidentiality notice automatically. Trigger when the user says "write a letter to", "letter of introduction", "letter of interest", "formal letter", "external letter", "send a letter to [named recipient]", or when the user requests formal written correspondence that is not a proposal, MOU, or official document.
argument-hint: "[recipient] [subject]"
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
   - write a letter to
   - external letter
   - formal letter
   - letter of introduction
   - letter of interest
   - letter of thanks
   - letter to
x-31c-capability:
  what: >
    Produces a branded 31C external letter (PDF + DOCX) from the locked corporate template,
    with letterhead, signature block, confidentiality footer, and a 31C-LET reference number
    applied automatically.
  how: >
    Run /corporate-letter <recipient> <subject>. Drafts the body, shows it for approval (hard stop),
    then renders via scripts/render-doctype.py to outputs/documents/{sender-slug}/letter/.
  when: >
    Use for formal correspondence to customers, partners, regulators, or investors. For peer CEO
    correspondence use /ceo-to-ceo; for a commercial offer with pricing use /proposal; for an
    MOU/LOI use /partnership-doc.
---
# External Letter (31C Branded)

Produce a formal external letter using the locked 31C corporate template. Renders to PDF and DOCX.

## Variables

- `recipient`: full name, title, organisation
- `subject`: subject line (under 70 chars)
- `objective`: what this letter should accomplish
- `context`: relationship and situational background

## Phase 0: Context Loading

Before drafting, read:
- `reference/corporate-style-guide.md` - locked template, colors, typography, signature, checklist
- `.claude/rules/corporate-docs.md` - guardrail and pipeline
- `.claude/rules/terminology.md` - Tribe, ODUN.ONE, DPI+, Five Principles
- `.claude/rules/voice.md` - writing rules
- `reference/misha-voice.md` (or sender's voice file if present)
- `.claude/rules/voss.md` - negotiation overlay
- `context/people.md` - CRM relationship context if the recipient is known
- `context/pipeline.md` - if this letter relates to an active pipeline entry

## Phase 1: Classify and Confirm

Announce the selection before drafting:

> Using `/corporate-letter` (31C external letter template). PDF + DOCX will be rendered. Locked letterhead, GT Standard typography, confidentiality footer applied.

If the recipient, subject, or objective is missing, ask one targeted block of questions before drafting.

## Phase 2: Draft the Letter Body

Constraints:
- First-person singular from the sender.
- Opening paragraph: peer-to-peer, not vendor. State the reason for writing in sentence one.
- Body: 1-3 short paragraphs, under 350 words total.
- Closing: one concrete next step with owner and timing.
- No round-number pricing or dates. Voss precision always.
- Use single hyphens `-` never double dashes.
- Never use "31 Concept GmbH" or legal-suffix variants. Always "31 Concept".
- Use "Tribe" for the company's people, never "team", "family", "crew".

The body HTML should be a sequence of `<p>...</p>` paragraphs. Do not include the salutation inside `BODY_HTML` - the salutation is a separate placeholder.

## Phase 3: Present for Approval

Show the draft inline, followed by the authoring checklist from `reference/corporate-style-guide.md` (External Letter section) with each box ticked or explicitly flagged.

Wait for explicit approval ("send", "go", "approve") before rendering. This is a hard stop.

## Phase 4: Render

Once approved, assemble the JSON payload and invoke the renderer.

Required JSON fields (per `scripts/utils/doctype_renderer.py` `TEMPLATE_REGISTRY["letter"]`):

```json
{
  "SENDER_NAME": "...",
  "SENDER_TITLE": "...",
  "SENDER_EMAIL": "...",
  "SENDER_PHONE": "...",
  "RECIPIENT_NAME": "...",
  "RECIPIENT_TITLE": "...",
  "RECIPIENT_ORG": "...",
  "SUBJECT": "...",
  "DATE": "YYYY-MM-DD",
  "REF_ID": "31C-LET-{YYYY}-{NNNN}",
  "SALUTATION": "<p>Dear ...,</p>",
  "BODY_HTML": "<p>...</p><p>...</p>"
}
```

Write the payload to `outputs/documents/{sender-slug}/letter/_work/data.json`, then:

```bash
python scripts/render-doctype.py --type letter \
  --data outputs/documents/{sender-slug}/letter/_work/data.json \
  --out outputs/documents/{sender-slug}/letter/
```

The renderer produces three files with the locked filename convention:
- `{date}_letter_{recipient-slug}_{subject-slug}.html` (build artefact)
- `{date}_letter_{recipient-slug}_{subject-slug}.pdf`
- `{date}_letter_{recipient-slug}_{subject-slug}.docx`

## Phase 5: Report

Present the full absolute paths of the PDF and DOCX. State: "Word count: X. Hidden characters: clean."

## Reference Number Generation

Reference IDs follow `31C-LET-{YYYY}-{NNNN}` where NNNN is a zero-padded sequence. The skill reads the latest used sequence from `outputs/documents/_letter-sequence.txt` (creating it at 0001 if absent) and increments it.

## NEVER

- Send without explicit approval.
- Use round-number dates or pricing.
- Refer to the Tribe as "team", "family", or "crew".
- Write "31 Concept GmbH" or any legal suffix variant.
- Skip the hidden-character scan.
- Use double dashes in any output.
