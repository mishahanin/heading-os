---
name: yt-pulse
description: >
  Domain-agnostic YouTube intelligence scanner. Searches YouTube for videos in any
  topic area (AI, DPI, cybersecurity, markets, sailing, etc.), identifies trending
  topics, ranks videos by engagement quality, deep-analyzes the top video with full
  transcript extraction, and delivers an actionable intelligence brief in MD, HTML,
  and PDF formats. Automates daily YouTube scanning. Use when the user says
  "yt-pulse", "youtube pulse", "scan YouTube for", "what's trending on YouTube about",
  "YouTube intel on", "find YouTube videos about", "what are creators talking about",
  "youtube scan", or asks for YouTube content discovery and analysis in any domain.
argument-hint: "[query]"
context: fork
allowed-tools: "WebSearch, WebFetch, Read, Bash(python3:*)"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.3"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - youtube pulse
    - youtube trends
    - what's trending on YouTube
    - scan YouTube for
x-31c-capability:
  what: >
    Domain-agnostic YouTube intelligence scanner - searches for videos in any topic, clusters the
    hot topics creators are discussing, deep-analyzes the top video by transcript, scores 31C relevance,
    and delivers a brief in MD, HTML, and PDF.
  how: >
    Run /yt-pulse <query>. Fires a mandatory VPN pre-flight gate first (datacenter IPs get blocked),
    then writes the three-format brief to outputs/intel/pulse/YYYY-MM-DD-<slug>/.
  when: >
    Use for YouTube content discovery and analysis in any domain. For non-YouTube target research use
    /osint; for an X/Twitter scan use /x-pulse.
---
# YT Pulse - YouTube Intelligence Scanner

Scan YouTube for trending content in any domain, identify what creators are talking about, find the best video worth watching, and deliver an intelligence brief with 31C business relevance.

## Variables

query: [required] Domain or topic to scan (e.g., "AI agents", "deep packet inspection", "sailing navigation", "cybersecurity threats 2026")
timeframe: [optional] 24h | 72h (default) | 7d | 30d
depth: [optional] quick (search + clustering only) | full (default, includes transcript analysis)

---

## Instructions

Before executing, read:
- `context/strategy.md` - Strategic priorities for relevance assessment
- `context/business-info.md` - ODUN.ONE capabilities
- `context/pipeline.md` - Active deals for relevance scoring

---

## Configuration

Per-channel cap, browser cookie sourcing (default: Brave ClaudeCode profile via yt-dlp `cookiesfrombrowser` — Brave is cross-platform and yt-dlp-native), and optional residential proxy fallback - see `references/configuration.md`. Key default: `--per-channel-cap 3` (v1.3 behaviour change; pass `0` to restore legacy uncapped ranking).

---

## Pre-flight: VPN Check (MANDATORY, runs before Phase 0)

Apply `.claude/rules/vpn-preflight.md` before any network operation in this skill.
In short:

1. Run a silent exit-IP check:

   ```bash
   python -c "import urllib.request, json; r = urllib.request.urlopen('https://api.ipify.org?format=json', timeout=5); print(json.loads(r.read().decode())['ip'])"
   ```

2. Geolocate via `https://ipinfo.io/<ip>/json` -- look at the `org` field.
3. Present the combined gate with AskUserQuestion:
   > **Pre-flight check before `/yt-pulse`.** Current exit: `<IP>` (`<ORG>`, `<COUNTRY>`).
   >
   > 1. **VPN:** Confirm you are connected to **Proton VPN** (Amsterdam verified
   >    working) or a residential proxy. If you are on Mullvad or bare ISP, the
   >    transcript step will likely fall back to web search.
   > 2. **Browser cookies:** For the authenticated path, close the cookie-source
   >    browser (Brave by default) before proceeding. Chromium-family browsers
   >    lock their cookie SQLite DB on Windows while running, blocking yt-dlp
   >    from reading the ClaudeCode profile's YouTube session. Not fatal -- the
   >    skill falls back to unauthenticated yt-dlp when locked -- but closing
   >    it briefly enables the authenticated path and reduces bot-detection.

   Options:
   - `Yes, Proton VPN is active and browser is closed` -> proceed (best path)
   - `Yes, Proton VPN is active (browser stays open)` -> proceed without cookies
   - `No, let me switch VPN / close browser first` -> halt, wait for user
   - `Skip the check this time` -> proceed, note fallback likelihood in brief

