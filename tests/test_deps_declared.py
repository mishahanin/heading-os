#!/usr/bin/env python3
"""Assert that key dependencies are explicitly pinned in the requirements files.

Prevents silent drift where a library is imported but not pinned, so the next
`pip install -r` on a fresh machine picks an unknown version.

Two rules, and the second exists because the first is a hand-written list.
`_REQUIRED_PINS` names three packages by hand; the satellite file holds six.
MEASURED 2026-09-01: rewriting `watchdog==6.0.0` to `watchdog>=6.0.0` left this
file AND `tests/test_satellite_requirements_lockstep.py` green at 7 passed.
The lockstep test cannot see it either, and for a sharper reason than "it was
not asked to": its `_PIN` regex only matches `==` lines, so a loosened pin
silently DROPS OUT of the set it compares against the root, and the comparison
of an absent name to a present one is vacuously equal. A range that a fresh
`pip install -r` resolves to whatever PyPI serves that morning is exactly the
drift this file's own first sentence says it prevents.

So the second rule is DERIVED from the file rather than listed: every
requirement line in every satellite must carry an exact `==`. The hand list
stays beside it, because the two answer different questions - the derived rule
asks "is every line still a pin", and the hand list asks "is this specific
CVE-relevant package still present at all", which a rule over the lines cannot
ask about a package that was deleted.
"""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Written out rather than globbed. A glob that stops matching turns this guard
# into a sweep over nothing, and the floors below would then be the only thing
# left holding it.
_SATELLITES = (
    "scripts/bridge_daemon/requirements.txt",
    "scripts/requirements-fireside.txt",
)

# `name`, optional `[extras]`, `==`, a version, then optionally an environment
# marker or a trailing comment. Anything else is not an exact pin.
_EXACT_PIN = re.compile(
    r"^[A-Za-z0-9._-]+(?:\[[^\]]*\])?\s*==\s*[^\s;#]+\s*(?:;[^#]*)?(?:#.*)?$")

# Each tuple: (package_name_pattern, requirements_file_relative_to_root)
_REQUIRED_PINS = [
    ("fastapi", "scripts/bridge_daemon/requirements.txt"),
    ("firecrawl-py", "scripts/bridge_daemon/requirements.txt"),
    ("uvicorn", "scripts/bridge_daemon/requirements.txt"),
]


def _is_pinned(req_path: Path, pkg_pattern: str) -> bool:
    """Return True if req_path contains an exact == pin for pkg_pattern."""
    if not req_path.is_file():
        return False
    content = req_path.read_text(encoding="utf-8")
    # Allow optional PEP 508 extras suffix like [standard] before ==
    pattern = re.compile(
        rf"^\s*{re.escape(pkg_pattern)}(\[[^\]]*\])?\s*==\s*\S+",
        re.IGNORECASE | re.MULTILINE,
    )
    return bool(pattern.search(content))


def test_bridge_daemon_deps_pinned():
    """All critical bridge-daemon dependencies must have exact == pins."""
    violations = []
    for pkg, rel_path in _REQUIRED_PINS:
        req_file = ROOT / rel_path
        if not _is_pinned(req_file, pkg):
            violations.append(f"{pkg!r} not pinned with == in {rel_path}")
    assert not violations, (
        "Missing exact-version pins in requirements files:\n  "
        + "\n  ".join(violations)
    )


def _requirement_lines(path: Path) -> list[str]:
    """Every line that states a requirement, comments and options removed.

    `-r other.txt`, `-c constraints.txt` and `--hash=...` are pip directives,
    not requirements, so they are not asked to carry a version.
    """
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        lines.append(line)
    return lines


@pytest.mark.parametrize("relative", _SATELLITES)
def test_every_satellite_requirement_is_an_exact_pin(relative):
    """Derived, not listed: nothing in a satellite may float.

    A satellite requirements file is installed on its own into a per-daemon
    venv, so a range here is resolved fresh on every machine and on every
    install date. The root graph is uv-locked and the satellites are hand
    written; this is the only thing asking them to stay pins.
    """
    path = ROOT / relative
    assert path.is_file(), f"{relative} is gone; this guard is scanning nothing"

    lines = _requirement_lines(path)
    # Floor: an empty or unreadable file must fail here rather than report a
    # clean sweep. Measured 2026-09-01: 6 requirements in the bridge daemon
    # file, 10 in fireside.
    assert len(lines) >= 5, (
        f"only {len(lines)} requirement line(s) parsed out of {relative}; the "
        f"parser has stopped seeing them")

    floating = [line for line in lines if not _EXACT_PIN.match(line)]
    assert not floating, (
        f"{relative} carries requirement(s) that are not exact `==` pins, so a "
        f"fresh `pip install -r` resolves whatever PyPI serves that day:\n  "
        + "\n  ".join(floating))


def test_the_exact_pin_rule_rejects_the_shapes_it_exists_to_catch():
    """Anti-vacuity from both sides: a regex that matched everything, or
    nothing, would pass the sweep above over the files as they stand today."""
    for accepted in ("fastapi==0.140.7", "uvicorn[standard]==0.51.0",
                     "certifi==2026.7.22", "requests==2.34.2  # comment",
                     'tzlocal==5.4.4; python_version >= "3.9"'):
        assert _EXACT_PIN.match(accepted), accepted
    for rejected in ("watchdog>=6.0.0", "httpx~=0.28.1", "apscheduler",
                     "fastapi<1.0", "uvicorn[standard]>=0.51.0",
                     "requests==2.34.2 --hash=sha256:abc"):
        assert not _EXACT_PIN.match(rejected), rejected
