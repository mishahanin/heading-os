---
name: x-pulse
description: >
  On-demand X.com (Twitter) intelligence scanner. Fetches recent posts from a curated,
  categorised YAML account list (peer CEOs, DPI competitors, sovereign-tech thinkers,
  AI policy figures, personal interest), applies a two-stage engagement-then-judgement
  filter via Apify, and produces an MD/HTML/PDF brief with top-3 highlights, per-category
  breakdown, and 31C relevance + actions. Use when the user says "x-pulse", "/x-pulse",
  "what's on X", "scan X for", "twitter pulse", "what are [accounts] saying", or asks
  for X account monitoring intelligence. Differs from /yt-pulse (topic-based YouTube)
  in that x-pulse is account-centric.
argument-hint: "[--window 24h|72h|7d] [--bucket <name>]"
context: fork
allowed-tools: "Read, Bash(python3:*)"
model: sonnet
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.0"
x-31c-orchestration:
  parallel_safe: true
  shared_state: []
  triggers:
    - x-pulse
    - twitter pulse
    - what's on X
    - scan X for
    - X account monitor
x-31c-capability:
  what: >
    Account-centric X.com intelligence scan - fetches recent posts from a
    curated YAML account list, applies an engagement-then-judgement filter via
    Apify, and produces an MD/HTML/PDF brief with top-3 highlights and 31C
    relevance.
  how: >
    Run /x-pulse [--window 24h|72h|7d] [--bucket <name>]. Runs a dry-run cost
    preview first, then writes the brief to the data overlay's intel/x-pulse/. Needs
    APIFY_TOKEN in .env.
  when: >
    Use to monitor specific X accounts. For topic-based YouTube discovery use
    /yt-pulse.
---

# X Pulse - X.com Account-Monitoring Intelligence

Scan a curated, categorised list of X.com accounts, surface what they posted in the last 72h (configurable), and deliver an intelligence brief with 31C relevance and concrete actions.

## Variables

window: [optional] 24h | 72h (default) | 7d
bucket: [optional] one category from `config/x-pulse-accounts.yaml`; if omitted, all categories scanned
max_per_account: [optional] tweets to fetch per handle (default 30, max 100)
dry_run: [optional] print plan + estimated cost, exit without API call

## Pre-flight

`/x-pulse` does NOT use a browser and does NOT hit X.com from this workspace's IP. Apify runs the scraping server-side. Therefore:

- VPN preflight rule does NOT apply.
- Comet rule applies only by absence (no browser involved).

The skill checks for `APIFY_TOKEN` in `.env`. If missing:
1. Halt
2. Print: "Apify token not found. Sign up at https://apify.com (free tier = $5/month credit), then add `APIFY_TOKEN=apify_api_xxx` to `.env`. Re-run /x-pulse when ready."
3. Exit cleanly.

## Phase 0: Setup

1. Parse user options. Default: `--window 72h`, all buckets, `--max-per-account 30`.
2. Compute the output directory. The brief is a DATA artifact -- it must land in the DATA overlay,
   never the engine tree. `pulse.py` writes `--output-dir` literally relative to cwd (the engine root
   after the `cd`), so resolve an absolute path under the data outputs dir:
   ```bash
   cd "$(git rev-parse --show-toplevel)"
   OUTPUTS_DIR="$(python3 -c "import sys; sys.path.insert(0,'.'); from scripts.utils.workspace import get_outputs_dir; print(get_outputs_dir())")"
   out_dir="$OUTPUTS_DIR/intel/x-pulse/YYYY-MM-DD-HHMM"   # timestamp to the minute; on-demand may run several times a day
   ```
   Use `$out_dir` (or its resolved value) for `--output-dir` in every command below. Never pass a bare
   `outputs/intel/x-pulse/...` -- that resolves into the engine.
3. Run `pulse.py` in dry-run mode FIRST and present the plan to the user:

```bash
cd "$(git rev-parse --show-toplevel)" && python ".claude/skills/x-pulse/scripts/pulse.py" --dry-run \
  --window <window> [--bucket <bucket>] \
  --max-per-account <N> \
  --output-dir $out_dir
```

4. Show the printed plan + estimated cost. Ask the user to confirm: "Proceed? (yes / no)". If no, halt.

## Phase 1: Fetch (deterministic, Python-driven)

After confirmation:

```bash
cd "$(git rev-parse --show-toplevel)" && python ".claude/skills/x-pulse/scripts/pulse.py" \
  --window <window> [--bucket <bucket>] \
  --max-per-account <N> \
  --output-dir $out_dir
```

This produces `$out_dir/raw-posts.json` and `$out_dir/filtered-posts.json`.

If `pulse.py` exits non-zero or `filtered-posts.json` is empty, halt with the script's error message. Do not fabricate brief content.

## Phase 2: Engagement filter (already done by pulse.py)

`filtered-posts.json` is the survivor set: top 50% per category by `likes + 2*retweets + 3*replies`. Threads are collapsed; engagement summed.

## Phase 3: Highlights (Claude judgement)

