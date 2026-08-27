"""A singleton that was not one, and four reports that were not true.

Covers the k3 audit shard `scripts-00-p4` for `scripts/bridge-daemon.py`,
`scripts/bridge_daemon/adoption.py` and `scripts/bridge_daemon/_jsonl.py`.

Nothing here starts the daemon. It is stopped and disabled on this machine on
purpose, and down is its intended state; every test drives a function directly
or against a temporary directory.

*One bad line took the whole report down.* `_iter_records` checks that a record
HAS a `ts` key and never that the value is a string, and
`datetime.fromisoformat(None)` raises TypeError, which is not the ValueError
`_local_date` catches. So a single `"ts": null` from a torn write or a hand
edit propagated out of `summarize()` and 500'd `GET /telemetry/summary` -- the
exact outcome `_iter_records`' own docstring says it exists to prevent. The
guard already existed: `_shapes.entry_ts` was written for this shape after the
identical defect in `sources/critical.py`, and was never applied here.

*The singleton guard was check-then-act with a long gap.* `_live_daemon_port()`
reads `.daemon-state/port`, which is not written until well after the check,
and `_pick_port` holds each probed socket open -- so two `--start` processes
launched together do not even collide on a port. They bind different ones, both
overwrite the shared port file, and both run schedulers: the action-queue
sweep, the watchdog and the critique sweep each run twice. That is duplicate
alerts and duplicate model spend, and `--health` sees only one of them.

*And it failed open in the degraded state.* `urllib.error.HTTPError` subclasses
`URLError`, so a daemon that was bound and answering 500 was classified as
absent -- and a second one started on top of it. A 500 is an answer.

*A range that named a port it never probed.* `range(start, start + 50)` stops
at `start + 49`; the error message said `start + 50`, so an operator freeing
"the last port in the range" freed one that was never tried.

*A health check that died instead of reporting.* `json.JSONDecodeError`
subclasses ValueError, not OSError, so a 200 with a non-JSON body -- which is
exactly what a stale port file plus a foreign process on that port produces --
gave a traceback rather than the 0/1/2 the docstring promises, and skipped the
heartbeat fallback that exists for precisely that moment.

*A restrictive mode applied one write too late.* `append_jsonl` created the
file with `open("a")`, wrote the first record, and only then chmodded. A caller
asking for `0o600` got a window where the file was world-readable and already
held content. `_atomic.py`, in the same package, carries a comment about
closing that exact race.
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import urllib.error
from datetime import date
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(WORKSPACE))

from scripts.bridge_daemon import _jsonl, adoption  # noqa: E402


@pytest.fixture(scope="module")
def bd():
    """`scripts/bridge-daemon.py`, hyphenated and so not importable by name."""
    spec = importlib.util.spec_from_file_location(
        "bridge_daemon_twice_mod", WORKSPACE / "scripts" / "bridge-daemon.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bridge_daemon_twice_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# 1. The one bad line that took a 14-day report down
# ============================================================

def _usage(tmp_path: Path, lines: list[str]) -> Path:
    state = tmp_path / ".daemon-state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "usage.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("bad_ts", ["null", "1700000000", '["2026-08-01"]',
                                    "{}", "true"])
def test_a_non_string_timestamp_does_not_take_the_report_down(tmp_path, bad_ts):
    """`datetime.fromisoformat(None)` is a TypeError, which nothing caught."""
    root = _usage(tmp_path, ['{"ts": %s, "event": "page_view"}' % bad_ts])
    report = adoption.summarize(root, days=14, today=date(2026, 8, 25))
    assert report["totals"]["page_views"] == 0


def test_one_bad_line_does_not_hide_the_good_ones(tmp_path):
    """The whole point of `_iter_records`' resilience claim."""
    root = _usage(tmp_path, [
        '{"ts": null, "event": "page_view"}',
        '{"ts": "2026-08-25T09:00:00+00:00", "event": "page_view"}',
        '{"ts": "2026-08-25T10:00:00+00:00", "event": "launch"}',
    ])
    report = adoption.summarize(root, days=14, today=date(2026, 8, 25))
    assert report["totals"]["page_views"] == 1
    assert report["totals"]["actions"] == 1


