#!/usr/bin/env python3
"""The exemplar capture pair: fault isolation that did not isolate.

`scripts/capture-design-exemplars.py` and its `-retry.py` sibling each wrap
every target in a `try/except` so one bad site cannot lose the others. Both had
the same hole, in the same place: `browser.new_context()` and `ctx.new_page()`
sat ABOVE that `try`. A browser that crashed or disconnected mid-run raised
straight past the handler, and in the main script `asyncio.gather` was called
without `return_exceptions=True`, so one target's infrastructure failure
cancelled the other 29, skipped `browser.close()` (leaked headless Chromium),
and wrote no manifest at all.

Three more silent-failure paths in the retry script, each ending in exit 0:

* `OUTPUT_DIR` was never created, so on a clean checkout every screenshot
  failed into `result["error"]` and all three retries printed ERR;
* `if MANIFEST_PATH.exists():` had no `else`, so with no manifest the script
  captured everything and then wrote nothing at all;
* `json.loads` of an existing manifest was unguarded, so a truncated file
  raised AFTER every capture had run and discarded the lot.

And one wrong constant with no failure at all: `TARGETS` shipped
`https://linear-status.com`, which is not Linear's status page. It captured
fine and was filed under `excellent-status` as a design exemplar. The retry
script has carried the correct URL, and a comment saying the original was
wrong, since it was written.

Found by the 2026-08-23 engine audit, shard `scripts-03-p3`. Fixed 2026-08-24.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MAIN_SRC = (ROOT / "scripts" / "capture-design-exemplars.py").read_text(encoding="utf-8")
RETRY_SRC = (ROOT / "scripts" / "capture-design-exemplars-retry.py").read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """The source with `#` comments removed.

    The tests below assert that a wrong URL, a disabled TLS flag and a false
    comment are GONE, and the fix for each explains itself in a comment that
    quotes the thing it removed. Scanning the raw file made every one of those
    assertions fail on its own explanation — the same trap that bit the app.js
    pass on 2026-08-24. Only code is evidence of behaviour.
    """
    out = []
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if "  # " in line:
            line = line.split("  # ", 1)[0]
        out.append(line)
    return "\n".join(out)


MAIN_CODE = _code_only(MAIN_SRC)
RETRY_CODE = _code_only(RETRY_SRC)


def test_the_comment_stripper_keeps_the_code():
    """Guard the premise: a stripper that ate everything passes every scan."""
    assert "def capture_one" in MAIN_CODE and "TARGETS = [" in MAIN_CODE
    assert "def capture_one" in RETRY_CODE and "RETRIES = [" in RETRY_CODE
    assert "# linear-status.com is NOT" not in MAIN_CODE


def _load(name: str, filename: str):
    """Import a kebab-case script by path.

    Both used to create OUTPUT_DIR at import. That module-level `mkdir` wrote
    into the engine clone on any checkout with no private data overlay, and both
    scripts now do it from `main()` via `prepare_output_dir()` instead.
    """
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def main_mod():
    return _load("cap_main", "capture-design-exemplars.py")


@pytest.fixture(scope="module")
def retry_mod():
    return _load("cap_retry", "capture-design-exemplars-retry.py")


# ---------------------------------------------------------------------------
# Fakes: a browser that fails where the real one fails
# ---------------------------------------------------------------------------

class _FakeCtx:
    def __init__(self, page_raises=False, close_raises=False):
        self._page_raises = page_raises
        self._close_raises = close_raises
        self.closed = False

    async def new_page(self):
        if self._page_raises:
            raise RuntimeError("target page crashed")
        return _FakePage()

    async def close(self):
        self.closed = True
        if self._close_raises:
            raise RuntimeError("browser already gone")


class _FakePage:
    async def goto(self, *a, **k):
        return None

    async def wait_for_load_state(self, *a, **k):
        return None

    async def wait_for_timeout(self, *a, **k):
        return None

    async def title(self):
        return "A page"

    async def screenshot(self, path, **k):
        Path(path).write_bytes(b"png")


class _FakeBrowser:
    """Fails `new_context` on the Nth call, like a browser that died mid-run."""

    def __init__(self, fail_on=None, page_raises=False, close_raises=False):
        self.fail_on = fail_on
        self.calls = 0
        self.closed = False
        self._page_raises = page_raises
        self._close_raises = close_raises

    async def new_context(self, **kwargs):
        self.calls += 1
        if self.fail_on is not None and self.calls == self.fail_on:
            raise RuntimeError("browser disconnected")
        return _FakeCtx(self._page_raises, self._close_raises)

    async def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# capture-design-exemplars.py
# ---------------------------------------------------------------------------

def test_a_dead_browser_becomes_an_error_row_not_an_escape(main_mod, tmp_path,
                                                           monkeypatch):
    monkeypatch.setattr(main_mod, "OUTPUT_DIR", tmp_path)
    browser = _FakeBrowser(fail_on=1)
    sem = asyncio.Semaphore(1)
    result = asyncio.run(
        main_mod.capture_one(browser, sem, "linear", "https://linear.app",
                             "excellent-product", 0))
    assert isinstance(result, dict), (
        "the exception escaped capture_one; with a bare gather that cancels "
        "every other target in flight"
    )
    assert result["error"] and "disconnected" in result["error"]


def test_a_page_that_cannot_open_still_closes_its_context(main_mod, tmp_path,
                                                          monkeypatch):
    """`new_page` failing after `new_context` succeeded used to leak the ctx."""
    monkeypatch.setattr(main_mod, "OUTPUT_DIR", tmp_path)
    seen = []

    class _Tracking(_FakeBrowser):
        async def new_context(self, **kwargs):
            ctx = _FakeCtx(page_raises=True)
            seen.append(ctx)
            return ctx

    result = asyncio.run(
        main_mod.capture_one(_Tracking(), asyncio.Semaphore(1), "x", "u", "c", 0))
    assert result["error"]
    assert seen and seen[0].closed, "the context leaked when new_page raised"


def test_a_close_that_raises_does_not_replace_a_good_result(main_mod, tmp_path,
                                                            monkeypatch):
    """A `finally` exception used to overwrite a capture that had succeeded."""
    monkeypatch.setattr(main_mod, "OUTPUT_DIR", tmp_path)
    browser = _FakeBrowser(close_raises=True)
    result = asyncio.run(
        main_mod.capture_one(browser, asyncio.Semaphore(1), "linear",
                             "https://linear.app", "excellent-product", 0))
    assert result["error"] is None, (
        "the capture succeeded and the context close failed; the close must "
        "not be able to lose the result"
    )
    assert result["above_fold"]


class _FakePlaywright:
    """Stands in for `async_playwright()`; hands out one `_FakeBrowser`."""

    def __init__(self, browser):
        self.browser = browser

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def chromium(self):
        outer = self

        class _C:
            async def launch(self, **k):
                return outer.browser
        return _C()


def test_one_escaping_capture_does_not_lose_the_other_targets(main_mod, tmp_path,
                                                              monkeypatch):
    """The whole shape, end to end: a bare `gather` cancelled the siblings,
    skipped `browser.close()` and wrote no manifest; a filtered result list
    would instead make the failure vanish from the report."""
    monkeypatch.setattr(main_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(main_mod, "CONCURRENCY", 3)
    monkeypatch.setattr(main_mod, "TARGETS", [
        ("a", "https://a", "cat", 0),
        ("boom", "https://b", "cat", 0),
        ("c", "https://c", "cat", 0),
    ])
    monkeypatch.setattr(main_mod, "_ensure_playwright", lambda: None)
    browser = _FakeBrowser()
    monkeypatch.setattr(main_mod, "async_playwright", _FakePlaywright(browser))

    async def flaky(browser_, sem, slug, url, cat, settle):
        async with sem:
            if slug == "boom":
                raise RuntimeError("browser disconnected")
            return {"slug": slug, "url": url, "category": cat, "above_fold": "p",
                    "full_page": "p", "title": slug, "error": None}

    monkeypatch.setattr(main_mod, "capture_one", flaky)
    asyncio.run(main_mod.main())

    assert browser.closed, "browser.close() was skipped, leaking headless Chromium"
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    slugs = [r["slug"] for r in manifest["results"]]
    assert slugs == ["a", "boom", "c"], (
        f"got {slugs}: a target that raised must still appear as an error row, "
        "in position, or the report silently under-counts the run"
    )
    assert manifest["total"] == 3
    assert manifest["ok"] == 2 and manifest["errors"] == 1
    by_slug = {r["slug"]: r for r in manifest["results"]}
    assert "RuntimeError" in by_slug["boom"]["error"]


def test_an_escaped_exception_still_gets_a_row(main_mod):
    """`return_exceptions=True` must not turn a failure into an absence."""
    row = main_mod._crashed(("slug", "https://u", "cat", 2500),
                            RuntimeError("boom"))
    assert row["slug"] == "slug"
    assert "RuntimeError" in row["error"]


def test_tls_verification_is_on_for_both_scripts():
    for name, src in (("capture-design-exemplars.py", MAIN_CODE),
                      ("capture-design-exemplars-retry.py", RETRY_CODE)):
        assert "ignore_https_errors" not in src, (
            f"{name} disables TLS validation while carrying a hand-maintained "
            "URL list; a lapsed domain with a bad certificate would be captured "
            "into the research corpus with no signal"
        )


def test_the_status_page_url_is_linears_own():
    assert "linear-status.com" not in MAIN_CODE, (
        "linear-status.com is not Linear's status page; it captured "
        "successfully and was filed as an excellent-status exemplar"
    )
    assert "https://linear.app/status" in MAIN_CODE


def test_no_handler_calls_every_failure_a_timeout():
    """The blanket `except Exception` logged 'networkidle wait timed out' for a
    closed page, a destroyed navigation and a dead context alike."""
    for name, src in (("capture-design-exemplars.py", MAIN_CODE),
                      ("capture-design-exemplars-retry.py", RETRY_CODE)):
        assert "networkidle wait timed out" not in src, (
            f"{name} names one cause for every exception the settle wait can "
            "raise, which is a claim the handler cannot make"
        )


def test_the_main_manifest_is_written_atomically():
    assert "atomic_write_text(manifest_path" in MAIN_CODE, (
        "the retry script reads this file; a truncated write made its "
        "json.loads raise after every retry capture had run"
    )


# ---------------------------------------------------------------------------
# capture-design-exemplars-retry.py
# ---------------------------------------------------------------------------

def test_the_retry_script_creates_its_output_directory(retry_mod, tmp_path,
                                                       monkeypatch):
    """The directory still gets made, just not at import.

    This read the source text for `OUTPUT_DIR.mkdir(...)` until 2026-08-27,
    which passed whether or not anything ever called it. It now runs the
    function, because the reason for the directory is unchanged: without it
    every screenshot failed with a directory-not-found error, the error was
    swallowed into result['error'], and the script exited 0.
    """
    target = tmp_path / "exemplars"
    monkeypatch.setattr(retry_mod, "OUTPUT_DIR", target)
    retry_mod.prepare_output_dir()
    assert target.is_dir(), (
        "prepare_output_dir() did not create the capture directory; on a fresh "
        "checkout every screenshot then fails with directory-not-found, the "
        "error is swallowed into result['error'], and the script exits 0"
    )


def test_a_missing_manifest_does_not_discard_the_work(retry_mod, tmp_path,
                                                      monkeypatch, capsys):
    monkeypatch.setattr(retry_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(retry_mod, "MANIFEST_PATH", tmp_path / "manifest.json")
    fallback = tmp_path / "retry-results.json"
    monkeypatch.setattr(retry_mod, "FALLBACK_RESULTS_PATH", fallback)
    monkeypatch.setattr(retry_mod, "_ensure_playwright", lambda: None)
    monkeypatch.setattr(retry_mod, "RETRIES",
                        [("raycast", "https://www.raycast.com", "p", 0, False)])
    _run_retry_main(retry_mod, monkeypatch)

    assert fallback.exists(), (
        "with no manifest the script captured everything and wrote nothing: no "
        "manifest, no fallback, no summary, exit 0"
    )
    saved = json.loads(fallback.read_text())
    assert saved["results"][0]["slug"] == "raycast"
    assert "No manifest" in capsys.readouterr().out


def test_a_corrupt_manifest_is_refused_not_crashed_into(retry_mod, tmp_path,
                                                        monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"results": [')  # the truncated-write outcome
    monkeypatch.setattr(retry_mod, "MANIFEST_PATH", manifest)
    assert retry_mod._load_manifest() is None, (
        "json.loads raised AFTER every capture had run, so the whole retry was "
        "thrown away over a file this script was about to rewrite"
    )
    assert manifest.read_text() == '{"results": [', "the old manifest was clobbered"


def test_a_manifest_of_the_wrong_shape_is_refused(retry_mod, tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"results": "not a list"}')
    monkeypatch.setattr(retry_mod, "MANIFEST_PATH", manifest)
    assert retry_mod._load_manifest() is None


def test_a_good_manifest_is_loaded(retry_mod, tmp_path, monkeypatch):
    """Anchor: a loader that refuses everything would pass the two above."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"results": [], "total": 0}))
    monkeypatch.setattr(retry_mod, "MANIFEST_PATH", manifest)
    assert retry_mod._load_manifest() == {"results": [], "total": 0}


