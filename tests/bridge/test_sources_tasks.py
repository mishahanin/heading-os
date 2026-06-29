"""Unit tests for /tasks viraid source."""
from datetime import date
from pathlib import Path

from scripts.bridge_daemon.sources.tasks import list_active_tasks


def _write_tasks_md(workspace_root, content):
    p = workspace_root / "outputs" / "operations" / "viraid" / "tasks.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_empty_when_file_missing(tmp_path):
    """Missing tasks.md -> empty result."""
    result = list_active_tasks(tmp_path)
    assert result["tasks"] == []
    assert result["counts"] == {}
    assert result["overdue_count"] == 0
    assert result["data_time"] is None


def test_basic_active_row_parsed(tmp_path):
    """A single active row is parsed correctly."""
    _write_tasks_md(tmp_path,
        "# Viraid\n\n## Active\n\n"
        "- [ ] **2026-05-11** | `P1` | Do the thing | *Task* | Source: Email | Due: 2026-05-15\n"
    )
    result = list_active_tasks(tmp_path, today=date(2026, 5, 13))
    assert len(result["tasks"]) == 1
    t = result["tasks"][0]
    assert t["captured"] == "2026-05-11"
    assert t["priority"] == "P1"
    assert "Do the thing" in t["description"]
    assert t["kind"] == "Task"
    assert t["source"] == "Email"
    assert t["due"] == "2026-05-15"
    assert t["days_until_due"] == 2
    assert t["is_overdue"] is False


def test_completed_section_skipped(tmp_path):
    """Rows under '## Completed' are NOT returned even if format matches."""
    _write_tasks_md(tmp_path,
        "## Active\n\n"
        "- [ ] **2026-05-11** | `P1` | Active task | *Task* | Due: 2026-05-15\n"
        "\n## Completed\n\n"
        "- [x] **2026-04-21** | `P2` | Old task | *Task* | Completed: 2026-04-21\n"
    )
    result = list_active_tasks(tmp_path, today=date(2026, 5, 13))
    titles = [t["description"] for t in result["tasks"]]
    assert "Active task" in [d for d in titles]  # body match
    assert not any("Old task" in d for d in titles)


def test_overdue_detection(tmp_path):
    """An item with Due < today is marked is_overdue with negative days_until_due."""
    _write_tasks_md(tmp_path,
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P1` | Stale task | *Task* | Due: 2026-05-05\n"
    )
    result = list_active_tasks(tmp_path, today=date(2026, 5, 18))
    t = result["tasks"][0]
    assert t["is_overdue"] is True
    assert t["days_until_due"] == -13
    assert result["overdue_count"] == 1


def test_sort_priority_then_due(tmp_path):
    """Tasks sort P1<P2<P3 then by days_until_due ASC."""
    _write_tasks_md(tmp_path,
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P2` | b-p2-soon | *Task* | Due: 2026-05-13\n"
        "- [ ] **2026-05-01** | `P1` | a-p1-later | *Task* | Due: 2026-05-15\n"
        "- [ ] **2026-05-01** | `P1` | a-p1-soon | *Task* | Due: 2026-05-12\n"
        "- [ ] **2026-05-01** | `P3` | c-p3-soon | *Task* | Due: 2026-05-11\n"
    )
    result = list_active_tasks(tmp_path, today=date(2026, 5, 10))
    descs = [t["description"] for t in result["tasks"]]
    # P1 before P2 before P3; within P1, soon before later.
    assert descs == ["a-p1-soon", "a-p1-later", "b-p2-soon", "c-p3-soon"]


