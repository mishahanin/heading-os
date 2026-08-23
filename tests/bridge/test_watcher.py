"""Watcher path classification and debounce.

`classify_path` returns a TUPLE of components since 2026-08-23: one write can
invalidate several pages, and returning a single name is what left
`outputs/documents/`, `outputs/content/tribe/`, `threads/` and the Studio
archive with no coverage at all. Full account and the coverage assertions:
`tests/bridge/test_watcher_covers_what_it_claims.py`.
"""
import time

from scripts.bridge_daemon.watcher import classify_path, DebouncedBumper


def test_classify_inbox_path():
    components = classify_path("outputs/operations/email-intelligence/state.json")
    assert "inbox" in components


def test_classify_inflight_paths():
    assert "inflight" in classify_path("outputs/content/linkedin/2026-05-17-draft.md")
    assert "inflight" in classify_path("outputs/intel/osint/2026-05-17_exampletelco.md")


def test_classify_unknown_returns_empty():
    assert classify_path("outputs/unknown/foo.md") == ()


def test_debounced_bumper_coalesces(workspace_root):
    bumps = []
    bumper = DebouncedBumper(lambda c: bumps.append(c), interval=0.05)
    for _ in range(5):
        bumper.schedule("inbox")
    time.sleep(0.15)
    assert bumps == ["inbox"]  # 5 events coalesced into 1
