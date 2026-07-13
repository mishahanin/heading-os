import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(args, env_home):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "reminders.py"), *args],
        cwd=str(ROOT), capture_output=True, text=True,
        env={**__import__("os").environ, "HEADING_OS_DATA": str(env_home)},
    )


def test_add_then_list(tmp_path):
    (tmp_path / "outputs" / "operations" / "reminders").mkdir(parents=True)
    r = _run(["add", "--once", "2026-07-26", "--message", "Prep Beacon"], tmp_path)
    assert r.returncode == 0, r.stderr
    out = _run(["list"], tmp_path)
    assert "Prep Beacon" in out.stdout
