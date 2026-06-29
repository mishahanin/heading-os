---
name: intel-briefing-newsletter
description: Produce an external-facing intelligence briefing newsletter - curated, publishable intel framed for an audience beyond the CEO. Use when the output is meant to be shared/published, not a private desk brief. Trigger when the user says "newsletter", "intel briefing", "publish intelligence brief", or "external intel brief". Do NOT use for an internal-only situational brief (use /ceo-intel).
argument-hint: "[focus]"
context: fork
allowed-tools: "WebSearch, WebFetch, Read, Bash(python3:*)"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.2"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - newsletter
    - intel briefing
    - publish intelligence brief
    - external intel brief
x-31c-capability:
  what: >
    A branded, public-facing 31C Intelligence Briefing newsletter with live world-intel research - hero, indicators, Sea State, Cyber Front, regional Navigation Chart, Market Depth - carrying zero confidential information, safe for Tribe and external sharing.
  how: >
    Run /intel-briefing-newsletter [focus]. Forked-context research, then a confidentiality scrub, then HTML + PDF generated into outputs/intel/newsletters/YYYY-MM-DD/.
  when: >
    Use to produce the external newsletter. For the CEO-only version that names partners and deals and adds action items use /ceo-intel.
---
# Intel Briefing Newsletter

Generate a branded 31C Intelligence Briefing newsletter with live world intelligence research. This is the PUBLIC-FACING newsletter - safe for Tribe distribution, LinkedIn, prospects, and external sharing. Contains zero confidential information. For CEO-only intelligence with business implications and action items, use `/ceo-intel` instead.

## Variables

focus: [Optional - specific topic or region to emphasize, e.g., "GCC cybersecurity", "Africa telecom", "global markets"]
issue_number: [Optional - issue number, defaults to auto-detect from existing folders in outputs/intel/newsletters/]

---

## Instructions

**Classification:** Semi-Public. Safe for Tribe distribution and external sharing. NEVER include confidential information.

Before drafting, read:
- `reference/newsletter-guide.md` - Content structure, design system, confidentiality rules, image guidelines
- `reference/misha-voice.md` - Voice and tone (especially Tribe Communications section)
- `reference/dpi-market-intelligence.md` - DPI market data for Market Depth section
- `context/people.md` - To know which names must NEVER appear in the newsletter
- `context/pipeline.md` - To know which deals/clients must NEVER appear in the newsletter

---

## Phase 1: Research (Parallel)

Before searching, read `reference/search-domains.md` for domain filtering configuration and the Stream-to-Topic Mapping table.

Run these searches in parallel to gather live intelligence. For each stream, use `allowed_domains` from the matching topic group(s) and always apply the Blocked Domains list as `blocked_domains`:

1. **Geopolitics & Security** - WebSearch for major world events, conflicts, policy changes
   - `allowed_domains`: Geopolitics & Defense group
   - `blocked_domains`: Blocked Domains list
2. **Cybersecurity** - WebSearch for cyber incidents, threat actors, vulnerabilities, breaches
   - `allowed_domains`: Cybersecurity + General Tech groups
   - `blocked_domains`: Blocked Domains list
3. **[Priority Region 1]** - WebSearch for defense, cybersecurity, telecom developments
   - `allowed_domains`: [Priority Region 1] + Geopolitics & Defense groups
   - `blocked_domains`: Blocked Domains list
4. **[Priority Region 2]** - WebSearch for the priority region and regional developments
   - `allowed_domains`: [Priority Region 2] + Geopolitics & Defense groups
   - `blocked_domains`: Blocked Domains list
5. **[Priority Region 3]** - WebSearch for telecom, cybersecurity, infrastructure investment
   - `allowed_domains`: [Priority Region 3] + Telecom & DPI groups
   - `blocked_domains`: Blocked Domains list
6. **Markets & DPI** - WebSearch for capital markets, DPI industry, telecom trends
   - `allowed_domains`: Markets & Finance + Telecom & DPI groups
   - `blocked_domains`: Blocked Domains list
7. **Perplexity Deep Research** - Run with domain filtering:
   ```bash
   python scripts/perplexity-research.py --domains "reuters.com,bbc.com,ft.com,bleepingcomputer.com,lightreading.com,darkreading.com,therecord.media" "current world events [focus area] cybersecurity telecom defense markets [current month] [current year]"
   ```

Also fetch World Monitor for aggregated intelligence:
- https://worldmonitor.app/
- https://tech.worldmonitor.app/
- https://finance.worldmonitor.app/

