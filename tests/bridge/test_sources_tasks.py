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
    """Rows under '## Completed' are NOT returned even if the format matches.

    The fixture used to carry only a `- [x]` row there, which is not the format
    `_ACTIVE_RE` matches at all: its checkbox is `\\[\\s*\\]`, empty brackets, so
    the row was excluded by the checkbox and never by the section. The
    assertion could not tell the two rules apart, and the docstring's "even if
    format matches" was the part nothing measured. MEASURED 2026-08-31 by
    deleting `if not in_active: continue` from `list_active_tasks` and running
    the whole `tests/bridge` directory: 1349 passed, 1 skipped.

    An UNCHECKED row now sits under `## Completed` too. That is a real shape:
    the /viraid skill moves a row down and the CEO ticks the box afterwards,
    and in that window the section is the only thing keeping it off /tasks.
    """
    _write_tasks_md(tmp_path,
        "## Active\n\n"
        "- [ ] **2026-05-11** | `P1` | Active task | *Task* | Due: 2026-05-15\n"
        "\n## Completed\n\n"
        "- [x] **2026-04-21** | `P2` | Old task | *Task* | Completed: 2026-04-21\n"
        "- [ ] **2026-04-22** | `P2` | Moved but unticked | *Task* | Completed: 2026-04-22\n"
    )
    result = list_active_tasks(tmp_path, today=date(2026, 5, 13))
    titles = [t["description"] for t in result["tasks"]]
    assert "Active task" in [d for d in titles]  # body match
    assert not any("Old task" in d for d in titles)
    assert not any("Moved but unticked" in d for d in titles), titles


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
    """A pipe inside the description (before metadata) is preserved.

    Fixed 2026-08-30: the fixture contained no pipe. The description read
    `foo and bar with detail`, so the assertion passed against a parser that
    split naively on every `|` and truncated the body at the first one — which
    is precisely the regression this test is named to catch. There is now a real
    `|` between `foo` and `bar with detail`, and `_strip_metadata_suffix` has to
    rejoin the two body segments to satisfy it.
    """
    _write_tasks_md(tmp_path,
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P1` | foo | bar with detail | *Task* | Due: 2026-05-15\n"
    )
    result = list_active_tasks(tmp_path, today=date(2026, 5, 10))
    assert result["tasks"][0]["description"] == "foo | bar with detail"


def test_metadata_after_a_piped_body_is_still_stripped(tmp_path):
    """The other half: preserving body pipes must not swallow the metadata.

    A parser that simply stopped splitting would keep `*Task*` and `Due:` in
    the description and pass the test above.
    """
    _write_tasks_md(tmp_path,
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P1` | alpha | beta | *Task* | Due: 2026-05-15\n"
    )
    task = list_active_tasks(tmp_path, today=date(2026, 5, 10))["tasks"][0]
    assert task["description"] == "alpha | beta"
    assert task["kind"] == "Task"
    assert task["due"] == "2026-05-15"


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


def test_done_log_recent_orders_ts_desc(tmp_path, monkeypatch):
    """Newest mark-done first.

    The clock is driven, not slept through. This read `time.sleep(0.01)` and
    hoped the wall clock had moved between the two writes; a WSL2 host resync
    can step it BACKWARDS and invert them, and the sleep measures nothing when
    it does not. Same cause and same cure as
    `test_dismiss_log_recent_orders_ts_desc` in `test_sources_inbox.py`, which
    was converted for exactly this reason: state the two instants instead of
    hoping for them.
    """
    from datetime import datetime, timedelta, timezone
    from scripts.bridge_daemon.sources import tasks as tasks_mod
    from scripts.bridge_daemon.sources.tasks import done_log_recent, mark_done

    # A holder, not an iterator: `mark_done` reads the clock twice per call
    # (UTC for `ts`, local for `date`), so a per-call sequence would
    # desynchronise the moment a field is added or removed.
    moment = [datetime(2026, 8, 9, 12, 0, 0, tzinfo=timezone.utc)]

    class _HeldClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment[0] if tz in (None, timezone.utc) else moment[0].astimezone(tz)

    monkeypatch.setattr(tasks_mod, "datetime", _HeldClock)

    mark_done(tmp_path, "2026-05-19|P1|first")
    moment[0] += timedelta(seconds=1)
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


