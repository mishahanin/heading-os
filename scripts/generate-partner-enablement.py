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
def build_html(header_logo_b64, page_logo_b64, blue_b64, black_b64):
    # Pick correct logos and colors based on theme
    if LIGHT_MODE:
        p1_logo = black_b64
        p2_logo = blue_b64
        p3_logo = blue_b64
    else:
        p1_logo = header_logo_b64  # white
        p2_logo = blue_b64
        p3_logo = blue_b64

    # Theme-specific CSS overrides
    if LIGHT_MODE:
        theme_vars = """
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
    --dark-base: #0D0D1A;"""
        proof_strong_color = "#1A1A2E"
        screen_bg = "#e0e0e8"
        screen_shadow = "0 4px 40px rgba(0,0,0,0.12)"
        footer_brand_color = "rgba(0,0,0,0.2)"
        table_border_color = "rgba(0,0,0,0.06)"
        table_first_col_bg = "rgba(0,0,0,0.02)"
        table_even_bg = "rgba(0,0,0,0.02)"
        table_us_bg = "rgba(74,78,224,0.06)"
        table_us_even_bg = "rgba(74,78,224,0.09)"
        table_us_header_bg = "rgba(74,78,224,0.08)"
        pillar_icon_bg = "rgba(74,78,224,0.1)"
        proof_gradient = "linear-gradient(135deg, rgba(74,78,224,0.06) 0%, rgba(224,123,0,0.04) 100%)"
        proof_border = "1px solid rgba(74,78,224,0.12)"
        killer_gradient = "linear-gradient(135deg, rgba(74,78,224,0.08) 0%, rgba(224,123,0,0.05) 100%)"
        killer_border = "1.5px solid rgba(74,78,224,0.18)"
        footer_bg = "var(--card-bg)"
        footer_border = "1px solid rgba(74,78,224,0.08)"
        outcome_bg = "rgba(74,78,224,0.06)"
        ind_entry_color = "#6B6FA8"
        trustone_span_color = "#6B6FA8"
    else:
        theme_vars = """
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
    --dark-base: #0D0D1A;"""
        proof_strong_color = "#fff"
        screen_bg = "#1a1a1a"
        screen_shadow = "0 4px 40px rgba(0,0,0,0.5)"
        footer_brand_color = "rgba(255,255,255,0.3)"
        table_border_color = "rgba(255,255,255,0.04)"
        table_first_col_bg = "rgba(255,255,255,0.02)"
        table_even_bg = "rgba(255,255,255,0.015)"
        table_us_bg = "rgba(91,95,255,0.05)"
        table_us_even_bg = "rgba(91,95,255,0.07)"
        table_us_header_bg = "rgba(91,95,255,0.12)"
        pillar_icon_bg = "rgba(91,95,255,0.15)"
        proof_gradient = "linear-gradient(135deg, rgba(91,95,255,0.08) 0%, rgba(255,140,0,0.06) 100%)"
        proof_border = "1px solid rgba(91,95,255,0.15)"
        killer_gradient = "linear-gradient(135deg, rgba(91,95,255,0.12) 0%, rgba(255,140,0,0.08) 100%)"
        killer_border = "1.5px solid rgba(91,95,255,0.25)"
        footer_bg = "var(--card-bg)"
        footer_border = "1px solid rgba(91,95,255,0.1)"
        outcome_bg = "rgba(91,95,255,0.08)"
        ind_entry_color = "#8B8FCC"
        trustone_span_color = "#8B8FCC"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ODUN.ONE Partner Enablement - 31 Concept</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
  @page {{
    size: A4;
    margin: 0;
  }}

  *, *::before, *::after {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }}

  :root {{{theme_vars}
  }}

  html, body {{
    width: 210mm;
    margin: 0 auto;
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 10pt;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}

  .page {{
    width: 210mm;
    height: 297mm;
    position: relative;
    overflow: hidden;
    page-break-after: always;
    background: var(--bg);
  }}

  .page:last-child {{
    page-break-after: auto;
  }}

  /* ===== PAGE 1 - THE HOOK ===== */
  .page-1 {{
    display: flex;
    flex-direction: column;
    padding: 0;
  }}

  .p1-header {{
    padding: 36px 44px 0 44px;
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
  }}

  .p1-logo {{
    height: 28px;
    opacity: 0.95;
  }}

  .p1-badge {{
    font-size: 7.5pt;
    color: var(--accent);
    letter-spacing: 2.5px;
    text-transform: uppercase;
    font-weight: 600;
    border: 1px solid rgba(91,95,255,0.3);
    padding: 5px 14px;
    border-radius: 4px;
  }}

  .p1-hero {{
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    padding: 0 44px;
    margin-top: -20px;
  }}

  .p1-tagline {{
    font-size: 8pt;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: var(--accent-secondary);
    font-weight: 600;
    margin-bottom: 16px;
  }}

  .p1-headline {{
    font-size: 28pt;
    font-weight: 800;
    line-height: 1.15;
    color: var(--text);
    margin-bottom: 10px;
    max-width: 85%;
  }}

  .p1-headline .accent {{
    color: var(--accent);
  }}

  .p1-subheadline {{
    font-size: 11.5pt;
    color: var(--text-secondary);
    line-height: 1.55;
    max-width: 82%;
    margin-bottom: 36px;
    font-weight: 400;
  }}

  .p1-pillars {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px 20px;
    margin-bottom: 32px;
    max-width: 95%;
  }}

  .pillar {{
    display: flex;
    align-items: flex-start;
    gap: 12px;
    background: var(--card-bg);
    border-radius: 8px;
    padding: 14px 16px;
    border-left: 3px solid var(--accent);
  }}

  .pillar-icon {{
    width: 22px;
    height: 22px;
    min-width: 22px;
    background: {pillar_icon_bg};
    border-radius: 5px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    margin-top: 1px;
  }}

  .pillar-text {{
    font-size: 8.8pt;
    line-height: 1.45;
    color: var(--text);
    font-weight: 500;
  }}

  .pillar-text span {{
    color: var(--text-secondary);
    font-weight: 400;
    font-size: 8pt;
  }}

  .p1-proof {{
    display: flex;
    align-items: center;
    gap: 20px;
    background: {proof_gradient};
    border: {proof_border};
    border-radius: 8px;
    padding: 16px 20px;
    max-width: 95%;
  }}

  .proof-stat {{
    font-size: 26pt;
    font-weight: 800;
    color: var(--accent);
    line-height: 1;
    min-width: 70px;
  }}

  .proof-text {{
    font-size: 8.5pt;
    color: var(--text-secondary);
    line-height: 1.45;
  }}

  .p1-fifth-pillar {{
    grid-column: 1 / -1;
  }}

  /* Footer CTA */
  .page-footer {{
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    padding: 14px 44px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: {footer_bg};
    border-top: {footer_border};
  }}

  .footer-cta {{
    font-size: 7.5pt;
    color: var(--text-secondary);
    font-style: italic;
  }}

  .footer-brand {{
    font-size: 7.5pt;
    color: {footer_brand_color};
    letter-spacing: 1.5px;
  }}

  .footer-squares {{
    display: flex;
    gap: 4px;
  }}

  .sq-blue {{
    width: 8px;
    height: 8px;
    background: var(--accent);
    border-radius: 1px;
  }}

  .sq-orange {{
    width: 8px;
    height: 8px;
    background: var(--accent-secondary);
    border-radius: 1px;
  }}


  /* ===== PAGE 2 - BATTLE CARD ===== */
  .page-2 {{
    padding: 32px 36px 0 36px;
    display: flex;
    flex-direction: column;
  }}

  .p2-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
  }}

  .p2-title {{
    font-size: 16pt;
    font-weight: 700;
    color: var(--text);
  }}

  .p2-logo {{
    height: 20px;
    opacity: 0.4;
  }}

  .p2-subtitle {{
    font-size: 8.5pt;
    color: var(--text-secondary);
    margin-bottom: 18px;
    line-height: 1.4;
  }}

  /* Comparison Table */
  .comp-table {{
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    font-size: 7.8pt;
    margin-bottom: 22px;
    border-radius: 8px;
    overflow: hidden;
  }}

  .comp-table thead th {{
    background: var(--card-bg);
    padding: 12px 10px;
    text-align: center;
    font-weight: 700;
    color: var(--text);
    border-bottom: 2px solid var(--accent);
    font-size: 7.5pt;
  }}

  .comp-table thead th:first-child {{
    text-align: left;
    width: 22%;
    color: var(--text-secondary);
    font-weight: 600;
    border-bottom-color: rgba(91,95,255,0.3);
  }}

  .comp-table thead th.us {{
    background: {table_us_header_bg};
    color: var(--accent);
    border-bottom-color: var(--accent);
  }}

  .comp-table tbody td {{
    padding: 11px 10px;
    text-align: center;
    border-bottom: 1px solid {table_border_color};
    color: var(--text-secondary);
    vertical-align: middle;
    line-height: 1.4;
  }}

  .comp-table tbody td:first-child {{
    text-align: left;
    font-weight: 600;
    color: var(--text);
    background: {table_first_col_bg};
  }}

  .comp-table tbody td.us {{
    background: {table_us_bg};
    color: var(--text);
    font-weight: 500;
  }}

  .comp-table tbody tr:nth-child(even) td {{
    background: {table_even_bg};
  }}

  .comp-table tbody tr:nth-child(even) td.us {{
    background: {table_us_even_bg};
  }}

  .check {{ color: #22C55E; font-weight: 700; }}
  .partial {{ color: var(--accent-secondary); }}
  .miss {{ color: #EF4444; }}

  /* Say This Not That */
  .stnt-section {{
    margin-bottom: 16px;
  }}

  .stnt-title {{
    font-size: 10pt;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
  }}

  .stnt-title::before {{
    content: '';
    width: 3px;
    height: 16px;
    background: var(--accent-secondary);
    border-radius: 2px;
  }}

  .stnt-card {{
    background: var(--card-bg);
    border-radius: 8px;
    padding: 14px 18px;
    margin-bottom: 10px;
    border-left: 3px solid transparent;
  }}

  .stnt-if {{
    font-size: 8pt;
    color: var(--text-secondary);
    margin-bottom: 8px;
    font-style: italic;
  }}

  .stnt-say {{
    font-size: 8.5pt;
    color: var(--text);
    font-weight: 500;
    line-height: 1.5;
    padding-left: 12px;
    border-left: 2px solid var(--accent);
  }}

  /* Killer Differentiator */
  .killer-box {{
    background: {killer_gradient};
    border: {killer_border};
    border-radius: 10px;
    padding: 22px 24px;
    margin-top: 24px;
    margin-bottom: 50px;
  }}

  .killer-label {{
    font-size: 7pt;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--accent-secondary);
    font-weight: 700;
    margin-bottom: 8px;
  }}

  .killer-text {{
    font-size: 9.5pt;
    font-weight: 600;
    line-height: 1.5;
    color: var(--text);
  }}


  /* ===== PAGE 3 - THE TARGETS ===== */
  .page-3 {{
    padding: 32px 36px 0 36px;
    display: flex;
    flex-direction: column;
  }}

  .p3-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
  }}

  .p3-title {{
    font-size: 16pt;
    font-weight: 700;
    color: var(--text);
  }}

  .p3-subtitle {{
    font-size: 8.5pt;
    color: var(--text-secondary);
    margin-bottom: 16px;
  }}

  .industry-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    margin-bottom: 12px;
  }}

  .ind-card {{
    background: var(--card-bg);
    border-radius: 10px;
    padding: 18px 20px;
    border-top: 3px solid var(--accent);
    display: flex;
    flex-direction: column;
  }}

  .ind-card.orange {{ border-top-color: var(--accent-secondary); }}
  .ind-card.purple {{ border-top-color: var(--accent-tertiary); }}
  .ind-card.green {{ border-top-color: #22C55E; }}
  .ind-card.cyan {{ border-top-color: #06B6D4; }}

  .ind-icon {{
    font-size: 16px;
    margin-bottom: 6px;
  }}

  .ind-name {{
    font-size: 9.5pt;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 6px;
  }}

  .ind-pain {{
    font-size: 7.8pt;
    color: var(--accent-secondary);
    font-weight: 600;
    margin-bottom: 5px;
    line-height: 1.35;
  }}

  .ind-usecase {{
    font-size: 7.8pt;
    color: var(--text-secondary);
    line-height: 1.4;
    margin-bottom: 6px;
    flex: 1;
  }}

  .ind-outcome {{
    font-size: 7.8pt;
    color: var(--text);
    font-weight: 600;
    background: {outcome_bg};
    padding: 6px 10px;
    border-radius: 5px;
    line-height: 1.35;
    margin-bottom: 6px;
  }}

  .ind-entry {{
    font-size: 7pt;
    color: {ind_entry_color};
    font-style: italic;
  }}

  /* ===== SPECS BAR ===== */
  .specs-bar {{
    display: flex;
    justify-content: space-between;
    gap: 10px;
    margin-top: auto;
    margin-bottom: 52px;
  }}

  .spec-item {{
    flex: 1;
    text-align: center;
    background: var(--card-bg);
    border-radius: 8px;
    padding: 12px 8px;
  }}

  .spec-value {{
    font-size: 14pt;
    font-weight: 800;
    color: var(--accent);
    line-height: 1.2;
  }}

  .spec-label {{
    font-size: 6.8pt;
    color: var(--text-secondary);
    margin-top: 3px;
    line-height: 1.3;
  }}

  /* ===== PRINT ===== */
  @media print {{
    html, body {{
      width: 210mm;
      margin: 0;
    }}
    .page {{
      margin: 0;
      box-shadow: none;
    }}
  }}

  /* Screen preview */
  @media screen {{
    body {{
      padding: 20px 0;
      background: {screen_bg};
    }}
    .page {{
      margin: 0 auto 30px auto;
      box-shadow: {screen_shadow};
    }}
  }}
</style>
</head>
<body>

<!-- ============================================================ -->
<!-- PAGE 1 - THE HOOK: Solution Overview & Executive Pitch        -->
<!-- ============================================================ -->
<div class="page page-1">
  <div class="p1-header">
    <img src="data:image/png;base64,{p1_logo}" alt="31C" class="p1-logo">
    <div class="p1-badge">PARTNER ENABLEMENT</div>
  </div>

  <div class="p1-hero">
    <div class="p1-tagline">From Deep Packet Inspection to Deep Packet Intelligence</div>

    <h1 class="p1-headline">
      Deep Packet Intelligence.<br>
      <span class="accent">Sovereign by Design.</span>
    </h1>

    <p class="p1-subheadline">
      Networks generate billions of data points every second. Most organizations can only inspect them.
      ODUN.ONE transforms raw packet data into actionable intelligence - giving operators, governments,
      and enterprises the visibility, control, and sovereignty they need in an encrypted, AI-driven world.
    </p>

    <div class="p1-pillars">
      <div class="pillar">
        <div class="pillar-icon">&#x1F512;</div>
        <div class="pillar-text">
          Encrypted Traffic Visibility<br>
          <span>98% classification accuracy across 3,500+ applications - even in fully encrypted environments</span>
        </div>
      </div>

      <div class="pillar">
        <div class="pillar-icon">&#x1F3DB;</div>
        <div class="pillar-text">
          Complete Data Sovereignty<br>
          <span>Air-gapped, on-premises deployment. No foreign cloud. No external data flows. Non-aligned.</span>
        </div>
      </div>

      <div class="pillar">
        <div class="pillar-icon">&#x1F916;</div>
        <div class="pillar-text">
          AI-Native Analytics<br>
          <span>Natural language queries, predictive insights, and automated policy - not bolt-on ML</span>
        </div>
      </div>

      <div class="pillar">
        <div class="pillar-icon">&#x1F4C8;</div>
        <div class="pillar-text">
          Revenue Acceleration<br>
          <span>Application-aware billing, QoS optimization, churn reduction, and new monetization models</span>
        </div>
      </div>

      <div class="pillar p1-fifth-pillar">
        <div class="pillar-icon">&#x26A1;</div>
        <div class="pillar-text">
          Carrier-Grade on Commodity Hardware<br>
          <span>Up to 1.2 Tbps passive monitoring or 500 Gbps inline on standard x86 servers. No proprietary appliances.</span>
        </div>
      </div>
    </div>

    <div class="p1-proof">
      <div class="proof-stat">$78B</div>
      <div class="proof-text">
        The global DPI market is projected to reach <strong style="color:{proof_strong_color}">$78 billion by 2030</strong> at 22% CAGR.
        The exit of legacy vendors from 56 countries has created an immediate window -
        and no sovereign, AI-native alternative existed until now.
      </div>
    </div>
  </div>

  <div class="page-footer">
    <div class="footer-cta">See ODUN.ONE in action - ask us to arrange a 30-minute live demonstration with the 31C engineering team.</div>
    <div class="footer-squares">
      <div class="sq-blue"></div>
      <div class="sq-orange"></div>
    </div>
    <div class="footer-brand">31C.IO</div>
  </div>
</div>


<!-- ============================================================ -->
<!-- PAGE 2 - THE BATTLE CARD: Competitive Differentiation         -->
<!-- ============================================================ -->
<div class="page page-2">
  <div class="p2-header">
    <h2 class="p2-title">Competitive Positioning</h2>
    <img src="data:image/png;base64,{p2_logo}" alt="31C" class="p2-logo">
  </div>

  <p class="p2-subtitle">How ODUN.ONE compares to the alternatives your prospect is likely evaluating.</p>

  <table class="comp-table">
    <thead>
      <tr>
        <th>Capability</th>
        <th class="us">ODUN.ONE</th>
        <th>Russian-Origin<br>Vendors</th>
        <th>Bundled Core<br>Network DPI</th>
        <th>Legacy Pivoting<br>Vendors</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>Geopolitical Alignment</td>
        <td class="us"><span class="check">&#x2713;</span> Non-aligned</td>
        <td><span class="miss">&#x2717;</span> State-linked</td>
        <td><span class="miss">&#x2717;</span> State-linked</td>
        <td><span class="partial">~</span> Western-centric</td>
      </tr>
      <tr>
        <td>AI / ML Integration</td>
        <td class="us"><span class="check">&#x2713;</span> Native, built-in</td>
        <td><span class="partial">~</span> Bolt-on</td>
        <td><span class="miss">&#x2717;</span> Basic / none</td>
        <td><span class="partial">~</span> Legacy add-on</td>
      </tr>
      <tr>
        <td>Encrypted Traffic Analysis</td>
        <td class="us"><span class="check">&#x2713;</span> 98% accuracy</td>
        <td><span class="partial">~</span> Moderate</td>
        <td><span class="miss">&#x2717;</span> ~82% on QUIC</td>
        <td><span class="partial">~</span> Moderate</td>
      </tr>
      <tr>
        <td>Data Sovereignty</td>
        <td class="us"><span class="check">&#x2713;</span> Full air-gap capable</td>
        <td><span class="miss">&#x2717;</span> Foreign regulatory obligations</td>
        <td><span class="miss">&#x2717;</span> Ecosystem lock-in</td>
        <td><span class="miss">&#x2717;</span> Pivoting to cloud SaaS</td>
      </tr>
      <tr>
        <td>Hardware Requirements</td>
        <td class="us"><span class="check">&#x2713;</span> Standard x86</td>
        <td><span class="partial">~</span> Mixed</td>
        <td><span class="miss">&#x2717;</span> Proprietary stack</td>
        <td><span class="miss">&#x2717;</span> Proprietary appliances</td>
      </tr>
      <tr>
        <td>Vendor Lock-in Risk</td>
        <td class="us"><span class="check">&#x2713;</span> None (open APIs)</td>
        <td><span class="partial">~</span> Low-Medium</td>
        <td><span class="miss">&#x2717;</span> Extreme</td>
        <td><span class="partial">~</span> Medium</td>
      </tr>
      <tr>
        <td>Long-Term DPI Commitment</td>
        <td class="us"><span class="check">&#x2713;</span> Core mission</td>
        <td><span class="check">&#x2713;</span> Core</td>
        <td><span class="partial">~</span> Bundled add-on</td>
        <td><span class="miss">&#x2717;</span> Exiting DPI</td>
      </tr>
    </tbody>
  </table>

  <div class="stnt-section">
    <div class="stnt-title">Conversation Pivots</div>

    <div class="stnt-card">
      <div class="stnt-if">If they say: "We already get DPI bundled with our core network vendor."</div>
      <div class="stnt-say">"A bundled DPI module gives you basic traffic management. ODUN.ONE gives you intelligence - the difference between seeing what is on your network and understanding what it means for your business. And it works with any core vendor, so you are never locked in."</div>
    </div>

    <div class="stnt-card">
      <div class="stnt-if">If they say: "We are looking at a more affordable option from an Eastern European vendor."</div>
      <div class="stnt-say">"Cost matters, but so does where your network intelligence data ultimately flows. ODUN.ONE is sovereignty-first - your data stays yours, with no architectural obligations to foreign regulatory frameworks. And our total cost of ownership on commodity x86 hardware is highly competitive."</div>
    </div>

    <div class="stnt-card">
      <div class="stnt-if">If they say: "Our current vendor says their next release will cover this."</div>
      <div class="stnt-say">"Their public roadmap shows a strategic pivot toward cloud-based security services and away from on-premises DPI. We are the only vendor doubling down on sovereign, AI-native deep packet intelligence. A 30-minute demo will show you the difference."</div>
    </div>
  </div>

  <div class="killer-box">
    <div class="killer-label">The Line to Remember</div>
    <div class="killer-text">
      ODUN.ONE is the only sovereign, AI-native Deep Packet Intelligence platform built from the ground up - not bolted onto a legacy architecture, not bundled as an afterthought, and not aligned to any government. Your data. Your infrastructure. Your intelligence.
    </div>
  </div>

  <div class="page-footer">
    <div class="footer-cta">Need competitive positioning for a specific deal? We provide tailored battle cards for active opportunities.</div>
    <div class="footer-squares">
      <div class="sq-blue"></div>
      <div class="sq-orange"></div>
    </div>
    <div class="footer-brand">31C.IO</div>
  </div>
</div>


<!-- ============================================================ -->
<!-- PAGE 3 - THE TARGETS: Industry Use Cases & Client Profiles    -->
<!-- ============================================================ -->
<div class="page page-3">
  <div class="p3-header">
    <h2 class="p3-title">Who to Approach - and How</h2>
    <img src="data:image/png;base64,{p3_logo}" alt="31C" class="p3-logo" style="height:20px;opacity:0.4;">
  </div>

  <p class="p3-subtitle">Each card is a self-contained mini-pitch. Find the prospect's industry and lead with their pain point.</p>

  <div class="industry-grid">
    <div class="ind-card">
      <div class="ind-icon">&#x1F4E1;</div>
      <div class="ind-name">Telecom Operators</div>
      <div class="ind-pain">#1 Pain: Losing visibility as 90%+ of traffic encrypts</div>
      <div class="ind-usecase">Real-time encrypted traffic classification and subscriber-level intelligence across mobile and fixed networks - enabling QoS packs, monetization models, and churn prevention.</div>
      <div class="ind-outcome">Application-aware billing, 15-20% churn reduction, new revenue streams from traffic intelligence</div>
      <div class="ind-entry">Best entry: VP/SVP Network Operations or CTO</div>
    </div>

    <div class="ind-card orange">
      <div class="ind-icon">&#x1F3DB;&#xFE0F;</div>
      <div class="ind-name">Government & National Security</div>
      <div class="ind-pain">#1 Pain: Foreign vendor dependency creates sovereign risk</div>
      <div class="ind-usecase">Fully air-gapped deployment for national traffic intelligence, regulatory compliance, and critical infrastructure protection - with zero external data flows.</div>
      <div class="ind-outcome">Complete sovereignty over national network intelligence infrastructure</div>
      <div class="ind-entry">Best entry: Director of Digital Transformation, escalate to Minister/Secretary</div>
    </div>

    <div class="ind-card purple">
      <div class="ind-icon">&#x1F3E6;</div>
      <div class="ind-name">Banking & Financial Services</div>
      <div class="ind-pain">#1 Pain: Rising cyber threats and regulatory compliance pressure</div>
      <div class="ind-usecase">Real-time anomaly detection and automated compliance monitoring across branch networks, trading floors, and digital banking channels.</div>
      <div class="ind-outcome">Faster incident response, automated compliance reporting, reduced audit preparation costs</div>
      <div class="ind-entry">Best entry: CISO, escalate to CIO or Head of Risk</div>
    </div>

    <div class="ind-card green">
      <div class="ind-icon">&#x1F6F0;&#xFE0F;</div>
      <div class="ind-name">Satellite & Defense</div>
      <div class="ind-pain">#1 Pain: Encrypted traffic surging over bandwidth-constrained links</div>
      <div class="ind-usecase">Bandwidth optimization and encrypted traffic analysis for satellite-connected military, maritime, aviation, and remote operations.</div>
      <div class="ind-outcome">3-5x bandwidth efficiency gains on congested satellite links</div>
      <div class="ind-entry">Best entry: VP Engineering or Program Director</div>
    </div>

    <div class="ind-card cyan" style="grid-column: 1 / -1;">
      <div style="display:flex;gap:16px;align-items:flex-start;">
        <div>
          <div class="ind-icon">&#x1F9E0;</div>
        </div>
        <div style="flex:1;">
          <div class="ind-name">Enterprise AI Security <span style="font-weight:400;font-size:7.5pt;color:{trustone_span_color};"> - powered by TrustONE</span></div>
          <div class="ind-pain">#1 Pain: Sensitive data leaking through unmonitored LLM and AI interactions</div>
          <div class="ind-usecase" style="margin-bottom:8px;">TrustONE is a sovereign AI gateway that protects enterprise data across 300+ LLM platforms. Context-aware ML detection identifies PII, proprietary code, and trade secrets in real time - not keyword blocking, but intelligent content understanding. Deploys on-premises with zero data leaving the perimeter.</div>
          <div style="display:flex;gap:12px;">
            <div class="ind-outcome" style="flex:1;">Full visibility into enterprise AI usage with real-time DLP enforcement</div>
            <div class="ind-entry" style="align-self:center;">Best entry: CISO or Head of AI Governance</div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div class="specs-bar">
    <div class="spec-item">
      <div class="spec-value">1.2 Tbps</div>
      <div class="spec-label">Passive monitoring<br>per server</div>
    </div>
    <div class="spec-item">
      <div class="spec-value">3,500+</div>
      <div class="spec-label">Applications<br>classified</div>
    </div>
    <div class="spec-item">
      <div class="spec-value">5</div>
      <div class="spec-label">Modular platform<br>components</div>
    </div>
    <div class="spec-item">
      <div class="spec-value">x86</div>
      <div class="spec-label">Standard hardware<br>no appliances</div>
    </div>
    <div class="spec-item">
      <div class="spec-value">100%</div>
      <div class="spec-label">Sovereign<br>deployment</div>
    </div>
  </div>

  <div class="page-footer">
    <div class="footer-cta">Have a prospect in mind? Contact us to schedule a customized demo aligned to their industry and use case.</div>
    <div class="footer-squares">
      <div class="sq-blue"></div>
      <div class="sq-orange"></div>
    </div>
    <div class="footer-brand">31C.IO</div>
  </div>
</div>

</body>
</html>"""


# ============================================================
# CLI / Main
# ============================================================
def main():
    white_b64 = load_logo_b64("31C_Logo_White_Color.png")
    blue_b64 = load_logo_b64("31C_Logo_Palantinate_Blue_Color.png")
    black_b64 = load_logo_b64("31C_Logo_Black_Color.png")

    html = build_html(white_b64, blue_b64, blue_b64, black_b64)

    outdir = get_outputs_dir() / "content" / "partner-enablement"
    outdir.mkdir(parents=True, exist_ok=True)

    suffix = "-Light" if LIGHT_MODE else ""
    outpath = outdir / f"ODUN-ONE-Partner-Enablement-2026{suffix}.html"
    outpath.write_text(html, encoding="utf-8")
    print(f"HTML written: {outpath} ({len(html):,} bytes)")
    return str(outpath)


if __name__ == "__main__":
    main()
