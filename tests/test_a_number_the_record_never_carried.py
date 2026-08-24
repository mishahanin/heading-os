"""Three defects where a tool stated more than its data supported.

Shard `scripts-04-p2` of the 2026-08 engine audit.

  - `crm_next.render_draft` rebuilt "days since our last exchange" as
    `days_overdue + (cadence or 14)`, while `days_since` sat unread in the very
    dict it was handed. For a contact with no cadence, `days_overdue` is 0, so a
    record saying 40 days of silence produced a draft telling a real person it
    had been 14. The figure came from the fallback constant, not from any data.
  - `datastore-extract.get_companion_path` derives `<stem>-extract.md` from the
    STEM, so `pitch.pptx` and `pitch.xlsx` in one folder claim the same file.
    The deck sorts first and wins; the workbook was then told "Skip (companion
    already exists)" about a companion describing the deck, and was never
    extracted. Under `--force` both ran, the second overwrote the first, and the
    run reported two files extracted with one file of output.
  - `daemon-fleet-health._read_heartbeat` promises "the heartbeat dict or a
    synthetic 'missing'/'error' record" and raised TypeError on a heartbeat that
    parsed to `null`, `[]`, a string or a number. Those files are written by
    OTHER machines, so one exec's torn write took down the whole fleet report -
    the tool whose only job is to say which daemons are down.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(stem: str, relpath: str):
    spec = importlib.util.spec_from_file_location(stem, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================
# crm_next: the draft states only what the record carries
# ============================================================

@pytest.fixture(scope="module")
def crm_next():
    return _load("crm_next_mod", "scripts/crm_next.py")


def _opening(mod, contact: dict) -> str:
    """The one line of the draft that states the elapsed time."""
    return mod.render_draft(contact, "(no prior interaction)").splitlines()[4]


def test_the_draft_quotes_the_recorded_days(crm_next):
    contact = {"name": "Ann", "days_since": 40, "cadence": 14, "days_overdue": 26}
    assert "40 days" in _opening(crm_next, contact)


def test_a_contact_with_no_cadence_is_not_told_a_made_up_number(crm_next):
    """The regression: 40 days of silence used to be reported as 14."""
    contact = {"name": "Cy", "days_since": 40, "cadence": 0, "days_overdue": 0}
    opening = _opening(crm_next, contact)
    assert "40 days" in opening
    assert "14 days" not in opening


def test_an_unparseable_cadence_does_not_invent_a_default(crm_next):
    contact = {"name": "Bo", "days_since": 40, "cadence": "unknown",
               "days_overdue": 26}
    assert "40 days" in _opening(crm_next, contact)


def test_the_reconstruction_still_works_when_days_since_is_absent(crm_next):
    """Backwards compatible with a health payload that predates the field."""
    contact = {"name": "Di", "cadence": 14, "days_overdue": 26}
    assert "40 days" in _opening(crm_next, contact)


def test_with_nothing_usable_the_clause_is_dropped_not_guessed(crm_next):
    contact = {"name": "Ed", "cadence": None, "days_overdue": None}
    opening = _opening(crm_next, contact)
    assert opening == "Wanted to check back in."
    assert "days" not in opening


@pytest.mark.parametrize("contact", [
    {"name": "F", "days_since": None, "cadence": None},
    {"name": "G", "days_since": "soon", "cadence": "later"},
    {"name": "H"},
    {"name": "I", "days_since": -3, "cadence": 0, "days_overdue": 0},
])
def test_no_unusable_record_produces_a_number(crm_next, contact):
    assert "days since our last exchange" not in _opening(crm_next, contact)


def test_a_zero_cadence_does_not_reach_the_reconstruction(crm_next):
    """`cadence <= 0` is what made `or 14` fire. It must not derive from it."""
    assert crm_next._days_since({"cadence": 0, "days_overdue": 40}) is None


def test_a_true_flag_is_not_a_day_count(crm_next):
    """`int(True)` is 1, so a bool would have become a one-day silence."""
    assert crm_next._overdue_days_or_none(True) is None
    assert crm_next._overdue_days_or_none(False) is None


def test_the_sort_key_still_answers_zero_for_junk(crm_next):
    """`_overdue_days` and `_overdue_days_or_none` differ on purpose."""
    assert crm_next._overdue_days("unknown") == 0
    assert crm_next._overdue_days_or_none("unknown") is None


def test_the_greeting_survives_a_whitespace_name(crm_next):
    """Anchor on an earlier fix in the same function."""
    assert "Hey there," in crm_next.render_draft({"name": "   "}, "(no prior)")


# ============================================================
# datastore-extract: two files never share one companion
# ============================================================

@pytest.fixture(scope="module")
def extract():
    return _load("datastore_extract_mod", "scripts/datastore-extract.py")


def test_a_lone_file_keeps_the_plain_companion_name(extract):
    """Every companion on disk today uses this name. None of them may move."""
    solo = Path("/ds/solo.xlsx")
    ambiguous = extract._ambiguous_stems([solo])
    assert extract._companion_for(solo, ambiguous).name == "solo-extract.md"


def test_a_colliding_pair_gets_one_companion_each(extract):
    deck = Path("/ds/pitch.pptx")
    book = Path("/ds/pitch.xlsx")
    ambiguous = extract._ambiguous_stems([deck, book])
    first = extract._companion_for(deck, ambiguous)
    second = extract._companion_for(book, ambiguous)
    assert first != second
    assert first.name == "pitch-pptx-extract.md"
    assert second.name == "pitch-xlsx-extract.md"


def test_the_same_stem_in_different_folders_is_not_a_collision(extract):
    paths = [Path("/ds/a/pitch.pptx"), Path("/ds/b/pitch.xlsx")]
    ambiguous = extract._ambiguous_stems(paths)
    assert ambiguous == set()
    assert all(extract._companion_for(p, ambiguous).name == "pitch-extract.md"
               for p in paths)


def test_two_files_of_the_same_type_are_not_a_collision(extract):
    """Impossible on one filesystem, but the predicate must not invent one."""
    assert extract._ambiguous_stems([Path("/ds/a.xlsx"), Path("/ds/a.xlsx")]) == set()


def test_the_collision_is_announced_not_silent(extract, tmp_path, monkeypatch,
                                               capsys):
    for name in ("pitch.pptx", "pitch.xlsx"):
        (tmp_path / name).write_bytes(b"PK\x03\x04 not really")
    monkeypatch.setattr(extract, "extract_xlsx", lambda p: "# workbook\n")
    monkeypatch.setattr(extract, "extract_pptx", lambda p: "# deck\n")

    extracted = extract.scan_and_extract(target_dir=tmp_path)
    out = capsys.readouterr().out
    assert "carry the source suffix" in out, "a renamed companion is a surprise"
    assert len(extracted) == 2, "both files must actually be extracted"
    assert (tmp_path / "pitch-pptx-extract.md").read_text() == "# deck\n"
    assert (tmp_path / "pitch-xlsx-extract.md").read_text() == "# workbook\n"


def test_a_lone_file_is_not_announced(extract, tmp_path, monkeypatch, capsys):
    (tmp_path / "solo.xlsx").write_bytes(b"PK\x03\x04 not really")
    monkeypatch.setattr(extract, "extract_xlsx", lambda p: "# workbook\n")
    extract.scan_and_extract(target_dir=tmp_path)
    assert "source suffix" not in capsys.readouterr().out


def test_both_extractable_types_are_still_found(extract, tmp_path, monkeypatch):
    """The rglob was rewritten over EXTRACTABLE_SUFFIXES; it must find both."""
    (tmp_path / "a.xlsx").write_bytes(b"x")
    (tmp_path / "b.pptx").write_bytes(b"x")
    (tmp_path / "c.txt").write_text("not extractable")
    monkeypatch.setattr(extract, "extract_xlsx", lambda p: "# x\n")
    monkeypatch.setattr(extract, "extract_pptx", lambda p: "# p\n")
    names = {orig.name for orig, _ in extract.scan_and_extract(target_dir=tmp_path)}
    assert names == {"a.xlsx", "b.pptx"}


# ============================================================
# daemon-fleet-health: one bad beat does not end the report
# ============================================================

@pytest.fixture(scope="module")
def fleet():
    return _load("daemon_fleet_health_mod", "scripts/daemon-fleet-health.py")


def _beat(tmp_path: Path, body: str, name: str = "ws") -> Path:
    ws = tmp_path / name
    (ws / ".daemon-state").mkdir(parents=True)
    (ws / ".daemon-state" / "heartbeat.json").write_text(body, encoding="utf-8")
    return ws


@pytest.mark.parametrize("body", ["null", "[]", '"a string"', "42", "true"])
def test_a_heartbeat_that_is_not_an_object_is_an_error_record(fleet, tmp_path,
                                                              body):
    """It used to raise TypeError past the only handler in the function."""
    record = fleet._read_heartbeat(_beat(tmp_path, body))
    assert record["status"] == "error"
    assert "not an object" in record["detail"]


def test_the_error_record_names_the_workspace(fleet, tmp_path):
    ws = _beat(tmp_path, "[]")
    assert fleet._read_heartbeat(ws)["workspace"] == str(ws)


def test_the_error_record_names_the_type_it_found(fleet, tmp_path):
    assert "list" in fleet._read_heartbeat(_beat(tmp_path, "[]"))["detail"]


def test_unparseable_json_is_still_its_own_message(fleet, tmp_path):
    """The shape guard must not swallow the syntax one."""
    record = fleet._read_heartbeat(_beat(tmp_path, "{not json"))
    assert record["status"] == "error"
    assert "parse failed" in record["detail"]


def test_a_missing_heartbeat_is_missing_not_error(fleet, tmp_path):
    ws = tmp_path / "empty"
    ws.mkdir()
    assert fleet._read_heartbeat(ws)["status"] == "missing"


def test_a_real_heartbeat_passes_through_with_its_fields(fleet, tmp_path):
    body = json.dumps({"last_heartbeat": "2026-08-24T10:00:00+00:00",
                       "version": "1.2.3"})
    record = fleet._read_heartbeat(_beat(tmp_path, body))
    assert record["version"] == "1.2.3"
    assert record["kind"] == "local"


def test_a_bad_beat_can_be_classified_rather_than_crash(fleet, tmp_path):
    """End to end: the record the guard returns must survive _classify."""
    record = fleet._read_heartbeat(_beat(tmp_path, "[]"))
    assert fleet._classify(record, 120, None) == "error"
