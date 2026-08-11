#!/usr/bin/env python3
"""Where the templates/ -> docs/ sync publishes each shared page.

Two destinations exist and they are not interchangeable. The copy beside the
template feeds the corporate repo the executives pull. The copy in the engine
clone is the page served on the public docs site. A CEO-only guide must reach
only the first; a public page must reach both.

The defect this pins: anchoring every file to the template's own sibling docs/
stopped docs/EMERGENCY-PROCEDURES.md in the engine from ever updating again,
without an error anywhere, because the template lives in the private overlay.
"""
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "sync-docs.py"

_hook = runpy.run_path(str(HOOK))
sync_targets = _hook["sync_targets"]
SYNC_FILES = _hook["SYNC_FILES"]
ENGINE_PUBLISHED = _hook["ENGINE_PUBLISHED"]


def _template(name: str, overlay: Path) -> Path:
    path = overlay / "templates" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


def test_a_public_page_reaches_both_the_overlay_and_the_engine(tmp_path):
    engine = tmp_path / "engine"
    overlay = tmp_path / "overlay"
    targets = sync_targets(_template("EMERGENCY-PROCEDURES.md", overlay), engine)
    assert overlay / "docs" / "EMERGENCY-PROCEDURES.md" in targets
    assert engine / "docs" / "EMERGENCY-PROCEDURES.md" in targets


def test_a_ceo_only_guide_never_reaches_the_engine(tmp_path):
    engine = tmp_path / "engine"
    overlay = tmp_path / "overlay"
    targets = sync_targets(_template("CEO-ADMIN-GUIDE.md", overlay), engine)
    assert targets == [overlay / "docs" / "CEO-ADMIN-GUIDE.md"]
    assert not any(engine in t.parents for t in targets)


@pytest.mark.parametrize("name", sorted(ENGINE_PUBLISHED))
def test_every_engine_published_name_is_a_sync_file(name):
    """A name here that the sync never handles would be a dead entry."""
    assert name in SYNC_FILES


@pytest.mark.parametrize("name", sorted(ENGINE_PUBLISHED))
def test_every_engine_published_page_exists_in_this_clone(name):
    """The engine really does publish these, so the destination is not invented."""
    assert (ROOT / "docs" / name).is_file()


def test_the_engine_copy_of_a_public_page_matches_its_template():
    """The drift this test exists to catch, asserted against the live trees.

    Skipped on a clone with no private overlay, where there is no template to
    compare against.
    """
    overlay = ROOT.parent / ".heading-os-data"
    template = overlay / "templates" / "EMERGENCY-PROCEDURES.md"
    if not template.is_file():
        pytest.skip("no private overlay in this clone")
    published = ROOT / "docs" / "EMERGENCY-PROCEDURES.md"
    assert published.read_text(encoding="utf-8") == template.read_text(encoding="utf-8"), (
        "docs/EMERGENCY-PROCEDURES.md has drifted from its template. Re-save "
        "templates/EMERGENCY-PROCEDURES.md so the sync hook republishes it."
    )
