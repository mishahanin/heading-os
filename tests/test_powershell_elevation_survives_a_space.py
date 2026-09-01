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


def _code() -> str:
    """The script with its comment lines removed.

    Every assertion here is a substring search over a file whose first seven
    lines are a comment explaining the defect, in the defect's own vocabulary.
    That comment names `$PSScriptRoot`, `-ArgumentList` and `-File`, so a
    substring search cannot tell the fix from the story about the fix. MEASURED
    2026-09-01 with the mutation harness: replacing the only real use,
    `Join-Path $PSScriptRoot ...`, with a hardcoded `C:\\Tools\\...` path left
    this file GREEN on the strength of line 4.

    PowerShell line comments start with `#`; this script has no block comments
    (`<# ... #>`) and the guard below keeps it that way, since one would let the
    same words back in through a form this strip does not handle.
    """
    return "\n".join(line for line in _source().splitlines()
                     if not line.lstrip().startswith("#"))


def test_the_script_uses_no_block_comment_form():
    """`_code()` strips `#` line comments only, so a `<# ... #>` block would
    smuggle the vocabulary back past every check below."""
    assert "<#" not in _source(), (
        "the script gained a PowerShell block comment; _code() cannot strip it "
        "and the substring assertions become readable-prose checks again"
    )


def test_the_script_is_still_here():
    assert SCRIPT.is_file(), "the guard points at a file that no longer exists"


def test_argument_list_is_not_an_unquoted_array():
    src = _code()
    array_form = re.search(r"-ArgumentList\s*@\(", src)
    assert array_form is None, (
        "-ArgumentList is an array again. Windows PowerShell joins array "
        "elements with spaces and quotes none of them, so a path containing a "
        "space breaks apart before the elevated child ever sees it."
    )


def test_the_file_path_is_quoted_in_the_argument_string():
    src = _code()
    assert re.search(r'-File\s+"\{0\}"', src) or re.search(r'-File\s+\\?"\$', src), (
        "the -File value is not wrapped in double quotes; a path with a space "
        "will still split"
    )


def test_the_target_is_resolved_beside_this_script():
    """`$PSScriptRoot`, not a hardcoded directory: the workspace moves.

    Asserted over the CODE, and on the assignment that builds `$target`. The
    old form searched the whole file, which the header comment satisfies.
    """
    code = _code()
    assert "$PSScriptRoot" in code, (
        "no executable line resolves $PSScriptRoot; the header comment naming "
        "it is not the resolution"
    )
    target = re.search(r"^\s*\$target\s*=\s*(.+)$", code, re.M)
    assert target, "the script no longer assigns $target"
    assert "$PSScriptRoot" in target.group(1), (
        f"$target is built from {target.group(1)!r} instead of $PSScriptRoot; a "
        "fixed directory breaks the moment the workspace moves"
    )
    assert not re.search(r"^\s*\$target\s*=\s*'[A-Za-z]:\\", code, re.M), (
        "$target is a hardcoded drive-letter path")


def test_a_missing_target_is_reported_before_the_uac_prompt():
    """Failing after elevation hides the error behind the prompt."""
    src = _code()
    assert "Test-Path" in src
    assert src.index("Test-Path") < src.index("Start-Process"), (
        "the existence check runs after the elevation, where its message is "
        "invisible to whoever clicked Yes"
    )


def test_the_flags_the_child_needs_are_all_still_passed():
    src = _code()   # `-File` also appears in the header comment
    for flag in ("-NoProfile", "-ExecutionPolicy", "Bypass", "-File"):
        assert flag in src, flag
