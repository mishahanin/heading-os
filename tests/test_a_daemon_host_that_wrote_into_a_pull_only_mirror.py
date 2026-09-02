#!/usr/bin/env python3
"""The CRM auto-log rewrote contact cards on a host that only ever pulls.

MEASURED 2026-08-30 on the operator's Steward VM. That host executes scripts and
runs three daemons, and its clone of the private DATA repo is a PULL-ONLY
mirror: a sync script takes `git pull --ff-only` hourly and nothing there ever
commits or pushes. At 15:00:00 the `steward-fireside` daemon's `email-backup`
job shelled out to `scripts/send-email.py`; `_autolog_to` calls
`crm_autolog.log_outbound` for every recipient with no environment flag, no host
check and no mode switch; five contact cards were rewritten in the mirror's
working tree between 15:00:03 and 15:00:21. Every hourly `git pull --ff-only`
since then aborted on the dirty tree, so the mirror sat five commits behind for
about three and a half days while the sync script printed a warning, exited 0,
and systemd reported success.

`HEADING_OS_DATA_READONLY` is the control. These tests pin all three halves of
it, because each one alone is worthless:

* the guard STOPS the write (byte-identity, not "no new log line");
* the guard ANNOUNCES the skip, naming the record - a silent skip trades a
  visible frozen mirror for an invisible missing interaction;
* the guard is OFF by default, so it cannot pass the first two by being
  permanently on and breaking the operator's own workstation.
"""
import hashlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SEND_EMAIL = ROOT / "scripts" / "send-email.py"

