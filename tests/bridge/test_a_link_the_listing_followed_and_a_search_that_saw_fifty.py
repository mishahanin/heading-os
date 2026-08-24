"""Shard 02-p1: a symlink ban that only looked at the leaf, and a search that
matched fifty files while reporting on all of them.

* ``studio._artifact_md_is_readable`` said it refused "a symlink out of the
  artifact tree". It tested ``md.is_symlink()``, which asks only about the LEAF
  file, while ``list_artifacts`` selects folders with ``folder.is_dir()``, which
  FOLLOWS links. An artifact folder that was itself a symlink to a directory
  outside the archive therefore passed every check - it resolved as a
  directory, its name matched the slug pattern, the markdown inside was a real
  file - and that file's prose was read into the /studio summary. The check now
  walks every component from the archive subdirectory down, unresolved.

* ``search`` consumed ``recent_inflight_items(data_root)["items"]``, which is
  pre-truncated to the 50 newest. Past 50 in-flight files, a query matched only
  those and the result set said nothing about it: a file the operator edited
  six days ago was simply unfindable. The scan now takes ``cap=None`` for
  search while the page keeps its display cap.

* ``tribe.read_contact``'s docstring promised its root "falls back to the
  ``get_data_root()`` seam when not supplied". There is no default on the
  parameter, so taking that literally is a TypeError.

Run: python3 -m pytest tests/bridge/test_a_link_the_listing_followed_and_a_search_that_saw_fifty.py
"""
import logging
from pathlib import Path

import pytest

import scripts.bridge_daemon.sources.studio as studio_src
from scripts.bridge_daemon.sources.search import search
from scripts.bridge_daemon.sources.studio import (
    ARTIFACT_ROOT,
    STUDIO_ROW_CAP,
    list_artifacts,
    read_artifact,
    recent_inflight_items,
)
from scripts.bridge_daemon.sources.tribe import read_contact

# A marker string, not a credential: it stands in for whatever prose an
# out-of-tree file happens to hold, so a test can assert it never reached
# the page. (Named SECRET until ruff's S105 read the name as a password.)
LEAK_MARKER = "the-quarterly-numbers-nobody-outside-should-read"


# ============================================================
# The symlinked artifact folder the listing walked straight into
# ============================================================

def _archive(root: Path) -> Path:
    d = root / ARTIFACT_ROOT / "posts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _real_artifact(root: Path, slug: str, body: str = "ordinary post") -> Path:
    folder = _archive(root) / slug
    folder.mkdir(parents=True)
    (folder / f"{slug}.md").write_text(
        f"---\ntitle: {slug}\ndate: 2026-01-01\n---\n\n{body}\n", encoding="utf-8")
    return folder


def _outside_tree(tmp_path: Path, slug: str) -> Path:
    """A directory OUTSIDE the archive holding a well-formed artifact."""
    outside = tmp_path / "elsewhere" / slug
    outside.mkdir(parents=True)
    (outside / f"{slug}.md").write_text(
        f"---\ntitle: Leaked\ndate: 2026-01-01\n---\n\n{LEAK_MARKER}\n", encoding="utf-8")
    return outside


def test_a_symlinked_artifact_folder_is_not_listed(tmp_path):
    """The reported reproduction. `folder.is_dir()` follows the link."""
    data_root = tmp_path / "data"
    _archive(data_root)
    outside = _outside_tree(tmp_path, "2026-01-01-test")
    (_archive(data_root) / "2026-01-01-test").symlink_to(outside, target_is_directory=True)

    got = list_artifacts(data_root)
    assert got["artifacts"] == []
    assert got["total"] == 0


def test_the_linked_file_content_never_reaches_the_page(tmp_path):
    """The consequence, asserted on the payload rather than on the count."""
    data_root = tmp_path / "data"
    _archive(data_root)
    outside = _outside_tree(tmp_path, "2026-01-01-test")
    (_archive(data_root) / "2026-01-01-test").symlink_to(outside, target_is_directory=True)

    assert LEAK_MARKER not in repr(list_artifacts(data_root))


