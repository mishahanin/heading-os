---
name: xpager
description: Produce a 31C OnePager (xPager) using the locked corporate template. Three-page branded document - dark full-bleed cover with ODUN.ONE wordmark, capability page, and proof/contact page. Renders to PDF + HTML. Trigger when the user says "xPager", "x-pager", "onepager", "one-pager", "1-pager", "product one-pager", "capability sheet", or asks for a single concise branded summary of a 31C product, module, or offering.
argument-hint: "[product] [audience]"
allowed-tools: "Read, Write, Bash(python3:*)"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-heading-orchestration:
  parallel_safe: true
  shared_state:
   - outputs/documents/
  triggers:
   - xPager
   - x-pager
   - onepager
   - one-pager
   - 1-pager
   - product one-pager
   - capability sheet
x-heading-capability:
  what: >
    Produces a three-page branded 31C OnePager (xPager) - dark full-bleed cover with the ODUN.ONE wordmark, a capability-card page, and a proof/contact page - using the locked corporate template.
  how: >
    Run /xpager [product] [audience]; it drafts cover tagline, 4 stats, capability cards, and proof cards, waits for approval, then renders PDF + HTML via scripts/render-doctype.py into outputs/documents/{sender}/xpager/.
  when: >
    Use for a single concise branded summary of a product or module. For a multi-page client deck use /pptx-generator; for a commercial proposal with pricing use /proposal.
x-heading-routing:
  category: Strategy
  triggers:
    - xPager
    - x-pager
    - onepager
    - one-pager
    - 1-pager
    - product one-pager
    - capability sheet
  exclusions:
    - Multi-page client deck -> /pptx-generator
    - simple render -> /marp
  compound: 'No'
  router: auto
---
# xPager (OnePager)

Produce a three-page branded OnePager for a 31C product, module, or offering. Renders to PDF + HTML.

## Variables

- `product`: name (default `ODUN.ONE`), with base+suffix split for the wordmark (e.g., `ODUN` + `ONE`)
- `tagline`: 1-2 sentence positioning statement
- `audience`: who the xPager is for (regulator, operator, investor, partner)
- `stats`: exactly 4 top-line data points
- `capabilities`: 4-6 capability cards (key, title, description)
- `proof_points`: 4 proof cards (key, title, description)
- `contact`: name, title, email, phone of the point of contact

## Phase 0: Context Loading

Read:
- `reference/corporate-style-guide.md` - locked template, checklist (xPager section)
- `.claude/rules/corporate-docs.md` - guardrail
- `.claude/rules/terminology.md` - ODUN.ONE, DPI+, Five Principles
- `reference/misha-voice.md` - voice for taglines and descriptions
- Existing xPager reference: `outputs/content/odun-one-xpager-2026/` for visual continuity

## Phase 1: Classify and Confirm

Announce:

> Using `/xpager` (OnePager template). PDF + HTML will be rendered. Full-bleed cover, locked wordmark with blue `.ONE` accent, stats bar, capability cards, proof cards, and brand footer applied.

Confirm product name, audience, and whether this is a fresh xPager or an update to an existing one.

## Phase 2: Draft

Constraints:
- Cover tagline under 180 characters, no corporate jargon.
- Stats bar: exactly 4 data points. Use Voss precision on numeric values.
- Capability cards: 4-6. Each card's description 1-2 sentences, under 220 characters.
- Proof cards: exactly 4. the flagship deployment is a mandatory proof point for any ODUN.ONE xPager.
- Contact: use the actual executive issuing the xPager.
- Use "Tribe" never "team". Use "ODUN.ONE" never "Odun One" or "odun.one".

Do NOT attempt to rebuild the closing page in HTML - if a raster closing exists in `outputs/content/odun-one-xpager-2026/extracted-assets/`, reuse it; otherwise, the three-page layout suffices.

## Phase 3: Present for Approval

Show:
- Cover preview (tagline + stats)
- Capability card grid
- Proof card grid
- Contact block

Run the authoring checklist from `reference/corporate-style-guide.md` (xPager section).

Wait for explicit approval before rendering. Hard stop.

## Phase 4: Render

Assemble the JSON payload for the `xpager` schema. Write it with the Write tool to `outputs/documents/{sender-slug}/xpager/_work/data.json`. The path redirect sends that write to the DATA overlay. Bash gets no redirect, so resolve the root before you render:

```bash
OUTPUTS_DIR="$(python3 -c "import sys; sys.path.insert(0,'.'); from scripts.utils.workspace import get_outputs_dir; print(get_outputs_dir())")"
python scripts/render-doctype.py --type xpager \
  --data "$OUTPUTS_DIR/documents/{sender-slug}/xpager/_work/data.json" \
  --out "$OUTPUTS_DIR/documents/{sender-slug}/xpager/"
```

Outputs: PDF + HTML. Both files are deliverables - HTML for web/preview, PDF for print and sharing.

## Phase 5: Report

Full absolute paths of the PDF and HTML, then the confirmation line from
`.claude/rules/hidden-chars.md`.

## NEVER

- Use decrypted traffic claims. ODUN.ONE classifies encrypted traffic via metadata and AI. Never "decrypts".
- Mention presence in sanctioned countries.
- Use round numbers in stats bar or pricing references.
- Use "team" - always "Tribe".
- Rebuild the PDF-extracted logotype in HTML. Use the white logo from `datastore/brand/assets/logos/` only.
- Skip the hidden-character scan.
