#!/usr/bin/env python3
"""A batch that aborted, and four sentences the sender could not have known.

`scripts/send-email.py` is the workspace's only outbound path, so a defect here
costs a real message to a real person. Nine were found on 2026-08-25 and are
pinned below. Four are worth naming, because they are the ones that could have
been acted on:

`build_file_attachments` called ``sys.exit(1)`` on a bad path. SystemExit
derives from BaseException, so neither `except Exception` in `_send_email_core`
caught it, and it was raised before either of them ran. A five-message batch
with a typo in message three delivered two, then died with no per-message
result and no ``[BATCH]`` summary. Worse in practice: `send_batch` passed
``m.get("attach")`` through with no normalisation, so ``"attach": "/tmp/f.pdf"``
iterated the string one CHARACTER at a time, and the first character of an
absolute path is ``/``, which exists and is a directory. The operator got an
uncaught ``IsADirectoryError`` naming a path they never typed.

`send_email` printed "The draft was saved but NOT sent" over all four failure
paths, including the one where a ReadTimeout means the message may ALREADY be
out. On that path `_UNSURE_NOTE` prints one line above saying exactly that, and
then the last line the operator reads contradicts it. Acting on the last line
sends a second copy of an irreversible message, which is the duplicate the
`_SAFE_TO_RESEND` design exists to prevent.

`--dry-run` returned before the mode-level validation, so
``--dry-run --reply --body x`` with no ``--match-*`` printed the dry-run block
and exited 0, while the same command without ``--dry-run`` exited 2. The flag's
help text says it exists because "checking that a flag parses meant sending a
real message" - and it could not check the modes that matter. The sharpest case
is the ``--cc`` refusal, which exists because cc/bcc were once silently
DISCARDED on an irreversible send: it was the one refusal a dry run could not
see.

`--match-folder` had no validation, so an unknown name fell through to the
Inbox. ``--match-folder Drafts`` searched the Inbox, replied into whatever
thread it found there, and on a miss reported "No message found in Drafts" -
naming the folder it never opened. Both halves are fixed together here; fixing
only the fall-through would have left that message correct by accident.

NOTE ON METHOD: every test in this file drives pure functions or fakes. Nothing
here reaches Exchange, reads a credential, or spawns the CLI without
``--dry-run``. `send-email.py` refuses to send at all when
``PYTEST_CURRENT_TEST`` is set, and that refusal is verified by
`tests/test_send_body_never_reaches_argv.py`, not weakened here.
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SEND_EMAIL = ROOT / "scripts" / "send-email.py"


def _stub_exchangelib():
    """Stub exchangelib only when genuinely absent. Mirrors the guard in
    tests/test_send_email_contract.py, including removing the stub again.

    A module already in `sys.modules` used to be accepted on faith. A STUB left
    behind by an earlier module that failed to clean up is also already in
    `sys.modules`, and then `send-email.py` was exec'd against a fake this
    fixture neither built nor controls - and the `se` fixture's teardown only
    pops what `stubbed` says it installed, so the foreign stub travelled on. A
    real package has a spec with an origin; a `types.ModuleType` has neither, so
    the two are distinguishable and the ambiguous case is refused rather than
    trusted.
    """
    existing = sys.modules.get("exchangelib")
    if existing is not None:
        spec = getattr(existing, "__spec__", None)
        if getattr(existing, "__file__", None) or getattr(spec, "origin", None):
            return False
        raise AssertionError(
            "sys.modules['exchangelib'] is a stub this fixture did not create "
            "(no __file__, no spec origin). Some earlier test module leaked it; "
            "exec'ing send-email.py against it would test an uncontrolled fake."
        )
    try:
        importlib.import_module("exchangelib")
        return False
    except ImportError:
        pass
    stub = types.ModuleType("exchangelib")
    for attr in ("Account", "Credentials", "Configuration", "DELEGATE",
                 "FileAttachment", "HTMLBody", "Message", "Mailbox"):
        setattr(stub, attr, None)
    sys.modules["exchangelib"] = stub
    return True


@pytest.fixture(scope="module")
def se():
    stubbed = _stub_exchangelib()
    try:
        spec = importlib.util.spec_from_file_location("send_email_p3", _SEND_EMAIL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        if stubbed:
            sys.modules.pop("exchangelib", None)


def _cli(*args):
    """Run the real CLI. Every caller passes --dry-run, which returns before
    load_config() reads a credential and before connect() opens a session."""
    return subprocess.run(
        [sys.executable, str(_SEND_EMAIL), *args],
        capture_output=True, text=True, cwd=str(ROOT), timeout=120,
    )


# ============================================================
# Finding 1 - a bad attachment killed the run
# ============================================================

def test_a_missing_attachment_raises_instead_of_exiting(se, tmp_path):
    """The whole finding in one line: no SystemExit escapes this function."""
    with pytest.raises(se.AttachmentError):
        se.build_file_attachments([str(tmp_path / "nope.pdf")])


def test_the_missing_attachment_error_is_not_a_systemexit(se, tmp_path):
    """SystemExit derives from BaseException, which is WHY the two
    `except Exception` handlers in _send_email_core never caught the old exit."""
    try:
        se.build_file_attachments([str(tmp_path / "nope.pdf")])
    except BaseException as exc:  # noqa: BLE001 - the type IS the assertion
        assert not isinstance(exc, SystemExit)
        assert isinstance(exc, Exception)
    else:
        pytest.fail("a missing attachment raised nothing at all")


def test_the_error_names_the_path_that_was_missing(se, tmp_path):
    missing = tmp_path / "quarterly-report.pdf"
    with pytest.raises(se.AttachmentError, match="quarterly-report.pdf"):
        se.build_file_attachments([str(missing)])


def test_a_missing_path_is_refused_by_the_check_not_by_the_read(se, tmp_path):
    """"not found" and "unreadable" are different diagnoses.

    Without the `is_file()` check the read still fails - FileNotFoundError is an
    OSError, so the guard below catches it and raises AttachmentError all the
    same. The operator then gets "unreadable" for a path that simply is not
    there, and goes looking at permissions.
    """
    with pytest.raises(se.AttachmentError, match="not found"):
        se.build_file_attachments([str(tmp_path / "nope.pdf")])


def test_a_directory_is_refused_rather_than_read(se, tmp_path):
    """`p.exists()` was true for a directory, so the old code fell through to
    `read_bytes()` and raised an uncaught IsADirectoryError.

    Asserted on the WORDING for the same reason as the test above: `exists()`
    would still end in an AttachmentError, just via the read guard and with the
    wrong diagnosis.
    """
    with pytest.raises(se.AttachmentError, match="not found"):
        se.build_file_attachments([str(tmp_path)])


def test_a_bare_string_attach_is_one_path_not_a_string_of_characters(se, tmp_path):
    """The likely trigger. Iterating "/tmp/f.pdf" character by character starts
    at "/", which exists and is a directory."""
    real = tmp_path / "one.txt"
    real.write_text("x", encoding="utf-8")
    missing = str(tmp_path / "gone.txt")
    with pytest.raises(se.AttachmentError) as caught:
        se.build_file_attachments(missing)
    # The message names the operator's path, never a single character.
    assert "gone.txt" in str(caught.value)
    assert "attachment not found: /\n" not in str(caught.value)


def test_an_unreadable_attachment_is_refused_not_a_traceback(se, tmp_path):
    """An existing file whose bytes cannot be read is UNKNOWN, not fine."""
    blocked = tmp_path / "locked.pdf"
    blocked.write_bytes(b"data")
    blocked.chmod(0o000)
    try:
        if blocked.read_bytes():  # running as root: the chmod does not bite
            pytest.skip("this user can read a 000 file, so the branch is unreachable")
    except OSError:
        pass
    else:
        pytest.skip("this user can read a 000 file, so the branch is unreachable")
    finally:
        blocked.chmod(0o600)
    blocked.chmod(0o000)
    try:
        with pytest.raises(se.AttachmentError, match="unreadable"):
            se.build_file_attachments([str(blocked)])
    finally:
        blocked.chmod(0o600)


def test_no_attachments_is_still_an_empty_list(se):
    assert se.build_file_attachments(None) == []
    assert se.build_file_attachments([]) == []


def test_the_core_returns_a_failure_dict_for_a_bad_attachment(se, tmp_path):
    """The batch contract is one status dict per message. This is the seam the
    old sys.exit broke: the run died before any dict was appended."""
    result = se._send_email_core(
        account=object(), to=["a@example.com"], subject="s", body="b",
        attach=[str(tmp_path / "nope.pdf")], signature="SIG",
    )
    assert result["status"] == "failed"
    assert result["stage"] == "attachments"
    assert result["to"] == ["a@example.com"]
    assert "nope.pdf" in result["error"]


def test_the_bad_attachment_failure_says_nothing_was_sent(se, tmp_path):
    """This stage runs before msg.save(), so no draft exists and nothing left."""
    result = se._send_email_core(
        account=object(), to=["a@example.com"], subject="s", body="b",
        attach=[str(tmp_path / "nope.pdf")], signature="SIG",
    )
    assert "nothing was sent" in result["error"]


def test_the_threaded_core_also_returns_a_dict_for_a_bad_attachment(se, tmp_path):
    """The same call sat unguarded in _send_threaded_core, so the reply path
    died the same way."""
    result = se._send_threaded_core(
        account=object(), mode="reply", original=object(), body="b",
        to=["a@example.com"], attach=[str(tmp_path / "nope.pdf")], signature="SIG",
    )
    assert result["status"] == "failed"
    assert result["stage"] == "attachments"


def test_the_source_carries_no_sys_exit_in_the_attachment_builder(se):
    """Structural, because the behavioural tests above pass on any raise. The
    old defect was specifically an exit, and only in this function."""
    import ast
    src = _SEND_EMAIL.read_text(encoding="utf-8")
    tree = ast.parse(src)
    target = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "build_file_attachments")
    exits = [n for n in ast.walk(target)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "exit"]
    assert not exits, "build_file_attachments exits again instead of raising"


# ============================================================
# Finding 2 - the batch loop guarded three fields of seven
# ============================================================

def _batch(se, messages):
    """Drive send_batch with a load_signature that reads no file and a core
    that records rather than sends. Nothing here touches Exchange."""
    calls = []
    orig_sig = se.load_signature
    orig_bsa = se.build_signature_attachments
    orig_core = se._send_email_core
    se.load_signature = lambda: "SIG"
    se.build_signature_attachments = list
    se._send_email_core = lambda **kw: (
        calls.append(kw)
        or {"to": kw["to"], "status": "sent", "stage": "sent", "error": None})
    try:
        return se.send_batch(object(), messages), calls
    finally:
        se.load_signature = orig_sig
        se.build_signature_attachments = orig_bsa
        se._send_email_core = orig_core


@pytest.mark.parametrize("bad,why", [
    ("a string, not a dict", "m['to'] raises TypeError, which was not caught"),
    ({"subject": "s", "body": "b"}, "no 'to' key at all"),
    ({"to": None, "subject": "s", "body": "b"}, "'to' normalises to None"),
    ({"to": [], "subject": "s", "body": "b"}, "'to' is an empty list"),
    ({"to": 42, "subject": "s", "body": "b"}, "'to' is neither str nor list"),
    ({"to": "a@e.com", "cc": 42, "subject": "s", "body": "b"}, "cc was normalised BELOW the try"),
    ({"to": "a@e.com", "bcc": 42, "subject": "s", "body": "b"}, "bcc, likewise"),
    ({"to": "a@e.com", "subject": 123, "body": "b"}, "a non-string subject"),
    ({"to": "a@e.com", "subject": "s", "body": 123}, "a non-string body reaches re.sub"),
    ({"to": "a@e.com", "subject": "s", "body": None}, "a null body, likewise"),
    ({"to": "a@e.com", "subject": "s", "body": ["a"]}, "a list body, likewise"),
])
def test_one_malformed_message_costs_only_that_message(se, bad, why):
    results, calls = _batch(se, [
        {"to": "first@example.com", "subject": "s", "body": "b"},
        bad,
        {"to": "third@example.com", "subject": "s", "body": "b"},
    ])
    assert len(results) == 3, f"the batch aborted: {why}"
    assert results[0]["status"] == "sent"
    assert results[1]["status"] == "failed"
    assert results[1]["stage"] == "malformed"
    assert results[2]["status"] == "sent", "message three never ran"
    assert [c["to"] for c in calls] == [["first@example.com"], ["third@example.com"]]


def test_a_malformed_message_reports_which_field(se):
    results, _ = _batch(se, [{"to": "a@e.com", "subject": "s", "body": 123}])
    assert "body" in results[0]["error"]


def test_a_well_formed_batch_still_passes_every_field_through(se, tmp_path):
    results, calls = _batch(se, [{
        "to": ["a@example.com", "b@example.com"],
        "cc": "c@example.com",
        "bcc": ["d@example.com"],
        "subject": "Subject",
        "body": "Body",
        "attach": [str(tmp_path)],
    }])
    assert len(calls) == 1
    assert calls[0]["to"] == ["a@example.com", "b@example.com"]
    assert calls[0]["cc"] == ["c@example.com"]
    assert calls[0]["bcc"] == ["d@example.com"]
    assert calls[0]["attach"] == [str(tmp_path)]
    assert results[0]["status"] == "sent"


def test_an_empty_batch_sends_nothing_and_returns_nothing(se):
    results, calls = _batch(se, [])
    assert results == [] and calls == []


# ============================================================
# Finding 3 - plain-text line breaks vanished
# ============================================================

def test_a_single_newline_survives_as_a_line_break(se):
    """HTML collapses a bare newline inside a <p> to one space, so an address
    block typed as separate lines arrived as one run-on line."""
    out = se._build_full_html("Line one\nLine two", "SIG")
    assert "<p>Line one<br>Line two</p>" in out
    assert "Line one\nLine two" not in out


def test_a_blank_line_still_starts_a_new_paragraph(se):
    out = se._build_full_html("P1\n\nP2", "SIG")
    assert "<p>P1</p><p>P2</p>" in out


def test_extra_blank_lines_do_not_become_empty_paragraphs(se):
    """A body typed with a double gap, or with padding at either end, must not
    ship `<p></p>` tags to the recipient."""
    out = se._build_full_html("\n\nP1\n\n\n\nP2\n\n", "SIG")
    assert "<p>P1</p><p>P2</p>" in out
    assert "<p></p>" not in out


def test_escaping_happens_before_the_line_break_is_inserted(se):
    """If the order flipped, a body containing "<br>" as TEXT would become a
    real tag - an HTML injection through the plain-text path."""
    out = se._build_full_html("a<b\nc&d", "SIG")
    assert "a&lt;b<br>c&amp;d" in out


def test_an_angle_bracket_beside_a_line_break_stays_escaped(se):
    """The <br> substitution runs on the ESCAPED string, so operator text can
    never contribute a real tag through the plain-text path.

    Note the body chosen: a literal "<br>" is not usable here, because
    `is_html` matches `<[a-zA-Z/][^>]*>` and routes any body containing one
    down the pass-through branch instead. That is pre-existing behaviour of
    `is_html`, unrelated to this shard, and it is named rather than changed.
    """
    out = se._build_full_html("5 < 6\n7 > 4", "SIG")
    assert "<p>5 &lt; 6<br>7 &gt; 4</p>" in out


def test_a_crlf_body_leaves_no_stray_carriage_return(se):
    out = se._build_full_html("A\r\nB", "SIG")
    assert "<p>A<br>B</p>" in out
    assert "\r" not in out


def test_real_html_is_still_passed_through_untouched(se):
    out = se._build_full_html("<p>Hello</p>", "SIG")
    assert "<p>Hello</p>" in out
    assert "&lt;p&gt;" not in out
    assert "<br>Hello" not in out


def test_the_signature_is_still_appended_last(se):
    assert se._build_full_html("body", "SIGBLOCK").endswith("SIGBLOCK")


# ============================================================
# Finding 4 - the dry run skipped the contract it advertised
# ============================================================

@pytest.mark.parametrize("argv,expect", [
    (["--dry-run", "--reply", "--body", "x"],
     "requires one of --match-id"),
    (["--dry-run", "--reply-all", "--match-subject", "s"],
     "requires --body"),
    (["--dry-run", "--forward", "--body", "x", "--match-subject", "s"],
     "--forward requires --to"),
    (["--dry-run", "--reply", "--body", "x", "--match-subject", "s", "--cc", "a@e.com"],
     "not supported with --reply"),
    (["--dry-run", "--reply", "--body", "x", "--match-subject", "s", "--bcc", "a@e.com"],
     "not supported with --reply"),
    (["--dry-run"],
     "either --batch or all of --to"),
    (["--dry-run", "--to", "a@e.com"],
     "either --batch or all of --to"),
])
def test_an_invalid_invocation_is_refused_under_dry_run(argv, expect):
    """Each of these printed the DRY-RUN block and exited 0 before 2026-08-25,
    while the same command without --dry-run exited 2."""
    proc = _cli(*argv)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert expect in proc.stderr
    assert "[DRY-RUN]" not in proc.stdout


@pytest.mark.parametrize("argv", [
    ["--dry-run", "--to", "a@e.com", "--subject", "s", "--body", "b"],
    ["--dry-run", "--reply", "--body", "x", "--match-subject", "s"],
    ["--dry-run", "--reply", "--body", "x", "--match-id", "AAMk"],
    ["--dry-run", "--forward", "--body", "x", "--match-subject", "s", "--to", "a@e.com"],
    ["--dry-run", "--reply", "--body", "x", "--match-subject", "s", "--match-folder", "Sent"],
    ["--dry-run", "--batch", "anything.json"],
])
def test_a_valid_invocation_still_reports_and_sends_nothing(argv):
    """The guard must not have become a wall. --batch is here on purpose: the
    batch FILE checks stay below the guard, so a missing file is not an
    argument error and the dry run still succeeds."""
    proc = _cli(*argv)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[DRY-RUN] nothing was sent." in proc.stdout


def test_the_dry_run_still_reads_no_credential():
    """The guard moved down past the new checks; it must still sit above
    load_config(). Asserted structurally so no .env is needed."""
    src = _SEND_EMAIL.read_text(encoding="utf-8")
    guard = src.index("if args.dry_run:")
    assert guard < src.index("config = load_config()")
    assert guard < src.index("account = connect(config)")


def test_the_checks_are_not_written_twice(se):
    """Two copies drift. The mode checks moved above the guard and must not
    have been left behind below it."""
    src = _SEND_EMAIL.read_text(encoding="utf-8")
    assert src.count("threaded mode requires one of --match-id") == 1
    assert src.count('parser.error("--forward requires --to') == 1
    assert src.count("either --batch or all of --to, --subject, --body are required") == 1


def test_the_guard_comment_no_longer_claims_what_it_did_not_do(se):
    """The comment was the only statement of the feature's purpose."""
    src = _SEND_EMAIL.read_text(encoding="utf-8")
    assert "after every argparse check and after the body has" not in src


