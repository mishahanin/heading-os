"""F-M11: alert.py must not import bridge_daemon at module load time."""
import importlib
import sys
from pathlib import Path

import pytest


def test_alert_imports_without_bridge_daemon(monkeypatch):
    """Importing scripts.utils.alert must not pull in bridge_daemon."""
    # Remove alert and bridge_daemon from sys.modules to force a fresh import
    to_remove = [k for k in sys.modules if "alert" in k or "bridge_daemon" in k]
    for k in to_remove:
        del sys.modules[k]

    # Block bridge_daemon from being importable (simulate environment without it)
    class _BlockedFinder:
        def find_module(self, fullname, path=None):
            if "bridge_daemon" in fullname:
                return self
            return None

        def load_module(self, fullname):
            raise ImportError(f"bridge_daemon blocked in test: {fullname}")

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
