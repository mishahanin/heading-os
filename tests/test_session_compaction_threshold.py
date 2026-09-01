"""One session's compaction threshold, set mid-work, without a restart.

The operator sets `--compact-at 35` at the start of important work and the hard
threshold becomes 35 for THAT session, with the soft reminder derived at 30. No
restart is involved because every reader of the threshold is a fresh process per
event that re-reads the session's own state file.

The key is `session_hard_threshold` and never `hard_threshold`. The status line
rewrites the second one on every render as its echo of what `config()` resolved,
so a choice stored there would survive about one turn.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import checkpoint_paths as CP  # noqa: E402


@pytest.fixture(autouse=True)
def _workspace_env(monkeypatch):
    """Pin the environment pair to the workspace's real values (40/45).

    Every test that asserts a fallback asserts against these, so a change to
    settings.local.json cannot silently turn a real assertion into a tautology.
    """
    monkeypatch.setenv("CLAUDE_HANDOFF_SOFT_THRESHOLD", "40")
    monkeypatch.setenv("CLAUDE_HANDOFF_HARD_THRESHOLD", "45")


def test_the_session_value_wins_over_the_environment():
    cfg = CP.config({"session_hard_threshold": 35})
    assert cfg["hard"] == 35
    assert cfg["soft"] == 30


def test_soft_is_always_five_below_the_session_hard(monkeypatch):
    """The environment's soft value is ignored entirely, not merged."""
    monkeypatch.setenv("CLAUDE_HANDOFF_SOFT_THRESHOLD", "10")
    cfg = CP.config({"session_hard_threshold": 35})
    assert cfg["soft"] == 30


def test_no_session_key_keeps_the_environment_pair():
    cfg = CP.config({"session_auto": True})
    assert (cfg["soft"], cfg["hard"]) == (40, 45)


def test_no_state_at_all_keeps_the_environment_pair():
    cfg = CP.config(None)
    assert (cfg["soft"], cfg["hard"]) == (40, 45)


def test_an_inverted_environment_pair_still_falls_back(monkeypatch):
    """Regression guard on the branch this change does NOT touch."""
    monkeypatch.setenv("CLAUDE_HANDOFF_SOFT_THRESHOLD", "50")
    monkeypatch.setenv("CLAUDE_HANDOFF_HARD_THRESHOLD", "45")
    cfg = CP.config(None)
    assert cfg["soft"] == 25
    assert cfg["hard"] == 30


@pytest.mark.parametrize("unusable", [5, 95, 0, "thirty-five", "", None])
def test_an_unusable_session_value_falls_back_instead_of_raising(unusable):
    """A hand-edited or stale file must not crash the status line.

    The CLI refuses these at the door, so reaching `config()` means the file was
    written by something else. `env_int` already treats an invalid value as
    absent; this mirrors it.
    """
    cfg = CP.config({"session_hard_threshold": unusable})
    assert (cfg["soft"], cfg["hard"]) == (40, 45)


def test_two_sessions_never_see_each_other_s_number():
    """CAP-6. The isolation is structural: state is per session, and `config()`
    is handed one session's dict."""
    a = CP.config({"session_hard_threshold": 35})
    b = CP.config({"session_hard_threshold": 60})
    assert (a["hard"], b["hard"]) == (35, 60)
    assert (a["soft"], b["soft"]) == (30, 55)


def test_the_bounds_are_named_constants():
    assert CP.SOFT_OFFSET == 5
    assert (CP.HARD_THRESHOLD_MIN, CP.HARD_THRESHOLD_MAX) == (15, 90)


# --------------------------------------------------------------------------
# The CLI. Driven as a subprocess, the way the operator and the slash command
# drive it, following the idiom of tests/test_checkpoint_session_scope.py.
# --------------------------------------------------------------------------

CLI = ROOT / "scripts" / "checkpoint-paths.py"
SESSION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture
def env(tmp_path):
    """An isolated project root with one session state file."""
    project = tmp_path / "workspace"
    (project / ".claude" / "state").mkdir(parents=True)
    e = dict(os.environ)
    e["CLAUDE_CODE_SESSION_ID"] = SESSION
    e["CLAUDE_PROJECT_DIR"] = str(project)
    e["CLAUDE_HANDOFF_SOFT_THRESHOLD"] = "40"
    e["CLAUDE_HANDOFF_HARD_THRESHOLD"] = "45"
    e.pop("CLAUDE_HANDOFF_AUTO", None)
    e.pop("CLAUDE_HANDOFF_UNATTENDED", None)
    return {"project": project, "env": e}


