"""Unit tests for /library knowledge-base source."""
from pathlib import Path

from scripts.bridge_daemon.sources.library import list_library


def _make_note(workspace_root, rel_path, title="X", type_="principle",
               keywords="", status="evergreen", updated="2026-05-15"):
    p = workspace_root / "knowledge" / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    fm_parts = [f'title: "{title}"', f"type: {type_}"]
    if keywords:
        fm_parts.append(f"keywords: {keywords}")
    if status:
        fm_parts.append(f"status: {status}")
    if updated:
        fm_parts.append(f"updated: {updated}")
    fm = "\n".join(fm_parts)
    p.write_text(f"---\n{fm}\n---\n\n# {title}\n\nBody.\n", encoding="utf-8")


def test_empty_when_no_knowledge_dir(tmp_path):
    """No knowledge/ -> empty."""
    result = list_library(tmp_path)
    assert result["notes"] == []
    assert result["total"] == 0
    assert result["counts"] == {}
    assert result["data_time"] is None


def test_basic_note_parsed(tmp_path):
    """A note with full frontmatter is parsed correctly."""
    _make_note(tmp_path, "odin-brain/principles/foo.md", title="The Foo Principle",
               type_="principle", updated="2026-05-17")
    result = list_library(tmp_path)
    assert result["total"] == 1
    n = result["notes"][0]
    assert n["title"] == "The Foo Principle"
    assert n["type"] == "principle"
    assert n["updated"] == "2026-05-17"


def test_sorted_by_updated_desc(tmp_path):
    """Notes are sorted by 'updated' DESC; None last."""
    _make_note(tmp_path, "a.md", title="Oldest", updated="2026-01-01")
    _make_note(tmp_path, "b.md", title="Newest", updated="2026-05-18")
    _make_note(tmp_path, "c.md", title="No date", updated="")
    _make_note(tmp_path, "d.md", title="Middle", updated="2026-03-15")
    result = list_library(tmp_path)
    titles = [n["title"] for n in result["notes"]]
    # Newest first, None last.
    assert titles == ["Newest", "Middle", "Oldest", "No date"]


def test_index_md_skipped(tmp_path):
    """The knowledge/INDEX.md file is skipped (workspace convention)."""
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "INDEX.md").write_text("# Index\n")
    _make_note(tmp_path, "real.md", title="Real note")
    result = list_library(tmp_path)
    titles = [n["title"] for n in result["notes"]]
    assert "Real note" in titles
    assert "INDEX.md" not in [n.get("path", "") for n in result["notes"]]


def test_archive_subdir_skipped(tmp_path):
    """Notes inside _archive subdir are skipped."""
    _make_note(tmp_path, "main.md", title="Live note")
    _make_note(tmp_path, "_archive/old.md", title="Archived note")
    result = list_library(tmp_path)
    titles = [n["title"] for n in result["notes"]]
    assert "Live note" in titles
    assert "Archived note" not in titles


def test_keywords_parse_list_format(tmp_path):
    """keywords: [a, b, c] is parsed into a list."""
    _make_note(tmp_path, "k.md", title="K", keywords="[culture, leadership, navigation]")
    result = list_library(tmp_path)
    assert result["notes"][0]["keywords"] == ["culture", "leadership", "navigation"]


def test_type_counts_aggregated(tmp_path):
    """counts dict tracks per-type totals across ALL notes (not capped)."""
    _make_note(tmp_path, "p1.md", type_="principle")
    _make_note(tmp_path, "p2.md", type_="principle")
    _make_note(tmp_path, "pos1.md", type_="position")
    _make_note(tmp_path, "src1.md", type_="source")
    result = list_library(tmp_path)
    assert result["counts"]["principle"] == 2
    assert result["counts"]["position"] == 1
    assert result["counts"]["source"] == 1


def test_capped_at_50_but_total_reflects_all(tmp_path):
    """Returns at most LIBRARY_ROW_CAP notes; 'total' reflects pre-cap count."""
    for i in range(60):
        _make_note(tmp_path, f"n-{i:03d}.md", title=f"Note {i}", updated=f"2026-05-{(i % 28) + 1:02d}")
    result = list_library(tmp_path)
    assert result["total"] == 60
    assert len(result["notes"]) == 50


def test_missing_frontmatter_fallback(tmp_path):
    """A note without frontmatter still appears (title falls back to stem)."""
    p = tmp_path / "knowledge" / "plain.md"
    p.parent.mkdir(parents=True)
    p.write_text("Just body text. No frontmatter.\n", encoding="utf-8")
    result = list_library(tmp_path)
    assert result["total"] == 1
    assert result["notes"][0]["title"] == "plain"
    assert result["notes"][0]["type"] == ""