Read `filtered-posts.json`. From all surviving posts across all categories, pick the **top 3 highlights** by judgement: insight density, novelty, 31C relevance, thread quality. Engagement is the tiebreaker.

For each highlight:
- 1-line "why this matters" framing
- The post text (or full thread if 6 tweets or fewer; truncated with ellipsis + link if longer)
- Engagement metrics
- 31C relevance (HIGH/MEDIUM/LOW/NONE) with 1-2 sentence reasoning

## Phase 4: Per-category breakdown

For each non-empty category, pick **up to 3 posts**, ranked by judgement.

**Important:** posts already chosen as Phase 3 highlights MUST be excluded from per-category lists to avoid duplication. If a category's strongest posts were all promoted to highlights, omit that category section but note in the brief footer (`peer_ceos: best post promoted to highlights, no further posts to surface`).

For each per-category post:
- Handle, post text excerpt (first 280 chars), engagement, link
- Single-line note from Claude on why it's notable

## Phase 5: Synthesis

Final section: **31C Relevance & Actions**.

- 2-3 sentences synthesising the broader signal across all surfaced posts
- 1-3 specific action items if any post was rated MEDIUM+ relevance
- Cross-reference 31C strategy:
  - Competitor mentions (named DPI and networking vendors)
  - DPI, network intelligence, sovereign tech
  - GCC, CIS, Africa, Europe market signals
  - AI agent / sovereignty / regulation that touches ODUN.ONE

## Phase 6: Brief generation

### 6a. Write Markdown

Create `$out_dir/x-pulse-brief.md` with this structure:

```markdown
# X Pulse: [Date] - [Window: 72h]

**Accounts scanned:** N (across M categories)
**Posts in window:** X
**After engagement filter:** Y

---

## Top 3 Highlights

### 1. @[handle] - [category]

[1-line why-this-matters framing]

> [post text or full thread if <=6 tweets]

[Engagement: likes / retweets / replies. Link]

**31C relevance:** [HIGH/MEDIUM/LOW/NONE] - [1-2 sentence reasoning]

[... repeat for #2 and #3 ...]

---

## Per-Category Breakdown

### peer_ceos

- @[handle]: "[excerpt 280 chars]" - [Likes / RTs / Replies] - [Link]
  - [single-line Claude note]

[... and so on per category ...]

---

## 31C Relevance & Actions

[2-3 sentences synthesising signal]

**Action items:**
1. [specific action]
2. [specific action]

---

[footer: failed accounts (if any), runtime, cost]
```

### 6b. Write HTML

Create `$out_dir/x-pulse-brief.html` using the SAME design tokens as `/yt-pulse`:

- Font: Segoe UI, Helvetica Neue, Arial, sans-serif
- Body: 10pt, line-height 1.55
- Page: A4, 20mm margins, @media print ready
- Colors: #1a1a1a (text), #c77b30 (accent), #2980b9 (links), #f7f3ed (callout bg)
- Header: "31 Concept - X Pulse Intelligence" + date + window
- Highlights: cards with handle pill, post quoted in blockquote with #c77b30 left border
- Per-category section: clean tables, alternating row backgrounds (#f9f9f9)
- 31C Relevance: callout box, #f7f3ed background, 3px left border #c77b30
- Footer: "Generated by /x-pulse" + date

### 6c. Convert to PDF

```bash
python scripts/html-to-pdf.py "$out_dir/x-pulse-brief.html"
```

## Phase 7: Validate & Report

1. Sanitiser scan:

```bash
python scripts/sanitize-text.py "$out_dir/x-pulse-brief.md" --scan
python scripts/sanitize-text.py "$out_dir/x-pulse-brief.html" --scan
```

2. Report to user (per always-show-full-paths rule):
   - Full absolute Windows paths for all 5 output files (MD, HTML, PDF, raw JSON, filtered JSON)
   - Word count + hidden character status
   - Top handle by engagement
   - Highest 31C relevance level on any single post
   - Any failed accounts noted in footer
   - Suggest: "Want me to deep-dive any of the per-category posts?"

## Voice Rules

- **Highlights and per-category sections:** factual, objective. Report what each handle posted.
- **Quoted tweet text:** preserve verbatim, including X's curly quotes, em-dashes, abbreviations. Do NOT apply humanisation rule to quoted X content (third-party text, exception #1).
- **31C relevance section:** direct, tactical, 1st person ("you", "your"). Mirror /yt-pulse style.
- **Action items:** imperative, specific, link to real pipeline/strategy.
- **General Claude prose** (commentary outside quotes): hyphens only (never `--`), ODUN.ONE styled, DPI+ with plus, pass humanisation audit.

## NEVER

- Fabricate engagement counts, follower numbers, or post text
- Present a tweet's claims as validated facts without noting they are the author's opinion
- Include posts already promoted to highlights in the per-category section
- Skip the 31C relevance assessment, even if relevance is NONE
- Generate briefs without running the sanitiser
- Edit `.env` to add APIFY_TOKEN automatically - if missing, instruct the user
