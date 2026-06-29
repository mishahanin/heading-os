---
name: pptx-generator
description: |
  Generate and edit presentation slides as PPTX files. Also create LinkedIn carousels and manage reusable slide layouts.

  TRIGGERS - Use this skill when user says:
  - "create slides for [brand]" / "generate presentation for [brand]" / "make slides for [brand]"
  - "create a carousel for [brand]" / "linkedin carousel" / "make a carousel about [topic]"
  - "edit this pptx" / "update the slides" / "modify this presentation"
  - "create a new layout" / "add a layout to the cookbook" / "make a [type] layout template"
  - "edit the [name] layout" / "update the cookbook" / "improve the [name] template"
  - Any request mentioning slides, presentations, carousels, PPTX, or layouts with a brand name

  Creates .pptx files compatible with PowerPoint, Google Slides, and Keynote.
  Creates PDF carousels for LinkedIn (square 1:1 format).
argument-hint: "[brand] [topic]"
allowed-tools: "Bash(python3:*), Read"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - create slides
    - generate presentation
    - linkedin carousel
    - edit pptx
x-31c-capability:
  what: >
    Generates and edits on-brand presentation slides as PPTX files (PowerPoint/Slides/Keynote compatible),
    builds square LinkedIn carousels exported to PDF, and manages the reusable cookbook layouts.
  how: >
    Run /pptx-generator <brand> <topic>. Reads the brand config and cookbook layouts, presents a slide
    plan, then generates in batches of 5 max and combines into one file in the brand's output directory.
  when: >
    Use for editable branded decks, carousels, or layout work. For non-slide visuals use /design; for a
    quick markdown-driven internal deck use /marp.
---
# PPTX Slide Generator

Generate professional, on-brand presentation slides using python-pptx. This skill supports:
- **Slide Generation** - Create presentations for any brand in `brands/`
- **Carousel Generation** - Create LinkedIn carousels (square format, exports to PDF)
- **Slide Editing** - Modify existing PPTX files
- **Layout Management** - Create, edit, update cookbook layouts

**IMPORTANT:** All skill resources are in `.claude/skills/pptx-generator/`. Always use Glob patterns starting with `.claude/skills/pptx-generator/` to find files.

---

## CRITICAL: Batch Generation Rules

**NEVER generate more than 5 slides at once.**

| Rule | Details |
|------|---------|
| Max slides per batch | **5** (can be 1, 2, 3, 4, or 5) |
| After each batch | **STOP and validate output** |
| Validation required | Check: no duplicate titles, proper spacing, correct colors |
| Continue when | Validation passes |
| **After ALL batches** | **COMBINE into single file and DELETE part files** |

**CRITICAL: Always clean up part files after combining.** The user should only see ONE final PPTX file.

---

## PREREQUISITE: Brand Check

**Before generating slides, check if any brands exist.**

```
Glob: .claude/skills/pptx-generator/brands/*/brand.json
```

**If NO brands found (only `template/` exists):** STOP and ask the user to create a brand first. See [references/brand-setup.md](references/brand-setup.md) for the full brand creation workflow.

---

## Skill Modes

### Mode 1: Generate Presentation Slides
User wants presentation slides (16:9) created using a brand's styling.
Follow the workflow below. Layouts in: `cookbook/*.py`

### Mode 2: Generate LinkedIn Carousels
User wants a LinkedIn carousel (square 1:1 format) for social media.
See [references/carousel-workflow.md](references/carousel-workflow.md) for the full carousel workflow.

### Mode 3: Manage Cookbook Layouts
User wants to create, edit, or improve layout templates.
See [references/layout-crud.md](references/layout-crud.md) for layout CRUD operations.

---

## Mode 1: Generate Presentation Slides

### Step 1: Brand Discovery

1. **List available brands:**
   ```
   Glob: .claude/skills/pptx-generator/brands/*/brand.json
   ```

2. **Read the brand configuration files:**
   ```
   Read: .claude/skills/pptx-generator/brands/{brand-name}/brand.json
   Read: .claude/skills/pptx-generator/brands/{brand-name}/config.json
   ```

3. **Read supporting markdown files for context:**
   ```
   Glob: .claude/skills/pptx-generator/brands/{brand-name}/*.md
   ```

