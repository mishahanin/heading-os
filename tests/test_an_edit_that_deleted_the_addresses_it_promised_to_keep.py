"""Shard 07-p3: four tools that answered a narrower question than they asked.

`_replace_first` in `scripts/google-contacts.py` carries a docstring promising
"the contact's others kept", and kept them for phones, emails and URLs. For
addresses it compared `e.get("value") != entry.get("value")` -- and a People
API Address has no `value` member on either side, so every comparison was
`None != None`, every existing address was dropped, and `edit --address` sent a
one-element list into a call that replaces the whole field. A contact with a
home and an office address kept the one just typed.

`_check_dependencies` in the same file claimed to verify
google-api-python-client by importing `google.oauth2.credentials`, which ships
in google-auth. Nothing imported `googleapiclient` at all, so the one package
the error message names was the one package never checked.

`fetch_drafts` in `scripts/gmail-send.py` read one page and `--match-subject`
then declared the match UNIQUE over it. The module docstring says "an ambiguous
match is an error, not a guess"; uniqueness asserted over an unstated horizon is
the guess.

`_walk_surface` in `scripts/harness-audit.py` promises a symlink "is reported by
name and never resolved". It handled symlinked FILES. On Python 3.11 -- this
workspace's floor -- `rglob` descends through a symlinked DIRECTORY, and the
children it yields are not symlinks, so a link aimed outside the plugin root had
its contents hashed into the reviewed baseline as ordinary vouched surface.

Tests: this file.
"""
from __future__ import annotations

import builtins
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(stem: str, module_name: str):
    """Import a hyphenated CLI script by path."""
    spec = importlib.util.spec_from_file_location(
        module_name, ROOT / "scripts" / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


gc = _load("google-contacts", "google_contacts_07p3")
gs = _load("gmail-send", "gmail_send_07p3")
ha = _load("harness-audit", "harness_audit_07p3")


# ==========================================================================
# 1 - the address that was deleted by the edit that promised to keep it
# ==========================================================================

def test_editing_one_address_keeps_the_others():
    current = {"addresses": [
        {"formattedValue": "1 Old St", "type": "home"},
        {"formattedValue": "2 Office Rd", "type": "work"},
        {"formattedValue": "3 Summer Ln", "type": "other"},
    ]}
    out = gc._replace_first(current, "addresses", {"formattedValue": "9 New Way"})
    values = [e["formattedValue"] for e in out]
    assert values[0] == "9 New Way", "the edited address must be primary"
    assert "2 Office Rd" in values and "3 Summer Ln" in values, \
        "editing one address deleted the others"
    assert "1 Old St" not in values, "the replaced primary must not linger"


def test_an_address_the_contact_already_has_is_not_duplicated():
    current = {"addresses": [
        {"formattedValue": "1 Old St"}, {"formattedValue": "2 Office Rd"},
    ]}
    out = gc._replace_first(current, "addresses", {"formattedValue": "2 Office Rd"})
    assert [e["formattedValue"] for e in out] == ["2 Office Rd"]


def test_the_comparison_key_comes_from_the_entry_not_from_a_fixed_name():
    """The two field shapes must both dedupe, in one call each.

    This is the whole defect in one assertion: a hardcoded key can satisfy at
    most one of these.
    """
    by_value = gc._replace_first(
        {"emailAddresses": [{"value": "a@x.test"}, {"value": "b@x.test"}]},
        "emailAddresses", {"value": "b@x.test"})
    by_formatted = gc._replace_first(
        {"addresses": [{"formattedValue": "A"}, {"formattedValue": "B"}]},
        "addresses", {"formattedValue": "B"})
    assert len(by_value) == 1, "the value-keyed field stopped de-duplicating"
    assert len(by_formatted) == 1, "the formattedValue-keyed field never did"


def test_an_entry_with_no_recognised_key_drops_nothing():
    """Not understanding a shape must cost a duplicate, never a deletion."""
    current = {"urls": [{"value": "a"}, {"value": "b"}, {"value": "c"}]}
    out = gc._replace_first(current, "urls", {"type": "work"})
    assert len(out) == 3, "an unrecognised entry shape deleted the tail"
    assert out[0] == {"type": "work"}


def test_a_contact_with_no_addresses_at_all_gains_exactly_one():
    assert gc._replace_first({}, "addresses", {"formattedValue": "1 New St"}) == \
        [{"formattedValue": "1 New St"}]


def test_the_edit_command_sends_every_surviving_address(monkeypatch):
    """End to end through cmd_edit: the body reaching the API keeps both."""
    sent = {}

    class _Call:
        def __init__(self, payload):
            self._payload = payload

        def execute(self):
            return self._payload

    class _People:
        def get(self, **kw):
            return _Call({
                "etag": "etag-1",
                "addresses": [{"formattedValue": "1 Old St"},
                              {"formattedValue": "2 Office Rd"}],
                "names": [{"displayName": "Test Person"}],
            })

        def updateContact(self, **kw):
            sent.update(kw)
            return _Call({"names": [{"displayName": "Test Person"}]})

    class _Service:
        def people(self):
            return _People()

    monkeypatch.setattr(gc, "_print_detail", lambda *a, **k: None)
    gc.cmd_edit(_Service(), "people/c1", address="9 New Way")

    addresses = sent["body"]["addresses"]
    assert [a["formattedValue"] for a in addresses] == ["9 New Way", "2 Office Rd"], \
        "the update body dropped an address the operator never touched"
    assert "addresses" in sent["updatePersonFields"]


# ==========================================================================
# 2 - the dependency check that never imported the package it named
# ==========================================================================

def _block_imports(monkeypatch, *prefixes):
    real = builtins.__import__

    def fake(name, *args, **kwargs):
        for prefix in prefixes:
            if name == prefix or name.startswith(prefix + "."):
                raise ImportError(f"No module named {name!r}")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake)


