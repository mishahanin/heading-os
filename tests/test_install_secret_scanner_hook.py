"""install-hooks.py merges the secret scanner into a pre-existing pre-commit hook.

The merge path is the one that matters here. This workspace installs the
`pre-commit` framework first, so by the time `install-hooks.py` runs there is
almost always an existing `.git/hooks/pre-commit`, and the merge branch is the
branch that executes.

It used to APPEND, and this file was written for that. On 2026-08-25 the
installer changed to PREPEND, because an existing hook ending in `exit 0` - the
ordinary shape - made every appended line dead while the marker sat in the file
and `--check` certified a scanner that could not run. The tests here kept the
old vocabulary, and worse, kept slicing the file at the pre-existing comment and
inspecting the half AFTER it. MEASURED 2026-09-01: that slice is `'\\nexit 0\\n'`,
the tail of the original hook, so the "no second shebang" and "does not open
with an indented bare word" assertions were reading two lines the installer
never wrote. They now read the half BEFORE the marker comment, which is the
inserted block.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "install_hooks", ROOT / "scripts" / "install-hooks.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules["install_hooks"] = mod
_spec.loader.exec_module(mod)


def _hooks_dir(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    d = tmp_path / ".git" / "hooks"
    d.mkdir(parents=True, exist_ok=True)
    return d


ORIGINAL_LINE = "# pre-existing framework hook"


def test_merge_keeps_the_marker_and_stays_valid_shell(tmp_path):
    """The 2026-08-23 defect: the merge used
    ``PRE_COMMIT_HOOK.lstrip("#!/bin/sh\\n")``. ``str.lstrip`` takes a SET of
    characters, not a prefix, and that set is ``{#,!,/,b,i,n,s,h,\\n}`` -- so it
    ate the shebang, the newline, AND the leading ``#`` of the marker line,
    stopping only at the space. Two consequences: the marker was destroyed, so
    every later run merged a second copy; and the inserted block opened with a
    bare word, which the shell reads as a command that does not exist.
    """
    hooks = _hooks_dir(tmp_path)
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\n{ORIGINAL_LINE}\nexit 0\n", encoding="utf-8")

    assert mod.install_pre_commit(hooks)
    text = hook.read_text(encoding="utf-8")

    assert mod.HOOK_MARKER in text, "the merge destroyed its own marker"
    # The block goes in ABOVE the original hook's body, so it is the half BEFORE
    # the pre-existing line. Slicing the other way inspects the original hook's
    # own tail and asserts nothing about what was written.
    inserted, sep, _rest = text.partition(ORIGINAL_LINE)
    assert sep, "the original hook's body did not survive the merge"
    assert text.index(mod.HOOK_MARKER) < text.index(ORIGINAL_LINE), (
        "the scanner was appended below the original hook, whose trailing "
        "`exit 0` makes every line of it dead while the marker still reads as "
        "installed"
    )
    assert inserted.count("#!/bin/sh") == 1, (
        f"the inserted block carries its own shebang: {inserted[:200]!r}")
    body = inserted.split("\n", 1)[1]  # drop the one legitimate shebang line
    for line in body.splitlines():
        if line.strip():
            assert line.startswith("#") or line[0] not in " \t", \
                f"inserted block opens with a non-comment indented word: {line!r}"
            break


def test_merge_is_idempotent(tmp_path):
    """A destroyed marker made the check at the top of install_pre_commit miss,
    so a second run merged the scanner in all over again."""
    hooks = _hooks_dir(tmp_path)
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    mod.install_pre_commit(hooks)
    first = hook.read_text(encoding="utf-8")
    mod.install_pre_commit(hooks)
    second = hook.read_text(encoding="utf-8")

    assert first == second, "a second install merged in a duplicate scanner"
    assert second.count(mod.HOOK_MARKER) == 1


def test_fresh_install_writes_the_whole_hook(tmp_path):
    hooks = _hooks_dir(tmp_path)
    assert mod.install_pre_commit(hooks)
    text = (hooks / "pre-commit").read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh")
    assert mod.HOOK_MARKER in text
    assert (hooks / "pre-commit").stat().st_mode & 0o111
