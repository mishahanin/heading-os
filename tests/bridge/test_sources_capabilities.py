"""Unit tests for /capabilities skill catalog source."""
from pathlib import Path

from scripts.bridge_daemon.sources.capabilities import (
    CATEGORY_ORDER,
    list_capabilities,
    skill_category,
)


def _make_skill(workspace_root, slug, name=None, description="", version="", author=""):
    d = workspace_root / ".claude" / "skills" / slug
    d.mkdir(parents=True, exist_ok=True)
    fm_lines = [f"name: {name or slug}"]
    if description:
        fm_lines.append(f'description: "{description}"')
    if version or author:
        fm_lines.append("metadata:")
        if author:
            fm_lines.append(f"  author: {author}")
        if version:
            fm_lines.append(f'  version: "{version}"')
    fm = "\n".join(fm_lines)
    (d / "SKILL.md").write_text(f"---\n{fm}\n---\n\n# {name or slug}\n\nBody.\n", encoding="utf-8")


def test_empty_when_no_skills_dir(tmp_path):
    """No .claude/skills/ -> empty."""
    result = list_capabilities(tmp_path)
    assert result["skills"] == []
    assert result["count"] == 0
    assert result["data_time"] is None


def test_skill_with_full_frontmatter_parsed(tmp_path):
    """A skill with name, description, version, author is parsed correctly."""
    _make_skill(tmp_path, "osint",
                description="Deep OSINT intelligence gathering",
                version="1.2", author="31c")
    result = list_capabilities(tmp_path)
    assert result["count"] == 1
    s = result["skills"][0]
    assert s["slug"] == "osint"
    assert s["name"] == "osint"
    assert "OSINT" in s["description"]
    assert s["version"] == "1.2"
    assert s["author"] == "31c"


def test_skills_sorted_by_slug(tmp_path):
    """Skills returned alphabetically by slug."""
    _make_skill(tmp_path, "zeta")
    _make_skill(tmp_path, "alpha")
    _make_skill(tmp_path, "mike")
    result = list_capabilities(tmp_path)
    assert [s["slug"] for s in result["skills"]] == ["alpha", "mike", "zeta"]


def test_archive_subdir_skipped(tmp_path):
    """The 'archive' subdir is skipped (workspace convention)."""
    _make_skill(tmp_path, "real")
    _make_skill(tmp_path, "archive")
    result = list_capabilities(tmp_path)
    slugs = [s["slug"] for s in result["skills"]]
    assert "real" in slugs
    assert "archive" not in slugs


def test_skill_without_skill_md_skipped(tmp_path):
    """A subdir without SKILL.md is skipped silently."""
    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "empty-dir").mkdir()
    _make_skill(tmp_path, "valid")
    result = list_capabilities(tmp_path)
    assert [s["slug"] for s in result["skills"]] == ["valid"]


def test_long_description_truncated(tmp_path):
    """A description longer than 240 chars is truncated with an ellipsis."""
    long_desc = "x" * 500
    _make_skill(tmp_path, "wordy", description=long_desc)
    result = list_capabilities(tmp_path)
    desc = result["skills"][0]["description"]
    assert len(desc) <= 245  # 240 + ellipsis margin
    assert desc.endswith("...") or len(desc) <= 240


def test_first_sentence_preferred(tmp_path):
    """When description has multiple sentences, only the first is returned."""
    _make_skill(tmp_path, "fast", description="First sentence here. Second sentence we don't want.")
    result = list_capabilities(tmp_path)
    desc = result["skills"][0]["description"]
    assert "First sentence here" in desc
    assert "Second" not in desc


def test_missing_metadata_fields_yield_empty_strings(tmp_path):
    """A skill with no metadata block leaves version and author as empty strings."""
    _make_skill(tmp_path, "minimal", description="Just a description")
    result = list_capabilities(tmp_path)
    s = result["skills"][0]
    assert s["version"] == ""
    assert s["author"] == ""


def test_data_time_is_most_recent_mtime(tmp_path):
    """data_time reflects the most-recent SKILL.md mtime among scanned skills."""
    _make_skill(tmp_path, "a")
    result = list_capabilities(tmp_path)
    from datetime import datetime
    parsed = datetime.fromisoformat(result["data_time"])
    assert parsed.tzinfo is not None


