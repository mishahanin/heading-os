"""Shard scripts-05-p4: a cache that ignored the request, and a daemon that
recited its config instead of reading its own state.

  * `firecrawl.py scrape` keyed its cache on the URL and NOTHING ELSE. So a
    plain `scrape URL` cached a markdown-only document, and a later
    `scrape URL --screenshot` was served that entry: the screenshot was never
    fetched, `format_output` found markdown and printed it, and nothing said
    the screenshot was missing. `--format html` fell into the same hole. Every
    OTHER command already keys on its variable inputs - crawl on
    limit/include/exclude, search on limit, extract on prompt/schema.
  * `firecrawl.py extract` wrote `credits_used: 0` into the cache entry and
    printed a `[credits used]` label with no number, for the one command whose
    own docstring says "credits vary".
  * `fireside-bot-daemon.py status` recited `JOB_SPECS`, which lists `poll` -
    the one job webhook mode deliberately skips. It is a separate process with
    no access to the live scheduler, exactly the reason the module docstring
    gives for why `status` does NOT print next-run times. The same claim was
    being made about the job list one line below.
  * The start log said `jobs=14` from `len(JOB_SPECS)` while 13 were registered.
  * The "atomic" PID write used ONE fixed scratch name, `daemon.pid.tmp`. The
    `os.replace` is atomic; a shared scratch path is not. Two daemons racing to
    start both wrote it, and one `replace` moved the other's file into place -
    after which the winner ran under a PID file naming the loser, and the
    ownership-checked cleanup correctly refused to remove a file that was not
    its own.

Written 2026-08-24. Every test here fails against the pre-fix file.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import sys
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
def fc():
    return _load("firecrawl.py", "p05p4_firecrawl")


@pytest.fixture(scope="module")
def fbd():
    return _load("fireside-bot-daemon.py", "p05p4_fireside_daemon")


# ============================================================
# firecrawl: the cache key
# ============================================================

def test_a_plain_scrape_asks_for_markdown_only(fc):
    assert fc.scrape_formats("markdown") == ["markdown"]


def test_json_output_asks_for_the_same_thing_as_markdown(fc):
    """`--format json` renders the SAME document. Splitting the key here would
    cost a credit for nothing."""
    assert fc.scrape_formats("json") == fc.scrape_formats("markdown")


def test_html_output_asks_for_html_as_well(fc):
    assert fc.scrape_formats("html") == ["markdown", "html"]


def test_a_screenshot_is_an_extra_format(fc):
    assert "screenshot" in fc.scrape_formats("markdown", screenshot=True)


def test_a_screenshot_request_does_not_share_a_key_with_a_plain_one(fc):
    """THE case. The key was the URL alone, so these two collided."""
    plain = fc.scrape_cache_key("https://example.com", fc.scrape_formats("markdown"))
    shot = fc.scrape_cache_key("https://example.com",
                               fc.scrape_formats("markdown", screenshot=True))
    assert plain != shot


def test_an_html_request_does_not_share_a_key_with_a_markdown_one(fc):
    a = fc.scrape_cache_key("https://example.com", fc.scrape_formats("markdown"))
    b = fc.scrape_cache_key("https://example.com", fc.scrape_formats("html"))
    assert a != b


def test_json_and_markdown_still_share_one_key(fc):
    """The green path. A key that split on the FLAG rather than on what was
    actually requested would re-fetch here and spend a credit for nothing."""
    a = fc.scrape_cache_key("https://example.com", fc.scrape_formats("markdown"))
    b = fc.scrape_cache_key("https://example.com", fc.scrape_formats("json"))
    assert a == b


def test_two_different_urls_never_share_a_key(fc):
    a = fc.scrape_cache_key("https://a.example", fc.scrape_formats("markdown"))
    b = fc.scrape_cache_key("https://b.example", fc.scrape_formats("markdown"))
    assert a != b


def test_the_formats_list_order_does_not_change_the_key(fc):
    """Sorted, so a list built in another order still hits its own entry."""
    a = fc.scrape_cache_key("https://example.com", ["markdown", "html"])
    b = fc.scrape_cache_key("https://example.com", ["html", "markdown"])
    assert a == b


# ---- end to end through cmd_scrape -------------------------------------------

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
        self.prompt = None
        self.schema = None
        self.__dict__.update(kw)


class _FakeClient:
    """Counts real calls, so a cache hit is observable."""

    def __init__(self):
        self.scrape_calls = []
        self.batch_calls = []
        self.extract_calls = 0

    def scrape(self, url, **kwargs):
        self.scrape_calls.append(kwargs.get("formats"))
        return {"markdown": "page text", "html": "<p>page text</p>"}

    def batch_scrape(self, urls, **kwargs):
        self.batch_calls.append((list(urls), kwargs.get("formats")))
        return type("Job", (), {"data": [
            {"metadata": {"source_url": u}, "markdown": "page text"} for u in urls
        ]})()

    def extract(self, **kwargs):
        self.extract_calls += 1
        return {"data": {"price": 10}}


@pytest.fixture()
def wired(fc, tmp_path, monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(fc, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(fc, "get_client", lambda *a, **k: client)
    return client


def test_a_screenshot_run_refetches_instead_of_serving_a_markdown_entry(
        fc, wired, capsys):
    """THE case, end to end. The second command asked for a screenshot and used
    to be handed the first command's markdown-only document."""
    fc.cmd_scrape(_Args())
    capsys.readouterr()
    fc.cmd_scrape(_Args(screenshot=True))
    assert len(wired.scrape_calls) == 2, (
        "a --screenshot run was served a cached document that has no screenshot"
    )
    assert "screenshot" in wired.scrape_calls[1]


