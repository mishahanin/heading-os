#!/usr/bin/env python3
"""Retry the 3 failed targets from capture-design-exemplars.py with tuned settings."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.venv_guard import ensure_venv  # noqa: E402

ensure_venv()
from scripts.utils.atomic import atomic_write_text  # noqa: E402
from scripts.utils.colors import CYAN, GREEN, RED, RESET, YELLOW  # noqa: E402
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
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"
# The sibling script creates this at import; this one did not. On a fresh
# checkout, or after outputs were cleaned, every `page.screenshot(path=...)`
# failed with a directory-not-found error, that error was swallowed into
# `result["error"]`, all three retries printed ERR, and the script exited 0.
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
# Where retry results go when there is no manifest to merge into. Without this
# the script captured everything and then wrote NOTHING: no manifest, no
# fallback, no summary, exit 0 — a completely silent successful no-op.
FALLBACK_RESULTS_PATH = OUTPUT_DIR / "retry-results.json"

# (slug, url, category, settle_ms, full_page)
RETRIES = [
    # raycast had a full-page screenshot timeout - skip full, above-fold only
    ("raycast", "https://www.raycast.com", "excellent-product", 4000, False),
    # mercury hit anti-bot or a slow load - longer settle, and take the full
    # page. (This comment said "user-agent override" until 2026-08-24; there is
    # no per-target UA mechanism, the UA is set once in new_context for all
    # three, and the 5th element is full_page. Anyone tuning from the comment
    # would have changed the wrong field.)
    ("mercury", "https://mercury.com", "excellent-product", 5000, True),
    # status-linear URL was wrong - this is the actual one. Fixed at the source
    # in capture-design-exemplars.py on 2026-08-24; this stays as the retry.
    ("status-linear", "https://linear.app/status", "excellent-status", 3000, True),
]

VIEWPORT = {"width": 1440, "height": 900}
NAV_TIMEOUT = 45000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_manifest():
    """The existing manifest, or None when there is nothing usable to merge.

    A corrupt or truncated manifest raised JSONDecodeError AFTER every capture
    had run, so the whole retry was discarded over a file this script was about
    to rewrite anyway. Refuse to merge into it, keep it, and fall back.
    """
    if not MANIFEST_PATH.exists():
        return None
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"{YELLOW}Existing manifest is unreadable ({exc}); leaving it in "
              f"place.{RESET}", file=sys.stderr)
        return None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("results"), list):
        print(f"{YELLOW}Existing manifest has an unexpected shape; leaving it in "
              f"place.{RESET}", file=sys.stderr)
        return None
    return manifest


async def close_quietly(closeable, what):
    """Close a context or the browser without letting the close lose the run.

    A raise inside `finally` REPLACES the result that was built, so a capture
    that fully succeeded still aborted `main`, skipped `browser.close()` and
    left the manifest untouched. Never silent, just not fatal.

    It took `(ctx, slug)` until 2026-08-24 and guarded contexts only, while the
    browser's own close stayed bare one level up — the same dead browser
    raising there aborted `main` before the merge, which is the outcome this
    docstring already named.
    """
    if closeable is None:
        return
    try:
        await closeable.close()
    except Exception as exc:
        print(f"capture-design-exemplars-retry: closing {what} failed: {exc}",
              file=sys.stderr)


async def capture_one(browser, slug, url, category, settle_ms, full_page):
    result = {"slug": slug, "url": url, "category": category, "above_fold": None,
              "full_page": None, "full_page_error": None, "title": None,
              "error": None}
    ctx = None
    try:
        # Inside the try, for the same reason as the sibling script: these two
        # calls sat above it, so a dead browser raised past the per-target
        # handler, aborted the loop, leaked the Chromium process and threw away
        # the retries that had already succeeded.
        # No `ignore_https_errors` (removed 2026-08-24, same reason as the
        # sibling script): TLS validation off plus a hand-maintained URL list
        # means a wrong domain with a bad certificate lands in the corpus with
        # no signal at all.
        ctx = await browser.new_context(
            viewport=VIEWPORT,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        try:
            await page.wait_for_load_state("networkidle", timeout=settle_ms + 5000)
        except Exception as exc:
            # A closed page, a destroyed navigation and a dead context all land
            # here; calling every one of them a timeout is a claim this handler
            # cannot make.
            print(f"capture-design-exemplars-retry: settle wait for {slug} ended "
                  f"early: {exc}", file=sys.stderr)
        await page.wait_for_timeout(settle_ms)
        try:
            result["title"] = (await page.title())[:80]
        except Exception as exc:
            # The sibling script has always had this fallback. Without it a
            # crashed renderer marked the WHOLE capture failed, even when the
            # screenshots it was retried for were obtainable.
            print(f"capture-design-exemplars-retry: {slug} title unreadable: "
                  f"{exc}", file=sys.stderr)
            result["title"] = "(no title)"
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
                print(f"{YELLOW}PART{RESET} {slug:<20} full-page failed: {e}",
                      file=sys.stderr)
        print(f"{GREEN}OK {RESET}{slug:<20} {result['title']}")
    except Exception as e:
        result["error"] = str(e)[:200]
        print(f"{RED}ERR{RESET} {slug:<20} {e}")
    finally:
        await close_quietly(ctx, f"the context for {slug}")
    return result


def _replaces(new: dict, old: dict | None) -> bool:
    """Is `new` at least as complete as the manifest row it would overwrite?

    The merge counted a retry as a success on `error is None` alone. But
    `capture_one` also finishes with `full_page_error` set and `full_page` left
    None — the PART row printed above — and such a row used to DELETE a
    complete old one. The manifest lost its only reference to a full-page
    screenshot that is still on disk, and `ok`/`total` were recomputed as
    though nothing had gone.
    """
    if new.get("error"):
        return False
    if old is None:
        return True
    return bool(new.get("full_page")) or not old.get("full_page")


async def main():
    _ensure_playwright()
    print(f"{CYAN}Retrying {len(RETRIES)} targets with tuned settings{RESET}\n")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        results = []
        for slug, url, cat, settle, full in RETRIES:
            r = await capture_one(browser, slug, url, cat, settle, full)
            results.append(r)
        # Guarded for the same reason the contexts are: a raise here aborted
        # `main` before the merge below, discarding every retry that had just
        # succeeded.
        await close_quietly(browser, "the browser")

    partial = [r["slug"] for r in results if r.get("full_page_error")]
    if partial:
        print(f"{YELLOW}Above-fold only (full page failed):{RESET} "
              f"{', '.join(partial)}")

    manifest = _load_manifest()
    if manifest is None:
        # No manifest to merge into. Writing nothing here meant every retry
        # outcome existed only in stdout scrollback.
        atomic_write_text(FALLBACK_RESULTS_PATH,
                          json.dumps({"retried_at_utc": _now(), "results": results},
                                     indent=2) + "\n")
        print(f"\n{YELLOW}No manifest at {display_path(MANIFEST_PATH)}.{RESET} "
              f"Results written to {display_path(FALLBACK_RESULTS_PATH)}; run "
              f"capture-design-exemplars.py to build the manifest.")
        return

    old_rows = {r["slug"]: r for r in manifest.get("results", [])}
    accepted = []
    downgrades = []
    for r in results:
        if _replaces(r, old_rows.get(r["slug"])):
            accepted.append(r)
        elif not r.get("error"):
            downgrades.append(r["slug"])
    if downgrades:
        print(f"{YELLOW}Kept the earlier full-page row for:{RESET} "
              f"{', '.join(downgrades)}")
    slugs_updated = {r["slug"] for r in accepted}
    manifest["results"] = [r for r in manifest.get("results", [])
                           if r["slug"] not in slugs_updated] + accepted
    manifest["retried_at_utc"] = _now()
    manifest["ok"] = sum(1 for r in manifest["results"] if not r.get("error"))
    manifest["errors"] = sum(1 for r in manifest["results"] if r.get("error"))
    # `total` was left at its old value while ok/errors were recomputed, so a
    # merge that ADDED a slug printed an inconsistent `ok/total` and persisted a
    # total that no longer equalled len(results). A manifest with no `total` key
    # at all raised KeyError on the summary line — after the file had already
    # been overwritten.
    manifest["total"] = len(manifest["results"])
    atomic_write_text(MANIFEST_PATH, json.dumps(manifest, indent=2) + "\n")
    print(f"\n{GREEN}Manifest updated.{RESET} "
          f"{manifest['ok']}/{manifest['total']} captured")


if __name__ == "__main__":
    asyncio.run(main())
