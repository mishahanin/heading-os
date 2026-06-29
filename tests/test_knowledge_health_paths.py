#!/usr/bin/env python3
"""Regression test for the knowledge-health data-root seam.

Knowledge notes live under the DATA root, not the ENGINE root, so the old
`file_path.relative_to(WORKSPACE)` raised ValueError once a real note existed
under `knowledge/` (surfaced 2026-06-29 by knowledge/technology/google-okf-...).
The fix routes display paths through scripts.utils.workspace.display_path (unit-
tested in test_display_path.py). This test guards the integration: scan_notes()
must not raise on the live knowledge dir regardless of which root holds it.

Standalone-runnable, plain asserts.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("knowledge_health", ROOT / "scripts" / "knowledge-health.py")
khealth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(khealth)


def test_scan_notes_does_not_raise():
    """The exact regression: scanning the live knowledge dir must not raise the
    data-root ValueError, regardless of how many real notes exist."""
    notes = khealth.scan_notes()
    assert isinstance(notes, list)
    for n in notes:
        assert isinstance(n["path"], str)
        # Paths are displayed relative to a known root, never an absolute leak.
        assert not n["path"].startswith("/")


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  [OK ] {name}")
            except AssertionError as e:
                failures += 1
                print(f"  [FAIL] {name}: {e}")
    sys.exit(1 if failures else 0)
