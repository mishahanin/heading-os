#!/usr/bin/env python3
"""Unit tests for scripts/marp_render.py."""

import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
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
    substitute_theme_fonts,
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

def _marp():
    import scripts.marp_render as marp
    return marp


def _fake_marp(monkeypatch, marp):
    """Stand in for marp-cli: report installed, and write whatever `-o` names.

    marp-cli is not installed in the test environment, so `render()` returned
    `marp-not-installed` at line 6 of a 200-line function and every claim past
    that point went unmeasured. Faking the four seams (`check_marp_installed`,
    `check_version_match`, `probe_browser`, `_run_marp`) drives the real body:
    the sanitize branch, the temp-source write, the output collection and the
    `finally` cleanup all run.
    """
    monkeypatch.setattr(marp, "check_marp_installed", lambda: (True, "0.0.0-fake"))
    monkeypatch.setattr(marp, "check_version_match", lambda v: True)
    monkeypatch.setattr(marp, "probe_browser", lambda: None)
    monkeypatch.setattr(marp, "_resolve_marp_bin", lambda: "/nonexistent/marp")
    seen = []

    def fake_run(cmd):
        seen.append(list(cmd))
        out = Path(cmd[cmd.index("-o") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("rendered\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(marp, "_run_marp", fake_run)
    return seen


def test_render_never_mutates_the_source_file(tmp_path, monkeypatch):
    """The shim renders from a temporary copy, and leaves the original alone.

    This was `test_source_file_never_mutated_by_shim`, and it never rendered.
    Its body read the file and called `run_sanitizer` on the returned STRING,
    then asserted the file's hash was unchanged - which is true of any program
    that opens a file for reading, and would stay true if `render` overwrote the
    source on the very next line. Its own comment said "we don't actually
    render".

    The input carries a zero-width space on purpose: the sanitize branch is
    where a naive implementation writes the cleaned text back over the source,
    and it is the only branch in `render` that has a reason to write near it.
    """
    marp = _marp()
    source = tmp_path / "deck.md"
    # The zero-width space is written as an escape, never as a literal: this
    # file is scanned by the hidden-character gate like every other.
    source.write_text(
        "---\nmarp: true\ntheme: 31c\n---\n\n# Test\u200b\n\nContent\n",
        encoding="utf-8")
    before = source.read_bytes()
    assert b"\xe2\x80\x8b" in before, "the fixture must carry a hidden character"

    _fake_marp(monkeypatch, marp)
    result = marp.render(source, output_dir=tmp_path / "out", pdf_only=True)

    assert result["ok"] is True, result
    assert source.read_bytes() == before, "render rewrote its own source file"
    # The temp copy lives BESIDE the source (relative image paths), so a leaked
    # one is litter inside the operator's deck directory.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["deck.md", "out"], (
        sorted(p.name for p in tmp_path.iterdir()))
    # and the hidden character never reached marp: the temp source is what was
    # rendered, and it was the sanitized text.
    assert result["hidden_characters"].startswith("1 found"), result


def test_the_shim_holds_no_collision_or_force_handling():
    """Named for what is true, and asserting the boundary rather than nothing.

    This was `test_collision_refuses_overwrite_without_force`, and it exercised
    no collision and no force. Its last line was
    `assert generate_slug("Test Topic") == generate_slug("Test Topic")` - a pure
    function compared to itself, which holds for every input and cannot fail.
    Its own comment already admitted "the actual collision check is in the skill
    dispatch, not the shim", so the name promised a contract this module does
    not have. Slug FORMATTING is covered three times above, at
    `test_generate_slug_*`.

    What is worth pinning is the boundary itself: if half a collision check
    lands here, the two places disagree about who refuses an overwrite, and
    that is the bug this file would then need to catch.
    """
    assert generate_slug("Test Topic") == "test-topic"

    src = (Path(__file__).resolve().parent.parent
           / "scripts" / "marp_render.py").read_text(encoding="utf-8")
    for token in ("--force", "force=", "exist_ok=False", "overwrite"):
        assert token not in src, (
            f"{token!r} appeared in the marp shim. Overwrite policy lives in "
            "the skill dispatch; two owners for one refusal is how a deck gets "
            "silently replaced."
        )


# --- Theme Preparation ---

def test_theme_prepare_substitutes_fonts_dir():
    """Both placeholder families have to be gone, not just the directory one.

    The face names used to be literals in the `.css.tmpl`, so this test asserted
    a real datastore filename fragment was present. On 2026-09-02 the operator
    ruled that a datastore filename is private and this repository is public, so
    the names moved to the manifest and the assertion moved with them: what is
    checked now is that nothing is left unsubstituted, which is the property that
    holds whether or not this clone has a data overlay.
    """
    theme_path = prepare_theme()
    try:
        content = theme_path.read_text(encoding="utf-8")
        assert "{FONTS_DIR}" not in content
        assert not re.findall(r"\{FONT_[A-Z0-9_]+\}", content)
        assert ".woff2" in content, "the @font-face src lines went missing"
    finally:
        theme_path.unlink(missing_ok=True)


def test_a_theme_face_resolves_to_the_name_the_manifest_registers(tmp_path,
                                                                  monkeypatch):
    """The seam, driven by an invented manifest so no real name is in this file."""
    data = tmp_path / "data"
    (data / "config").mkdir(parents=True)
    (data / "config" / "brand-assets.json").write_text(
        json.dumps({"font_gt_m_medium": "brand/fonts/Kestrel/Kestrel-Text-Medium.woff2"}),
        encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(data))

    out = substitute_theme_fonts("src: url('{FONTS_DIR}/{FONT_GT_M_MEDIUM}')")

    assert out == "src: url('{FONTS_DIR}/Kestrel-Text-Medium.woff2')"


def test_a_missing_manifest_degrades_the_faces_and_says_so(tmp_path, monkeypatch,
                                                           capsys):
    """A public clone has no manifest and no licensed fonts, and must still render.

    `themes/fonts/README.md` documents the system-font fallback as the normal
    state there, so refusing would take a working deck away from a clone that
    never had the faces. The refusal still has to be VISIBLE, or an operator who
    does have the fonts cannot tell why the deck came out unbranded.
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("HEADING_OS_DATA", str(empty))

    out = substitute_theme_fonts("src: url('{FONTS_DIR}/{FONT_GT_M_MEDIUM}')")

    assert "{FONT_" not in out
    assert "unresolved-brand-face.woff2" in out
    assert "brand-asset manifest could not be read" in capsys.readouterr().err


def test_a_manifest_missing_one_key_names_that_key(tmp_path, monkeypatch, capsys):
    """A partial manifest is the likelier fault than a missing one, and a face
    that silently vanished is the failure this whole seam exists to avoid."""
    data = tmp_path / "data"
    (data / "config").mkdir(parents=True)
    (data / "config" / "brand-assets.json").write_text(
        json.dumps({"font_gt_m_light": "brand/fonts/Kestrel/Kestrel-Text-Light.woff2"}),
        encoding="utf-8")
    monkeypatch.setenv("HEADING_OS_DATA", str(data))

    out = substitute_theme_fonts("url('{FONT_GT_M_LIGHT}') url('{FONT_GT_S_MEDIUM}')")

    assert "Kestrel-Text-Light.woff2" in out
    assert "unresolved-brand-face.woff2" in out
    assert "font_gt_s_medium" in capsys.readouterr().err


def test_an_unresolvable_data_root_does_not_kill_the_render(tmp_path, monkeypatch,
                                                            capsys):
    """`DataRootError` is deliberately not an `OSError`, so nothing upstream of
    `prepare_theme` catches it. A set-but-missing HEADING_OS_DATA would have
    turned a render that used to work into a traceback."""
    monkeypatch.setenv("HEADING_OS_DATA", str(tmp_path / "gone"))

    out = substitute_theme_fonts("url('{FONT_GT_M_MEDIUM}')")

    assert "unresolved-brand-face.woff2" in out
    assert "HEADING_OS_DATA" in capsys.readouterr().err


# --- Browser Probe ---

@pytest.mark.skipif(platform.system() != "Linux",
                    reason="the candidate list under test is the Linux branch")
def test_browser_probe_picks_a_chromium_and_refuses_firefox(monkeypatch):
    """`assert result is None or isinstance(result, str)` was the whole test.

    That holds for every function whose annotation is `str | None`, and it held
    on this machine whether the probe found Brave or found nothing, so it could
    not tell a working lookup from a broken one. What matters is which browser
    comes back: marp drives Puppeteer over the Chrome DevTools Protocol, so a
    Firefox path is not a degraded answer, it is a broken render.

    `shutil` is replaced as a NAME on the module, never by rebinding
    `shutil.which` itself: that attribute belongs to `sys.modules["shutil"]`,
    which every other module in the interpreter shares.
    """
    marp = _marp()

    def only(*names):
        table = {n: f"/usr/bin/{n}" for n in names}
        return SimpleNamespace(which=lambda name: table.get(name))

    monkeypatch.setattr(marp, "shutil", only("chromium"))
    assert marp.probe_browser() == "/usr/bin/chromium"

    monkeypatch.setattr(marp, "shutil", only("firefox", "firefox-esr", "safari"))
    assert marp.probe_browser() is None, (
        "a Firefox path was accepted; marp needs a Chromium-family browser and "
        "a wrong one fails at render time with an opaque Puppeteer error")

    monkeypatch.setattr(marp, "shutil", only())
    assert marp.probe_browser() is None


# --- Watch State ---

def test_watch_start_writes_the_state_file_and_reports_the_pid(tmp_path, monkeypatch):
    """The name promised a write; the body asserted a filename and a substring.

    `WATCH_STATE_FILE.name == "watch.json"` is a fact about a module constant.
    It stays true if `watch_start` never writes anything, which is what
    `/marp watch stop` and `watch_status` both depend on it having done.

    `Popen` is faked, so no marp-cli is spawned and no port is bound; the state
    file is redirected into tmp_path, so the operator's live `~/.marp/watch.json`
    is not touched (this file has deleted it once already, orphaning a running
    watch, which is why the neighbour below carries its own warning).
    """
    marp = _marp()
    source = tmp_path / "deck.md"
    source.write_text("---\nmarp: true\n---\n\n# Deck\n", encoding="utf-8")
    state_file = tmp_path / ".marp" / "watch.json"
    monkeypatch.setattr(marp, "WATCH_STATE_FILE", state_file)
    monkeypatch.setattr(marp, "_resolve_marp_bin", lambda: "/nonexistent/marp")
    monkeypatch.setattr(marp, "probe_browser", lambda: None)

    spawned = []

    def fake_popen(cmd, **kw):
        spawned.append(list(cmd))
        return SimpleNamespace(pid=424242)

    # The `subprocess` NAME on the module, never `subprocess.Popen` itself:
    # `marp.subprocess` IS `sys.modules["subprocess"]`, so rebinding its
    # attribute poisons every other module in the interpreter for the duration.
    # `DEVNULL` is carried through because `watch_start` reads it.
    monkeypatch.setattr(marp, "subprocess", SimpleNamespace(
        Popen=fake_popen, DEVNULL=subprocess.DEVNULL))
    assert sys.modules["subprocess"].Popen is subprocess.Popen
    result = marp.watch_start(source)

    assert result["ok"] is True, result
    assert result["pid"] == 424242
    assert state_file.is_file(), "watch_start wrote no state file"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["pid"] == 424242
    assert state["source_path"] == str(source)
    assert spawned and "--watch" in spawned[0]


def test_watch_stop_handles_missing_state(tmp_path, monkeypatch):
    """Point the module at a tmp path; never unlink the operator's real one.

    The old body did `WATCH_STATE_FILE.unlink(missing_ok=True)` on the REAL
    `~/.marp/watch.json`. With an actual `marp --watch` session running, this
    test deleted its live PID/state file, orphaning the process and leaving the
    real watch unmanageable — a test that mutates production state to check a
    not-found branch.
    """
    import scripts.marp_render as marp

    monkeypatch.setattr(marp, "WATCH_STATE_FILE", tmp_path / ".marp" / "watch.json")
    assert not marp.WATCH_STATE_FILE.exists()
    result = marp.watch_stop()
    assert result["ok"] is False
    assert "No active" in result["message"]
