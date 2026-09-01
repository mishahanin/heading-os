#!/usr/bin/env python3
"""SEC-006: Verify .sessions/ directories and files have restricted permissions.

Behavioral (live os.stat) + AST guard. The live check walks the actual .sessions/
tree; the AST check confirms the creation calls pass an integer `mode=` granting
no group or other bits, so new token stores are also locked down.

That second sentence read "include mode=0o700" until 2026-09-01 while the code
checked only that the KEYWORD was present. Measured that day, `mode=0o755` on
`gmail_auth`'s token directory left every test in this file green.

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
    """Parse AST: every os.makedirs call must pass a mode that locks the dir down.

    The VALUE, not just the keyword. Until 2026-09-01 this asked only whether a
    `mode=` keyword was present, while the module docstring three screens up
    said it "confirms the creation calls include mode=0o700". MEASURED that day
    by changing `gmail_auth`'s call to `mode=0o755`: every SEC-006 test stayed
    green over a credential directory readable and traversable by every account
    on the machine. That is `.claude/rules/scope-claims.md` in a security gate,
    and the same "asked about the call, not the value" shape the workspace has
    hit before.

    The invariant asserted is that no group or other bit is granted
    (`mode & 0o077 == 0`), rather than exactly 0o700, because 0o700 is not the
    only safe answer and pinning one literal would refuse a correct 0o500. A
    non-literal mode is a violation too: this reader cannot settle it, and a
    security gate that cannot settle a value must not pass it.
    """
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
                mode_kw = next((kw for kw in node.keywords if kw.arg == "mode"), None)
                if mode_kw is None:
                    violations.append(
                        f"Line {node.lineno}: os.makedirs() without mode= parameter"
                    )
                elif not (isinstance(mode_kw.value, ast.Constant)
                          and isinstance(mode_kw.value.value, int)
                          and not isinstance(mode_kw.value.value, bool)):
                    violations.append(
                        f"Line {node.lineno}: os.makedirs() mode= is not an integer "
                        f"literal ({ast.unparse(mode_kw.value)}), so this guard "
                        f"cannot establish it locks the directory down"
                    )
                elif mode_kw.value.value & 0o077:
                    violations.append(
                        f"Line {node.lineno}: os.makedirs(mode="
                        f"{oct(mode_kw.value.value)}) grants group or other bits "
                        f"on a credential directory; expected owner-only"
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


@pytest.mark.parametrize("mode,wanted", [
    ("0o700", []), ("0o500", []), ("0o600", []),
    ("0o755", ["grants group or other bits"]),
    ("0o777", ["grants group or other bits"]),
    ("0o710", ["grants group or other bits"]),
    ("0o701", ["grants group or other bits"]),
    ("os.environ.get('M')", ["not an integer literal"]),
])
def test_the_makedirs_reader_refuses_a_permissive_mode(tmp_path, mode, wanted):
    """The case ON the line for the guard above.

    Nothing ever made `_check_makedirs_has_mode` report a violation over a mode
    VALUE, which is how `mode=0o755` on the OAuth token directory passed the
    whole SEC-006 file. Each case here is a mode a real edit could introduce.
    """
    src = tmp_path / "sample.py"
    src.write_text(f"import os\nos.makedirs('/tmp/x', mode={mode}, exist_ok=True)\n",
                   encoding="utf-8")

    violations = _check_makedirs_has_mode(src)

    if not wanted:
        assert violations == [], violations
    else:
        assert len(violations) == 1, violations
        assert wanted[0] in violations[0], violations


def test_the_makedirs_reader_still_catches_a_missing_mode(tmp_path):
    """Anchor: the original check must survive the widening."""
    src = tmp_path / "sample.py"
    src.write_text("import os\nos.makedirs('/tmp/x', exist_ok=True)\n",
                   encoding="utf-8")

    violations = _check_makedirs_has_mode(src)

    assert len(violations) == 1, violations
    assert "without mode= parameter" in violations[0]


def test_the_makedirs_reader_finds_the_calls_it_is_pointed_at(scripts_dir):
    """A reader that matches no `os.makedirs` at all reports no violation
    either, and every parametrized case above would then be measuring a
    hand-written fixture and nothing in the tree."""
    found = 0
    for script_name in OAUTH_SCRIPTS:
        tree = ast.parse(read_file_content(scripts_dir / script_name))
        found += sum(
            1 for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "makedirs" and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "os")
    assert found >= 1, (
        "no os.makedirs call in any OAUTH_SCRIPTS entry; "
        "test_oauth_script_makedirs_has_mode asserts nothing")


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