# ============================================================
# Finding 5 - a sign-off at the start of the body was kept
# ============================================================

def test_a_body_that_is_only_a_signoff_is_stripped(se):
    """`\\n` is a literal, never an anchor, so the old pattern could not match
    a body that BEGINS with the sign-off - while both HTML patterns could."""
    assert se.strip_trailing_signoff("Best,\nMisha") == ""


def test_the_html_shape_is_still_stripped(se):
    assert se.strip_trailing_signoff("<p>Best,<br>Misha</p>") == ""


def test_the_ordinary_trailing_signoff_still_goes(se):
    assert se.strip_trailing_signoff("Hello there\n\nBest,\nMisha") == "Hello there"


def test_a_bare_signoff_with_no_name_is_preserved(se):
    """Documented behaviour: "Best," alone is not a doubled signature."""
    assert se.strip_trailing_signoff("Hi\n\nBest,") == "Hi\n\nBest,"


def test_body_text_that_merely_mentions_regards_is_untouched(se):
    body = "Regards were sent to the board and they replied."
    assert se.strip_trailing_signoff(body) == body


def test_a_signoff_in_the_middle_of_the_body_is_left_alone(se):
    """The pattern is anchored at the END with \\Z. Only a TRAILING sign-off
    doubles the branded signature; one quoted mid-body is the operator's text."""
    body = "Hi\n\nBest,\nMisha\n\nOne more thing: the invoice is attached."
    assert se.strip_trailing_signoff(body) == body


