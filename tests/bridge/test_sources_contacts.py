"""Unit tests for the /contacts source (CEO + executive CRM, combined)."""
import pytest

import scripts.bridge_daemon.sources.contacts as contacts_src
from scripts.bridge_daemon.sources.contacts import list_contacts, read_one_contact


def _contact_md(name, **fm):
    """Build a CRM contact markdown file body with frontmatter + H1 name."""
    fm_lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
    return f"---\n{fm_lines}\n---\n\n# {name}\n\nNotes.\n"


@pytest.fixture(autouse=True)
def _stub_registry(monkeypatch):
    """Default: an empty exec registry, so a test that names no executive
    reaches no roster and no sibling directory.

    The stated reason for this fixture used to be "so the legacy crm-central
    fallback path is exercised". That path was deleted on 2026-08-30 with the
    root it read. The fixture stays for the reason it was always ALSO doing
    its real work: unstubbed, `get_all_active_exec_slugs()` reads the live
    roster out of the operator's own data root, and every slug it returned
    would be resolved against the operator's real `.heading-os-data-*`
    siblings. A test must never read those. Tests that need an executive
    override this with an invented slug.
    """
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs", list)


def _ws(tmp_path):
    """A workspace dir nested under tmp_path, so .parent is controllable.

    `list_contacts` resolves each executive's contacts at
    `<ws>/../.heading-os-data-{slug}/crm/contacts/`, so the sibling overlays
    the tests build land under tmp_path and never under the real workspace.
    """
    ws = tmp_path / "workspace"
    (ws / "crm" / "contacts").mkdir(parents=True)
    return ws


def _ceo_contact(ws, slug, name, **fm):
    (ws / "crm" / "contacts" / f"{slug}.md").write_text(
        _contact_md(name, **fm), encoding="utf-8")


def _exec_overlay(tmp_path, exec_slug):
    """The sandboxed path of an exec's DATA overlay contacts dir."""
    return tmp_path / f".heading-os-data-{exec_slug}" / "crm" / "contacts"


def _exec_contact(tmp_path, exec_slug, slug, name, **fm):
    """Write a contact into an exec's DATA overlay - the one live source."""
    d = _exec_overlay(tmp_path, exec_slug)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.md").write_text(_contact_md(name, **fm), encoding="utf-8")


def _dead_root_contact(tmp_path, exec_slug, slug, name, **fm):
    """Write a contact into one of the two roots the 2026-08-23 migration
    retired. Nothing may ever read these again; the tests that call this
    assert exactly that."""
    for d in (tmp_path / f"31c-crm-{exec_slug}" / "contacts",
              tmp_path / "31c-crm-central" / "contacts" / exec_slug):
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{slug}.md").write_text(_contact_md(name, **fm), encoding="utf-8")


def test_list_contacts_ceo_only(tmp_path):
    ws = _ws(tmp_path)
    _ceo_contact(ws, "alice", "Alice Smith", relationship_type="prospect", last_touch="2026-05-01")
    _ceo_contact(ws, "bob", "Bob Jones", relationship_type="partner")
    d = list_contacts(ws, data_root=ws)
    assert d["total"] == 2
    assert d["owner_counts"] == {"ceo": 2}
    assert all(c["owner"] == "ceo" for c in d["contacts"])


def test_list_contacts_combines_execs(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    _ceo_contact(ws, "alice", "Alice", relationship_type="prospect")
    _exec_contact(tmp_path, "marlow-carter", "jordan-kim", "Jordan Kim",
                  relationship_type="prospect")
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs",
                        lambda: ["marlow-carter"])
    d = list_contacts(ws, data_root=ws)
    assert d["total"] == 2
    assert d["owner_counts"] == {"ceo": 1, "marlow-carter": 1}
    exec_row = next(c for c in d["contacts"] if c["owner"] != "ceo")
    assert exec_row["owner_label"] == "Marlow Carter"


def test_list_contacts_skips_the_operators_own_overlay(tmp_path, monkeypatch):
    """An overlay under the operator's OWN slug is skipped: their live
    crm/contacts/ is authoritative, so reading it too would double every row.

    The registry must LIST that slug for the guard to have anything to refuse,
    and the overlay must exist on disk, or the row is absent for the boring
    reason and the test proves nothing. Until 2026-08-30 this seeded the
    retired crm-central root, so after the migration deleted that reader the
    test passed no matter what the guard did. Mutation-checked: dropping
    `or owner == self_dir` from the registry loop now fails this test.
    """
    monkeypatch.setattr(contacts_src, "_crm_central_self_dir", lambda: "misha-hanin")
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs",
                        lambda: ["misha-hanin"])
    ws = _ws(tmp_path)
    _ceo_contact(ws, "alice", "Alice", relationship_type="prospect")
    _exec_contact(tmp_path, "misha-hanin", "stale-contact", "Stale",
                  relationship_type="prospect")
    assert _exec_overlay(tmp_path, "misha-hanin").is_dir()  # the guard's target exists
    d = list_contacts(ws, data_root=ws)
    assert d["total"] == 1
    assert {c["slug"] for c in d["contacts"]} == {"alice"}


