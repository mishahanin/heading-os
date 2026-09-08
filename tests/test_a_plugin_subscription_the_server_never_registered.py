"""The manifest declared two event subscriptions and the server had armed one.

`scripts/herdr/heading-os-yard/herdr-plugin.toml` gained a `worktree.opened`
subscription in commit eababd0 on 2026-09-03 at 21:48. Herdr's registration for
that plugin had been written at 19:04 the same day, two hours and forty-four
minutes earlier, and a linked plugin's subscriptions come from the SNAPSHOT the
server took at link time, never from the manifest on disk. So the new
subscription was never armed.

MEASURED 2026-09-08, five days later, against the live server:

    manifest   [[events]] on = worktree.created, worktree.opened
    registered herdr plugin list --plugin heading-os.yard --json
               -> events: ["worktree.created"]

Nothing said so. `herdr plugin list` prints a name and a path and no
subscriptions; the file that holds them is not one anybody opens; and a herdr
upgrade to 0.9.0 that restarted the server re-read the snapshot rather than the
manifest, so even a restart did not repair it. The three routes the missing
subscription exists to cover -- `herdr worktree open`, the `keys.open_worktree`
binding, and closing a workspace and opening it again -- reached an
unprovisioned YARD in silence for those five days.

The repair is one command (`herdr plugin unlink` then `herdr plugin link`). The
defect is that the drift was undetectable, and that is what this file closes:
the manifest is the authored intent, the server's own answer is the fact, and
an edit to the first that never reaches the second now fails a test.

THE SERVER IS ASKED, not its state file. `~/.config/herdr/plugins.json` happens
to carry the same records today, but a file beside a socket is a cache until the
process that owns it says otherwise, and `plugin list --json` is that process
answering.

NOT CLAIMED. This compares the SET of event names. It does not establish that a
subscription fires, that the command behind it exists, or that the bootstrap
succeeds when it runs -- `tests/test_yard_bootstrap_lint.py` and
`tests/test_a_bootstrap_that_read_one_level_above_the_path.py` own those.

Run: python3 -m pytest tests/test_a_plugin_subscription_the_server_never_registered.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "scripts" / "herdr" / "heading-os-yard" / "herdr-plugin.toml"

#: The manifest declared this many subscriptions when this test was written.
#: A floor, not a description: without it the comparison below is green over an
#: empty corpus, which is the shape a manifest emptied by a bad edit produces.
DECLARED_SUBSCRIPTIONS_2026_09_08 = 2


def unarmed(declared: list[str], registered: list[str]) -> list[str]:
    """Subscriptions the manifest declares that the server has not registered.

    Order-independent and one-directional on purpose. A subscription the SERVER
    holds and the manifest no longer declares is a stale link rather than a dead
    subscription: it fires a command that still exists, so it is not this
    defect, and folding it in here would make one failure message answer two
    different questions.
    """
    return sorted(set(declared) - set(registered))


def declared_events(manifest: Path) -> list[str]:
    """The `on` value of every `[[events]]` block in a plugin manifest."""
    document = tomllib.loads(manifest.read_text(encoding="utf-8"))
    events = document.get("events") or []
    return [entry["on"] for entry in events
            if isinstance(entry, dict) and isinstance(entry.get("on"), str)]


# ============================================================
# The detector, both directions, over literals
# ============================================================

def test_a_declared_subscription_the_server_lacks_is_reported():
    """The measured case: the manifest moved, the registration did not."""
    assert unarmed(["worktree.created", "worktree.opened"],
                   ["worktree.created"]) == ["worktree.opened"]


def test_a_fully_armed_plugin_reports_nothing():
    """The other direction. A detector that reports on a healthy plugin is a
    detector nobody reads by the second week."""
    assert unarmed(["worktree.created", "worktree.opened"],
                   ["worktree.opened", "worktree.created"]) == []


def test_a_subscription_only_the_server_holds_is_not_this_defect():
    """A stale link is a different sentence, so it is not folded in here."""
    assert unarmed(["worktree.created"],
                   ["worktree.created", "worktree.opened"]) == []


def test_an_emptied_manifest_reports_nothing_which_is_why_the_floor_exists():
    """Stated rather than left implicit: the comparison cannot catch a manifest
    that declares nothing at all. `test_the_manifest_still_declares_its_
    subscriptions` is what stands between that and a green suite."""
    assert unarmed([], ["worktree.created"]) == []


# ============================================================
# The manifest, and the floor under everything above
# ============================================================

def test_the_manifest_still_declares_its_subscriptions():
    declared = declared_events(MANIFEST)
    assert len(declared) >= DECLARED_SUBSCRIPTIONS_2026_09_08, (
        f"{MANIFEST.relative_to(ROOT)} declares {len(declared)} event "
        f"subscription(s); {DECLARED_SUBSCRIPTIONS_2026_09_08} were measured on "
        f"2026-09-08. A subscription that left the manifest is either a "
        f"deliberate removal, in which case lower this floor in the same "
        f"change, or an edit that dropped one.")
    assert "worktree.created" in declared
    assert "worktree.opened" in declared


# ============================================================
# The pin against the running server
# ============================================================

def test_every_declared_subscription_is_armed_in_the_running_server():
    """The manifest is intent; the server's answer is the fact.

    Skipped where herdr is absent or this checkout's plugin is not the linked
    one, and both skips are narrow on purpose. The second matters in a YARD:
    `manifest_path` in the registration names HELM's copy, so comparing a
    yard's manifest against it would report a difference that belongs to
    neither.
    """
    if shutil.which("herdr") is None:
        pytest.skip("herdr is not installed here, so no server can be asked "
                    "whether the subscriptions in this manifest are armed")

    plugin_id = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))["id"]
    proc = subprocess.run(
        ["herdr", "plugin", "list", "--plugin", plugin_id, "--json"],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        pytest.skip(f"herdr could not be asked about {plugin_id} "
                    f"(exit {proc.returncode}): {proc.stderr.strip()[:200]}")
    try:
        found = (json.loads(proc.stdout).get("result") or {}).get("plugins")
    except ValueError:
        pytest.fail(f"herdr plugin list --json returned unparseable output: "
                    f"{proc.stdout[:200]}")
    if not found:
        pytest.skip(f"{plugin_id} is not linked into herdr on this machine, so "
                    f"there is no registration to compare the manifest against")

    registration = found[0]
    linked = Path(registration.get("manifest_path", "")).resolve()
    if linked != MANIFEST.resolve():
        pytest.skip(f"the linked {plugin_id} is another checkout's "
                    f"({linked}), not this one's; its registration answers for "
                    f"that manifest and not for {MANIFEST}")

    registered = [entry["on"] for entry in registration.get("events") or []
                  if isinstance(entry, dict) and isinstance(entry.get("on"), str)]
    missing = unarmed(declared_events(MANIFEST), registered)
    assert not missing, (
        f"{plugin_id} declares {missing} in its manifest and the running herdr "
        f"has not registered them. A linked plugin's subscriptions are the "
        f"SNAPSHOT taken at link time, so editing the manifest arms nothing. "
        f"Re-link it:\n"
        f"    herdr plugin unlink {plugin_id}\n"
        f"    herdr plugin link {MANIFEST.parent}\n"
        f"registered: {sorted(registered)}")
