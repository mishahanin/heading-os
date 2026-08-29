"""`run_detector` promised "Never raises" and raised on undecodable output.

`scripts/utils/impeccable_engine.run_detector` sends the detector's stdout to a
file and reads it back with `read_text(encoding="utf-8")`. The read sits inside
a `try` that caught `subprocess.TimeoutExpired` and `OSError`.
`UnicodeDecodeError` subclasses ValueError, so neither handler saw it.

Measured 2026-08-30: a child writing `b"\\xff\\xfe\\x00bad"` raised
`UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0` out of
`run_detector` at the `read_text` call, three lines below a docstring that says
every failure "comes back as a human-readable string that the caller reports and
moves past".

The victim is `scripts/visual-discipline-check.py`. Its whole design is that a
broken deep engine degrades to the regex engine rather than taking the run down,
and it got a raw traceback instead. Guarding the caller would have papered over
it; the promise belongs to this function.

Nothing here runs the real `impeccable` CLI. `resolve_cli` is replaced with a
short Python child, so no Node, no npx and no network.

Run: python3 -m pytest tests/test_a_never_raises_reader_that_died_on_bytes_it_could_not_decode.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import impeccable_engine as IE  # noqa: E402


def _child(monkeypatch, source: str) -> None:
    """Stand in for the detector with a Python one-liner. Never touches Node."""
    monkeypatch.setattr(IE, "resolve_cli", lambda: [sys.executable, "-c", source])


WRITE_UNDECODABLE = "import sys; sys.stdout.buffer.write(bytes([255, 254, 0]) + b'bad')"
WRITE_VALID_JSON = "import sys; sys.stdout.write('[]')"
WRITE_BAD_JSON = "import sys; sys.stdout.write('{oops')"


def test_undecodable_output_comes_back_as_a_reason_not_a_traceback(monkeypatch, tmp_path):
    """THE case."""
    _child(monkeypatch, WRITE_UNDECODABLE)

    findings, error = IE.run_detector([str(tmp_path)])

    assert findings == []
    assert error is not None
    assert "UTF-8" in error
    assert "deep design checks skipped" in error


def test_the_reason_is_not_the_one_given_for_bad_json(monkeypatch, tmp_path):
    """Undecodable bytes and malformed JSON need different things looked at, so
    they must not share a sentence."""
    _child(monkeypatch, WRITE_UNDECODABLE)
    _, undecodable = IE.run_detector([str(tmp_path)])

    _child(monkeypatch, WRITE_BAD_JSON)
    _, bad_json = IE.run_detector([str(tmp_path)])

    assert undecodable != bad_json
    assert "not JSON" in bad_json
    assert "not JSON" not in undecodable


def test_the_caller_degrades_instead_of_dying(monkeypatch, tmp_path):
    """`deep_findings` is what `visual-discipline-check.py` calls. It must return
    a reason, so the regex engine still runs."""
    _child(monkeypatch, WRITE_UNDECODABLE)

    findings, error = IE.deep_findings(str(tmp_path))

    assert findings == []
    assert error and "UTF-8" in error


def test_valid_output_is_still_read_normally(monkeypatch, tmp_path):
    """The negative control. A handler that swallowed every read would pass the
    three tests above and report zero findings on every real run."""
    _child(monkeypatch, WRITE_VALID_JSON)

    findings, error = IE.run_detector([str(tmp_path)])

    assert findings == []
    assert error is None


def test_a_real_finding_still_arrives(monkeypatch, tmp_path):
    """The stronger control: a non-empty payload survives the same path."""
    payload = ('import sys; sys.stdout.write(\'[{"antipattern": "side-tab", '
               '"file": "docs/moneypenny.html", "line": 7}]\')')
    _child(monkeypatch, payload)

    findings, error = IE.run_detector([str(tmp_path)])

    assert error is None
    assert len(findings) == 1
    assert findings[0]["antipattern"] == "side-tab"


@pytest.mark.parametrize("source,fragment", [
    (WRITE_UNDECODABLE, "UTF-8"),
    (WRITE_BAD_JSON, "not JSON"),
    ("import sys; sys.stdout.write('\"a string\"')", "not a finding list"),
    ("import sys; sys.exit(3)", "no output"),
])
def test_every_failure_shape_returns_rather_than_raises(monkeypatch, tmp_path,
                                                        source, fragment):
    """The promise is "Never raises", and one uncaught shape is enough to break
    it. Each row drives a different way the detector can misbehave."""
    _child(monkeypatch, source)

    findings, error = IE.run_detector([str(tmp_path)])

    assert findings == []
    assert error is not None and fragment in error
