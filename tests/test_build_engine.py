"""Regression tests for the engine-repo build tool.

Locks the subtle bug found during the Plan 3 build: git quotes non-ASCII paths
by default (`"datastore/..."` with octal escapes), which made Cyrillic-named
data files fail their private/corporate routing rule and mis-route to `engine`.
The tool must enumerate with core.quotepath=false so a non-ASCII data path
classifies correctly.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.build_engine_repo import _suspicious_engine, _tracked_files, partition


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def test_tracked_files_unquoted_non_ascii(tmp_path):
    _git(["init", "-q"], tmp_path)
    # A Cyrillic-named data file (routes private) + a normal engine file.
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "тест-файл.md").write_text("x", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "-A"], tmp_path)

    files = _tracked_files(tmp_path)
    # Real UTF-8 path, no surrounding quote / octal escaping.
    assert "outputs/тест-файл.md" in files
    assert not any(f.startswith('"') for f in files)


def test_partition_routes_non_ascii_data_to_private(tmp_path):
    _git(["init", "-q"], tmp_path)
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "тест-файл.md").write_text("x", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "foo.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "-A"], tmp_path)

    buckets = partition(tmp_path)
    assert "outputs/тест-файл.md" in buckets["private"]
    assert "scripts/foo.py" in buckets["engine"]
    # The non-ASCII data file must NOT leak into engine.
    assert "outputs/тест-файл.md" not in buckets["engine"]


# --- the belt's second clause -------------------------------------------------
#
# `_suspicious_engine` tests each token two ways: `rel.startswith(t)`, and
# `("/" + t) in rel` for a token that appears further down the path. Only the
# first had any coverage. MEASURED 2026-09-01: deleting the `or ("/" + t) in rel`
# clause left all 116 tests across the five files that import this module green,
# so the branch that catches a data directory nested one level down was standing
# unmeasured. That is the branch that matters for a tree carrying per-executive
# overlays, where the private directory is never the first segment.

def test_the_belt_refuses_a_data_token_nested_below_the_first_segment():
    nested = "overlays/executive-01/crm/contacts/alpha.md"
    assert _suspicious_engine([nested]) == [nested], (
        "a nested private directory routed engine and the belt said nothing"
    )
    deep = "vendor/bundle/outputs/reports/q3.md"
    assert _suspicious_engine([deep]) == [deep]


def test_the_belt_does_not_fire_on_a_token_that_is_only_a_word_fragment():
    """The negative case. `("/" + t) in rel` requires a real path separator, so a
    filename that merely ENDS in the token stays clean; a belt that refused these
    would block ordinary engine files and get switched off rather than fixed."""
    assert _suspicious_engine([
        "docs/build-outputs/README.md",     # "-outputs/", not "/outputs/"
        "scripts/utils/subthreads/pool.py",  # "subthreads/", not "/threads/"
        "examples/crm/contacts/alpha.md",    # the bundled demo tree is exempt
    ]) == []
