#!/usr/bin/env python3
"""Odin Brain Health - validate brain integrity and regenerate INDEX.md.

Usage:
    python scripts/odin-brain-health.py                  # Full health report
    python scripts/odin-brain-health.py --update-index   # Regenerate INDEX.md
    python scripts/odin-brain-health.py --validate       # Validate all brain files (exit 1 if issues)
    python scripts/odin-brain-health.py --stats          # Quick stats only
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.markdown import parse_frontmatter as _parse_frontmatter_text
from scripts.utils.workspace import get_knowledge_dir

# R11 temporal-validity lint. Imported defensively: odin_brain_lint is ceo-only
# and not synced to exec workspaces, so on an exec this import is absent and the
# compile report simply omits the temporal_validity section.
try:
    from scripts.odin_brain_lint import lint as _temporal_lint
except Exception:  # pragma: no cover - exec workspace without the ceo-only lint
    _temporal_lint = None

# ============================================================
# Configuration
# ============================================================
# Resolve under the DATA root (get_knowledge_dir -> get_data_root), so this works
# from the engine clone against the data sibling. Not a fixed engine-relative path.
BRAIN_ROOT = get_knowledge_dir() / "odin-brain"
SUBDIRS = ["sources", "principles", "positions", "episodes", "conflicts", "reference"]
# Temporal-validity fields are OPTIONAL (not in REQUIRED_FIELDS):
#   superseded_by   -- slug of the note that supersedes this one (never deleted)
#   superseded_date -- ISO date the supersession occurred
#   valid_until     -- ISO date a time-bound stance expires (alt. to superseded_by)
# Validated by scripts/odin_brain_lint.py; see .claude/skills/odin/references/temporal-validity.md.
REQUIRED_FIELDS = {
    "source": ["id", "title", "type", "format", "author", "ingested", "confidence", "keywords"],
    "principle": ["id", "title", "type", "sources", "confidence", "keywords", "created"],
    "position": ["id", "title", "type", "principles", "sources", "confidence", "keywords", "created", "revisit_when"],
    "episode": ["id", "title", "type", "date", "keywords", "created"],
    "conflict": ["id", "title", "type", "side_a", "side_b", "status", "created"],
    "reference": ["id", "title", "type", "keywords"],
}


# ============================================================
# Brain Loading
# ============================================================
def parse_frontmatter(filepath):
    """Extract YAML frontmatter from a markdown file. Returns dict or None.

    Thin wrapper around ``scripts.utils.markdown.parse_frontmatter`` that
    takes a filepath (legacy call shape) and returns ``None`` instead of
    ``{}`` when no frontmatter is present, since downstream code uses
    ``if fm is None`` truthiness checks.

    Uses yaml.safe_load (via the shared util) so both inline lists
    (``principles: ["id1", "id2"]``) and block lists (``principles:\\n  -
    "id1"\\n  - "id2"``) parse correctly.
    """
    try:
        text = filepath.read_text(encoding="utf-8")
    except OSError:
        return None
    data, _ = _parse_frontmatter_text(text)
    if not data:
        return None
    return data


def collect_brain_files():
    """Collect all .md files from brain subdirectories."""
    files = {"sources": [], "principles": [], "positions": [], "episodes": [], "conflicts": [], "reference": []}
    for subdir in SUBDIRS:
        dirpath = BRAIN_ROOT / subdir
        if dirpath.exists():
            files[subdir] = sorted(
                [f for f in dirpath.glob("*.md")],
                key=lambda f: f.name,
                reverse=True,
            )
    return files


# ============================================================
# Health Checks
# ============================================================
def validate_file(filepath, expected_type):
    """Validate a single brain file. Returns list of issues."""
    issues = []
    fm = parse_frontmatter(filepath)
    if fm is None:
        issues.append(f"  MISSING frontmatter: {filepath.name}")
        return issues

    file_type = fm.get("type", "")
    if file_type != expected_type:
        issues.append(f"  WRONG type: {filepath.name} has type='{file_type}', expected '{expected_type}'")

    required = REQUIRED_FIELDS.get(expected_type, [])
    for field in required:
        if field not in fm:
            issues.append(f"  MISSING field '{field}': {filepath.name}")

    if not fm.get("id", ""):
        issues.append(f"  EMPTY id: {filepath.name}")
    if not fm.get("title", ""):
        issues.append(f"  EMPTY title: {filepath.name}")

    return issues


# ============================================================
# Cross-Reference Checks
# ============================================================
def collect_domains(files):
    """Aggregate keyword stats across all brain files."""
    domains = defaultdict(lambda: {"sources": 0, "principles": 0, "positions": 0})
    for subdir in ["sources", "principles", "positions"]:
        for f in files[subdir]:
            fm = parse_frontmatter(f)
            if not fm:
                continue
            keywords = fm.get("keywords", [])
            if isinstance(keywords, str):
                keywords = [keywords]
            for kw in keywords:
                domains[kw][subdir] += 1
    return dict(sorted(domains.items(), key=lambda x: sum(x[1].values()), reverse=True))


def count_open_conflicts(files):
    """Count conflicts with status=open."""
    count = 0
    for f in files["conflicts"]:
        fm = parse_frontmatter(f)
        if fm and fm.get("status") == "open":
            count += 1
    return count


def find_orphan_principles(files):
    """Find principles not referenced by any position.

    Position files reference their constituent principles in two interchangeable
    forms: by timestamp id (e.g. "20260421100100") or by filename slug
    (e.g. "specific-knowledge-cannot-be-trained"). A principle counts as
    referenced if EITHER form appears in any position's `principles:` array.
    """
    position_principles = set()
    for f in files["positions"]:
        fm = parse_frontmatter(f)
        if fm:
            refs = fm.get("principles", [])
            if isinstance(refs, str):
                refs = [refs]
            position_principles.update(refs)

    orphans = []
    for f in files["principles"]:
        fm = parse_frontmatter(f)
        if not fm:
            continue
        pid = fm.get("id", "")
        slug = f.stem
        rel_path = f"principles/{f.name}"
        in_position = (pid in position_principles) or (slug in position_principles)

        if not in_position:
            orphans.append({
                "file": rel_path,
                "title": fm.get("title", f.stem),
                "keywords": fm.get("keywords", []),
                "sources": fm.get("sources", []),
            })
    return orphans


def find_domain_clusters(files):
    """Group principles by keyword and count distinct authors via source lookup."""
    keyword_map = defaultdict(list)
    for f in files["principles"]:
        fm = parse_frontmatter(f)
        if not fm:
            continue
        keywords = fm.get("keywords", [])
        if isinstance(keywords, str):
            keywords = [keywords]
        source_ids = fm.get("sources", [])
        if isinstance(source_ids, str):
            source_ids = [source_ids]
        for kw in keywords:
            keyword_map[kw].append({
                "file": f"principles/{f.name}",
                "title": fm.get("title", f.stem),
                "source_ids": source_ids,
            })

    source_authors = {}
    for f in files["sources"]:
        fm = parse_frontmatter(f)
        if fm:
            source_authors[fm.get("id", "")] = fm.get("author", "Unknown")

    clusters = []
    for keyword, principles in keyword_map.items():
        if len(principles) < 3:
            continue
        all_source_ids = set()
        for p in principles:
            all_source_ids.update(p["source_ids"])
        authors = set()
        for sid in all_source_ids:
            if sid in source_authors:
                authors.add(source_authors[sid])
        if len(authors) >= 2:
            clusters.append({
                "keyword": keyword,
                "principle_count": len(principles),
                "principles": [p["title"] for p in principles],
                "source_count": len(all_source_ids),
                "author_count": len(authors),
                "authors": sorted(authors),
            })
    return sorted(clusters, key=lambda x: x["principle_count"], reverse=True)


def find_keyword_overlaps(files):
    """Find sources that share 2+ keywords with principles but aren't wiki-linked."""
    source_keywords = {}
    for f in files["sources"]:
        fm = parse_frontmatter(f)
        if fm:
            kws = fm.get("keywords", [])
            if isinstance(kws, str):
                kws = [kws]
            source_keywords[f"sources/{f.name}"] = {
                "id": fm.get("id", ""),
                "keywords": set(k.lower() for k in kws),
            }

    principle_data = {}
    for f in files["principles"]:
        fm = parse_frontmatter(f)
        if fm:
            kws = fm.get("keywords", [])
            if isinstance(kws, str):
                kws = [kws]
            linked_sources = fm.get("sources", [])
            if isinstance(linked_sources, str):
                linked_sources = [linked_sources]
            principle_data[f"principles/{f.name}"] = {
                "keywords": set(k.lower() for k in kws),
                "linked_sources": set(linked_sources),
            }

    overlaps = []
    for src_path, src_info in source_keywords.items():
        for pri_path, pri_info in principle_data.items():
            shared = src_info["keywords"] & pri_info["keywords"]
            if len(shared) >= 2 and src_info["id"] not in pri_info["linked_sources"]:
                overlaps.append({
                    "source_file": src_path,
                    "principle_file": pri_path,
                    "shared_keywords": sorted(shared),
                    "overlap_count": len(shared),
                })
    return sorted(overlaps, key=lambda x: x["overlap_count"], reverse=True)


