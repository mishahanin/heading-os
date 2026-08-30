"""Two `--limit` flags in chronicle.py took a negative and silently truncated.

Both were `type=int` with no floor, and each subcommand spends the value in a
different expression, so the same bad input produced two different wrong
answers. Both exit 0. Neither says a word.

  build --limit=-5          the cap is `if limit and len(selected) >= limit`.
                            A negative is truthy and every length is >= it, so
                            the loop breaks on its FIRST append. Measured on
                            this file's three-session fixture: 3 sessions
                            selected with `--limit 0`, exactly 1 with
                            `--limit=-5`, and a "done:" line that reports the
                            one as a completed build.

  personal-recall --limit=-1  the cap is `hits[:args.limit]`, applied BEFORE the
                            header counts them. Two matching entries print as
                            "1 hit(s)" and one row. The truncation is invisible
                            inside the very count that reports it, so no part
                            of the output lets the operator notice a hit was
                            withheld.

Both are refused at parse time now, exit 2, through `argparse.ArgumentTypeError`
-- the code `--limit abc` already returned, so no new exit code enters either
contract. Refused, never clamped: a negative cap cannot mean anything here, so
it is a typo, and reading it as 0 would run the FULL backfill the operator was
trying to bound.

The floor differs by subcommand and the difference is the point. `build`
floors at 0, because 0 is documented as "no cap" and still works.
`personal-recall` floors at 1, because 0 is a wrong answer there too and sits
one integer from the measured one: `hits[:0]` is empty, and the `if not hits`
branch then prints "no match (best <score> < <floor>)" while naming a score
above its own floor.

Every assertion below is on the COUNT of items actually processed, not on the
exit code. A test that only read the exit code would pass with the truncation
still happening: argparse exits 2 for any usage error, including ones that
never touched this floor.

No test here reaches the network or a model: `socket.connect` is blocked for
every test in the file and the blocker is proved to be armed.

Tests: scripts/chronicle.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scripts.chronicle as ch  # noqa: E402


# ==========================================================================
# Isolation: no network, no model, no operator data
# ==========================================================================

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """`summarize` POSTs to a real ollama endpoint and `personal-recall`
    embeds against one. `connect` is the narrowest place no client library
    can route around."""
    reached = []

    def _blocked(self_or_addr, *args, **kwargs):
        reached.append(str(self_or_addr))
        raise RuntimeError("a test in this file tried to open a real socket")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    yield reached
    assert reached == [], f"a test reached the network: {reached}"


def test_the_network_blocker_is_actually_armed(no_network):
    """A guard nobody made refuse is not known to refuse anything."""
    with pytest.raises(RuntimeError, match="real socket"):
        socket.create_connection(("host.invalid", 11434))
    assert no_network == ["('host.invalid', 11434)"]
    no_network.clear()  # this one attempt was made on purpose


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path, monkeypatch):
    """chronicle/ resolves under tmp_path, never the operator's live overlay.

    `record_skipped` and `write_marker` write there, and `personal-recall`
    READS there -- without this the recall tests would search the operator's
    real personal chronicle, which is the one record class that is air-gapped.
    """
    data = tmp_path / "data"
    (data / "chronicle" / "personal").mkdir(parents=True)
    monkeypatch.setenv("HEADING_OS_DATA", str(data))
    monkeypatch.setattr(ch, "_PERSONAL_KEYWORDS_CACHE", None)
    return data


# ==========================================================================
# Fixture corpus
# ==========================================================================

SUBSTANTIVE = "We weighed the retry budget against the ingest backlog. " * 40


def _epoch(day: str) -> float:
    return datetime.fromisoformat(f"{day}T10:00:00+00:00").replace(
        tzinfo=timezone.utc).timestamp()


def _session(dirpath: Path, day: str) -> Path:
    """One top-level transcript with a body well over TRIVIAL_TEXT_CHARS, so it
    reaches the summarize branch instead of being skipped for length."""
    f = dirpath / f"sess-{day}-nnnn.jsonl"
    f.write_text(
        json.dumps({
            "type": "user",
            "timestamp": f"{day}T10:00:00Z",
            "message": {"role": "user", "content": SUBSTANTIVE},
        }) + "\n",
        encoding="utf-8",
    )
    os.utime(f, (_epoch(day), _epoch(day)))
    return f


@pytest.fixture()
def sessions(tmp_path) -> Path:
    """Three sessions across three days, all substantive."""
    d = tmp_path / "sessions"
    d.mkdir()
    for day in ("2026-08-10", "2026-08-20", "2026-08-28"):
        _session(d, day)
    return d


@pytest.fixture()
def processed(monkeypatch) -> list[str]:
    """Records every session the build loop actually enters.

    THE load-bearing observable. `parse_jsonl` is the first call inside the
    per-session loop, so its call count is exactly "sessions processed" --
    independent of body length, of the summarize verdict, and of the summary
    line, any of which a fix could get right while still truncating.
    """
    seen: list[str] = []
    real = ch.parse_jsonl

    def _counting(path):
        seen.append(path.stem)
        return real(path)

    monkeypatch.setattr(ch, "parse_jsonl", _counting)
    return seen


@pytest.fixture(autouse=True)
def stub_model(monkeypatch):
    """A business verdict, with no prefill spent. Only `gist`, `personal` and
    `skip` are read on the --dry-run path this file uses."""
    monkeypatch.setattr(ch, "summarize", lambda body: {
        "skip": False, "personal": False, "gist": "ingest backlog vs retry budget",
        "topics": ["ingest"],
    })


def _build(argv: list[str], sessions_dir: Path) -> int:
    return ch.main(["build", "--sessions-dir", str(sessions_dir), "--dry-run", *argv])


def _exit_code(call) -> object:
    """Run `call` and return its exit code however it arrived.

    Deliberate: it lets the COUNT be asserted UNCONDITIONALLY and FIRST. Under
    `with pytest.raises(SystemExit)` the count assertion sits after the block
    and never executes when the refusal is missing, so the red measurement
    reports "DID NOT RAISE" and says nothing about how many sessions ran -- and
    a fix that exits 2 while still truncating would satisfy it. Here the count
    is what fails, and it fails naming the wrong number.
    """
    try:
        return call()
    except SystemExit as e:
        return e.code


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# ==========================================================================
# 1 - build --limit: a negative truncated the run to one session
# ==========================================================================

def test_zero_still_means_no_cap(sessions, processed):
    """The documented value, pinned first: every fix below must keep it."""
    assert _build(["--backfill", "--limit=0"], sessions) == 0
    assert len(processed) == 3, f"--limit 0 must process all three, got {processed}"


def test_a_positive_build_limit_still_caps_where_it_says(sessions, processed):
    """A floor that also broke the cap would be a worse defect than the one
    being fixed, so the cap is measured, not assumed."""
    assert _build(["--backfill", "--limit=2"], sessions) == 0
    assert len(processed) == 2, f"--limit 2 must process exactly two, got {processed}"


def test_a_negative_build_limit_processes_nothing_rather_than_one(sessions, processed):
    """THE defect. Pre-fix this processed exactly ONE session and exited 0.

    The count is the assertion. Exit 2 alone would pass while the truncation
    still ran, because argparse also exits 2 for `--limit abc`, which never
    reaches this floor.
    """
    code = _exit_code(lambda: _build(["--backfill", "--limit=-5"], sessions))

    # The count FIRST, and unconditional. Pre-fix this line reads
    # ['sess-2026-08-28-nnnn'] -- one of three, chosen by an off-by-sign.
    assert processed == [], (
        "a refused --limit must process NO sessions; processing one is the "
        f"silent truncation this refuses, got {processed}"
    )
    assert code == 2


def test_the_build_refusal_names_the_value_and_the_floor(sessions, capsys):
    """An operator who typed `--limit -5` has to be able to read what to type."""
    with pytest.raises(SystemExit):
        _build(["--backfill", "--limit=-5"], sessions)
    err = _plain(capsys.readouterr().err)
    assert "-5" in err
    assert ">= 0" in err
    assert "no cap" in err


def test_a_non_integer_build_limit_keeps_its_existing_exit_code(sessions):
    """`--limit abc` exited 2 before this change and still does: replacing
    `type=int` with a validator introduces no new exit code."""
    with pytest.raises(SystemExit) as exc:
        _build(["--limit=abc"], sessions)
    assert exc.value.code == 2


# ==========================================================================
# 2 - personal-recall --limit: a negative hid a hit inside its own count
# ==========================================================================

_ENTRY = """---
title: "{title}"
date: {day}
created: {day}
topics: [{topic}]
source: chronicle
class: chronicle
personal: true
---