def _state_path(env):
    # `CP.safe_slug`, never a local `SESSION[:32]`. The two agree for this
    # constant, but a re-implementation drifts silently if SESSION ever changes
    # shape - safe_slug also rewrites non-alphanumerics and strips a trailing `-`.
    return CP.state_path(env["project"], CP.safe_slug(SESSION))


def _write_state(env, data: dict):
    _state_path(env).write_text(json.dumps(data), encoding="utf-8")


def _state(env) -> dict:
    path = _state_path(env)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _run(env, *args):
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True, text=True, env=env["env"],
    )


def test_compact_at_writes_the_session_key(env):
    result = _run(env, "--compact-at", "35")
    assert result.returncode == 0, result.stderr
    assert _state(env)["session_hard_threshold"] == 35


def test_compact_at_never_writes_the_statusline_echo_key(env):
    """The constraint, asserted directly. `hard_threshold` is rewritten by the
    status line on every render, so a value stored there lasts one turn."""
    _run(env, "--compact-at", "35")
    assert "hard_threshold" not in _state(env)


def test_compact_at_reports_the_derived_soft_threshold(env):
    result = _run(env, "--compact-at", "35")
    assert "30" in result.stdout


@pytest.mark.parametrize("good", ["15", "90"])
def test_a_value_exactly_on_a_bound_is_accepted(env, good):
    """The refused set was 5, 95 and 0, and none of them stands on a bound.

    `checkpoint-paths.py` refuses on `hard < MIN or hard > MAX`, so both bounds
    are INCLUSIVE and the message says so: "outside 15-90". Nothing asserted the
    ends. Either comparison could gain an `=` and the CLI would refuse the exact
    numbers it tells the operator to use, with a message naming them as legal.
    """
    result = _run(env, "--compact-at", good)
    assert result.returncode == 0, result.stderr
    assert _state(env)["session_hard_threshold"] == int(good)


@pytest.mark.parametrize("bad", ["5", "95", "0"])
def test_a_value_outside_the_bounds_is_refused(env, bad):
    result = _run(env, "--compact-at", bad)
    assert result.returncode != 0
    assert "15" in result.stderr and "90" in result.stderr
    assert "session_hard_threshold" not in _state(env)


def test_a_token_that_is_not_a_number_is_refused(env):
    result = _run(env, "--compact-at", "soon")
    assert result.returncode != 0
    assert "session_hard_threshold" not in _state(env)


def test_a_threshold_at_or_below_the_current_fill_is_refused(env):
    """CAP-4. The operator chose refusal over accept-and-warn."""
    _write_state(env, {"used_percentage": 46.0})
    result = _run(env, "--compact-at", "35")
    assert result.returncode != 0
    assert "35" in result.stderr and "46" in result.stderr
    assert "session_hard_threshold" not in _state(env)


def test_a_threshold_above_the_current_fill_is_accepted(env):
    _write_state(env, {"used_percentage": 20.0})
    result = _run(env, "--compact-at", "35")
    assert result.returncode == 0, result.stderr
    assert _state(env)["session_hard_threshold"] == 35


def test_an_unknown_fill_accepts_and_says_the_check_did_not_run(env):
    """No `used_percentage` means the session has not rendered yet. Per
    .claude/rules/scope-claims.md, say the check could not run rather than
    letting silence read as a pass."""
    result = _run(env, "--compact-at", "35")
    assert result.returncode == 0, result.stderr
    assert "not checked against the current fill" in result.stdout


@pytest.mark.parametrize("junk", ["n/a", "", [], {"a": 1}])
def test_an_unparseable_fill_does_not_traceback(env, junk):
    """The state file is hand-editable, and `_session_hard` is hardened against
    exactly this class of value from exactly this file. The fill reader is too."""
    _write_state(env, {"used_percentage": junk})
    result = _run(env, "--compact-at", "35")
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert _state(env)["session_hard_threshold"] == 35


def test_the_refusal_names_the_reading_as_one_render_old(env):
    """scope-claims.md: `used_percentage` has one writer, and only on a render
    that measured. Stating it as the present fill would over-claim."""
    _write_state(env, {"used_percentage": 46.0})
    result = _run(env, "--compact-at", "35")
    assert result.returncode != 0
    assert "one render old" in result.stderr


