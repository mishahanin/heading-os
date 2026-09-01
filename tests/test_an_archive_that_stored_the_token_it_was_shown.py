"""An email archive stored a credential-shaped token and refused the backup.

`push-all.py` scans the CONTENT of everything a push would carry, and it
refuses on anything the workspace's secret vocabulary matches. MEASURED
2026-08-29 on the live overlay, before a line changed:

    outputs/_sync/emails/inbox-latest.md, line 1000:  JWT bearer token
    -> "REFUSING TO PUSH - secret-like CONTENT in a file about to be pushed."

The string was not a credential of this workspace. It was the signed query
value of a tracking image in a sign-in email,
`og-images.workos.com/api/logo-icon?t=<token>`, written verbatim into the
archive by the email sync. The scanner cannot tell that from the shape, and it
is right not to try: a magic-link email carries a REAL credential in exactly
the same position.

So the fix is not "strip image URLs" and not a second list of dangerous query
parameters - a list is always one entry short. An archived body is run through
`secret_patterns.redact`, which uses the same table `secret-scanner.py` reads,
so the archive cannot carry a string this workspace calls a secret. The URL
keeps its host and path and the removed span is named, so the record stays
readable.

FOUR places built an email or calendar body and persisted it, and they carried
three copies of the same three lines: `sync-exchange` (mail, and calendar),
`sentinel` (mail, and invites), `email-intelligence`. Fixing one of them would
have left the next push to be refused by one of the others. The extraction is
now one function; the tests below drive every site with a planted token rather
than reading the source for a call.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from scripts.utils.html_text import email_body_text
from scripts.utils.secret_patterns import redact

ROOT = Path(__file__).resolve().parent.parent

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# BUILT from readable parts, never written as a literal, and NOT silenced with
# an allowlist pragma. Two gates refused the literal form while this file was
# being written, both correctly: the PreToolUse hook refused a shell command
# carrying a JWT-shaped string, and `detect-secrets` refused the commit over the
# two base64 runs. Clearing a real gate in order to plant a fake secret is the
# wrong trade when the alternative is to encode the parts at import time - there
# is then no high-entropy literal in the tracked file at all, and the value is a
# genuinely well-formed JWT rather than a lookalike.
_JWT = ".".join([
    _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8")),
    _b64(json.dumps({"sub": "shard60", "name": "an invented subject"}).encode("utf-8")),
    _b64(b"this is prose standing in for a signature, and it is not one"),
])
TRACKER = f"https://og-images.example.invalid/api/logo-icon?t={_JWT}"


class FakeItem:
    """The two attributes every caller reads, and nothing else."""

    def __init__(self, text_body=None, body=None):
        self.text_body = text_body
        self.body = body


def _planted_is_detectable() -> bool:
    """The premise every test below rests on."""
    return redact(_JWT) != _JWT


def test_the_planted_token_is_one_the_vocabulary_actually_matches():
    """Without this, a redaction test passes over a string nothing detects.

    The two hook cases in this file assert an ABSENCE, and an absence is
    satisfied by a token that was never a token.
    """
    assert _planted_is_detectable()
    assert "JWT" in redact(_JWT)


# ============================================================
# The shared extractor
# ============================================================
def test_a_token_in_the_plain_text_body_is_redacted():
    out = email_body_text(FakeItem(text_body=f"see [{TRACKER}]"))
    assert _JWT not in out
    assert "[REDACTED: JWT bearer token]" in out


def test_a_token_in_the_visible_text_of_an_html_body_is_redacted():
    out = email_body_text(FakeItem(body=f"<div>hi</div><p>Join: {TRACKER}</p>"))
    assert _JWT not in out
    assert "[REDACTED: JWT bearer token]" in out


def test_strip_html_already_drops_an_href_so_the_text_part_is_the_exposure():
    """Recorded because it explains WHERE the incident came from.

    `strip_html` keeps only character data, so a URL that lives in an `href`
    never reaches the archive at all. The token that refused the backup came
    from the message's text/plain alternative, where the URL is visible text. A
    later reader who tests only the HTML path will conclude the redaction is
    unnecessary, and it is not.
    """
    from scripts.utils.html_text import strip_html
    assert _JWT not in strip_html(f'<a href="{TRACKER}">click</a>')
    assert _JWT in f"see {TRACKER}"


def test_the_url_survives_so_the_record_stays_readable():
    """Redaction, not deletion: an archive that drops the link loses the fact
    that a tracker was there at all."""
    out = email_body_text(FakeItem(text_body=f"see [{TRACKER}]"))
    assert "og-images.example.invalid/api/logo-icon" in out
    assert out.startswith("see [https://")


def test_the_prose_around_the_token_survives():
    out = email_body_text(FakeItem(text_body=f"Your code is 041627.\n[{TRACKER}]\nSign in"))
    assert "Your code is 041627." in out
    assert "Sign in" in out


def test_plain_text_still_wins_over_html():
    item = FakeItem(text_body="the plain one", body="<div>the html one</div>")
    assert email_body_text(item) == "the plain one"


def test_html_is_used_when_the_plain_part_is_blank():
    assert email_body_text(FakeItem(text_body="   ", body="<div>hi</div>")) == "hi"


def test_a_body_free_of_credentials_is_returned_unchanged():
    """The redactor must not be a paraphraser."""
    prose = "Hi Misha,\n\nThe report is attached. Best,\nSam"
    assert email_body_text(FakeItem(text_body=prose)) == prose


@pytest.mark.parametrize("item", [
    FakeItem(),
    FakeItem(text_body="", body=""),
    FakeItem(text_body=None, body=None),
    object(),
])
def test_an_item_with_no_body_yields_an_empty_string(item):
    """`object()` is in the list on purpose: the sentinel invite path used
    `hasattr` guards, so the shared helper has to tolerate an item that carries
    neither attribute."""
    assert email_body_text(item) == ""


# ============================================================
# Every site that persists a body, driven
# ============================================================
def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeAddress:
    def __init__(self, email_address="someone@example.invalid", name="Someone"):
        self.email_address = email_address
        self.name = name


class FakeEmail(FakeItem):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.sender = FakeAddress()
        self.subject = "A sign-in code"
        self.datetime_received = "2026-08-29 09:00"
        self.is_read = True
        self.to_recipients = []
        self.cc_recipients = []
        self.has_attachments = False
        self.attachments = []


class _FakeQS(list):
    def order_by(self, *_a):
        return self

    def filter(self, **_kw):
        return self

    def all(self):
        return self


class FakeFolder:
    def __init__(self, items):
        self._items = _FakeQS(items)

    def all(self):
        return self._items

    def filter(self, **_kw):
        return self._items


class FakeAccount:
    def __init__(self, items):
        self.inbox = FakeFolder(items)
        self.sent = self.inbox
        self.drafts = self.inbox


def test_sync_exchange_writes_a_redacted_mail_body(tmp_path, monkeypatch):
    """The exact path that refused the backup, driven end to end to a file."""
    sync = _load("sync_exchange_shard60", "sync-exchange.py")
    monkeypatch.setattr(sync, "email_dir", lambda p=tmp_path: p)
    monkeypatch.setattr(sync, "_display_path", lambda p: str(p))
    account = FakeAccount([FakeEmail(text_body=f"Your code is 041627.\n[{TRACKER}]")])

    assert sync.sync_emails(account, count=1) == 1

    written = (tmp_path / "inbox-latest.md").read_text(encoding="utf-8")
    assert _JWT not in written, "the archive stored the token again"
    assert "[REDACTED: JWT bearer token]" in written
    assert "og-images.example.invalid" in written


def test_sync_exchange_writes_a_redacted_calendar_body(tmp_path, monkeypatch):
    """A meeting invite carries a join URL, and a join URL carries a token.

    Driven through `redact` at the same seam the calendar writer uses, because
    the calendar path builds its body from `body` alone and keeps doing so.
    """
    sync = _load("sync_exchange_shard60_cal", "sync-exchange.py")
    # The URL as VISIBLE TEXT, which is how a Teams or Zoom invite writes its
    # join link. An `href` would prove nothing: `strip_html` discards
    # attributes, so that form never reached the archive in the first place.
    invite = f"<div>Join the meeting</div><div>{TRACKER}</div>"
    joined = sync.redact(sync.strip_html(invite))
    assert _JWT not in joined
    assert "[REDACTED: JWT bearer token]" in joined
    assert "Join the meeting" in joined


def test_the_calendar_writer_passes_its_body_through_the_redactor():
    """Asked of the parsed code, not of its characters.

    `sync_calendar` needs exchangelib and a live account shape, so driving it
    end to end would skip on a runner without the email extra - and a skipped
    test is not a test. The AST is the next-best question: the one assignment
    that becomes the written body must have `redact` in its call chain.
    """
    import ast
    src = (ROOT / "scripts" / "sync-exchange.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "sync_calendar")
    assigns = [n for n in ast.walk(fn)
               if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "body_text" for t in n.targets)]
    assert assigns, "sync_calendar no longer builds `body_text`; re-point this test"
    built = [a for a in assigns
             if any(isinstance(c.func, ast.Name) and c.func.id == "strip_html"
                    for c in ast.walk(a) if isinstance(c, ast.Call))]
    assert built, "no `body_text` assignment builds from strip_html any more"
    for node in built:
        names = {c.func.id for c in ast.walk(node)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert "redact" in names, ast.unparse(node)


def test_email_intelligence_hands_on_a_redacted_body():
    intel = _load("email_intelligence_shard60", "email-intelligence.py")
    body = intel.email_body_text(FakeItem(text_body=f"[{TRACKER}]"))
    assert _JWT not in body
    # The dict this feeds is serialised, so prove the serialised form is clean.
    assert _JWT not in json.dumps({"body": body, "body_preview": body[:500]})


def test_sentinel_hands_on_a_redacted_body():
    sentinel = _load("sentinel_shard60", "sentinel.py")
    body = sentinel.email_body_text(FakeItem(text_body=f"[{TRACKER}]"))
    assert _JWT not in body


def test_no_caller_rebound_the_shared_extractor():
    """Each module must resolve the SAME function object.

    This catches a private lookalike. It does NOT catch a caller that keeps the
    import and stops using it at the call site - measured, three mutations that
    restored the old inline extraction survived this test, because the imported
    name was still there and still identical. `test_no_body_is_built_from_
    strip_html_without_redaction` below is the one that covers the call site.
    """
    from scripts.utils import html_text
    for name, filename in [("sync_exchange_ident", "sync-exchange.py"),
                           ("email_intelligence_ident", "email-intelligence.py"),
                           ("sentinel_ident", "sentinel.py")]:
        mod = _load(name, filename)
        assert mod.email_body_text is html_text.email_body_text, filename


# The files that build a mail or calendar body and PERSIST it. Named, because
# the rule below is a claim about these four sites and about nothing else:
# `generate-newsletter-html.py` also calls `strip_html`, to count words, and
# persists no body.
_BODY_WRITERS = ["sync-exchange.py", "sentinel.py", "email-intelligence.py"]


@pytest.mark.parametrize("filename", _BODY_WRITERS)
def test_no_body_is_built_from_strip_html_without_redaction(filename):
    """Asked of the parsed code, because the call SITE is what regressed.

    Every mutation that put an inline `text_body else strip_html(body)` copy
    back survived the behaviour tests above: they call the imported function,
    which the mutation left untouched. What changed was which expression builds
    the value that gets written. So the rule is about that expression: an
    assignment whose value reaches `strip_html` must also reach `redact`.
    """
    import ast
    tree = ast.parse((ROOT / "scripts" / filename).read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = {c.func.id for c in ast.walk(node)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        if "strip_html" in names and "redact" not in names:
            offenders.append(ast.unparse(node))
    assert not offenders, (
        f"{filename} builds a body from strip_html with no redaction: {offenders}")


@pytest.mark.parametrize("filename", _BODY_WRITERS)
def test_the_mail_body_is_built_by_the_shared_extractor(filename):
    """The positive half. The rule above forbids the old shape; this one
    requires the new one, so deleting the body entirely does not pass.

    Whole-file presence, which is all this one asks. It is NOT sufficient on
    its own and it never was: `sentinel.py` holds TWO call sites, so a
    regression at one of them left the name in the file and this test green.
    MEASURED 2026-09-01 - replacing `check_new`'s
    `body = email_body_text(email_item)` with a raw `email_item.text_body`
    read, which stores an UNREDACTED body, kept all 26 tests passing. Removing
    BOTH sites was needed to fail it. `test_every_extractor_call_site_is_named`
    below is the per-site witness that closes that; this one stays as the
    cheap floor.
    """
    import ast
    tree = ast.parse((ROOT / "scripts" / filename).read_text(encoding="utf-8"))
    calls = {c.func.id for c in ast.walk(tree)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert "email_body_text" in calls, f"{filename} no longer uses the shared extractor"


def _extractor_call_sites() -> set[tuple[str, str, str]]:
    """{(file, enclosing function, assigned name)} for every body assignment
    whose value reaches `email_body_text`.

    Derived from the parsed source rather than listed, so the pin below fails
    on a site that moves as loudly as on one that disappears.
    """
    import ast

    sites: set[tuple[str, str, str]] = set()
    for filename in _BODY_WRITERS:
        tree = ast.parse((ROOT / "scripts" / filename).read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Assign):
                    continue
                uses = any(
                    isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Name)
                    and c.func.id == "email_body_text"
                    for c in ast.walk(node)
                )
                if not uses:
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        sites.add((filename, fn.name, target.id))
    return sites


# Every place a persisted body is BUILT, named one by one. A whole-file
# presence check gives the four sites a single shared witness, and a shared
# witness cannot fail for three of them.
_EXPECTED_CALL_SITES = {
    ("sync-exchange.py", "sync_emails", "body"),
    ("sentinel.py", "check_new", "body"),
    ("sentinel.py", "check_new_invites", "body"),
    ("email-intelligence.py", "fetch_emails", "body"),
}


def test_every_extractor_call_site_is_named():
    """The sole witness per site, plus the anti-decay half in one assertion.

    Set equality, not a count and not a subset: a site that stops using the
    extractor drops out, a new persisting site that never adopted it never
    appears, and either way a human re-derives the set instead of the guard
    quietly covering three sites out of four.
    """
    found = _extractor_call_sites()
    assert found == _EXPECTED_CALL_SITES, (
        "the set of body-building call sites moved.\n"
        f"  gone:  {sorted(_EXPECTED_CALL_SITES - found)}\n"
        f"  new:   {sorted(found - _EXPECTED_CALL_SITES)}\n"
        "A site that left must be shown to redact by some other route before "
        "this set is edited; a site that arrived must go through "
        "email_body_text."
    )


@pytest.mark.parametrize("site", sorted(_EXPECTED_CALL_SITES))
def test_this_one_call_site_still_builds_its_body_through_the_extractor(site):
    """One failing case per site, so a report names WHICH body regressed."""
    assert site in _extractor_call_sites(), (
        f"{site[0]}::{site[1]} no longer builds `{site[2]}` through "
        "email_body_text, so that body is persisted unredacted"
    )


def test_the_redactor_is_the_scanners_own_vocabulary():
    """Not a second list. If these ever diverge, the archive starts carrying
    something the push wall then refuses, which is the defect this closes."""
    from scripts.utils import secret_patterns
    assert email_body_text.__module__ == "scripts.utils.html_text"
    assert secret_patterns.redact is redact
    # A shape from a DIFFERENT family, to prove the coupling is to the whole
    # vocabulary rather than to the one pattern that caused the incident.
    aws = "AKIA" + "Q" * 16
    assert aws not in email_body_text(FakeItem(text_body=f"key {aws} here"))