def find_stale_seeds(files, stale_days=7):
    """Find brain files with status=seed older than stale_days."""
    stale = []
    today = datetime.now().date()
    for subdir in ["sources", "principles", "positions", "reference"]:
        for f in files[subdir]:
            fm = parse_frontmatter(f)
            if not fm:
                continue
            if fm.get("status") != "seed" or not fm.get("created"):
                continue
            try:
                created = datetime.strptime(str(fm["created"]), "%Y-%m-%d").date()
                age = (today - created).days
                if age > stale_days:
                    stale.append({
                        "file": f"{subdir}/{f.name}",
                        "title": fm.get("title", f.stem),
                        "created": fm["created"],
                        "age_days": age,
                    })
            except (ValueError, TypeError):
                pass
    return stale


def collect_all_keywords(files):
    """Collect keyword frequency across all brain files."""
    freq = defaultdict(int)
    for subdir in ["sources", "principles", "positions", "reference"]:
        for f in files[subdir]:
            fm = parse_frontmatter(f)
            if fm:
                kws = fm.get("keywords", [])
                if isinstance(kws, str):
                    kws = [kws]
                for k in kws:
                    freq[k.lower()] += 1
    return dict(sorted(freq.items(), key=lambda x: x[1], reverse=True))


def find_stale_positions(files):
    """Find positions whose revisit_when condition might be met."""
    stale = []
    for f in files["positions"]:
        fm = parse_frontmatter(f)
        if fm:
            revisit = fm.get("revisit_when", "")
            if revisit:
                stale.append({
                    "file": f"positions/{f.name}",
                    "title": fm.get("title", f.stem),
                    "revisit_when": revisit,
                })
    return stale


