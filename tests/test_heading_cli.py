"""Tests for the F-10.1 `heading` CLI dispatcher (scripts/heading_cli.py).

Includes the both-ways smoke test: a representative script must produce the
same result run repo-relative and run through the CLI, so the plugin-native
path and the vendor-independent CLI surface cannot silently diverge.
"""

import subprocess
import sys
from pathlib import Path

from scripts.heading_cli import REGISTRY, _resolve, main

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "heading_cli.py"
TARGET = "scripts/utils/paths.py"  # read-only, no deps, prints the workspace root


def test_list_returns_zero(capsys):
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    for name in REGISTRY:
        assert name in out


def test_resolve_bare_name_goes_under_scripts():
    assert _resolve("paths.py", ROOT) == (ROOT / "scripts" / "paths.py").resolve()
    assert _resolve("utils/paths.py", ROOT) == (ROOT / "utils" / "paths.py").resolve()
    assert _resolve("scripts/utils/paths.py", ROOT) == (ROOT / "scripts" / "utils" / "paths.py").resolve()


def test_missing_target_exits_nonzero():
    assert main(["run", "scripts/does-not-exist.py"]) == 2


def test_both_ways_parity():
    """The representative script yields identical output both ways."""
    direct = subprocess.run(
        [sys.executable, str(ROOT / TARGET)], capture_output=True, text=True, cwd=ROOT
    )
    via_cli = subprocess.run(
        [sys.executable, str(CLI), "run", TARGET], capture_output=True, text=True, cwd=ROOT
    )
    assert direct.returncode == via_cli.returncode == 0
    assert direct.stdout.strip() == via_cli.stdout.strip()
    assert direct.stdout.strip() != ""
