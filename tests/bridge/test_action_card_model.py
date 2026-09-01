"""F-M6: POST /aq/deposit must validate per-card fields and return 422 on bad input.

The six rejection tests below used to be `pytest.raises(Exception)` with no
`match` and no boundary case, which measured almost nothing. Two holes, both
found 2026-08-26:

* `Exception` accepts any failure at all. A required field added to the model, a
  renamed attribute, an unrelated TypeError - each keeps every one of the six
  green while the rule the test names goes untested. They now pin
  `ValidationError` and assert WHICH field pydantic rejected and why.

* Only the over-limit side was tested. `title="x" * 257` raises whether the
  limit is 256 or 1, so the six could not tell a correct limit from a limit
  that breaks every real card. Each limit is now checked from both sides: the
  exact maximum is accepted, one character more is refused.

`action_type` carries `min_length=1, max_length=64` and had no test of either.
"""
import pytest

pytest.importorskip("fastapi")  # F-7.1: bridge_daemon.app needs the dashboard extra
ValidationError = pytest.importorskip("pydantic").ValidationError


def _refused(**kwargs) -> list:
    """The (field, error type) pairs pydantic reported, or fail the test."""
    from scripts.bridge_daemon.app import ActionCardModel
    with pytest.raises(ValidationError) as exc:
        ActionCardModel(**kwargs)
    return [(e["loc"], e["type"]) for e in exc.value.errors()]


def test_action_card_model_requires_title():
    assert _refused(kind="note") == [(("title",), "missing")]


def test_action_card_model_title_empty_rejected():
    assert _refused(kind="note", title="") == [(("title",), "string_too_short")]


def test_action_card_model_kind_explicit_empty_rejected():
    """Raised by the model validator, so it lands on the MODEL, not on `kind`.

    Worth pinning: a reader of the 422 payload sees an empty `loc`, and a caller
    matching on the field name finds nothing to match.
    """
    assert _refused(kind="", title="t") == [((), "value_error")]


def test_action_card_model_kind_max_length():
    assert _refused(kind="x" * 65, title="t") == [(("kind",), "string_too_long")]


def test_action_card_model_title_max_length():
    assert _refused(kind="note", title="x" * 257) == [(("title",), "string_too_long")]


def test_action_card_model_body_max_length():
    assert _refused(kind="note", title="t", body="x" * 4097) == [
        (("body",), "string_too_long")]


def test_action_card_model_action_type_empty_rejected():
    assert _refused(title="t", action_type="") == [
        (("action_type",), "string_too_short")]


def test_action_card_model_action_type_max_length():
    assert _refused(title="t", action_type="x" * 65) == [
        (("action_type",), "string_too_long")]


@pytest.mark.parametrize("field,limit", [
    ("kind", 64), ("title", 256), ("body", 4096), ("action_type", 64),
])
def test_each_limit_accepts_its_exact_maximum(field, limit):
    """The half the over-limit tests cannot see: a limit of 1 refuses nothing
    they check, and breaks every real card."""
    from scripts.bridge_daemon.app import ActionCardModel
    kwargs = {"title": "t"}
    kwargs[field] = "x" * limit          # overwrites `title` when that is the field

    card = ActionCardModel(**kwargs)

    assert len(getattr(card, field)) == limit


def test_action_card_model_valid_with_kind():
    from scripts.bridge_daemon.app import ActionCardModel
    card = ActionCardModel(kind="email_send", title="hello", body="world")
    assert card.kind == "email_send"
    assert card.title == "hello"
    assert card.action_type == "note"  # default


def test_action_card_model_kind_defaults_to_action_type():
    """Omitting kind is backward-compat: kind is derived from action_type."""
    from scripts.bridge_daemon.app import ActionCardModel
    card = ActionCardModel(title="hello", action_type="email_send")
    assert card.kind == "email_send"


def test_action_card_model_kind_omitted_uses_default_action_type():
    from scripts.bridge_daemon.app import ActionCardModel
    card = ActionCardModel(title="hello")
    assert card.kind == "note"  # action_type default is "note"