def test_a_missing_usage_file_is_still_an_empty_report(tmp_path):
    report = adoption.summarize(tmp_path, days=14, today=date(2026, 8, 25))
    assert report["totals"]["page_views"] == 0
    assert report["window_days"] == 14


def test_the_shared_guard_is_the_one_being_used():
    """A local `isinstance` here would be the fourth copy of one rule.

    `_shapes.py` says it in its own docstring: the guard was written once,
    applied in one module, and not given to the siblings with the same read.
    """
    text = (WORKSPACE / "scripts" / "bridge_daemon" / "adoption.py").read_text(
        encoding="utf-8")
    assert "from scripts.bridge_daemon._shapes import entry_ts" in text
    # Comments stripped: the fix left one quoting the old expression.
    code = "\n".join(ln for ln in text.split("\n")
                     if not ln.lstrip().startswith("#"))
    assert 'rec["ts"]' not in code, "the unguarded read is back"


# ============================================================
# 2. The singleton that was not one
# ============================================================

def test_a_second_start_cannot_take_the_lock(bd, tmp_path, monkeypatch):
    """The fix, driven directly: hold it once, and the next attempt is refused."""
    monkeypatch.setattr(bd, "WORKSPACE_ROOT", tmp_path)
    first = bd._acquire_start_lock()
    assert first is not None
    try:
        assert bd._acquire_start_lock() is None
    finally:
        first.close()


def test_the_lock_is_released_when_the_holder_goes_away(bd, tmp_path,
                                                        monkeypatch):
    """A crashed start must not block the next one forever."""
    monkeypatch.setattr(bd, "WORKSPACE_ROOT", tmp_path)
    first = bd._acquire_start_lock()
    first.close()                       # what process exit does
    second = bd._acquire_start_lock()
    assert second is not None
    second.close()


def test_the_lock_file_is_never_unlinked(bd, tmp_path, monkeypatch):
    """Removing a flocked path lets the next process lock a different inode
    under the same name, which is the same race in a different coat."""
    monkeypatch.setattr(bd, "WORKSPACE_ROOT", tmp_path)
    handle = bd._acquire_start_lock()
    handle.close()
    assert (tmp_path / ".daemon-state" / "start.lock").exists()
    src = (WORKSPACE / "scripts" / "bridge-daemon.py").read_text(encoding="utf-8")
    assert "start.lock" in src
    for line in src.splitlines():
        if "unlink" in line or "missing_ok" in line:
            assert "start.lock" not in line and "lock_path" not in line, line


