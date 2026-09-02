#!/usr/bin/env python3
"""The Stop gate budgeted five minutes inside a hook the harness kills at 100s.

`scripts/turn-check.py` is the check a turn should not end without. It runs from
`.claude/hooks/turn-check.py`, which every `.claude/settings.local*.json`
registers under `Stop` with a `timeout`. Three numbers described the same run
and none of them knew about the others.

MEASURED 2026-09-02, by reading the files:

    registered Stop timeout, all four settings files      100 s
    .claude/hooks/turn-check.py BUDGET_SECONDS            150 s
      (its own kill on the child, and it hands the
       checker `--timeout BUDGET_SECONDS - 10` = 140 s)
    scripts/turn-check.py DEFAULT_TEST_TIMEOUT            120 s

and inside the checker the lanes are SEQUENTIAL, so their caps add:

    changed_python_files()   2 git calls  @ 20 s   =  40 s
    deleted_python_files()   1 git call   @ 20 s   =  20 s
    lane_import()            1 subprocess @ 60 s   =  60 s
    lane_tests()             up to 3 subprocesses @ the test cap
                             (the run, the xdist collection-race retry,
                              then `_files_holding_no_test` on exit 5)

    worst case = 120 + 3 x cap  ->  540 s at the 140 s the hook passes.

A hook that outruns its registered timeout has its output DISCARDED: the same
observation is written down at `.claude/hooks/checkpoint-offer.py:98-112`,
where a Stop hook measured at 92.0 s against a 90 s registration lost its whole
continuation. So the failure mode here is not a slow turn. It is a gate that
goes SILENT precisely on the big turn, because a big turn is what makes the
lanes take their caps, and silence from this hook is byte-for-byte what a clean
tree looks like.

The fix has two halves and this file pins both.

1. The checker bounds its WHOLE run against one wall clock instead of handing
   each lane an independent cap. Every child gets `min(its own cap, what is
   left)`, so three lanes can no longer spend three budgets, and a run cut
   short reports what it did not measure rather than returning a shape that
   reads as clean.
2. The registered timeout covers the wrapper's budget with a reserve, and the
   checker DERIVES its default budget from that registration rather than
   carrying a constant that describes somebody else's number.

Shrinking the test lane to fit 100 s was the other way to make the numbers
agree, and it is refused: `tests/test_a_stop_hook_that_became_a_toll.py`
records what a too-small budget did the last time (five refused turns, every
re-run clean, 45.21 s measured for the campaign-sized parallel lane). Trading a
harness kill for a lane that cannot finish is one silent pass for another.

What this file does NOT establish: what Claude Code does with an over-running
Stop hook. The tree records an observation (the 92.0 s measurement above) and
no test in it drives the harness, so the fix is written conservatively - keep
every internal budget under the registered number - rather than on a claim
about harness behaviour.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LANE = ROOT / "scripts" / "turn-check.py"
HOOK = ROOT / ".claude" / "hooks" / "turn-check.py"
SETTINGS_DIR = ROOT / ".claude"

# The floor. Three tracked per-OS templates ship in every clone; the live
# `settings.local.json` is gitignored and present on a set-up workspace. A run
# that finds fewer than the templates has stopped reading the real source and
# must say so rather than pass over an empty corpus.
MIN_SETTINGS_FILES = 3


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tc():
    return _load(LANE, "turn_check_budget_under_test")


@pytest.fixture(scope="module")
def wrapper():
    return _load(HOOK, "turn_check_hook_under_test")


def _registrations() -> dict[str, int]:
    """{settings file name: Stop timeout} read straight off disk.

    Deliberately NOT read through the checker's own helper. This is the test's
    independent view of the registration, so a helper that learned to return a
    convenient number cannot satisfy the comparison against itself.
    """
    found: dict[str, int] = {}
    for path in sorted(SETTINGS_DIR.glob("settings.local*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for group in data.get("hooks", {}).get("Stop", []) or []:
            for entry in (group or {}).get("hooks", []) or []:
                command = entry.get("command")
                if not isinstance(command, str) or "turn-check.py" not in command:
                    continue
                timeout = entry.get("timeout")
                if isinstance(timeout, int):
                    found[path.name] = timeout
    return found


def test_the_registration_is_readable_at_all():
    """The floor. A gate that finds nothing must refuse, never pass quietly.

    Every assertion below compares an internal budget against the registered
    number. With no registrations the comparisons are vacuously true, which is
    the "a guard is green over an empty corpus" shape.
    """
    found = _registrations()
    assert len(found) >= MIN_SETTINGS_FILES, (
        f"found the turn-check Stop registration in {len(found)} settings "
        f"file(s) ({sorted(found)}), fewer than the {MIN_SETTINGS_FILES} "
        f"tracked templates. Either the hook was unregistered, the command "
        f"string stopped naming turn-check.py, or this test is reading the "
        f"wrong place. Until that is resolved nothing below is measuring "
        f"anything.")


def test_every_settings_file_registers_the_same_timeout():
    """Four files, one number. `bash scripts/setup-platform.sh --check` compares
    a template's registrations against the live file, so a value that drifts in
    one of them is reported as drift there and silently changes the budget
    here."""
    found = _registrations()
    assert len(set(found.values())) == 1, (
        f"the turn-check Stop hook is registered with different timeouts per "
        f"settings file: {found}. The checker derives its budget from the "
        f"smallest, so the others are describing a run that cannot happen.")


def test_the_checker_derives_its_budget_from_the_registration(tc):
    """The strong form. Not a constant that happens to be small enough.

    A hardcoded number is what produced this defect: three files each carried
    their own and none of them was wrong on its own terms.
    """
    assert hasattr(tc, "registered_hook_timeout"), (
        "scripts/turn-check.py cannot read the timeout it is registered with, "
        "so any budget it carries is an independent constant describing "
        "somebody else's number")
    assert hasattr(tc, "budget_seconds"), (
        "scripts/turn-check.py has no derived budget")

    registered = min(_registrations().values())
    assert tc.registered_hook_timeout() == registered, (
        f"the checker reads the registration as "
        f"{tc.registered_hook_timeout()}, the settings files say {registered}")

    budget = tc.budget_seconds()
    assert budget + tc.HOOK_RESERVE_SECONDS <= registered, (
        f"the checker's default budget is {budget}s and it reserves "
        f"{tc.HOOK_RESERVE_SECONDS}s for the wrapper, which needs "
        f"{budget + tc.HOOK_RESERVE_SECONDS}s of a {registered}s registration")


def test_the_derived_budget_follows_a_changed_registration(tc, tmp_path):
    """Derivation, proved by moving the input.

    Reading the real file and comparing against the real number cannot tell a
    derivation from a constant that agrees with it today. This writes a
    registration nobody would choose and checks the budget moves with it.
    """
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.local.json").write_text(json.dumps({
        "hooks": {"Stop": [{"matcher": ".*", "hooks": [
            {"type": "command",
             "command": "python3 -c \"...turn-check.py...\"",
             "timeout": 337},
        ]}]}
    }), encoding="utf-8")

    assert tc.registered_hook_timeout(tmp_path) == 337
    assert tc.budget_seconds(tmp_path) == 337 - tc.HOOK_RESERVE_SECONDS

    # Two files disagreeing. The four registrations agree today, so reading the
    # real tree cannot tell `min` from `max` and a reader that took the largest
    # would derive a budget the stingiest platform's harness kills.
    (claude / "settings.local.windows.json").write_text(json.dumps({
        "hooks": {"Stop": [{"matcher": ".*", "hooks": [
            {"type": "command", "command": "turn-check.py", "timeout": 211},
        ]}]}
    }), encoding="utf-8")
    assert tc.registered_hook_timeout(tmp_path) == 211, (
        "with two registrations the checker must derive from the SMALLEST, or "
        "it budgets past the harness that kills first")

    empty = tmp_path / "nothing"
    empty.mkdir()
    assert tc.registered_hook_timeout(empty) is None, (
        "a tree with no registration reported a number, so the 'no harness' "
        "case is indistinguishable from a real reading")
    assert tc.budget_seconds(empty) == tc.FALLBACK_BUDGET_SECONDS, (
        "with no registration to derive from, the budget must fall back to a "
        "named constant rather than to whatever was read last")


def test_the_wrapper_budget_fits_inside_the_registration(tc, wrapper):
    """The number the harness kills at must exceed the number the wrapper
    spends. `.claude/hooks/turn-check.py` kills its child at `BUDGET_SECONDS`
    and then formats and prints the block message; the harness clock started
    before python did."""
    registered = min(_registrations().values())
    assert registered >= wrapper.BUDGET_SECONDS + tc.HOOK_RESERVE_SECONDS, (
        f"the Stop wrapper allows its child {wrapper.BUDGET_SECONDS}s and the "
        f"checker reserves {tc.HOOK_RESERVE_SECONDS}s for interpreter start "
        f"and the wrapper's own work, which needs "
        f"{wrapper.BUDGET_SECONDS + tc.HOOK_RESERVE_SECONDS}s of a "
        f"{registered}s registration. Past that the harness discards the "
        f"hook's output and the gate is silent on exactly the turn that made "
        f"it slow.")
    assert tc.budget_seconds() <= wrapper.BUDGET_SECONDS, (
        f"the checker's derived budget is {tc.budget_seconds()}s while the "
        f"wrapper kills it at {wrapper.BUDGET_SECONDS}s. A checker killed "
        f"mid-report loses the honest 'did not finish' it was about to print, "
        f"which is the same silence measured one layer up.")


def test_the_timeout_the_wrapper_hands_the_checker_fits_its_own_kill(wrapper):
    """Read out of the wrapper's real command line, not restated.

    The wrapper must not ask the checker for more wall time than it is willing
    to wait for, or the checker is killed mid-report and the honest
    "did not finish" it was about to print is lost.
    """
    source = HOOK.read_text(encoding="utf-8")
    assert "BUDGET_SECONDS - 10" in source, (
        "the wrapper no longer derives the checker's cap from its own budget; "
        "read the new expression and re-pin it here rather than deleting this")
    assert wrapper.BUDGET_SECONDS - 10 <= wrapper.BUDGET_SECONDS


class _Clock:
    """A wall clock the test owns, so bounding wall time costs no wall time."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def spend(self, seconds: float) -> None:
        self.t += max(0.0, float(seconds))


