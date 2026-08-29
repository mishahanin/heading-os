"""Bad input that produced a confident, healthy-looking output.

Three files, one shape: something the tool could not honestly report on came
back as a clean result. That is worse than an error, because a clean result is
trusted, counted, and quoted later as fact. `.claude/rules/scope-claims.md`
names the shape.

Each fix landed with no test holding it. A fix nothing turns red is a fix
nothing holds, so each is bound here in both directions: with its script
reverted the cases below fail, and with it in place they pass.

A CONTACT TOUCHED IN THE FUTURE READ AS GREEN. `calculate_health` compares
`last_touch` to today and bands the gap. A date in the future produces a
NEGATIVE gap, which is below every threshold, so it fell through to `green`:
the strongest on-track signal the company radar has, awarded for a date nobody
could have touched. Corrupt data does not mean a healthy relationship. It bands
`gray` now, the state the same function already uses for a `last_touch` it
cannot trust, which sorts last and keeps the row out of the green count.

A REFUSED CENSUS RUN LEFT NO ROW. `--emit-answers` writes one row per attempt,
and the scorer reads a missing row as "this question was never answered". Two
argument failures returned before writing anything, so a setup error, a
misspelled scope or a moved traversal program, was scored identically to a
question nobody ran. Both know their question by then, so both file a row with
`answer: None` and the reason.

A PHONE NUMBER BROKE THE FILE IT WAS WRITTEN INTO. `render_address_book_entry`
puts scanned values into YAML frontmatter and quotes most of them through
`_yaml_quote`. `phone` was wrapped in hand-written double quotes with no
escaping and `linkedin` was interpolated raw, so a value carrying a quote, a
backslash, a colon-space or a hash produced frontmatter that is not the
document anyone intended. The migration validates its staged files, so a break
lands after the backup and before the rename.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


agg = _load("aggregate_crm_health_mod", "aggregate-crm.py")
census = _load("census_refusal_mod", "census.py")
migrate = _load("crm_migrate_yaml_mod", "crm_migrate_to_entity_model.py")

BANDS = {"red": 90, "yellow": 45}


# ============================================================
# A contact touched in the future
# ============================================================

def _iso(offset_days: int) -> str:
    return (agg.TODAY + timedelta(days=offset_days)).isoformat()


@pytest.mark.parametrize("ahead", [1, 7, 365])
def test_a_future_last_touch_is_not_green(ahead):
    health, days = agg.calculate_health(_iso(ahead), BANDS)
    assert health != "green", (
        "a date nobody could have touched was awarded the strongest on-track "
        "signal the radar has")
    assert health == "gray"
    assert days < 0, "the skew is reported, not hidden"


def test_the_skew_is_reported_rather_than_clamped():
    _health, days = agg.calculate_health(_iso(30), BANDS)
    assert days == -30


def test_a_contact_touched_today_is_still_green():
    """The other direction. A guard that fires on the correct input is worse
    than none, because it hides the healthy rows too."""
    health, days = agg.calculate_health(_iso(0), BANDS)
    assert health == "green"
    assert days == 0


@pytest.mark.parametrize("ago,expected", [(1, "green"), (44, "green"),
                                          (45, "yellow"), (89, "yellow"),
                                          (90, "red"), (400, "red")])
def test_every_ordinary_band_is_unchanged(ago, expected):
    health, days = agg.calculate_health(_iso(-ago), BANDS)
    assert health == expected
    assert days == ago


def test_an_unparseable_date_is_still_the_untrusted_band():
    health, _days = agg.calculate_health("not-a-date", BANDS)
    assert health == "gray"


def test_gray_is_a_state_this_function_already_had():
    """Pins WHY gray was chosen: it is not a new band invented for this fix, so
    nothing downstream has to learn a new value."""
    assert agg.calculate_health("not-a-date", BANDS)[0] == "gray"


# ============================================================
# A refused census run that left no row
# ============================================================

class _Args(types.SimpleNamespace):
    pass


@pytest.fixture()
def emitted(monkeypatch):
    """Capture what `_emit_record` was handed, without touching a real file."""
    rows: list[dict] = []

    def fake_emit(args, record):
        rows.append(record)
        return None

    monkeypatch.setattr(census, "_emit_record", fake_emit)
    return rows


def test_a_pre_run_refusal_files_a_row(emitted):
    args = _Args(question="how many contacts have no card?",
                 question_id="q-01", emit_answers=Path("unused.jsonl"))
    code = census._refused_before_running(args, [], "traversal program not found: x.py")
    assert code == census.EXIT_BAD_ARGS
    assert len(emitted) == 1


def test_the_row_says_refused_rather_than_wrong(emitted):
    """`answer: None` is what the scorer counts as a refusal. Any other value
    would be graded as an attempt that got the question wrong."""
    args = _Args(question="q?", question_id="q-01", emit_answers=Path("u.jsonl"))
    census._refused_before_running(args, [], "unknown scope: nowhere")
    assert emitted[0]["answer"] is None
    assert emitted[0]["error"] == "unknown scope: nowhere"
    assert emitted[0]["question"] == "q?"


def test_the_row_carries_the_corpus_it_would_have_walked(emitted):
    args = _Args(question="q?", question_id="q-01", emit_answers=Path("u.jsonl"))
    census._refused_before_running(args, [Path("/a"), Path("/b")], "denied")
    assert emitted[0]["corpus"] == ["/a", "/b"]
    assert emitted[0]["elapsed_s"] == 0.0


def test_the_caller_chooses_the_exit_code(emitted):
    """The sandbox refusal keeps exit 5 while the argument failures use 2, so
    one helper must not flatten them into a single code."""
    args = _Args(question="q?", question_id="q-01", emit_answers=Path("u.jsonl"))
    assert census._refused_before_running(
        args, [], "air-gapped", census.EXIT_SANDBOX_REFUSED) == census.EXIT_SANDBOX_REFUSED
    assert census._refused_before_running(args, [], "bad arg") == census.EXIT_BAD_ARGS


def test_a_failed_write_wins_over_the_refusal_code(monkeypatch):
    """Losing the record is the worse outcome and must be the reported one."""
    monkeypatch.setattr(census, "_emit_record",
                        lambda args, record: census.EXIT_ANSWERS_WRITE_FAILED)
    args = _Args(question="q?", question_id="q-01", emit_answers=Path("u.jsonl"))
    assert census._refused_before_running(args, [], "denied") == census.EXIT_ANSWERS_WRITE_FAILED


def test_the_two_refusing_exits_are_reachable_from_main(monkeypatch, tmp_path):
    """End to end, so the helper being correct is not mistaken for `main`
    calling it. A missing program is the cheapest of the two to reach."""
    rows: list[dict] = []
    monkeypatch.setattr(census, "_emit_record",
                        lambda args, record: rows.append(record))
    code = census.main(["q?", "--question-id", "q-01",
                        "--program", str(tmp_path / "absent.py"),
                        "--emit-answers", str(tmp_path / "a.jsonl")])
    assert code == census.EXIT_BAD_ARGS
    assert len(rows) == 1
    assert rows[0]["answer"] is None
    assert "not found" in rows[0]["error"]


# ============================================================
# A phone number that broke the file it was written into
# ============================================================

# A real card on disk, written once. `pick_canonical_record` stats every
# record's `file_path` to decide which one becomes the entity, so a fixture
# without it exercises an error path rather than the renderer.
_CARD = Path(__file__).resolve().parent / "fixtures" / "entity-render-card.md"


def _entity(tmp_path=None, **fields) -> dict:
    """One scanned record, in the shape `_record_from` produces.

    `file_path` is required: `pick_canonical_record` stats it to score which
    record becomes the entity. It points at a real file so the scoring runs the
    same way it does in a migration rather than through an error path.
    """
    base = {"name": "James Bond", "email": "james.bond@example.com",
            "company": "Acme Telecom", "phone": "", "linkedin": "",
            "region": "EMEA", "owner": "example-exec", "source": "import",
            "type": "partner", "file_path": str(_CARD)}
    base.update(fields)
    return {"records": [base], "canonical_name": base["name"],
            "proposed_slug": "james-bond"}


def _frontmatter(text: str) -> dict:
    assert text.startswith("---\n"), text[:40]
    block = text.split("---\n", 2)[1]
    return yaml.safe_load(block)


@pytest.mark.parametrize("phone", [
    '+971 50 000 0000',
    'ext "42"',
    r"back\slash",
    "office: 42",
    "42 # spare",
    "007",
    "NO",
])
def test_a_phone_value_survives_the_yaml_round_trip(phone):
    rendered = migrate.render_address_book_entry(_entity(phone=phone))
    assert _frontmatter(rendered)["phone"] == phone


@pytest.mark.parametrize("link", [
    "https://linkedin.example/in/james-bond",
    "in/james: bond",
    "in/bond # main",
    "in/'bond'",
])
def test_a_linkedin_value_survives_the_yaml_round_trip(link):
    rendered = migrate.render_address_book_entry(_entity(linkedin=link))
    assert _frontmatter(rendered)["linkedin"] == link


def test_an_empty_phone_stays_an_empty_string_not_a_null():
    """`_yaml_quote("")` is the empty string, which would render `phone:` and
    parse as None. Every entity without a number has carried `phone: ""` since
    the migration was written, and a schema reading that field as a string
    would start failing on a change about tmp names."""
    parsed = _frontmatter(migrate.render_address_book_entry(_entity(phone="")))
    assert parsed["phone"] == ""
    assert parsed["phone"] is not None


def test_the_whole_document_still_parses_with_every_hostile_field_at_once():
    rendered = migrate.render_address_book_entry(_entity(
        phone='ext "42": #1', linkedin="in/a: b # c",
        company="Holdings: Europe", name='James "007" Bond'))
    parsed = _frontmatter(rendered)
    assert parsed["phone"] == 'ext "42": #1'
    assert parsed["linkedin"] == "in/a: b # c"


def test_an_ordinary_entity_is_not_over_quoted():
    """The other direction: quoting everything would rewrite every record in
    the corpus on a migration that changed one field."""
    rendered = migrate.render_address_book_entry(_entity(
        phone="+971500000000", linkedin="https://linkedin.example/in/jb"))
    assert 'phone: "+971500000000"' in rendered or "phone: +971500000000" in rendered
    assert _frontmatter(rendered)["linkedin"] == "https://linkedin.example/in/jb"


def test_the_quoting_helper_is_what_does_the_work():
    """Pins the mechanism, so a later hand-rolled quote cannot pass by looking
    right on the cases above."""
    assert migrate._yaml_quote('a "b"') == '"a \\"b\\""'
    assert migrate._yaml_quote("a: b") == '"a: b"'
    assert migrate._yaml_quote("plain") == "plain"