def test_the_keyword_and_the_name_must_be_on_separate_lines(se):
    """Pre-existing behaviour, pinned rather than changed: an inline
    "Best, Misha" is left in place. Only the leading-anchor half of this
    pattern was touched on 2026-08-25."""
    body = "Hello there\n\nBest, Misha"
    assert se.strip_trailing_signoff(body) == body


def test_only_the_first_matching_pattern_strips(se):
    """The loop breaks on the first pattern that changes the string, so a body
    carrying two sign-offs loses exactly one. Without the break the HTML shape
    would go and the plain-text one below it would go too, taking a line of the
    operator's real text with it."""
    body = "Text\n\nBest,\nMisha<p>Regards,<br>Anna</p>"
    assert se.strip_trailing_signoff(body) == "Text\n\nBest,\nMisha"


# ============================================================
# Finding 6 - the pre-flight comment named the case it misses
# ============================================================

def test_a_missing_signature_asset_only_warns(se, capsys, monkeypatch):
    """The comment claimed a missing asset "fails once rather than N times".
    It fails zero times: both branches print a WARN and fall through."""
    monkeypatch.setattr(se, "logo_path", lambda p=Path("/nonexistent/logo.png"): p)
    monkeypatch.setattr(se, "divider_path", lambda p=Path("/nonexistent/divider.png"): p)
    monkeypatch.setattr(se, "_ensure_exchangelib", lambda: None)
    assert se.build_signature_attachments() == []
    out = capsys.readouterr().out
    assert "[WARN] Logo not found" in out
    assert "[WARN] Divider not found" in out


