#!/usr/bin/env python3
"""A routing rule that recorded the right intent and named the wrong path.

`config/routing-map.yaml` decides, per path, whether a file is `engine` (public),
`private`, or `corporate`. Its default is `engine`. So on a data directory a
missing rule is not an omission, it is a public classification arrived at by
silence.

The rule at `".claude/projects/": private` carried a comment saying it covered
"the CEO's auto-memory (personal facts, friends, projects, threads) ... Fail-closed
wholesale so no future memory file or transcript can leak via the engine default."
The intent was written down. The path was wrong: live auto-memory sits at
`<data-root>/auto-memory/`, which matched no key at all, and on 2026-08-30 all 235
tracked files there resolved `engine`. Four more data-overlay paths were in the
same state, and one of them, `scripts/sentinel_config.yaml`, is a stale duplicate
of a file the map already protects under its real name.

That comment is why this is worse than a plain gap. A reader auditing the map sees
a rule, a rationale and a dated CEO audit, and moves on. Nothing in the file
disagreed with it, because nothing in the file was measured against the tree.

The wrong-path defect propagated into enforcement rather than stopping at
classification: `scripts/leak-guard.py` `check-staged` and
`scripts/utils/engine_guard.find_data_artifacts` both filter on the routing
destination, so a path the map calls `engine` is waved through by every routing
gate downstream. Measured the same day, `content-guard.py` did not close the gap
either: 206 of the 235 auto-memory files carry no real-entity token at all, so the
content gate would have passed them too.

These tests assert on the RESOLVER'S OUTPUT, never on whether a string appears in
the YAML. A grep over the map would pass on a rule spelled into a comment, which
is the exact failure above.

Both directions are asserted. A rule key that over-captures would silently stop
shareable engine code from publishing, which is the mirror-image defect and just
as quiet, so the engine-side sample below is not decoration.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from scripts.build_engine_repo import _suspicious_engine  # noqa: E402
from scripts.utils.engine_guard import find_data_artifacts  # noqa: E402
from scripts.utils.workspace import (  # noqa: E402
    get_data_root,
    get_routing_destination,
    matched_routing_rule,
)

ROOT = Path(__file__).resolve().parent.parent

# The five paths the 2026-08-30 audit found resolving `engine`, plus the sixth
# found while fixing them. Every one is a real path in the DATA overlay; the map
# is public and already names them, so naming them here carries no content.
FIXED = (
    "auto-memory/MEMORY.md",
    "CLAUDE.operational.md",
    "scripts/sentinel_config.yaml",
    "config/fireside-schedule.2026-05-11-cycle1.json",
    "config/telco-pipeline-flags.json",
    "config/chronicle-personal-keywords.txt",
)


# ============================================================
# 1. Each named path fail-closes
# ============================================================

@pytest.mark.parametrize("rel", FIXED)
def test_the_audited_path_routes_private(rel):
    assert get_routing_destination(rel) == "private", (
        f"{rel} resolves {get_routing_destination(rel)!r}; the routing default is "
        "`engine`, which is PUBLIC, so an unmatched data path is a leak-shaped "
        "defect and not an omission"
    )


@pytest.mark.parametrize("rel", FIXED)
def test_the_audited_path_matches_an_explicit_rule(rel):
    """`private` by an explicit key, never by a changed default.

    Without this the suite above would still pass if someone "fixed" the problem
    by flipping `default: engine` to `default: private`, which would reclassify
    the entire public engine in one line.
    """
    assert matched_routing_rule(rel) is not None, f"{rel} falls through to the default"


def test_the_whole_auto_memory_tree_is_covered_not_just_its_index():
    """A directory key, so a memory file nobody has written yet is already private.

    MEMORY.md is the index; the 234 files it points at are the memories. A rule
    naming only the index would have fixed the one file an auditor happens to open.
    """
    for rel in (
        "auto-memory/MEMORY.md",
        "auto-memory/some-future-memory-nobody-has-written.md",
        "auto-memory/nested/deeper/whatever.json",
        "auto-memory/",
    ):
        assert get_routing_destination(rel) == "private", rel


def test_the_rule_that_named_the_wrong_path_still_covers_the_path_it_named():
    """Adding `auto-memory/` did not cost `.claude/projects/` its coverage.

    Both are real: session transcripts do live under `.claude/projects/`. The
    defect was a rule covering one of two stores while its comment claimed both.
    """
    assert get_routing_destination(
        ".claude/projects/-home-x-ws/memory/alpha.md") == "private"
    assert get_routing_destination(
        ".claude/projects/-home-x-ws/some-session.jsonl") == "private"


# ============================================================
# 2. The enforcement layer, not only the classification
# ============================================================

def test_the_engine_guard_now_flags_every_one_of_them():
    """The map is a classification; this is the refusal that consumes it.

    `find_data_artifacts` is what `tests/test_engine_tree_clean.py` and the
    unbypassable push wall in `scripts/push-all.py` both call, and
    `scripts/leak-guard.py check-staged` applies the same destination filter. It
    returned `[]` for all six of these paths before the map was fixed, so the
    routing defect was a three-layer defeat and not a classification nicety.
    """
    assert sorted(find_data_artifacts(list(FIXED))) == sorted(FIXED)


def test_the_engine_guard_still_passes_ordinary_engine_code():
    """The same call, the other direction: it must not start flagging code.

    A detector that flags everything is as useless as one that flags nothing, and
    only this direction can tell the two apart.
    """
    assert find_data_artifacts([
        "scripts/sentinel.py",
        "scripts/sentinel_config.example.yaml",
        "config/routing-map.yaml",
        "CLAUDE.md",
    ]) == []


# ============================================================
# 3. Over-capture: the engine must not start routing private
# ============================================================

ENGINE_NEAR_MISSES = (
    # One character away from a new key, on the side that must stay public.
    "scripts/sentinel_config.example.yaml",
    "scripts/fireside-schedule.example.json",
    "scripts/auto-memory-tool.py",          # `auto-memory` as a filename fragment
    "docs/auto-memory.md",
    "CLAUDE.md",                            # vs. CLAUDE.operational.md
    "config/fireside-schedule.example.json",
    # Ordinary engine surface, unrelated to any new key.
    "scripts/leak-guard.py",
    "scripts/build_engine_repo.py",
    "config/tool-risk.json",
    "config/memory-index.yaml",
    ".claude/rules/security.md",
    ".claude/skills/sentinel/SKILL.md",
    "tests/test_routing_map.py",
    "docs/ARCHITECTURE.md",
    "README.md",
)


@pytest.mark.parametrize("rel", ENGINE_NEAR_MISSES)
def test_engine_paths_did_not_flip_private(rel):
    assert get_routing_destination(rel) == "engine", (
        f"{rel} now resolves {get_routing_destination(rel)!r} via rule "
        f"{matched_routing_rule(rel)!r}; an over-capturing key silently stops "
        "shareable code from publishing, which fails as quietly as a leak"
    )


def test_every_tracked_engine_file_still_routes_engine():
    """The whole tree, not a sample. 2,099 tracked files on 2026-08-30.

    The count is deliberately not asserted: it moves with every ordinary commit,
    and a test that fails on a new source file teaches people to edit the test.
    What must never move is the partition.
    """
    import subprocess

    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files", "-z"],
        cwd=str(ROOT), capture_output=True, check=True,
    ).stdout.decode("utf-8", "surrogateescape")
    rels = [e for e in out.split("\0") if e]
    assert rels, "git reported no tracked files; the measurement did not run"
    moved = {r: get_routing_destination(r) for r in rels
             if get_routing_destination(r) != "engine"}
    assert moved == {}, (
        "tracked engine file(s) no longer route `engine`; a routing-map key is "
        f"over-capturing: {moved}"
    )


# ============================================================
# 4. The dated archive the map cannot generalise over
# ============================================================

def test_the_dated_roster_archive_that_the_exact_key_missed():
    """`config/fireside-schedule.json` did not cover `...2026-05-11-cycle1.json`.

    `matched_routing_rule` matches `norm == key` or a `key + "/"` directory
    prefix. A dated filename is neither, so the archive of the same roster
    resolved `engine` while the roster itself resolved `private`.
    """
    assert get_routing_destination("config/fireside-schedule.json") == "private"
    assert get_routing_destination(
        "config/fireside-schedule.2026-05-11-cycle1.json") == "private"


def test_the_resolver_still_cannot_reach_the_next_dated_archive():
    """The honest statement of what was NOT fixed.

    This asserts a LIMITATION, on purpose. The map has no pattern key, so the
    second dated archive will resolve `engine` the day it is written, exactly as
    the first one did. Two belts stand behind that and are asserted below. When
    someone gives the resolver glob keys, this test goes red and should be
    deleted in the same change: a limitation test that outlives its limitation is
    a false claim about the present.
    """
    assert get_routing_destination(
        "config/fireside-schedule.2099-01-01-cycle99.json") == "engine"


def test_the_build_belt_refuses_a_dated_archive_the_map_cannot_name():
    """`_DATA_TOKENS` matches by plain `startswith`, so it can end mid-filename.

    That is why the generalisation lives in `scripts/build_engine_repo.py` and not
    in the map: the resolver compares whole path segments and cannot express it.
    """
    future = "config/fireside-schedule.2099-01-01-cycle99.json"
    assert _suspicious_engine([future]) == [future]


def test_the_build_belt_refuses_auto_memory_even_with_no_routing_rule():
    """Deliberate duplicate of the map rule: the belt survives losing the rule.

    `_suspicious_engine` is handed the ENGINE bucket, so it only ever sees a path
    the map already called public. Its whole job is to disagree.
    """
    assert _suspicious_engine(["auto-memory/MEMORY.md"]) == ["auto-memory/MEMORY.md"]


def test_the_build_belt_does_not_refuse_the_shipped_examples():
    """The other direction. A belt that refuses the engine's own scaffolding
    would block every build, and someone would delete the token rather than the
    build."""
    assert _suspicious_engine([
        "scripts/fireside-schedule.example.json",
        "scripts/sentinel_config.example.yaml",
        "scripts/auto-memory-tool.py",
        "examples/crm/contacts/alpha.md",   # the bundled demo tree is exempt
    ]) == []


# ============================================================
# 5. The live overlay sweep: what catches the NEXT one
# ============================================================

# Data-overlay `config/` files that legitimately resolve `engine`. Reviewed
# 2026-08-30: this one is a generated harness inventory carrying no real-entity
# token. Anything else appearing here is a routing decision somebody has to make,
# which is the entire point of the list.
OVERLAY_CONFIG_ENGINE_ALLOWED = frozenset({
    "config/harness-manifest.json",
})


def test_no_new_overlay_config_file_resolves_public_unreviewed():
    """The catcher for the defect class, run against the real overlay.

    The five audited paths were all found by hand. This is what finds the sixth
    without a person: every tracked file under the DATA overlay's `config/` must
    resolve `private`, or be on the reviewed allowlist above. A new dated roster
    archive lands here and turns this red.

    Skipped where there is no DATA overlay, because the file list IS the overlay:
    a public clone and a CI runner have nothing to enumerate. The skip therefore
    means "not measured here", never "measured and clean" - the pure-resolver
    tests above run everywhere and do not depend on it.
    """
    import subprocess

    try:
        data_root = get_data_root()
    except Exception as exc:                       # noqa: BLE001 - reported, not swallowed
        pytest.skip(f"DATA overlay unresolvable on this host: {exc}")
    if data_root == ROOT or not (data_root / ".git").exists():
        pytest.skip("no separate DATA overlay git repo on this host")

    out = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files", "-z", "config"],
        cwd=str(data_root), capture_output=True, check=True,
    ).stdout.decode("utf-8", "surrogateescape")
    rels = [e for e in out.split("\0") if e]
    if not rels:
        pytest.skip("DATA overlay tracks no config/ files")

    unreviewed = sorted(
        r for r in rels
        if get_routing_destination(r) == "engine"
        and r not in OVERLAY_CONFIG_ENGINE_ALLOWED
    )
    assert unreviewed == [], (
        "DATA-overlay config file(s) resolve `engine`, which is PUBLIC, and are "
        "not on the reviewed allowlist. Add a `private` rule in "
        "config/routing-map.yaml, or add the path to "
        f"OVERLAY_CONFIG_ENGINE_ALLOWED with the reason: {unreviewed}"
    )


def test_the_sweep_would_actually_fail_on_an_unreviewed_file():
    """The sweep above is only as good as its filter, and on a clean overlay it
    asserts an empty list against an empty list - green over nothing. This drives
    the same filter with a path that is not on the allowlist and confirms it is
    selected."""
    rels = ["config/harness-manifest.json",
            "config/fireside-schedule.2099-01-01-cycle99.json"]
    unreviewed = sorted(
        r for r in rels
        if get_routing_destination(r) == "engine"
        and r not in OVERLAY_CONFIG_ENGINE_ALLOWED
    )
    assert unreviewed == ["config/fireside-schedule.2099-01-01-cycle99.json"]
