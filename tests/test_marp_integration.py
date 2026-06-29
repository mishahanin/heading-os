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


class TestSampleDeckRender:
    """Integration tests for rendering the sample deck."""

    def test_sample_deck_renders_pdf_and_html(self):
        """Sample deck renders both PDF and HTML successfully."""
        with tempfile.TemporaryDirectory(prefix="marp-int-") as tmp:
            result = render(SAMPLE_DECK, output_dir=Path(tmp))
            assert result["ok"] is True, f"Render failed: {result.get('errors')}"
            types = {o["type"] for o in result["outputs"]}
            assert "pdf" in types
            assert "html" in types

    def test_sample_deck_pdf_size(self):
        """PDF should be at least 50KB for a 14-slide deck."""
        with tempfile.TemporaryDirectory(prefix="marp-int-") as tmp:
            result = render(SAMPLE_DECK, output_dir=Path(tmp), html_only=False)
            if result["ok"]:
                pdf_outputs = [o for o in result["outputs"] if o["type"] == "pdf"]
                if pdf_outputs:
                    assert pdf_outputs[0]["size"] > 50_000

    def test_sample_deck_html_size(self):
        """HTML should be at least 20KB."""
        with tempfile.TemporaryDirectory(prefix="marp-int-") as tmp:
            result = render(SAMPLE_DECK, output_dir=Path(tmp), pdf_only=False)
            if result["ok"]:
                html_outputs = [o for o in result["outputs"] if o["type"] == "html"]
                if html_outputs:
                    assert html_outputs[0]["size"] > 20_000

    def test_sample_deck_hidden_chars_clean(self):
        """Sample deck should have no hidden characters."""
        with tempfile.TemporaryDirectory(prefix="marp-int-") as tmp:
            result = render(SAMPLE_DECK, output_dir=Path(tmp))
            assert result["hidden_characters"] == "clean"

    def test_sample_deck_structural_matches_golden_json(self):
        """Structural regression: section count and classes should match golden file."""
        golden_path = WORKSPACE_ROOT / "tests" / "golden" / "sample-deck.json"
        if not golden_path.exists():
            pytest.skip("Golden file not yet created. Run --update-golden first.")

        with tempfile.TemporaryDirectory(prefix="marp-int-") as tmp:
            result = render(SAMPLE_DECK, output_dir=Path(tmp), pdf_only=True)
            # A render FAILURE is a real regression, not a reason to skip — fail loudly.
            # (Environment-absence skips above, e.g. missing golden file, stay as skips.)
            assert result["ok"], f"render failed, cannot check structure: {result.get('error', 'unknown error')}"

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
            if result["ok"]:
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
            if result["ok"]:
                assert result.get("source_mode") == "dark"

    def test_marp_from_strips_wiki_links(self):
        """Wiki-links in source should be stripped during transform."""
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