def test_the_preflight_comment_no_longer_claims_a_missing_asset_fails(se):
    src = _SEND_EMAIL.read_text(encoding="utf-8")
    assert "a missing asset fails once rather than N times" not in src
    assert "It does NOT catch a MISSING asset" in src


def test_the_preflight_call_is_still_there(se):
    """It is NOT dead code. It is the only thing that catches an asset that
    EXISTS and cannot be read (an unpulled Git-LFS pointer, a permissions
    error) before message 1, because read_bytes() raises out of it."""
    import ast
    src = _SEND_EMAIL.read_text(encoding="utf-8")
    tree = ast.parse(src)
    target = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "send_batch")
    names = [n.func.id for n in ast.walk(target)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "build_signature_attachments" in names


def test_an_unreadable_signature_asset_really_does_raise(se, tmp_path, monkeypatch):
    """The case the comment now names. Proven, not asserted."""
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"x")
    logo.chmod(0o000)
    try:
        logo.read_bytes()
    except OSError:
        pass
    else:
        logo.chmod(0o600)
        pytest.skip("this user can read a 000 file, so the branch is unreachable")
    monkeypatch.setattr(se, "logo_path", lambda p=logo: p)
    monkeypatch.setattr(se, "_ensure_exchangelib", lambda: None)
    try:
        with pytest.raises(OSError):
            se.build_signature_attachments()
    finally:
        logo.chmod(0o600)


