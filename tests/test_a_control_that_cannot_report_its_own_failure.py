"""Three controls that reported success while doing nothing.

Shard `scripts-05-p4` of the 2026-08-23/24 engine audit. The through-line is
narrower than "bugs": each of these is a SAFETY control whose failure mode was
a clean-looking success.

  - the config exporter's redaction masked a scalar `apiKey` but not the plural
    `apiKeys` holding a list, and printed "0 keys masked" either way;
  - the research tool's blocked-domain filter loaded from the wrong tree and
    returned an empty list, so every blocked source passed and no line said so
    (measured on the operator's own workspace: 0 domains, should be 10);
  - the Fireside daemon's webhook could die while the heartbeat kept the
    healthchecks.io check green, so Telegram updates were lost behind a
    monitor that could not go red.

Findings covered (numbering from `/tmp/audit_out3/scripts-05-p4.md`):

   1  sensitive key holding a list/dict was not masked
   2  trailing-comma regex corrupted string values
   3  a BOM made the masker fail OPEN
   4  the CLI subprocess had no timeout
   5  missing Windows env vars raised a bare KeyError
   6  bullet-listed blocked domains were skipped   (+ the wrong-tree defect
      the audit missed, which was the reason the list was empty in practice)
   7  a cache hit printed JSON where a fresh run printed markdown
   8  an unrecognised crawl shape was cached as an empty success for 48h
   9  an unrecognised batch shape printed empty sections and exited 0
  10  map silently dropped links of unexpected types
  12  dead `raise last_error` and a comment describing the wrong branch
  13  blocked-domain matching was a naive substring
  14  an explicit `0` reverted to the default
  15  credits were counted after local filtering
  16  the webhook task's death was observed by nobody          (HIGH)
  17  two RotatingFileHandlers on one log file
  18  a start-race loser deleted the winner's PID file
  19  the webhook abort path skipped cleanup
  20  PermissionError from os.kill read as "not running"
  21  status/stop re-read the PID file and could crash
  22  stop killed an unverified PID with no ProcessLookupError guard
  24  stop claimed the daemon "will exit" without checking
  25  the docstring promised next-run times status cannot know
  26  the poll comment said 5min where the trigger says 5s
"""

import ast
import asyncio
import contextlib
import importlib.util
import io
import json
import logging
import os
import sys
import tokenize
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _code_only(path: Path) -> str:
    """Source with comment lines removed.

    Every fix in this shard explains itself in a comment that QUOTES the string
    it removed, so a plain `"raise last_error" not in src` finds its own
    tombstone and fails. Six times this session. The scans below read code.
    """
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def test_the_comment_stripper_keeps_the_code(tmp_path):
    """A stripper that ate everything would make every scan below vacuous."""
    f = tmp_path / "sample.py"
    f.write_text("# raise last_error\nx = 1\ny = 2  # trailing raise last_error\n",
                 encoding="utf-8")
    out = _code_only(f)
    assert "x = 1" in out
    assert "y = 2" in out
    assert "# raise last_error" not in out


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ag = _load("export_antigravity_p5d", "scripts/export-antigravity-config.py")
fc = _load("firecrawl_p5d", "scripts/firecrawl.py")
fbd = _load("fireside_bot_daemon_p5d", "scripts/fireside-bot-daemon.py")


# ============================================================
# 1 - a sensitive key masks its whole value
# ============================================================

def test_a_sensitive_key_holding_a_list_is_masked():
    """`{"apiKeys": ["sk-real"]}` went into the shared zip in cleartext."""
    data, masked = ag.mask_sensitive({"apiKeys": ["sk-abc123", "sk-def456"]})
    assert data == {"apiKeys": ["***MASKED***", "***MASKED***"]}
    assert len(masked) == 2


def test_a_sensitive_key_holding_a_dict_is_masked():
    data, masked = ag.mask_sensitive({"token": {"value": "ghp_secret", "kind": "pat"}})
    assert data["token"]["value"] == "***MASKED***"
    assert data["token"]["kind"] == "***MASKED***"
    assert len(masked) == 2


def test_a_sensitive_key_holding_a_plain_string_is_still_masked():
    """The original behaviour must survive the widening."""
    data, masked = ag.mask_sensitive({"apiKey": "sk-live"})
    assert data == {"apiKey": "***MASKED***"}
    assert masked == ["apiKey"]


