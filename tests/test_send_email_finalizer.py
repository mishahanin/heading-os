"""The bridge's send-email finalizer must never claim a send it did not make.

Found by the 2026-08-23 audit. `send_drafted` returned `{"sent": True}` when the
draft sidecar existed on disk, and nothing in the function calls any transport.
It is wired into the daemon as the handler for the browser action literally named
`send-email`, so a click reported a delivered email that no transport ever saw.

The word is what made it dangerous. A stub is fine; a stub whose return value is
indistinguishable from success is a false report, and the only reason it never
produced one in practice is that the bridge daemon is stopped.

The rule it would violate if Phase 2 ever "wired the send in here" is
`.claude/rules/lethal-trifecta.md`: an outbound send is gated behind an explicit
human approval, which since 2026-06-27 is the operator typing
`scripts/action-queue.py approve <id>`. A browser POST is not that.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.bridge_daemon.finalizers.send_email import send_drafted  # noqa: E402

DRAFTS = Path("outputs") / "operations" / "email-intelligence" / "drafts"


def _draft(root: Path, artifact_id: str) -> Path:
    path = root / DRAFTS / f"{artifact_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"to": "a@b.c", "subject": "s", "body": "b"}', encoding="utf-8")
    return path


def test_an_existing_draft_is_found_but_never_reported_as_sent(tmp_path):
    _draft(tmp_path, "abc123")
    result = send_drafted(tmp_path, "abc123")
    assert result["sent"] is False, "nothing in this function can send an email"
    assert result["found"] is True
    assert "action-queue.py approve" in result["error"], (
        "a caller told 'not sent' must also be told where the send actually lives"
    )


def test_a_missing_draft_is_not_sent_either(tmp_path):
    result = send_drafted(tmp_path, "nope")
    assert result["sent"] is False
    assert result["found"] is False
    assert "not found" in result["error"]


def test_the_module_reaches_no_transport(tmp_path):
    """Structural, because the two tests above only prove what it returns.

    If a future edit imports a transport here, that edit is the one this file
    exists to stop, and it should fail before anyone reads the docstring.
    """
    source = (
        ROOT / "scripts" / "bridge_daemon" / "finalizers" / "send_email.py"
    ).read_text(encoding="utf-8")
    for forbidden in ("send-email.py", "exchangelib", "smtplib", "subprocess"):
        assert forbidden not in source, (
            f"{forbidden!r} appears here; an outbound send from a browser action "
            f"is exactly what .claude/rules/lethal-trifecta.md forbids"
        )


def test_a_traversing_artifact_id_is_still_refused(tmp_path):
    with pytest.raises(ValueError):
        send_drafted(tmp_path, "../../etc/passwd")
