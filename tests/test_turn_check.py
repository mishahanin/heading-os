"""The end-of-turn check, and the mapping that makes it worth running.

`scripts/turn-check.py` exists because on 2026-08-09 a constant rename in
`scripts/wizard-verify-key.py` broke four tests and nothing noticed until a full
suite was run by hand much later. The piece that makes it catch that specific
case is unglamorous: the changed file is `wizard-verify-key.py` and its tests
live in `test_wizard_verify_key.py`, so the stem match only works once hyphens
are normalised to underscores. That mapping is tested here by name.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "turn_check_mod", ROOT / "scripts" / "turn-check.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["turn_check_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


tc = _load()


def test_hyphenated_script_maps_to_its_underscored_test_file():
    """The exact pair the script was written for."""
    target = ROOT / "scripts" / "wizard-verify-key.py"
    if not target.exists():
        pytest.skip("wizard-verify-key.py is gone; the mapping example moved")
    matched = {p.name for p in tc.matching_tests([target])}
    assert "test_wizard_verify_key.py" in matched, matched


def test_a_changed_test_file_selects_itself():
    target = ROOT / "tests" / "test_turn_check.py"
    assert target in tc.matching_tests([target])


def test_stem_match_does_not_drag_in_unrelated_neighbours():
    """`test_<stem>_*` is allowed, a mere prefix of a longer word is not.

    Without the underscore, a change to `crm.py` would pull in every
    `test_crm_*.py` AND anything starting with the letters `crm`, which turns a
    seconds-long check into a suite run and teaches people to skip it.

    Declared tests are subtracted first. `matching_tests` is the union of the
    stem rule and the module's own `Tests:` line, and this test is about the
    stem rule alone. It asserted over the union and passed only while
    `crm.py` happened to carry no declaration; the day one was added, a
    correct declaration failed a test about something else.
    """
    target = ROOT / "scripts" / "utils" / "crm.py"
    declared = {p.name for p in tc.declared_tests(target)}
    assert declared, "this test needs a declaring module to be the union it claims"
    picked = {p.name for p in tc.matching_tests([target])} - declared
    for name in picked:
        body = name[len("test_"): -len(".py")]
        assert body == "crm" or body.startswith("crm_"), name


def test_only_library_packages_are_import_probed():
    """A top-level CLI script may re-exec the interpreter through `ensure_venv`
    at module scope, which a Stop hook must never trigger."""
    assert tc.module_name(ROOT / "scripts" / "utils" / "tool_risk.py") == "scripts.utils.tool_risk"
    assert tc.module_name(ROOT / "scripts" / "push-all.py") is None
    assert tc.module_name(ROOT / "scripts" / "utils" / "__init__.py") is None


def test_compile_lane_names_the_file_and_leaves_no_artefact(tmp_path):
    bad = tmp_path / "broken.py"
    bad.write_text("def f(:\n", encoding="utf-8")
    failures = tc.lane_compile([bad])
    assert failures and "broken.py" in failures[0]
    assert not (tmp_path / "broken.py.turncheck.pyc").exists()


def test_compile_lane_is_silent_on_valid_source(tmp_path):
    good = tmp_path / "fine.py"
    good.write_text("VALUE = 1\n", encoding="utf-8")
    assert tc.lane_compile([good]) == []


def test_fingerprint_tracks_content_not_mtime(tmp_path):
    """A save that changes no bytes is not a new thing to check."""
    import os

    f = tmp_path / "a.py"
    f.write_text("X = 1\n", encoding="utf-8")
    monkey = tc.ROOT
    tc.ROOT = tmp_path
    try:
        first = tc.fingerprint([f])
        os.utime(f, (1_000_000, 1_000_000))
        assert tc.fingerprint([f]) == first
        f.write_text("X = 2\n", encoding="utf-8")
        assert tc.fingerprint([f]) != first
    finally:
        tc.ROOT = monkey


def test_an_unreadable_file_does_not_raise(tmp_path):
    """The check is a warning system; it never becomes the reason work stops."""
    missing = tmp_path / "gone.py"
    monkey = tc.ROOT
    tc.ROOT = tmp_path
    try:
        assert tc.fingerprint([missing])
    finally:
        tc.ROOT = monkey


def test_import_lane_reports_a_broken_module(tmp_path, monkeypatch):
    """One subprocess, and its traceback is what the operator is shown."""
    monkeypatch.setattr(tc, "module_name", lambda p: "definitely_not_a_real_module_xyz")
    failures = tc.lane_import([tmp_path / "x.py"])
    assert failures and "definitely_not_a_real_module_xyz" in failures[0]


# ============================================================
# Whose edits (the 2026-08-12 misattribution)
# ============================================================

def _transcript(tmp_path: Path, written: list[Path]) -> Path:
    """A session transcript naming exactly the files that session wrote."""
    import json

    path = tmp_path / "session.jsonl"
    lines = [
        json.dumps({"message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": str(p)}}
        ]}})
        for p in written
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_a_parallel_sessions_broken_file_is_not_this_turns_failure(tmp_path, monkeypatch):
    """The exact 2026-08-12 shape, re-armed.

    A syntactically broken file sits uncommitted in the shared tree, written by
    another session. This turn wrote nothing. Before the scope narrowing the
    compile lane failed and the hook blocked the turn on someone else's work.
    """
    theirs = tmp_path / "written_by_the_other_session.py"
    theirs.write_text("def f(:\n", encoding="utf-8")
    monkeypatch.setattr(tc, "changed_python_files", lambda: [theirs])

    result = tc.run(timeout=30, use_cache=False,
                    transcript=_transcript(tmp_path, []))

    assert result["status"] == "idle", result
    assert result["skipped_foreign"] == 1
    assert "another" in result["reason"], "the drop has to be visible, not silent"


def test_this_sessions_own_break_is_still_caught(tmp_path, monkeypatch):
    """The narrowing must not become a way to stop noticing anything.

    Same broken file, same tree, but this session's transcript claims it.
    """
    mine = tmp_path / "written_here.py"
    mine.write_text("def f(:\n", encoding="utf-8")
    monkeypatch.setattr(tc, "changed_python_files", lambda: [mine])

    result = tc.run(timeout=30, use_cache=False,
                    transcript=_transcript(tmp_path, [mine]))

    assert result["status"] == "fail" and result["lane"] == "compile", result
    assert result["skipped_foreign"] == 0


def test_no_transcript_checks_the_whole_tree(tmp_path, monkeypatch):
    """A hand run from a terminal belongs to no session and must lose nothing."""
    broken = tmp_path / "broken.py"
    broken.write_text("def f(:\n", encoding="utf-8")
    monkeypatch.setattr(tc, "changed_python_files", lambda: [broken])

    result = tc.run(timeout=30, use_cache=False, transcript=None)
    assert result["status"] == "fail", result


def test_the_render_names_what_the_scope_left_out():
    """A narrowed check that prints like a complete one is the original defect
    wearing a different hat."""
    text = tc.render({"status": "pass", "files": 1, "tests_run": 0,
                      "skipped_foreign": 3})
    assert "3" in text and "another session" in text


# ============================================================
# The frozen contract, which is red on purpose
# ============================================================

def _a_contract_file() -> Path | None:
    found = sorted((ROOT / "tests" / "contract").glob("*/test_*.py"))
    return found[0] if found else None


def test_a_frozen_contract_is_matched_then_skipped():
    """Step 3 of a Canopus slice writes a contract that MUST fail until step 6.

    A hook that blocks the turn on it teaches the operator to ignore the hook,
    which costs more than the contract test was ever going to catch here. The
    full suite still runs it; this lane does not.
    """
    contract = _a_contract_file()
    if contract is None:
        pytest.skip("no frozen contract in the tree to check the skip against")
    assert tc.is_contract(contract), contract
    # Five values since 2026-08-26: the fifth is how many matched files
    # collected NO tests, which pytest answers with exit 5 and the lane used
    # to read as a failure.
    failures, ran, skipped, _, _empty = tc.lane_tests([contract], timeout=30)
    assert failures == [], failures
    assert ran == 0, "a contract file was handed to pytest"
    assert skipped == 1, "the skip was not counted"


def test_an_ordinary_test_file_is_not_treated_as_a_contract():
    """The skip is a prefix, and a prefix that grew would silence the lane."""
    assert not tc.is_contract(ROOT / "tests" / "test_turn_check.py")
    assert not tc.is_contract(ROOT / "tests" / "security" / "test_anything.py")


def _plain(text: str) -> str:
    """The render without its colour codes.

    `GREEN` is `\\033[92m` and it opens every passing render, so the literal
    character `2` is in EVERY pass line whatever the counts say. The old
    assertion here was `"2" in text`, which was therefore true even with
    `skipped_contract: 0`, and stayed true when `_contract_note` was mutated to
    report `count * 3`. Measured 2026-08-27 against the repo module: the render
    said "6 frozen-contract file(s)" for a count of 2 and the test was green.
    The operator reads the number; the test now reads the same number.
    """
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_the_ansi_stripper_actually_removes_the_escapes():
    """Anchor. A stripper that returned its input unchanged would restore the
    exact hole this file was fixed for."""
    assert _plain("\x1b[92mok\x1b[0m") == "ok"
    assert "2" not in _plain("\x1b[92mclean\x1b[0m")


@pytest.mark.parametrize("count", [1, 2, 3, 11])
def test_the_render_names_the_contracts_it_declined_to_judge(count):
    text = _plain(tc.render({"status": "pass", "files": 1, "tests_run": 0,
                             "skipped_foreign": 0, "skipped_contract": count}))
    assert f"[{count} frozen-contract file(s) not run" in text, text
    # And no OTHER number is being passed off as the contract count.
    assert re.search(rf"\[{count} frozen-contract", text), text


def test_a_render_with_no_skipped_contracts_says_nothing_about_them():
    """The other half. Without it, a note printed unconditionally would satisfy
    every case above."""
    text = _plain(tc.render({"status": "pass", "files": 1, "tests_run": 0,
                             "skipped_foreign": 0, "skipped_contract": 0}))
    assert "frozen-contract" not in text, text


def test_the_changed_file_and_test_file_counts_are_the_ones_given():
    """Same failure shape, same fix: these two numbers sit beside the contract
    note and were never asserted against a value that could disagree."""
    text = _plain(tc.render({"status": "pass", "files": 4, "tests_run": 9,
                             "skipped_foreign": 0, "skipped_contract": 0}))
    assert "(4 changed file(s), 9 test file(s))" in text, text


# ============================================================
# A module that names its own tests
# ============================================================

def test_a_module_whose_tests_are_named_after_behaviour_is_still_matched(tmp_path):
    """The stem rule maps a module to tests NAMED after it, and nothing else.

    `scripts/checkpoint-paths.py` is the case that exposed the hole: fifteen test
    files exercise it, all of them named after the behaviour they pin
    (`test_checkpoint_state_lock.py`, `test_unattended_state_machine.py`), so the
    stem `checkpoint_paths` matched NONE of them and editing the module ran zero
    tests at the end of a turn. Silently - the lane printed `clean`.

    Matching by content instead was measured and rejected: the fifteen files that
    merely MENTION the module cost 60.6s, which is the wait this lane was just
    fixed to remove. So the module declares its own fast contract and the author
    picks what belongs in it.
    """
    module = tmp_path / "thing-with-dashes.py"
    module.write_text(
        '"""Does a thing.\n\nTests: tests/test_turn_check.py\n"""\n',
        encoding="utf-8",
    )
    declared = tc.declared_tests(module)
    assert [p.name for p in declared] == ["test_turn_check.py"], declared


def test_a_declaration_may_span_several_lines(tmp_path):
    """Six paths do not fit on one line, and wrapping must not drop the tail."""
    module = tmp_path / "m.py"
    module.write_text(
        '"""Doc.\n\n'
        "Tests: tests/test_turn_check.py, tests/test_session_scope.py\n"
        "Tests: tests/test_scope_claims.py\n"
        '"""\n',
        encoding="utf-8",
    )
    names = {p.name for p in tc.declared_tests(module)}
    assert names == {"test_turn_check.py", "test_session_scope.py",
                     "test_scope_claims.py"}, names