# ============================================================
# Report Generation
# ============================================================
def run_compile(files):
    """Run full compile analysis and output JSON report."""
    report = {
        "brain_stats": {
            "sources": len(files["sources"]),
            "principles": len(files["principles"]),
            "positions": len(files["positions"]),
            "episodes": len(files["episodes"]),
            "conflicts": len(files["conflicts"]),
            "reference": len(files["reference"]),
            "open_conflicts": count_open_conflicts(files),
        },
        "orphan_principles": find_orphan_principles(files),
        "domain_clusters": find_domain_clusters(files),
        "keyword_overlaps": find_keyword_overlaps(files),
        "stale_seeds": find_stale_seeds(files),
        "stale_positions": find_stale_positions(files),
        "keyword_frequency": collect_all_keywords(files),
    }

    # R11: temporal-validity lint (superseded_by). Omitted when the ceo-only
    # lint module is absent (exec workspace) so compile never crashes there.
    if _temporal_lint is not None:
        issues = _temporal_lint(BRAIN_ROOT)
        report["temporal_validity"] = {
            "total_issues": len(issues),
            "errors": [i for i in issues if i.get("severity") == "error"],
            "warnings": [i for i in issues if i.get("severity") == "warn"],
        }

    # default=str: frontmatter dates (YAML parses `created:`/`updated:` to
    # datetime.date) reach the report via find_stale_seeds; stringify them so
    # the JSON report serialises. (Pre-existing latent bug surfaced by R11.)
    print(json.dumps(report, indent=2, default=str))


def generate_index(files):
    """Generate INDEX.md content."""
    src_count = len(files["sources"])
    pri_count = len(files["principles"])
    pos_count = len(files["positions"])
    ep_count = len(files["episodes"])
    con_count = len(files["conflicts"])
    open_con = count_open_conflicts(files)
    domains = collect_domains(files)
    today = datetime.now().strftime("%Y-%m-%d")

    lines = [
        "# Odin's Brain",
        "",
        f"Last updated: {today}",
        f"Sources: {src_count} | Principles: {pri_count} | Positions: {pos_count} | Episodes: {ep_count} | Conflicts: {con_count} ({open_con} open)",
        "",
        "## Domains",
    ]

    if not domains:
        lines.append("")
        lines.append("*No domains yet. Feed Odin his first source with `/odin learn [url or file]`.*")
    else:
        lines.append("")
        for domain, counts in domains.items():
            parts = []
            if counts["sources"]:
                parts.append(f"{counts['sources']} sources")
            if counts["principles"]:
                parts.append(f"{counts['principles']} principles")
            if counts["positions"]:
                parts.append(f"{counts['positions']} positions")
            lines.append(f"- {domain}: {', '.join(parts)}")

    lines.append("")
    lines.append("## Recent")
    lines.append("")

    recent = []
    for f in files["sources"][:5]:
        fm = parse_frontmatter(f)
        if fm:
            date = fm.get("ingested", fm.get("created", "unknown"))
            recent.append((date, f"Learned: {fm.get('title', f.stem)}"))
    for f in files["positions"][:3]:
        fm = parse_frontmatter(f)
        if fm:
            date = fm.get("created", "unknown")
            recent.append((date, f"Position formed: {fm.get('title', f.stem)}"))
    for f in files["episodes"][:5]:
        fm = parse_frontmatter(f)
        if fm:
            date = fm.get("date", fm.get("created", "unknown"))
            recent.append((date, f"Logged: {fm.get('title', f.stem)}"))
    for f in files["conflicts"][:3]:
        fm = parse_frontmatter(f)
        if fm:
            date = fm.get("created", "unknown")
            status = fm.get("status", "open")
            recent.append((date, f"Conflict {status}: {fm.get('title', f.stem)}"))

    recent.sort(key=lambda x: x[0], reverse=True)
    if not recent:
        lines.append("*No activity yet.*")
    else:
        for date, desc in recent[:10]:
            lines.append(f"- [{date}] {desc}")

    lines.append("")
    return "\n".join(lines)


