"""The in-flight scanner, and the `refresh` no test in the tree ever called.

`scan_inflight` had two tests. `refresh` had none, anywhere. It is the function
the scheduler actually invokes, and the only reason `scan_inflight` is reachable
in production at all.

Full-suite branch coverage on 2026-08-31 over 19,835 tests:

    scripts/bridge_daemon/refreshers/inflight.py  50  10  14  1  80%
        Missing 72, 106-116

Lines 106-116 are the entire body of `refresh`. Line 72 is the `continue` that
filters non-`.md` files out of the scan.

MEASURED the same day in a clone under /tmp, removing the change-detection
that is the whole point of the function:

    if current == _LAST_SCAN:
        return

    .venv/bin/python -m pytest tests/bridge/test_refresher_inflight.py -q
    -> 2 passed
    .venv/bin/python -m pytest tests/bridge -q
    -> 1312 passed, 1 skipped

The mutation survived.
Confirmed at the only scope that settles it. The same mutation was carried into
a whole-suite run alongside five others:

    pytest tests -> 12 failed, 19810 passed, 14 skipped (0:35:43)

and not one of those twelve failures is attributable to this one. That check
matters here because three sibling findings in this shard did NOT survive it:
each looked naked against `tests/bridge` alone and was caught by a file
elsewhere in `tests/`. A directory-scoped mutation run proves a guard is
untested in that directory and nothing more.

That mutation reinstates the exact regression the
function's docstring records: "This used to be a bare `state_obj.bump
("inflight")` with no scan behind it: every tick told ETag-watching clients the
data was new while nothing had been recomputed." A freshness signal that fires
on a schedule rather than on a change is what the freshness envelope was
redesigned to eliminate, and nothing was holding it down.

`_LAST_SCAN` is module state, deliberately (one daemon, one scanner), so every
test here that touches `refresh` resets it. Without that reset the tests would
pass or fail on execution order, which is the sort of luck this file is here to
remove.
"""
import os
import time

import pytest

import scripts.bridge_daemon.refreshers.inflight as inflight_mod
from scripts.bridge_daemon.refreshers.inflight import refresh, scan_inflight
from scripts.bridge_daemon.state import State


@pytest.fixture(autouse=True)
def _reset_last_scan(monkeypatch):
    """Module-level fingerprint, reset per test rather than shared across them."""
    monkeypatch.setattr(inflight_mod, "_LAST_SCAN", None)


def test_scan_finds_recent_drafts(workspace_root):
    draft = workspace_root / "outputs/content/linkedin/2026-05-17-draft.md"
    draft.write_text("---\ntitle: Test\nsession_id: abc123\n---\nbody")
    rows = scan_inflight(workspace_root, retention_hours=24)
    assert len(rows) == 1
    assert rows[0]["category"] == "linkedin"
    assert rows[0]["session_id"] == "abc123"

def test_scan_ignores_old_files(workspace_root):
    draft = workspace_root / "outputs/content/linkedin/old.md"
    draft.write_text("body")
    # set mtime to 2 days ago
    old = time.time() - 2 * 86400
    os.utime(draft, (old, old))
    assert scan_inflight(workspace_root, retention_hours=24) == []


def test_the_scanned_directory_really_exists(workspace_root):
    """Floor under both tests above.

    `scan_inflight` skips a directory that is not on disk, so an empty result
    is what a MISSING `outputs/content/linkedin` produces too. The `workspace_root`
    fixture creates it; if that ever changes, `test_scan_ignores_old_files`
    starts passing for the wrong reason and says nothing about the cutoff.
    """
    assert (workspace_root / "outputs" / "content" / "linkedin").is_dir()


def test_only_markdown_is_scanned(workspace_root):
    """Line 72, never executed by any test in the tree.

    The producers write PNGs, JSON sidecars and `.pdf` renders into these same
    directories. Without the suffix filter each becomes an in-flight row, and
    `read_text` on a PNG raises UnicodeDecodeError into the per-file handler,
    so the row would vanish for a reason no log would explain.
    """
    d = workspace_root / "outputs" / "content" / "linkedin"
    (d / "keeper.md").write_text("---\nsession_id: keep\n---\nbody",
                                 encoding="utf-8")
    (d / "render.pdf").write_bytes(b"%PDF-1.4 not markdown")
    (d / "meta.json").write_text('{"a": 1}', encoding="utf-8")
    rows = scan_inflight(workspace_root, retention_hours=24)
    assert [r["id"] for r in rows] == ["keeper"]


def test_refresh_bumps_the_component_when_the_set_changed(workspace_root):
    d = workspace_root / "outputs" / "content" / "linkedin"
    (d / "first.md").write_text("---\nsession_id: s1\n---\nbody",
                                encoding="utf-8")
    state = State()
    assert state.version("inflight") == 0
    refresh(workspace_root, state)
    assert state.version("inflight") == 1, (
        "a new in-flight artifact must move the component version"
    )


def test_refresh_does_not_bump_when_nothing_moved(workspace_root):
    """The assertion the surviving mutation had nobody to answer to.

    Two refreshes over an unchanged tree: the second must be a no-op. With the
    change-detection removed this reads 2, which is the bare-bump regression.
    """
    d = workspace_root / "outputs" / "content" / "linkedin"
    (d / "first.md").write_text("---\nsession_id: s1\n---\nbody",
                                encoding="utf-8")
    state = State()
    refresh(workspace_root, state)
    first = state.version("inflight")
    refresh(workspace_root, state)
    assert state.version("inflight") == first, (
        "an unchanged in-flight set bumped the version anyway; every tick then "
        "tells an ETag-watching client the data is new"
    )


def test_refresh_bumps_again_once_the_set_really_changes(workspace_root):
    """The other side of the same bound. Without this, `return` before any bump
    would satisfy the no-op test and freeze the component forever."""
    d = workspace_root / "outputs" / "content" / "linkedin"
    (d / "first.md").write_text("---\nsession_id: s1\n---\nbody",
                                encoding="utf-8")
    state = State()
    refresh(workspace_root, state)
    refresh(workspace_root, state)
    settled = state.version("inflight")

    (d / "second.md").write_text("---\nsession_id: s2\n---\nbody",
                                 encoding="utf-8")
    refresh(workspace_root, state)
    assert state.version("inflight") == settled + 1


def test_refresh_survives_a_scan_that_raises(workspace_root, caplog, monkeypatch):
    """The `except OSError` in `refresh`, which no test reached.

    The scheduler calls this on a timer with nothing above it to catch an
    exception, and the contract in the docstring is that a failed scan leaves
    the component version alone rather than lying about freshness.
    """
    def boom(*a, **kw):
        raise OSError("the tree went away mid-scan")

    monkeypatch.setattr(inflight_mod, "scan_inflight", boom)
    state = State()
    with caplog.at_level("WARNING"):
        refresh(workspace_root, state)          # must not raise
    assert state.version("inflight") == 0, (
        "a failed scan established nothing, so it must not claim new data"
    )
    assert "inflight scan failed" in caplog.text, caplog.text
