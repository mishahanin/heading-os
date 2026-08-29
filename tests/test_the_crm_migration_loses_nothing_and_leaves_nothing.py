#!/usr/bin/env python3
"""The CRM migration: three ways to lose or duplicate the operator's history.

`scripts/crm_migrate_to_entity_model.py` is a one-shot, hard-to-rehearse
migration over every relationship record the operator has. The 2026-08-23
engine audit, shard `scripts-04-p3`, found three defects in it that all pass
validation, because `validate-crm-schema.py` checks file SHAPE and not record
count:

* **Two of the operator's own files in one group overwrote each other.** A
  group is "the same person", and it can hold two legacy CEO files for them —
  which is precisely what this migration exists to merge. Every one of them
  rendered to the SAME `contacts_staging/<slug>.md`, so the last write won and
  the earlier record's Interaction Log and Active Commitments were gone. Data
  loss with a green checkmark.
* **`--apply` never removed the legacy files.** Legacy names are name-derived
  and the new ones are slug-derived, so `os.replace` never overwrote them and
  `crm/contacts/` was left holding both generations. Every downstream consumer
  saw each contact twice: doubled health scores, duplicated radar rows. The
  backup taken two steps earlier is what makes the deletion safe, and its
  existence is why the deletion was clearly meant to be there.
* **`--rollback` restored the backups and left everything `--apply` created.**
  An apply-then-rollback cycle ended with BOTH generations on disk, under the
  words "Rollback complete".

A fourth finding, the dropped `cadence` field, was already fixed in the tree
before this audit output was written, with the 2026-05-15 incident named in the
comment. It is covered here anyway, because it is the same shape and the fix is
one dictionary key away from being lost again.

The collision case is REFUSED rather than merged: concatenating two interaction
logs is a judgement about what actually happened with a person, and a migration
must not make that judgement silently.

Fixed 2026-08-24.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scripts.crm_migrate_to_entity_model as mig  # noqa: E402

# A module-scope `TODAY = datetime.now(...)` stood here and four assertions
# rebuilt `crm/.migration-backup/<TODAY>` from it. `cmd_apply` names that same
# directory from ITS OWN clock, at the moment the test body runs, and the two
# reads are not the same instant: the constant is computed once at IMPORT, and
# under `pytest -n 8` the bodies run minutes later. Two ways that went red on
# code nobody had touched, both MEASURED 2026-08-30:
#
#  * clock 0.8 s before local midnight, this file alone -> 3 failures; at 1.0 s
#    and at 0.6 s, none. Midnight landed between the import and the bodies.
#  * whole suite at `TZ=America/New_York`, no clock shift at all -> the two
#    backup-reading tests failed. `test_a_high_water_mark_read_off_the_wrong_
#    clock.py` sets TZ to Pacific/Kiritimati and then DELETES the variable in
#    its `finally`, so libc falls back to /etc/localtime rather than to the
#    exported zone. Any test importing this module on the other side of that
#    leak gets one calendar day and the code under test gets another.
#
# The four sites read the directory off disk now (see `_backup_dir`). The date
# is still needed for the review-map filename, so it is computed inside the
# fixture, next to the call that consumes it, rather than at import.
# A test that reads the host clock is not a test; it is a test of the second it
# ran at.

LEGACY = """---
name: {name}
email: {email}
company: Universal Exports
type: partner
cadence: 14
last_touch: 2026-08-01
---

# {name}

## Background

Quartermaster's contact.

## Active Commitments

- {commitment}

## Interaction Log

- 2026-08-01 — {log}
"""


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A whole fake engine+data pair, wired through the module's own resolvers."""
    ws = tmp_path / "engine"
    data = tmp_path / "data"
    contacts = data / "crm" / "contacts"
    outputs = data / "outputs"
    contacts.mkdir(parents=True)
    (outputs / "operations" / "crm").mkdir(parents=True)
    ws.mkdir()

    monkeypatch.setattr(mig, "get_workspace_root", lambda: ws)
    monkeypatch.setattr(mig, "get_crm_contacts_dir", lambda: contacts)
    monkeypatch.setattr(mig, "get_outputs_dir", lambda: outputs)
    # --apply wants a review map, and prints a note when the newest one is not
    # from today. Computed HERE, milliseconds before the `cmd_apply` that reads
    # it, so the two agree; at module scope they were minutes apart.
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    (outputs / "operations" / "crm" / f"{today}_migration-map.md").write_text(
        "# map\n", encoding="utf-8")

    # Both validators are separate scripts with their own tests; here they are
    # the gate that must PASS so the steps after it are reachable.
    monkeypatch.setattr(
        mig.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 0, "", ""))
    return ws, data, contacts


