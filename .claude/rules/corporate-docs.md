<!-- version: 1.0.0 | last-updated: 2026-04-21 -->
# Corporate Document Guardrail

Last Updated: 2026-04-21
Last Verified: 2026-06-08

Always-active rule governing five external-facing document types. When any executive requests one of these types, the correct 31C-branded skill is applied automatically - the executive never has to ask for branding, tone, or format.

Consolidated reference: `reference/corporate-style-guide.md` (read first before drafting any of the five types).

## In-Scope Document Types

| # | Doctype | Skill | Render formats |
|---|---|---|---|
| 1 | External Letter | `/corporate-letter` | PDF + DOCX |
| 2 | Proposal | `/proposal` | PDF + DOCX |
| 3 | Partnership Document (MOU / LOI / term sheet) | `/partnership-doc` | PDF + DOCX |
| 4 | Official Document (resolution, formal notice, position letter) | `/official-doc` | PDF + DOCX |
| 5 | OnePager (xPager) | `/xpager` | PDF + HTML |

## Out of Scope

Internal Tribe messages, LinkedIn posts, press releases, marketing collateral. These have dedicated skills (`/tribe-message`, `/linkedin-post`, etc.) and do not use the five locked templates.

## Trigger Protocol

When a user message matches any of the patterns below, auto-select the corresponding skill, announce the selection, and apply the locked template. The executive does not need to name the template.

### Classifier Signals

| If the message contains... | Select |
|---|---|
| "letter to", "write to [named recipient]", "formal letter", "letter of [interest\|introduction\|thanks]" | `/corporate-letter` |
| "proposal", "commercial proposal", "partnership proposal" (commercial structure), "sales proposal", "offer" | `/proposal` |
| "MOU", "LOI", "memorandum of understanding", "letter of intent", "term sheet", "partnership agreement", "partnership document" | `/partnership-doc` |
| "board resolution", "formal notice", "letter of position", "certificate", "official document", "official letter" | `/official-doc` |
| "xPager", "x-pager", "onepager", "one-pager", "1-pager", "product one-pager", "capability sheet" | `/xpager` |

### Ambiguity Resolution

If the message spans two categories (e.g., "partnership proposal" could be commercial `/proposal` or legal `/partnership-doc`):

1. Look for structural signals. Legal/MOU/LOI/term sheet -> `/partnership-doc`. Commercial pricing/module activation -> `/proposal`.
2. If still ambiguous, ask one targeted question: "Commercial proposal with pricing and modules, or MOU/LOI defining mutual obligations?"

### Silent Fall-Through Is Forbidden

If a request unambiguously falls into one of the five types, Claude MUST announce the selection in the first response line: `Using /{skill} (external letter template). Confidentiality footer applied, GT Standard typography, 31C letterhead.` Announcement is non-negotiable - it gives the executive an audit trail of what template is being applied.

## Brand Enforcement (applies to all five types)

Before drafting, every skill in this group loads:

1. `reference/corporate-style-guide.md` - locked colors, typography, letterhead, signature, footer, file naming, authoring checklist
2. `.claude/rules/terminology.md` - Tribe, ODUN.ONE, DPI+, Five Principles
3. `.claude/rules/voice.md` - writing rules
4. `reference/misha-voice.md` (or the sender's executive voice file if present)
5. `.claude/rules/voss.md` - negotiation overlay
6. `.claude/rules/hidden-chars.md` - zero invisible characters

After drafting, every skill runs:

- `python scripts/sanitize-text.py {path} --scan` on the generated HTML/MD
- Authoring checklist from `reference/corporate-style-guide.md` for that doctype

Before declaring complete, the skill must state: `Word count: X. Hidden characters: clean.` If the scan found and removed characters, it must say so explicitly.

## Rendering Pipeline

All five types render through one script: `scripts/render-doctype.py`.

```
python scripts/render-doctype.py --type {letter|proposal|partnership|official|xpager} \
  --data path/to/data.json --out outputs/documents/{sender}/ \
  --formats {pdf,docx|pdf,html}
```

The renderer:

1. Loads the locked HTML template from `datastore/brand/templates/doctypes/{type}.html`.
2. Substitutes placeholders from the JSON data file.
3. Embeds logos, fonts, brand CSS inline (self-contained output).
4. Renders PDF via Playwright (`scripts/html-to-pdf.py`).
5. For types needing DOCX (letter, proposal, partnership, official), also renders DOCX via python-docx using the brand master template.
6. For xpager, also saves the HTML alongside the PDF.
7. Returns the output file paths.

## Authoring Outputs Location

Rendered documents land in `outputs/documents/{sender-slug}/{doctype}/` using the locked file naming convention:

`YYYY-MM-DD_{doctype}_{recipient-slug}_{short-subject-slug}.{ext}`

Senders without an explicit slug default to `misha-hanin`.

## Classification

- `reference/corporate-style-guide.md` - corporate (shared with all execs via `reference/` directory default).
- `.claude/rules/corporate-docs.md` (this file) - corporate.
- `datastore/brand/templates/doctypes/` - corporate.
- `scripts/render-doctype.py` - corporate.
- `.claude/skills/corporate-letter/`, `.claude/skills/partnership-doc/`, `.claude/skills/official-doc/`, `.claude/skills/xpager/` - corporate.
- Rendered outputs (`outputs/documents/`) - ceo-only (local to each exec).

## Change Control

Changes to any locked template require CEO approval. After edit, run `/push-updates` to propagate to all execs.
