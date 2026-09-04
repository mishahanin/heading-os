"""Day mode's routes, bidirectionally: what must be selected, and what must not.

A selector is only worth its NEGATIVE half. "Did the change select the test"
passes trivially for a selector that returns the whole suite, and that is the
selector nobody notices they have: it is green, it is correct, and it saves
nothing. Every route below therefore ships with a case that must NOT select, and
each of those negatives is the half that fails against a selector returning
everything.

The route this file exists for is `literal`. The code graph reports ZERO tests
affected by `.claude/hooks/_dispatch.py`, the single PreToolUse entry point for
twelve walls, because its tests drive it as a subprocess and a subprocess is not
an import edge. A day mode built on an import graph alone would let a change to
every wall in this workspace through untested, with the run green.

Authoring rule, as elsewhere in this suite: the module under test is imported
inside a test body, never at module scope.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CLI = _ROOT / "scripts" / "day-mode.py"


@pytest.fixture
def tree(tmp_path):
    """A tiny git repository with one of each route's shape in it.

    A real `git init`, because the selector asks git which files are tracked and
    a fake would be answering a different question than the one production asks.
    """

    def write(rel, text):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")

    write("scripts/utils/imported.py", "VALUE = 1\n")
    write("scripts/utils/unrelated.py", "OTHER = 2\n")
    write("scripts/run-me.py", "print('hello')\n")
    write("scripts/utils/repo_files.py", "def tracked_paths():\n    return []\n")

    # Reached by import.
    write(
        "tests/test_imported.py",
        """
        def test_it():
            from scripts.utils.imported import VALUE
            assert VALUE == 1
        """,
    )
    # Reached ONLY as a subprocess -- no import of the file anywhere.
    write(
        "tests/test_subprocess_driver.py",
        """
        import subprocess, sys
        from pathlib import Path
        ROOT = Path(__file__).resolve().parent.parent

        def test_it():
            out = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "run-me.py")],
                capture_output=True, text=True,
            )
            assert out.returncode == 0
        """,
    )
    # Reads the tree: the mandatory core.
    write(
        "tests/test_sweep.py",
        """
        from pathlib import Path
        ROOT = Path(__file__).resolve().parent.parent

        def test_it():
            assert list(ROOT.rglob("*.py"))
        """,
    )
    # Mentions nothing under test: must never be selected by a change elsewhere.
    write(
        "tests/test_isolated.py",
        """
        def test_it():
            assert 1 + 1 == 2
        """,
    )
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True
    )
    return tmp_path


def _select(tree, changed):
    from scripts.utils.day_mode import build_index, select

    index = build_index(tree, use_cache=False)
    return select(index, list(changed))


def _routes_for(selection, test):
    return [r.split(":", 1)[0] for r in selection.routes.get(test, [])]


# ---------------------------------------------------------------- import route


def test_a_change_to_an_imported_module_selects_its_test(tree):
    selection = _select(tree, ["scripts/utils/imported.py"])
    assert "tests/test_imported.py" in selection.tests
    assert "import" in _routes_for(selection, "tests/test_imported.py")


def test_a_change_to_an_unrelated_module_does_not_select_that_test(tree):
    """The negative half. Against a selector that returns everything, this fails."""
    selection = _select(tree, ["scripts/utils/unrelated.py"])
    assert "tests/test_imported.py" not in selection.tests
    assert "tests/test_subprocess_driver.py" not in selection.tests


# --------------------------------------------------------------- literal route


def test_a_subprocess_driven_script_selects_its_driver(tree):
    """The route the whole design turns on.

    `scripts/run-me.py` is imported by nothing. The only edge to it is the
    string `"run-me.py"` inside a path the test builds at run time, which is
    exactly the shape `.claude/hooks/_dispatch.py` has in the real suite.
    """
    selection = _select(tree, ["scripts/run-me.py"])
    assert "tests/test_subprocess_driver.py" in selection.tests
    assert "literal" in _routes_for(selection, "tests/test_subprocess_driver.py")


def test_a_subprocess_driver_is_not_selected_by_an_unrelated_script(tree):
    selection = _select(tree, ["scripts/utils/unrelated.py"])
    assert "tests/test_subprocess_driver.py" not in selection.tests


def test_prose_that_merely_mentions_a_file_does_not_select(tree):
    """A docstring naming a file is not a test of it.

    Measured 2026-09-04 on this repository: `grep -rl _dispatch tests/` returns
    73 files and the literal route selects 39. Four of the difference were
    checked by hand and every one names `_dispatch.py` inside a prose docstring,
    including one that says "It says nothing about `.claude/hooks/_dispatch.py`".
    A grep-based selector would run all 73 and call the extra 34 coverage.
    """
    doc = tree / "tests" / "test_prose_only.py"
    doc.write_text(
        '"""A note about scripts/utils/unrelated.py and what it does not do."""\n'
        "\n\ndef test_it():\n    assert True\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True, capture_output=True)
    selection = _select(tree, ["scripts/utils/unrelated.py"])
    assert "tests/test_prose_only.py" not in selection.tests


# ------------------------------------------------------------------- the core


def test_a_tree_sweep_is_in_the_core_and_runs_for_any_change(tree):
    selection = _select(tree, ["scripts/utils/unrelated.py"])
    assert "tests/test_sweep.py" in selection.tests
    assert "core" in _routes_for(selection, "tests/test_sweep.py")


def test_a_test_that_reads_no_tree_is_not_in_the_core(tree):
    """The negative half of the core.

    `ast.walk` is why this matters. An earlier draft matched the bare attribute
    name and pulled 214 test files -- most of the suite -- into the core,
    because `ast.walk` and `os.walk` spell the same word. The core fell from
    37% of the suite to 14% once the receiver had to derive from a repo root.
    """
    from scripts.utils.day_mode import build_index

    index = build_index(tree, use_cache=False)
    assert "tests/test_sweep.py" in index.core
    assert "tests/test_isolated.py" not in index.core
    assert "tests/test_imported.py" not in index.core


def test_ast_walk_is_not_mistaken_for_a_tree_sweep(tree):
    """The exact false positive that would have made the core worthless."""
    from scripts.utils.day_mode import extract

    facts = extract(
        "tests/test_astwalk.py",
        "import ast\n\ndef test_it():\n    for n in ast.walk(ast.parse('x')):\n        pass\n",
    )
    assert facts.sweeps == frozenset()


# ----------------------------------------------------------------- conftest


def test_a_changed_conftest_selects_its_whole_subtree(tree):
    (tree / "tests" / "conftest.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True, capture_output=True)
    selection = _select(tree, ["tests/conftest.py"])
    assert "tests/test_isolated.py" in selection.tests
    assert "conftest" in _routes_for(selection, "tests/test_isolated.py")


def test_a_module_the_conftest_imports_selects_the_whole_subtree(tree):
    """A conftest is a proxy for its subtree, and this is the defect that proved it.

    MEASURED 2026-09-04 by the five-commit replay, before this rule existed.
    Commit a356b26 changed `scripts/utils/overlay_write_guard.py`, which
    `tests/conftest.py` imports to install a guard over the WHOLE suite. Pytest
    loads a conftest for every test beneath it and never through an import
    statement, so no edge runs from a test file to it: day mode selected 169 of
    1077 files and 14 of that commit's own 18 regression tests went unrun. With
    the rule, the same change selects all 1077, which is the honest answer for a
    guard installed over every test.
    """
    (tree / "scripts" / "installed.py").write_text("GUARD = 1\n", encoding="utf-8")
    (tree / "tests" / "conftest.py").write_text(
        "from scripts.installed import GUARD\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True, capture_output=True)
    selection = _select(tree, ["scripts/installed.py"])
    assert "tests/test_isolated.py" in selection.tests
    assert "conftest-input" in _routes_for(selection, "tests/test_isolated.py")


def test_a_module_no_conftest_imports_does_not_select_the_subtree(tree):
    """The negative half. Without it the rule above is "select everything"."""
    (tree / "scripts" / "installed.py").write_text("GUARD = 1\n", encoding="utf-8")
    (tree / "tests" / "conftest.py").write_text(
        "from scripts.installed import GUARD\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True, capture_output=True)
    selection = _select(tree, ["scripts/utils/unrelated.py"])
    assert "tests/test_isolated.py" not in selection.tests


def test_a_data_file_the_conftest_reads_selects_the_whole_subtree(tree):
    """The same proxy, reached by a literal rather than an import.

    MEASURED 2026-09-04: commit 3055671 changed
    `config/tmp-leak-baseline.json`, whose only edge is the literal
    `"tmp-leak-baseline.json"` in `tests/conftest.py`. Day mode selected 177
    files and the one test guarding that baseline was not among them. The
    literal filter had rejected the name outright, because a hyphenated
    basename is neither a `.py` file nor a legal dotted identifier.
    """
    (tree / "config").mkdir()
    (tree / "config" / "some-baseline.json").write_text("{}\n", encoding="utf-8")
    (tree / "tests" / "conftest.py").write_text(
        'from pathlib import Path\n'
        'ROOT = Path(__file__).resolve().parent.parent\n'
        'BASE = ROOT / "config" / "some-baseline.json"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True, capture_output=True)
    selection = _select(tree, ["config/some-baseline.json"])
    assert "tests/test_isolated.py" in selection.tests


def test_a_hyphenated_data_filename_counts_as_a_reference():
    """The unit under the defect above, so a rewrite cannot silently undo it."""
    from scripts.utils.day_mode import _looks_like_a_reference

    assert _looks_like_a_reference("tmp-leak-baseline.json")
    assert _looks_like_a_reference("routing-map.yaml")
    assert _looks_like_a_reference("scripts.utils.paths")
    # Prose and version strings must still be rejected, or the cache fills with
    # every docstring in the repository and the route matches English.
    assert not _looks_like_a_reference("a sentence about paths.py")
    assert not _looks_like_a_reference("")


def test_a_nested_conftest_does_not_select_its_siblings(tree):
    """A conftest selects DOWN, never across."""
    (tree / "tests" / "sub").mkdir()
    (tree / "tests" / "sub" / "conftest.py").write_text("", encoding="utf-8")
    (tree / "tests" / "sub" / "test_inner.py").write_text(
        "def test_it():\n    assert True\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True, capture_output=True)
    selection = _select(tree, ["tests/sub/conftest.py"])
    assert "tests/sub/test_inner.py" in selection.tests
    assert "tests/test_isolated.py" not in selection.tests


# ------------------------------------------------------- reporting obligations


def test_a_change_that_reaches_nothing_is_reported_not_swallowed(tree):
    """Silence is the failure mode. An unreachable change must be NAMED.

    A selector that reports "0 tests selected" for a file it cannot analyse, and
    exits 0, is indistinguishable from one that checked and found nothing to do.
    """
    orphan = tree / "scripts" / "orphan.py"
    orphan.write_text("X = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True, capture_output=True)
    selection = _select(tree, ["scripts/orphan.py"])
    assert "scripts/orphan.py" in selection.undecided


def test_a_deleted_test_file_is_never_handed_to_pytest(tree):
    """A deletion is in the change set and not on disk.

    Selecting it by its name shape hands pytest a path that does not exist, and
    pytest fails the WHOLE run at collection with an error naming day mode
    rather than the deletion. The file is still reported, under the paths that
    are no longer tracked, so the deletion is visible without being run.
    """
    selection = _select(tree, ["tests/test_deleted_yesterday.py"])
    assert "tests/test_deleted_yesterday.py" not in selection.tests
    assert "tests/test_deleted_yesterday.py" in selection.unknown_changed


def test_selection_is_deterministic(tree):
    first = _select(tree, ["scripts/utils/imported.py", "scripts/run-me.py"])
    second = _select(tree, ["scripts/run-me.py", "scripts/utils/imported.py"])
    assert first.tests == second.tests
    assert first.routes == second.routes


def test_an_empty_change_set_still_runs_the_core(tree):
    """The floor holds even when the change set is empty.

    Worth pinning because it is the difference between a floor and a default. A
    day mode whose core were merely the union of what the routes found would
    return nothing here and report a pass.
    """
    selection = _select(tree, [])
    assert selection.tests == ["tests/test_sweep.py"]


def test_the_cli_refuses_to_run_an_empty_selection(tmp_path):
    """An empty selection is a selector failure, never a clean bill of health.

    The `codegraph affected` filter is why this test exists. Measured
    2026-09-04: a bare call with no filter returns 0 and prints "No test files
    affected", and a comma list or a brace list inside the filter also returns 0
    without saying why. A selector that treated an empty answer as "nothing to
    run" would have skipped the entire suite and exited 0.

    The tree here has no tree-sweeping test, so the core is empty too and the
    selection really can be nothing. In the `tree` fixture it never can be,
    which is the property the test above pins.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_alone.py").write_text(
        "def test_it():\n    assert True\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True
    )
    result = subprocess.run(
        [sys.executable, str(_CLI), "run", "--root", str(tmp_path), "--files"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, result.stdout
    assert "No tests selected" in result.stdout
    assert "run-tests.py" in result.stdout


def test_the_cli_reports_the_route_each_file_arrived_by(tree):
    result = subprocess.run(
        [
            sys.executable,
            str(_CLI),
            "select",
            "--root",
            str(tree),
            "--files",
            "scripts/run-me.py",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "by route:" in result.stdout
    assert "literal" in result.stdout
    assert "core" in result.stdout
