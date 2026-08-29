"""No test may post to `/return` without patching `webbrowser.open`.

`POST /return` opens a real browser window. `scripts/bridge_daemon/app.py` calls
`webbrowser.open(url, new=0)`, which on this machine launches Brave at
`http://127.0.0.1:<port>/#/<page>`. There is no dry-run flag and no test mode.

On 2026-08-29 a new test posted to `/return` once per entry in the frontend's
`ROUTES` table, eighteen of them, with no patch. Every full-suite run therefore
opened eighteen browser windows on the operator's desktop. The suite was run
eight times before the OPERATOR noticed and said stop. Nothing in the suite
could have noticed: eighteen passing assertions and eighteen windows look
identical from inside pytest.

Every `/return` test in `tests/bridge/test_endpoints.py` already wrapped the call
in `patch("webbrowser.open")`. The new one was written without reading them, so
prose in a docstring was never going to prevent the next one either.

This guard is mechanical. It parses every test module under `tests/`, finds each
call that posts to `/return`, and requires a `webbrowser.open` patch to be in
scope at that point: a `with patch("webbrowser.open")`, a decorator, or a
`monkeypatch.setattr` on the same name.

Scope, stated honestly: this checks LEXICAL scope inside the test function. A
patch installed by a fixture in another file is not visible here and would be
reported. That is the safe direction. A false report costs one explicit
`patch(...)` line; a miss costs the operator a screen full of windows.
"""
from __future__ import annotations

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = TESTS_ROOT.parent

# The endpoint that opens a window. Others can be added as they appear; each
# needs the same treatment and none of them should be discovered the way this
# one was.
BROWSER_OPENING_ROUTES = ("/return",)

_PATCH_TARGETS = ("webbrowser.open", "webbrowser")


def _test_files():
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _is_return_post(node: ast.Call) -> bool:
    """A `.post("/return", ...)` call, however the client is named."""
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "post":
        return False
    if not node.args:
        return False
    first = node.args[0]
    return (isinstance(first, ast.Constant) and isinstance(first.value, str)
            and first.value in BROWSER_OPENING_ROUTES)


def _patches_the_browser(node: ast.AST) -> bool:
    """True when this node installs a `webbrowser.open` patch."""
    if isinstance(node, ast.Call):
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name in ("patch", "setattr", "patch_object", "object"):
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                        and any(t in arg.value for t in _PATCH_TARGETS)):
                    return True
                if (isinstance(arg, ast.Attribute) and arg.attr == "open"
                        and isinstance(arg.value, ast.Name)
                        and arg.value.id == "webbrowser"):
                    return True
                if isinstance(arg, ast.Name) and arg.id == "webbrowser":
                    return True
    return False


def _guarded_calls(tree: ast.AST) -> tuple[list[int], list[int]]:
    """Line numbers of `/return` posts, split into (guarded, unguarded).

    A post is guarded when a `webbrowser.open` patch appears anywhere in the
    enclosing function: a `with` item, a decorator, or a bare statement such as
    `monkeypatch.setattr(webbrowser, "open", ...)`.
    """
    guarded: list[int] = []
    unguarded: list[int] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        posts = [n.lineno for n in ast.walk(func)
                 if isinstance(n, ast.Call) and _is_return_post(n)]
        if not posts:
            continue
        patched = any(_patches_the_browser(n) for n in ast.walk(func))
        (guarded if patched else unguarded).extend(posts)
    return guarded, unguarded


def test_no_test_posts_to_return_without_patching_the_browser():
    violations = []
    for path in _test_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        _ok, bad = _guarded_calls(tree)
        rel = path.relative_to(REPO_ROOT).as_posix()
        violations += [f"{rel}:{line}" for line in bad]

    assert not violations, (
        f"{len(violations)} test call(s) post to /return without patching "
        f"`webbrowser.open`. That endpoint opens a REAL browser window, one per "
        f"call, on the operator's desktop. Wrap the call:\n"
        f"    with patch(\"webbrowser.open\"):\n        client.post(\"/return\", ...)\n  "
        + "\n  ".join(violations))


def test_the_guard_recognises_both_shapes():
    """Pin the detector against the defect and against its fix.

    A guard that silently matched nothing would pass forever, which is exactly
    how the thing it checks for got in.
    """
    bad = ast.parse(
        "def test_x(c):\n"
        "    c.post('/return', json={})\n")
    assert _guarded_calls(bad) == ([], [2])

    with_stmt = ast.parse(
        "def test_x(c):\n"
        "    with patch('webbrowser.open'):\n"
        "        c.post('/return', json={})\n")
    assert _guarded_calls(with_stmt) == ([3], [])

    decorated = ast.parse(
        "@patch('webbrowser.open')\n"
        "def test_x(m, c):\n"
        "    c.post('/return', json={})\n")
    assert _guarded_calls(decorated) == ([3], [])

    monkey = ast.parse(
        "def test_x(c, monkeypatch):\n"
        "    monkeypatch.setattr(webbrowser, 'open', lambda *a, **k: None)\n"
        "    c.post('/return', json={})\n")
    assert _guarded_calls(monkey) == ([3], [])

    # A post to some other route is not this guard's business.
    other = ast.parse(
        "def test_x(c):\n"
        "    c.post('/telemetry/page-view', json={})\n")
    assert _guarded_calls(other) == ([], [])


def test_the_scan_actually_finds_the_return_tests():
    """Anti-vacuity. If the walk stopped matching, the guard above would report
    a clean tree over nothing at all, which is how it would rot."""
    total = 0
    for path in _test_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        ok, bad = _guarded_calls(tree)
        total += len(ok) + len(bad)
    assert total >= 5, f"only {total} /return call sites reached the guard"
