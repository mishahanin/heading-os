#!/usr/bin/env python3
"""SEC-006: Verify .sessions/ directories and files have restricted permissions.

Behavioral (live os.stat) + AST guard. The live check walks the actual .sessions/
tree; the AST check confirms the creation calls include mode=0o700 so new token
stores are also locked down.

Live check is skipped when .sessions/ doesn't exist (CI without real OAuth tokens).
"""
import ast
import os
import stat
from pathlib import Path

import pytest

from tests.security.conftest import read_file_content

SESSIONS_DIR = Path(__file__).resolve().parent.parent.parent / ".sessions"

# Files that ACTUALLY create the OAuth token store. `gmail-reader.py` was listed
# here until 2026-08-27 and contains zero `open(` and zero `os.makedirs` calls -
# it imports `scripts/utils/gmail_auth.py`, which is where the dance lives - so
# both AST guards below walked an empty tree and passed by vacuum. Paths are
# relative to `scripts/`; `test_the_listed_files_actually_write_a_token` is the
# floor that stops this list from drifting back to a file that writes nothing.
OAUTH_SCRIPTS = [
    "google-contacts.py",
    "utils/gmail_auth.py",
]


# ---- Live behavioral ----

def _check_dir_mode(path: Path) -> list[str]:
    """Return violations: dirs must be 0o700, files must be 0o600."""
    violations = []
    for entry in path.rglob("*"):
        mode = stat.S_IMODE(os.stat(entry).st_mode)
        if entry.is_dir():
            if mode != 0o700:
                violations.append(
                    f"DIR  {entry.relative_to(path)}: mode={oct(mode)} (expected 0o700)"
                )
        elif entry.is_file():
            if mode != 0o600:
                violations.append(
                    f"FILE {entry.relative_to(path)}: mode={oct(mode)} (expected 0o600)"
                )
    return violations


@pytest.mark.skipif(not SESSIONS_DIR.exists(), reason=".sessions/ not present; skipped in CI")
def test_sessions_dir_permissions():
    """All dirs under .sessions/ must be 0o700; all files must be 0o600."""
    top_mode = stat.S_IMODE(os.stat(SESSIONS_DIR).st_mode)
    assert top_mode == 0o700, (
        f".sessions/ top-level mode={oct(top_mode)}, expected 0o700"
    )
    violations = _check_dir_mode(SESSIONS_DIR)
    assert not violations, (
        "Restricted permission violations in .sessions/:\n  "
        + "\n  ".join(violations)
    )


# ---- AST guard ----

def _check_makedirs_has_mode(file_path: Path) -> list[str]:
    """Parse AST: every os.makedirs call must include mode= keyword."""
    content = read_file_content(file_path)
    tree = ast.parse(content)
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "makedirs"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
            ):
                kwarg_names = [kw.arg for kw in node.keywords]
                if "mode" not in kwarg_names:
                    violations.append(
                        f"Line {node.lineno}: os.makedirs() without mode= parameter"
                    )
    return violations


