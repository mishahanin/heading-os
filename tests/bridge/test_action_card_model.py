"""F-M6: POST /aq/deposit must validate per-card fields and return 422 on bad input."""
import pytest


def test_action_card_model_requires_title():
    from scripts.bridge_daemon.app import ActionCardModel
    with pytest.raises(Exception):  # pydantic ValidationError
        ActionCardModel(kind="note")


def test_action_card_model_title_empty_rejected():
    from scripts.bridge_daemon.app import ActionCardModel
    with pytest.raises(Exception):
        ActionCardModel(kind="note", title="")


def test_action_card_model_kind_explicit_empty_rejected():
    from scripts.bridge_daemon.app import ActionCardModel
    with pytest.raises(Exception):
        ActionCardModel(kind="", title="t")


def test_action_card_model_kind_max_length():
    from scripts.bridge_daemon.app import ActionCardModel
    with pytest.raises(Exception):
        ActionCardModel(kind="x" * 65, title="t")


def test_action_card_model_title_max_length():
    from scripts.bridge_daemon.app import ActionCardModel
    with pytest.raises(Exception):
        ActionCardModel(kind="note", title="x" * 257)


def test_action_card_model_body_max_length():
    from scripts.bridge_daemon.app import ActionCardModel
    with pytest.raises(Exception):
        ActionCardModel(kind="note", title="t", body="x" * 4097)


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
