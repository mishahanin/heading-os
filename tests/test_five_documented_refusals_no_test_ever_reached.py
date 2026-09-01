"""Five refusals and degradations that are written down and were never run.

One shape, five files, found by mutating each subject and running every test in
the repo that names it. In each case the behaviour is stated in a docstring, a
comment, or the module's own published exit-code table, and the assertion that
would notice its removal does not exist anywhere.

MEASURED 2026-09-01, each against the full neighbour set for its file:

  1. `scripts/email-sweep.py` exit 2 for a bad `--date`.
     Changing `return 2` to `return 1` in `main`'s `except ValueError` left 190
     tests passing. `tests/test_a_sweep_that_promised_an_exit_code_it_never_
     returned.py` asserts that the DOCSTRING names the date condition under exit
     2, which is a check on the sentence, not on the code. The traversal guard
     itself is exercised elsewhere; the exit code the wrapper contract is written
     around is not.

  2. `scripts/elicit.py` clamping a negative `-n`.
     Dropping `max(0, ...)` from `n = max(0, min(args.n, len(pool)))` left 82
     passing. The comment reads "clamp: never crash on a negative or oversized
     -n" and only the oversized half has a case. `random.sample(pool, -1)` raises
     `ValueError`, so the negative half is a crash rather than a wrong answer.

  3. `scripts/utils/docx_helpers.py` naming what a template directory holds.
     Replacing the `<unreadable: ...>` fallback with an empty list left 58
     passing. The docstring says the message lists the directory's contents
     "because 'template not found' sends the reader to the wrong question", and
     the unreadable branch is not exotic: it is what a clone with no private
     datastore overlay hits on every run.

  4. `scripts/utils/egress_proof.py` admitting how many sources it did not name.
     Deleting the `(+N more)` suffix left 45 passing. This module exists to
     refuse to over-claim, and a refusal that lists three of twelve dirty
     sources while implying three is the whole set is that same over-claim on the
     refusal side.

  5. `scripts/bridge_daemon/sources/investors.py` skipping a corrupt log line.
     Deleting the `isinstance(firm_num, int)` guard left 67 passing. The
     docstring promises "Corrupt lines are skipped"; without the guard a line
     carrying `"firm_num": "3"` keys the returned dict by a string, and every
     caller looks the firm up by integer, so the mark is silently lost while the
     read reports success.

Every fixture is invented. Nothing here sends, and nothing writes outside
tmp_path.

Run: .venv/bin/python -m pytest tests/test_five_documented_refusals_no_test_ever_reached.py -q
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, relative: str):
    """Load a kebab-case script by path. `import` cannot spell a hyphen."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ============================================================
# 1. email-sweep: the exit code its own docstring publishes
# ============================================================

@pytest.fixture(scope="module")
def sweep():
    return _load("email_sweep_exit_codes", "scripts/email-sweep.py")


