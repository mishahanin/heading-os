#!/usr/bin/env python3
"""`scripts/visual-discipline-check.py` labelling a purple ramp "purple->pink".

`_check_gradient`'s Tailwind branch asked two independent questions -- does this
line contain a `from-` hue from one list, and a `to-` hue from another -- and
`purple` was on both lists. Measured before the change:

    scan_text('<div class="bg-gradient-to-r from-purple-600 to-purple-800">')
    -> {'type': 'gradient_purple_pink', 'severity': 'error',
        'tell': 'Tailwind purple->pink gradient'}

A monochrome purple ramp containing no pink at all, reported at ERROR severity
under a factually false label. Severity matters here: the module docstring
reserves `error` for "the purple->pink hero gradient" and errors gate the exit
code, so a same-hue ramp failed the gate over a tell it does not carry.

The CSS `linear-gradient` branch beside it was always right, because it keeps
fuchsia on the pink side only and asks whether ONE span holds both families.
The Tailwind branch now spans the same two families, and still admits fuchsia at
either end -- fuchsia genuinely sits between purple and pink, and
`from-fuchsia-500 to-pink-500` is the tell.

Run: .venv/bin/python -m pytest
     tests/test_a_monochrome_ramp_flagged_as_a_two_hue_gradient.py -q
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "visual-discipline-check.py"


def _load():
    spec = importlib.util.spec_from_file_location("vdc_gradient", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vdc = _load()


def _gradient_findings(classes: str):
    html = f'<div class="{classes}"></div>'
    return [f for f in vdc.scan_text(html) if f["type"] == "gradient_purple_pink"]


# ============================================================
# 1 - a same-hue ramp is not a two-family gradient
# ============================================================

@pytest.mark.parametrize(
    "classes",
    [
        "bg-gradient-to-r from-purple-600 to-purple-800",
        "bg-gradient-to-b from-purple-50 to-purple-900",
        "bg-gradient-to-r from-fuchsia-500 to-fuchsia-700",
        # A `to-` hue outside the pink family is not the pink end of anything.
        "bg-gradient-to-r from-violet-600 to-purple-700",
        "bg-gradient-to-br from-fuchsia-400 to-purple-600",
    ],
)
def test_a_same_family_ramp_raises_no_purple_pink_error(classes):
    assert _gradient_findings(classes) == [], classes


# ============================================================
# 2 - the tell it exists for is unchanged
# ============================================================

@pytest.mark.parametrize(
    "classes",
    [
        "bg-gradient-to-r from-purple-500 to-pink-500",
        "bg-gradient-to-r from-purple-600 to-rose-400",
        "bg-gradient-to-r from-violet-600 to-fuchsia-500",
        "bg-gradient-to-br from-violet-500 to-pink-600",
        "bg-gradient-to-r from-fuchsia-500 to-pink-500",
        "bg-gradient-to-r from-purple-600 to-fuchsia-500",
    ],
)
def test_a_genuine_two_family_gradient_is_still_an_error(classes):
    findings = _gradient_findings(classes)
    assert len(findings) == 1, (classes, findings)
    assert findings[0]["severity"] == "error", findings
    assert findings[0]["tell"] == "Tailwind purple->pink gradient", findings


def test_the_css_linear_gradient_branch_is_untouched():
    """The branch that was already correct, pinned in both directions."""
    hit = vdc.scan_text(
        "<style>.hero{background:linear-gradient(90deg,#9333ea,#ec4899);}</style>")
    assert [f["tell"] for f in hit if f["type"] == "gradient_purple_pink"] == [
        "CSS purple->pink linear-gradient"], hit

    miss = vdc.scan_text(
        "<style>.hero{background:linear-gradient(90deg,#9333ea,#6d28d9);}</style>")
    assert not [f for f in miss if f["type"] == "gradient_purple_pink"], miss


# ============================================================
# 3 - the exit code follows the label
# ============================================================

def test_a_purple_ramp_no_longer_fails_the_gate(tmp_path):
    """`error` severity gates the run; a false label failed a clean artifact."""
    art = tmp_path / "brand.html"
    art.write_text(
        '<html><body class="bg-gradient-to-r from-purple-600 to-purple-800">'
        "<h1>Skyfall</h1></body></html>", encoding="utf-8")

    result = vdc.audit_file(art)
    assert result["summary"]["errors"] == 0, result["findings"]
    assert result["passed"] is True, result


def test_a_real_hero_gradient_still_fails_the_gate(tmp_path):
    art = tmp_path / "hero.html"
    art.write_text(
        '<html><body class="bg-gradient-to-r from-purple-600 to-pink-500">'
        "<h1>Skyfall</h1></body></html>", encoding="utf-8")

    result = vdc.audit_file(art)
    assert result["summary"]["errors"] >= 1, result["findings"]
    assert result["passed"] is False, result


def test_the_pink_family_is_declared_and_non_empty():
    """Derived from the module constant, so a rename cannot leave this stale."""
    assert vdc._GRAD_PINKISH_HUES, "the pink-family set is empty; nothing can match"
    for hue in vdc._GRAD_PINKISH_HUES:
        assert vdc._GRAD_TO.search(f"to-{hue}-500"), hue
