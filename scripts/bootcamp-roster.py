#!/usr/bin/env python3
"""Build a Bootcamp Tribe roster + track recommendations.

Event-specific values (input/output xlsx names, event folder, sheet title) are
instance DATA: they resolve from the private bootcamp-org-chart config in the
data overlay; the engine ships scripts/bootcamp-org-chart.example.json with
generic placeholders.

Reads:
  - outputs/_sync/gal-<domain>.json (from gal-export.py)
  - the org-chart markdown referenced in ops
  - datastore/events/<event>/<prelim>.xlsx   (event + filename from config)

Writes:
  - datastore/events/<event>/<roster>.xlsx   (event + filename from config)

Logic:
  - Filter out Public DLs and shared/system mailboxes
  - Filter out non-Tribe members (resellers, shareholders) listed in the org-chart config
  - Override GAL title with org chart title where chart is post-restructure (Apr 19)
  - Tag each Tribe member with: Function, Reports To, In prelim?, Tech track?, Ops/Exec track?, Rationale
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.venv import ensure_venv  # noqa: E402

ensure_venv()
from scripts.utils.workspace import get_datastore_dir, get_outputs_dir, get_workspace_root, resolve_config_with_example

WS = get_workspace_root()
GAL_JSON = get_outputs_dir() / "_sync" / "gal-31c.io.json"

# ============================================================
# Exclusion lists + org chart are per-instance DATA (real Tribe names, titles,
# internal mailboxes). They live in the data overlay at
# <data-root>/config/bootcamp-org-chart.json (resolved via get_data_config_dir());
# the engine ships scripts/bootcamp-org-chart.example.json as the generic fallback.
# Schema: chart = email_local -> {title, function, reports_to}.
# ============================================================
_ORG_CHART_FILE = resolve_config_with_example(
    "bootcamp-org-chart.json", WS / "scripts" / "bootcamp-org-chart.example.json"
)
_org_data = json.loads(_ORG_CHART_FILE.read_text(encoding="utf-8"))
SHARED_MAILBOXES = set(_org_data["shared_mailboxes"])
NON_TRIBE = set(_org_data["non_tribe"])  # dict email -> reason; membership uses keys
CHART = _org_data["chart"]
_LEADER_EMAILS = set(_org_data["leader_emails"])
_GAL_ALIASES = _org_data["aliases"]

# Event-specific paths/title are instance DATA resolved from the (private) config;
# the engine example ships generic placeholders.
_EVENT = _org_data.get("event", {})
_EVENT_DIR = _EVENT.get("dir", "Example Bootcamp")
PRELIM_XLSX = get_datastore_dir() / "events" / _EVENT_DIR / _EVENT.get("prelim_xlsx", "prelim.xlsx")
OUT_XLSX = get_datastore_dir() / "events" / _EVENT_DIR / _EVENT.get("out_xlsx", "roster.xlsx")

# ============================================================
# Track recommendation logic
# ============================================================
def recommend_tracks(email_local: str, function: str, title: str) -> tuple[str, str, str]:
    """Return (tech_track, ops_exec_track, rationale)."""
    f = (function or "").lower()
    t = (title or "").lower()

    # CEO + executives that touch both - go to BOTH (data-driven; see _LEADER_EMAILS)
    if email_local in _LEADER_EMAILS:
        return "Y", "Y", "Leadership with technical authority — attend both passes"

    # Pure technical chain (Engineering, AI Lab researchers, DevOps, QA)
    technical_functions = (
        "engineering", "ai lab", "devops", "qa", "core engine",
        "backend", "frontend", "ai engineering",
    )
    if any(k in f for k in technical_functions):
        return "Y", "N", f"Technical IC ({function}) — Tech pass only"

    # Strategy / Marketing / Product / HR / Finance / Legal / Pre-sales = ops/exec only
    ops_functions = (
        "strategy", "marketing", "hr", "finance", "legal",
        "operations", "product", "pre-sales", "customer alignment",
    )
    if any(k in f for k in ops_functions):
        return "N", "Y", f"{function} — Ops/Executive pass only"

    # InfoSec sits between - attend both
    if "infosec" in f or "trustone" in f:
        return "Y", "Y", "InfoSec/TrustONE — both passes (technical + governance)"

    # Default: title heuristics for unknowns
    if any(k in t for k in ("developer", "devloper", "engineer", "devops", "qa", "architect", "researcher", "ml ", "ai ")):
        return "Y", "N", f"Title indicates IC technical role ({title}) — Tech pass"
    if any(k in t for k in ("analyst",)):
        return "N", "Y", f"Business/data analyst ({title}) — Ops/Exec pass"
    if any(k in t for k in ("manager", "director", "officer", "ceo", "cto", "coo", "cfo", "chief", "vp", "head", "lead")):
        return "N", "Y", f"Leadership/management ({title}) — Ops/Exec pass"

    return "?", "?", "Unknown role — needs CEO confirmation"


# ============================================================
# Preliminary list parser
# ============================================================
def load_prelim() -> set[str]:
    """Return set of first-name strings (lowercased) from prelim Excel."""
    try:
        wb = openpyxl.load_workbook(PRELIM_XLSX, data_only=True)
        ws = wb.active
        names = set()
        for row in ws.iter_rows(values_only=True):
            for cell in row:
                if isinstance(cell, str) and cell and cell.lower() not in ("name",):
                    # First-name match, normalize
                    names.add(cell.strip().lower())
        return names
    except Exception as e:
        print(f"[WARN] Could not parse prelim list: {e}")
        return set()


def in_prelim(display_name: str, email_local: str, prelim: set[str]) -> bool:
    """Match prelim short names against full GAL names."""
    if not display_name:
        return False
    first_name = display_name.split()[0].lower()
    last_name = display_name.split()[-1].lower() if len(display_name.split()) > 1 else ""
    # Exact first-name hit
    if first_name in prelim:
        return True
    # Initials + last-name (e.g., "E. Melnikov" matches "Evgeni Melnikov")
    initial_form = f"{first_name[0]}. {last_name}".lower() if last_name else ""
    initial_form_no_space = f"{first_name[0]}.{last_name}".lower() if last_name else ""
    if initial_form in prelim or initial_form_no_space in prelim:
        return True
    # Last name only
    if last_name and last_name in prelim:
        return True
    # Special aliases (data-driven; see _GAL_ALIASES)
    for k, v in _GAL_ALIASES.items():
        if first_name == k and any(a in prelim for a in v):
            return True
    return False


# ============================================================
# Build roster
# ============================================================
def build_roster() -> tuple[list[dict], dict]:
    with GAL_JSON.open(encoding="utf-8") as f:
        gal = json.load(f)
    prelim = load_prelim()

    rows = []
    excluded = {"public_dl": [], "shared_mailbox": [], "non_tribe": []}

    for r in gal:
        email = r.get("email") or ""
        if not email:
            continue
        if r.get("mailbox_type") == "PublicDL":
            excluded["public_dl"].append(email)
            continue
        if email in SHARED_MAILBOXES:
            excluded["shared_mailbox"].append(email)
            continue
        if email in NON_TRIBE:
            excluded["non_tribe"].append(email)
            continue

        local = email.split("@")[0]
        chart_entry = CHART.get(local)
        gal_title = r.get("job_title") or ""
        if chart_entry:
            title = chart_entry["title"]
            function = chart_entry["function"]
            reports_to = chart_entry["reports_to"]
            in_chart = "Y"
        else:
            title = gal_title or "TBD"
            function = "TBD - not in Apr 19 chart"
            reports_to = "TBD"
            in_chart = "N"

        display_name = r.get("display_name") or r.get("name") or ""
        in_prelim_flag = "Y" if in_prelim(display_name, local, prelim) else "N"

        tech, ops, rationale = recommend_tracks(local, function, title)

        rows.append({
            "name": display_name,
            "email": email,
            "title_chart": title,
            "title_gal": gal_title,
            "function": function,
            "reports_to": reports_to,
            "in_chart": in_chart,
            "in_prelim": in_prelim_flag,
            "tech": tech,
            "ops_exec": ops,
            "rationale": rationale,
        })

    rows.sort(key=lambda r: (r["function"], r["name"]))
    return rows, excluded


# ============================================================
# Excel writer
# ============================================================
HEADER_FILL = PatternFill(start_color="1A2332", end_color="1A2332", fill_type="solid")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
TECH_FILL = PatternFill(start_color="E8F4FD", end_color="E8F4FD", fill_type="solid")
OPS_FILL = PatternFill(start_color="FFF4E6", end_color="FFF4E6", fill_type="solid")
BOTH_FILL = PatternFill(start_color="E8F8E8", end_color="E8F8E8", fill_type="solid")
UNKNOWN_FILL = PatternFill(start_color="F8E8E8", end_color="F8E8E8", fill_type="solid")


def row_fill(tech: str, ops: str) -> PatternFill | None:
    if tech == "Y" and ops == "Y":
        return BOTH_FILL
    if tech == "Y":
        return TECH_FILL
    if ops == "Y":
        return OPS_FILL
    if tech == "?" or ops == "?":
        return UNKNOWN_FILL
    return None


def write_excel(rows: list[dict], excluded: dict):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Tribe Roster"

    # Title row
    ws["A1"] = _EVENT.get("title", "Bootcamp - Tribe Roster & Track Recommendations")
    ws["A1"].font = Font(bold=True, size=14)
    ws.merge_cells("A1:K1")
    ws["A2"] = "Tribe Roster & Track Recommendations | Generated 2026-05-01 from Exchange GAL + Apr 19 v3 Org Chart"
    ws["A2"].font = Font(italic=True, color="666666")
    ws.merge_cells("A2:K2")

    headers = [
        "#", "Name", "Email", "Title (reconciled)", "GAL Title (raw)",
        "Function / Department", "Reports To",
        "In Apr 19 Chart?", "In Prelim List?",
        "Attend Tech Track?", "Attend Ops/Exec Track?", "Rationale",
    ]
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=4, column=col, value=h)
        c.fill = HEADER_FILL
        c.font = HEADER_FONT
        c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

    for idx, r in enumerate(rows, 1):
        row_num = idx + 4
        cells = [
            idx, r["name"], r["email"], r["title_chart"], r["title_gal"],
            r["function"], r["reports_to"],
            r["in_chart"], r["in_prelim"],
            r["tech"], r["ops_exec"], r["rationale"],
        ]
        for col, v in enumerate(cells, 1):
            c = ws.cell(row=row_num, column=col, value=v)
            c.alignment = Alignment(vertical="top", wrap_text=True)
        fill = row_fill(r["tech"], r["ops_exec"])
        if fill:
            for col in range(1, len(cells) + 1):
                ws.cell(row=row_num, column=col).fill = fill

    # Column widths
    widths = [4, 24, 32, 38, 30, 30, 22, 8, 8, 8, 8, 50]
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = w

    ws.row_dimensions[4].height = 32
    ws.freeze_panes = "A5"

    # Summary sheet
    ws2 = wb.create_sheet("Summary")
    ws2["A1"] = "Bootcamp Roster Summary"
    ws2["A1"].font = Font(bold=True, size=14)

    tech_count = sum(1 for r in rows if r["tech"] == "Y")
    ops_count = sum(1 for r in rows if r["ops_exec"] == "Y")
    both_count = sum(1 for r in rows if r["tech"] == "Y" and r["ops_exec"] == "Y")
    unknown_count = sum(1 for r in rows if r["tech"] == "?" or r["ops_exec"] == "?")

    rows2 = [
        ("Metric", "Value"),
        ("Total Tribe in GAL (filtered)", len(rows)),
        ("Recommended for Tech track", tech_count),
        ("Recommended for Ops/Exec track", ops_count),
        ("Recommended for BOTH passes", both_count),
        ("Unknown role (needs CEO confirmation)", unknown_count),
        ("In Apr 19 Org Chart", sum(1 for r in rows if r["in_chart"] == "Y")),
        ("Not in Apr 19 Chart (newer/contractors)", sum(1 for r in rows if r["in_chart"] == "N")),
        ("In your preliminary 16-person list", sum(1 for r in rows if r["in_prelim"] == "Y")),
        ("", ""),
        ("Excluded — Public DLs", len(excluded["public_dl"])),
        ("Excluded — Shared/system mailboxes", len(excluded["shared_mailbox"])),
        ("Excluded — Non-Tribe (resellers/shareholders)", len(excluded["non_tribe"])),
    ]
    for i, (k, v) in enumerate(rows2, 3):
        ws2.cell(row=i, column=1, value=k).font = Font(bold=(i == 3))
        ws2.cell(row=i, column=2, value=v)

    ws2.column_dimensions["A"].width = 50
    ws2.column_dimensions["B"].width = 12

    # Excluded detail sheet
    ws3 = wb.create_sheet("Excluded")
    ws3["A1"] = "Excluded entries (for audit)"
    ws3["A1"].font = Font(bold=True, size=14)
    ws3.append([])
    ws3.append(["Reason", "Email"])
    for label, key in [("Public DL", "public_dl"), ("Shared mailbox", "shared_mailbox"), ("Non-Tribe", "non_tribe")]:
        for e in excluded[key]:
            ws3.append([label, e])
    ws3.column_dimensions["A"].width = 22
    ws3.column_dimensions["B"].width = 40

    OUT_XLSX.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT_XLSX)
    print(f"[OK] Wrote {OUT_XLSX}")
    print(f"     Tribe roster: {len(rows)} | Tech: {tech_count} | Ops/Exec: {ops_count} | Both: {both_count} | Unknown: {unknown_count}")


def main():
    rows, excluded = build_roster()
    write_excel(rows, excluded)


if __name__ == "__main__":
    main()