def test_an_indented_example_of_the_syntax_is_prose_not_a_declaration(tmp_path):
    """Where the convention is DOCUMENTED is inside a docstring, indented - and
    the first draft read its own example as a declaration of two tests that have
    never existed. Column 0 is the whole difference."""
    module = tmp_path / "m.py"
    module.write_text(
        '"""Doc.\n\n'
        "A module names its contract like this:\n\n"
        "    Tests: tests/test_made_up_example.py\n"
        '"""\n',
        encoding="utf-8",
    )
    assert tc.declared_tests(module) == []
    assert tc.dangling_declarations(module) == []


def test_a_declaration_pointing_nowhere_is_reported_not_swallowed(tmp_path):
    """A renamed test file would otherwise turn a declaration into silent zero
    coverage - the exact failure this whole mechanism exists to end."""
    module = tmp_path / "m.py"
    module.write_text(
        '"""Doc.\n\nTests: tests/test_this_does_not_exist_at_all.py\n"""\n',
        encoding="utf-8",
    )
    assert tc.declared_tests(module) == []
    assert tc.dangling_declarations(module) == ["tests/test_this_does_not_exist_at_all.py"]


def test_checkpoint_paths_declares_a_contract_and_it_resolves():
    """The module the hole was found in, pinned so it cannot regress to zero."""
    module = ROOT / "scripts" / "checkpoint-paths.py"
    matched = tc.matching_tests([module])
    assert matched, "checkpoint-paths.py maps to no tests again"
    assert tc.dangling_declarations(module) == []