def test_an_innocent_key_is_left_alone():
    """Widening the mask must not have turned it into "mask everything"."""
    data, masked = ag.mask_sensitive({"fontSize": 14, "theme": "dark",
                                      "paths": ["/a", "/b"]})
    assert data == {"fontSize": 14, "theme": "dark", "paths": ["/a", "/b"]}
    assert masked == []


def test_a_sensitive_key_nested_under_an_innocent_one_is_found():
    data, masked = ag.mask_sensitive({"editor": {"secrets": ["s1"]}})
    assert data["editor"]["secrets"] == ["***MASKED***"]
    assert masked == ["editor.secrets[0]"]


def test_an_empty_string_is_not_counted_as_a_masked_secret():
    data, masked = ag.mask_sensitive({"apiKey": ""})
    assert data == {"apiKey": ""}
    assert masked == []


# ============================================================
# 2 - the comma stripper respects string literals
# ============================================================

@pytest.mark.parametrize("src,expected_value", [
    ('{"myext.delimiter": ", ]"}', ", ]"),
    ('{"note": "x,]"}', "x,]"),
    ('{"note": "a, }"}', "a, }"),
])
def test_a_comma_inside_a_string_value_survives(src, expected_value):
    """The regex ran over the whole document and threw the scanner's work away."""
    out = json.loads(ag.strip_jsonc(src))
    assert list(out.values()) == [expected_value]


def test_a_real_trailing_comma_is_still_removed():
    """Removing the regex entirely would also pass the test above."""
    assert json.loads(ag.strip_jsonc('{"a": 1, "b": [1, 2,],}')) == {"a": 1, "b": [1, 2]}


def test_comments_are_still_stripped():
    src = '{\n  // a line comment\n  "a": 1, /* block */ "b": 2,\n}'
    assert json.loads(ag.strip_jsonc(src)) == {"a": 1, "b": 2}


def test_a_comment_marker_inside_a_string_is_not_a_comment():
    assert json.loads(ag.strip_jsonc('{"url": "https://x.test//path"}')) == {
        "url": "https://x.test//path"
    }


# ============================================================
# 3, 4, 5 - the exporter fails closed
# ============================================================

def _run_export(tmp_path, monkeypatch, settings_bytes, extra_argv=()):
    user_data = tmp_path / "User"
    user_data.mkdir()
    (user_data / "settings.json").write_bytes(settings_bytes)
    out_zip = tmp_path / "out.zip"
    monkeypatch.setattr(ag, "detect_paths", lambda: (user_data, None))
    monkeypatch.setattr(sys, "argv", ["export-antigravity-config.py",
                                      "--output", str(out_zip), *extra_argv])
    ag.main()
    return out_zip


def test_a_bom_no_longer_defeats_the_masker(tmp_path, monkeypatch, capsys):
    """`json.loads("\\ufeff{...}")` raises, and the old code then shipped the
    raw file. A BOM is an ordinary state for an editor-written file, so the
    control failed open exactly where it was most likely to be needed."""
    body = json.dumps({"apiKey": "sk-live"}).encode("utf-8")
    out_zip = _run_export(tmp_path, monkeypatch, b"\xef\xbb\xbf" + body)
    capsys.readouterr()

    with zipfile.ZipFile(out_zip) as zf:
        settings = json.loads(zf.read("settings.json").decode("utf-8"))
    assert settings == {"apiKey": "***MASKED***"}


def test_an_unparseable_settings_file_is_excluded_not_shipped_raw(tmp_path, monkeypatch, capsys):
    out_zip = _run_export(tmp_path, monkeypatch, b'{"apiKey": "sk-live", oops')
    out = capsys.readouterr().out

    with zipfile.ZipFile(out_zip) as zf:
        names = zf.namelist()
    assert "settings.json" not in names, "the unmaskable file was shipped anyway"
    assert "EXCLUDED" in out


def test_no_mask_still_ships_the_raw_file_when_asked(tmp_path, monkeypatch, capsys):
    """Fail-closed must not have removed the explicit escape hatch."""
    # Deliberately malformed JSON. There is no credential in it.
    truncated = b'{"apiKey": "sk-live", oops'  # noqa: S105  # pragma: allowlist secret
    out_zip = _run_export(tmp_path, monkeypatch, truncated,
                          extra_argv=("--no-mask",))
    capsys.readouterr()
    with zipfile.ZipFile(out_zip) as zf:
        assert "settings.json" in zf.namelist()


