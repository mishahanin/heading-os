"""The elevation wrapper must pass a path containing a space.

Found by the 2026-08-23 engine audit.

`scripts/_elevate-and-delete-schtasks.ps1` built its child arguments as a
PowerShell ARRAY:

    Start-Process -FilePath powershell -Verb RunAs -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $target)

Windows PowerShell joins array elements with a single space and quotes NONE of
them. So a `$PSScriptRoot` under `C:\\Users\\Some Name\\...` arrived at the
elevated child split across several arguments, and `-File` got only the first
fragment. The child then failed behind the UAC prompt, which is the one place a
Windows error message is not visible to the person who clicked Yes.

The file cannot run on this machine, so the guard reads it. That is a real
limit and it is stated rather than papered over: what can be checked is the
argument SHAPE, which is exactly what was wrong.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "_elevate-and-delete-schtasks.ps1"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_the_script_is_still_here():
    assert SCRIPT.is_file(), "the guard points at a file that no longer exists"


def test_argument_list_is_not_an_unquoted_array():
    src = _source()
    array_form = re.search(r"-ArgumentList\s*@\(", src)
    assert array_form is None, (
        "-ArgumentList is an array again. Windows PowerShell joins array "
        "elements with spaces and quotes none of them, so a path containing a "
        "space breaks apart before the elevated child ever sees it."
    )


def test_the_file_path_is_quoted_in_the_argument_string():
    src = _source()
    assert re.search(r'-File\s+"\{0\}"', src) or re.search(r'-File\s+\\?"\$', src), (
        "the -File value is not wrapped in double quotes; a path with a space "
        "will still split"
    )


def test_the_target_is_resolved_beside_this_script():
    """`$PSScriptRoot`, not a hardcoded directory: the workspace moves."""
    assert "$PSScriptRoot" in _source()


def test_a_missing_target_is_reported_before_the_uac_prompt():
    """Failing after elevation hides the error behind the prompt."""
    src = _source()
    assert "Test-Path" in src
    assert src.index("Test-Path") < src.index("Start-Process"), (
        "the existence check runs after the elevation, where its message is "
        "invisible to whoever clicked Yes"
    )


def test_the_flags_the_child_needs_are_all_still_passed():
    src = _source()
    for flag in ("-NoProfile", "-ExecutionPolicy", "Bypass", "-File"):
        assert flag in src, flag