def test_list_contacts_skips_readme_and_underscore(tmp_path):
    ws = _ws(tmp_path)
    _ceo_contact(ws, "alice", "Alice", relationship_type="prospect")
    (ws / "crm" / "contacts" / "README.md").write_text("# readme", encoding="utf-8")
    (ws / "crm" / "contacts" / "_template.md").write_text("# tmpl", encoding="utf-8")
    d = list_contacts(ws, data_root=ws)
    assert {c["slug"] for c in d["contacts"]} == {"alice"}


def test_list_contacts_slug_collision_across_owners(tmp_path, monkeypatch):
    """The same slug under two owners yields two distinct rows."""
    ws = _ws(tmp_path)
    _ceo_contact(ws, "jordan-kim", "Jordan Kim", relationship_type="prospect")
    _exec_contact(tmp_path, "marlow-carter", "jordan-kim", "Jordan Kim",
                  relationship_type="prospect")
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs",
                        lambda: ["marlow-carter"])
    d = list_contacts(ws, data_root=ws)
    assert d["total"] == 2
    owners = sorted(c["owner"] for c in d["contacts"] if c["slug"] == "jordan-kim")
    assert owners == ["ceo", "marlow-carter"]


def test_list_contacts_uncategorised_is_other(tmp_path):
    ws = _ws(tmp_path)
    _ceo_contact(ws, "alice", "Alice")  # no relationship_type
    d = list_contacts(ws, data_root=ws)
    assert d["contacts"][0]["relationship_type"] == "other"


def test_list_contacts_empty(tmp_path):
    ws = _ws(tmp_path)
    d = list_contacts(ws, data_root=ws)
    assert d["total"] == 0
    assert d["contacts"] == []


def test_read_one_contact_ceo(tmp_path, monkeypatch):
    """The CEO owner label follows the configured operator, not a literal.

    This asserted one instance's operator name, which the source held as a
    hardcoded string until 2026-08-28. Once `_owner_label` started resolving
    through `operator_identity`, the assertion measured the ENVIRONMENT rather
    than the code: it passed on a machine with a data overlay and failed in CI,
    which has none and resolves the generic default. Injecting the identity
    keeps the real invariant - the label comes from the seam - and makes the
    test give the same answer everywhere.
    """
    monkeypatch.setattr(contacts_src, "get_operator", lambda: {"name": "Ada Lovelace"})
    ws = _ws(tmp_path)
    _ceo_contact(ws, "alice", "Alice Smith", relationship_type="prospect")
    r = read_one_contact(ws, "ceo", "alice", data_root=ws)
    assert r["ok"] is True
    assert r["name"] == "Alice Smith"
    assert r["owner_label"] == "Ada Lovelace"


def test_read_one_contact_exec(tmp_path):
    ws = _ws(tmp_path)
    _exec_contact(tmp_path, "marlow-carter", "jordan-kim", "Jordan Kim",
                  relationship_type="prospect")
    r = read_one_contact(ws, "marlow-carter", "jordan-kim", data_root=ws)
    assert r["ok"] is True
    assert r["name"] == "Jordan Kim"
    assert r["owner_label"] == "Marlow Carter"


def test_read_one_contact_rejects_bad_slug(tmp_path):
    ws = _ws(tmp_path)
    assert read_one_contact(ws, "ceo", "../etc/passwd", data_root=ws)["ok"] is False
    assert read_one_contact(ws, "ceo", "", data_root=ws)["ok"] is False


