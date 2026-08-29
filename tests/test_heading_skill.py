"""Tests for the F-10.3 headless skill runner (scripts/heading_cli.py `skill`).

The security-critical proof of this slice is the send boundary: for EVERY tier,
`build_skill_command` must exclude the outbound transports from `--allowedTools`
and name them under `--disallowedTools`. These are pure-Python assertions on the
constructed argv; no test invokes the real `claude` binary (it is not in CI).

The send-boundary assertions below name the transports LITERALLY. Three of them
read `for entry in SEND_DENY: assert entry in disallowed` until 2026-08-29,
against a `disallowed` that `build_skill_command` builds as `list(SEND_DENY)`:
the same object on both sides, so each loop held for every value of the
constant. Measured that day, with `SEND_DENY = []` those three still passed.
The file was not fully blind. `test_draft_tier_send_boundary` spells `"approve"`
and `"send-email.py"` out, and it caught both truncations (1 failed at one
entry, 2 failed at zero). What nothing here could catch was an OMISSION, and one
was on disk: `gmail-send.py` landed 2026-08-08 outside a denylist written
2026-07-09, and 13 tests passed over it for 21 days.

Whether the literal set below is the RIGHT set is a separate question, answered
against the scripts on disk in
tests/test_two_controls_that_measured_themselves.py.
"""

from scripts import heading_cli
from scripts.heading_cli import (
    ODIN_BRAIN_DENY_REL,
    PROPOSE_WRITE_REL,
    SEND_DENY,
    SKILL_ALLOWLIST,
    TIER_ALLOWED,
    build_skill_command,
    run_skill,
)
from scripts.utils.paths import get_data_root, get_workspace_root


# Written out, never derived from SEND_DENY. See the module docstring.
MUST_BE_DENIED = (
    "scripts/send-email.py",
    "scripts/gmail-send.py",
    "scripts/action-queue-execute.py",
    "scripts/fireside-bot.py",
    "scripts/action-queue.py approve",
)


def _assert_transports_denied(disallowed):
    """Every named transport is denied for both `python` and `python3`."""
    joined = " ".join(disallowed)
    for target in MUST_BE_DENIED:
        for interpreter in ("python", "python3"):
            assert f"Bash({interpreter} {target}:*)" in joined, (interpreter, target)


def _values_after(cmd, flag):
    """The value tokens immediately after `flag`, up to the next `--` option."""
    i = cmd.index(flag) + 1
    vals = []
    while i < len(cmd) and not cmd[i].startswith("--"):
        vals.append(cmd[i])
        i += 1
    return vals


def test_allowlist_and_tiers():
    assert SKILL_ALLOWLIST["state-check"]["tier"] == "read-only"
    assert SKILL_ALLOWLIST["queue-draft"]["tier"] == "draft"
    assert SKILL_ALLOWLIST["odin"]["tier"] == "propose"
    assert SKILL_ALLOWLIST["odin"]["args_prefix"] == ["reflect", "--propose"]
    assert set(TIER_ALLOWED) == {"read-only", "draft", "propose"}
    # Not `assert SEND_DENY`: a one-entry list is truthy too.
    _assert_transports_denied(SEND_DENY)


def test_read_only_tier_send_boundary():
    cmd = build_skill_command("state-check", [], tier="read-only")
    assert "-p" in cmd and "/state-check" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert cmd[cmd.index("--max-budget-usd") + 1] == "0.5"
    allowed = _values_after(cmd, "--allowedTools")
    assert allowed == ["Read"]
    assert not any(
        "send-email.py" in a or "action-queue.py" in a or a == "Write" for a in allowed
    )
    disallowed = _values_after(cmd, "--disallowedTools")
    _assert_transports_denied(disallowed)


def test_data_overlay_add_dir():
    """H1: the read-only command grants the external data root read access."""
    cmd = build_skill_command("state-check", [], tier="read-only")
    if get_data_root() != get_workspace_root():
        assert "--add-dir" in cmd
        assert cmd[cmd.index("--add-dir") + 1] == str(get_data_root())
    # The grant is read-only: it must NOT add Write to the allowed set.
    assert "Write" not in _values_after(cmd, "--allowedTools")


def test_draft_tier_send_boundary():
    """The draft tier grants deposit + write but never approve/send."""
    cmd = build_skill_command("x", [], tier="draft")
    allowed = _values_after(cmd, "--allowedTools")
    assert "Write" in allowed
    assert any("deposit" in a for a in allowed)
    assert not any("approve" in a for a in allowed)
    assert not any("send-email.py" in a for a in allowed)
    disallowed = _values_after(cmd, "--disallowedTools")
    assert any("approve" in d for d in disallowed)
    assert any("send-email.py" in d for d in disallowed)


def test_queue_draft_live_draft_boundary():
    """The live draft skill queue-draft is allowlisted at the draft tier, and its
    BUILT command grants the Action-Queue deposit but never approve or send - the
    send boundary holds for a real depositing skill, not only a synthetic tier."""
    assert SKILL_ALLOWLIST["queue-draft"]["tier"] == "draft"
    cmd = build_skill_command("queue-draft", [], tier=SKILL_ALLOWLIST["queue-draft"]["tier"])
    assert "/queue-draft" in cmd
    allowed = _values_after(cmd, "--allowedTools")
    assert any("action-queue.py deposit" in a for a in allowed)  # deposit granted
    assert not any("approve" in a for a in allowed)  # approve never granted
    assert not any("send-email.py" in a for a in allowed)  # send transport never granted
    disallowed = _values_after(cmd, "--disallowedTools")
    _assert_transports_denied(disallowed)  # every send transport is denied


