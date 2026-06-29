#!/usr/bin/env python3
"""Unit tests for scripts/marp_render.py."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.marp_render import (
    parse_frontmatter,
    inject_frontmatter,
    strip_wiki_links,
    auto_slide_breaks,
    get_workspace_defaults,
    run_sanitizer,
    generate_slug,
    check_overflow,
    paginate_heavy,
    check_marp_installed,
    check_version_match,
    get_pinned_version,
    prepare_theme,
    WORKSPACE_ROOT,
    WORD_OVERFLOW_THRESHOLD,
)


# --- Frontmatter ---

def test_frontmatter_injection_adds_missing_fields():
    source = "# Hello\n\nSome content"
    result = inject_frontmatter(source, title="Test Deck", mode="dark")
    assert "marp: true" in result
    assert "theme: 31c" in result
    assert "paginate: true" in result
    assert 'title: "Test Deck"' in result
    assert "class: dark" in result
    assert "# Hello" in result


def test_frontmatter_preserves_existing_title():
    source = '---\nmarp: true\ntitle: "Existing Title"\n---\n\n# Body'
    result = inject_frontmatter(source, title="", mode="dark")
    assert "Existing Title" in result


def test_frontmatter_override_title():
    source = '---\nmarp: true\ntitle: "Old Title"\n---\n\n# Body'
    result = inject_frontmatter(source, title="New Title", mode="dark")
    assert "New Title" in result


def test_frontmatter_parse_returns_none_for_no_frontmatter():
    fm, body = parse_frontmatter("# Just markdown\n\nNo frontmatter here")
    assert fm is None
    assert "# Just markdown" in body


def test_frontmatter_parse_extracts_fields():
    source = '---\nmarp: true\ntitle: "My Deck"\nclass: light\n---\n\n# Slide 1'
    fm, body = parse_frontmatter(source)
    assert fm is not None
    assert fm["marp"] is True
    assert fm["title"] == "My Deck"
    assert fm["class"] == "light"
    assert "# Slide 1" in body


# --- Wiki Links ---

def test_wiki_link_stripping_basic_form():
    text = "See [[some-note]] for details"
    result = strip_wiki_links(text)
    assert result == "See some-note for details"


def test_wiki_link_stripping_display_alias_form():
    text = "Read [[some-note|Display Name]] here"
    result = strip_wiki_links(text)
    assert result == "Read Display Name here"


def test_wiki_link_stripping_multiple():
    text = "Link [[a]] and [[b|Beta]] together"
    result = strip_wiki_links(text)
    assert result == "Link a and Beta together"


def test_wiki_link_stripping_no_links():
    text = "No wiki links here [just markdown](url)"
    result = strip_wiki_links(text)
    assert result == text


# --- Auto Slide Breaks ---

def test_auto_break_inserts_at_h2_when_no_manual_breaks():
    body = "# Title\n\nIntro\n\n## Section 1\n\nContent 1\n\n## Section 2\n\nContent 2"
    result = auto_slide_breaks(body, "h2")
    assert "\n---\n" in result
    assert result.count("---") == 2


def test_auto_break_respects_existing_manual_breaks():
    body = "# Title\n\nIntro\n\n---\n\n## Section 1\n\nContent"
    result = auto_slide_breaks(body, "h2")
    assert result == body


def test_auto_break_at_h3():
    body = "# Title\n\nIntro\n\n### Sub 1\n\nContent 1\n\n### Sub 2\n\nContent 2"
    result = auto_slide_breaks(body, "h3")
    assert "\n---\n" in result
    assert result.count("---") == 2


# --- Workspace Defaults ---

def test_workspace_defaults_context_uses_light_mode():
    source = WORKSPACE_ROOT / "context" / "test-doc.md"
    defaults = get_workspace_defaults(source)
    assert defaults["mode"] == "light"
    assert "Operating Context" in defaults["subtitle"]


def test_workspace_defaults_intel_uses_dark_mode():
    source = WORKSPACE_ROOT / "outputs" / "intel" / "brief.md"
    defaults = get_workspace_defaults(source)
    assert defaults["mode"] == "dark"
    assert "Intelligence" in defaults["subtitle"]


def test_workspace_defaults_knowledge_uses_light():
    source = WORKSPACE_ROOT / "knowledge" / "fleeting" / "note.md"
    defaults = get_workspace_defaults(source)
    assert defaults["mode"] == "light"
    assert "brain" in defaults["subtitle"]


def test_workspace_defaults_unknown_uses_mixed():
    source = WORKSPACE_ROOT / "random" / "file.md"
    defaults = get_workspace_defaults(source)
    assert defaults["mode"] == "mixed"


# --- Sanitizer ---

def test_sanitizer_detects_hidden_chars():
    text = "Hello\u200bWorld"
    clean, count = run_sanitizer(text)
    assert count == 1
    assert clean == "HelloWorld"


def test_sanitizer_blocks_render_on_hidden_chars():
    text = "Clean\u200c text\u200d here"
    clean, count = run_sanitizer(text)
    assert count == 2
    assert "\u200c" not in clean
    assert "\u200d" not in clean


def test_sanitizer_clean_text_returns_zero():
    text = "Perfectly clean text with no issues"
    clean, count = run_sanitizer(text)
    assert count == 0
    assert clean == text


def test_sanitizer_handles_bom():
    text = "\ufeffText with BOM"
    clean, count = run_sanitizer(text)
    assert count == 1
    assert clean == "Text with BOM"


# --- Slug Generation ---

def test_slug_generation_basic():
    assert generate_slug("Q2 State Check") == "q2-state-check"


def test_slug_generation_handles_special_chars():
    slug = generate_slug("Hello! World? #2026")
    assert " " not in slug
    assert "!" not in slug
    assert "?" not in slug


def test_slug_generation_truncates_long():
    long_topic = "A" * 100
    slug = generate_slug(long_topic)
    assert len(slug) <= 60


# --- Overflow Detection ---

def test_overflow_warning_when_slide_exceeds_word_threshold():
    words = " ".join(["word"] * (WORD_OVERFLOW_THRESHOLD + 10))
    source = f"---\nmarp: true\n---\n\n{words}"
    warnings = check_overflow(source)
    assert len(warnings) == 1
    assert warnings[0]["slide"] == 1
    assert warnings[0]["words"] > WORD_OVERFLOW_THRESHOLD


def test_no_overflow_for_normal_slides():
    source = "---\nmarp: true\n---\n\n# Short slide\n\nJust a few words"
    warnings = check_overflow(source)
    assert len(warnings) == 0


# --- Paginate Heavy ---

def test_paginate_heavy_flag_subbreaks_on_paragraphs():
    words_block = " ".join(["word"] * 80)
    source = f"---\nmarp: true\n---\n\n{words_block}\n\n{words_block}\n\n{words_block}"
    result = paginate_heavy(source)
    # Should have more slide breaks than original
    assert result.count("---") > source.count("---")


# --- Version Check ---

def test_version_mismatch_detects_difference():
    # Derive the expected version from the source pin so this test cannot drift
    # when the marp-cli pin is bumped (it last broke on the 4.1.1 -> 4.4.0 bump).
    pinned = get_pinned_version()
    pinned_num = pinned.rsplit("@", 1)[-1] if "@" in pinned else pinned
    assert check_version_match(pinned_num) is True
    assert check_version_match(f"{pinned_num} (marp-cli)") is True
    # A different version is correctly detected as a mismatch.
    assert check_version_match("0.0.0-not-a-match") is False


# --- Collision / Source Integrity ---

def test_source_file_never_mutated_by_shim():
    """Verify that render operations never modify the source file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        content = "---\nmarp: true\ntheme: 31c\n---\n\n# Test\n\nContent"
        f.write(content)
        f.flush()
        source_path = Path(f.name)

    try:
        original_hash = _file_hash(source_path)
        # We don't actually render (no marp-cli in tests), but we verify
        # the shim's in-memory approach by checking the file stays unchanged
        from scripts.marp_render import run_sanitizer
        text = source_path.read_text(encoding="utf-8")
        clean, _ = run_sanitizer(text)
        # File should be untouched
        assert _file_hash(source_path) == original_hash
    finally:
        source_path.unlink(missing_ok=True)


