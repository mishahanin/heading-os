"""Shard 08-p1: the Gmail reader, the Gmail sender, and the People API walks.

Nine findings, all against remote parties that this workspace does not control:
a mail server that wraps its body one way rather than another, and a paging API
that hands back the page it just handed back. Every test below drives the real
function with a stub that behaves the way the finding says a real server can.

The three that lose data quietly:

* a single-part `text/html` message returned its raw markup from a function
  documented to extract plain text, because the MIME type was read on the
  nested path and ignored on the top-level one;
* a `multipart/mixed` holding an html branch before a plain branch returned
  the html, because the search recursed branch-by-branch and the first branch
  to answer won -- under a docstring promising plain text wins at every depth;
* a repeated page token was refused one page too late, so the duplicate page
  was already counted, already fetched, and already printed.
"""

import base64
import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load(filename, modname):
    path = PROJECT_ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


reader = _load("gmail-reader.py", "gmail_reader_cli_08p1")
sender = _load("gmail-send.py", "gmail_send_cli_08p1")
contacts = _load("google-contacts.py", "google_contacts_cli_08p1")


def b64(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode()


def part(mime, text=None, sub=None):
    """One Gmail payload part. `text` and `sub` are mutually exclusive."""
    out = {"mimeType": mime}
    if text is not None:
        out["body"] = {"data": b64(text)}
    if sub is not None:
        out["parts"] = sub
    return out


# ---------------------------------------------------------------------------
# Finding 1 -- a single-part text/html body was returned as raw markup
# ---------------------------------------------------------------------------

def test_a_top_level_html_body_is_stripped_to_text():
    body = reader.decode_body(part("text/html", "<p>Hello <b>world</b></p>"))
    assert body == "Hello world"
    assert "<" not in body


def test_a_top_level_html_body_is_stripped_the_same_way_a_nested_one_is():
    """The defect appeared and disappeared with the sender's choice of wrapper."""
    html_text = "<p>Hello <b>world</b></p>"
    top = reader.decode_body(part("text/html", html_text))
    nested = reader.decode_body(
        part("multipart/alternative", sub=[part("text/html", html_text)]))
    assert top == nested


def test_a_top_level_plain_body_is_returned_verbatim():
    assert reader.decode_body(part("text/plain", "line one\nline two")) == \
        "line one\nline two"


def test_a_body_with_no_mime_type_is_still_treated_as_text():
    """Every message behaved this way before the type was consulted."""
    assert reader.decode_body({"body": {"data": b64("bare")}}) == "bare"


def test_a_single_part_binary_body_reports_no_text_body():
    """Not replacement characters presented as a message."""
    payload = {"mimeType": "application/pdf", "body": {"data": b64("%PDF-1.7 \x00\x01")}}
    assert reader.decode_body(payload) == reader.NO_TEXT_BODY


def test_the_mime_type_is_read_without_its_parameters():
    payload = {"mimeType": "text/html; charset=UTF-8",
               "body": {"data": b64("<i>x</i>")}}
    assert reader.decode_body(payload) == "x"


def test_an_empty_payload_reports_no_text_body():
    assert reader.decode_body({}) == reader.NO_TEXT_BODY


def test_html_entities_are_resolved_not_left_as_markup():
    """`&amp;` in a body is an ampersand the reader should print as one."""
    body = reader.decode_body(part("text/html", "<p>Ben &amp; Jerry &lt;3</p>"))
    assert body == "Ben & Jerry <3"


# ---------------------------------------------------------------------------
# Finding 2 -- plain text lost to an html branch that came first
# ---------------------------------------------------------------------------

def test_plain_text_in_a_later_branch_beats_html_in_an_earlier_one():
    parts = [
        part("multipart/related", sub=[part("text/html", "<b>wrong body</b>")]),
        part("multipart/alternative", sub=[part("text/plain", "right body")]),
    ]
    assert reader._decode_parts(parts) == "right body"


def test_plain_text_wins_from_three_levels_down_against_a_shallow_html():
    parts = [
        part("text/html", "<b>wrong</b>"),
        part("multipart/mixed", sub=[
            part("multipart/related", sub=[
                part("multipart/alternative", sub=[part("text/plain", "right")]),
            ]),
        ]),
    ]
    assert reader._decode_parts(parts) == "right"


def test_html_is_used_only_when_no_plain_text_exists_anywhere():
    parts = [
        part("multipart/related", sub=[part("text/html", "<b>only body</b>")]),
        part("multipart/mixed", sub=[part("application/pdf", "binary")]),
    ]
    assert reader._decode_parts(parts) == "only body"


def test_a_tree_with_neither_plain_nor_html_returns_the_empty_string():
    """"" is what lets `decode_body` say NO_TEXT_BODY instead of guessing."""
    parts = [part("multipart/mixed", sub=[part("image/png", "PNG")])]
    assert reader._decode_parts(parts) == ""


def test_the_first_plain_part_in_document_order_wins():
    parts = [
        part("multipart/alternative", sub=[part("text/plain", "first")]),
        part("multipart/alternative", sub=[part("text/plain", "second")]),
    ]
    assert reader._decode_parts(parts) == "first"


def test_a_part_that_only_has_sub_parts_no_longer_ends_the_search():
    """The older regression: a branch that answered "" used to end the loop."""
    parts = [
        part("multipart/related", sub=[part("image/png", "PNG")]),
        part("text/plain", "found me"),
    ]
    assert reader._decode_parts(parts) == "found me"


# ---------------------------------------------------------------------------
# Finding 3 -- a repeated page token counted its page twice
# ---------------------------------------------------------------------------

class _StubMessages:
    def __init__(self, pages):
        self._pages = list(pages)
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        page = self._pages[min(len(self.calls) - 1, len(self._pages) - 1)]
        return _Exec(page)


class _Exec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _StubUsers:
    def __init__(self, messages=None, drafts=None):
        self._messages = messages
        self._drafts = drafts

    def messages(self):
        return self._messages

    def drafts(self):
        return self._drafts


class _StubService:
    def __init__(self, messages=None, drafts=None):
        self._users = _StubUsers(messages, drafts)

    def users(self):
        return self._users


def test_a_repeated_message_page_is_not_counted_twice():
    page = {"messages": [{"id": "m1"}], "nextPageToken": "T"}
    msgs = _StubMessages([page, page])
    rows, complete = reader.list_all_messages(_StubService(messages=msgs), "is:unread")
    assert [r["id"] for r in rows] == ["m1"]
    assert complete is False


def test_a_repeated_message_page_still_stops_the_walk():
    page = {"messages": [{"id": "m1"}], "nextPageToken": "T"}
    msgs = _StubMessages([page, page, page])
    reader.list_all_messages(_StubService(messages=msgs), "is:unread")
    assert len(msgs.calls) == 2


def test_distinct_pages_are_all_kept():
    pages = [
        {"messages": [{"id": "m1"}], "nextPageToken": "A"},
        {"messages": [{"id": "m2"}], "nextPageToken": "B"},
        {"messages": [{"id": "m3"}]},
    ]
    msgs = _StubMessages(pages)
    rows, complete = reader.list_all_messages(_StubService(messages=msgs), "is:unread")
    assert [r["id"] for r in rows] == ["m1", "m2", "m3"]
    assert complete is True


def test_the_message_page_cap_still_bounds_a_server_that_never_stops():
    pages = [{"messages": [{"id": f"m{i}"}], "nextPageToken": f"T{i}"}
             for i in range(50)]

    class _Endless(_StubMessages):
        def list(self, **kwargs):
            self.calls.append(kwargs)
            i = len(self.calls) - 1
            return _Exec({"messages": [{"id": f"m{i}"}], "nextPageToken": f"T{i}"})

    msgs = _Endless(pages)
    rows, complete = reader.list_all_messages(
        _StubService(messages=msgs), "is:unread", max_pages=4)
    assert len(rows) == 4
    assert complete is False


# ---------------------------------------------------------------------------
# Finding 4 -- the same repeat in the draft walk, plus a wasted round trip
# ---------------------------------------------------------------------------

class _StubDrafts:
    def __init__(self, pages):
        self._pages = list(pages)
        self.list_calls = []
        self.get_ids = []

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        page = self._pages[min(len(self.list_calls) - 1, len(self._pages) - 1)]
        return _Exec(page)

    def get(self, **kwargs):
        # **kwargs, not named `id=`/`format=`: the real call uses those keywords
        # and naming them here shadows two builtins (ruff A002).
        draft_id = kwargs["id"]
        self.get_ids.append(draft_id)
        return _Exec({"message": {"payload": {"headers": [
            {"name": "To", "value": f"{draft_id}@example.com"},
            {"name": "Subject", "value": f"subject for {draft_id}"},
        ]}}})


def test_a_repeated_draft_page_is_not_listed_twice():
    page = {"drafts": [{"id": "d1"}], "nextPageToken": "T"}
    drafts = _StubDrafts([page, page])
    rows, complete = sender.fetch_drafts(_StubService(drafts=drafts), limit=25)
    assert [r["id"] for r in rows] == ["d1"]
    assert complete is False


def test_a_repeated_draft_is_not_fetched_a_second_time():
    """The skip happens before the metadata GET, so the round trip is saved."""
    page = {"drafts": [{"id": "d1"}], "nextPageToken": "T"}
    drafts = _StubDrafts([page, page])
    sender.fetch_drafts(_StubService(drafts=drafts), limit=25)
    assert drafts.get_ids == ["d1"]


def test_a_duplicated_draft_no_longer_looks_like_an_ambiguous_match():
    """One real draft read twice used to refuse the send as two matches."""
    page = {"drafts": [{"id": "d1"}], "nextPageToken": "T"}
    drafts = _StubDrafts([page, page])
    rows, complete = sender.fetch_drafts(_StubService(drafts=drafts), limit=25)
    assert len(rows) == 1
    # complete is False, so select_draft still refuses -- but for the honest
    # reason (a truncated walk), not for a phantom second draft.
    with pytest.raises(sender.DraftSelectionError) as exc:
        sender.select_draft(rows, match_subject="subject for d1",
                            complete=complete, searched=len(rows))
    assert "2 drafts match" not in str(exc.value)


def test_distinct_draft_pages_are_all_kept():
    pages = [
        {"drafts": [{"id": "d1"}], "nextPageToken": "A"},
        {"drafts": [{"id": "d2"}]},
    ]
    drafts = _StubDrafts(pages)
    rows, complete = sender.fetch_drafts(_StubService(drafts=drafts), limit=25)
    assert [r["id"] for r in rows] == ["d1", "d2"]
    assert complete is True


# ---------------------------------------------------------------------------
# Finding 5 -- an id miss over a truncated walk asserted more than it knew
# ---------------------------------------------------------------------------

def test_an_id_miss_over_a_truncated_list_names_the_horizon():
    with pytest.raises(sender.DraftSelectionError) as exc:
        sender.select_draft([{"id": "d1", "to": "", "subject": ""}],
                            draft_id="d9", complete=False, searched=25)
    msg = str(exc.value)
    assert "25" in msg
    assert "truncated" in msg
    assert "beyond" in msg


def test_an_id_miss_over_a_complete_list_still_says_it_plainly():
    with pytest.raises(sender.DraftSelectionError) as exc:
        sender.select_draft([{"id": "d1", "to": "", "subject": ""}],
                            draft_id="d9", complete=True)
    assert str(exc.value) == "no draft with id d9"


def test_an_id_found_inside_a_truncated_list_is_still_conclusive():
    """Present is present; only absence needed the whole mailbox."""
    rows = [{"id": "d1", "to": "", "subject": ""}]
    assert sender.select_draft(rows, draft_id="d1", complete=False, searched=25) == "d1"


def test_the_docstring_no_longer_claims_an_unread_page_cannot_matter():
    """Pinned by ORDER, not by absence.

    A plain `"no unread page changes that" not in doc` cannot work here: the
    correction QUOTES the sentence it retracts, so the words are still present
    and always will be. What must hold is that the true claim comes first and
    the old one survives only as the thing being quoted. Whitespace is
    normalised too, because a re-wrap is not a retraction.
    """
    doc = " ".join(sender.select_draft.__doc__.split())
    assert "only half-unaffected" in doc
    assert "used to read" in doc
    assert doc.index("only half-unaffected") < doc.index("no unread page changes that")


# ---------------------------------------------------------------------------
# Finding 6 -- send validated its selectors only after contacting Gmail
# ---------------------------------------------------------------------------

class _Args:
    def __init__(self, draft_id=None, match_subject=None, limit=25):
        self.draft_id = draft_id
        self.match_subject = match_subject
        self.limit = limit


def test_send_with_no_selector_never_touches_the_network(monkeypatch, capsys):
    def _boom():
        raise AssertionError("get_service was called before validating the args")

    monkeypatch.setitem(
        sys.modules, "scripts.utils.gmail_auth",
        type(sys)("scripts.utils.gmail_auth"))
    sys.modules["scripts.utils.gmail_auth"].get_service = _boom

    assert sender.cmd_send(_Args()) == 2
    assert "exactly one of" in capsys.readouterr().err


def test_send_with_both_selectors_never_touches_the_network(monkeypatch, capsys):
    def _boom():
        raise AssertionError("get_service was called before validating the args")

    monkeypatch.setitem(
        sys.modules, "scripts.utils.gmail_auth",
        type(sys)("scripts.utils.gmail_auth"))
    sys.modules["scripts.utils.gmail_auth"].get_service = _boom

    assert sender.cmd_send(_Args(draft_id="d1", match_subject="x")) == 2
    assert "exactly one of" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Finding 7 -- contact search stopped at one page and said nothing
# ---------------------------------------------------------------------------

class _StubPeople:
    def __init__(self, search_pages=None, list_pages=None):
        self._search_pages = list(search_pages or [])
        self._list_pages = list(list_pages or [])
        self.search_calls = []
        self.list_calls = []

    # searchContacts is reached directly off people()
    def searchContacts(self, **kwargs):
        if kwargs.get("query") == "" and kwargs.get("readMask") == "names":
            return _Exec({})            # the warmup call
        self.search_calls.append(kwargs)
        i = min(len(self.search_calls) - 1, len(self._search_pages) - 1)
        return _Exec(self._search_pages[i])

    def connections(self):
        return self

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        i = min(len(self.list_calls) - 1, len(self._list_pages) - 1)
        return _Exec(self._list_pages[i])


class _PeopleService:
    def __init__(self, people):
        self._people = people

    def people(self):
        return self._people


def _person(name):
    return {"resourceName": f"people/{name}", "names": [{"displayName": name}]}


def test_search_follows_the_page_token():
    pages = [
        {"results": [{"person": _person("a")}], "nextPageToken": "T1"},
        {"results": [{"person": _person("b")}]},
    ]
    people = _StubPeople(search_pages=pages)
    found = contacts.cmd_search(_PeopleService(people), "x", as_json=False, limit=100)
    assert [p["names"][0]["displayName"] for p in found] == ["a", "b"]


def test_search_never_asks_for_more_than_the_limit_allows():
    """A server that fills the page it is offered must not overshoot --limit."""

    class _Filling(_StubPeople):
        def searchContacts(self, **kwargs):
            if kwargs.get("readMask") == "names":
                return _Exec({})
            self.search_calls.append(kwargs)
            n = kwargs["pageSize"]
            return _Exec({"results": [{"person": _person(f"p{i}")} for i in range(n)],
                          "nextPageToken": "T"})

    people = _Filling()
    found = contacts.cmd_search(_PeopleService(people), "x", as_json=False, limit=1)
    assert people.search_calls[0]["pageSize"] == 1
    assert len(found) == 1


def test_search_says_so_when_it_stops_at_the_limit(capsys):
    pages = [{"results": [{"person": _person("a")}], "nextPageToken": "T1"}]
    people = _StubPeople(search_pages=pages)
    contacts.cmd_search(_PeopleService(people), "x", as_json=False, limit=1)
    assert "more matches remain" in capsys.readouterr().err


def test_search_is_silent_when_it_saw_everything(capsys):
    pages = [{"results": [{"person": _person("a")}]}]
    people = _StubPeople(search_pages=pages)
    contacts.cmd_search(_PeopleService(people), "x", as_json=False, limit=30)
    assert "more matches remain" not in capsys.readouterr().err


def test_search_refuses_a_repeated_page_token(capsys):
    page = {"results": [{"person": _person("a")}], "nextPageToken": "T"}
    people = _StubPeople(search_pages=[page, page])
    contacts.cmd_search(_PeopleService(people), "x", as_json=False, limit=1000)
    assert "repeated a search page token" in capsys.readouterr().err
    assert len(people.search_calls) == 2


def test_search_stops_at_the_page_cap(monkeypatch, capsys):
    monkeypatch.setattr(contacts, "MAX_PAGES", 3)
    people = _StubPeople(search_pages=[
        {"results": [{"person": _person(f"p{i}")}], "nextPageToken": f"T{i}"}
        for i in range(10)])
    contacts.cmd_search(_PeopleService(people), "x", as_json=False, limit=1000)
    assert len(people.search_calls) == 3
    assert "page cap" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Finding 8 -- cmd_list could call the API forever
# ---------------------------------------------------------------------------

def test_an_empty_page_with_a_token_does_not_loop_forever(capsys):
    """The exact shape: nothing returned, same token, neither counter moves."""
    page = {"connections": [], "nextPageToken": "same-token"}
    people = _StubPeople(list_pages=[page])
    got = contacts.cmd_list(_PeopleService(people), limit=100)
    assert got == []
    assert len(people.list_calls) == 1
    assert "empty page" in capsys.readouterr().err


def test_a_repeated_list_token_stops_the_walk(capsys):
    page = {"connections": [_person("a")], "nextPageToken": "T"}
    people = _StubPeople(list_pages=[page, page])
    got = contacts.cmd_list(_PeopleService(people), limit=100)
    assert len(people.list_calls) == 2
    assert len(got) == 2          # the pages themselves are not de-duplicated
    assert "repeated a page token" in capsys.readouterr().err


def test_the_list_page_cap_bounds_a_server_that_never_stops(monkeypatch, capsys):
    monkeypatch.setattr(contacts, "MAX_PAGES", 3)

    class _Endless(_StubPeople):
        def list(self, **kwargs):
            self.list_calls.append(kwargs)
            i = len(self.list_calls)
            return _Exec({"connections": [_person(f"p{i}")], "nextPageToken": f"T{i}"})

    people = _Endless(list_pages=[])
    contacts.cmd_list(_PeopleService(people), limit=100000)
    assert len(people.list_calls) == 3
    assert "page cap" in capsys.readouterr().err


def test_a_normal_two_page_list_is_unaffected(capsys):
    pages = [
        {"connections": [_person("a")], "nextPageToken": "T1"},
        {"connections": [_person("b")]},
    ]
    people = _StubPeople(list_pages=pages)
    got = contacts.cmd_list(_PeopleService(people), limit=100)
    assert [c["names"][0]["displayName"] for c in got] == ["a", "b"]
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# Finding 9 -- the no-key branch drops the first entry the docstring protected
# ---------------------------------------------------------------------------

def test_the_no_key_branch_replaces_the_first_and_keeps_the_tail():
    got = contacts._replace_first(
        {"custom": [{"legacy": 1}, {"legacy": 2}]}, "custom", {"new": 3})
    assert got == [{"new": 3}, {"legacy": 2}]


def test_the_docstring_no_longer_promises_that_nothing_is_dropped():
    doc = contacts._replace_first.__doc__.replace("\n", " ")
    assert "so no TAIL entry is dropped" in doc
    assert "against, so nothing is dropped" not in doc


def test_an_address_edit_still_keeps_the_other_addresses():
    """The keyed path, pinned again beside the docstring it belongs to."""
    current = {"addresses": [{"formattedValue": "home"},
                             {"formattedValue": "office"}]}
    got = contacts._replace_first(current, "addresses", {"formattedValue": "new"})
    assert got == [{"formattedValue": "new"}, {"formattedValue": "office"}]
