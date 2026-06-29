<!-- version: 1.0.0 | last-updated: 2026-04-28 -->

# 31C TypeFace Usage Guide

Full typographic rules for the 31C custom display typeface and the GT Standard workhorse font, derived from the ODUN.ONE Product Presentation (05-Apr-2026, 36 slides).

Last Updated: 2026-04-05
Last Verified: 2026-04-28 (asset paths and font inventory cross-checked against `datastore/brand/fonts/`)

---

## Font Inventory

### GT Standard (Primary Text Font)

Location: `datastore/brand/fonts/GT Standard/`

| Variant | Role |
|---|---|
| **GT Standard M Light** | Body text, descriptions, supporting copy, captions, sources |
| **GT Standard M Medium** | Headings, labels, category names, emphasized text, data values |
| **GT Standard L Light** | Hero/display text (slide 1 tagline), pull quotes, attributions |

GT Standard handles **all readable text** in presentations - titles, body, labels, captions, everything.

### 31C TypeFace (Display/Accent Font)

Location: `datastore/brand/fonts/31C_TypeFace/`

Two families:
- **31C-Horizontal** (T03 series) - horizontal letterform orientation
- **31C-Vertical** - vertical letterform orientation

Each family ships 12 weights: 50, 100, 180, 260, 350, 450, 560, 680, 800, 920, Clarity, Noise.

Formats: Desktop (OTF), Web (WOFF2), Variable Font (VF TTF).

**Only the Clarity weight is used in the current product presentation.** The font name in PPTX is `31C Horizontal T03 Clarity`.

---

## 31C TypeFace Usage Rules

### What It Is Used For

The 31C TypeFace is a **display/accent font only**. It is never used for readable body text, headings, or descriptions. Its purpose is visual branding - adding the 31C pixel-art DNA to specific decorative elements.

### Approved Uses

| Use Case | Font | Size | Color | Example |
|---|---|---|---|---|
| **Numbered list markers** (large) | 31C Horizontal T03 Clarity | 54pt | Inherited (dark) | "1", "2", "3", "4" in square containers |
| **Numbered list markers** (medium) | 31C Horizontal T03 Clarity | 28pt | #423BFF (Palatinate Blue) | "01", "02", "03" inline with list items |
| **Cost/impact counters** | 31C Horizontal T03 Clarity | 60pt | #FF9235 (Dark Orange) | "01", "02", "03" for cost callout items |
| **Step indicators** (small) | 31C Horizontal T03 Clarity | 28pt | Inherited | "1", "2", "3", "4" in process flow diagrams |
| **Closing slide display text** | 31C Horizontal T03 Clarity | 173pt | Inherited | "Thank you!" on the final slide |

### Key Patterns

1. **Numbers and single-digit sequences only.** The 31C TypeFace is used exclusively for numerals (1, 2, 3..., 01, 02, 03...) and very short display phrases ("Thank you!"). Never for sentences, labels, or descriptions.

2. **Always paired with GT Standard.** Every 31C TypeFace element appears alongside GT Standard text that provides the actual readable content. The TypeFace provides the visual anchor; GT Standard provides the meaning.

3. **Container pattern.** Numbered markers are typically placed inside square or rectangular containers (shapes), positioned to the left of the content they enumerate.

