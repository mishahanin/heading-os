"""Shard 05-p4, second pass: a redaction tool that misdescribed its most
dangerous run, five cache and rendering paths that ignored what was asked for,
and a cleanup handler with a hole in the one exception its sibling handles.

* ``export-antigravity-config --no-mask`` never calls ``mask_sensitive``, so
  ``masked_keys`` is empty - and the console footer read "Nothing was masked.
  The scan matches KEY NAMES only", asserting that a name-based scan RAN and
  matched nothing. No scan ran; the zip carries every credential verbatim. The
  bundle README said nothing about settings.json at all, so neither the console
  nor the artifact told the truth about that run.

* The same script read settings.json OUTSIDE its fail-closed ``try``. A UTF-16
  file - Notepad's "Unicode" save - raised UnicodeDecodeError before the try
  was entered and killed the process inside ``with zipfile.ZipFile(...)``,
  leaving a zip holding nothing: settings.json is the first entry, so snippets
  and extensions never ran.

* ``firecrawl crawl --format html`` requested html from the API and then
  rendered markdown: ``render_crawl`` took ``output_format`` and read it only
  for "json". The html was fetched, paid for, and discarded.

* The crawl cache key omitted the requested formats and the map cache key
  omitted ``--limit``, so a second run with different flags was served the
  first run's answer - for 48 hours, and for 168 hours respectively.

* ``search`` and ``map`` cached an empty payload when the SDK returned an
  unrecognised shape. ``crawl`` was given a guard for exactly this; its two
  siblings were not.

* ``batch`` keyed fresh results by the URL the document reports and looked them
  up by the URL the operator requested, so any redirect printed the literal
  ``{}`` for a page that had been fetched and paid for; ``"metadata": null``
  reached ``None.get`` and killed the run; and an unrecognised shape still
  printed a success credit line and exited 0.

* ``fireside-bot-daemon``'s shutdown ran ``_shutdown_and_clean`` as a trailing
  statement under ``except Exception``. ``asyncio.CancelledError`` subclasses
  BaseException, so a cancelled webhook task skipped the scheduler shutdown,
  the PID cleanup and the ``daemon-stop`` line - and the handler's own comment
  promised "cleanup still runs".

Run: python3 -m pytest tests/test_a_bundle_that_never_said_the_keys_were_live.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / "scripts" / stem))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ag():
    return _load("export-antigravity-config.py", "p05p4b_antigravity")


@pytest.fixture(scope="module")
def fc():
    return _load("firecrawl.py", "p05p4b_firecrawl")


@pytest.fixture(scope="module")
def fbd():
    return _load("fireside-bot-daemon.py", "p05p4b_fireside_daemon")


# ============================================================
# The bundle that never said the keys were live
# ============================================================

def _profile(tmp_path: Path, settings: bytes | None) -> Path:
    """An Antigravity user-data folder with one snippet and optional settings."""
    user_data = tmp_path / "User"
    (user_data / "snippets").mkdir(parents=True)
    (user_data / "snippets" / "py.json").write_text("{}", encoding="utf-8")
    if settings is not None:
        (user_data / "settings.json").write_bytes(settings)
    return user_data


def _run_export(ag, monkeypatch, tmp_path, settings: bytes | None, *extra):
    user_data = _profile(tmp_path, settings)
    monkeypatch.setattr(ag, "detect_paths", lambda: (user_data, None))
    out_zip = tmp_path / "bundle.zip"
    monkeypatch.setattr(sys, "argv",
                        ["export-antigravity-config.py", "--output", str(out_zip),
                         *extra])
    ag.main()
    return out_zip


# The value is the literal words "sk-live-value", not a key. The masking pass
# under test matches on the KEY NAME, so the fixture needs a key-shaped entry
# and nothing more. The entropy detector cannot tell the two apart.
LIVE_SETTINGS = b'{"editor.fontSize": 13, "someExt.apiKey": "sk-live-value"}'  # pragma: allowlist secret
# UTF-16-LE with a BOM: exactly what a Windows editor writes on "Unicode" save.
UTF16_SETTINGS = '{"editor.fontSize": 13}'.encode("utf-16")


def test_a_no_mask_run_does_not_claim_a_scan_ran(ag, monkeypatch, tmp_path, capsys):
    """The reported reproduction. `--no-mask` printed the words of the safest run."""
    _run_export(ag, monkeypatch, tmp_path, LIVE_SETTINGS, "--no-mask")
    out = capsys.readouterr().out
    assert "Nothing was masked." not in out
    assert "The scan matches KEY NAMES only" not in out
    assert "Nothing was scanned." in out


def test_a_no_mask_run_says_the_file_shipped_verbatim(ag, monkeypatch, tmp_path,
                                                      capsys):
    _run_export(ag, monkeypatch, tmp_path, LIVE_SETTINGS, "--no-mask")
    out = capsys.readouterr().out
    assert "verbatim" in out
    assert "credentials and all" in out


def test_a_masking_run_still_gets_the_key_names_caution(ag, monkeypatch, tmp_path,
                                                        capsys):
    """The older warning must survive the new branch beside it: zero masked keys
    is exactly the run that most needs 'a name-based scan cannot see this'."""
    _run_export(ag, monkeypatch, tmp_path, b'{"editor.fontSize": 13}')
    out = capsys.readouterr().out
    assert "Nothing was masked." in out
    assert "The scan matches KEY NAMES only" in out


def test_a_masking_run_that_hit_something_says_so(ag, monkeypatch, tmp_path, capsys):
    _run_export(ag, monkeypatch, tmp_path, LIVE_SETTINGS)
    out = capsys.readouterr().out
    assert "1 keys masked" in out
    assert "The auto-masker replaced values" in out


def test_the_no_mask_bundle_carries_the_warning_in_its_readme(ag, monkeypatch,
                                                              tmp_path):
    """The console line is read once; the zip outlives the run and gets forwarded."""
    out_zip = _run_export(ag, monkeypatch, tmp_path, LIVE_SETTINGS, "--no-mask")
    with zipfile.ZipFile(out_zip) as zf:
        readme = zf.read("README.md").decode("utf-8")
        assert "sk-live-value" in zf.read("settings.json").decode("utf-8")
    assert "UNMASKED credentials" in readme
    assert "Do not forward this zip" in readme


def test_a_masked_bundle_readme_carries_no_such_warning(ag, monkeypatch, tmp_path):
    out_zip = _run_export(ag, monkeypatch, tmp_path, LIVE_SETTINGS)
    with zipfile.ZipFile(out_zip) as zf:
        readme = zf.read("README.md").decode("utf-8")
        assert "***MASKED***" in zf.read("settings.json").decode("utf-8")
    assert "UNMASKED credentials" not in readme
    assert "auto-masked" in readme


def test_a_clean_bundle_readme_mentions_neither(ag, monkeypatch, tmp_path):
    """Masking on, nothing matched: no scare note, no masked-count note."""
    out_zip = _run_export(ag, monkeypatch, tmp_path, b'{"editor.fontSize": 13}')
    with zipfile.ZipFile(out_zip) as zf:
        readme = zf.read("README.md").decode("utf-8")
    assert "UNMASKED credentials" not in readme
    assert "auto-masked" not in readme


def test_the_readme_distinguishes_disabled_from_matched_nothing(ag):
    """Both look like zero from a count, and only one of them is safe."""
    disabled = ag.build_readme("2026-08-25", 0, "shipped", masking_enabled=False)
    scanned = ag.build_readme("2026-08-25", 0, "shipped", masking_enabled=True)
    assert "UNMASKED credentials" in disabled
    assert "UNMASKED credentials" not in scanned


def test_an_excluded_settings_file_is_never_called_unmasked(ag):
    """Nothing shipped, so there is nothing live to warn about - the absent-file
    note is the one that matters."""
    readme = ag.build_readme("2026-08-25", 0, "excluded", masking_enabled=False)
    assert "UNMASKED credentials" not in readme
    assert "settings.json is NOT in this bundle" in readme


# ---- the read that sat outside the guard --------------------------------------

def test_a_utf16_settings_file_does_not_kill_the_export(ag, monkeypatch, tmp_path,
                                                        capsys):
    """It raised UnicodeDecodeError before the try, inside the open zip."""
    out_zip = _run_export(ag, monkeypatch, tmp_path, UTF16_SETTINGS)
    with zipfile.ZipFile(out_zip) as zf:
        names = set(zf.namelist())
    assert "settings.json" not in names, "an unreadable file must not ship"
    assert "README.md" in names
    assert "snippets/py.json" in names, (
        "the crash left the zip empty; the entries after settings never ran"
    )


def test_a_utf16_settings_file_is_reported_as_excluded(ag, monkeypatch, tmp_path,
                                                       capsys):
    _run_export(ag, monkeypatch, tmp_path, UTF16_SETTINGS)
    out = capsys.readouterr().out
    assert "UnicodeDecodeError" in out
    assert "EXCLUDED from the export" in out


def test_no_mask_does_not_ship_a_file_it_could_not_decode(ag, monkeypatch, tmp_path):
    """`--no-mask` ships the RAW text on a parse failure. A decode failure has no
    raw text, and inventing an empty one would put an empty settings.json in the
    bundle under a green line."""
    out_zip = _run_export(ag, monkeypatch, tmp_path, UTF16_SETTINGS, "--no-mask")
    with zipfile.ZipFile(out_zip) as zf:
        assert "settings.json" not in zf.namelist()


def test_no_mask_still_ships_a_file_that_only_failed_to_parse(ag, monkeypatch,
                                                             tmp_path):
    """The documented escape hatch, unchanged: valid UTF-8, invalid JSON."""
    out_zip = _run_export(ag, monkeypatch, tmp_path, b'{"a": 1,,}', "--no-mask")
    with zipfile.ZipFile(out_zip) as zf:
        assert zf.read("settings.json").decode("utf-8") == '{"a": 1,,}'


def test_an_unparseable_file_is_still_excluded_when_masking(ag, monkeypatch,
                                                            tmp_path):
    """The older fail-closed branch must survive the widened except clause."""
    out_zip = _run_export(ag, monkeypatch, tmp_path, b'{"a": 1,,}')
    with zipfile.ZipFile(out_zip) as zf:
        assert "settings.json" not in zf.namelist()


def test_a_valid_bom_prefixed_file_still_ships(ag, monkeypatch, tmp_path):
    """utf-8-sig is why the read is what it is; widening the except must not
    turn an ordinary BOM into an exclusion."""
    out_zip = _run_export(ag, monkeypatch, tmp_path,
                          '\ufeff{"editor.fontSize": 13}'.encode("utf-8"))
    with zipfile.ZipFile(out_zip) as zf:
        assert json.loads(zf.read("settings.json")) == {"editor.fontSize": 13}


# ============================================================
# firecrawl: the format that was asked for and not delivered
# ============================================================

class _Args:
    def __init__(self, **kw):
        self.target = "https://example.com"
        self.format = "markdown"
        self.no_cache = False
        self.cache_ttl = None
        self.output = None
        self.timeout = 30000
        self.quiet = False
        self.screenshot = False
        self.limit = None
        self.include = None
        self.exclude = None
        self.__dict__.update(kw)


class _Job:
    def __init__(self, data, credits_used=1):
        self.data = data
        self.credits_used = credits_used


class _FakeClient:
    """Counts real calls, so a cache hit is observable, and lets each command's
    return shape be swapped for the unrecognised-shape tests."""

    def __init__(self):
        self.crawl_calls = []
        self.map_calls = []
        self.search_calls = []
        self.batch_calls = []
        self.map_result = None
        self.search_result = None
        self.batch_result = None

    def crawl(self, url, **kwargs):
        self.crawl_calls.append(kwargs)
        return _Job([{"metadata": {"source_url": url},
                      "markdown": "MD BODY", "html": "<p>HTML BODY</p>"}])

    def map(self, url, **kwargs):
        self.map_calls.append(kwargs)
        if self.map_result is not None:
            return self.map_result
        n = kwargs.get("limit") or 2
        return type("MapData", (), {
            "links": [type("L", (), {"url": f"{url}/{i}", "title": None,
                                     "description": None})()
                      for i in range(n)]})()

    def search(self, query, **kwargs):
        self.search_calls.append(kwargs)
        if self.search_result is not None:
            return self.search_result
        return type("SearchData", (), {"web": []})()

    def batch_scrape(self, urls, **kwargs):
        self.batch_calls.append((list(urls), kwargs.get("formats")))
        if self.batch_result is not None:
            return self.batch_result
        return _Job([{"metadata": {"source_url": u}, "markdown": "page text"}
                     for u in urls])


@pytest.fixture()
def wired(fc, tmp_path, monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(fc, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(fc, "get_client", lambda *a, **k: client)
    return client


# ---- crawl renders what was requested -----------------------------------------

def test_crawl_html_renders_the_html_it_paid_for(fc, wired, capsys):
    fc.cmd_crawl(_Args(format="html"))
    out = capsys.readouterr().out
    assert "<p>HTML BODY</p>" in out
    assert "MD BODY" not in out


def test_crawl_markdown_still_renders_markdown(fc, wired, capsys):
    fc.cmd_crawl(_Args())
    out = capsys.readouterr().out
    assert "MD BODY" in out
    assert "<p>HTML BODY</p>" not in out


def test_crawl_html_falls_back_to_markdown_when_no_html_came_back(fc, wired,
                                                                  monkeypatch,
                                                                  capsys):
    """A page the API returned no html for still has to render something."""
    monkeypatch.setattr(wired, "crawl", lambda url, **kw: _Job(
        [{"metadata": {"source_url": url}, "markdown": "MD ONLY"}]))
    fc.cmd_crawl(_Args(format="html"))
    assert "MD ONLY" in capsys.readouterr().out


def test_crawl_json_is_unaffected(fc, wired, capsys):
    fc.cmd_crawl(_Args(format="json"))
    assert json.loads(capsys.readouterr().out)["pages_found"] == 1


# ---- crawl and map cache keys carry what varies --------------------------------

def test_a_crawl_html_run_refetches_instead_of_serving_markdown(fc, wired, capsys):
    fc.cmd_crawl(_Args())
    capsys.readouterr()
    fc.cmd_crawl(_Args(format="html"))
    assert len(wired.crawl_calls) == 2, (
        "an --format html crawl was served a markdown-only cache entry"
    )
    assert wired.crawl_calls[1]["scrape_options"]["formats"] == ["markdown", "html"]


def test_an_identical_crawl_still_hits_the_cache(fc, wired, capsys):
    fc.cmd_crawl(_Args())
    capsys.readouterr()
    fc.cmd_crawl(_Args())
    assert len(wired.crawl_calls) == 1
    assert "cache hit" in capsys.readouterr().err


def test_a_crawl_json_run_still_shares_the_markdown_entry(fc, wired, capsys):
    """json and markdown request the same documents; splitting them wastes credits."""
    fc.cmd_crawl(_Args())
    capsys.readouterr()
    fc.cmd_crawl(_Args(format="json"))
    assert len(wired.crawl_calls) == 1


def test_a_crawl_still_keys_on_limit_and_paths(fc, wired, capsys):
    """The key components that were already there must survive the new one."""
    fc.cmd_crawl(_Args())
    fc.cmd_crawl(_Args(limit=5))
    fc.cmd_crawl(_Args(include="/docs"))
    fc.cmd_crawl(_Args(exclude="/blog"))
    assert len(wired.crawl_calls) == 4


def test_a_wider_map_limit_refetches(fc, wired, capsys):
    """168 hours of being told a five-link site has five links."""
    fc.cmd_map(_Args(limit=5))
    capsys.readouterr()
    fc.cmd_map(_Args(limit=500))
    assert len(wired.map_calls) == 2
    assert wired.map_calls[1]["limit"] == 500


def test_an_identical_map_still_hits_the_cache(fc, wired, capsys):
    fc.cmd_map(_Args(limit=5))
    capsys.readouterr()
    fc.cmd_map(_Args(limit=5))
    assert len(wired.map_calls) == 1
    assert "cache hit" in capsys.readouterr().err


def test_an_unlimited_map_does_not_share_with_a_limited_one(fc, wired, capsys):
    fc.cmd_map(_Args())
    capsys.readouterr()
    fc.cmd_map(_Args(limit=5))
    assert len(wired.map_calls) == 2


# ---- the shape guards crawl had and its siblings did not -----------------------

def _cache_files(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "cache").glob("*.json")) if (tmp_path / "cache").exists() else []


def test_a_map_of_an_unknown_shape_is_not_cached(fc, wired, tmp_path, capsys):
    wired.map_result = object()
    fc.cmd_map(_Args())
    assert _cache_files(tmp_path) == [], "an empty answer was cached for 168 hours"
    assert "unexpected shape" in capsys.readouterr().err


def test_a_map_of_a_known_shape_is_still_cached(fc, wired, tmp_path, capsys):
    fc.cmd_map(_Args())
    assert len(_cache_files(tmp_path)) == 1


def test_a_search_of_an_unknown_shape_is_not_cached(fc, wired, tmp_path, capsys):
    wired.search_result = object()
    fc.cmd_search(_Args(target="sovereign dpi"))
    assert _cache_files(tmp_path) == []
    assert "unexpected shape" in capsys.readouterr().err


def test_a_genuinely_empty_search_is_still_cached(fc, wired, tmp_path, capsys):
    """An empty `.web` is a real zero-result answer, and re-asking costs credits."""
    fc.cmd_search(_Args(target="sovereign dpi"))
    assert len(_cache_files(tmp_path)) == 1


# ---- batch: the URL it was asked about ----------------------------------------

def test_a_redirected_page_lands_under_the_url_that_was_requested(fc, wired,
                                                                  monkeypatch,
                                                                  capsys):
    """The reported reproduction: the section for a fetched, paid-for page read
    `{}` because the answer was filed under the canonical URL."""
    monkeypatch.setattr(wired, "batch_scrape", lambda urls, **kw: _Job(
        [{"metadata": {"source_url": "https://www.example.com/"},
          "markdown": "REAL BODY"}]))
    fc.cmd_batch(_Args(target="https://example.com"))
    out = capsys.readouterr().out
    assert "REAL BODY" in out
    assert "{}" not in out


def test_a_null_metadata_does_not_kill_an_aligned_batch(fc, wired, monkeypatch,
                                                        capsys):
    """Position answers the question, so the metadata is never consulted."""
    monkeypatch.setattr(wired, "batch_scrape", lambda urls, **kw: _Job(
        [{"markdown": "x", "metadata": None}]))
    fc.cmd_batch(_Args(target="https://example.com"))
    assert "x" in capsys.readouterr().out


def test_a_null_metadata_does_not_kill_a_misaligned_batch(fc, wired, monkeypatch,
                                                          capsys):
    """The path that DOES read it. `.get("metadata", {})` substitutes only for
    an ABSENT key, so a null went to `None.get` and took the whole run with it
    after the credits were spent."""
    monkeypatch.setattr(wired, "batch_scrape", lambda urls, **kw: _Job(
        [{"markdown": "x", "metadata": None}]))
    with pytest.raises(SystemExit):
        fc.cmd_batch(_Args(target="https://a.example,https://b.example"))
    captured = capsys.readouterr()
    assert "AttributeError" not in captured.err
    assert "no document was returned" in captured.out


def test_a_short_answer_says_position_cannot_be_trusted(fc, wired, monkeypatch,
                                                        capsys):
    """Two URLs asked, one document back: position is no longer the mapping."""
    monkeypatch.setattr(wired, "batch_scrape", lambda urls, **kw: _Job(
        [{"metadata": {"source_url": "https://b.example"}, "markdown": "B BODY"}]))
    with pytest.raises(SystemExit):
        fc.cmd_batch(_Args(target="https://a.example,https://b.example"))
    captured = capsys.readouterr()
    assert "position cannot be trusted" in captured.err
    assert "B BODY" in captured.out


def test_an_unmatched_url_is_named_rather_than_rendered_as_a_document(fc, wired,
                                                                      monkeypatch,
                                                                      capsys):
    monkeypatch.setattr(wired, "batch_scrape", lambda urls, **kw: _Job([]))
    with pytest.raises(SystemExit) as se:
        fc.cmd_batch(_Args(target="https://a.example"))
    assert se.value.code == 1
    captured = capsys.readouterr()
    assert "no document was returned" in captured.out
    assert "--- https://a.example ---\n{}" not in captured.out
    assert "1 of 1 URL(s) produced no document" in captured.err


def test_an_unknown_batch_shape_does_not_claim_a_successful_scrape(fc, wired,
                                                                   capsys):
    wired.batch_result = object()
    with pytest.raises(SystemExit) as se:
        fc.cmd_batch(_Args(target="https://a.example"))
    assert se.value.code == 1
    err = capsys.readouterr().err
    assert "Batch scraped" not in err, "a run that produced nothing reported success"
    assert "unexpected shape" in err


def test_a_normal_batch_still_reports_its_credits_and_exits_clean(fc, wired, capsys):
    fc.cmd_batch(_Args(target="https://a.example,https://b.example"))
    captured = capsys.readouterr()
    assert "[2 credits] Batch scraped 2 URLs" in captured.err
    assert "page text" in captured.out


def test_the_module_docstring_documents_every_ttl(fc):
    doc = fc.__doc__
    for command in fc.DEFAULT_TTLS:
        assert f"{command} {fc.DEFAULT_TTLS[command]}h" in doc, command


# ============================================================
# The cleanup that a cancellation walked out of
# ============================================================

class _StubBot:
    """Stands in for fireside-bot.py: `__getattr__` answers every cmd_* the
    dispatcher wires up, without importing a module that reads live state."""

    def ensure_state_dir(self):
        return None

    def __getattr__(self, name):
        return lambda *a, **k: None


class _FakeScheduler:
    """Records the shutdown `_shutdown_and_clean` is supposed to perform."""

    def __init__(self, *a, **k):
        self.shutdowns = 0

    def add_job(self, *a, **k):
        return None

    def start(self):
        return None

    def shutdown(self, wait=False):
        self.shutdowns += 1


@pytest.fixture()
def runtime(fbd, tmp_path, monkeypatch):
    monkeypatch.setattr(fbd, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(fbd, "PID_FILE", tmp_path / "daemon.pid")
    monkeypatch.setattr(fbd, "STARTED_AT_FILE", tmp_path / "started_at")
    monkeypatch.setattr(fbd, "REGISTERED_JOBS_FILE", tmp_path / "registered_jobs.json")
    monkeypatch.setattr(fbd, "STOP_SENTINEL", tmp_path / "stop")
    return tmp_path


def _drive_real_daemon(fbd, monkeypatch, runtime, serve):
    """Run the REAL `_run_daemon` in webhook mode, with `serve` as the server.

    Everything the daemon needs from outside is stubbed - the bot, the
    scheduler, uvicorn, the app factory - so the code under test is the actual
    startup and, the part this section is about, the actual shutdown block.
    Returns the scheduler, so `shutdowns` reports whether cleanup ran.
    """
    scheduler = _FakeScheduler()
    monkeypatch.setattr(fbd, "AsyncIOScheduler", lambda *a, **k: scheduler)
    monkeypatch.setattr(fbd, "load_env", lambda: None)
    monkeypatch.setattr(fbd, "_load_fireside_bot", _StubBot)
    monkeypatch.setenv("FIRESIDE_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("FIRESIDE_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("FIRESIDE_WEBHOOK_CERT", "c")
    monkeypatch.setenv("FIRESIDE_WEBHOOK_KEY", "k")

    class _Server:
        def __init__(self, _config):
            self.should_exit = False

        async def serve(self):
            await serve()

    fake_uvicorn = type(sys)("uvicorn")
    fake_uvicorn.Config = lambda *a, **k: object()
    fake_uvicorn.Server = _Server
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    fake_webhook = type(sys)("scripts.fireside_webhook")
    fake_webhook.create_app = lambda *a, **k: object()
    monkeypatch.setitem(sys.modules, "scripts.fireside_webhook", fake_webhook)

    logger = logging.getLogger("p05p4b-daemon")
    logger.handlers = []
    logger.addHandler(logging.NullHandler())
    return scheduler, logger


async def _serve_that_cancels_itself():
    """The state `make_webhook_death_handler` already anticipates, and the one
    `except Exception` could not see: CancelledError is a BaseException."""
    asyncio.current_task().cancel()
    await asyncio.sleep(0)


async def _serve_that_dies_of_a_bad_certificate():
    raise OSError("certificate verify failed")


async def _serve_that_returns_on_its_own():
    return None


def test_a_cancelled_webhook_task_still_runs_the_cleanup(fbd, monkeypatch, runtime):
    """THE case, through the real shutdown block."""
    scheduler, logger = _drive_real_daemon(fbd, monkeypatch, runtime,
                                           _serve_that_cancels_itself)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(fbd._run_daemon(logger))
    assert scheduler.shutdowns == 1, (
        "the scheduler, the PID file and the daemon-stop line were all skipped"
    )


def test_a_cancelled_webhook_task_still_removes_the_pid_file(fbd, monkeypatch,
                                                             runtime):
    """The stale PID file is what makes the next `status` report RUNNING."""
    _, logger = _drive_real_daemon(fbd, monkeypatch, runtime,
                                   _serve_that_cancels_itself)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(fbd._run_daemon(logger))
    assert not (runtime / "daemon.pid").exists()


def test_a_failed_webhook_task_still_runs_the_cleanup(fbd, monkeypatch, runtime):
    """The case the old handler DID cover; it must survive the restructure."""
    scheduler, logger = _drive_real_daemon(fbd, monkeypatch, runtime,
                                           _serve_that_dies_of_a_bad_certificate)
    asyncio.run(fbd._run_daemon(logger))
    assert scheduler.shutdowns == 1


def test_a_webhook_that_exits_on_its_own_still_runs_the_cleanup(fbd, monkeypatch,
                                                                runtime):
    scheduler, logger = _drive_real_daemon(fbd, monkeypatch, runtime,
                                           _serve_that_returns_on_its_own)
    asyncio.run(fbd._run_daemon(logger))
    assert scheduler.shutdowns == 1


def test_the_cleanup_runs_exactly_once(fbd, monkeypatch, runtime):
    """Nesting it in a finally must not double it: `_shutdown_and_clean` logs
    `daemon-stop`, which a reader counts as one process exit."""
    scheduler, logger = _drive_real_daemon(fbd, monkeypatch, runtime,
                                           _serve_that_dies_of_a_bad_certificate)
    asyncio.run(fbd._run_daemon(logger))
    assert scheduler.shutdowns == 1
