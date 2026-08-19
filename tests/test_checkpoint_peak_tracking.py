"""Where compaction fired must survive the compaction.

Measured on 2026-08-19. Auto-compact fired with 38% of the window remaining;
seven minutes later `.claude/state/checkpoint-<slug>.json` read
`used_percentage: 11.0` and carried nothing else about the event. The statusline
rewrites the whole state dict after every turn, so the field is the LAST reading
and never the largest, and the post-compact render had already overwritten the
only number that answers the operator's question: does auto-compact fire at the
threshold the environment configures?

The fix is two hooks agreeing on one shared pair of helpers in
scripts/utils/checkpoint_paths.py - the statusline keeps a monotone peak, and
the PostCompact save closes that peak into a dated record before it resets the
hysteresis. This file holds the four properties that make the record worth
reading:

  - the peak survives a lower reading (otherwise it is just used_percentage);
  - a compaction turns it into a history entry naming configured vs observed;
  - the entry is a LOWER BOUND, and is named as one, because the statusline
    renders per turn and the harness can compact between two renders
    (.claude/rules/scope-claims.md);
  - the CLI can read it with the browser closed (.claude/rules/console-first.md).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".claude" / "hooks"
STATUSLINE = HOOKS / "checkpoint-statusline.py"
SAVE = HOOKS / "checkpoint-save.py"
CLI = ROOT / "scripts" / "checkpoint-paths.py"

SESSION = "cccccccc-1111-2222-3333-555555555555"

sys.path.insert(0, str(ROOT))
from scripts.utils import checkpoint_paths as CP  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    e = dict(os.environ)
    e["HEADING_OS_DATA"] = str(data)
    e["CLAUDE_PROJECT_DIR"] = str(project)
    e.pop("CLAUDE_HANDOFF_AUTO", None)
    e["CLAUDE_HANDOFF_SOFT_THRESHOLD"] = "40"
    e["CLAUDE_HANDOFF_HARD_THRESHOLD"] = "45"
    e["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = "50"
    e.pop("CLAUDE_CODE_AUTO_COMPACT_WINDOW", None)
    return {"env": e, "project": project, "data": data}


def _run(hook: Path, env: dict, payload: dict, argv=()) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(hook), *argv],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env["env"],
    )


def _statusline(env, used, window=1_000_000):
    return _run(STATUSLINE, env, {
        "session_id": SESSION,
        "cwd": str(env["project"]),
        "workspace": {"project_dir": str(env["project"])},
        "context_window": {
            "used_percentage": used,
            "remaining_percentage": 100 - used,
            "context_window_size": window,
        },
    })


def _compact(env, trigger="auto"):
    return _run(SAVE, env, {
        "session_id": SESSION,
        "cwd": str(env["project"]),
        "workspace": {"project_dir": str(env["project"])},
        "trigger": trigger,
        "compact_summary": "a summary",
        "transcript_path": "",
    })


def _state(env) -> dict:
    path = env["project"] / ".claude" / "state" / f"checkpoint-{CP.safe_slug(SESSION)}.json"
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# The peak is a maximum, not the last reading
# --------------------------------------------------------------------------

def test_the_peak_survives_a_lower_reading(env):
    """The whole defect in one test: 62 then 11 must not read as 11."""
    _statusline(env, 30)
    _statusline(env, 62)
    _statusline(env, 11)
    state = _state(env)
    assert state["peak_used_percentage"] == 62.0
    assert state["used_percentage"] == 11.0


def test_the_peak_rises_with_a_higher_reading(env):
    _statusline(env, 30)
    _statusline(env, 62)
    assert _state(env)["peak_used_percentage"] == 62.0


def test_a_missing_reading_leaves_the_peak_alone(env):
    """A payload with no context_window must not erase the measurement."""
    _statusline(env, 62)
    _run(STATUSLINE, env, {
        "session_id": SESSION,
        "cwd": str(env["project"]),
        "workspace": {"project_dir": str(env["project"])},
    })
    assert _state(env)["peak_used_percentage"] == 62.0


# --------------------------------------------------------------------------
# A compaction closes the peak into a record that outlives it
# --------------------------------------------------------------------------

def test_compaction_records_the_level_it_fired_at(env):
    _statusline(env, 62)
    _compact(env)
    history = _state(env)["compact_history"]
    assert len(history) == 1
    assert history[0]["used_pct_at_or_above"] == 62.0
    assert history[0]["trigger"] == "auto"


def test_the_record_names_what_was_configured(env):
    """Configured versus observed, in one line, or the record settles nothing."""
    _statusline(env, 62)
    _compact(env)
    assert _state(env)["compact_history"][0]["configured"] == "percent:50"


def test_the_record_carries_the_window_the_harness_reported(env):
    _statusline(env, 62)
    _compact(env)
    assert _state(env)["compact_history"][0]["context_window_size"] == 1_000_000


def test_the_peak_restarts_after_a_compaction(env):
    """Otherwise the second compaction inherits the first one's number."""
    _statusline(env, 62)
    _compact(env)
    assert "peak_used_percentage" not in _state(env)
    _statusline(env, 11)
    _statusline(env, 44)
    _compact(env)
    history = _state(env)["compact_history"]
    assert [e["used_pct_at_or_above"] for e in history] == [62.0, 44.0]


def test_a_compaction_with_no_reading_records_nothing(env):
    """Silence beats a fabricated 0%, which would read as "fires immediately"."""
    _compact(env)
    assert "compact_history" not in _state(env)


def test_the_history_is_bounded(env):
    state = {}
    for i in range(CP.COMPACT_HISTORY_MAX + 5):
        CP.record_peak(state, float(i + 1), "2026-08-19T00:00:00+00:00")
        CP.record_compaction(state, f"2026-08-19T00:00:{i:02d}+00:00", "auto")
    history = state["compact_history"]
    assert len(history) == CP.COMPACT_HISTORY_MAX
    # The NEWEST entries are the ones kept; dropping the tail would leave the
    # record permanently describing the first compaction of the session.
    assert history[-1]["used_pct_at_or_above"] == float(CP.COMPACT_HISTORY_MAX + 5)


def test_the_record_never_claims_to_be_the_firing_point(env):
    """The statusline renders per turn, so the true level is at or above this.

    The field name is the claim. A name like `fired_at_used_pct` would assert a
    precision the method never established.
    """
    _statusline(env, 62)
    _compact(env)
    entry = _state(env)["compact_history"][0]
    assert "used_pct_at_or_above" in entry
    assert not any(k.startswith("fired_at_") for k in entry)


# --------------------------------------------------------------------------
# Readable from a terminal
# --------------------------------------------------------------------------

def test_the_cli_prints_the_record(env):
    _statusline(env, 62)
    _compact(env)
    result = subprocess.run(
        [sys.executable, str(CLI), "--compact-history"],
        capture_output=True, text=True, env=env["env"],
    )
    assert result.returncode == 0
    assert "62.0" in result.stdout
    assert "percent:50" in result.stdout


def test_the_cli_says_so_when_nothing_was_recorded(env):
    """An empty page reads as "it never fires", which is a different claim."""
    result = subprocess.run(
        [sys.executable, str(CLI), "--compact-history"],
        capture_output=True, text=True, env=env["env"],
    )
    assert result.returncode == 0
    assert "No compaction has been recorded yet" in result.stdout