@pytest.mark.parametrize("bad_date", [
    "../../../../etc/sweep",          # the traversal the guard was written for
    "2026-13-45",                     # right shape, not a calendar date
    "2026-02-30",                     # right shape, not a real February
    "20260801",                       # ISO-ish, and `fromisoformat` accepts it
    "2026-8-1",                       # unpadded
])
def test_a_bad_date_exits_two_from_the_command_line(sweep, monkeypatch, capsys,
                                                    tmp_path, bad_date):
    """`main` returns 2, which is what a wrapper reads. The module docstring
    lists this under exit 2 beside the missing-file condition, and a wrapper
    told "1" would treat a rejected date as a usage error and retry it.
    """
    payload = tmp_path / "proposed.json"
    payload.write_text(json.dumps([{"type": "task", "title": "seed"}]),
                       encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    monkeypatch.setattr(sys, "argv", [
        "email-sweep.py", "propose", "--file", str(payload), "--date", bad_date])

    assert sweep.main() == 2
    assert "--date" in capsys.readouterr().err


def test_a_good_date_is_not_refused(sweep, monkeypatch, capsys, tmp_path):
    """Vacuity guard: a `_valid_date` that refused everything would satisfy every
    case above."""
    payload = tmp_path / "proposed.json"
    payload.write_text(json.dumps([{"type": "task", "title": "seed"}]),
                       encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    monkeypatch.setattr(sys, "argv", [
        "email-sweep.py", "propose", "--file", str(payload),
        # A real leap day, and 2028 rather than 2026: 2026 is not a leap year,
        # so `2026-02-29` is refused by the calendar check one line below the
        # shape check. That is the correct answer and it makes a useless
        # vacuity guard, since it exercises a refusal rather than an acceptance.
        "--date", "2028-02-29"])

    assert sweep.main() == 0
    capsys.readouterr()


def test_a_refused_date_wrote_no_state_file(sweep, monkeypatch, capsys, tmp_path):
    """The refusal is the whole point of validating a value that becomes a
    filename. `propose` creates parent directories, so a date that got through
    would leave a JSON file wherever it pointed."""
    payload = tmp_path / "proposed.json"
    payload.write_text(json.dumps([{"type": "task", "title": "seed"}]),
                       encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    monkeypatch.setattr(sys, "argv", [
        "email-sweep.py", "propose", "--file", str(payload),
        "--date", "../../escaped"])

    assert sweep.main() == 2
    capsys.readouterr()
    assert list(tmp_path.rglob("sweep-actions-*.json")) == []
    assert not (tmp_path.parent / "escaped").exists()


# ============================================================
# 2. elicit: the negative half of a two-sided clamp
# ============================================================

def test_a_negative_n_returns_nothing_instead_of_raising(capsys):
    """`random.sample(pool, -1)` is a `ValueError`. The clamp is what turns a
    nonsense argument into an empty answer, and only its upper half was pinned.
    """
    from scripts import elicit

    assert elicit.main(["random", "-n", "-4", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_a_zero_n_is_the_same_empty_answer(capsys):
    """The case ON the line between the two arms of `max(0, ...)`."""
    from scripts import elicit

    assert elicit.main(["random", "-n", "0", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_a_positive_n_still_draws(capsys):
    """Vacuity guard: a clamp stuck at 0 would pass both cases above."""
    from scripts import elicit

    assert elicit.main(["random", "-n", "2", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2


# ============================================================
# 3. docx_helpers: a not-found message that names the directory
# ============================================================

def test_a_missing_template_directory_is_named_in_the_refusal(tmp_path):
    """The state a public clone reaches on every run: no private datastore
    overlay, so `iterdir` raises rather than returning an empty list. The reader
    needs to know the directory is absent, not that a glob matched nothing.
    """
    from scripts.utils.docx_helpers import brand_master_template

    absent = tmp_path / "no-such-datastore" / "brand" / "templates"
    with pytest.raises(FileNotFoundError) as caught:
        brand_master_template(".dotx", templates_dir=absent)

    message = str(caught.value)
    assert "unreadable" in message, message
    assert "No such file or directory" in message, message
    assert str(absent) in message


def test_a_present_but_empty_template_directory_says_it_is_empty(tmp_path):
    """The other branch of the same message, so the fallback cannot be written
    to fire in both cases and claim to distinguish them."""
    from scripts.utils.docx_helpers import brand_master_template

    empty = tmp_path / "templates"
    empty.mkdir()
    with pytest.raises(FileNotFoundError) as caught:
        brand_master_template(".dotx", templates_dir=empty)

    message = str(caught.value)
    assert "(nothing)" in message, message
    assert "unreadable" not in message


def test_a_directory_holding_the_wrong_files_lists_them(tmp_path):
    """The case the docstring is actually about: something IS there and it is
    not the template, and naming it is what sends the reader to the right
    question."""
    from scripts.utils.docx_helpers import brand_master_template

    directory = tmp_path / "templates"
    directory.mkdir()
    (directory / "an-unrelated-file.txt").write_text("x", encoding="utf-8")
    with pytest.raises(FileNotFoundError) as caught:
        brand_master_template(".dotx", templates_dir=directory)

    assert "an-unrelated-file.txt" in str(caught.value)


# ============================================================
# 4. egress_proof: a refusal that admits what it left out
# ============================================================

def _dirty(n: int) -> list[str]:
    return [f"reference/skill-router/source-{i:02d}.md" for i in range(n)]


class _StubDenylist:
    """Stands in for `content_denylist.Denylist` on the ONE path that never
    reaches it: `dirty_sources` refuses before any scan, so the denylist here is
    only proof that the refusal did not depend on it."""

    degraded = False
    tokens = ("never-scanned",)

    def scan_text(self, _payload):
        raise AssertionError("a dirty payload must be refused before the scan")


def test_a_long_dirty_list_says_how_many_it_did_not_name():
    """Three names and no count reads as three dirty sources. Twelve is a
    different fact about the tree, and the operator acts on it differently.
    """
    from scripts.utils.egress_proof import EGRESS_UNVERIFIABLE, egress_state

    state, reason = egress_state("a clean payload", _StubDenylist(),
                                 dirty_sources=_dirty(12))

    assert state == EGRESS_UNVERIFIABLE
    assert "+9 more" in reason, reason
    assert reason.count("source-") == 3, "the refusal listed more than three"


def test_exactly_three_dirty_sources_add_no_suffix():
    """The case ON the line: `<= 3` is the boundary the code writes."""
    from scripts.utils.egress_proof import egress_state

    _state, reason = egress_state("a clean payload", _StubDenylist(),
                                  dirty_sources=_dirty(3))

    assert "more)" not in reason, reason
    assert reason.count("source-") == 3


def test_four_dirty_sources_are_the_first_to_carry_a_suffix():
    """One past the boundary, so `<=` cannot silently become `<`."""
    from scripts.utils.egress_proof import egress_state

    _state, reason = egress_state("a clean payload", _StubDenylist(),
                                  dirty_sources=_dirty(4))

    assert "+1 more" in reason, reason


# ============================================================
# 5. investors: the corrupt line the send log promises to skip
# ============================================================

def _write_send_log(root: Path, inv, lines: list[str]) -> Path:
    path = root / inv.PROGRAM_DIR / inv.SEND_LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


def test_a_string_firm_number_is_skipped_rather_than_keyed(tmp_path):
    """Every caller looks a firm up by integer. A string key is a mark that is
    on disk, read without error, and invisible: the program view then invites a
    second first-touch to somebody who already had one, which is the exact
    failure the oversized-log fix beside it was written for.
    """
    from scripts.bridge_daemon.sources import investors as inv

    _write_send_log(tmp_path, inv, [
        json.dumps({"firm_num": "3", "date": "2026-05-01", "ts": "t0", "note": "a"}),
        json.dumps({"firm_num": 7, "date": "2026-05-02", "ts": "t1", "note": "b"}),
    ])

    log = inv._read_send_log(tmp_path)

    assert set(log) == {7}, log
    assert all(isinstance(key, int) for key in log)


def test_a_line_with_no_firm_number_at_all_is_skipped(tmp_path):
    """The other corrupt shape. `entry.get("firm_num")` is None, and None as a
    dict key would survive every later lookup silently."""
    from scripts.bridge_daemon.sources import investors as inv

    _write_send_log(tmp_path, inv, [
        json.dumps({"date": "2026-05-01", "ts": "t0", "note": "orphan"}),
        json.dumps({"firm_num": 7, "date": "2026-05-02", "ts": "t1", "note": "b"}),
    ])

    assert set(inv._read_send_log(tmp_path)) == {7}


def test_a_log_of_nothing_but_corrupt_lines_is_empty_not_an_error(tmp_path):
    """A read that cannot find a single usable mark answers "none", the same as
    a missing file, rather than raising into the dashboard."""
    from scripts.bridge_daemon.sources import investors as inv

    _write_send_log(tmp_path, inv, [
        json.dumps({"firm_num": "3"}),
        json.dumps({"firm_num": None}),
        "{not json at all",
    ])

    assert inv._read_send_log(tmp_path) == {}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