# ============================================================
# Finding 7 - a fallback that repeated the query that just failed
# ============================================================

class _FakeFolder:
    def __init__(self, name, item=None, error=None):
        self.name = name
        self._item = item
        self._error = error
        self.gets = 0

    def get(self, **kwargs):
        self.gets += 1
        if self._error is not None:
            raise self._error
        return self._item


class _FakeAccount:
    def __init__(self, inbox, sent):
        self.inbox = inbox
        self.sent = sent


def test_a_failed_inbox_id_lookup_is_not_retried_on_the_inbox(se):
    """Under the default --match-folder Inbox the "fallback" WAS the inbox, so
    it repeated the identical query and called the repeat a fallback."""
    inbox = _FakeFolder("inbox", error=RuntimeError("no such id"))
    account = _FakeAccount(inbox, _FakeFolder("sent"))
    assert se.find_message(account, match_id="AAMk", folder_name="Inbox") is None
    assert inbox.gets == 1, "the same folder was queried twice"


def test_a_failed_sent_id_lookup_does_fall_back_to_the_inbox(se):
    """When a DIFFERENT folder was searched, a second chance is real."""
    sent = _FakeFolder("sent", error=RuntimeError("not here"))
    inbox = _FakeFolder("inbox", item="THE-ITEM")
    account = _FakeAccount(inbox, sent)
    assert se.find_message(account, match_id="AAMk", folder_name="Sent") == "THE-ITEM"
    assert sent.gets == 1 and inbox.gets == 1


def test_a_successful_id_lookup_never_reaches_the_fallback(se):
    inbox = _FakeFolder("inbox", item="THE-ITEM")
    account = _FakeAccount(inbox, _FakeFolder("sent"))
    assert se.find_message(account, match_id="AAMk", folder_name="Inbox") == "THE-ITEM"
    assert inbox.gets == 1


def test_the_id_lookup_failure_is_reported_not_swallowed(se, capsys):
    """`except Exception: return None` lost the reason, so the caller's error
    message could not tell "no such id" from "EWS refused the request"."""
    inbox = _FakeFolder("inbox", error=RuntimeError("EWS refused"))
    account = _FakeAccount(inbox, _FakeFolder("sent"))
    se.find_message(account, match_id="AAMk", folder_name="Inbox")
    assert "EWS refused" in capsys.readouterr().out


