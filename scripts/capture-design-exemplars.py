#!/usr/bin/env python3
"""Batch-capture design exemplars via headless Playwright.

Research helper for the visual-design-discipline rule. Captures above-fold
(1440x900) and full-page screenshots of the design exemplar shelf plus
anti-pattern shelf. Outputs PNG files and a manifest JSON.

Usage:
    python scripts/capture-design-exemplars.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import CYAN, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.workspace import display_path, get_outputs_dir  # noqa: E402

try:
    from playwright.async_api import async_playwright
except ImportError:
    print(f"{RED}playwright not installed. Run: pip install playwright && playwright install chromium{RESET}")
    sys.exit(1)

OUTPUT_DIR = get_outputs_dir() / "research" / "_drafts" / "exemplars"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# (slug, url, category, settle_ms)
TARGETS = [
    # Excellent product shelf
    ("linear", "https://linear.app", "excellent-product", 2500),
    ("vercel", "https://vercel.com", "excellent-product", 2500),
    ("stripe", "https://stripe.com", "excellent-product", 2500),
    ("raycast", "https://www.raycast.com", "excellent-product", 2500),
    ("arc", "https://arc.net", "excellent-product", 2500),
    ("plausible-demo", "https://plausible.io/plausible.io", "excellent-product", 4000),
    ("posthog", "https://posthog.com", "excellent-product", 2500),
    ("resend", "https://resend.com", "excellent-product", 2500),
    ("notion-calendar", "https://www.notion.so/product/calendar", "excellent-product", 2500),
    ("superhuman", "https://superhuman.com", "excellent-product", 2500),
    ("mercury", "https://mercury.com", "excellent-product", 2500),
    ("figma", "https://www.figma.com", "excellent-product", 2500),
    ("framer", "https://www.framer.com", "excellent-product", 2500),
    # Status pages
    ("status-linear", "https://linear-status.com", "excellent-status", 2500),
    ("status-stripe", "https://status.stripe.com", "excellent-status", 2500),
    ("status-notion", "https://status.notion.so", "excellent-status", 2500),
    ("status-cloudflare", "https://www.cloudflarestatus.com", "excellent-status", 2500),
    # Design studios
    ("pentagram", "https://www.pentagram.com", "excellent-studio", 2500),
    ("andwalsh", "https://andwalsh.com", "excellent-studio", 2500),
    ("metalab", "https://www.metalab.com", "excellent-studio", 2500),
    ("sutherland", "https://www.studio-sutherland.co.uk", "excellent-studio", 2500),
    ("lusion", "https://lusion.co", "excellent-studio", 5000),
    ("active-theory", "https://activetheory.net", "excellent-studio", 5000),
    ("basement", "https://basement.studio", "excellent-studio", 3500),
    # Anti-patterns / template fatigue
    ("salesforce", "https://www.salesforce.com", "anti-pattern", 2500),
    ("sap", "https://www.sap.com", "anti-pattern", 2500),
    ("servicenow", "https://www.servicenow.com", "anti-pattern", 2500),
    ("material3", "https://m3.material.io", "anti-pattern", 2500),
    ("gamma", "https://gamma.app", "anti-pattern", 2500),
    ("tabler", "https://tabler.io", "anti-pattern", 2500),
]

VIEWPORT = {"width": 1440, "height": 900}
NAV_TIMEOUT = 20000
CONCURRENCY = 4


async def capture_one(browser, semaphore, slug, url, category, settle_ms):
    async with semaphore:
        ctx = await browser.new_context(viewport=VIEWPORT, ignore_https_errors=True)
        page = await ctx.new_page()
        result = {
            "slug": slug,
            "url": url,
            "category": category,
            "above_fold": None,
            "full_page": None,
            "title": None,
            "error": None,
        }
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            try:
                await page.wait_for_load_state("networkidle", timeout=settle_ms + 3000)
            except Exception as exc:
                print(f"capture-design-exemplars: networkidle wait timed out: {exc}", file=sys.stderr)
            await page.wait_for_timeout(settle_ms)
            try:
                result["title"] = (await page.title())[:80]
            except Exception:
                result["title"] = "(no title)"
            above_path = OUTPUT_DIR / f"{slug}-above.png"
            await page.screenshot(path=str(above_path), full_page=False)
            result["above_fold"] = display_path(above_path)
            full_path = OUTPUT_DIR / f"{slug}-full.png"
            await page.screenshot(path=str(full_path), full_page=True, timeout=15000)
            result["full_page"] = display_path(full_path)
            print(f"{GREEN}OK {RESET}{slug:<20} {result['title']}")
        except Exception as e:
            result["error"] = str(e)[:200]
            print(f"{RED}ERR{RESET} {slug:<20} {e}")
        finally:
            await ctx.close()
        return result


async def main():
    print(f"{CYAN}Capturing {len(TARGETS)} targets at {VIEWPORT['width']}x{VIEWPORT['height']}, concurrency={CONCURRENCY}{RESET}")
    print(f"Output dir: {OUTPUT_DIR}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        semaphore = asyncio.Semaphore(CONCURRENCY)
        tasks = [capture_one(browser, semaphore, slug, url, cat, wait) for slug, url, cat, wait in TARGETS]
        results = await asyncio.gather(*tasks)
        await browser.close()

    manifest = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "viewport": VIEWPORT,
        "total": len(results),
        "ok": sum(1 for r in results if not r["error"]),
        "errors": sum(1 for r in results if r["error"]),
        "results": results,
    }
    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n{GREEN}Done.{RESET} {manifest['ok']}/{manifest['total']} captured, {manifest['errors']} failed")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    asyncio.run(main())