def test_read_skill_returns_content(tmp_path):
    """Valid slug returns SKILL.md content + size."""
    from scripts.bridge_daemon.sources.capabilities import read_skill
    d = tmp_path / ".claude" / "skills" / "osint"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: osint\n---\n\n# OSINT skill\n\nFull body here.\n",
                                encoding="utf-8")
    result = read_skill(tmp_path, "osint")
    assert result["ok"] is True
    assert "Full body here" in result["content"]
    assert result["slug"] == "osint"


def test_read_skill_rejects_invalid_slug(tmp_path):
    """Uppercase, special chars, traversal, empty - all rejected."""
    from scripts.bridge_daemon.sources.capabilities import read_skill
    for bad in ["UPPERCASE", "with spaces", "../escape", "with.dot", "with/slash", ""]:
        result = read_skill(tmp_path, bad)
        assert result["ok"] is False, f"{bad!r} should be rejected, got: {result}"


def test_read_skill_rejects_namespaced(tmp_path):
    """Namespaced slugs (plugin:skill) are rejected in Phase 1."""
    from scripts.bridge_daemon.sources.capabilities import read_skill
    result = read_skill(tmp_path, "superpowers:brainstorming")
    assert result["ok"] is False
    assert "namespaced" in result["error"].lower()


def test_read_skill_missing_returns_not_found(tmp_path):
    """Valid-shape slug but missing SKILL.md returns not-found."""
    from scripts.bridge_daemon.sources.capabilities import read_skill
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    result = read_skill(tmp_path, "missing-skill")
    assert result["ok"] is False
    assert "not found" in result["error"].lower()


def test_read_skill_size_cap(tmp_path):
    """Files larger than SKILL_MAX_BYTES are rejected."""
    from scripts.bridge_daemon.sources.capabilities import read_skill, SKILL_MAX_BYTES
    d = tmp_path / ".claude" / "skills" / "huge"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("x" * (SKILL_MAX_BYTES + 1), encoding="utf-8")
    result = read_skill(tmp_path, "huge")
    assert result["ok"] is False
    assert "too large" in result["error"].lower()


def test_read_skill_accepts_realistic_slugs(tmp_path):
    """Realistic skill slugs (kebab-case) are accepted."""
    from scripts.bridge_daemon.sources.capabilities import read_skill
    for slug in ["osint", "linkedin-post", "competitor-intel", "meeting-prep"]:
        d = tmp_path / ".claude" / "skills" / slug
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: " + slug + "\n---\n", encoding="utf-8")
        result = read_skill(tmp_path, slug)
        assert result["ok"] is True, f"{slug!r} should be accepted, got: {result.get('error')}"


# ============================================================
# Phase 1.65: category classification
# ============================================================
def test_skill_category_known_slugs():
    assert skill_category("osint") == "Intel"
    assert skill_category("email-respond") == "Communication"
    assert skill_category("linkedin-post") == "Content"
    assert skill_category("crm") == "CRM"
    assert skill_category("pptx-generator") == "Design"
    assert skill_category("voss") == "Strategy"
    assert skill_category("backup") == "Operations"


def test_skill_category_unknown_defaults_to_operations():
    assert skill_category("never-heard-of-this") == "Operations"
    assert skill_category("") == "Operations"


def test_category_order_starts_with_intel():
    """Section ordering is locked: Intel first, Operations last."""
    assert CATEGORY_ORDER[0] == "Intel"
    assert CATEGORY_ORDER[-1] == "Operations"


def test_list_capabilities_emits_category_per_skill(tmp_path):
    """Each skill gets a category; counts dict aggregates them."""
    _make_skill(tmp_path, "osint", description="x")
    _make_skill(tmp_path, "follow-up", description="x")
    _make_skill(tmp_path, "linkedin-post", description="x")
    _make_skill(tmp_path, "weird-custom-slug", description="x")  # defaults to Operations
    r = list_capabilities(tmp_path)
    by_slug = {s["slug"]: s["category"] for s in r["skills"]}
    assert by_slug["osint"] == "Intel"
    assert by_slug["follow-up"] == "Communication"
    assert by_slug["linkedin-post"] == "Content"
    assert by_slug["weird-custom-slug"] == "Operations"
    assert r["category_counts"]["Intel"] == 1
    assert r["category_counts"]["Communication"] == 1
    assert r["category_counts"]["Content"] == 1
    assert r["category_counts"]["Operations"] == 1
    assert r["category_order"][0] == "Intel"
