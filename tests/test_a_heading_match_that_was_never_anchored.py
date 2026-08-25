"""Shard 03-p2: a heading pattern with no anchor, two flags dropped in silence,
and a docstring that refuted itself in its own second clause.

* ``check-version-sync._readme_status_version`` matched ``##\\s+Status`` with an
  unanchored ``re.search``. ``### Status`` CONTAINS ``## Status`` from its
  second ``#``, and so does prose that merely names the section, so the first
  such match in the file won. The guard could then report drift on a correct
  README, or - worse - pass while the real ``## Status`` heading had gone
  stale. Its sibling ``_changelog_latest_version`` was anchored all along.

* ``check-path-references`` accepted ``--coverage --check``. The coverage
  branch returns before ``scan()`` runs, so the dangling-path check never
  happened and the exit code was 0: a green result standing in for a check
  nobody made.

* ``checkpoint-paths`` warned that ``--json`` does not apply beside an action
  flag, and dropped ``--kind`` in the identical position without a word.

* ``check-path-references``'s docstring said the extraction is "heuristic in
  one direction only" and then listed two directions. BASELINE carries four
  over-match entries, so the half it denied is the documented one.

* ``code_files`` built repo-relative strings with ``str()``, which is
  backslash-separated on Windows, and compared them against keys from a regex
  that admits ``/`` only. Every file read as undocumented there.

Run: python3 -m pytest tests/test_a_heading_match_that_was_never_anchored.py
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


vsync = _load("version_sync_under_test", "scripts/check-version-sync.py")
pathrefs = _load("path_references_under_test", "scripts/check-path-references.py")


# ============================================================
# The heading that matched inside a deeper one
# ============================================================

def _readme(tmp_path: Path, body: str) -> Path:
    (tmp_path / "README.md").write_text(body, encoding="utf-8")
    return tmp_path


def test_a_deeper_status_heading_is_not_mistaken_for_the_real_one(tmp_path):
    """The reported reproduction: `### Status` contains `## Status`."""
    root = _readme(tmp_path,
                   "# Project\n\n"
                   "## Components\n\n"
                   "### Status\n\n"
                   "Was `v0.3.0` once.\n\n"
                   "## Status\n\n"
                   "HEADING OS is `v0.13.0`.\n")
    assert vsync._readme_status_version(root) == "0.13.0"


def test_prose_naming_the_section_is_not_the_section(tmp_path):
    root = _readme(tmp_path,
                   "# Project\n\n"
                   "See the `## Status` section for `v0.3.0` history.\n\n"
                   "## Status\n\n"
                   "HEADING OS is `v0.13.0`.\n")
    assert vsync._readme_status_version(root) == "0.13.0"


def test_a_stale_real_heading_is_still_reported_stale(tmp_path):
    """The dangerous direction: the guard must not pass on a subsection's token.

    A wrong-section match that happens to hold the CURRENT version hides real
    drift, which is worse than a false alarm.
    """
    root = _readme(tmp_path,
                   "### Status\n\n"
                   "Current is `v0.13.0`.\n\n"
                   "## Status\n\n"
                   "HEADING OS is `v0.3.0`.\n")
    assert vsync._readme_status_version(root) == "0.3.0"


def test_an_ordinary_readme_still_reads(tmp_path):
    root = _readme(tmp_path, "# Project\n\n## Status\n\nIt is `v1.2.3`.\n\n## Next\n")
    assert vsync._readme_status_version(root) == "1.2.3"


def test_the_section_still_ends_at_the_next_heading(tmp_path):
    """The terminator is anchored too, or the section would swallow the file."""
    root = _readme(tmp_path,
                   "## Status\n\nNo version here.\n\n## History\n\nOnce `v9.9.9`.\n")
    assert vsync._readme_status_version(root) is None


def test_no_status_heading_at_all_reads_none(tmp_path):
    root = _readme(tmp_path, "# Project\n\n## Install\n\nRun `v1.0.0` of nothing.\n")
    assert vsync._readme_status_version(root) is None


def test_the_real_readme_still_reads():
    """The guard must keep working on this repository, not only on fixtures."""
    assert vsync._readme_status_version(ROOT) is not None


# ============================================================
# The two flags that could not both run
# ============================================================

def test_coverage_and_check_together_are_refused():
    """Exit 0 with no scan was a green light for a check nobody made."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check-path-references.py"),
         "--coverage", "--check"],
        capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 2, proc.stderr
    assert "separately" in proc.stderr