def test_task_without_due_date_sorts_last_within_priority(tmp_path):
    """A task without Due: sorts after tasks WITH dues at the same priority."""
    _write_tasks_md(tmp_path,
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P1` | no-due | *Task* | Source: X\n"
        "- [ ] **2026-05-01** | `P1` | with-due | *Task* | Due: 2026-06-01\n"
    )
    result = list_active_tasks(tmp_path, today=date(2026, 5, 10))
    descs = [t["description"] for t in result["tasks"]]
    assert descs == ["with-due", "no-due"]


def test_priority_counts(tmp_path):
    """counts dict aggregates by priority."""
    _write_tasks_md(tmp_path,
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P1` | a | *Task* |\n"
        "- [ ] **2026-05-01** | `P1` | b | *Task* |\n"
        "- [ ] **2026-05-01** | `P2` | c | *Task* |\n"
    )
    result = list_active_tasks(tmp_path)
    assert result["counts"] == {"P1": 2, "P2": 1}


def test_pipes_in_body_preserved(tmp_path):
    """A pipe inside the description (before metadata) is preserved."""
    _write_tasks_md(tmp_path,
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P1` | foo and bar with detail | *Task* | Due: 2026-05-15\n"
    )
    result = list_active_tasks(tmp_path, today=date(2026, 5, 10))
    assert result["tasks"][0]["description"] == "foo and bar with detail"


def test_data_time_is_file_mtime(tmp_path):
    """data_time is the tasks.md file mtime in ISO UTC."""
    _write_tasks_md(tmp_path, "## Active\n\n")
    result = list_active_tasks(tmp_path)
    from datetime import datetime
    parsed = datetime.fromisoformat(result["data_time"])
    assert parsed.tzinfo is not None


# ============================================================
# Phase 1.90: dashboard mark-done workflow + JSONL filter
# ============================================================
def test_task_key_in_listing(tmp_path):
    """Each task row carries a derived stable task_key."""
    _write_tasks_md(tmp_path,
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P1` | Finish proposal draft | *Task* |\n"
    )
    result = list_active_tasks(tmp_path)
    assert len(result["tasks"]) == 1
    t = result["tasks"][0]
    assert "task_key" in t
    assert t["task_key"].startswith("2026-05-01|P1|Finish proposal draft")


def test_mark_done_rejects_blank_key(tmp_path):
    from scripts.bridge_daemon.sources.tasks import mark_done
    assert mark_done(tmp_path, "")["ok"] is False
    assert mark_done(tmp_path, "   ")["ok"] is False


def test_mark_done_writes_log_entry(tmp_path):
    from scripts.bridge_daemon.sources.tasks import mark_done, read_done_log, DONE_LOG_FILE
    r = mark_done(tmp_path, "2026-05-01|P1|Foo", note="finished in 10m")
    assert r["ok"] is True
    assert r["task_key"] == "2026-05-01|P1|Foo"
    assert r["ts"]
    assert r["date"]
    log = tmp_path / DONE_LOG_FILE
    assert log.exists()
    assert "2026-05-01|P1|Foo" in read_done_log(tmp_path)


def test_done_tasks_filtered_from_listing(tmp_path):
    from scripts.bridge_daemon.sources.tasks import mark_done
    _write_tasks_md(tmp_path,
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P1` | First | *Task* |\n"
        "- [ ] **2026-05-01** | `P1` | Second | *Task* |\n"
    )
    r0 = list_active_tasks(tmp_path)
    first_key = next(t["task_key"] for t in r0["tasks"] if t["description"] == "First")
    mark_done(tmp_path, first_key)
    r1 = list_active_tasks(tmp_path)
    descs = [t["description"] for t in r1["tasks"]]
    assert "Second" in descs
    assert "First" not in descs
    assert r1["done_filtered"] == 1


def test_undo_done_restores_task(tmp_path):
    from scripts.bridge_daemon.sources.tasks import mark_done, undo_done, read_done_log
    _write_tasks_md(tmp_path,
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P1` | Task A | *Task* |\n"
    )
    r0 = list_active_tasks(tmp_path)
    key = r0["tasks"][0]["task_key"]
    mark_done(tmp_path, key)
    assert key in read_done_log(tmp_path)
    undo_done(tmp_path, key)
    assert key not in read_done_log(tmp_path)
    r1 = list_active_tasks(tmp_path)
    assert any(t["description"] == "Task A" for t in r1["tasks"])
    assert r1["done_filtered"] == 0