# {title}

> Личное

{gist}

Transcript: sess-{day}-nnnn.jsonl
"""


@pytest.fixture()
def two_personal_hits(isolated_data_root, monkeypatch):
    """Two personal entries that BOTH clear the lexical floor for one query.

    Lexical scoring, forced: `index_embed_target` raising OSError is one of the
    two exceptions `cmd_personal_recall` catches to fall back, and it keeps the
    embedder (and the network) out of the measurement entirely.
    """
    import scripts.utils.embeddings as emb

    def _no_host():
        raise OSError("no embedding host in tests")

    monkeypatch.setattr(emb, "index_embed_target", _no_host)

    d = isolated_data_root / "chronicle" / "personal"
    for day, title, gist in (
        ("2026-08-11", "sailing the harbour approach",
         "Walked the harbour approach and re-read the tide tables."),
        ("2026-08-19", "harbour berth paperwork",
         "Signed the harbour berth paperwork and filed the receipt."),
    ):
        (d / f"session-{day}-nnnn.md").write_text(
            _ENTRY.format(title=title, day=day, topic="harbour", gist=gist),
            encoding="utf-8",
        )
    return d


def _recall(argv: list[str]) -> int:
    return ch.main(["personal-recall", "harbour", *argv])


def _reported_and_shown(captured) -> tuple[int, int]:
    """(the count the header claims, the rows actually printed).

    Read apart on purpose. The defect made these two agree on a wrong number,
    so a test that read only one of them could not have seen it.
    """
    out = _plain(captured.out)
    m = re.search(r"(\d+) hit\(s\)", out)
    reported = int(m.group(1)) if m else -1
    shown = len(re.findall(r"\[Личное \d{4}-\d{2}-\d{2}\]", out))
    return reported, shown


def test_the_default_limit_shows_both_hits(two_personal_hits, capsys):
    """The baseline the truncation was measured against."""
    assert _recall([]) == 0
    reported, shown = _reported_and_shown(capsys.readouterr())
    assert (reported, shown) == (2, 2)


def test_a_positive_recall_limit_still_caps_where_it_says(two_personal_hits, capsys):
    assert _recall(["--limit=1"]) == 0
    reported, shown = _reported_and_shown(capsys.readouterr())
    assert (reported, shown) == (1, 1)


def test_a_negative_recall_limit_shows_no_hit_count_at_all(two_personal_hits, capsys):
    """THE defect. Pre-fix this printed "1 hit(s)" and one row, of two matches.

    Asserting on exit 2 alone would miss it: the wrong answer WAS the count, so
    the count is what this pins. After the refusal there is no hit line to
    print, and in particular no line claiming 1.
    """
    code = _exit_code(lambda: _recall(["--limit=-1"]))

    # The counts FIRST, and unconditional. Pre-fix both read 1, over two
    # matching entries, with nothing anywhere saying the second was dropped.
    reported, shown = _reported_and_shown(capsys.readouterr())
    assert reported == -1, f"a refused run must report no hit count, it reported {reported}"
    assert shown == 0, f"a refused run must print no hits, it printed {shown}"
    assert code == 2


def test_a_zero_recall_limit_is_refused_rather_than_called_no_match(
        two_personal_hits, capsys):
    """One integer from the measured defect, through the same slice.

    `hits[:0]` is empty, so the `if not hits` branch used to print "no match
    (best <score> < 0.34)" over two entries that both scored ABOVE 0.34. There
    is no no-cap reading available on this subcommand to rescue it, so the
    floor is 1.
    """
    code = _exit_code(lambda: _recall(["--limit=0"]))

    out = _plain(capsys.readouterr().out)
    assert "no match" not in out, "0 must be refused, never reported as no match"
    assert code == 2


def test_the_recall_refusal_names_the_value_and_the_floor(two_personal_hits, capsys):
    with pytest.raises(SystemExit):
        _recall(["--limit=-1"])
    err = _plain(capsys.readouterr().err)
    assert "-1" in err
    assert ">= 1" in err


# ==========================================================================
# 3 - the two argparse types, directly
# ==========================================================================

@pytest.mark.parametrize("good", ["0", "1", "25", "1000"])
def test_nonneg_int_arg_admits_zero_and_up_and_returns_an_int(good):
    assert ch.nonneg_int_arg(good) == int(good)


@pytest.mark.parametrize("bad", ["-1", "-5", "-1000"])
def test_nonneg_int_arg_refuses_every_negative(bad):
    with pytest.raises(argparse.ArgumentTypeError, match=r">= 0"):
        ch.nonneg_int_arg(bad)


@pytest.mark.parametrize("good", ["1", "5", "1000"])
def test_positive_int_arg_admits_one_and_up(good):
    assert ch.positive_int_arg(good) == int(good)


@pytest.mark.parametrize("bad", ["0", "-1", "-99"])
def test_positive_int_arg_refuses_zero_and_every_negative(bad):
    with pytest.raises(argparse.ArgumentTypeError, match=r">= 1"):
        ch.positive_int_arg(bad)


# Resolved by NAME at call time, never as a module-level attribute reference.
# `[ch.nonneg_int_arg, ...]` in the decorator is evaluated at import, so a
# missing symbol becomes a COLLECTION error that aborts the file -- and a red
# measurement in which no behavioural test ran proves nothing about behaviour.
@pytest.mark.parametrize("name", ["nonneg_int_arg", "positive_int_arg"])
def test_a_non_integer_raises_the_type_argparse_turns_into_exit_2(name):
    """Same exception class as `iso_date_arg`, so argparse prints usage and
    exits 2 rather than a traceback -- and the wording argparse's own
    `type=int` used is preserved, so no existing message changed."""
    factory = getattr(ch, name)
    with pytest.raises(argparse.ArgumentTypeError, match="invalid int value"):
        factory("abc")


def test_neither_type_clamps_a_refused_value(sessions, processed):
    """A clamp is the same defect wearing a fix: the run would continue under a
    number nobody typed. Nothing is processed, so nothing was clamped to 0-as-
    no-cap either -- which would have run the full backfill."""
    _exit_code(lambda: _build(["--backfill", "--limit=-5"], sessions))
    assert processed == []
