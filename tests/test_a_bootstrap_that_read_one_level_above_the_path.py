"""The YARD bootstrap never once ran on a real Herdr event.

`yard-bootstrap.sh` read the checkout path from `worktree.path`. Herdr sends it
at `data.worktree.path`, ONE LEVEL DEEPER. So every YARD created the ordinary
way -- `herdr worktree create` -- hit the script's refusal branch and provisioned
nothing at all.

MEASURED 2026-09-03 by the operator, from HELM, by catching the plugin
environment with a throwaway probe plugin outside this repository:

    bootstrap exit 1, and NO status file written at all
    stderr: "[YARD] STOP: the event did not say which worktree to provision"
    .venv: absent   .env: absent   .claude/settings.local.json: ABSENT
    remote push url: https://github.com/mishahanin/heading-os.git -- LIVE

Read that last pair together. A real YARD ran with the eleven PreToolUse walls
unregistered (they live in the gitignored `settings.local.json` that step 5
writes) AND with a working push url into a PUBLIC repository. The only thing
that said so was a plugin log nobody reads.

WHY NO TEST CAUGHT IT, which is the more important half. Tests existed and were
green. They fed the script `{"worktree":{"path":...}}` -- a payload shape their
own author had invented and then implemented against. A fixture derived from the
code under test measures the code against itself and can never disagree with it.
The two runs that looked like successes were hand-fed that same invented shape.

So this file pins the REAL payload as a data file captured from a live event,
`tests/fixtures/herdr-worktree-created-event.json`, and asserts against THAT.
`test_the_pinned_payload_is_the_real_shape_and_not_the_invented_one` is the load
-bearing one: it fails if the fixture ever drifts back toward the shape the old
code could read, which is the only way this file could go quietly vacuous.

The invented shape still parses on purpose. It is the documented by-hand
procedure in the script's own header, and the operator uses it. Keeping it is a
compatibility promise, not the thing under test.

Run: python3 -m pytest tests/test_a_bootstrap_that_read_one_level_above_the_path.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BOOTSTRAP = ROOT / "scripts" / "herdr" / "heading-os-yard" / "yard-bootstrap.sh"
FIXTURES = ROOT / "tests" / "fixtures"
EVENT_FIXTURE = FIXTURES / "herdr-worktree-created-event.json"
CONTEXT_FIXTURE = FIXTURES / "herdr-worktree-created-context.json"

REFUSAL = "STOP: the event did not say which worktree to provision"

# The script stats the resolved directory and, finding no engine there, says so
# and exits 0. That sentence is therefore proof of TWO things at once: a path was
# resolved, and it was resolved to a directory that exists. Nothing else the
# script prints distinguishes "found the path" from "guessed one".
RESOLVED = "not a HEADING OS engine checkout"


# ============================================================
# The fixture is the measurement, so guard the fixture first
# ============================================================

def _load(fixture: Path, root: Path) -> dict:
    """The pinned payload with its placeholder root pointed at `root`."""
    return json.loads(fixture.read_text(encoding="utf-8").replace("/WS", str(root)))


def test_the_pinned_payload_is_the_real_shape_and_not_the_invented_one():
    """The one assertion that keeps this whole file honest.

    If somebody ever "simplifies" the fixture to the flat shape, every other
    test here would still pass while measuring nothing. So: the path must be
    nested under `data`, and must NOT be reachable at the top level, because the
    top level is exactly where the broken code looked and found nothing.
    """
    payload = json.loads(EVENT_FIXTURE.read_text(encoding="utf-8"))

    assert payload["data"]["worktree"]["path"], (
        "the pinned event no longer carries the path at data.worktree.path, "
        "which is the shape Herdr actually sends and the shape this file exists "
        "to hold the parser to")
    assert payload["data"]["workspace"]["worktree"]["checkout_path"], (
        "the pinned event lost its second, independent source of the path")
    assert "path" not in payload.get("worktree", {}), (
        "the pinned event grew a TOP-LEVEL worktree.path. That is the invented "
        "shape. With it present the old broken parser would pass this file, and "
        "the file would be measuring nothing at all")

    context = json.loads(CONTEXT_FIXTURE.read_text(encoding="utf-8"))
    assert context["worktree"]["checkout_path"] and context["workspace_cwd"], (
        "the pinned context lost the two top-level keys that make it an "
        "independent second source")


def test_the_fixtures_are_present_and_are_json():
    """A floor. A corpus of zero fixtures satisfies every loop above it."""
    present = sorted(p.name for p in FIXTURES.glob("herdr-worktree-created-*.json"))
    assert present == [
        "herdr-worktree-created-context.json",
        "herdr-worktree-created-event.json",
    ], f"the pinned payloads moved or were removed: {present}"


# ============================================================
# Driving the real script
# ============================================================

def _run(env_extra: dict, herdr_stub, tmp_path: Path):
    env = herdr_stub.env()
    env.update(env_extra)
    return subprocess.run(
        ["bash", str(BOOTSTRAP)],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=300, env=env)


@pytest.fixture
def yard(tmp_path: Path) -> Path:
    """An existing directory that is NOT an engine checkout."""
    target = tmp_path / ".yard" / ".heading-os" / "yard-probe2"
    target.mkdir(parents=True)
    return target


# ------------------------------------------------------------
# The direction that was broken
# ------------------------------------------------------------

def test_the_real_event_payload_resolves_the_worktree(yard, tmp_path, herdr_stub):
    """The defect, reproduced against the captured payload and then fixed.

    Against the parser as it stood on 2026-09-03 this run printed the refusal
    and exited 1, which is what every real YARD got.
    """
    event = _load(EVENT_FIXTURE, tmp_path)
    proc = _run({"HERDR_PLUGIN_EVENT_JSON": json.dumps(event)}, herdr_stub, tmp_path)

    assert REFUSAL not in proc.stderr, (
        "the real Herdr payload was refused; this is the defect itself\n"
        f"{proc.stderr}")
    assert RESOLVED in proc.stderr, proc.stderr
    assert proc.returncode == 0, proc.stderr


def test_the_real_context_payload_alone_resolves_the_worktree(
        yard, tmp_path, herdr_stub):
    """CONTEXT_JSON is an independent second source and must stand alone."""
    context = _load(CONTEXT_FIXTURE, tmp_path)
    proc = _run({"HERDR_PLUGIN_CONTEXT_JSON": json.dumps(context)},
                herdr_stub, tmp_path)

    assert REFUSAL not in proc.stderr, proc.stderr
    assert RESOLVED in proc.stderr, proc.stderr


def test_an_event_without_a_path_does_not_hide_the_context(
        yard, tmp_path, herdr_stub):
    """The second half of the same defect, and the subtler one.

    The old code chose its document with `${EVENT:-${CONTEXT:-}}`. A NON-EMPTY
    event therefore made the context invisible, whatever it held. Since Herdr
    always sends an event, the context fallback could never once have run: it
    read like a safety net and was unreachable code.
    """
    event = {"event": "worktree_created", "data": {"type": "worktree_created"}}
    context = _load(CONTEXT_FIXTURE, tmp_path)
    proc = _run({"HERDR_PLUGIN_EVENT_JSON": json.dumps(event),
                 "HERDR_PLUGIN_CONTEXT_JSON": json.dumps(context)},
                herdr_stub, tmp_path)

    assert REFUSAL not in proc.stderr, (
        "an event carrying no path suppressed the context that carried one\n"
        f"{proc.stderr}")
    assert RESOLVED in proc.stderr, proc.stderr


# ------------------------------------------------------------
# Precedence: first non-empty wins, in the operator's stated order
# ------------------------------------------------------------
#
# The discriminator is deliberately crude and therefore unambiguous: only ONE
# candidate in each pair names a directory that exists. A resolver that reads
# the wrong one hits `[ ! -d "$WT_PATH" ]` and refuses, so the two outcomes
# cannot be confused with each other.

def test_data_worktree_path_outranks_the_workspace_checkout_path(
        yard, tmp_path, herdr_stub):
    event = _load(EVENT_FIXTURE, tmp_path)
    event["data"]["workspace"]["worktree"]["checkout_path"] = str(
        tmp_path / "does-not-exist")
    proc = _run({"HERDR_PLUGIN_EVENT_JSON": json.dumps(event)}, herdr_stub, tmp_path)

    assert REFUSAL not in proc.stderr, (
        "the first source in the chain lost to a later one\n" + proc.stderr)


def test_the_workspace_checkout_path_is_used_when_the_first_is_absent(
        yard, tmp_path, herdr_stub):
    event = _load(EVENT_FIXTURE, tmp_path)
    del event["data"]["worktree"]["path"]
    proc = _run({"HERDR_PLUGIN_EVENT_JSON": json.dumps(event)}, herdr_stub, tmp_path)

    assert REFUSAL not in proc.stderr, (
        "the second source in the chain never runs\n" + proc.stderr)


def test_the_event_outranks_the_context(yard, tmp_path, herdr_stub):
    event = _load(EVENT_FIXTURE, tmp_path)
    context = _load(CONTEXT_FIXTURE, tmp_path)
    context["worktree"]["checkout_path"] = str(tmp_path / "does-not-exist")
    context["workspace_cwd"] = str(tmp_path / "does-not-exist")
    proc = _run({"HERDR_PLUGIN_EVENT_JSON": json.dumps(event),
                 "HERDR_PLUGIN_CONTEXT_JSON": json.dumps(context)},
                herdr_stub, tmp_path)

    assert REFUSAL not in proc.stderr, proc.stderr


def test_the_context_checkout_path_stands_on_its_own(yard, tmp_path, herdr_stub):
    """Each context key is asserted ALONE, and this one was the gap.

    The captured context carries the path under BOTH `worktree.checkout_path`
    and `workspace_cwd`, so a test driven by the whole fixture cannot tell the
    two apart: delete the first and the second answers. A mutation dropping
    `worktree.checkout_path` SURVIVED for exactly that reason before this test
    existed (7/8 caught, 8/8 after).
    """
    context = {"workspace_id": "w48", "worktree": {"checkout_path": str(yard)}}
    proc = _run({"HERDR_PLUGIN_CONTEXT_JSON": json.dumps(context)},
                herdr_stub, tmp_path)

    assert REFUSAL not in proc.stderr, proc.stderr
    assert RESOLVED in proc.stderr, proc.stderr


def test_workspace_cwd_is_the_last_source_before_refusing(
        yard, tmp_path, herdr_stub):
    context = {"workspace_id": "w48", "workspace_cwd": str(yard)}
    proc = _run({"HERDR_PLUGIN_CONTEXT_JSON": json.dumps(context)},
                herdr_stub, tmp_path)

    assert REFUSAL not in proc.stderr, proc.stderr
    assert RESOLVED in proc.stderr, proc.stderr


# ------------------------------------------------------------
# The hand-fed shape stays working: a promise, not the thing under test
# ------------------------------------------------------------

def test_the_by_hand_payload_in_the_scripts_own_header_still_parses(
        yard, tmp_path, herdr_stub):
    """The shape the script's usage comment tells an operator to type.

    Kept working deliberately. It is NOT evidence about the real event, and the
    two runs that once looked like proof of this script working were this shape
    being fed to it by hand.
    """
    proc = _run({"HERDR_PLUGIN_EVENT_JSON":
                 json.dumps({"worktree": {"path": str(yard)}})},
                herdr_stub, tmp_path)

    assert REFUSAL not in proc.stderr, proc.stderr
    assert RESOLVED in proc.stderr, proc.stderr


def test_a_checkout_path_containing_a_space_survives_the_split(
        tmp_path, herdr_stub):
    """The fields are joined on US (0x1f), not on a space.

    The previous parser printed four values separated by spaces and split them
    with a default-IFS `read`, so a checkout path containing a space arrived
    truncated at the space and the rest of it became the workspace id. Herdr's
    own worktree directory is operator-chosen, so this is reachable.
    """
    spaced = tmp_path / "yard with a space"
    spaced.mkdir()
    event = {"data": {"worktree": {"path": str(spaced), "branch": "b"}}}
    proc = _run({"HERDR_PLUGIN_EVENT_JSON": json.dumps(event)}, herdr_stub, tmp_path)

    assert REFUSAL not in proc.stderr, (
        "a path with a space was split and lost\n" + proc.stderr)
    assert RESOLVED in proc.stderr, proc.stderr


# ------------------------------------------------------------
# The other direction: it still refuses rather than guessing
# ------------------------------------------------------------

def test_no_payload_at_all_still_refuses(tmp_path, herdr_stub):
    """The refusal is the correct behaviour and must survive the repair.

    MEASURED 2026-09-03: the plugin command's working directory is
    HERDR_PLUGIN_ROOT, never the worktree. So `$PWD` is not a fallback that
    happens to be unused -- it is a fallback that would be WRONG every time.
    """
    proc = _run({}, herdr_stub, tmp_path)

    assert REFUSAL in proc.stderr, proc.stderr
    assert proc.returncode == 1, (
        f"refusing must exit non-zero, got {proc.returncode}\n{proc.stderr}")


def test_a_payload_with_no_path_anywhere_still_refuses(tmp_path, herdr_stub):
    event = {"event": "worktree_created",
             "data": {"workspace": {"workspace_id": "w48"}}}
    proc = _run({"HERDR_PLUGIN_EVENT_JSON": json.dumps(event),
                 "HERDR_PLUGIN_CONTEXT_JSON": json.dumps({"workspace_id": "w48"})},
                herdr_stub, tmp_path)

    assert REFUSAL in proc.stderr, proc.stderr
    assert proc.returncode == 1, proc.stderr


def test_unparseable_json_refuses_rather_than_falling_through(
        yard, tmp_path, herdr_stub):
    """A truncated payload must not become a guess.

    `$PWD` here is `tmp_path`, which EXISTS -- so a fallthrough to it would pass
    the `-d` check and the script would provision a directory nobody chose.
    """
    proc = _run({"HERDR_PLUGIN_EVENT_JSON": '{"data": {"worktree": {"pa'},
                herdr_stub, tmp_path)

    assert REFUSAL in proc.stderr, proc.stderr
    assert proc.returncode == 1, proc.stderr


def test_a_path_that_does_not_exist_refuses(tmp_path, herdr_stub):
    event = {"data": {"worktree": {"path": str(tmp_path / "never-created")}}}
    proc = _run({"HERDR_PLUGIN_EVENT_JSON": json.dumps(event)}, herdr_stub, tmp_path)

    assert REFUSAL in proc.stderr, proc.stderr
    assert proc.returncode == 1, proc.stderr
