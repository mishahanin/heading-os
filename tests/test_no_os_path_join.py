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
    violations = []
    for py in SCRIPTS_DIR.rglob("*.py"):
        if "archive" in py.parts:
            continue
        text = py.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "os.path.join" in line:
                violations.append(f"{py.relative_to(ROOT).as_posix()}:{lineno}: {line.strip()}")
    assert not violations, (
        "os.path.join found in live scripts/ — use pathlib.Path instead (F-L2):\n  "
        + "\n  ".join(violations)
    )
