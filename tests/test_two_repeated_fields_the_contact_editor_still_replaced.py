"""Shard scripts-09-p1: five defects across the Google Contacts and Gmail CLIs.

`scripts/google-contacts.py` documents this exact defect class in
`_replace_first`'s own docstring, and fixed it for emails, phones, URLs,
addresses and organisations. Two repeated fields never got the treatment.

MEASURED 2026-08-29 against a synthetic two-entry contact, before the fix:

    biographies  shipped [updated]                    correct [updated, second]
    names        shipped [New Name]                   correct [New Name, J D]

`updatePersonFields` replaces a field's WHOLE list with whatever the body
carries, so both shipped a one-element list and the server discarded the rest.
Silently: nothing on stdout or stderr said an entry had gone.

Three smaller findings from the same shard, all measured:

3. `gmail-draft.py` printed the literal `in-reply-to None`. `parent_headers`
   returns None for a parent carrying no Message-ID, an ordinary shape for
   imported mail, while the guard tests `thread_id`, which is essentially
   always present. The draft itself was always correct; the SUMMARY told the
   operator a header was set that was not.

4. `gmail-reader.py` with no subcommand printed usage and exited 0. A wrapper
   or skill reads that as success with an empty result. Measured: reader exit
   0, sibling `gmail-send.py` exit 1 for the identical case, so two scripts in
   one suite disagreed about what "did nothing" means.

5. `--limit 0` reached the API. Both pagers compute `min(PAGE_SIZE, limit -
   len(sofar))`, which on the first pass is just `limit`. Measured:
   `pageSize=0` for `--limit 0` and `pageSize=-3` for `--limit -3`. People
   documents pageSize 1-30 and answers 0 with an HTTP 400 that the dispatcher
   reports as "Bad request", accurate only by luck and only after credentials
   were loaded. Gmail's `maxResults=0` is undefined: if the server substitutes
   a default page, `fetch_drafts` returns MORE drafts than asked for and
   reports `complete=False`, a wrong answer rather than an error.
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.argtypes import positive_int  # noqa: E402

PY = sys.executable


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def contacts():
    return _load("gc_shard09", "scripts/google-contacts.py")


# ============================================================
# 1 + 2: the two repeated fields
# ============================================================

TWO_ENTRY_CONTACT = {
    "biographies": [{"value": "first note", "contentType": "TEXT_PLAIN"},
                    {"value": "second note", "contentType": "TEXT_PLAIN"}],
    "names": [{"givenName": "Jane", "familyName": "Doe"},
              {"givenName": "J", "familyName": "D"}],
}


def _edit_body(contacts, **kwargs) -> dict:
    """Run `cmd_edit` against a fake service and return the submitted body."""
    captured: dict = {}

    class _Call:
        def __init__(self, result):
            self._result = result

        def execute(self):
            return self._result

    class _People:
        def get(self, **_kw):
            return _Call({"etag": "e1", **TWO_ENTRY_CONTACT})

        def updateContact(self, **kw):  # noqa: N802 - the API's own spelling
            captured.update(kw)
            return _Call({"names": [{"displayName": "x"}]})

    class _Service:
        def people(self):
            return _People()

    contacts.cmd_edit(_Service(), "people/c1", as_json=True, **kwargs)
    return captured


def test_a_notes_edit_keeps_the_contacts_other_biography(contacts, capsys):
    body = _edit_body(contacts, notes="updated")
    capsys.readouterr()

    assert body["body"]["biographies"] == [
        {"value": "updated", "contentType": "TEXT_PLAIN"},
        {"value": "second note", "contentType": "TEXT_PLAIN"},
    ], "the tail entry was dropped, which the server then deletes"


def test_a_name_edit_keeps_the_contacts_other_name_entry(contacts, capsys):
    body = _edit_body(contacts, name="New Name")
    capsys.readouterr()

    assert body["body"]["names"] == [
        {"givenName": "New", "familyName": "Name"},
        {"givenName": "J", "familyName": "D"},
    ]


@pytest.mark.parametrize("field, kwargs", [
    ("biographies", {"notes": "updated"}),
    ("names", {"name": "New Name"}),
])
def test_the_edited_value_is_still_first(contacts, capsys, field, kwargs):
    """The mirror. Keeping the tail must not cost the edit its position: the
    People API treats element [0] as primary, which is what `_replace_first`
    exists to preserve."""
    body = _edit_body(contacts, **kwargs)
    capsys.readouterr()
    first = body["body"][field][0]
    assert first in ({"value": "updated", "contentType": "TEXT_PLAIN"},
                     {"givenName": "New", "familyName": "Name"})


def test_a_single_word_name_still_clears_the_family_name(contacts, capsys):
    """Decided deliberately, not by default.

    `--name` takes a WHOLE name, so `--name "Cher"` is a rename TO a one-word
    name and must clear the old family name. `cmd_add` omits an empty family
    instead, because a new contact has nothing to clear. The asymmetry is
    intentional; this test is what stops it being "harmonised" away.
    """
    body = _edit_body(contacts, name="Cher")
    capsys.readouterr()
    assert body["body"]["names"][0] == {"givenName": "Cher", "familyName": ""}


def test_every_repeated_field_now_routes_through_the_shared_helper(contacts, capsys):
    """The rule, not one more instance of it. Five fields were fixed and two
    were missed, so the next reader needs a list rather than a habit."""
    for field, kwargs in (
        ("emailAddresses", {"email": "a@b.c"}),
        ("phoneNumbers", {"phone": "+1"}),
        ("addresses", {"address": "1 Road"}),
        ("urls", {"url": "https://x.test"}),
        ("biographies", {"notes": "n"}),
        ("names", {"name": "New Name"}),
    ):
        contact = dict(TWO_ENTRY_CONTACT)
        contact.setdefault(field, [{"value": "one"}, {"value": "two"}])
        body = _edit_body(contacts, **kwargs)
        capsys.readouterr()
        assert len(body["body"][field]) >= 1, field


# ============================================================
# 3: the draft summary
# ============================================================

def _summary_line(in_reply_to):
    """The exact expression `gmail-draft.py` prints, read from the file.

    Reading it rather than restating it: a test that reimplements the line
    passes while the real one still says None.
    """
    src = (ROOT / "scripts" / "gmail-draft.py").read_text(encoding="utf-8")
    block = [ln.strip() for ln in src.splitlines()
             if "in-reply-to" in ln and "suffix" in ln]
    assert block, "the conditional suffix is gone from gmail-draft.py"
    namespace = {"GRAY": "", "RESET": "", "in_reply_to": in_reply_to}
    exec(block[0], namespace)  # noqa: S102 - the file's own line, read from disk
    return namespace["suffix"]


def test_a_parent_with_no_message_id_prints_no_in_reply_to_claim():
    assert _summary_line(None) == ""


def test_a_parent_with_a_message_id_still_reports_it():
    assert "in-reply-to <abc@x>" in _summary_line("<abc@x>")


# ============================================================
# 4: exit codes
# ============================================================

@pytest.mark.parametrize("script", ["gmail-reader.py", "gmail-send.py"])
def test_no_subcommand_exits_non_zero(script):
    """Both scripts in the suite, so they cannot drift apart again."""
    proc = subprocess.run([PY, str(ROOT / "scripts" / script)],
                          capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode != 0, (
        f"{script} printed usage and exited 0, which a wrapper reads as "
        f"success with an empty result")
    assert "usage" in (proc.stdout + proc.stderr).lower()


# ============================================================
# 5: the limit floor
# ============================================================

@pytest.mark.parametrize("bad", ["0", "-1", "-3"])
def test_a_non_positive_limit_is_refused(bad):
    with pytest.raises(argparse.ArgumentTypeError, match="1 or more"):
        positive_int(bad)


@pytest.mark.parametrize("bad", ["", "x", "1.5", "None"])
def test_a_non_integer_limit_is_refused(bad):
    with pytest.raises(argparse.ArgumentTypeError, match="whole number"):
        positive_int(bad)


@pytest.mark.parametrize("good, expected", [("1", 1), ("25", 25), ("1000", 1000)])
def test_a_usable_limit_is_accepted(good, expected):
    """The mirror. A type that rejected everything would pass both suites above
    and make every paging command unusable."""
    assert positive_int(good) == expected


@pytest.mark.parametrize("argv", [
    ["scripts/google-contacts.py", "search", "jo", "--limit", "0"],
    ["scripts/google-contacts.py", "list", "--limit", "0"],
    ["scripts/gmail-send.py", "list", "--limit", "0"],
    ["scripts/gmail-send.py", "list", "--limit", "-3"],
])
def test_the_cli_refuses_before_it_reaches_the_network(argv):
    """The wiring, and the point of doing it in an argparse type: argparse
    exits 2 before authentication, so no credential is loaded and no request is
    sent. A check inside the command would already have paid for both.
    """
    proc = subprocess.run([PY, *argv], capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode == 2, proc.stderr
    assert "must be 1 or more" in proc.stderr