def test_a_symlinked_folder_is_refused_even_pointing_back_inside(tmp_path):
    """Containment is not the rule here; the workspace bans links outright.

    A link that resolves back inside the archive passes every containment
    check ever written, which is exactly why the ban has to be its own test.
    """
    data_root = tmp_path / "data"
    _real_artifact(data_root, "2026-01-01-real")
    (_archive(data_root) / "2026-02-02-alias").symlink_to(
        _archive(data_root) / "2026-01-01-real", target_is_directory=True)

    slugs = [a["slug"] for a in list_artifacts(data_root)["artifacts"]]
    assert slugs == ["2026-01-01-real"]


def test_a_symlinked_markdown_leaf_is_still_refused(tmp_path):
    """The old guard's one real job must survive the widening."""
    data_root = tmp_path / "data"
    folder = _archive(data_root) / "2026-01-01-leaf"
    folder.mkdir(parents=True)
    outside_md = tmp_path / "secret.md"
    outside_md.write_text(f"---\ntitle: X\n---\n\n{LEAK_MARKER}\n", encoding="utf-8")
    (folder / "2026-01-01-leaf.md").symlink_to(outside_md)

    assert list_artifacts(data_root)["artifacts"] == []


def test_an_oversize_source_is_still_refused(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    _real_artifact(data_root, "2026-01-01-big", body="x" * 4000)
    monkeypatch.setattr(studio_src, "ARTIFACT_MD_MAX_BYTES", 100)
    assert list_artifacts(data_root)["artifacts"] == []


def test_an_ordinary_artifact_is_still_listed(tmp_path):
    """The guard must refuse links, not content."""
    data_root = tmp_path / "data"
    _real_artifact(data_root, "2026-01-01-real")
    got = list_artifacts(data_root)
    assert [a["slug"] for a in got["artifacts"]] == ["2026-01-01-real"]
    assert got["total"] == 1


def test_the_skipped_artifact_is_named_in_the_log(tmp_path, caplog):
    data_root = tmp_path / "data"
    _archive(data_root)
    outside = _outside_tree(tmp_path, "2026-01-01-test")
    (_archive(data_root) / "2026-01-01-test").symlink_to(outside, target_is_directory=True)

    with caplog.at_level(logging.WARNING, logger=studio_src.__name__):
        list_artifacts(data_root)
    assert any("2026-01-01-test" in r.getMessage() for r in caplog.records), caplog.text


def test_the_detail_view_refuses_the_same_folder(tmp_path):
    """Listing and drill-down must agree, which is this module's own lesson.

    The detail view already refused a link pointing OUT (containment); it
    served one pointing back in.
    """
    data_root = tmp_path / "data"
    _real_artifact(data_root, "2026-01-01-real")
    (_archive(data_root) / "2026-02-02-alias").symlink_to(
        _archive(data_root) / "2026-01-01-real", target_is_directory=True)

    assert read_artifact(data_root, "post", "2026-02-02-alias")["ok"] is False


def test_the_detail_view_still_serves_a_real_artifact(tmp_path):
    data_root = tmp_path / "data"
    _real_artifact(data_root, "2026-01-01-real", body="ordinary body text")
    got = read_artifact(data_root, "post", "2026-01-01-real")
    assert got["ok"] is True
    assert "ordinary body text" in got["content"]


def test_the_detail_view_refuses_a_linked_leaf(tmp_path):
    data_root = tmp_path / "data"
    folder = _archive(data_root) / "2026-01-01-leaf"
    folder.mkdir(parents=True)
    outside_md = tmp_path / "secret.md"
    outside_md.write_text(f"---\ntitle: X\n---\n\n{LEAK_MARKER}\n", encoding="utf-8")
    (folder / "2026-01-01-leaf.md").symlink_to(outside_md)

    got = read_artifact(data_root, "post", "2026-01-01-leaf")
    assert got["ok"] is False
    assert LEAK_MARKER not in repr(got)


# ============================================================
# The search that matched only the newest fifty
# ============================================================

def _inflight(data_root: Path, names: list[str]) -> None:
    d = data_root / "outputs" / "intel"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_text("body\n", encoding="utf-8")


def _many_files(n: int) -> list[str]:
    return [f"filler-{i:03d}.md" for i in range(n)]


def test_a_file_below_the_display_cap_is_still_findable(tmp_path):
    """The reported reproduction: 60 files, the target is not in the newest 50."""
    import os
    _inflight(tmp_path, ["quarterly-review-acme.md", *_many_files(60)])
    target = tmp_path / "outputs" / "intel" / "quarterly-review-acme.md"
    # Make the target the OLDEST, so a cap on the newest 50 excludes it.
    old = target.stat().st_mtime - 60 * 60
    os.utime(target, (old, old))

    hits = search(tmp_path, "acme")["categories"].get("studio", [])
    assert [h["name"] for h in hits] == ["quarterly-review-acme.md"]


def test_the_page_still_caps_its_display_list(tmp_path):
    """Fixing search must not turn the /studio page into an unbounded list."""
    _inflight(tmp_path, _many_files(STUDIO_ROW_CAP + 10))
    got = recent_inflight_items(tmp_path)
    assert len(got["items"]) == STUDIO_ROW_CAP
    assert got["total_count"] == STUDIO_ROW_CAP + 10


def test_an_uncapped_scan_returns_every_item(tmp_path):
    _inflight(tmp_path, _many_files(STUDIO_ROW_CAP + 10))
    got = recent_inflight_items(tmp_path, cap=None)
    assert len(got["items"]) == STUDIO_ROW_CAP + 10
    assert got["total_count"] == STUDIO_ROW_CAP + 10


def test_the_categories_follow_the_returned_items(tmp_path):
    """counts describe what came back, at either cap."""
    _inflight(tmp_path, _many_files(STUDIO_ROW_CAP + 10))
    capped = recent_inflight_items(tmp_path)
    uncapped = recent_inflight_items(tmp_path, cap=None)
    assert sum(capped["categories"].values()) == len(capped["items"])
    assert sum(uncapped["categories"].values()) == len(uncapped["items"])


def test_search_still_honours_its_own_per_category_limit(tmp_path):
    """Uncapping the SOURCE must not uncap the RESULT."""
    _inflight(tmp_path, [f"acme-{i:03d}.md" for i in range(80)])
    hits = search(tmp_path, "acme")["categories"].get("studio", [])
    assert len(hits) == 10


def test_search_finds_nothing_when_nothing_matches(tmp_path):
    _inflight(tmp_path, _many_files(60))
    assert "studio" not in search(tmp_path, "acme")["categories"]


# ============================================================
# The default the signature never had
# ============================================================

def test_read_contact_requires_its_root(tmp_path):
    """Taking the old sentence literally is a TypeError, so it had to go."""
    with pytest.raises(TypeError):
        read_contact(slug="james-bond")


def test_read_contact_still_accepts_an_explicit_none(tmp_path, monkeypatch):
    """The fallback is real; only the "omit it" reading was not."""
    import scripts.bridge_daemon.sources.tribe as tribe_src
    monkeypatch.setattr(tribe_src, "get_data_root", lambda: tmp_path)
    got = read_contact(None, "nobody-here")
    assert got["ok"] is False   # resolved a root, then found no such contact


def test_the_docstring_no_longer_invites_the_omission():
    doc = read_contact.__doc__
    assert "the argument itself is REQUIRED" in doc
    # The correction QUOTES the sentence it replaced, so a bare `not in` would
    # fail on the fix. Pin the order: the old wording may appear only after the
    # clause that marks it as the old wording.
    assert doc.index('used to read "when not supplied"') < doc.index(
        'which invites ``read_contact(slug=...)``')