CONTACT = "karl-mertens"
EMAIL = "karl@rivex.com"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def crm_tree(tmp_path):
    """A minimal address book plus one contact card, under `tmp_path`.

    Deliberately built inside the test's own `tmp_path` and never anywhere near
    the operator's live overlay: the whole subject of this file is a writer that
    reached a tree it had no business reaching.
    """
    crm = tmp_path / "crm"
    (crm / "address-book").mkdir(parents=True)
    (crm / "contacts").mkdir(parents=True)
    (crm / "address-book" / f"{CONTACT}.md").write_text(
        "---\n"
        f"slug: {CONTACT}\n"
        "name: Karl Mertens\n"
        f"canonical_email: {EMAIL}\n"
        "created: 2026-03-15\n"
        "---\n",
        encoding="utf-8",
    )
    (crm / "contacts" / f"{CONTACT}.md").write_text(
        "---\n"
        f"entity_ref: {CONTACT}\n"
        "last_touch: 2026-05-01\n"
        "owner: misha-hanin\n"
        "---\n\n"
        "## Interaction Log\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def card(crm_tree):
    return crm_tree / "crm" / "contacts" / f"{CONTACT}.md"


# ---------------------------------------------------------------------------
# log_outbound
# ---------------------------------------------------------------------------


def test_log_outbound_leaves_the_card_byte_identical_when_readonly(
        crm_tree, card, monkeypatch):
    """The defect itself: a write into a tree the host may only read from."""
    from scripts.utils.crm_autolog import log_outbound
    monkeypatch.setenv("HEADING_OS_DATA_READONLY", "1")

    before = _sha256(card)
    result = log_outbound(
        recipient_email=EMAIL,
        subject="Partnership terms",
        body_excerpt="Quick check on the pricing for tier 2.",
        date="2026-08-30",
        workspace_root=crm_tree,
    )

    assert result is False
    # sha256, not a substring absence. `bump_last_touch_in_text` rewrites a
    # frontmatter field in place, so a card can be modified without gaining a
    # single new line - and a modified card is exactly what aborts the pull.
    assert _sha256(card) == before


def test_log_outbound_announces_the_skip_and_names_the_record(
        crm_tree, monkeypatch, capsys):
    from scripts.utils.crm_autolog import log_outbound
    monkeypatch.setenv("HEADING_OS_DATA_READONLY", "1")

    log_outbound(EMAIL, "Partnership terms", "body", "2026-08-30",
                 workspace_root=crm_tree)

    err = capsys.readouterr().err
    assert "DATA_READONLY" in err
    assert CONTACT in err
    assert EMAIL in err
    assert "Partnership terms" in err


def test_log_outbound_still_writes_when_the_guard_is_absent(crm_tree, card):
    """The jaw. A guard that is always on passes every test above and silently
    stops the operator's own workstation from logging anything."""
    from scripts.utils.crm_autolog import log_outbound

    before = _sha256(card)
    assert log_outbound(EMAIL, "Partnership terms", "body", "2026-08-30",
                        workspace_root=crm_tree) is True
    assert _sha256(card) != before
    text = card.read_text(encoding="utf-8")
    assert "last_touch: 2026-08-30" in text
    assert "### 2026-08-30 | Email | Partnership terms" in text


def test_log_outbound_still_writes_when_the_guard_is_empty(
        crm_tree, card, monkeypatch):
    """`HEADING_OS_DATA_READONLY=` with nothing after it is the ordinary shape of
    a half-edited `.env` line, and it must not silence the auto-log."""
    from scripts.utils.crm_autolog import log_outbound
    monkeypatch.setenv("HEADING_OS_DATA_READONLY", "")

    before = _sha256(card)
    assert log_outbound(EMAIL, "Partnership terms", "body", "2026-08-30",
                        workspace_root=crm_tree) is True
    assert _sha256(card) != before


# ---------------------------------------------------------------------------
# bump_inbound
# ---------------------------------------------------------------------------


def test_bump_inbound_leaves_the_card_byte_identical_when_readonly(
        crm_tree, card, monkeypatch):
    from scripts.utils.crm_autolog import bump_inbound
    monkeypatch.setenv("HEADING_OS_DATA_READONLY", "yes")

    before = _sha256(card)
    result = bump_inbound(sender_email=EMAIL, date="2026-08-30",
                          workspace_root=crm_tree)

    assert result is False
    assert _sha256(card) == before


def test_bump_inbound_announces_the_skip_and_names_the_sender(
        crm_tree, monkeypatch, capsys):
    from scripts.utils.crm_autolog import bump_inbound
    monkeypatch.setenv("HEADING_OS_DATA_READONLY", "yes")

    bump_inbound(EMAIL, "2026-08-30", workspace_root=crm_tree)

    err = capsys.readouterr().err
    assert "DATA_READONLY" in err
    assert CONTACT in err
    assert EMAIL in err


def test_bump_inbound_still_writes_when_the_guard_is_absent(crm_tree, card):
    from scripts.utils.crm_autolog import bump_inbound

    before = _sha256(card)
    assert bump_inbound(EMAIL, "2026-08-30", workspace_root=crm_tree) is True
    assert _sha256(card) != before
    assert "last_touch: 2026-08-30" in card.read_text(encoding="utf-8")


def test_bump_inbound_still_writes_when_the_guard_is_empty(
        crm_tree, card, monkeypatch):
    from scripts.utils.crm_autolog import bump_inbound
    monkeypatch.setenv("HEADING_OS_DATA_READONLY", "")

    before = _sha256(card)
    assert bump_inbound(EMAIL, "2026-08-30", workspace_root=crm_tree) is True
    assert _sha256(card) != before


# ---------------------------------------------------------------------------
# The audit trail survives the skip
# ---------------------------------------------------------------------------


def test_the_skipped_send_is_still_recorded_in_the_audit_trail(
        crm_tree, monkeypatch):
    """`.sync/` is gitignored in the DATA repo (`/.sync/`, .gitignore line 23,
    confirmed with `git check-ignore -v` on 2026-09-02), so the JSONL trail does
    not dirty the tree and cannot be what aborts a fast-forward pull. Keeping it
    is what stops the guard from turning a frozen mirror into a lost record."""
    import json

    from scripts.utils.crm_autolog import log_outbound
    monkeypatch.setenv("HEADING_OS_DATA_READONLY", "1")

    log_outbound(EMAIL, "Partnership terms", "body", "2026-08-30",
                 workspace_root=crm_tree)

    logs = sorted((crm_tree / ".sync" / "logs").glob("crm-autolog-*.jsonl"))
    assert logs, "the skip left no audit entry at all"
    entries = [json.loads(line) for line in
               logs[-1].read_text(encoding="utf-8").splitlines() if line.strip()]
    skipped = [e for e in entries if e.get("skipped") == "data_readonly"]
    assert len(skipped) == 1
    assert skipped[0]["email"] == EMAIL
    assert skipped[0]["slug"] == CONTACT


# ---------------------------------------------------------------------------
# Truthiness, pinned deliberately
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", "yes", "YES",
                                   "on", " 1 "])
