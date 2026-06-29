import time
from scripts.bridge_daemon.watcher import classify_path, DebouncedBumper

def test_classify_inbox_path():
    assert classify_path("outputs/operations/email-intelligence/state.json") == "inbox"

def test_classify_inflight_paths():
    assert classify_path("outputs/content/linkedin/2026-05-17-draft.md") == "inflight"
    assert classify_path("outputs/intel/osint/2026-05-17_exampletelco.md") == "inflight"

def test_classify_unknown_returns_none():
    assert classify_path("outputs/unknown/foo.md") is None

def test_debounced_bumper_coalesces(workspace_root):
    bumps = []
    bumper = DebouncedBumper(lambda c: bumps.append(c), interval=0.05)
    for _ in range(5):
        bumper.schedule("inbox")
    time.sleep(0.15)
    assert bumps == ["inbox"]  # 5 events coalesced into 1
