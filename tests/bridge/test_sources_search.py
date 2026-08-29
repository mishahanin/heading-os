"""Unit tests for unified /search source."""
import json
from datetime import date
from pathlib import Path

from scripts.bridge_daemon.sources.search import search


def _setup_workspace(tmp_path):
    """Populate a minimal workspace with one fixture per source."""
    # Inbox
    em_dir = tmp_path / "outputs" / "operations" / "email-intelligence"
    em_dir.mkdir(parents=True)
    (em_dir / "_latest-fetch.json").write_text(json.dumps({
        "run_info": {"timestamp": "2026-05-18T10:00:00+00:00"},
        "conversations": [
            {"id": "c1", "topic": "ExampleProject project Globex briefing", "priority": "P1",
             "latest_datetime": "2026-05-18T08:00:00+00:00", "analysis": {}},
            {"id": "c2", "topic": "Unrelated thread", "priority": "P3",
             "latest_datetime": "2026-05-15T08:00:00+00:00", "analysis": {}},
        ],
    }), encoding="utf-8")
    # Tribe
    crm_dir = tmp_path / "crm" / "contacts"
    crm_dir.mkdir(parents=True)
    (crm_dir / "victor-stein.md").write_text(
        "---\nrelationship_type: tribe-leadership\nlast_touch: 2026-05-15\n---\n\n# Marlow Rivera (operator)\n\nBody.\n",
        encoding="utf-8",
    )
    (crm_dir / "raul-mendez.md").write_text(
        "---\nrelationship_type: tribe\nlast_touch: 2026-04-28\n---\n\n# Raul Mendez (operator)\n\nBody.\n",
        encoding="utf-8",
    )
    # Tasks
    (tmp_path / "outputs" / "operations" / "viraid").mkdir(parents=True)
    (tmp_path / "outputs" / "operations" / "viraid" / "tasks.md").write_text(
        "## Active\n\n"
        "- [ ] **2026-05-11** | `P1` | ExampleProject integration call with Globex | *Task* | Due: 2026-05-15\n"
        "- [ ] **2026-05-11** | `P2` | Generic task | *Task* | Due: 2026-05-20\n",
        encoding="utf-8",
    )
    # Library
    kn_dir = tmp_path / "knowledge"
    kn_dir.mkdir()
    (kn_dir / "exampleproject.md").write_text(
        '---\ntitle: "ExampleProject project lessons"\ntype: position\nupdated: 2026-05-17\n---\n\n# ExampleProject project lessons\n\nBody.\n',
        encoding="utf-8",
    )
    # Studio: just leave a file in linkedin
    li_dir = tmp_path / "outputs" / "content" / "linkedin"
    li_dir.mkdir(parents=True)
    (li_dir / "exampleproject-post.md").write_text("body", encoding="utf-8")
    # Day: today's calendar
    cal_dir = tmp_path / "outputs" / "_sync" / "calendar"
    cal_dir.mkdir(parents=True)
    from datetime import datetime, timezone

    # `today_agenda()` dates the file through `get_default_tz()`, so this reads
    # the same seam. It was the literal `Etc/GMT-4`, equal to the conftest pin
    # by coincidence; the moment that pin moves, the fixture writes a filename
    # the code does not look for, four hours out of every day.
    from scripts.utils.workspace import get_default_tz
    today_local = datetime.now(timezone.utc).astimezone(get_default_tz()).strftime("%Y-%m-%d")
    (cal_dir / f"{today_local}.md").write_text(
        "| 09:00 | ExampleProject review | - |\n"
        "| 13:00 | Other meeting | - |\n",
        encoding="utf-8",
    )
    # Capabilities
    sk_dir = tmp_path / ".claude" / "skills" / "exampleproject-recap"
    sk_dir.mkdir(parents=True)
    (sk_dir / "SKILL.md").write_text(
        '---\nname: exampleproject-recap\ndescription: "Summarize the ExampleProject project status"\nmetadata:\n  version: "1.0"\n---\n\n# ExampleProject recap\n',
        encoding="utf-8",
    )


def test_empty_query_returns_empty(tmp_path):
    """An empty query string returns total 0, no categories."""
    _setup_workspace(tmp_path)
    result = search(tmp_path, "")
    assert result["total"] == 0
    assert result["categories"] == {}
    assert result["query"] == ""