def test_these_values_arm_the_guard(value, monkeypatch):
    from scripts.utils.crm_autolog import data_is_readonly
    monkeypatch.setenv("HEADING_OS_DATA_READONLY", value)
    assert data_is_readonly() is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", "",
                                   "   "])
def test_these_values_leave_the_guard_down(value, monkeypatch):
    from scripts.utils.crm_autolog import data_is_readonly
    monkeypatch.setenv("HEADING_OS_DATA_READONLY", value)
    assert data_is_readonly() is False


def test_an_absent_variable_leaves_the_guard_down(monkeypatch):
    from scripts.utils.crm_autolog import data_is_readonly
    monkeypatch.delenv("HEADING_OS_DATA_READONLY", raising=False)
    assert data_is_readonly() is False


def test_an_unrecognised_value_arms_the_guard_and_says_so(monkeypatch, capsys):
    """Deliberate asymmetry. The name is absent on every ordinary host, so a
    non-empty value nobody recognises means somebody set it and misspelled it.
    Reading `treu` as OFF would leave a mirror mutating exactly as before, with
    the operator believing it was protected; reading it as ON costs an announced
    skip. `resolve_mode` in `scripts/utils/overlay_write_guard.py` made the same
    call for `HEADING_OS_OVERLAY_GUARD=recrod`."""
    from scripts.utils.crm_autolog import data_is_readonly
    monkeypatch.setenv("HEADING_OS_DATA_READONLY", "treu")
    assert data_is_readonly() is True
    assert "treu" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The real caller: send-email.py
# ---------------------------------------------------------------------------


def _stub_exchangelib():
    """Stub exchangelib ONLY when it is genuinely absent; report whether we did.

    Lifted from `tests/test_send_email_contract.py`, including its two fixes: the
    guard asks whether the package EXISTS rather than whether it has been
    imported, and the stub is removed afterwards so no later test in the same
    xdist worker gets Nones where classes belong.
    """
    if "exchangelib" in sys.modules:
        return False
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