def test_a_run_cannot_spend_more_wall_time_than_its_budget(tc, monkeypatch):
    """The defect itself, driven end to end on a fake clock.

    Every child is answered by a stub that consumes the ENTIRE timeout it was
    handed. That is the worst case by construction, and it is the case the
    static caps were written for. The changed set is 25 real test files, which
    is over `PARALLEL_FILE_THRESHOLD`, so the tests lane goes parallel and both
    extra attempts are reachable: the first run answers with xdist's collection
    -race string, the retry answers exit 5, and exit 5 sends the lane into
    `_files_holding_no_test` for a third child.

    Pre-fix this spent 40 + 20 + 60 + 3 x budget seconds. Post-fix it cannot
    exceed the budget, because each cap is what is LEFT rather than what the
    lane would like.
    """
    assert hasattr(tc, "_now"), (
        "scripts/turn-check.py has no clock seam, so nothing measures how much "
        "of the budget a lane has already spent and the per-lane caps simply "
        "add up")

    clock = _Clock()
    monkeypatch.setattr(tc, "_now", clock)

    # Real files, so the compile lane and the test-matching are genuine.
    changed = sorted(p for p in (ROOT / "tests").glob("test_a_*.py"))[:25]
    assert len(changed) == 25, "fixture needs 25 real test files to go parallel"
    changed.append(ROOT / "scripts" / "utils" / "session_scope.py")
    payload = "\0".join(str(p.relative_to(ROOT)) for p in changed).encode()

    calls: list[tuple[str, float]] = []

    def fake_run(args, **kwargs):
        timeout = kwargs.get("timeout")
        assert timeout is not None, f"an unbounded child: {args[:3]}"
        calls.append((Path(args[0]).name, float(timeout)))
        clock.spend(timeout)
        if args[0] == "git":
            return subprocess.CompletedProcess(args, 0, payload, b"")
        text = "--collect-only" in args
        if not text and "pytest" not in args:          # the import probe
            return subprocess.CompletedProcess(args, 0, "", "")
        if "--collect-only" in args:                   # _files_holding_no_test
            return subprocess.CompletedProcess(args, 0, "", "")
        if len([c for c in calls if c[0] != "git"]) <= 2:
            return subprocess.CompletedProcess(
                args, 1, "Different tests were collected between gw0 and gw9", "")
        return subprocess.CompletedProcess(args, tc.NO_TESTS_COLLECTED, "", "")

    monkeypatch.setattr(tc.subprocess, "run", fake_run)
    monkeypatch.setattr(tc, "read_state", dict)
    monkeypatch.setattr(tc, "write_state", lambda data: None)

    budget = 100
    started = clock()
    tc.run(timeout=budget, use_cache=False, transcript=None)
    spent = clock() - started

    assert calls, "no child ran at all, so this measured nothing"
    assert spent <= budget, (
        f"the run spent {spent:.0f}s of wall time against a {budget}s budget. "
        f"Caps handed to children: {calls}. Sequential lanes with independent "
        f"caps add up; the harness kills the hook at the registered timeout "
        f"and discards everything it was about to say.")


