"""`impeccable_engine` hardened one `read_text` and left three beside it.

`tests/test_a_never_raises_reader_that_died_on_bytes_it_could_not_decode.py`
fixed the read at `run_detector`'s line 298 and added an explicit
`except UnicodeDecodeError` for it. The same module holds three more UTF-8 reads,
and none of them was touched:

  * `get_pinned_version` (line 95) reads `scripts/.impeccable-version` with NO
    `try` at all, and `run_detector` calls it through `resolve_cli()` on the line
    BEFORE its own `try` opens. So the function whose docstring says "Never
    raises. Every failure ... comes back as a human-readable string" still
    raised, on the same byte, three lines above the handler added to stop it.
  * `load_profiles` (line 145) catches `(json.JSONDecodeError, OSError)` around a
    `read_text`. Its docstring promises "a missing or malformed file falls back
    to a screen-only config ... a config we cannot read must make the check
    noisier, never quieter", and a handler that cannot see the error cannot
    deliver that fallback.
  * `load_baseline` (line 430) carries the same two-element tuple around the same
    read, and reads an unreadable baseline as an EMPTY freeze.

`UnicodeDecodeError` subclasses `ValueError`, so it is a sibling of
`json.JSONDecodeError` and not a subclass of `OSError`. Neither element of either
tuple sees it.

MEASURED 2026-09-01 against the unfixed module, each function driven directly
with a real non-UTF-8 byte on disk (`0xff`):

    1  get_pinned_version RAISED UnicodeDecodeError 'utf-8' codec can't decode
                          byte 0xff in position 16: invalid start byte
    1b run_detector       RAISED UnicodeDecodeError (same byte, through
                          resolve_cli, outside its own try)
    2  load_profiles      RAISED UnicodeDecodeError
    3  load_baseline      RAISED UnicodeDecodeError

The victim is the same one the earlier file names: `_collect_deep` in
`scripts/visual-discipline-check.py` calls `impeccable_engine.deep_findings`
with no `try`, so the raise reaches `_run_audit` and the whole scan dies with a
traceback instead of degrading to the regex engine.

After the fix the four readings are a fallback pin, a returned reason, a
screen-only profile with its warning, and an empty freeze with a note. Every
test below also carries the direction that keeps the fix from being "swallow
everything": a good file on the same path still parses.

No Node, no npx and no network. `resolve_cli` is replaced with a Python child
wherever the detector has to run.

Run: .venv/bin/python -m pytest
     tests/test_a_decode_fix_that_landed_in_one_reader_of_four.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import impeccable_engine as IE  # noqa: E402

UNDECODABLE = b"\xff\xfe\x00bad"


@pytest.fixture()
def pin_root(monkeypatch, tmp_path):
    """A workspace root whose version-pin file is the only one that exists."""
    monkeypatch.setattr(IE, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(IE, "VERSION_PIN_FILE", "pin.txt")
    return tmp_path


# ============================================================
# Reader 1 - the version pin, read with no try at all
# ============================================================
def test_an_undecodable_pin_file_falls_back_instead_of_raising(pin_root):
    """THE case for this reader. It had no handler of any kind."""
    (pin_root / "pin.txt").write_bytes(b"impeccable@9.9.9" + UNDECODABLE)

    assert IE.get_pinned_version() == IE.FALLBACK_PIN


def test_an_undecodable_pin_file_says_so_on_stderr(pin_root, capsys):
    """Swallowing it would leave the operator on a pin nobody chose, silently."""
    (pin_root / "pin.txt").write_bytes(UNDECODABLE)

    IE.get_pinned_version()

    assert "pin" in capsys.readouterr().err.lower()


def test_a_readable_pin_is_still_the_pin(pin_root):
    """The control. A handler that always fell back would pass the two above and
    quietly un-pin a package this integration executes at call time.

    The value is deliberately NOT `FALLBACK_PIN`. Written as `impeccable@3.5.0`,
    which is what `FALLBACK_PIN` currently holds, this assertion could not fail:
    measured 2026-09-01, replacing the whole body of `get_pinned_version` with
    `return FALLBACK_PIN` left it green while every sibling here went red.
    """
    (pin_root / "pin.txt").write_text("impeccable@4.1.2\n", encoding="utf-8")

    assert IE.get_pinned_version() == "impeccable@4.1.2"
    assert IE.get_pinned_version() != IE.FALLBACK_PIN, (
        "the fixture pin equals the fallback, so this test cannot tell a read "
        "from a fallback"
    )


def test_an_absent_pin_file_still_takes_the_documented_fallback(pin_root):
    assert IE.get_pinned_version() == IE.FALLBACK_PIN


def test_the_never_raises_promise_holds_through_the_pin_read(pin_root, tmp_path):
    """The one that makes this a defect rather than a tidy-up.

    `run_detector` calls `resolve_cli()` one line ABOVE its own `try`, and
    `resolve_cli` calls `get_pinned_version`. So an undecodable pin file broke
    the promise in that docstring from outside every handler it has.
    """
    (pin_root / "pin.txt").write_bytes(UNDECODABLE)

    findings, error = IE.run_detector([str(tmp_path)])

    assert findings == []
    assert isinstance(error, str) or error is None


# ============================================================
# Reader 2 - the profile config, whose fallback is the point
# ============================================================
def test_an_undecodable_profile_config_falls_back_noisily(tmp_path):
    """The docstring's own rule: unreadable must be noisier, never quieter."""
    path = tmp_path / "profiles.json"
    path.write_bytes(b'{"profiles": {"screen": {}}, "x": "' + UNDECODABLE + b'"}')

    profiles, warning = IE.load_profiles(path)

    assert warning, "an unreadable config fell back in silence"
    assert profiles == IE._SAFE_PROFILES
    assert profiles["profiles"]["screen"]["suppress"] == {}, (
        "the fallback must suppress nothing, or an unreadable config makes the "
        "gate quieter instead of noisier"
    )


