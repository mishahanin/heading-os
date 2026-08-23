"""The plugin policy document must match the setting it documents.

`.claude/settings.README.md` carries two lists: the plugins shipped ON, and the
ones deliberately OFF "so nobody re-enables one by accident". `.claude/settings.json`
carries the actual `enabledPlugins` map. Nothing compared them, and on 2026-08-23
they had drifted: the README named `playwright` as shipped while settings.json
had it off, and omitted three that were on. The README now says so in its own
text, and tells the reader to verify with `scripts/harness-audit.py` "rather than
by reading".

An instruction to verify is not verification. That is the same shape as the
other defects found the same night: a settings file more permissive than its
siblings with nothing comparing them, a docs page naming a config path the code
does not use, a roadmap quoting a test count no guard checked. In every case the
second list existed and nothing read both.

So this is the comparator. It fails on drift in either direction, which is the
part a human reading two lists reliably misses: an entry ADDED to settings.json
and not to the README looks like nothing at all.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / ".claude" / "settings.json"
README = ROOT / ".claude" / "settings.README.md"

# A bullet naming a plugin: `- \`name@repo\` — reason`
BULLET = re.compile(r"^-\s+`([a-z0-9-]+@[a-z0-9-]+)`", re.M)


@pytest.fixture(scope="module")
def enabled() -> dict[str, bool]:
    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    plugins = data.get("enabledPlugins")
    assert plugins, "settings.json has no enabledPlugins block"
    return plugins


@pytest.fixture(scope="module")
def documented() -> dict[str, set[str]]:
    """The README's two lists, split at the 'Deliberately OFF' heading."""
    text = README.read_text(encoding="utf-8")
    start = text.index("Current shipped plugins")
    split = text.index("Deliberately OFF", start)
    # The OFF list ends at the next blank-line-separated prose paragraph.
    end = text.index("\n\n", text.index("\n", split))
    while BULLET.search(text[split:end + 200]) and text[end:end + 200].lstrip().startswith("-"):
        end = text.index("\n\n", end + 2)
    return {
        "on": set(BULLET.findall(text[start:split])),
        "off": set(BULLET.findall(text[split:end])),
    }


# --- the ON list ---------------------------------------------------------------

def test_every_enabled_plugin_is_documented_as_shipped(enabled, documented):
    really_on = {name for name, on in enabled.items() if on}
    missing = sorted(really_on - documented["on"])
    assert not missing, (
        f"these plugins are ENABLED in settings.json and appear in no shipped "
        f"list in settings.README.md: {missing}. They reach every workspace that "
        "takes this settings file, undocumented."
    )


def test_nothing_documented_as_shipped_is_actually_off(enabled, documented):
    wrong = sorted(p for p in documented["on"] if not enabled.get(p, False))
    assert not wrong, (
        f"settings.README.md lists these as shipped, but settings.json has them "
        f"off or absent: {wrong}. This is the exact 2026-08-23 drift, where the "
        "README described playwright as a working shipped capability."
    )


# --- the OFF list --------------------------------------------------------------

def test_every_disabled_plugin_is_documented_as_off(enabled, documented):
    really_off = {name for name, on in enabled.items() if not on}
    missing = sorted(really_off - documented["off"])
    assert not missing, (
        f"these plugins are disabled in settings.json and the README does not "
        f"say why: {missing}. The OFF list exists so nobody re-enables one by "
        "accident, which needs the reason, not just the absence."
    )


def test_nothing_documented_as_off_is_actually_on(enabled, documented):
    wrong = sorted(p for p in documented["off"] if enabled.get(p, False))
    assert not wrong, (
        f"settings.README.md lists these as deliberately OFF while settings.json "
        f"has them ON: {wrong}"
    )


# --- neither list may be empty, or the comparison is vacuous --------------------

def test_both_lists_were_actually_parsed(documented):
    """A regex that matches nothing makes every test above pass. That failure
    mode is why the README's own advice was 'verify with the tool, not by
    reading' in the first place."""
    assert documented["on"], "parsed no shipped plugins out of settings.README.md"
    assert documented["off"], "parsed no disabled plugins out of settings.README.md"


def test_a_plugin_appears_in_exactly_one_list(documented):
    both = sorted(documented["on"] & documented["off"])
    assert not both, f"listed as both shipped and deliberately off: {both}"