4. Wait for explicit confirmation. Do NOT proceed on silence.

Once confirmed within a session, do not re-fire for 30 minutes unless a prior run
returned `IP_BLOCKED`.

---

## Phase 0: Setup

1. Parse the user's query and options. If no query provided, ask what domain to scan.
2. Set defaults: timeframe = 72h, depth = full
3. Create the output directory. The brief and its intermediates are DATA artifacts -- they must land
   in the DATA overlay, never the engine tree. `pulse.py` and `pw.py` write their `--output`/`-o`
   paths literally relative to cwd (the engine root after the `cd`), so resolve an absolute path under
   the data outputs dir and reuse `$PULSE_DIR` everywhere below:
   ```bash
   cd "$(git rev-parse --show-toplevel)"
   OUTPUTS_DIR="$(python3 -c "import sys; sys.path.insert(0,'.'); from scripts.utils.workspace import get_outputs_dir; print(get_outputs_dir())")"
   PULSE_DIR="$OUTPUTS_DIR/intel/pulse/YYYY-MM-DD-[slug]"   # slug = kebab-case of the first 3 meaningful query words
   mkdir -p "$PULSE_DIR"
   ```
   Never pass a bare `outputs/intel/pulse/...` to a Bash script -- that resolves into the engine.

---

## Phase 1: YouTube Search

Run the search script:

```bash
cd "$(git rev-parse --show-toplevel)" && python ".claude/skills/yt-pulse/scripts/pulse.py" \
  --query "[user query]" \
  --timeframe [timeframe] \
  --max-results 50 \
  --min-duration 120 \
  --output "$PULSE_DIR/search-results.json"
```

