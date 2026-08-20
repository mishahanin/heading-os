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


def _hook_default() -> int:
    """The default the hook falls back to, read without running its `main`."""
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("checkpoint_offer_timeout", HOOK)
    mod = importlib.util.module_from_spec(spec)
    import contextlib

    with contextlib.suppress(SystemExit):
        spec.loader.exec_module(mod)
    return int(mod.HOOK_TIMEOUT_SECONDS)


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
    bundle's different registration invisible to the bound."""
    text = HOOK.read_text(encoding="utf-8")
    assert 'CLAUDE_HANDOFF_HOOK_TIMEOUT' in text
    assert "HOOK_TIMEOUT_SECONDS = CP.env_int(" in text, (
        "the timeout is no longer read as data; a hardcoded number would "
        "describe somebody else's registration"
    )
