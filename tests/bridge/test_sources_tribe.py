"""Unit tests for /tribe CRM frontmatter source."""
from datetime import date
from pathlib import Path

from scripts.bridge_daemon.sources.tribe import (
    list_tribe,
    _load_tribe_roster,
    _merge_tribe,
)


def _make_contact(workspace_root, slug, **frontmatter):
    """Write a minimal CRM contact file."""
    contacts_dir = workspace_root / "crm" / "contacts"
    contacts_dir.mkdir(parents=True, exist_ok=True)
    fm_lines = "\n".join(f"{k}: {v}" for k, v in frontmatter.items())
    display = frontmatter.get("_name", slug.replace("-", " ").title())
    text = f"---\n{fm_lines}\n---\n\n# {display}\n\nBody.\n"
    (contacts_dir / f"{slug}.md").write_text(text, encoding="utf-8")


def test_empty_when_no_contacts_dir(tmp_path):
    """No crm/contacts/ -> empty + None data_time."""
    result = list_tribe(tmp_path)
    assert result["members"] == []
    assert result["counts"] == {}
    assert result["data_time"] is None


def test_filters_by_relationship_type(tmp_path):
    """Only relationship_type 'tribe' or 'tribe-leadership' are surfaced."""
    _make_contact(tmp_path, "tribe-member",     relationship_type="tribe",            last_touch="2026-05-15", _name="Tribe Member")
    _make_contact(tmp_path, "tribe-lead",       relationship_type="tribe-leadership", last_touch="2026-05-17", _name="Tribe Lead")
    _make_contact(tmp_path, "prospect-pers",    relationship_type="prospect",         last_touch="2026-05-10", _name="Prospect Person")
    _make_contact(tmp_path, "reseller-pers",    relationship_type="reseller",         last_touch="2026-05-10", _name="Reseller Person")
    result = list_tribe(tmp_path)
    slugs = [m["slug"] for m in result["members"]]
    assert "tribe-member" in slugs
    assert "tribe-lead" in slugs
    assert "prospect-pers" not in slugs
    assert "reseller-pers" not in slugs


def test_sorted_by_days_since_touch_desc(tmp_path):
    """Members with longer time since last touch come first; None last."""
    today = date(2026, 5, 18)
    _make_contact(tmp_path, "recent",     relationship_type="tribe", last_touch="2026-05-17", _name="Recent")
    _make_contact(tmp_path, "ancient",    relationship_type="tribe", last_touch="2026-01-01", _name="Ancient")
    _make_contact(tmp_path, "no-log",     relationship_type="tribe",                          _name="No Log")
    _make_contact(tmp_path, "mid",        relationship_type="tribe", last_touch="2026-04-01", _name="Mid")
    result = list_tribe(tmp_path, today=today)
    names = [m["name"] for m in result["members"]]
    # Oldest first, None last.
    assert names == ["Ancient", "Mid", "Recent", "No Log"]


def test_days_since_touch_computation(tmp_path):
    """days_since_touch is the integer day difference."""
    today = date(2026, 5, 18)
    _make_contact(tmp_path, "x", relationship_type="tribe", last_touch="2026-05-15", _name="X")
    result = list_tribe(tmp_path, today=today)
    assert result["members"][0]["days_since_touch"] == 3


def test_role_counts(tmp_path):
    """counts dict tracks tribe vs tribe-leadership."""
    _make_contact(tmp_path, "m1", relationship_type="tribe",            _name="M1")
    _make_contact(tmp_path, "m2", relationship_type="tribe",            _name="M2")
    _make_contact(tmp_path, "L1", relationship_type="tribe-leadership", _name="L1")
    result = list_tribe(tmp_path)
    assert result["counts"]["tribe"] == 2
    assert result["counts"]["tribe-leadership"] == 1


def test_h1_name_parse(tmp_path):
    """The display name comes from the H1 line after the frontmatter."""
    contacts_dir = tmp_path / "crm" / "contacts"
    contacts_dir.mkdir(parents=True)
    (contacts_dir / "foo.md").write_text(
        "---\nrelationship_type: tribe\nlast_touch: 2026-05-15\n---\n\n# Display Name Here (owner-slug)\n\nBody.\n",
        encoding="utf-8",
    )
    result = list_tribe(tmp_path)
    assert result["members"][0]["name"] == "Display Name Here"


def test_h1_name_fallback_to_slug(tmp_path):
    """If no H1, the display name falls back to the slug Title Cased."""
    contacts_dir = tmp_path / "crm" / "contacts"
    contacts_dir.mkdir(parents=True)
    (contacts_dir / "bar-baz.md").write_text(
        "---\nrelationship_type: tribe\n---\n\nNo H1 here.\n",
        encoding="utf-8",
    )
    result = list_tribe(tmp_path)
    assert result["members"][0]["name"] == "Bar Baz"


