"""A version guard that checked three markdown files and not the lockfile.

`scripts/check-version-sync.py` treats `pyproject.toml` as the source of truth
and asserted README, CHANGELOG and ROADMAP agreed with it. `uv.lock` carries
the same version for this package and was never read.

MEASURED 2026-09-03 on `main`:

    pyproject.toml   version = "0.14.0"
    uv.lock          [[package]] name = "heading-os-engine"
                     version = "0.13.0"

    $ python scripts/check-version-sync.py
    Version in sync: 0.14.0 (README, CHANGELOG, ROADMAP, pyproject agree)
    $ echo $?
    0

A full minor release shipped over that drift and the guard reported sync. The
operational cost was not cosmetic: step 4 of the YARD bootstrap runs `uv sync`,
which rewrites the stale lock version on the spot, so every freshly created
worktree opened with `M uv.lock` already in `git status` and its operator had to
work out whether that edit was theirs.

Two things had to change together, and this file asserts both. The script had to
learn the fourth surface, and `.pre-commit-config.yaml` had to add `uv.lock` to
the hook's `files:` pattern -- without that, a commit touching only the lock
never fires the hook at all, which is how the drift survived in the first place.

The name is read from `project.name`, never hardcoded. A hardcoded
`heading-os-engine` would keep passing its own tests forever while silently
matching no entry the day the project is renamed.

Run: python3 -m pytest tests/test_a_version_gate_that_never_read_the_lockfile.py
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "scripts" / "check-version-sync.py"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


vsync = _load("version_sync_lockfile_under_test", "scripts/check-version-sync.py")


# ============================================================
# A scratch workspace, so nothing here reads the real repository
# ============================================================

def _write_root(tmp_path: Path, *, pyproject: str, lock: str,
                version: str = "1.2.3") -> Path:
    """A minimal but COMPLETE workspace: all four surfaces plus the truth.

    The three markdown files are always in sync at `version`, so any failure
    the tests below observe is attributable to the lock and to nothing else.
    """
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    (tmp_path / "uv.lock").write_text(lock, encoding="utf-8")
    (tmp_path / "README.md").write_text(
        f"# P\n\n## Status\n\nHEADING OS is `v{version}`.\n", encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [Unreleased]\n\n## [{version}] - 2026-09-03\n",
        encoding="utf-8")
    (tmp_path / "ROADMAP.md").write_text(
        f"# Roadmap\n\nHEADING OS is `v{version}` today.\n", encoding="utf-8")
    return tmp_path


def _pyproject(name: str, version: str) -> str:
    return f'[project]\nname = "{name}"\nversion = "{version}"\n'


def _lock(entries: list[tuple[str, str]]) -> str:
    return "version = 1\n\n" + "\n".join(
        f'[[package]]\nname = "{n}"\nversion = "{v}"\nsource = {{ editable = "." }}\n'
        for n, v in entries)


def _run(root: Path) -> subprocess.CompletedProcess:
    """Drive the real CLI, pinned to the scratch root by WORKSPACE_ROOT."""
    env = dict(os.environ)
    env["WORKSPACE_ROOT"] = str(root)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, timeout=120, check=False, env=env)


# ============================================================
# The direction that must now be refused
# ============================================================

def test_a_stale_lock_is_reported(tmp_path):
    """The reported reproduction, at the real entry point: exit 1, not 0."""
    root = _write_root(
        tmp_path,
        pyproject=_pyproject("heading-os-engine", "1.2.3"),
        lock=_lock([("heading-os-engine", "1.2.2")]))
    proc = _run(root)
    assert proc.returncode == 1, proc.stdout
    assert "uv.lock" in proc.stdout
    assert "1.2.2" in proc.stdout


def test_the_lock_is_checked_even_when_every_markdown_agrees(tmp_path):
    """The exact shape of the miss: three surfaces green, the fourth stale.

    Without this the guard's own success line ("Version in sync") is printed
    over a drifted lock, which is what happened across the v0.14.0 release.
    """
    root = _write_root(
        tmp_path,
        pyproject=_pyproject("heading-os-engine", "0.14.0"),
        lock=_lock([("heading-os-engine", "0.13.0")]),
        version="0.14.0")
    proc = _run(root)
    assert proc.returncode == 1
    assert "Version in sync" not in proc.stdout
    # The lock is the ONLY thing reported. If README, CHANGELOG or ROADMAP
    # appeared here the fixture would be at fault and the assertion above would
    # be passing for the wrong reason.
    reported = [line for line in proc.stdout.splitlines()
                if "!= pyproject" in line]
    assert len(reported) == 1, proc.stdout
    assert "uv.lock" in reported[0]


def test_a_lock_with_no_entry_for_this_package_is_reported(tmp_path):
    """Input collapse must report, not pass. `None` reads as drift."""
    root = _write_root(
        tmp_path,
        pyproject=_pyproject("heading-os-engine", "1.2.3"),
        lock=_lock([("httpx", "0.28.1"), ("jsonschema", "4.26.0")]))
    proc = _run(root)
    assert proc.returncode == 1, proc.stdout
    assert "uv.lock" in proc.stdout
    assert "None" in proc.stdout


def test_a_missing_lock_is_reported_not_crashed(tmp_path):
    root = _write_root(
        tmp_path,
        pyproject=_pyproject("heading-os-engine", "1.2.3"),
        lock=_lock([("heading-os-engine", "1.2.3")]))
    (root / "uv.lock").unlink()
    proc = _run(root)
    assert proc.returncode == 1, proc.stdout
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "uv.lock" in proc.stdout


# ============================================================
# The direction that must still pass
# ============================================================

def test_a_lock_in_sync_passes(tmp_path):
    root = _write_root(
        tmp_path,
        pyproject=_pyproject("heading-os-engine", "1.2.3"),
        lock=_lock([("heading-os-engine", "1.2.3")]))
    proc = _run(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "uv.lock" in proc.stdout, "the success line must name the new surface"


def test_other_packages_in_the_lock_are_ignored(tmp_path):
    """A lock holds every dependency. Only OUR entry decides the verdict.

    The decoys carry versions that would fail if the reader picked the first
    or the last entry rather than matching on name.
    """
    root = _write_root(
        tmp_path,
        pyproject=_pyproject("heading-os-engine", "1.2.3"),
        lock=_lock([("anthropic", "9.9.9"),
                    ("heading-os-engine", "1.2.3"),
                    ("zstandard", "0.0.1")]))
    proc = _run(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_real_repository_is_in_sync():
    """The guard must hold on this checkout, not only on fixtures."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ============================================================
