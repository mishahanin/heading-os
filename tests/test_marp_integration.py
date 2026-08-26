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
from scripts.utils.paths import DataRootError
from scripts.utils.workspace import get_data_root


def _workspace_dir(rel: str):
    """The first root that actually holds `rel`, or None.

    Both source dirs these tests need (`context/`, `outputs/intel/`) live in the
    private DATA overlay, never in the engine clone. Looking only under
    `WORKSPACE_ROOT` made both tests skip on the operator's own machine, which
    is the only machine where the workspace-aware defaults they cover can be
    exercised at all.
    """
    roots = [WORKSPACE_ROOT]
    try:
        data_root = get_data_root()
    except DataRootError:
        data_root = None
    if data_root is not None and data_root != WORKSPACE_ROOT:
        roots.append(data_root)
    for root in roots:
        candidate = root / rel
        if candidate.is_dir():
            return candidate
    return None

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

        # Compare the RENDER against the golden file. Until 2026-08-23 this
        # loaded the golden file and asserted a property of the golden file --
        # `section_count > 0` -- so any structural regression in the rendered
        # HTML passed as long as the baseline was non-empty, and the golden
        # mechanism could rot invisibly. A golden test that never reads the
        # artefact under test is not a golden test.
        import re

        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        html_outputs = [o for o in sample_deck_result["outputs"] if o["type"] == "html"]
        assert html_outputs, "no HTML output to compare against the golden file"
        html = Path(html_outputs[0]["path"]).read_text(encoding="utf-8")

        rendered = [set(m.group(1).split())
                    for m in re.finditer(r'<section[^>]*\bclass="([^"]*)"', html)]
        assert len(rendered) == golden["section_count"], (
            f"section count drifted: rendered {len(rendered)}, "
            f"golden {golden['section_count']}")

        for expected in golden["sections"]:
            got = rendered[expected["index"]]
            missing = set(expected["classes"]) - got
            assert not missing, (
                f"section {expected['index']} lost class(es) {sorted(missing)}; "
                f"rendered classes were {sorted(got)}")


class TestWorkspaceTransform:
    """Integration tests for /marp from <workspace-path>."""

    def test_marp_from_context_fixture_applies_light_mode(self):
        """Context files should render with light mode default."""
        # Create a temp fixture simulating a context file
        context_dir = _workspace_dir("context")
        if context_dir is None:
            pytest.skip("No context/ directory in either root")

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
        intel_dir = _workspace_dir("outputs/intel")
        if intel_dir is None:
            pytest.skip("No outputs/intel/ directory in either root")

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
