"""`canopus where` must never contradict itself about where you are.

Found late in the canopus-skill slice, by probing the seam rather than by
reading it. A slice whose label is the empty string made the header print "no
slice open" three lines above the build step. Both lines came from the same
payload; only the header consulted the label's truthiness instead of the step,
which is the authoritative field.

The bug matters more than its reachability. This command exists so an operator
who has been away for a week can trust one page about where he is, so an output
that disagrees with itself is worse than no output: it is the failure the whole
display was built to prevent, printed by the display itself.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CLI = _ROOT / "scripts" / "canopus.py"


@pytest.fixture
def frozen_tree(tmp_path):
    """A tree `where` accepts, carrying a freeze under the given label."""
    made = []

    def build(label):
        # Indexed, never derived from the label: `tmp_path / ""` is tmp_path
        # itself, so every empty-ish label collided on one directory.
        made.append(label)
        root = tmp_path / f"tree{len(made)}"
        (root / "scripts").mkdir(parents=True)
        (root / "scripts" / "run-tests.py").write_text("# gate\n", encoding="utf-8")
        contract = root / "tests" / "contract" / "s"
        contract.mkdir(parents=True)
        (contract / "test_c.py").write_text("def test_a():\n    assert 1\n", encoding="utf-8")

        from scripts.utils.canopus_freeze import build_manifest, write_freeze

        write_freeze(root, build_manifest(
            [contract], root, label=label, frozen_at="2026-08-02T00:00:00+00:00"))
        return root
    return build


def _where(root, *args):
    proc = subprocess.run([sys.executable, str(_CLI), "--root", str(root), "where", *args],
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def test_a_freeze_with_an_empty_label_does_not_read_as_no_slice(frozen_tree):
    root = frozen_tree("")
    out = _where(root)
    assert "no slice open" not in out, (
        "the header called it closed while the body reported the build step:\n" + out)
    assert "Step 5 of 7" in out


def test_the_header_and_the_step_never_disagree(frozen_tree):
    """The general property, not the one label that exposed it."""
    for label in ("", "   ", "0", "a-slice"):
        root = frozen_tree(label)
        out = _where(root)
        closed = "no slice open" in out
        payload = json.loads(_where(root, "--json"))
        assert closed == (payload["step"] == 0), f"label {label!r} disagreed:\n{out}"