def test_both_failures_are_reported_when_the_fallback_also_fails(se, capsys):
    sent = _FakeFolder("sent", error=RuntimeError("first reason"))
    inbox = _FakeFolder("inbox", error=RuntimeError("second reason"))
    account = _FakeAccount(inbox, sent)
    assert se.find_message(account, match_id="AAMk", folder_name="Sent") is None
    out = capsys.readouterr().out
    assert "first reason" in out and "second reason" in out


def test_the_comment_no_longer_claims_an_account_root_lookup(se):
    """`account.root` is not referenced anywhere in this file."""
    src = _SEND_EMAIL.read_text(encoding="utf-8")
    assert "cross-folder lookup by id via the account root" not in src
    assert "account.root" not in src.replace(
        "`account.root` is not referenced", "")


# ============================================================
# Finding 8 - one guidance line over four different outcomes
# ============================================================

def test_every_failing_stage_has_its_own_guidance(se):
    """DERIVED from the code, not listed here.

    This asserted a hardcoded four-name set, which pins the invariant only for
    as long as nobody adds a stage -- and then reports the addition as a
    failure instead of reporting an UNGUIDED stage. A `validation` stage was
    added on 2026-08-30 (a missing --to and an unknown mode were both stamped
    `attachments`, so the guidance told the operator to fix a path on a failure
    that involved no path) and this test failed for the one reason that is not
    a defect. Walk the two senders for the stage literals they can return and
    require each to have an entry; `sent` is the success value and needs none.
    """
    import ast

    tree = ast.parse(_SEND_EMAIL.read_text(encoding="utf-8"))
    returned = set()
    for name in ("_send_email_core", "_send_threaded_core"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        for node in ast.walk(fn):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values, strict=True):
                if (isinstance(key, ast.Constant) and key.value == "stage"
                        and isinstance(value, ast.Constant)):
                    returned.add(value.value)

    assert returned, "no stage literals found; the AST walk is binding nothing"
    unguided = returned - {"sent"} - set(se._STAGE_GUIDANCE)
    assert not unguided, f"stages a send can return with no guidance: {sorted(unguided)}"
    # And nothing in the table is dead: a guidance line no stage returns is a
    # sentence the operator can never be shown. `malformed` is send_batch's own.
    orphans = set(se._STAGE_GUIDANCE) - returned - {"malformed"}
    assert not orphans, f"guidance entries no stage returns: {sorted(orphans)}"


def test_the_send_stage_does_not_claim_the_message_was_not_sent(se):
    """The dangerous one. On a ReadTimeout _UNSURE_NOTE prints one line above
    saying the mail may already be out; the old blanket line then said it was
    not, and acting on it duplicates an irreversible message."""
    guidance = se._STAGE_GUIDANCE["send"]
    assert "NOT sent" not in guidance
    assert "Sent Items" in guidance


def test_the_attach_stage_still_points_at_the_saved_draft(se):
    """This is the ONE stage the old sentence was right about."""
    assert "draft was saved but NOT sent" in se._STAGE_GUIDANCE["attach"]


def test_the_save_draft_stage_does_not_promise_a_draft(se):
    """It must not promise a draft, and it must not promise the ABSENCE of one.

    Rewritten 2026-08-26. This asserted the literal "No draft was saved", on the
    reasoning that `msg.save()` is the first persistence call so a failure there
    means nothing exists. That holds for a REFUSAL and not for a read timeout:
    the stage is stamped on every exception out of `save()`, and a timeout on
    the CreateItem call establishes only that the ANSWER did not come back. The
    item may well be on the server. Asserting otherwise is the over-claim
    `.claude/rules/scope-claims.md` names, and this same file already reasons
    exactly this way about the `send` stage one test below.

    What the stage really establishes is that nothing was SENT.
    """
    guidance = se._STAGE_GUIDANCE["save_draft"]

    assert "Nothing was sent" in guidance
    assert "UNKNOWN" in guidance
    assert "No draft was saved" not in guidance


def test_the_attachments_stage_says_nothing_left_the_machine(se):
    assert "Nothing was saved and nothing was sent" in se._STAGE_GUIDANCE["attachments"]


def test_an_unstamped_result_reports_the_state_as_unknown(se, capsys):
    """Fail toward over-reporting. A result from somewhere new must not be
    guessed into one of the four."""
    orig = se._send_email_core
    se._send_email_core = lambda **kw: {
        "to": ["a@e.com"], "status": "failed", "error": "something new"}
    try:
        with pytest.raises(SystemExit) as exc:
            se.send_email(object(), ["a@e.com"], "s", "b")
    finally:
        se._send_email_core = orig
    assert exc.value.code == 1
    assert "UNKNOWN" in capsys.readouterr().out


