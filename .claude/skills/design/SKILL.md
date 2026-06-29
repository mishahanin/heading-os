---
name: design
description: Professional design system - illustrations, UI mockups, social graphics, brochures, logos, infographics, and image editing. Routes to optimal pipeline (HTML Studio for layouts, Replicate AI for creative imagery, PPTX for presentations). Use when user asks to design, create visuals, generate graphics, make a mockup, create a brochure, design a logo, or produce any visual asset. Do NOT use for simple one-off image generation (use /flux-image instead).
argument-hint: "[type] [brief]"
allowed-tools: "Read, Write, Edit, Bash(python3:*)"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - design social
    - design infographic
    - design mockup
    - design illustration
    - design logo
x-31c-capability:
  what: >
    Professional design studio for social graphics, infographics, UI mockups, cards, brochures,
    logos, and AI imagery. Routes each task to the optimal pipeline - HTML Studio (free, layout/text),
    Replicate AI (creative imagery, vectors), or PPTX (presentations).
  how: >
    Run /design <type> <brief> (e.g. /design infographic, /design logo, /design photo, /design edit).
    HTML renders land in outputs/design/; AI-generated images in outputs/content/images/.
  when: >
    Use for any branded visual asset. For a simple one-off image from a prompt use /flux-image;
    for presentation slides use /pptx-generator.
---
# Design System

Professional design studio with three rendering pipelines: HTML Studio (free, deterministic), Replicate AI (17 models), and Document Production (PPTX, PDF). Routes each task to the optimal pipeline automatically.

## Commands

| Command | Pipeline | Description |
|---|---|---|
| `/design social [brief]` | HTML Studio | Social media graphic (LinkedIn, Twitter) |
| `/design infographic [brief]` | HTML Studio | Data visualization, infographic |
| `/design mockup [brief]` | HTML Studio | UI/UX mockup, app screen, dashboard |
| `/design card [brief]` | HTML Studio | Branded card, quote card, stat card |
| `/design illustration [brief]` | Replicate AI | Creative illustration, conceptual art |
| `/design photo [brief]` | Replicate AI | Photorealistic image, product shot |
| `/design logo [brief]` | Replicate AI (SVG) | Logo, icon, native vector graphic |
| `/design text-graphic [brief]` | Replicate AI | Graphic with embedded text (poster, banner) |
| `/design brochure [brief]` | HTML + Replicate | Layout from HTML, illustrations from AI |
| `/design edit [instruction]` | Replicate AI | Edit existing image via natural language |
| `/design upscale [path]` | Replicate AI | Upscale image for print quality |
| `/design remove-bg [path]` | Replicate AI | Remove background from image |
| `/design draft [brief]` | Replicate AI | Fast concept at $0.003 via FLUX Schnell |

## Routing Logic

### Pipeline 1: HTML Studio (free, $0)

**When:** Task is layout-driven, text-driven, or data-driven.

Routes: `social`, `infographic`, `mockup`, `card`, brochure layouts.

