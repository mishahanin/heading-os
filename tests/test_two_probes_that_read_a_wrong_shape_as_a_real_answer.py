"""A probe that crashes on a malformed body, and a harvest that drops a string id.

Two modules, one shape of defect: code that asks whether a value is the type it
hoped for, and then treats the answer as if the question had been about the
value's meaning.

`council_freshness.probe_proxy` promises "None on any failure" because the whole
downstream chain (`classify_proxy_model` -> `nudge_line`) is built on a probe
failure reading as `unknown` rather than as an assertion about a pin. It read
`body.get("data", [])`, and `dict.get` substitutes its default only when the key
is ABSENT - so `{"data": null}` iterated `None` and raised, and `{"data":
"abc"}` iterated a string's characters and produced an empty catalog, turning
one probe failure into three `broken` pins.

`content_denylist._harvest_fireside_roster` gates the Telegram id on
`isinstance(uid, int)`, so a roster storing the id as a JSON string produced no
token, raised nothing and left `degraded` False: the content gate reported the
tree clean over an id it had never held. Its sibling `_harvest_config` finds ids
with `_ID_RE` over raw text and catches both forms, so the asymmetry was already
in the file.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.utils.council_freshness as council_freshness
from scripts.utils.content_denylist import build_denylist


# --------------------------------------------------------------------------
# council_freshness.probe_proxy
# --------------------------------------------------------------------------

@pytest.fixture
def proxy(monkeypatch):
    """Patch the key load and the HTTP call; return a setter for the body."""
    monkeypatch.setattr(council_freshness, "load_api_key", lambda *a, **k: "test-key")

    def serve(body):
        monkeypatch.setattr(council_freshness, "_http_json", lambda *a, **k: body)

    return serve


@pytest.mark.parametrize(
    "body",
    [
        {"data": None},
        {"data": "abc"},
        {"data": {"id": "gemini-3-flash"}},
        {"data": 7},
        [],
        ["gemini-3-flash"],
        "not json at all",
        None,
    ],
    ids=[
        "data-null", "data-string", "data-object", "data-int",
        "body-empty-list", "body-list", "body-string", "body-none",
    ],
)
def test_a_body_of_the_wrong_shape_reads_as_a_failed_probe(proxy, body):
    """None, never an exception and never a catalog invented out of the shape."""
    proxy(body)
    assert council_freshness.probe_proxy() is None


def test_a_well_formed_catalog_still_parses(proxy):
    """The guard must not swallow the answer it exists to protect."""
    proxy({"data": [
        {"id": "gemini-3-flash"},
        {"id": ""},
        {"no": "id"},
        "a bare string",
        {"id": "grok-4.5"},
    ]})
    assert council_freshness.probe_proxy() == ["gemini-3-flash", "grok-4.5"]


def test_an_empty_catalog_is_not_a_probe_failure(proxy):
    """`[]` is a real answer from a proxy with nothing loaded, not a crash."""
    proxy({"data": []})
    assert council_freshness.probe_proxy() == []


def test_a_malformed_body_reaches_the_operator_as_not_checked(proxy):
    """The end the contract is actually about.

    `assess()` used to raise `TypeError` straight into the daily
    `council-models-notify` unit. It must instead produce `unknown` findings,
    which `nudge_line` renders as a line saying the check did not happen -
    never as `broken`, which asserts something about a pin that nothing
    established.
    """
    proxy({"data": None})
    findings = council_freshness.assess()

    assert findings, "assess returned no findings at all"
    assert {f["status"] for f in findings} == {"unknown"}
    assert council_freshness.nudge_line(findings).startswith("Council pins NOT checked")


# --------------------------------------------------------------------------
# content_denylist._harvest_fireside_roster
# --------------------------------------------------------------------------

def _overlay(tmp_path: Path, roster: dict) -> Path:
    directory = (
        tmp_path / "datastore" / "operations" / "tribe" / "fireside-state"
    )
    directory.mkdir(parents=True)
    (directory / "tribe-roster.json").write_text(
        json.dumps(roster), encoding="utf-8"
    )
    return tmp_path


def test_a_telegram_id_stored_as_a_string_still_becomes_a_token(tmp_path):
    """The defect: a quoted id vanished with no error and no `degraded`."""
    denylist = build_denylist(_overlay(tmp_path, {
        "bondhandle": {"name": "James Bond", "telegram_user_id": "123456789"},
    }))

    assert denylist.degraded is False
    assert denylist.tokens.get("123456789") == "telegram-id"
    assert denylist.scan_text("reached out on tg 123456789") != []


def test_both_id_shapes_are_caught_in_one_roster(tmp_path):
    """The two forms coexist in a hand-edited roster; both must be held."""
    denylist = build_denylist(_overlay(tmp_path, {
        "bondhandle": {"name": "James Bond", "telegram_user_id": "123456789"},
        "leiterhandle": {"name": "Felix Leiter", "telegram_user_id": 987654321},
        "spacedhandle": {"name": "Vesper Lynd", "telegram_user_id": "  555444333  "},
    }))

    for uid in ("123456789", "987654321", "555444333"):
        assert denylist.tokens.get(uid) == "telegram-id", uid
        assert denylist.scan_text(f"id {uid}") != []


@pytest.mark.parametrize(
    "uid",
    ["1990", "", "not-a-number", "12.5", "-123456789", None, True, False, [], {}],
    ids=[
        "too-short", "empty", "letters", "decimal", "negative",
        "none", "true", "false", "list", "dict",
    ],
)
def test_a_value_that_is_not_a_telegram_id_does_not_become_a_token(tmp_path, uid):
    """The widening must not turn every short number into a denylist token.

    `1990` is the case that matters: a 4-digit field admitted as an id would
    match every year and street number in the corpus and wedge the gate. The
    floor is `_ID_RE`'s seven digits, the same one `_harvest_config` uses.
    `True` is here because `isinstance(True, int)` is True in Python.
    """
    denylist = build_denylist(_overlay(tmp_path, {
        "bondhandle": {"name": "James Bond", "telegram_user_id": uid},
    }))

    assert denylist.degraded is False
    assert "telegram-id" not in denylist.tokens.values()
    assert denylist.scan_text("in 1990 he was at 12.5 degrees") == []


def test_the_handle_and_name_harvest_still_runs_beside_the_id(tmp_path):
    """The id branch sits at the end of the loop; it must not shadow the rest."""
    denylist = build_denylist(_overlay(tmp_path, {
        "bondhandle": {"name": "James Bond", "telegram_user_id": "123456789"},
    }))

    assert denylist.tokens.get("bondhandle") == "handle"
    assert denylist.tokens.get("james bond") == "handle-name"
