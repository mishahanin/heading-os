"""Unit tests for scripts/utils/council_freshness.py — proxy-catalog pin check."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils import council_freshness as cf


def test_classify_ok_when_pin_present():
    f = cf.classify_proxy_model("grok", "grok-4.5", ["grok-4.5", "gemini-3-flash"])
    assert f["status"] == "ok" and f["candidate"] is None


def test_classify_broken_when_pin_absent():
    f = cf.classify_proxy_model("kimi", "kimi-for-coding", ["grok-4.5"])
    assert f["status"] == "broken" and f["candidate"] is None


def test_classify_unknown_when_catalog_none():
    f = cf.classify_proxy_model("gemini", "gemini-3-flash", None)
    assert f["status"] == "unknown"


def test_assess_over_three_voices_all_ok():
    findings = cf.assess(probes={"proxy": ["gemini-3-flash", "grok-4.5", "kimi-for-coding"]})
    assert {f["provider"] for f in findings} == {"gemini", "grok", "kimi"}
    assert all(f["status"] == "ok" for f in findings)


def test_assess_flags_broken_pin():
    findings = cf.assess(probes={"proxy": ["gemini-3-flash", "grok-4.5"]})  # kimi absent
    kimi = next(f for f in findings if f["provider"] == "kimi")
    assert kimi["status"] == "broken"


def test_nudge_line_empty_when_all_ok():
    findings = cf.assess(probes={"proxy": ["gemini-3-flash", "grok-4.5", "kimi-for-coding"]})
    assert cf.nudge_line(findings) == ""


def test_nudge_line_surfaces_broken():
    findings = cf.assess(probes={"proxy": ["gemini-3-flash", "grok-4.5"]})
    line = cf.nudge_line(findings)
    assert line.startswith("Council models:")
    assert "kimi-for-coding" in line