def _dependency_failure(monkeypatch, capsys, *blocked):
    _block_imports(monkeypatch, *blocked)
    with pytest.raises(SystemExit) as exc:
        gc._check_dependencies()
    assert exc.value.code == 1
    return capsys.readouterr().err


def test_a_missing_googleapiclient_is_caught_before_authenticate(monkeypatch, capsys):
    """The package that provides `build` must be the one that is imported."""
    err = _dependency_failure(monkeypatch, capsys, "googleapiclient")
    assert "google-api-python-client" in err
    assert "google-auth-oauthlib" not in err, "an installed package was blamed"


def test_a_missing_google_auth_is_named_google_auth(monkeypatch, capsys):
    """google-api-python-client is also named here, and that is correct.

    It imports `google.auth` itself, so removing google-auth really does break
    both. Over-reporting a dependency the operator must install anyway is the
    safe direction; the defect was the opposite one, pinned by the test above.
    """
    err = _dependency_failure(monkeypatch, capsys, "google.auth", "google.oauth2")
    assert "google-auth" in err, \
        "google-auth's absence was never attributed to google-auth"


def test_a_missing_oauthlib_is_named(monkeypatch, capsys):
    err = _dependency_failure(monkeypatch, capsys, "google_auth_oauthlib")
    assert "google-auth-oauthlib" in err


def test_the_remedy_installs_what_is_missing_and_not_the_whole_set(monkeypatch, capsys):
    err = _dependency_failure(monkeypatch, capsys, "google_auth_oauthlib")
    remedy = [ln for ln in err.splitlines() if "pip install" in ln]
    assert remedy, "no remedy line was printed"
    assert "google-api-python-client" not in remedy[0], \
        "the remedy told the operator to install a package that is present"
    assert "python-dotenv" not in remedy[0]


def test_everything_present_is_silent(capsys):
    gc._check_dependencies()
    assert capsys.readouterr().err == ""


# ==========================================================================
# 3 - the draft search that claimed uniqueness over one page
# ==========================================================================

class _FakeDrafts:
    """Enough of `service.users().drafts()` to page."""

    def __init__(self, pages, subjects):
        self.pages = pages
        self.subjects = subjects
        self.list_calls = []

    def list(self, **kw):
        self.list_calls.append(kw)
        token = kw.get("pageToken")
        index = 0 if token is None else int(token)
        return _Executable(self.pages[index])

    def get(self, **kw):
        return _Executable({"message": {"payload": {"headers": [
            {"name": "To", "value": "someone@example.test"},
            {"name": "Subject", "value": self.subjects[kw["id"]]},
        ]}}})


class _Executable:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class _FakeUsers:
    def __init__(self, drafts):
        self._drafts = drafts

    def drafts(self):
        return self._drafts


class _FakeService:
    def __init__(self, drafts):
        self._users = _FakeUsers(drafts)

    def users(self):
        return self._users


def _two_page_service():
    pages = [
        {"drafts": [{"id": "r1"}, {"id": "r2"}], "nextPageToken": "1"},
        {"drafts": [{"id": "r3"}]},
    ]
    subjects = {"r1": "Lunch", "r2": "Invoice 41", "r3": "Quarterly report"}
    return _FakeService(_FakeDrafts(pages, subjects))


