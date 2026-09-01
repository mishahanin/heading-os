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
    data-root ValueError, regardless of how many real notes exist.

    Kept, but it is the WEAK half and says so. The loop body runs once per note
    on disk: two on the operator's machine on 2026-09-01, and ZERO on a public
    clone with no data overlay, where every assertion below is skipped and the
    test is green having measured nothing (cross-shard finding 25). The
    hermetic case beneath it is the one that measures the regression on every
    machine.
    """
    notes = khealth.scan_notes()
    assert isinstance(notes, list)
    for n in notes:
        assert isinstance(n["path"], str)
        # Paths are displayed relative to a known root, never an absolute leak.
        assert not n["path"].startswith("/")


def test_a_note_under_the_data_root_is_scanned_and_displayed_relative(monkeypatch, tmp_path):
    """The regression itself, on a corpus this test builds.

    The 2026-06-29 failure needed a note that lives under the DATA root while
    the engine root is somewhere else, which is exactly the topology a bare
    clone does not have. Seeding a throwaway overlay reproduces it anywhere:
    `file_path.relative_to(WORKSPACE)` would raise `ValueError` here, and
    `display_path` must instead return the path relative to the data root.
    """
    from scripts.utils import workspace

    data = tmp_path / "data-overlay"
    notes_dir = data / "knowledge" / "technology"
    notes_dir.mkdir(parents=True)
    (notes_dir / "probe-note.md").write_text(
        "---\ntitle: Probe\ncreated: 2026-09-01\n---\n\nBody.\n", encoding="utf-8")

    monkeypatch.setattr(workspace, "get_data_root", lambda: data)
    monkeypatch.setattr(khealth, "get_knowledge_dir", lambda: data / "knowledge")

    notes = khealth.scan_notes()
    paths = [n["path"] for n in notes]
    assert paths == ["knowledge/technology/probe-note.md"], (
        f"the seeded note was not scanned, or its display path leaked an "
        f"absolute location: {paths}"
    )


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
