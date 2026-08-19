"""Smoke tests for the four standalone HTML generators after the template move.

These generators emit self-contained single-file HTML with the stylesheet (and,
for the morning dashboard, the base64 fonts) inlined. The failure mode that
motivated these tests is silent: a template that fails to load leaves a complete,
plausible-looking, entirely UNSTYLED document, and nobody notices until the
artifact is in front of a counterpart. So each generator gets one test asserting
its accent colour token and its font declaration survive the render, plus a test
that the loader RAISES rather than degrading when a template is missing.

Loads the kebab-case generators via importlib, the same pattern as
tests/test_capture_payoff.py.

Every test here needs the private DATA overlay, because that is where the brand
templates live. On a data-less clone `get_data_root()` resolves to the bundled
`examples/` demo root, `templates_dir()` lands at `examples/datastore/...` which
does not exist, and 8 of the 9 tests below fail rather than skip — measured
2026-08-20 against CI's own configuration (the `unit + capability tests` job runs
on a runner with no overlay, as its own comment states). So the module is gated
on `data_overlay_present()`, the same guard
tests/security/test_security_constitution_exists.py uses.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE))

from scripts.utils import html_templates  # noqa: E402
from scripts.utils.paths import data_overlay_present  # noqa: E402

pytestmark = pytest.mark.skipif(
    not data_overlay_present(),
    reason="no private data overlay: the generator brand templates are DATA-overlay assets",
)


def _load(script_name, argv=None):
    old_argv = sys.argv
    if argv is not None:
        sys.argv = argv
    try:
        path = WORKSPACE / "scripts" / script_name
        spec = importlib.util.spec_from_file_location(
            f"gen_{script_name.replace('-', '_').removesuffix('.py')}", path
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.argv = old_argv


# ------------------------------------------------------------
# Per-generator smoke: accent token + font declaration present
# ------------------------------------------------------------
def test_dashboard_css_carries_accent_and_font_face():
    """The morning dashboard is the one generator that inlines its own fonts."""
    mod = _load("generate-dashboard.py")

    css = mod.build_css("TEST_LIGHT_B64", "TEST_MEDIUM_B64")
    assert "#5B5FFF" in css, "accent token missing - dashboard rendered unstyled"
    assert css.count("@font-face") == 2, "GT Standard @font-face blocks missing"
    assert "TEST_LIGHT_B64" in css and "TEST_MEDIUM_B64" in css

    # No fonts on disk is a legitimate degrade (Inter fallback), but the
    # stylesheet itself must still be there.
    bare = mod.build_css()
    assert "#5B5FFF" in bare
    assert "@font-face" not in bare


def test_newsletter_css_carries_accent_and_font_stack():
    mod = _load("generate-newsletter-html.py")

    css = mod.build_css()
    assert "#D93D06" in css, "orange accent token missing - newsletter rendered unstyled"
    # The newsletter links its faces from Google Fonts rather than inlining
    # them, so the stylesheet's own proof of life is the font-family stack.
    assert "'Crimson Pro'" in css and "'Bebas Neue'" in css


def test_crm_dashboard_css_carries_accent_and_font_stack():
    mod = _load("generate-crm-dashboard.py")

    css = mod.build_css()
    assert "#5B5FFF" in css, "accent token missing - CRM dashboard rendered unstyled"
    assert "'Inter'" in css


@pytest.mark.parametrize(
    "argv,accent",
    [
        (["generate-partner-enablement.py"], "#5B5FFF"),          # dark (default)
        (["generate-partner-enablement.py", "--light"], "#4A4EE0"),  # light
    ],
)
def test_partner_enablement_html_carries_accent_and_font_stack(argv, accent):
    mod = _load("generate-partner-enablement.py", argv=argv)

    html = mod.build_html("HDR", "PAGE", "BLUE", "BLACK")
    assert f"--accent: {accent};" in html, "theme accent missing - document rendered unstyled"
    assert "fonts.googleapis.com" in html and "'Inter'" in html
    assert "<!DOCTYPE html>" in html and "</html>" in html
    # Every placeholder resolved; none leaked into the shipped document.
    assert "{{" not in html


# ------------------------------------------------------------
# The loader must fail loud, never degrade to unstyled
# ------------------------------------------------------------
def test_missing_template_raises_rather_than_rendering_unstyled():
    with pytest.raises(FileNotFoundError):
        html_templates.load_template("no-such-template.css")


def test_unfilled_placeholder_raises():
    with pytest.raises(KeyError):
        html_templates.render_template("dashboard.css")


def test_unused_value_raises():
    with pytest.raises(KeyError):
        html_templates.render_template("dashboard.css", FONT_FACE="", NOT_A_SLOT="x")


def test_templates_resolve_into_the_data_overlay_not_the_engine():
    """Brand assets live in the DATA overlay; the engine carries only the code."""
    assert WORKSPACE not in html_templates.templates_dir().parents
