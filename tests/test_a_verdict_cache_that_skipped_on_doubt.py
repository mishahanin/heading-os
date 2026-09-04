#!/usr/bin/env python3
"""A cache that skips on doubt is a hole with a speed-up painted on it.

Two properties, and neither is a docstring here because a docstring is what the
previous generation of this idea had instead of a test.

FAIL CLOSED. Every uncertainty runs the test. No cache entry, an unreadable
store, a file the classifier could not parse, a hash it could not compute, a base
that moved: each of them puts the file in `run`. The tests below drive each of
those states and assert the file runs.

DERIVED, NOT LISTED. The set of files that must always run is computed from the
tree on every call. A hand-maintained list of "these must always run" falls
behind silently, and the day it matters is the day nobody notices. That is not a
hypothetical in this repository: on 2026-09-04 `STOPWORDS` in the content
denylist carried a comment reading "this is the ONLY such collision in the tree",
true when it was written and false the moment a CRM row landed.
`test_a_new_test_file_that_reads_the_data_root_needs_no_list_edited` writes a
brand-new test file into a scratch tree and requires the classifier to place it
in the must-run set with nothing else changed, and
`test_the_module_holds_no_hand_written_list_of_test_filenames` asks the AST
whether anybody has since added the list back.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.test_cache import (  # noqa: E402
    DATA_OVERLAY, ENGINE, ENVIRONMENT, UNSURE, Classifier, VerdictStore,
    plan_run,
)

ROOT = Path(__file__).resolve().parent.parent
BASE = "night-2026-09-04"

#: Floors and a ceiling over the live tests tree. MEASURED 2026-09-04 on this
#: checkout: 1079 test files, of which 778 (72.1%) read only the engine checkout
#: and 301 (27.9%) do not -- 173 environment, 111 data_overlay, 17 clock, 0
#: unsure. Both ends are asserted. A classifier that called everything cacheable
#: would be a cache with no safety property at all, and one that called
#: everything must-run would be a no-op wearing a speed-up's name.
LIVE_TEST_FILE_FLOOR = 700
LIVE_CACHEABLE_SHARE_MIN = 0.45
LIVE_CACHEABLE_SHARE_MAX = 0.90


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True)


@pytest.fixture
def engine(tmp_path: Path) -> Path:
    """A scratch tree shaped like this engine: a git repo with a tests/ package.

    Small on purpose. What is under test is the decision procedure, and a
    procedure that needs the real 1077-file tree to exercise a branch is a
    procedure nobody can reason about.
    """
    root = tmp_path / "engine"
    (root / "tests").mkdir(parents=True)
    (root / "scripts").mkdir()
    _git(root, "init", "-q", ".")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    (root / ".gitignore").write_text(".cache/\n", encoding="utf-8")
    (root / "scripts" / "thing.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tests" / "conftest.py").write_text(
        "import pytest\n"
        "\n"
        "@pytest.fixture\n"
        "def pinned_root(monkeypatch, tmp_path):\n"
        "    monkeypatch.setenv('HEADING_OS_DATA', str(tmp_path))\n"
        "\n"
        "@pytest.fixture\n"
        "def the_real_overlay():\n"
        "    from scripts.utils.workspace import get_data_root\n"
        "    return get_data_root()\n",
        encoding="utf-8")
    (root / "tests" / "test_plain.py").write_text(
        "def test_one():\n    assert 1 == 1\n", encoding="utf-8")
    (root / "tests" / "test_also_plain.py").write_text(
        "def test_two():\n    assert 2 == 2\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    return root


@pytest.fixture
def store(tmp_path: Path) -> VerdictStore:
    return VerdictStore(tmp_path / "verdicts.db")


FILES = ["tests/test_plain.py", "tests/test_also_plain.py"]


def _record_green(engine: Path, store: VerdictStore, files=FILES) -> None:
    from scripts.utils.test_cache import corpus_key
    assert store.record(BASE, corpus_key(engine), files)


# ============================================================
# The cache does its job when nothing changed
# ============================================================

def test_an_unchanged_tree_skips_what_it_recorded_green(engine, store):
    _record_green(engine, store)

    plan = plan_run(FILES, BASE, root=engine, store=store)

    assert sorted(plan.skip) == sorted(FILES)
    assert plan.run == []
    assert plan.warnings == []


def test_with_nothing_recorded_every_file_runs(engine, store):
    plan = plan_run(FILES, BASE, root=engine, store=store)

    assert sorted(plan.run) == sorted(FILES)
    assert plan.skip == []
    assert all(plan.reasons[f] == "no verdict at this key" for f in FILES)


# ============================================================
# The cache stops doing it the moment anything could have changed
# ============================================================

def test_touching_one_engine_file_runs_everything_again(engine, store):
    _record_green(engine, store)

    (engine / "scripts" / "thing.py").write_text("VALUE = 2\n", encoding="utf-8")

    plan = plan_run(FILES, BASE, root=engine, store=store)
    assert sorted(plan.run) == sorted(FILES)
    assert plan.skip == []


def test_an_untracked_non_ignored_file_runs_everything_again(engine, store):
    """The corpus is the working tree, not the index.

    A scratch `.py` a parallel agent drops into the tree is read by the sweeps,
    so it must invalidate. `git add` is never called here; the file is untracked
    for the whole test, which is exactly the state an index-keyed cache cannot
    see.
    """
    _record_green(engine, store)

    (engine / "scripts" / "scratch_from_another_agent.py").write_text(
        "# half written\n", encoding="utf-8")

    plan = plan_run(FILES, BASE, root=engine, store=store)
    assert sorted(plan.run) == sorted(FILES)
    assert plan.skip == []


def test_a_gitignored_write_does_not_invalidate(engine, store):
    """The other direction. Without it nothing would ever be skipped."""
    _record_green(engine, store)

    (engine / ".cache").mkdir()
    (engine / ".cache" / "whatever.db").write_bytes(b"run output")

    plan = plan_run(FILES, BASE, root=engine, store=store)
    assert sorted(plan.skip) == sorted(FILES)


def test_moving_the_night_base_discards_every_verdict(engine, store):
    """The night contract. `mark-green` moves the base; the greens do not follow.

    The tree is byte-identical across this assertion, so the key alone retracts
    nothing. The base is what makes a nightly result able to govern a daytime
    skip.
    """
    _record_green(engine, store)
    assert plan_run(FILES, BASE, root=engine, store=store).skip == FILES[:1] + FILES[1:]

    plan = plan_run(FILES, "night-2026-09-05", root=engine, store=store)

    assert sorted(plan.run) == sorted(FILES)
    assert plan.skip == []


def test_revoking_a_verdict_runs_that_file_and_leaves_the_others(engine, store):
    """What a LOUD nightly failure does with its hand on the switch."""
    from scripts.utils.test_cache import corpus_key
    _record_green(engine, store)

    assert store.revoke(BASE, corpus_key(engine), ["tests/test_plain.py"])

    plan = plan_run(FILES, BASE, root=engine, store=store)
    assert plan.run == ["tests/test_plain.py"]
    assert plan.skip == ["tests/test_also_plain.py"]


# ============================================================
# Every uncertainty runs
# ============================================================

def test_a_corrupt_store_runs_everything_and_says_so_loudly(engine, tmp_path):
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"this is not a database, it is 40 bytes of noise")

    plan = plan_run(FILES, BASE, root=engine, store=VerdictStore(corrupt))

    assert sorted(plan.run) == sorted(FILES)
    assert plan.skip == []
    assert plan.warnings, "a silent fall-back to running is still a silent state"
    assert "cache DISABLED" in plan.warnings[0]


def test_a_store_that_cannot_be_opened_at_all_runs_everything(engine, tmp_path):
    """A DIRECTORY where the store should be. Not corrupt: unopenable."""
    blocked = tmp_path / "store-is-a-directory.db"
    blocked.mkdir()

    plan = plan_run(FILES, BASE, root=engine, store=VerdictStore(blocked))

    assert sorted(plan.run) == sorted(FILES)
    assert plan.warnings


def test_a_key_that_cannot_be_computed_runs_everything(engine, store,
                                                       monkeypatch, tmp_path):
    _record_green(engine, store)
    empty_bin = tmp_path / "no-tools"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    plan = plan_run(FILES, BASE, root=engine, store=store)

    assert sorted(plan.run) == sorted(FILES)
    assert plan.warnings and "cache DISABLED" in plan.warnings[0]


def test_a_test_file_the_classifier_cannot_parse_runs(engine, store):
    (engine / "tests" / "test_broken.py").write_text(
        "def test_(:\n    this is not python\n", encoding="utf-8")

    verdict = Classifier(engine).classify("tests/test_broken.py")

    assert verdict.bucket == UNSURE
    assert not verdict.cacheable


def test_a_test_file_that_is_not_there_runs(engine):
    verdict = Classifier(engine).classify("tests/test_never_written.py")

    assert verdict.bucket == UNSURE
    assert not verdict.cacheable


# ============================================================
# The must-run set is DERIVED
# ============================================================

def test_a_new_test_file_that_reads_the_data_root_needs_no_list_edited(engine):
    """Write a brand-new file, edit nothing else, watch it land in must-run.

    This is the property the whole design turns on. If it ever has to be made to
    pass by adding a name somewhere, the design has become the hand-maintained
    list it was built to avoid.
    """
    classifier = Classifier(engine)
    assert classifier.classify("tests/test_plain.py").cacheable

    (engine / "tests" / "test_brand_new_and_nobody_told_the_cache.py").write_text(
        "from scripts.utils.workspace import get_data_root\n"
        "\n"
        "def test_it_reads_the_overlay():\n"
        "    assert get_data_root().exists()\n",
        encoding="utf-8")

    verdict = Classifier(engine).classify(
        "tests/test_brand_new_and_nobody_told_the_cache.py")

    assert verdict.bucket == DATA_OVERLAY
    assert not verdict.cacheable


def test_a_new_file_that_reads_nothing_outside_stays_cacheable(engine):
    """The other half. A classifier that refuses everything is not a classifier."""
    (engine / "tests" / "test_brand_new_and_hermetic.py").write_text(
        "def test_arithmetic():\n    assert 2 + 2 == 4\n", encoding="utf-8")

    verdict = Classifier(engine).classify("tests/test_brand_new_and_hermetic.py")

    assert verdict.bucket == ENGINE
    assert verdict.cacheable


def test_a_new_file_that_asks_the_machine_lands_in_must_run(engine):
    (engine / "tests" / "test_asks_the_machine.py").write_text(
        "import shutil\n"
        "\n"
        "def test_tool_is_installed():\n"
        "    assert shutil.which('git')\n",
        encoding="utf-8")

    verdict = Classifier(engine).classify("tests/test_asks_the_machine.py")

    assert verdict.bucket == ENVIRONMENT
    assert not verdict.cacheable


def test_spawning_a_bare_tool_name_is_a_machine_dependency(engine):
    """`subprocess.run(["git", ...])` reads PATH, and PATH is not in the corpus.

    MEASURED 2026-09-04: 83 of the 864 files otherwise classified cacheable take
    this route, so counting it costs 7.7 points of cacheable share and closes a
    false green that no amount of tree hashing could have caught. This test
    exists because the classifier called THIS FILE cacheable until it was added,
    and this file builds git repositories.
    """
    (engine / "tests" / "test_shells_out.py").write_text(
        "import subprocess\n"
        "\n"
        "def test_runs_git():\n"
        "    subprocess.run(['git', '--version'], check=True)\n",
        encoding="utf-8")

    verdict = Classifier(engine).classify("tests/test_shells_out.py")

    assert verdict.bucket == ENVIRONMENT
    assert any("spawn:git" in f.signal for f in verdict.findings)


def test_spawning_an_absolute_path_or_a_computed_one_is_not_flagged(engine):
    """The other half, and the reason the detector reads the FIRST ARGUMENT.

    An absolute path names the executable directly rather than asking PATH for
    it, and `sys.executable` names the pinned venv interpreter whose dependency
    set `uv.lock` pins inside the corpus. A detector that flagged every spawn
    would put most of the suite in must-run and save nothing.
    """
    (engine / "tests" / "test_spawns_precisely.py").write_text(
        "import subprocess\n"
        "import sys\n"
        "\n"
        "def test_absolute():\n"
        "    subprocess.run(['/usr/bin/true'], check=False)\n"
        "\n"
        "def test_pinned_interpreter():\n"
        "    subprocess.run([sys.executable, '-c', 'pass'], check=False)\n",
        encoding="utf-8")

    assert Classifier(engine).classify("tests/test_spawns_precisely.py").bucket == ENGINE


def test_the_module_holds_no_hand_written_list_of_test_filenames():
    """Asked of the AST: has anybody put the list back?

    Docstrings are excluded, because they explain rather than assert -- the same
    carve-out `.claude/rules/scope-claims.md` makes for its own scanner, and the
    reason this module's own prose may name a test file.
    """
    module = ROOT / "scripts" / "utils" / "test_cache.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef))
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    named = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
        and "test_" in node.value and node.value.endswith(".py")
    ]

    assert named == [], (
        f"{module} names test files in executable strings: {named}. The "
        f"must-run set is derived from the tree; a name here is the start of "
        f"the list that falls behind.")


# ============================================================
# Control is not dependence
# ============================================================

def test_setting_an_env_var_is_control_and_reading_one_is_not(engine):
    """`monkeypatch.setenv` is the test choosing its input, not reading the box.

    Both halves in one place, because the distinction is the classifier's
    sharpest edge and a test of only the first half would pass against a
    classifier that ignored the environment entirely.
    """
    (engine / "tests" / "test_controls_its_input.py").write_text(
        "def test_pins(monkeypatch, tmp_path):\n"
        "    monkeypatch.setenv('HEADING_OS_DATA', str(tmp_path))\n"
        "    assert tmp_path.exists()\n",
        encoding="utf-8")
    (engine / "tests" / "test_reads_the_box.py").write_text(
        "import os\n"
        "\n"
        "def test_reads():\n"
        "    assert os.environ['HEADING_OS_DATA']\n",
        encoding="utf-8")

    classifier = Classifier(engine)

    assert classifier.classify("tests/test_controls_its_input.py").bucket == ENGINE
    assert classifier.classify("tests/test_reads_the_box.py").bucket == DATA_OVERLAY


def test_pinning_to_a_value_taken_from_the_overlay_is_still_a_dependency(engine):
    """The carve-out covers the NAME, never the VALUE.

    `setenv("HEADING_OS_DATA", str(get_data_root()))` pins the variable to
    whatever the real overlay resolves to, which is not control of anything.
    """
    (engine / "tests" / "test_pins_to_the_real_thing.py").write_text(
        "from scripts.utils.workspace import get_data_root\n"
        "\n"
        "def test_pins(monkeypatch):\n"
        "    monkeypatch.setenv('HEADING_OS_DATA', str(get_data_root()))\n",
        encoding="utf-8")

    verdict = Classifier(engine).classify("tests/test_pins_to_the_real_thing.py")

    assert verdict.bucket == DATA_OVERLAY


def test_a_dict_lookup_is_not_an_environment_read(engine):
    """`payload.get("inbox")` is not `os.environ.get("inbox")`.

    MEASURED 2026-09-04 before the receiver check went in: 308 of 1077 test
    files landed in the environment bucket, the great majority of them on
    ordinary dict lookups. Over-reporting is the safe direction and it is still
    a defect, because a must-run set that holds four fifths of the suite is a
    cache nobody will keep switched on.
    """
    (engine / "tests" / "test_reads_a_dict.py").write_text(
        "def test_lookup():\n"
        "    payload = {'inbox': 3}\n"
        "    assert payload.get('inbox') == 3\n",
        encoding="utf-8")

    assert Classifier(engine).classify("tests/test_reads_a_dict.py").bucket == ENGINE


def test_a_relative_traversal_fixture_is_not_a_machine_path(engine):
    """`"../etc/passwd"` handed to a guard is test data, not a read of /etc."""
    (engine / "tests" / "test_traversal_guard.py").write_text(
        "def test_refuses():\n"
        "    assert not _accept('../../etc/passwd')\n"
        "\n"
        "def _accept(p):\n"
        "    return not p.startswith('..')\n",
        encoding="utf-8")

    assert Classifier(engine).classify("tests/test_traversal_guard.py").bucket == ENGINE


# ============================================================
# The indirect case: a fixture pulls the outside in
# ============================================================

def test_a_fixture_carries_its_dependency_to_the_file_that_asks_for_it(engine):
    """A file whose own text mentions nothing can still read the overlay.

    `tests/conftest.py` in the scratch tree declares `the_real_overlay`, which
    calls `get_data_root()`. The test file below names it in a signature and
    nothing else. Classifying the file's own source alone would call it
    cacheable, which is the false green this branch exists to prevent.
    """
    (engine / "tests" / "test_asks_for_a_fixture.py").write_text(
        "def test_uses(the_real_overlay):\n"
        "    assert the_real_overlay is not None\n",
        encoding="utf-8")

    verdict = Classifier(engine).classify("tests/test_asks_for_a_fixture.py")

    assert verdict.bucket == DATA_OVERLAY
    assert any("fixture:the_real_overlay" in f.signal for f in verdict.findings)


def test_a_fixture_that_only_pins_carries_nothing(engine):
    """The other half: `pinned_root` sets the variable, so asking for it is safe."""
    (engine / "tests" / "test_asks_for_the_pin.py").write_text(
        "def test_uses(pinned_root):\n"
        "    assert True\n",
        encoding="utf-8")

    assert Classifier(engine).classify("tests/test_asks_for_the_pin.py").bucket == ENGINE


def test_an_autouse_conftest_dependency_reaches_every_file_beneath_it(engine):
    """One autouse fixture that reads the overlay makes its whole directory run."""
    package = engine / "tests" / "integrationish"
    package.mkdir()
    (package / "conftest.py").write_text(
        "import pytest\n"
        "\n"
        "@pytest.fixture(autouse=True)\n"
        "def _reach_outside():\n"
        "    from scripts.utils.workspace import get_data_root\n"
        "    return get_data_root()\n",
        encoding="utf-8")
    (package / "test_innocent.py").write_text(
        "def test_arithmetic():\n    assert 1 + 1 == 2\n", encoding="utf-8")

    classifier = Classifier(engine)

    verdict = classifier.classify("tests/integrationish/test_innocent.py")
    assert verdict.bucket == DATA_OVERLAY
    assert any("autouse:_reach_outside" in f.signal for f in verdict.findings)
    # And it does NOT leak upward to a sibling outside that directory.
    assert classifier.classify("tests/test_plain.py").bucket == ENGINE


# ============================================================
# Over the live tree
# ============================================================

def test_the_live_tests_tree_splits_into_both_buckets_and_neither_is_empty():
    """Floors AND a ceiling, with the measurement and its date.

    MEASURED 2026-09-04 on this checkout: 1079 test files, 778 cacheable
    (72.1%), 173 environment, 111 data_overlay, 17 clock, 0 unsure.
    """
    files = sorted((ROOT / "tests").rglob("test_*.py"))
    assert len(files) >= LIVE_TEST_FILE_FLOOR, (
        f"only {len(files)} test files found; measured 1077 on 2026-09-04")

    classifier = Classifier(ROOT)
    verdicts = [classifier.classify(f) for f in files]
    cacheable = [v for v in verdicts if v.cacheable]
    share = len(cacheable) / len(verdicts)

    assert LIVE_CACHEABLE_SHARE_MIN <= share <= LIVE_CACHEABLE_SHARE_MAX, (
        f"{share:.1%} of the suite classified cacheable. Measured 80.0% on "
        f"2026-09-04. Outside this band the classifier has either stopped "
        f"protecting anything or stopped saving anything.")

    buckets = {v.bucket for v in verdicts}
    assert DATA_OVERLAY in buckets and ENVIRONMENT in buckets, (
        f"the live tree produced only {buckets}; a classifier that finds no "
        f"data-overlay reader in this repository is not reading the tree")
    # CLOCK is deliberately NOT asserted. 17 files carry it today and every one
    # is a conservative over-report -- a `datetime.now()` written into a scratch
    # file within three lines of a `timedelta`, which the detector cannot tell
    # from a freshness threshold. The task brief's own reading of the 155 core
    # files found ZERO genuine clock dependencies, so requiring the bucket to be
    # non-empty would be requiring the false positives to survive.


def test_this_very_file_is_classified_must_run():
    """It builds git repositories and reads `PATH`. It must never be skipped."""
    verdict = Classifier(ROOT).classify(Path(__file__))

    assert not verdict.cacheable
