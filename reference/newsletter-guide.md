# 31C Intelligence Briefing - Newsletter Guide

> Reference document for the `/intel-briefing-newsletter` command. Defines content structure, design system, confidentiality rules, voice guidelines, and image generation. This is the PUBLIC-FACING newsletter. For CEO-only intelligence with business implications, see `/ceo-intel`.
> Last Updated: 2026-04-18

---

## Classification

**Semi-Public** - Safe for internal Tribe distribution and external sharing. Contains only publicly available intelligence framed through 31C's sovereign technology lens. No confidential business information.

---

## Design System (V3)

### Theme

Light cream editorial/newspaper aesthetic. Clean, authoritative, intelligence-grade.

| Property | Value |
|----------|-------|
| Background | `#F4F2EC` (cream) |
| Card/Panel | `#FDFCFA` (warm white) |
| Ink | `#0C0C0B` (near black) |
| Accent | `#D93D06` (burnt orange) |
| Green | `#175C30` (up indicators) |
| Red | `#AA2208` (danger indicators) |
| Gold | `#B8860B` (gold indicators) |

### Typography

| Typeface | Use | Weight |
|----------|-----|--------|
| **Bebas Neue** | Display headers, hero title, section numbers | 400 |
| **Crimson Pro** | Body text, paragraphs, reading content | 400, 400 italic, 600 |
| **IBM Plex Mono** | Labels, metadata, indicator values, kickers | 400, 500, 600 |

All loaded via Google Fonts. Fallback stacks included.

### Logo

`31C_Logo_Black_Color.png` (black on cream background). Located at `.claude/skills/pptx-generator/brands/31c/assets/`.

### Layout

- Max-width: 700px centered
- Generous whitespace, editorial spacing
- CSS custom properties (`:root` variables) for theming
- `<style>` block with class names (not inline styles)

### Visual Elements (CSS-only)

| Element | Section | Description |
|---------|---------|-------------|
| Radar circles | Sea State | Concentric animated circles on dark background |
| Scanlines + dot grid | Cyber Front | Animated vertical scanline with random dot grid |
| Bar chart | Market Depth | CSS-driven bar chart with gradient overlay |
| Pulse dot | Top bar | Blinking green dot indicating live feed |

These are default visualizations built into the HTML. AI-generated images are optional enhancements.

---

## Content Structure

### Pre-Content Elements

| Element | Description |
|---------|-------------|
| **Top Bar** | Black bar with animated pulse dot, "Live Intelligence Feed", region tags, classification |
| **Masthead** | Two-column: logo + tagline (left), date + issue + regions + threat level indicator (right) |
| **Hero** | Large Bebas Neue title (82px) with one accent-colored word, italic deck paragraph with left border |
| **Indicators** | 5 equal columns showing key metrics with large values and small labels, color-coded by status |

### Numbered Sections

| # | Section | Visual Banner | Purpose |
|---|---------|--------------|---------|
| 01 | **Sea State** | CSS radar | Geopolitical and security landscape - the dominant global events |
| 02 | **The Cyber Front** | CSS scanlines | Cybersecurity threats, incidents, breaches, APT activity |
| 03 | **Navigation Chart** | Grid table | Regional intelligence: GCC, CIS, Africa |
| 04 | **Market Depth** | CSS bar chart | DPI market data, telecom trends, capital markets |
| 05 | **The Heading** | None (text only) | 31C's strategic read - what this means for sovereign technology |
| 06 | **Signal Watch** | Numbered table | Forward-looking items to monitor in the coming weeks |
| 07 | **Recommended** | Numbered list | 2-3 curated external links to significant reports |

---

## Section Writing Guidelines

### Hero

- **Title**: 2-4 words, dramatic, uses line breaks for stacking. One word highlighted in accent color.
- **Deck**: 1-2 sentences summarizing the dominant narrative. Sets the tone for the entire briefing.

### Indicators

- Exactly 5 metrics capturing the current state
- Each has: value (short), label (2-3 words), style (up/danger/neutral)
- Examples: oil price, gold price, infrastructure status, incident counts, market size

### Sea State (Section 01)

- Lead with the single most impactful global event
- Focus on events that affect 31C's markets: GCC, CIS, Africa, Europe
- Include scale and scope (numbers, countries affected, economic impact)
- 2-4 paragraphs maximum
- Section metadata: `banner_title`, `banner_detail`, `caption`

### The Cyber Front (Section 02)

- Specific incidents with attribution where known (state actors, APT groups)
- Industry verticals affected (telecom, government, financial, critical infrastructure)
- Connect to network visibility / DPI relevance where natural
- Never speculate on attribution without sourcing
- Section metadata: `badge` (APT identity), `big_stat` (one standout number), `banner_title`, `banner_detail`, `caption`

### Navigation Chart (Section 03)

- Three sub-sections: GCC, CIS, Africa
- Each region has: `code` (3-letter), `name` (full name with line breaks), `body` (1-2 paragraphs)
- Focus on developments relevant to telecom, cybersecurity, defense, and data sovereignty
- Include regulatory changes, infrastructure investments, and political developments

### Market Depth (Section 04)

- DPI market size and growth data
- Telecom operator adoption trends
- Capital market movements affecting cybersecurity and sovereign tech investment
- Section metadata: `bars` (15 values for bar chart), `stats` (3 key metrics), `caption`, `pullquote`
- Optional `market_caption` for chart annotation

### The Heading (Section 05)

- 31C's interpretation of what the intelligence means for sovereign technology
- Frame through the lens of data sovereignty, non-alignment, and on-premises deployment
- This is the thought leadership section - the "so what" for readers
- Reference ODUN.ONE positioning generically (product category, not internals)
- Plain text, no visual banner

### Signal Watch (Section 06)