@pytest.fixture
def send_email_mod():
    stubbed = _stub_exchangelib()
    try:
        spec = importlib.util.spec_from_file_location("send_email", _SEND_EMAIL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        if stubbed:
            sys.modules.pop("exchangelib", None)


def test_send_email_autolog_honours_the_guard(send_email_mod, crm_tree, card,
                                              monkeypatch, capsys):
    """Driven through the real `_autolog_to`, not a re-implementation of it.

    This is the exact frame the VM ran: `send-email.py` invoked as a subprocess
    by the fireside daemon, resolving the CRM through the data-root seam with no
    `workspace_root` argument anywhere in the chain. `_autolog_to` swallows every
    exception, so a guard that raised instead of returning would look identical
    to a guard that worked - the byte-identity assertion is what separates them.
    """
    monkeypatch.setenv("HEADING_OS_DATA", str(crm_tree))
    monkeypatch.setenv("HEADING_OS_DATA_READONLY", "1")

    before = _sha256(card)
    send_email_mod._autolog_to([EMAIL], "Fireside backup", "<p>body</p>")

    assert _sha256(card) == before
    err = capsys.readouterr().err
    assert "DATA_READONLY" in err
    assert CONTACT in err
    assert "crm_autolog skipped" not in err, "the guard raised instead of returning"


def test_send_email_autolog_still_writes_without_the_guard(
        send_email_mod, crm_tree, card, monkeypatch):
    """The same jaw, one layer up: prove the seam this test drives is live."""
    monkeypatch.setenv("HEADING_OS_DATA", str(crm_tree))
    monkeypatch.delenv("HEADING_OS_DATA_READONLY", raising=False)

    before = _sha256(card)
    send_email_mod._autolog_to([EMAIL], "Fireside backup", "<p>body</p>")

    assert _sha256(card) != before
    assert "Fireside backup" in card.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The second door: the bridge crm-log finalizer
# ---------------------------------------------------------------------------
#
# `scripts/bridge_daemon/finalizers/crm_log.py::log_to_crm` reaches the SAME
# contact file without going through `log_outbound` or `bump_inbound`: it calls
# `bump_last_touch_in_text` and `append_log_entry` directly and does its own
# `atomic_write`. Guarding only the two crm_autolog writers therefore left a
# dashboard crm-log click able to dirty a mirror host's tree exactly as the
# send path had. Found while reviewing the first guard; closed the same day.


CONV_ID = "AAQkAG-probe-conversation-0001"


@pytest.fixture
def inbox_fetch(crm_tree):
    """A `_latest-fetch.json` holding one conversation linked to the card."""
    import json

    from scripts.bridge_daemon.sources.inbox import LATEST_FETCH_FILE

    fetch = crm_tree / LATEST_FETCH_FILE
    fetch.parent.mkdir(parents=True, exist_ok=True)
    fetch.write_text(json.dumps({"conversations": [{
        "id": CONV_ID,
        "topic": "Partnership terms",
        "latest_datetime": "2026-08-30T15:00:00+00:00",
        "crm_context": {"contact_slug": CONTACT},
    }]}), encoding="utf-8")
    return crm_tree


def test_the_bridge_crm_log_refuses_on_a_mirror_host(inbox_fetch, card, monkeypatch):
    """The second writer must obey the same switch as the first."""
    from scripts.bridge_daemon.finalizers.crm_log import log_to_crm
    monkeypatch.setenv("HEADING_OS_DATA_READONLY", "1")
    before = _sha256(card)

    result = log_to_crm(CONV_ID, data_root=inbox_fetch)

    assert result["ok"] is False
    assert _sha256(card) == before, (
        "the bridge finalizer rewrote a contact card on a host whose data clone "
        "is a pull-only mirror; that is the write that froze the mirror for "
        "three and a half days, reached through a second door")


def test_the_bridge_refusal_says_why_and_names_the_record(inbox_fetch, monkeypatch):
    """The dashboard shows this string. "Failed" alone sends the operator
    hunting; the reason is the whole difference between a bug and a policy."""
    from scripts.bridge_daemon.finalizers.crm_log import log_to_crm
    monkeypatch.setenv("HEADING_OS_DATA_READONLY", "1")

    error = log_to_crm(CONV_ID, data_root=inbox_fetch)["error"]

    assert "HEADING_OS_DATA_READONLY" in error
    assert CONTACT in error


def test_the_bridge_crm_log_still_writes_when_the_guard_is_down(
        inbox_fetch, card, monkeypatch):
    """The jaw. A finalizer that refused everything would pass both tests above
    while breaking the operator's own dashboard."""
    from scripts.bridge_daemon.finalizers.crm_log import log_to_crm
    monkeypatch.delenv("HEADING_OS_DATA_READONLY", raising=False)
    before = _sha256(card)

    result = log_to_crm(CONV_ID, data_root=inbox_fetch)

    assert result["ok"] is True, result
    assert _sha256(card) != before, "the interaction was not written"
    assert "Partnership terms" in card.read_text(encoding="utf-8")