def _token_writes(file_path: Path) -> tuple[int, list[str]]:
    """Count the token writes in a file and report the ones left world-readable.

    Two sanctioned forms, because both are in use:

    * `open(path, "w")` plus `os.chmod(path, 0o600)` in the same function. The
      window between the two is small and the file is short-lived, so this form
      is tolerated where it already exists.
    * `atomic_write_text(path, text, mode=0o600)`, which sets the mode on the
      tempfile BEFORE the rename, so the token is never briefly world-readable
      and a crash mid-write cannot truncate the live credential.

    Returns (writes_found, violations). The count is what the floor test asserts
    on: a guard that finds no write at all reports no violation either.
    """
    tree = ast.parse(read_file_content(file_path))
    violations: list[str] = []
    writes = 0

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        plain_write = False
        has_chmod_600 = False
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            # open(TOKEN_PATH, "w") or open(TOKEN_PATH, "w", ...)
            if isinstance(func, ast.Name) and func.id == "open" and len(child.args) >= 2:
                mode_arg = child.args[1]
                if isinstance(mode_arg, ast.Constant) and "w" in str(mode_arg.value):
                    plain_write = True
                    writes += 1
            # atomic_write_text(path, text, mode=0o600)
            if isinstance(func, ast.Name) and func.id == "atomic_write_text":
                writes += 1
                mode_kw = next((kw for kw in child.keywords if kw.arg == "mode"), None)
                if not (mode_kw and isinstance(mode_kw.value, ast.Constant)
                        and mode_kw.value.value == 0o600):
                    violations.append(
                        f"Line {child.lineno}: atomic_write_text without mode=0o600"
                    )
            # os.chmod(..., 0o600)
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "chmod"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
            ):
                for arg in child.args:
                    if isinstance(arg, ast.Constant) and arg.value == 0o600:
                        has_chmod_600 = True

        if plain_write and not has_chmod_600:
            violations.append(
                f"Function '{node.name}' (line {node.lineno}): writes token file "
                "but has no os.chmod(..., 0o600) call"
            )
    return writes, violations


@pytest.mark.parametrize("script_name", OAUTH_SCRIPTS)
def test_oauth_script_makedirs_has_mode(scripts_dir, script_name):
    """OAuth scripts must call os.makedirs with an explicit mode= argument."""
    path = scripts_dir / script_name
    assert path.exists(), f"{script_name} is listed in OAUTH_SCRIPTS but absent"
    violations = _check_makedirs_has_mode(path)
    assert not violations, (
        f"{script_name}: os.makedirs calls without mode=0o700:\n"
        + "\n".join(violations)
    )


@pytest.mark.parametrize("script_name", OAUTH_SCRIPTS)
def test_the_listed_files_actually_write_a_token(scripts_dir, script_name):
    """The floor under both AST guards.

    An entry that writes nothing makes every guard below it pass without
    checking anything, which is how `gmail-reader.py` sat on this list while
    the real write lived one import away.
    """
    writes, _ = _token_writes(scripts_dir / script_name)
    assert writes >= 1, (
        f"{script_name} performs no token write, so the SEC-006 AST guards "
        "over it assert nothing. Point the list at the file that writes."
    )


@pytest.mark.parametrize("script_name", OAUTH_SCRIPTS)
def test_oauth_script_token_file_is_written_restricted(scripts_dir, script_name):
    """Every OAuth token write must land at 0o600, by chmod or by atomic mode."""
    _, violations = _token_writes(scripts_dir / script_name)
    assert not violations, (
        f"{script_name}: token-file write left unrestricted:\n"
        + "\n".join(violations)
    )


def test_session_start_cache_chmod():
    """The SessionStart hook writes .sessions/crm-health-cache.json every session.

    It must chmod that cache to 0o600 so the live .sessions/ tree stays restricted
    across regenerations. This AST guard covers the fresh-clone/CI case where the
    live test_sessions_dir_permissions above skips (no .sessions/ present).
    """
    hook = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks" / "session-start.py"
    if not hook.exists():
        pytest.skip("session-start.py not found")
    tree = ast.parse(read_file_content(hook))
    target = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "check_crm_health"),
        None,
    )
    assert target is not None, "check_crm_health not found in session-start.py"
    has_chmod_600 = any(
        isinstance(c, ast.Call)
        and isinstance(c.func, ast.Attribute) and c.func.attr == "chmod"
        and isinstance(c.func.value, ast.Name) and c.func.value.id == "os"
        and any(isinstance(a, ast.Constant) and a.value == 0o600 for a in c.args)
        for c in ast.walk(target)
    )
    assert has_chmod_600, (
        "check_crm_health writes .sessions/crm-health-cache.json but has no "
        "os.chmod(..., 0o600) — the cache would land 0o644 (SEC-006/F-H2 regression)"
    )