def test_malformed_frontmatter_skipped(tmp_path):
    """A file without a proper frontmatter block is silently skipped."""
    contacts_dir = tmp_path / "crm" / "contacts"
    contacts_dir.mkdir(parents=True)
    (contacts_dir / "broken.md").write_text(
        "no frontmatter at all\nrelationship_type: tribe\n",
        encoding="utf-8",
    )
    _make_contact(tmp_path, "good", relationship_type="tribe", _name="Good")
    result = list_tribe(tmp_path)
    # The broken file has no fm -> not Tribe -> excluded. The good file stays.
    assert len(result["members"]) == 1
    assert result["members"][0]["name"] == "Good"


def test_data_time_is_most_recent_mtime(tmp_path):
    """data_time reflects the most-recent mtime among Tribe contact files."""
    _make_contact(tmp_path, "a", relationship_type="tribe", _name="A")
    result = list_tribe(tmp_path)
    assert result["data_time"] is not None
    # ISO format with timezone.
    from datetime import datetime
    parsed = datetime.fromisoformat(result["data_time"])
    assert parsed.tzinfo is not None


def test_read_contact_returns_parsed_sections(tmp_path):
    """Valid slug returns ok=True with frontmatter + parsed sections."""
    from scripts.bridge_daemon.sources.tribe import read_contact
    p = tmp_path / "crm" / "contacts" / "foo-bar.md"
    p.parent.mkdir(parents=True)
    p.write_text(
        "---\nrelationship_type: tribe\nlast_touch: 2026-05-15\n---\n\n"
        "# Foo Bar (misha-hanin)\n\n"
        "## Active Commitments\n"
        "- ISO certification\n"
        "- MWC follow-up\n\n"
        "## Interaction Log\n"
        "### 2026-05-15 | Email | Subject\nBody.\n",
        encoding="utf-8",
    )
    result = read_contact(tmp_path, "foo-bar")
    assert result["ok"] is True
    assert result["slug"] == "foo-bar"
    assert result["name"] == "Foo Bar"
    assert "ISO certification" in result["active_commitments"]
    assert "MWC follow-up" in result["active_commitments"]
    assert "Body" in result["interaction_log"]


def test_read_contact_rejects_invalid_slug(tmp_path):
    """Slug with uppercase or special chars is rejected."""
    from scripts.bridge_daemon.sources.tribe import read_contact
    for bad in ["Foo", "../etc/passwd", "foo bar", "foo/bar", "foo.md", "FOO-BAR", "_foo"]:
        result = read_contact(tmp_path, bad)
        assert result["ok"] is False, f"slug {bad!r} should be rejected"


def test_read_contact_rejects_missing(tmp_path):
    """Missing contact returns ok=False with 'not found'."""
    from scripts.bridge_daemon.sources.tribe import read_contact
    (tmp_path / "crm" / "contacts").mkdir(parents=True)
    result = read_contact(tmp_path, "missing-person")
    assert result["ok"] is False
    assert "not found" in result["error"].lower()


def test_read_contact_section_extraction(tmp_path):
    """Section extraction is case-insensitive and stops at next H2."""
    from scripts.bridge_daemon.sources.tribe import _extract_section
    text = (
        "## Active Commitments\n"
        "A\n\n"
        "## Interaction Log\n"
        "B\n"
        "## Notes\n"
        "C\n"
    )
    assert _extract_section(text, "Active Commitments") == "A"
    assert _extract_section(text, "interaction log") == "B"  # case-insensitive
    assert _extract_section(text, "Nope") == ""


def test_read_contact_empty_sections_when_missing(tmp_path):
    """A contact without commitments/log sections returns empty strings."""
    from scripts.bridge_daemon.sources.tribe import read_contact
    p = tmp_path / "crm" / "contacts" / "minimal.md"
    p.parent.mkdir(parents=True)
    p.write_text(
        "---\nrelationship_type: tribe\n---\n\n# Minimal Person\n\nNo sections here.\n",
        encoding="utf-8",
    )
    result = read_contact(tmp_path, "minimal")
    assert result["ok"] is True
    assert result["active_commitments"] == ""
    assert result["interaction_log"] == ""


def test_read_contact_empty_slug(tmp_path):
    """Empty slug rejected."""
    from scripts.bridge_daemon.sources.tribe import read_contact
    result = read_contact(tmp_path, "")
    assert result["ok"] is False


# ============================================================
# Phase 1.37: 31C_Tribe.xlsx roster enrichment
# ============================================================
def _crm(slug, name, email="", role="tribe", last_touch=None):
    return {"slug": slug, "name": name, "email": email, "role": role,
            "last_touch": last_touch, "days_since_touch": None, "status": None}