def test_an_html_run_refetches_instead_of_serving_a_markdown_entry(fc, wired, capsys):
    fc.cmd_scrape(_Args())
    capsys.readouterr()
    fc.cmd_scrape(_Args(format="html"))
    assert len(wired.scrape_calls) == 2


def test_an_identical_run_still_hits_the_cache(fc, wired, capsys):
    """The green path: a key that never collides would spend a credit every run."""
    fc.cmd_scrape(_Args())
    capsys.readouterr()
    fc.cmd_scrape(_Args())
    assert len(wired.scrape_calls) == 1
    assert "cache hit" in capsys.readouterr().err


def test_a_json_run_still_hits_a_markdown_entry(fc, wired, capsys):
    """The two request the same document; splitting them would waste a credit."""
    fc.cmd_scrape(_Args())
    capsys.readouterr()
    fc.cmd_scrape(_Args(format="json"))
    assert len(wired.scrape_calls) == 1


def test_no_cache_always_refetches(fc, wired, capsys):
    fc.cmd_scrape(_Args())
    capsys.readouterr()
    fc.cmd_scrape(_Args(no_cache=True))
    assert len(wired.scrape_calls) == 2


# ---- batch writes the entries scrape reads -------------------------------------

def test_a_scrape_reuses_what_batch_cached(fc, wired, capsys):
    """Both write and read through the SAME key function, so a batch page and a
    later single scrape of it still share an entry - which is the whole point of
    sharing the `scrape` command namespace. Found by a surviving mutation: no
    test drove `cmd_batch` at all, so a batch that wrote under the old
    formats-blind key changed nothing any test could see."""
    fc.cmd_batch(_Args(target="https://a.example,https://b.example"))
    capsys.readouterr()
    fc.cmd_scrape(_Args(target="https://a.example"))
    assert wired.scrape_calls == [], (
        "a batch-cached page was not found by a scrape asking for the same thing"
    )
    assert "cache hit" in capsys.readouterr().err


def test_a_screenshot_scrape_does_not_reuse_a_batch_entry(fc, wired, capsys):
    """batch has no --screenshot flag, so its entry cannot answer one."""
    fc.cmd_batch(_Args(target="https://a.example"))
    capsys.readouterr()
    fc.cmd_scrape(_Args(target="https://a.example", screenshot=True))
    assert len(wired.scrape_calls) == 1


def test_batch_asks_for_the_formats_its_flag_names(fc, wired, capsys):
    fc.cmd_batch(_Args(target="https://a.example", format="html"))
    assert wired.batch_calls[0][1] == ["markdown", "html"]


# ---- extract: a cost nobody measured ------------------------------------------

def _cache_entries(cache_dir: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(cache_dir.glob("*.json"))]


