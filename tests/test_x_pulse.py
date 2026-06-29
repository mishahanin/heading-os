"""Tests for /x-pulse skill (.claude/skills/x-pulse/scripts/pulse.py)."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
PULSE_PATH = WORKSPACE_ROOT / ".claude" / "skills" / "x-pulse" / "scripts" / "pulse.py"
spec = importlib.util.spec_from_file_location("x_pulse_module", PULSE_PATH)
xpulse = importlib.util.module_from_spec(spec)
spec.loader.exec_module(xpulse)


# ---------- engagement_score ----------

def test_engagement_score_basic():
    post = {"engagement": {"likes": 100, "retweets": 10, "replies": 5}}
    # 100 + 2*10 + 3*5 = 135
    assert xpulse.engagement_score(post) == 135


def test_engagement_score_zero():
    post = {"engagement": {"likes": 0, "retweets": 0, "replies": 0}}
    assert xpulse.engagement_score(post) == 0


def test_engagement_score_missing_keys_treated_as_zero():
    post = {"engagement": {"likes": 50}}
    assert xpulse.engagement_score(post) == 50


# ---------- in_window ----------

def test_in_window_recent_post_passes():
    since = datetime(2026, 5, 8, 0, 0, 0, tzinfo=timezone.utc)
    post = {"timestamp": "2026-05-10T14:00:00Z"}
    assert xpulse.in_window(post, since) is True


def test_in_window_old_post_dropped():
    since = datetime(2026, 5, 8, 0, 0, 0, tzinfo=timezone.utc)
    post = {"timestamp": "2026-05-01T14:00:00Z"}
    assert xpulse.in_window(post, since) is False


def test_in_window_exact_boundary_passes():
    since = datetime(2026, 5, 8, 0, 0, 0, tzinfo=timezone.utc)
    post = {"timestamp": "2026-05-08T00:00:00Z"}
    assert xpulse.in_window(post, since) is True


def test_in_window_missing_timestamp_returns_false():
    since = datetime(2026, 5, 8, 0, 0, 0, tzinfo=timezone.utc)
    assert xpulse.in_window({}, since) is False


def test_in_window_empty_timestamp_returns_false():
    since = datetime(2026, 5, 8, 0, 0, 0, tzinfo=timezone.utc)
    assert xpulse.in_window({"timestamp": ""}, since) is False


def test_in_window_malformed_timestamp_returns_false():
    since = datetime(2026, 5, 8, 0, 0, 0, tzinfo=timezone.utc)
    assert xpulse.in_window({"timestamp": "not-a-date"}, since) is False


# ---------- load_accounts_yaml ----------

FIXTURES = WORKSPACE_ROOT / ".claude" / "skills" / "x-pulse" / "tests" / "fixtures"


def test_load_accounts_yaml_well_formed():
    cats = xpulse.load_accounts_yaml(FIXTURES / "sample-accounts.yaml")
    assert "peer_ceos" in cats
    assert cats["peer_ceos"]["handles"] == ["techfounder", "founder_two"]
    assert cats["dpi_competitors"]["handles"] == ["acmenetworks"]


def test_load_accounts_yaml_drops_empty_buckets():
    cats = xpulse.load_accounts_yaml(FIXTURES / "sample-accounts.yaml")
    assert "empty_bucket" not in cats


def test_load_accounts_yaml_missing_file(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        xpulse.load_accounts_yaml(tmp_path / "nonexistent.yaml")
    assert exc_info.value.code == 1


def test_load_accounts_yaml_malformed(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("not:\n  valid:\n    - yaml: [unclosed\n")
    with pytest.raises(SystemExit) as exc_info:
        xpulse.load_accounts_yaml(bad)
    assert exc_info.value.code == 1


def test_load_accounts_yaml_missing_categories_key(tmp_path):
    bad = tmp_path / "no-categories.yaml"
    bad.write_text("foo: bar\n")
    with pytest.raises(SystemExit) as exc_info:
        xpulse.load_accounts_yaml(bad)
    assert exc_info.value.code == 1


def test_load_accounts_yaml_null_categories(tmp_path):
    """categories: null is treated as empty (returns {} cleanly)."""
    f = tmp_path / "null-cats.yaml"
    f.write_text("categories:\n")
    cats = xpulse.load_accounts_yaml(f)
    assert cats == {}


def test_load_accounts_yaml_categories_not_dict(tmp_path):
    """categories: must be a mapping; list/string is rejected with SystemExit."""
    f = tmp_path / "list-cats.yaml"
    f.write_text("categories:\n  - foo\n  - bar\n")
    with pytest.raises(SystemExit) as exc_info:
        xpulse.load_accounts_yaml(f)
    assert exc_info.value.code == 1


# ---------- collapse_thread ----------

def _post(text, t_id, thread_id=None, position=0, likes=0, rts=0, replies=0, quotes=0, views=0):
    return {
        "handle": "user",
        "category": "cat",
        "tweet_id": t_id,
        "text": text,
        "timestamp": "2026-05-10T12:00:00Z",
        "is_thread": thread_id is not None,
        "thread_id": thread_id,
        "thread_position": position,
        "engagement": {"likes": likes, "retweets": rts, "replies": replies, "quotes": quotes, "views": views},
    }


def test_collapse_thread_merges_three_tweets():
    posts = [
        _post("First", "1", thread_id="T1", position=0, likes=100),
        _post("Second", "2", thread_id="T1", position=1, likes=50),
        _post("Third", "3", thread_id="T1", position=2, likes=25),
    ]
    merged = xpulse.collapse_thread(posts)
    assert merged["text"] == "First"
    assert "thread_text" in merged
    assert "First" in merged["thread_text"]
    assert "Second" in merged["thread_text"]
    assert "Third" in merged["thread_text"]
    assert merged["engagement"]["likes"] == 175


def test_collapse_thread_single_tweet_passthrough():
    posts = [_post("Only", "1", likes=100)]
    merged = xpulse.collapse_thread(posts)
    assert merged["text"] == "Only"
    assert merged["engagement"]["likes"] == 100


def test_collapse_thread_sorts_by_position():
    # Out-of-order input should be sorted by thread_position
    posts = [
        _post("Third", "3", thread_id="T1", position=2),
        _post("First", "1", thread_id="T1", position=0),
        _post("Second", "2", thread_id="T1", position=1),
    ]
    merged = xpulse.collapse_thread(posts)
    assert merged["text"] == "First"
    assert merged["thread_text"].index("First") < merged["thread_text"].index("Second")
    assert merged["thread_text"].index("Second") < merged["thread_text"].index("Third")


def test_collapse_thread_missing_engagement_key():
    """A post with no engagement key at all should be treated as all-zero, not crash."""
    posts = [
        _post("Has eng", "1", likes=100),
        {"tweet_id": "2", "text": "No eng key", "thread_position": 1},  # no engagement at all
    ]
    merged = xpulse.collapse_thread(posts)
    assert merged["engagement"]["likes"] == 100  # second post contributed 0


# ---------- filter_per_category ----------

def _scored_post(category, score):
    likes = score  # such that engagement_score == score
    return {
        "category": category,
        "engagement": {"likes": likes, "retweets": 0, "replies": 0},
        "tweet_id": f"{category}-{score}",
    }


def test_filter_per_category_drops_bottom_half():
    posts = [
        _scored_post("a", 10),
        _scored_post("a", 20),
        _scored_post("a", 30),
        _scored_post("a", 40),
    ]
    survivors = xpulse.filter_per_category(posts)
    # Bottom 50% of 4 = 2 dropped, top 2 kept
    assert len(survivors) == 2
    scores = sorted(xpulse.engagement_score(p) for p in survivors)
    assert scores == [30, 40]


def test_filter_per_category_per_category_separation():
    posts = [
        _scored_post("a", 10), _scored_post("a", 20),
        _scored_post("a", 30), _scored_post("a", 40),
        _scored_post("b", 1), _scored_post("b", 2),
        _scored_post("b", 3), _scored_post("b", 4),
    ]
    survivors = xpulse.filter_per_category(posts)
    # Each category cut to its top 50% independently
    a_survivors = [p for p in survivors if p["category"] == "a"]
    b_survivors = [p for p in survivors if p["category"] == "b"]
    assert len(a_survivors) == 2
    assert len(b_survivors) == 2
    # b's top score (4) survives even though it's lower than a's worst kept (30)
    assert any(xpulse.engagement_score(p) == 4 for p in b_survivors)


def test_filter_per_category_round_up_for_odd_count():
    # 5 posts -> bottom 50% = 2 (floor), so 3 survive
    posts = [_scored_post("a", i * 10) for i in range(1, 6)]
    survivors = xpulse.filter_per_category(posts)
    assert len(survivors) == 3


def test_filter_per_category_single_post_keeps_it():
    posts = [_scored_post("a", 10)]
    survivors = xpulse.filter_per_category(posts)
    assert len(survivors) == 1


def test_filter_per_category_empty_input():
    assert xpulse.filter_per_category([]) == []


def test_filter_per_category_n_two_keeps_one():
    """Boundary case: N=2 drops 1, keeps 1 (the higher-scoring post)."""
    posts = [_scored_post("a", 10), _scored_post("a", 20)]
    survivors = xpulse.filter_per_category(posts)
    assert len(survivors) == 1
    assert xpulse.engagement_score(survivors[0]) == 20


# ---------- normalize_apify_post ----------

def test_normalize_apify_post_basic_fields():
    apify = {
        "id": "1789012345678",
        "url": "https://twitter.com/techfounder/status/1789012345678",
        "createdAt": "Sat May 10 14:23:00 +0000 2026",
        "text": "Hello",
        "isReply": False,
        "conversationId": "1789012345678",
        "likeCount": 100,
        "retweetCount": 10,
        "replyCount": 5,
        "quoteCount": 1,
        "viewCount": 1000,
    }
    out = xpulse.normalize_apify_post(apify, handle="techfounder", category="peer_ceos")
    assert out["handle"] == "techfounder"
    assert out["category"] == "peer_ceos"
    assert out["tweet_id"] == "1789012345678"
    assert out["text"] == "Hello"
    assert out["timestamp"] == "2026-05-10T14:23:00+00:00"
    assert out["engagement"]["likes"] == 100
    assert out["engagement"]["retweets"] == 10
    assert out["engagement"]["replies"] == 5


def test_normalize_apify_post_thread_detection():
    # When tweet_id != conversationId AND isReply, it's a thread continuation
    apify = {
        "id": "1789012345679",
        "url": "...",
        "createdAt": "Sat May 10 14:25:00 +0000 2026",
        "text": "...",
        "isReply": True,
        "conversationId": "1789012345678",
        "likeCount": 0, "retweetCount": 0, "replyCount": 0,
        "quoteCount": 0, "viewCount": 0,
    }
    out = xpulse.normalize_apify_post(apify, handle="techfounder", category="peer_ceos")
    assert out["is_thread"] is True
    assert out["thread_id"] == "1789012345678"


def test_normalize_apify_post_standalone_not_thread():
    apify = {
        "id": "1789012345680",
        "url": "...",
        "createdAt": "Fri May 09 10:00:00 +0000 2026",
        "text": "...",
        "isReply": False,
        "conversationId": "1789012345680",
        "likeCount": 0, "retweetCount": 0, "replyCount": 0,
        "quoteCount": 0, "viewCount": 0,
    }
    out = xpulse.normalize_apify_post(apify, handle="techfounder", category="peer_ceos")
    assert out["is_thread"] is False


def test_normalize_apify_post_media_reply_quote_fields():
    """media_urls, reply_to, is_quote_of pass-through coverage."""
    apify = {
        "id": "1", "url": "u", "createdAt": "Sat May 10 14:23:00 +0000 2026",
        "text": "t", "isReply": False, "conversationId": "1",
        "likeCount": 0, "retweetCount": 0, "replyCount": 0,
        "quoteCount": 0, "viewCount": 0,
        "media": [{"url": "https://pbs.twimg.com/media/abc.jpg"}, {"url": "https://example.com/x.png"}],
        "inReplyToId": "999",
        "quotedTweetId": "888",
    }
    out = xpulse.normalize_apify_post(apify, handle="techfounder", category="peer_ceos")
    assert out["media_urls"] == ["https://pbs.twimg.com/media/abc.jpg", "https://example.com/x.png"]
    assert out["reply_to"] == "999"
    assert out["is_quote_of"] == "888"


def test_normalize_apify_post_empty_createdAt_returns_empty_timestamp():
    """Missing createdAt -> empty timestamp (in_window then drops the post)."""
    apify = {
        "id": "1", "url": "u", "createdAt": "",
        "text": "t", "isReply": False, "conversationId": "1",
        "likeCount": 0, "retweetCount": 0, "replyCount": 0,
        "quoteCount": 0, "viewCount": 0,
    }
    out = xpulse.normalize_apify_post(apify, handle="techfounder", category="peer_ceos")
    assert out["timestamp"] == ""


# ---------- fetch_account ----------

class _FakeDataset:
    def __init__(self, items):
        self._items = items

    def list_items(self, **kwargs):
        return type("R", (), {"items": self._items})()


class _FakeApifyClient:
    """Minimal stand-in for apify_client.ApifyClient for unit tests."""
    def __init__(self, dataset_items=None, fail_count=0):
        """fail_count: how many times .call() raises before succeeding (0 = always succeed)."""
        self._items = dataset_items or []
        self._fail_count = fail_count
        self._calls = 0

    def actor(self, actor_id):
        client = self
        class _Actor:
            def call(self, run_input=None, **_kwargs):
                client._calls += 1
                if client._calls <= client._fail_count:
                    raise RuntimeError(f"simulated apify failure #{client._calls}")
                return {"defaultDatasetId": "fake-dataset"}
        return _Actor()

    def dataset(self, dataset_id):
        return _FakeDataset(self._items)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Disable real time.sleep during tests so retry logic doesn't add 30s."""
    monkeypatch.setattr(xpulse.time, "sleep", lambda *_args, **_kw: None)


