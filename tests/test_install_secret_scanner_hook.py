"""install-hooks.py appends the secret scanner to a pre-existing pre-commit hook.

The append path is the one that matters here. This workspace installs the
`pre-commit` framework first, so by the time `install-hooks.py` runs there is
almost always an existing `.git/hooks/pre-commit`, and the append branch is the
branch that executes.
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


def test_append_keeps_the_marker_and_stays_valid_shell(tmp_path):
    """The 2026-08-23 defect: the append used
    ``PRE_COMMIT_HOOK.lstrip("#!/bin/sh\\n")``. ``str.lstrip`` takes a SET of
    characters, not a prefix, and that set is ``{#,!,/,b,i,n,s,h,\\n}`` -- so it
    ate the shebang, the newline, AND the leading ``#`` of the marker line,
    stopping only at the space. Two consequences: the marker was destroyed, so
    every later run appended a second copy; and the appended block opened with a
    bare word, which the shell reads as a command that does not exist.
    """
    hooks = _hooks_dir(tmp_path)
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\n# pre-existing framework hook\nexit 0\n",
                    encoding="utf-8")

    assert mod.install_pre_commit(hooks)
    text = hook.read_text(encoding="utf-8")

    assert mod.HOOK_MARKER in text, "the append destroyed its own marker"
    # the shebang belongs to the ORIGINAL hook only -- the appended block must
    # not carry a second one, and must not open with a bare word either.
    appended = text.split("# pre-existing framework hook", 1)[1]
    assert "#!/bin/sh" not in appended, "a second shebang mid-file"
    for line in appended.splitlines():
        if line.strip():
            assert line.startswith("#") or line[0] not in " \t", \
                f"appended block opens with a non-comment indented word: {line!r}"
            break


def test_append_is_idempotent(tmp_path):
    """A destroyed marker made the check at the top of install_pre_commit miss,
    so a second run appended the scanner all over again."""
    hooks = _hooks_dir(tmp_path)
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    mod.install_pre_commit(hooks)
    first = hook.read_text(encoding="utf-8")
    mod.install_pre_commit(hooks)
    second = hook.read_text(encoding="utf-8")

    assert first == second, "a second install appended a duplicate scanner"
    assert second.count(mod.HOOK_MARKER) == 1


def test_fresh_install_writes_the_whole_hook(tmp_path):
    hooks = _hooks_dir(tmp_path)
    assert mod.install_pre_commit(hooks)
    text = (hooks / "pre-commit").read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh")
    assert mod.HOOK_MARKER in text
    assert (hooks / "pre-commit").stat().st_mode & 0o111
