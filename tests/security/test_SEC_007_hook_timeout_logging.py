#!/usr/bin/env python3
"""SEC-007: a hook that times out must say so, not degrade in silence.

Vulnerability: a timeout caught and silently passed. A control whose failure is
indistinguishable from its success is not a control - the tree looks clean
because nothing ran, and nobody can tell.

This test was aimed at ONE file, `.claude/hooks/post-write-sanitize.py`, and it
walked that file's AST looking for an except handler whose type name contains
"Timeout". That hook stopped shelling out ("in-process instead of subprocess
fan-out", saving 150-200 ms per Write), so it has no such handler and has not
had one for some time. The loop found nothing, the body never ran, and the test
passed on zero assertions. Measured 2026-08-27: `grep -c Timeout` on that file
answers 0.

Rewritten to cover the surface the control is actually about - every hook that
waits on a subprocess - with a floor, so it cannot go quiet again the next time
a hook is rewritten. Two of the four handlers it found were silent
(`session-start.py`, `turn-check.py`); both now print one line to stderr.
"""

import ast

import pytest

HOOK_DIR_NAME = ".claude/hooks"

# Every way a handler can be heard. `raise` counts: re-raising is not silence.
_VOICES = ("stderr", "print(", "logging.", "_log(", "raise")


def _hook_files(hooks_dir):
    return sorted(p for p in hooks_dir.glob("*.py") if p.is_file())


def _read_hook(path):
    """Read one hook, or FAIL naming it. Never skip it.

    The walk and the read are two moments, and a file can go between them when
    several agents or pytest workers share one checkout. `read_sources` skips
    such a file with a warning, which is right for a scan: a file that is not
    there cannot violate anything.

    This is not a scan. Both callers below assert something about EVERY hook -
    the floor counts handlers across the whole surface, and the silence check
    claims no hook swallows a timeout. A skipped hook there is an unexamined
    timeout handler reported as a clean control, which is the exact defect
    SEC-007 exists to prevent. So: retry once, in case the miss was a rewrite
    window, then fail with the path.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        pass
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        pytest.fail(
            f"{path} vanished between the hook walk and the read. This control "
            f"cannot skip a hook and still say no hook swallows a timeout in "
            f"silence, so it stops here instead."
        )


def _timeout_handlers(tree, content):
    """Yield (lineno, handler_text) for every except that catches a timeout."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        caught = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
        names = []
        for item in caught:
            if isinstance(item, ast.Attribute):
                names.append(item.attr)
            elif isinstance(item, ast.Name):
                names.append(item.id)
        if not any("Timeout" in n for n in names):
            continue
        lines = content.split("\n")[node.lineno - 1:node.end_lineno]
        yield node.lineno, "\n".join(lines), node


def test_the_hook_surface_still_has_timeout_handlers_to_judge(hooks_dir):
    """The floor. Without it this file is green over an empty search again.

    It is not decoration: that is exactly how the original version of this test
    spent its life. It searched one file for a handler that had been deleted and
    reported nothing wrong, every run, for as long as it sat in the tree.
    """
    files = _hook_files(hooks_dir)
    assert len(files) >= 10, f"only {len(files)} hook(s) found in {hooks_dir}"

    total = 0
    for path in files:
        content = _read_hook(path)
        total += sum(1 for _ in _timeout_handlers(ast.parse(content), content))
    assert total >= 3, (
        f"only {total} timeout handler(s) across {len(files)} hooks. Either the "
        "hooks stopped waiting on subprocesses, or this scan stopped finding "
        "them. Both need a human to look."
    )


def test_no_hook_swallows_a_timeout_without_saying_so(hooks_dir):
    silent = []
    for path in _hook_files(hooks_dir):
        content = _read_hook(path)
        for lineno, text, node in _timeout_handlers(ast.parse(content), content):
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                silent.append(f"{path.name}:{lineno}: handler is a bare pass")
                continue
            if not any(v in text for v in _VOICES):
                silent.append(f"{path.name}:{lineno}: handler degrades in silence")

    assert not silent, (
        "a hook that times out must print one line before it degrades, or its "
        "failure looks exactly like a clean run:\n  " + "\n  ".join(silent)
    )


@pytest.mark.parametrize("hook", ["turn-check.py", "session-start.py"])
def test_the_two_hooks_that_used_to_be_silent_now_speak(hooks_dir, hook):
    """Named, because the general test above would pass again the moment
    someone removed these two lines and left the others in place."""
    path = hooks_dir / hook
    assert path.is_file(), f"{hook} is gone; this pin needs re-aiming"
    content = path.read_text(encoding="utf-8")
    found = list(_timeout_handlers(ast.parse(content), content))
    assert found, f"{hook} no longer catches a timeout at all"
    for lineno, text, _node in found:
        assert "stderr" in text, f"{hook}:{lineno} went quiet again"