- 3-5 items to watch in the coming weeks
- Events, deadlines, regulatory milestones, conference dates, geopolitical triggers
- Brief - one line per item with bold keywords
- Rendered as numbered table with orange indicator dots

### Recommended Reading (Section 07)

- 2-3 links to significant external publications
- Each entry: `title`, `url`, `source`, `description`
- Prioritize reports from WEF, CISA, industry analysts, and major publications

---

## Image Generation (Optional)

### When to Use

AI-generated images enhance the newsletter when the content warrants it. The CSS-only visual banners (radar, scanlines, bar chart) serve as strong defaults. Images are most valuable for Sea State or Hero sections when the subject matter is visually compelling.

### Tool

Nano Banana 2 via Replicate API:

```bash
python ".claude/skills/flux-image/scripts/generate_image.py" \
  --prompt "[style prefix + section suffix]" \
  --output "outputs/intel/newsletters/YYYY-MM-DD/[section]-visual.png" \
  --aspect-ratio 16:9 --model banana --format png
```

### Consistent Style Prompt

All newsletter images share a style prefix for visual consistency:

```
Minimalist editorial illustration, cream and burnt orange color palette,
dark ink accents on light textured background, abstract data visualization aesthetic,
no text, no words, no letters, no typography, clean negative space,
newspaper editorial style, sophisticated and restrained
```

Section-specific suffixes:
- **Sea State**: `", concentric radar circles, geopolitical threat mapping, surveillance aesthetic"`
- **Cyber**: `", digital grid pattern, network node breach visualization, scanline interference"`
- **Markets**: `", abstract financial chart lines, market flow visualization"`

### Embedding

Generated images are base64-encoded directly into the HTML (self-contained file). The original PNG is also kept in the newsletter folder for reuse.

Pass images to the generator via CLI:

```bash
python scripts/generate-newsletter-html.py input.json --images sea_state=sea-state-visual.png
```

---

## Confidentiality Guardrails

### NEVER Include

| Category | Examples |
|----------|----------|
| Partner names | Any company 31C has a partnership with |
| Client names | Any company using or evaluating ODUN.ONE |
| Investor names | Any individual or fund investing in or evaluating 31C |
| Reseller names | Any individual or company reselling 31C products |
| Deal details | Pipeline values, deployment locations, contract terms, pricing |
| Internal metrics | Revenue figures, exact team size, hiring numbers, burn rate |
| Shareholder structure | Ownership percentages, corporate entities, holding companies |
| Internal strategy | Pricing models, go-to-market phases, patent specifics, roadmap |
| Tribe member names | No individual Tribe members named in the newsletter |
| Meeting details | No meeting notes, call summaries, or internal communications |

### Safe to Include

- Public market data and industry statistics (always sourced)
- Public news from recognized outlets
- Industry trends and analyst reports
- 31C's public positioning: "sovereign, non-aligned, AI-native"
- ODUN.ONE product category description: "sovereign deep packet intelligence platform"
- The tagline: "From Deep Packet Inspection to Deep Packet Intelligence"
- DPI+ as a category concept
- The Five Core Principles (as published)
- General statements about 31C's market presence (regions, not specific clients)

### Pre-Publication Review

Before generating the final HTML, scan the content for:
1. Any proper nouns that match entries in `context/people.md`
2. Any company names that match entries in `context/pipeline.md`
3. Any financial figures that could be traced to specific deals
4. Any deployment details that identify specific customers

---

## Voice & Tone

Follow `reference/misha-voice.md` with these newsletter-specific adjustments:

- **Register:** Informed authority - the captain sharing intelligence, not lecturing
- **Perspective:** First-person plural ("we") for The Heading section only; third-person objective for all intelligence sections
- **Length:** Each section should be concise - 2-4 paragraphs max. Total newsletter: 800-1200 words
- **Formatting:** Hyphens only (never em-dashes). ODUN.ONE always styled correctly. DPI+ with the plus.
- **Maritime vocabulary:** Use naturally in The Heading and Signal Watch. Don't force it into factual intelligence sections.
- **Never say:** "I'm excited," "thrilled," "in today's rapidly evolving landscape," or any phrase from the voice guide's "never" list

---

## Research Sources

When generating newsletter content, pull from:

1. **WebSearch** - Parallel queries across geopolitics, cybersecurity, markets, and regions. Apply domain filtering per stream using `reference/search-domains.md` (see Stream-to-Topic Mapping table). Always apply the Blocked Domains list as `blocked_domains`.
2. **Perplexity API** (`scripts/perplexity-research.py`) - Deep synthesized research with citations. Use `--domains` flag with relevant domains for the focus area.
3. **World Monitor** (worldmonitor.app, tech.worldmonitor.app, finance.worldmonitor.app) - Aggregated intelligence (specific URLs, no filtering needed)
4. **Reference files** - `reference/dpi-market-intelligence.md`, `reference/geopolitical-landscape.md`

Always cross-reference claims. If a statistic appears in only one source, flag it or find corroboration.

---

## Output

- **HTML:** Self-contained file with `<style>` block and base64-embedded assets at `outputs/intel/newsletters/YYYY-MM-DD/intelligence-briefing.html`
- **PDF:** Single continuous page (no page breaks) at `outputs/intel/newsletters/YYYY-MM-DD/intelligence-briefing.pdf` - generated automatically alongside HTML
- **Input JSON:** Archived alongside at `outputs/intel/newsletters/YYYY-MM-DD/input.json`
- **Images:** Stored in newsletter folder as PNGs, embedded as base64 in HTML
- **Generator:** `scripts/generate-newsletter-html.py` (use `--no-pdf` to skip PDF)
- **Validation:** Run `scripts/sanitize-text.py` on output to check for hidden characters
