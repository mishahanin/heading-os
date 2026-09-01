#!/usr/bin/env python3
"""The Stop hook's timeout bound must track the timeout it is REGISTERED with.

`.claude/hooks/checkpoint-offer.py` shortens its grace period so the whole hook
fits inside the Stop timeout, because Claude Code DISCARDS the output of a hook
that outruns one - the continuation, the state write and the stall notice all
vanish, after the operator has been told in writing that the session will carry
on. Measured 2026-08-20 with a slow HERDR: 92.0 seconds against a 90-second
registration.

The bound reads `CLAUDE_HANDOFF_HOOK_TIMEOUT` from the environment, and correctly
so: the number is DATA. It lives in the `Stop` registration, and a plugin bundle
that registers this hook with a different budget must be able to say so without
editing the file.

But nothing sets that variable on this tree, so the bound falls back to the
hook's own default. Today that default and the registration are both 90, which
makes the bound correct BY COINCIDENCE rather than by construction: change the
registration to 60 and the hook keeps budgeting for 90, silently, in the one
branch whose whole purpose is not to overrun.

This test closes the loop without adding configuration. It reads the number out
of every tracked platform template and holds it to the hook's default. The
override stays available for the bundle case; what is no longer possible is the
two drifting apart unnoticed.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "checkpoint-offer.py"

# The live `settings.local.json` is gitignored, so it is checked when present and
# never required - a fresh clone has only the platform templates.
TEMPLATES = (
    ".claude/settings.local.linux.json",
    ".claude/settings.local.macos.json",
    ".claude/settings.local.windows.json",
)
LIVE = ".claude/settings.local.json"


def _load_hook(env_value: str | None):
    """Import the hook with `CLAUDE_HANDOFF_HOOK_TIMEOUT` set to `env_value`.

    `None` means the variable is ABSENT for the duration, which is the state the
    word "default" describes.
    """
    import contextlib

    sys.path.insert(0, str(ROOT))
    previous = os.environ.pop("CLAUDE_HANDOFF_HOOK_TIMEOUT", None)
    if env_value is not None:
        os.environ["CLAUDE_HANDOFF_HOOK_TIMEOUT"] = env_value
    try:
        spec = importlib.util.spec_from_file_location("checkpoint_offer_timeout", HOOK)
        mod = importlib.util.module_from_spec(spec)
        with contextlib.suppress(SystemExit):
            spec.loader.exec_module(mod)
        return mod
    finally:
        os.environ.pop("CLAUDE_HANDOFF_HOOK_TIMEOUT", None)
        if previous is not None:
            os.environ["CLAUDE_HANDOFF_HOOK_TIMEOUT"] = previous


def _hook_default() -> int:
    """The default the hook falls back to, read without running its `main`.

    The variable is cleared for the import, and that is the whole correction
    made on 2026-09-01. `HOOK_TIMEOUT_SECONDS` is `CP.env_int(...)`, evaluated at
    import, so this function returned whatever the AMBIENT environment said. On
    a machine that used the documented override, the three shipped platform
    templates would have been held to that machine's private number and the
    guard would have failed a correct tree. The function's name says default;
    now it reads one.
    """
    return int(_load_hook(None).HOOK_TIMEOUT_SECONDS)


def _registered_timeout(rel: str) -> int | None:
    path = ROOT / rel
    if not path.is_file():
        return None
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for block in (cfg.get("hooks") or {}).get("Stop", []):
        for hook in block.get("hooks", []):
            if "checkpoint-offer" in hook.get("command", ""):
                return hook.get("timeout")
    return None


@pytest.mark.parametrize("rel", TEMPLATES)
def test_each_platform_template_registers_the_timeout_the_hook_budgets_for(rel):
    registered = _registered_timeout(rel)
    assert registered is not None, (
        f"{rel} no longer registers checkpoint-offer.py on Stop, or the hook "
        "moved; this guard cannot see the number it is holding"
    )
    assert registered == _hook_default(), (
        f"{rel} registers the Stop hook with timeout={registered} while "
        f"checkpoint-offer.py budgets for {_hook_default()}. The hook will "
        "either overrun and have its continuation discarded, or shorten the "
        "operator's grace period for no reason. Change both, or set "
        "CLAUDE_HANDOFF_HOOK_TIMEOUT in the same file."
    )


def test_the_live_settings_agree_too_when_present():
    """Gitignored, so absent on a fresh clone. Checked when it is there, because
    it is the file that actually runs."""
    registered = _registered_timeout(LIVE)
    if registered is None:
        pytest.skip("no local settings on this machine")
    env = json.loads((ROOT / LIVE).read_text(encoding="utf-8")).get("env") or {}
    override = env.get("CLAUDE_HANDOFF_HOOK_TIMEOUT")
    expected = int(override) if override else _hook_default()
    assert registered == expected, (
        f"{LIVE} registers timeout={registered} while the hook budgets for "
        f"{expected}"
    )


def test_the_hook_still_reads_the_number_from_the_environment():
    """The override must survive. Hardcoding the constant would make a plugin
    bundle's different registration invisible to the bound.

    Driven, not grepped. The two assertions here read the hook's SOURCE for the
    variable name and for the assignment text, and a comment mentioning either
    would have satisfied both. Importing the module under a set variable and an
    absent one asks the question the file actually cares about: does the number
    move when the environment says so?
    """
    default = _hook_default()
    override = str(default + 7)
    assert int(_load_hook(override).HOOK_TIMEOUT_SECONDS) == int(override), (
        "the timeout is no longer read as data; a hardcoded number would "
        "describe somebody else's registration"
    )
    assert _hook_default() == default, (
        "reading the override changed what the hook falls back to"
    )


def test_the_default_does_not_move_with_this_machine(monkeypatch):
    """`_hook_default` must answer for the SHIPPED default, whatever this host
    has configured, or the three platform templates get held to a local number.
    """
    monkeypatch.setenv("CLAUDE_HANDOFF_HOOK_TIMEOUT", "37")
    assert _hook_default() != 37, (
        "the ambient environment leaked into what this guard calls the default"
    )
    for rel in TEMPLATES:
        assert _registered_timeout(rel) == _hook_default()
