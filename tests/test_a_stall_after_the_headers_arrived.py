"""Shard 05-p1: two exit-code contracts that a late failure walked straight out of.

* ``draft-critique._fetch_card`` handles HTTPError, URLError and a
  non-JSON body, and its comments say why each was added: a docstring
  promising three exit codes must not be left through a traceback.
  ``urlopen``'s timeout covers the connect and the headers only. A stall
  during ``r.read()`` raises TimeoutError, and urllib does NOT wrap a
  body-read failure in URLError - so it matched no handler, and a daemon that
  accepts, sends ``200 OK`` and then hangs produced exactly the failure the
  neighbouring comment says was eliminated.

* ``dream-shadow.main`` wraps ``gather()`` so a scan failure returns 2, per a
  docstring promising "0 always on a clean run; 2 script error". Rendering and
  writing the report sat OUTSIDE that guard, so an OSError from a read-only or
  full outputs volume exited 1 - neither documented code, from a tool its
  consumers are told is advisory with no gate.

The other five findings in this shard were already fixed: the cache key now
carries `pages` and `dpi`, the box walk lowercases during the mapping and
shares one whitespace definition with `_normalize_text`, the report refuses an
ambiguous basename, and the `_cache_key` docstring no longer claims a Windows
case-fold that `Path.resolve()` does not do.

Run: python3 -m pytest tests/test_a_stall_after_the_headers_arrived.py
"""
from __future__ import annotations

import importlib.util
import io
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


critique = _load("draft_critique_under_test", "scripts/draft-critique.py")
dream = _load("dream_shadow_under_test", "scripts/dream-shadow.py")


# ============================================================
# The read that stalled after the headers
# ============================================================

@pytest.fixture()
def wired(monkeypatch):
    """A daemon that is reachable and authenticated; only the read varies."""
    monkeypatch.setattr(critique, "_read_state",
                        lambda _root, key: "tok" if key == "token" else "8765")


class _StallingResponse(io.RawIOBase):
    """Headers arrived; the body never does."""

    def __init__(self, exc):
        self._exc = exc

    def read(self, *_a, **_kw):
        raise self._exc

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


@pytest.mark.parametrize("exc", [
    TimeoutError("timed out"),          # the reported reproduction
    ConnectionResetError("reset by peer"),
    OSError("transport went away"),
])
def test_a_stall_during_the_body_read_exits_cleanly(wired, monkeypatch, capsys, exc):
    monkeypatch.setattr(critique.urllib.request, "urlopen",
                        lambda *_a, **_kw: _StallingResponse(exc))
    with pytest.raises(SystemExit) as se:
        critique._fetch_card(ROOT, "abc123")
    assert se.value.code == 1
    err = capsys.readouterr().err
    assert "stalled" in err
    assert "Traceback" not in err


def test_the_unreachable_daemon_keeps_its_own_path(wired, monkeypatch, capsys):
    """`URLError` IS an OSError, so the new handler must sit after it."""
    def _boom(*_a, **_kw):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(critique.urllib.request, "urlopen", _boom)

    with pytest.raises(SystemExit):
        critique._fetch_card(ROOT, "abc123")
    err = capsys.readouterr().err
    assert "stalled" not in err, "an unreachable daemon is not a stall"


def test_an_http_error_keeps_its_own_path(wired, monkeypatch, capsys):
    def _boom(*_a, **_kw):
        raise urllib.error.HTTPError("u", 503, "nope", {}, None)
    monkeypatch.setattr(critique.urllib.request, "urlopen", _boom)

    with pytest.raises(SystemExit):
        critique._fetch_card(ROOT, "abc123")
    assert "HTTP 503" in capsys.readouterr().err


def test_a_non_json_body_keeps_its_own_path(wired, monkeypatch, capsys):
    class _Html(io.RawIOBase):
        def read(self, *_a, **_kw):
            return b"<html>not json</html>"

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(critique.urllib.request, "urlopen",
                        lambda *_a, **_kw: _Html())
    with pytest.raises(SystemExit):
        critique._fetch_card(ROOT, "abc123")
    assert "not JSON" in capsys.readouterr().err


def test_the_handler_order_puts_urlerror_first():
    """Source-pinned: `URLError` is an OSError subclass, and Python takes the
    FIRST matching handler. Reordering these two silently reroutes every
    unreachable-daemon run into the stall message."""
    src = (ROOT / "scripts" / "draft-critique.py").read_text(encoding="utf-8")
    assert src.index("except urllib.error.URLError:") < src.index("except OSError as e:")


# ============================================================
# The report write outside the guard
# ============================================================

def _result():
    """The shape `render_report` reads: it also needs `memory_dir`."""
    return {"dormant": [], "merge": {"ok": True, "pairs": []},
            "memory_dir": "/srv/auto-memory"}


def _run_main(monkeypatch, argv=("dream-shadow.py", "--quiet")):
    monkeypatch.setattr(sys, "argv", list(argv))
    return dream.main()


def test_a_failing_report_write_exits_two(monkeypatch, capsys):
    """The documented code for a script error, not the 1 a traceback gives."""
    monkeypatch.setattr(dream, "gather", _result)

    def _boom(*_a, **_kw):
        raise OSError("read-only file system")
    monkeypatch.setattr(dream, "write_report", _boom)

    assert _run_main(monkeypatch) == 2
    err = capsys.readouterr().err
    assert "could not write its report" in err


def test_a_failing_render_exits_two(monkeypatch, capsys):
    monkeypatch.setattr(dream, "gather", _result)

    def _boom(*_a, **_kw):
        raise KeyError("missing section")
    monkeypatch.setattr(dream, "render_report", _boom)

    assert _run_main(monkeypatch) == 2


def test_a_failing_gather_still_exits_two(monkeypatch, capsys):
    """The older guard must survive the new one being added beside it."""
    def _boom():
        raise RuntimeError("scan blew up")
    monkeypatch.setattr(dream, "gather", _boom)

    assert _run_main(monkeypatch) == 2
    assert "dream-shadow failed" in capsys.readouterr().err


def test_a_clean_run_still_exits_zero(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(dream, "gather", _result)
    monkeypatch.setattr(dream, "write_report",
                        lambda _text, _now: tmp_path / "report.md")

    assert _run_main(monkeypatch) == 0
    assert "ERROR" not in capsys.readouterr().err


def test_no_report_skips_the_write_and_still_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(dream, "gather", _result)

    def _never(*_a, **_kw):
        raise AssertionError("--no-report must not write")
    monkeypatch.setattr(dream, "write_report", _never)

    assert _run_main(monkeypatch,
                     ("dream-shadow.py", "--quiet", "--no-report")) == 0
