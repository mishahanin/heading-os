#!/usr/bin/env python3
"""Generate ODUN.ONE Partner Enablement HTML document with embedded brand assets.

Usage:
    python scripts/generate-partner-enablement.py          # dark theme (default)
    python scripts/generate-partner-enablement.py --light   # light theme

Output:
    outputs/content/partner-enablement/ODUN-ONE-Partner-Enablement-2026.html
    outputs/content/partner-enablement/ODUN-ONE-Partner-Enablement-2026-Light.html
"""

# ============================================================
# Imports
# ============================================================
import base64
import os
import sys
from pathlib import Path

# ============================================================
# Configuration
# ============================================================
# Workspace root
ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT))
from scripts.utils.html_templates import render_template
from scripts.utils.workspace import get_datastore_dir, get_outputs_dir

LIGHT_MODE = "--light" in sys.argv


# ============================================================
# Helpers / Asset Loaders
# ============================================================
def load_logo_b64(filename):
    path = get_datastore_dir() / "brand" / "assets" / "logos" / filename
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


# ============================================================
# Rendering / HTML Document Builder
# ============================================================
def build_html(header_logo_b64, blue_b64, black_b64):
    """Render the partner-enablement document from the locked brand template.

    There is no `page_logo_b64` parameter. There was one, it was never
    referenced, and the caller passed `blue_b64` into it — so anyone wiring a
    genuinely different page logo through that slot got the blue logo on pages
    2 and 3 with no error and no warning. A parameter that silently does
    nothing is worse than an absent one.

    The markup lives in datastore/brand/templates/generators/partner-enablement.html;
    everything below is the theme layer, the only thing that varies between the
    dark (default) and light builds.
    """
    # Pick correct logos and colors based on theme
    if LIGHT_MODE:
        tokens = {
            "P1_LOGO": black_b64,
            "P2_LOGO": blue_b64,
            "P3_LOGO": blue_b64,
            "THEME_VARS": """
    --bg: #FFFFFF;
    --bg-alt: #F5F5FA;
    --text: #1A1A2E;
    --text-secondary: #5A5A78;
    --accent: #4A4EE0;
    --accent-secondary: #E07B00;
    --accent-tertiary: #6B6FA8;
    --card-bg: #F0F0F8;
    --card-bg-alt: #E8E8F2;
    --surface: #F6F6FC;
    --dark-base: #0D0D1A;""",
            "PROOF_STRONG_COLOR": "#1A1A2E",
            "SCREEN_BG": "#e0e0e8",
            "SCREEN_SHADOW": "0 4px 40px rgba(0,0,0,0.12)",
            "FOOTER_BRAND_COLOR": "rgba(0,0,0,0.2)",
            "TABLE_BORDER_COLOR": "rgba(0,0,0,0.06)",
            "TABLE_FIRST_COL_BG": "rgba(0,0,0,0.02)",
            "TABLE_EVEN_BG": "rgba(0,0,0,0.02)",
            "TABLE_US_BG": "rgba(74,78,224,0.06)",
            "TABLE_US_EVEN_BG": "rgba(74,78,224,0.09)",
            "TABLE_US_HEADER_BG": "rgba(74,78,224,0.08)",
            "PILLAR_ICON_BG": "rgba(74,78,224,0.1)",
            "PROOF_GRADIENT": "linear-gradient(135deg, rgba(74,78,224,0.06) 0%, rgba(224,123,0,0.04) 100%)",
            "PROOF_BORDER": "1px solid rgba(74,78,224,0.12)",
            "KILLER_GRADIENT": "linear-gradient(135deg, rgba(74,78,224,0.08) 0%, rgba(224,123,0,0.05) 100%)",
            "KILLER_BORDER": "1.5px solid rgba(74,78,224,0.18)",
            "FOOTER_BG": "var(--card-bg)",
            "FOOTER_BORDER": "1px solid rgba(74,78,224,0.08)",
            "OUTCOME_BG": "rgba(74,78,224,0.06)",
            "IND_ENTRY_COLOR": "#6B6FA8",
            "TRUSTONE_SPAN_COLOR": "#6B6FA8",
        }
    else:
        tokens = {
            "P1_LOGO": header_logo_b64,  # white
            "P2_LOGO": blue_b64,
            "P3_LOGO": blue_b64,
            "THEME_VARS": """
    --bg: #000000;
    --bg-alt: #0A0A14;
    --text: #FFFFFF;
    --text-secondary: #B0B0C0;
    --accent: #5B5FFF;
    --accent-secondary: #FF8C00;
    --accent-tertiary: #8B8FCC;
    --card-bg: #12122A;
    --card-bg-alt: #1A1A35;
    --surface: #F6F6FC;
    --dark-base: #0D0D1A;""",
            "PROOF_STRONG_COLOR": "#fff",
            "SCREEN_BG": "#1a1a1a",
            "SCREEN_SHADOW": "0 4px 40px rgba(0,0,0,0.5)",
            "FOOTER_BRAND_COLOR": "rgba(255,255,255,0.3)",
            "TABLE_BORDER_COLOR": "rgba(255,255,255,0.04)",
            "TABLE_FIRST_COL_BG": "rgba(255,255,255,0.02)",
            "TABLE_EVEN_BG": "rgba(255,255,255,0.015)",
            "TABLE_US_BG": "rgba(91,95,255,0.05)",
            "TABLE_US_EVEN_BG": "rgba(91,95,255,0.07)",
            "TABLE_US_HEADER_BG": "rgba(91,95,255,0.12)",
            "PILLAR_ICON_BG": "rgba(91,95,255,0.15)",
            "PROOF_GRADIENT": "linear-gradient(135deg, rgba(91,95,255,0.08) 0%, rgba(255,140,0,0.06) 100%)",
            "PROOF_BORDER": "1px solid rgba(91,95,255,0.15)",
            "KILLER_GRADIENT": "linear-gradient(135deg, rgba(91,95,255,0.12) 0%, rgba(255,140,0,0.08) 100%)",
            "KILLER_BORDER": "1.5px solid rgba(91,95,255,0.25)",
            "FOOTER_BG": "var(--card-bg)",
            "FOOTER_BORDER": "1px solid rgba(91,95,255,0.1)",
            "OUTCOME_BG": "rgba(91,95,255,0.08)",
            "IND_ENTRY_COLOR": "#8B8FCC",
            "TRUSTONE_SPAN_COLOR": "#8B8FCC",
        }

    return render_template("partner-enablement.html", **tokens)


# ============================================================
# CLI / Main
# ============================================================
def main():
    white_b64 = load_logo_b64("31C_Logo_White_Color.png")
    blue_b64 = load_logo_b64("31C_Logo_Palantinate_Blue_Color.png")
    black_b64 = load_logo_b64("31C_Logo_Black_Color.png")

    html = build_html(white_b64, blue_b64, black_b64)

    outdir = get_outputs_dir() / "content" / "partner-enablement"
    outdir.mkdir(parents=True, exist_ok=True)

    suffix = "-Light" if LIGHT_MODE else ""
    outpath = outdir / f"ODUN-ONE-Partner-Enablement-2026{suffix}.html"
    outpath.write_text(html, encoding="utf-8")
    print(f"HTML written: {outpath} ({len(html):,} bytes)")
    return str(outpath)


if __name__ == "__main__":
    main()
