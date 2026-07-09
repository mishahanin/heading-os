"""Tests for the F-10.3 headless skill runner (scripts/heading_cli.py `skill`).

The security-critical proof of this slice is the send boundary: for EVERY tier,
`build_skill_command` must exclude the outbound transports from `--allowedTools`
and name them under `--disallowedTools`. These are pure-Python assertions on the
constructed argv; no test invokes the real `claude` binary (it is not in CI).
"""

from scripts import heading_cli
from scripts.heading_cli import (
    SEND_DENY,
    SKILL_ALLOWLIST,
    TIER_ALLOWED,
    build_skill_command,
    run_skill,
)
from scripts.utils.paths import get_data_root, get_workspace_root


def _values_after(cmd, flag):
    """The value tokens immediately after `flag`, up to the next `--` option."""
    i = cmd.index(flag) + 1
    vals = []
    while i < len(cmd) and not cmd[i].startswith("--"):
        vals.append(cmd[i])
        i += 1
    return vals


def test_allowlist_and_tiers():
    assert SKILL_ALLOWLIST["state-check"] == "read-only"
    assert set(TIER_ALLOWED) == {"read-only", "draft"}
    assert SEND_DENY  # non-empty denylist


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
    for entry in SEND_DENY:
        assert entry in disallowed


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
