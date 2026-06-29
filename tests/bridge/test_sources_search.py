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
            {"id": "c1", "topic": "Picasso project Lenovo briefing", "priority": "P1",
             "latest_datetime": "2026-05-18T08:00:00+00:00", "analysis": {}},
            {"id": "c2", "topic": "Unrelated thread", "priority": "P3",
             "latest_datetime": "2026-05-15T08:00:00+00:00", "analysis": {}},
        ],
    }), encoding="utf-8")
    # Tribe
    crm_dir = tmp_path / "crm" / "contacts"
    crm_dir.mkdir(parents=True)
    (crm_dir / "victor-stein.md").write_text(
        "---\nrelationship_type: tribe-leadership\nlast_touch: 2026-05-15\n---\n\n# Sam Rivera (misha-hanin)\n\nBody.\n",
        encoding="utf-8",
    )
    (crm_dir / "raul-mendez.md").write_text(
        "---\nrelationship_type: tribe\nlast_touch: 2026-04-28\n---\n\n# Raul Mendez (misha-hanin)\n\nBody.\n",
        encoding="utf-8",
    )
    # Tasks
    (tmp_path / "outputs" / "operations" / "viraid").mkdir(parents=True)
    (tmp_path / "outputs" / "operations" / "viraid" / "tasks.md").write_text(
        "## Active\n\n"
        "- [ ] **2026-05-11** | `P1` | Picasso integration call with Lenovo | *Task* | Due: 2026-05-15\n"
        "- [ ] **2026-05-11** | `P2` | Generic task | *Task* | Due: 2026-05-20\n",
        encoding="utf-8",
    )
    # Library
    kn_dir = tmp_path / "knowledge"
    kn_dir.mkdir()
    (kn_dir / "picasso.md").write_text(
        '---\ntitle: "Picasso project lessons"\ntype: position\nupdated: 2026-05-17\n---\n\n# Picasso project lessons\n\nBody.\n',
        encoding="utf-8",
    )
    # Studio: just leave a file in linkedin
    li_dir = tmp_path / "outputs" / "content" / "linkedin"
    li_dir.mkdir(parents=True)
    (li_dir / "picasso-post.md").write_text("body", encoding="utf-8")
    # Day: today's calendar
    cal_dir = tmp_path / "outputs" / "_sync" / "calendar"
    cal_dir.mkdir(parents=True)
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    # Use local (UTC+4) TZ since today_agenda() reads in local (UTC+4)-local time.
    today_local = datetime.now(timezone.utc).astimezone(ZoneInfo("Etc/GMT-4")).strftime("%Y-%m-%d")
    (cal_dir / f"{today_local}.md").write_text(
        "| 09:00 | Picasso review | - |\n"
        "| 13:00 | Other meeting | - |\n",
        encoding="utf-8",
    )
    # Capabilities
    sk_dir = tmp_path / ".claude" / "skills" / "picasso-recap"
    sk_dir.mkdir(parents=True)
    (sk_dir / "SKILL.md").write_text(
        '---\nname: picasso-recap\ndescription: "Summarize the Picasso project status"\nmetadata:\n  version: "1.0"\n---\n\n# Picasso recap\n',
        encoding="utf-8",
    )


def test_empty_query_returns_empty(tmp_path):
    """An empty query string returns total 0, no categories."""
    _setup_workspace(tmp_path)
    result = search(tmp_path, "")
    assert result["total"] == 0
    assert result["categories"] == {}
    assert result["query"] == ""


def test_picasso_hits_all_sources(tmp_path):
    """A query matching across all sources returns hits in each."""
    _setup_workspace(tmp_path)
    result = search(tmp_path, "Picasso")
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
    result1 = search(tmp_path, "PICASSO")
    result2 = search(tmp_path, "picasso")
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
    assert result["categories"]["tribe"][0]["name"] == "Sam Rivera"


def test_tasks_search_by_description(tmp_path):
    """Tasks search matches the description body."""
    _setup_workspace(tmp_path)
    result = search(tmp_path, "Lenovo")
    assert "tasks" in result["categories"]
    assert "Lenovo" in result["categories"]["tasks"][0]["description"]


def test_limit_per_category(tmp_path):
    """Each category caps results at the requested limit."""
    _setup_workspace(tmp_path)
    # Add many extra tribe entries.
    crm_dir = tmp_path / "crm" / "contacts"
    for i in range(20):
        (crm_dir / f"member-{i:02d}.md").write_text(
            f"---\nrelationship_type: tribe\nlast_touch: 2026-05-10\n---\n\n# Picasso Member {i}\n",
            encoding="utf-8",
        )
    result = search(tmp_path, "Picasso", limit=5)
    assert len(result["categories"]["tribe"]) == 5


def test_data_time_is_iso_utc(tmp_path):
    """data_time is ISO 8601 UTC string."""
    _setup_workspace(tmp_path)
    from datetime import datetime
    result = search(tmp_path, "Picasso")
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
        "| Acme Co | USA | Proposal | $1,000,000 | 2026-05-01 | Misha | Send NDA | - |\n"
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
        "| Acme Co | USA | Proposal | $1,000,000 | 2026-05-01 | Misha | Send NDA tomorrow | - |\n"
    )
    result = search(tmp_path, "NDA")
    assert "pipeline" in result["categories"]


def test_investor_search_matches_firm(tmp_path):
    _write_investor_shortlist(tmp_path,
        "## Europe (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 8 | DTCP | VC | Hamburg | 20M | HIGH | Telco DNA |\n"
    )
    result = search(tmp_path, "DTCP")
    assert "investors" in result["categories"]
    assert result["categories"]["investors"][0]["firm"] == "DTCP"


def test_investor_search_matches_region(tmp_path):
    """Region match surfaces the firm too."""
    _write_investor_shortlist(tmp_path,
        "## US (1)\n\n"
        "| # | Firm | Type | HQ | Cheque | Fit | Notes |\n"
        "|---|------|------|----|--------|-----|-------|\n"
        "| 14 | Ten Eleven | VC | London | 50M | HIGH | x |\n"
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
        "| 8 | DTCP | VC | Hamburg | 20M | HIGH | x |\n"
    )
    mark_sent(tmp_path, 8)
    result = search(tmp_path, "DTCP")
    hit = result["categories"]["investors"][0]
    assert hit["sent_date"] is not None