@pytest.mark.parametrize("stage", ["attachments", "save_draft", "attach", "send"])
def test_send_email_prints_the_guidance_for_the_stage_it_got(se, capsys, stage):
    orig = se._send_email_core
    se._send_email_core = lambda **kw: {
        "to": ["a@e.com"], "status": "failed", "stage": stage, "error": "boom"}
    try:
        with pytest.raises(SystemExit):
            se.send_email(object(), ["a@e.com"], "s", "b")
    finally:
        se._send_email_core = orig
    assert se._STAGE_GUIDANCE[stage] in capsys.readouterr().out


def test_send_email_still_exits_one_on_failure(se, capsys):
    """The single-message CLI contract is unchanged."""
    orig = se._send_email_core
    se._send_email_core = lambda **kw: {
        "to": ["a@e.com"], "status": "failed", "stage": "send", "error": "boom"}
    try:
        with pytest.raises(SystemExit) as exc:
            se.send_email(object(), ["a@e.com"], "s", "b")
    finally:
        se._send_email_core = orig
    assert exc.value.code == 1


def test_send_email_is_silent_and_returns_on_success(se, capsys):
    orig = se._send_email_core
    se._send_email_core = lambda **kw: {
        "to": ["a@e.com"], "status": "sent", "stage": "sent", "error": None}
    try:
        assert se.send_email(object(), ["a@e.com"], "s", "b") is None
    finally:
        se._send_email_core = orig
    assert "[ERROR]" not in capsys.readouterr().out


# --- the stamps themselves, driven through the real core ---
#
# The guidance tests above stub `_send_email_core` wholesale, so they pin the
# table and not the stamps. These drive the real function against a fake
# Exchange so each stage is produced by the code that has to produce it.

class _FakeMessage:
    """Stands in for exchangelib.Message. Each failure is opt-in."""

    def __init__(self, fail_on=None, send_error=None, **kwargs):
        self.fail_on = fail_on
        self.send_error = send_error
        self.sends = 0

    def save(self):
        if self.fail_on == "save":
            raise RuntimeError("EWS rejected the draft")

    def attach(self, attachment):
        if self.fail_on == "attach":
            raise RuntimeError("attachment too large")

    def send(self):
        self.sends += 1
        if self.fail_on == "send":
            raise self.send_error


@pytest.fixture
def core(se, monkeypatch):
    """`_send_email_core` with every exchangelib name replaced. Returns a
    callable taking the fake-message settings."""
    made = []

    def _run(fail_on=None, send_error=None):
        def _message(**kwargs):
            msg = _FakeMessage(fail_on=fail_on, send_error=send_error, **kwargs)
            made.append(msg)
            return msg
        monkeypatch.setattr(se, "_ensure_exchangelib", lambda: None)
        monkeypatch.setattr(se, "Message", _message)
        monkeypatch.setattr(se, "HTMLBody", lambda x: x)
        monkeypatch.setattr(se, "Mailbox", lambda email_address: email_address)
        # One inline image, so the attach loop actually runs. With an empty
        # list `msg.attach` is never called and the attach stage is unreachable.
        monkeypatch.setattr(se, "build_signature_attachments", lambda: [object()])
        monkeypatch.setattr(se, "_autolog_to", lambda *a, **k: None)
        account = types.SimpleNamespace(drafts=object())
        result = se._send_email_core(
            account=account, to=["a@example.com"], subject="s", body="b",
            signature="SIG")
        return result, made

    return _run


def test_a_failed_draft_save_is_stamped_save_draft(core):
    """msg.save() is the first persistence call, so nothing exists yet."""
    result, _ = core(fail_on="save")
    assert result["status"] == "failed"
    assert result["stage"] == "save_draft"
    assert "save draft failed" in result["error"]


def test_a_failed_attach_is_stamped_attach(core):
    """The draft IS saved by this point. This is the one stage where "the draft
    was saved but NOT sent" is a true sentence."""
    result, _ = core(fail_on="attach")
    assert result["status"] == "failed"
    assert result["stage"] == "attach"


def test_an_unsafe_send_failure_is_stamped_send(core):
    """A ReadTimeout does not prove the message never left, so the guidance for
    this stage must not claim it was not sent."""
    class ReadTimeout(Exception):
        pass
    result, made = core(fail_on="send", send_error=ReadTimeout("no answer"))
    assert result["stage"] == "send"
    assert made[0].sends == 1, "an unsafe failure must not be retried"
    assert "Check Sent Items" in result["error"]


def test_a_safe_send_failure_retries_and_is_still_stamped_send(core):
    """Three attempts, then the exhausted return - which also needs a stage."""
    class ConnectTimeout(Exception):
        pass
    result, made = core(fail_on="send", send_error=ConnectTimeout("refused"))
    assert result["stage"] == "send"
    assert made[0].sends == 3
    assert "after 3 attempts" in result["error"]


def test_a_clean_send_is_stamped_sent(core):
    result, made = core()
    assert result == {"to": ["a@example.com"], "status": "sent",
                      "stage": "sent", "error": None}
    assert made[0].sends == 1


@pytest.mark.parametrize("fail_on,expected", [
    ("save", "save_draft"), ("attach", "attach"), (None, "sent"),
])
def test_every_core_result_carries_a_stage(core, fail_on, expected):
    """A result with no stage falls to the UNKNOWN guidance, which is safe but
    useless. Every return the core can produce has to be stamped."""
    result, _ = core(fail_on=fail_on)
    assert result.get("stage") == expected


