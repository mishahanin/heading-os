"""The autonomy switches are visible, and the assistant never lowers them.

Both properties were added on 2026-08-19 after the unattended mechanism ran twice
in one session and compacted zero times.

The cause was a loop with two halves, and this file pins one guard for each.

`_request_compaction` fires only when the switch is UP and an `_handoff_auto_`
file for the session is already on disk. The Stop hook's continuation prose used
to instruct the assistant to run `--unattended off` "if the work is finished or
you were waiting on a judgement only the operator can make". Real work reaches
the operator's decision at the end of nearly every stretch, so the switch went
down before any auto handoff had been written, every time. Neither half of the
condition was ever satisfied together.

The second half of the loop was that nothing showed the switch's state until a
checkpoint was already due, so a switch that was off and a mechanism that was
broken looked identical from the terminal.
"""

import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / ".claude" / "hooks"
STATUSLINE = HOOKS / "checkpoint-statusline.py"
OFFER = HOOKS / "checkpoint-offer.py"

sys.path.insert(0, str(ROOT))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def statusline():
    os.environ.setdefault("TERM", "xterm-256color")
    return _load(STATUSLINE, "_sl_autonomy")


@pytest.mark.parametrize(
    "auto,unattended,expected",
    [
        (False, False, "manual"),
        (True, False, "auto"),
        (False, True, "unattended"),
        (True, True, "unattended"),
    ],
)
def test_autonomy_segment_names_every_state(statusline, auto, unattended, expected):
    """All four switch combinations render, and none renders as empty.

    `off` is rendered too. That is the point: an absent segment cannot be told
    apart from a hook that stopped running.
    """
    segment = statusline.autonomy_segment(auto, unattended)
    assert expected in segment
    assert segment.strip(), "the off state must still print something"


def test_autonomy_segment_present_at_every_level(statusline):
    """The segment is in the line whether or not a checkpoint is due.

    Before this change the only hint was an `auto-` prefix inside the checkpoint
    tag, which appears solely at the soft and hard thresholds. Below them the
    operator saw nothing at all.
    """
    payload = {"workspace": {"current_dir": str(ROOT)}, "model": {"display_name": "M"}}
    for level in (None, "soft", "hard"):
        line = statusline.build_status_line(payload, ROOT, 55.0, level, False, True)
        assert "unattended" in line, f"missing at level={level}"
        off = statusline.build_status_line(payload, ROOT, 55.0, level, False, False)
        assert "manual" in off, f"missing at level={level}"


def test_unattended_defaults_off_in_the_signature(statusline):
    """A caller that predates this change still renders, and renders honestly.

    The parameter is optional so the older four-argument call site cannot crash,
    but the default is False, so an un-passed switch is never shown as live.
    """
    payload = {"workspace": {"current_dir": str(ROOT)}, "model": {"display_name": "M"}}
    line = statusline.build_status_line(payload, ROOT, 10.0, None, False)
    assert "manual" in line
    assert "unattended" not in line


def test_an_ended_stretch_is_not_rendered_as_a_running_one(statusline):
    """The done marker and the ceiling end a stretch WITHOUT lowering the switch.

    That is deliberate - `session_unattended` is the operator's - so between the
    03:00 finish and the operator reading the bar, `unattended_mode()` is still
    true while the next pause hands the turn back instead of continuing. Measured
    2026-08-20 against the shipped hook: the two states rendered the same bytes,
    while `--unattended status` reported `DONE:` and `PAUSED:` for the second.
    """
    live = statusline.autonomy_segment(True, True, False)
    ended = statusline.autonomy_segment(True, True, True)
    assert live != ended, (
        "a stretch stopped by the done marker renders exactly like one still "
        "running, so the bar cannot tell a working night from a finished one"
    )
    assert "paused" in ended, (
        f"the ended state does not name itself the way the CLI does: {ended!r}"
    )
    assert "unattended" in ended, "the switch itself is still up and must show"


