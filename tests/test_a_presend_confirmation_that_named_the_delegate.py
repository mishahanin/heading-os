"""The pre-send confirmation named the delegate; the reply went to the author.

EWS carries two addresses on a message. `author` is the From header; `sender`
is `message:Sender`, the mailbox that SUBMITTED the item -- an assistant or a
delegate. exchangelib addresses a reply to `author`. `_reply_target` exists in
this file because conflating the two once wrote a wrong fact into the CRM, and
the CRM log and the post-send `[OK]` line were both moved onto it.

The `[FOUND]` line was not. It is the LAST thing the operator reads before an
irreversible outbound action, and it printed `original.sender`.

MEASURED 2026-08-30 against a delegate-sent stub:

    [FOUND] printed   -> moneypenny@example.invalid   (the delegate)
    the reply reaches -> felix.leiter@example.invalid (the author)

Nothing here touches Exchange: `_found_line` is pure, and the stub objects
carry only the two mailbox attributes it reads.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "send_email_found_probe", ROOT / "scripts" / "send-email.py")
se = importlib.util.module_from_spec(_spec)
sys.modules["send_email_found_probe"] = se
_spec.loader.exec_module(se)

AUTHOR = "felix.leiter@example.invalid"
DELEGATE = "moneypenny@example.invalid"


class _Mailbox:
    def __init__(self, address):
        self.email_address = address


class _Message:
    def __init__(self, subject="Q3 numbers", author=None, sender=None):
        self.subject = subject
        self.author = _Mailbox(author) if author else None
        self.sender = _Mailbox(sender) if sender else None


def test_the_confirmation_names_the_author_of_a_delegate_sent_message():
    original = _Message(author=AUTHOR, sender=DELEGATE)

    line = se._found_line(original)

    assert AUTHOR in line, line
    assert DELEGATE not in line, (
        "the operator is shown the delegate and the mail goes to the author")


def test_the_confirmation_names_the_same_mailbox_the_reply_reaches():
    """Bind the two together, so they cannot drift apart again."""
    original = _Message(author=AUTHOR, sender=DELEGATE)

    target = se._reply_target(original)

    assert target is not None
    assert target.email_address in se._found_line(original)


def test_an_ordinary_message_reads_exactly_as_before():
    """The other direction: where the two agree, nothing about the line changes."""
    original = _Message(author=AUTHOR, sender=AUTHOR)

    assert se._found_line(original) == f"[FOUND] Q3 numbers from {AUTHOR}"


def test_a_sender_only_message_still_names_its_sender():
    """`_reply_target` falls back to sender; the line must not go blank."""
    original = _Message(author=None, sender=DELEGATE)

    assert DELEGATE in se._found_line(original)


def test_a_message_with_no_resolvable_mailbox_says_so():
    line = se._found_line(_Message(author=None, sender=None))

    assert line.endswith("from ?"), line


def test_a_message_with_no_subject_is_labelled_not_blank():
    line = se._found_line(_Message(subject="", author=AUTHOR))

    assert "(no subject)" in line


def test_the_threaded_branch_of_main_prints_this_line_and_reads_no_sender():
    """Wiring bind: the fix is worthless if `main` still formats its own line."""
    tree = ast.parse((ROOT / "scripts" / "send-email.py").read_text(encoding="utf-8"))
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")

    called = {ast.unparse(n.func) for n in ast.walk(main) if isinstance(n, ast.Call)}
    assert "_found_line" in called, (
        "main does not call _found_line, so the confirmation it prints is "
        "whatever it formats itself")

    sender_reads = [
        ast.unparse(n) for n in ast.walk(main)
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "getattr"
        and any(isinstance(a, ast.Constant) and a.value == "sender" for a in n.args)
    ]
    assert not sender_reads, f"main still reads .sender directly: {sender_reads}"