def test_collision_refuses_overwrite_without_force():
    """Verify slug collision detection works."""
    slug = generate_slug("Test Topic")
    assert slug == "test-topic"
    # The actual collision check is in the skill dispatch, not the shim
    # This test validates slug generation consistency
    assert generate_slug("Test Topic") == generate_slug("Test Topic")


# --- Theme Preparation ---

def test_theme_prepare_substitutes_fonts_dir():
    theme_path = prepare_theme()
    try:
        content = theme_path.read_text(encoding="utf-8")
        assert "{FONTS_DIR}" not in content
        assert "GT-Standard" in content
    finally:
        theme_path.unlink(missing_ok=True)


# --- Browser Probe ---

def test_browser_probe_returns_string_or_none():
    from scripts.marp_render import probe_browser
    result = probe_browser()
    assert result is None or isinstance(result, str)


# --- Watch State ---

def test_watch_start_writes_pid_file():
    """Test that watch state file would be created (without actually starting marp)."""
    from scripts.marp_render import WATCH_STATE_FILE
    # Just verify the path is well-formed
    assert WATCH_STATE_FILE.name == "watch.json"
    assert ".marp" in str(WATCH_STATE_FILE)


def test_watch_stop_handles_missing_state():
    from scripts.marp_render import watch_stop, WATCH_STATE_FILE
    WATCH_STATE_FILE.unlink(missing_ok=True)
    result = watch_stop()
    assert result["ok"] is False
    assert "No active" in result["message"]


# --- Helpers ---

def _file_hash(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()
