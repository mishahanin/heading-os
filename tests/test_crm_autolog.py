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
        "name: Karl Mertens\n"
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
        "<p>Dana,</p><p>Nolan flagged that only 2 of 4 bypass cards are "
        "detected &amp; asked for a war room.</p><p>Misha</p>"
    )
    result = log_outbound(
        recipient_email="karl@rivex.com",
        subject="the pilot deployment status",
        body_excerpt=html_body,
        date="2026-06-09",
        workspace_root=crm_workspace,
    )
    assert result is True
    rel_text = (crm_workspace / "crm" / "contacts" / "karl-mertens.md").read_text(encoding="utf-8")
    assert "<p>" not in rel_text
    assert "&amp;" not in rel_text  # entities unescaped
    assert "Dana, Nolan flagged" in rel_text
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


# --------------------------------------------------- a bare "<" is not a tag
#
# MEASURED 2026-09-01, before the fix, straight out of `plain_snippet`:
#
#     'Budget is < 50k'      -> 'Budget is'
#     'temp < 0 degrees'     -> 'temp'
#     'Q3 revenue < target'  -> 'Q3 revenue'
#     'x < y and y > z'      -> 'x z'
#
# Two regexes did it. The dangling-fragment rule was `<[^>]*$`, which drops
# every character from ANY trailing "<" to the end of the string, and the
# tag-strip rule was `<[^>]+>`, which treats any "<...>" span as markup. A
# less-than sign is ordinary prose in a business email ("budget is < 50k"), and
# `log_outbound` writes this snippet into the operator's interaction log, so the
# half sentence that survives is what the record says was discussed. The
# docstring's "Safe on plain text (nothing to strip)" was false for exactly the
# character the function is written around.
#
# Both rules now require a tag NAME after the "<" (an optional "/" then a
# letter, or "!" for a comment/doctype), which is what real markup always has.
# A bare "<" at the very end still goes, because that IS a body truncated
# mid-tag and the case below pins it.

@pytest.mark.parametrize("body", [
    "Budget is < 50k",
    "temp < 0 degrees",
    "Q3 revenue < target",
    "x < y and y > z",
    "2 < 3",
])
def test_plain_snippet_keeps_prose_around_a_bare_less_than(body):
    from scripts.utils.crm_autolog import plain_snippet
    assert plain_snippet(body) == body


@pytest.mark.parametrize("body,expected", [
    # The anchor cases: a body cut mid-tag still loses the fragment.
    ("<p>Where does this stand now?</p><", "Where does this stand now?"),
    ("<p>Where does this stand now?</p><p", "Where does this stand now?"),
    ("<p>Where does this stand now?</p></di", "Where does this stand now?"),
])
def test_plain_snippet_still_drops_a_truncated_tag(body, expected):
    """The narrowing must not become "keep everything after a <"."""
    from scripts.utils.crm_autolog import plain_snippet
    assert plain_snippet(body) == expected


def test_plain_snippet_still_strips_real_markup():
    from scripts.utils.crm_autolog import plain_snippet
    out = plain_snippet("<div><p>One.</p><p>Two &amp; three.</p></div>")
    assert "<" not in out and ">" not in out
    assert out == "One. Two & three."


def test_plain_snippet_truncates_at_the_limit():
    """The `limit` argument is the only thing keeping a whole email body out of
    a one-line CRM log entry."""
    from scripts.utils.crm_autolog import plain_snippet
    assert plain_snippet("x" * 500) == "x" * 200
    assert plain_snippet("y" * 500, limit=12) == "y" * 12


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