4. **Color follows context:**
   - Blue (#423BFF) for standard enumeration and structural numbering
   - Orange (#FF9235) for cost/impact/warning items
   - Inherited/dark for neutral numbered lists

5. **Size hierarchy:**
   - 173pt - hero/closing display only (one per presentation)
   - 54-60pt - primary numbered markers (section-level items)
   - 28pt - secondary numbered markers (sub-items, process steps)

### Forbidden Uses

- **Never for headings or titles.** All headings use GT Standard M Medium.
- **Never for body text or descriptions.** All body text uses GT Standard M Light.
- **Never for labels or captions.** These use GT Standard M Medium or M Light.
- **Never for long text.** Maximum practical length is a short phrase like "Thank you!"
- **Never for alphabetical characters in running text.** The typeface is optimized for numerals and short display words, not paragraph readability.

---

## GT Standard Typography Hierarchy (Product Presentation)

### Slide Titles
- **Font:** GT Standard M Medium, bold
- **Size:** 72-80pt (primary titles), 64-74.7pt (section titles)
- **Color:** #2A2A2A (dark), #423BFF (accent), #F3F3F3 (on dark backgrounds)

### Subtitles / Taglines
- **Font:** GT Standard M Light
- **Size:** 29.3-32pt
- **Color:** #5A5A5A (gray)

### Section Headers
- **Font:** GT Standard M Medium, bold
- **Size:** 34.7-42.7pt
- **Color:** #423BFF (blue), #FF9235 (orange), #2A2A2A (dark)

### Content Labels / Category Names
- **Font:** GT Standard M Medium, bold
- **Size:** 24-29.3pt
- **Color:** Context-dependent (blue for features, orange for actions, dark for neutral)

### Body Text / Descriptions
- **Font:** GT Standard M Light
- **Size:** 20-28pt
- **Color:** #5A5A5A (primary body), #888888 (secondary/notes)

### Captions / Sources
- **Font:** GT Standard M Light
- **Size:** 11-18.7pt
- **Color:** #888888 or #8A92AB

### Hero/Display Text
- **Font:** GT Standard L Light
- **Size:** 80pt (slide 1: "Next Gen DPI+")
- **Color:** Inherited

### Pull Quotes
- **Font:** GT Standard L Light, italic
- **Size:** 24pt
- **Color:** #747DBE (Glaucous)

### Large Data Values
- **Font:** GT Standard M Medium, bold
- **Size:** 90.7pt
- **Color:** #423BFF

---

## Color Palette (as used in presentation)

These are the **original brand colors** from designer Max Arden, used directly in the PPTX (not the adjusted digital palette):

| Color | Hex | Usage |
|---|---|---|
| Palatinate Blue | #423BFF | Primary accent - headings, links, feature highlights, data values |
| Dark Orange | #FF9235 | Secondary accent - warnings, costs, legacy/problem items, action items |
| Glaucous | #747DBE | Tertiary - partner mentions, quotes, protocol labels, decorative arrows |
| Charcoal | #2A2A2A | Primary dark text on light backgrounds |
| Gray | #5A5A5A | Body text, descriptions |
| Silver Gray | #888888 | Captions, sources, secondary descriptions |
| Cream | #E0E7C0 | Accent text on dark backgrounds (status indicators) |
| White | #FFFFFF | Text on dark/colored backgrounds |
| Near-Black | #0F1524 | Tech stack labels |

---

## Implementation Notes for PPTX Generator

When producing 31C-branded presentations:

1. **Use GT Standard M Medium (bold) for all headings.** Sizes 72-80pt for slide titles, 34-42pt for section headers, 24-29pt for content labels.
2. **Use GT Standard M Light for all body text.** Sizes 20-28pt, gray tones.
3. **Use 31C Horizontal T03 Clarity for numbered markers only.** Place inside rectangular containers. Size 54pt for primary, 28pt for secondary.
4. **Use GT Standard L Light sparingly** - only for slide 1 hero tagline and pull quotes.
5. **Slide dimensions:** 26.67" x 15" (widescreen 16:9 at high resolution).
6. **Font embedding:** Embed GT Standard and 31C TypeFace in PPTX output to ensure portability.

## Implementation Notes for HTML/CSS (Design Studio)

When producing branded HTML outputs:

1. **Load 31C TypeFace via `@font-face`** from `datastore/brand/fonts/31C_TypeFace/31C-Horizontal/Web/31CHorizontalT03-Clarity.woff2` for the Clarity weight.
2. **Use as a CSS variable:** `--font-display: '31C Horizontal T03 Clarity', sans-serif;`
3. **Apply only to:** numbered list markers (`.step-number`, `.list-marker`), counter elements, and hero display text.
4. **Never apply to:** headings, body text, labels, or any readable prose.
5. **The Vertical family** (`31C-Vertical`) is available but not yet used in production materials. Reserve for future creative applications.
