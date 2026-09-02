#!/usr/bin/env python3
"""The documented `/queue-draft` workflow used to end at a placeholder address.

`/queue-draft` deposits an `email_send` card whose recipient falls back to a
reserved documentation address when none is given, and its own skill body tells
the operator to correct that address before approving. There was no way to do it.
`action-queue.py edit` accepted `--subject` and `--body-file` and nothing else,
`approve` sends synchronously, and every skill in the tree is told never to write
`queue.json` by hand. So the workflow ended either at a real message addressed to
`someone@example.com` or at a card the operator could not move.

Measured against the pre-fix source (bound at .tmp/bind/qd/): `edit --to` exited
2 on an unrecognised argument, and `approve` on a placeholder card ran the send
transport and reported `sent`.

Two halves, and the negative one is the point:
  - `edit --to` corrects the recipient from the terminal, browser closed;
  - `approve` REFUSES a card still holding a placeholder, attempts no send, and
    leaves the card approvable once the address is fixed.

The lethal-trifecta control is untouched here: this adds a refusal, never a
send path. `_Sender(refuse=True)` raises if the transport is reached at all, so a
regression that sends where it must not fails loudly rather than mailing anyone.
"""

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import recipient

_spec_x = importlib.util.spec_from_file_location(
    "aqx_placeholder", ROOT / "scripts" / "action-queue-execute.py")
aqx = importlib.util.module_from_spec(_spec_x)
_spec_x.loader.exec_module(aqx)

PLACEHOLDER = "someone@example.com"


