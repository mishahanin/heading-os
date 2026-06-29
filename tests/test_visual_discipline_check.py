"""Tests for scripts/visual-discipline-check.py.

Mechanical scanner for the AI-default visual tells named in
.claude/rules/visual-design-discipline.md (forbidden fonts, purple->pink
gradient, oversized Tailwind radii, Lucide/Heroicons icon libraries, the
ChatGPT-emerald / captured-pastel hero colors, etc.). Loaded via importlib
because the CLI filename is kebab-case.
"""
from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "scripts" / "visual-discipline-check.py"


def _load():
    spec = importlib.util.spec_from_file_location("visual_discipline_check", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vdc = _load()


def _types(findings):
    return {f["type"] for f in findings}


def _errors(findings):
    return [f for f in findings if f["severity"] == "error"]


# ---------------------------------------------------------------------------
# Forbidden fonts
# ---------------------------------------------------------------------------

def test_flags_inter_font():
    findings = vdc.scan_text("body { font-family: Inter, sans-serif; }")
    errs = _errors(findings)
    assert any(f["type"] == "forbidden_font" and "Inter" in f["tell"] for f in errs)


def test_flags_google_fonts_poppins():
    findings = vdc.scan_text(
        '<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@400">'
    )
    assert any(f["type"] == "forbidden_font" and "Poppins" in f["tell"] for f in _errors(findings))


def test_gt_standard_font_is_clean():
    findings = vdc.scan_text("body { font-family: 'GT Standard', Geist, sans-serif; }")
    assert not any(f["type"] == "forbidden_font" for f in findings)


def test_interface_word_is_not_a_font_false_positive():
    # "Interface" contains "Inter" but is not a font-family declaration.
    findings = vdc.scan_text("<h2>The analyst interface</h2>")
    assert not any(f["type"] == "forbidden_font" for f in findings)


# ---------------------------------------------------------------------------
# Gradient / color
# ---------------------------------------------------------------------------

def test_flags_purple_pink_tailwind_gradient():
    findings = vdc.scan_text('<div class="bg-gradient-to-r from-purple-500 to-pink-500"></div>')
    assert any(f["type"] == "gradient_purple_pink" for f in _errors(findings))


def test_flags_chatgpt_emerald():
    findings = vdc.scan_text("a { color: #10A37F; }")
    assert any(f["type"] == "banned_color" for f in _errors(findings))


def test_flags_captured_pastel():
    findings = vdc.scan_text("section { background: #E8DDF4; }")
    assert any(f["type"] == "banned_color" for f in _errors(findings))


def test_brand_color_is_clean():
    # 31C orange + ODUN blue are fine.
    findings = vdc.scan_text(".accent { color: #F26522; } .blue { color: #1B3A5B; }")
    assert not any(f["type"] in ("banned_color", "gradient_purple_pink") for f in findings)


# ---------------------------------------------------------------------------
# Radii + icon libraries
# ---------------------------------------------------------------------------

def test_flags_rounded_2xl():
    findings = vdc.scan_text('<div class="rounded-2xl p-4"></div>')
    assert any(f["type"] == "rounded_oversized" for f in _errors(findings))


def test_rounded_md_is_clean():
    findings = vdc.scan_text('<div class="rounded-md p-4"></div>')
    assert not any(f["type"] == "rounded_oversized" for f in findings)


def test_flags_lucide_icons():
    findings = vdc.scan_text('<i data-lucide="activity"></i>')
    assert any(f["type"] == "icon_library" for f in _errors(findings))


# ---------------------------------------------------------------------------
# Advisory tells
# ---------------------------------------------------------------------------

def test_title_case_heading_is_advisory():
    findings = vdc.scan_text("<h1>Build The Future Of Sovereign Networks</h1>")
    warns = [f for f in findings if f["severity"] == "warning"]
    assert any(f["type"] == "title_case_heading" for f in warns)


def test_line_numbers_reported():
    text = "line1\nline2\nbody { font-family: Inter; }\n"
    findings = vdc.scan_text(text)
    font = [f for f in findings if f["type"] == "forbidden_font"][0]
    assert font["line"] == 3


# ---------------------------------------------------------------------------
# PPTX
# ---------------------------------------------------------------------------

def test_pptx_typeface_inter_flagged(tmp_path):
    pptx = tmp_path / "deck.pptx"
    theme_xml = (
        '<?xml version="1.0"?><a:theme xmlns:a="x">'
        '<a:fontScheme><a:majorFont><a:latin typeface="Inter"/></a:majorFont>'
        '</a:fontScheme></a:theme>'
    )
    with zipfile.ZipFile(pptx, "w") as z:
        z.writestr("ppt/theme/theme1.xml", theme_xml)
        z.writestr("ppt/slides/slide1.xml", '<p:sld xmlns:p="x"></p:sld>')
    result = vdc.audit_file(pptx)
    assert any(
        f["type"] == "forbidden_font" and "Inter" in f["tell"]
        for f in result["findings"]
    )
    assert result["passed"] is False


def test_clean_pptx_passes(tmp_path):
    pptx = tmp_path / "clean.pptx"
    theme_xml = (
        '<?xml version="1.0"?><a:theme xmlns:a="x">'
        '<a:fontScheme><a:majorFont><a:latin typeface="GT Standard"/></a:majorFont>'
        '</a:fontScheme></a:theme>'
    )
    with zipfile.ZipFile(pptx, "w") as z:
        z.writestr("ppt/theme/theme1.xml", theme_xml)
    result = vdc.audit_file(pptx)
    assert not any(f["severity"] == "error" for f in result["findings"])
    assert result["passed"] is True


# ---------------------------------------------------------------------------
# audit_file integration + exit semantics
# ---------------------------------------------------------------------------

def test_audit_file_html_passed_flag(tmp_path):
    good = tmp_path / "good.html"
    good.write_text("<style>body{font-family:'GT Standard';border-radius:6px}</style>")
    assert vdc.audit_file(good)["passed"] is True

    bad = tmp_path / "bad.html"
    bad.write_text('<div class="rounded-2xl" style="font-family:Inter"></div>')
    res = vdc.audit_file(bad)
    assert res["passed"] is False
    assert len(_errors(res["findings"])) >= 2
