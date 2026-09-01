"""A contact command that always crashed, and guards that measured the wrong thing.

Covers the k3 audit shard `scripts-07-p2` for `scripts/google-contacts.py`,
`scripts/gmail-draft.py`, `scripts/gmail-reader.py`, `scripts/gmail-send.py`,
`scripts/generate-usecases-docx.py` and `scripts/harness-audit.py`.

The worst of it is not in the audit. `cmd_add` in `google-contacts.py` called
`_replace_first(current, ...)` five times, and `current` does not exist in that
function: it is a NEW contact, there is nothing to preserve. Every
`google-contacts.py add` carrying `--email`, `--phone`, `--company`,
`--address` or `--url` raised NameError before it reached the API. The helper
was written for `cmd_edit`, where a fetched contact IS in scope, and was wired
into the wrong function; `cmd_edit` meanwhile still replaced each list
wholesale. The fix moves the preservation to where it belongs and gives
`cmd_add` the plain lists a new contact needs.

The rest are guards that measured something adjacent to what they claimed.
`gmail-draft.py` summed RAW attachment bytes against a limit that applies to
the base64url-encoded message, so ~19 MB sailed through `19 < 25` and failed at
the API. `mark-all-read` fetched 100 messages, followed no page token, and
printed "Marked 100 emails as read" over a mailbox that still had unread mail.
`harness-audit.py` printed unreadable files and exited 0, while treating a
symlink -- the same class of content it cannot vouch for -- as exit 1; and its
empty-surface guard only fired when a previous non-empty baseline existed, so a
mistyped root on a FIRST run minted a baseline that matched nothing, forever.

Nothing here authenticates, sends, or reaches Google. Every test drives a pure
function or a fake service object.
"""
from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_").replace(".py", ""), str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _code(name: str) -> str:
    """Source minus whole-line comments.

    Every fix here left a comment quoting the code it removed, so a plain grep
    for the old shape finds its own tombstone and passes for the wrong reason.
    """
    text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
    return "\n".join(ln for ln in text.split("\n") if not ln.lstrip().startswith("#"))


# ============================================================
# google-contacts: add never worked, edit deleted data
# ============================================================

@pytest.fixture(scope="module")
def gc():
    return _load("google-contacts.py")


class _FakePeople:
    """Records the body of the one create/update call it receives."""

    def __init__(self, current=None):
        self.current = current or {"etag": "e1"}
        self.created = None
        self.updated = None
        self.update_fields = None

    def get(self, **kwargs):
        return _Exec(self.current)

    def createContact(self, body=None, **kwargs):   # noqa: N802 (API name)
        self.created = body
        return _Exec({"names": [{"displayName": "Bond, James Bond"}],
                      "resourceName": "people/c1"})

    def updateContact(self, body=None, updatePersonFields=None, **kwargs):  # noqa: N802
        self.updated = body
        self.update_fields = updatePersonFields
        return _Exec({"names": [{"displayName": "Bond, James Bond"}],
                      "resourceName": "people/c1"})


class _Exec:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _FakeService:
    def __init__(self, people):
        self._people = people

    def people(self):
        return self._people


def test_add_with_an_email_does_not_raise_name_error(gc, capsys):
    """`_replace_first(current, ...)` in cmd_add referenced an undefined name."""
    people = _FakePeople()
    gc.cmd_add(_FakeService(people), "Bond, James Bond",
               email="bond@acme.example", phone="+971500000000",
               company="Acme", title="Agent", address="Dubai",
               url="https://acme.example")
    assert people.created is not None
    assert people.created["emailAddresses"] == [{"value": "bond@acme.example"}]
    assert people.created["phoneNumbers"] == [{"value": "+971500000000"}]
    assert people.created["organizations"] == [{"name": "Acme", "title": "Agent"}]
    assert people.created["addresses"] == [{"formattedValue": "Dubai"}]
    assert people.created["urls"] == [{"value": "https://acme.example"}]


def test_cmd_add_never_reaches_for_a_contact_it_does_not_have():
    code = _code("google-contacts.py")
    start = code.index("def cmd_add(")
    body = code[start:code.index("def cmd_get(", start)]
    assert "_replace_first(current" not in body, (
        "cmd_add has no `current`; the helper belongs to cmd_edit"
    )