def test_start_refuses_when_the_lock_is_held(bd, tmp_path, monkeypatch, capsys):
    """`start_daemon` must stop at the lock, before it picks or binds anything."""
    monkeypatch.setattr(bd, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(bd, "_live_daemon_port", lambda *a, **k: None)
    picked = []
    monkeypatch.setattr(bd, "_pick_port",
                        lambda s: picked.append(s) or (s, None))
    holder = bd._acquire_start_lock()
    try:
        with pytest.raises(SystemExit) as exc:
            bd.start_daemon()
        assert exc.value.code == 1
        assert picked == [], "it got as far as picking a port"
        err = capsys.readouterr().err
        assert "already in progress or running" in err
    finally:
        holder.close()


def test_the_lock_is_taken_before_the_liveness_probe(bd):
    """Order is the fix. Probing first leaves the same gap the lock closes."""
    src = (WORKSPACE / "scripts" / "bridge-daemon.py").read_text(encoding="utf-8")
    body = src.split("def start_daemon(")[1]
    assert body.index("_acquire_start_lock()") < body.index("_live_daemon_port()")


# ---------------------------------------------------------------------------
# The probe that read a 500 as "nothing there"
# ---------------------------------------------------------------------------

def _port_file(bd, tmp_path, monkeypatch, port: int = 31415):
    monkeypatch.setattr(bd, "WORKSPACE_ROOT", tmp_path)
    state = tmp_path / ".daemon-state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "port").write_text(str(port), encoding="utf-8")
    return port


def test_a_daemon_answering_500_counts_as_running(bd, tmp_path, monkeypatch):
    """HTTPError subclasses URLError, so this fell into the "absent" branch."""
    port = _port_file(bd, tmp_path, monkeypatch)

    def _raise(*a, **k):
        raise urllib.error.HTTPError(
            url="http://127.0.0.1/health", code=500, msg="boom", hdrs=None,
            fp=None)

    # Patched on the stdlib module: `bridge-daemon.py` imports urllib INSIDE
    # each function, so there is no module attribute to reach.
    import urllib.request as _ur
    monkeypatch.setattr(_ur, "urlopen", _raise)
    assert bd._live_daemon_port(timeout=0.1) == port


def test_a_refused_connection_still_counts_as_absent(bd, tmp_path, monkeypatch):
    """A crashed daemon must never block the next start."""
    _port_file(bd, tmp_path, monkeypatch)

    def _raise(*a, **k):
        raise urllib.error.URLError("connection refused")

    import urllib.request as _ur
    monkeypatch.setattr(_ur, "urlopen", _raise)
    assert bd._live_daemon_port(timeout=0.1) is None


def test_a_missing_port_file_counts_as_absent(bd, tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "WORKSPACE_ROOT", tmp_path)
    assert bd._live_daemon_port(timeout=0.1) is None


def test_a_corrupt_port_file_counts_as_absent(bd, tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "WORKSPACE_ROOT", tmp_path)
    state = tmp_path / ".daemon-state"
    state.mkdir(parents=True)
    (state / "port").write_text("not-a-port", encoding="utf-8")
    assert bd._live_daemon_port(timeout=0.1) is None


# ============================================================
# 3. The range that named a port it never probed
# ============================================================

def test_the_exhausted_range_message_names_the_last_port_tried(bd, monkeypatch):
    def _busy(_p):
        raise OSError("in use")

    monkeypatch.setattr(bd, "_bind_listener", _busy)
    with pytest.raises(RuntimeError) as exc:
        bd._pick_port(31415)
    message = str(exc.value)
    assert "31464" in message, "31464 is the last port range() reaches"
    assert "31465" not in message, "31465 was never probed"


# ============================================================
# 4. The health check that died instead of reporting
# ============================================================

class _Body:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize("payload", [b"<html>hello</html>", b"", b"\xff\xfe"])
def test_a_non_json_two_hundred_falls_back_instead_of_crashing(bd, tmp_path,
                                                               monkeypatch,
                                                               capsys, payload):
    """A stale port file plus a foreign process on the port produces this."""
    _port_file(bd, tmp_path, monkeypatch)
    import urllib.request as _ur
    monkeypatch.setattr(_ur, "urlopen", lambda *a, **k: _Body(payload))
    monkeypatch.setattr(bd, "_read_heartbeat_fallback",
                        lambda: {"version": "0.0.0", "pid": 1})
    with pytest.raises(SystemExit) as exc:
        bd.check_health()
    assert exc.value.code == 1, "1 means 'fell back to heartbeat'"
    assert json.loads(capsys.readouterr().out)["version"] == "0.0.0"


def test_a_non_json_two_hundred_with_no_heartbeat_exits_two(bd, tmp_path,
                                                            monkeypatch, capsys):
    _port_file(bd, tmp_path, monkeypatch)
    import urllib.request as _ur
    monkeypatch.setattr(_ur, "urlopen", lambda *a, **k: _Body(b"nope"))
    monkeypatch.setattr(bd, "_read_heartbeat_fallback", lambda: None)
    with pytest.raises(SystemExit) as exc:
        bd.check_health()
    assert exc.value.code == 2
    capsys.readouterr()


def test_a_real_json_health_response_still_prints_and_returns(bd, tmp_path,
                                                              monkeypatch,
                                                              capsys):
    """The success path must not have been traded away for the fallback.

    The body is the shape `build_app`'s /health route actually returns. It was
    `{"status": "ok"}` until 2026-08-28, which that route has never produced, so
    the test modelled a responder that does not exist and could not tell this
    daemon from any other JSON server on the port.
    """
    _port_file(bd, tmp_path, monkeypatch)
    import urllib.request as _ur
    real = b'{"pid": 4321, "version": "1.2.3", "uptime_s": 9, "ok": true}'
    monkeypatch.setattr(_ur, "urlopen", lambda *a, **k: _Body(real))
    bd.check_health()
    assert json.loads(capsys.readouterr().out) == json.loads(real)


def test_a_stranger_answering_on_the_port_is_not_reported_as_the_daemon(
        bd, tmp_path, monkeypatch, capsys):
    """A stale port file plus any other local JSON server used to exit 0.

    The port file survives a crash, another process takes the port, and its
    perfectly readable 200 was printed as this daemon's health. The comment on
    the ValueError handler in `check_health` already described that scenario for
    an UNREADABLE body; a readable one from the same wrong process went through.
    """
    _port_file(bd, tmp_path, monkeypatch)
    import urllib.request as _ur
    monkeypatch.setattr(_ur, "urlopen",
                        lambda *a, **k: _Body(b'{"status": "ok"}'))
    with pytest.raises(SystemExit) as exc:
        bd.check_health()
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "not this daemon" in captured.err
    assert json.loads(captured.out) == {"status": "ok"}, (
        "what answered is still shown, so the operator can identify it")


@pytest.mark.parametrize("body", [
    b'{"ok": true}',                                   # no pid, no version
    b'{"ok": true, "pid": 1}',                         # no version
    b'{"ok": true, "pid": "1", "version": "1.0"}',     # pid not an int
    b'{"ok": "yes", "pid": 1, "version": "1.0"}',      # ok not the boolean
    b'[]',                                             # valid JSON, not an object
])
def test_a_near_miss_payload_is_still_not_this_daemon(bd, body):
    """Vacuity guard: the shape test must refuse as well as accept."""
    assert bd._is_bridge_health_payload(json.loads(body)) is False, body


def test_the_real_health_shape_is_accepted(bd):
    assert bd._is_bridge_health_payload(
        {"pid": 1, "version": "1.0.0", "uptime_s": 0, "ok": True}) is True


# ============================================================
# 5. The mode applied one write too late
# ============================================================

def test_a_restrictive_mode_is_in_place_before_any_content(tmp_path,
                                                           monkeypatch):
    """The race, observed at the only moment it is observable.

    The spy fires at the chmod, which under the old code ran AFTER the first
    record was written. Seeing content already there with the wide mode still
    on is the exposure window itself.
    """
    seen = {}
    real_chmod = os.chmod

    def _spy(path, mode):
        info = os.stat(path)
        seen["mode"] = stat.S_IMODE(info.st_mode)
        seen["size"] = info.st_size
        real_chmod(path, mode)

    old_umask = os.umask(0o022)
    try:
        monkeypatch.setattr(_jsonl.os, "chmod", _spy)
        target = tmp_path / "sensitive.jsonl"
        _jsonl.append_jsonl(target, {"a": 1}, mode=0o600)
    finally:
        os.umask(old_umask)

    assert seen["size"] > 0, "the spy fired before anything was written"
    assert seen["mode"] == 0o600, (
        f"the file held content at mode {seen['mode']:o} before the chmod"
    )
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_the_default_mode_is_unchanged(tmp_path):
    old_umask = os.umask(0o022)
    try:
        target = tmp_path / "log.jsonl"
        _jsonl.append_jsonl(target, {"a": 1})
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_a_strict_umask_does_not_narrow_the_requested_mode(tmp_path):
    """`os.open` masks with the umask; the chmod after restores the intent."""
    old_umask = os.umask(0o077)
    try:
        target = tmp_path / "log.jsonl"
        _jsonl.append_jsonl(target, {"a": 1})
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_an_existing_file_keeps_its_own_mode(tmp_path):
    """Only a NEW file is chmodded; appending must not re-open the question."""
    target = tmp_path / "log.jsonl"
    _jsonl.append_jsonl(target, {"a": 1}, mode=0o600)
    os.chmod(target, 0o640)
    _jsonl.append_jsonl(target, {"b": 2}, mode=0o600)
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_the_records_still_land_one_per_line(tmp_path):
    target = tmp_path / "log.jsonl"
    _jsonl.append_jsonl(target, {"a": 1}, mode=0o600)
    _jsonl.append_jsonl(target, {"b": 2}, mode=0o600)
    rows = [json.loads(ln) for ln in
            target.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows == [{"a": 1}, {"b": 2}]


def test_a_file_with_no_trailing_newline_is_still_repaired(tmp_path):
    """The pre-create must not disturb the tail repair beside it."""
    target = tmp_path / "log.jsonl"
    target.write_text('{"a": 1}', encoding="utf-8")
    _jsonl.append_jsonl(target, {"b": 2})
    rows = [json.loads(ln) for ln in
            target.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows == [{"a": 1}, {"b": 2}]