**Workflow:**
1. Generate a complete, self-contained HTML file with inline CSS
2. Read `references/brand.css` for the 31C design system variables
3. Apply brand design: dark backgrounds (#000000), Inter font, accent colors (#5B5FFF blue-purple, #FF8C00 orange)
4. Save HTML to `outputs/design/source/` for version control
5. Run the renderer:
```bash
python3 scripts/design-studio.py render \
  --file outputs/design/source/{name}.html \
  --width {W} --height {H} \
  --brand 31c \
  -o outputs/design/{name}.png
```
6. Read the generated image to show the user
7. If user wants changes, edit the HTML and re-render

**HTML generation rules:**
- Self-contained: all CSS inline, no external dependencies except Google Fonts
- Use CSS custom properties from brand.css: `var(--accent)`, `var(--bg)`, `var(--text)`, etc.
- Use utility classes from brand.css: `.card`, `.container`, `.grid-2`, `.accent`, `.display`
- Dark backgrounds ONLY - never white or light
- Maximum 6 lines of text per visual
- 40% empty space (breathing room)
- One main message per visual

### Pipeline 2: Replicate AI (API-driven)

**When:** Task needs creative imagery, photography, vectors, or text rendered into images.

Routes: `illustration`, `photo`, `logo`, `text-graphic`, `draft`, `edit`, `upscale`, `remove-bg`.

**Model selection:**

| Task | Model | Command |
|---|---|---|
| Illustration, brand art | `recraft-v4` | `generate --model recraft-v4` |
| Photorealistic image | `flux-2-pro` | `generate --model flux-2-pro` |
| Logo / icon (vector SVG) | `recraft-v4-svg` | `generate --model recraft-v4-svg` |
| Graphic with text | `ideogram-v3` | `generate --model ideogram-v3` |
| Fast concept draft | `flux-schnell` | `generate --model flux-schnell` |
| Edit existing image | `kontext` | `edit --model kontext --image {path}` |
| Upscale for print | `crisp-upscale` | `upscale --image {path}` |
| Remove background | `eraser` | `remove-bg --image {path}` |

**Generation command:**
```bash
python3 scripts/design-engine.py generate \
  --model {model} \
  --prompt "{prompt}" \
  --width {W} --height {H} \
  -o outputs/content/images/{name}.png
```

**Edit/upscale/remove-bg:**
```bash
python3 scripts/design-engine.py edit --model kontext --image {path} --prompt "{instruction}" -o {output}
python3 scripts/design-engine.py upscale --image {path} -o {output}
python3 scripts/design-engine.py remove-bg --image {path} -o {output}
```

**Brand-aware prompting for AI generation:**
When generating images for 31C, append to prompt: "Dark background, professional, sovereign, bold. Color palette: deep blues, blue-purple (#5B5FFF), orange (#FF8C00) accents. Clean, modern, authoritative."

### Pipeline 3: Document Production

**When:** Task is a presentation deck.

Redirect: "For presentation slides, use `/pptx-generator` directly - it has the full 16-layout system with 31C branding."

For PDF from HTML, use the design studio:
```bash
python3 scripts/design-studio.py pdf --file {html_path} --brand 31c -o {output}.pdf
```

## Standard Dimensions

| Format | Width | Height | Use Case |
|---|---|---|---|
| LinkedIn post | 1200 | 628 | Standard LinkedIn image |
| LinkedIn square | 1080 | 1080 | Carousel slide |
| Instagram | 1080 | 1080 | Square social |
| Instagram story | 1080 | 1920 | Vertical story |
| Twitter/X | 1200 | 675 | Twitter image |
| Presentation | 1920 | 1080 | 16:9 slide |
| A4 portrait | 2480 | 3508 | Print (300 DPI) |
| A4 landscape | 3508 | 2480 | Print landscape |

## Output Locations

- HTML Studio renders: `outputs/design/{name}.png` + source `outputs/design/source/{name}.html`
- AI-generated images: `outputs/content/images/{name}.png` (consistent with /flux-image)
- Project work (brochures, campaigns): `outputs/design/{project-name}/`
- Multi-format exports: `outputs/design/{name}-{W}x{H}.png`

## Model Reference

Full model registry with capabilities, pricing, and prompt tips: [references/models.md](references/models.md)

## Brand CSS Reference

Injectable 31C design system (Inter font, dark theme, accent colors, utility classes): [references/brand.css](references/brand.css)

Source of truth for colors: `.claude/skills/pptx-generator/brands/31c/brand.json`

## Voice and Style

- Hyphens only (never em-dashes)
- ODUN.ONE (never ODUN or Odun)
- DPI+ (never DPI Plus)
- Dark backgrounds always (31C is dark-mode only)
- Signal over noise - every element earns its place
- Sovereign and bold - communicate independence and authority

## Sensitive sessions (SENSITIVE_MODE)

Before sending any prompt or query to an external API (Replicate imagery, web
search/fetch for references), check session sensitivity:

```bash
python3 -c "from scripts.utils.sensitive import is_sensitive, sanitize_prompt_guidance; print(sanitize_prompt_guidance()) if is_sensitive() else None"
```

If it prints guidance (sensitivity not explicitly cleared — the fail-closed
default), strip ALL project-identifying detail from the prompt/query before the
call: no codenames, company names, people names, deal terms, or strategic
specifics. Use abstract, generic descriptions. The asset stays local; only the
query goes external. This replaces the removed `secure-design` vault wrapper.

## NEVER

- Never use MCP servers for design work
- Never call external APIs besides Replicate
- Never hardcode API tokens in any file
- Never use light or white backgrounds (31C brand is dark-mode only)
- Never skip brand CSS injection for HTML Studio designs
- Never generate final images without confirming the brief first
- Never output more than needed - start with one variant, iterate
