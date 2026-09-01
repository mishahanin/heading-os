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


# ---------------------------------------------------------------------------
# Deep engine (impeccable) - the facade must not change under the default path
# ---------------------------------------------------------------------------

def test_default_audit_does_not_call_the_deep_engine(tmp_path):
    """Fifteen skills already call this CLI. Adding a second engine must not
    change what any of them sees unless they ask for it with --deep.
    """
    from scripts.utils import impeccable_engine

    calls = []
    original = impeccable_engine.run_detector
    impeccable_engine.run_detector = lambda *a, **k: (calls.append(1), ([], None))[1]
    try:
        page = tmp_path / "page.html"
        page.write_text("<html><body><h1>Fine</h1></body></html>", encoding="utf-8")
        result = vdc.audit_file(page)
    finally:
        impeccable_engine.run_detector = original

    assert calls == []
    assert result["findings"] == []


def test_deep_findings_merge_without_disturbing_the_regex_findings(tmp_path):
    """Both engines' findings live in one list, one severity partition, one
    exit code - no special-casing downstream.
    """
    page = tmp_path / "page.html"
    page.write_text("body { font-family: Inter; }", encoding="utf-8")

    deep = [{
        "type": "impeccable:side-tab",
        "severity": "error",
        "tell": "Side-tab accent border",
        "line": 3,
        "context": "border-left: 4px + border-radius: 8px",
        "file": str(page),
    }]
    result = vdc.audit_file(page, deep_findings=deep)

    types = _types(result["findings"])
    assert "forbidden_font" in types, "the regex finding must survive the merge"
    assert "impeccable:side-tab" in types
    assert result["summary"]["errors"] == 2
    assert result["passed"] is False


def test_minified_assets_are_skipped_by_the_file_walk(tmp_path):
    """What this actually measures, corrected 2026-09-01.

    The name says the suffix list keeps minified bundles out. It does not: none
    of `.min.js`, `.min.css` or `.min.mjs` has an extension in SCAN_EXTENSIONS
    (`.html`, `.htm`, `.svg`, `.pptx`), so the extension filter one line above
    rejects every one of them first and `OUT_OF_SCOPE_SUFFIXES` is unreachable
    inside `_iter_files`. MEASURED: deleting `.min.js` from that tuple left this
    test green.

    The exclusion is therefore asserted where it is real - the extension
    whitelist - and the suffix list is pinned separately, below, against the
    copy that does govern. The dead branch in `_iter_files` is left in place and
    named here rather than removed; it is a decision for whoever owns that file.
    """
    (tmp_path / "app.min.js").write_text("var a=1", encoding="utf-8")
    (tmp_path / "page.html").write_text("<html></html>", encoding="utf-8")

    walked = [p.name for p in vdc._iter_files(tmp_path, include_internal=False)]
    assert "page.html" in walked
    assert "app.min.js" not in walked
    assert ".js" not in vdc.SCAN_EXTENSIONS, (
        "a .js file is now in scope, which makes OUT_OF_SCOPE_SUFFIXES live in "
        "_iter_files; give it a test of its own rather than relying on this one"
    )


def test_the_two_minified_suffix_lists_cannot_drift_apart():
    """There are two of them, and only one is enforced.

    `visual-discipline-check.OUT_OF_SCOPE_SUFFIXES` is dead inside `_iter_files`
    (see above). The list that actually keeps a minified bundle away from a
    scanner is `out_of_scope.suffixes` in `config/visual-check-profiles.json`,
    read by `impeccable_engine.is_out_of_scope`, which the deep engine calls
    while walking a directory itself. The comment beside the constant says the
    deep engine enforces "the suffix list", singular, as though the two were one
    object. They are two, so adding `.min.ts` to either leaves the other behind
    and the sentence in the source becomes false without anything failing.
    """
    from scripts.utils.impeccable_engine import is_out_of_scope, load_profiles

    profiles, warning = load_profiles()
    assert warning is None, warning
    live = tuple(profiles["out_of_scope"]["suffixes"])
    assert live, "the profile config lists no minified suffixes at all"
    assert set(live) == set(vdc.OUT_OF_SCOPE_SUFFIXES), (
        f"config/visual-check-profiles.json lists {sorted(live)} while "
        f"visual-discipline-check.py lists {sorted(vdc.OUT_OF_SCOPE_SUFFIXES)}"
    )
    # And the live list is live: the enforced copy still rejects what it names.
    for suffix in live:
        assert is_out_of_scope(f"docs/assets/vendor-bundle{suffix}") is True
    assert is_out_of_scope("docs/ARCHITECTURE.html") is False
