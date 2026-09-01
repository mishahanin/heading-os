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
# The same disagreement, one field over: the SIZE cap
# ============================================================
# The symlink rule was not the only policy `read_skill` and `read_draft` kept to
# themselves. Both refuse a file over 200 KB, and neither list scan asked.
# MEASURED 2026-09-01 on a 250 KB SKILL.md and a 250 KB draft, before the fix:
#
#   list_capabilities -> ['huge']   read_skill -> file too large (250069 bytes)
#   list_approvals    -> ['...huge.md']  read_draft -> file too large (250054)
#
# The agreement tests above could not see it: their corpus holds one honest row
# and one symlinked row, and neither sits near the cap, so the bound had no
# case on either side of it.


def _oversized(path, header, filler):
    path.write_text(header + filler * (capabilities.SKILL_MAX_BYTES + 50_000),
                    encoding="utf-8")
    return path


def test_an_oversized_skill_is_not_listed(workspace, outside):
    ws = _skills_workspace(workspace, outside)
    huge = ws / ".claude" / "skills" / "huge"
    huge.mkdir()
    _oversized(huge / "SKILL.md",
               "---\nname: huge\ndescription: Manifest past the cap.\n---\n", "x")

    slugs = [s["slug"] for s in capabilities.list_capabilities(ws)["skills"]]
    assert "dossier" in slugs, "empty corpus proves nothing"
    assert "huge" not in slugs, (
        "the list published a skill whose manifest read_skill refuses on size")


def test_the_oversized_skill_is_still_refused_by_the_read_layer(workspace, outside):
    """The refusal the list layer now matches. Asserted on the REASON, so a
    row refused for some other cause could not stand in for this one."""
    ws = _skills_workspace(workspace, outside)
    huge = ws / ".claude" / "skills" / "huge"
    huge.mkdir()
    _oversized(huge / "SKILL.md", "---\nname: huge\n---\n", "x")

    result = capabilities.read_skill(ws, "huge")
    assert result["ok"] is False
    assert "too large" in result["error"], result


def test_a_skill_just_under_the_cap_is_still_listed(workspace, outside):
    """The other side of the bound. A cap applied one byte too eagerly hides
    real skills from the dashboard, which is the opposite failure."""
    ws = _skills_workspace(workspace, outside)
    ordinary = ws / ".claude" / "skills" / "ordinary"
    ordinary.mkdir()
    body = "y" * (capabilities.SKILL_MAX_BYTES - 200)
    (ordinary / "SKILL.md").write_text(
        f"---\nname: ordinary\ndescription: Just under the cap.\n---\n{body}",
        encoding="utf-8")

    slugs = [s["slug"] for s in capabilities.list_capabilities(ws)["skills"]]
    assert "ordinary" in slugs, slugs
    assert capabilities.read_skill(ws, "ordinary")["ok"] is True


def test_an_oversized_draft_is_not_listed(workspace, outside):
    ws = _drafts_workspace(workspace, outside)
    drafts = ws / approvals.EMAIL_DRAFTS_DIR
    _oversized(drafts / "huge.md",
               "**To:** bond@example.com\n**Subject:** Past the cap\n\n---\n\n", "z")

    names = [i["filename"] for i in approvals.list_approvals(ws)["items"]]
    assert "acme-intro.md" in names, "empty corpus proves nothing"
    assert "huge.md" not in names, (
        "the queue published a draft read_draft refuses on size, and the row "
        "stays markable-sent")


def test_the_oversized_draft_is_still_refused_by_the_read_layer(workspace, outside):
    ws = _drafts_workspace(workspace, outside)
    drafts = ws / approvals.EMAIL_DRAFTS_DIR
    _oversized(drafts / "huge.md", "**To:** bond@example.com\n\n---\n\n", "z")

    result = approvals.read_draft(ws, f"{approvals.EMAIL_DRAFTS_DIR}/huge.md")
    assert result["ok"] is False
    assert "too large" in result["error"], result


def test_a_draft_just_under_the_cap_is_still_listed(workspace, outside):
    ws = _drafts_workspace(workspace, outside)
    drafts = ws / approvals.EMAIL_DRAFTS_DIR
    body = "w" * (approvals.DRAFT_MAX_BYTES - 200)
    (drafts / "ordinary.md").write_text(
        f"**To:** bond@example.com\n**Subject:** Just under\n\n---\n\n{body}",
        encoding="utf-8")

    names = [i["filename"] for i in approvals.list_approvals(ws)["items"]]
    assert "ordinary.md" in names, names
    assert approvals.read_draft(
        ws, f"{approvals.EMAIL_DRAFTS_DIR}/ordinary.md")["ok"] is True


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
