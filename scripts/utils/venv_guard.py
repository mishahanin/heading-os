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
    from scripts.utils.venv_guard import ensure_venv  # noqa: E402
    ensure_venv()

os.execv replaces the whole process image and RESTARTS the script from line 1,
so anything above the call runs a second time. For pure imports that is
harmless: the fresh run re-imports them from .venv. For a SIDE EFFECT it is not.
Output written before the call is emitted twice on an unbuffered or
line-buffered stream (a terminal, PYTHONUNBUFFERED, `python -u`) and is lost
outright on a block-buffered one (a pipe, which is how hooks and daemons capture
a script), because the buffer dies with the process image. Put no work above the
guard: not a print, not a file write, not a lock.

ensure_venv() is a no-op when already under .venv, when .venv is absent, or when
the env sentinel is present (which guards against an exec loop). The sentinel is
REMOVED from the environment as it is read, so a relaunch does not disable this
guard for every descendant process; see `_SENTINEL_SEEN` below.
"""
import os
import sys
from pathlib import Path

# Sentinel env var: set on the first relaunch so the re-exec'd process does not
# loop. Path comparison alone would also stop the loop, but the sentinel is
# belt-and-braces against symlink/realpath edge cases.
_SENTINEL = "HEADING_OS_VENV_RELAUNCHED"

# Whether THIS process saw the sentinel. `ensure_venv` removes the variable from
# the environment as it reads it, and remembers the answer here instead.
#
# `os.environ[...] = ` is putenv, so before this the flag was inherited by the
# WHOLE process tree below a relaunch rather than consumed by it: any child a
# relaunched script spawned with a non-venv interpreter (a `python3 ...`
# subprocess, a `bash scripts/install-*.sh` that resolves `command -v python3`, a
# systemd unit inheriting the env) had this guard silently disabled and ran
# against system site-packages instead of the pinned set. The module state keeps
# the deliberate in-process opt-out that `tests/conftest.py` relies on, including
# across repeated calls, while stopping it leaking downward.
_SENTINEL_SEEN = False


def venv_python() -> Path:
    """Path to the project venv interpreter (may not exist on this machine)."""
    # scripts/utils/venv_guard.py -> repo root is parents[2].
    return Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"


def interpreter_identity(path: Path) -> tuple:
    """What makes two interpreter paths the SAME environment, as a comparable.

    The containing directory beside the real file, and the directory is the half
    that matters. A venv interpreter's environment is decided by where it SITS —
    `pyvenv.cfg` lies beside `bin/`, and that file is what puts the venv's
    `site-packages` on the path — not by which base binary its symlink finally
    lands on.

    Measured 2026-08-05 against a venv built by the stdlib `python -m venv`,
    which the project's setup notes document as a supported layout:
    `.venv/bin/python` is a symlink to the very system interpreter an operator
    types. A comparison that resolved BOTH sides collapsed the two onto one real
    file and answered "the same".

    The real file is kept in the tuple as well, so this is strictly narrower than
    a resolved-path comparison rather than merely different: `/usr/bin/python3.11`
    and `/usr/bin/python3.12` share a directory and are still told apart. Only
    the parent is resolved for the environment half; resolving the leaf too is
    what caused the defect above, and is deliberately not done.

    Lives HERE rather than beside either caller. Both `ensure_venv` below and
    `canopus_contract.interpreter_notice` ask the same question, and a second
    spelling of it would drift SILENTLY — each returns something comparable, and
    the disagreement only shows up as a suite that ran under the wrong
    interpreter. This module is the lower layer, so the dependency points one
    way.
    """
    candidate = Path(path)
    return (candidate.parent.resolve(), candidate.resolve())


def ensure_venv() -> None:
    """Re-exec the running script under .venv/bin/python if needed; else no-op.

    The comparison is `interpreter_identity`, never resolved paths, and that is
    not a refinement. Measured 2026-08-05 on a stdlib `python -m venv` layout:
    resolving both sides collapsed the venv interpreter and the system one onto
    a single real file, so this function returned WITHOUT re-execing and the
    suite ran under the system interpreter with none of the pinned dependencies
    — the exact outcome the first line of this docstring promises to prevent, on
    a layout the project supports. A guard that fails open on a documented
    configuration is worse than no guard, because the promise above is what the
    next reader relies on.
    """
    global _SENTINEL_SEEN
    # Read and REMOVE, before anything can return early. Popping it here is what
    # keeps a relaunch from disabling this guard for every descendant process;
    # `_SENTINEL_SEEN` keeps the answer for the rest of this process, so a
    # deliberate opt-out still holds across repeated calls.
    if os.environ.pop(_SENTINEL, None) == "1":
        _SENTINEL_SEEN = True

    target = venv_python()
    if not target.exists():
        return
    if interpreter_identity(Path(sys.executable)) == interpreter_identity(target):
        return
    if _SENTINEL_SEEN:
        return
    os.environ[_SENTINEL] = "1"
    script = str(Path(sys.argv[0]).resolve())
    os.execv(str(target), [str(target), script, *sys.argv[1:]])  # noqa: S606  # fixed argv, no shell