def test_exampleproject_hits_all_sources(tmp_path):
    """A query matching across all sources returns hits in each."""
    _setup_workspace(tmp_path)
    result = search(tmp_path, "ExampleProject")
    cats = result["categories"]
    assert "inbox" in cats and len(cats["inbox"]) == 1
    assert "tasks" in cats and len(cats["tasks"]) == 1
    assert "library" in cats and len(cats["library"]) == 1
    assert "studio" in cats and len(cats["studio"]) == 1
    assert "capabilities" in cats and len(cats["capabilities"]) == 1
    # Day depends on today's calendar fixture being valid.
    assert result["total"] >= 5


def test_case_insensitive(tmp_path):
    """Search is case-insensitive."""
    _setup_workspace(tmp_path)
    result1 = search(tmp_path, "EXAMPLEPROJECT")
    result2 = search(tmp_path, "exampleproject")
    assert result1["total"] == result2["total"]


def test_no_match_returns_empty_categories(tmp_path):
    """A query with no matches returns empty categories + total 0."""
    _setup_workspace(tmp_path)
    result = search(tmp_path, "nonexistent-term-xyz")
    assert result["total"] == 0
    # Categories dict has only entries for sources that found hits.
    assert result["categories"] == {}


def test_tribe_search_by_name(tmp_path):
    """Tribe search hits on the H1 display name."""
    _setup_workspace(tmp_path)
    result = search(tmp_path, "Rivera")
    assert "tribe" in result["categories"]
    assert result["categories"]["tribe"][0]["name"] == "Marlow Rivera"


def test_tasks_search_by_description(tmp_path):
    """Tasks search matches the description body."""
    _setup_workspace(tmp_path)
    result = search(tmp_path, "Globex")
    assert "tasks" in result["categories"]
    assert "Globex" in result["categories"]["tasks"][0]["description"]


def test_limit_per_category(tmp_path):
    """Each category caps results at the requested limit."""
    _setup_workspace(tmp_path)
    # Add many extra tribe entries.
    crm_dir = tmp_path / "crm" / "contacts"
    for i in range(20):
        (crm_dir / f"member-{i:02d}.md").write_text(
            f"---\nrelationship_type: tribe\nlast_touch: 2026-05-10\n---\n\n# ExampleProject Member {i}\n",
            encoding="utf-8",
        )
    result = search(tmp_path, "ExampleProject", limit=5)
    assert len(result["categories"]["tribe"]) == 5


def test_data_time_is_iso_utc(tmp_path):
    """data_time is ISO 8601 UTC string."""
    _setup_workspace(tmp_path)
    from datetime import datetime
    result = search(tmp_path, "ExampleProject")
    parsed = datetime.fromisoformat(result["data_time"])
    assert parsed.tzinfo is not None


# ============================================================
# Phase 1.37: pipeline + investors search categories
# ============================================================
def _write_pipeline(tmp_path, body):
    p = tmp_path / "context" / "pipeline.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _write_investor_shortlist(tmp_path, body):
    from scripts.bridge_daemon.sources.investors import PROGRAM_DIR
    p = tmp_path / PROGRAM_DIR / "00-master-shortlist-v1.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_pipeline_search_matches_company(tmp_path):
    _write_pipeline(tmp_path,
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---------|---------|-------|------------|------------|-------|-------------|----------|\n"
        "| Acme Co | USA | Proposal | $1,000,000 | 2026-05-01 | Operator | Send NDA | - |\n"
    )
    result = search(tmp_path, "Acme")
    assert "pipeline" in result["categories"]
    assert result["categories"]["pipeline"][0]["company"] == "Acme Co"


def test_pipeline_search_matches_next_action(tmp_path):
    """Substring match against next_action also surfaces the deal."""
    _write_pipeline(tmp_path,
        "## Active Deals\n\n"
        "| Company | Country | Stage | Est. Value | Stage Date | Owner | Next Action | Due Date |\n"
        "|---------|---------|-------|------------|------------|-------|-------------|----------|\n"
        "| Acme Co | USA | Proposal | $1,000,000 | 2026-05-01 | Operator | Send NDA tomorrow | - |\n"
    )
    result = search(tmp_path, "NDA")
    assert "pipeline" in result["categories"]


