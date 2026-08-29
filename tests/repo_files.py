#!/usr/bin/env python3
"""Re-export of `scripts/utils/repo_files.py`, kept for the test sweeps.

This module used to CARRY the implementation, and that was the defect. Twenty
test sweeps were migrated onto it on 2026-08-29, and the same day the same
defect was measured live in production code: `classification-health.py`
reported 2363 files, 427 of which git ignores. Production cannot import from
`tests/`, so the implementation living here guaranteed a second copy in
`scripts/`, and the second copy is the one that stops being fixed.

The implementation moved to `scripts/utils/repo_files.py`. This file stays so
the twenty migrated sweeps keep their import, and so
`tests/test_a_walker_that_never_asked_git.py` keeps pointing new sweeps at one
name. Add nothing here: a helper added to this file would be exactly the second
copy the move exists to prevent.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.repo_files import (  # noqa: E402,F401
    ROOT,
    ignored_paths,
    not_ignored,
    tracked_paths,
    tracked_python_files,
)
