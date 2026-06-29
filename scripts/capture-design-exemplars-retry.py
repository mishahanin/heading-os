#!/usr/bin/env python3
"""Retry the 3 failed targets from capture-design-exemplars.py with tuned settings."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.venv import ensure_venv  # noqa: E402

ensure_venv()
from scripts.utils.colors import CYAN, GREEN, RED, RESET  # noqa: E402
from scripts.utils.workspace import display_path, get_outputs_dir  # noqa: E402

from playwright.async_api import async_playwright

OUTPUT_DIR = get_outputs_dir() / "research" / "_drafts" / "exemplars"
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"

RETRIES = [
    # raycast had full-page screenshot timeout - skip full, take above-fold only
    ("raycast", "https://www.raycast.com", "excellent-product", 4000, False),
    # mercury hit anti-bot or slow load - try longer + user-agent override
    ("mercury", "https://mercury.com", "excellent-product", 5000, True),
    # status-linear URL was wrong - try the actual one
    ("status-linear", "https://linear.app/status", "excellent-status", 3000, True),
]

VIEWPORT = {"width": 1440, "height": 900}
NAV_TIMEOUT = 45000


async def capture_one(browser, slug, url, category, settle_ms, full_page):
    ctx = await browser.new_context(
        viewport=VIEWPORT,
        ignore_https_errors=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )
    page = await ctx.new_page()
    result = {"slug": slug, "url": url, "category": category, "above_fold": None, "full_page": None, "title": None, "error": None}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        try:
            await page.wait_for_load_state("networkidle", timeout=settle_ms + 5000)
        except Exception as exc:
            print(f"capture-design-exemplars-retry: networkidle wait timed out: {exc}", file=sys.stderr)
        await page.wait_for_timeout(settle_ms)
        result["title"] = (await page.title())[:80]
        above_path = OUTPUT_DIR / f"{slug}-above.png"
        await page.screenshot(path=str(above_path), full_page=False, timeout=20000)
        result["above_fold"] = display_path(above_path)
        if full_page:
            full_path = OUTPUT_DIR / f"{slug}-full.png"
            try:
                await page.screenshot(path=str(full_path), full_page=True, timeout=25000)
                result["full_page"] = display_path(full_path)
            except Exception as e:
                result["full_page_error"] = str(e)[:100]
        print(f"{GREEN}OK {RESET}{slug:<20} {result['title']}")
    except Exception as e:
        result["error"] = str(e)[:200]
        print(f"{RED}ERR{RESET} {slug:<20} {e}")
    finally:
        await ctx.close()
    return result


async def main():
    print(f"{CYAN}Retrying {len(RETRIES)} targets with tuned settings{RESET}\n")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        results = []
        for slug, url, cat, settle, full in RETRIES:
            r = await capture_one(browser, slug, url, cat, settle, full)
            results.append(r)
        await browser.close()

    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        slugs_updated = {r["slug"] for r in results if not r["error"]}
        manifest["results"] = [r for r in manifest["results"] if r["slug"] not in slugs_updated] + [r for r in results if not r["error"]]
        manifest["retried_at_utc"] = datetime.now(timezone.utc).isoformat()
        manifest["ok"] = sum(1 for r in manifest["results"] if not r.get("error"))
        manifest["errors"] = sum(1 for r in manifest["results"] if r.get("error"))
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"\n{GREEN}Manifest updated.{RESET} {manifest['ok']}/{manifest['total']} captured")


if __name__ == "__main__":
    asyncio.run(main())
