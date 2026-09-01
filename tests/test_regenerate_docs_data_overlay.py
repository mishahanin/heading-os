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


def _tracked_dirs(env_extra: dict) -> tuple[str, str]:
    """Import the renderer with a given env; return (stdout dirs, stderr).

    The HEADING_OS_DATA pin lives in this CHILD's environment only. Pinning it
    for a whole pytest run makes `overlay_write_guard` treat the scratch path as
    the operator's live overlay and refuse unrelated tests, so the pin stays
    where the question is.
    """
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
    return r.stdout, r.stderr


def test_tracked_dirs_include_data_overlay(tmp_path):
    data = tmp_path / "data"
    (data / "docs").mkdir(parents=True)
    (data / "templates").mkdir()
    out, _err = _tracked_dirs({"HEADING_OS_DATA": str(data)})
    assert str(data / "docs") in out
    assert str(data / "templates") in out


def test_an_unresolvable_data_root_still_renders_the_engines_own_docs(tmp_path):
    """Fail-soft, and loudly. The docstring promises both; nothing measured it.

    `HEADING_OS_DATA` naming a directory that does not exist makes
    `get_data_root()` raise, which is the exact state this handler exists for: a
    misconfigured overlay must not stop the engine's own docs/ and templates/
    from rendering, and it must not be swallowed in silence either
    (`.claude/rules/security.md`). MEASURED 2026-09-01: replacing the handler's
    `return dirs` with `return []` was caught by nothing, so the renderer could
    have gone from "skips the overlay" to "renders nothing at all" unnoticed.
    """
    absent = tmp_path / "no-such-overlay"
    out, err = _tracked_dirs({"HEADING_OS_DATA": str(absent)})

    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 2, lines
    assert lines[0].endswith("/docs") and lines[1].endswith("/templates"), lines
    assert str(absent) not in out
    assert "data-overlay scan skipped" in err