def test_action_card_model_extra_fields_allowed():
    from scripts.bridge_daemon.app import ActionCardModel
    card = ActionCardModel(kind="email_send", title="t", body="b", recipient="alice@example.com")
    assert card.recipient == "alice@example.com"


# ============================================================
# The route, not only the model
# ============================================================
#
# Everything above builds `ActionCardModel` by hand. Nothing asked whether the
# endpoint named in this module's first line still uses it. Measured 2026-08-31:
# replacing the request body's `cards: list[ActionCardModel]` with
# `cards: list[dict]` (and dropping the `model_dump()` that goes with it) left
# `tests/bridge` at 1312 passed, 1 skipped, identical to the baseline. Every
# rejection test above stays green over a route that validates nothing, because
# each one instantiates the class itself.
#
# The route is `POST /action-queue/deposit`. This file and the model's own
# docstring both said `/aq/deposit`, which no version of `app.py` serves, so a
# reader following either would have tested a 404.

def _client(workspace_root, token="t1"):  # noqa: S107  test fixture default, not a secret
    from fastapi.testclient import TestClient

    from scripts.bridge_daemon.app import build_app
    from scripts.bridge_daemon.state import State
    app = build_app(workspace_root=workspace_root, state=State(), token=token,
                    user_slug="misha", data_root=workspace_root)
    return TestClient(app, base_url="http://127.0.0.1")


_AUTH = {"Authorization": "Bearer t1"}


def test_the_deposit_route_refuses_a_card_the_model_refuses(workspace_root):
    """A card with no title must not reach the queue, and the 422 must name it."""
    r = _client(workspace_root).post(
        "/action-queue/deposit", headers=_AUTH,
        json={"cards": [{"kind": "note", "action_type": "note"}]})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert any(e["loc"][-1] == "title" and e["type"] == "missing" for e in detail), detail
    assert not (workspace_root / "outputs" / "operations" / "action-queue"
                / "queue.json").exists(), "a refused deposit still wrote the queue"


@pytest.mark.parametrize("card,field", [
    ({"title": "", "action_type": "note"}, "title"),
    ({"title": "x" * 257, "action_type": "note"}, "title"),
    ({"title": "t", "body": "x" * 4097, "action_type": "note"}, "body"),
    ({"title": "t", "action_type": ""}, "action_type"),
    # `kind=""` is raised by the MODEL validator, not by a field constraint, so
    # its `loc` stops at the card's position in the list and names no field.
    # `test_action_card_model_kind_explicit_empty_rejected` pins the same
    # asymmetry one level down, where the empty `loc` is visible directly.
    ({"title": "t", "kind": "", "action_type": "note"}, 0),
])
def test_every_limit_is_enforced_at_the_route_too(workspace_root, card, field):
    """The per-field limits, asked of the endpoint rather than the class."""
    r = _client(workspace_root).post("/action-queue/deposit", headers=_AUTH,
                                     json={"cards": [card]})
    assert r.status_code == 422, r.text
    locs = [e["loc"][-1] if e["loc"] else None for e in r.json()["detail"]]
    assert field in locs, locs


def test_the_route_applies_the_model_before_the_queue_sees_the_card(workspace_root):
    """`kind` is derived by the model, so its value on disk proves who ran.

    A route typed `list[dict]` passes the caller's JSON through untouched, and
    the stored card then has no `kind` at all. Asserting the DERIVED field is
    what distinguishes the two; asserting the title would not.
    """
    import json as _json

    r = _client(workspace_root).post(
        "/action-queue/deposit", headers=_AUTH,
        json={"cards": [{"title": "Reply to Q", "action_type": "email_send",
                         "recipient": "q@example.com"}]})
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 1, r.json()

    queue = _json.loads((workspace_root / "outputs" / "operations" / "action-queue"
                         / "queue.json").read_text(encoding="utf-8"))
    stored = queue["actions"][0]
    assert stored["kind"] == "email_send", (
        "the deposited card carries no model-derived `kind`, so the route did "
        "not validate through ActionCardModel")
    assert stored["title"] == "Reply to Q"
    # extra="allow": an action-specific field the model does not declare still
    # reaches the queue.
    assert stored["recipient"] == "q@example.com"