@pytest.mark.parametrize("marker", ["unattended_done_at", "unattended_paused_at"])
def test_the_bar_reads_the_end_of_a_stretch_out_of_the_state_file(
    statusline, tmp_path, marker
):
    """End to end through `main()`, not just the segment.

    The segment taking a third argument proves nothing on its own: what decides
    what the operator sees is whether `main()` reads the two window keys and
    passes them down. Driven as a subprocess so the real stdin payload, the real
    state file and the real write path are all exercised.
    """
    import json
    import os
    import re
    import subprocess

    project = tmp_path / "project"
    (project / ".claude" / "state").mkdir(parents=True)
    session = "ended-0000-0000-0000-000000000000"
    state = project / ".claude" / "state" / f"checkpoint-{session[:32]}.json"
    env = dict(os.environ)
    # Created, not just named. `env_data_root()` ignores an override pointing at
    # a path that does not exist and falls back to the live overlay, so a bare
    # `str(tmp_path / "data")` is an isolation claim the seam never honoured.
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    env["HEADING_OS_DATA"] = str(data_root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("CLAUDE_HANDOFF_UNATTENDED", None)
    payload = json.dumps({
        "session_id": session,
        "cwd": str(project),
        "workspace": {"project_dir": str(project), "current_dir": str(project)},
        "model": {"display_name": "M"},
        "context_window": {"used_percentage": 46.0, "remaining_percentage": 54.0},
    })

    def render() -> str:
        out = subprocess.run(
            [sys.executable, str(STATUSLINE)],
            input=payload, capture_output=True, text=True, env=env,
        ).stdout
        return re.sub(r"\x1b\[[0-9;]*m", "", out)

    state.write_text(json.dumps({"session_unattended": True}), encoding="utf-8")
    running = render()
    assert "unattended" in running and "paused" not in running, (
        f"a live stretch was already rendered as ended: {running!r}"
    )

    fresh = json.loads(state.read_text(encoding="utf-8"))
    fresh[marker] = "2026-08-20T03:00:00+00:00"
    state.write_text(json.dumps(fresh), encoding="utf-8")
    assert "unattended paused" in render(), (
        f"{marker} is set, the next pause hands the turn back, and the bar still "
        f"reads as a run under way: {render()!r}"
    )


@pytest.fixture(scope="module")
def offer():
    return _load(OFFER, "_co_autonomy")


def test_options_detail_prints_once_per_session(offer, tmp_path_factory):
    """The mechanism explanation is worth reading once and is noise on the fifth.

    The operator reads every blocking Stop hook message in full, and the
    threshold offer repeats at each hysteresis bucket. So the four numbered
    options stay on every offer, and the paragraph explaining HERDR and the
    grace period is gated on a state flag.
    """
    import json

    state_path = tmp_path_factory.mktemp("s") / "state.json"
    state_path.write_text(json.dumps({"session_id": "x"}), encoding="utf-8")

    first = offer.build_reason("hard", 62.0, 38.0, {"session_id": "x"}, state_path, "x")
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted.get("offer_detail_shown") is True

    second = offer.build_reason("hard", 67.0, 33.0, persisted, state_path, "x")

    assert "About option 2" in first
    assert "About option 2" not in second
    for options in ("`/checkpoint`", "`/compact`", "Continue as is"):
        assert options in first and options in second, "the choices must never be gated"
    assert len(second) < len(first) / 2


def test_continuation_message_stays_short(offer):
    """A cap with a number in it, because prose creep is what this file catches.

    The template reached about 1,950 characters by 2026-08-19 and printed at
    every pause of an unattended run. 600 leaves room for a real edit and fails
    on another paragraph.
    """
    rendered = offer.UNATTENDED_WRAPPER.format(used=62.0, wait=10, done=1, maximum=100)
    assert len(rendered) < 600, f"continuation grew back to {len(rendered)} chars"
    assert "Do not touch the unattended switch" in rendered
    assert "Never invent work" in rendered
    # The one instruction the mechanism cannot work without. The hook reads state
    # and never prose, so an assistant told only to "stop and say so" - which is
    # what this line replaced on 2026-08-19 - ends nothing, and the next pause
    # continues it again.
    assert "--done" in rendered, "the continuation no longer names the done marker"


def test_continuation_prose_does_not_tell_the_assistant_to_lower_the_switch():
    """The instruction that broke the mechanism must not come back.

    Asserted against the source text rather than a rendered message because the
    wrapper is a module-level template: if the sentence returns to it, it returns
    to every continuation the hook emits.
    """
    source = OFFER.read_text(encoding="utf-8")
    start = source.index("UNATTENDED_WRAPPER = ")
    wrapper = source[start : source.index('"""', source.index('"""', start) + 3)]
    assert "--unattended off" not in wrapper, (
        "the continuation prose again tells the assistant to lower the operator's "
        "switch; that is the defect this file exists for"
    )
    # Case-insensitive: the guard is the instruction, not its capitalisation,
    # and pinning the shouted form once failed this test on a shortening edit
    # that kept the rule intact.
    assert "do not touch the unattended switch" in wrapper.lower()