def run_health_report(files):
    """Print a full health report."""
    print("=== Odin Brain Health Report ===\n")

    print(f"Sources:    {len(files['sources'])}")
    print(f"Principles: {len(files['principles'])}")
    print(f"Positions:  {len(files['positions'])}")
    print(f"Episodes:   {len(files['episodes'])}")
    print(f"Conflicts:  {len(files['conflicts'])} ({count_open_conflicts(files)} open)")
    print(f"Reference:  {len(files['reference'])}")
    print()

    all_issues = []
    type_map = {"sources": "source", "principles": "principle", "positions": "position", "episodes": "episode", "conflicts": "conflict", "reference": "reference"}
    for subdir in SUBDIRS:
        for f in files[subdir]:
            issues = validate_file(f, type_map[subdir])
            all_issues.extend(issues)

    if all_issues:
        print(f"Validation Issues ({len(all_issues)}):")
        for issue in all_issues:
            print(issue)
    else:
        print("Validation: all files clean.")

    position_principles = set()
    for f in files["positions"]:
        fm = parse_frontmatter(f)
        if fm:
            refs = fm.get("principles", [])
            if isinstance(refs, str):
                refs = [refs]
            position_principles.update(refs)

    orphan_principles = []
    for f in files["principles"]:
        fm = parse_frontmatter(f)
        if not fm:
            continue
        pid = fm.get("id", "")
        slug = f.stem
        if pid not in position_principles and slug not in position_principles:
            orphan_principles.append(fm.get("title", f.stem))

    if orphan_principles:
        print(f"\nOrphan Principles (not in any position): {len(orphan_principles)}")
        for title in orphan_principles[:10]:
            print(f"  - {title}")

    open_conflicts = []
    for f in files["conflicts"]:
        fm = parse_frontmatter(f)
        if fm and fm.get("status") == "open":
            open_conflicts.append(fm.get("title", f.stem))

    if open_conflicts:
        print(f"\nOpen Conflicts: {len(open_conflicts)}")
        for title in open_conflicts:
            print(f"  - {title}")

    print("\n=== End Report ===")
    return len(all_issues)


# ============================================================
# Main / CLI
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Odin Brain Health - validate and index Odin's knowledge brain")
    parser.add_argument("--update-index", action="store_true", help="Regenerate INDEX.md")
    parser.add_argument("--validate", action="store_true", help="Validate all brain files (exit 1 if issues)")
    parser.add_argument("--stats", action="store_true", help="Quick stats only")
    parser.add_argument("--compile", action="store_true",
                        help="Run full compile analysis, output JSON for LLM semantic layer")
    args = parser.parse_args()

    if not BRAIN_ROOT.exists():
        print(f"Brain root not found: {BRAIN_ROOT}", file=sys.stderr)
        sys.exit(1)

    files = collect_brain_files()

    if args.stats:
        print(f"Sources: {len(files['sources'])}, Principles: {len(files['principles'])}, "
              f"Positions: {len(files['positions'])}, Episodes: {len(files['episodes'])}, "
              f"Conflicts: {len(files['conflicts'])} "
              f"({count_open_conflicts(files)} open), Reference: {len(files['reference'])}")
        return

    if args.compile:
        run_compile(files)
        return

    if args.update_index:
        index_content = generate_index(files)
        index_path = BRAIN_ROOT / "INDEX.md"
        index_path.write_text(index_content, encoding="utf-8")
        print(f"INDEX.md regenerated: {index_path}")
        return

    if args.validate:
        type_map = {"sources": "source", "principles": "principle", "positions": "position", "episodes": "episode", "conflicts": "conflict", "reference": "reference"}
        issue_count = 0
        for subdir in SUBDIRS:
            for f in files[subdir]:
                issues = validate_file(f, type_map[subdir])
                for issue in issues:
                    print(issue, file=sys.stderr)
                issue_count += len(issues)
        if issue_count:
            print(f"FAIL: {issue_count} issue(s) found", file=sys.stderr)
            sys.exit(1)
        else:
            print("PASS: all brain files valid")
        return

    issue_count = run_health_report(files)
    if issue_count:
        sys.exit(1)


if __name__ == "__main__":
    main()