def test_fetch_account_returns_normalised_posts():
    fixture_path = FIXTURES / "sample-apify-response.json"
    items = json.loads(fixture_path.read_text(encoding="utf-8"))
    sama_items = [i for i in items if i.get("author", {}).get("userName") == "techfounder"]
    client = _FakeApifyClient(dataset_items=sama_items)
    posts = xpulse.fetch_account(client, "techfounder", "peer_ceos", max_per_account=30)
    assert len(posts) == len(sama_items)
    assert all(p["handle"] == "techfounder" for p in posts)
    assert all(p["category"] == "peer_ceos" for p in posts)


def test_fetch_account_retries_once_then_succeeds():
    """Spec: one retry after 30s. First call raises, second succeeds."""
    fixture_path = FIXTURES / "sample-apify-response.json"
    items = json.loads(fixture_path.read_text(encoding="utf-8"))
    client = _FakeApifyClient(dataset_items=items, fail_count=1)
    posts = xpulse.fetch_account(client, "techfounder", "peer_ceos", max_per_account=30)
    assert len(posts) == len(items)
    assert client._calls == 2


def test_fetch_account_returns_empty_after_two_failures(capsys):
    """Spec: after retry also fails, halt for that account, log to stderr, return []."""
    client = _FakeApifyClient(fail_count=2)
    posts = xpulse.fetch_account(client, "broken", "peer_ceos", max_per_account=30)
    assert posts == []
    assert client._calls == 2
    captured = capsys.readouterr()
    # WARN must land on stderr (the spec is explicit; do NOT relax to stdout-or-stderr)
    assert "broken" in captured.err


