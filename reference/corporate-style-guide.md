# 31C Corporate Style Guide

Consolidated authority for all external-facing documents produced by any 31C executive. This file is the entry point every doctype skill reads first.

Last Updated: 2026-06-05
Last Verified: 2026-06-05

## Scope

Governs five in-scope document types. All other document classes (internal Tribe messages, LinkedIn, press/marketing) follow their own skills and are out of scope.

| # | Doctype | Skill | Render formats |
|---|---|---|---|
| 1 | External Letter | `/corporate-letter` | PDF + DOCX |
| 2 | Proposal | `/proposal` | PDF + DOCX |
| 3 | Partnership Document (MOU / LOI / term sheet) | `/partnership-doc` | PDF + DOCX |
| 4 | Official Document (resolution, formal notice, position letter) | `/official-doc` | PDF + DOCX |
| 5 | OnePager (xPager) | `/xpager` | PDF + HTML |

## Source of Truth (distributed)

No single file owns the brand. The authorities below, collectively, are the reference. If any two conflict, the Visual Authority (Product Deck) wins on visual questions and `.claude/rules/terminology.md` wins on linguistic questions.

| Layer | File |
|---|---|
| Visual authority (layout, color usage, typographic weight) | `datastore/products/odun-one/presentations/31C - ODUN.ONE Product Presentation (Master, 12-Apr-2026).pdf` |
| DOCX master template | `datastore/brand/templates/31C - Master Template (New Identity 2026 v1.01).docx` |
| DOCX Word template | `datastore/brand/templates/31C - Master Template (New Identity 2026 v1.01).dotx` |
| Logos (4 variants) | `datastore/brand/assets/logos/` |
| Fonts: GT Standard (Light + Medium, optical sizes L/M/S) | `datastore/brand/fonts/GT Standard/` |
| Fonts: 31C TypeFace (display / markers only) | `datastore/brand/fonts/31C_TypeFace/` |
| HTML/PDF CSS (dual-mode, signature elements) | `.claude/skills/design/references/brand.css` |
| PPTX brand tokens | `.claude/skills/pptx-generator/brands/31c/brand.json` |
| Voice | `reference/misha-voice.md` |
| Terminology (Tribe, ODUN.ONE, DPI+, Five Principles) | `.claude/rules/terminology.md` |
| Writing rules (hyphens, "31 Concept" only) | `.claude/rules/voice.md` |
| Negotiation overlay | `.claude/rules/voss.md` |
| Email signature + confidentiality notice | `reference/email-signature.html` |
| xPager production reference | `outputs/content/odun-one-xpager-2026/generate-v6.py` |
| Hidden-character policy | `.claude/rules/hidden-chars.md` |

## Colors (locked)

| Token | Hex | Usage |
|---|---|---|
| `--accent` | `#5B5FFF` | Primary accent, card top border, blue accent blocks |
| `--orange` | `#F5922B` | Signature orange corner block (56x68px) |
| `--orange-hi` | `#FF8C00` | Highlight text |
| `--bg-light` | `#EEECEA` | Content document background |
| `--bg-dark` | `#000000` | Cover slides, hero pages |
| `--card-light` | `#FFFFFF` | Light-mode cards |
| `--card-dark` | `#12122A` | Dark-mode cards |
| `--ink` | `#151515` | Primary text on light backgrounds |
| `--text-secondary-light` | `#5A5A78` | Secondary text on light |
| `--text-secondary-dark` | `#B0B0C0` | Secondary text on dark |

## Typography (locked)

- Primary: **GT Standard** (M Medium weight 500 for headings, M Light weight 300 for body).
- Fallbacks in order: `Inter, Segoe UI, Calibri, Arial, sans-serif`.
- Display/markers only: `31C TypeFace` (never for readable body text).
- Code: `JetBrains Mono`.

## Signature Brand Elements (every doctype)

1. **Orange corner block** - top-left, 56x68px, `#F5922B`, no border-radius. THE #1 brand marker on every first page.
2. **31C logo** - bottom-left (Palatinate Blue on light backgrounds, White on dark). Path: `datastore/brand/assets/logos/`.
3. **Copyright footer** - bottom-right: `© 2025-2026 / 31 Concept 31C.io / Proprietary & Confidential`.
4. **Blue top border on cards** - 3-4px solid `#5B5FFF`.
5. **Confidentiality notice** - on every page footer of letters, proposals, partnership docs, and official documents.

## Letterhead Block (locked)

Used on the first page of letters, proposals, partnership docs, and official documents.

```
[orange corner block]        [31C logo, bottom-left of letterhead row]
31 Concept
Deep Packet Intelligence

Sender: [Executive Full Name]
        [Title]
        [Sender Email] | [Sender Phone]

To:     [Recipient Full Name]
        [Recipient Title]
        [Recipient Organization]

Subject: [Document Subject]
Ref:     [Document Ref ID - auto-generated]
Date:    [YYYY-MM-DD]
```

## Signature Block (locked)

```
Sincerely,

[Executive Full Name]
[Title]
31 Concept
[Sender Email] | [Sender Phone]
```

## Footer (locked, every page)

```
[left: blue square 10px]  31C.io  [right: orange square 10px]
                          © 2025-2026 / 31 Concept 31C.io / Proprietary & Confidential
```

## File Naming Convention (locked)

`YYYY-MM-DD_{doctype}_{recipient-slug}_{short-subject-slug}.{ext}`

Examples:
- `2026-04-21_letter_agency-gov_partnership-invitation.pdf`
- `2026-04-21_proposal_exampletelco_dpi-migration.pdf`
- `2026-04-21_partnership_partnerco_channel-mou.pdf`
- `2026-04-21_official_board-resolution-2026-q2.pdf`
- `2026-04-21_xpager_odun-one-q2-2026.pdf`