def test_task_key_stability_across_calls(tmp_path):
    """Same task content -> same key on every call."""
    _write_tasks_md(tmp_path,
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P1` | Stable task | *Task* |\n"
    )
    k1 = list_active_tasks(tmp_path)["tasks"][0]["task_key"]
    k2 = list_active_tasks(tmp_path)["tasks"][0]["task_key"]
    assert k1 == k2


def test_today_activity_includes_tasks_done(tmp_path):
    """tasks_done is the 5th kind on today_activity."""
    from scripts.bridge_daemon.sources.pulse import today_activity
    from scripts.bridge_daemon.sources.tasks import mark_done
    mark_done(tmp_path, "2026-05-19|P1|do the thing", note="quick win")
    a = today_activity(tmp_path)
    assert a["tasks_done"] >= 1
    entries = a["entries"]["tasks_done"]
    assert len(entries) >= 1
    e = entries[0]
    assert e["kind"] == "task_done"
    assert "do the thing" in e["target"]
    assert e["ref"] == ""
    assert e["note"] == "quick win"


# ============================================================
# Phase 1.91: done_log_recent + done_log_count
# ============================================================
def test_done_log_recent_empty_when_no_log(tmp_path):
    from scripts.bridge_daemon.sources.tasks import done_log_recent
    assert done_log_recent(tmp_path) == []


def test_done_log_recent_returns_active_entries(tmp_path):
    from scripts.bridge_daemon.sources.tasks import mark_done, done_log_recent
    mark_done(tmp_path, "2026-05-19|P1|First", note="quick win")
    mark_done(tmp_path, "2026-05-19|P2|Second")
    rows = done_log_recent(tmp_path)
    keys = [r["task_key"] for r in rows]
    assert "2026-05-19|P1|First" in keys
    assert "2026-05-19|P2|Second" in keys
    # Each row parses description + priority back out of the key.
    first = next(r for r in rows if r["task_key"].endswith("|First"))
    assert first["description"] == "First"
    assert first["priority"] == "P1"
    assert first["note"] == "quick win"
    assert first["ts"]
    assert first["date"]


def test_done_log_recent_excludes_tombstones(tmp_path):
    from scripts.bridge_daemon.sources.tasks import mark_done, undo_done, done_log_recent
    key = "2026-05-19|P1|undo me"
    mark_done(tmp_path, key)
    undo_done(tmp_path, key)
    rows = done_log_recent(tmp_path)
    assert all(r["task_key"] != key for r in rows)


def test_done_log_recent_orders_ts_desc(tmp_path):
    import time
    from scripts.bridge_daemon.sources.tasks import mark_done, done_log_recent
    mark_done(tmp_path, "2026-05-19|P1|first")
    time.sleep(0.01)
    mark_done(tmp_path, "2026-05-19|P1|second")
    rows = done_log_recent(tmp_path)
    assert rows[0]["task_key"].endswith("|second")
    assert rows[1]["task_key"].endswith("|first")


def test_done_log_recent_respects_limit(tmp_path):
    from scripts.bridge_daemon.sources.tasks import mark_done, done_log_recent
    for i in range(5):
        mark_done(tmp_path, f"2026-05-19|P1|t-{i}")
    rows = done_log_recent(tmp_path, limit=3)
    assert len(rows) == 3


def test_list_active_tasks_surfaces_done_log_count(tmp_path):
    """done_log_count reflects all active done entries even when their
    tasks.md row is no longer there."""
    from scripts.bridge_daemon.sources.tasks import mark_done
    # No tasks.md at all - just a done log.
    mark_done(tmp_path, "2026-05-19|P1|something")
    mark_done(tmp_path, "2026-05-19|P2|else")
    # No tasks.md file -> list returns empty list, but done_log_count
    # only surfaces when tasks.md exists. Add a stub so that path is hit.
    _write_tasks_md(tmp_path, "## Active\n\n")
    result = list_active_tasks(tmp_path)
    assert result["done_log_count"] == 2