def test_a_readable_profile_config_is_still_loaded(tmp_path):
    """The control. A handler that fell back on every file would disable every
    calibration the operator wrote and report a warning nobody reads."""
    path = tmp_path / "profiles.json"
    path.write_text(
        '{"profiles": {"screen": {"description": "d", "suppress": {"r": 1}}}}',
        encoding="utf-8",
    )

    profiles, warning = IE.load_profiles(path)

    assert warning is None
    assert profiles["profiles"]["screen"]["suppress"] == {"r": 1}


def test_malformed_json_still_reports_its_own_reason(tmp_path):
    """Undecodable bytes and bad JSON must not collapse into one message: they
    need different things looked at, which is the rule the sibling file set."""
    path = tmp_path / "profiles.json"
    path.write_text("{oops", encoding="utf-8")

    _, warning = IE.load_profiles(path)

    assert warning and "unreadable" in warning


# ============================================================
# Reader 3 - the frozen baseline
# ============================================================
def test_an_undecodable_baseline_reads_as_an_empty_freeze(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_bytes(b'{"files": {"a' + UNDECODABLE + b'": {}}}')

    assert IE.load_baseline(path) == {}


def test_an_undecodable_baseline_is_not_swallowed_in_silence(tmp_path, capsys):
    """An empty freeze un-suppresses every frozen finding, so the gate goes red
    for a reason that is nowhere in the output unless this line exists."""
    path = tmp_path / "baseline.json"
    path.write_bytes(UNDECODABLE)

    IE.load_baseline(path)

    assert "baseline" in capsys.readouterr().err.lower()


def test_a_readable_baseline_is_still_read(tmp_path):
    """The control, and it is the one that binds: a `load_baseline` that always
    answered {} would pass both tests above and un-freeze the whole file."""
    path = tmp_path / "baseline.json"
    path.write_text('{"files": {"docs/a.html": {"rounded_oversized": 2}}}',
                    encoding="utf-8")

    assert IE.load_baseline(path) == {"docs/a.html": {"rounded_oversized": 2}}


def test_an_absent_baseline_is_still_an_empty_freeze(tmp_path):
    assert IE.load_baseline(tmp_path / "nothing.json") == {}
