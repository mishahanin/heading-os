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

OAUTH_SCRIPTS = [
    "google-contacts.py",
    "gmail-reader.py",
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


def _check_token_file_chmod(file_path: Path) -> list[str]:
    """Parse AST: after any open(..., 'w') write to a token/session path,
    os.chmod(..., 0o600) must be called in the same function body."""
    content = read_file_content(file_path)
    tree = ast.parse(content)
    violations = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Collect all open() calls that write to a token/session path
        writes_token = False
        has_chmod_600 = False
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func = child.func
                # Detect: open(TOKEN_PATH, "w") or open(TOKEN_PATH, "w", ...)
                if isinstance(func, ast.Name) and func.id == "open":
                    args = child.args
                    if len(args) >= 2:
                        mode_arg = args[1]
                        if isinstance(mode_arg, ast.Constant) and "w" in str(mode_arg.value):
                            writes_token = True
                # Detect: os.chmod(..., 0o600)
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "chmod"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "os"
                ):
                    # Check the mode argument is 0o600 (384 decimal)
                    for arg in child.args:
                        if isinstance(arg, ast.Constant) and arg.value == 0o600:
                            has_chmod_600 = True

        if writes_token and not has_chmod_600:
            violations.append(
                f"Function '{node.name}' (line {node.lineno}): writes token file "
                "but has no os.chmod(..., 0o600) call"
            )
    return violations


@pytest.mark.parametrize("script_name", OAUTH_SCRIPTS)
def test_oauth_script_makedirs_has_mode(scripts_dir, script_name):
    """OAuth scripts must call os.makedirs with an explicit mode= argument."""
    path = scripts_dir / script_name
    if not path.exists():
        pytest.skip(f"{script_name} not found")
    violations = _check_makedirs_has_mode(path)
    assert not violations, (
        f"{script_name}: os.makedirs calls without mode=0o700:\n"
        + "\n".join(violations)
    )


@pytest.mark.parametrize("script_name", OAUTH_SCRIPTS)
def test_oauth_script_token_file_chmod(scripts_dir, script_name):
    """OAuth scripts must call os.chmod(..., 0o600) after writing any token file."""
    path = scripts_dir / script_name
    if not path.exists():
        pytest.skip(f"{script_name} not found")
    violations = _check_token_file_chmod(path)
    assert not violations, (
        f"{script_name}: token-file write without os.chmod(..., 0o600):\n"
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