def test_compact_at_off_clears_the_key(env):
    _run(env, "--compact-at", "35")
    result = _run(env, "--compact-at", "off")
    assert result.returncode == 0, result.stderr
    assert "session_hard_threshold" not in _state(env)
    assert "45" in result.stdout


def test_compact_at_status_is_read_only(env):
    _run(env, "--compact-at", "35")
    before = _state(env)
    result = _run(env, "--compact-at", "status")
    assert result.returncode == 0
    assert "35" in result.stdout and "30" in result.stdout
    assert _state(env) == before


def test_status_reports_when_the_threshold_was_set(env):
    _run(env, "--compact-at", "35")
    result = _run(env, "--compact-at", "status")
    assert "set at:" in result.stdout


def test_setting_a_threshold_raises_unattended(env):
    """Operator directive, 2026-08-22: one command, not two.

    Until then a bare `--compact-at 35` only moved the point at which the hook
    ASKED, and the operator had to follow it with `--unattended on` for anything
    to compact. He types the pair together every time, so the pair is now one
    command. A threshold set and never acted on was the failure mode, not a
    switch raised without being asked for.

    `raise_unattended` raises `session_auto` too, which is what makes the
    compaction driven rather than merely offered.
    """
    result = _run(env, "--compact-at", "35")
    assert result.returncode == 0, result.stderr
    state = _state(env)
    assert state["session_hard_threshold"] == 35
    assert state["session_unattended"] is True
    assert state["session_auto"] is True
    assert "unattended" in result.stdout.lower()


def test_a_refused_threshold_raises_nothing(env):
    """The switch rides on the write, so a refusal must leave the session alone.

    Both refusal paths: out of range, and at or below the last rendered fill.
    """
    for setup, value in (({}, "5"), ({"used_percentage": 60}, "35")):
        _write_state(env, setup)
        result = _run(env, "--compact-at", value)
        assert result.returncode == 2, result.stdout
        state = _state(env)
        assert "session_unattended" not in state, f"{value} raised the switch anyway"
        assert "session_auto" not in state


def test_status_and_off_raise_nothing(env):
    """Reading a value and clearing one are not the operator asking to work."""
    _run(env, "--compact-at", "35")
    _run(env, "--unattended", "off")
    for arg in ("status", "off"):
        result = _run(env, "--compact-at", arg)
        assert result.returncode == 0, result.stderr
        assert _state(env).get("session_unattended") is not True, arg


def test_a_live_stretch_is_not_reset_by_a_new_threshold(env):
    """Re-raising would clear the window, and the window is the running stretch.

    `raise_unattended` pops the continuation counter and every window key, so
    calling it on a session already unattended would silently hand the stretch a
    fresh ceiling. The operator asked for the two commands to become one, not for
    a threshold change to restart his run.
    """
    _write_state(env, {"session_unattended": True, "session_auto": True,
                       "unattended_continuations": 7})
    result = _run(env, "--compact-at", "35")
    assert result.returncode == 0, result.stderr
    state = _state(env)
    assert state["session_hard_threshold"] == 35
    assert state["unattended_continuations"] == 7, "the running stretch was reset"
    assert "already on" in result.stdout.lower()


# --------------------------------------------------------------------------
# The status line. It is the SOLE producer of `needs_compact_offer`, which
# eventually stamps `last_offer_at` - the floor `_request_compaction` hands to
# `_handoff_since`. So resolving the threshold per session here is a behaviour
# fix, not a display one: a status line stuck on the environment pair would queue
# no offer at the session's own threshold, and the number would look set while
# nothing happened.
# --------------------------------------------------------------------------

import importlib.util  # noqa: E402

STATUSLINE = ROOT / ".claude" / "hooks" / "checkpoint-statusline.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _render(env, payload: dict):
    return subprocess.run(
        [sys.executable, str(STATUSLINE)],
        input=json.dumps(payload), capture_output=True, text=True, env=env["env"],
    )


def _payload(env, used: float):
    return {
        "session_id": SESSION,
        "cwd": str(env["project"]),
        "workspace": {"current_dir": str(env["project"])},
        "context_window": {"used_percentage": used, "remaining_percentage": 100 - used},
        "model": {"display_name": "Claude"},
    }


def test_the_statusline_queues_the_offer_at_the_session_threshold(env):
    """36% used, session threshold 35, environment still 45."""
    _write_state(env, {"session_hard_threshold": 35})
    result = _render(env, _payload(env, 36.0))
    assert result.returncode == 0, result.stderr
    state = _state(env)
    assert state["offer_level"] == "hard"
    assert state["needs_compact_offer"] is True


