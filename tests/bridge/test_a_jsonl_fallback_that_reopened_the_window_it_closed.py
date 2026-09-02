"""`append_jsonl`'s pre-create closes a disclosure window. Its error path reopened it.

`append_jsonl` creates a new log EMPTY at the caller's mode before any content
goes in, precisely so a caller asking for 0o600 never has a world-readable file
that already holds records. The comment saying so sat above a `try` whose
`except OSError` only logged, after which `path.open("a")` created the file at
the process umask, wrote the record, and only THEN chmodded. On that branch the
window was exactly the one the pre-create exists to close.

These logs carry investor sends, approved cards and critical items. On a
multi-user host the window is a real disclosure, so it is measured here rather
than argued about.

**The measurement is taken at the moment of the write, not afterwards.**
Asserting the final mode passes against the broken code too, because the trailing
chmod does eventually land; the defect is entirely a matter of ORDER. So the
tests below wrap the handle `append_jsonl` writes through and record the file's
permission bits at the instant the first byte is written.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon import _jsonl  # noqa: E402

pytestmark = pytest.mark.skipif(
    not hasattr(os, "fchmod"),
    reason="POSIX permission bits; Windows chmod is a partial no-op",
)


class _ModeSpy:
    """A file proxy that records the target's mode bits at each write."""

    def __init__(self, handle: "object", path: Path, seen: list[int]):
        self._handle = handle
        self._path = path
        self._seen = seen

    def write(self, data):
        self._seen.append(self._path.stat().st_mode & 0o777)
        return self._handle.write(data)

    def __getattr__(self, name):
        return getattr(self._handle, name)

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)


@pytest.fixture
def modes_at_write(monkeypatch):
    """Return a list that fills with the file mode seen at each write call."""
    seen: list[int] = []
    real_open = Path.open

    def spying_open(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        if args and isinstance(args[0], str) and "a" in args[0]:
            return _ModeSpy(handle, self, seen)
        return handle

    monkeypatch.setattr(Path, "open", spying_open)
    return seen


@pytest.fixture
def permissive_umask():
    """0o022, so a file created by `open("a")` lands at 0o644 and the window shows."""
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def _break_the_precreate(monkeypatch):
    real_open = os.open

    def refusing_open(path, flags, *rest):
        if str(path).endswith(".jsonl"):
            raise OSError(24, "Too many open files")
        return real_open(path, flags, *rest)

    monkeypatch.setattr(os, "open", refusing_open)


def test_the_fallback_path_narrows_the_file_before_any_content(
        tmp_path, monkeypatch, permissive_umask, modes_at_write):
    """THE FINDING. Pre-create fails; the record must still never touch 0o644."""
    _break_the_precreate(monkeypatch)
    log = tmp_path / "investor-sends.jsonl"

    _jsonl.append_jsonl(log, {"to": "lp@example.invalid", "amount": 4_150_000},
                        mode=0o600)

    assert modes_at_write, "the spy never saw a write; the harness is broken"
    assert modes_at_write[0] == 0o600, (
        f"content was written while the file was {oct(modes_at_write[0])}: the "
        f"pre-create's error path reopened the disclosure window"
    )
    assert log.stat().st_mode & 0o777 == 0o600
    assert json.loads(log.read_text(encoding="utf-8").strip())["amount"] == 4_150_000


def test_the_normal_path_still_narrows_before_content(
        tmp_path, permissive_umask, modes_at_write):
    """ANCHOR for the harness. The unbroken path must read 0o600 too.

    Without this the test above could be passing on a spy that measures the
    wrong thing, or on a fixture that silently never runs.
    """
    log = tmp_path / "critical.jsonl"
    _jsonl.append_jsonl(log, {"item": "x"}, mode=0o600)
    assert modes_at_write[0] == 0o600
    assert log.stat().st_mode & 0o777 == 0o600


def test_the_spy_can_see_a_wide_file(tmp_path, permissive_umask, modes_at_write):
    """ANCHOR for the harness, the other way round.

    If the spy reported 0o600 whatever the file's real mode, every assertion
    above would be vacuous. A caller asking for 0o644 must be SEEN to get it.
    """
    log = tmp_path / "wide.jsonl"
    _jsonl.append_jsonl(log, {"item": "x"}, mode=0o644)
    assert modes_at_write[0] == 0o644


def test_the_fallback_still_appends_rather_than_refusing(
        tmp_path, monkeypatch, permissive_umask):
    """ANCHOR. A narrowing that refused every write would satisfy the finding.

    The pre-create's error path exists because a dropped critical mark is worse
    than a glued line; it must keep writing, and keep one record per line.
    """
    _break_the_precreate(monkeypatch)
    log = tmp_path / "done-log.jsonl"
    _jsonl.append_jsonl(log, {"n": 1}, mode=0o600)
    _jsonl.append_jsonl(log, {"n": 2}, mode=0o600)

    lines = log.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["n"] for line in lines] == [1, 2]


def test_an_existing_file_is_not_re_narrowed(tmp_path, permissive_umask,
                                             modes_at_write):
    """ANCHOR. The fix must fire only on creation, never on a later append.

    A file the operator deliberately widened must keep its own mode; a chmod on
    every append would quietly overwrite that choice.
    """
    log = tmp_path / "existing.jsonl"
    log.write_text('{"n": 0}\n', encoding="utf-8")
    os.chmod(log, 0o664)

    _jsonl.append_jsonl(log, {"n": 1}, mode=0o600)

    assert log.stat().st_mode & 0o777 == 0o664
    assert modes_at_write[0] == 0o664
