#!/usr/bin/env python3
"""
DataStore Compression Candidates Scanner

Scans datastore/ for document files that are good candidates for NXPowerLite
compression. Ranks by size and estimates potential savings based on empirical
ratios from past compressions (Battle Card: 87%, Executive Summary: 89%).

Usage:
    python scripts/compression-candidates.py                   # default: datastore/, skip _archive/
    python scripts/compression-candidates.py --path corporate  # scan specific subfolder
    python scripts/compression-candidates.py --include-archive # include _archive/ folders
    python scripts/compression-candidates.py --min-mb 5        # only files >= 5MB
    python scripts/compression-candidates.py --format json     # JSON output
    python scripts/compression-candidates.py --output report.md # write to file

Compression candidate heuristics (NXPowerLite empirical ratios):
    PDF  >= 1.5 MB  : est. 70-85% reduction  (image-heavy PDFs)
    PPTX >= 5.0 MB  : est. 50-70% reduction  (embedded media, uncompressed images)
    DOCX >= 2.0 MB  : est. 30-50% reduction  (embedded images)
    XLSX >= 2.0 MB  : est. 20-40% reduction  (embedded charts/images)

Use the output to batch files into NXPowerLite Desktop's GUI.
"""
import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import get_workspace_root
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, GRAY, BOLD, RESET

# Per-extension size thresholds (MB) and estimated NXPowerLite compression ratio (midpoint).
# "ratio" is the expected RETAINED fraction (i.e., 0.20 means compressed file is ~20% of original,
# so saving is ~80%). Based on Neuxpower published benchmarks + observed 31C datastore results.
PROFILES = {
    ".pdf":  {"threshold_mb": 1.5, "retained_ratio": 0.22, "saving_label": "70-85%"},
    ".pptx": {"threshold_mb": 5.0, "retained_ratio": 0.40, "saving_label": "50-70%"},
    ".docx": {"threshold_mb": 2.0, "retained_ratio": 0.60, "saving_label": "30-50%"},
    ".xlsx": {"threshold_mb": 2.0, "retained_ratio": 0.70, "saving_label": "20-40%"},
}