def test_without_a_session_threshold_36_percent_queues_nothing(env):
    """The same render against the environment pair. This is the assertion that
    makes the test above mean something."""
    result = _render(env, _payload(env, 36.0))
    assert result.returncode == 0, result.stderr
    assert _state(env)["needs_compact_offer"] is False


def test_the_echo_keys_report_the_resolved_pair(env):
    _write_state(env, {"session_hard_threshold": 35})
    _render(env, _payload(env, 20.0))
    state = _state(env)
    assert (state["soft_threshold"], state["hard_threshold"]) == (30, 35)
    assert state["session_hard_threshold"] == 35, "the operator's key survived the render"


def test_the_number_shows_wherever_compaction_can_fire():
    """CAP-5. The test is not "is the mode on" but "can the driven compaction
    fire". `_request_compaction` gates on `auto_mode OR unattended_mode`, so a
    PAUSED stretch still compacts - the stretch ended, the switch did not."""
    sl = _load(STATUSLINE, "_sl_threshold")
    assert "35%" in sl.autonomy_segment(True, False, False, 35)          # auto
    assert "35%" in sl.autonomy_segment(False, True, False, 35)          # unattended
    assert "35%" in sl.autonomy_segment(False, True, True, 35)           # paused


def test_manual_stays_bare():
    """In manual the hook only ASKS; it never compacts. A number there would
    describe something that does not happen."""
    sl = _load(STATUSLINE, "_sl_threshold_manual")
    segment = sl.autonomy_segment(False, False, False, 35)
    assert "35" not in segment
    assert "manual" in segment


def test_the_number_shows_even_when_it_came_from_the_environment():
    """A number that appeared only after an override would leave the operator
    unable to tell "not set" from "not working"."""
    sl = _load(STATUSLINE, "_sl_threshold_env")
    assert "45%" in sl.autonomy_segment(True, False, False, 45)


def test_the_rendered_bar_carries_the_session_number(env):
    _write_state(env, {"session_hard_threshold": 35, "session_auto": True})
    result = _render(env, _payload(env, 20.0))
    assert result.returncode == 0, result.stderr
    assert "35%" in result.stdout


# --------------------------------------------------------------------------
# The CLI and the reader are two copies of one bound
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bound", [CP.HARD_THRESHOLD_MIN, CP.HARD_THRESHOLD_MAX])
def test_the_reader_accepts_the_exact_bounds_the_cli_accepts(bound):
    """`test_a_value_exactly_on_a_bound_is_accepted` above proves the CLI takes
    15 and 90 and writes the key. It does not prove anything READS the key back.

    `_session_hard` carries its own copy of the same comparison, and MEASURED
    2026-09-01 either half of it could gain an `=` with this whole file green:
    the CLI would accept `--compact-at 90`, print "compaction at 90%", write
    `session_hard_threshold: 90`, and every reader would silently resolve the
    environment's 45 instead. The operator sees a number that is set, stored,
    and echoed back at him, and nothing in the session uses it.
    """
    cfg = CP.config({"session_hard_threshold": bound})
    assert cfg["hard"] == bound
    assert cfg["soft"] == bound - CP.SOFT_OFFSET


@pytest.mark.parametrize("just_outside", [CP.HARD_THRESHOLD_MIN - 1,
                                          CP.HARD_THRESHOLD_MAX + 1])
def test_the_reader_refuses_one_step_outside_each_bound(just_outside):
    """The other jaw. A reader that accepted everything would satisfy the pair
    above; the existing out-of-range cases (5, 95, 0) are far enough out that a
    bound moved by one still refuses them."""
    assert CP.config({"session_hard_threshold": just_outside})["hard"] == 45


def test_an_environment_pair_that_is_exactly_equal_falls_back(monkeypatch):
    """`soft >= hard`, not `soft > hard`, and only the strict case was pinned.

    `test_an_inverted_environment_pair_still_falls_back` uses 50/45, which both
    comparisons reject. Equal is the reachable one: a hand-edited settings file
    with the same number twice. Without the `=`, soft and hard coincide, so the
    soft reminder and the hard threshold fire on the same render and the graded
    warning the two-tier design exists for disappears.
    """
    monkeypatch.setenv("CLAUDE_HANDOFF_SOFT_THRESHOLD", "45")
    monkeypatch.setenv("CLAUDE_HANDOFF_HARD_THRESHOLD", "45")
    assert (CP.config(None)["soft"], CP.config(None)["hard"]) == (25, 30)
