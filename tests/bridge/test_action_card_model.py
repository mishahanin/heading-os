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
