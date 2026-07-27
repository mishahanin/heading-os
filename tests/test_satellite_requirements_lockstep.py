"""The satellite requirement files move in lockstep with the uv-managed root.

`scripts/requirements-fireside.txt` and `scripts/bridge_daemon/requirements.txt`
exist so each daemon can be installed into its own venv without dragging in the
whole workspace graph. They are hand-pinned, which means nothing kept them
honest: every package they name is ALSO resolved by uv at the root, and the
bridge daemon's own header already states the rule in prose for PyYAML ("do not
pin a different version here").

Prose does not hold a pin. Two things happened while it was only prose:

  * the root moved off the YANKED charset-normalizer 3.4.8 and fireside kept it,
    the same one-sibling-fixed shape this workspace keeps producing; and
  * Dependabot, which reads each file independently, proposed bumping fastapi,
    firecrawl-py and uvicorn in the satellites alone, which would have split
    them from the root graph that uv resolves.

So the invariant is measured here rather than remembered.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SATELLITES = [
    Path("scripts/requirements-fireside.txt"),
    Path("scripts/bridge_daemon/requirements.txt"),
]
# `uvicorn[standard]==0.51.0` names the same distribution as `uvicorn==0.51.0`.
_PIN = re.compile(r"^([A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*==\s*([^\s;#]+)")


def _pins(path: Path) -> dict:
    found = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _PIN.match(line.strip())
        if match:
            # PEP 503 normalisation: the root export writes `apscheduler`, the
            # fireside file writes `APScheduler`, and they are one package.
            found[re.sub(r"[-_.]+", "-", match.group(1)).lower()] = match.group(2)
    return found


@pytest.mark.parametrize("satellite", SATELLITES, ids=lambda p: p.as_posix())
def test_every_satellite_pin_matches_the_root_export(satellite):
    root_pins = _pins(ROOT / "requirements.txt")
    drifted = {
        name: (version, root_pins[name])
        for name, version in _pins(ROOT / satellite).items()
        if name in root_pins and root_pins[name] != version
    }

    assert not drifted, (
        f"{satellite} has drifted from the uv-managed root graph: "
        + ", ".join(f"{n} pins {mine} while requirements.txt resolves {theirs}"
                    for n, (mine, theirs) in sorted(drifted.items()))
        + ". Bump it at the root (`uv lock --upgrade-package <name>`), regenerate "
          "requirements.txt, then mirror the new pin here."
    )


@pytest.mark.parametrize("satellite", SATELLITES, ids=lambda p: p.as_posix())
def test_the_satellites_pin_something_at_all(satellite):
    """Guards the guard: an unreadable or reformatted file must not pass empty."""
    assert _pins(ROOT / satellite), f"{satellite} parsed to zero pins"
