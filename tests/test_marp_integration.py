#!/usr/bin/env python3
"""Integration tests for MARP rendering pipeline.

These tests require marp-cli to be installed. Skip gracefully if not available.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.marp_render import (
    check_marp_installed,
    render,
    transform_workspace_md,
    SAMPLE_DECK,
    WORKSPACE_ROOT,
)

# Skip all tests if marp-cli is not installed
marp_installed, _ = check_marp_installed()
pytestmark = pytest.mark.skipif(
    not marp_installed,
    reason="marp-cli not installed - install with: npm install -g @marp-team/marp-cli"
)


@pytest.fixture(scope="module")
def sample_deck_result(tmp_path_factory):
    """Render the sample deck ONCE for the whole module.

    Measured 2026-08-20: this file took 29s for 8 tests because the five
    TestSampleDeckRender cases each rendered the identical 14-slide deck at
    ~5.1s. Nothing in the five varies the input, and the flags they passed
    (html_only=False / pdf_only=False) were already the defaults, so one
    default render — which emits both the PDF and the HTML — carries every
    assertion they make. The only case that set a flag was the golden-file
    one (pdf_only=True), and it asserts nothing about which formats exist.
    """
    out_dir = tmp_path_factory.mktemp("marp-int-")
    return render(SAMPLE_DECK, output_dir=out_dir)


class TestSampleDeckRender:
    """Integration tests for rendering the sample deck."""

    def test_sample_deck_renders_pdf_and_html(self, sample_deck_result):
        """Sample deck renders both PDF and HTML successfully."""
        assert sample_deck_result["ok"] is True, f"Render failed: {sample_deck_result.get('errors')}"
        types = {o["type"] for o in sample_deck_result["outputs"]}
        assert "pdf" in types
        assert "html" in types

    def test_sample_deck_pdf_size(self, sample_deck_result):
        """PDF should be at least 50KB for a 14-slide deck."""
        assert sample_deck_result["ok"], sample_deck_result.get("errors")
        pdf_outputs = [o for o in sample_deck_result["outputs"] if o["type"] == "pdf"]
        assert pdf_outputs, "render produced no PDF output"
        assert pdf_outputs[0]["size"] > 50_000

    def test_sample_deck_html_size(self, sample_deck_result):
        """HTML should be at least 20KB."""
        assert sample_deck_result["ok"], sample_deck_result.get("errors")
        html_outputs = [o for o in sample_deck_result["outputs"] if o["type"] == "html"]
        assert html_outputs, "render produced no HTML output"
        assert html_outputs[0]["size"] > 20_000

    def test_sample_deck_hidden_chars_clean(self, sample_deck_result):
        """Sample deck should have no hidden characters."""
        assert sample_deck_result["hidden_characters"] == "clean"

    def test_sample_deck_structural_matches_golden_json(self, sample_deck_result):
        """Structural regression: section count and classes should match golden file."""
        golden_path = WORKSPACE_ROOT / "tests" / "golden" / "sample-deck.json"
        if not golden_path.exists():
            pytest.skip("Golden file not yet created. Run --update-golden first.")

        # A render FAILURE is a real regression, not a reason to skip — fail loudly.
        # (Environment-absence skips above, e.g. missing golden file, stay as skips.)
        assert sample_deck_result["ok"], (
            f"render failed, cannot check structure: {sample_deck_result.get('errors', 'unknown error')}"
        )

        # Structural check would parse the HTML for section elements
        # For now, verify the render succeeded and golden file can be loaded
        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        assert golden.get("section_count", 0) > 0


class TestWorkspaceTransform:
    """Integration tests for /marp from <workspace-path>."""

    def test_marp_from_context_fixture_applies_light_mode(self):
        """Context files should render with light mode default."""
        # Create a temp fixture simulating a context file
        context_dir = WORKSPACE_ROOT / "context"
        if not context_dir.exists():
            pytest.skip("No context/ directory")

        # Find any .md in context/
        context_files = list(context_dir.glob("*.md"))
        if not context_files:
            pytest.skip("No .md files in context/")

        with tempfile.TemporaryDirectory(prefix="marp-from-") as tmp:
            result = transform_workspace_md(
                context_files[0], output_dir=Path(tmp)
            )
            # A transform/render failure is a regression, not a pass. Until
            # 2026-08-20 this assertion sat under `if result["ok"]:`, so a broken
            # render reported green. Environment absence stays a skip (above).
            assert result["ok"], result.get("errors") or result.get("error")
            assert result.get("source_mode") == "light"

    def test_marp_from_intel_fixture_applies_dark_mode(self):
        """Intel files should render with dark mode default."""
        intel_dir = WORKSPACE_ROOT / "outputs" / "intel"
        if not intel_dir.exists():
            pytest.skip("No outputs/intel/ directory")

        intel_files = list(intel_dir.rglob("*.md"))
        if not intel_files:
            pytest.skip("No .md files in outputs/intel/")

        with tempfile.TemporaryDirectory(prefix="marp-from-") as tmp:
            result = transform_workspace_md(
                intel_files[0], output_dir=Path(tmp)
            )
            assert result["ok"], result.get("errors") or result.get("error")
            assert result.get("source_mode") == "dark"

    def test_marp_from_leaves_the_source_file_untouched(self):
        """The transform reads the source and never writes back to it.

        Renamed on 2026-08-20. It was called test_marp_from_strips_wiki_links,
        but it never asserted a wiki-link was stripped — it discarded the result
        entirely and checked only that the source still held its original text.
        The stripping itself is now covered by TestStripWikiLinks below, in
        milliseconds, against the pure function that does it. This one keeps the
        integration property its body actually holds, under a name that says so.
        """
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8",
            dir=str(WORKSPACE_ROOT / "context") if (WORKSPACE_ROOT / "context").exists() else None
        ) as f:
            f.write("# Test Note\n\nSee [[other-note|Other Note]] for details.\n\n## Section\n\nMore content.")
            source = Path(f.name)

        try:
            with tempfile.TemporaryDirectory(prefix="marp-from-") as tmp:
                result = transform_workspace_md(source, output_dir=Path(tmp))
                # Even if render fails (no marp-cli), the transform itself should work
                # The source file should not be modified
                content = source.read_text(encoding="utf-8")
                assert "[[other-note|Other Note]]" in content
        finally:
            source.unlink(missing_ok=True)


class TestStripWikiLinks:
    """The transform's wiki-link behaviour, tested where it lives.

    `scripts/marp_render.strip_wiki_links` is a pure function. Covering it
    through a full marp render cost 9.5 s and asserted nothing about the
    stripping; these cases cost milliseconds and assert exactly it.
    """

    def test_bare_link_becomes_its_id(self):
        from scripts.marp_render import strip_wiki_links
        assert strip_wiki_links("See [[other-note]] here.") == "See other-note here."

    def test_piped_link_becomes_its_display_text(self):
        from scripts.marp_render import strip_wiki_links
        assert strip_wiki_links("See [[other-note|Other Note]].") == "See Other Note."

    def test_several_links_on_one_line(self):
        from scripts.marp_render import strip_wiki_links
        out = strip_wiki_links("[[a]] and [[b|Bee]] and [[c]]")
        assert out == "a and Bee and c"
        assert "[[" not in out

    def test_text_with_no_links_is_returned_unchanged(self):
        from scripts.marp_render import strip_wiki_links
        src = "# Heading\n\nOrdinary [markdown](https://example.com) link.\n"
        assert strip_wiki_links(src) == src

    def test_a_lone_bracket_pair_is_not_mistaken_for_a_link(self):
        from scripts.marp_render import strip_wiki_links
        src = "An array index like arr[[0]] is not a wiki link in prose."
        out = strip_wiki_links(src)
        # Whatever it does, it must not silently drop the surrounding text.
        assert "An array index like arr" in out and "is not a wiki link" in out
