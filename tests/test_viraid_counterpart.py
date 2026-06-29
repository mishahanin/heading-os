#!/usr/bin/env python3
"""Regression tests for the VIRAID counterpart resolver (the /odin collect gate).

Anchored to the two failure modes from the resolver's first live run, neither of
which a name-match-alone gate can stop:
  - noise-token leak: generic words harvested from CRM *bodies* must never resolve
    a counterpart -- the vocabulary is built ONLY from structured name fields.
  - internal-personal content: a message resolving only to tribe members drops;
    admission requires at least one EXTERNAL (non-tribe) counterpart.

Data-free: each test builds a synthetic CRM / aliases / people.md fixture under
tmp_path, so the suite runs from a data-less engine clone and embeds no real
contact identities.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.utils.viraid_counterpart import build_vocab, gate_message

SINCE = "2026-05-19"


def _write_fixture(root: Path):
    """Synthetic CRM tree: one external partner (person Quentin Vale, company
    Norvik), one tribe member (Tom Reed, 31C), an aliases entry, and a people.md
    section header. Contact bodies deliberately carry noise words so the
    structured-only vocab build is exercised."""
    contacts = root / "crm" / "contacts"
    contacts.mkdir(parents=True)
    (contacts / "quentin-vale.md").write_text(
        "---\nname: Quentin Vale\nentity_ref: quentin-vale\n"
        "relationship_type: partner\npipeline_company: Norvik\n---\n\n"
        "## Profile\n- channel notes and a document from the last meeting\n",
        encoding="utf-8",
    )
    (contacts / "tom-reed.md").write_text(
        "---\nname: Tom Reed\nentity_ref: tom-reed\n"
        "relationship_type: tribe\npipeline_company: 31C\n---\n\n"
        "## Profile\n- internal status and action items per the request\n",
        encoding="utf-8",
    )
    (root / "crm" / "aliases.md").write_text(
        "### Vantyr\n- Vantyr Holdings\n", encoding="utf-8"
    )
    ctx = root / "context"
    ctx.mkdir(parents=True)
    (ctx / "people.md").write_text(
        "### Cresta Systems\n- Quentin Vale (CEO, Norvik) - lead\n", encoding="utf-8"
    )


def test_vocab_excludes_noise_tokens(tmp_path):
    _write_fixture(tmp_path)
    vocab = build_vocab(tmp_path)
    # generic words + body filler must never become counterpart tokens
    for noise in ("channel", "document", "from", "with", "per",
                  "get", "case", "status", "request", "action"):
        assert noise not in vocab, f"noise token leaked into vocab: {noise}"


def test_real_counterparts_classify(tmp_path):
    _write_fixture(tmp_path)
    vocab = build_vocab(tmp_path)
    # external partner: person + company tokens
    assert vocab.get("norvik") == "external"
    assert vocab.get("quentin") == "external"
    assert vocab.get("vale") == "external"
    assert vocab.get("vantyr") == "external"   # aliases.md
    assert vocab.get("cresta") == "external"   # people.md section header
    # tribe member surname -> tribe
    assert vocab.get("reed") == "tribe"
    # a name that is not a contact is absent
    assert "zephyr" not in vocab


def test_gate_external_counterpart_passes(tmp_path):
    _write_fixture(tmp_path)
    vocab = build_vocab(tmp_path)
    msg = {
        "disposition": "crm", "date": "2026-05-20",
        "text": "Ask Tom to craft a request from Norvik to support the deployment",
        "action_summary": "Norvik formal request for the second deployment region",
    }
    admit, reason, r = gate_message(msg, vocab, SINCE)
    assert admit, reason
    assert "norvik" in r["external"]


def test_gate_tribe_only_drops(tmp_path):
    _write_fixture(tmp_path)
    vocab = build_vocab(tmp_path)
    msg = {
        "disposition": "task", "date": "2026-05-19",
        "text": "Check with Tom regarding Reed's case",
        "action_summary": "P1 task: check with Tom re Reed's case",
    }
    admit, _reason, r = gate_message(msg, vocab, SINCE)
    assert not admit
    assert r["tribe"] and not r["external"]


def test_gate_no_counterpart_drops(tmp_path):
    _write_fixture(tmp_path)
    vocab = build_vocab(tmp_path)
    msg = {
        "disposition": "task", "date": "2026-05-19",
        "text": "Get the employer paper apostilled per the advice",
        "action_summary": "Added P2 task: get document apostilled",
    }
    admit, _reason, _r = gate_message(msg, vocab, SINCE)
    assert not admit


def test_gate_disposition_and_date_floors(tmp_path):
    _write_fixture(tmp_path)
    vocab = build_vocab(tmp_path)
    base = {
        "disposition": "crm", "date": "2026-05-20",
        "text": "request from Norvik", "action_summary": "",
    }
    assert gate_message(base, vocab, SINCE)[0]
    assert not gate_message({**base, "disposition": "ignored"}, vocab, SINCE)[0]
    assert not gate_message({**base, "date": "2026-05-01"}, vocab, SINCE)[0]
