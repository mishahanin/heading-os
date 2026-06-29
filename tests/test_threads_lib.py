"""Tests for scripts.utils.threads_lib."""
from pathlib import Path
import pytest
from scripts.utils.threads_lib import parse_thread_file, ThreadFile, write_thread_file, slugify, new_thread_path


def test_parse_thread_file_extracts_frontmatter_and_body(tmp_path: Path) -> None:
    f = tmp_path / "2026-04-28-porkbun.md"
    f.write_text(
        "---\n"
        "id: 2026-04-28-porkbun\n"
        "title: Porkbun queued 31c.io\n"
        "status: active\n"
        "type: business\n"
        "classification: ceo-only\n"
        "opened: 2026-04-28\n"
        "last_touched: 2026-04-29\n"
        "counterparties: []\n"
        "links:\n"
        "  crm: []\n"
        "  pipeline: []\n"
        "  outputs: []\n"
        "  knowledge: []\n"
        "tags: [registrar]\n"
        "---\n"
        "\n"
        "# Porkbun queued 31c.io\n"
        "\n"
        "## Open follow-ups\n"
        "\n"
        "- [ ] Audit DNS\n",
        encoding="utf-8",
    )
    parsed = parse_thread_file(f)
    assert isinstance(parsed, ThreadFile)
    assert parsed.id == "2026-04-28-porkbun"
    assert parsed.status == "active"
    assert parsed.type == "business"
    assert parsed.tags == ["registrar"]
    assert "## Open follow-ups" in parsed.body


def test_parse_thread_file_rejects_missing_required_fields(tmp_path: Path) -> None:
    f = tmp_path / "broken.md"
    f.write_text("---\nid: only-id\n---\nbody\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing required field"):
        parse_thread_file(f)


def test_parse_thread_file_normalizes_missing_link_subkeys(tmp_path: Path) -> None:
    """`links` block may be empty or partial; parser must guarantee 4 sub-keys exist."""
    f = tmp_path / "partial-links.md"
    f.write_text(
        "---\n"
        "id: partial-links\ntitle: t\nstatus: active\ntype: business\nclassification: ceo-only\n"
        "opened: 2026-04-29\nlast_touched: 2026-04-29\n"
        "counterparties: []\n"
        "links: {}\n"  # explicit empty
        "tags: []\n---\nbody\n",
        encoding="utf-8",
    )
    parsed = parse_thread_file(f)
    assert parsed.links["crm"] == []
    assert parsed.links["pipeline"] == []
    assert parsed.links["outputs"] == []
    assert parsed.links["knowledge"] == []


def test_slugify_kebab_case_lowercase_strips_punctuation() -> None:
    # Dots become hyphens (preserves '31c.io' as '31c-io', not destructive '31cio').
    assert slugify("Porkbun queued 31c.io for suspension!") == "porkbun-queued-31c-io-for-suspension"
    assert slugify("HEADING book launch") == "heading-book-launch"
    assert slugify("  Multiple   spaces  ") == "multiple-spaces"
    assert slugify("v4.7 release notes") == "v4-7-release-notes"


def test_new_thread_path_combines_date_and_slug(tmp_path: Path) -> None:
    p = new_thread_path(tmp_path, "business", "Porkbun TrustONE phishing", "2026-04-28")
    assert p == tmp_path / "business" / "2026-04-28-porkbun-trustone-phishing.md"


def test_new_thread_path_rejects_paren_in_slug(tmp_path: Path) -> None:
    """Path-stored regexes assume no parens; the slugifier must guarantee this."""
    p = new_thread_path(tmp_path, "business", "Test (with parens)", "2026-04-29")
    assert "(" not in str(p) and ")" not in str(p)


def test_write_thread_file_round_trip(tmp_path: Path) -> None:
    original = ThreadFile(
        id="2026-04-29-test",
        title="Test thread",
        status="active",
        type="business",
        classification="ceo-only",
        opened="2026-04-29",
        last_touched="2026-04-29",
        counterparties=["Someone"],
        links={"crm": [], "pipeline": [], "outputs": [], "knowledge": []},
        tags=["test"],
        body="# Test thread\n\n## Open follow-ups\n\n- [ ] First item\n",
    )
    f = tmp_path / "2026-04-29-test.md"
    write_thread_file(f, original)
    parsed = parse_thread_file(f)
    assert parsed.id == original.id
    assert parsed.title == original.title
    assert parsed.tags == original.tags
    assert "First item" in parsed.body


# ======================================
# Task 6: Index Manager Tests
# ======================================

from scripts.utils.threads_lib import (
    add_thread_to_index, remove_thread_from_index, update_thread_hook,
    ensure_active_threads_section,
)


ACTIVE_THREADS_HEADER = (
    "## Active Threads\n"
    "<!-- managed-by: /thread - do not edit by hand; /dream skips this section -->\n"
)


def test_ensure_active_threads_section_appends_when_missing(tmp_path: Path) -> None:
    mem = tmp_path / "MEMORY.md"
    mem.write_text("# Persistent Memory\n\n## User Profile\n\n- Some line\n", encoding="utf-8")
    ensure_active_threads_section(mem)
    text = mem.read_text(encoding="utf-8")
    assert ACTIVE_THREADS_HEADER in text
    assert "### Business" in text
    assert "### Personal (CEO-ONLY)" in text