def test_editing_a_title_keeps_the_second_organization(gc):
    """A current employer plus a board seat: the board seat used to vanish."""
    current = {"etag": "e1", "organizations": [
        {"name": "Acme", "title": "Agent"},
        {"name": "Umbrella", "title": "Board member"},
    ]}
    people = _FakePeople(current)
    gc.cmd_edit(_FakeService(people), "people/c1", title="Chief Agent")
    assert people.updated["organizations"] == [
        {"name": "Acme", "title": "Chief Agent"},
        {"name": "Umbrella", "title": "Board member"},
    ]


def test_editing_a_company_on_a_contact_with_one_org_still_works(gc):
    people = _FakePeople({"etag": "e1", "organizations": [{"name": "Acme"}]})
    gc.cmd_edit(_FakeService(people), "people/c1", company="NewCo")
    assert people.updated["organizations"] == [{"name": "NewCo"}]


def test_editing_a_company_on_a_contact_with_no_org_creates_one(gc):
    people = _FakePeople({"etag": "e1"})
    gc.cmd_edit(_FakeService(people), "people/c1", company="NewCo")
    assert people.updated["organizations"] == [{"name": "NewCo"}]


def test_editing_one_phone_keeps_the_others(gc):
    people = _FakePeople({"etag": "e1", "phoneNumbers": [
        {"value": "+1"}, {"value": "+2"}, {"value": "+3"}]})
    gc.cmd_edit(_FakeService(people), "people/c1", phone="+9")
    assert people.updated["phoneNumbers"] == [
        {"value": "+9"}, {"value": "+2"}, {"value": "+3"}]


def test_editing_one_email_keeps_the_others(gc):
    people = _FakePeople({"etag": "e1", "emailAddresses": [
        {"value": "a@acme.example"}, {"value": "b@acme.example"}]})
    gc.cmd_edit(_FakeService(people), "people/c1", email="c@acme.example")
    assert people.updated["emailAddresses"] == [
        {"value": "c@acme.example"}, {"value": "b@acme.example"}]


@pytest.mark.parametrize("raw,expected", [
    ("Bond", ("Bond", "")),
    ("James Bond", ("James", "Bond")),
    ("  James   Bond  ", ("James", "Bond")),
    ("Bond, James Bond", ("Bond,", "James Bond")),
])
def test_a_name_splits_into_given_and_family(gc, raw, expected):
    assert gc.split_name(raw) == expected


@pytest.mark.parametrize("blank", ["", "   ", "\t\n", None])
def test_a_blank_name_is_a_validation_error_not_an_index_error(gc, blank):
    """`parts[0]` on an empty list printed "[ERROR] list index out of range"."""
    with pytest.raises(gc.ContactInputError):
        gc.split_name(blank)


def test_the_dependency_hint_names_the_package_that_provides_the_import():
    code = _code("google-contacts.py")
    idx = code.index("from google.auth.transport.requests import Request")
    window = code[idx:idx + 300]
    assert 'missing.append("google-auth")' in window
    assert "google-auth-httplib2" not in window, (
        "google.auth.transport.requests comes from google-auth"
    )


def test_a_credentials_override_that_does_not_exist_is_fatal(gc, tmp_path,
                                                             monkeypatch, capsys):
    """Falling back writes to a different Google account, silently."""
    default = tmp_path / "sessions"
    default.mkdir()
    (default / "credentials.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(gc, "SESSION_DIR", str(default))
    monkeypatch.setenv("GOOGLE_CONTACTS_CREDENTIALS_PATH", str(tmp_path / "typo.json"))

    with pytest.raises(SystemExit) as exc:
        gc._get_credentials_path()
    assert exc.value.code == 1
    assert "typo.json" in capsys.readouterr().err


def test_a_credentials_override_that_exists_is_used(gc, tmp_path, monkeypatch):
    override = tmp_path / "mine.json"
    override.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CONTACTS_CREDENTIALS_PATH", str(override))
    assert gc._get_credentials_path() == str(override)


def test_no_override_falls_back_to_the_default(gc, tmp_path, monkeypatch):
    default = tmp_path / "sessions"
    default.mkdir()
    (default / "credentials.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(gc, "SESSION_DIR", str(default))
    monkeypatch.delenv("GOOGLE_CONTACTS_CREDENTIALS_PATH", raising=False)
    assert gc._get_credentials_path() == str(default / "credentials.json")


# ============================================================
# gmail-reader: plain text at any depth beats HTML at the top
# ============================================================

@pytest.fixture(scope="module")
def reader():
    return _load("gmail-reader.py")


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def test_nested_plain_text_wins_over_top_level_html(reader):
    payload = {"parts": [
        {"mimeType": "text/html", "body": {"data": _b64("<p>html version</p>")}},
        {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("plain version")}},
        ]},
    ]}
    assert reader.decode_body(payload) == "plain version"


