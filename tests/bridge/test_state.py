from scripts.bridge_daemon.state import State

def test_initial_versions_zero():
    s = State()
    assert s.version("inbox") == 0
    assert s.version("inflight") == 0

def test_bump_increments():
    s = State()
    s.bump("inbox")
    s.bump("inbox")
    assert s.version("inbox") == 2

def test_global_version_aggregates():
    s = State()
    s.bump("inbox")
    s.bump("inflight")
    assert s.global_version() == 2  # sum of all component bumps

def test_etag_changes_on_bump():
    s = State()
    e1 = s.etag("inbox")
    s.bump("inbox")
    e2 = s.etag("inbox")
    assert e1 != e2

def test_snapshot_shape_after_bump():
    s = State()
    s.bump("inbox")
    snap = s.snapshot()
    assert set(snap.keys()) == {"global", "components", "data_times"}
    assert snap["global"] == 1
    assert snap["components"]["inbox"] == 1
    assert snap["components"]["inflight"] == 0
    assert snap["data_times"]["inbox"] is not None
    assert snap["data_times"]["inflight"] is None

def test_data_time_populates_on_bump():
    s = State()
    assert s.data_time("inbox") is None
    s.bump("inbox")
    ts = s.data_time("inbox")
    assert ts is not None
    assert "T" in ts  # ISO 8601 datetime separator
    assert ts.endswith("+00:00")  # UTC offset

def test_etag_format_is_quoted_v_prefix():
    s = State()
    assert s.etag("inbox") == '"v0"'
    s.bump("inbox")
    assert s.etag("inbox") == '"v1"'

def test_bump_handles_unknown_component():
    s = State()
    new_version = s.bump("ad-hoc-component")
    assert new_version == 1
    assert s.version("ad-hoc-component") == 1