def test_investor_search_matches_firm(tmp_path):
    _write_investor_shortlist(tmp_path,
        "## Europe (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 8 | Contoso Capital | VC | Hamburg | 20M | HIGH | Telco DNA |\n"
    )
    result = search(tmp_path, "Contoso Capital")
    assert "investors" in result["categories"]
    assert result["categories"]["investors"][0]["firm"] == "Contoso Capital"


def test_investor_search_matches_region(tmp_path):
    """Region match surfaces the firm too."""
    _write_investor_shortlist(tmp_path,
        "## US (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 14 | Example Ventures | VC | London | 50M | HIGH | x |\n"
    )
    result = search(tmp_path, "London")
    assert "investors" in result["categories"]


def test_investor_search_surfaces_sent_status(tmp_path):
    """Search result includes sent_date if firm was marked sent."""
    from scripts.bridge_daemon.sources.investors import mark_sent
    _write_investor_shortlist(tmp_path,
        "## Europe (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 8 | Contoso Capital | VC | Hamburg | 20M | HIGH | x |\n"
    )
    mark_sent(tmp_path, 8)
    result = search(tmp_path, "Contoso Capital")
    hit = result["categories"]["investors"][0]
    assert hit["sent_date"] is not None


# ---- the two roots, which this file's single tmp_path could not see ----
#
# Found by the 2026-08-23 audit. `search()` took ONE root and handed it to eight
# sources. Seven of them read the DATA overlay; `list_capabilities` reads
# `.claude/skills`, which is ENGINE. `app.py` passes `data_root`, so on the
# two-part topology the search page's capability results came from
# `<data-root>/.claude/skills`.
#
# That directory is not empty on this machine — it holds one skill — so the
# search page reported 1 skill where the `/capabilities` endpoint, which is
# correctly given `workspace_root`, reports 96. Not a crash and not an obviously
# empty section: a plausible wrong number.
#
# Every test above seeds both trees under one `tmp_path`, which is exactly why
# none of them could fail on this.


def _split_roots(tmp_path):
    """A DATA overlay and an ENGINE tree that are genuinely different directories."""
    data = tmp_path / "data"
    engine = tmp_path / "engine"
    data.mkdir()
    engine.mkdir()
    _setup_workspace(data)

    # The real skills live in the ENGINE tree.
    sk = engine / ".claude" / "skills" / "exampleproject-recap"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        '---\nname: exampleproject-recap\ndescription: "Summarize the ExampleProject '
        'project status"\nmetadata:\n  version: "1.0"\n---\n\n# ExampleProject recap\n',
        encoding="utf-8")

    # A stale near-namesake in the DATA overlay, standing in for the one skill
    # that really does sit under `.heading-os-data/.claude/skills`. If search
    # reads the wrong root it finds THIS instead, and says so plausibly.
    stale = data / ".claude" / "skills" / "exampleproject-stale"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text(
        '---\nname: exampleproject-stale\ndescription: "A stale ExampleProject copy '
        'in the data overlay"\nmetadata:\n  version: "0.1"\n---\n\n# stale\n',
        encoding="utf-8")
    return data, engine


def test_capabilities_are_searched_in_the_engine_tree(tmp_path):
    data, engine = _split_roots(tmp_path)
    result = search(data, "exampleproject", workspace_root=engine)
    names = [c["name"] for c in result["categories"].get("capabilities", [])]
    assert names == ["exampleproject-recap"], (
        f"search read skills from the wrong root; got {names}"
    )


def test_the_data_sources_still_read_the_data_overlay(tmp_path):
    """The mutation guard: the fix must not send everything to the engine root."""
    data, engine = _split_roots(tmp_path)
    result = search(data, "exampleproject", workspace_root=engine)
    for category in ("inbox", "tasks", "library"):
        assert result["categories"].get(category), (
            f"{category} lost its results, so a DATA source is now reading the "
            "engine tree"
        )


def test_one_root_still_works_for_a_single_tree_clone(tmp_path):
    """A public clone with no overlay passes one root; that must keep working."""
    _setup_workspace(tmp_path)
    result = search(tmp_path, "exampleproject")
    assert result["categories"].get("capabilities")
