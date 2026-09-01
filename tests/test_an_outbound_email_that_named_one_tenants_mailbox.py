#!/usr/bin/env python3
"""The fireside backup email offered a tenant's address as "reach a human".

`EMAIL_BACKUP_BODY` in `scripts/fireside-bot.py` is a template that goes OUT, by
email, to a Tribe member who has not answered their Telegram DMs. Until
2026-09-01 it ended:

    - 31C Fireside Bot
    (via ceo@31c.io if you need to reach a human)

The engine repository is public. That literal trips no secret gate, because
`31c.io` is published identity, and that is exactly why it survived a year of
review: the defect is not disclosure. It is that the line answers for ONE
deployment. Any other operator running this daemon invites their own Tribe to
write to this operator's mailbox, in a message that operator never saw.

Found while closing the same defect in `scripts/email-intelligence.py`,
`scripts/utils/crm.py` and `scripts/crm-health.py` (the `corporate_email_domain`
key) and in five `.claude/skills/` instruction files. This was the sixth site and
it had NO test at all: `git grep -l EMAIL_BACKUP_BODY -- tests/` returned
nothing, so nothing in the suite had ever rendered this template.

## Why the empty case gets its own test rather than a default

The address resolves from `get_operator()["email"]`, which returns `""` when
unconfigured - the documented sentinel for that whole seam, never None and never
a raise. Interpolating it would ship

    (via  if you need to reach a human)

to a real recipient, who then hunts for an address that is not there. An offer of
help with no way to take it is worse than no offer, so the whole line is omitted.
That is a behaviour the code has to choose deliberately, which means it is a
behaviour a test has to hold.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BOT = ROOT / "scripts" / "fireside-bot.py"

# Every address the engine is permitted to name is a matter of policy, not of
# taste. These two are the ones this defect actually shipped.
FORBIDDEN_LITERALS = ("ceo@31c.io", "@31c.io")


@pytest.fixture(scope="module")
def bot():
    """The module, loaded by path: its filename is not an importable name."""
    spec = importlib.util.spec_from_file_location("fireside_bot_probe", BOT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render(bot, human: str) -> str:
    """The template as the call site renders it, for a chosen contact address.

    The conditional is duplicated from `scripts/fireside-bot.py` rather than
    reached through the sending function, which needs a roster, a session
    registry, a Telegram bot and a subprocess. The duplication is what
    `test_the_call_site_still_builds_the_line_this_way` exists to catch: it
    asks the SOURCE whether the two still agree, so this file cannot quietly
    keep testing a rule the daemon has stopped following.
    """
    return bot.EMAIL_BACKUP_BODY.format(
        name="Dana", session_date="2026-09-08", session_day="Tuesday",
        theme="Shipping under pressure",
        human_contact=(f"\n(via {human} if you need to reach a human)"
                       if human else ""))


def test_the_template_names_no_tenant_mailbox(bot):
    """The headline. The literal must not be in the shipped template."""
    for literal in FORBIDDEN_LITERALS:
        assert literal not in bot.EMAIL_BACKUP_BODY, (
            f"scripts/fireside-bot.py ships {literal!r} inside an outbound "
            f"email template. On any deployment but this one it invites a "
            f"stranger's Tribe to write to this operator. Resolve the address "
            f"from `get_operator()['email']` instead.")


def test_a_configured_address_reaches_the_reader(bot):
    """The jaw for the test above.

    Deleting the whole sign-off satisfies "names no tenant mailbox" perfectly.
    So the offer of help has to survive, with a real address in it.
    """
    body = _render(bot, "someone@example.invalid")

    assert "(via someone@example.invalid if you need to reach a human)" in body
    assert "Fireside Bot" in body, (
        "the sign-off is gone, so the previous test is passing over a template "
        "that no longer says anything")


def test_an_unconfigured_operator_gets_no_broken_offer(bot):
    """The reason this is a conditional rather than an interpolation.

    `get_operator()['email']` returns `""` when unconfigured. Interpolating it
    ships "(via  if you need to reach a human)" to a real person.
    """
    body = _render(bot, "")

    assert "reach a human" not in body, (
        "the help line survived with no address in it, so a recipient is being "
        "told to contact nobody")
    assert "(via" not in body
    # The message itself must still be intact and sendable.
    assert "Fireside Bot" in body
    assert "Dana" in body
    assert "2026-09-08" in body


def test_the_admin_inbox_is_preferred_over_the_operators_own(monkeypatch):
    """`admin_email` first, `email` second, and the order is load-bearing.

    The human who fields "my fireside slot is wrong" is whoever administers the
    fleet, not necessarily whoever the daemon runs as. On the operator's own
    workspace the two are the same address, so the order never shows there -
    which is exactly why it has to be pinned by a test rather than by reading
    the running system, where both answers look identical and correct.

    Two halves, driven at two different depths, and the split is deliberate.

    The PREFERENCE is driven through the real environment tier, so a change to
    the precedence rules inside `operator_identity` reaches this test rather
    than passing beside it.

    The FALLBACK is driven by patching `get_operator`, because it cannot be
    reached from the environment on a configured machine. Setting the variable
    to the empty string does not mean "unset" to that loader - `_load` skips a
    value in `(None, "")` and falls through to the next tier, which on this
    workspace is a real `operator.yaml` holding a real address. Measured: the
    first version of this test asserted `admin_email() == ""` after blanking the
    variable and got the operator's live admin inbox back, which is the loader
    behaving exactly as documented. The test was wrong, not the code.
    """
    from scripts.utils import operator_identity as oi

    monkeypatch.setenv("HEADING_OS_OPERATOR_ADMIN_EMAIL", "admin@example.invalid")
    monkeypatch.setenv("HEADING_OS_OPERATOR_EMAIL", "runner@example.invalid")
    oi._reset_cache()
    try:
        assert oi.admin_email() == "admin@example.invalid"
        chosen = oi.admin_email() or oi.get_operator()["email"]
        assert chosen == "admin@example.invalid", (
            "the operator's own address won over the admin inbox, so on an "
            "executive's workspace this mails them their own request")
    finally:
        oi._reset_cache()

    # The fallback: a clone that has set only the older key must still work.
    monkeypatch.setattr(oi, "get_operator",
                        lambda: {"admin_email": "", "email": "runner@example.invalid"})
    assert oi.admin_email() == "", (
        "an empty stored value did not resolve to the documented '' sentinel")
    chosen = oi.admin_email() or oi.get_operator()["email"]
    assert chosen == "runner@example.invalid", (
        "with no admin inbox configured the call sites would send nowhere, "
        "when they should fall back to the operator's own address")


def test_both_call_sites_ask_for_the_admin_inbox_first():
    """Asked of the SOURCE, because one call site is a markdown instruction.

    `.claude/skills/request-skill/SKILL.md` is executed by a model reading a
    shell snippet, so there is no function to import and no behaviour to drive.
    The only thing a test can hold is that the snippet still names the right
    resolver in the right order. That is weaker than exercising it, and saying
    so here is the point: it catches a rewrite that drops the key, not one that
    keeps the words and breaks the meaning.
    """
    skill = (ROOT / ".claude" / "skills" / "request-skill" / "SKILL.md").read_text(
        encoding="utf-8")
    assert "admin_email() or get_operator()['email']" in skill, (
        "/request-skill no longer resolves admin_email first. It runs on an "
        "EXECUTIVE's workspace, where get_operator()['email'] is the executive "
        "themselves, so that order mails them their own request.")

    bot_src = BOT.read_text(encoding="utf-8")
    assert 'admin_email() or (get_operator().get("email") or "").strip()' in bot_src, (
        "scripts/fireside-bot.py no longer prefers the admin inbox for its "
        "'reach a human' line")

    for literal in FORBIDDEN_LITERALS:
        assert literal not in skill, (
            f"/request-skill names {literal!r} again")


def test_the_call_site_still_builds_the_line_this_way():
    """`_render` above duplicates the daemon's conditional. Hold them together.

    Asked of the SOURCE, because the sending function is unreachable without a
    roster, a session registry, a live Telegram bot and a subprocess. A test
    that duplicates production logic and never checks the copy is a test that
    goes on passing after production stops behaving that way, which is the
    defect shape this whole audit kept finding.
    """
    source = BOT.read_text(encoding="utf-8")

    assert "human_contact=" in source, (
        "the call site no longer passes `human_contact`, so `_render` in this "
        "file is rendering a shape the daemon does not produce")
    assert 'get_operator().get("email")' in source, (
        "the contact address no longer comes from the operator seam")
    assert "if _human else" in source, (
        "the empty-address branch is gone from the call site, so "
        "`test_an_unconfigured_operator_gets_no_broken_offer` is measuring a "
        "conditional that only this test file still has")
