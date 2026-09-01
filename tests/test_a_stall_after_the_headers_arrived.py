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

import ast
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


# ============================================================
# The same shape one layer down: a read that cannot be decoded
# ============================================================

def test_a_body_file_that_is_not_utf8_exits_one_with_a_message(monkeypatch,
                                                               capsys, tmp_path):
    """`UnicodeDecodeError` is a `ValueError`, so `except OSError` walked past it.

    MEASURED 2026-09-01 against the shipped script: one 0xff byte in the file
    produced `UnicodeDecodeError: invalid start byte` as a traceback and exit 1,
    from a module whose docstring promises 0, 1 and 2 and whose neighbouring
    handler exists to turn an unreadable body file into exactly this message.
    The same defect class as the stall above, one layer down.
    """
    bad = tmp_path / "draft.txt"
    bad.write_bytes(b"hello \xff world\n")
    monkeypatch.setattr(sys, "argv",
                        ["draft-critique.py", "--body-file", str(bad)])

    assert critique.main() == 1
    err = capsys.readouterr().err
    assert "cannot read --body-file" in err
    assert "Traceback" not in err
    assert str(bad) in err, "the operator is not told which file to go and look at"


def test_a_readable_body_file_is_still_read(monkeypatch, capsys, tmp_path):
    """The negative case: the widened handler must not swallow a good file.

    `critique_draft` is stubbed to None, which is the documented "no critique
    produced" exit 1 - a DIFFERENT 1 from the one above, and told apart by the
    message, which is why both are asserted rather than the code alone.
    """
    good = tmp_path / "draft.txt"
    good.write_text("Dear Ms Brand, the quote stands at 347,850.\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv",
                        ["draft-critique.py", "--body-file", str(good)])
    monkeypatch.setattr(critique.draft_critique, "critique_draft",
                        lambda *_a, **_kw: None)

    assert critique.main() == 1
    err = capsys.readouterr().err
    assert "cannot read --body-file" not in err
    assert "no critique produced" in err


def test_an_undecodable_daemon_state_file_reads_as_absent(tmp_path):
    """`_read_state` promises `str | None`, and raised a third thing.

    A `.daemon-state/token` holding a non-UTF-8 byte is a truncated or foreign
    write, which is the same situation as an absent one from this helper's point
    of view. It used to raise out of a function whose annotation says otherwise.
    """
    state = tmp_path / ".daemon-state"
    state.mkdir()
    (state / "token").write_bytes(b"\xff\xfe not utf-8")
    assert critique._read_state(tmp_path, "token") is None


def test_an_undecodable_token_takes_the_documented_no_daemon_exit(tmp_path,
                                                                  capsys):
    """The consequence, at the seam the operator meets: exit 2, not a traceback."""
    state = tmp_path / ".daemon-state"
    state.mkdir()
    (state / "token").write_bytes(b"\xff\xfe")
    (state / "port").write_text("8765", encoding="utf-8")

    with pytest.raises(SystemExit) as se:
        critique._fetch_card(tmp_path, "abc123")
    assert se.value.code == 2
    err = capsys.readouterr().err
    assert "not reachable" in err
    assert "Traceback" not in err


def test_a_readable_state_file_is_still_returned(tmp_path):
    """The other direction, so the guard above is not passing over a helper that
    answers None for everything."""
    state = tmp_path / ".daemon-state"
    state.mkdir()
    (state / "token").write_text("  tok-31c  \n", encoding="utf-8")
    assert critique._read_state(tmp_path, "token") == "tok-31c"


def test_every_state_and_file_read_in_this_module_covers_the_decode_class():
    """The sibling sweep, because the first fix landed in one of two copies.

    Both `read_text` calls in this module carried `except OSError` and neither
    covered `UnicodeDecodeError`. Asked of the syntax tree so a third one added
    later cannot inherit the same hole quietly.
    """
    tree = ast.parse((ROOT / "scripts" / "draft-critique.py")
                     .read_text(encoding="utf-8"))
    reads = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute)
             and node.func.attr == "read_text"]
    assert len(reads) >= 2, (
        f"only {len(reads)} text reads found; this sweep has stopped reaching "
        f"the calls it was written for")

    guarded, uncovered = 0, []
    for tried in [n for n in ast.walk(tree) if isinstance(n, ast.Try)]:
        if not any("read_text" in ast.unparse(stmt) for stmt in tried.body):
            continue
        guarded += 1
        # A decode failure is a `ValueError`; either spelling catches it, and
        # a bare `except:` or `except Exception` does too.
        covers = any(
            h.type is None
            or {"UnicodeDecodeError", "ValueError", "Exception", "BaseException"}
            & {n.id for n in ast.walk(h.type) if isinstance(n, ast.Name)}
            for h in tried.handlers
        )
        if not covers:
            uncovered.append(ast.unparse(tried.body[0]))
    assert guarded == len(reads), (
        f"{len(reads)} text reads but only {guarded} of them sit in a try; a "
        f"read with no handler at all is invisible to this sweep")
    assert uncovered == [], (
        "these text reads are guarded by handlers that cannot see a decode "
        "failure:\n  " + "\n  ".join(uncovered))


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