def human_size(bytes_: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if bytes_ < 1024:
            return f"{bytes_:.1f} {unit}" if unit != "B" else f"{bytes_} B"
        bytes_ /= 1024
    return f"{bytes_:.1f} TB"


def scan(root: Path, include_archive: bool, min_mb: float) -> list[dict]:
    candidates = []
    for ext, profile in PROFILES.items():
        threshold_bytes = max(profile["threshold_mb"], min_mb) * 1024 * 1024
        for f in root.rglob(f"*{ext}"):
            if not include_archive and "_archive" in f.parts:
                continue
            if not f.is_file():
                continue
            size = f.stat().st_size
            if size < threshold_bytes:
                continue
            estimated = int(size * profile["retained_ratio"])
            candidates.append({
                "path": str(f.relative_to(root.parent)),
                "folder": str(f.parent.relative_to(root.parent)),
                "name": f.name,
                "ext": ext,
                "size_bytes": size,
                "est_compressed_bytes": estimated,
                "est_saving_bytes": size - estimated,
                "saving_label": profile["saving_label"],
            })
    candidates.sort(key=lambda c: c["size_bytes"], reverse=True)
    return candidates


def format_text(candidates: list[dict], workspace: Path) -> str:
    if not candidates:
        return f"{GREEN}No compression candidates found.{RESET}"

    total_size = sum(c["size_bytes"] for c in candidates)
    total_saved = sum(c["est_saving_bytes"] for c in candidates)
    avg_saving = (total_saved / total_size * 100) if total_size else 0

    lines = []
    lines.append(f"{BOLD}=== DataStore Compression Candidates ==={RESET}")
    lines.append(f"{GRAY}Workspace: {workspace}{RESET}")
    lines.append("")
    lines.append(f"{CYAN}Found {len(candidates)} candidates{RESET}")
    lines.append(f"  Current size: {BOLD}{human_size(total_size)}{RESET}")
    lines.append(f"  Est. after compression: {BOLD}{human_size(total_size - total_saved)}{RESET}")
    lines.append(f"  Est. savings: {GREEN}{human_size(total_saved)} ({avg_saving:.0f}%){RESET}")
    lines.append("")

    # Group by folder
    by_folder: dict[str, list[dict]] = {}
    for c in candidates:
        by_folder.setdefault(c["folder"], []).append(c)

    for folder, items in by_folder.items():
        lines.append(f"{BOLD}{folder}/{RESET}")
        for c in items:
            size = human_size(c["size_bytes"])
            est = human_size(c["est_compressed_bytes"])
            saved = human_size(c["est_saving_bytes"])
            lines.append(
                f"  {c['name']}\n"
                f"    {GRAY}{size:>10} -> est. {est:>10}  "
                f"(saves ~{saved}, typical {c['saving_label']}){RESET}"
            )
        lines.append("")

    lines.append(f"{BOLD}Workflow:{RESET}")
    lines.append(f"  1. Open NXPowerLite Desktop")
    lines.append(f"  2. Drag the files above into the window (or use File > Add Files)")
    lines.append(f"  3. Click 'Optimize'")
    lines.append(f"  4. Visually verify each result opens correctly")
    lines.append(f"  5. {CYAN}git add datastore/ && git commit -m \"compress: NXPowerLite batch\"{RESET}")
    lines.append("")
    return "\n".join(lines)


def format_markdown(candidates: list[dict], workspace: Path) -> str:
    if not candidates:
        return "# DataStore Compression Candidates\n\nNo candidates found.\n"

    total_size = sum(c["size_bytes"] for c in candidates)
    total_saved = sum(c["est_saving_bytes"] for c in candidates)
    avg_saving = (total_saved / total_size * 100) if total_size else 0

    lines = [
        "# DataStore Compression Candidates",
        "",
        f"**Workspace:** `{workspace}`",
        f"**Scanned:** {len(candidates)} files",
        f"**Current total:** {human_size(total_size)}",
        f"**Est. compressed total:** {human_size(total_size - total_saved)}",
        f"**Est. savings:** {human_size(total_saved)} ({avg_saving:.0f}%)",
        "",
        "| File | Size | Est. Compressed | Est. Savings |",
        "|---|---:|---:|---:|",
    ]
    for c in candidates:
        lines.append(
            f"| `{c['path']}` "
            f"| {human_size(c['size_bytes'])} "
            f"| {human_size(c['est_compressed_bytes'])} "
            f"| {human_size(c['est_saving_bytes'])} ({c['saving_label']}) |"
        )
    lines.append("")
    lines.append("## Workflow")
    lines.append("")
    lines.append("1. Open NXPowerLite Desktop")
    lines.append("2. Drag files above into the window")
    lines.append("3. Click Optimize")
    lines.append("4. Verify each result opens correctly")
    lines.append("5. `git add datastore/ && git commit -m \"compress: NXPowerLite batch\"`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", default="datastore", help="Folder within workspace to scan (default: datastore)")
    parser.add_argument("--include-archive", action="store_true", help="Include _archive/ subfolders")
    parser.add_argument("--min-mb", type=float, default=0.0, help="Additional minimum size floor in MB (per-ext thresholds still apply)")
    parser.add_argument("--format", choices=("text", "markdown", "json"), default="text")
    parser.add_argument("--output", type=Path, help="Write output to file instead of stdout")
    args = parser.parse_args()

    workspace = get_workspace_root()
    scan_root = workspace / args.path
    if not scan_root.exists():
        print(f"{RED}Path not found: {scan_root}{RESET}", file=sys.stderr)
        return 1

    candidates = scan(scan_root, args.include_archive, args.min_mb)

    if args.format == "json":
        output = json.dumps(candidates, indent=2)
    elif args.format == "markdown":
        output = format_markdown(candidates, scan_root)
    else:
        output = format_text(candidates, scan_root)

    if args.output:
        args.output.write_text(output, encoding="utf-8")
        print(f"{GREEN}Report written: {args.output}{RESET}")
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