def _backup_dir(data: Path) -> Path:
    """The dated backup directory `--apply` created, read off disk.

    Naming it from this process's clock is what made three tests here fail on
    the second they ran at; see the note at the top of the file. The two
    assertions keep the property the old spelling was there for: exactly one
    backup exists, and it is still filed under a date rather than a fixed name,
    so the pin cannot rot back into the defect it replaces.
    """
    root = data / "crm" / ".migration-backup"
    dirs = sorted(p for p in root.iterdir() if p.is_dir())
    assert len(dirs) == 1, f"expected one backup directory under {root}, got {dirs}"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", dirs[0].name), (
        f"--apply must file its backup under a date, not {dirs[0].name!r}")
    return dirs[0]


def _legacy_file(contacts: Path, stem: str, name: str, email: str,
                 commitment: str, log: str) -> Path:
    path = contacts / f"{stem}.md"
    path.write_text(LEGACY.format(name=name, email=email,
                                  commitment=commitment, log=log),
                    encoding="utf-8")
    return path


def _record(path: Path, name: str, email: str) -> dict:
    return mig._record_from(path, "owner-exec-a", {
        "name": name, "email": email, "company": "Universal Exports",
        "type": "partner", "cadence": 14, "last_touch": "2026-08-01",
    })


# ---------------------------------------------------------------------------
# The collision
# ---------------------------------------------------------------------------

def test_two_of_the_operators_files_for_one_person_refuse_to_merge(
        workspace, monkeypatch, capsys):
    ws, data, contacts = workspace
    a = _legacy_file(contacts, "james-bond", "James Bond", "007@example.com",
                     "Deliver the briefcase", "Met in Vienna")
    b = _legacy_file(contacts, "bond-james", "James Bond", "007@example.com",
                     "Return the Aston", "Called from Istanbul")
    monkeypatch.setattr(mig, "scan_all_contacts",
                        lambda: ([_record(a, "James Bond", "007@example.com"),
                                 _record(b, "James Bond", "007@example.com")], []))

    rc = mig.cmd_apply()
    out = capsys.readouterr().out

    assert rc == 1, "the apply must refuse, not pick a winner"
    assert "james-bond.md would be written from 2 files" in out
    assert str(a) in out and str(b) in out, (
        "the operator has to be told WHICH files to merge"
    )
    assert a.exists() and b.exists(), "a refused apply must change nothing"
    assert not (data / "crm" / ".migration-staging").exists(), "staging leaked"


def test_a_refused_apply_writes_no_relationship_record(workspace, monkeypatch):
    ws, data, contacts = workspace
    # Neither legacy stem may EQUAL the slug, or the file the assertion looks
    # for exists before the apply runs and the test proves nothing. That is the
    # shape of the defect in miniature: legacy names are name-derived and the
    # new ones are slug-derived, and only sometimes do they coincide.
    a = _legacy_file(contacts, "Bond, James (MI6)", "James Bond",
                     "007@example.com", "One", "First")
    b = _legacy_file(contacts, "bond-j", "James Bond", "007@example.com",
                     "Two", "Second")
    monkeypatch.setattr(mig, "scan_all_contacts",
                        lambda: ([_record(a, "James Bond", "007@example.com"),
                                 _record(b, "James Bond", "007@example.com")], []))
    mig.cmd_apply()
    assert not (contacts / "james-bond.md").exists(), (
        "the slug-named record was written from one of two files and the other "
        "record's Interaction Log was silently gone"
    )


def test_two_people_who_are_not_the_same_person_still_apply(workspace,
                                                            monkeypatch):
    """Anchor: the refusal must trigger on a COLLISION, not on two records."""
    ws, data, contacts = workspace
    a = _legacy_file(contacts, "james-bond", "James Bond", "007@example.com",
                     "One", "First")
    b = _legacy_file(contacts, "felix-leiter", "Felix Leiter", "felix@example.com",
                     "Two", "Second")
    monkeypatch.setattr(mig, "scan_all_contacts",
                        lambda: ([_record(a, "James Bond", "007@example.com"),
                                 _record(b, "Felix Leiter", "felix@example.com")], []))
    assert mig.cmd_apply() == 0
    assert (contacts / "james-bond.md").exists()
    assert (contacts / "felix-leiter.md").exists()


# ---------------------------------------------------------------------------
# The legacy files
# ---------------------------------------------------------------------------

@pytest.fixture
def applied(workspace, monkeypatch):
    """One clean apply over a single legacy file with a non-slug name."""
    ws, data, contacts = workspace
    legacy = _legacy_file(contacts, "Bond, James (MI6)", "James Bond",
                          "007@example.com", "Deliver the briefcase",
                          "Met in Vienna")
    monkeypatch.setattr(mig, "scan_all_contacts",
                        lambda: ([_record(legacy, "James Bond", "007@example.com")], []))
    rc = mig.cmd_apply()
    return rc, ws, data, contacts, legacy


def test_the_apply_succeeds(applied):
    rc, ws, data, contacts, legacy = applied
    assert rc == 0
    assert (contacts / "james-bond.md").exists()


def test_the_legacy_file_is_gone_after_apply(applied):
    rc, ws, data, contacts, legacy = applied
    assert not legacy.exists(), (
        "both generations were left in crm/contacts/, so every consumer saw "
        "the contact twice and health scores double-counted"
    )