def _run_retry_main(retry_mod, monkeypatch):
    """Drive `main` against the fake browser."""
    class _FakePW:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        @property
        def chromium(self):
            outer = self

            class _C:
                async def launch(self, **k):
                    outer.browser = _FakeBrowser()
                    return outer.browser
            return _C()

    monkeypatch.setattr(retry_mod, "async_playwright", lambda: _FakePW())
    asyncio.run(retry_mod.main())


def test_the_merge_recomputes_total(retry_mod, tmp_path, monkeypatch):
    """`total` kept its old value while ok/errors were recomputed, so a merge
    that ADDED a slug printed an inconsistent ok/total and persisted a total
    that no longer equalled len(results)."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(
        {"results": [{"slug": "vercel", "error": None}], "total": 1,
         "ok": 1, "errors": 0}))
    monkeypatch.setattr(retry_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(retry_mod, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(retry_mod, "_ensure_playwright", lambda: None)
    monkeypatch.setattr(retry_mod, "RETRIES",
                        [("raycast", "https://www.raycast.com", "p", 0, False)])
    _run_retry_main(retry_mod, monkeypatch)

    out = json.loads(manifest.read_text())
    assert out["total"] == len(out["results"]) == 2


def test_a_manifest_with_no_total_key_does_not_crash(retry_mod, tmp_path,
                                                     monkeypatch):
    """The summary line raised KeyError AFTER the file had been overwritten."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"results": []}))
    monkeypatch.setattr(retry_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(retry_mod, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(retry_mod, "_ensure_playwright", lambda: None)
    monkeypatch.setattr(retry_mod, "RETRIES",
                        [("raycast", "https://www.raycast.com", "p", 0, False)])
    _run_retry_main(retry_mod, monkeypatch)
    assert json.loads(manifest.read_text())["total"] == 1


def test_a_partial_capture_is_visible(retry_mod, tmp_path, monkeypatch, capsys):
    """`full_page_error` was written to a key absent from the schema and read by
    nothing: the merge and both counts inspect only `error`, so a retry that
    lost its full-page shot was recorded as fully OK."""
    monkeypatch.setattr(retry_mod, "OUTPUT_DIR", tmp_path)

    class _HalfPage(_FakePage):
        async def screenshot(self, path, **k):
            if k.get("full_page"):
                raise RuntimeError("full page timed out")
            Path(path).write_bytes(b"png")

    class _HalfCtx(_FakeCtx):
        async def new_page(self):
            return _HalfPage()

    class _HalfBrowser(_FakeBrowser):
        async def new_context(self, **kwargs):
            return _HalfCtx()

    result = asyncio.run(retry_mod.capture_one(
        _HalfBrowser(), "mercury", "https://mercury.com", "p", 0, True))
    assert "full_page_error" in result, "the key must be in the schema"
    assert result["full_page_error"], "the partial failure was recorded nowhere"
    assert "full-page failed" in capsys.readouterr().err


def test_a_successful_retry_still_carries_the_partial_key(retry_mod, tmp_path,
                                                          monkeypatch):
    """The key has to be in the SCHEMA, not only set on the failure path.

    `full_page_error` was written into a key absent from the initialiser, so a
    consumer reading `r["full_page_error"]` on any other row raised KeyError,
    and `r.get(...)` could not tell "no partial failure" from "this producer
    does not report them".
    """
    monkeypatch.setattr(retry_mod, "OUTPUT_DIR", tmp_path)
    result = asyncio.run(retry_mod.capture_one(
        _FakeBrowser(), "raycast", "https://www.raycast.com", "p", 0, True))
    assert result["error"] is None
    assert "full_page_error" in result and result["full_page_error"] is None


def test_a_dead_browser_is_an_error_row_in_the_retry_too(retry_mod, tmp_path,
                                                         monkeypatch):
    """Same hole as the main script: `new_context` sat above the try, so the
    exception aborted the sequential loop, leaked the browser, and threw away
    the retries that had already succeeded."""
    monkeypatch.setattr(retry_mod, "OUTPUT_DIR", tmp_path)
    result = asyncio.run(retry_mod.capture_one(
        _FakeBrowser(fail_on=1), "raycast", "https://www.raycast.com", "p", 0, False))
    assert isinstance(result, dict) and result["error"]
    assert "disconnected" in result["error"]


def test_a_retry_page_that_cannot_open_still_closes_its_context(retry_mod,
                                                                tmp_path,
                                                                monkeypatch):
    monkeypatch.setattr(retry_mod, "OUTPUT_DIR", tmp_path)
    seen = []

    class _Tracking(_FakeBrowser):
        async def new_context(self, **kwargs):
            ctx = _FakeCtx(page_raises=True)
            seen.append(ctx)
            return ctx

    result = asyncio.run(retry_mod.capture_one(
        _Tracking(), "raycast", "https://www.raycast.com", "p", 0, False))
    assert result["error"]
    assert seen and seen[0].closed, "the context leaked when new_page raised"


def test_the_retry_title_has_the_same_fallback_as_the_main_script(retry_mod,
                                                                  tmp_path,
                                                                  monkeypatch):
    """A crashed renderer marked the WHOLE capture failed, even though the
    screenshots it was retried for were obtainable."""
    monkeypatch.setattr(retry_mod, "OUTPUT_DIR", tmp_path)

    class _NoTitlePage(_FakePage):
        async def title(self):
            raise RuntimeError("renderer crashed")

    class _NoTitleCtx(_FakeCtx):
        async def new_page(self):
            return _NoTitlePage()

    class _B(_FakeBrowser):
        async def new_context(self, **kwargs):
            return _NoTitleCtx()

    result = asyncio.run(retry_mod.capture_one(
        _B(), "raycast", "https://www.raycast.com", "p", 0, False))
    assert result["error"] is None
    assert result["title"] == "(no title)"
    assert result["above_fold"]


def test_each_retry_tuple_matches_the_signature_it_is_unpacked_into(retry_mod):
    """The comment above RETRIES said mercury got a "user-agent override".

    There is no per-target UA mechanism: the UA is set once in `new_context`
    for all three, and the 5th tuple element is `full_page`. Anyone tuning from
    that comment changed the wrong field. Scanning for the wrong sentence is
    not the test — a comment is not evidence, and the fix's own explanation
    quotes the phrase it removed. What IS checkable is the thing the comment
    got wrong: what the 5th element actually is.
    """
    import inspect
    params = list(inspect.signature(retry_mod.capture_one).parameters)
    assert params[-1] == "full_page", (
        f"capture_one's last parameter is {params[-1]!r}; the RETRIES tuples "
        "are unpacked positionally into it"
    )
    # browser + the five tuple fields
    assert len(params) == 6
    for entry in retry_mod.RETRIES:
        assert len(entry) == 5, f"{entry[0]}: {len(entry)} fields, not 5"
        assert isinstance(entry[4], bool), (
            f"{entry[0]}: the 5th field is {entry[4]!r}, and it is passed as "
            "full_page — not as any kind of override string"
        )
