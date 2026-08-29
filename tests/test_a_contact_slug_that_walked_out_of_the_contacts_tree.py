"""Shard 15-p2 finding 7: `scripts/transfer-contact.py`, one finding, measured.

THE ONE PATH COMPONENT NOBODY CHECKED WAS THE ONE THE OPERATOR TYPES MOST.

`--from` and `--to` are both checked against the roster, and the file carries a
long comment explaining why: a typo in `--to` used to create a phantom
`../.heading-os-data-<typo>/` tree, write the contact into it, warn about the
failed commit there, then SUCCEED at committing the deletion in the real repo
and print "Transfer complete:". The contact slug is a path component too, and it
went straight into `from_contacts / f"{slug}.md"` with nothing looking at it.

Measured 2026-08-29. A `crm/config.md` sitting OUTSIDE the contacts tree, with
`--contact "../config"`:

    Contact written: <target repo>/crm/config.md
    Source backed up: <source repo>/crm/config.md.transferred-20260829
    Transfer complete:
      Contact:  ../config

Exit 0. The file was read from outside the contacts directory, rewritten with an
`owner:` field and a transfer note, moved into the target exec's `crm/` root,
renamed away in the source repo, and committed in both. Nothing in the output
said the tool had left the tree it manages.

The fix rejects the same three shapes `get_per_exec_repo_path` already rejects
for an exec slug, plus the empty string, which argparse accepts happily because
`required=True` only means the flag was present.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SRC = ROOT / "scripts" / "transfer-contact.py"

CONTACT_CARD = """---
name: Q Branch
owner: james-bond
company: Acme Telecom
---

# Q Branch

## Interaction Log
"""

# A file that lives in `crm/`, one level ABOVE the contacts directory. This is
# the shape the escape reached: a real workspace has `crm/config.md` there.
OUTSIDE_FILE = """---
title: CRM configuration
---

Health thresholds and cadence policy.
"""


def _load():
    spec = importlib.util.spec_from_file_location("transfer_contact_shard15",
                                                  str(SRC))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["transfer_contact_shard15"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tc():
    return _load()


@pytest.fixture
def crm(tc, tmp_path, monkeypatch):
    """Two repos under tmp_path, every workspace resolver pointed at them.

    Nothing here may reach the operator's overlay: `get_crm_contacts_dir` and
    `get_per_exec_contacts_dir` are the two functions that would, and both are
    replaced. `git_commit` is replaced as well, so no test needs a real repo and
    no test can commit anything.
    """
    source_repo = tmp_path / "source-repo"
    target_repo = tmp_path / "target-repo"
    source_contacts = source_repo / "crm" / "contacts"
    target_contacts = target_repo / "crm" / "contacts"
    source_contacts.mkdir(parents=True)
    target_contacts.mkdir(parents=True)

    (source_contacts / "q-branch.md").write_text(CONTACT_CARD, encoding="utf-8")
    outside = source_repo / "crm" / "config.md"
    outside.write_text(OUTSIDE_FILE, encoding="utf-8")

    commits: list[tuple[Path, list[str], str]] = []

    monkeypatch.setattr(tc, "validate_admin", lambda: None)
    monkeypatch.setattr(tc, "get_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(tc, "get_admin_slugs", lambda: ["james-bond"])
    monkeypatch.setattr(tc, "get_all_active_exec_slugs",
                        lambda: ["marlow-carter"])
    monkeypatch.setattr(tc, "get_crm_contacts_dir", lambda: source_contacts)
    monkeypatch.setattr(tc, "get_per_exec_contacts_dir",
                        lambda slug: target_contacts)
    monkeypatch.setattr(
        tc, "git_commit",
        lambda repo, files, message: commits.append(
            (repo, [str(f) for f in files], message)))

    return types.SimpleNamespace(
        root=tmp_path,
        source_repo=source_repo,
        target_repo=target_repo,
        source_contacts=source_contacts,
        target_contacts=target_contacts,
        outside=outside,
        commits=commits,
    )


def _run(tc, monkeypatch, contact, frm="james-bond", to="marlow-carter"):
    monkeypatch.setattr(sys, "argv", ["transfer-contact.py",
                                      "--contact", contact,
                                      "--from", frm, "--to", to])
    try:
        tc.main()
    except SystemExit as exc:
        return exc.code
    return 0


def _files_under(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


# The three shapes `get_per_exec_repo_path` rejects for an exec slug, plus the
# empty string. `..` alone is the degenerate case that resolves to the contacts
# directory's own parent.
ESCAPES = ["../config", "..", "../../etc/passwd", "sub/q-branch",
           "sub\\q-branch", ""]


# ============================================================
# The escape itself
# ============================================================

@pytest.mark.parametrize("contact", ESCAPES)
def test_a_slug_that_is_not_a_bare_stem_is_refused(tc, crm, monkeypatch,
                                                   contact):
    """The finding. `../config` used to exit 0 with the move already done."""
    assert _run(tc, monkeypatch, contact) == 1


def test_the_file_outside_the_contacts_tree_is_left_exactly_as_it_was(
        tc, crm, monkeypatch):
    """Byte for byte: it was rewritten with an owner field and a note."""
    _run(tc, monkeypatch, "../config")
    assert crm.outside.read_text(encoding="utf-8") == OUTSIDE_FILE


def test_the_file_outside_the_contacts_tree_is_not_moved_away(tc, crm,
                                                              monkeypatch):
    """It was renamed to a `.transferred-` backup and vanished from `crm/`."""
    _run(tc, monkeypatch, "../config")
    assert crm.outside.exists()
    backups = sorted(p.name for p in (crm.source_repo / "crm").glob("*.transferred-*"))
    assert backups == []


def test_nothing_is_written_into_the_target_repo(tc, crm, monkeypatch):
    """`crm/config.md` landed in the target exec's repo root, outside contacts."""
    before = _files_under(crm.target_repo)
    _run(tc, monkeypatch, "../config")
    assert _files_under(crm.target_repo) == before


