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

# The same line WITHOUT committing to `==`. `_PIN` only matches an exact pin, so
# a requirement loosened to a range drops out of the comparison below and matches
# nothing, silently. MEASURED 2026-09-01: rewriting `charset-normalizer==3.4.9`
# as `charset-normalizer>=3.4.8`, `fastapi==0.140.7` as `fastapi>=0.139.0`, and
# `uvicorn[standard]==0.51.0` as `uvicorn[standard]~=0.51` each left this whole
# file green, while changing 3.4.9 to 3.4.8 was caught at once. Loosening is the
# shape a dependency bot proposes and the shape a hand-edit reaches for when a
# pin is inconvenient, so it is the one that had to be seen.
_REQUIREMENT = re.compile(r"^([A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*(.*)$")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirements(path: Path) -> dict:
    """Every requirement line as `{canonical name: the specifier text}`.

    Comment lines, blank lines and pip flags (`-r`, `--index-url`) are skipped;
    everything else that opens with a distribution name is a requirement, pinned
    or not.
    """
    found = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#")[0].strip()
        if not stripped or stripped.startswith("-"):
            continue
        match = _REQUIREMENT.match(stripped)
        if match:
            found[_canonical(match.group(1))] = match.group(2).strip()
    return found


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


@pytest.mark.parametrize("satellite", SATELLITES, ids=lambda p: p.as_posix())
def test_no_satellite_requirement_is_loosened_out_of_the_comparison(satellite):
    """A range is not a softer pin here, it is an EXIT from the check above.

    `test_every_satellite_pin_matches_the_root_export` iterates `_pins`, which
    only sees `name==version`. Rewrite one line as `name>=version` and that line
    is no longer compared with anything, while
    `test_the_satellites_pin_something_at_all` stays satisfied by every other
    line in the file. The satellite then resolves whatever pip finds, which is
    the split from the uv-resolved root graph this file exists to prevent, and
    the yanked charset-normalizer 3.4.8 is exactly what a range would have kept.

    Judged only against packages the root export also names: a satellite is
    allowed a dependency the root does not carry, and this check has nothing to
    say about one.
    """
    root_pins = _pins(ROOT / "requirements.txt")
    loose = {
        name: spec
        for name, spec in _requirements(ROOT / satellite).items()
        if name in root_pins and spec != f"=={root_pins[name]}"
    }

    assert not loose, (
        f"{satellite} names a root-pinned package without the root's exact pin: "
        + ", ".join(f"{n} says {s!r} while requirements.txt resolves "
                    f"=={root_pins[n]}" for n, s in sorted(loose.items()))
        + ". A range drops the line out of the lockstep comparison entirely. "
          "Bump at the root (`uv lock --upgrade-package <name>`), regenerate "
          "requirements.txt, then mirror the exact pin here."
    )


@pytest.mark.parametrize("satellite", SATELLITES, ids=lambda p: p.as_posix())
def test_the_loosened_pin_check_sees_the_same_lines_the_exact_pin_check_does(satellite):
    """The two parsers must not disagree about what a requirement line is.

    Otherwise the wider one could be reading zero lines and the narrower one
    doing all the work, which is the empty-corpus hole one layer along.
    """
    exact = set(_pins(ROOT / satellite))
    every = set(_requirements(ROOT / satellite))

    assert exact <= every, sorted(exact - every)
    assert len(every) >= len(exact) >= 5, (len(every), len(exact))


# ============================================================
# The dev toolchain has the same two-sources shape
# ============================================================
#
# `pyproject.toml`'s `[dependency-groups] dev` is what `uv sync` installs, and
# `requirements-dev.txt` is what CLAUDE.md's setup step installs
# (`pip install -r requirements-dev.txt`). Two files, one toolchain, and nothing
# held them together: measured 2026-07-27, requirements-dev.txt forbade the very
# pre-commit the pyproject pins, and omitted pytest-cov entirely while the gate
# enforces a coverage floor. Someone following the documented path got a
# different toolchain than CI, silently, which is the failure this whole file
# exists to stop for the daemon satellites.


def _dev_group() -> dict:
    import tomllib

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    group = tomllib.loads(text)["dependency-groups"]["dev"]
    return dict(entry.split("==") for entry in group)


def _dev_ranges() -> dict:
    """Every `name<spec>` line in requirements-dev.txt, normalised like _pins."""
    found = {}
    pattern = re.compile(r"^([A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*([<>=!~][^\s;#]*.*)$")
    for line in (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").splitlines():
        stripped = line.split("#")[0].strip()
        if not stripped or stripped.startswith("-"):
            continue
        match = pattern.match(stripped)
        if match:
            name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
            found[name] = match.group(2).strip()
    return found


def test_every_pyproject_dev_pin_satisfies_the_dev_requirements_range():
    """A range that excludes the pin means the two paths install different tools."""
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    ranges = _dev_ranges()
    conflicts = {
        name: (version, ranges[name])
        for name, version in _dev_group().items()
        if name in ranges and Version(version) not in SpecifierSet(ranges[name])
    }

    assert not conflicts, (
        "requirements-dev.txt excludes a version pyproject.toml pins: "
        + ", ".join(f"{n} pinned {pin} but the range is {rng}"
                    for n, (pin, rng) in sorted(conflicts.items()))
        + ". `uv sync` and `pip install -r requirements-dev.txt` would install "
          "different toolchains."
    )


def test_every_pyproject_dev_tool_appears_in_the_dev_requirements():
    """Omission is the quieter half: an absent tool is not a loosened one.

    pytest-cov was missing, and the gate runs under a coverage floor, so the
    documented install produced an environment where `scripts/run-tests.py`
    cannot run at all.
    """
    missing = sorted(set(_dev_group()) - set(_dev_ranges()))

    assert not missing, (
        "pyproject.toml's dev group names tools requirements-dev.txt omits: "
        + ", ".join(missing)
        + ". The documented `pip install -r requirements-dev.txt` path would "
          "install an incomplete toolchain."
    )
