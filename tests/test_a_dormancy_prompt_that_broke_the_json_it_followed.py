#!/usr/bin/env python3
"""`crm-health.py --json --demote-candidates` put prose after the JSON document.

Shard `scripts-04-p3`, finding 3, of the 2026-08-23 engine audit. Two earlier
branches of the same `main()` were moved off stdout for this exact reason, each
with a comment explaining that `crm_next.py` and every other pipeline consumer
parses stdout: the dangling-reference warning, and the `--update` status line.
The third branch that emits non-JSON was left behind.

MEASURED 2026-08-29 against the operator's live CRM, 334 records (169 contact
files, 165 address-book entities). `crm-health.py --json --demote-candidates`
with stdin closed wrote 98 536 bytes to stdout: a valid 169-record JSON array,
then 58 candidate lines, the "To demote" sentence, the confirmation question and
the bare `> ` prompt. `json.loads` on that stream fails with "Extra data: line
3263 column 1". The prompt is on stdout too, because `input("> ")` writes its
argument there whatever the caller intended.

The fix routes the whole branch to stderr when `--json` is set, which is where a
terminal caller still reads it, and leaves stdout as the primary stream
otherwise so the interactive flow is unchanged.

Fixed 2026-08-29.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def health():
    return _load("health_json_demote_mod", "crm-health.py")


# A FIXED date, not `date.today() - 400 days`. Nothing under test recomputes
# staleness from it: `scan_contacts` is stubbed below and every `days_silent`
# is supplied by the fixture, so the only job this string has is to look like a
# stale `last_touch` in a contact file. Reading the host clock would have made
# the corpus differ by the day the suite runs, for no gain, and a test that
# reads the host clock is not a test.
LONG_AGO = "2025-01-01"


@pytest.fixture
def tree(health, monkeypatch, tmp_path):
    """A two-contact corpus, both of them dormancy candidates."""
    for slug, name in (("james-bond", "James Bond"), ("jane-moneypenny", "Jane Moneypenny")):
        (tmp_path / f"{slug}.md").write_text(
            f"---\nname: {name}\ncompany: Acme Telecom\ntype: partner\n"
            f"status: active\nlast_touch: {LONG_AGO}\n---\n\n# {name}\n",
            encoding="utf-8")

    contacts = [
        {"name": "James Bond", "slug": "james-bond", "file": "james-bond.md",
         "company": "Acme Telecom", "type": "partner", "last_touch": LONG_AGO,
         "days_silent": 400, "health": "red", "days_since": 400, "cadence": 30,
         "status": "active", "email": "james.bond@example.com",
         "commitments": []},
        {"name": "Jane Moneypenny", "slug": "jane-moneypenny",
         "file": "jane-moneypenny.md", "company": "Acme Telecom",
         "type": "partner", "last_touch": LONG_AGO, "days_silent": 400,
         "health": "red", "days_since": 400, "cadence": 30, "status": "active",
         "email": "jane.moneypenny@example.com", "commitments": []},
    ]
    monkeypatch.setattr(health, "contacts_dir", lambda p=tmp_path: p)
    monkeypatch.setattr(health, "parse_config", lambda path: {})
    monkeypatch.setattr(health, "scan_contacts", lambda cfg: (contacts, [], [], {}, {}))
    monkeypatch.setattr(health, "format_terminal_report",
                        lambda contacts, warnings=None: "REPORT")
    monkeypatch.setattr("scripts.utils.crm.find_dormancy_candidates",
                        lambda contacts, today, threshold_days: list(contacts))
    return tmp_path


def _run(health, monkeypatch, argv, answer="no"):
    monkeypatch.setattr("builtins.input", lambda *a: answer)
    monkeypatch.setattr(sys, "argv", argv)
    health.main()


def test_the_json_document_is_the_whole_of_stdout(health, tree, monkeypatch, capsys):
    _run(health, monkeypatch,
         ["crm-health.py", "--json", "--demote-candidates"])
    cap = capsys.readouterr()
    parsed = json.loads(cap.out)   # raises if a candidate line landed here
    assert len(parsed) == 2, parsed


def test_the_candidate_list_is_still_reported_under_json(health, tree,
                                                         monkeypatch, capsys):
    """Anchor: moving the branch to stderr must not silence it."""
    _run(health, monkeypatch,
         ["crm-health.py", "--json", "--demote-candidates"])
    err = capsys.readouterr().err
    assert "DORMANCY CANDIDATES" in err, err
    assert "james-bond.md" in err, err


def test_the_confirmation_prompt_is_off_stdout_under_json(health, tree,
                                                          monkeypatch, capsys):
    """`input("> ")` wrote its prompt to stdout, after the JSON document."""
    _run(health, monkeypatch,
         ["crm-health.py", "--json", "--demote-candidates"])
    cap = capsys.readouterr()
    assert "> " not in cap.out, cap.out[-200:]
    assert "Confirm demote" not in cap.out, cap.out[-200:]
    assert "Confirm demote" in cap.err


def test_the_no_candidates_line_is_off_stdout_under_json(health, tree,
                                                         monkeypatch, capsys):
    """The empty-candidate branch printed on the same stream as the document."""
    monkeypatch.setattr("scripts.utils.crm.find_dormancy_candidates",
                        lambda contacts, today, threshold_days: [])
    _run(health, monkeypatch,
         ["crm-health.py", "--json", "--demote-candidates"])
    cap = capsys.readouterr()
    json.loads(cap.out)
    assert "No dormancy candidates" in cap.err, cap.err


def test_the_written_count_is_off_stdout_under_json(health, tree, monkeypatch,
                                                    capsys):
    """The demote summary and the per-file [demoted] lines follow the document."""
    _run(health, monkeypatch,
         ["crm-health.py", "--json", "--demote-candidates"], answer="yes")
    cap = capsys.readouterr()
    json.loads(cap.out)
    assert "[demoted]" in cap.err, cap.err
    assert "2 contacts demoted to dormant." in cap.err, cap.err
    assert "status: dormant" in (tree / "james-bond.md").read_text(encoding="utf-8")


def test_without_json_the_branch_still_owns_stdout(health, tree, monkeypatch,
                                                   capsys):
    """Anchor: the interactive, non-piped flow must be unchanged."""
    _run(health, monkeypatch, ["crm-health.py", "--demote-candidates"])
    cap = capsys.readouterr()
    assert "DORMANCY CANDIDATES" in cap.out, cap.out
    assert "Confirm demote" in cap.out
    assert "DORMANCY CANDIDATES" not in cap.err