def test_no_repository_is_committed(tc, crm, monkeypatch):
    """Both halves of the escape were committed and made durable."""
    _run(tc, monkeypatch, "../config")
    assert crm.commits == []


def test_the_refusal_names_the_value_it_refused(tc, crm, monkeypatch, capsys):
    """An operator who mistypes a slug has to be told which argument was wrong."""
    _run(tc, monkeypatch, "../config")
    out = capsys.readouterr().out
    assert "--contact" in out
    assert "'../config'" in out


def test_the_guard_refuses_the_shape_rather_than_the_missing_file(tc, crm,
                                                                  monkeypatch,
                                                                  capsys):
    """A guard that fires only when the escaped path is absent is not a guard.

    Delete the escape target and the run still has to refuse the SHAPE. Both
    exits are code 1, so the message is what separates "I will not resolve that
    slug" from "I resolved it and found nothing there".
    """
    crm.outside.unlink()
    _run(tc, monkeypatch, "../config")
    out = capsys.readouterr().out
    assert "is not a contact slug" in out
    assert "Source contact not found" not in out


# ============================================================
# The controls: a real slug must still transfer
# ============================================================

def test_a_real_slug_still_transfers(tc, crm, monkeypatch):
    assert _run(tc, monkeypatch, "q-branch") == 0
    assert (crm.target_contacts / "q-branch.md").exists()


def test_the_transferred_card_carries_the_new_owner(tc, crm, monkeypatch):
    _run(tc, monkeypatch, "q-branch")
    text = (crm.target_contacts / "q-branch.md").read_text(encoding="utf-8")
    assert "owner: marlow-carter" in text
    assert "Transferred from james-bond to marlow-carter" in text


def test_the_source_card_is_backed_up_not_left_in_place(tc, crm, monkeypatch):
    _run(tc, monkeypatch, "q-branch")
    assert not (crm.source_contacts / "q-branch.md").exists()
    backups = sorted(p.name for p in
                     crm.source_contacts.glob("q-branch.md.transferred-*"))
    assert backups, "no backup was written; the assertion below is vacuous"
    assert len(backups) == 1


def test_a_real_transfer_still_commits_both_repositories(tc, crm, monkeypatch):
    _run(tc, monkeypatch, "q-branch")
    assert crm.commits, "no commit was attempted; a control that proves nothing"
    assert [repo for repo, _files, _msg in crm.commits] == [
        crm.target_repo / "crm", crm.source_repo / "crm"]


@pytest.mark.parametrize("contact", ["q-branch", "priya-anand", "j-bond-007",
                                     "a.b", "Q_Branch"])
def test_an_ordinary_slug_shape_is_not_caught_by_the_guard(tc, crm, monkeypatch,
                                                           contact):
    """The other direction: the guard must not refuse legitimate stems.

    A stem with a dot in it is the interesting one. The check looks for the two
    characters `..`, not for any dot, so `a.b` has to pass. Each of these hits
    the "source contact not found" exit rather than the slug refusal, which is
    the same code but a different message, so the message is what is asserted.
    """
    monkeypatch.setattr(sys, "argv", ["transfer-contact.py",
                                      "--contact", contact,
                                      "--from", "james-bond",
                                      "--to", "marlow-carter"])
    (crm.source_contacts / f"{contact}.md").write_text(CONTACT_CARD,
                                                       encoding="utf-8")
    try:
        tc.main()
    except SystemExit as exc:  # pragma: no cover - a refusal is the failure
        raise AssertionError(
            f"the guard refused {contact!r}, which is a valid stem") from exc
    assert (crm.target_contacts / f"{contact}.md").exists()
