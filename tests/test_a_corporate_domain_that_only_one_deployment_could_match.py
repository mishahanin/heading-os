"""The corporate mail domain decided behaviour, and only one tenant could match it.

Closed 2026-09-01. `tests/test_no_tenant_domain_is_compiled_into_the_engine.py`
named three sites as known-open and deferred, because the right value is the
instance's CORPORATE mail domain, which is not `operator_email_domain()`
(measured on the operator's machine: the operator's own email is on one domain
and the company mail on another). The operator settled where it lives, so the
three are closed against a new key on the existing identity seam,
`corporate_email_domain()`:

  scripts/email-intelligence.py   INTERNAL_DOMAIN, which classifies a whole
                                  conversation internal-or-external
  scripts/utils/crm.py            the tribe-email warning in `scan_contacts`
  scripts/crm-health.py           the warning line that reports it

Two of the three carried NO behavioural coverage at all before this file.
`tests/test_crm_entity_helpers.py` unpacks `tribe_warnings` twice and never
looks inside it, so the warning could have fired on everyone or on nobody and
the suite stayed green.

The case this file exists for is the third one, and it is not the defect that
was reported. The obvious rewrite of the `crm.py` site is

    if email and f"@{domain}" in email.lower() and ...

which on an unconfigured clone -- the normal state of a public checkout -- is
`"@" in email.lower()`, true of every address ever written. The site that
warned about nobody would then warn about everybody, and the CRM health report
would open with a wall of noise. The `email-intelligence.py` site degrades the
other way on its own (`endswith("@")` is false for a real address), and that is
asserted below rather than assumed.

Fixture domains are `example.com` / `example.net`, reserved by RFC 2606, so
this file carries no tenant value of its own.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

import scripts.utils.operator_identity as operator_identity

ROOT = Path(__file__).resolve().parent.parent

CORPORATE = "example.com"
ELSEWHERE = "example.net"


def _load(name: str, rel: str):
    """Import a kebab-case script under a python-legal module name."""
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ==========================================================================
# The seam: a real four-tier resolution over temporary roots, never a stub
# ==========================================================================

@pytest.fixture
def seam(tmp_path, monkeypatch):
    """Steer the identity seam with real directories, as the resolver's own
    test file does. `WORKSPACE_ROOT` moves the engine-local tier and
    `HEADING_OS_DATA` moves the overlay tier, both onto empty temporary trees,
    so an unconfigured instance falls all the way to the shipped example and
    resolves the documented `""` sentinel for real rather than by patch.
    """
    engine = tmp_path / "engine"
    (engine / "config").mkdir(parents=True)
    overlay = tmp_path / "overlay"
    (overlay / "config").mkdir(parents=True)
    monkeypatch.setenv("WORKSPACE_ROOT", str(engine))
    monkeypatch.setenv("HEADING_OS_DATA", str(overlay))
    for env in operator_identity._ENV_KEYS.values():
        monkeypatch.delenv(env, raising=False)
    operator_identity._reset_cache()

    def configure(domain: str | None) -> None:
        """Write (or omit) the overlay's corporate_email_domain, then re-resolve."""
        if domain is not None:
            (overlay / "config" / "operator.yaml").write_text(
                "name: Ada Lovelace\n"
                "slug: ada-lovelace\n"
                f"corporate_email_domain: {domain}\n",
                encoding="utf-8",
            )
        operator_identity._reset_cache()

    yield configure
    operator_identity._reset_cache()


# ==========================================================================
# The accessor itself
# ==========================================================================

def test_the_corporate_domain_resolves_from_the_overlay(seam):
    seam(CORPORATE)
    assert operator_identity.corporate_email_domain() == CORPORATE


def test_an_unconfigured_instance_gets_the_empty_sentinel_not_a_tenant(seam):
    seam(None)
    assert operator_identity.corporate_email_domain() == ""


def test_the_environment_beats_the_overlay(seam, monkeypatch):
    seam(CORPORATE)
    monkeypatch.setenv("HEADING_OS_OPERATOR_CORPORATE_EMAIL_DOMAIN", ELSEWHERE)
    operator_identity._reset_cache()
    assert operator_identity.corporate_email_domain() == ELSEWHERE


def test_the_value_is_the_bare_domain_with_no_at_sign(seam):
    """Two of the three call sites prepend the `@` themselves, so a value that
    carried one would build `@@example.com` and match nothing."""
    seam(CORPORATE)
    assert not operator_identity.corporate_email_domain().startswith("@")


def test_it_is_not_the_operator_email_domain(seam):
    """The two fields are different questions. The seam must keep them apart:
    on the operator's own machine the personal email and the company mail sit
    on different domains, which is why this key exists at all."""
    seam(CORPORATE)
    assert operator_identity.corporate_email_domain() == CORPORATE
    assert operator_identity.operator_email_domain() == ""


def test_the_shipped_example_carries_no_tenant_value():
    text = (ROOT / "scripts" / "operator.example.yaml").read_text(encoding="utf-8")
    assert "corporate_email_domain" in text, "the key is not documented for a new clone"
    line = [ln for ln in text.splitlines()
            if ln.startswith("corporate_email_domain")]
    assert line == ['corporate_email_domain: ""'], line


# ==========================================================================
# scripts/utils/crm.py - the tribe-email warning
# ==========================================================================

def _card(name: str, rel_type: str, email: str, tribe_email_ok: bool = False) -> str:
    ok = "tribe_email_ok: true\n" if tribe_email_ok else ""
    return (
        "---\n"
        f"name: {name}\n"
        "company: Northwind\n"
        f"type: {rel_type}\n"
        f"email: {email}\n"
        "last_touch: 2026-08-01\n"
        f"{ok}"
        "---\n\n"
        "## Interaction Log\n"
    )


