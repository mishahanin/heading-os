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

from scripts.utils.atomic import atomic_write_text  # noqa: E402
from scripts.utils.colors import CYAN, GREEN, RED, RESET  # noqa: E402
from scripts.utils.workspace import display_path, get_outputs_dir  # noqa: E402

# playwright is bound lazily (F-2.1: import stays pure).
async_playwright = None


def _ensure_playwright():
    global async_playwright
    if async_playwright is not None:
        return
    from scripts.utils.optdeps import require
    require("playwright", extra="browser")
    from playwright.async_api import async_playwright


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
    # linear-status.com is NOT Linear's status page. It was captured, it
    # succeeded, and it was filed in the manifest under `excellent-status` as a
    # design exemplar — a plausible-looking wrong artifact, which is worse than
    # a crash because nothing downstream can tell. The sibling retry script has
    # carried the correct URL and a comment saying so since it was written;
    # nobody fixed it at the source. Fixed 2026-08-24.
    ("status-linear", "https://linear.app/status", "excellent-status", 2500),
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


async def close_quietly(closeable, what):
    """Close a context or the browser without letting the close lose the run.

    `await ctx.close()` in a bare `finally` raises when the browser has already
    died, and a `finally`-block exception REPLACES the result that was built —
    so a capture that fully succeeded still took down the whole run. Never
    silent: the failure is printed, it just does not propagate.

    It took `(ctx, slug)` until 2026-08-24 and guarded contexts only. The
    browser's own close was left bare one level up, where the same dead browser
    raises past `main` BEFORE the manifest is written: every captured row still
    lived only in memory, so the crash this function exists to survive
    destroyed the whole run's output anyway.
    """
    if closeable is None:
        return
    try:
        await closeable.close()
    except Exception as exc:
        print(f"capture-design-exemplars: closing {what} failed: {exc}",
              file=sys.stderr)


async def capture_one(browser, semaphore, slug, url, category, settle_ms):
    async with semaphore:
        result = {
            "slug": slug,
            "url": url,
            "category": category,
            "above_fold": None,
            "full_page": None,
            "title": None,
            "error": None,
        }
        ctx = None
        try:
            # Inside the try. `new_context` and `new_page` sat ABOVE it until
            # 2026-08-24, so a browser that crashed or disconnected mid-run
            # raised straight out of this function — past the per-target
            # handler this whole structure exists to be — and, with a bare
            # `gather`, cancelled all 29 other captures, skipped
            # `browser.close()`, and wrote no manifest. One target's
            # infrastructure failure vaporised the run.
            # No `ignore_https_errors`. It was True, which turns off TLS
            # validation for every capture, and finding 4 above proves the URL
            # list does carry wrong domains: a lapsed or mistyped host serving
            # attacker-controlled content behind a bad certificate would have
            # been captured silently into the design-research corpus. Every
            # target here is a major site with a valid certificate, so a TLS
            # failure is a signal worth having, not a capture worth forcing.
            ctx = await browser.new_context(viewport=VIEWPORT)
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            try:
                await page.wait_for_load_state("networkidle", timeout=settle_ms + 3000)
            except Exception as exc:
                # Not necessarily a timeout: a closed page, a destroyed
                # navigation and a dead context all land here. Naming one cause
                # for every failure is a claim the handler cannot make.
                print(f"capture-design-exemplars: settle wait for {slug} ended "
                      f"early: {exc}", file=sys.stderr)
            await page.wait_for_timeout(settle_ms)
            try:
                result["title"] = (await page.title())[:80]
            except Exception as exc:
                print(f"capture-design-exemplars: {slug} title unreadable: {exc}",
                      file=sys.stderr)
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
            await close_quietly(ctx, f"the context for {slug}")
        return result


def _crashed(target, exc):
    """A result row for a capture whose exception escaped `capture_one`.

    With `return_exceptions=True` an escape is no longer fatal, but it must not
    become invisible either: an absent row would make `total` disagree with the
    target list and the failure would appear nowhere.
    """
    slug, url, category, _settle = target
    print(f"{RED}ERR{RESET} {slug:<20} escaped the handler: {exc}", file=sys.stderr)
    return {
        "slug": slug, "url": url, "category": category,
        "above_fold": None, "full_page": None, "title": None,
        "error": f"{type(exc).__name__}: {exc}"[:200],
    }


async def main():
    _ensure_playwright()
    print(f"{CYAN}Capturing {len(TARGETS)} targets at {VIEWPORT['width']}x{VIEWPORT['height']}, concurrency={CONCURRENCY}{RESET}")
    print(f"Output dir: {OUTPUT_DIR}\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        semaphore = asyncio.Semaphore(CONCURRENCY)
        tasks = [capture_one(browser, semaphore, slug, url, cat, wait) for slug, url, cat, wait in TARGETS]
        # `return_exceptions=True`. A bare gather cancels every sibling on the
        # first exception, so one target's infrastructure failure lost all 29
        # other captures AND skipped browser.close() below.
        raw = await asyncio.gather(*tasks, return_exceptions=True)
        # Guarded for the same reason the contexts are: a browser that died
        # mid-run raises on close, and that raise landed BEFORE the manifest
        # write below. Every row this gather just salvaged was thrown away by
        # the cleanup for the crash it survived.
        await close_quietly(browser, "the browser")

    results = [r if isinstance(r, dict) else _crashed(TARGETS[i], r)
               for i, r in enumerate(raw)]

    manifest = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "viewport": VIEWPORT,
        "total": len(results),
        "ok": sum(1 for r in results if not r["error"]),
        "errors": sum(1 for r in results if r["error"]),
        "results": results,
    }
    manifest_path = OUTPUT_DIR / "manifest.json"
    # Atomic: the retry script reads this file, and a truncated write made its
    # json.loads raise after every retry capture had already run.
    atomic_write_text(manifest_path, json.dumps(manifest, indent=2) + "\n")
    print(f"\n{GREEN}Done.{RESET} {manifest['ok']}/{manifest['total']} captured, {manifest['errors']} failed")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    asyncio.run(main())
