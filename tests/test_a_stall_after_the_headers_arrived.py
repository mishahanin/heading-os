"""Shard 05-p1: exit-code contracts that a late failure walked straight out of.

Three now, not two. The third arrived from shard 05-p4 on 2026-09-02 and is
about the CONTRACT rather than a code path: ``draft-critique``'s docstring
promised three exit codes and described neither direction correctly. See the
comment above ``test_a_parse_error_no_longer_impersonates_an_unreachable_daemon``
at the bottom of this file.

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


@pytest.fixture(autouse=True)
def _reach_dream_main(unguard_main_clone):
    """`dream-shadow.main()` opens with `require_main_clone(__file__)`, which
    exits 2 from a worktree before the exit-code contract under test is
    reached. Neutralised on THIS loaded module, for the duration of one test.
    `draft-critique.py` carries no such guard and is left untouched.

    The guard is still measured, by its own owners:
    `tests/test_guarded_entry_points_refuse_from_a_worktree.py` pins through the
    AST that the call is the first statement of `main()` and is passed
    `__file__`, and `tests/test_clone_guard.py` pins that it fires.
    """
    unguard_main_clone(dream)


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


# ============================================================
# The exit-code contract that held in neither direction
# ============================================================
#
# Shard 05-p4 finding 1. The module docstring promised "0 critique produced,
# 1 usage error or no critique produced (model unavailable / missing API key /
# empty body), 2 daemon not reachable", and both halves were false.
#
# Upward: stock `argparse.ArgumentParser.error()` calls `sys.exit(2)`, so a
# typo'd flag exited through the code reserved for an unreachable daemon while
# the daemon was healthy, and `_die_no_daemon`'s advice ("use --body-file,
# which needs no daemon") is the wrong instruction for a parse error. `main`
# already answered its OWN usage errors with 1, so the module had two usage
# exits that disagreed.
#
# Downward: eight paths exit 1 that the enumeration never named, all of them
# in the tests above this line. An operator reading the docstring diagnosed a
# 401 or a version-mismatched daemon payload as a missing API key.

def test_a_parse_error_no_longer_impersonates_an_unreachable_daemon(monkeypatch,
                                                                    capsys):
    monkeypatch.setattr(sys, "argv", ["draft-critique.py", "--bogus-flag"])

    with pytest.raises(SystemExit) as se:
        critique.main()
    assert se.value.code == 1, (
        "an argparse parse error still exits 2, which this module reserves for "
        "an unreachable bridge daemon")
    err = capsys.readouterr().err
    assert "usage error" in err
    assert "not reachable" not in err, (
        "a parse error must not be reported as a daemon problem")


def test_an_unreachable_daemon_still_owns_exit_two(wired, monkeypatch, capsys):
    """The anchor. A parser that exited 1 for everything would pass the test
    above while destroying the code the whole contract is built around."""
    def _boom(*_a, **_kw):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(critique.urllib.request, "urlopen", _boom)

    with pytest.raises(SystemExit) as se:
        critique._fetch_card(ROOT, "abc123")
    assert se.value.code == 2
    assert "not reachable" in capsys.readouterr().err


def test_help_still_exits_zero_through_the_overridden_parser():
    """The other anchor. `-h` goes through `parser.exit(0)`, not `error()`;
    an override that caught both would turn the help screen into a failure."""
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(sys, "argv", ["draft-critique.py", "--help"])
        with pytest.raises(SystemExit) as se:
            critique.main()
    finally:
        monkey.undo()
    assert se.value.code == 0


_UNNAMED_EXIT_ONE_CAUSES = {
    "an HTTP error from the daemon": "http",
    "a 200 whose body is not JSON": "not json",
    "a 200 whose JSON is the wrong shape": "action-queue payload",
    "a 200 the daemon stalled on": "stalled",
    "no matching card": "no matching card",
    "an ambiguous prefix": "ambiguous prefix",
    "an unreadable --body-file": "body-file",
    "a card carrying no draft body": "no draft body",
}


def _exit_code_section() -> str:
    """The docstring's exit-code block alone, lowercased.

    Sliced, not searched whole. The Usage block three lines above already
    contains "--body-file", so a whole-docstring search reported that cause as
    documented while the enumeration never named it: measured 2026-09-02 by
    restoring the old one-line block, where seven of the eight causes failed
    and the `--body-file` one passed on the usage text.
    """
    doc = (ast.get_docstring(
        ast.parse((ROOT / "scripts" / "draft-critique.py")
                  .read_text(encoding="utf-8"))) or "").lower()
    assert "exit codes:" in doc, "the exit-code block has gone entirely"
    start = doc.index("exit codes:")
    end = doc.index("tests:", start)
    return doc[start:end]


@pytest.mark.parametrize("cause,phrase", sorted(_UNNAMED_EXIT_ONE_CAUSES.items()))
def test_the_exit_one_enumeration_names_every_way_this_module_exits_one(cause,
                                                                       phrase):
    assert phrase in _exit_code_section(), (
        f"the exit-code block does not name {cause}, which exits 1; an "
        f"operator reading it diagnoses that as a missing API key")


def test_the_exit_code_block_still_reserves_two_for_the_daemon():
    """The anchor for the docstring guard. A block that listed every phrase
    while losing the 0/2 contract would pass every case above."""
    section = _exit_code_section()
    assert "0  critique produced" in section
    assert "2  bridge daemon not reachable, and nothing else" in section