The script uses YouTube's native date filter (server-side) in fast extract_flat mode. This means:
- All returned videos are already within the timeframe (YouTube filters them)
- Metadata includes: title, channel, view_count, duration, thumbnail
- No upload_date per video (not needed - YouTube's filter handles it)
- Fast execution (~5-10 seconds for 50 results)

Read the output JSON. Check `filtered_results`:
- If 0: Report "No videos found in [timeframe] for '[query]'. Try expanding timeframe to 7d or adjusting the query." Stop.
- If < 5: Note limited results, proceed anyway.
- If > 5: Proceed normally.

---

## Phase 2: Topic Clustering

Read all video titles and descriptions from `search-results.json`.

Cluster them into 3-5 hot topics:

1. Read through all titles. Identify recurring themes, technologies, events, or concepts that multiple videos cover.
2. Group videos by shared topic. A video can belong to multiple clusters.
3. Name each cluster with a descriptive 2-4 word label.
4. Count videos per cluster. Sort clusters by video count descending.
5. Write a one-line description for each cluster explaining what creators are saying about it.

The goal: answer "What is everyone talking about right now in [domain]?"

If `depth` is `quick`, skip to Phase 5 (generate brief with search results and clusters only, no transcript analysis).

---

## Phase 3: Top Video Selection & Deep Analysis

### 3a. Select Top Video

The search results are already sorted by engagement score. Take the #1 video.

The engagement score formula (already computed by pulse.py):
- Raw view count as primary signal (all videos are within the same timeframe)
- Subscriber normalization when available (identifies breakout content from smaller channels)
- Duration penalty for < 2 min (filters shorts/clickbait)
- Duration boost for 5-20 min (rewards in-depth analysis)

### 3b. Extract Transcript

Use the existing Playwright youtube command:

```bash
cd "$(git rev-parse --show-toplevel)" && python ".claude/skills/playwright/scripts/pw.py" youtube "[video_id]" \
  -f json \
  -o "$PULSE_DIR/top-video.json" \
  [--proxy "PROXY_URL" if pre-flight directed a proxy fallback]
```

Browser cookies (`--browser brave:ClaudeCode` by default; yt-dlp also accepts
`chrome`, `chromium`, `edge`, `firefox`) and VPN exit are handled per the
Configuration and Pre-flight sections above. Use `--cookies path/to/file` or
`--browser "chrome:Default"` only to override the defaults.

Read the JSON output. Check for errors and follow this fallback chain:

1. **Transcript available** (transcript field is populated) - Proceed to analysis with full content.

2. **"IP_BLOCKED" in transcript_error** - YouTube blocked the server IP. Automated fallback:
   - Use WebSearch to find `"[video title]" [channel name] summary OR takeaways OR review`
   - Use WebFetch on any blog posts, articles, or summaries found
   - If metadata was also blocked, use the search results data (title, channel, views, duration) from pulse.py
   - Note in brief: "Analysis based on web sources (transcript blocked from server IP)"

3. **Other transcript_error** (no captions, private video, etc.) - Use title, description, and chapter list for analysis. Note: "Transcript unavailable - analysis based on title and description only"

### 3c. Analyze Content

From the transcript (or description fallback), produce:

1. **Key Takeaways** - 4-5 bullet points. Each should be a standalone insight, not a summary sentence. A reader should understand the video's value from these bullets alone.

2. **Content Summary** - 2-3 paragraphs covering: what the video argues or demonstrates, the evidence or examples used, and the creator's conclusions or predictions.

3. **Notable Quotes** - 2-3 direct quotes from the transcript that capture the most insightful or provocative statements.

4. **Watch Recommendation** - One of:
   - **Must watch** - Groundbreaking content, directly relevant, high production quality
   - **Watch if time permits** - Good content, somewhat relevant, worth 15-20 min
   - **Skim transcript** - Useful information but can be absorbed from text
   - **Skip** - Low value relative to time investment

---

## Phase 4: 31C Relevance Assessment

Cross-reference the video content (and broader hot topics) against 31C's context:

**Check for:**
- Competitor mentions (named DPI and networking vendors)
- DPI, deep packet inspection, network intelligence, lawful intercept, traffic management
- Telecom infrastructure, 5G security, network visibility
- Data sovereignty, government cybersecurity, national security tech
- Markets where 31C operates (GCC, CIS, Africa, Europe)
- AI/ML applied to network security or traffic analysis
- Regulatory changes affecting telecom or cybersecurity vendors
- Technologies that align with or threaten ODUN.ONE's architecture

**Assign relevance score:**
- **HIGH** - Directly discusses DPI vendors, network security platforms, telecom intelligence, or 31C's target markets. Immediate business value.
- **MEDIUM** - Discusses adjacent topics (cybersecurity trends, AI in networking, telecom policy, sovereign tech) with indirect relevance.
- **LOW** - General domain content with no clear connection to 31C.
- **NONE** - Completely unrelated to 31C's business.

If relevance is MEDIUM or above, generate 1-3 specific action items:
- What should Misha do with this information?
- Connect to specific pipeline deals, partners, or strategy points where relevant.

---

## Phase 5: Generate Brief (MD + HTML + PDF)

Markdown template, HTML design system, and PDF conversion command - see `references/output-template.md`. Produce three artifacts in `$PULSE_DIR/` (the data-overlay dir resolved in Phase 0):

- `yt-pulse-brief.md` - markdown brief per the template
- `yt-pulse-brief.html` - styled HTML per the design system (consistent with ceo-intel)
- `yt-pulse-brief.pdf` - rendered via `python scripts/html-to-pdf.py <html-path>`

---

## Phase 6: Validate & Report

1. Validate:
   ```bash
   python scripts/sanitize-text.py "$PULSE_DIR/yt-pulse-brief.html" --scan
   ```

2. Report to user:
   - Output directory path
   - All 3 file paths: MD, HTML, PDF
   - Word count and hidden character status
   - Top video URL (clickable)
   - Relevance score
   - Suggest: "Want me to deep-dive any of the runners-up?"

---

## Voice Rules

- **Hot Topics section**: Factual, objective. Report what creators are discussing, not opinions.
- **Video analysis**: Analytical, third-person. Summarize arguments fairly.
- **31C Relevance**: Direct, tactical, first-person ("you", "your"). Speak to Misha as the operator.
- **Action items**: Imperative. Specific. Connected to real pipeline or strategy.
- **Overall**: Hyphens only (never em-dashes). ODUN.ONE styled correctly. DPI+ with plus.

## NEVER

- Fabricate view counts, subscriber numbers, or engagement metrics
- Present a video's claims as validated facts without noting they are the creator's perspective
- Include full transcript text in the brief (only quotes and summaries)
- Skip the relevance assessment, even if the topic seems unrelated to 31C
- Generate briefs without running the sanitizer
