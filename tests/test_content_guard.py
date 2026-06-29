"""Tests for the engine CONTENT-leak gate (scripts/utils/content_denylist.py).

The gate is the content sibling of the routing guards: it flags real entities
(person slugs/names, handles, e-mails, Telegram IDs, curated company/event tokens)
embedded in engine-routed files. These tests build a denylist from a synthetic
DATA overlay (never the real one) and assert it flags real tokens, exempts the
public-identity + fictional allowlists, honors inline suppression, and degrades to
a no-op when the overlay is absent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.content_denylist import build_denylist


def _make_overlay(tmp_path: Path) -> Path:
    data = tmp_path / ".heading-os-data"
    (data / "crm" / "contacts").mkdir(parents=True)
    (data / "admin").mkdir(parents=True)
    (data / "config").mkdir(parents=True)
    # a real-ish CRM contact (slug = filename)
    (data / "crm" / "contacts" / "zenon-makarios.md").write_text("# x", encoding="utf-8")
    # executives
    (data / "admin" / "executives.json").write_text(
        json.dumps({"executives": [
            {"slug": "vex-thorne", "name": "Vex Thorne", "github_user": "vthorne",
             "data_repo": "heading-os-data-vex-thorne", "status": "active"}
        ]}), encoding="utf-8")
    # fireside roster: a member dict -> handle + name + telegram id
    (data / "config" / "fireside-schedule.json").write_text(
        json.dumps({"qorvath": {"name": "Qorvath Lune", "telegram_user_id": 581234567,
                                "active": True}}), encoding="utf-8")
    # a config carrying a real-ish e-mail
    (data / "config" / "exec-registry.json").write_text(
        json.dumps({"people": [{"email": "zenon.makarios@realco.test"}]}), encoding="utf-8")
    # curated non-person tokens
    (data / "config" / "content-denylist.yaml").write_text(
        "companies: [\"Krellide Systems\"]\nevents: [\"Vortex Summit\"]\n"
        "competitors: [\"Nullsoft Telco\"]\n", encoding="utf-8")
    return data


def test_flags_harvested_real_entities(tmp_path):
    dl = build_denylist(_make_overlay(tmp_path))
    assert not dl.degraded and dl.tokens
    # slug, exec slug+name, handle, telegram id, email, curated all present
    sample = "deploy to zenon-makarios via vex-thorne; ping qorvath at 581234567"
    hits = {m.lower() for _, m, _ in dl.scan_text(sample)}
    assert "zenon-makarios" in hits
    assert "vex-thorne" in hits
    assert "qorvath" in hits
    assert "581234567" in hits
    assert dl.scan_text("contact zenon.makarios@realco.test")  # email
    assert dl.scan_text("the Krellide Systems deal")           # curated company
    assert dl.scan_text("met them at Vortex Summit")           # curated event


def test_allowlist_and_fictional_not_flagged(tmp_path):
    dl = build_denylist(_make_overlay(tmp_path))
    for safe in (
        "Misha Hanin leads 31 Concept on ODUN.ONE and TrustONE",
        "draft an email to alice and bob about ExampleCorp",
        "the jane-doe exec slug and Acme Globex",
    ):
        assert dl.scan_text(safe) == [], f"false positive on: {safe!r}"


def test_inline_suppression(tmp_path):
    dl = build_denylist(_make_overlay(tmp_path))
    assert dl.scan_text("uses vex-thorne here")  # flagged without marker
    assert dl.scan_text("uses vex-thorne here  # content-guard: ok (fixture)") == []


def test_word_boundary_no_substring_false_positive(tmp_path):
    dl = build_denylist(_make_overlay(tmp_path))
    # 'qorvath' must not match when glued inside a larger identifier
    assert dl.scan_text("qorvathic_helper = 1") == []
    assert dl.scan_text("xqorvath = 1") == []


def test_degrades_without_overlay():
    dl = build_denylist(None)
    assert dl.degraded
    assert dl.tokens == {}
    assert dl.scan_text("zenon-makarios vex-thorne 581234567") == []


def test_strict_adds_name_words_default_does_not(tmp_path):
    overlay = _make_overlay(tmp_path)
    default = build_denylist(overlay, strict=False)
    strict = build_denylist(overlay, strict=True)
    # bare surname word from the slug is only present in strict mode
    assert default.scan_text("the makarios report") == []
    assert strict.scan_text("the makarios report")
    assert len(strict.tokens) > len(default.tokens)