After WebSearch returns article URLs, batch-scrape top articles via `python scripts/firecrawl.py batch` for full content instead of relying on search snippets.

---

## Phase 2: Synthesize Content

Write each section following `reference/newsletter-guide.md`:

1. **Hero** - Dramatic 2-4 word title (with line breaks for stacking, one accent word), plus a 1-2 sentence deck summarizing the dominant narrative.
2. **Indicators** - Select 5 key metrics with value, label, and style (up/danger/neutral). Examples: commodity prices, infrastructure status, incident counts, market sizes.
3. **Sea State** - The dominant global events. Lead with what matters most. 2-4 paragraphs. Include `banner_title`, `banner_detail`, and `caption` for the radar banner.
4. **The Cyber Front** - Specific incidents, threat actors, attack patterns. 2-3 paragraphs. Include `badge` (APT identity), `big_stat` (one standout statistic), `banner_title`, `banner_detail`, and `caption`.
5. **Navigation Chart** - Regional intel: GCC, CIS, Africa. Each region gets `code`, `name` (with line breaks), and `body` (1-2 paragraphs).
6. **Market Depth** - DPI market, telecom adoption, investment trends. 2-3 paragraphs. Include `bars` (15 values 0-100 for bar chart), `stats` (3 key metrics), `caption`, and optional `pullquote`.
7. **The Heading** - 31C's strategic read. What this means for sovereign technology. Frame through data sovereignty lens. 2-3 paragraphs. Plain markdown text.
8. **Signal Watch** - 3-5 forward-looking items with **bold** keywords.
9. **Recommended Reading** - 2-3 curated links with title, url, source, and description.
10. **Threat Level** - Set to one of: ELEVATED, HIGH, CRITICAL, GUARDED, LOW.

---

## Phase 2.5: Structural pass (optional)

For a long deliverable, you may run the `/editorial-review` structural checklist over the assembled draft before finalizing, to verify the argument arc, claim-to-evidence linkage, and section hierarchy. Reference: `reference/editorial-review.md`. The prose-level voice pass (`humanization.md`) runs as usual after. Skip when the draft is short or already tight.

---

## Phase 3: Confidentiality Review

**CRITICAL.** Before generating HTML, scan ALL content for:

- Partner names (check `context/people.md` Partner section)
- Client names (check `context/pipeline.md` Active Deals and Won/Closed)
- Investor names (check `context/people.md` Investors section)
- Reseller names (check `context/people.md` Resellers section)
- Deal values, deployment locations, contract terms
- Internal metrics, team size, revenue figures
- Shareholder structure, corporate entities

If ANY confidential information is found, remove it and replace with generic language.

---

## Phase 4: Generate HTML

**Resolve the DATA-overlay output dir first.** The newsletter folder, its `input.json`, the generated
images, and the HTML/PDF are all DATA artifacts -- they must land in the data overlay, never the
engine tree. A bare `outputs/...` passed to a Bash script (or `mkdir`) resolves against the engine
git root. Resolve once from the workspace root and reuse `$NEWS_DIR` for every path below:

```bash
cd "$(git rev-parse --show-toplevel)"
OUTPUTS_DIR="$(python3 -c "import sys; sys.path.insert(0,'.'); from scripts.utils.workspace import get_outputs_dir; print(get_outputs_dir())")"
NEWS_DIR="$OUTPUTS_DIR/intel/newsletters/YYYY-MM-DD"   # substitute today's date
```

1. **Determine issue number:** Count existing folders in `$OUTPUTS_DIR/intel/newsletters/` and add 1, or use the provided issue_number variable.

2. **Create newsletter folder** at `$NEWS_DIR` (`mkdir -p "$NEWS_DIR"`).

3. **Generate images (optional).** If the content warrants a visual (especially for Sea State or Hero), generate via Nano Banana 2:

```bash
python ".claude/skills/flux-image/scripts/generate_image.py" \
  --prompt "Minimalist editorial illustration, cream and burnt orange color palette, dark ink accents on light textured background, abstract data visualization aesthetic, no text, no words, no letters, no typography, clean negative space, newspaper editorial style, sophisticated and restrained, [section-specific suffix]" \
  --output "$NEWS_DIR/[section]-visual.png" \
  --aspect-ratio 16:9 --model banana --format png
```