def test_missing_windows_env_vars_give_a_sentence_not_a_keyerror(monkeypatch):
    monkeypatch.setattr(ag.platform, "system", lambda: "Windows")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(SystemExit) as exc:
        ag.detect_paths()
    assert "APPDATA" in str(exc.value)


def _subprocess_timeouts_for(source: str, marker: str) -> list[int | None]:
    """Every `subprocess.run(...)` whose argv mentions `marker`, and its timeout.

    `None` means that call has no `timeout=` keyword at all.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "run":
            continue
        if marker not in ast.unparse(ast.Tuple(elts=list(node.args), ctx=ast.Load())):
            continue
        timeout = None
        for kw in node.keywords:
            if kw.arg == "timeout" and isinstance(kw.value, ast.Constant):
                timeout = kw.value.value
        found.append(timeout)
    return found


def test_the_extension_listing_subprocess_is_bounded():
    """Finding 4: the CLI subprocess had no timeout, and the export hung.

    Rewritten 2026-08-30. The predecessor asserted `"--list-extensions" in src`
    and `"timeout=60" in src` as two INDEPENDENT whole-file greps: putting
    `timeout=60` on any unrelated call in the same file satisfied it while the
    extension listing went back to unbounded. The bound is now read off the
    same call node that carries the flag.
    """
    src = (ROOT / "scripts" / "export-antigravity-config.py").read_text(encoding="utf-8")
    timeouts = _subprocess_timeouts_for(src, "--list-extensions")
    assert timeouts, "no subprocess.run passes --list-extensions any more"
    for timeout in timeouts:
        assert timeout is not None, "the extension-listing subprocess is unbounded again"
        assert 0 < timeout <= 300, f"implausible bound on the CLI call: {timeout!r}"


def test_the_bound_reader_sees_an_unbounded_call_next_to_a_bounded_one():
    """The negative case: nothing above ever made the bound reader refuse.

    This is finding 4 reintroduced exactly as the grep permitted it — the
    listing call loses its timeout while a neighbour keeps one.
    """
    evaded = (
        "import subprocess\n"
        "subprocess.run([cli, '--list-extensions'], capture_output=True)\n"
        "subprocess.run([cli, '--version'], timeout=60)\n"
    )
    assert _subprocess_timeouts_for(evaded, "--list-extensions") == [None]
    assert _subprocess_timeouts_for(evaded, "--version") == [60]


# ============================================================
# 6 (+ the wrong-tree defect) - the blocked-domain filter
# ============================================================

def test_the_data_overlay_is_searched_for_the_domains_file(tmp_path, monkeypatch):
    """THE REGRESSION, pinned without depending on this checkout's layout.

    `config/routing-map.yaml` routes `reference/search-domains.md` private, so
    on the operator's machine it lives in the DATA overlay. The loader looked
    only at the ENGINE tree, found nothing, and returned `[]` -- the filter had
    been a complete no-op. Measured 2026-08-24 on the live workspace: 0 domains
    where there should be 10.
    """
    data_root = tmp_path / "data"
    engine_ref = tmp_path / "engine-reference"
    engine_ref.mkdir()
    (data_root / "reference").mkdir(parents=True)
    target = data_root / "reference" / "search-domains.md"
    target.write_text("## Blocked Domains\n\nblocked.test\n", encoding="utf-8")

    monkeypatch.setattr(fc, "get_data_root", lambda: data_root)
    monkeypatch.setattr(fc, "get_reference_dir", lambda: engine_ref)

    assert fc.find_search_domains_file() == target
    assert fc.load_blocked_domains() == ["blocked.test"]


def test_the_engine_tree_is_still_searched_as_a_fallback(tmp_path, monkeypatch):
    """A public clone has no overlay and ships a generic list in the engine."""
    data_root = tmp_path / "data"
    data_root.mkdir()
    engine_ref = tmp_path / "engine-reference"
    engine_ref.mkdir()
    target = engine_ref / "search-domains.md"
    target.write_text("## Blocked Domains\n\nengine.test\n", encoding="utf-8")

    monkeypatch.setattr(fc, "get_data_root", lambda: data_root)
    monkeypatch.setattr(fc, "get_reference_dir", lambda: engine_ref)

    assert fc.find_search_domains_file() == target


def test_the_blocked_domain_file_is_found_where_it_actually_lives():
    """THE REGRESSION, and it was live.

    `config/routing-map.yaml` routes `reference/search-domains.md` private, so
    on the operator's machine it sits in the DATA overlay. The loader looked
    only at the ENGINE tree, found nothing, and returned `[]` — the filter had
    been a complete no-op. Measured 2026-08-24: 0 domains loaded.

    Skipped where there is no overlay and no engine copy, e.g. a bare public
    clone or CI: there the empty list is correct, and the warning is the point.
    """
    found = fc.find_search_domains_file()
    if found is None:
        pytest.skip("no search-domains.md in this checkout (public clone / CI)")
    assert found.is_file()
    assert fc.load_blocked_domains(), "the file was found and still parsed to nothing"


def test_a_bullet_list_of_domains_is_parsed(tmp_path, monkeypatch):
    """A Markdown bullet is the ordinary way to write a list. Every bulleted
    entry used to be skipped outright."""
    f = tmp_path / "search-domains.md"
    f.write_text(
        "## Blocked Domains\n\nSome prose about why.\n\n"
        "- pinterest.test\n- quora.test\n\n## Next Section\n- notblocked.test\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(fc, "find_search_domains_file", lambda: f)
    assert fc.load_blocked_domains() == ["pinterest.test", "quora.test"]


def test_a_comma_separated_line_of_domains_is_still_parsed(tmp_path, monkeypatch):
    """The live file uses this form; accepting bullets must not break it."""
    f = tmp_path / "search-domains.md"
    f.write_text("## Blocked Domains\n\na.test, b.test, c.test\n\n## Next\n", encoding="utf-8")
    monkeypatch.setattr(fc, "find_search_domains_file", lambda: f)
    assert fc.load_blocked_domains() == ["a.test", "b.test", "c.test"]


def test_the_prose_paragraph_is_not_mistaken_for_domains(tmp_path, monkeypatch):
    """The live file's intro sentence has dots in it too."""
    f = tmp_path / "search-domains.md"
    f.write_text(
        "## Blocked Domains\n\nContent farms and low-signal aggregators. "
        "Apply as blocked_domains on all calls.\n\nreal.test\n\n## Next\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(fc, "find_search_domains_file", lambda: f)
    assert fc.load_blocked_domains() == ["real.test"]


def test_a_missing_file_says_nothing_is_being_blocked(monkeypatch, capsys):
    monkeypatch.setattr(fc, "find_search_domains_file", lambda: None)
    assert fc.load_blocked_domains() == []
    assert "NO domains are being blocked" in capsys.readouterr().err


def test_a_present_file_that_parses_to_nothing_also_says_so(tmp_path, monkeypatch, capsys):
    f = tmp_path / "search-domains.md"
    f.write_text("## Blocked Domains\n\n## Next\n", encoding="utf-8")
    monkeypatch.setattr(fc, "find_search_domains_file", lambda: f)
    assert fc.load_blocked_domains() == []
    assert "ZERO domains" in capsys.readouterr().err


# ============================================================
# 13 - blocked matching is on the host
# ============================================================

@pytest.mark.parametrize("url", [
    "https://pinterest.com/pin/1",
    "https://www.pinterest.com/",
    "http://PINTEREST.COM/x",
])
def test_a_blocked_host_is_blocked(url):
    assert fc.is_blocked_url(url, ["pinterest.com"]) is True


@pytest.mark.parametrize("url", [
    "https://pinterest.com.au/pin/1",       # a different registrable domain
    "https://notpinterest.com/",            # substring, not a label boundary
    "https://example.test/?ref=pinterest.com",   # in a query parameter
])
def test_a_lookalike_host_is_not_blocked(url):
    """`any(bd in url)` over-blocked every one of these."""
    assert fc.is_blocked_url(url, ["pinterest.com"]) is False


def test_a_path_scoped_entry_blocks_only_that_path():
    """`linkedin.com/pulse` is a real entry in the live list."""
    assert fc.is_blocked_url("https://linkedin.com/pulse/x", ["linkedin.com/pulse"]) is True
    assert fc.is_blocked_url("https://linkedin.com/in/someone", ["linkedin.com/pulse"]) is False


def test_a_url_that_is_not_a_url_does_not_slip_through_as_blocked():
    assert fc.is_blocked_url("", ["pinterest.com"]) is False


# ============================================================
# 7 - a cache hit renders exactly like a fresh run
# ============================================================

CRAWL = {"url": "https://x.test", "pages_found": 1, "credits_used": 1,
         "documents": [{"metadata": {"source_url": "https://x.test/a"},
                        "markdown": "# A"}]}
MAP = {"url": "https://x.test", "links_found": 2,
       "links": [{"url": "https://x.test/a", "title": "A"}, {"url": "https://x.test/b"}]}
SEARCH = {"query": "q", "results_count": 1, "credits_used": 1,
          "results": [{"url": "https://x.test/a", "title": "A", "markdown": "body"}]}


@pytest.mark.parametrize("renderer,payload,marker", [
    (lambda d, f: fc.render_crawl(d, f), CRAWL, "--- https://x.test/a ---"),
    (lambda d, f: fc.render_map(d, f), MAP, "# Site Map"),
    (lambda d, f: fc.render_search(d, f), SEARCH, "**URL:** https://x.test/a"),
])
def test_the_markdown_render_is_markdown_not_a_json_blob(renderer, payload, marker):
    """A cache hit handed the aggregate wrapper to `format_output`, which found
    no "markdown" key and dumped JSON. The same command therefore printed
    markdown once and JSON on the next run inside the TTL."""
    out = renderer(payload, "markdown")
    assert marker in out
    assert not out.lstrip().startswith("{")


@pytest.mark.parametrize("renderer,payload", [
    (lambda d, f: fc.render_crawl(d, f), CRAWL),
    (lambda d, f: fc.render_map(d, f), MAP),
    (lambda d, f: fc.render_search(d, f), SEARCH),
])
def test_json_format_is_still_json(renderer, payload):
    assert json.loads(renderer(payload, "json")) == payload


def test_no_command_still_renders_a_cache_hit_through_format_output():
    """The three aggregate commands must all be rewired, not just one."""
    body = _code_only(ROOT / "scripts" / "firecrawl.py")
    for renderer in ("render_crawl(cached", "render_map(cached", "render_search(cached"):
        assert renderer in body, f"{renderer} is not wired to the cache path"


# ============================================================
# 14, 15 - counting and defaults
# ============================================================

def test_an_explicit_zero_is_not_treated_as_unset():
    """`args.limit or 25` turned `--limit 0` into 25 and `--cache-ttl 0` into 24."""
    body = _code_only(ROOT / "scripts" / "firecrawl.py")
    assert "args.cache_ttl or DEFAULT_TTLS" not in body
    assert "args.limit or " not in body
    assert body.count("args.cache_ttl if args.cache_ttl is not None") >= 5


def test_the_retry_loop_has_no_unreachable_tail():
    """`raise last_error` sat below a loop every path exits from."""
    assert "raise last_error" not in _code_only(ROOT / "scripts" / "firecrawl.py")


# ============================================================
# 16 - the webhook's death is observed
# ============================================================

class _Recorder(logging.Logger):
    def __init__(self):
        super().__init__("recorder")
        self.errors = []

    def error(self, msg, *args, **kw):
        self.errors.append(msg % args if args else msg)


@pytest.mark.asyncio
async def test_a_webhook_that_dies_stops_the_daemon():
    """THE HIGH. In webhook mode this server is the only ingress path, and
    nothing observed the task: the heartbeat kept healthchecks.io green while
    Telegram POSTed into a dead endpoint and dropped the updates."""
    stop_event = asyncio.Event()
    logger = _Recorder()

    async def boom():
        raise RuntimeError("port 8443 already bound")

    task = asyncio.ensure_future(boom())
    task.add_done_callback(fbd.make_webhook_death_handler(stop_event, logger))
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)

    assert stop_event.is_set(), "the daemon kept running with no ingress"
    assert any("DIED" in e for e in logger.errors)


