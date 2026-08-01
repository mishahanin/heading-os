"""The depth floor must not depend on how a path is spelled.

Regression for a /scrutinize execution pass, 2026-08-01. `_normalise` handled
backslashes and a leading `./` and nothing else, so the SAME enforcement-surface
file reached three different answers:

    depth-gate.py .claude/hooks/_dispatch.py          -> exit 1  (refused)
    depth-gate.py "$PWD/.claude/hooks/_dispatch.py"   -> exit 0  (allowed)
    classify(["scripts/./push-all.py"])               -> "standard"

pre-commit feeds git-relative names, so the wired gate was never bypassed. The
advisory CLI the operator reads BEFORE starting work was, and a floor whose
answer changes with the spelling is the dilution `slice_depth`'s own docstring
forbids.

These live outside `tests/contract/2026-08-01-depth-calibration/` on purpose:
that contract is frozen to its slice, and a later regression belongs beside it,
not inside it.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.utils.slice_depth import classify

_ROOT = Path(__file__).resolve().parent.parent
_GATE = _ROOT / "scripts" / "depth-gate.py"

_SURFACE = ".claude/hooks/_dispatch.py"


@pytest.mark.parametrize("spelling", [
    _SURFACE,
    f"./{_SURFACE}",
    ".claude/hooks/../hooks/_dispatch.py",
    ".claude/./hooks/_dispatch.py",
    ".claude\\hooks\\_dispatch.py",
    "scripts/./push-all.py",
    "scripts/../scripts/push-all.py",
])
def test_every_spelling_of_a_surface_path_is_full_depth(spelling):
    assert classify([spelling])["depth"] == "full", spelling


def test_an_absolute_path_under_the_root_is_full_depth():
    assert classify([str(_ROOT / _SURFACE)], root=_ROOT)["depth"] == "full"


def test_an_absolute_path_resolves_without_the_caller_passing_a_root():
    """The floor may not depend on every caller remembering the argument."""
    assert classify([str(_ROOT / _SURFACE)])["depth"] == "full"


def test_an_absolute_path_outside_the_root_is_not_forced_full(tmp_path):
    """`/tmp/scripts/push-all.py` is a different file that happens to rhyme."""
    assert classify([str(tmp_path / "scripts" / "push-all.py")],
                    root=_ROOT)["depth"] == "standard"


def test_dilution_still_fails_with_mixed_spellings():
    paths = [f"docs/page-{i}.md" for i in range(20)] + [f"./{_SURFACE}"]
    assert classify(paths)["depth"] == "full"


def test_a_frozen_path_matches_whatever_shape_the_change_uses():
    manifest = {"files": {"scripts/utils/markdown.py": "deadbeef"}}
    assert classify(["scripts/./utils/markdown.py"],
                    freeze=manifest)["depth"] == "full"


@pytest.mark.parametrize("spelling", [
    _SURFACE,
    f"./{_SURFACE}",
    ".claude/hooks/../hooks/_dispatch.py",
])
def test_the_gate_refuses_every_relative_spelling(tmp_path, spelling):
    root = tmp_path / "ws"
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("probe", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_GATE), spelling],
        capture_output=True, text=True, cwd=str(_ROOT), timeout=120,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "WORKSPACE_ROOT": str(root),
             "WORKSPACE_LOG_DIR": str(tmp_path / "logs")},
    )
    assert proc.returncode != 0, f"{spelling} passed the gate: {proc.stdout}"


def test_the_gate_refuses_an_absolute_spelling(tmp_path):
    root = tmp_path / "ws"
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("probe", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_GATE), str(root / _SURFACE)],
        capture_output=True, text=True, cwd=str(_ROOT), timeout=120,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
             "WORKSPACE_ROOT": str(root),
             "WORKSPACE_LOG_DIR": str(tmp_path / "logs")},
    )
    assert proc.returncode != 0, f"absolute path passed the gate: {proc.stdout}"