def test_log_outbound_audits_a_miss_too(crm_workspace):
    """`log_outbound` promises a JSONL audit entry "on every invocation
    regardless of match outcome", and the miss branch is the one that matters:
    a run that matched nothing is indistinguishable from a run that never
    happened unless the miss is written down. The seventy-one-day silent
    failure this module's `_address_book_dir` docstring records was diagnosed
    from exactly these `"matched": false` lines."""
    import json
    from scripts.utils.crm_autolog import log_outbound
    assert log_outbound("nobody@vorlite.test", "Cold reach", "",
                        date="2026-05-15", workspace_root=crm_workspace) is False
    logs = sorted((crm_workspace / ".sync" / "logs").glob("crm-autolog-*.jsonl"))
    assert logs, "no audit entry was written for the miss"
    records = [json.loads(line) for line in
               logs[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    misses = [r for r in records if r.get("email") == "nobody@vorlite.test"]
    assert misses and misses[0]["matched"] is False
    assert misses[0]["kind"] == "outbound"


def test_the_email_index_notices_a_new_entity(crm_workspace):
    """The index is cached per address-book directory and invalidated on an
    mtime signature. Serving a stale index means resolving an address to the
    wrong contact, or to none, for the life of the process, and
    `sync-exchange` is a long-lived process."""
    from scripts.utils.crm_autolog import resolve_recipient
    assert resolve_recipient("karl@rivex.com", workspace_root=crm_workspace) is not None
    assert resolve_recipient("nadia@vorlite.test", workspace_root=crm_workspace) is None

    (crm_workspace / "crm" / "address-book" / "nadia-orso.md").write_text(
        "---\nslug: nadia-orso\nname: Nadia Orso\n"
        "canonical_email: nadia@vorlite.test\n---\n", encoding="utf-8")
    (crm_workspace / "crm" / "contacts" / "nadia-orso.md").write_text(
        "---\nentity_ref: nadia-orso\nrelationship_type: lead\n---\n\n"
        "## Interaction Log\n", encoding="utf-8")

    found = resolve_recipient("nadia@vorlite.test", workspace_root=crm_workspace)
    assert found is not None and found.name == "nadia-orso.md", \
        "the cached index was served after the address book changed"


def test_the_email_index_notices_an_edited_entity(crm_workspace):
    """The other half of the signature: a file whose CONTENT changed keeps its
    name, so only the mtime component can catch it. `os.utime` sets a distinct
    mtime explicitly rather than racing the filesystem's clock granularity."""
    import os
    from scripts.utils.crm_autolog import resolve_recipient
    entity = crm_workspace / "crm" / "address-book" / "karl-mertens.md"
    assert resolve_recipient("karl@rivex.com", workspace_root=crm_workspace) is not None

    entity.write_text(entity.read_text(encoding="utf-8").replace(
        "canonical_email: karl@rivex.com",
        "canonical_email: karl@newdomain.test"), encoding="utf-8")
    stamp = entity.stat().st_mtime + 10
    os.utime(entity, (stamp, stamp))

    assert resolve_recipient("karl@newdomain.test",
                             workspace_root=crm_workspace) is not None, \
        "the cached index survived an edit to the entity it was built from"


def test_resolve_recipient_refuses_an_entity_with_no_relationship_card(crm_workspace):
    """An address book entry is a person; a relationship record is the file the
    log entry is written INTO. With the entity present and the card absent there
    is nowhere to write, so the resolver must answer None rather than hand back
    a path that does not exist. `log_outbound` would then `read_text` it and
    raise FileNotFoundError out of a function that returns False on every other
    unresolvable address."""
    from scripts.utils.crm_autolog import resolve_recipient
    (crm_workspace / "crm" / "address-book" / "unlinked.md").write_text(
        "---\nslug: unlinked\nname: Unlinked Person\n"
        "canonical_email: unlinked@vorlite.test\n---\n", encoding="utf-8")
    assert resolve_recipient("unlinked@vorlite.test",
                             workspace_root=crm_workspace) is None


def test_a_scalar_other_emails_is_indexed(crm_workspace):
    """`other_emails` is normally a YAML list, but a hand-edited entity can
    carry a single scalar. The index has a branch for it; nothing exercised
    that branch, so it could have been deleted with the suite still green."""
    from scripts.utils.crm_autolog import resolve_recipient
    (crm_workspace / "crm" / "address-book" / "solo-alias.md").write_text(
        "---\nslug: solo-alias\nname: Solo Alias\n"
        "canonical_email: solo@vorlite.test\n"
        "other_emails: s.alias@vorlite.test\n---\n", encoding="utf-8")
    (crm_workspace / "crm" / "contacts" / "solo-alias.md").write_text(
        "---\nentity_ref: solo-alias\nrelationship_type: lead\n---\n\n"
        "## Interaction Log\n", encoding="utf-8")
    found = resolve_recipient("s.alias@vorlite.test", workspace_root=crm_workspace)
    assert found is not None and found.name == "solo-alias.md"


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