@pytest.mark.asyncio
async def test_a_webhook_that_exits_cleanly_but_early_also_stops_the_daemon():
    """No exception is not the same as no problem: `serve()` returning on its
    own before shutdown still leaves the bot deaf."""
    stop_event = asyncio.Event()
    logger = _Recorder()

    async def quiet_exit():
        return None

    task = asyncio.ensure_future(quiet_exit())
    task.add_done_callback(fbd.make_webhook_death_handler(stop_event, logger))
    await task
    await asyncio.sleep(0)

    assert stop_event.is_set()
    assert any("exited on its own" in e for e in logger.errors)


@pytest.mark.asyncio
async def test_an_ordinary_shutdown_logs_no_alarm():
    """The handler must not shout on every clean stop, or it stops meaning
    anything."""
    stop_event = asyncio.Event()
    stop_event.set()  # shutdown already requested
    logger = _Recorder()

    async def quiet_exit():
        return None

    task = asyncio.ensure_future(quiet_exit())
    task.add_done_callback(fbd.make_webhook_death_handler(stop_event, logger))
    await task
    await asyncio.sleep(0)

    assert logger.errors == []


# ============================================================
# 17 - one log file, one rotating handle
# ============================================================

_SHARED_HANDLE_LOGGERS = ("fireside-daemon", "scripts.utils.healthchecks")