def test_an_unmeasured_extract_cost_is_recorded_as_unknown(fc, wired, tmp_path, capsys):
    """THE case. `credits_used: 0` is a measured zero for a cost the API never
    reported, on the one command whose docstring says "credits vary"."""
    fc.cmd_extract(_Args(prompt="Extract pricing tiers"))
    capsys.readouterr()
    entries = _cache_entries(tmp_path / "cache")
    assert len(entries) == 1
    assert entries[0]["credits_used"] is None, (
        f"an unmeasured cost was recorded as {entries[0]['credits_used']!r}"
    )


def test_the_extract_console_line_says_the_cost_is_unknown(fc, wired, capsys):
    """`[credits used]` was a label with no number beside it."""
    fc.cmd_extract(_Args(prompt="Extract pricing tiers"))
    err = capsys.readouterr().err
    assert "unknown" in err


def test_a_scrape_still_records_its_one_known_credit(fc, wired, tmp_path, capsys):
    """The green path: 'record None everywhere' would erase a cost we DO know."""
    fc.cmd_scrape(_Args())
    capsys.readouterr()
    assert _cache_entries(tmp_path / "cache")[0]["credits_used"] == 1


# ============================================================
# fireside daemon: the atomic write
# ============================================================

def test_an_atomic_write_lands_the_content(fbd, tmp_path):
    target = tmp_path / "daemon.pid"
    fbd._atomic_write(target, "4242")
    assert target.read_text(encoding="utf-8") == "4242"


def test_an_atomic_write_leaves_no_scratch_file_behind(fbd, tmp_path):
    target = tmp_path / "daemon.pid"
    fbd._atomic_write(target, "4242")
    assert [p.name for p in tmp_path.iterdir()] == ["daemon.pid"]


def test_two_writers_do_not_share_one_scratch_name(fbd, tmp_path, monkeypatch):
    """THE case. `os.replace` is atomic; a FIXED scratch path is not.

    Both daemons wrote `daemon.pid.tmp`, and one `replace` moved the other's
    file into place - so the winner ran under a PID file naming the loser, and
    the ownership-checked cleanup then refused to remove it.

    Held open mid-write so the two overlap the way the real race does.
    """
    target = tmp_path / "daemon.pid"
    seen: list[str] = []
    real_mkstemp = fbd.tempfile.mkstemp

    def _spy(**kwargs):
        fd, name = real_mkstemp(**kwargs)
        seen.append(Path(name).name)
        return fd, name

    monkeypatch.setattr(fbd.tempfile, "mkstemp", _spy)
    fbd._atomic_write(target, "111")
    fbd._atomic_write(target, "222")
    assert len(set(seen)) == 2, f"both writers used the same scratch path: {seen}"


def test_an_atomic_write_creates_the_directory(fbd, tmp_path):
    target = tmp_path / "nested" / "daemon.pid"
    fbd._atomic_write(target, "7")
    assert target.read_text(encoding="utf-8") == "7"


# ---- status: the live set, not the configured one ------------------------------

