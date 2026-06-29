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
    """Default: empty exec registry so legacy crm-central fallback path is exercised.

    Individual tests override this to inject specific slugs when validating the
    per-exec mirror path.
    """
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs", lambda: [])


def _ws(tmp_path):
    """A workspace dir nested under tmp_path, so .parent is controllable
    (list_contacts reads ../31c-crm-central + ../31c-crm-{slug} relative to it)."""
    ws = tmp_path / "workspace"
    (ws / "crm" / "contacts").mkdir(parents=True)
    return ws


def _ceo_contact(ws, slug, name, **fm):
    (ws / "crm" / "contacts" / f"{slug}.md").write_text(
        _contact_md(name, **fm), encoding="utf-8")


def _exec_contact(tmp_path, exec_slug, slug, name, **fm):
    """Write a contact into the legacy crm-central aggregate."""
    d = tmp_path / "31c-crm-central" / "contacts" / exec_slug
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.md").write_text(_contact_md(name, **fm), encoding="utf-8")


def _per_exec_contact(tmp_path, exec_slug, slug, name, **fm):
    """Write a contact into the per-exec mirror (current source of truth)."""
    d = tmp_path / f"31c-crm-{exec_slug}" / "contacts"
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


def test_list_contacts_combines_execs(tmp_path):
    ws = _ws(tmp_path)
    _ceo_contact(ws, "alice", "Alice", relationship_type="prospect")
    _exec_contact(tmp_path, "sam-carter", "jordan-kim", "Jordan Kim",
                  relationship_type="prospect")
    d = list_contacts(ws, data_root=ws)
    assert d["total"] == 2
    assert d["owner_counts"] == {"ceo": 1, "sam-carter": 1}
    exec_row = next(c for c in d["contacts"] if c["owner"] != "ceo")
    assert exec_row["owner_label"] == "Sam Carter"


def test_list_contacts_skips_misha_hanin_snapshot(tmp_path):
    """The crm-central misha-hanin/ snapshot is excluded - the live
    crm/contacts/ is authoritative for the CEO's own contacts."""
    ws = _ws(tmp_path)
    _ceo_contact(ws, "alice", "Alice", relationship_type="prospect")
    _exec_contact(tmp_path, "misha-hanin", "stale-contact", "Stale",
                  relationship_type="prospect")
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


def test_list_contacts_slug_collision_across_owners(tmp_path):
    """The same slug under two owners yields two distinct rows."""
    ws = _ws(tmp_path)
    _ceo_contact(ws, "jordan-kim", "Jordan Kim", relationship_type="prospect")
    _exec_contact(tmp_path, "sam-carter", "jordan-kim", "Jordan Kim",
                  relationship_type="prospect")
    d = list_contacts(ws, data_root=ws)
    assert d["total"] == 2
    owners = sorted(c["owner"] for c in d["contacts"] if c["slug"] == "jordan-kim")
    assert owners == ["ceo", "sam-carter"]


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


def test_read_one_contact_ceo(tmp_path):
    ws = _ws(tmp_path)
    _ceo_contact(ws, "alice", "Alice Smith", relationship_type="prospect")
    r = read_one_contact(ws, "ceo", "alice", data_root=ws)
    assert r["ok"] is True
    assert r["name"] == "Alice Smith"
    assert r["owner_label"] == "Misha Hanin"


def test_read_one_contact_exec(tmp_path):
    ws = _ws(tmp_path)
    _exec_contact(tmp_path, "sam-carter", "jordan-kim", "Jordan Kim",
                  relationship_type="prospect")
    r = read_one_contact(ws, "sam-carter", "jordan-kim", data_root=ws)
    assert r["ok"] is True
    assert r["name"] == "Jordan Kim"
    assert r["owner_label"] == "Sam Carter"


