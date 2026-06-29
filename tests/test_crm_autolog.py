"""Tests for scripts/utils/crm_autolog.py."""

from pathlib import Path

import pytest


@pytest.fixture
def crm_workspace(tmp_path, monkeypatch):
    """Set up a minimal CRM tree with one entity + one relationship."""
    crm = tmp_path / "crm"
    (crm / "address-book").mkdir(parents=True)
    (crm / "contacts").mkdir(parents=True)

    (crm / "address-book" / "karl-mertens.md").write_text(
        "---\n"
        "slug: karl-mertens\n"
        "name: Sebastian Mueller\n"
        "canonical_email: karl@rivex.com\n"
        "other_emails:\n"
        "  - karl.mertens@rivex.com\n"
        "employer: AllianceCo\n"
        "canonical_owner: alex-rivera\n"
        "created: 2026-03-15\n"
        "---\n",
        encoding="utf-8",
    )

    (crm / "contacts" / "karl-mertens.md").write_text(
        "---\n"
        "entity_ref: karl-mertens\n"
        "relationship_type: partner\n"
        "last_touch: 2026-05-01\n"
        "created: 2026-03-15\n"
        "owner: misha-hanin\n"
        "---\n\n"
        "## Active Commitments\n\n"
        "## Interaction Log\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("CRM_WORKSPACE_ROOT", str(tmp_path))
    return tmp_path


def test_resolve_recipient_by_canonical_email(crm_workspace):
    from scripts.utils.crm_autolog import resolve_recipient
    path = resolve_recipient("karl@rivex.com", workspace_root=crm_workspace)
    assert path is not None
    assert path.name == "karl-mertens.md"


def test_resolve_recipient_by_other_email(crm_workspace):
    from scripts.utils.crm_autolog import resolve_recipient
    path = resolve_recipient("karl.mertens@rivex.com", workspace_root=crm_workspace)
    assert path is not None
    assert path.name == "karl-mertens.md"


def test_resolve_recipient_case_insensitive(crm_workspace):
    from scripts.utils.crm_autolog import resolve_recipient
    path = resolve_recipient("KARL@RIVEX.COM", workspace_root=crm_workspace)
    assert path is not None


def test_resolve_recipient_no_match(crm_workspace):
    from scripts.utils.crm_autolog import resolve_recipient
    path = resolve_recipient("unknown@external.com", workspace_root=crm_workspace)
    assert path is None


def test_resolve_recipient_multi_match_conflict(crm_workspace):
    """Two entities claim the same email - resolver refuses and returns None."""
    # Create a second entity claiming karl@rivex.com
    (crm_workspace / "crm" / "address-book" / "evil-twin.md").write_text(
        "---\n"
        "slug: evil-twin\n"
        "name: Evil Twin\n"
        "canonical_email: karl@rivex.com\n"
        "employer: Imposter Corp\n"
        "canonical_owner: alex-rivera\n"
        "created: 2026-05-15\n"
        "---\n",
        encoding="utf-8",
    )
    from scripts.utils.crm_autolog import resolve_recipient
    path = resolve_recipient("karl@rivex.com", workspace_root=crm_workspace)
    assert path is None  # ambiguous; resolver refuses


def test_log_outbound_appends_entry_and_bumps_last_touch(crm_workspace):
    from scripts.utils.crm_autolog import log_outbound
    result = log_outbound(
        recipient_email="karl@rivex.com",
        subject="Partnership terms",
        body_excerpt="Quick check on the pricing for tier 2.",
        date="2026-05-15",
        workspace_root=crm_workspace,
    )
    assert result is True
    rel_text = (crm_workspace / "crm" / "contacts" / "karl-mertens.md").read_text(encoding="utf-8")
    assert "last_touch: 2026-05-15" in rel_text
    assert "Partnership terms" in rel_text
    assert "### 2026-05-15 | Email |" in rel_text


def test_log_outbound_strips_html_body(crm_workspace):
    """send-email.py passes the raw HTML body (one line, no newlines). The CRM
    log must record clean plain text, never raw <p> markup. Regression for the
    2026-06-09 contamination where raw tags landed in 7 contact files."""
    from scripts.utils.crm_autolog import log_outbound
    html_body = (
        "<p>Victor,</p><p>Kesha flagged that only 2 of 4 Silicom cards are "
        "detected &amp; asked for a war room.</p><p>Misha</p>"
    )
    result = log_outbound(
        recipient_email="karl@rivex.com",
        subject="the second deployment region status",
        body_excerpt=html_body,
        date="2026-06-09",
        workspace_root=crm_workspace,
    )
    assert result is True
    rel_text = (crm_workspace / "crm" / "contacts" / "karl-mertens.md").read_text(encoding="utf-8")
    assert "<p>" not in rel_text
    assert "&amp;" not in rel_text  # entities unescaped
    assert "Victor, Kesha flagged" in rel_text
    assert "war room. & asked" not in rel_text  # words don't run together across tags


def test_plain_snippet_drops_dangling_truncated_tag():
    """A body truncated mid-tag (send-email caps at 300, snippet at 200) must
    not leave a dangling '<' in the log line."""
    from scripts.utils.crm_autolog import plain_snippet
    out = plain_snippet("<p>Where does this stand now?</p><")
    assert out == "Where does this stand now?"
    assert "<" not in out


def test_plain_snippet_safe_on_plain_text():
    from scripts.utils.crm_autolog import plain_snippet
    assert plain_snippet("Just a plain line.") == "Just a plain line."
    assert plain_snippet("") == ""


def test_log_outbound_skip_on_no_match(crm_workspace):
    from scripts.utils.crm_autolog import log_outbound
    result = log_outbound(
        recipient_email="unknown@external.com",
        subject="Cold reach",
        body_excerpt="",
        date="2026-05-15",
        workspace_root=crm_workspace,
    )
    assert result is False
    # No mutation to existing files
    rel_text = (crm_workspace / "crm" / "contacts" / "karl-mertens.md").read_text(encoding="utf-8")
    assert "last_touch: 2026-05-01" in rel_text


def test_bump_inbound_silent_bump_only(crm_workspace):
    from scripts.utils.crm_autolog import bump_inbound
    result = bump_inbound(
        sender_email="karl@rivex.com",
        date="2026-05-15",
        workspace_root=crm_workspace,
    )
    assert result is True
    rel_text = (crm_workspace / "crm" / "contacts" / "karl-mertens.md").read_text(encoding="utf-8")
    assert "last_touch: 2026-05-15" in rel_text
    # No log entry was written
    assert "### 2026-05-15" not in rel_text


def test_bump_inbound_inserts_last_touch_when_absent(tmp_path, monkeypatch):
    """A relationship record without last_touch in frontmatter should get one inserted."""
    crm = tmp_path / "crm"
    (crm / "address-book").mkdir(parents=True)
    (crm / "contacts").mkdir(parents=True)

    (crm / "address-book" / "no-touch.md").write_text(
        "---\n"
        "slug: no-touch\n"
        "name: No Touch\n"
        "canonical_email: notouch@x.com\n"
        "employer: Acme\n"
        "canonical_owner: misha-hanin\n"
        "created: 2026-05-15\n"
        "---\n",
        encoding="utf-8",
    )
    (crm / "contacts" / "no-touch.md").write_text(
        "---\n"
        "entity_ref: no-touch\n"
        "relationship_type: prospect\n"
        "created: 2026-05-15\n"
        "owner: misha-hanin\n"
        "---\n\n"
        "## Active Commitments\n\n"
        "## Interaction Log\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("CRM_WORKSPACE_ROOT", str(tmp_path))

    from scripts.utils.crm_autolog import bump_inbound
    result = bump_inbound(
        sender_email="notouch@x.com",
        date="2026-05-16",
        workspace_root=tmp_path,
    )
    assert result is True
    rel_text = (crm / "contacts" / "no-touch.md").read_text(encoding="utf-8")
    assert "last_touch: 2026-05-16" in rel_text
    # Verify the line was inserted inside the frontmatter block, not appended after it
    fm_section = rel_text.split("---")[1]
    assert "last_touch: 2026-05-16" in fm_section


def test_atomic_write_uses_tmp_rename(crm_workspace):
    """Verify the write goes through a .tmp file (no partial-write hazard)."""
    from scripts.utils.crm_autolog import log_outbound

    # Monkey-patch os.replace to record the call
    import os
    calls = []
    original_replace = os.replace
    def tracking_replace(src, dst):
        calls.append((str(src), str(dst)))
        return original_replace(src, dst)
    import scripts.utils.crm_autolog as autolog
    autolog.os.replace = tracking_replace
    try:
        log_outbound("karl@rivex.com", "Test", "Body", "2026-05-15", workspace_root=crm_workspace)
    finally:
        autolog.os.replace = original_replace
    assert len(calls) >= 1
    assert calls[0][0].endswith(".tmp")
    assert calls[0][1].endswith("karl-mertens.md")
