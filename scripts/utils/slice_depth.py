#!/usr/bin/env python3
"""How much process a change carries, computed from the change itself.

Canopus ran the same eleven steps through a CHANGELOG typo and through a change
to the credential patterns. The obvious repair was to collapse the lifecycle for
everybody, which is a UNIFORM trade of rigour for speed, including on the slices
where rigour is the entire point. This module is the other repair: full depth
where the change touches the enforcement surface, near zero where it touches
prose. See `docs/superpowers/specs/2026-08-01-canopus-v2-design.md` §6 A11.

We did not fail to think of this. `/pre-impl` says "skip for trivial one-liner
fixes", its Phase 3 says "skip for small, low-architectural-risk plans", and the
Odin brain carries `right-size-the-harness-calibrated-not-maximalist`. All three
leave the decision to a human each time, which is what THE LAW calls already
dead. This is that principle as a function whose answer binds.

Two rules keep it from being decorative:

**The floor cannot be diluted.** One enforcement-surface path among any number of
prose paths still yields full depth. A calibration that waters down when unrelated
files join the commit reads as rigour while granting none.

**Calibration may only ever REMOVE ceremony.** It lowers depth for work that
touches none of the surface. It never lowers depth for work that does, however
small the diff. There is no "it is only one line" path into the hooks.

Measured against the last 60 engine commits on 2026-08-01, this classifier puts
52% at full, 43% at standard and 5% at light. Calibration is therefore not
primarily a speed win; slightly over half of real work touches the surface
anyway. What it buys is the right to keep full depth on that half without the
standard becoming too heavy to use on the rest.
"""
from __future__ import annotations

DEPTH_FULL = "full"
DEPTH_STANDARD = "standard"
DEPTH_LIGHT = "light"

# Ordered most to least ceremony, so a caller can compare depths meaningfully.
DEPTH_ORDER = (DEPTH_LIGHT, DEPTH_STANDARD, DEPTH_FULL)

# The enforcement surface. A change touching any of these takes full depth
# whatever its size. An entry ending in "/" matches as a directory prefix;
# anything else matches that exact path.
#
# `tests/contract/test_slice_depth_surface.py` asserts every entry still names a
# path that exists, because a rename silently dropping a file off this list is
# how the mechanism would die without anyone noticing: the guard keeps passing,
# on a smaller set.
ENFORCEMENT_SURFACE = (
    # The blocking gate between a model mistake and a written credential.
    ".claude/hooks/",
    # The pattern vocabulary every scanner and the redactor share.
    "scripts/utils/secret_patterns.py",
    # The content scanner, used at commit time and inside the push wall.
    "scripts/secret-scanner.py",
    # The unbypassable push walls: secrets, routing, real-entity content.
    "scripts/push-all.py",
    # The detectors those walls are built on.
    "scripts/utils/engine_guard.py",
    "scripts/utils/content_denylist.py",
    # The commit-time guards against data reaching a public repository.
    "scripts/leak-guard.py",
    "scripts/content-guard.py",
    # The send gate. send_capable floors at human approval, in code not config.
    "scripts/utils/tool_risk.py",
    "config/tool-risk.json",
    # Decides what counts as private. A wrong edit reroutes real data into the
    # public engine.
    "config/routing-map.yaml",
    # The policies those controls implement. Weakening the prose weakens the
    # control in practice, because the prose is what the model reads.
    ".claude/rules/security.md",
    ".claude/rules/lethal-trifecta.md",
    ".claude/rules/tiered-risk.md",
    # The freeze primitive itself: the thing that makes a locked test immovable.
    "scripts/utils/canopus_freeze.py",
    "scripts/utils/canopus_gate.py",
    # The approve/freeze/verify CLI and the redness-and-vacuity checks behind it.
    # Omitting these was a hole in the floor for the first hour this file
    # existed: the primitives were covered while the command that drives them,
    # and the code deciding whether a contract is honest, were not.
    "scripts/canopus.py",
    "scripts/utils/canopus_contract.py",
    # What decides pass or fail. `run-tests.py` is the gate CI and the push path
    # run, and `tests/conftest.py` holds both the freeze check that refuses to
    # run a suite against a moved contract and the re-exec guard whose absence
    # once made the whole suite print nothing while exiting 0.
    "scripts/run-tests.py",
    "tests/conftest.py",
    # This mechanism, and the gate that binds it. A calibration whose own
    # classifier can be lowered at standard depth calibrates nothing.
    "scripts/utils/slice_depth.py",
    "scripts/depth-gate.py",
)

# Prose. Light depth requires EVERY path in the change to be prose; one file
# outside this set lifts the whole change to standard.
_PROSE_SUFFIXES = (".md", ".markdown", ".rst", ".txt")
_PROSE_PREFIXES = ("docs/", "reference/")


def _normalise(path) -> str:
    """A repo-relative POSIX path. Backslashes are separators, not content.

    A Windows-shaped path that skipped the floor would be a silent bypass, and
    the workspace is driven from WSL where both forms appear.
    """
    text = str(path).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def _surface_match(path: str):
    """The surface entry this path matches, or None."""
    for entry in ENFORCEMENT_SURFACE:
        if entry.endswith("/"):
            if path.startswith(entry):
                return entry
        elif path == entry:
            return entry
    return None


def _is_prose(path: str) -> bool:
    return path.endswith(_PROSE_SUFFIXES) or path.startswith(_PROSE_PREFIXES)


def _frozen_paths(freeze) -> set:
    """Paths under a live freeze. Tolerates a manifest shape we do not own."""
    if not isinstance(freeze, dict):
        return set()
    files = freeze.get("files")
    if isinstance(files, dict):
        return {_normalise(p) for p in files}
    if isinstance(files, (list, tuple)):
        return {_normalise(p) for p in files}
    return set()


def classify(paths, freeze=None) -> dict:
    """Return the depth a change over *paths* carries.

    `freeze` is an optional Canopus manifest; anything under it is load-bearing
    for the slice in flight, whatever the file happens to be, so it forces full
    depth the same way the surface does.

    The result names WHICH path raised the depth. An answer nobody can audit is
    an answer nobody will trust, and the first instinct on being refused is to
    ask what tripped it.
    """
    normalised = [_normalise(p) for p in paths if str(p).strip()]
    frozen = _frozen_paths(freeze)

    triggers = []
    for path in normalised:
        entry = _surface_match(path)
        if entry:
            triggers.append({"path": path, "rule": entry, "kind": "enforcement-surface"})
        elif path in frozen:
            triggers.append({"path": path, "rule": path, "kind": "under-a-live-freeze"})

    if triggers:
        first = triggers[0]
        return {
            "depth": DEPTH_FULL,
            "triggers": triggers,
            "reason": (
                f"{first['path']} is {first['kind'].replace('-', ' ')}"
                f" ({first['rule']})"
            ),
        }

    if not normalised:
        return {"depth": DEPTH_LIGHT, "triggers": [],
                "reason": "nothing to classify"}

    if all(_is_prose(path) for path in normalised):
        return {"depth": DEPTH_LIGHT, "triggers": [],
                "reason": f"all {len(normalised)} path(s) are prose"}

    # Everything else, including a path nobody has classified. Failing toward
    # ceremony is deliberate: an unrecognised path is not evidence of safety.
    return {"depth": DEPTH_STANDARD, "triggers": [],
            "reason": f"{len(normalised)} path(s), none on the enforcement surface"}