# ============================================================
# Three guards on the done-log that nothing ever made refuse
# ============================================================
# The done log is append-only JSONL on disk. Every test above writes it through
# `mark_done`, so the reader's shape guards and the writer's bounds had no
# negative case at all. MEASURED 2026-08-31 by deleting each guard in turn and
# running the whole `tests/bridge` directory: 1349 passed, 1 skipped, every
# time.

def test_a_done_log_line_with_no_usable_key_is_skipped(tmp_path):
    """A hand-edited or truncated line must not enter the done-key SET.

    A key of `null` or `7` in that set is compared against every real task key
    on every /tasks render; an empty one matches nothing and just inflates
    `done_log_count`, which the 'Recently done' footer keys its visibility on.
    """
    import json
    from scripts.bridge_daemon.sources.tasks import (
        DONE_LOG_FILE, done_log_recent, read_done_log,
    )
    log = tmp_path / DONE_LOG_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"task_key": "2026-05-19|P1|Real", "ts": "2026-08-31T10:00:00+00:00"}) + "\n"
        + json.dumps({"task_key": None, "ts": "x"}) + "\n"
        + json.dumps({"task_key": 7, "ts": "x"}) + "\n"
        + json.dumps({"task_key": "", "ts": "x"}) + "\n"
        + json.dumps({"note": "no key at all"}) + "\n",
        encoding="utf-8")

    assert read_done_log(tmp_path) == {"2026-05-19|P1|Real"}
    assert [r["task_key"] for r in done_log_recent(tmp_path)] == ["2026-05-19|P1|Real"]


def test_undo_done_rejects_what_mark_done_rejects(tmp_path):
    """The two halves of one workflow have to agree on what a key is.

    `mark_done` has had `test_mark_done_rejects_blank_key` since it was
    written; `undo_done` never had the matching case, so a blank key would be
    appended as a tombstone that can tombstone nothing.
    """
    from scripts.bridge_daemon.sources.tasks import DONE_LOG_FILE, undo_done
    assert undo_done(tmp_path, "")["ok"] is False
    assert undo_done(tmp_path, "   ")["ok"] is False
    assert undo_done(tmp_path, None)["ok"] is False  # type: ignore[arg-type]
    assert not (tmp_path / DONE_LOG_FILE).exists(), "a refused undo must write nothing"


def test_mark_done_rejects_an_oversized_key(tmp_path):
    """The 500-character bound, with a case ON the line as well as over it."""
    from scripts.bridge_daemon.sources.tasks import mark_done
    assert mark_done(tmp_path, "k" * 501)["ok"] is False
    assert mark_done(tmp_path, "k" * 500)["ok"] is True


def test_emphasis_inside_the_body_is_not_the_metadata_boundary(tmp_path):
    """Half a task description used to be cut off by an italic word.

    `_strip_metadata_suffix` decided "metadata starts here" with
    `part.startswith("*")`, which fires on a body segment that merely OPENS
    with emphasis. MEASURED 2026-08-31 on the row below: the description came
    back as "Call Zeek", so the half of the sentence saying what to do was gone
    from the /tasks card, from unified search (which matches on `description`)
    and from the `task_key` the mark-done workflow is built on, with nothing
    logged.

    The two body-pipe tests above could not see it: their extra segments are
    `bar with detail` and `beta`, plain text that trips no metadata rule at
    all, so they measured the rejoining and never the boundary test that
    decides where rejoining stops.
    """
    _write_tasks_md(tmp_path,
        "## Active\n\n"
        "- [ ] **2026-05-01** | `P1` | Call Zeek | *urgently* about the SOW "
        "| *Task* | Source: Telegram | Due: 2026-05-15\n")

    t = list_active_tasks(tmp_path, today=date(2026, 5, 10))["tasks"][0]

    assert t["description"] == "Call Zeek | *urgently* about the SOW"
    # And the metadata is still stripped and still parsed: a boundary loosened
    # far enough to keep the emphasis would otherwise swallow `*Task*` too.
    assert t["kind"] == "Task"
    assert t["source"] == "Telegram"
    assert t["due"] == "2026-05-15"
