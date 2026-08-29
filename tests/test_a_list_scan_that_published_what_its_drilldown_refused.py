"""A list scan that published exactly what its own drill-down refused.

Two bridge sources kept a symlink policy in the READ layer and nothing in the
LIST layer, so the dashboard advertised rows that could not be opened, and the
advertisement itself carried content from outside the workspace.

* ``capabilities.list_capabilities`` iterated ``skills_dir.iterdir()`` and
  filtered on ``d.is_dir()``, which follows a symlink. A link planted at
  ``.claude/skills/<name>`` had the frontmatter of a ``SKILL.md`` outside the
  workspace parsed and published: name, description, author, capability text.
  ``read_skill``, three functions down the same file, refuses that identical
  skill.

* ``approvals.list_approvals`` globbed ``*.md`` and filtered on ``p.is_file()``,
  same following behaviour. A link planted in the drafts directory had its
  first 4 KB read and its ``**To:**`` and ``**Subject:**`` surfaced on the
  approvals queue, and the row stayed markable-sent. ``read_draft`` refuses it.

Both were measured on 2026-08-29 before the fix: the symlinked skill listed
under the outside file's frontmatter name, and the symlinked draft listed with
the outside file's recipient.

Each case asserts a genuine sibling row is present in the same result, so a
scan that returned nothing at all could not pass this file.
"""
import os

import pytest

from scripts.bridge_daemon.sources import approvals, capabilities


# ============================================================
# Fixtures: one honest row plus one symlinked row, per surface
# ============================================================

def _skills_workspace(root, outside):
    """A skills tree holding one real skill and one symlinked one."""
    skills = root / ".claude" / "skills"
    (skills / "dossier").mkdir(parents=True)
    (skills / "dossier" / "SKILL.md").write_text(
        "---\nname: dossier\ndescription: Build a dossier on a target.\n---\nbody\n",
        encoding="utf-8",
    )
    elsewhere = outside / "planted"
    elsewhere.mkdir(parents=True)
    (elsewhere / "SKILL.md").write_text(
        "---\nname: read-from-outside\ndescription: Lives outside the workspace.\n"
        "metadata:\n  author: James Bond\n---\n",
        encoding="utf-8",
    )
    os.symlink(elsewhere, skills / "planted")
    return root


def _drafts_workspace(root, outside):
    """A drafts directory holding one real draft and one symlinked one."""
    drafts = root / approvals.EMAIL_DRAFTS_DIR
    drafts.mkdir(parents=True)
    (drafts / "acme-intro.md").write_text(
        "**To:** bond@example.com\n**Subject:** Acme Telecom introduction\n"
        "\n---\n\nBody.\n",
        encoding="utf-8",
    )
    planted = outside / "planted.md"
    planted.write_text(
        "**To:** moneypenny@example.com\n**Subject:** Read from outside the workspace\n"
        "\n---\n\nBody.\n",
        encoding="utf-8",
    )
    os.symlink(planted, drafts / "planted.md")
    return root


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "engine"
    ws.mkdir()
    return ws


@pytest.fixture
def outside(tmp_path):
    out = tmp_path / "outside"
    out.mkdir()
    return out


# ============================================================
# capabilities.list_capabilities
# ============================================================

def test_the_skills_scan_returns_the_real_skill(workspace, outside):
    """Corpus guard: the scan is not passing the tests below by returning []."""
    result = capabilities.list_capabilities(_skills_workspace(workspace, outside))
    assert [s["slug"] for s in result["skills"]] == ["dossier"]
    assert result["count"] == 1


def test_a_symlinked_skill_directory_is_not_listed(workspace, outside):
    result = capabilities.list_capabilities(_skills_workspace(workspace, outside))
    slugs = [s["slug"] for s in result["skills"]]
    assert "planted" not in slugs, (
        "the list scan followed a symlinked skill directory that read_skill refuses"
    )


def test_no_frontmatter_from_outside_the_workspace_is_published(workspace, outside):
    result = capabilities.list_capabilities(_skills_workspace(workspace, outside))
    published = repr(result["skills"])
    assert "read-from-outside" not in published
    assert "James Bond" not in published
    assert "Lives outside the workspace" not in published


def test_the_skills_list_and_drilldown_agree_on_every_row(workspace, outside):
    """Whatever the list advertises, the drill-down must be able to open."""
    ws = _skills_workspace(workspace, outside)
    listed = capabilities.list_capabilities(ws)["skills"]
    assert listed, "empty corpus proves nothing"
    for row in listed:
        assert capabilities.read_skill(ws, row["slug"])["ok"] is True, (
            f"list published {row['slug']!r} but read_skill refuses it"
        )


def test_the_symlinked_skill_is_still_refused_by_the_read_layer(workspace, outside):
    """The read layer's refusal is what the list layer now matches."""
    ws = _skills_workspace(workspace, outside)
    assert capabilities.read_skill(ws, "planted")["ok"] is False


# ============================================================
# approvals.list_approvals
# ============================================================

def test_the_drafts_scan_returns_the_real_draft(workspace, outside):
    """Corpus guard: the scan is not passing the tests below by returning []."""
    result = approvals.list_approvals(_drafts_workspace(workspace, outside))
    assert [i["filename"] for i in result["items"]] == ["acme-intro.md"]
    assert result["total"] == 1


def test_a_symlinked_draft_is_not_listed(workspace, outside):
    result = approvals.list_approvals(_drafts_workspace(workspace, outside))
    names = [i["filename"] for i in result["items"]]
    assert "planted.md" not in names, (
        "the list scan followed a symlinked draft that read_draft refuses"
    )


def test_no_draft_headers_from_outside_the_workspace_are_published(workspace, outside):
    result = approvals.list_approvals(_drafts_workspace(workspace, outside))
    published = repr(result["items"])
    assert "moneypenny@example.com" not in published
    assert "Read from outside the workspace" not in published


def test_the_drafts_list_and_drilldown_agree_on_every_row(workspace, outside):
    ws = _drafts_workspace(workspace, outside)
    listed = approvals.list_approvals(ws)["items"]
    assert listed, "empty corpus proves nothing"
    for row in listed:
        assert approvals.read_draft(ws, row["path"])["ok"] is True, (
            f"list published {row['path']!r} but read_draft refuses it"
        )


def test_the_symlinked_draft_is_still_refused_by_the_read_layer(workspace, outside):
    ws = _drafts_workspace(workspace, outside)
    rel = f"{approvals.EMAIL_DRAFTS_DIR}/planted.md"
    assert approvals.read_draft(ws, rel)["ok"] is False
