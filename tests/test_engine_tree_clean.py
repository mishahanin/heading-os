#!/usr/bin/env python3
"""Runtime proof: the engine clone (.heading-os) carries NO data-class artifact.

HEADING OS engine/data separation invariant: the engine repo is code only; every
data artifact lives in the DATA root (.heading-os-data) reached via the get_*_dir()
seam. The static guard (test_data_root_no_bypass.py) proves *code* does not join a
data dir to an engine root. THIS test proves the complementary, runtime fact: the
engine working tree itself contains no file that routes to `private`/`corporate`.

Why both: finding #3 (2026-06-16) showed a static regex guard can miss an entire
misroute class for years. A tree-level assertion is the belt to the regex's braces
-- if anything ever lands a data artifact in the engine clone (a script, a SKILL
Bash call, or a plugin write), this fails regardless of how the write happened.

The detector itself lives in scripts/utils/engine_guard.py so the UNBYPASSABLE
push wall (scripts/push-all.py) enforces the exact same invariant this asserts --
the 2026-06-22 `docs/superpowers/` leak survived because the routing check ran
only at layers `--no-verify` skips, so the logic is now shared, not test-only.

Filtering is by routing destination, NOT a raw top-level-name match: classification
carve-outs (e.g. `datastore/brand/templates/` routes ENGINE) legitimately share a
top-level name with data dirs and must NOT be flagged.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402

from scripts.utils.engine_guard import (  # noqa: E402
    DEMO_MANIFEST,
    find_data_artifacts,
    repo_carried_paths,
    scan_engine_repo,
)
from scripts.utils.workspace import get_workspace_root  # noqa: E402

# --- Negative branch: the real engine tree must be clean ---------------------


def test_engine_tree_has_no_data_artifacts():
    flagged = scan_engine_repo(get_workspace_root())
    assert not flagged, (
        "Data-class artifact(s) found inside the engine clone -- the engine must "
        "stay code-only; these route private/corporate and belong in the DATA root "
        "(.heading-os-data) via the get_*_dir() seam:\n  " + "\n  ".join(flagged)
    )


# --- Positive branch: the detector actually fires ----------------------------


def test_detector_flags_a_private_data_path():
    # A real data path (outputs/) routes private -> must be flagged.
    assert find_data_artifacts(["outputs/operations/x.md"]) == ["outputs/operations/x.md"]


def test_detector_flags_private_outside_data_dirs():
    # Regression for the real leak (2026-06-22): docs/superpowers/ has top-level
    # 'docs' (not a data-dir name) yet routes `private`. The detector must flag any
    # private-routed path, not only those under a fixed data-dir allowlist.
    assert find_data_artifacts(["docs/superpowers/specs/x.md"]) == ["docs/superpowers/specs/x.md"]


def test_filter_skips_engine_routed_path():
    # The filter flags only private/corporate. A data-dir-named path that routes
    # ENGINE (a hypothetical carve-out) must NOT be flagged. Proven with an injected
    # routing fn so the test does not depend on such a carve-out existing.
    assert find_data_artifacts(["datastore/brand/x.dotx"], routing_fn=lambda r: "engine") == []


def test_filter_flags_corporate_data():
    # Corporate data (e.g. datastore/, knowledge/shared/) also must not sit in the
    # engine clone -- execs receive it via the corporate repo, not the engine.
    assert find_data_artifacts(
        ["datastore/intelligence/x.md"], routing_fn=lambda r: "corporate"
    ) == ["datastore/intelligence/x.md"]


def test_detector_ignores_non_data_dir():
    # Engine code paths are never candidates.
    assert find_data_artifacts(["scripts/foo.py", "tests/bar.py", ".claude/rules/x.md"]) == []


# --- The demo tree is a closed manifest --------------------------------------
#
# Operator law, 2026-08-26: no data from the DATA repository may ever sit in the
# engine, and everything under `examples/` must be invented. The routing map has
# no entry for `examples/`, so before this section every one of the paths below
# fell through to the `engine` default and PASSED the wall. The route in is real:
# with no overlay `get_data_root()` answers `<workspace_root>/examples`, so a tool
# that writes to the data root writes inside the engine clone.


LEAKS_UNDER_THE_DEMO_TREE = [
    "examples/crm/contacts/real-person.md",
    "examples/outputs/operations/a-brief.md",
    "examples/knowledge/a-private-note.md",
    "examples/datastore/brand/logo.png",
    "examples/state/captured-mail-bodies.json",
    "examples/.sync/logs/audit.jsonl",
]


@pytest.mark.parametrize("rel", LEAKS_UNDER_THE_DEMO_TREE)
def test_a_file_that_is_not_shipped_demo_data_is_flagged_under_examples(rel):
    """Every one of these passed the wall until 2026-08-26."""
    assert find_data_artifacts([rel]) == [rel]


@pytest.mark.parametrize("rel", sorted(DEMO_MANIFEST))
def test_every_shipped_demo_file_is_allowed(rel):
    """The other jaw. A manifest that flagged its own tree would be deleted by the
    next person who hit it, and the law would go with it."""
    assert find_data_artifacts([rel]) == []


@pytest.mark.parametrize("spelling", [
    "/examples/crm/contacts/real-person.md",        # leading slash
    "//examples/crm/contacts/real-person.md",       # doubled, as a join can produce
    "examples\\crm\\contacts\\real-person.md",      # Windows separators
    "\\examples\\crm\\contacts\\real-person.md",    # both at once
])
def test_the_demo_branch_normalises_before_it_compares(spelling):
    """The demo-tree rule matches STRINGS, so the spelling has to be settled first.

    Every other rule in `find_data_artifacts` is decided by
    `get_routing_destination`, which normalises the separator and the leading
    slash itself, so those survive a caller that spells a path oddly. The
    `examples/` rule does not: it is a literal `startswith(DEMO_ROOT)` and a
    literal membership test against `DEMO_MANIFEST`, and it sees whatever it is
    handed.

    MEASURED 2026-09-01 by removing `rel.replace("\\\\", "/").lstrip("/")` from
    the function and re-running the 116 tests across this file, test_engine_guard
    and the five leak-wall neighbours: all 116 stayed GREEN, while

        find_data_artifacts(["/examples/crm/contacts/real.md"])   -> []
        find_data_artifacts(["examples\\\\crm\\\\contacts\\\\real.md"]) -> []

    against `['examples/crm/contacts/real.md']` for the same file spelled plainly.
    A contacts file under the bundled demo tree walked through the wall, which is
    the exact operator law of 2026-08-26 that the manifest section exists to
    enforce, and nothing in the suite noticed.

    The route in is not hypothetical. `scan_engine_repo` renders `extra_paths`
    with `str(p)`, and the push wall hands it the paths of the unpushed history;
    `str()` on a Path under Windows yields backslashes.
    """
    assert find_data_artifacts([spelling]) == ["examples/crm/contacts/real-person.md"], (
        f"a data file under the demo tree spelled {spelling!r} was cleared by the "
        "wall. The demo rule compares strings and cannot normalise them itself."
    )


@pytest.mark.parametrize("spelling", [
    "/examples/README.md",
    "examples\\README.md",
])
def test_normalising_does_not_start_flagging_the_shipped_demo(spelling):
    """The other jaw. A normalisation that flagged the manifest's own files would
    make the tree-clean test fail on a correct tree, and the fix would be to
    delete the rule."""
    assert "examples/README.md" in DEMO_MANIFEST, (
        "this anchor names a file the manifest no longer ships"
    )
    assert find_data_artifacts([spelling]) == []


def test_the_manifest_matches_the_tree_git_actually_carries():
    """A manifest that drifts from disk is a claim that has stopped being true.

    Asserted in BOTH directions on purpose. A file added to `examples/` and not to
    the manifest is caught by the tree-clean test above, loudly. A file REMOVED
    from `examples/` and left on the manifest is caught only here, and it matters:
    a manifest naming files that no longer exist reads as wider coverage than it
    has.
    """
    root = get_workspace_root()
    on_disk = {
        p for p in repo_carried_paths(root)
        if p.startswith("examples/")
    }
    assert on_disk == set(DEMO_MANIFEST), (
        "the shipped demo tree and DEMO_MANIFEST disagree.\n"
        f"  on disk, not on the manifest: {sorted(on_disk - set(DEMO_MANIFEST))}\n"
        f"  on the manifest, not on disk: {sorted(set(DEMO_MANIFEST) - on_disk)}"
    )


def test_every_shipped_demo_thread_parses_as_a_thread():
    """A demo file the engine's own parser rejects is worse than no demo file.

    `examples/threads/business/EXAMPLE-thread.md` shipped with no YAML
    frontmatter until 2026-08-27. On a clone with no private data folder the
    threads root IS that directory, so `python scripts/thread.py list` answered
    a first-time user with a warning about the one thread the engine itself
    ships, and the census benchmark refused to compute truth at all
    ("cannot compute truth over unparseable thread file(s)").

    Floored, because `glob` over a directory that has been renamed returns an
    empty list and an empty loop asserts nothing.
    """
    from scripts.utils.threads_lib import parse_thread_file

    root = get_workspace_root() / "examples" / "threads"
    files = sorted(root.rglob("*.md"))
    assert len(files) >= 1, (
        f"no demo thread found under {root}; this guard measured nothing"
    )
    for path in files:
        parse_thread_file(path)
