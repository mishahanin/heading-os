"""F-M11: alert.py must not import bridge_daemon at module load time."""
import contextlib
import importlib
import sys
from pathlib import Path

import pytest


class _BlockedFinder:
    """A meta-path finder that refuses every `bridge_daemon` import.

    This implements `find_spec`, the PEP 451 protocol. It used to implement
    only `find_module`/`load_module`, the pre-3.4 protocol whose import-system
    fallback was REMOVED in Python 3.12: a meta-path entry without `find_spec`
    is skipped outright there, so the blocker blocked nothing, `bridge_daemon`
    imported normally, and the test's one assertion (`hasattr(alert_mod,
    "init")`) held whether or not the F-M11 cycle existed. The guard could not
    fail on any 3.12+ interpreter. This venv runs 3.11, which is the only
    reason it still had teeth at all, and a version bump would have removed
    them silently.

    `test_the_blocker_actually_blocks` below measures that this class refuses,
    rather than asserting it. A blocker nobody made refuse is not a blocker.
    """

    def find_spec(self, fullname, path=None, target=None):
        if "bridge_daemon" in fullname:
            raise ImportError(f"bridge_daemon blocked in test: {fullname}")
        return None


@contextlib.contextmanager
def _forgotten():
    """Yield an evict-callable; restore sys.modules exactly on exit.

    Restoring exactly means three things, not one:

    1. put back what was evicted;
    2. drop anything the fresh imports added that was not there before;
    3. re-bind each restored submodule as an ATTRIBUTE of its parent package.

    (3) is the one that is easy to miss and the one that actually bit. A fresh
    `import scripts.bridge_daemon.watcher` rebuilds the package object AND
    rebinds `scripts.bridge_daemon` on the `scripts` module. Putting
    `sys.modules` back does not undo that attribute write, so
    `sys.modules["scripts.bridge_daemon.watcher"]` and
    `scripts.bridge_daemon.watcher` then name two different module objects.
    `monkeypatch.setattr("scripts.bridge_daemon.watcher.Observer", ...)`
    resolves by attribute traversal, so it patched the orphan while the code
    under test kept reading the original. That is the whole failure.
    """
    saved: dict[str, object] = {}
    needles: list[str] = []

    def _forget(*more):
        needles.extend(more)
        for key in [k for k in sys.modules if any(n in k for n in more)]:
            saved.setdefault(key, sys.modules.pop(key))

    try:
        yield _forget
    finally:
        for key in [k for k in sys.modules if any(n in k for n in needles)]:
            if key not in saved:
                del sys.modules[key]
        sys.modules.update(saved)
        for key, module in saved.items():
            parent_name, _, leaf = key.rpartition(".")
            parent = sys.modules.get(parent_name) if parent_name else None
            if parent is not None:
                setattr(parent, leaf, module)


def test_the_eviction_does_not_outlive_its_scope():
    """The guard on the guard: sys.modules must survive this file intact.

    Module object IDENTITY, not key presence. Restoring a DIFFERENT object
    under the same name is exactly what broke the three bridge tests, and a
    key-presence check cannot see it.
    """
    import scripts.bridge_daemon.watcher as watcher_before

    with _forgotten() as forget:
        forget("bridge_daemon")
        assert "scripts.bridge_daemon.watcher" not in sys.modules, \
            "eviction did not happen, so the restore below proves nothing"
        rebuilt = importlib.import_module("scripts.bridge_daemon.watcher")
        assert rebuilt is not watcher_before, "the import was served from cache"

    assert sys.modules["scripts.bridge_daemon.watcher"] is watcher_before, \
        "the evicted watcher module was not restored to its original object"
    # The attribute path, which is what monkeypatch's string form traverses.
    # sys.modules and the parent-package attribute can disagree, and when they
    # do, a patch and the code under test see different module objects.
    import scripts.bridge_daemon
    assert scripts.bridge_daemon.watcher is watcher_before, \
        "sys.modules was restored but the parent package still names the orphan"


@pytest.fixture
def forget():
    """Evict modules for the duration of ONE test, then put sys.modules back.

    The eviction is unavoidable: forcing a fresh `scripts.utils.alert` import
    is the whole method here. Leaving it in place is not. This used to be a
    bare `del sys.modules[k]` over every key containing "alert" or
    "bridge_daemon", with no restore, and it silently broke other files.

    Measured 2026-08-30, three files in one process, in this order:
    `test_alert_no_import_cycle.py`, `tests/bridge/test_sources_approvals.py`,
    `tests/bridge/test_a_moved_file_bumps_the_page_it_arrived_on.py` gives
    `3 failed, 40 passed`; the same three with the first two swapped gives
    `43 passed`. The mechanism: the moved-file module binds `start_observer`
    from `scripts.bridge_daemon.watcher` at collection, this fixture then
    evicts that module, a later import rebuilds a SECOND watcher module
    object, and `monkeypatch.setattr("scripts.bridge_daemon.watcher.Observer",
    ...)` patches the new one while `start_observer` keeps reading the old
    one's globals. Three tests failed with `scheduled == []` and no defect
    anywhere near them.

    The restore itself lives in `_forgotten`, which
    `test_the_eviction_does_not_outlive_its_scope` drives directly.
    """
    with _forgotten() as _forget:
        yield _forget


def test_the_blocker_actually_blocks(forget, monkeypatch):
    """The negative case for the guard below: prove the finder refuses.

    Without this, a blocker that silently does nothing (which is exactly what
    the pre-PEP-451 version became on Python 3.12+) leaves the F-M11 test
    green over an unblocked import.
    """
    forget("bridge_daemon")
    monkeypatch.setattr(sys, "meta_path", [_BlockedFinder()] + sys.meta_path)
    with pytest.raises(ImportError, match="bridge_daemon blocked in test"):
        importlib.import_module("scripts.bridge_daemon")


def test_alert_imports_without_bridge_daemon(forget, monkeypatch):
    """Importing scripts.utils.alert must not pull in bridge_daemon."""
    # Remove alert and bridge_daemon from sys.modules to force a fresh import
    forget("alert", "bridge_daemon")

    # Block bridge_daemon from being importable (simulate environment without it)
    monkeypatch.setattr(sys, "meta_path", [_BlockedFinder()] + sys.meta_path)

    # This must not raise ImportError
    import scripts.utils.alert as alert_mod  # noqa: F401
    assert hasattr(alert_mod, "init"), \
        "alert.py must expose an init(fn) setter for the AQ-append callable"




def test_alert_init_sets_aq_fn():
    """alert.init(fn) must store the callable for use in _post_card."""
    import scripts.utils.alert as alert_mod
    dummy_fn = lambda ws, cards: {"ok": True, "added": 1}
    alert_mod.init(dummy_fn)
    assert alert_mod._aq_append_fn is dummy_fn


def test_alert_post_card_graceful_without_init(tmp_path):
    """_post_card must return False (not raise) when init() was never called."""
    import scripts.utils.alert as alert_mod
    alert_mod._aq_append_fn = None  # reset
    result = alert_mod._post_card(tmp_path, "warning", "t", "b", "test")
    assert result is False, f"expected False when _aq_append_fn is None, got {result!r}"
