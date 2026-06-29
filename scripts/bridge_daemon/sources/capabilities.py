"""Real-data source for the /capabilities endpoint.

Scans .claude/skills/*/SKILL.md and returns the workspace skill catalog
with name, description, version. The browser displays as a read-only
list; Phase 2 will add launch buttons + invocation tracking.

Phase 1.11 is browse-only - no skill is invoked from this page.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Locked archive directory (per workspace convention) - skip.
ARCHIVE_NAMES = {"archive"}

# ============================================================
# Skill -> Category map (Phase 1.65, v8 Capabilities reference)
# ============================================================
# Categories mirror the v8 reference grouping. Skills not listed default
# to 'Operations'. Each category maps to a v8 design-token color for the
# section header + chip; the token names are stable in app.css.
CATEGORY_ORDER = ["Intel", "Communication", "Content", "CRM", "Design", "Strategy", "Operations"]

CATEGORY_BY_SLUG = {
    # Intel
    "osint": "Intel", "osint-advanced": "Intel", "competitor-intel": "Intel",
    "market-brief": "Intel", "ceo-intel": "Intel", "intel-briefing-newsletter": "Intel",
    "yt-pulse": "Intel", "x-pulse": "Intel", "notebooklm": "Intel", "docparse": "Intel",
    # Communication
    "email-draft": "Communication", "email-respond": "Communication",
    "email-intel": "Communication", "follow-up": "Communication",
    "ceo-to-ceo": "Communication", "corporate-letter": "Communication",
    "telegram": "Communication", "tribe-message": "Communication",
    "tribe-monday": "Communication", "translate": "Communication",
    # Content
    "linkedin-post": "Content", "linkedin-series": "Content",
    "linkedin-archive": "Content", "keynote-deck": "Content",
    "image-prompt": "Content", "flux-image": "Content",
    # CRM
    "crm": "CRM", "viraid": "CRM", "google-contacts": "CRM",
    # Design
    "design": "Design", "pptx-generator": "Design", "marp": "Design",
    # Strategy
    "deep-think": "Strategy", "council": "Strategy", "deal-strategy": "Strategy",
    "investor-pitch": "Strategy", "investor-update": "Strategy",
    "proposal": "Strategy", "partnership-doc": "Strategy", "official-doc": "Strategy",
    "xpager": "Strategy", "rfp-response": "Strategy", "data-room": "Strategy",
    "voss": "Strategy", "state-check": "Strategy", "meeting-prep": "Strategy",
    "odin": "Strategy",
    # Operations (default; explicit listing keeps slugs greppable)
    "prime": "Operations", "dashboard": "Operations", "weekly-review": "Operations",
    "dream": "Operations", "backup": "Operations", "sync": "Operations",
    "push-updates": "Operations", "publish-corporate": "Operations",
    "create-plan": "Operations", "implement": "Operations", "evaluate": "Operations",
    "scrutinize": "Operations", "workspace-deep-audit": "Operations",
    "calibrate": "Operations", "align": "Operations", "devil": "Operations",
    "burst": "Operations", "validate": "Operations", "sentinel": "Operations",
    "thread": "Operations", "mullvad": "Operations", "playwright": "Operations",
    "setup-browser-cookies": "Operations", "context7": "Operations",
    "skill-creator": "Operations", "request-skill": "Operations",
    "setup-wizard": "Operations", "event-debrief": "Operations",
    "interview-prep": "Operations", "zk": "Operations", "vault": "Operations",
}


def skill_category(slug: str) -> str:
    """Classify a skill by slug. Returns 'Operations' for unmapped slugs."""
    if not slug:
        return "Operations"
    return CATEGORY_BY_SLUG.get(slug, "Operations")

# Frontmatter block delimited by a leading `---` line and the first
# following line that starts with `---`. The closing delimiter may carry
# trailing content on the same line (some generated skills append an HTML
# comment, e.g. `---<!-- AUTO-GENERATED -->`), so match `---` to end-of-line
# rather than requiring it to stand alone.
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---[^\n]*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> dict:
    """Parse the YAML frontmatter block into a dict.

    Uses yaml.safe_load so block scalars (`description: >`), nested maps
    (`metadata:`, `x-31c-capability:`), and quoted values all parse
    correctly. Returns {} when there is no frontmatter or it is malformed
    (the skill still lists with its directory name as a fallback)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _clean(val) -> str:
    """Coerce a frontmatter scalar to a trimmed string ('' for None)."""
    return str(val).strip() if val is not None else ""


def _truncate_description(desc: str, max_chars: int = 240) -> str:
    """Trim the description to first sentence or max_chars, whichever shorter."""
    if not desc:
        return ""
    # First sentence: split on '. ' (preserve abbreviations imperfectly - fine for UI).
    first_sentence = desc.split(". ")[0].rstrip(".")
    if len(first_sentence) <= max_chars:
        return first_sentence
    return desc[:max_chars].rsplit(" ", 1)[0] + "..."