def _ros(name, email="", title="", department="", reports_to="", telegram=""):
    return {"name": name, "email": email, "title": title, "department": department,
            "reports_to": reports_to, "telegram": telegram}


def _write_roster_xlsx(workspace_root, data_rows):
    """Write a minimal 31C_Tribe.xlsx with the real header layout."""
    import openpyxl
    d = workspace_root / "datastore" / "operations" / "tribe"
    d.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tribe Roster"
    ws.append(["31C Tribe, test"])
    ws.append(["#", "Name", "Email", "Title (reconciled)",
               "Function / Department", "Reports To", "Telegram Username"])
    for i, row in enumerate(data_rows, 1):
        ws.append([i, *row])
    wb.save(d / "31C_Tribe.xlsx")


def test_merge_tribe_matches_by_email():
    crm = [_crm("ada", "Ada Lovelace", email="heidi@31c.io",
                role="tribe-leadership", last_touch="2026-05-01")]
    roster = [_ros("Ada Lovelace", email="heidi@31c.io",
                   title="Chief Engineer", department="Eng")]
    merged = _merge_tribe(crm, roster)
    assert len(merged) == 1
    m = merged[0]
    assert m["slug"] == "ada"               # CRM field
    assert m["role"] == "tribe-leadership"  # CRM field
    assert m["title"] == "Chief Engineer"   # roster field
    assert m["department"] == "Eng"
    assert m["in_roster"] is True


def test_merge_tribe_matches_by_name_when_no_email():
    crm = [_crm("ada", "Ada Lovelace", email="")]
    roster = [_ros("ada  lovelace", title="Engineer")]  # case + spacing differ
    merged = _merge_tribe(crm, roster)
    assert len(merged) == 1
    assert merged[0]["slug"] == "ada"
    assert merged[0]["title"] == "Engineer"


def test_merge_tribe_roster_only_member():
    """A roster person with no CRM contact: slug None, role defaults tribe."""
    merged = _merge_tribe([], [_ros("Bob Smith", title="QA Lead")])
    assert len(merged) == 1
    assert merged[0]["slug"] is None
    assert merged[0]["role"] == "tribe"
    assert merged[0]["title"] == "QA Lead"
    assert merged[0]["in_roster"] is True
    assert merged[0]["days_since_touch"] is None


def test_merge_tribe_keeps_crm_only_member():
    """A CRM tribe member absent from the roster is still listed."""
    crm = [_crm("ceo", "The CEO", role="tribe-leadership")]
    merged = _merge_tribe(crm, [_ros("Someone Else", title="X")])
    by_name = {m["name"]: m for m in merged}
    assert "The CEO" in by_name
    assert by_name["The CEO"]["in_roster"] is False
    assert by_name["The CEO"]["slug"] == "ceo"


def test_load_tribe_roster_missing_file(tmp_path):
    assert _load_tribe_roster(tmp_path) == []


def test_load_tribe_roster_reads_xlsx(tmp_path):
    _write_roster_xlsx(tmp_path, [
        ["Ada Lovelace", "heidi@31c.io", "Chief Engineer", "Engineering",
         "Misha Hanin", "ada_tg"],
    ])
    roster = _load_tribe_roster(tmp_path)
    assert len(roster) == 1
    r = roster[0]
    assert r["name"] == "Ada Lovelace"
    assert r["email"] == "heidi@31c.io"  # lowercased
    assert r["title"] == "Chief Engineer"
    assert r["department"] == "Engineering"
    assert r["reports_to"] == "Misha Hanin"
    assert r["telegram"] == "ada_tg"


def test_list_tribe_enriches_from_roster(tmp_path):
    """A CRM tribe contact gains roster org fields; a roster-only person
    appears too with slug None."""
    _make_contact(tmp_path, "ada-lovelace", relationship_type="tribe-leadership",
                  email="heidi@31c.io", last_touch="2026-05-01", _name="Ada Lovelace")
    _write_roster_xlsx(tmp_path, [
        ["Ada Lovelace", "heidi@31c.io", "Chief Engineer", "Engineering", "Misha", "ada_tg"],
        ["Bob Roster", "bob@31c.io", "QA Lead", "QA", "Ada", "bob_tg"],
    ])
    result = list_tribe(tmp_path)
    by_name = {m["name"]: m for m in result["members"]}
    assert by_name["Ada Lovelace"]["title"] == "Chief Engineer"
    assert by_name["Ada Lovelace"]["slug"] == "ada-lovelace"   # CRM matched
    assert by_name["Bob Roster"]["slug"] is None               # roster only
    assert by_name["Bob Roster"]["title"] == "QA Lead"
    assert result["counts"]["tribe-leadership"] == 1           # Ada
