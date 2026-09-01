"""`EmailSource` read its two hand-edited lists the way the calendar one did.

`sentinel_config.yaml` is edited by hand. A dash left with nothing after it makes
a None ENTRY, a key written with nothing after it parses as None rather than as
an ABSENT key, and `config.get(key, [])` fires its default only on absence - so
both shapes reached `.lower()`.

`scripts/sentinel.py` fixed this on 2026-09-01 for the calendar policy engine's
`tribe_domains`, `vip_senders` and `external_domains`, and for its numeric and
mapping keys. The email section of the same file has its OWN `vip_senders` and
its own `ignore_patterns`, read by `EmailSource._is_vip` and
`EmailSource._is_ignored`, and those two were left on the old footing. That is
the campaign's dominant pattern arriving one more time: a fix that landed in
some of the copies.

MEASURED 2026-09-01 against the shipped code, one malformed key at a time:

    ignore_patterns: [null]   AttributeError: 'NoneType' object has no 'lower'
    ignore_patterns:          TypeError: 'NoneType' object is not iterable
    vip_senders: [null]       AttributeError: 'NoneType' object has no 'lower'
    vip_senders:              TypeError: 'NoneType' object is not iterable

Both methods are called at try-depth 0 inside `check_new`'s `for email_item in
items` loop, so any of those ends the whole email cycle rather than one message.
Nothing marks the messages processed, so the next cycle reaches the same inbox
and does the same thing, and the operator sees one logged line.

The scalar shape is the quieter half. `vip_senders: partner@example.com` written
without a leading dash did not raise: `for vip in "partner@example.com"`
iterates CHARACTERS, so no entry ever matched and a configured VIP silently lost
its priority floor.
"""
import importlib
import logging

import pytest

sn = importlib.import_module("scripts.sentinel")


def _source(config):
    src = sn.EmailSource.__new__(sn.EmailSource)
    src.config = config
    src.logger = logging.getLogger("test-email-source")
    return src


MALFORMED = [
    ("null_entry", [None, "noreply@*"]),
    ("mapping_entry", [{"noreply@*": None}, "noreply@*"]),
    ("blank_entry", ["   ", "noreply@*"]),
    ("scalar_not_a_list", "noreply@*"),
]


@pytest.mark.parametrize("name,value", MALFORMED, ids=[c[0] for c in MALFORMED])
def test_a_malformed_ignore_pattern_does_not_end_the_email_cycle(name, value):
    src = _source({"ignore_patterns": value})
    assert src._is_ignored("noreply@vendor.example") is True
    assert src._is_ignored("a-real-person@example.org") is False


@pytest.mark.parametrize("name,value", [("null_entry", [None, "vip@example.org"]),
                                        ("mapping_entry", [{"a": 1}, "vip@example.org"]),
                                        ("blank_entry", ["", "vip@example.org"]),
                                        ("scalar_not_a_list", "vip@example.org")],
                         ids=["null_entry", "mapping_entry", "blank_entry",
                              "scalar_not_a_list"])
def test_a_malformed_vip_entry_does_not_end_the_email_cycle(name, value):
    src = _source({"vip_senders": value})
    assert src._is_vip("VIP@example.org") is True
    assert src._is_vip("stranger@example.org") is False


@pytest.mark.parametrize("key,method", [("ignore_patterns", "_is_ignored"),
                                        ("vip_senders", "_is_vip")])
def test_a_blank_key_is_an_empty_list_not_a_crash(key, method):
    """`ignore_patterns:` with nothing after it parses as None, not as absent."""
    src = _source({key: None})
    assert getattr(src, method)("anyone@example.org") is False


@pytest.mark.parametrize("method", ["_is_ignored", "_is_vip"])
def test_a_null_sender_address_does_not_raise(method):
    """`str(item.sender.email_address or "")` is the producer today, so this is
    the guard for the next producer rather than for a shape seen in the wild.
    It is one `or ""` and it removes a whole class of cycle-ending raise."""
    src = _source({"ignore_patterns": ["noreply@*"], "vip_senders": ["v@e.test"]})
    assert getattr(src, method)(None) is False


def test_a_malformed_entry_says_which_key_it_dropped(caplog):
    src = _source({"vip_senders": [None, "vip@example.org"]})
    with caplog.at_level(logging.WARNING, logger="test-email-source"):
        src._is_vip("vip@example.org")
    assert any("vip_senders" in r.message for r in caplog.records), (
        [r.message for r in caplog.records])


def test_the_hardening_did_not_neuter_a_well_formed_config():
    """The anti-vacuity jaw. A reader that dropped every entry would satisfy
    every case above, and would also silently disable the ignore list - which
    is what stops newsletters reaching a paid LLM call - and the VIP floor."""
    src = _source({"ignore_patterns": ["*@expensify.com", "noreply@*",
                                       "*newsletter*"],
                   "vip_senders": ["key-partner@example.com",
                                   "investor@example.com"]})

    assert src._is_ignored("billing@expensify.com") is True
    assert src._is_ignored("noreply@vendor.example") is True
    assert src._is_ignored("weekly-newsletter@press.example") is True
    assert src._is_ignored("real.person@example.org") is False

    assert src._is_vip("Key-Partner@example.com") is True
    assert src._is_vip("investor@example.com") is True
    assert src._is_vip("investor@example.com.attacker.test") is False
    assert src._is_vip("someone@example.com") is False