def list_capabilities(workspace_root: Path) -> dict:
    """Scan .claude/skills/ for skill catalogs.

    Returns:
        {
            "skills": [
                {
                    "slug": "osint",
                    "name": "osint",
                    "description": "Deep OSINT intelligence gathering on any target...",
                    "version": "1.2",
                    "author": "Misha Hanin",
                    "capability": {"what": "...", "how": "...", "when": "..."},
                },
                ...
            ] sorted by slug ASC,
            "count": int,
            "data_time": ISO 8601 UTC of the most-recent SKILL.md mtime,
        }
    """
    skills_dir = workspace_root / ".claude" / "skills"
    if not skills_dir.exists():
        return {"skills": [], "count": 0, "data_time": None}

    skills = []
    most_recent_mtime: float = 0.0
    for d in sorted(skills_dir.iterdir()):
        if not d.is_dir():
            continue
        if d.name in ARCHIVE_NAMES:
            continue
        skill_md = d / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
            mtime = skill_md.stat().st_mtime
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        name = _clean(fm.get("name")) or d.name
        description = _clean(fm.get("description"))
        meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
        version = _clean(meta.get("version"))
        author = _clean(meta.get("author"))
        # New: x-31c-capability block (what / how / when) - the plain-language
        # self-explanation rendered on the Capabilities page. Absent on skills
        # not yet annotated; the page falls back to the description.
        cap = fm.get("x-31c-capability") if isinstance(fm.get("x-31c-capability"), dict) else {}
        capability = {
            "what": _clean(cap.get("what")),
            "how": _clean(cap.get("how")),
            "when": _clean(cap.get("when")),
        }
        skills.append({
            "slug": d.name,
            "name": name,
            "description": _truncate_description(description),
            "version": version,
            "author": author,
            "category": skill_category(d.name),
            "capability": capability,
        })
        if mtime > most_recent_mtime:
            most_recent_mtime = mtime

    # Per-category counts for the browser-side section headers.
    category_counts: dict = {}
    for s in skills:
        c = s["category"]
        category_counts[c] = category_counts.get(c, 0) + 1

    data_time = (
        datetime.fromtimestamp(most_recent_mtime, tz=timezone.utc).isoformat()
        if most_recent_mtime else None
    )
    return {
        "skills": skills,
        "count": len(skills),
        "category_counts": category_counts,
        "category_order": CATEGORY_ORDER,
        "data_time": data_time,
    }


# Slug allowlist: lowercase letters, digits, hyphens, optional namespace prefix.
# Examples: "osint", "linkedin-post", "superpowers:brainstorming"
_SKILL_SLUG_RE = re.compile(r"^[a-z][a-z0-9_-]{0,40}(?::[a-z][a-z0-9_-]{0,40})?$")

SKILL_MAX_BYTES = 200_000  # SKILL.md files cap at 500 lines per workspace standard


def read_skill(workspace_root: Path, slug: str) -> dict:
    """Read a single SKILL.md file safely.

    Slug validation:
    - Lowercase alphanumeric + hyphens, optional :namespace prefix
    - Resolved file must be inside .claude/skills/
    - No symlinks
    - Size <= SKILL_MAX_BYTES

    Returns:
        {"ok": True, "slug": str, "content": str, "size": int}
        OR
        {"ok": False, "error": str}
    """
    if not slug or not isinstance(slug, str):
        return {"ok": False, "error": "missing slug"}
    if not _SKILL_SLUG_RE.match(slug):
        return {"ok": False, "error": "invalid slug"}
    # Namespaced slugs (plugin:skill-name) map to nested directories
    # under .claude/skills/{plugin}/skills/{skill-name}/SKILL.md. Phase 1
    # only supports flat workspace skills - reject namespaced for now.
    if ":" in slug:
        return {"ok": False, "error": "namespaced skills not yet supported"}
    skills_root = (workspace_root / ".claude" / "skills").resolve()
    target = (workspace_root / ".claude" / "skills" / slug / "SKILL.md").resolve()
    try:
        target.relative_to(skills_root)
    except ValueError:
        return {"ok": False, "error": "path escapes skills/"}
    if not target.exists():
        return {"ok": False, "error": "not found"}
    try:
        if target.is_symlink():
            return {"ok": False, "error": "symlinks not allowed"}
    except OSError:
        return {"ok": False, "error": "stat failed"}
    if not target.is_file():
        return {"ok": False, "error": "not a file"}
    try:
        size = target.stat().st_size
    except OSError:
        return {"ok": False, "error": "stat failed"}
    if size > SKILL_MAX_BYTES:
        return {"ok": False, "error": f"file too large ({size} bytes, max {SKILL_MAX_BYTES})"}
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"read failed: {e}"}
    return {"ok": True, "slug": slug, "content": content, "size": size}