def _load_aq_cli():
    """Import scripts/action-queue.py by path (hyphenated, not dotted-importable)."""
    spec = importlib.util.spec_from_file_location(
        "aqcli_placeholder", ROOT / "scripts" / "action-queue.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Sender:
    """Stands in for the send transport. `refuse=True` makes any send a failure."""

    def __init__(self, refuse=False):
        self.calls = []
        self.refuse = refuse

    def __call__(self, *a, **k):
        self.calls.append((a, k))
        if self.refuse:
            raise AssertionError(
                "the transport ran on a path that must never send: "
                f"argv={a[0] if a else None!r}")
        return _FakeProc(0)


def _card(**over):
    c = {"id": "ph00001", "action_type": "email_send", "status": "pending",
         "to": PLACEHOLDER, "subject": "Draft from /queue-draft",
         "draft_body": "A body the operator wrote.",
         "draft_status": "ready_for_review"}
    c.update(over)
    return c


def _queue(td, *cards):
    data_root = Path(td)
    qdir = data_root / "outputs" / "operations" / "action-queue"
    qdir.mkdir(parents=True)
    path = qdir / "queue.json"
    path.write_text(json.dumps({"version": 1, "generated_at": None,
                                "actions": list(cards)}), encoding="utf-8")
    return data_root, path


def _card_on_disk(path, action_id):
    q = json.loads(path.read_text(encoding="utf-8"))
    return next(c for c in q["actions"] if c["id"] == action_id)


class _Args:
    """argparse.Namespace stand-in for cmd_edit, with every flag it reads."""

    def __init__(self, card_id, to=None, subject=None, body_file=None):
        # The ATTRIBUTE stays `id`, because that is argparse's dest and what
        # `cmd_edit` reads. Only the parameter is renamed, so this double does
        # not shadow the builtin inside its own constructor.
        self.id = card_id
        self.to = to
        self.subject = subject
        self.body_file = body_file


# ---------------------------------------------------------------------------
# The negative case: approve refuses a placeholder and sends nothing.
# ---------------------------------------------------------------------------

def test_approve_refuses_a_card_still_addressed_to_the_placeholder():
    aqcli = _load_aq_cli()
    sender = _Sender(refuse=True)
    with tempfile.TemporaryDirectory() as td:
        data_root, path = _queue(td, _card())
        orig = aqcli._AQX.subprocess.run
        try:
            aqcli._AQX.subprocess.run = sender
            res = aqcli.approve_and_send(ROOT, data_root, "ph00001")
        finally:
            aqcli._AQX.subprocess.run = orig
        status = _card_on_disk(path, "ph00001")["status"]
    assert res.get("result") == "refused", res
    assert "example.com" in (res.get("error") or ""), res
    assert "--to" in (res.get("error") or ""), \
        "the refusal must name the command that fixes it"
    assert sender.calls == [], "no send may be attempted"
    assert status == "pending", \
        "the claim must go back so the card is approvable once corrected"


def test_send_card_refuses_the_placeholder_before_any_transport_runs():
    """The gate sits in the one function that can send, so the batch executor
    inherits it from the same copy rather than a second one."""
    called = []
    orig = aqx.subprocess.run
    try:
        aqx.subprocess.run = lambda *a, **k: called.append(a) or _FakeProc(0)
        res = aqx.send_card(ROOT, _card(status="approved"))
    finally:
        aqx.subprocess.run = orig
    assert res["result"] == "refused", res
    assert res["classification"] == "none", res
    assert called == [], "the transport must not be reached"


def test_a_refused_recipient_is_not_recorded_as_a_send_failure():
    """`refused`, never `send_failed`. A send_failure offers `retry`, and
    retrying a send that never ran cannot succeed while the address is wrong."""
    aqcli = _load_aq_cli()
    sender = _Sender(refuse=True)
    with tempfile.TemporaryDirectory() as td:
        data_root, path = _queue(td, _card())
        orig = aqcli._AQX.subprocess.run
        try:
            aqcli._AQX.subprocess.run = sender
            aqcli.approve_and_send(ROOT, data_root, "ph00001")
        finally:
            aqcli._AQX.subprocess.run = orig
        assert _card_on_disk(path, "ph00001")["status"] != "send_failed"


# ---------------------------------------------------------------------------
# The positive case: the recipient is correctable from the terminal.
# ---------------------------------------------------------------------------

def test_edit_to_corrects_the_recipient_and_then_approve_sends():
    aqcli = _load_aq_cli()
    sender = _Sender()
    with tempfile.TemporaryDirectory() as td:
        data_root, path = _queue(td, _card())
        rc = aqcli.cmd_edit(ROOT, data_root,
                            _Args("ph00001", to="hana.velos@northreef.test"))
        assert rc == 0
        assert _card_on_disk(path, "ph00001")["to"] == "hana.velos@northreef.test"
        orig = aqcli._AQX.subprocess.run
        try:
            aqcli._AQX.subprocess.run = sender
            res = aqcli.approve_and_send(ROOT, data_root, "ph00001")
        finally:
            aqcli._AQX.subprocess.run = orig
    assert res.get("result") == "sent", res
    assert len(sender.calls) == 1
    argv = sender.calls[0][0][0]
    assert "hana.velos@northreef.test" in argv, argv
    assert PLACEHOLDER not in argv, "the corrected address must be the one sent to"


def test_edit_to_reaches_the_queue_store_and_leaves_the_body_alone():
    """`--to` on its own is a complete edit; it must not require a body rewrite."""
    aqcli = _load_aq_cli()
    with tempfile.TemporaryDirectory() as td:
        data_root, path = _queue(td, _card(draft_body="Keep this body."))
        rc = aqcli.cmd_edit(ROOT, data_root,
                            _Args("ph00001", to="ops@northreef.test"))
        assert rc == 0
        card = _card_on_disk(path, "ph00001")
    assert card["to"] == "ops@northreef.test"
    assert card["draft_body"] == "Keep this body."
    assert card["subject"] == "Draft from /queue-draft"
    assert card["draft_status"] == "ready_for_review"


def test_edit_with_no_flags_at_all_is_still_a_usage_error():
    aqcli = _load_aq_cli()
    with tempfile.TemporaryDirectory() as td:
        data_root, _ = _queue(td, _card())
        assert aqcli.cmd_edit(ROOT, data_root, _Args("ph00001")) == 1


def test_edit_refuses_correcting_one_placeholder_into_another():
    """The keystroke-time check. Without it the operator learns at approve."""
    aqcli = _load_aq_cli()
    with tempfile.TemporaryDirectory() as td:
        data_root, path = _queue(td, _card())
        rc = aqcli.cmd_edit(ROOT, data_root,
                            _Args("ph00001", to="recipient@example.org"))
        assert rc == 1
        assert _card_on_disk(path, "ph00001")["to"] == PLACEHOLDER, \
            "a refused recipient must not be written to the card"


def test_the_edit_parser_accepts_to():
    """Guards the flag itself. Pre-fix this raised SystemExit(2)."""
    aqcli = _load_aq_cli()
    with tempfile.TemporaryDirectory() as td:
        data_root, _ = _queue(td, _card())
        parsed = []
        orig = aqcli.cmd_edit
        try:
            aqcli.cmd_edit = lambda e, d, a: parsed.append(a.to) or 0
            aqcli.main(["edit", "ph00001", "--to", "ana.brekke@northreef.test"])
        finally:
            aqcli.cmd_edit = orig
    assert parsed == ["ana.brekke@northreef.test"], parsed


# ---------------------------------------------------------------------------
# The validator's own boundary. Refusing too much is a failure here too.
# ---------------------------------------------------------------------------

def test_the_validator_refuses_the_shapes_a_placeholder_actually_takes():
    for bad in ("someone@example.com", "someone@example.org",
                "anyone@sub.example.com", "ops@northreef.example",
                "root@localhost", "recipient@northreef.test",
                "changeme@northreef.test", "<ana@northreef.test>",
                "not-an-address", "two@@northreef.test",
                "ana brekke@northreef.test", "", None):
        assert recipient.refusal_reason(bad) is not None, bad
        assert recipient.is_sendable(bad) is False, bad


def test_the_validator_passes_ordinary_addresses():
    for good in ("misha.hanin@odinix.com", "hana.velos@northreef.test",
                 "ops+queue@northreef.test", "a.b-c_d@mail.northreef.travel",
                 "  ana.brekke@northreef.test  "):
        assert recipient.refusal_reason(good) is None, good
        assert recipient.is_sendable(good) is True, good


def test_the_reserved_testing_tlds_are_deliberately_left_sendable():
    """A decision, not a gap, and it is load-bearing for the corpus.

    `tests/test_send_body_never_reaches_argv.py` addresses its send fixtures at
    `.test` because a reserved TLD is what kept three accidental real sends from
    reaching a person. Refusing `.test` here would push every future fixture
    toward a plausible invented domain, which is a domain somebody may own. If
    this assertion is ever flipped, read that file first.
    """
    assert recipient.refusal_reason("x@b.test") is None
    assert recipient.refusal_reason("harriet.vane@northreef.invalid") is None


def test_the_queue_draft_placeholder_is_the_one_this_guard_refuses():
    """Ties the guard to the skill that produces the placeholder. If the skill
    changes its fallback address, this fails rather than going quietly blind."""
    skill = (ROOT / ".claude" / "skills" / "queue-draft" / "SKILL.md").read_text(
        encoding="utf-8")
    assert PLACEHOLDER in skill, \
        "/queue-draft no longer documents this placeholder - retarget the guard"
    assert recipient.refusal_reason(PLACEHOLDER) is not None


def test_the_queue_skills_document_the_flag_that_exists():
    """The CLI and the skill bodies must agree, which is half of the defect."""
    for rel in (".claude/skills/queue/SKILL.md",
                ".claude/skills/queue-draft/SKILL.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "--to" in text, f"{rel} does not name the recipient-correction flag"
