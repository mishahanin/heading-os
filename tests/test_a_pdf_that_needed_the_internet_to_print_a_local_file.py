#!/usr/bin/env python3
"""Shard 08-p2 finding 5: the newsletter PDF required a reachable font CDN.

`generate_pdf` opens a LOCAL file whose CSS is already inlined, so the page is
fully laid out with no network at all. It then called
`page.wait_for_load_state("networkidle")` unbounded. The generated HTML links
`https://fonts.googleapis.com/css2?...`, and `networkidle` waits for ALL
network to settle, so on an offline or firewalled host that one request never
resolved: the wait ran to Playwright's ~30 second navigation default, raised,
and the blanket `except Exception` below turned it into
"Warning: PDF generation failed" with no PDF produced. The PDF is an artifact
the run exists to produce, and nothing in that warning named the remote font as
the cause.

The wait is now bounded at 5 seconds and a timeout is survivable: the page is
already laid out, the only loss is the webfont, and the operator is told which
of the two happened.

The double is a fake `playwright.sync_api`. Driving real Chromium here would
need a real network partition, which a test cannot arrange, and the thing under
test is this module's reaction to the timeout rather than Playwright's own
behaviour.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "newsletter_pdf_08p2", ROOT / "scripts" / "generate-newsletter-html.py")
gen = importlib.util.module_from_spec(_spec)
sys.modules["newsletter_pdf_08p2"] = gen
_spec.loader.exec_module(gen)


class _FakeTimeout(Exception):
    """Stands in for playwright.sync_api.TimeoutError."""


class _FakePage:
    def __init__(self, recorder, idle_raises):
        self._rec = recorder
        self._idle_raises = idle_raises

    def goto(self, url):
        self._rec["goto"] = url

    def wait_for_load_state(self, state, timeout=None):
        self._rec["wait"] = (state, timeout)
        if self._idle_raises:
            raise _FakeTimeout("Timeout 30000ms exceeded.")

    def evaluate(self, _script):
        return 800

    def pdf(self, path, **_kwargs):
        Path(path).write_bytes(b"%PDF-1.4 fake\n")
        self._rec["pdf"] = path


class _FakeBrowser:
    def __init__(self, recorder, idle_raises):
        self._rec = recorder
        self._idle_raises = idle_raises

    def new_page(self):
        return _FakePage(self._rec, self._idle_raises)

    def close(self):
        self._rec["closed"] = True


class _FakePlaywright:
    def __init__(self, recorder, idle_raises):
        self._rec = recorder
        self._idle_raises = idle_raises

    def __enter__(self):
        outer = self

        class _Chromium:
            def launch(self_inner, **_kw):
                return _FakeBrowser(outer._rec, outer._idle_raises)

        self.chromium = _Chromium()
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture()
def fake_playwright(monkeypatch):
    """Install a fake `playwright.sync_api`, returning the call recorder."""
    recorder: dict = {}

    def _install(idle_raises: bool):
        mod = types.ModuleType("playwright")
        sync_api = types.ModuleType("playwright.sync_api")
        sync_api.TimeoutError = _FakeTimeout
        sync_api.sync_playwright = lambda: _FakePlaywright(recorder, idle_raises)
        mod.sync_api = sync_api
        monkeypatch.setitem(sys.modules, "playwright", mod)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
        return recorder

    return _install


def test_a_page_that_never_goes_network_idle_still_produces_the_pdf(
        fake_playwright, tmp_path, capsys):
    """The offline case. The font request never settles, the wait raises, and
    the PDF has to be printed anyway: the page is a local file with its CSS
    inlined, so it is fully laid out without the CDN."""
    rec = fake_playwright(idle_raises=True)
    html = tmp_path / "n.html"
    html.write_text("<html></html>", encoding="utf-8")
    pdf = tmp_path / "n.pdf"

    ok = gen.generate_pdf(html, pdf)

    assert ok is True, "the run reported failure over a font that did not load"
    assert pdf.exists(), (
        "no PDF was produced, which is the whole artifact the run exists for")
    assert rec.get("closed") is True, "the browser was left open"


def test_the_idle_wait_is_bounded_rather_than_left_at_the_thirty_second_default(
        fake_playwright, tmp_path):
    """Unbounded is what made the offline case cost 30 seconds before failing.

    Asserted on the argument the module passes, because the stall is a
    consequence of omitting it and cannot be timed reliably in a test.
    """
    rec = fake_playwright(idle_raises=False)
    html = tmp_path / "n.html"
    html.write_text("<html></html>", encoding="utf-8")

    gen.generate_pdf(html, tmp_path / "n.pdf")

    state, timeout = rec["wait"]
    assert state == "networkidle", rec
    assert timeout is not None, (
        "the networkidle wait is unbounded again, so an unreachable font CDN "
        "costs the full navigation timeout and then the PDF")
    assert timeout <= 10000, f"the bound is too loose to matter: {timeout}ms"


def test_the_operator_is_told_the_remote_font_is_why(
        fake_playwright, tmp_path, capsys):
    """A silent degradation is the other half of the defect. The old warning
    said only "PDF generation failed" and never named the cause."""
    fake_playwright(idle_raises=True)
    html = tmp_path / "n.html"
    html.write_text("<html></html>", encoding="utf-8")

    gen.generate_pdf(html, tmp_path / "n.pdf")

    err = capsys.readouterr().err
    assert "network-idle" in err, err
    assert "Fonts" in err or "font" in err, (
        f"the warning does not name the remote font as the cause: {err}")


def test_a_real_failure_is_still_reported_as_a_failure(
        fake_playwright, tmp_path, monkeypatch):
    """The counter-case. Swallowing every exception from the wait would pass
    the first test and would also hide a browser that genuinely died."""
    fake_playwright(idle_raises=False)

    def _boom(self, path, **_kw):
        raise RuntimeError("chromium crashed")

    monkeypatch.setattr(_FakePage, "pdf", _boom)
    html = tmp_path / "n.html"
    html.write_text("<html></html>", encoding="utf-8")
    pdf = tmp_path / "n.pdf"

    ok = gen.generate_pdf(html, pdf)

    assert ok is False, "a crashed render was reported as a success"
    assert not pdf.exists()
