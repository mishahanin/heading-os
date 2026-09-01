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
    monkeypatch.setattr(replay, "scrutiny_dir", lambda p=d: p)
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
    # The EMPTY half. `--from ""` is a value the operator typed, and the
    # resolver tests it with `is not None` rather than truthiness for exactly
    # this case: an empty string is falsy, so a truthiness test reads the pair
    # as "neither given" and falls through to the quarter default, discarding
    # the half that was supplied. Measured 2026-09-01 by swapping the two
    # comparisons for `bool(...)`: with only the non-empty cases above, all 80
    # tests over this module stayed green.
    ["--from", ""],
    ["--to", ""],
])
def test_half_a_range_is_refused_rather_than_dropped(scrutiny, tmp_path, argv):
    out = tmp_path / "sheet.md"
    assert replay.main(argv + ["--out", str(out)]) == EXIT_ARGUMENT_ERROR
    assert not out.exists(), "a refused window must not write a sheet"


@pytest.mark.parametrize("given,named,other", [
    (["--from", "2026-03-01"], "--from", "--to"),
    (["--to", "2026-03-31"], "--to", "--from"),
])
def test_the_named_half_of_the_range_is_named_in_the_error(scrutiny, capsys,
                                                           given, named, other):
    """The message must name the half that WAS supplied, not merely mention it.

    This asserted `"--from" in err` after a `--from`-only run. Both flag names
    are already in the fixed prefix ("--from and --to must be given together"),
    so the assertion was satisfied by text that has nothing to do with which
    half was seen: swapping the conditional to name the WRONG half left all 80
    tests over this module green (measured 2026-09-01). The claim lives in the
    "got only" clause, so that is what is read here, in both directions.
    """
    replay.main(given)
    err = capsys.readouterr().err
    assert f"got only {named}" in err, err
    assert f"got only {other}" not in err, err


def test_a_well_formed_explicit_range_still_finds_its_report(scrutiny, tmp_path):
    """The anchor. The refusals above must not refuse a legitimate window."""
    out = tmp_path / "sheet.md"
    assert replay.main(["--from", "2026-03-01", "--to", "2026-03-31",
                        "--out", str(out)]) == 0
    assert "[B1]" in out.read_text(encoding="utf-8")


def test_the_window_includes_its_own_endpoints_and_nothing_past_them(scrutiny):
    """Both bounds, measured on the selection rather than on the message.

    The one report the fixture carries sits in the middle of every window this
    file asks for, so the comparison had no case on either edge and no case
    outside. Measured 2026-09-01 over 80 tests: dropping the upper bound,
    dropping the lower bound, and turning the inclusive `<=` pair strict all
    three left the suite green.
    """
    for stem in ("2026-02-28", "2026-03-01", "2026-03-31", "2026-04-01"):
        (scrutiny / f"{stem}_execution.md").write_text(
            "[B1] (conf: 90) a placeholder finding\n", encoding="utf-8")

    picked = sorted(p.stem[:10] for p in replay.list_reports_in_range(
        replay.parse_date_arg("2026-03-01"), replay.parse_date_arg("2026-03-31")))

    assert picked == ["2026-03-01", "2026-03-15", "2026-03-31"], picked


@pytest.mark.parametrize("since", ["90d", "1d", "0d"])
def test_a_well_formed_since_is_still_accepted(scrutiny, since):
    """`--since` reads the clock by design; only its PARSE is asserted here.

    Exit 3 ("no reports in range") and exit 0 are both fine - what must not
    happen is exit 2, which would mean the parse guard swallowed a valid value.
    """
    assert replay.main(["--since", since]) != EXIT_ARGUMENT_ERROR