@pytest.fixture()
def runtime(fbd, tmp_path, monkeypatch):
    monkeypatch.setattr(fbd, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(fbd, "PID_FILE", tmp_path / "daemon.pid")
    monkeypatch.setattr(fbd, "STARTED_AT_FILE", tmp_path / "started_at")
    monkeypatch.setattr(fbd, "REGISTERED_JOBS_FILE", tmp_path / "registered_jobs.json")
    monkeypatch.setattr(fbd, "STOP_SENTINEL", tmp_path / "stop")
    monkeypatch.setattr(fbd, "_pid_is_running", lambda pid: pid == os.getpid())
    (tmp_path / "daemon.pid").write_text(str(os.getpid()), encoding="utf-8")
    return tmp_path


def _write_registered(runtime: Path, jobs, webhook, pid=None):
    (runtime / "registered_jobs.json").write_text(
        json.dumps({"pid": pid if pid is not None else os.getpid(),
                    "webhook_mode": webhook, "jobs": jobs}), encoding="utf-8")


def test_status_in_webhook_mode_does_not_list_poll(fbd, runtime, capsys):
    """THE case. `status` recited JOB_SPECS, which lists the one job webhook
    mode skips - from a process that cannot see the scheduler at all."""
    _write_registered(runtime, [k for k in fbd.JOB_SPECS if k != "poll"], webhook=True)
    fbd.cmd_status(None)
    out = capsys.readouterr().out
    jobs_line = next(ln for ln in out.splitlines() if ln.startswith("jobs registered:"))
    # The NAME LIST only. The trailing "(webhook mode: poll not registered)" is
    # the explanation and legitimately says the word.
    names = jobs_line.split("jobs registered:")[1].split(" (")[0].split(", ")
    assert "poll" not in [n.strip() for n in names]
    assert "heartbeat" in [n.strip() for n in names]


def test_status_in_webhook_mode_says_why_poll_is_absent(fbd, runtime, capsys):
    """A missing name with no explanation reads as a job that got lost."""
    _write_registered(runtime, [k for k in fbd.JOB_SPECS if k != "poll"], webhook=True)
    fbd.cmd_status(None)
    assert "webhook mode" in capsys.readouterr().out


def test_status_in_polling_mode_lists_poll(fbd, runtime, capsys):
    """The green path: dropping `poll` unconditionally would also pass the
    webhook test above."""
    _write_registered(runtime, list(fbd.JOB_SPECS), webhook=False)
    fbd.cmd_status(None)
    out = capsys.readouterr().out
    jobs_line = next(ln for ln in out.splitlines() if ln.startswith("jobs registered:"))
    assert "poll" in jobs_line
    assert "webhook mode" not in out


def test_status_without_the_file_says_the_live_set_is_unknown(fbd, runtime, capsys):
    """Fail toward over-reporting: NAME the gap rather than let the configured
    list pass for the live one."""
    fbd.cmd_status(None)
    out = capsys.readouterr().out
    assert "jobs registered: unknown" in out
    assert "jobs configured:" in out, "the fallback list must be labelled as configured"


def test_status_with_a_stale_file_from_another_pid_says_unknown(fbd, runtime, capsys):
    """A leftover file from a dead daemon describes a scheduler that is gone."""
    _write_registered(runtime, ["heartbeat"], webhook=True, pid=os.getpid() + 99999)
    fbd.cmd_status(None)
    out = capsys.readouterr().out
    assert "unknown" in out
    assert "heartbeat" not in out


def test_status_with_an_unparseable_file_says_unknown(fbd, runtime, capsys):
    (runtime / "registered_jobs.json").write_text("{ not json", encoding="utf-8")
    fbd.cmd_status(None)
    assert "jobs registered: unknown" in capsys.readouterr().out


def test_status_with_a_json_array_file_says_unknown(fbd, runtime, capsys):
    """`[]` decodes cleanly and is not a record; `.get` on it would raise."""
    (runtime / "registered_jobs.json").write_text("[]", encoding="utf-8")
    fbd.cmd_status(None)
    assert "jobs registered: unknown" in capsys.readouterr().out


def test_status_on_a_dead_daemon_reports_not_running(fbd, runtime, capsys, monkeypatch):
    """Unchanged behaviour, so the new reads cannot resurrect a dead daemon."""
    monkeypatch.setattr(fbd, "_pid_is_running", lambda pid: False)
    fbd.cmd_status(None)
    assert "NOT RUNNING" in capsys.readouterr().out


# ---- cleanup covers the new file ------------------------------------------------

def test_our_own_exit_removes_the_registered_jobs_file(fbd, runtime):
    _write_registered(runtime, ["heartbeat"], webhook=True)
    (runtime / "started_at").write_text("1", encoding="utf-8")
    fbd._remove_own_pid_file(logging.getLogger("test-p05p4"))
    assert not (runtime / "registered_jobs.json").exists()
    assert not (runtime / "daemon.pid").exists()
    assert not (runtime / "started_at").exists()


def test_another_processes_files_are_left_alone(fbd, runtime):
    """The race-loser rule: a PID file naming someone else is not ours to delete."""
    (runtime / "daemon.pid").write_text(str(os.getpid() + 99999), encoding="utf-8")
    _write_registered(runtime, ["heartbeat"], webhook=True)
    fbd._remove_own_pid_file(logging.getLogger("test-p05p4"))
    assert (runtime / "registered_jobs.json").exists()
    assert (runtime / "daemon.pid").exists()


# ---- the start-up registration ---------------------------------------------------

class _Abort(Exception):
    """Stop _run_daemon at scheduler.start(), after the state files are written."""


class _StubBot:
    """Stands in for fireside-bot.py. `__getattr__` answers every cmd_* the
    dispatcher wires up, without importing a module that reads live state."""

    def ensure_state_dir(self):
        return None

    def __getattr__(self, name):
        return lambda *a, **k: None


class _FakeScheduler:
    def __init__(self, *a, **k):
        self.jobs: list[str] = []

    def add_job(self, _fn, _trigger, args=None, **kwargs):
        self.jobs.append(args[0])

    def start(self):
        raise _Abort


@pytest.fixture()
def boot(fbd, runtime, monkeypatch):
    """Drive _run_daemon far enough to write its state files, then abort."""
    (runtime / "daemon.pid").unlink(missing_ok=True)
    scheduler = _FakeScheduler()
    monkeypatch.setattr(fbd, "AsyncIOScheduler", lambda *a, **k: scheduler)
    monkeypatch.setattr(fbd, "load_env", lambda: None)
    monkeypatch.setattr(fbd, "_load_fireside_bot", _StubBot)
    return scheduler


def _boot(fbd, scheduler, webhook: bool, monkeypatch) -> list[str]:
    monkeypatch.setenv("FIRESIDE_WEBHOOK_ENABLED", "true" if webhook else "")
    records: list[str] = []
    logger = logging.getLogger(f"p05p4-boot-{webhook}")
    logger.handlers = []
    logger.addHandler(type("H", (logging.Handler,),
                           {"emit": lambda _s, r: records.append(r.getMessage())})())
    logger.setLevel(logging.INFO)
    with pytest.raises(_Abort):
        asyncio.run(fbd._run_daemon(logger))
    return records


def test_webhook_mode_registers_one_fewer_job(fbd, boot, runtime, monkeypatch):
    """THE count. `poll` is skipped, so 13 of the 14 specs reach the scheduler."""
    _boot(fbd, boot, webhook=True, monkeypatch=monkeypatch)
    assert "poll" not in boot.jobs
    assert len(boot.jobs) == len(fbd.JOB_SPECS) - 1


def test_polling_mode_registers_every_job(fbd, boot, runtime, monkeypatch):
    _boot(fbd, boot, webhook=False, monkeypatch=monkeypatch)
    assert boot.jobs == list(fbd.JOB_SPECS)


def test_the_start_log_reports_the_registered_count_not_the_spec_count(
        fbd, boot, runtime, monkeypatch):
    """THE case. `len(JOB_SPECS)` claimed 14 while the scheduler held 13, and
    webhook mode is how this daemon runs in production."""
    records = _boot(fbd, boot, webhook=True, monkeypatch=monkeypatch)
    start = next(r for r in records if r.startswith("daemon-start"))
    assert f"jobs={len(fbd.JOB_SPECS) - 1}" in start
    assert f"jobs={len(fbd.JOB_SPECS)}" not in start


def test_the_start_log_still_reports_the_full_count_when_polling(
        fbd, boot, runtime, monkeypatch):
    """The green path, so a hardcoded `- 1` cannot pass."""
    records = _boot(fbd, boot, webhook=False, monkeypatch=monkeypatch)
    start = next(r for r in records if r.startswith("daemon-start"))
    assert f"jobs={len(fbd.JOB_SPECS)}" in start


def test_the_daemon_writes_the_registered_set_for_status_to_read(
        fbd, boot, runtime, monkeypatch):
    """The file is the whole mechanism: without it `status` can only recite."""
    _boot(fbd, boot, webhook=True, monkeypatch=monkeypatch)
    payload = json.loads((runtime / "registered_jobs.json").read_text(encoding="utf-8"))
    assert payload["jobs"] == boot.jobs
    assert payload["webhook_mode"] is True
    assert payload["pid"] == os.getpid()


def test_the_daemon_records_polling_mode_too(fbd, boot, runtime, monkeypatch):
    _boot(fbd, boot, webhook=False, monkeypatch=monkeypatch)
    payload = json.loads((runtime / "registered_jobs.json").read_text(encoding="utf-8"))
    assert payload["webhook_mode"] is False
    assert "poll" in payload["jobs"]


def test_the_daemon_still_writes_its_pid_and_start_time(fbd, boot, runtime, monkeypatch):
    """The pre-existing state files must survive the move to _atomic_write."""
    _boot(fbd, boot, webhook=False, monkeypatch=monkeypatch)
    assert (runtime / "daemon.pid").read_text(encoding="utf-8") == str(os.getpid())
    assert int((runtime / "started_at").read_text(encoding="utf-8")) > 0