4. **Extract from brand files:**
   - **From brand.json:** Colors (hex without #), fonts, asset paths
   - **From config.json:** Output directory, slides per batch, naming convention
   - **From markdown:** Voice, tone, vocabulary, visual principles

If brand not found, list available brands and ask user to choose.

### Step 2: Layout Discovery & Selection

**MANDATORY: Read ALL layout frontmatters before selecting any layout.**

```
Glob: .claude/skills/pptx-generator/cookbook/*.py
```

For each `.py` file, read the first 40 lines to extract the `# /// layout` frontmatter block. Build a map of each layout's `purpose`, `best_for`, `avoid_when`, and `max_*` limits.

**Read `references/layout-guide.md`** for the complete layout selection guide including:
- Visual-First selection decision tree (CRITICAL)
- Variety enforcement rules (content-slide <25%, visual layouts 50%+)
- Content type to best layout mapping table

**Key rule: Default to VISUAL layouts. Content-slide is the LAST RESORT, not the default.**

### Step 3: Slide Planning (ALWAYS DO THIS)

**Before generating ANY slides, create a written plan.**

**Create a slide plan table:**

```markdown
| # | Layout | Title | Key Content | Notes |
|---|--------|-------|-------------|-------|
| 1 | title-slide | [Title] | [Subtitle, author] | Opening slide |
| 2 | content-slide | [Title] | [3-4 bullet points] | Main concepts |
| 3 | stats-slide | [Title] | [2-3 metrics] | Impact data |
```

**Planning checklist:**
- [ ] No duplicate titles across slides
- [ ] Logical flow from slide to slide
- [ ] Appropriate layout for each content type
- [ ] Content fits the chosen layout
- [ ] Batches are logically grouped (5 slides max each)
- [ ] **VARIETY CHECK: Content-slide used <25% of total slides**
- [ ] **VARIETY CHECK: No more than 2-3 consecutive slides with same layout**
- [ ] **VARIETY CHECK: Visual layouts (cards, stats, columns, hero) are 50%+ of slides**

**After planning, briefly present the plan before generating.**

### Step 4: Content Adaptation

#### Presentation Text Formatting Rules

| Element | Rule | Example |
|---------|------|---------|
| Titles | No trailing periods or commas | "Why AI Matters" not "Why AI Matters." |
| Subtitles | No trailing punctuation | "The future of coding" |
| Bullet points | No trailing periods (unless full sentences) | "Faster development" |
| Headlines | Minimal punctuation, no ellipsis | "What's Next" |
| Stats/Numbers | Clean format, no trailing punctuation | "50%" |
| CTAs | No trailing punctuation | "Get Started" |

**Exception:** Full sentence descriptions or quotes may use appropriate punctuation.

#### Brand Value Mapping

Map brand.json values to layout placeholders:

| Layout Placeholder | brand.json Path |
|--------------------|-----------------|
| `BRAND_BG` | `colors.background` |
| `BRAND_BG_ALT` | `colors.background_alt` |
| `BRAND_TEXT` | `colors.text` |
| `BRAND_TEXT_SECONDARY` | `colors.text_secondary` |
| `BRAND_ACCENT` | `colors.accent` |
| `BRAND_ACCENT_SECONDARY` | `colors.accent_secondary` |
| `BRAND_ACCENT_TERTIARY` | `colors.accent_tertiary` |
| `BRAND_CODE_BG` | `colors.code_bg` |
| `BRAND_CARD_BG` | `colors.card_bg` |
| `BRAND_CARD_BG_ALT` | `colors.card_bg_alt` |
| `BRAND_HEADING_FONT` | `fonts.heading` |
| `BRAND_BODY_FONT` | `fonts.body` |
| `BRAND_CODE_FONT` | `fonts.code` |

**Note:** All color values in brand.json are hex WITHOUT the `#` prefix.

### Step 5: Generate, Validate & Combine

**Read `references/generation-workflow.md`** for the complete batch generation, validation, and combining workflow.

**Key rules (always apply):**
- **Output lands in the DATA overlay, never the engine** -- resolve `$DECK_DIR` (`get_outputs_dir()/content/decks/{brand}`) before any heredoc and save there (see `references/generation-workflow.md` § Execution Methods). A bare `output/{brand}` resolves into the engine tree.
- **Max 5 slides per batch** -- stop and validate after each
- **EVERY slide MUST have background explicitly set** -- `slide.background.fill.solid()` + `slide.background.fill.fore_color.rgb = hex_to_rgb(BRAND_BG)` -- this prevents white slides on dark brands
- **Combining batches also requires background setting** -- `add_slide()` defaults to white
- **Use heredoc or `.tmp/` directory** -- never create Python files in the repo root
- **Clean up part files** -- user should only see ONE final PPTX
- **Validate after every batch** -- check for white backgrounds, duplicate titles, spacing, overflow, wrong colors

---

## Reference Files

| File | When to Read |
|------|-------------|
| [references/layout-guide.md](references/layout-guide.md) | Layout selection, variety rules, decision tree |
| [references/generation-workflow.md](references/generation-workflow.md) | Batch generation, validation, combining |
| [references/brand-setup.md](references/brand-setup.md) | Creating a new brand configuration |
| [references/carousel-workflow.md](references/carousel-workflow.md) | LinkedIn carousel generation (Mode 2) |
| [references/layout-crud.md](references/layout-crud.md) | Creating/editing/deleting layouts (Mode 3) |
| [references/technical-reference.md](references/technical-reference.md) | python-pptx imports, chart types, dimensions, editing PPTX |

---

## Quick Checklist

**Slide Generation:**
- [ ] Read brand.json, config.json, brand markdown files
- [ ] Read ALL cookbook layout frontmatters (`references/layout-guide.md`)
- [ ] Create slide plan table -- check variety rules
- [ ] Present plan, then generate (max 5 per batch)
- [ ] Set background on EVERY slide -- validate after each batch
- [ ] Combine batches, clean up part files (`references/generation-workflow.md`)

**Layout Creation:**
- [ ] Study existing layouts, design production-ready quality
- [ ] Add detailed TOML frontmatter (`name`, `best_for`, `avoid_when`, `instructions`, limits)
- [ ] Test the new layout