def test_every_declaration_in_the_tree_points_at_a_real_file():
    """Suite-level, not hook-level, on purpose: a dangling pointer is a real
    defect, but blocking every turn on it teaches the operator to ignore the
    hook - which is the failure mode the frozen-contract skip was written for."""
    paths = [
        path
        for folder in ("scripts", ".claude/hooks")
        for path in sorted((ROOT / folder).rglob("*.py"))
    ]
    # "no declaration dangles" is green over zero files, so a renamed folder or a
    # changed suffix would switch this guard off and still read as a pass.
    # Measured 2026-08-26: 371 under scripts plus 17 under .claude/hooks, 388.
    assert len(paths) >= 240, f"the scan collapsed to {len(paths)} files"
    broken = {}
    for path in paths:
        missing = tc.dangling_declarations(path)
        if missing:
            broken[str(path.relative_to(ROOT))] = missing
    assert not broken, f"declarations pointing at files that do not exist: {broken}"


# ============================================================
# The slow lane, which the full suite owns
# ============================================================

SLOW_FIXTURE = '''\
"""Temporary fixture written by tests/test_turn_check.py; deleted in that test."""
import time

import pytest


@pytest.mark.slow
def test_a_slow_one():
    time.sleep(2)


def test_a_fast_one():
    assert True
'''


