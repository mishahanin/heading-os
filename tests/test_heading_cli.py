"""Tests for the F-10.1 `heading` CLI dispatcher (scripts/heading_cli.py).

Includes the both-ways smoke test: a representative script must produce the
same result run repo-relative and run through the CLI, so the plugin-native
path and the vendor-independent CLI surface cannot silently diverge.
"""

import subprocess
import sys
from pathlib import Path

import pytest

import scripts.heading_cli as heading_cli
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


class _Done:
    returncode = 0


def _capture_dispatch(monkeypatch):
    """Record the argv `_dispatch` builds instead of spawning the child."""
    captured = {}

    def _fake(argv, *_a, **_k):
        captured["argv"] = list(argv)
        return _Done()

    monkeypatch.setattr(heading_cli.subprocess, "run", _fake)
    return captured


def test_run_passes_its_arguments_through(monkeypatch):
    """The dispatcher's one job, and nothing in the tree measured it.

    Measured 2026-09-01: `heading run <script> <args>` could be changed to hand
    the child an EMPTY argument list, and `_dispatch` itself could drop `*args`,
    with 187 tests green across this file and every other file that names
    `heading_cli`. `heading run scripts/x.py --dry-run` would then have run
    without `--dry-run` and reported the exit code of the wrong invocation.
    `test_both_ways_parity` cannot see it: its representative script takes no
    arguments, so parity is asserted only for the zero-argument case.
    """
    captured = _capture_dispatch(monkeypatch)
    assert main(["run", TARGET, "--flag", "value"]) == 0
    assert captured["argv"][0] == sys.executable
    assert captured["argv"][1] == str((ROOT / TARGET).resolve())
    assert captured["argv"][2:] == ["--flag", "value"]


def test_a_named_shortcut_passes_an_option_looking_argument_through(monkeypatch):
    """A live CLI defect, not only a missing test.

    `nargs=argparse.REMAINDER` as the FIRST positional of a subparser matches
    ZERO arguments when the next token starts with `-`, so argparse read the
    token as an option of its own and refused. Measured 2026-09-01:
    `heading classification --json` exited 2 with "unrecognized arguments:
    --json" while `heading run scripts/classification-health.py --json` printed
    the JSON. Every flag both registry targets accept is option-looking, so no
    flag was reachable through a shortcut at all.
    """
    captured = _capture_dispatch(monkeypatch)
    assert main(["classification", "--json"]) == 0
    assert captured["argv"][1] == str((ROOT / REGISTRY["classification"]).resolve())
    assert captured["argv"][2:] == ["--json"]


def test_a_named_shortcut_flag_really_reaches_the_target_script():
    """The same claim through the real process, because the assertion above is
    about the argv this module builds and not about what the child then did."""
    proc = subprocess.run(
        [sys.executable, str(CLI), "classification", "--json"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.lstrip().startswith("{"), proc.stdout[:200] + proc.stderr[:400]


def test_a_bare_shortcut_and_its_help_still_go_through_argparse(capsys):
    """The interception must not swallow the subcommand's own `--help`."""
    with pytest.raises(SystemExit) as exc:
        main(["classification", "--help"])
    assert exc.value.code == 0
    assert "usage: heading classification" in capsys.readouterr().out


def test_console_entry_point_installed():
    """CAP-4 real gate: the installed `heading` console script runs.

    The in-tree `import scripts.heading_cli` passes even without the package
    installed (pytest `pythonpath = ["."]`), so it does NOT gate CAP-4. This
    exercises the actual entry point placed on PATH by the build backend.
    """
    heading_bin = Path(sys.executable).parent / "heading"
    if heading_bin.exists():
        proc = subprocess.run([str(heading_bin), "list"], capture_output=True, text=True, cwd=ROOT)
    else:  # not directly on the venv bin dir; drive the same entry point through uv
        proc = subprocess.run(
            ["uv", "run", "heading", "list"], capture_output=True, text=True, cwd=ROOT
        )
    assert proc.returncode == 0
    assert "health" in proc.stdout and "classification" in proc.stdout


def test_pyproject_declares_console_script():
    """The build config that makes the entry point reachable is present (an
    installed package with the `heading` script), not a uv virtual project."""
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "build-system" in data  # not a virtual project
    assert data["project"]["scripts"]["heading"] == "scripts.heading_cli:main"
    assert data["tool"]["uv"].get("package") is True
