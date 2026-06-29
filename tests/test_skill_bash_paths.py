"""Baseline-ratchet guard: no NEW SKILL.md bash line passes a bare data-class path to a script.

Advisory layer of the engine/data separation (the authoritative guarantee is
test_engine_tree_clean.py). The PreToolUse data-path-redirect hook does NOT cover Bash,
so a SKILL handing a bare `outputs/...` path to a Bash-invoked script can misroute a
write into the engine clone (auto-memory `skill-data-paths-need-explicit-resolution`).

Current hits are illustrative template paths in documentation examples, frozen as a
BASELINE in scripts/audit-skill-bash-paths.py. This test fails only on a REGRESSION:
a skill gaining a new bare-data-path bash line, or a new skill appearing. That catches
creep without forcing churn on existing illustrative examples.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "audit_skill_bash_paths", str(ROOT / "scripts" / "audit-skill-bash-paths.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

from scripts.utils.workspace import get_workspace_root  # noqa: E402


def test_no_new_skill_bash_data_path_misroutes():
    found = _mod.scan_all(get_workspace_root())
    counts = {name: len(hits) for name, hits in found.items()}
    regressions = []
    for name, n in counts.items():
        base = _mod.BASELINE.get(name)
        if base is None:
            regressions.append(f"{name}: NEW skill with {n} bare-data-path bash line(s)")
        elif n > base:
            regressions.append(f"{name}: {n} > baseline {base}")
    assert not regressions, (
        "New SKILL bash data-path misroute candidate(s) -- resolve via get_*_dir()/"
        "$OUTPUTS_DIR, or update BASELINE in scripts/audit-skill-bash-paths.py if "
        "intentional:\n  " + "\n  ".join(regressions)
    )


def test_baseline_matches_current_corpus():
    """The frozen baseline must equal the live scan -- so a CLEANED skill (count drops
    below baseline) forces a baseline update, keeping the ratchet honest."""
    found = _mod.scan_all(get_workspace_root())
    counts = {name: len(hits) for name, hits in found.items()}
    assert counts == _mod.BASELINE, (
        "Baseline drift: scripts/audit-skill-bash-paths.py BASELINE must equal the live "
        f"scan.\n  live:     {counts}\n  baseline: {_mod.BASELINE}"
    )


def test_scanner_excludes_resolved_paths():
    """A bash line that resolves via the seam must NOT be flagged."""
    # scan_skill works on a real file; assert the regex pair behaves on representative lines.
    assert _mod._DATA.search("python scripts/x.py outputs/foo.md")
    assert _mod._RESOLVED.search('python scripts/x.py "$(... get_outputs_dir)/foo.md"')
    assert _mod._RESOLVED.search("OUT=$OUTPUTS_DIR/foo.md")
