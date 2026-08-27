#!/usr/bin/env python3
"""SEC-010: the sentinel state file must be replaced, never truncated in place.

Until 2026-08-27 the whole control was two substring scans of a 3,000-line
file: `"os.replace(" in content` and `".tmp" in content or "with_suffix" in
content`. Both are satisfied by a file that mentions either anywhere, including
in a comment, and neither can tell a real atomic write from `open(path, "w")`.

The one runtime test that claimed to cover it,
`tests/integration/test_sentinel_components.py::test_state_manager_save_is_atomic`,
globbed `state.json.*` for leftover tempfiles while
`Path("state.json").with_suffix(".tmp")` produces `state.tmp` - a pattern that
could never match what it was looking for - and never failed the write, so the
property the control exists to buy was untested.

The BEHAVIOURAL proof lives in
`tests/test_a_shutdown_that_took_thirty_seconds.py`: it fails `os.replace` and
reads the previous state back byte for byte. This file keeps the structural
claim, now made on the AST of the one method that writes.
"""

import ast
from pathlib import Path

from tests.security.conftest import read_file_content

BEHAVIOURAL = (Path(__file__).resolve().parent.parent
               / "test_a_shutdown_that_took_thirty_seconds.py")


def test_the_state_writer_replaces_and_never_truncates_in_place(scripts_dir):
    """Scoped to `StateManager.save`, not to the file.

    The old scan asked whether the string "os.replace(" appears in sentinel.py.
    It does, in several places. What matters is whether the method that writes
    the state uses it, and whether it opens the LIVE path for writing.
    """
    tree = ast.parse(read_file_content(scripts_dir / "sentinel.py"))
    state_manager = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.ClassDef) and n.name == "StateManager"), None)
    assert state_manager is not None, "StateManager is gone from sentinel.py"
    save = next((n for n in state_manager.body
                 if isinstance(n, ast.FunctionDef) and n.name == "save"), None)
    assert save is not None, "StateManager.save is gone"

    calls = [n for n in ast.walk(save) if isinstance(n, ast.Call)]
    replaces = [c for c in calls
                if isinstance(c.func, ast.Attribute) and c.func.attr == "replace"]
    assert replaces, "StateManager.save does not call os.replace; the write is not atomic"

    # The write target must be the TEMPFILE. `open(self.path, "w")` is
    # O_WRONLY|O_CREAT|O_TRUNC: the live state is empty before a single byte of
    # the new content is written, and every reader treats unparseable state as
    # "no state".
    opens = [c for c in calls if isinstance(c.func, ast.Name) and c.func.id == "open"]
    for call in opens:
        target = call.args[0] if call.args else None
        mode = call.args[1] if len(call.args) > 1 else None
        writes = isinstance(mode, ast.Constant) and "w" in str(mode.value)
        names_live_path = (isinstance(target, ast.Attribute) and target.attr == "path")
        assert not (writes and names_live_path), (
            "StateManager.save opens the live state file for writing; write the "
            "tempfile and os.replace it")


def test_the_behavioural_proof_fails_the_replace_and_reads_the_old_state_back():
    """A structural control must name where its behavioural proof lives.

    Without this, deleting the behavioural file leaves SEC-010 green over an AST
    shape again, which is the state this rewrite is correcting.
    """
    assert BEHAVIOURAL.is_file(), f"{BEHAVIOURAL.name} is missing"
    text = BEHAVIOURAL.read_text(encoding="utf-8")
    assert "No space left on device" in text and 'monkeypatch.setattr(sen.os, "replace"' in text, (
        "the behavioural test no longer fails the replace, so it cannot tell an "
        "atomic write from a truncating one")
    assert "state.tmp" in text, (
        "the leftover check no longer names the tempfile this writer actually "
        "produces; the old one globbed state.json.* and matched nothing")