def test_read_one_contact_rejects_bad_owner(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    _ceo_contact(ws, "alice", "Alice")
    assert read_one_contact(ws, "../escape", "alice", data_root=ws)["ok"] is False
    # The operator's own slug is not a valid drill-down owner. Pin the seam and
    # build the overlay, so the refusal is the guard's doing and not merely a
    # directory that happens not to exist.
    monkeypatch.setattr(contacts_src, "_crm_central_self_dir", lambda: "misha-hanin")
    _exec_contact(tmp_path, "misha-hanin", "alice", "Alice Stale")
    assert read_one_contact(ws, "misha-hanin", "alice", data_root=ws)["ok"] is False


def test_read_one_contact_not_found(tmp_path):
    ws = _ws(tmp_path)
    r = read_one_contact(ws, "ceo", "ghost", data_root=ws)
    assert r["ok"] is False
    assert "not found" in r["error"]


# Executive DATA-overlay coverage. Added 2026-05-27 against the per-exec mirror
# repo; repointed 2026-08-30 to `../.heading-os-data-{slug}/crm/contacts/` after
# a measurement found the daemon still reading two roots that the 2026-08-23
# migration had deleted from disk, rendering every executive as zero contacts.
# The three "which of the two roots wins" tests that used to sit here tested a
# precedence that no longer has two sides; they are replaced by the invariant
# that survives, which is that there is exactly ONE source.


def test_list_contacts_reads_the_exec_data_overlay(tmp_path, monkeypatch):
    """LOAD-BEARING. A contacts dir at `../.heading-os-data-{slug}/crm/contacts/`
    is found and its contacts are counted.

    This is the measurement the migration exists to satisfy, and it FAILS on
    the pre-2026-08-30 resolver: with a fixture at this layout the resolver
    returned None and `list_contacts` reported 0 contacts and an empty
    `owner_counts`, for a directory whose `is_dir()` was True.
    """
    ws = _ws(tmp_path)
    _exec_contact(tmp_path, "marlow-carter", "taylor-reed", "Taylor Reed",
                  relationship_type="prospect")
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs",
                        lambda: ["marlow-carter"])

    d = list_contacts(ws, data_root=ws)
    assert d["total"] == 1, "the exec's DATA overlay was not read"
    assert d["owner_counts"] == {"marlow-carter": 1}
    row = d["contacts"][0]
    assert row["owner"] == "marlow-carter"
    assert row["slug"] == "taylor-reed"


def test_a_retired_root_is_not_read_even_when_it_still_exists(tmp_path, monkeypatch):
    """Both retired roots may still be lying on disk from before the migration.
    Neither is a source. Only the DATA overlay's copy is listed."""
    ws = _ws(tmp_path)
    _exec_contact(tmp_path, "marlow-carter", "jordan-kim", "Jordan from the overlay",
                  relationship_type="prospect")
    _dead_root_contact(tmp_path, "marlow-carter", "jordan-kim", "Jordan from a dead root",
                       relationship_type="prospect")
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs",
                        lambda: ["marlow-carter"])

    d = list_contacts(ws, data_root=ws)
    assert d["total"] == 1
    assert d["contacts"][0]["name"] == "Jordan from the overlay"


def test_an_exec_with_no_overlay_on_disk_is_simply_absent(tmp_path, monkeypatch):
    """NEGATIVE. A registry entry whose overlay is not cloned here yields no
    rows and no exception - the page degrades by one executive, it does not
    500. Replaces the crm-central fallback test: there is no second root to
    fall back to, so "missing" is now a terminal answer and must be a quiet
    one."""
    ws = _ws(tmp_path)
    _ceo_contact(ws, "alice", "Alice", relationship_type="prospect")
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs",
                        lambda: ["nina-falk"])
    assert not _exec_overlay(tmp_path, "nina-falk").exists()

    d = list_contacts(ws, data_root=ws)
    assert d["owner_counts"] == {"ceo": 1}
    assert "nina-falk" not in {c["owner"] for c in d["contacts"]}


def test_an_offboarded_overlay_off_the_registry_is_never_globbed(tmp_path, monkeypatch):
    """NEGATIVE, and the reason no `.heading-os-data-*` glob replaced the
    deleted backstop. An overlay left on disk for someone no longer on the
    roster must not resurrect them onto the CEO's page."""
    ws = _ws(tmp_path)
    _exec_contact(tmp_path, "quinn-abara", "old-lead", "Old Lead",
                  relationship_type="prospect")
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs", list)

    d = list_contacts(ws, data_root=ws)
    assert d["total"] == 0, (
        "an overlay absent from the registry was scanned anyway; the registry "
        "is the only authority on who is an executive"
    )


def test_read_one_contact_from_the_exec_data_overlay(tmp_path):
    ws = _ws(tmp_path)
    _exec_contact(tmp_path, "marlow-carter", "taylor-reed", "Taylor Reed",
                  relationship_type="prospect")
    r = read_one_contact(ws, "marlow-carter", "taylor-reed", data_root=ws)
    assert r["ok"] is True
    assert r["name"] == "Taylor Reed"


def test_read_one_contact_ignores_a_retired_root(tmp_path):
    """The drill-down resolves through the same single source as the listing,
    so a row and the page it opens can never disagree about which file they
    mean."""
    ws = _ws(tmp_path)
    _exec_contact(tmp_path, "marlow-carter", "jordan-kim", "Jordan from the overlay",
                  relationship_type="prospect")
    _dead_root_contact(tmp_path, "marlow-carter", "jordan-kim", "Jordan from a dead root",
                       relationship_type="prospect")
    r = read_one_contact(ws, "marlow-carter", "jordan-kim", data_root=ws)
    assert r["ok"] is True
    assert r["name"] == "Jordan from the overlay"
