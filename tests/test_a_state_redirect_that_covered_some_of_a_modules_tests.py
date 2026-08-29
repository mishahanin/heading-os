#!/usr/bin/env python3
"""A fireside test module redirected its state root per test, and missed seven.

`scripts/fireside-bot.py` resolves `STATE_DIR` once, at import, through
`get_datastore_dir()`. On the operator's machine that is the live data overlay.
Every writer in the file reaches it: `save_state`, `append_jsonl`, `_log_event`,
`ensure_state_dir`, and `log_error`. A test that loads the module and does not
redirect the constant therefore writes the operator's real data, and the two
error paths do it from inside an `except` block where nobody was expecting a
write at all.

MEASURED 2026-08-29, running each fireside shard against the conftest overlay
write guard. Seven tests in two modules were refused:

    tests/test_a_promise_that_misha_would_help.py                  4
      `_handle_a_tap` cannot DM B, calls `log_error`, and the append lands at
      .heading-os-data/.../fireside-state/errors.log
    tests/test_eleven_guards_the_fireside_bot_applied_to_one_side.py  3
      `_nudge_ceo_on_helmsman_gaps` writes a `helmsman_gap_nudge` row through
      `_log_event` into the live sessions.jsonl; the refusal was then caught by
      the nudge's own `except Exception`, and `log_error` raised a second time
      out of the handler

Neither test was wrong about the behaviour it pinned. Both modules already
redirected `STATE_DIR` somewhere: one in two fixtures near the end of the file,
the other in an opt-in `state` fixture these tests did not ask for. The redirect
had landed in some of the module's tests and not the rest, which is the shape
the two modules' own docstrings catalogue in the code they cover.

The sweep for the same shape found three more modules in the same state, 42
further tests one error path away from the operator's overlay:

    tests/test_a_bot_that_said_it_was_alive_while_it_was_not.py    11
    tests/test_a_roster_the_leak_gate_never_opened.py              27
    tests/test_fireside_cycle_number.py                             4

All five now redirect from an autouse fixture, which is the difference that
matters: a per-test line protects the author who wrote it, and an autouse
fixture protects the test written next year by someone who never read this file.

This guard holds that. It reports a fireside test module that redirects the
state root SOMEWHERE and leaves some of its bot-reaching tests uncovered. Stated
honestly, because a check that over-claims is worse than a narrow one: a module
that never redirects anywhere is not reported, since nothing in it has shown it
reaches a writer, and a test that never touches the loaded module is not
reported either. The conftest write guard is what covers those, at the moment of
the write.

Run: .venv/bin/python -m pytest tests/test_a_state_redirect_that_covered_some_of_a_modules_tests.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.repo_files import tracked_paths  # noqa: E402

BOT = "fireside-bot.py"

# The three ways a test in this repository points a fireside writer somewhere
# safe. `STATE_DIR` is the constant the writers read; `state_path` is the helper
# over it; `HEADING_OS_DATA` is the environment name the whole seam resolves
# from, used by the modules that drive a child process.
REDIRECT_NAMES = ("STATE_DIR", "state_path", "HEADING_OS_DATA")


# ============================================================
# The predicate, as a pure function over source text
# ============================================================

def _called_and_requested(node: ast.AST) -> set[str]:
    """Every fixture this function requests, plus every plain name it calls."""
    names = {arg.arg for arg in node.args.args}
    names |= {
        call.func.id for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    }
    return names


def uncovered_tests(source: str) -> list[str]:
    """Tests that reach the loaded fireside module without a state redirect.

    Empty when the module redirects from an autouse fixture, when it never
    redirects at all, or when nothing in it loads the bot. Pure: it takes source
    text and returns names, so the tests below can drive it on strings that
    exist nowhere on disk.
    """
    tree = ast.parse(source)

    # A module constant holding the bot's path, so a loader written as
    # `spec_from_file_location(name, str(SRC))` is still recognised.
    bot_consts: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and BOT in ast.unparse(node):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            bot_consts |= {t.id for t in targets if isinstance(t, ast.Name)}

    loaders: set[str] = set()
    redirectors: set[str] = set()
    autouse_redirect = False
    functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        functions.append(node)
        body = ast.unparse(node)
        mentioned = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        if "spec_from_file_location" in body and (BOT in body or mentioned & bot_consts):
            loaders.add(node.name)
        if any(name in body for name in REDIRECT_NAMES):
            redirectors.add(node.name)
            decorators = "\n".join(ast.unparse(d) for d in node.decorator_list)
            if "fixture" in decorators and "autouse=True" in decorators:
                autouse_redirect = True

    if autouse_redirect or not redirectors or not loaders:
        return []

    # A fixture that requests the loader hands the module on, so it reaches the
    # bot too. Iterate to a fixed point: the chain can be several deep.
    changed = True
    while changed:
        changed = False
        for node in functions:
            if node.name in loaders:
                continue
            if _called_and_requested(node) & loaders:
                loaders.add(node.name)
                changed = True

    uncovered = []
    for node in functions:
        if not node.name.startswith("test_"):
            continue
        reached = _called_and_requested(node)
        if not reached & loaders:
            continue
        if node.name in redirectors or reached & redirectors:
            continue
        uncovered.append(node.name)
    return uncovered


# ============================================================
# The predicate, driven on synthetic source in both directions
# ============================================================

_LOADER = '''
import importlib.util
import pytest

SRC = ROOT / "scripts" / "fireside-bot.py"


@pytest.fixture(scope="module")
def fb():
    spec = importlib.util.spec_from_file_location("bot", str(SRC))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
'''

_OPT_IN_FIXTURE = '''
@pytest.fixture
def state(fb, tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "STATE_DIR", tmp_path)
    return tmp_path
'''

_AUTOUSE_FIXTURE = '''
@pytest.fixture(autouse=True)
def state(fb, tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "STATE_DIR", tmp_path)
    return tmp_path
'''

_COVERED_TEST = '''
def test_a_covered_one(fb, state):
    assert fb.something()
'''

_UNCOVERED_TEST = '''
def test_an_uncovered_one(fb):
    assert fb.something()
'''

_UNRELATED_TEST = '''
def test_it_touches_no_bot():
    assert 1 + 1 == 2
'''


def test_the_predicate_names_a_test_the_redirect_missed():
    """The measured defect: a redirect exists, and this test does not reach it."""
    source = _LOADER + _OPT_IN_FIXTURE + _COVERED_TEST + _UNCOVERED_TEST
    assert uncovered_tests(source) == ["test_an_uncovered_one"]


def test_the_predicate_clears_a_module_that_redirects_for_everyone():
    """The other direction. The same two tests, one autouse fixture."""
    source = _LOADER + _AUTOUSE_FIXTURE + _COVERED_TEST + _UNCOVERED_TEST
    assert uncovered_tests(source) == []


def test_a_test_that_never_reaches_the_bot_is_not_reported():
    """Scope, stated in the docstring and checked here: the rule is about tests
    that hold the loaded module, not about every function in the file."""
    source = _LOADER + _OPT_IN_FIXTURE + _COVERED_TEST + _UNRELATED_TEST
    assert uncovered_tests(source) == []


def test_a_module_that_never_redirects_is_not_reported():
    """The honest limit. Nothing here has shown it reaches a writer, so there is
    no inconsistency to report and the conftest write guard is what covers it."""
    source = _LOADER + _UNCOVERED_TEST
    assert uncovered_tests(source) == []


def test_a_module_that_loads_no_bot_is_not_reported():
    source = _OPT_IN_FIXTURE + _UNCOVERED_TEST
    assert uncovered_tests(source) == []


def test_a_redirect_written_inside_the_test_body_counts():
    """A test that does the work itself needs no fixture to be covered."""
    source = _LOADER + _OPT_IN_FIXTURE + _COVERED_TEST + '''
def test_it_redirects_itself(fb, tmp_path, monkeypatch):
    monkeypatch.setattr(fb, "STATE_DIR", tmp_path)
    assert fb.something()
'''
    assert uncovered_tests(source) == []


def test_a_fixture_that_passes_the_module_on_is_followed():
    """`fb` reaches the bot, and so does anything built from it. A test holding
    only the derived fixture is still a test holding the module."""
    source = _LOADER + _OPT_IN_FIXTURE + '''
@pytest.fixture
def wired(fb):
    return fb


def test_it_holds_the_module_indirectly(wired):
    assert wired.something()
'''
    assert uncovered_tests(source) == ["test_it_holds_the_module_indirectly"]


def test_a_loader_named_through_a_module_constant_is_found():
    """Written as `str(SRC)`, the loader body carries no literal filename. A
    predicate that grepped the body alone read the whole module as bot-free and
    reported nothing, which is how the first pass over this tree missed
    test_a_bot_that_said_it_was_alive_while_it_was_not.py entirely."""
    source = _LOADER + _OPT_IN_FIXTURE + _UNCOVERED_TEST
    assert uncovered_tests(source) == ["test_an_uncovered_one"]


def test_the_environment_name_counts_as_a_redirect():
    """A module that drives a child process pins HEADING_OS_DATA instead, and
    that is the same guarantee reached a different way."""
    source = _LOADER + '''
@pytest.fixture
def rooted(tmp_path, monkeypatch):
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path))
    return tmp_path


def test_a_covered_one(fb, rooted):
    assert fb.something()
''' + _UNCOVERED_TEST
    assert uncovered_tests(source) == ["test_an_uncovered_one"]


# ============================================================
# The sweep over the real tree
# ============================================================

SELF = Path(__file__).resolve()


def _corpus() -> list[tuple[Path, str]]:
    """Every tracked test module that names the fireside bot, minus this one.

    This file is excluded because its synthetic modules above are STRING
    literals, and reading them as its own fixtures makes the predicate report
    every unit test that drives it. They are already exercised directly, which
    is the stronger check of the two.
    """
    found = []
    for path in tracked_paths(["tests/**/*.py"]):
        if path.resolve() == SELF:
            continue
        source = path.read_text(encoding="utf-8")
        if BOT in source:
            found.append((path, source))
    return found


def test_the_sweep_reaches_a_real_corpus():
    """A rule is green over an empty corpus, so the corpus is pinned too.

    Nineteen modules named the bot on 2026-08-29 and five of them load it and
    redirect. The floor is deliberately below both: it fails when the glob, the
    filename or `tracked_paths` stops finding anything, and not when a shard is
    added or retired.
    """
    corpus = _corpus()
    assert len(corpus) >= 10, (
        f"only {len(corpus)} test modules mention {BOT}; the sweep has stopped "
        "reaching the fireside suite"
    )
    loaders = [p for p, src in corpus if "spec_from_file_location" in src]
    assert len(loaders) >= 5, (
        f"only {len(loaders)} of them load the module; re-point this sweep"
    )


def test_the_predicate_still_finds_its_own_corpus_interesting():
    """The other half of the floor above. If no module in the tree redirects
    from an autouse fixture, the predicate is returning empty for the wrong
    reason and every result below is worthless."""
    autoused = [
        path.name for path, src in _corpus()
        if "spec_from_file_location" in src and "autouse=True" in src
        and any(name in src for name in REDIRECT_NAMES)
    ]
    assert len(autoused) >= 5, (
        f"only {autoused} redirect the state root from an autouse fixture; the "
        "five fixed on 2026-08-29 should all be here"
    )


def test_no_fireside_module_redirects_its_state_root_for_only_some_tests():
    findings = []
    for path, source in _corpus():
        for name in uncovered_tests(source):
            findings.append(f"{path.name}::{name}")
    assert findings == [], (
        "these tests hold the fireside module in a file that redirects "
        "STATE_DIR elsewhere, so they resolve at the operator's live overlay "
        "and the first error path they touch writes it. Move the redirect into "
        "an autouse fixture:\n  " + "\n  ".join(findings)
    )
