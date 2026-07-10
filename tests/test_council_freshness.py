"""Unit tests for scripts/utils/council_freshness.py -- the /council pin freshness check."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils import council_freshness as cf


# ---- parse_version -------------------------------------------------------

@pytest.mark.parametrize("model_id,expected", [
    ("grok-4.5", (4, 5)),
    ("gemini-3.5-flash", (3, 5)),
    ("kimi-k2.6:cloud", (2, 6)),
    ("grok-4", (4,)),
    ("bge-m3", (3,)),
    ("no-version-here", None),
])
def test_parse_version(model_id, expected):
    assert cf.parse_version(model_id) == expected


# ---- is_variant ----------------------------------------------------------

@pytest.mark.parametrize("model_id", [
    "grok-4-mini", "gemini-3.5-flash-lite", "grok-code-1", "gemini-2.5-flash-preview",
    "kimi-latest:cloud", "grok-4-vision",
])
def test_is_variant_true(model_id):
    assert cf.is_variant(model_id)


@pytest.mark.parametrize("model_id", ["grok-4.5", "gemini-3.5-flash", "kimi-k2.6:cloud"])
def test_is_variant_false(model_id):
    assert not cf.is_variant(model_id)


# ---- newer_flagship ------------------------------------------------------

def test_newer_flagship_finds_higher_same_family():
    avail = ["grok-4.5", "grok-4.6", "grok-3", "grok-4-mini", "gemini-3.5-flash"]
    assert cf.newer_flagship("grok-4.5", avail) == "grok-4.6"


def test_newer_flagship_none_when_pin_is_newest():
    avail = ["grok-4.5", "grok-4.4", "grok-4-mini"]
    assert cf.newer_flagship("grok-4.5", avail) is None


def test_newer_flagship_excludes_variants_even_if_higher():
    # grok-4.9-mini is higher but a variant -> not proposed.
    avail = ["grok-4.5", "grok-4.9-mini", "grok-4.9-code"]
    assert cf.newer_flagship("grok-4.5", avail) is None


def test_newer_flagship_respects_family_stem():
    # gemini-flash pin must not jump to a gemini-pro id.
    avail = ["gemini-3.5-flash", "gemini-4.0-pro"]
    assert cf.newer_flagship("gemini-3.5-flash", avail) is None


def test_newer_flagship_gemini_flash_bump():
    avail = ["gemini-3.5-flash", "gemini-4.0-flash", "gemini-3.5-flash-lite"]
    assert cf.newer_flagship("gemini-3.5-flash", avail) == "gemini-4.0-flash"


def test_newer_flagship_kimi_cloud_tag():
    avail = ["kimi-k2.6:cloud", "kimi-k2.7:cloud", "bge-m3:latest"]
    assert cf.newer_flagship("kimi-k2.6:cloud", avail) == "kimi-k2.7:cloud"


def test_newer_flagship_general_kimi_never_jumps_to_code():
    # A general kimi pin must NOT adopt a '-code' variant, even if higher.
    avail = ["kimi-k2.6:cloud", "kimi-k2.7-code:cloud"]
    assert cf.newer_flagship("kimi-k2.6:cloud", avail) is None


def test_newer_flagship_code_pin_tracks_newer_code():
    # A code pin IS pin-relative: 'code' is not a disqualifying marker for it.
    avail = ["kimi-k2.7-code:cloud", "kimi-k2.8-code:cloud"]
    assert cf.newer_flagship("kimi-k2.7-code:cloud", avail) == "kimi-k2.8-code:cloud"


def test_newer_flagship_code_pin_still_excludes_extra_variant():
    # A code pin does not jump to a code+mini id (mini is an extra marker).
    avail = ["kimi-k2.7-code:cloud", "kimi-k2.9-code-mini:cloud"]
    assert cf.newer_flagship("kimi-k2.7-code:cloud", avail) is None


# ---- classify_direct_api (grok/gemini) -----------------------------------

def test_direct_api_ok():
    f = cf.classify_direct_api("grok", "grok-4.5", ["grok-4.5", "grok-4-mini"])
    assert f["status"] == "ok"
    assert f["candidate"] is None


def test_direct_api_newer():
    f = cf.classify_direct_api("grok", "grok-4.5", ["grok-4.6"])
    assert f["status"] == "newer"
    assert f["candidate"] == "grok-4.6"


def test_direct_api_unknown_on_probe_failure():
    f = cf.classify_direct_api("gemini", "gemini-3.5-flash", None)
    assert f["status"] == "unknown"


def test_direct_api_never_broken_when_pin_absent():
    # Cloud API may not enumerate the exact snapshot -> absence is NOT breakage.
    f = cf.classify_direct_api("grok", "grok-4.5", ["grok-4.6-only"])
    assert f["status"] != "broken"


# ---- classify_ollama_model (kimi) ----------------------------------------

def test_ollama_ok():
    f = cf.classify_ollama_model("kimi", "kimi-k2.6:cloud", ["kimi-k2.6:cloud", "bge-m3:latest"])
    assert f["status"] == "ok"


def test_ollama_broken_when_pin_not_pulled():
    # The concrete bug: pin kimi-k2.7:cloud but only k2.6 is in ollama.
    f = cf.classify_ollama_model("kimi", "kimi-k2.7:cloud", ["kimi-k2.6:cloud"])
    assert f["status"] == "broken"
    assert f["candidate"] is None


def test_ollama_newer_when_higher_tag_present_but_unpinned():
    f = cf.classify_ollama_model("kimi", "kimi-k2.6:cloud", ["kimi-k2.6:cloud", "kimi-k2.7:cloud"])
    assert f["status"] == "newer"
    assert f["candidate"] == "kimi-k2.7:cloud"


def test_ollama_unknown_when_unreachable():
    f = cf.classify_ollama_model("kimi", "kimi-k2.6:cloud", None)
    assert f["status"] == "unknown"


# ---- nudge_line ----------------------------------------------------------

def test_nudge_line_empty_when_all_ok():
    findings = [
        cf.classify_direct_api("grok", "grok-4.5", ["grok-4.5"]),
        cf.classify_ollama_model("kimi", "kimi-k2.6:cloud", ["kimi-k2.6:cloud"]),
    ]
    assert cf.nudge_line(findings) == ""


def test_nudge_line_includes_apply_command_for_candidates():
    findings = [
        cf.classify_direct_api("grok", "grok-4.5", ["grok-4.6"]),
        cf.classify_ollama_model("kimi", "kimi-k2.7:cloud", ["kimi-k2.6:cloud"]),
    ]
    line = cf.nudge_line(findings)
    assert "grok-4.6 available" in line
    assert "not in ollama" in line
    # grok has a candidate -> apply command present; broken kimi has none.
    assert "--set grok=grok-4.6" in line


def test_nudge_line_broken_only_has_no_apply_command():
    findings = [cf.classify_ollama_model("kimi", "kimi-k2.7:cloud", ["kimi-k2.6:cloud"])]
    line = cf.nudge_line(findings)
    assert line.startswith("Council models:")
    assert "Apply:" not in line


# ---- assess (injected probes) --------------------------------------------

def test_assess_with_injected_probes():
    findings = cf.assess(probes={
        "xai": ["grok-4.5"],
        "gemini": ["gemini-3.5-flash"],
        "ollama": ["kimi-k2.6:cloud", "kimi-k2.7-code:cloud", "bge-m3:latest"],
    })
    assert {f["provider"] for f in findings} == {"grok", "gemini", "kimi", "kimi-code"}
    # With current pins matching, everything should be ok.
    assert all(f["status"] == "ok" for f in findings)


def test_assess_flags_kimi_code_broken_when_not_pulled():
    findings = cf.assess(probes={
        "xai": ["grok-4.5"],
        "gemini": ["gemini-3.5-flash"],
        "ollama": ["kimi-k2.6:cloud"],  # kimi-code tag absent
    })
    code = next(f for f in findings if f["provider"] == "kimi-code")
    assert code["status"] == "broken"