def test_a_run_cut_short_names_what_it_did_not_measure(tc, monkeypatch):
    """Honest truncation. Stopping early is fine; saying nothing is not.

    `.claude/rules/scope-claims.md` obligation 2: silence about an exclusion
    reads as coverage. A budget that runs out during the git scan used to leave
    the changed set empty, which `run` reports as "no uncommitted Python edits"
    - byte-for-byte what a genuinely clean tree returns.
    """
    assert hasattr(tc, "_now"), "no clock seam; see the test above"
    clock = _Clock()
    monkeypatch.setattr(tc, "_now", clock)

    def fake_run(args, **kwargs):
        timeout = kwargs.get("timeout")
        clock.spend(timeout if timeout is not None else 0)
        raise subprocess.TimeoutExpired(args, timeout or 0)

    monkeypatch.setattr(tc.subprocess, "run", fake_run)
    monkeypatch.setattr(tc, "read_state", dict)
    monkeypatch.setattr(tc, "write_state", lambda data: None)

    result = tc.run(timeout=30, use_cache=False, transcript=None)
    assert result["status"] == "fail", (
        f"the budget was gone before anything could be measured and the run "
        f"reported {result!r}, which the Stop hook renders as a clean turn")
    text = tc.render(result)
    assert "budget" in text.lower(), (
        f"the report never says the run ran out of time: {text!r}")


def test_the_test_lane_is_not_shrunk_below_the_measured_campaign_run(tc):
    """The prohibition. Making the numbers agree by cutting the lane trades one
    silent pass for another.

    45.21 s is the measured campaign-sized parallel run recorded in
    `tests/test_a_stop_hook_that_became_a_toll.py` (78 files, 1932 tests, on a
    loaded machine). A budget that cannot cover it puts the hook back where
    that file found it: refusing turns that were clean.
    """
    measured_campaign_seconds = 46
    budget = tc.budget_seconds()
    assert budget >= 2 * measured_campaign_seconds, (
        f"the derived budget is {budget}s against a measured {measured_campaign_seconds}s "
        f"campaign run. That is the toll this workspace already paid once: the "
        f"hook refuses, every re-run with a longer cap comes back clean, and "
        f"the operator learns to skip the check.")