def test_the_blanket_guidance_line_is_gone_from_the_call_sites(se):
    """It was printed unconditionally in TWO places: send_email and main's
    threaded branch."""
    src = _SEND_EMAIL.read_text(encoding="utf-8")
    assert 'print("         The draft was saved but NOT sent. Check Exchange drafts folder.")' not in src
    assert "The draft may have been saved but NOT sent" not in src


# ============================================================
# Finding 9 - an unknown folder name searched a different folder
# ============================================================

@pytest.mark.parametrize("name,expected", [
    ("Inbox", "inbox"), ("inbox", "inbox"), ("  INBOX  ", "inbox"), (None, "inbox"),
    ("", "inbox"), ("Sent", "sent"), ("sent items", "sent"), ("SentItems", "sent"),
])
def test_a_known_folder_name_resolves(se, name, expected):
    assert se.folder_key(name) == expected


@pytest.mark.parametrize("name", ["Drafts", "Snet", "Archive", "sent-items", "junk"])
def test_an_unknown_folder_name_is_refused_not_defaulted(se, name):
    """It fell through to the Inbox, so a typo replied into whatever thread was
    newest in the Inbox - on an irreversible outbound action."""
    with pytest.raises(ValueError, match="unknown folder"):
        se.folder_key(name)


def test_the_refusal_lists_the_folders_that_do_work(se):
    with pytest.raises(ValueError) as caught:
        se.folder_key("Drafts")
    assert "inbox" in str(caught.value) and "sent" in str(caught.value)


def test_resolve_folder_maps_to_the_account_attribute(se):
    account = _FakeAccount(_FakeFolder("inbox"), _FakeFolder("sent"))
    assert se._resolve_folder(account, "Inbox") is account.inbox
    assert se._resolve_folder(account, "Sent") is account.sent


def test_resolve_folder_refuses_an_unknown_name(se):
    account = _FakeAccount(_FakeFolder("inbox"), _FakeFolder("sent"))
    with pytest.raises(ValueError):
        se._resolve_folder(account, "Drafts")


def test_an_unknown_folder_is_refused_before_a_credential_is_read():
    """The refusal must land at argument-parse time, not after connecting."""
    proc = _cli("--dry-run", "--reply", "--body", "x",
                "--match-subject", "s", "--match-folder", "Drafts")
    assert proc.returncode == 2
    assert "unknown folder" in proc.stderr


def _not_found_folder_expression(src: str) -> str:
    """What the "No message found in ..." line interpolates, as source.

    A local alias is resolved: `folder = folder_key(args.match_folder)` followed
    by `f"... {folder} ..."` reports the same folder to the operator, and the
    literal pin this replaces went red on that behaviour-preserving refactor
    while a rewording of the sentence around it went undetected.
    """
    tree = ast.parse(src)
    aliases = {
        t.id: node.value
        for node in ast.walk(tree) if isinstance(node, ast.Assign)
        for t in node.targets if isinstance(t, ast.Name)
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr) or len(node.values) < 2:
            continue
        head = node.values[0]
        if not (isinstance(head, ast.Constant) and isinstance(head.value, str)
                and "No message found in " in head.value):
            continue
        slot = node.values[1]
        assert isinstance(slot, ast.FormattedValue), ast.dump(node)
        expr = slot.value
        if isinstance(expr, ast.Name) and expr.id in aliases:
            expr = aliases[expr.id]
        return ast.unparse(expr)
    raise AssertionError(
        "no f-string beginning 'No message found in ' exists in send-email.py; "
        "the miss path was reworded and this guard stopped covering it")


def test_the_not_found_message_names_the_folder_that_was_searched(se):
    """It echoed the string the operator typed. Under --match-folder Drafts it
    searched the Inbox and reported "No message found in Drafts"."""
    reported = _not_found_folder_expression(
        _SEND_EMAIL.read_text(encoding="utf-8"))
    assert reported != "args.match_folder", (
        "the miss reports the string the operator typed, not the folder that "
        "was opened")
    assert reported == "folder_key(args.match_folder)", reported


# ============================================================
# The send gate itself is untouched by all of the above
# ============================================================

def test_the_test_run_refusal_is_still_in_place():
    """Nine fixes landed in the workspace's only outbound path. The refusal
    that keeps a test off the wire must be exactly where it was."""
    src = _SEND_EMAIL.read_text(encoding="utf-8")
    assert 'os.environ.get("PYTEST_CURRENT_TEST") and not args.dry_run' in src
    refusal = src.index('os.environ.get("PYTEST_CURRENT_TEST")')
    assert refusal < src.index("config = load_config()")
    assert refusal < src.index("if args.dry_run:")


def test_the_resend_safety_set_is_unchanged(se):
    """`_SAFE_TO_RESEND` decides whether a failed send is retried. Nothing in
    this shard had any business widening it."""
    assert frozenset({
        "ConnectTimeout", "NewConnectionError", "ErrorServerBusy",
        "RateLimitError"}) == se._SAFE_TO_RESEND


def test_a_read_timeout_is_still_not_safe_to_resend(se):
    class ReadTimeout(Exception):
        pass
    assert se._is_safe_to_resend(ReadTimeout()) is False