def test_read_note_returns_content(tmp_path):
    """Valid note path returns content + size."""
    from scripts.bridge_daemon.sources.library import read_note
    p = tmp_path / "knowledge" / "test.md"
    p.parent.mkdir(parents=True)
    p.write_text("---\ntitle: Test\n---\n\n# Test\nBody text.\n", encoding="utf-8")
    result = read_note(tmp_path, "knowledge/test.md")
    assert result["ok"] is True
    assert "Body text" in result["content"]
    assert result["path"] == "knowledge/test.md"
    assert result["size"] > 0


def test_read_note_rejects_traversal(tmp_path):
    """A path containing '..' is rejected."""
    from scripts.bridge_daemon.sources.library import read_note
    p = tmp_path / "knowledge" / "test.md"
    p.parent.mkdir(parents=True)
    p.write_text("body", encoding="utf-8")
    # Try to escape with ..
    result = read_note(tmp_path, "knowledge/../secrets.md")
    assert result["ok"] is False
    assert "invalid path segment" in result["error"].lower()


def test_read_note_rejects_outside_knowledge(tmp_path):
    """A path not starting with 'knowledge/' is rejected."""
    from scripts.bridge_daemon.sources.library import read_note
    result = read_note(tmp_path, "outputs/secret.md")
    assert result["ok"] is False
    assert "knowledge" in result["error"].lower()


def test_read_note_rejects_non_md(tmp_path):
    """Only .md files allowed."""
    from scripts.bridge_daemon.sources.library import read_note
    p = tmp_path / "knowledge" / "secret.txt"
    p.parent.mkdir(parents=True)
    p.write_text("body", encoding="utf-8")
    result = read_note(tmp_path, "knowledge/secret.txt")
    assert result["ok"] is False
    assert ".md" in result["error"].lower()


def test_read_note_missing_returns_not_found(tmp_path):
    """Missing file returns ok=False not-found."""
    from scripts.bridge_daemon.sources.library import read_note
    (tmp_path / "knowledge").mkdir()
    result = read_note(tmp_path, "knowledge/missing.md")
    assert result["ok"] is False
    assert "not found" in result["error"].lower()


def test_read_note_size_cap(tmp_path):
    """Files larger than NOTE_MAX_BYTES are rejected."""
    from scripts.bridge_daemon.sources.library import read_note, NOTE_MAX_BYTES
    p = tmp_path / "knowledge" / "huge.md"
    p.parent.mkdir(parents=True)
    p.write_text("x" * (NOTE_MAX_BYTES + 1), encoding="utf-8")
    result = read_note(tmp_path, "knowledge/huge.md")
    assert result["ok"] is False
    assert "too large" in result["error"].lower()


def test_read_note_empty_path(tmp_path):
    """Empty path is rejected."""
    from scripts.bridge_daemon.sources.library import read_note
    result = read_note(tmp_path, "")
    assert result["ok"] is False


# ============================================================
# Phase 1.66: type_order sectioning
# ============================================================
def test_list_library_emits_type_order(tmp_path):
    """type_order follows LIBRARY_TYPE_ORDER for known types; unknown follow alphabetically."""
    kn = tmp_path / "knowledge"
    kn.mkdir(parents=True)
    for slug, typ in [
        ("p1", "principle"),
        ("p2", "position"),
        ("p3", "source"),
        ("p4", "zinger"),  # unknown -> alpha tail
    ]:
        (kn / f"{slug}.md").write_text(
            f'---\ntitle: "{slug}"\ntype: {typ}\nupdated: 2026-05-18\n---\n\nbody\n',
            encoding="utf-8",
        )
    r = list_library(tmp_path)
    assert r["type_order"][:3] == ["principle", "position", "source"]
    assert r["type_order"][-1] == "zinger"


def test_list_library_type_order_omits_absent_types(tmp_path):
    """A type that has no notes is not in type_order."""
    kn = tmp_path / "knowledge"
    kn.mkdir(parents=True)
    (kn / "x.md").write_text(
        '---\ntitle: "x"\ntype: principle\nupdated: 2026-05-18\n---\n\nbody\n',
        encoding="utf-8",
    )
    r = list_library(tmp_path)
    assert "principle" in r["type_order"]
    assert "position" not in r["type_order"]