# ---------- fetch_all_accounts ----------

def test_fetch_all_accounts_aggregates_across_handles():
    fixture_path = FIXTURES / "sample-apify-response.json"
    all_items = json.loads(fixture_path.read_text(encoding="utf-8"))
    client = _FakeApifyClient(dataset_items=all_items)
    accounts = [("techfounder", "peer_ceos"), ("founder_two", "peer_ceos")]
    posts = xpulse.fetch_all_accounts(client, accounts, max_per_account=30)
    assert len(posts) == 2 * len(all_items)
    handles = {p["handle"] for p in posts}
    assert handles == {"techfounder", "founder_two"}


def test_fetch_all_accounts_empty_list():
    client = _FakeApifyClient(dataset_items=[])
    posts = xpulse.fetch_all_accounts(client, [], max_per_account=30)
    assert posts == []


# ---------- load_mock_response ----------

def test_load_mock_response_tags_by_author():
    fixture_path = FIXTURES / "sample-apify-response.json"
    accounts = [("techfounder", "peer_ceos"), ("founder_two", "peer_ceos"), ("acmenetworks", "dpi_competitors")]
    posts = xpulse.load_mock_response(fixture_path, accounts)
    assert len(posts) > 0
    handles = {p["handle"] for p in posts}
    assert handles.issubset({"techfounder", "founder_two", "acmenetworks"})
    sand_posts = [p for p in posts if p["handle"] == "acmenetworks"]
    assert all(p["category"] == "dpi_competitors" for p in sand_posts)


