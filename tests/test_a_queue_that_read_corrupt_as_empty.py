"""Shard 00-p1: five tools that answered a narrower question than they printed.

`action-queue-execute.py` caught `json.JSONDecodeError` beside `OSError`, printed
`[]` and exited 0. The documented caller contract is "capture this stdout and
apply the status changes", so one stray comma in `queue.json` meant the caller
applied nothing, reported success, and every approved send card was dropped from
that run with no diagnostic anywhere. `load_fleet_registry` in `aggregate-crm.py`
states the principle in its own docstring: absent and unreadable are different
facts and must not share an answer.

`aggregate-crm.py --json` wrote NOTHING on an empty fleet and exited 0, so a
consumer piping stdout into a parser got EOF while the exit code said success --
while `admin-health.py` prints a well-formed empty document in the same case.

`parse_config` returned the full `DEFAULT_CADENCE` for an absent file and for an
unparseable table, but a PARTIAL table replaced the defaults outright: every type
the table omitted fell through to a hardcoded 14/10/14. A config listing only
`partner` pushed `media` from 60 days to 14 and painted it red -- the false-red
failure `get_thresholds` warns about, caused by the file meant to prevent it.

`admin-health.py` excluded exactly `README.md` while the aggregator excluded
`readme.md` case-insensitively, and `_get_contact_files` excluded neither. It
also grouped shared contacts by FILENAME, so one person saved under two
filenames counted as two people here and one in `shared-contacts.md`. And its
skew guard returned STALE only BEYOND tolerance, so a commit two minutes ahead
of this clock rendered "-120 sec ago" beside an OK row.

Refuted rather than fixed: the race the `approve_and_send` comment described.
See `test_the_documented_batch_executor_race_cannot_happen`.

Tests: this file.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import crm as crm_utils  # noqa: E402


def _load(stem: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


AGG = _load("aggregate-crm", "aggregate_crm_00p1")
AQ = _load("action-queue", "action_queue_00p1")
AQE = _load("action-queue-execute", "action_queue_execute_00p1")
AH = _load("admin-health", "admin_health_00p1")


# ==========================================================================
# 1 - the queue file that could not be read, reported as empty
# ==========================================================================

@pytest.fixture()
def queue_at(tmp_path, monkeypatch):
    """Point the executor at a queue file under tmp_path."""
    outputs = tmp_path / "outputs"
    (outputs / "operations/action-queue").mkdir(parents=True)
    monkeypatch.setattr(AQE, "get_outputs_dir", lambda: outputs)
    monkeypatch.setattr(AQE, "get_workspace_root", lambda: tmp_path)
    return outputs / "operations/action-queue/queue.json"


def test_a_corrupt_queue_does_not_exit_zero(queue_at, capsys):
    """The finding. Exit 0 told the caller "nothing to send" about a file it
    could not read, and every approved card vanished from the run."""
    queue_at.write_text("{not json", encoding="utf-8")
    assert AQE.main() != 0
    capsys.readouterr()


def test_a_corrupt_queue_says_so_on_stderr(queue_at, capsys):
    queue_at.write_text("{not json", encoding="utf-8")
    AQE.main()
    err = capsys.readouterr().err
    assert "cannot read" in err
    assert str(queue_at) in err, "the diagnostic must name the file"


def test_a_corrupt_queue_still_prints_parseable_stdout(queue_at, capsys):
    """The caller parses stdout unconditionally; a traceback there helps nobody."""
    queue_at.write_text("{not json", encoding="utf-8")
    AQE.main()
    assert json.loads(capsys.readouterr().out) == []


def test_an_absent_queue_is_not_an_error(queue_at, capsys):
    """Absent is a fact: there is nothing to send, and that is a clean run."""
    assert not queue_at.exists()
    assert AQE.main() == 0
    assert json.loads(capsys.readouterr().out) == []


def test_an_empty_queue_is_not_an_error(queue_at, capsys):
    queue_at.write_text(json.dumps({"actions": []}), encoding="utf-8")
    assert AQE.main() == 0
    assert json.loads(capsys.readouterr().out) == []


def test_a_readable_queue_with_no_approved_cards_exits_zero(queue_at, capsys):
    queue_at.write_text(json.dumps(
        {"actions": [{"id": "a", "status": "pending", "action_type": "note"}]}),
        encoding="utf-8")
    assert AQE.main() == 0
    assert json.loads(capsys.readouterr().out) == []


def test_absent_and_unreadable_do_not_share_an_exit_code(queue_at, capsys):
    absent = AQE.main()
    capsys.readouterr()
    queue_at.write_text("{not json", encoding="utf-8")
    corrupt = AQE.main()
    capsys.readouterr()
    assert absent != corrupt


# ==========================================================================
# 2 - the --json run that emitted no JSON
# ==========================================================================

def _json_stats(capsys, **kwargs):
    AGG.print_json_stats([], 0, [], kwargs.get("errors", []))
    return json.loads(capsys.readouterr().out)


def test_an_empty_fleet_still_emits_a_json_document(capsys):
    doc = _json_stats(capsys)
    assert doc["total_contacts"] == 0
    assert doc["exec_count"] == 0
    assert doc["shared_contacts"] == 0


def test_the_empty_document_carries_the_same_keys_as_a_full_one(capsys):
    AGG.print_json_stats([], 0, [], [])
    empty = json.loads(capsys.readouterr().out)
    AGG.print_json_stats(
        [{"health": "green", "owner_slug": "jd"}], 1, [], [])
    full = json.loads(capsys.readouterr().out)
    assert set(empty) == set(full), "a consumer must not have to special-case empty"


def test_the_empty_document_still_reports_errors(capsys):
    doc = _json_stats(capsys, errors=["one repo could not be pulled"])
    assert doc["errors"] == ["one repo could not be pulled"]


def test_main_prints_the_empty_document_before_exiting(monkeypatch, capsys):
    """The whole finding: stdout was silent and the exit code said success."""
    monkeypatch.setattr(AGG, "scan_all_contacts", lambda *a, **k: ([], []))
    monkeypatch.setattr(AGG, "load_admin_config", dict)
    monkeypatch.setattr(AGG, "parse_config", lambda p: AGG.DEFAULT_CADENCE.copy())
    monkeypatch.setattr(sys, "argv", ["aggregate-crm.py", "--json"])
    with pytest.raises(SystemExit) as exc:
        AGG.main()
    assert exc.value.code == 0
    assert json.loads(capsys.readouterr().out)["total_contacts"] == 0


def test_main_stays_quiet_on_stdout_without_the_json_flag(monkeypatch, capsys):
    monkeypatch.setattr(AGG, "scan_all_contacts", lambda *a, **k: ([], []))
    monkeypatch.setattr(AGG, "load_admin_config", dict)
    monkeypatch.setattr(AGG, "parse_config", lambda p: AGG.DEFAULT_CADENCE.copy())
    monkeypatch.setattr(sys, "argv", ["aggregate-crm.py"])
    with pytest.raises(SystemExit):
        AGG.main()
    assert capsys.readouterr().out == "", "a terminal run must not print JSON"


# ==========================================================================
# 3 - the partial config table that overrode what it never mentioned
# ==========================================================================

def _config(tmp_path, rows: str) -> Path:
    p = tmp_path / "config.md"
    p.write_text("| Type | Cadence | Yellow | Red |\n|---|---|---|---|\n" + rows,
                 encoding="utf-8")
    return p


def test_a_partial_table_keeps_the_defaults_for_unlisted_types(tmp_path):
    """The finding. A table naming only `partner` moved `media` to 14 days."""
    cfg = AGG.parse_config(_config(tmp_path, "| partner | 21 | 14 | 21 |\n"))
    assert cfg["media"] == AGG.DEFAULT_CADENCE["media"]


def test_the_listed_row_still_wins(tmp_path):
    cfg = AGG.parse_config(_config(tmp_path, "| partner | 21 | 14 | 21 |\n"))
    assert cfg["partner"] == {"cadence": 21, "yellow": 14, "red": 21}


def test_a_partial_table_does_not_turn_media_red(tmp_path):
    """End to end, in the number the radar prints."""
    cfg = AGG.parse_config(_config(tmp_path, "| partner | 21 | 14 | 21 |\n"))
    thresholds = AGG.get_thresholds({"type": "media"}, cfg)
    health, _days = AGG.calculate_health(
        (AGG.TODAY - __import__("datetime").timedelta(days=20)).isoformat(), thresholds)
    assert health == "green", "an unlisted type was accelerated to a 14-day cadence"


def test_an_absent_config_still_returns_the_defaults(tmp_path):
    assert AGG.parse_config(tmp_path / "nope.md") == AGG.DEFAULT_CADENCE


def test_an_unparseable_table_still_returns_the_defaults(tmp_path):
    p = tmp_path / "config.md"
    p.write_text("no table here at all\n", encoding="utf-8")
    assert AGG.parse_config(p) == AGG.DEFAULT_CADENCE


def test_a_table_may_add_a_type_the_defaults_do_not_know(tmp_path):
    cfg = AGG.parse_config(_config(tmp_path, "| ombudsman | 90 | 60 | 90 |\n"))
    assert cfg["ombudsman"] == {"cadence": 90, "yellow": 60, "red": 90}


def test_a_type_in_neither_place_still_falls_to_the_frequent_default():
    """A genuinely unknown label should surface, not sleep for sixty days."""
    assert AGG.get_thresholds({"type": "invented"}, AGG.DEFAULT_CADENCE.copy()) == {
        "cadence": 14, "yellow": 10, "red": 14}


# ==========================================================================
# 4 - the README rule that three files spelled three ways
# ==========================================================================

@pytest.mark.parametrize("name", ["README.md", "readme.md", "Readme.md", "README.MD"])
def test_no_spelling_of_readme_is_a_contact(tmp_path, name):
    p = tmp_path / name
    p.write_text("x", encoding="utf-8")
    assert crm_utils.is_contact_file(p) is False


def test_an_ordinary_contact_is_a_contact(tmp_path):
    p = tmp_path / "jordan-kim.md"
    p.write_text("x", encoding="utf-8")
    assert crm_utils.is_contact_file(p) is True


def test_an_uppercase_extension_is_still_a_contact(tmp_path):
    p = tmp_path / "jordan-kim.MD"
    p.write_text("x", encoding="utf-8")
    assert crm_utils.is_contact_file(p) is True


def test_a_non_markdown_file_is_not_a_contact(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("x", encoding="utf-8")
    assert crm_utils.is_contact_file(p) is False


def test_a_directory_is_not_a_contact(tmp_path):
    d = tmp_path / "archive.md"
    d.mkdir()
    assert crm_utils.is_contact_file(d) is False


def test_both_fleet_tools_reach_the_same_predicate():
    """The drift itself: two modules, one rule, verified by identity."""
    assert AH.is_contact_file is crm_utils.is_contact_file
    assert AGG.is_contact_file is crm_utils.is_contact_file


# ==========================================================================
# 5 - the shared-contact count that keyed on a filename
# ==========================================================================

def test_one_person_under_two_filenames_is_one_person():
    """The finding. `jordan-kim.md` and `kim-jordan.md` counted as two."""
    a = crm_utils.contact_identity_key({"name": "Jordan Kim"})
    b = crm_utils.contact_identity_key({"name": "jordan  kim"})
    assert a == b


def test_two_different_people_stay_two():
    assert (crm_utils.contact_identity_key({"name": "Jordan Kim"})
            != crm_utils.contact_identity_key({"name": "Dana Cole"}))


def test_an_entity_ref_wins_over_the_name():
    """Canonical identity: two spellings of one entity are one key."""
    a = crm_utils.contact_identity_key({"entity_ref": "jordan-kim", "name": "J. Kim"})
    b = crm_utils.contact_identity_key({"entity_ref": "jordan-kim", "name": "Jordan Kim"})
    assert a == b


def test_an_entity_key_cannot_collide_with_a_legacy_key():
    assert not crm_utils.contact_identity_key({"entity_ref": "x"}).startswith("legacy::")


def test_every_nameless_record_produces_the_same_key():
    """Renamed 2026-08-30, because the old name asserted the opposite of the code.

    It was `test_a_nameless_record_does_not_become_everyone`, and its docstring
    said "two records with no name at all must not merge into one person" -
    while its single assertion pinned the exact value under which they DO merge.
    Every nameless record maps to `legacy::name::`, so any two of them land in
    one bucket in any dict or `groupby` keyed on it. The inline comment conceded
    this; the name and docstring contradicted it, and the assertion locked the
    hazardous value in as expected, so a future fix that disambiguated nameless
    records would have failed this test for being correct.

    The real contract, stated: the key is DETERMINISTIC, it collides for
    nameless records, and the caller is what must filter them. That filtering is
    no longer merely documented - `test_the_caller_drops_a_nameless_record`
    below measures it.
    """
    assert crm_utils.contact_identity_key({}) == "legacy::name::"
    assert crm_utils.contact_identity_key({}) == crm_utils.contact_identity_key({})
    assert crm_utils.contact_identity_key({"name": ""}) == "legacy::name::", (
        "an empty name is the same nameless state and must not fork the key")


def test_the_caller_drops_a_nameless_record(tmp_path):
    """The claim the comment made, measured instead of asserted.

    "The caller filters nameless records before grouping" was the only thing
    standing between the colliding key above and a phantom shared contact, and
    nothing checked it. If `scan_contacts` ever stops dropping these, the
    collision becomes reachable and this goes red.
    """
    contacts = tmp_path / "contacts"
    contacts.mkdir()
    (contacts / "nameless.md").write_text(
        "---\ntype: partner\nlast_touch: 2026-08-01\n---\n\nNo name field.\n",
        encoding="utf-8")
    (contacts / "named.md").write_text(
        "---\nname: Jordan Kim\ntype: partner\nlast_touch: 2026-08-01\n---\n\nBody.\n",
        encoding="utf-8")

    found, *_ = crm_utils.scan_contacts({}, today=date(2026, 8, 20),
                                        contacts_dir=contacts,
                                        workspace_root=tmp_path)

    names = [c.get("name") for c in found]
    assert "Jordan Kim" in names, "the readable record vanished; the fixture is wrong"
    assert not [n for n in names if not n], (
        f"scan_contacts kept a nameless record, so the colliding "
        f"`legacy::name::` key is now reachable: {names}")


def test_the_aggregator_delegates_to_the_shared_key():
    """`_legacy_fuzzy_key` must not be a second copy of the rule."""
    rec = {"name": "Jordan Kim", "company": "CraneCo"}
    assert AGG._legacy_fuzzy_key(rec) == crm_utils.contact_identity_key({"name": "Jordan Kim"})


def test_the_company_stays_out_of_the_legacy_key():
    """Company strings differ across exec repos for known dual-owners."""
    a = AGG._legacy_fuzzy_key({"name": "Jordan Kim", "company": "CraneCo"})
    b = AGG._legacy_fuzzy_key({"name": "Jordan Kim", "company": "Crane-Co"})
    assert a == b


def test_the_dashboard_uses_the_same_key_function():
    assert AH.contact_identity_key is crm_utils.contact_identity_key


# ==========================================================================
# 6 - the clock that ran backwards inside its own tolerance
# ==========================================================================

def _status_for(delta_seconds: float):
    """`calculate_status` reads `last_commit`, an ISO-8601 committer date.

    A negative `delta_seconds` puts the commit AHEAD of this clock, which is
    what clock skew looks like.
    """
    from datetime import datetime, timedelta, timezone
    when = datetime.now(timezone.utc) - timedelta(seconds=delta_seconds)
    return AH.calculate_status({"last_commit": when.isoformat()})


def test_skew_inside_tolerance_does_not_print_a_negative_age():
    """The finding. A commit 2 minutes ahead rendered "-120 sec ago"."""
    _status, _label, time_ago = _status_for(-120)
    assert not time_ago.startswith("-"), time_ago


def test_skew_inside_tolerance_still_reads_as_fresh():
    status, _label, _ago = _status_for(-120)
    assert status == "OK"


def test_skew_beyond_tolerance_is_still_stale():
    status, _label, note = _status_for(-(AH.SKEW_TOLERANCE + 60))
    assert status == "STALE"
    assert "ahead" in note


def test_an_ordinary_age_is_unchanged():
    _status, _label, time_ago = _status_for(3600 * 5)
    assert "hours ago" in time_ago


def test_a_fresh_commit_reads_in_seconds():
    _status, _label, time_ago = _status_for(30)
    assert time_ago.endswith("sec ago")
    assert not time_ago.startswith("-")


def test_zero_skew_is_not_special_cased():
    """Zero is an ordinary age, not a branch.

    Widened 2026-08-30. `_status_for(0)` captures `datetime.now()`, then
    `calculate_status` captures its own "now" a few statements later, and this
    asserted the gap between them was exactly `"0 sec ago"`. Any stall of one
    second - a loaded CI runner, a GC pause, scheduler preemption - failed it
    with `"1 sec ago"`, which says nothing about the property under test. The
    sibling cases carry 30s and 120s of slack; only the zero case had none.

    The property is that zero renders as a small POSITIVE age like any other:
    not negative, not special-cased, not absent.
    """
    _status, _label, time_ago = _status_for(0)
    assert time_ago in {"0 sec ago", "1 sec ago", "2 sec ago"}, time_ago
    assert not time_ago.startswith("-"), f"zero rendered as a negative age: {time_ago}"


# ==========================================================================
# 7 - the race the comment described, which the code prevents
# ==========================================================================

def test_the_documented_batch_executor_race_cannot_happen():
    """Refuted. The comment said the card "stays `approved` during the send,
    so a concurrent batch executor could still select it."

    Both halves are false, and each is pinned here so the next audit does not
    re-derive them. `approved` is excluded from `SENDABLE_STATUSES`, so this
    path returns `blocked` for an approved card and never sends one; and the
    batch executor selects ONLY cards whose status IS `approved`.
    """
    assert "approved" not in AQ.SENDABLE_STATUSES


def test_sendable_is_derived_from_active_not_typed_out():
    """A hand-typed set is the thing that drifts back."""
    assert frozenset(AQ.ACTIVE_STATUSES) - {"approved", AQ.SENDING} == AQ.SENDABLE_STATUSES


_NON_APPROVED_STATUSES = ["pending", "gated", "send_failed", "dismissed",
                          "sent", "sending", "", "APPROVED"]


@pytest.mark.parametrize("status", _NON_APPROVED_STATUSES)
def test_the_executor_selects_only_approved_cards(queue_at, capsys, status):
    """The send-safety gate, driven rather than grepped.

    Rewritten 2026-08-30. This was the ONLY test pinning "the batch executor
    selects ONLY approved cards", and it was two raw substring checks over the
    source:

        assert 'if card.get("status") != "approved":' in source
        assert "continue" in source

    Both are satisfiable without the guard existing. `"continue" in source`
    matches the word in ANY unrelated loop, so it pinned nothing; and a
    substring check is satisfied by the guard sitting in a comment or a
    docstring while the executable code sends every card. Paste the first line
    into the module docstring, delete the real guard, and it stayed green while
    non-approved cards went out.

    `APPROVED` is in the list on purpose: the comparison is case-sensitive, and
    a card whose status differs only in case is not an approved card.
    """
    queue_at.write_text(json.dumps({"actions": [
        {"id": "card-1", "status": status, "action_type": "email_send",
         "to": "nobody@example.invalid", "subject": "s", "body": "b"},
    ]}), encoding="utf-8")

    assert AQE.main() == 0
    assert json.loads(capsys.readouterr().out) == [], (
        f"a card with status {status!r} was selected for sending")


def test_the_approved_status_guard_is_executable_code_not_a_comment():
    """The other half: a guard nothing selects is also a guard that never runs.

    The behavioural test above proves non-approved cards are skipped, but a
    file that selected NOTHING would satisfy it too. This pins the guard as a
    real `If` statement whose body is `continue`, inside the loop over the
    cards - read by AST, so a comment or a docstring quoting the same line
    cannot stand in for it.
    """
    tree = ast.parse((ROOT / "scripts" / "action-queue-execute.py")
                     .read_text(encoding="utf-8"))

    guards = []
    for loop in ast.walk(tree):
        if not (isinstance(loop, ast.For) and isinstance(loop.target, ast.Name)):
            continue
        var = loop.target.id
        for node in ast.walk(loop):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            if not (isinstance(test, ast.Compare)
                    and len(test.ops) == 1 and isinstance(test.ops[0], ast.NotEq)):
                continue
            call = test.left
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "get"
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == var
                    and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and call.args[0].value == "status"):
                continue
            right = test.comparators[0]
            if not (isinstance(right, ast.Constant) and right.value == "approved"):
                continue
            if any(isinstance(b, ast.Continue) for b in node.body):
                guards.append(loop.lineno)

    assert guards, (
        "no `if <card>.get('status') != 'approved': continue` statement exists "
        "inside a loop over the queue's cards; the send-safety gate is gone or "
        "has moved somewhere this test cannot see it")


def test_the_comment_no_longer_describes_the_impossible_race():
    source = (ROOT / "scripts" / "action-queue.py").read_text(encoding="utf-8")
    assert "The card stays `approved` for the up-to-120s" not in source


def test_the_race_the_comment_named_is_now_closed(tmp_path):
    """The race this section used to only DESCRIBE is closed by a claim.

    This asserted that a phrase appeared in the source, which punished the file
    for rewording its own warning and proved nothing about behaviour. It drives
    the claim instead: a card already claimed refuses the second claimer.
    """
    import json as _json
    qpath = tmp_path / "outputs/operations/action-queue/queue.json"
    qpath.parent.mkdir(parents=True, exist_ok=True)
    card = {"id": "race-0001", "action_type": "email_send", "status": "pending",
            "draft_status": "ready_for_review", "to": "a@example.com",
            "subject": "s", "draft_body": "b"}
    qpath.write_text(_json.dumps({"version": 1, "generated_at": None,
                                  "actions": [card]}), encoding="utf-8")

    first = AQ.claim_card_for_send(tmp_path, "race-0001", AQ.SENDABLE_STATUSES)
    second = AQ.claim_card_for_send(tmp_path, "race-0001", AQ.SENDABLE_STATUSES)

    assert first["ok"] is True and first["prev_status"] == "pending"
    assert second["ok"] is False
    assert second["status"] == AQ.SENDING
    assert "may still be sending" in second["error"]


# ==========================================================================
# 8 - the same five properties, asserted where they are USED
# ==========================================================================
#
# Five mutations survived the first pass because these tests checked the
# helper, or the import, and never the call site. `AH.is_contact_file is
# crm_utils.is_contact_file` proves the name was imported; it says nothing
# about whether `collect_exec_state` calls it. The tests below drive the real
# functions over a real directory.

@pytest.fixture()
def overlay(tmp_path, monkeypatch):
    """Give each exec slug its own contacts directory under tmp_path."""
    def _dir_for(slug):
        d = tmp_path / slug / "crm" / "contacts"
        d.mkdir(parents=True, exist_ok=True)
        return d
    monkeypatch.setattr(AH, "get_per_exec_contacts_dir", _dir_for)
    monkeypatch.setattr(AH, "read_last_commit", lambda repo: None)
    return _dir_for


def _write_contact(directory: Path, filename: str, name: str, ref: str = "") -> Path:
    ref_line = f"entity_ref: {ref}\n" if ref else ""
    (directory / filename).write_text(
        f"---\nname: {name}\n{ref_line}---\n\nnotes\n", encoding="utf-8")
    return directory / filename


def test_the_dashboard_does_not_count_a_lowercase_readme(overlay):
    """F5 at the call site: the count itself, not the imported name."""
    d = overlay("jane")
    _write_contact(d, "jordan-kim.md", "Jordan Kim")
    (d / "readme.md").write_text("# how this folder works\n", encoding="utf-8")
    records = AH.collect_exec_state([("jane", Path("/nowhere"))])
    assert records[0]["contact_count"] == 1, "a lowercase readme was counted as a contact"


def test_the_dashboard_does_not_count_an_uppercase_readme(overlay):
    d = overlay("jane")
    _write_contact(d, "jordan-kim.md", "Jordan Kim")
    (d / "README.md").write_text("# how this folder works\n", encoding="utf-8")
    assert AH.collect_exec_state([("jane", Path("/nowhere"))])[0]["contact_count"] == 1


def test_the_dashboard_counts_ordinary_contacts(overlay):
    d = overlay("jane")
    _write_contact(d, "a.md", "A Person")
    _write_contact(d, "b.md", "B Person")
    assert AH.collect_exec_state([("jane", Path("/nowhere"))])[0]["contact_count"] == 2


def test_one_person_under_two_filenames_counts_once(overlay):
    """K1 at the call site. Keyed on filename this returned 0 shared."""
    _write_contact(overlay("jane"), "jordan-kim.md", "Jordan Kim")
    _write_contact(overlay("alex"), "kim-jordan.md", "Jordan Kim")
    shared = AH.find_shared_contacts([("jane", Path("/n")), ("alex", Path("/n"))])
    assert shared == 1, "the same person under two filenames was not seen as shared"


def test_two_different_people_are_not_shared(overlay):
    _write_contact(overlay("jane"), "jordan-kim.md", "Jordan Kim")
    _write_contact(overlay("alex"), "dana-cole.md", "Dana Cole")
    assert AH.find_shared_contacts([("jane", Path("/n")), ("alex", Path("/n"))]) == 0


def test_an_entity_ref_groups_across_two_spellings(overlay):
    _write_contact(overlay("jane"), "jk.md", "J. Kim", ref="jordan-kim")
    _write_contact(overlay("alex"), "jordan.md", "Jordan Kim", ref="jordan-kim")
    assert AH.find_shared_contacts([("jane", Path("/n")), ("alex", Path("/n"))]) == 1


def test_an_unreadable_contact_is_skipped_and_reported(overlay, monkeypatch, capsys):
    """K9. Counting an unreadable file as a nameless person merges it with
    every other unreadable file into one phantom shared contact."""
    d_jane = overlay("jane")
    d_alex = overlay("alex")
    bad_a = _write_contact(d_jane, "broken-a.md", "A")
    bad_b = _write_contact(d_alex, "broken-b.md", "B")

    real_read = Path.read_text

    def _read(self, *a, **k):
        if self in (bad_a, bad_b):
            raise OSError("permission denied")
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", _read)
    shared = AH.find_shared_contacts([("jane", Path("/n")), ("alex", Path("/n"))])
    assert shared == 0, "two unreadable files merged into one phantom shared person"
    assert "unreadable contact" in capsys.readouterr().err


def test_a_readable_neighbour_survives_an_unreadable_file(overlay, monkeypatch):
    d_jane = overlay("jane")
    d_alex = overlay("alex")
    bad = _write_contact(d_jane, "broken.md", "Broken")
    _write_contact(d_jane, "jordan-kim.md", "Jordan Kim")
    _write_contact(d_alex, "kim-jordan.md", "Jordan Kim")

    real_read = Path.read_text

    def _read(self, *a, **k):
        if self == bad:
            raise OSError("permission denied")
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", _read)
    assert AH.find_shared_contacts([("jane", Path("/n")), ("alex", Path("/n"))]) == 1


def test_the_empty_json_document_carries_the_errors_main_collected(monkeypatch, capsys):
    """J3 at the call site. The direct-call test passed its own errors in and
    never exercised what `main` hands over."""
    monkeypatch.setattr(AGG, "scan_all_contacts",
                        lambda *a, **k: ([], ["exec jane: repo unreachable"]))
    monkeypatch.setattr(AGG, "load_admin_config", dict)
    monkeypatch.setattr(AGG, "parse_config", lambda p: AGG.DEFAULT_CADENCE.copy())
    monkeypatch.setattr(sys, "argv", ["aggregate-crm.py", "--json"])
    with pytest.raises(SystemExit):
        AGG.main()
    doc = json.loads(capsys.readouterr().out)
    assert doc["errors"] == ["exec jane: repo unreachable"], \
        "an empty run reported success and dropped the reasons it was empty"


def test_the_sendable_set_is_derived_in_the_source(): 
    """R2. `frozenset(ACTIVE_STATUSES) - {"approved"}` and the hand-typed
    `frozenset({"pending", "send_failed"})` are EQUAL today, so no value
    comparison can tell them apart. The point of deriving it is what happens
    when a new active status is added, which is a property of the expression,
    not of today's value -- so the expression is what gets asserted.
    """
    import ast
    source = (ROOT / "scripts" / "action-queue.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assigned = [n for n in ast.walk(tree)
                if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == "SENDABLE_STATUSES" for t in n.targets)]
    assert len(assigned) == 1, "SENDABLE_STATUSES is assigned more than once"
    expr = ast.dump(assigned[0].value)
    assert "ACTIVE_STATUSES" in expr, \
        "SENDABLE_STATUSES no longer derives from ACTIVE_STATUSES"
    assert "'approved'" in expr, "the exclusion of `approved` is no longer explicit"


def test_a_new_active_status_would_become_sendable():
    """States the consequence the derivation exists for, in one assertion."""
    assert set(AQ.ACTIVE_STATUSES) - {"approved", AQ.SENDING} == set(AQ.SENDABLE_STATUSES)
    assert "approved" in AQ.ACTIVE_STATUSES, "the exclusion has nothing to exclude"
    assert AQ.SENDING in AQ.ACTIVE_STATUSES, \
        "a claimed card must stay ACTIVE, or no lister and no dedup can see it"