# The package name is read, never assumed
# ============================================================

def test_the_package_name_comes_from_pyproject(tmp_path):
    """Rename the project and the guard must follow it.

    A hardcoded `heading-os-engine` passes every test above and fails only
    here, which is why this case exists.
    """
    root = _write_root(
        tmp_path,
        pyproject=_pyproject("renamed-engine", "1.2.3"),
        lock=_lock([("heading-os-engine", "0.0.1"),
                    ("renamed-engine", "1.2.3")]))
    proc = _run(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_renamed_project_with_a_stale_lock_still_fails(tmp_path):
    """The same rename, drifted. Proves the previous test is not passing by luck."""
    root = _write_root(
        tmp_path,
        pyproject=_pyproject("renamed-engine", "1.2.3"),
        lock=_lock([("heading-os-engine", "1.2.3"),
                    ("renamed-engine", "1.0.0")]))
    proc = _run(root)
    assert proc.returncode == 1, proc.stdout
    assert "renamed-engine" in proc.stdout


def test_the_source_does_not_hardcode_the_distribution_name():
    """Asserted on the source, because no runtime value can distinguish
    "read from pyproject" from "hardcoded" once the two agree."""
    src = SCRIPT.read_text(encoding="utf-8")
    body = src.split('"""', 2)[-1]  # drop the module docstring
    assert 'data["project"]["name"]' in body
    assert '"heading-os-engine"' not in body


def test_the_reader_returns_the_matching_entry(tmp_path):
    """Unit-level, for the anchor the mutation run aims at."""
    (tmp_path / "uv.lock").write_text(
        _lock([("a", "1.0.0"), ("b", "2.0.0")]), encoding="utf-8")
    assert vsync._uv_lock_version(tmp_path, "b") == "2.0.0"
    assert vsync._uv_lock_version(tmp_path, "a") == "1.0.0"
    assert vsync._uv_lock_version(tmp_path, "missing") is None


# ============================================================
# The gate's scope (development-standards obligation 11)
# ============================================================

def test_the_precommit_hook_watches_the_lockfile():
    """A `files:` pattern that misses uv.lock leaves the gate unarmed.

    This is the half that made the drift survivable: the script could be
    perfect and still never run on the commit that changed only the lock.
    """
    cfg = (ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    block = cfg.split("id: version-sync", 1)[1].split("- id:", 1)[0]
    m = re.search(r"files:\s*'([^']+)'", block)
    assert m, block
    pattern = re.compile(m.group(1))
    assert pattern.match("uv.lock"), m.group(1)
    # The four it already covered must not have been dropped on the way in.
    for kept in ("README.md", "CHANGELOG.md", "ROADMAP.md", "pyproject.toml"):
        assert pattern.match(kept), f"{kept} fell out of {m.group(1)}"
    assert not pattern.match("scripts/check-version-sync.py")


def test_the_docstring_states_the_new_counts_and_the_measurement():
    """Adding a surface made two numbers wrong; obligation 2 says restate them
    and carry the dated measurement beside the claim."""
    doc = vsync.__doc__
    assert "four surfaces" in doc
    assert "five files" in doc
    assert "uv.lock" in doc
    assert "MEASURED 2026-09-03" in doc
    # The retired counts survive only inside the sentence that retires them.
    for retired in ("three human-facing surfaces", "the four files"):
        assert retired in doc, "the correction must quote what it replaced"
    assert doc.index("four surfaces") < doc.index("three human-facing surfaces")