def test_coverage_alone_still_runs():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check-path-references.py"),
         "--coverage", "--json"],
        capture_output=True, text=True, timeout=300, check=False)
    assert proc.returncode == 0, proc.stderr


# ============================================================
# The paths built with the wrong separator
# ============================================================

def test_code_paths_use_forward_slashes(tmp_path):
    """The prose regex admits `/` and not `\\`, so the keys must be posix."""
    (tmp_path / "scripts" / "utils").mkdir(parents=True)
    (tmp_path / "scripts" / "utils" / "thing.py").write_text("x", encoding="utf-8")
    (tmp_path / "scripts" / "utils" / "__init__.py").write_text("", encoding="utf-8")

    files, skipped = pathrefs.code_files(tmp_path)
    assert files == ["scripts/utils/thing.py"]
    assert skipped == 1


def test_the_separator_is_asserted_on_the_source_not_the_result():
    """`str(PurePath)` and `.as_posix()` are identical on posix.

    No runtime value on this machine can distinguish them, so the claim is
    pinned where it is decided. This is the same reasoning the workspace
    already applied to a Windows-only forward-slash fix.
    """
    src = (ROOT / "scripts" / "check-path-references.py").read_text(encoding="utf-8")
    assert "p.relative_to(root).as_posix() for p in root.glob(_CODE_GLOB)" in src
    assert "str(p.relative_to(root)) for p in root.glob(_CODE_GLOB)" not in src


def test_a_package_marker_is_still_dropped_and_counted(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    files, skipped = pathrefs.code_files(tmp_path)
    assert files == [] and skipped == 1


# ============================================================
# The docstring that refuted itself
# ============================================================

def test_the_docstring_claims_both_directions():
    doc = pathrefs.__doc__
    assert "heuristic in BOTH directions" in doc
    # The correction quotes the phrase it replaced, so pin the order.
    assert doc.index("heuristic in BOTH directions") < doc.index(
        'read "in one direction only"')


def test_the_baseline_still_carries_over_match_entries():
    """The evidence the docstring now admits to. If these went, so does the claim."""
    fragments = [p for p in pathrefs.BASELINE
                 if p.endswith((".appen", ".assign", ".swap", ".tier"))]
    assert fragments, "the over-match half of the claim needs its evidence"


# ============================================================
# The kind that was dropped without a word
# ============================================================

def _checkpoint(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "checkpoint-paths.py"), *args],
        capture_output=True, text=True, timeout=120, check=False)


def test_a_kind_beside_an_action_flag_is_reported():
    """`--json` in the identical position was warned about; `--kind` was not."""
    proc = _checkpoint("--compact-history", "--kind", "auto")
    assert "--kind" in proc.stderr
    assert "ignoring it" in proc.stderr


def test_a_json_beside_an_action_flag_is_still_reported():
    proc = _checkpoint("--compact-history", "--json")
    assert "--json" in proc.stderr


def test_a_kind_with_no_action_flag_is_silent_and_used():
    proc = _checkpoint("--kind", "auto")
    assert proc.returncode == 0, proc.stderr
    assert "ignoring it" not in proc.stderr
    assert "_handoff_auto_" in proc.stdout, "the kind names the archive file"


def test_an_action_flag_alone_says_nothing_about_kind(monkeypatch):
    """The warning must fire on the FLAG, not on the branch.

    A mutation making it unconditional survived the first pass, because every
    test that reached this branch also passed --kind.
    """
    proc = _checkpoint("--compact-history")
    assert "--kind" not in proc.stderr
    assert "ignoring it" not in proc.stderr


def test_an_action_flag_alone_says_nothing_about_json():
    proc = _checkpoint("--compact-history")
    assert "--json" not in proc.stderr


def test_the_dump_still_defaults_to_manual():
    """Removing the argparse default must not remove the behaviour."""
    proc = _checkpoint()
    assert proc.returncode == 0, proc.stderr
    assert "_handoff_manual_" in proc.stdout
