"""regenerate-docs-html.py must scan the DATA overlay's docs/ + templates/, not
just the engine clone — else an edited CEO-only guide (CEO-ADMIN-GUIDE, USAGE-GUIDE)
whose HTML was never regenerated reads fresh to `--check` (a silent staleness
blind spot). Guards the two-root `tracked_dirs()` fix.

The subprocess below is what made this test pass over a module-level
`TRACKED_DIRS` constant that froze `get_data_root()` at import: a fresh
interpreter per env is the one way to observe a frozen value following the
environment. `tracked_dirs()` is now a function resolved at call time, so an
in-process caller follows it too; that half is pinned in
tests/test_a_tracked_dir_list_frozen_before_any_test_could_move_it.py.
"""
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "regenerate-docs-html.py"


def _tracked_dirs(env_extra: dict) -> str:
    """Import the renderer with a given env and return its tracked dirs, one per line."""
    code = (
        "import importlib.util;from pathlib import Path;"
        f"spec=importlib.util.spec_from_file_location('r',r'{SCRIPT}');"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "print(chr(10).join(str(d) for d in m.tracked_dirs()))"
    )
    r = subprocess.run([sys.executable, "-c", code],
                       env={**os.environ, **env_extra},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_tracked_dirs_include_data_overlay(tmp_path):
    data = tmp_path / "data"
    (data / "docs").mkdir(parents=True)
    (data / "templates").mkdir()
    out = _tracked_dirs({"HEADING_OS_DATA": str(data)})
    assert str(data / "docs") in out
    assert str(data / "templates") in out