def test_add_thread_to_index_appends_under_correct_subsection(tmp_path: Path) -> None:
    mem = tmp_path / "MEMORY.md"
    mem.write_text("# Persistent Memory\n", encoding="utf-8")
    ensure_active_threads_section(mem)
    add_thread_to_index(mem, type_="business", title="Porkbun TrustONE phishing",
                        path="threads/business/2026-04-28-porkbun-trustone-phishing.md",
                        hook="Awaiting Porkbun abuse evidence")
    text = mem.read_text(encoding="utf-8")
    business_block = text.split("### Business")[1].split("###")[0]
    assert "Porkbun TrustONE phishing" in business_block
    assert "Awaiting Porkbun abuse evidence" in business_block


def test_update_thread_hook_replaces_only_hook_text(tmp_path: Path) -> None:
    mem = tmp_path / "MEMORY.md"
    mem.write_text("# Persistent Memory\n", encoding="utf-8")
    ensure_active_threads_section(mem)
    add_thread_to_index(mem, type_="business", title="Porkbun",
                        path="threads/business/p.md", hook="Old hook")
    update_thread_hook(mem, path="threads/business/p.md", hook="New hook")
    text = mem.read_text(encoding="utf-8")
    assert "New hook" in text
    assert "Old hook" not in text


def test_remove_thread_from_index_drops_only_target_line(tmp_path: Path) -> None:
    mem = tmp_path / "MEMORY.md"
    mem.write_text("# Persistent Memory\n", encoding="utf-8")
    ensure_active_threads_section(mem)
    add_thread_to_index(mem, type_="business", title="A", path="threads/business/a.md", hook="hook A")
    add_thread_to_index(mem, type_="business", title="B", path="threads/business/b.md", hook="hook B")
    remove_thread_from_index(mem, path="threads/business/a.md")
    text = mem.read_text(encoding="utf-8")
    assert "threads/business/b.md" in text
    assert "threads/business/a.md" not in text


def test_add_thread_to_index_rejects_newline_in_hook(tmp_path: Path) -> None:
    mem = tmp_path / "MEMORY.md"
    mem.write_text("# Persistent Memory\n", encoding="utf-8")
    ensure_active_threads_section(mem)
    with pytest.raises(ValueError, match="hook must not contain newlines"):
        add_thread_to_index(mem, type_="business", title="X",
                            path="threads/business/x.md",
                            hook="Multi\nline hook")


def test_update_thread_hook_rejects_newline_in_hook(tmp_path: Path) -> None:
    mem = tmp_path / "MEMORY.md"
    mem.write_text("# Persistent Memory\n", encoding="utf-8")
    ensure_active_threads_section(mem)
    add_thread_to_index(mem, type_="business", title="X",
                        path="threads/business/x.md", hook="Single line")
    with pytest.raises(ValueError, match="hook must not contain newlines"):
        update_thread_hook(mem, path="threads/business/x.md", hook="Multi\nline")


# ======================================
# Task 7: Archive Scanner Tests
# ======================================

from datetime import date, timedelta
from scripts.utils.threads_lib import scan_for_archive, ArchiveCandidate


def _make_thread(tmp_path: Path, type_: str, slug: str, status: str, last_touched: date) -> Path:
    f = tmp_path / type_ / f"2026-01-01-{slug}.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(
        f"---\nid: 2026-01-01-{slug}\ntitle: t\nstatus: {status}\ntype: {type_}\n"
        f"classification: ceo-only\nopened: 2026-01-01\nlast_touched: {last_touched.isoformat()}\n"
        f"counterparties: []\nlinks:\n  crm: []\n  pipeline: []\n  outputs: []\n  knowledge: []\n"
        f"tags: []\n---\n\nbody\n",
        encoding="utf-8",
    )
    return f


def test_scan_for_archive_finds_closed_older_than_90_days(tmp_path: Path) -> None:
    today = date(2026, 4, 29)
    fresh = _make_thread(tmp_path, "business", "fresh", "closed", today - timedelta(days=10))
    stale = _make_thread(tmp_path, "business", "stale", "closed", today - timedelta(days=100))
    candidates = scan_for_archive(tmp_path, today=today)
    archive_paths = [c.path for c in candidates if c.action == "archive"]
    assert stale in archive_paths
    assert fresh not in archive_paths


def test_scan_for_archive_proposes_on_hold_for_active_older_than_60_days(tmp_path: Path) -> None:
    today = date(2026, 4, 29)
    silent = _make_thread(tmp_path, "business", "silent", "active", today - timedelta(days=70))
    candidates = scan_for_archive(tmp_path, today=today)
    on_hold_paths = [c.path for c in candidates if c.action == "propose-on-hold"]
    assert silent in on_hold_paths


# ======================================
# Scrutiny regressions (2026-04-30)
# ======================================


def test_new_thread_path_rejects_empty_slug(tmp_path: Path) -> None:
    """H5 regression: titles that slugify to empty must be rejected loudly."""
    with pytest.raises(ValueError, match="slugifies to empty"):
        new_thread_path(tmp_path, "business", "!!!", "2026-04-30")
    with pytest.raises(ValueError, match="slugifies to empty"):
        new_thread_path(tmp_path, "business", "Привет мир", "2026-04-30")
    with pytest.raises(ValueError, match="slugifies to empty"):
        new_thread_path(tmp_path, "business", "    ", "2026-04-30")


def test_parse_thread_file_rejects_id_filename_mismatch(tmp_path: Path) -> None:
    """L3 regression: id field must match filename stem."""
    f = tmp_path / "2026-04-30-real-slug.md"
    f.write_text(
        "---\n"
        "id: different-id\ntitle: t\nstatus: active\ntype: business\nclassification: ceo-only\n"
        "opened: 2026-04-30\nlast_touched: 2026-04-30\n"
        "counterparties: []\nlinks: {}\ntags: []\n---\nbody\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not match filename stem"):
        parse_thread_file(f)