def test_read_one_contact_rejects_bad_slug(tmp_path):
    ws = _ws(tmp_path)
    assert read_one_contact(ws, "ceo", "../etc/passwd", data_root=ws)["ok"] is False
    assert read_one_contact(ws, "ceo", "", data_root=ws)["ok"] is False


def test_read_one_contact_rejects_bad_owner(tmp_path):
    ws = _ws(tmp_path)
    _ceo_contact(ws, "alice", "Alice")
    assert read_one_contact(ws, "../escape", "alice", data_root=ws)["ok"] is False
    # The misha-hanin snapshot dir is not a valid drill-down owner.
    assert read_one_contact(ws, "misha-hanin", "alice", data_root=ws)["ok"] is False


def test_read_one_contact_not_found(tmp_path):
    ws = _ws(tmp_path)
    r = read_one_contact(ws, "ceo", "ghost", data_root=ws)
    assert r["ok"] is False
    assert "not found" in r["error"]


# Per-exec mirror coverage (added 2026-05-27 after the exec/per-exec-mirror
# visibility gap; the bridge daemon previously read only from crm-central).


def test_list_contacts_reads_from_per_exec_mirror(tmp_path, monkeypatch):
    """Per-exec mirror at ../31c-crm-{slug}/contacts/ surfaces in the listing."""
    ws = _ws(tmp_path)
    _per_exec_contact(tmp_path, "sam-carter", "taylor-reed", "Taylor Reed",
                      relationship_type="prospect")
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs",
                        lambda: ["sam-carter"])

    d = list_contacts(ws, data_root=ws)
    assert d["total"] == 1
    row = d["contacts"][0]
    assert row["owner"] == "sam-carter"
    assert row["slug"] == "taylor-reed"


def test_per_exec_mirror_wins_over_crm_central(tmp_path, monkeypatch):
    """When both exist, the per-exec mirror is authoritative."""
    ws = _ws(tmp_path)
    _per_exec_contact(tmp_path, "sam-carter", "jordan-kim", "Jordan from per-exec",
                      relationship_type="prospect")
    _exec_contact(tmp_path, "sam-carter", "jordan-kim", "Jordan from central stale",
                  relationship_type="prospect")
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs",
                        lambda: ["sam-carter"])

    d = list_contacts(ws, data_root=ws)
    assert d["total"] == 1
    assert d["contacts"][0]["name"] == "Jordan from per-exec"


def test_falls_back_to_crm_central_when_per_exec_missing(tmp_path, monkeypatch):
    """An exec without a per-exec mirror still surfaces via crm-central."""
    ws = _ws(tmp_path)
    _exec_contact(tmp_path, "nina-falk", "lead-x", "Lead X",
                  relationship_type="prospect")
    # Registry advertises nina-falk; per-exec mirror does not exist on disk.
    monkeypatch.setattr(contacts_src, "get_all_active_exec_slugs",
                        lambda: ["nina-falk"])

    d = list_contacts(ws, data_root=ws)
    assert d["total"] == 1
    assert d["contacts"][0]["owner"] == "nina-falk"


def test_read_one_contact_per_exec(tmp_path):
    ws = _ws(tmp_path)
    _per_exec_contact(tmp_path, "sam-carter", "taylor-reed", "Taylor Reed",
                      relationship_type="prospect")
    r = read_one_contact(ws, "sam-carter", "taylor-reed", data_root=ws)
    assert r["ok"] is True
    assert r["name"] == "Taylor Reed"


def test_read_one_contact_prefers_per_exec_over_central(tmp_path):
    ws = _ws(tmp_path)
    _per_exec_contact(tmp_path, "sam-carter", "jordan-kim", "Jordan from per-exec",
                      relationship_type="prospect")
    _exec_contact(tmp_path, "sam-carter", "jordan-kim", "Jordan from central",
                  relationship_type="prospect")
    r = read_one_contact(ws, "sam-carter", "jordan-kim", data_root=ws)
    assert r["ok"] is True
    assert r["name"] == "Jordan from per-exec"
