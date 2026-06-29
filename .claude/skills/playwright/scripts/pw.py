#!/usr/bin/env python3
"""Playwright browser automation CLI for Claude Code workspace.

Subcommands:
  screenshot    Capture page screenshot
  extract       Extract text/data via CSS selectors
  fill          Fill form fields
  click         Click element and capture result
  pdf           Generate PDF from web page
  youtube       Extract YouTube video content (title, description, transcript)
  batch-screenshots  Screenshot multiple URLs
  monitor       Check URL status and capture state
  execute       Run a custom Playwright Python script
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Fix Windows console encoding for Unicode output
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Resolve workspace root (4 levels up from this script)
SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SCRIPT_DIR.parent.parent.parent.parent

# Load .env via workspace central loader
sys.path.insert(0, str(WORKSPACE_ROOT))
from scripts.utils.workspace import get_outputs_dir
try:
    from scripts.utils.workspace import load_env
    load_env(WORKSPACE_ROOT)
except ImportError:
    pass

OUTPUT_DIR = get_outputs_dir() / "browser"


def ensure_output_dir(subdir=None):
    """Ensure output directory exists and return it."""
    d = OUTPUT_DIR / subdir if subdir else OUTPUT_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_wait_for(wait_for, page, timeout=30000):
    """Handle --wait-for options: networkidle, domcontentloaded, selector:CSS."""
    if not wait_for:
        page.wait_for_load_state("networkidle", timeout=timeout)
        return
    if wait_for == "networkidle":
        page.wait_for_load_state("networkidle", timeout=timeout)
    elif wait_for == "domcontentloaded":
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
    elif wait_for.startswith("selector:"):
        selector = wait_for[len("selector:"):]
        page.wait_for_selector(selector, timeout=timeout)
    else:
        page.wait_for_load_state("networkidle", timeout=timeout)


COOKIES_FILE = get_outputs_dir() / "browser" / "cookies.json"


def launch_browser(headless=True, device=None, cookies_json=None):
    """Launch Chromium and return (playwright, browser, context, page).

    Automatically loads cookies from outputs/browser/cookies.json if present,
    enabling authenticated sessions imported via /setup-browser-cookies.
    Use cookies_json to override with a specific cookie file path.
    """
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=headless)

    context_opts = {}
    if device:
        dev = pw.devices.get(device)
        if dev:
            context_opts.update(dev)
        else:
            print(f"Warning: Device '{device}' not found. Using default viewport.", file=sys.stderr)

    context = browser.new_context(**context_opts)

    # Auto-load cookies: explicit path > default shared cookie store
    cookie_path = Path(cookies_json) if cookies_json else COOKIES_FILE
    if cookie_path.exists():
        try:
            with open(cookie_path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            if cookies:
                context.add_cookies(cookies)
                print(f"Loaded {len(cookies)} cookies from {cookie_path.name}", file=sys.stderr)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Could not load cookies from {cookie_path}: {e}", file=sys.stderr)

    page = context.new_page()
    return pw, browser, context, page


def cleanup(pw, browser):
    """Safely close browser and stop Playwright."""
    try:
        browser.close()
    except Exception as exc:
        print(f"pw: browser.close() failed: {exc}", file=sys.stderr)
    try:
        pw.stop()
    except Exception as exc:
        print(f"pw: playwright stop failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Subcommand: screenshot
# ---------------------------------------------------------------------------
def cmd_screenshot(args):
    url = args.url
    output = args.output
    if not output:
        safe_name = re.sub(r'[^\w\-.]', '_', url.split("//")[-1][:60])
        output = str(ensure_output_dir("screenshots") / f"{safe_name}.png")
    else:
        Path(output).parent.mkdir(parents=True, exist_ok=True)

    pw, browser, context, page = launch_browser(
        headless=not args.headed,
        device=args.device,
        cookies_json=getattr(args, 'cookies_json', None),
    )
    try:
        page.goto(url, timeout=args.timeout)
        parse_wait_for(args.wait_for, page, timeout=args.timeout)
        page.screenshot(path=output, full_page=args.full_page)
        print(json.dumps({"status": "ok", "file": output, "url": url}))
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e), "url": url}))
        sys.exit(1)
    finally:
        cleanup(pw, browser)


# ---------------------------------------------------------------------------
# Subcommand: extract
# ---------------------------------------------------------------------------
def cmd_extract(args):
    url = args.url
    selector = args.selector
    fmt = args.format

    pw, browser, context, page = launch_browser(
        headless=not args.headed,
        device=args.device,
        cookies_json=getattr(args, 'cookies_json', None),
    )
    try:
        page.goto(url, timeout=args.timeout)
        parse_wait_for(args.wait_for, page, timeout=args.timeout)

        if args.all:
            elements = page.query_selector_all(selector)
            results = []
            for el in elements:
                text = el.inner_text()
                href = el.get_attribute("href")
                src = el.get_attribute("src")
                entry = {"text": text.strip()}
                if href:
                    entry["href"] = href
                if src:
                    entry["src"] = src
                results.append(entry)
        else:
            el = page.query_selector(selector)
            if el:
                results = [{"text": el.inner_text().strip()}]
                href = el.get_attribute("href")
                src = el.get_attribute("src")
                if href:
                    results[0]["href"] = href
                if src:
                    results[0]["src"] = src
            else:
                results = []

        if fmt == "json":
            output_data = json.dumps({"status": "ok", "url": url, "selector": selector, "results": results}, indent=2)
        elif fmt == "csv":
            lines = ["text,href,src"]
            for r in results:
                lines.append(f'"{r.get("text", "")}","{r.get("href", "")}","{r.get("src", "")}"')
            output_data = "\n".join(lines)
        else:
            output_data = "\n".join(r.get("text", "") for r in results)

        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(output_data, encoding="utf-8")
            print(json.dumps({"status": "ok", "file": args.output, "count": len(results)}))
        else:
            print(output_data)
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e), "url": url}))
        sys.exit(1)
    finally:
        cleanup(pw, browser)


# ---------------------------------------------------------------------------
# Subcommand: fill
# ---------------------------------------------------------------------------
def cmd_fill(args):
    url = args.url
    try:
        fields = json.loads(args.fields)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "error": f"Invalid JSON in --fields: {e}", "url": args.url}))
        sys.exit(1)

    pw, browser, context, page = launch_browser(
        headless=not args.headed,
        device=args.device,
        cookies_json=getattr(args, 'cookies_json', None),
    )
    try:
        page.goto(url, timeout=args.timeout)
        parse_wait_for(args.wait_for, page, timeout=args.timeout)

        filled = []
        for selector, value in fields.items():
            page.fill(selector, value)
            filled.append(selector)

        result = {"status": "ok", "url": url, "filled": filled}

        if args.submit_selector:
            page.click(args.submit_selector)
            page.wait_for_load_state("networkidle", timeout=args.timeout)
            result["submitted"] = True
            result["final_url"] = page.url

        if args.screenshot_after:
            out_path = args.screenshot_after
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=out_path, full_page=True)
            result["screenshot"] = out_path

        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e), "url": url}))
        sys.exit(1)
    finally:
        cleanup(pw, browser)


# ---------------------------------------------------------------------------
# Subcommand: click
# ---------------------------------------------------------------------------
def cmd_click(args):
    url = args.url
    selector = args.selector

    pw, browser, context, page = launch_browser(
        headless=not args.headed,
        device=args.device,
        cookies_json=getattr(args, 'cookies_json', None),
    )
    try:
        page.goto(url, timeout=args.timeout)
        parse_wait_for(args.wait_for, page, timeout=args.timeout)

        page.click(selector)
        if args.wait_after:
            time.sleep(args.wait_after / 1000)
        page.wait_for_load_state("networkidle", timeout=args.timeout)

        result = {
            "status": "ok",
            "url": url,
            "clicked": selector,
            "final_url": page.url,
            "title": page.title(),
        }

        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=args.output, full_page=True)
            result["screenshot"] = args.output

        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e), "url": url}))
        sys.exit(1)
    finally:
        cleanup(pw, browser)


# ---------------------------------------------------------------------------
# Subcommand: pdf
# ---------------------------------------------------------------------------
def cmd_pdf(args):
    url = args.url
    output = args.output
    if not output:
        safe_name = re.sub(r'[^\w\-.]', '_', url.split("//")[-1][:60])
        output = str(ensure_output_dir("pdfs") / f"{safe_name}.pdf")
    else:
        Path(output).parent.mkdir(parents=True, exist_ok=True)

    # PDF generation requires headless Chromium (not headed)
    pw, browser, context, page = launch_browser(headless=True, device=args.device, cookies_json=getattr(args, 'cookies_json', None))
    try:
        page.goto(url, timeout=args.timeout)
        parse_wait_for(args.wait_for, page, timeout=args.timeout)

        pdf_opts = {"path": output}
        if args.format == "Letter":
            pdf_opts["format"] = "Letter"
        else:
            pdf_opts["format"] = "A4"
        if args.landscape:
            pdf_opts["landscape"] = True
        pdf_opts["print_background"] = True

        page.pdf(**pdf_opts)
        print(json.dumps({"status": "ok", "file": output, "url": url}))
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e), "url": url}))
        sys.exit(1)
    finally:
        cleanup(pw, browser)


# ---------------------------------------------------------------------------
# Subcommand: youtube
# ---------------------------------------------------------------------------
def extract_video_id(url_or_id):
    """Extract YouTube video ID from URL or return as-is if already an ID."""
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([a-zA-Z0-9_-]{11})',
        r'^([a-zA-Z0-9_-]{11})$',
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    return url_or_id


def cmd_youtube(args):
    url_or_id = args.url
    video_id = extract_video_id(url_or_id)
    fmt = args.format

    result = {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }

    # Get metadata via yt-dlp
    try:
        import yt_dlp
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "no_check_certificates": True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["mweb", "android"],
                },
            },
        }
        if getattr(args, "cookies", None):
            ydl_opts["cookiefile"] = args.cookies
        else:
            browser_spec = getattr(args, "browser", None)
            if browser_spec and browser_spec.strip().lower() != "none":
                parts = browser_spec.split(":", 1)
                browser = parts[0].strip().lower()
                profile = parts[1].strip() if len(parts) == 2 and parts[1].strip() else None
                ydl_opts["cookiesfrombrowser"] = (browser, profile, None, None)
        if getattr(args, "proxy", None):
            ydl_opts["proxy"] = args.proxy
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            result["title"] = info.get("title", "")
            result["channel"] = info.get("uploader", info.get("channel", ""))
            result["description"] = info.get("description", "")
            result["duration"] = info.get("duration")
            result["duration_string"] = info.get("duration_string", "")
            result["view_count"] = info.get("view_count")
            result["upload_date"] = info.get("upload_date", "")
            result["thumbnail"] = info.get("thumbnail", "")
            # Extract chapters if available
            chapters = info.get("chapters")
            if chapters:
                result["chapters"] = [
                    {"title": ch.get("title", ""), "start_time": ch.get("start_time", 0)}
                    for ch in chapters
                ]
    except Exception as e:
        result["metadata_error"] = str(e)

    # Get transcript via youtube-transcript-api
    try:
        from youtube_transcript_api._errors import IpBlocked, RequestBlocked
    except ImportError:
        IpBlocked = RequestBlocked = type("_Stub", (Exception,), {})
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt_kwargs = {}
        if getattr(args, "proxy", None):
            from youtube_transcript_api.proxies import GenericProxyConfig
            ytt_kwargs["proxy_config"] = GenericProxyConfig(
                http_url=args.proxy,
                https_url=args.proxy,
            )
        ytt = YouTubeTranscriptApi(**ytt_kwargs)
        transcript = ytt.fetch(video_id)
        entries = []
        for snippet in transcript:
            entries.append({
                "text": snippet.text,
                "start": snippet.start,
                "duration": snippet.duration,
            })
        result["transcript_entries"] = entries
        result["transcript"] = " ".join(s.text for s in transcript)
    except (IpBlocked, RequestBlocked) as e:
        result["transcript_error"] = f"IP_BLOCKED: {e}"
        result["transcript"] = None
    except Exception as e:
        result["transcript_error"] = str(e)
        result["transcript"] = None

    # Format output
    if fmt == "markdown":
        lines = []
        lines.append(f"# {result.get('title', 'Unknown Title')}")
        lines.append("")
        lines.append(f"**Channel:** {result.get('channel', 'Unknown')}")
        lines.append(f"**Duration:** {result.get('duration_string', 'Unknown')}")
        lines.append(f"**Views:** {result.get('view_count', 'Unknown'):,}" if result.get('view_count') else "**Views:** Unknown")
        lines.append(f"**Uploaded:** {result.get('upload_date', 'Unknown')}")
        lines.append(f"**URL:** {result.get('url', '')}")
        lines.append("")
        if result.get("description"):
            lines.append("## Description")
            lines.append("")
            lines.append(result["description"])
            lines.append("")
        if result.get("chapters"):
            lines.append("## Chapters")
            lines.append("")
            for ch in result["chapters"]:
                mins = int(ch["start_time"]) // 60
                secs = int(ch["start_time"]) % 60
                lines.append(f"- [{mins}:{secs:02d}] {ch['title']}")
            lines.append("")
        if result.get("transcript"):
            lines.append("## Transcript")
            lines.append("")
            lines.append(result["transcript"])
        elif result.get("transcript_error"):
            lines.append("## Transcript")
            lines.append("")
            lines.append(f"*Transcript unavailable: {result['transcript_error']}*")
        output_text = "\n".join(lines)
    elif fmt == "json":
        output_text = json.dumps(result, indent=2, ensure_ascii=False)
    else:
        # Plain text
        lines = []
        lines.append(f"Title: {result.get('title', 'Unknown')}")
        lines.append(f"Channel: {result.get('channel', 'Unknown')}")
        lines.append(f"Duration: {result.get('duration_string', 'Unknown')}")
        lines.append(f"Views: {result.get('view_count', 'Unknown')}")
        lines.append(f"Uploaded: {result.get('upload_date', 'Unknown')}")
        lines.append("")
        if result.get("description"):
            lines.append("Description:")
            lines.append(result["description"])
            lines.append("")
        if result.get("transcript"):
            lines.append("Transcript:")
            lines.append(result["transcript"])
        elif result.get("transcript_error"):
            lines.append(f"Transcript unavailable: {result['transcript_error']}")
        output_text = "\n".join(lines)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output_text, encoding="utf-8")
        print(json.dumps({"status": "ok", "file": args.output, "video_id": video_id, "title": result.get("title", "")}))
    else:
        print(output_text)


# ---------------------------------------------------------------------------
# Subcommand: batch-screenshots
# ---------------------------------------------------------------------------
def cmd_batch_screenshots(args):
    urls_input = args.urls
    output_dir = args.output_dir or str(ensure_output_dir("batch"))

    # Parse URLs from file or comma-separated string
    if os.path.isfile(urls_input):
        with open(urls_input, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    else:
        urls = [u.strip() for u in urls_input.split(",") if u.strip()]

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    pw, browser, context, page = launch_browser(
        headless=not args.headed,
        device=args.device,
        cookies_json=getattr(args, 'cookies_json', None),
    )
    results = []
    try:
        for i, url in enumerate(urls):
            safe_name = re.sub(r'[^\w\-.]', '_', url.split("//")[-1][:60])
            out_path = os.path.join(output_dir, f"{i+1:03d}_{safe_name}.png")
            try:
                page.goto(url, timeout=args.timeout)
                parse_wait_for(args.wait_for, page, timeout=args.timeout)
                page.screenshot(path=out_path, full_page=args.full_page)
                results.append({"status": "ok", "url": url, "file": out_path})
            except Exception as e:
                results.append({"status": "error", "url": url, "error": str(e)})
    finally:
        cleanup(pw, browser)

    print(json.dumps({"total": len(urls), "success": sum(1 for r in results if r["status"] == "ok"), "results": results}, indent=2))


# ---------------------------------------------------------------------------
# Subcommand: monitor
# ---------------------------------------------------------------------------
def cmd_monitor(args):
    url = args.url

    pw, browser, context, page = launch_browser(
        headless=not args.headed,
        device=args.device,
        cookies_json=getattr(args, 'cookies_json', None),
    )
    try:
        start = time.time()
        response = page.goto(url, timeout=args.timeout)
        parse_wait_for(args.wait_for, page, timeout=args.timeout)
        load_time = round(time.time() - start, 2)

        result = {
            "status": "ok",
            "url": url,
            "http_status": response.status if response else None,
            "title": page.title(),
            "load_time_seconds": load_time,
        }

        if args.check_selector:
            el = page.query_selector(args.check_selector)
            result["selector_found"] = el is not None
            if el and args.expected_text:
                text = el.inner_text().strip()
                result["selector_text"] = text
                result["text_matches"] = args.expected_text.lower() in text.lower()

        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=args.output, full_page=True)
            result["screenshot"] = args.output

        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({"status": "error", "url": url, "error": str(e)}))
        sys.exit(1)
    finally:
        cleanup(pw, browser)


# ---------------------------------------------------------------------------
# Subcommand: execute
# ---------------------------------------------------------------------------
def cmd_execute(args):
    script_path = args.script
    if not os.path.isfile(script_path):
        print(json.dumps({"status": "error", "error": f"Script not found: {script_path}"}))
        sys.exit(1)

    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=args.timeout // 1000,
            cwd=str(WORKSPACE_ROOT),
        )
        output = result.stdout
        if result.returncode != 0:
            output += "\n" + result.stderr if result.stderr else ""
            print(json.dumps({"status": "error", "exit_code": result.returncode, "output": output.strip()}))
            sys.exit(1)
        else:
            print(output)
    except subprocess.TimeoutExpired:
        print(json.dumps({"status": "error", "error": f"Script timed out after {args.timeout // 1000}s"}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}))
        sys.exit(1)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------
def build_parser():
    parser = argparse.ArgumentParser(
        description="Playwright browser automation CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Common arguments
    def add_common_args(p):
        p.add_argument("url", help="Target URL")
        p.add_argument("--headed", action="store_true", help="Run with visible browser (default: headless)")
        p.add_argument("--device", help="Emulate device (e.g., 'iPhone 15', 'Pixel 7')")
        p.add_argument("--timeout", type=int, default=30000, help="Timeout in ms (default: 30000)")
        p.add_argument("--wait-for", dest="wait_for", help="Wait strategy: networkidle, domcontentloaded, selector:CSS")
        p.add_argument("--cookies-json", dest="cookies_json", help="Path to cookies JSON file (default: auto-detect outputs/browser/cookies.json)")

    # screenshot
    p = subparsers.add_parser("screenshot", help="Capture page screenshot")
    add_common_args(p)
    p.add_argument("--output", "-o", help="Output file path (default: auto-generated)")
    p.add_argument("--full-page", action="store_true", help="Capture full scrollable page")
    p.set_defaults(func=cmd_screenshot)

    # extract
    p = subparsers.add_parser("extract", help="Extract text/data via CSS selectors")
    add_common_args(p)
    p.add_argument("--selector", "-s", required=True, help="CSS selector to extract from")
    p.add_argument("--all", action="store_true", help="Extract all matching elements (default: first only)")
    p.add_argument("--format", "-f", choices=["json", "csv", "text"], default="text", help="Output format")
    p.add_argument("--output", "-o", help="Save to file instead of stdout")
    p.set_defaults(func=cmd_extract)

    # fill
    p = subparsers.add_parser("fill", help="Fill form fields")
    add_common_args(p)
    p.add_argument("--fields", required=True, help='JSON object of selector:value pairs, e.g. \'{"#email": "test@test.com"}\'')
    p.add_argument("--submit-selector", help="CSS selector for submit button (click after filling)")
    p.add_argument("--screenshot-after", help="Take screenshot after submission, save to this path")
    p.set_defaults(func=cmd_fill)

    # click
    p = subparsers.add_parser("click", help="Click element and capture result")
    add_common_args(p)
    p.add_argument("--selector", "-s", required=True, help="CSS selector to click")
    p.add_argument("--wait-after", type=int, default=0, help="Additional wait in ms after clicking")
    p.add_argument("--output", "-o", help="Screenshot the result page to this path")
    p.set_defaults(func=cmd_click)

    # pdf
    p = subparsers.add_parser("pdf", help="Generate PDF from web page")
    add_common_args(p)
    p.add_argument("--output", "-o", help="Output PDF path (default: auto-generated)")
    p.add_argument("--format", choices=["A4", "Letter"], default="A4", help="Page format")
    p.add_argument("--landscape", action="store_true", help="Landscape orientation")
    p.set_defaults(func=cmd_pdf)

    # youtube
    p = subparsers.add_parser("youtube", help="Extract YouTube video content")
    p.add_argument("url", help="YouTube URL or video ID")
    p.add_argument("--format", "-f", choices=["markdown", "json", "text"], default="markdown", help="Output format")
    p.add_argument("--output", "-o", help="Save to file instead of stdout")
    p.add_argument("--proxy", help="HTTP/SOCKS proxy URL (e.g., http://user:pass@host:port)")
    p.add_argument("--cookies", help="Path to Netscape-format cookie file")
    p.add_argument(
        "--browser",
        default="brave:ClaudeCode",
        help=(
            "Browser to extract YouTube cookies from (format: BROWSER[:PROFILE]). "
            "Default: brave:ClaudeCode (cross-platform, yt-dlp-native). "
            "Pass 'none' to disable. Overridden by --cookies if both are supplied."
        ),
    )
    p.set_defaults(func=cmd_youtube)

    # batch-screenshots
    p = subparsers.add_parser("batch-screenshots", help="Screenshot multiple URLs")
    p.add_argument("urls", help="Comma-separated URLs or path to file with one URL per line")
    p.add_argument("--output-dir", help="Output directory (default: outputs/browser/batch/)")
    p.add_argument("--headed", action="store_true", help="Run with visible browser")
    p.add_argument("--device", help="Emulate device")
    p.add_argument("--timeout", type=int, default=30000, help="Timeout per page in ms")
    p.add_argument("--wait-for", dest="wait_for", help="Wait strategy")
    p.add_argument("--full-page", action="store_true", help="Full page screenshots")
    p.add_argument("--cookies-json", dest="cookies_json", help="Path to cookies JSON file (default: auto-detect outputs/browser/cookies.json)")
    p.set_defaults(func=cmd_batch_screenshots)

    # monitor
    p = subparsers.add_parser("monitor", help="Check URL status and capture state")
    add_common_args(p)
    p.add_argument("--check-selector", help="CSS selector to verify exists on page")
    p.add_argument("--expected-text", help="Expected text within the selector")
    p.add_argument("--output", "-o", help="Screenshot output path")
    p.set_defaults(func=cmd_monitor)

    # execute
    p = subparsers.add_parser("execute", help="Run a custom Playwright Python script")
    p.add_argument("script", help="Path to Python script")
    p.add_argument("--timeout", type=int, default=60000, help="Script timeout in ms (default: 60000)")
    p.set_defaults(func=cmd_execute)

    return parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