def test_a_part_with_no_text_does_not_end_the_search(reader):
    """`if result:` was always true, so a later sibling was never visited."""
    payload = {"parts": [
        {"mimeType": "multipart/mixed", "parts": [
            {"mimeType": "application/pdf", "body": {"attachmentId": "x"}},
        ]},
        {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("found me")}},
        ]},
    ]}
    assert reader.decode_body(payload) == "found me"


def test_top_level_plain_text_is_still_preferred(reader):
    payload = {"parts": [
        {"mimeType": "text/plain", "body": {"data": _b64("top plain")}},
        {"mimeType": "text/html", "body": {"data": _b64("<p>html</p>")}},
    ]}
    assert reader.decode_body(payload) == "top plain"


def test_html_is_used_when_there_is_no_plain_text_anywhere(reader):
    payload = {"parts": [
        {"mimeType": "text/html", "body": {"data": _b64("<p>only  html</p>")}},
    ]}
    assert reader.decode_body(payload) == "only html"


def test_a_payload_with_no_text_at_all_says_so(reader):
    payload = {"parts": [{"mimeType": "application/pdf", "body": {"attachmentId": "x"}}]}
    assert reader.decode_body(payload) == reader.NO_TEXT_BODY


def test_a_missing_header_is_an_empty_string(reader):
    """Found by a mutation that aimed elsewhere and hit this instead.

    Nothing asserted the default, so any wrong value here would have rendered
    straight into the CC or Subject line of every summary.
    """
    headers = [{"name": "From", "value": "bond@acme.example"}]
    assert reader.get_header(headers, "Cc") == ""
    assert reader.get_header([], "Subject") == ""


def test_a_header_lookup_ignores_case(reader):
    headers = [{"name": "SUBJECT", "value": "Memo"}]
    assert reader.get_header(headers, "subject") == "Memo"


def test_a_direct_body_is_returned_without_walking_parts(reader):
    assert reader.decode_body({"body": {"data": _b64("direct")}}) == "direct"


# ------------------------------------------------------------
# The single-part path, which `decode_body`'s docstring is mostly about and
# which nothing above reaches. Every case here is multipart, so the three
# branches that read the MIME type of a payload's OWN body were unmeasured.
# MEASURED 2026-09-01: deleting `if mime == "text/html"` - the defect the
# docstring names FIRST - left all 44 tests passing, as did deleting the
# non-text refusal below it.
# ------------------------------------------------------------

def test_a_whole_payload_that_is_html_is_stripped_not_returned_raw(reader):
    """The headline defect: an HTML-only sender with no multipart wrapper.

    Nested HTML was stripped correctly and only the top level was not, so the
    defect appeared and disappeared with the sender's choice of wrapper, and
    `read` printed tag soup at the operator.
    """
    payload = {"mimeType": "text/html",
               "body": {"data": _b64("<p>Hello  <b>there</b></p>")}}
    assert reader.decode_body(payload) == "Hello there"


def test_a_whole_payload_that_is_binary_has_no_text_body(reader):
    """Rather than decoding binary into replacement characters and calling it
    a message."""
    payload = {"mimeType": "application/pdf",
               "body": {"data": _b64("%PDF-1.4 not a message")}}
    assert reader.decode_body(payload) == reader.NO_TEXT_BODY


def test_a_whole_payload_that_is_plain_text_is_returned_verbatim(reader):
    """The sole witness for the accept branch, so a refusal-everywhere "fix"
    cannot satisfy the two cases above."""
    payload = {"mimeType": "text/plain", "body": {"data": _b64("just words")}}
    assert reader.decode_body(payload) == "just words"


