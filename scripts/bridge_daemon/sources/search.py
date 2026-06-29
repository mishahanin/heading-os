"""Unified search across all bridge daemon data sources.

Phase 1.14: substring match (case-insensitive). Searches:
- Inbox conversation topics
- Tribe contact names + roles
- Tasks descriptions
- Library note titles + keywords
- Studio file paths
- Day calendar event subjects + locations
- Capabilities skill names + descriptions
- Pipeline deals (Phase 1.37: company, country, owner, next_action)
- Investors (Phase 1.37: firm, region, hq, type, notes)

Returns categorized results. No fancy ranking - results within a category
preserve the source's native sort order, capped at per_category_limit.
Phase 2 will add fuzzy match + cross-category ranking.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from .capabilities import list_capabilities
from .calendar import today_agenda
from .inbox import read_inbox
from .investors import list_investors
from .library import list_library
from .pipeline import list_pipeline
from .studio import recent_inflight_items
from .tasks import list_active_tasks
from .tribe import list_tribe

logger = logging.getLogger(__name__)

SEARCH_PER_CATEGORY_LIMIT = 10


def _match(query: str, *fields) -> bool:
    """Case-insensitive substring match against any of the provided fields."""
    if not query:
        return False
    q = query.lower()
    for f in fields:
        if f is None:
            continue
        if q in str(f).lower():
            return True
    return False


def search(workspace_root: Path, query: str, limit: int = SEARCH_PER_CATEGORY_LIMIT) -> dict:
    """Run a unified search against all known sources.

    Returns:
        {
            "query": str,
            "categories": {
                "inbox": [...],
                "tribe": [...],
                "tasks": [...],
                "library": [...],
                "studio": [...],
                "day": [...],
                "capabilities": [...],
            },
            "total": int (sum of hits across categories),
            "data_time": ISO 8601 UTC of when the search ran,
        }
    """
    query = (query or "").strip()
    if not query:
        return {
            "query": "",
            "categories": {},
            "total": 0,
            "data_time": datetime.now(timezone.utc).isoformat(),
        }

    categories: dict = {}

    # --- Inbox ---
    # Broad try/except per source: search is best-effort across many
    # independent sources. If ONE source raises (corrupt file, missing
    # dir, etc.), the others should still return results. "5 of 7
    # categories returned" beats "search broken because of an Inbox bug."
    try:
        inbox = read_inbox(workspace_root)
        # Phase 1.32: inbox is banded - flatten every band into one list.
        rows = [r for band in inbox["bands"].values() for r in band]
        hits = [r for r in rows if _match(query, r.get("subject"))]
        if hits:
            categories["inbox"] = [
                {"subject": r["subject"], "ts": r.get("latest_datetime"),
                 "unread": r.get("band") == "needs-you"}
                for r in hits[:limit]
            ]
    except Exception as exc:
        logger.warning("search: inbox source failed: %s", exc)

    # --- Tribe ---
    try:
        tribe = list_tribe(workspace_root)
        hits = [m for m in tribe["members"] if _match(query, m.get("name"), m.get("role"), m.get("slug"))]
        if hits:
            categories["tribe"] = [
                {"name": m["name"], "slug": m["slug"], "role": m["role"], "last_touch": m.get("last_touch")}
                for m in hits[:limit]
            ]
    except Exception as exc:
        logger.warning("search: tribe source failed: %s", exc)

    # --- Tasks ---
    try:
        tasks = list_active_tasks(workspace_root)
        hits = [t for t in tasks["tasks"] if _match(query, t.get("description"), t.get("kind"), t.get("source"))]
        if hits:
            categories["tasks"] = [
                {"description": t["description"], "priority": t["priority"], "due": t.get("due"), "is_overdue": t.get("is_overdue", False)}
                for t in hits[:limit]
            ]
    except Exception as exc:
        logger.warning("search: tasks source failed: %s", exc)

    # --- Library ---
    try:
        library = list_library(workspace_root)
        hits = []
        for n in library["notes"]:
            kw_str = " ".join(n.get("keywords") or [])
            if _match(query, n.get("title"), kw_str, n.get("type"), n.get("path")):
                hits.append(n)
        if hits:
            categories["library"] = [
                {"title": n["title"], "type": n.get("type", ""), "path": n["path"], "updated": n.get("updated")}
                for n in hits[:limit]
            ]
    except Exception as exc:
        logger.warning("search: library source failed: %s", exc)

    # --- Studio ---
    try:
        studio = recent_inflight_items(workspace_root)
        hits = [it for it in studio["items"] if _match(query, it.get("name"), it.get("path"), it.get("category"))]
        if hits:
            categories["studio"] = [
                {"name": it["name"], "path": it["path"], "category": it["category"], "mtime": it.get("mtime")}
                for it in hits[:limit]
            ]
    except Exception as exc:
        logger.warning("search: studio source failed: %s", exc)

    # --- Day (today's agenda) ---
    try:
        day = today_agenda(workspace_root)
        hits = [e for e in day["events"] if _match(query, e.get("subject"), e.get("location"))]
        if hits:
            categories["day"] = [
                {"time": e["time"], "subject": e["subject"], "location": e.get("location", ""), "is_next": e.get("is_next", False)}
                for e in hits[:limit]
            ]
    except Exception as exc:
        logger.warning("search: day-agenda source failed: %s", exc)

    # --- Capabilities ---
    try:
        caps = list_capabilities(workspace_root)
        hits = [s for s in caps["skills"] if _match(query, s.get("name"), s.get("description"), s.get("slug"))]
        if hits:
            categories["capabilities"] = [
                {"name": s["name"], "description": (s.get("description") or "")[:200], "version": s.get("version", "")}
                for s in hits[:limit]
            ]
    except Exception as exc:
        logger.warning("search: capabilities source failed: %s", exc)

    # --- Pipeline (Phase 1.37) ---
    try:
        pipe = list_pipeline(workspace_root)
        hits = [d for d in pipe["deals"] if _match(query, d.get("company"), d.get("country"), d.get("owner"), d.get("next_action"), d.get("stage"))]
        if hits:
            categories["pipeline"] = [
                {
                    "company": d["company"],
                    "country": d.get("country", ""),
                    "stage": d.get("stage", ""),
                    "value_display": d.get("value_display", ""),
                    "owner": d.get("owner", ""),
                    "due_date": d.get("due_date"),
                    "is_overdue": d.get("is_overdue", False),
                }
                for d in hits[:limit]
            ]
    except Exception as exc:
        logger.warning("search: pipeline source failed: %s", exc)

    # --- Investors (Phase 1.37) ---
    try:
        invs = list_investors(workspace_root)
        hits = [f for f in invs["firms"] if _match(query, f.get("firm"), f.get("region"), f.get("hq"), f.get("type"), f.get("notes"), f.get("fit"))]
        if hits:
            categories["investors"] = [
                {
                    "firm": f["firm"],
                    "region": f.get("region", ""),
                    "type": f.get("type", ""),
                    "hq": f.get("hq", ""),
                    "cheque": f.get("cheque", ""),
                    "fit": f.get("fit", ""),
                    "status": f.get("status", ""),
                    "status_label": f.get("status_label", ""),
                    "sent_date": f.get("sent_date"),
                }
                for f in hits[:limit]
            ]
    except Exception as exc:
        logger.warning("search: investors source failed: %s", exc)

    total = sum(len(v) for v in categories.values())
    return {
        "query": query,
        "categories": categories,
        "total": total,
        "data_time": datetime.now(timezone.utc).isoformat(),
    }
