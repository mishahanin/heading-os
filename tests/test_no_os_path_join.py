"""Regression: live scripts/ use pathlib, not os.path.join (F-L2).

The data-root seam and general code-quality both favour pathlib over os.path.join.
CEO commit e180220 migrated the data-root-bypass cases; Phase 3 F-L2 finished the
remaining 8 files. This guard keeps os.path.join out of live scripts/ so new code
follows the pathlib convention. archive/ is dead code (never executed) and exempt.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"


def test_no_os_path_join_in_live_scripts():
    """No live script under scripts/ may use os.path.join (use pathlib.Path)."""
    paths = sorted(p for p in SCRIPTS_DIR.rglob("*.py") if "archive" not in p.parts)
    # An empty violations list is green over zero files, so a renamed scripts/
    # directory or a changed suffix would switch this guard off without a failure.
    # 371 files survived the archive filter on 2026-08-26.
    assert len(paths) >= 220, f"the scan collapsed to {len(paths)} files"
    violations = []
    for py in paths:
        text = py.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "os.path.join" in line:
                violations.append(f"{py.relative_to(ROOT).as_posix()}:{lineno}: {line.strip()}")
    assert not violations, (
        "os.path.join found in live scripts/ — use pathlib.Path instead (F-L2):\n  "
        + "\n  ".join(violations)
    )
