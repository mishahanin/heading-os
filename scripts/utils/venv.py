#!/usr/bin/env python3
"""Re-exec helper: ensure workspace scripts run under the project .venv.

The repo pins exact dependency versions in pyproject.toml + uv.lock, installed
into .venv. The system interpreter may carry drifted versions (e.g. anthropic
0.102 vs the locked 0.109.2) or lack dev-only deps like pytest-cov. ensure_venv()
re-execs the current script under .venv/bin/python whenever it was launched with
any other interpreter, so every entry point gets the locked dependency set no
matter how it was invoked (system python, a bare `python scripts/X.py`, etc.).

Call it once, as early as practical, in a CLI entry point -- right after the
standard sys.path bootstrap and before the heavy third-party imports:

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.utils.venv import ensure_venv  # noqa: E402
    ensure_venv()

os.execv replaces the whole process image, so it is correct even if some heavy
modules were already imported under the system interpreter before the call --
the fresh run re-imports them from .venv. ensure_venv() is a no-op when already
under .venv, when .venv is absent, or after one relaunch (an env sentinel guards
against an exec loop).
"""
import os
import sys
from pathlib import Path

# Sentinel env var: set on the first relaunch so the re-exec'd process does not
# loop. Path comparison alone would also stop the loop, but the sentinel is
# belt-and-braces against symlink/realpath edge cases.
_SENTINEL = "HEADING_OS_VENV_RELAUNCHED"


def venv_python() -> Path:
    """Path to the project venv interpreter (may not exist on this machine)."""
    # scripts/utils/venv.py -> repo root is parents[2].
    return Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"


def ensure_venv() -> None:
    """Re-exec the running script under .venv/bin/python if needed; else no-op."""
    target = venv_python()
    if not target.exists():
        return
    if Path(sys.executable).resolve() == target.resolve():
        return
    if os.environ.get(_SENTINEL) == "1":
        return
    os.environ[_SENTINEL] = "1"
    script = str(Path(sys.argv[0]).resolve())
    os.execv(str(target), [str(target), script, *sys.argv[1:]])  # noqa: S606  # fixed argv, no shell
