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


@pytest.mark.parametrize("artifact_id", [
    "../../etc/passwd",           # the obvious one: refused by the FIRST character
    "abc/../../etc/passwd",       # the realistic near-miss: an allowed prefix
    "abc/def",                    # a bare separator with no traversal at all
    "abc\\..\\..\\etc",           # the Windows separator
    "a" * 65,                     # over the length bound
    "abc.json",                   # a dot, which the allowlist does not carry
    "",                           # empty
])
def test_a_traversing_artifact_id_is_still_refused(tmp_path, artifact_id):
    """The first case alone was a straw man.

    `"../../etc/passwd"` is refused by the very first character, so it passes
    against an UNANCHORED pattern too. Measured 2026-09-01 by mutation: dropping
    the `^...$` anchors from `_ARTIFACT_ID_RE` left this file green, while
    `"abc/../../etc/passwd"` - an id whose allowed prefix satisfies a floating
    match - was accepted and joined onto the drafts directory. The near-miss is
    the case worth writing; the obvious one is kept beside it, not instead.
    """
    with pytest.raises(ValueError):
        send_drafted(tmp_path, artifact_id)


@pytest.mark.parametrize("artifact_id", [None, 42, b"abc", ["abc"], {"id": "abc"}])
def test_a_non_string_artifact_id_is_refused_with_the_documented_error(
        tmp_path, artifact_id):
    """`re.match` on a non-string raises TypeError, not the ValueError this
    guard is documented to produce, so a JSON body carrying `"artifact_id": null`
    reached the endpoint as an unhandled 500. The `isinstance` check that fixes
    it is written down in the source and was carried by no test: measured
    2026-09-01, deleting it left this file green.
    """
    with pytest.raises(ValueError):
        send_drafted(tmp_path, artifact_id)


def test_a_well_formed_artifact_id_is_still_accepted(tmp_path):
    """The positive twin. Without it every refusal above is satisfied by a guard
    that refuses everything, which locates no draft at all."""
    _draft(tmp_path, "Abc-123_x")
    assert send_drafted(tmp_path, "Abc-123_x")["found"] is True