@pytest.mark.parametrize("mime", [
    "text/html; charset=UTF-8",   # what a real HTML sender actually writes
    "TEXT/HTML",
    "  text/html  ",
])
def test_the_mime_type_is_read_without_its_parameters(reader, mime):
    """`_mime_of` lowercases and strips parameters, and only the HTML branch
    can witness it.

    A charset case on `text/plain` proves nothing and was written here first:
    both the normalising and the non-normalising form still satisfy
    `startswith("text/")`, so the assertion held either way - MEASURED
    2026-09-01. `text/html` is different, because its branch is an EQUALITY
    test. Without normalisation `text/html; charset=UTF-8` misses it, falls
    through to the `startswith` accept, and the raw markup goes back to the
    operator, which is the original defect wearing a charset.
    """
    payload = {"mimeType": mime, "body": {"data": _b64("<p>Hello  there</p>")}}
    assert reader.decode_body(payload) == "Hello there"


@pytest.mark.parametrize("mime", ["text/calendar", "text/csv"])
def test_any_text_subtype_is_still_text(reader, mime):
    """The accept branch is `startswith("text/")`, not a list of two types.

    `text/calendar` is an ordinary Gmail part (every meeting invite carries
    one), and narrowing this to `== "text/plain"` would turn it into
    NO_TEXT_BODY. Nothing else in this file distinguishes the two forms:
    MEASURED 2026-09-01, that narrowing left all 50 tests passing.
    """
    payload = {"mimeType": mime, "body": {"data": _b64("BEGIN:VCALENDAR")}}
    assert reader.decode_body(payload) == "BEGIN:VCALENDAR"


class _FakeMessages:
    """Two pages, so a caller that ignores nextPageToken sees half."""

    def __init__(self, pages):
        self._pages = pages
        self.requested = []

    def list(self, **kwargs):
        self.requested.append(kwargs)
        idx = 0
        if kwargs.get("pageToken"):
            idx = int(kwargs["pageToken"])
        return _Exec(self._pages[idx])


class _FakeUsers:
    def __init__(self, messages):
        self._messages = messages

    def messages(self):
        return self._messages


class _FakeGmail:
    def __init__(self, messages):
        self._users = _FakeUsers(messages)

    def users(self):
        return self._users


def test_mark_all_read_follows_every_page(reader):
    """100 was the whole answer, and the success line implied completion."""
    pages = [
        {"messages": [{"id": f"a{i}"} for i in range(500)], "nextPageToken": "1"},
        {"messages": [{"id": f"b{i}"} for i in range(7)]},
    ]
    messages = _FakeMessages(pages)
    got, complete = reader.list_all_messages(_FakeGmail(messages), "is:unread")
    assert len(got) == 507
    assert complete is True
    assert len(messages.requested) == 2
    assert messages.requested[1]["pageToken"] == "1"


def test_a_single_page_result_makes_one_call(reader):
    messages = _FakeMessages([{"messages": [{"id": "a"}]}])
    got, complete = reader.list_all_messages(_FakeGmail(messages), "is:unread")
    assert got == [{"id": "a"}] and complete is True
    assert len(messages.requested) == 1


class _LoopingMessages:
    """A server that hands out the same page token forever.

    Not hypothetical. On 2026-08-24 a mutation disabled the line that SENDS
    the token, this shape appeared, the bare `while True` never ended, and the
    pytest process reached 47 GB before the kernel OOM-killer took the whole
    WSL session with it. The loop is bounded now, and this is the test that
    says so.
    """

    def __init__(self, page="same"):
        # Not `token`: ruff's S107 reads that name as a hardcoded credential.
        self.page = page
        self.calls = 0

    def list(self, **kwargs):
        self.calls += 1
        return _Exec({"messages": [{"id": f"m{self.calls}"}],
                      "nextPageToken": self.page})


def test_a_repeated_page_token_stops_the_walk(reader, capsys):
    messages = _LoopingMessages()
    got, complete = reader.list_all_messages(_FakeGmail(messages), "is:unread")
    assert complete is False
    assert messages.calls == 2, "one page, then the repeat is refused"
    assert len(got) == 2
    assert "repeated page token" in capsys.readouterr().err


class _EndlessMessages:
    """A server with a genuinely endless supply of DISTINCT tokens."""

    def __init__(self):
        self.calls = 0

    def list(self, **kwargs):
        self.calls += 1
        return _Exec({"messages": [{"id": f"m{self.calls}"}],
                      "nextPageToken": str(self.calls)})


def test_the_page_cap_stops_the_walk(reader, capsys):
    messages = _EndlessMessages()
    got, complete = reader.list_all_messages(_FakeGmail(messages), "is:unread",
                                             max_pages=5)
    assert complete is False
    assert messages.calls == 5
    assert len(got) == 5
    assert "5-page cap" in capsys.readouterr().err


