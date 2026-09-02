"""Unit tests for /threads source (Phase 1.76)."""
from datetime import datetime, timedelta
from scripts.utils.workspace import get_default_tz
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
    today = datetime.now(get_default_tz()).date().isoformat()
    _write_thread(tmp_path, "now", last_touched=today)
    r = list_active_threads(tmp_path)
    assert r["threads"][0]["bucket"] == "today"
    assert r["counts"]["today"] == 1


def test_bucket_this_week_for_recent(tmp_path):
    recent = (datetime.now(get_default_tz()).date() - timedelta(days=3)).isoformat()
    _write_thread(tmp_path, "recent", last_touched=recent)
    r = list_active_threads(tmp_path)
    assert r["threads"][0]["bucket"] == "this_week"


def test_bucket_older_for_eight_plus_days(tmp_path):
    old = (datetime.now(get_default_tz()).date() - timedelta(days=30)).isoformat()
    _write_thread(tmp_path, "old", last_touched=old)
    r = list_active_threads(tmp_path)
    assert r["threads"][0]["bucket"] == "older"


def test_bucket_order_omits_empty_buckets(tmp_path):
    """bucket_order only lists buckets that actually have threads."""
    today = datetime.now(get_default_tz()).date().isoformat()
    _write_thread(tmp_path, "now", last_touched=today)
    r = list_active_threads(tmp_path)
    assert r["bucket_order"] == ["today"]
    assert "this_week" not in r["bucket_order"]


def test_bucket_order_follows_canonical_order(tmp_path):
    """When all three buckets exist, order is today -> this_week -> older."""
    today = datetime.now(get_default_tz()).date()
    _write_thread(tmp_path, "now", last_touched=today.isoformat())
    _write_thread(tmp_path, "recent", last_touched=(today - timedelta(days=3)).isoformat())
    _write_thread(tmp_path, "old", last_touched=(today - timedelta(days=30)).isoformat())
    r = list_active_threads(tmp_path)
    assert r["bucket_order"] == ["today", "this_week", "older"]


def test_sort_by_days_since_asc(tmp_path):
    """Most recently touched threads sort first."""
    today = datetime.now(get_default_tz()).date()
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


def test_the_listing_applies_the_byte_cap_read_thread_applies(tmp_path):
    """One cap, two readers, and only one of them was applying it.

    Found by the 2026-08-24 campaign (shard `scripts-02-p2`, finding 4). The
    comment on `THREAD_MAX_BYTES` says it is a "200 KB upper bound on any
    thread body read", and `read_thread` refuses over it. The listing read
    every body in full, so a 50 MB `.md` in `threads/business/` was pulled into
    memory on every poll of `/threads` while the detail view refused to open
    it. The comment described the intended invariant, so the code was the wrong
    half.
    """
    from scripts.bridge_daemon.sources.threads import THREAD_MAX_BYTES
    _write_thread(tmp_path, "small", title="Small", last_touched="2026-05-18")
    _write_thread(tmp_path, "huge", title="Huge", last_touched="2026-05-18",
                  body="\n" + "x" * (THREAD_MAX_BYTES + 1) + "\n")
    r = list_active_threads(tmp_path)
    paths = [t["path"] for t in r["threads"]]
    assert "threads/business/small.md" in paths, (
        "the honest thread stopped being listed, so the exclusion below proves "
        f"nothing: {paths}")
    assert "threads/business/huge.md" not in paths, (
        f"a body over the {THREAD_MAX_BYTES}-byte cap was read whole by the "
        f"listing that read_thread refuses to open: {paths}")
    assert r["total"] == 1, (
        f"an excluded thread must not be counted either: {r['total']}")


