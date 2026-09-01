#!/usr/bin/env python3
"""Two readers caught the error and never said which file they dropped.

This defect class has two halves, and through 2026-09-01 the campaign kept
landing the first and leaving the second. The first half is the crash: a read
that raises `UnicodeDecodeError` out of a function whose docstring promises a
degraded return, because that exception is a `ValueError` and a SIBLING of
`json.JSONDecodeError` rather than a subclass, so the decode fails inside the
read before any parse is entered.

The second half is SILENCE. A handler that catches the error and returns an
empty answer without naming the file turns a count into a lower bound that
reads as a total, and the caller cannot tell a healthy empty from an unreadable
one.

Both readers here had the first half fixed earlier the same day and the second
half left.

`read_inbox` returns `_empty()`. That value is byte-identical to the answer for
a healthy mailbox with no mail in it, so the dashboard rendered an empty inbox
panel meaning "this file will not decode" that looked exactly like one meaning
"you have no mail".

`load_jsonl` had no handler at all, and no docstring either, which is why
widening it read as a judgement call rather than a fix. Its sibling
`load_roster_names` in the SAME FILE was given the treatment hours earlier: the
one-of-N shape, again. It now carries a stated contract as well as the handler,
so the next reader inherits the decision instead of re-deciding it.

MEASURED 2026-09-01, driving each function with a file holding one lone 0xff:

    read_inbox    silent `_empty()`  -> `_empty()` AND a WARNING naming the file
    load_jsonl    RAISED             -> the records it got, and how many

How much `load_jsonl` salvages is a property of BUFFERING, not of the data, and
both ends were measured rather than assumed:

    4-line file, bad byte on line 3      ->   0 records
    401-line file, bad byte at 88690 B   -> 369 records

Iterating a text handle decodes a whole buffer before yielding the first line,
so a small file loses everything. That is exactly why the warning states the
count instead of describing it: a caller reading "369 records" cannot tell a
complete file from a truncated one unless the truncation says so.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BAD = b"\xff"
ACCENTED = "café latté"


def _load_fireside() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "fireside_pulse_ut", ROOT / "scripts" / "fireside-pulse.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# read_inbox - an empty panel that means "unreadable" must not look like one
# that means "no mail"
# ---------------------------------------------------------------------------

def _fetch_file(data_root: Path) -> Path:
    p = (data_root / "outputs" / "operations" / "email-intelligence"
         / "_latest-fetch.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def test_an_unreadable_fetch_file_is_named_not_just_swallowed(
        data_root, caplog):
    """The defect. Degrading is right; degrading in silence is not.

    Two jaws. The call must still return the degraded shape, AND it must say
    which file it could not read. Asserting only the first would pass against
    the silent version this test exists to reject.
    """
    _fetch_file(data_root).write_bytes(b'{"conversations": [' + BAD + b"]}")
    from scripts.bridge_daemon.sources import inbox

    with caplog.at_level(logging.WARNING):
        result = inbox.read_inbox(data_root)

    assert result["counts"]["needs-you"] == 0, (
        "read_inbox stopped degrading over an unreadable fetch file")
    assert "_latest-fetch.json" in caplog.text, (
        f"the unreadable fetch file was never named, so an empty inbox panel "
        f"meaning 'unreadable' is indistinguishable from one meaning 'no "
        f"mail': {caplog.text!r}")


def test_a_healthy_empty_inbox_stays_quiet(data_root, caplog):
    """The anchor that makes the warning worth reading.

    A warning printed on every healthy run is noise, and noise teaches the
    operator to skip the log, which costs more than the silence did.
    """
    _fetch_file(data_root).write_text(
        json.dumps({"conversations": []}), encoding="utf-8")
    from scripts.bridge_daemon.sources import inbox

    with caplog.at_level(logging.WARNING):
        inbox.read_inbox(data_root)

    assert "cannot read" not in caplog.text, (
        f"a healthy empty inbox produced an unreadable warning: {caplog.text!r}")


def test_an_absent_fetch_file_is_still_not_a_warning(data_root, caplog):
    """Absent and unreadable are different facts and keep different answers."""
    from scripts.bridge_daemon.sources import inbox

    with caplog.at_level(logging.WARNING):
        inbox.read_inbox(data_root)

    assert "cannot read" not in caplog.text, (
        "an absent fetch file was reported as an unreadable one")


def test_the_inbox_still_reads_a_fetch_file_with_accented_text(
        data_root, caplog):
    """Over-refusal anchor. A high byte is not the same as a bad byte."""
    _fetch_file(data_root).write_text(json.dumps(
        {"conversations": [{"topic": ACCENTED, "subject": ACCENTED}]}),
        encoding="utf-8")
    from scripts.bridge_daemon.sources import inbox

    with caplog.at_level(logging.WARNING):
        inbox.read_inbox(data_root)

    assert "cannot read" not in caplog.text, (
        f"valid accented UTF-8 was treated as unreadable, so the fix widened "
        f"into a blanket refusal: {caplog.text!r}")


# ---------------------------------------------------------------------------
# load_jsonl - the sibling that was left behind
# ---------------------------------------------------------------------------

def test_an_undecodable_log_no_longer_ends_the_walk(tmp_path, capsys):
    log = tmp_path / "pulse.jsonl"
    log.write_bytes(b'{"a": 1}\n{"b": 2}\n' + BAD + b" bad\n")

    got = _load_fireside().load_jsonl(log)
    err = capsys.readouterr().err

    assert isinstance(got, list), (
        "load_jsonl raised on one undecodable byte instead of returning the "
        "records it could read, taking derive_state and main with it")
    assert str(log) in err or log.name in err, (
        f"the truncated log was not named: {err!r}")
    assert "record(s)" in err, (
        f"the warning did not state HOW MANY records survived, so a truncated "
        f"result reads as a complete one: {err!r}")


def test_the_count_in_the_warning_is_the_count_returned(tmp_path, capsys):
    """The number must be measured, not narrated.

    A message quoting a figure the return value does not match is worse than
    no message: it is a validated-looking claim that is false.
    """
    log = tmp_path / "big.jsonl"
    body = b"".join(json.dumps({"i": i, "pad": "x" * 200}).encode() + b"\n"
                    for i in range(400))
    log.write_bytes(body + BAD + b" bad\n")

    got = _load_fireside().load_jsonl(log)
    err = capsys.readouterr().err

    assert f"stopped after {len(got)} record(s)" in err, (
        f"the warning's count disagrees with the {len(got)} records actually "
        f"returned: {err!r}")
    assert got, (
        "a file whose leading 88 KB decode cleanly returned nothing, so the "
        "buffering claim in the module docstring is wrong in the direction "
        "that loses data")


def test_a_clean_log_is_read_whole_and_says_nothing(tmp_path, capsys):
    """Clean-path anchor, with the accented case folded in.

    A fix that skipped any line holding a high byte would satisfy every case
    above while dropping real records, so the accented row must survive AND
    the run must stay quiet.
    """
    log = tmp_path / "clean.jsonl"
    log.write_text(json.dumps({"a": 1}) + "\n"
                   + json.dumps({"note": ACCENTED}) + "\n", encoding="utf-8")

    got = _load_fireside().load_jsonl(log)
    err = capsys.readouterr().err

    assert got == [{"a": 1}, {"note": ACCENTED}], (
        f"a clean log holding valid accented UTF-8 did not come back whole: "
        f"{got!r}")
    assert err == "", f"a healthy log produced a warning: {err!r}"


def test_an_unparseable_line_is_counted_and_named(tmp_path, capsys):
    """The pre-existing skip was already silent, and that half is fixed too.

    A line that is valid UTF-8 but not JSON was dropped with `pass` before this
    change. Same defect, one layer up: the record vanishes and the count reads
    as a total.
    """
    log = tmp_path / "torn.jsonl"
    log.write_text('{"a": 1}\nthis is not json\n{"b": 2}\n', encoding="utf-8")

    got = _load_fireside().load_jsonl(log)
    err = capsys.readouterr().err

    assert got == [{"a": 1}, {"b": 2}], f"a readable record was lost: {got!r}"
    assert "skipped 1 unparseable line(s)" in err, (
        f"the dropped line was never reported: {err!r}")


def test_an_absent_log_is_empty_and_silent(tmp_path, capsys):
    """Absent is a fact, not a failure, and must not warn."""
    got = _load_fireside().load_jsonl(tmp_path / "nothing-here.jsonl")

    assert got == []
    assert capsys.readouterr().err == "", (
        "an absent log produced a warning, which would fire on every fresh "
        "install")