def test_the_legacy_file_is_in_the_backup(applied):
    """The deletion is only safe because the backup happened first."""
    rc, ws, data, contacts, legacy = applied
    backup = _backup_dir(data)
    survivors = list(backup.rglob("*.md"))
    assert survivors, f"nothing backed up under {backup}"
    assert any("Met in Vienna" in p.read_text(encoding="utf-8")
               for p in survivors), "the original body was not preserved"


def test_the_interaction_log_survives_the_migration(applied):
    rc, ws, data, contacts, legacy = applied
    text = (contacts / "james-bond.md").read_text(encoding="utf-8")
    assert "Met in Vienna" in text
    assert "Deliver the briefcase" in text


def test_cadence_survives_the_migration(applied):
    """Already fixed before this shard; held because the fix is one key wide."""
    rc, ws, data, contacts, legacy = applied
    text = (contacts / "james-bond.md").read_text(encoding="utf-8")
    assert "cadence: 14" in text, (
        "the 2026-05-15 run stripped cadence from about a hundred live contacts "
        "and the radar went blind for weeks"
    )


def test_the_applied_manifest_names_what_was_created_and_removed(applied):
    rc, ws, data, contacts, legacy = applied
    manifest_path = _backup_dir(data) / "applied-manifest.json"
    assert manifest_path.exists(), "rollback has nothing to undo without this"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["created_contacts"] == ["james-bond.md"]
    assert manifest["removed_legacy"] == [str(legacy)]
    assert manifest["created_address_book"] == ["james-bond.md"]


# ---------------------------------------------------------------------------
# The rollback
# ---------------------------------------------------------------------------

def test_rollback_removes_what_apply_created(applied, monkeypatch, capsys):
    rc, ws, data, contacts, legacy = applied
    monkeypatch.setattr("builtins.input", lambda *a: "yes")

    # `X or True` is unconditionally true: Python evaluates the comparison,
    # discards it, and yields True. No return value could make it red. The
    # comment that licensed it ("returns None on the success path") was also
    # false - `cmd_rollback` is annotated `-> int` and its success path returns
    # 0 - so a `return 1` there would exit a clean rollback non-zero and every
    # scripted caller would read success as failure, with this line still green.
    assert mig.cmd_rollback() == 0, "a successful rollback must exit 0"
    assert not (contacts / "james-bond.md").exists(), (
        "an apply-then-rollback cycle left both generations on disk while "
        "printing 'Rollback complete'"
    )
    assert not (data / "crm" / "address-book").exists()


def test_rollback_restores_the_original(applied, monkeypatch):
    rc, ws, data, contacts, legacy = applied
    monkeypatch.setattr("builtins.input", lambda *a: "yes")
    mig.cmd_rollback()
    assert legacy.exists(), "the original was not restored"
    assert "Met in Vienna" in legacy.read_text(encoding="utf-8")


def test_rollback_without_a_manifest_says_what_it_cannot_undo(applied,
                                                              monkeypatch,
                                                              capsys):
    """A backup taken before the manifest existed must not be guessed at."""
    rc, ws, data, contacts, legacy = applied
    (_backup_dir(data) / "applied-manifest.json").unlink()
    monkeypatch.setattr("builtins.input", lambda *a: "yes")
    mig.cmd_rollback()
    out = capsys.readouterr().out
    assert "predates the manifest" in out, (
        "silently leaving the created records is exactly the defect; if it "
        "cannot undo them it has to say so"
    )
    assert (contacts / "james-bond.md").exists(), (
        "with no manifest it must not guess which files to delete"
    )


def test_a_corrupt_manifest_aborts_the_rollback(applied, monkeypatch, capsys):
    rc, ws, data, contacts, legacy = applied
    path = _backup_dir(data) / "applied-manifest.json"
    path.write_text('{"created_contacts": [', encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda *a: "yes")
    assert mig.cmd_rollback() == 1
    assert "aborting" in capsys.readouterr().out.lower()


def test_rollback_still_asks_before_it_acts(applied, monkeypatch, capsys):
    """Anchor: the confirmation gate must survive the new removal step."""
    rc, ws, data, contacts, legacy = applied
    monkeypatch.setattr("builtins.input", lambda *a: "no")
    assert mig.cmd_rollback() == 1
    assert (contacts / "james-bond.md").exists(), "it acted without consent"


# ---------------------------------------------------------------------------
# The import comment
# ---------------------------------------------------------------------------

def test_json_is_actually_used_now():
    """The header comment claimed json was "for migration map writing"; the map
    is markdown, written by `write_review_map`, and no `json.` call existed
    anywhere in the file.

    Only the first half is checkable. Scanning for the wrong sentence is not a
    test here: the corrected comment quotes the claim it removed, so a prose
    scan fails on the fix's own explanation. What IS checkable is whether the
    import earns its place.
    """
    src = Path(mig.__file__).read_text(encoding="utf-8")
    assert "json.dumps(" in src and "json.loads(" in src, (
        "a dead import under a comment describing what it is for sends a reader "
        "looking for a code path that does not exist"
    )