def test_the_second_page_is_read():
    drafts, complete = gs.fetch_drafts(_two_page_service(), limit=25)
    assert [d["id"] for d in drafts] == ["r1", "r2", "r3"], \
        "a page token was handed back and never followed"
    assert complete is True


def test_the_page_token_is_actually_sent():
    service = _two_page_service()
    gs.fetch_drafts(service, limit=25)
    calls = service.users().drafts().list_calls
    assert len(calls) == 2
    assert calls[1].get("pageToken") == "1", "the second call refetched page one"


def test_a_draft_on_the_second_page_is_findable():
    drafts, complete = gs.fetch_drafts(_two_page_service(), limit=25)
    assert gs.select_draft(drafts, match_subject="quarterly",
                           complete=complete, searched=len(drafts)) == "r3"


def test_a_repeated_page_token_stops_the_walk(capsys):
    """A loop whose end depends only on the server can eat all memory."""
    pages = [{"drafts": [{"id": "r1"}], "nextPageToken": "0"}]
    service = _FakeService(_FakeDrafts(pages, {"r1": "Loop"}))
    drafts, complete = gs.fetch_drafts(service, limit=1000)
    assert complete is False
    assert len(drafts) < 100, "the walk followed a repeated token"
    assert "repeated page token" in capsys.readouterr().err


def test_the_page_cap_bounds_the_walk(capsys):
    """Distinct tokens forever must still terminate."""
    class _Endless(_FakeDrafts):
        def list(self, **kw):
            self.list_calls.append(kw)
            n = len(self.list_calls)
            return _Executable({"drafts": [{"id": f"r{n}"}],
                                "nextPageToken": str(n)})

        def get(self, **kw):
            return _Executable({"message": {"payload": {"headers": []}}})

    service = _FakeService(_Endless([], {}))
    drafts, complete = gs.fetch_drafts(service, limit=10_000, max_pages=4)
    assert complete is False
    assert len(drafts) == 4, "the page cap did not bound the walk"
    assert "page cap" in capsys.readouterr().err


def test_a_limit_that_stops_short_reports_an_incomplete_walk():
    """Stopping at the caller's limit is truncation, and must say so."""
    drafts, complete = gs.fetch_drafts(_two_page_service(), limit=2)
    assert len(drafts) == 2
    assert complete is False, \
        "the walk stopped at --limit and still called itself complete"


def test_a_truncated_search_refuses_rather_than_claiming_uniqueness():
    drafts = [{"id": "r1", "to": "", "subject": "Invoice 41"}]
    with pytest.raises(gs.DraftSelectionError) as exc:
        gs.select_draft(drafts, match_subject="invoice",
                        complete=False, searched=25)
    message = str(exc.value)
    assert "not known to be unique" in message
    assert "25" in message, "the refusal must name the horizon it searched"
    assert "--draft-id" in message


def test_a_truncated_search_with_no_hit_does_not_claim_the_draft_is_absent():
    with pytest.raises(gs.DraftSelectionError) as exc:
        gs.select_draft([{"id": "r1", "to": "", "subject": "Lunch"}],
                        match_subject="invoice", complete=False, searched=25)
    message = str(exc.value)
    assert "no draft whose subject contains" not in message, \
        "absence was asserted over a prefix of the mailbox"
    assert "25" in message


def test_ambiguity_inside_the_prefix_is_reported_as_ambiguity():
    """Two hits already answer the question; truncation does not soften it."""
    drafts = [{"id": "r1", "to": "", "subject": "Invoice 41"},
              {"id": "r2", "to": "", "subject": "Invoice 42"}]
    with pytest.raises(gs.DraftSelectionError) as exc:
        gs.select_draft(drafts, match_subject="invoice",
                        complete=False, searched=25)
    message = str(exc.value)
    assert "2 drafts match" in message
    assert "r1" in message and "r2" in message


def test_omitting_completeness_refuses_rather_than_assuming_it():
    """A caller who never established completeness gets friction, not an answer.

    The mutation that flipped this default survived a suite where every call
    passed `complete=` explicitly. That is the whole failure mode: one
    forgotten keyword restores a uniqueness claim over an unknown subset.
    """
    drafts = [{"id": "r1", "to": "", "subject": "Invoice 41"}]
    with pytest.raises(gs.DraftSelectionError, match="not known to be unique"):
        gs.select_draft(drafts, match_subject="invoice")


def test_an_id_lookup_needs_no_completeness_claim():
    """An id is present or absent; no unread page changes that."""
    drafts = [{"id": "r1", "to": "", "subject": "Invoice 41"}]
    assert gs.select_draft(drafts, draft_id="r1") == "r1"


