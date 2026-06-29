#!/usr/bin/env python3
"""SEC F-H5: check_protect_personal_threads must block Read of personal thread files,
and the Read PreToolUse hook must be wired in the tracked per-OS settings templates.

The Read tool was not in the guarded-tool set, and the templates had no Read matcher,
so a Read of threads/personal/... dumped CEO-only content into the transcript unguarded
(the Bash branch already blocks the `cat`/`grep` equivalent). The shipped wiring lives
in the tracked settings.local.{linux,macos,windows}.json templates that seed each
machine's gitignored live settings.local.json.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.workspace import get_workspace_root

_ROOT = get_workspace_root()
_HOOK_PATH = _ROOT / ".claude" / "hooks" / "_dispatch.py"
# The shippable, tracked source of the hook wiring (the live settings.local.json is
# gitignored and absent on a fresh clone, so the gate asserts against the templates).
_SETTINGS_TEMPLATES = [
    _ROOT / ".claude" / "settings.local.linux.json",
    _ROOT / ".claude" / "settings.local.macos.json",
    _ROOT / ".claude" / "settings.local.windows.json",
]


@pytest.fixture(scope="module")
def dispatch():
    spec = importlib.util.spec_from_file_location("_dispatch", _HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read_payload(file_path: str) -> dict:
    return {"tool_name": "Read", "tool_input": {"file_path": file_path}}


def test_read_personal_thread_is_blocked(dispatch):
    result = dispatch.check_protect_personal_threads(
        _read_payload("threads/personal/2026-06-15-test-thread.md"))
    assert result is not None, "Read of threads/personal/ returned None (allowed) — F-H5 not fixed"
    assert result.get("decision") == "block", f"expected block, got: {result}"
    assert result.get("_policy_deny") is True, "personal-threads blocks carry the policy-deny flag"


def test_read_personal_thread_backslash_is_blocked(dispatch):
    result = dispatch.check_protect_personal_threads(
        _read_payload("threads\\\\personal\\\\x.md"))
    assert result is not None and result.get("decision") == "block"


def test_read_business_thread_is_allowed(dispatch):
    result = dispatch.check_protect_personal_threads(
        _read_payload("threads/business/2026-06-15-deal.md"))
    assert result is None, f"business thread Read blocked: {result}"


def test_read_non_thread_is_allowed(dispatch):
    result = dispatch.check_protect_personal_threads(
        _read_payload("context/strategy.md"))
    assert result is None, f"non-thread Read blocked: {result}"


def test_write_to_personal_target_still_allowed(dispatch):
    """Existing semantics: writing TO a personal path is allowed (returns None)."""
    payload = {"tool_name": "Write",
               "tool_input": {"file_path": "threads/personal/2026-06-15-t.md", "content": "x"}}
    assert dispatch.check_protect_personal_threads(payload) is None


def test_write_leak_of_personal_path_still_blocked(dispatch):
    """Existing leak-guard: a non-personal target whose CONTENT references a
    threads/personal/ path must still block after the Read branch is inserted."""
    payload = {"tool_name": "Write",
               "tool_input": {"file_path": "context/notes.md",
                              "content": "reminder: see threads/personal/secret.md"}}
    result = dispatch.check_protect_personal_threads(payload)
    assert result is not None and result.get("decision") == "block", (
        f"leak guard regressed: {result}")


def test_e2e_read_personal_thread_denies(tmp_path):
    """End-to-end: run _dispatch.py as a subprocess, feed a Read payload for
    threads/personal/x.md, assert it denies via the PreToolUse permission-deny
    JSON (hookSpecificOutput / exit 0) — an intentional policy block, not a
    'hook error'. The deny is just as binding as the old exit-2 path."""
    import json as _json
    import subprocess

    payload = {"tool_name": "Read", "tool_input": {"file_path": "threads/personal/x.md"}}
    result = subprocess.run(
        [sys.executable, str(_HOOK_PATH)],
        input=_json.dumps(payload).encode(),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}; "
        f"stderr={result.stderr!r}; stdout={result.stdout!r}"
    )
    data = _json.loads(result.stdout.decode())
    hso = data.get("hookSpecificOutput", {})
    assert hso.get("permissionDecision") == "deny", (
        f"expected permissionDecision=deny; stdout={result.stdout!r}"
    )
    assert "personal" in hso.get("permissionDecisionReason", "").lower(), (
        f"expected reason to mention personal; stdout={result.stdout!r}"
    )


@pytest.mark.parametrize("template", _SETTINGS_TEMPLATES, ids=lambda p: p.name)
def test_settings_template_registers_read_pretooluse_matcher(template):
    """Regression-proof the wiring: the original bug was a missing Read matcher.
    Every tracked per-OS template must register a Read PreToolUse hook so the
    guard ships to every clone/exec, not just this machine's live file."""
    assert template.is_file(), f"tracked settings template missing: {template}"
    settings = json.loads(template.read_text(encoding="utf-8"))
    pre = settings.get("hooks", {}).get("PreToolUse", [])
    matchers = [entry.get("matcher", "") for entry in pre]
    assert any("Read" in m for m in matchers), (
        f"{template.name}: no PreToolUse entry matches Read; matchers={matchers}")