@pytest.fixture
def crm_tree(tmp_path):
    """Four cards: one on the corporate domain, one elsewhere, one opted out,
    one typed tribe. Returns a callable that scans them."""
    root = tmp_path / "ws"
    contacts = root / "crm" / "contacts"
    contacts.mkdir(parents=True)
    (root / "crm" / "config.md").write_text(
        "| Type | Expected Cadence | Yellow Threshold | Red Threshold |\n"
        "|------|-----------------|-----------------|---------------|\n"
        "| prospect | 30 | 20 | 30 |\n",
        encoding="utf-8",
    )
    (contacts / "dana-okoro.md").write_text(
        _card("Dana Okoro", "prospect", f"dana@{CORPORATE}"), encoding="utf-8")
    (contacts / "lee-park.md").write_text(
        _card("Lee Park", "prospect", f"lee@{ELSEWHERE}"), encoding="utf-8")
    (contacts / "sam-rivera.md").write_text(
        _card("Sam Rivera", "prospect", f"sam@{CORPORATE}", tribe_email_ok=True),
        encoding="utf-8")
    (contacts / "nia-costa.md").write_text(
        _card("Nia Costa", "tribe", f"nia@{CORPORATE}"), encoding="utf-8")

    def scan():
        from scripts.utils.crm import parse_config, scan_contacts
        config = parse_config(root / "crm" / "config.md")
        contacts_list, warnings, _dangling, _stages, _aliases = scan_contacts(
            config, contacts_dir=contacts, workspace_root=root,
        )
        return contacts_list, warnings

    return scan


def test_the_fixture_actually_produced_contacts(seam, crm_tree):
    """Anti-vacuity jaw. Every `silent` assertion below is trivially true over
    an empty corpus, so the corpus is asserted non-empty first: four cards in,
    four contact records out."""
    seam(CORPORATE)
    contacts, _warnings = crm_tree()
    assert len(contacts) == 4, [c["file"] for c in contacts]


def test_a_contact_on_the_corporate_domain_warns(seam, crm_tree):
    seam(CORPORATE)
    contacts, warnings = crm_tree()
    assert contacts
    assert [w["name"] for w in warnings] == ["Dana Okoro"], warnings


def test_a_contact_on_another_domain_is_silent(seam, crm_tree):
    seam(CORPORATE)
    contacts, warnings = crm_tree()
    assert contacts
    assert "Lee Park" not in [w["name"] for w in warnings]


def test_the_opt_out_still_suppresses_the_warning(seam, crm_tree):
    seam(CORPORATE)
    contacts, warnings = crm_tree()
    assert contacts
    assert "Sam Rivera" not in [w["name"] for w in warnings]


def test_a_tribe_typed_contact_is_not_warned_about(seam, crm_tree):
    seam(CORPORATE)
    contacts, warnings = crm_tree()
    assert contacts
    assert "Nia Costa" not in [w["name"] for w in warnings]


def test_an_unconfigured_instance_warns_about_nobody(seam, crm_tree):
    """The flood case, and the most important test in this file.

    With the key unset, `f"@{domain}"` is the bare `"@"`, which is a substring
    of every address in the corpus. The guard must be on the DOMAIN, not on the
    address: no domain, no warning. Three of these four cards carry an address,
    so a naive rewrite makes this list length 3.
    """
    seam(None)
    contacts, warnings = crm_tree()
    assert len(contacts) == 4
    assert [c for c in contacts if c["email"]], "the corpus has no addresses to flood on"
    assert warnings == [], warnings


# ==========================================================================
# scripts/email-intelligence.py - internal/external classification
# ==========================================================================

ei = _load("email_intelligence_corporate_domain", "scripts/email-intelligence.py")


def _msg(sender: str, to: tuple[str, ...] = ()) -> dict:
    return {
        "message_id": "<m@x>", "conversation_id": "conv-1", "conversation_topic": "T",
        "item_class": "IPM.Note", "subject": "S", "sender_email": sender,
        "sender_name": sender or "?", "to": [{"email": e, "name": e} for e in to],
        "cc": [], "body_preview": "p", "body": "b",
        "datetime": "2026-09-01T00:00:00+00:00", "direction": "incoming",
    }


def test_the_internal_domain_is_resolved_from_the_seam_not_compiled_in():
    """The module-level name must survive -- four tests in
    `tests/test_a_mail_run_that_reports_what_it_missed.py` build addresses out
    of `ei.INTERNAL_DOMAIN` -- but its VALUE must come from the seam."""
    assert operator_identity.corporate_email_domain() == ei.INTERNAL_DOMAIN


def test_a_thread_inside_the_configured_domain_is_internal(monkeypatch):
    monkeypatch.setattr(ei, "INTERNAL_DOMAIN", CORPORATE)
    conv = ei.group_conversations([_msg(f"a@{CORPORATE}", (f"b@{CORPORATE}",))])
    assert conv["conv-1"]["is_internal"] is True


def test_a_thread_reaching_outside_the_configured_domain_is_external(monkeypatch):
    monkeypatch.setattr(ei, "INTERNAL_DOMAIN", CORPORATE)
    conv = ei.group_conversations([_msg(f"a@{CORPORATE}", (f"b@{ELSEWHERE}",))])
    assert conv["conv-1"]["is_internal"] is False


def test_an_unconfigured_instance_calls_nothing_internal(monkeypatch):
    """Asserted, not assumed. `endswith("@")` is false for every real address,
    so this site degrades safely where the crm.py substring site does not."""
    monkeypatch.setattr(ei, "INTERNAL_DOMAIN", "")
    conv = ei.group_conversations([_msg(f"a@{CORPORATE}", (f"b@{CORPORATE}",))])
    assert conv["conv-1"]["is_internal"] is False
