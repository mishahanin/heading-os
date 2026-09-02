"""Three guards in `scripts/utils/impeccable_engine.py` that read the wrong thing.

Found by the 2026-08-24 engine audit campaign, verified still present and fixed
2026-09-02. Each one was a real check standing in front of a real hazard, asking
a question next to the one it was written for.

  resolve_cli      accepted a local binary on SUBSTRING containment of the pinned
                   version, so `13.5.0` and `3.5.0-rc1` both satisfied a pin of
                   `3.5.0` and ran ahead of the `npx --yes <pin>` path that does
                   enforce it. `get_pinned_version` one function up calls the
                   exact pin "the only mitigation this integration claims".

  is_plausible     tested the FIRST digit run in a free-form snippet rather than
                   the measured value, and failed in both directions: an
                   impossible reading survived behind a leading line number, and
                   a legitimate one was DROPPED behind an unrelated leading
                   number. The second is silent loss of a real finding, in a
                   module whose header commits every failure path to "reporting
                   MORE, never toward silence". `config/visual-check-profiles.json`
                   had carried a per-rule `unit` since the integration and
                   nothing read it.

  load_baseline    handled unparseable JSON and not parseable-but-wrong-shaped
                   JSON, so a hand edit or a truncate-then-repair that left valid
                   JSON crashed the gate on `.items()` instead of degrading to an
                   empty freeze.

Nothing here reads the live tree: profiles are passed in, the baseline is written
under tmp_path, and the CLI probe is stubbed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import impeccable_engine as IE  # noqa: E402


# ============================================================
# resolve_cli - the pin must be a version, not a substring
# ============================================================

class _Probe:
    """Stands in for the `impeccable --version` subprocess result."""

    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _resolve_with_version(monkeypatch, reported: str, pin: str = "impeccable@3.5.0"):
    monkeypatch.setattr(IE, "get_pinned_version", lambda: pin)
    monkeypatch.setattr(
        IE.shutil, "which",
        lambda name: "/usr/local/bin/impeccable" if name == "impeccable" else None,
    )
    monkeypatch.setattr(
        IE.subprocess, "run", lambda *a, **k: _Probe(reported)
    )
    return IE.resolve_cli()


def test_a_major_version_that_merely_contained_the_pin_was_run(monkeypatch):
    """`"3.5.0" in "impeccable 13.5.0"` is True. Major version 13 is not the pin.

    With no npx on PATH either, an unpinned binary must resolve to None rather
    than to itself: refusing is what leaves the regex engine running alone, which
    is the module's documented supported state.
    """
    monkeypatch.setattr(IE, "get_pinned_version", lambda: "impeccable@3.5.0")
    monkeypatch.setattr(
        IE.shutil, "which",
        lambda name: "/usr/local/bin/impeccable" if name == "impeccable" else None,
    )
    monkeypatch.setattr(IE.subprocess, "run", lambda *a, **k: _Probe("impeccable 13.5.0"))
    assert IE.resolve_cli() is None, (
        "a version-13 binary satisfied a 3.5.0 pin, so the exact pin the module "
        "calls its only supply-chain mitigation was bypassed"
    )


def test_a_prerelease_that_merely_contained_the_pin_was_run(monkeypatch):
    """`3.5.0-rc1` is not `3.5.0`, and a release candidate is exactly the
    unreviewed build a pin exists to keep out."""
    monkeypatch.setattr(IE, "get_pinned_version", lambda: "impeccable@3.5.0")
    monkeypatch.setattr(
        IE.shutil, "which",
        lambda name: "/usr/local/bin/impeccable" if name == "impeccable" else None,
    )
    monkeypatch.setattr(IE.subprocess, "run", lambda *a, **k: _Probe("3.5.0-rc1"))
    assert IE.resolve_cli() is None, "a prerelease satisfied an exact pin"


def test_the_matching_binary_is_still_accepted(monkeypatch):
    """The anchor. Tightening a version test is only correct if the real one
    still resolves, in each of the three shapes a CLI prints its version in."""
    for reported in ("3.5.0", "v3.5.0", "impeccable 3.5.0"):
        assert _resolve_with_version(monkeypatch, reported) == [
            "/usr/local/bin/impeccable"
        ], f"the pinned binary was refused when it reported {reported!r}"


def test_an_unresolvable_binary_still_falls_through_to_the_pinned_npx(monkeypatch):
    """Refusing the local binary must reach `npx --yes <pin>`, which enforces the
    pin itself. Refusing into None when npx exists would lose the deep engine for
    no reason."""
    monkeypatch.setattr(IE, "get_pinned_version", lambda: "impeccable@3.5.0")
    monkeypatch.setattr(IE.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(IE.subprocess, "run", lambda *a, **k: _Probe("13.5.0"))
    assert IE.resolve_cli() == ["npx", "--yes", "impeccable@3.5.0"]


# ============================================================
# is_plausible - the number tested must be the measured one
# ============================================================

_PX = {"plausibility": {"oversized-h1": {"unit": "px", "max": 200}}}
_RATIO = {"plausibility": {"tight-leading": {"unit": "ratio", "min": 0.5}}}


def test_an_impossible_reading_survived_behind_a_leading_line_number():
    """Direction (a): the filter under-fires.

    `_NUMBER.search` returned `12` from "line 12", `12 <= 200` held, and the
    physically impossible 2856px reading this filter exists to remove was kept.
    """
    finding = {"antipattern": "oversized-h1", "snippet": "line 12 - h1 renders at 2856px"}
    assert IE.is_plausible(finding, _PX) is False, (
        "a 2856px h1 was called plausible because a line number came first in "
        "the snippet"
    )


def test_a_legitimate_finding_was_dropped_behind_an_unrelated_leading_number():
    """Direction (b), and the worse half: SILENT LOSS of a real finding.

    A genuine 96px h1 is exactly the hit the module docstring says the filter
    must keep live. Dropping it contradicts "degrades toward reporting MORE,
    never toward silence" in the same file's header.
    """
    finding = {"antipattern": "oversized-h1", "snippet": "2856 scans later: h1 at 96px"}
    assert IE.is_plausible(finding, _PX) is True, (
        "a legitimate 96px reading was filtered away because an unrelated "
        "number appeared earlier in the snippet"
    )


def test_a_rule_whose_unit_is_absent_from_the_snippet_still_reads_a_number():
    """The declared fallback, stated as a limit rather than left to be found.

    `tight-leading` declares `unit: ratio` and its snippets read `0.11x`, so no
    unit-suffixed number exists. The first number is then correct, and both the
    misparse and the real reading must still be judged.
    """
    assert IE.is_plausible(
        {"antipattern": "tight-leading", "snippet": "line-height 0.11x (need >=1.3)"},
        _RATIO,
    ) is False
    assert IE.is_plausible(
        {"antipattern": "tight-leading", "snippet": "line-height 1.15x (need >=1.3)"},
        _RATIO,
    ) is True


def test_a_rule_with_no_bounds_is_never_filtered():
    """The other anchor: plausibility is opt-in per rule. An antipattern with no
    entry in the config must pass through whatever its snippet says."""
    assert IE.is_plausible(
        {"antipattern": "broken-image", "snippet": "9999px"}, _PX
    ) is True


# ============================================================
# load_baseline - a wrong shape must widen the gate, not crash it
# ============================================================

def _baseline(tmp_path: Path, payload: str) -> Path:
    path = tmp_path / "visual-baseline.json"
    path.write_text(payload, encoding="utf-8")
    return path


def test_a_baseline_whose_files_key_holds_a_list_crashed_the_gate(tmp_path, capsys):
    """`{"files": ["docs/index.html"]}` is valid JSON and the wrong shape.

    `apply_baseline` does `for f, rules in baseline.items()`, so this raised
    `AttributeError: 'list' object has no attribute 'items'` out of a check whose
    module header commits every failure path to reporting more, never crashing.
    """
    frozen = IE.load_baseline(_baseline(tmp_path, '{"files": ["docs/index.html"]}'))
    assert frozen == {}, "a list-shaped baseline was handed on to apply_baseline"
    assert "empty freeze" in capsys.readouterr().err, (
        "the degradation was silent; an empty freeze un-suppresses every frozen "
        "finding, so it has to be announced"
    )
    # The whole point: the caller downstream must survive it.
    assert IE.apply_baseline(
        [{"file": "docs/index.html", "type": "impeccable:oversized-h1"}], frozen
    ) == [{"file": "docs/index.html", "type": "impeccable:oversized-h1"}]


def test_a_baseline_whose_rule_map_is_a_string_crashed_the_gate(tmp_path):
    """The second wrong shape. `dict("x")` raises ValueError, not AttributeError,
    so one guard on the outer type alone would not have caught this one."""
    frozen = IE.load_baseline(
        _baseline(tmp_path, '{"files": {"docs/index.html": "x"}}')
    )
    assert frozen == {}
    assert IE.apply_baseline([], frozen) == []


def test_a_well_shaped_baseline_still_suppresses_what_it_froze(tmp_path):
    """The anchor. Refusing wrong shapes is only correct while the right shape
    still ratchets; a guard that refused everything would silently un-freeze the
    whole corpus and read as a clean tightening."""
    frozen = IE.load_baseline(
        _baseline(tmp_path, '{"files": {"a.html": {"impeccable:oversized-h1": 1}}}')
    )
    assert frozen == {"a.html": {"impeccable:oversized-h1": 1}}
    findings = [
        {"file": "a.html", "type": "impeccable:oversized-h1"},
        {"file": "a.html", "type": "impeccable:oversized-h1"},
    ]
    assert IE.apply_baseline(findings, frozen) == [findings[1]], (
        "the frozen count stopped suppressing, so the ratchet is gone"
    )


def test_an_unparseable_baseline_still_degrades_the_same_way(tmp_path):
    """The pre-existing branch this new one was written to match. Both answer an
    empty freeze; a divergence between them would be the next defect."""
    assert IE.load_baseline(_baseline(tmp_path, "{ not json")) == {}
    assert json.loads('{"files": {}}') == {"files": {}}  # sanity on the fixture shape