def test_a_thread_exactly_at_the_cap_is_still_listed(tmp_path):
    """The boundary, so the cap cannot be tightened into refusing everything.

    `read_thread` refuses `size > THREAD_MAX_BYTES`, so the last accepted size
    IS the cap. The listing has to draw the line in the same place or the two
    readers disagree again, one file further along.
    """
    from scripts.bridge_daemon.sources.threads import THREAD_MAX_BYTES
    p = _write_thread(tmp_path, "edge", title="Edge", last_touched="2026-05-18")
    pad = THREAD_MAX_BYTES - len(p.read_bytes())
    assert pad > 0, "the frontmatter alone already exceeds the cap"
    p.write_bytes(p.read_bytes() + b"x" * pad)
    assert p.stat().st_size == THREAD_MAX_BYTES
    assert read_thread(tmp_path, "threads/business/edge.md")["ok"] is True
    paths = [t["path"] for t in list_active_threads(tmp_path)["threads"]]
    assert "threads/business/edge.md" in paths, paths


def test_the_returns_docstring_names_every_key_the_function_emits(tmp_path):
    """A Returns block that opens and closes the dict reads as exhaustive.

    Found by the 2026-08-24 campaign (shard `scripts-02-p2`, finding 6):
    `truncated` and `row_cap` were emitted by both return statements and
    documented by neither, so a consumer of the truncation behaviour had to
    read the code to learn the keys were there. Asserted against the LIVE keys
    rather than a hand-written list, so a key added later fails here instead of
    slipping in undocumented.

    Both return paths are checked: the early return for a missing directory
    once carried a different shape from the parsed one, which is the drift the
    early return's own comment records being fixed.
    """
    doc = list_active_threads.__doc__ or ""
    _write_thread(tmp_path, "a", title="A", last_touched="2026-05-18")
    for root, which in ((tmp_path, "the parsed payload"),
                        (tmp_path / "no-such-root", "the early return")):
        for key in list_active_threads(root):
            assert f'"{key}"' in doc, (
                f"{which} emits {key!r} and the Returns block does not "
                f"document it")


def test_bucket_label_exposed_for_ui(tmp_path):
    """The bucket-label dict is importable so the frontend can mirror it."""
    assert THREADS_BUCKET_LABEL["today"] == "Today"
    assert THREADS_BUCKET_LABEL["this_week"] == "This week"
    assert THREADS_BUCKET_LABEL["older"] == "Older"


def test_the_week_bucket_boundary_has_a_case_on_the_line(tmp_path):
    """Seven days is "this week"; eight is "older".

    The bucket tests above use 0, 3 and 30 days, so nothing sat on the bound
    `days_since <= 7` and nothing distinguished it from `<= 6` or `<= 8`. The
    boundary is the whole content of `_recency_bucket`: everything else in it
    is the None case and the equality on zero.
    """
    from datetime import date as _date
    from scripts.bridge_daemon.sources.threads import _recency_bucket

    assert _recency_bucket(7) == "this_week"
    assert _recency_bucket(8) == "older"

    # And through the real walk, so the bucket the page renders is the one the
    # helper returns. `list_active_threads` dates against the host clock (it
    # takes no `today`), so the fixtures are written relative to it.
    today = datetime.now(get_default_tz()).date()
    _write_thread(tmp_path, "seven", title="Seven",
                  last_touched=(today - timedelta(days=7)).isoformat())
    _write_thread(tmp_path, "eight", title="Eight",
                  last_touched=(today - timedelta(days=8)).isoformat())

    buckets = {t["title"]: t["bucket"] for t in list_active_threads(tmp_path)["threads"]}
    assert buckets == {"Seven": "this_week", "Eight": "older"}


def test_a_thread_with_no_date_is_older_not_a_crash(tmp_path):
    """`days_since` is None when `last_touched` and `opened` are both absent or
    unparseable, and None must bucket rather than raise."""
    p = tmp_path / "threads" / "business" / "undated.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\nid: undated\ntitle: Undated\nstatus: active\n"
                 "last_touched: 'not-a-date'\n---\n\nbody\n", encoding="utf-8")

    got = list_active_threads(tmp_path)

    assert got["total"] == 1
    assert got["threads"][0]["days_since"] is None
    assert got["threads"][0]["bucket"] == "older"
