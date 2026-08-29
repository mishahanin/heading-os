"""`scrutinize-replay.py` must answer for its own date window.

Two faults, both measured 2026-08-30 by driving `main()`.

A malformed window value left the module as an uncaught `ValueError` and
interpreter exit 1: `--since abc` raised `time data 'abc' does not match format
'%Y-%m-%d'`, `--since d` raised `invalid literal for int() with base 10: ''`.
The file's own exit-code table reserves 2 for an argument error, and a traceback
is neither that nor any other documented code.

And exactly one of `--from` / `--to` was silently DISCARDED. The resolver read
`elif args.date_from and args.date_to`, so `--from 2026-03-01` alone fell
through to the current-quarter default. With one report dated 2026-03-15 on
disk, the run warned "no scrutiny reports found in range 2026-07-01 to
2026-08-30" and returned 3: a window the operator never asked for, printed back
at him as though he had.

No host clock is read anywhere below. Every date is a literal, and the
half-window case is judged on the exit code, not on which quarter today is.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load():
    path = ROOT / "scripts" / "scrutinize-replay.py"
    spec = importlib.util.spec_from_file_location("scrutinize_replay_window", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


replay = _load()

EXIT_ARGUMENT_ERROR = 2


@pytest.fixture
def scrutiny(tmp_path, monkeypatch):
    """An isolated scrutiny dir holding ONE report, dated 2026-03-15."""
    from scripts.utils import scrutinize_record as rec
    d = tmp_path / "scrutiny"
    d.mkdir()
    monkeypatch.setattr(rec, "record_path", lambda: d / "runs.jsonl")
    monkeypatch.setattr(replay, "SCRUTINY_DIR", d)
    (d / "2026-03-15_execution.md").write_text(
        "[B1] (conf: 90) the sweep reads a path it never resolved\n"
        "Location: scripts/example.py:41\n"
        "Evidence: the resolve call is two frames up\n",
        encoding="utf-8")
    assert list(d.glob("*.md")), "the corpus this test reasons over must be non-empty"
    return d


@pytest.mark.parametrize("bad", ["abc", "d", "2026-13-99", "", "xd", "2026/03/01"])
def test_a_malformed_since_is_an_argument_error_not_a_traceback(scrutiny, bad):
    assert replay.main(["--since", bad]) == EXIT_ARGUMENT_ERROR


@pytest.mark.parametrize("argv", [
    ["--from", "abc", "--to", "2026-03-31"],
    ["--from", "2026-03-01", "--to", "not-a-date"],
])
def test_a_malformed_explicit_range_is_an_argument_error_too(scrutiny, argv):
    assert replay.main(argv) == EXIT_ARGUMENT_ERROR


@pytest.mark.parametrize("argv", [
    ["--from", "2026-03-01"],
    ["--to", "2026-03-31"],
])
def test_half_a_range_is_refused_rather_than_dropped(scrutiny, tmp_path, argv):
    out = tmp_path / "sheet.md"
    assert replay.main(argv + ["--out", str(out)]) == EXIT_ARGUMENT_ERROR
    assert not out.exists(), "a refused window must not write a sheet"


def test_the_named_half_of_the_range_is_named_in_the_error(scrutiny, capsys):
    replay.main(["--from", "2026-03-01"])
    assert "--from" in capsys.readouterr().err


def test_a_well_formed_explicit_range_still_finds_its_report(scrutiny, tmp_path):
    """The anchor. The refusals above must not refuse a legitimate window."""
    out = tmp_path / "sheet.md"
    assert replay.main(["--from", "2026-03-01", "--to", "2026-03-31",
                        "--out", str(out)]) == 0
    assert "[B1]" in out.read_text(encoding="utf-8")


@pytest.mark.parametrize("since", ["90d", "1d", "0d"])
def test_a_well_formed_since_is_still_accepted(scrutiny, since):
    """`--since` reads the clock by design; only its PARSE is asserted here.

    Exit 3 ("no reports in range") and exit 0 are both fine - what must not
    happen is exit 2, which would mean the parse guard swallowed a valid value.
    """
    assert replay.main(["--since", since]) != EXIT_ARGUMENT_ERROR