def test_the_paging_loop_cannot_run_forever_by_construction():
    code = _code("gmail-reader.py")
    start = code.index("def list_all_messages(")
    body = code[start:code.index("def get_message_summary(", start)]
    assert "while True" not in body, (
        "a loop whose end depends only on a remote party can eat all memory"
    )
    assert "for _ in range(max_pages):" in body
    assert "seen_tokens" in body


def test_an_incomplete_listing_is_never_reported_as_finished():
    code = _code("gmail-reader.py")
    start = code.index("def cmd_mark_all_read(")
    body = code[start:code.index("def main(", start)]
    assert "if complete:" in body
    assert "NOT all of them" in body


def test_mark_all_read_no_longer_caps_itself_at_a_hundred():
    code = _code("gmail-reader.py")
    assert 'list_messages(service, "is:unread", 100)' not in code
    assert 'list_all_messages(service, "is:unread")' in code


# ============================================================
# gmail-send: an exact id is looked up directly
# ============================================================

def test_an_exact_draft_id_is_not_searched_inside_a_page():
    code = _code("gmail-send.py")
    start = code.index("def cmd_send(")
    body = code[start:code.index("def main(", start)]
    assert "_resolve_draft_id(service, args.draft_id)" in body
    assert "fetch_drafts" in body, "--match-subject still needs the list"


# ============================================================
# generate-usecases-docx: the summary agrees with the document
# ============================================================

def test_the_executive_summary_states_the_number_the_document_builds():
    src = (ROOT / "scripts" / "generate-usecases-docx.py").read_text(encoding="utf-8")
    assert "20 use cases across six categories" not in src, (
        "stale text from an earlier version, in the opening paragraph of a "
        "customer-facing artifact that then says 41 two sentences later"
    )
    assert "41 use cases across ten sections" in src
    # minus the def line itself
    assert src.count("add_usecase(") - 1 == 41


def test_the_template_scratch_copy_cannot_be_left_behind():
    code = _code("generate-usecases-docx.py")
    assert "_tmp_tpl.docx" not in code, "one fixed name in the outputs directory"
    assert "tempfile.mkdtemp" in code
    assert "finally:" in code[code.index("def load_template("):]


# ============================================================
# harness-audit: unvouchable content is not a pass
# ============================================================

def test_unreadable_content_is_treated_like_a_symlink():
    code = _code("harness-audit.py")
    idx = code.index('if (result["baseline_missing"] or drifted')
    window = code[idx:idx + 250]
    assert 'result["unreadable"]' in window, (
        "an unreadable file is content the audit cannot vouch for, same as a "
        "symlink, and symlinks already exit 1"
    )


def test_the_empty_surface_guard_does_not_need_a_previous_baseline():
    code = _code("harness-audit.py")
    assert "if not index and not args.allow_empty:" in code
    assert "if not index and previous and previous.get(\"entries\"):" not in code


def test_active_install_paths_takes_only_what_it_reads():
    code = _code("harness-audit.py")
    assert "def active_install_paths(path: Path) -> set | None:" in code, (
        "the unused `repo` parameter was the leftover of a reverted design"
    )


@pytest.fixture(scope="module")
def audit():
    return _load("harness-audit.py")


def test_an_empty_but_readable_plugin_record_is_not_unreadable(audit, tmp_path):
    """`return active or None` turned "no plugins" into "file unreadable"."""
    record = tmp_path / "installed_plugins.json"
    record.write_text('{"plugins": {}}', encoding="utf-8")
    assert audit.active_install_paths(record) == set()


def test_an_absent_plugin_record_is_unknown(audit, tmp_path):
    assert audit.active_install_paths(tmp_path / "nope.json") is None


def test_a_malformed_plugin_record_is_unknown(audit, tmp_path):
    record = tmp_path / "installed_plugins.json"
    record.write_text("{not json", encoding="utf-8")
    assert audit.active_install_paths(record) is None


def test_a_record_with_install_paths_returns_them(audit, tmp_path):
    import json
    one, two = tmp_path / "one", tmp_path / "two"
    record = tmp_path / "installed_plugins.json"
    record.write_text(json.dumps({"plugins": {"vendor": [
        {"installPath": str(one)}, {"installPath": str(two)}]}}), encoding="utf-8")
    assert audit.active_install_paths(record) == {one.resolve(), two.resolve()}