def test_a_complete_search_still_answers():
    drafts = [{"id": "r1", "to": "", "subject": "Invoice 41"}]
    assert gs.select_draft(drafts, match_subject="invoice",
                           complete=True, searched=1) == "r1"


def test_the_send_horizon_is_wider_than_one_page():
    parser_default = gs.SEARCH_LIMIT
    assert parser_default >= 100, \
        "--match-subject went back to searching a single short page"


# ==========================================================================
# 4 - the symlinked directory the audit followed and never reported
# ==========================================================================

def test_a_symlinked_directory_is_never_descended(tmp_path):
    outside = tmp_path / "payload"
    outside.mkdir()
    (outside / "pwn.md").write_text("payload\n", encoding="utf-8")

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "real.md").write_text("vouched\n", encoding="utf-8")
    (cache / "docs-link").symlink_to(outside, target_is_directory=True)

    files, links = ha._walk_surface(cache)
    names = [p.name for p in files]
    assert "pwn.md" not in names, \
        "content outside the plugin root was walked as installed surface"
    assert "real.md" in names


def test_a_symlinked_directory_is_reported_by_name(tmp_path):
    """A directory has no surface suffix, which is how it slipped the filter."""
    outside = tmp_path / "payload"
    outside.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "docs-link").symlink_to(outside, target_is_directory=True)

    _files, links = ha._walk_surface(cache)
    assert [p.name for p in links] == ["docs-link"], \
        "an unvouchable directory link was neither followed nor reported"


def test_the_link_is_reported_even_when_its_target_is_empty(tmp_path):
    outside = tmp_path / "empty"
    outside.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "link").symlink_to(outside, target_is_directory=True)
    assert len(ha._walk_surface(cache)[1]) == 1


def test_a_symlinked_file_is_still_reported_and_not_hashed(tmp_path):
    outside = tmp_path / "payload.md"
    outside.write_text("payload\n", encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "innocent.md").symlink_to(outside)

    files, links = ha._walk_surface(cache)
    assert [p.name for p in files] == []
    assert [p.name for p in links] == ["innocent.md"]


def test_real_nested_content_is_still_collected(tmp_path):
    cache = tmp_path / "cache"
    (cache / "plug" / "hooks").mkdir(parents=True)
    (cache / "plug" / "SKILL.md").write_text("x\n", encoding="utf-8")
    (cache / "plug" / "hooks" / "run.py").write_text("x\n", encoding="utf-8")

    files, links = ha._walk_surface(cache)
    assert sorted(p.name for p in files) == ["SKILL.md", "run.py"]
    assert links == []


def test_pruned_directories_are_still_pruned(tmp_path):
    cache = tmp_path / "cache"
    (cache / "node_modules" / "dep").mkdir(parents=True)
    (cache / "node_modules" / "dep" / "index.js").write_text("x\n", encoding="utf-8")
    (cache / "keep.md").write_text("x\n", encoding="utf-8")

    files, _links = ha._walk_surface(cache)
    assert [p.name for p in files] == ["keep.md"]


def test_a_symlink_inside_a_pruned_directory_is_not_reported(tmp_path):
    """Pruning wins over reporting, or PRUNED_DIRS would be noise."""
    outside = tmp_path / "payload"
    outside.mkdir()
    cache = tmp_path / "cache"
    (cache / "node_modules").mkdir(parents=True)
    (cache / "node_modules" / "link").symlink_to(outside, target_is_directory=True)

    files, links = ha._walk_surface(cache)
    assert files == [] and links == []


def test_a_missing_root_is_empty_rather_than_an_error(tmp_path):
    assert ha._walk_surface(tmp_path / "nope") == ([], [])


def test_the_walk_returns_a_stable_sorted_order(tmp_path):
    """A manifest diff must be a diff of content, not of walk order."""
    cache = tmp_path / "cache"
    (cache / "zeta").mkdir(parents=True)
    (cache / "alpha").mkdir()
    for rel in ("zeta/z.md", "alpha/a.md", "m.md"):
        (cache / rel).write_text("x\n", encoding="utf-8")

    files = ha._walk_surface(cache)[0]
    assert files == sorted(files), "the walk order leaked into the result"
    assert [p.name for p in files] == ["a.md", "m.md", "z.md"]


def test_the_walk_never_resolves_a_link_it_reports(tmp_path):
    """The reported path must be the link, not what it points at."""
    outside = tmp_path / "payload"
    outside.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "docs-link").symlink_to(outside, target_is_directory=True)

    link = ha._walk_surface(cache)[1][0]
    assert link.parent == cache, "the link was resolved out of the plugin root"
    assert link.is_symlink()