Slugs are lowercase, hyphen-separated, ASCII only, max 40 chars per slug.

## Authoring Checklists (per doctype)

Each skill enforces these before presenting the draft.

### 1. External Letter

- [ ] Recipient full name, title, organization - verified against CRM or user-provided
- [ ] Subject line present and under 70 characters
- [ ] Opening: peer-to-peer, no vendor framing
- [ ] Body: 1-3 short paragraphs, under 350 words total
- [ ] Closing: one concrete next step with owner and timing
- [ ] Signature block from sender's executive profile
- [ ] Terminology: uses "Tribe" never "team"; uses "31 Concept" never "31 Concept GmbH"; uses single hyphens only
- [ ] Voice: first-person singular from the sender
- [ ] No round-number pricing (Voss precision)
- [ ] Confidentiality footer present
- [ ] Hidden-character scan: clean

### 2. Proposal

- [ ] Recipient organization + country + sponsor contact named
- [ ] Executive opening under 150 words
- [ ] Opportunity framed to the recipient's pain (sovereignty, 5G, regulatory, incumbent exit)
- [ ] ODUN.ONE modules named explicitly (DataONE / ControlONE / etc.)
- [ ] Pricing uses Voss precision (no round numbers)
- [ ] flagship production deployment referenced as proof
- [ ] Five Principles echoed (at least one: Proof of Value, Partnership for Life, Operate with Integrity, Deliver Under Pressure, Data Sovereignty Always)
- [ ] Next steps numbered with owners and timing
- [ ] At least one explicit Non-Goal stated (what this proposal is NOT offering) - scope clarity, not a concession
- [ ] One testable Success Signal named (the measurable observable that defines the engagement as succeeded)
- [ ] Confidentiality footer present
- [ ] Hidden-character scan: clean

### 3. Partnership Document

- [ ] Parties fully named with legal entity and jurisdiction
- [ ] Document subtype declared in subject (MOU / LOI / Term Sheet)
- [ ] Effective date and term explicit
- [ ] Mutual obligations symmetrical where possible
- [ ] Exclusivity, territory, and confidentiality clauses present or explicitly waived
- [ ] Governance and dispute resolution section
- [ ] Signature blocks for both parties
- [ ] Voice: neutral legal-adjacent, not marketing prose
- [ ] No round-number economics
- [ ] Hidden-character scan: clean

### 4. Official Document

- [ ] Document class declared in header (Board Resolution / Formal Notice / Letter of Position / Certificate)
- [ ] Issuing entity: "31 Concept" - never legal-suffix variants
- [ ] Reference number auto-generated: `31C-{class}-{YYYY}-{seq}`
- [ ] Date, place, and signing officer explicit
- [ ] Authoritative voice - declarative, no hedging
- [ ] No marketing language
- [ ] Seal/signature block present
- [ ] Hidden-character scan: clean

### 5. xPager (OnePager)

- [ ] Cover page: full-bleed hero, zero PDF margins
- [ ] ODUN.ONE wordmark: text built in HTML/CSS with GT Standard, ".ONE" in `#5B5FFF`
- [ ] "by 31C" uses white logo from `datastore/brand/assets/logos/`, never PDF-extracted
- [ ] Stats bar: 4 data points max
- [ ] Logo top-right inner pages: Palatinate Blue, 56px wide, 0.5 opacity
- [ ] Footer: blue+orange squares at 36px, "31C.io" centered, no background band
- [ ] Closing page: raster embed (never HTML rebuild)
- [ ] Hidden-character scan: clean

## Locked Language

The following terms are locked across all five doctypes:

- **Tribe** / **31C Tribe** - the company's people. Never "team", "family", "crew".
- **ODUN.ONE** - platform name. Never "Odun One", "odun.one" (lowercase), "OdunOne".
- **DPI+** - Deep Packet Intelligence Plus. Never "DPI Plus", "DPI+ (deep packet intelligence plus)".
- **31 Concept** - full company name. Never "31 Concept GmbH", "31 Concept Ltd", "31C GmbH".
- **Five Core Principles** - never "Five Rules", "Five Values".
- **Partnership for Life** - never "lifetime partnership", "long-term partnership".
- **Proof of Value (PoV)** - never "PoC", "proof of concept" when describing 31C's approach.

## Forbidden Patterns

- Double dashes `--` - always single hyphen `-`.
- Corporate jargon: "synergy", "leverage" (as verb), "utilize", "at scale", "best-in-class", "cutting-edge".
- Round-number pricing ($350,000, "about 17 days"). Use Voss precision: $347,850, 17 days.
- Third-person self-reference in executive letters ("31C believes..."). Use first-person singular.
- Claims of presence in sanctioned countries or territories. Verify every country mention.
- Claims that ODUN.ONE "decrypts" or "breaks" encryption. The platform classifies encrypted traffic via metadata and AI.

## Guardrail Behavior

When any executive requests one of the five document types in conversation - even without naming a template - Claude auto-selects the correct skill, applies this style guide, and announces which doctype was selected and why. See `.claude/rules/corporate-docs.md` for the full protocol.

## Missing or Ambiguous Rules

None flagged as of 2026-04-21. If a future doctype requirement is not covered here, the authoring skill must stop and surface the gap rather than improvise.

## Change Control

This file is corporate-classified. Changes require CEO approval. After edit:

1. Bump `Last Updated` on line 5.
2. Advance `Last Verified` to today.
3. Run `/push-updates` to propagate to all execs.
