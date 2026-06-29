---
name: playwright
description: >
  Automate web browsers with Playwright for screenshots, scraping, form filling,
  PDF generation, YouTube video analysis, and website testing. Use for any task
  requiring a real browser or YouTube content understanding -- extracting data from
  JavaScript-rendered pages, capturing screenshots of competitor websites, filling
  and submitting web forms, generating PDFs from web pages, monitoring website
  availability, testing 31C web properties, or understanding YouTube video content.
  Trigger when the user says "playwright", "screenshot this site", "scrape this page",
  "fill this form", "capture this website", "extract data from", "web automation",
  "browser automation", "PDF from website", "monitor this site", "watch this video",
  "what's in this YouTube video", "understand this video", "youtube transcript",
  or any task involving programmatic browser interaction or YouTube content extraction.
  Also trigger when a URL points to a JS-rendered page that WebFetch cannot handle.
  Do NOT trigger for simple URL fetching (use WebFetch), quick interactive browsing
  (use agent-browser), or building Playwright test suites as a developer.
argument-hint: "[url] [action]"
allowed-tools: "Bash(python3:*), Bash(npx:*), Read"
metadata:
  author: Misha Hanin
  email: misha.hanin@odinix.com
  version: "1.1"
x-31c-orchestration:
  parallel_safe: false
  shared_state: []
  triggers:
    - screenshot this site
    - scrape this page
    - browse to
    - headless browser
x-31c-capability:
  what: >
    Drives a real headless browser for screenshots, scraping JS-rendered pages, form filling, PDF generation, and website monitoring - plus YouTube content extraction (title, description, chapters, full transcript).
  how: >
    Run /playwright; it shells to .claude/skills/playwright/scripts/pw.py with subcommands screenshot, extract, fill, click, pdf, youtube, monitor, batch-screenshots. Outputs land in outputs/browser/. The youtube subcommand needs the VPN pre-flight (Proton).
  when: >
    Use when a task needs a real browser or YouTube understanding. For a simple URL fetch use WebFetch; for importing auth cookies use /setup-browser-cookies.
---
# Playwright Browser Automation

Browser automation and content extraction via Playwright (Python). Handles screenshots, scraping, form filling, PDF generation, YouTube content extraction, and website monitoring.

## Prerequisites

Dependencies (already installed in this workspace):
```bash
pip install playwright youtube-transcript-api yt-dlp python-dotenv
python -m playwright install chromium
```

## Script Location

```
.claude/skills/playwright/scripts/pw.py
```

**Run from the workspace root.** First anchor the shell: `cd "$(git rev-parse --show-toplevel)"`. A prior skill can leave the shell in a subdirectory, which breaks the root-relative script path below. All commands follow this pattern:
```bash
python ".claude/skills/playwright/scripts/pw.py" <command> [args]
```

## Quick Reference

| Command | Purpose | Example |
|---------|---------|---------|
| `screenshot` | Capture page screenshot | `pw.py screenshot "https://example.com"` |
| `extract` | Extract text/data via selectors | `pw.py extract "https://example.com" -s "h1" -f json` |
| `fill` | Fill form fields | `pw.py fill "https://url" --fields '{"#email": "test@test.com"}'` |
| `click` | Click element, capture result | `pw.py click "https://url" -s "button.submit"` |
| `pdf` | Generate PDF from web page | `pw.py pdf "https://example.com"` |
| `youtube` | Extract video content + transcript | `pw.py youtube "https://youtu.be/VIDEO_ID"` |
| `batch-screenshots` | Screenshot multiple URLs | `pw.py batch-screenshots "url1,url2,url3"` |
| `monitor` | Check URL status | `pw.py monitor "https://odun.one"` |
| `execute` | Run custom Playwright script | `pw.py execute "/tmp/my_script.py"` |

## YouTube Content Extraction

The most direct way to understand YouTube video content. Extracts title, description, chapters, and full transcript without needing a browser -- uses YouTube's caption APIs directly.

```bash
# Get full video content in markdown (default)
python ".claude/skills/playwright/scripts/pw.py" youtube "https://youtu.be/VIDEO_ID"

# Save to file
python ".claude/skills/playwright/scripts/pw.py" youtube "https://youtu.be/VIDEO_ID" -o "$OUTPUTS_DIR/browser/video.md"

# JSON format for structured processing
python ".claude/skills/playwright/scripts/pw.py" youtube "VIDEO_ID" -f json
```

Accepts YouTube URLs (youtube.com/watch?v=, youtu.be/, embed/) or bare video IDs.

Output includes:
- Title, channel, duration, view count, upload date
- Full description text
- Chapter markers (if available)
- Complete transcript with timestamps

After extracting, read the output and provide Misha with a summary of what the video covers.

### Pre-flight: VPN Check (MANDATORY for `youtube` subcommand)

Apply `.claude/rules/vpn-preflight.md` before invoking `pw.py youtube`. The
youtube-transcript-api path is blocked from datacenter IPs (Mullvad exits are
blacklisted; Proton Amsterdam is verified working). Confirm the user is on
Proton VPN (or a residential proxy) before running.

### Browser Cookie Source

`pw.py youtube` pulls YouTube cookies from a browser profile by default
(`--browser brave:ClaudeCode`) via yt-dlp's `cookiesfrombrowser` mechanism.
Brave is cross-platform (Linux/macOS/Windows) and yt-dlp-native, with sessions
pre-loaded on the CEO machine. This authenticates yt-dlp's metadata path and
reduces bot-detection friction.

Overrides:

- `--browser "chrome:Default"` -- use a different browser/profile
- `--cookies path/to/cookies.txt` -- override with a Netscape cookie file
- `--browser none` -- disable browser cookie extraction

Caveat: Chromium-based browsers lock their cookie SQLite DB on Windows while
running. If the cookie-source browser is open, yt-dlp falls back to
unauthenticated mode (not fatal, just degraded). Close it briefly for the
authenticated path. Linux generally avoids this lock (plaintext cookie storage).

Note: browser cookies do NOT help `youtube-transcript-api` (upstream cookie
support is broken, and cookies don't bypass datacenter-IP blocks). The VPN
pre-flight above is what actually unblocks the transcript fetch.

## Core Browser Automation

Worked per-command examples (screenshots, data extraction, form filling, PDF generation, batch screenshots, website monitoring) and the custom-script execution pattern live in `references/usage-cookbook.md`. The Quick Reference table above lists every subcommand inline; load the cookbook when you need a full flag-by-flag example.

## Common Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--headed` | Show browser window | Headless (hidden) |
| `--device "NAME"` | Emulate device (e.g., "iPhone 15", "Pixel 7") | Desktop |
| `--timeout MS` | Timeout in milliseconds | 30000 |
| `--wait-for MODE` | Wait strategy: `networkidle`, `domcontentloaded`, `selector:CSS` | networkidle |
| `--full-page` | Capture entire scrollable page (screenshots) | Viewport only |
| `-o, --output` | Output file path | Auto-generated or stdout |
| `-f, --format` | Output format (varies by command) | Varies |

## Output Location

`pw.py` resolves its auto-output directory via `get_outputs_dir()`, so when you OMIT `-o` /
`--output-dir` the file lands correctly under the DATA overlay's `browser/` tree -- never the engine.
**Prefer omitting the output flag** and let the script auto-place.

When you DO pass an explicit `-o` / `--output-dir`, the script writes it literally relative to cwd
(the engine root). A bare `outputs/browser/...` would therefore create a stray file inside the engine.
Resolve an absolute data path first and prefix it:

```bash
cd "$(git rev-parse --show-toplevel)"
OUTPUTS_DIR="$(python3 -c "import sys; sys.path.insert(0,'.'); from scripts.utils.workspace import get_outputs_dir; print(get_outputs_dir())")"
# then e.g.  -o "$OUTPUTS_DIR/browser/screenshots/shot.png"
```

Every `$OUTPUTS_DIR/browser/...` path in the examples above denotes this resolved data-overlay path.
Auto-generated outputs use these subdirectories:
- `$OUTPUTS_DIR/browser/screenshots/` -- screenshots
- `$OUTPUTS_DIR/browser/pdfs/` -- PDF files
- `$OUTPUTS_DIR/browser/batch/` -- batch screenshot results
- `$OUTPUTS_DIR/browser/data/` -- extracted data (use -o flag)

(Tool-level reads of `outputs/browser/cookies.json` are transparently redirected to the data overlay
by the `data-path-redirect` PreToolUse hook, so the cookie-path prose below needs no `$OUTPUTS_DIR`.)

## Selector Patterns

For detailed CSS selector patterns and Playwright locator strategies, see [references/selectors.md](references/selectors.md).

## Authenticated Browsing (Cookie Integration)

Playwright auto-loads cookies from `outputs/browser/cookies.json` if present. This file is populated by `/setup-browser-cookies` (gstack browse cookie importer). Once cookies are imported, all Playwright commands get authenticated sessions automatically.

### Authentication Fallback Flow

When a browser task hits a login page, 403, or CAPTCHA:

1. **Check if `outputs/browser/cookies.json` exists** and contains cookies for the target domain
2. **If no cookies:** prompt user to run `/setup-browser-cookies` to import from their real browser
3. **If cookies exist but page still blocks:** try with `--cookies-json` pointing to a fresh export, or fall back to gstack browse directly (`$B navigate <url>`) which may succeed due to different browser fingerprint
4. **If both fail:** report failure with diagnostics (page title, screenshot of what loaded)

### Manual cookie override

```bash
python ".claude/skills/playwright/scripts/pw.py" screenshot "https://dashboard.example.com" \
  --cookies-json /path/to/custom-cookies.json
```

## When to Use What

| Need | Tool |
|------|------|
| Quick web page content | WebFetch |
| Interactive browsing session | agent-browser |
| Structured scraping, screenshots, forms | **This skill** |
| YouTube video understanding | **This skill** (`youtube` command) |
| PDF from web page | **This skill** (`pdf` command) |
| Competitive site monitoring | **This skill** (`monitor` + `batch-screenshots`) |
| Import browser cookies for auth | `/setup-browser-cookies` (then this skill uses them) |
| Auth page after Playwright fails | gstack browse (fallback) |

## Voice

- Report outcomes factually: the saved file path, page title, HTTP status. No embellishment.
- After a `youtube` extraction, summarise what the video actually covers — do not invent content not in the transcript.
- Use hyphens (`-`), never double dashes (`--`); ODUN.ONE and DPI+ styled correctly.

## NEVER

- Never skip the VPN pre-flight (`.claude/rules/vpn-preflight.md`) before `pw.py youtube` — datacenter IPs are blocked.
- Never submit a form (`fill --submit-selector`) on the CEO's behalf without explicit approval — outbound submission is a human-gated action.
- Never pass a bare cwd-relative `outputs/...` to `-o` / `--output-dir`; resolve `$OUTPUTS_DIR` via `get_outputs_dir()` first so artifacts land in the DATA overlay, never the engine.
- Never store scraped credentials, cookies, or session tokens in any tracked workspace file.
