"""Editing one phone number must not delete the other two.

`updatePersonFields` replaces a field's whole list with whatever the request
body carries. `cmd_edit` sent `body["phoneNumbers"] = [{"value": phone}]`, so
`edit people/c123 --phone "+1-555-0100"` on a contact with three numbers kept
one and destroyed two. Same for emails, addresses, URLs and organizations.
Permanent user-data loss through a routine CLI call.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "google_contacts", ROOT / "scripts" / "google-contacts.py")
gc = importlib.util.module_from_spec(_spec)
sys.modules["google_contacts"] = gc
_spec.loader.exec_module(gc)


def test_the_other_values_survive():
    current = {"phoneNumbers": [
        {"value": "+1-555-0001", "type": "work"},
        {"value": "+1-555-0002", "type": "mobile"},
        {"value": "+1-555-0003", "type": "home"},
    ]}
    out = gc._replace_first(current, "phoneNumbers", {"value": "+1-555-9999"})
    values = [e["value"] for e in out]
    assert values[0] == "+1-555-9999", "the edited value must be primary"
    assert "+1-555-0002" in values and "+1-555-0003" in values, \
        "editing one number deleted the others"
    assert "+1-555-0001" not in values, "the replaced primary must not linger"


def test_editing_to_a_value_the_contact_already_has_does_not_duplicate_it():
    current = {"emailAddresses": [
        {"value": "a@example.test"}, {"value": "b@example.test"},
    ]}
    out = gc._replace_first(current, "emailAddresses", {"value": "b@example.test"})
    assert [e["value"] for e in out] == ["b@example.test"]


def test_a_contact_with_no_such_field_gets_just_the_new_value():
    assert gc._replace_first({}, "urls", {"value": "https://x.test"}) == \
        [{"value": "https://x.test"}]


def test_a_malformed_entry_is_dropped_not_crashed_on():
    current = {"urls": [{"value": "https://a.test"}, "not-a-dict", {"value": "https://b.test"}]}
    out = gc._replace_first(current, "urls", {"value": "https://new.test"})
    assert all(isinstance(e, dict) for e in out)
    assert [e["value"] for e in out] == ["https://new.test", "https://b.test"]