def test_load_mock_response_logs_skipped(capsys):
    """Posts from authors not in account_list should be counted + logged to stderr."""
    fixture_path = FIXTURES / "sample-apify-response.json"
    # Only sama is configured; founder_two + acmenetworks should be skipped
    accounts = [("techfounder", "peer_ceos")]
    posts = xpulse.load_mock_response(fixture_path, accounts)
    assert all(p["handle"] == "techfounder" for p in posts)
    captured = capsys.readouterr()
    assert "skipped" in captured.err.lower()


# ---------- estimate_cost ----------

def test_estimate_cost_basic():
    cost = xpulse.estimate_cost(account_count=30, max_per_account=30)
    assert 0.20 <= cost <= 0.35  # 30*30*0.0003 = 0.27


def test_estimate_cost_zero_accounts():
    assert xpulse.estimate_cost(account_count=0, max_per_account=30) == 0.0


# ---------- end-to-end pipeline (--mock-apify) ----------

def _recent_fixture(tmp_path):
    """Copy the static Apify fixture but stamp every post's createdAt to ~1 day
    ago, so the posts fall inside any --window regardless of wall-clock date.

    The static fixture has hardcoded May-2026 dates; once those age past the
    --window cutoff the end-to-end tests filter everything out and assert on an
    empty list. Mirroring pulse._parse_twitter_date's "%a %b %d %H:%M:%S %z %Y"
    format here decouples these tests from time permanently.
    """
    src = FIXTURES / "sample-apify-response.json"
    posts = json.loads(src.read_text(encoding="utf-8"))
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
        "%a %b %d %H:%M:%S %z %Y"
    )
    for p in posts:
        p["createdAt"] = recent
    dest = tmp_path / "recent-apify-response.json"
    dest.write_text(json.dumps(posts), encoding="utf-8")
    return dest