Section-specific suffixes:
- Sea State: `concentric radar circles, geopolitical threat mapping, surveillance aesthetic`
- Cyber: `digital grid pattern, network node breach visualization, scanline interference`
- Markets: `abstract financial chart lines, market flow visualization`

4. **Create JSON input file** at `$NEWS_DIR/input.json` with this structure:

```json
{
  "date": "YYYY-MM-DD",
  "issue_number": N,
  "threat_level": "ELEVATED",
  "hero": {
    "kicker": "Intelligence Briefing - [Month] [Year]",
    "title": "Title\nWith\nBreaks",
    "accent_word": "OneWord",
    "deck": "1-2 sentence summary of dominant narrative."
  },
  "indicators": [
    {"value": "~$100", "label": "Oil / Barrel", "style": "up"},
    {"value": "$5,100", "label": "Gold / Troy oz", "style": "up"},
    {"value": "CLOSED", "label": "Hormuz Strait", "style": "danger"},
    {"value": "150+", "label": "Cyber Incidents", "style": "danger"},
    {"value": "$41B", "label": "DPI Market 2026", "style": "neutral"}
  ],
  "sea_state": {
    "body": "Markdown content with **bold** supported...",
    "banner_title": "Banner Title",
    "banner_detail": "Detail line",
    "caption": "Caption text"
  },
  "cyber_front": {
    "body": "Markdown content...",
    "badge": {"top": "APT Active", "name": "ThreatActorName", "bottom": "Attribution"},
    "banner_title": "Banner Title",
    "banner_detail": "Detail line",
    "caption": "Caption text",
    "big_stat": {
      "value": "150+",
      "title": "Stat title",
      "description": "Stat description"
    }
  },
  "navigation_chart": {
    "gcc": {"code": "GCC", "name": "Gulf\nCooperation\nCouncil", "body": "Content..."},
    "cis": {"code": "CIS", "name": "Commonwealth of Independent States", "body": "Content..."},
    "afr": {"code": "AFR", "name": "Africa -\nEmerging\nFrontier", "body": "Content..."}
  },
  "market_depth": {
    "body": "Markdown content...",
    "bars": [35, 42, 38, 50, 44, 55, 48, 63, 70, 66, 78, 85, 91, 88, 95],
    "stats": [
      {"value": "+25%", "label": "Energy YTD", "style": "up"},
      {"value": "-5.4%", "label": "Tech YTD", "style": "dn"},
      {"value": "$84B", "label": "DPI 2031", "style": "gold"}
    ],
    "caption": "Caption text",
    "market_caption": "Chart annotation",
    "pullquote": {"text": "Quote text", "attribution": "Source"}
  },
  "the_heading": "Markdown content - 31C perspective...",
  "signal_watch": ["Item 1 with **bold** keywords...", "Item 2..."],
  "recommended_reading": [
    {"title": "Title", "url": "https://...", "source": "Source", "description": "Brief description"}
  ]
}
```

5. **Run the generator:**

```bash
python scripts/generate-newsletter-html.py "$NEWS_DIR/input.json"
```

To include AI-generated images:

```bash
python scripts/generate-newsletter-html.py "$NEWS_DIR/input.json" \
  --images sea_state="$NEWS_DIR/sea-state-visual.png"
```

Output defaults to the data-overlay newsletter folder for that date (the generator resolves it via
`get_outputs_dir()`), matching `$NEWS_DIR`.

---

## Phase 5: Validate

1. The generator automatically creates both HTML and PDF (single continuous page) in the newsletter folder.
2. Run `python scripts/sanitize-text.py "$NEWS_DIR/intelligence-briefing.html" --scan` to check for hidden characters.
3. Report both output file paths (HTML + PDF), word count, and hidden character status.
4. Remind user to open the HTML file in a browser and the PDF to verify both outputs.

---

## Voice Rules

- **Intelligence sections** (Sea State, Cyber Front, Navigation Chart, Market Depth): Third-person, objective, factual. No "we" or "our."
- **The Heading section**: First-person plural ("we"). This is 31C's strategic interpretation.
- **Signal Watch**: Brief, forward-looking. One line per item.
- **Overall**: Hyphens only (never em-dashes). ODUN.ONE styled correctly. DPI+ with the plus.
- **Length**: Total content 800-1200 words across all sections.

## NEVER
- Partner, client, investor, or reseller names
- Internal financial data or deal details
- "Excited to share" / "thrilled" / corporate filler
- Em-dashes (use hyphens)
- Military references (maritime only)
- Unverified statistics without sourcing
