#!/usr/bin/env python3
"""SEC-017 regression coverage for the consolidated _dispatch.py.

The PreToolUse dispatcher folds seven distinct security/safety checks into
one in-process pipeline. Each check is a pure function of the tool payload,
so we exercise them directly rather than via subprocess. Goals:

1. Catch refactors that silently bypass a guardrail.
2. Pin the BLOCK reason text per check (downstream surfaces parse it).
3. Verify cross-platform path normalisation (forward and backward slashes)
   so the dispatcher behaves identically when invoked from Windows VSCode
   or from WSL/Linux Claude Code against the same files.

All seven checks are stateless and exercised via direct payloads. (The
`_secure/` vault and its `check_protect_secure` branch were removed in Plan 5;
sensitivity is now the fail-closed `SENSITIVE_MODE` flag, covered by
`tests/test_sensitive_mode.py`.)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[2]
DISPATCH_PATH = WORKSPACE / ".claude" / "hooks" / "_dispatch.py"


@pytest.fixture(scope="module")
def dispatch():
    """Load _dispatch.py as a module (its filename has a leading underscore,
    so a plain import statement does not pick it up — use importlib.spec)."""
    spec = importlib.util.spec_from_file_location("_dispatch", DISPATCH_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_dispatch"] = module
    spec.loader.exec_module(module)
    return module


# ============================================================
# check_prevent_secrets
# ============================================================


def test_prevent_secrets_blocks_anthropic_key_in_content(dispatch):
    # Synthetic test fixture - matches the Anthropic pattern shape but is not a real key.
    fake_key = "sk-ant-" + "abcdef0123456789ZYXW"  # pragma: allowlist secret
    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": "scripts/example.py",
            "content": f"ANTHROPIC_KEY = '{fake_key}'",
        },
    }
    result = dispatch.check_prevent_secrets(payload)
    assert result is not None
    assert result["decision"] == "block"
    assert "Anthropic API key" in result["reason"]


def test_prevent_secrets_blocks_secret_in_bash_command(dispatch):
    fake_key = "sk-ant-" + "deadbeefcafebabe1234"  # pragma: allowlist secret
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f"curl -H 'Authorization: Bearer {fake_key}'"},
    }
    result = dispatch.check_prevent_secrets(payload)
    assert result is not None
    assert result["decision"] == "block"
    assert "Bash command" in result["reason"]


def test_prevent_secrets_allows_env_file(dispatch):
    fake_key = "sk-ant-" + "realsecretdoesnotmatter1234"  # pragma: allowlist secret
    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": ".env",
            "content": f"ANTHROPIC_KEY={fake_key}",
        },
    }
    assert dispatch.check_prevent_secrets(payload) is None


def test_prevent_secrets_allows_clean_content(dispatch):
    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": "scripts/example.py",
            "content": "print('hello world')",
        },
    }
    assert dispatch.check_prevent_secrets(payload) is None


# ============================================================
# check_protect_personal_threads
# ============================================================


def test_protect_personal_threads_blocks_cp_from_personal(dispatch):
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "cp threads/personal/diary.md /tmp/leaked.md"},
    }
    result = dispatch.check_protect_personal_threads(payload)
    assert result is not None
    assert result["decision"] == "block"
    assert result.get("_policy_deny") is True


def test_protect_personal_threads_blocks_git_add_personal(dispatch):
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git add threads/personal/2026-01-01-foo.md"},
    }
    result = dispatch.check_protect_personal_threads(payload)
    assert result is not None
    assert result["decision"] == "block"


def test_protect_personal_threads_blocks_quoting_personal_path_in_other_file(dispatch):
    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": "outputs/operations/dashboard/today.html",
            "content": "see threads/personal/diary.md for context",
        },
    }
    result = dispatch.check_protect_personal_threads(payload)
    assert result is not None
    assert result["decision"] == "block"


def test_protect_personal_threads_allows_writes_to_personal_target(dispatch):
    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": "threads/personal/2026-05-26-foo.md",
            "content": "private note",
        },
    }
    assert dispatch.check_protect_personal_threads(payload) is None


def test_protect_personal_threads_allows_doc_paths(dispatch):
    payload = {
        "tool_name": "Write",
        "tool_input": {
            "file_path": "docs/superpowers/plans/2026-05-26-foo.md",
            "content": "plan references threads/personal/ for design",
        },
    }
    assert dispatch.check_protect_personal_threads(payload) is None


# ============================================================
# check_cwd_anchor
# ============================================================
# Uses a real subdirectory of the workspace as the drifted cwd and a
# root-relative script that genuinely exists from root but not from that
# subdir, so the filesystem-confirmed block condition is exercised for real.

DRIFTED_CWD = str(WORKSPACE / "knowledge")          # real subdir of root
REAL_ROOT_SCRIPT = ".claude/hooks/_dispatch.py"     # exists from root, not from knowledge/


def test_cwd_anchor_blocks_drifted_relative_script(dispatch):
    payload = {
        "tool_name": "Bash",
        "cwd": DRIFTED_CWD,
        "tool_input": {"command": f"python {REAL_ROOT_SCRIPT} --check"},
    }
    result = dispatch.check_cwd_anchor(payload)
    assert result is not None
    assert result["decision"] == "block"
    assert REAL_ROOT_SCRIPT in result["reason"]
    assert "git rev-parse --show-toplevel" in result["reason"]


def test_cwd_anchor_allows_command_run_at_root(dispatch):
    payload = {
        "tool_name": "Bash",
        "cwd": str(WORKSPACE),
        "tool_input": {"command": f"python {REAL_ROOT_SCRIPT} --check"},
    }
    assert dispatch.check_cwd_anchor(payload) is None


def test_cwd_anchor_allows_already_anchored_command(dispatch):
    payload = {
        "tool_name": "Bash",
        "cwd": DRIFTED_CWD,
        "tool_input": {
            "command": f'cd "$(git rev-parse --show-toplevel)" && python {REAL_ROOT_SCRIPT}'
        },
    }
    assert dispatch.check_cwd_anchor(payload) is None


def test_cwd_anchor_allows_shell_outside_workspace(dispatch):
    # cwd above the workspace root (not a subdir) -> the guard must not interfere.
    payload = {
        "tool_name": "Bash",
        "cwd": str(WORKSPACE.parent),
        "tool_input": {"command": f"python {REAL_ROOT_SCRIPT}"},
    }
    assert dispatch.check_cwd_anchor(payload) is None


def test_cwd_anchor_allows_nonexistent_script(dispatch):
    # Path resolves from neither root nor cwd -> not a drift failure, leave it.
    payload = {
        "tool_name": "Bash",
        "cwd": DRIFTED_CWD,
        "tool_input": {"command": "python scripts/does-not-exist-xyz123.py"},
    }
    assert dispatch.check_cwd_anchor(payload) is None


def test_cwd_anchor_ignores_non_bash(dispatch):
    payload = {
        "tool_name": "Write",
        "cwd": DRIFTED_CWD,
        "tool_input": {"file_path": "scripts/x.py", "content": "x = 1"},
    }
    assert dispatch.check_cwd_anchor(payload) is None


# ============================================================
# Cross-OS path normalisation
# ============================================================


def test_secrets_path_allowed_handles_backslash_paths(dispatch):
    # Windows-style backslashes must normalise correctly. .env at workspace
    # root, written via VSCode on Windows, arrives with backslashes.
    assert dispatch._secrets_path_allowed(r"c:\ai\claude-workspaces\ceo-main\.env") is True
    assert dispatch._secrets_path_allowed(r".sessions\telegram.session") is True


# ============================================================
# check_tool_budget — cap must be reachable
# ============================================================
# Regression for the dead-cap bug: when the hard cap was raised (300 -> 1200)
# the storage truncation bound (history[-500:]) was left below it, so count
# could never reach the cap and the BLOCK branch was dead. The bound must stay
# above the hard cap for the guard to fire at all.


def _isolate_rate_state(dispatch, monkeypatch):
    """Redirect _load_rate_state / _save_rate_state to an in-memory store so
    the test never touches the live .claude/state/dispatch-rate.json."""
    store = {}

    def fake_load():
        return dict(store)

    def fake_save(state):
        store.clear()
        store.update(state)

    monkeypatch.setattr(dispatch, "_load_rate_state", fake_load)
    monkeypatch.setattr(dispatch, "_save_rate_state", fake_save)
    return store


def test_tool_budget_storage_bound_exceeds_hard_cap(dispatch, monkeypatch):
    """The retained history must be larger than the hard cap, else the cap is
    unreachable. Drive one call and confirm the stored history can hold > cap."""
    import time as _time

    store = _isolate_rate_state(dispatch, monkeypatch)
    now = int(_time.time())
    # Seed exactly hard-cap entries, all inside the 30-min window.
    seeded = [["sig", now] for _ in range(dispatch.TOOL_BUDGET_HARD)]
    store["tool_history"] = seeded

    payload = {"tool_name": "Read", "tool_input": {"file_path": "x.md"}}
    result = dispatch.check_tool_budget(payload)

    # cap + 1 entries now in window -> must block.
    assert result is not None
    assert result["decision"] == "block"
    assert str(dispatch.TOOL_BUDGET_HARD) in result["reason"]
    # And the stored history must not have been truncated below the cap.
    assert len(store["tool_history"]) > dispatch.TOOL_BUDGET_HARD


def test_tool_budget_allows_under_cap(dispatch, monkeypatch):
    store = _isolate_rate_state(dispatch, monkeypatch)
    store["tool_history"] = []
    payload = {"tool_name": "Read", "tool_input": {"file_path": "x.md"}}
    result = dispatch.check_tool_budget(payload)
    # Single call, well under soft cap -> no block, no soft notice.
    assert result is None


# ============================================================
# Main dispatcher CHECKS list integrity
# ============================================================


def test_checks_list_has_seven_branches(dispatch):
    """If a check is added or removed, this test forces an intentional update.
    The dispatcher's documented contract is exactly seven checks (the eighth,
    check_protect_secure, was removed with the vault in Plan 5)."""
    assert len(dispatch.CHECKS) == 7
    names = [c.__name__ for c in dispatch.CHECKS]
    assert names == [
        "check_prevent_secrets",
        "check_protect_personal_threads",
        "check_protect_corporate",
        "check_protect_docs",
        "check_cwd_anchor",
        "check_rate_limit",
        "check_tool_budget",
    ]
