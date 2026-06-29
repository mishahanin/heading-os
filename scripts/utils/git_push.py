#!/usr/bin/env python3
"""Verified, supervised git push — the one must-complete push primitive.

Wraps ``scripts/utils/supervise.run_supervised`` around ``git push`` with an
``ahead/behind == (0, 0)`` postcondition, so every push path in the workspace
(the safe-push CLI, the /backup → push-all flow, the corporate promote/rollback
gates, offboard) shares ONE mechanism that:

  (a) is bounded by *inactivity*, not a wall-clock guess — a slow-but-healthy
      pre-push test gate (~2.5 min and growing) is never clipped, while a truly
      stalled connection is caught and killed; and
  (b) never trusts a bare-push exit code — a ``git push`` that reports success
      while the ref did not advance is caught by the postcondition (the
      documented "bare push silently fails" case).

Auth is flexible so each caller keeps its existing credential model:
  * ``token=`` injects the GH_TOKEN credential helper through the child env
    (the token never touches argv);
  * ``env=`` uses a caller-built auth env as-is;
  * neither inherits the ambient environment (preserves a caller's own setup).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.engine_guard import scan_engine_repo
from scripts.utils.supervise import run_supervised
from scripts.utils.workspace import get_data_root, get_workspace_root

# Echoes the token from the child env (NOT argv) into git's credential protocol.
_CRED_HELPER = '!f(){ echo username=x-access-token; echo "password=$GH_PUSH_TOKEN"; }; f'


def _is_split_engine(repo: Path) -> bool:
    """True iff ``repo`` is the split-topology ENGINE clone (data lives in a sibling).

    Only the engine must stay code-only. The DATA overlay and the corporate/CRM repos
    legitimately carry private/corporate content, so they are exempt. Detected from the
    data-root seam: engine == workspace root AND data root resolves elsewhere. On a
    pre-cutover single repo (data root == workspace root) nothing is walled here.
    """
    try:
        engine = get_workspace_root().resolve()
        data = get_data_root().resolve()
    except Exception:
        return False
    return data != engine and repo.resolve() == engine


def load_gh_token() -> Optional[str]:
    """Return GH_TOKEN from the engine ``.env`` (the git pushgh source of truth)."""
    env_path = get_workspace_root() / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("GH_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def current_branch(repo) -> Optional[str]:
    """Return the current branch name of ``repo`` (or None)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def ahead_behind(repo, remote: str = "origin", branch: str = "main") -> Optional[tuple[int, int]]:
    """Return (behind, ahead) of HEAD vs ``remote/branch``, or None on error."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-list", "--left-right", "--count",
             f"{remote}/{branch}...HEAD"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    parts = out.stdout.split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def supervised_push(
    repo,
    *,
    remote: str = "origin",
    branch: str = "main",
    env: Optional[dict] = None,
    token: Optional[str] = None,
    stall_window: float = 120.0,
    status_path: Optional[str] = None,
    label: Optional[str] = None,
) -> dict:
    """Push ``repo`` to ``remote/branch`` under the progress watchdog and verify
    the ref actually advanced (``ahead/behind == 0 0``) before reporting success.

    Returns the ``run_supervised`` verdict dict (state ∈ ok/failed/hung/
    postcondition_failed). The caller decides what a non-"ok" state means.
    """
    repo = Path(repo)

    # Engine/data leak wall (universal chokepoint). EVERY engine push -- push-all,
    # safe-push, or any future caller -- routes through here, so a private/corporate-
    # routed file in the engine clone can never leave the machine, on any path, with no
    # skip flag. Runs BEFORE the push subprocess (refuse, do not push-then-detect).
    # The DATA/corporate/CRM repos are exempt (they legitimately carry such files).
    if _is_split_engine(repo):
        flagged = scan_engine_repo(repo)
        if flagged:
            preview = ", ".join(flagged[:5]) + (" ..." if len(flagged) > 5 else "")
            return {
                "state": "failed",
                "reason": (
                    f"engine clone carries {len(flagged)} data-class artifact(s) "
                    f"(route private/corporate); refusing to push: {preview}"
                ),
                "elapsed_s": 0.0,
                "exit_code": None,
                "tail": "\n".join(flagged),
                "flagged": flagged,
            }

    run_env = dict(env) if env is not None else None
    cmd = ["git", "-C", str(repo)]
    if token:
        run_env = dict(run_env if run_env is not None else os.environ)
        run_env["GH_PUSH_TOKEN"] = token
        run_env["GIT_TERMINAL_PROMPT"] = "0"
        cmd += ["-c", f"credential.helper={_CRED_HELPER}"]
    cmd += ["push", remote, branch]

    def postcondition() -> bool:
        return ahead_behind(repo, remote, branch) == (0, 0)

    return run_supervised(
        cmd, env=run_env, stall_window=stall_window, poll=3,
        postcondition=postcondition, status_path=status_path,
        label=label or f"push:{repo.name}",
    )
