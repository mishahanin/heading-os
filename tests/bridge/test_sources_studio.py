"""Unit tests for /studio in-flight items source."""
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.bridge_daemon.sources.studio import recent_inflight_items


def _touch(path: Path, mtime_offset_seconds: int = 0, content: str = "x") -> None:
    """Create a file and optionally backdate its mtime by N seconds."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mtime_offset_seconds:
        t = time.time() - mtime_offset_seconds
        os.utime(path, (t, t))


def test_empty_when_no_inflight_dirs(tmp_path):
    """No in-flight dirs -> empty result, None data_time."""
    result = recent_inflight_items(tmp_path)
    assert result["items"] == []
    assert result["categories"] == {}
    assert result["data_time"] is None


def test_returns_items_sorted_by_mtime_desc(tmp_path):
    """Recent files are returned sorted by mtime DESC."""
    base = tmp_path / "outputs" / "content" / "linkedin"
    _touch(base / "old.md", mtime_offset_seconds=3600)  # 1h ago
    _touch(base / "new.md", mtime_offset_seconds=60)    # 1m ago
    _touch(base / "mid.md", mtime_offset_seconds=600)   # 10m ago
    result = recent_inflight_items(tmp_path)
    names = [it["name"] for it in result["items"]]
    assert names == ["new.md", "mid.md", "old.md"]


def test_skip_files_older_than_window(tmp_path):
    """Files older than window_days are excluded."""
    base = tmp_path / "outputs" / "intel"
    _touch(base / "recent.md", mtime_offset_seconds=60)
    _touch(base / "ancient.md", mtime_offset_seconds=10 * 86400)  # 10 days ago
    result = recent_inflight_items(tmp_path, window_days=7)
    names = [it["name"] for it in result["items"]]
    assert "recent.md" in names
    assert "ancient.md" not in names


def test_skip_archive_work_build_template_subtrees(tmp_path):
    """Files inside _archive, _work, _build, _template are excluded."""
    base = tmp_path / "outputs" / "documents"
    _touch(base / "main.docx")
    _touch(base / "_archive" / "old.docx")
    _touch(base / "_work" / "build.py")
    _touch(base / "_build" / "junk.txt")
    _touch(base / "_template" / "tpl.docx")
    result = recent_inflight_items(tmp_path)
    names = [it["name"] for it in result["items"]]
    assert names == ["main.docx"]


def test_skip_dotted_dirs(tmp_path):
    """Files in .git, .venv, etc. are excluded."""
    base = tmp_path / "outputs" / "intel"
    _touch(base / "real.md")
    _touch(base / ".git" / "HEAD")
    _touch(base / ".venv" / "lib.py")
    result = recent_inflight_items(tmp_path)
    names = [it["name"] for it in result["items"]]
    assert names == ["real.md"]


def test_category_aggregation(tmp_path):
    """Items count by category for the summary."""
    _touch(tmp_path / "outputs" / "content" / "linkedin" / "a.md")
    _touch(tmp_path / "outputs" / "content" / "linkedin" / "b.md")
    _touch(tmp_path / "outputs" / "intel" / "c.md")
    result = recent_inflight_items(tmp_path)
    assert result["categories"]["linkedin"] == 2
    assert result["categories"]["intel"] == 1


def test_cap_50_items(tmp_path):
    """Returns at most 50 items."""
    base = tmp_path / "outputs" / "content" / "linkedin"
    for i in range(60):
        _touch(base / f"item-{i:03d}.md", mtime_offset_seconds=i)
    result = recent_inflight_items(tmp_path)
    assert len(result["items"]) == 50


def test_data_time_is_most_recent_mtime(tmp_path):
    """data_time is the ISO mtime of the most-recent item."""
    base = tmp_path / "outputs" / "intel"
    _touch(base / "old.md", mtime_offset_seconds=600)
    _touch(base / "new.md", mtime_offset_seconds=60)
    result = recent_inflight_items(tmp_path)
    # data_time == items[0]["mtime"] (which is the newest).
    assert result["data_time"] == result["items"][0]["mtime"]
    # And the newest is new.md.
    assert result["items"][0]["name"] == "new.md"


def test_read_inflight_text_file(tmp_path):
    """A valid .md path under linkedin/ returns content + is_text=True."""
    from scripts.bridge_daemon.sources.studio import read_inflight
    base = tmp_path / "outputs" / "content" / "linkedin"
    base.mkdir(parents=True)
    (base / "post.md").write_text("Post body here.\n", encoding="utf-8")
    result = read_inflight(tmp_path, "outputs/content/linkedin/post.md")
    assert result["ok"] is True
    assert result["is_text"] is True
    assert "Post body here" in result["content"]


def test_read_inflight_binary_placeholder(tmp_path):
    """A .docx file returns a binary placeholder, not the raw bytes."""
    from scripts.bridge_daemon.sources.studio import read_inflight
    base = tmp_path / "outputs" / "documents"
    base.mkdir(parents=True)
    (base / "doc.docx").write_bytes(b"\x50\x4b\x03\x04binary content")
    result = read_inflight(tmp_path, "outputs/documents/doc.docx")
    assert result["ok"] is True
    assert result["is_text"] is False
    assert "binary file" in result["content"].lower()


def test_read_inflight_rejects_outside_inflight_dirs(tmp_path):
    """Paths not under IN_FLIGHT_DIRS prefixes are rejected."""
    from scripts.bridge_daemon.sources.studio import read_inflight
    result = read_inflight(tmp_path, "outputs/operations/state/foo.json")
    assert result["ok"] is False
    assert "not under" in result["error"].lower() or "escapes" in result["error"].lower()


def test_read_inflight_rejects_traversal(tmp_path):
    """A path with .. is rejected before any IO."""
    from scripts.bridge_daemon.sources.studio import read_inflight
    result = read_inflight(tmp_path, "outputs/content/linkedin/../secret.md")
    assert result["ok"] is False


def test_read_inflight_rejects_archive_subtree(tmp_path):
    """A path under _archive is rejected even if its prefix matches."""
    from scripts.bridge_daemon.sources.studio import read_inflight
    base = tmp_path / "outputs" / "content" / "linkedin" / "_archive"
    base.mkdir(parents=True)
    (base / "old.md").write_text("archived", encoding="utf-8")
    result = read_inflight(tmp_path, "outputs/content/linkedin/_archive/old.md")
    assert result["ok"] is False
    assert "excluded" in result["error"].lower()


def test_read_inflight_size_cap(tmp_path):
    """Files larger than FILE_MAX_BYTES are rejected."""
    from scripts.bridge_daemon.sources.studio import read_inflight, FILE_MAX_BYTES
    base = tmp_path / "outputs" / "content" / "linkedin"
    base.mkdir(parents=True)
    (base / "huge.md").write_text("x" * (FILE_MAX_BYTES + 1), encoding="utf-8")
    result = read_inflight(tmp_path, "outputs/content/linkedin/huge.md")
    assert result["ok"] is False
    assert "too large" in result["error"].lower()


def test_read_inflight_missing_file(tmp_path):
    """A missing file under a valid prefix returns not-found."""
    from scripts.bridge_daemon.sources.studio import read_inflight
    (tmp_path / "outputs" / "content" / "linkedin").mkdir(parents=True)
    result = read_inflight(tmp_path, "outputs/content/linkedin/does-not-exist.md")
    assert result["ok"] is False
    assert "not found" in result["error"].lower()


# ============================================================
# Phase 1.38: LinkedIn artifacts (the Studio page)
# ============================================================
from scripts.bridge_daemon.sources.studio import (  # noqa: E402
    list_artifacts,
    read_artifact,
    resolve_artifact_image,
)


def _make_post(workspace_root, slug, body="Caption text here.", images=(), **fm):
    """Create a linkedin-archive post folder: {slug}.md + image files."""
    folder = (workspace_root / "datastore" / "content" / "linkedin-archive"
              / "posts" / slug)
    folder.mkdir(parents=True, exist_ok=True)
    fm_block = ""
    if fm:
        fm_block = "---\n" + "\n".join(f"{k}: {v}" for k, v in fm.items()) + "\n---\n\n"
    (folder / f"{slug}.md").write_text(
        f"{fm_block}# LinkedIn Caption\n\n{body}\n", encoding="utf-8")
    for img in images:
        (folder / img).write_bytes(b"\x89PNG\r\n fake image bytes")
    return folder


def test_list_artifacts_empty(tmp_path):
    assert list_artifacts(tmp_path)["total"] == 0


def test_list_artifacts_reads_posts(tmp_path):
    _make_post(tmp_path, "2026-05-01-first-post", title="First Post", date="2026-05-01")
    _make_post(tmp_path, "2026-05-10-second-post", title="Second Post", date="2026-05-10")
    d = list_artifacts(tmp_path)
    assert d["total"] == 2
    assert d["counts"]["post"] == 2
    assert d["artifacts"][0]["title"] == "Second Post"  # date DESC
    assert d["artifacts"][0]["kind"] == "post"


def test_list_artifacts_includes_images(tmp_path):
    _make_post(tmp_path, "2026-05-01-with-images", title="Imaged",
               images=["a-v1.png", "a-v2.png"])
    a = list_artifacts(tmp_path)["artifacts"][0]
    assert a["image_count"] == 2
    assert all(p.endswith(".png") for p in a["images"])
    assert all("linkedin-archive/posts" in p for p in a["images"])


def test_list_artifacts_summary_and_frontmatter(tmp_path):
    _make_post(tmp_path, "2026-05-01-fm-post", body="The actual caption prose.",
               title="FM Post", date="2026-05-01", series="Sovereignty", status="draft")
    a = list_artifacts(tmp_path)["artifacts"][0]
    assert a["title"] == "FM Post"
    assert a["series"] == "Sovereignty"
    assert a["status"] == "draft"
    assert "actual caption prose" in a["summary"]


def test_list_artifacts_skips_underscore_folders(tmp_path):
    _make_post(tmp_path, "2026-05-01-real", title="Real")
    udir = (tmp_path / "datastore" / "content" / "linkedin-archive"
            / "posts" / "_work")
    udir.mkdir(parents=True)
    (udir / "_work.md").write_text("# scratch", encoding="utf-8")
    d = list_artifacts(tmp_path)
    assert d["total"] == 1
    assert d["artifacts"][0]["slug"] == "2026-05-01-real"


def test_list_artifacts_title_from_slug_when_no_frontmatter(tmp_path):
    _make_post(tmp_path, "2026-05-01-no-frontmatter-here")
    a = list_artifacts(tmp_path)["artifacts"][0]
    assert a["title"] == "No Frontmatter Here"
    assert a["date"] == "2026-05-01"


def test_read_artifact_ok(tmp_path):
    _make_post(tmp_path, "2026-05-01-readable", body="Body prose.",
               title="Readable", images=["x.png"])
    r = read_artifact(tmp_path, "post", "2026-05-01-readable")
    assert r["ok"] is True
    assert r["title"] == "Readable"
    assert "Body prose." in r["content"]
    assert len(r["images"]) == 1


def test_read_artifact_rejects_bad_input(tmp_path):
    assert read_artifact(tmp_path, "post", "ghost")["ok"] is False
    assert read_artifact(tmp_path, "post", "../escape")["ok"] is False
    assert read_artifact(tmp_path, "bogus-kind", "x")["ok"] is False


def test_resolve_artifact_image_ok(tmp_path):
    _make_post(tmp_path, "2026-05-01-img", images=["pic.png"])
    rel = "datastore/content/linkedin-archive/posts/2026-05-01-img/pic.png"
    img = resolve_artifact_image(tmp_path, rel)
    assert img is not None and img.exists()


def test_resolve_artifact_image_rejects_traversal(tmp_path):
    assert resolve_artifact_image(
        tmp_path, "datastore/content/linkedin-archive/../../../etc/passwd") is None


def test_resolve_artifact_image_rejects_non_image(tmp_path):
    _make_post(tmp_path, "2026-05-01-x")
    rel = "datastore/content/linkedin-archive/posts/2026-05-01-x/2026-05-01-x.md"
    assert resolve_artifact_image(tmp_path, rel) is None


def test_resolve_artifact_image_rejects_outside_archive(tmp_path):
    (tmp_path / "secret.png").write_bytes(b"x")
    assert resolve_artifact_image(tmp_path, "secret.png") is None