def test_args_passthrough():
    cmd = build_skill_command("state-check", ["--foo", "bar"], tier="read-only")
    assert "/state-check --foo bar" in cmd


def test_refusal_without_vendor_call(monkeypatch):
    """A non-allowlisted skill exits 2 before any which/subprocess call."""
    calls = {"which": False, "run": False}

    def flag_which(*a, **k):
        calls["which"] = True
        return "claude"

    def boom_run(*a, **k):
        calls["run"] = True
        raise AssertionError("subprocess.run must not run on a refusal")

    monkeypatch.setattr(heading_cli.shutil, "which", flag_which)
    monkeypatch.setattr(heading_cli.subprocess, "run", boom_run)
    assert run_skill("nope", []) == 2
    assert not calls["which"] and not calls["run"]


def test_claude_absent_degrade(monkeypatch):
    """With claude off PATH the runner returns 3 and never shells out."""

    def boom_run(*a, **k):
        raise AssertionError("subprocess.run must not run when claude is absent")

    monkeypatch.setattr(heading_cli.shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr(heading_cli.subprocess, "run", boom_run)
    assert run_skill("state-check", []) == 3


def test_runner_flag_ordering(monkeypatch):
    """M1: `skill --budget 1.0 state-check` binds the budget before the name."""
    captured = {}

    def fake_build(skill, args, *, tier, budget_usd=heading_cli.DEFAULT_BUDGET_USD, model=None):
        captured["skill"] = skill
        captured["budget"] = budget_usd
        captured["args"] = args
        return ["true"]

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(heading_cli, "build_skill_command", fake_build)
    monkeypatch.setattr(heading_cli.shutil, "which", lambda *a, **k: "claude")
    monkeypatch.setattr(heading_cli.subprocess, "run", lambda *a, **k: _Proc())
    rc = heading_cli.main(["skill", "--budget", "1.0", "state-check"])
    assert rc == 0
    assert captured["skill"] == "state-check"
    assert captured["budget"] == 1.0


def test_propose_tier_write_scoped_to_proposals_dir():
    """The propose tier's write grant is a single path-scoped Edit(...) pattern
    for PROPOSE_WRITE_REL -- never a bare Write/Edit grant."""
    cmd = build_skill_command("odin", ["reflect", "--propose"], tier="propose")
    allowed = _values_after(cmd, "--allowedTools")
    assert "Write" not in allowed
    assert "Edit" not in allowed
    expected_root = str((get_data_root() / PROPOSE_WRITE_REL).resolve())
    matches = [a for a in allowed if a.startswith("Edit(//") and expected_root.lstrip("/") in a]
    assert len(matches) == 1
    pattern = matches[0]
    # Regression guard against _abs_pattern reintroducing the stray-third-slash bug.
    assert pattern.startswith("Edit(//")
    assert not pattern.startswith("Edit(///")


def test_propose_tier_denies_odin_brain():
    """The propose tier explicitly denies Edit for knowledge/odin-brain/**,
    alongside every existing SEND_DENY entry unchanged."""
    cmd = build_skill_command("odin", ["reflect", "--propose"], tier="propose")
    disallowed = _values_after(cmd, "--disallowedTools")
    expected_root = str((get_data_root() / ODIN_BRAIN_DENY_REL).resolve())
    matches = [d for d in disallowed if d.startswith("Edit(//") and expected_root.lstrip("/") in d]
    assert len(matches) == 1
    _assert_transports_denied(disallowed)


def test_odin_reflect_propose_accepted(monkeypatch):
    """`odin reflect --propose` proceeds to the (mocked) vendor call."""
    calls = {"run": False}

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(*a, **k):
        calls["run"] = True
        return _Proc()

    monkeypatch.setattr(heading_cli.shutil, "which", lambda *a, **k: "claude")
    monkeypatch.setattr(heading_cli.subprocess, "run", fake_run)
    rc = run_skill("odin", ["reflect", "--propose"])
    assert rc == 0
    assert calls["run"]


def test_odin_other_modes_refused_before_vendor_call(monkeypatch):
    """Every non-`reflect --propose` Odin invocation is refused (exit 2) before
    any vendor call -- the args_prefix gate, not just the tool-permission layer,
    is what keeps the propose tier narrow."""
    calls = {"which": False, "run": False}

    def flag_which(*a, **k):
        calls["which"] = True
        return "claude"

    def boom_run(*a, **k):
        calls["run"] = True
        raise AssertionError("subprocess.run must not run on a refusal")

    monkeypatch.setattr(heading_cli.shutil, "which", flag_which)
    monkeypatch.setattr(heading_cli.subprocess, "run", boom_run)
    assert run_skill("odin", ["learn", "https://example.com"]) == 2
    assert run_skill("odin", ["log", "something happened"]) == 2
    assert run_skill("odin", ["reflect"]) == 2  # reflect WITHOUT --propose refused too
    assert not calls["which"] and not calls["run"]
