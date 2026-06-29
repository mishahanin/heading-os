"""Unit tests for /threads source (Phase 1.76)."""
from datetime import date, timedelta
from pathlib import Path

from scripts.bridge_daemon.sources.threads import (
    THREADS_BUCKET_LABEL,
    list_active_threads,
    read_thread,
)


def _write_thread(workspace_root, slug, title="Thread", status="active",
                  type_="business", last_touched=None, opened="2026-05-01", body="\nbody\n"):
    p = workspace_root / "threads" / "business" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    fm_lines = [
        f"id: {slug}",
        f"title: {title}",
        f"status: {status}",
        f"type: {type_}",
        f"opened: '{opened}'",
    ]
    if last_touched:
        fm_lines.append(f"last_touched: '{last_touched}'")
    fm = "\n".join(fm_lines)
    p.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
    return p


def test_empty_when_no_threads_dir(tmp_path):
    r = list_active_threads(tmp_path)
    assert r["threads"] == []
    assert r["total"] == 0
    assert r["bucket_order"] == []
    assert r["data_time"] is None


def test_parses_active_thread(tmp_path):
    _write_thread(tmp_path, "a", title="Thread A", last_touched="2026-05-18")
    r = list_active_threads(tmp_path)
    assert r["total"] == 1
    t = r["threads"][0]
    assert t["title"] == "Thread A"
    assert t["status"] == "active"
    assert t["path"] == "threads/business/a.md"


def test_skips_closed_threads(tmp_path):
    _write_thread(tmp_path, "live", status="active")
    _write_thread(tmp_path, "done", status="closed")
    _write_thread(tmp_path, "held", status="held")
    r = list_active_threads(tmp_path)
    paths = [t["path"] for t in r["threads"]]
    assert "threads/business/live.md" in paths
    assert "threads/business/done.md" not in paths
    assert "threads/business/held.md" not in paths


def test_bucket_today_for_zero_days(tmp_path):
    today = date.today().isoformat()
    _write_thread(tmp_path, "now", last_touched=today)
    r = list_active_threads(tmp_path)
    assert r["threads"][0]["bucket"] == "today"
    assert r["counts"]["today"] == 1


def test_bucket_this_week_for_recent(tmp_path):
    recent = (date.today() - timedelta(days=3)).isoformat()
    _write_thread(tmp_path, "recent", last_touched=recent)
    r = list_active_threads(tmp_path)
    assert r["threads"][0]["bucket"] == "this_week"


def test_bucket_older_for_eight_plus_days(tmp_path):
    old = (date.today() - timedelta(days=30)).isoformat()
    _write_thread(tmp_path, "old", last_touched=old)
    r = list_active_threads(tmp_path)
    assert r["threads"][0]["bucket"] == "older"


def test_bucket_order_omits_empty_buckets(tmp_path):
    """bucket_order only lists buckets that actually have threads."""
    today = date.today().isoformat()
    _write_thread(tmp_path, "now", last_touched=today)
    r = list_active_threads(tmp_path)
    assert r["bucket_order"] == ["today"]
    assert "this_week" not in r["bucket_order"]


def test_bucket_order_follows_canonical_order(tmp_path):
    """When all three buckets exist, order is today -> this_week -> older."""
    today = date.today()
    _write_thread(tmp_path, "now", last_touched=today.isoformat())
    _write_thread(tmp_path, "recent", last_touched=(today - timedelta(days=3)).isoformat())
    _write_thread(tmp_path, "old", last_touched=(today - timedelta(days=30)).isoformat())
    r = list_active_threads(tmp_path)
    assert r["bucket_order"] == ["today", "this_week", "older"]


def test_sort_by_days_since_asc(tmp_path):
    """Most recently touched threads sort first."""
    today = date.today()
    _write_thread(tmp_path, "old", title="Old", last_touched=(today - timedelta(days=30)).isoformat())
    _write_thread(tmp_path, "now", title="Now", last_touched=today.isoformat())
    _write_thread(tmp_path, "recent", title="Recent", last_touched=(today - timedelta(days=3)).isoformat())
    r = list_active_threads(tmp_path)
    titles = [t["title"] for t in r["threads"]]
    assert titles == ["Now", "Recent", "Old"]


def test_read_thread_happy_path(tmp_path):
    _write_thread(tmp_path, "a", title="Hello", body="\n# Hello\n\nBody text here.\n")
    r = read_thread(tmp_path, "threads/business/a.md")
    assert r["ok"] is True
    assert "Body text here" in r["content"]
    assert r["path"] == "threads/business/a.md"


def test_read_thread_blocks_traversal(tmp_path):
    _write_thread(tmp_path, "a")
    assert read_thread(tmp_path, "")["ok"] is False
    assert read_thread(tmp_path, "../etc/passwd")["ok"] is False
    assert read_thread(tmp_path, "threads/business/../../etc/passwd")["ok"] is False


def test_read_thread_rejects_outside_threads_dir(tmp_path):
    _write_thread(tmp_path, "a")
    r = read_thread(tmp_path, "outputs/secret.md")
    assert r["ok"] is False
    assert "threads" in r["error"].lower()


def test_read_thread_rejects_non_md(tmp_path):
    target = tmp_path / "threads" / "business" / "evil.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("nope", encoding="utf-8")
    r = read_thread(tmp_path, "threads/business/evil.exe")
    assert r["ok"] is False
    assert ".md" in r["error"].lower()


def test_read_thread_missing_returns_not_found(tmp_path):
    (tmp_path / "threads" / "business").mkdir(parents=True)
    r = read_thread(tmp_path, "threads/business/missing.md")
    assert r["ok"] is False
    assert "not found" in r["error"].lower()


def test_read_thread_size_cap(tmp_path):
    from scripts.bridge_daemon.sources.threads import THREAD_MAX_BYTES
    p = tmp_path / "threads" / "business" / "huge.md"
    p.parent.mkdir(parents=True)
    p.write_text("x" * (THREAD_MAX_BYTES + 1), encoding="utf-8")
    r = read_thread(tmp_path, "threads/business/huge.md")
    assert r["ok"] is False
    assert "too large" in r["error"].lower()


def test_bucket_label_exposed_for_ui(tmp_path):
    """The bucket-label dict is importable so the frontend can mirror it."""
    assert THREADS_BUCKET_LABEL["today"] == "Today"
    assert THREADS_BUCKET_LABEL["this_week"] == "This week"
    assert THREADS_BUCKET_LABEL["older"] == "Older"
