"""Instruments that lost the very thing they exist to report.

Shard `scripts-04-p3` of the 2026-08 engine audit. Each of these is a tool built
to make a silent failure visible, and each had a path where it fell silent
itself.

  - `dev/check-lfs-fixtures.py` exists because "a job whose fixture tests all
    skipped reports green while proving nothing". A file it could not open was
    warned about on stderr and counted as not-a-pointer, after which the tool
    printed "no pointer files under tests/" and exited 0: a green line over a
    check it did not finish.
  - `denials.py` answers "is a given guard catching anything, or is it
    ceremony?". A record whose `ts` could not be read was folded into "older
    than the window" by `(_epoch(...) or 0) >= cutoff` and silently left the
    count. `utils/denial_log.read_denials` promises a corrupt line is "skipped,
    not fatal" and returned a hand-edited `[]` as though it were a record, after
    which `summarize` raised AttributeError and cost the whole history.
  - `dead-letter.py` is the queue of sends that failed. `_age_str` raised
    TypeError on a numeric `recorded_at`, ending `list` MID-TABLE and hiding
    every entry after it. `show` and `retry` called `load` with no handler at
    all, so the operator's obvious next step after seeing "(unreadable)" in the
    list ended in a traceback.
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import dead_letter, denial_log  # noqa: E402


def _load(stem: str, relpath: str):
    spec = importlib.util.spec_from_file_location(stem, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# check-lfs-fixtures: the green line means the check finished
# ============================================================

@pytest.fixture(scope="module")
def lfs():
    return _load("check_lfs_mod", "scripts/dev/check-lfs-fixtures.py")


# A v1 pointer as the Git LFS spec defines one. The OID was `sha256:0000` -
# four hex characters where the spec says sixty-four - so the positive case
# below presented the checker with a malformed file and called the answer proof
# that a real pointer is recognised. It also quietly licensed an implementation
# that looks at the `oid sha256:` prefix and never at the digest.
_POINTER_OID = "0123456789abcdef" * 4
_POINTER = (b"version https://git-lfs.github.com/spec/v1\n"
            b"oid sha256:" + _POINTER_OID.encode("ascii") + b"\nsize 12\n")


def test_the_pointer_fixture_is_the_shape_the_spec_defines():
    """A fixture nothing checks is a fixture that drifts back.

    Every positive test in this file writes `_POINTER` and then asserts the
    guard found a pointer. If `_POINTER` stops being one, all of them keep
    passing and none of them measures anything.
    """
    lines = _POINTER.decode("ascii").splitlines()
    assert lines[0] == "version https://git-lfs.github.com/spec/v1"
    label, _, oid = lines[1].partition(":")
    assert label == "oid sha256"
    assert len(oid) == 64, len(oid)
    assert all(c in "0123456789abcdef" for c in oid), oid
    assert lines[2].startswith("size ") and lines[2][len("size "):].isdigit()
    assert len(_POINTER) <= 1024, "the spec caps a pointer at 1024 bytes"


def test_a_pointer_file_is_found(lfs, tmp_path):
    # `scan` grew a third bucket on 2026-08-25 for files deleted between the
    # listing and the read.
    (tmp_path / "fixture.docx").write_bytes(_POINTER)
    pointers, unreadable, vanished = lfs.scan(tmp_path)
    assert [p.name for p in pointers] == ["fixture.docx"]
    assert unreadable == []
    assert vanished == []


def test_a_real_blob_is_not_a_pointer(lfs, tmp_path):
    (tmp_path / "fixture.docx").write_bytes(b"PK\x03\x04" + b"x" * 4000)
    assert lfs.scan(tmp_path) == ([], [], [])


def test_a_small_file_that_is_not_a_pointer_is_left_alone(lfs, tmp_path):
    (tmp_path / "notes.txt").write_bytes(b"just text")
    assert lfs.scan(tmp_path) == ([], [], [])


def test_an_unreadable_file_is_reported_not_counted_as_clean(lfs, tmp_path,
                                                             monkeypatch):
    """It used to warn on stderr and answer False, and the run still said OK."""
    (tmp_path / "fixture.docx").write_bytes(_POINTER)
    real_open = Path.open

    def _deny(self, *a, **k):
        if self.name == "fixture.docx":
            raise PermissionError("permission denied")
        return real_open(self, *a, **k)

    monkeypatch.setattr(Path, "open", _deny)
    pointers, unreadable, vanished = lfs.scan(tmp_path)
    assert pointers == []
    assert [p.name for p, _ in unreadable] == ["fixture.docx"]
    assert vanished == [], "a file that is present but denied is not a deleted one"


def test_is_pointer_no_longer_swallows_the_error(lfs, tmp_path, monkeypatch):
    """The swallow is what let the caller print a complete-check line."""
    target = tmp_path / "fixture.docx"
    target.write_bytes(_POINTER)

    def _deny(self, *a, **k):
        raise PermissionError("permission denied")

    monkeypatch.setattr(Path, "open", _deny)
    with pytest.raises(OSError):
        lfs.is_pointer(target)


def test_the_main_report_names_what_it_could_not_read(lfs, tmp_path,
                                                      monkeypatch, capsys):
    (tmp_path / "fixture.docx").write_bytes(_POINTER)
    monkeypatch.setattr(lfs, "SCANNED", tmp_path)
    monkeypatch.setattr(lfs, "scan",
                        lambda base: ([], [(tmp_path / "fixture.docx", "denied")], []))
    assert lfs.main() == 1, "an unfinished check must not exit 0"
    err = capsys.readouterr().err
    assert "UNKNOWN" in err and "not complete" in err


def test_a_clean_tree_still_exits_zero(lfs, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(lfs, "SCANNED", tmp_path)
    assert lfs.main() == 0
    assert "resolved" in capsys.readouterr().out


# ============================================================
# denial_log / denials: the counts stay honest
# ============================================================

def test_a_line_that_parses_but_is_not_a_record_is_skipped(tmp_path):
    """`read_denials` promises a corrupt line costs nothing else."""
    log = tmp_path / "denials.jsonl"
    log.write_text(
        json.dumps({"ts": 1.0, "mechanism": "leak-guard"}) + "\n"
        "[]\n" "null\n" '"a string"\n' "42\n"
        + json.dumps({"ts": 2.0, "mechanism": "secret-scanner"}) + "\n",
        encoding="utf-8",
    )
    records = denial_log.read_denials(log)
    assert len(records) == 2
    assert all(isinstance(r, dict) for r in records)


def test_the_summary_survives_a_hand_edited_log(tmp_path):
    """This raised AttributeError and cost the whole history."""
    log = tmp_path / "denials.jsonl"
    log.write_text("[]\n" + json.dumps({"mechanism": "leak-guard"}) + "\n",
                   encoding="utf-8")
    assert denial_log.summarize(denial_log.read_denials(log)) == {"leak-guard": 1}


def test_an_unparseable_line_is_still_skipped(tmp_path):
    log = tmp_path / "denials.jsonl"
    log.write_text('{"ts": 1.0, "mecha\n'
                   + json.dumps({"ts": 2.0, "mechanism": "x"}) + "\n",
                   encoding="utf-8")
    assert len(denial_log.read_denials(log)) == 1


@pytest.fixture()
def denials(tmp_path, monkeypatch):
    mod = _load("denials_mod", "scripts/denials.py")
    log = tmp_path / "denials.jsonl"
    monkeypatch.setattr(mod, "denial_log_path", lambda: log)
    monkeypatch.setattr(mod, "read_denials", lambda: denial_log.read_denials(log))
    return mod, log


def _write(log: Path, *records: dict) -> None:
    log.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def test_an_undated_record_is_named_not_silently_dropped(denials, monkeypatch,
                                                         capsys):
    mod, log = denials
    now = time.time()
    _write(log, {"ts": now, "mechanism": "leak-guard"},
           {"ts": "not a number", "mechanism": "leak-guard"})
    monkeypatch.setattr(sys, "argv", ["denials.py", "--days", "30"])
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "1 record(s)" in out
    assert "unreadable timestamp" in out, "an unexplained subtraction is the bug"


def test_the_undated_count_reaches_the_json(denials, monkeypatch, capsys):
    mod, log = denials
    _write(log, {"ts": time.time(), "mechanism": "x"}, {"ts": None, "mechanism": "x"})
    monkeypatch.setattr(sys, "argv", ["denials.py", "--days", "30", "--json"])
    assert mod.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["undated_excluded"] == 1
    assert payload["total"] == 1


def test_an_old_record_is_excluded_without_a_warning(denials, monkeypatch,
                                                     capsys):
    mod, log = denials
    _write(log, {"ts": time.time() - 90 * 86400, "mechanism": "x"})
    monkeypatch.setattr(sys, "argv", ["denials.py", "--days", "30"])
    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "0 refusals recorded" in out
    assert "unreadable timestamp" not in out


def test_a_clean_window_says_nothing_about_undated(denials, monkeypatch, capsys):
    mod, log = denials
    _write(log, {"ts": time.time(), "mechanism": "leak-guard"})
    monkeypatch.setattr(sys, "argv", ["denials.py", "--days", "30"])
    assert mod.main() == 0
    assert "unreadable timestamp" not in capsys.readouterr().out


# ============================================================
# dead-letter: the queue of failed sends stays readable
# ============================================================

@pytest.fixture(scope="module")
def dlq():
    return _load("dead_letter_cli", "scripts/dead-letter.py")


@pytest.mark.parametrize("recorded", [1234567890, ["2026-01-01"], {"a": 1}, 3.5])
def test_a_non_string_timestamp_does_not_end_the_listing(dlq, tmp_path,
                                                         recorded):
    """It raised TypeError, so `list` stopped and hid every later entry."""
    artifact = tmp_path / "x.json"
    artifact.write_text("{}", encoding="utf-8")
    assert dlq._age_str({"recorded_at": recorded}, artifact) == "?"


def test_a_real_timestamp_still_renders(dlq, tmp_path):
    artifact = tmp_path / "x.json"
    artifact.write_text("{}", encoding="utf-8")
    age = dlq._age_str({"recorded_at": "2026-08-24T10:00:00+00:00"}, artifact)
    assert age.endswith(("m", "h", "d"))


@pytest.mark.parametrize("body", ["[]", "null", '"a string"', "42"])
def test_an_entry_that_is_not_an_object_is_refused_by_name(tmp_path, body):
    artifact = tmp_path / "y.json"
    artifact.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        dead_letter.load(artifact)
    assert "not an object" in str(exc.value)
    assert "y.json" in str(exc.value)


def test_a_real_entry_still_loads(tmp_path):
    artifact = tmp_path / "y.json"
    artifact.write_text(json.dumps({"kind": "email_send"}), encoding="utf-8")
    assert dead_letter.load(artifact)["kind"] == "email_send"


def test_the_wrong_shape_refusal_is_a_valueerror_so_old_handlers_catch_it():
    """JSONDecodeError is a ValueError; the widened handlers rely on that."""
    assert issubclass(json.JSONDecodeError, ValueError)


def test_show_on_an_unreadable_entry_exits_one_not_a_traceback(dlq, tmp_path,
                                                               capsys):
    """`list` prints "(unreadable)", so this is the operator's next command."""
    artifact = tmp_path / "broken.json"
    artifact.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        dlq._load_or_refuse(artifact)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "cannot read dead-letter entry" in err
    assert "broken.json" in err


def test_a_readable_entry_passes_straight_through(dlq, tmp_path):
    artifact = tmp_path / "ok.json"
    artifact.write_text(json.dumps({"kind": "email_send"}), encoding="utf-8")
    assert dlq._load_or_refuse(artifact)["kind"] == "email_send"


def test_a_missing_file_is_refused_the_same_way(dlq, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        dlq._load_or_refuse(tmp_path / "absent.json")
    assert exc.value.code == 1
    assert "cannot read dead-letter entry" in capsys.readouterr().err