def test_main_pipeline_with_mock_apify(tmp_path):
    """Full main() flow with fixture data; verifies raw + filtered JSON outputs."""
    import subprocess
    out_dir = tmp_path / "x-pulse-test"
    result = subprocess.run([
        "python", str(PULSE_PATH),
        "--window", "30d",
        "--accounts-yaml", str(FIXTURES / "sample-accounts.yaml"),
        "--output-dir", str(out_dir),
        "--mock-apify", str(_recent_fixture(tmp_path)),
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    raw = json.loads((out_dir / "raw-posts.json").read_text(encoding="utf-8"))
    filt = json.loads((out_dir / "filtered-posts.json").read_text(encoding="utf-8"))
    assert len(raw) > 0
    assert len(filt) > 0
    assert len(filt) <= len(raw)
    assert {p["category"] for p in filt}.issubset({p["category"] for p in raw})


def test_main_pipeline_with_bucket_filter(tmp_path):
    """--bucket should restrict to one category."""
    import subprocess
    out_dir = tmp_path / "x-pulse-bucket"
    result = subprocess.run([
        "python", str(PULSE_PATH),
        "--window", "30d",
        "--bucket", "dpi_competitors",
        "--accounts-yaml", str(FIXTURES / "sample-accounts.yaml"),
        "--output-dir", str(out_dir),
        "--mock-apify", str(_recent_fixture(tmp_path)),
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    raw = json.loads((out_dir / "raw-posts.json").read_text(encoding="utf-8"))
    assert len(raw) > 0
    assert all(p["category"] == "dpi_competitors" for p in raw)
    assert all(p["handle"] == "acmenetworks" for p in raw)


def test_main_pipeline_with_unknown_bucket_fails(tmp_path):
    """--bucket pointing to non-existent category exits non-zero."""
    import subprocess
    out_dir = tmp_path / "x-pulse-bad-bucket"
    result = subprocess.run([
        "python", str(PULSE_PATH),
        "--window", "72h",
        "--bucket", "no_such_bucket",
        "--accounts-yaml", str(FIXTURES / "sample-accounts.yaml"),
        "--output-dir", str(out_dir),
        "--mock-apify", str(FIXTURES / "sample-apify-response.json"),
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "no_such_bucket" in result.stderr


def test_main_pipeline_dry_run(tmp_path):
    """--dry-run prints plan + cost without calling Apify (no output files written)."""
    import subprocess
    out_dir = tmp_path / "x-pulse-dryrun"
    result = subprocess.run([
        "python", str(PULSE_PATH),
        "--window", "72h",
        "--dry-run",
        "--accounts-yaml", str(FIXTURES / "sample-accounts.yaml"),
        "--output-dir", str(out_dir),
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert "Estimated cost" in result.stdout
    assert "dry-run" in result.stdout.lower()
    # dry-run doesn't create the output directory
    assert not (out_dir / "raw-posts.json").exists()
