"""The findings registry is well-formed (always) and zero-open (acceptance, Phase 4)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.paths import data_root_is_demo, get_data_root  # noqa: E402

# Our engine-hardening program tracker. NOT docs/security/findings-registry.md —
# that path is the pre-existing canonical SEC-* assessment registry (tracked,
# referenced by SECURITY-CONSTITUTION.md and the global CLAUDE.md). Ours lives
# alongside it under its own dated name.
REGISTRY = get_data_root() / "docs" / "security" / "audit-2026-06-findings-registry.md"
_ALLOWED_STATUS = {"open", "resolved"}


def _rows():
    """Parse the registry table rows as dicts. Skips header + separator."""
    lines = [ln for ln in REGISTRY.read_text(encoding="utf-8").splitlines()
             if ln.strip().startswith("|")]
    body = lines[2:]  # drop header row + |---| separator
    rows = []
    for ln in body:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        rows.append(dict(zip(["id", "severity", "status", "location", "fix_ref"], cells)))
    return rows


@pytest.mark.skipif(data_root_is_demo(), reason="no data root (demo clone): registry gate is a CEO/exec concern")
def test_registry_well_formed():
    assert REGISTRY.is_file(), f"registry missing at {REGISTRY}"
    rows = _rows()
    assert rows, "registry has no finding rows"
    for r in rows:
        assert r["id"].startswith("F-"), f"bad id: {r}"
        assert r["status"] in _ALLOWED_STATUS, f"bad status: {r}"


@pytest.mark.acceptance
@pytest.mark.skipif(data_root_is_demo(), reason="no data root (demo clone): registry gate is a CEO/exec concern")
def test_registry_zero_open():
    """A+ sign-off gate: every finding must be resolved. Excluded from the per-push run."""
    open_ids = [r["id"] for r in _rows() if r["status"] == "open"]
    assert not open_ids, f"{len(open_ids)} findings still open: {open_ids}"
