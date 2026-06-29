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
from scripts.utils.engine_guard import find_data_artifacts, scan_engine_repo  # noqa: E402
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