@contextlib.contextmanager
def _restore_shared_loggers():
    """Snapshot and restore the two loggers this file reconfigures.

    Added 2026-08-30. The test below used to `handlers.clear()` and then, in
    teardown, close and remove whatever `_setup_logging` had installed — never
    putting back what was there before. Both loggers were left with zero
    handlers for the rest of the pytest session, so any later in-process test
    that relies on daemon logging saw different state depending on run order.
    """
    saved = {name: (logging.getLogger(name).handlers[:], logging.getLogger(name).level)
             for name in _SHARED_HANDLE_LOGGERS}
    try:
        yield
    finally:
        for name, (handlers, level) in saved.items():
            logger = logging.getLogger(name)
            for handler in logger.handlers[:]:
                if handler not in handlers:
                    handler.close()
                logger.removeHandler(handler)
            for handler in handlers:
                logger.addHandler(handler)
            logger.setLevel(level)


@pytest.fixture
def _restored_logging():
    with _restore_shared_loggers():
        yield


def test_the_two_loggers_share_one_rotating_handle(tmp_path, monkeypatch, _restored_logging):
    """Two RotatingFileHandlers on one path rotate independently: on Windows
    the rename fails while the other holds the file, and on POSIX the handler
    that did not rotate keeps writing into the renamed `daemon.log.1`."""
    monkeypatch.setattr(fbd, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(fbd, "LOG_FILE", tmp_path / "daemon.log")
    for name in _SHARED_HANDLE_LOGGERS:
        logging.getLogger(name).handlers.clear()

    fbd._setup_logging()
    main_files = [h for h in logging.getLogger("fireside-daemon").handlers
                  if isinstance(h, fbd.RotatingFileHandler)]
    hc_files = [h for h in logging.getLogger("scripts.utils.healthchecks").handlers
                if isinstance(h, fbd.RotatingFileHandler)]

    assert len(main_files) == 1
    assert len(hc_files) == 1
    assert main_files[0] is hc_files[0], "two independent handles on one log file"


def test_the_logging_test_leaves_the_two_loggers_as_it_found_them(tmp_path, monkeypatch):
    """The case ON the line for the restore fixture.

    Plant a sentinel handler on each logger, run the same reconfiguration
    inside the fixture, and prove the sentinel survives. Without the fixture
    this fails: the loggers came back with zero handlers.
    """
    sentinels = {}
    for name in _SHARED_HANDLE_LOGGERS:
        handler = logging.NullHandler()
        logging.getLogger(name).addHandler(handler)
        sentinels[name] = handler
    try:
        with _restore_shared_loggers():
            monkeypatch.setattr(fbd, "RUNTIME_DIR", tmp_path)
            monkeypatch.setattr(fbd, "LOG_FILE", tmp_path / "daemon.log")
            for name in _SHARED_HANDLE_LOGGERS:
                logging.getLogger(name).handlers.clear()
            fbd._setup_logging()
            assert sentinels["fireside-daemon"] not in \
                logging.getLogger("fireside-daemon").handlers

        for name, handler in sentinels.items():
            assert handler in logging.getLogger(name).handlers, (
                f"{name} did not get its pre-test handlers back")
    finally:
        for name, handler in sentinels.items():
            logging.getLogger(name).removeHandler(handler)


# ============================================================
# 18, 20, 21 - the PID file
# ============================================================

def test_a_permission_error_means_the_process_exists(monkeypatch):
    """`os.kill(pid, 0)` raising PermissionError means it is ALIVE and owned by
    someone else. Reading that as dead permits a duplicate daemon."""
    if os.name == "nt":
        pytest.skip("POSIX branch")

    def denied(pid, sig):
        raise PermissionError

    monkeypatch.setattr(fbd.os, "kill", denied)
    assert fbd._pid_is_running(4242) is True


def test_a_process_lookup_error_means_it_is_gone(monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX branch")

    def gone(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(fbd.os, "kill", gone)
    assert fbd._pid_is_running(4242) is False


def test_a_vanished_pid_file_gives_none_not_a_traceback(tmp_path, monkeypatch):
    """`status` and `stop` re-read the file after the liveness check and died
    on FileNotFoundError when the daemon exited in between."""
    monkeypatch.setattr(fbd, "PID_FILE", tmp_path / "nope.pid")
    assert fbd.live_daemon_pid() is None
    assert fbd.is_daemon_alive() is False


def test_a_live_pid_file_gives_the_pid(tmp_path, monkeypatch):
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text(str(os.getpid()))
    monkeypatch.setattr(fbd, "PID_FILE", pid_file)
    assert fbd.live_daemon_pid() == os.getpid()


def test_a_daemon_does_not_delete_another_daemons_pid_file(tmp_path, monkeypatch, caplog):
    """A start-race loser used to erase the WINNER's PID file, after which
    `status` said NOT RUNNING while a live daemon kept firing all its jobs."""
    pid_file = tmp_path / "daemon.pid"
    started = tmp_path / "started_at"
    pid_file.write_text("999999")  # somebody else
    started.write_text("1")
    monkeypatch.setattr(fbd, "PID_FILE", pid_file)
    monkeypatch.setattr(fbd, "STARTED_AT_FILE", started)

    fbd._remove_own_pid_file(logging.getLogger("test-fbd"))

    assert pid_file.exists(), "deleted a PID file belonging to another process"
    assert started.exists()


def test_a_daemon_does_delete_its_own_pid_file(tmp_path, monkeypatch):
    """Ownership-checking must not have turned cleanup into a no-op: a stale
    PID file makes the next start refuse."""
    pid_file = tmp_path / "daemon.pid"
    started = tmp_path / "started_at"
    pid_file.write_text(str(os.getpid()))
    started.write_text("1")
    monkeypatch.setattr(fbd, "PID_FILE", pid_file)
    monkeypatch.setattr(fbd, "STARTED_AT_FILE", started)

    fbd._remove_own_pid_file(logging.getLogger("test-fbd"))

    assert not pid_file.exists()
    assert not started.exists()


# ============================================================
# 24, 25, 26 - the sentences these tools print about themselves
# ============================================================

def test_stop_does_not_promise_an_exit_it_never_checked():
    assert "daemon will exit within ~1s" not in _code_only(
        ROOT / "scripts" / "fireside-bot-daemon.py")


def test_the_docstring_does_not_promise_next_run_times():
    """`status` is a separate process with no access to the live scheduler."""
    src = (ROOT / "scripts" / "fireside-bot-daemon.py").read_text(encoding="utf-8")
    header = src.split('"""', 2)[1]
    assert "next scheduled run for each job." not in header


DAEMON_SRC = (ROOT / "scripts" / "fireside-bot-daemon.py").read_text(encoding="utf-8")


def _poll_interval_seconds(source: str) -> int:
    """The `poll` job's interval, read off JOB_SPECS by AST.

    The predecessor did `body.split("JOB_SPECS", 1)[1].split('"heartbeat"', 1)[0]`,
    which is a declaration-ORDER dependency: move `heartbeat` above `poll` in the
    dict and the slice no longer contains the poll spec, so a correct file goes
    red. The dict is a dict; ask it for the key.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
        if target != "JOB_SPECS" or not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values, strict=True):
            if not (isinstance(key, ast.Constant) and key.value == "poll"):
                continue
            spec = ast.literal_eval(value)
            return int(spec["trigger"]["seconds"]), key.lineno
    raise AssertionError("JOB_SPECS has no literal 'poll' entry")


def _comment_block_above(source: str, lineno: int) -> str:
    """The contiguous run of COMMENT tokens directly above `lineno`.

    Read with `tokenize`, not by splitting text on `#`: a `#` inside a string
    literal is not a comment, and the whole point of this test is to read the
    comment that `_code_only` deletes.
    """
    comments = {}
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            comments[tok.start[0]] = tok.string
    block, probe = [], lineno - 1
    while probe in comments:
        block.append(comments[probe])
        probe -= 1
    return "\n".join(reversed(block))


def test_the_poll_comment_matches_the_poll_trigger():
    """The comment said `now + 5min` against a 5-second interval.

    Rewritten 2026-08-30. The predecessor ran on `_code_only`, which deletes
    every comment line BEFORE the scan, so the comment this test is named for
    was never examined: reverting the comment to `now + 5min` left it green.
    Both halves are now derived — the interval by AST off JOB_SPECS, the
    comment by `tokenize` off the block above the `poll` key — and compared.
    """
    seconds, poll_lineno = _poll_interval_seconds(DAEMON_SRC)
    comment = _comment_block_above(DAEMON_SRC, poll_lineno)
    assert comment, "the poll entry lost the comment this test exists to check"
    assert f"now + {seconds}s" in comment, (
        f"the poll trigger fires every {seconds}s but the comment above it "
        f"does not say `now + {seconds}s`:\n{comment}")


def test_the_poll_comment_check_fails_when_the_comment_disagrees():
    """The negative case: nothing above ever made the comment check refuse.

    Feed the same two readers a synthetic module whose comment claims a gap
    sixty times the trigger — the exact defect finding 26 named — and prove
    the derived comparison goes red on it.
    """
    stale = (
        'JOB_SPECS: dict[str, dict] = {\n'
        '    # first poll runs one interval later, so `now + 5min` for the value.\n'
        '    "poll": {"trigger": {"kind": "interval", "seconds": 5}},\n'
        '}\n'
    )
    seconds, lineno = _poll_interval_seconds(stale)
    assert seconds == 5
    assert f"now + {seconds}s" not in _comment_block_above(stale, lineno)


def test_the_poll_interval_is_read_by_key_not_by_declaration_order():
    """`heartbeat` declared first must not hide the poll spec from the reader."""
    reordered = (
        'JOB_SPECS = {\n'
        '    "heartbeat": {"trigger": {"kind": "interval", "minutes": 1}},\n'
        '    # so `now + 7s` for the value below.\n'
        '    "poll": {"trigger": {"kind": "interval", "seconds": 7}},\n'
        '}\n'
    )
    seconds, lineno = _poll_interval_seconds(reordered)
    assert seconds == 7
    assert "now + 7s" in _comment_block_above(reordered, lineno)


def test_status_with_no_daemon_says_so_and_exits_zero(tmp_path, monkeypatch, capsys):
    """No PID file means NOT RUNNING, and `cmd_status` returns rather than exits.

    Rewritten 2026-08-30. The predecessor shelled out to the real script with no
    isolation of PID_FILE / RUNTIME_DIR, so it read the DEVELOPER'S OWN machine
    state: it passed here only because no daemon happened to be running, and a
    live daemon (or a stale PID recycled by any unrelated process) turned it red
    for a reason that has nothing to do with the code. The PID file is now
    `tmp_path`, so the answer comes from the fixture, not from the host.
    """
    monkeypatch.setattr(fbd, "PID_FILE", tmp_path / "absent.pid")
    monkeypatch.setattr(fbd, "STARTED_AT_FILE", tmp_path / "absent.started")
    monkeypatch.setattr(fbd, "REGISTERED_JOBS_FILE", tmp_path / "absent.json")

    assert fbd.cmd_status(None) is None, "status raised instead of returning"
    assert "NOT RUNNING" in capsys.readouterr().out


def test_status_with_a_live_pid_file_does_not_say_not_running(tmp_path, monkeypatch, capsys):
    """The case ON the line: a status that always printed NOT RUNNING passed above.

    Without this, `cmd_status` could be reduced to a single unconditional
    `print("fireside-daemon: NOT RUNNING")` and the suite would stay green.
    """
    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(fbd, "PID_FILE", pid_file)
    monkeypatch.setattr(fbd, "STARTED_AT_FILE", tmp_path / "absent.started")
    monkeypatch.setattr(fbd, "REGISTERED_JOBS_FILE", tmp_path / "absent.json")

    fbd.cmd_status(None)
    out = capsys.readouterr().out
    assert "NOT RUNNING" not in out
    assert f"RUNNING pid={os.getpid()}" in out


def test_the_status_subcommand_is_wired_to_cmd_status(monkeypatch):
    """What the removed subprocess proved, without reading the host.

    The old test spawned the real CLI, which is the only reason the dispatch
    table was covered at all. Drive `main()` with a recorded `cmd_status`
    instead: same wiring, no PID file, no machine state.
    """
    called = []
    monkeypatch.setattr(fbd, "cmd_status", lambda args: called.append(args))
    monkeypatch.setattr(sys, "argv", ["fireside-bot-daemon.py", "status"])

    fbd.main()

    assert len(called) == 1, "the `status` subcommand did not reach cmd_status"