def test_the_test_lane_deselects_slow_marked_tests():
    """A sleep-based test is worth running once per push, not once per turn.

    Measured 2026-08-22: editing `.claude/hooks/checkpoint-offer.py` matched a
    checkpoint/unattended set whose real sleeps cost 122s, so the Stop hook sat
    for about a minute after every answer and the operator felt the whole harness
    as slow. `scripts/run-tests.py` still runs them; this lane does not.
    """
    # This file has to live in the REAL tests directory: `matching_tests` only
    # picks up a changed test file whose path is under `tests/`, so a tmp_path
    # fixture would exercise nothing. The cost is that the tests tree is briefly
    # mutated while other xdist workers walk it — that raced
    # `tests/test_venv_relaunch_guard.py` twice on 2026-08-22 with a
    # FileNotFoundError, and it took a full traceback on 2026-08-23 to see why.
    # Any new test that scans `tests/` must tolerate a path vanishing between
    # rglob and read; that guard shows the shape.
    #
    # `missing_ok=True`: cleanup must never be the thing that fails this test.
    # On 2026-08-23 `tests/test_venv_relaunch_guard.py` wrote and deleted this
    # exact path as its own probe, and when the two landed on different xdist
    # workers at the same moment this `unlink` raised FileNotFoundError. That
    # test now owns a distinct name, and asserts no one else uses it; this stays
    # as the second line of defence.
    fixture = ROOT / "tests" / "test_turn_check_slow_fixture.py"
    fixture.write_text(SLOW_FIXTURE, encoding="utf-8")
    try:
        failures, ran, skipped, deselected, _empty = tc.lane_tests(
            [fixture], timeout=60)
    finally:
        fixture.unlink(missing_ok=True)
    assert failures == [], failures
    assert ran == 1, "the fixture file was not handed to pytest"
    assert deselected == 1, "the slow-marked test was not deselected"


def test_the_render_names_the_slow_tests_it_deselected():
    """Same obligation as the foreign and contract notes: an exclusion nobody
    can see reads as coverage."""
    text = tc.render({"status": "pass", "files": 1, "tests_run": 1,
                      "skipped_foreign": 0, "skipped_contract": 0,
                      "deselected_slow": 4})
    assert "4" in text and "slow" in text


def test_the_hook_forwards_the_transcript_it_is_given():
    """The wrapper is the only place the session identity exists; a wrapper that
    drops it leaves the checker permanently un-scoped."""
    source = (ROOT / ".claude" / "hooks" / "turn-check.py").read_text(encoding="utf-8")
    assert "transcript_path" in source
    assert "--session-transcript" in source
