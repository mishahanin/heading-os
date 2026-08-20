"""The operator surface must describe the mechanism that actually ships.

The checkpoint system has three surfaces a human reads before it has ever run:
`.claude/skills/checkpoint/SKILL.md`, the always-on router row generated from
that skill's frontmatter, and the sentences `scripts/checkpoint-paths.py` prints
when a switch moves. None of the three is executed, so none of them fails a
suite when the code underneath changes - which is exactly how three of them came
to describe the pre-2026-08-19 mechanism after the mechanism had moved.

Each test below pins one prose claim to the code path that decides whether it is
true, so the sentence cannot drift alone. Per `.claude/rules/scope-claims.md`: a
printed sentence may say only what its method establishes, and these three were
saying the opposite of what their method does.
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import checkpoint_paths as CP  # noqa: E402

SKILL = ROOT / ".claude" / "skills" / "checkpoint" / "SKILL.md"
CLI = ROOT / "scripts" / "checkpoint-paths.py"
ROUTER = ROOT / ".claude" / "rules" / "skill-router.md"


def _offer_module():
    spec = importlib.util.spec_from_file_location(
        "checkpoint_offer_surface",
        str(ROOT / ".claude" / "hooks" / "checkpoint-offer.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cli_module():
    spec = importlib.util.spec_from_file_location("checkpoint_paths_cli", str(CLI))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ============================================================
# Auto mode drives a compaction. Three surfaces used to deny it.
# ============================================================


def test_auto_mode_alone_reaches_the_driven_compaction_path(monkeypatch):
    """The behaviour the prose has to match: `session_auto` is enough.

    `_request_compaction` gates on `auto_mode(state) or unattended_mode(state)`,
    so auto mode on its own reaches the handoff check and, past it, the HERDR
    submit. Anything that says only Claude Code can compact an auto-mode session
    is false, whichever surface says it.
    """
    module = _offer_module()
    seen = {}

    def _fake_handoff(project, session, since):
        seen["called"] = True
        return False

    monkeypatch.setattr(module, "_handoff_since", _fake_handoff)
    monkeypatch.setattr(
        module.HA,
        "resolve_pane",
        lambda session: (_ for _ in ()).throw(
            AssertionError("must not reach HERDR without a handoff on disk")
        ),
    )

    state = {"session_auto": True, "offer_bucket": 9, "last_offer_at": "2026-08-19T00:00:00+00:00"}
    module._request_compaction(
        {"session_id": "surface-test"}, state, Path("/nonexistent/state.json"),
        ROOT, 99.0,
    )
    assert seen.get("called"), (
        "auto mode alone did not reach the driven-compaction path; if this is "
        "now correct, the three prose surfaces below must change with it"
    )


def test_the_auto_switch_message_does_not_deny_driven_compaction(tmp_path):
    """`--auto on` printed 'no hook can trigger it' while a hook triggered it."""
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    env["CLAUDE_CODE_SESSION_ID"] = "cccccccc-0000-0000-0000-000000000000"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, str(CLI), "--auto", "on"],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    text = proc.stdout.lower()
    for denial in ("no hook can trigger", "compaction is unchanged"):
        assert denial not in text, (
            f"--auto on still prints {denial!r}; the Stop hook does submit "
            "/compact through HERDR once the auto handoff is on disk"
        )


def test_the_skill_page_does_not_deny_driven_compaction():
    body = SKILL.read_text(encoding="utf-8")
    assert "Nothing here triggers compaction on its own" not in body, (
        "SKILL.md still denies that auto mode drives a compaction"
    )


def test_the_public_docs_do_not_deny_driven_compaction():
    """docs/PLUGINS.md ships the same claim to a stranger installing the plugin.

    The plugin bundle carries checkpoint-offer.py and all of scripts/utils/, so
    the HERDR submit is in the shipped code, not only in this workspace.
    """
    body = (ROOT / "docs" / "PLUGINS.md").read_text(encoding="utf-8")
    assert "Nothing here triggers compaction" not in body, (
        "PLUGINS.md still tells plugin users that no hook can compact"
    )
    assert "HERDR" in body, "PLUGINS.md must name the path that does compact"


def test_the_hooks_reference_bounds_the_claimant_courtesy():
    row = ""
    for line in (ROOT / "docs" / "HOOKS-REFERENCE.md").read_text(
        encoding="utf-8"
    ).splitlines():
        if line.startswith("| `checkpoint-offer.py`"):
            row = line
    assert row, "the checkpoint-offer row left HOOKS-REFERENCE.md"
    assert "Stays silent when something already drives the Stop event" not in row, (
        "the row states the courtesy without its hard-threshold bound"
    )
    assert "hard threshold" in row


# ============================================================
# Nothing lowers the operator's switch.
# ============================================================


def test_a_paused_stretch_leaves_the_switch_up(tmp_path):
    """`_pause_unattended` stops the stretch and never lowers the mode."""
    module = _offer_module()
    state_path = tmp_path / "checkpoint-x.json"
    CP.write_json_atomic(state_path, {"session_unattended": True, "session_auto": True})
    module._pause_unattended(
        CP.read_json(state_path), state_path, "reached the ceiling of 100 continuations"
    )
    after = CP.read_json(state_path)
    assert after["session_unattended"] is True, (
        "the fuse lowered the operator's switch; the router row and SKILL.md "
        "both describe the opposite"
    )
    assert after["unattended_stop_reason"].startswith("reached the ceiling")


def test_no_surface_claims_a_fuse_lowers_the_mode():
    for path in (SKILL, ROUTER):
        body = path.read_text(encoding="utf-8")
        assert "fuses lower the mode" not in body, (
            f"{path.name} claims a fuse lowers the unattended switch; "
            "_pause_unattended leaves it up"
        )


# ============================================================
# The countdown length is configured, not fixed at 60.
# ============================================================


def test_the_wait_is_whatever_the_environment_sets(monkeypatch):
    monkeypatch.setenv("CLAUDE_HANDOFF_UNATTENDED_WAIT", "10")
    assert CP.wait_seconds() == 10
    monkeypatch.setenv("CLAUDE_HANDOFF_UNATTENDED_WAIT", "600")
    assert CP.wait_seconds() == 60, "out-of-range falls back to the 60s default"


def test_no_surface_hardcodes_a_sixty_second_countdown():
    """The router row is always-on context: a fixed number there is read as fact.

    This workspace runs a 10-second wait (`CLAUDE_HANDOFF_UNATTENDED_WAIT` in
    .claude/settings.local.json), so "a shown 60-second countdown" was wrong on
    the very tree that generated it.
    """
    for path in (SKILL, ROUTER):
        body = path.read_text(encoding="utf-8")
        assert "60-second countdown" not in body, (
            f"{path.name} names a countdown length the environment decides"
        )


# ============================================================
# The claimant courtesy stops at the hard threshold.
# ============================================================


def test_a_claimant_does_not_silence_the_hook_above_hard(tmp_path, monkeypatch):
    """Below hard a claimant returns; at or above hard the hook keeps going.

    Driven through `main()` with a real payload, because the placement of that
    `return` inside `main()` is the whole behaviour: an unattended run reached
    617k tokens with nothing on disk through the version that returned here.
    """
    module = _offer_module()
    monkeypatch.setenv("CLAUDE_HANDOFF_SOFT_THRESHOLD", "40")
    monkeypatch.setenv("CLAUDE_HANDOFF_HARD_THRESHOLD", "45")
    project = tmp_path / "project"
    (project / ".claude" / "state").mkdir(parents=True)
    session = "dddddddd-0000-0000-0000-000000000000"
    state_path = CP.state_path(project, CP.safe_slug(session))

    calls = []
    monkeypatch.setattr(
        module, "_request_compaction",
        lambda *a, **k: calls.append(a[4]),
    )

    def _run(used: float) -> None:
        CP.write_json_atomic(state_path, {
            "session_auto": True,
            "used_percentage": used,
            "needs_compact_offer": False,
        })
        payload = {
            "session_id": session,
            "cwd": str(project),
            "background_tasks": [{"status": "running"}],
        }
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        with redirect_stdout(io.StringIO()):
            module.main()

    _run(41.0)
    assert calls == [], "a claimant below the hard threshold must silence the hook"
    _run(46.0)
    assert calls == [46.0], (
        "a claimant above the hard threshold silenced the hook; the last save "
        "before compaction cannot wait a turn"
    )


def test_the_skill_page_bounds_the_claimant_courtesy():
    body = SKILL.read_text(encoding="utf-8")
    assert "The mode stays quiet whenever something else already drives the Stop event." not in body, (
        "SKILL.md states the courtesy without its hard-threshold bound"
    )


def test_the_documented_stamp_fallback_matches_the_script(tmp_path, monkeypatch):
    """SKILL.md's hand fallback must produce the stamp the script produces.

    The script moved the archive stamp from `utc_now()` to `local_now()` on
    2026-08-20, so the page's `date -u` fallback would file a 02:00 handoff under
    the previous calendar day - the exact defect that move corrected.
    """
    body = SKILL.read_text(encoding="utf-8")
    assert "date -u +'%Y-%m-%d-%H%M%S'" not in body, (
        "SKILL.md still tells the fallback to stamp in UTC"
    )
    assert "date +'%Y-%m-%d-%H%M%S'" in body

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "ffffffff-0000-0000-0000-000000000000")
    stamp = _cli_module().collect()["stamp"]
    # To the hour, which no test run can straddle twice.
    assert stamp[:13] == CP.local_now().strftime("%Y-%m-%d-%H%M%S")[:13], (
        "the script's stamp is not the operator's local time"
    )
    if CP.local_now().utcoffset() != CP.utc_now().utcoffset():
        assert stamp[:13] != CP.utc_now().strftime("%Y-%m-%d-%H%M%S")[:13], (
            "local and UTC differ here, so the stamp must not be the UTC one"
        )


# ============================================================
# The CLI's own help and behaviour agree.
# ============================================================


def test_every_documented_switch_value_round_trips(tmp_path, monkeypatch):
    """on / off / status on both switches, against a synthetic session."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "eeeeeeee-0000-0000-0000-000000000000")
    monkeypatch.delenv("CLAUDE_HANDOFF_AUTO", raising=False)
    monkeypatch.delenv("CLAUDE_HANDOFF_UNATTENDED", raising=False)
    cli = _cli_module()
    slug = CP.safe_slug("eeeeeeee-0000-0000-0000-000000000000")
    state_path = CP.state_path(tmp_path, slug)

    def _call(*argv) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            assert cli.main(list(argv)) == 0
        return buffer.getvalue()

    assert "unattended=off" in _call("--unattended", "status")
    _call("--unattended", "on")
    state = CP.read_json(state_path)
    assert state["session_unattended"] is True
    assert state["session_auto"] is True, "`on` must imply auto, as --help says"

    _call("--done", "plan X: 7 of 7")
    assert CP.read_json(state_path)["unattended_done_note"] == "plan X: 7 of 7"
    assert CP.read_json(state_path)["session_unattended"] is True, (
        "--done must not touch the operator's switch"
    )

    _call("--unattended", "off")
    state = CP.read_json(state_path)
    assert state["session_unattended"] is False
    assert "session_auto" not in state, (
        "off must RESTORE the prior auto, which here was unset - not pin False"
    )
