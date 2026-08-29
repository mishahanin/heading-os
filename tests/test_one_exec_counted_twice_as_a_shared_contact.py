"""A fleet dashboard that called one owner "multi-owner".

Covers the k3 audit shard `scripts-00-p1`, finding 1, for
`scripts/admin-health.py`.

`find_shared_contacts` maps a contact identity to the executives who hold a
record for it, and "shared" is `len(owners) > 1`. The value was a LIST, and a
list counts one exec twice: two files in ONE overlay that resolve to the same
identity (`james-bond.md` and `bond-james.md`, both `name: James Bond`)
appended the same slug twice, so the dashboard printed
`Shared contacts (multi-owner): 1` over a contact exactly one person owns.

`aggregate-crm.detect_shared_contacts` has always used a set, so the two fleet
tools printed different numbers for the same directory, which is the class of
disagreement `admin-health.py`'s own comments say was closed.

Nothing here clones, pulls, or reaches GitHub, and every path is under
`tmp_path`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

CONTACT = """---
name: {name}
company: Acme Telecom
---

Notes.
"""


@pytest.fixture(scope="module")
def ah():
    path = ROOT / "scripts" / "admin-health.py"
    spec = importlib.util.spec_from_file_location("admin_health_mod", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _overlay(tmp_path: Path, slug: str, filenames_to_names: dict) -> Path:
    contacts = tmp_path / slug / "crm" / "contacts"
    contacts.mkdir(parents=True)
    for filename, name in filenames_to_names.items():
        (contacts / filename).write_text(CONTACT.format(name=name),
                                         encoding="utf-8")
    return contacts


def test_two_files_one_exec_are_not_a_shared_contact(ah, tmp_path, monkeypatch):
    """The measured defect: one owner, two spellings, reported as shared."""
    dirs = {
        "misha-hanin": _overlay(tmp_path, "misha-hanin", {
            "james-bond.md": "James Bond",
            "bond-james.md": "James Bond",
        }),
    }
    monkeypatch.setattr(ah, "get_per_exec_contacts_dir", lambda slug: dirs[slug])

    # The corpus is real: two files, both parsed, both resolving to one identity.
    assert len(list(dirs["misha-hanin"].iterdir())) == 2

    assert ah.find_shared_contacts([("misha-hanin", tmp_path)]) == 0


def test_the_same_person_in_two_overlays_is_still_shared(ah, tmp_path,
                                                         monkeypatch):
    """The negative direction: the fix must not stop counting a real share.

    Different filenames, different execs, one identity. Without this the "fix"
    could be `return 0` and the test above would still pass.
    """
    dirs = {
        "misha-hanin": _overlay(tmp_path, "misha-hanin",
                                {"james-bond.md": "James Bond"}),
        "jane-moneypenny": _overlay(tmp_path, "jane-moneypenny",
                                    {"bond-james.md": "James Bond"}),
    }
    monkeypatch.setattr(ah, "get_per_exec_contacts_dir", lambda slug: dirs[slug])

    assert ah.find_shared_contacts([("misha-hanin", tmp_path),
                                    ("jane-moneypenny", tmp_path)]) == 1


def test_a_duplicate_inside_one_overlay_does_not_inflate_a_real_share(
        ah, tmp_path, monkeypatch):
    """Both effects at once, which is where a list and a set differ by count.

    Two execs share James Bond, and one of them also holds a duplicate file for
    him plus a contact nobody else has. The answer is 1, not 2.
    """
    dirs = {
        "misha-hanin": _overlay(tmp_path, "misha-hanin", {
            "james-bond.md": "James Bond",
            "bond-james.md": "James Bond",
            "eve-rosewater.md": "Eve Rosewater",
        }),
        "jane-moneypenny": _overlay(tmp_path, "jane-moneypenny",
                                    {"james-bond.md": "James Bond"}),
    }
    monkeypatch.setattr(ah, "get_per_exec_contacts_dir", lambda slug: dirs[slug])

    assert ah.find_shared_contacts([("misha-hanin", tmp_path),
                                    ("jane-moneypenny", tmp_path)]) == 1


def test_the_dashboard_and_the_aggregator_agree_on_one_corpus(
        ah, tmp_path, monkeypatch):
    """The point of the fix, asserted against the other tool's real rule.

    `aggregate-crm.detect_shared_contacts` groups on identity and takes a SET of
    owner slugs. Deriving the expected number from that function rather than
    writing `0` by hand means a future change to either rule shows up here.
    """
    agg_spec = importlib.util.spec_from_file_location(
        "aggregate_crm_mod", str(ROOT / "scripts" / "aggregate-crm.py"))
    agg = importlib.util.module_from_spec(agg_spec)
    agg_spec.loader.exec_module(agg)

    dirs = {
        "misha-hanin": _overlay(tmp_path, "misha-hanin", {
            "james-bond.md": "James Bond",
            "bond-james.md": "James Bond",
        }),
    }
    monkeypatch.setattr(ah, "get_per_exec_contacts_dir", lambda slug: dirs[slug])

    records = [{"name": "James Bond", "company": "Acme Telecom",
                "owner_slug": "misha-hanin"},
               {"name": "James Bond", "company": "Acme Telecom",
                "owner_slug": "misha-hanin"}]
    aggregator_says = len(agg.detect_shared_contacts(records))

    assert ah.find_shared_contacts([("misha-hanin", tmp_path)]) == aggregator_says
