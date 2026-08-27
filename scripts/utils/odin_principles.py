"""odin_principles.py - deal-side principle retrieval (R9, CEO-only).

Maps a CRM contact's (relationship_type, pipeline_stage) to a small set of
relationship-domain keywords, then returns the Odin principles whose `keywords`
intersect that set, ranked by match strength. This turns the dormant principle
corpus into a deal-side advisor for /meeting-prep and /deal-strategy.

It mirrors the glob + parse_frontmatter `keyword_map` build inside
odin-brain-health.find_domain_clusters() (which is kebab-case and so NOT
importable) - WITHOUT that function's >=3-principles / >=2-authors cross-author
filter, because a deal needs every matching principle, not just cross-author
clusters. Keys off `keywords` exclusively (Contract 1: the `domains:` field on
some brain files is dead/unvalidated metadata - never read, require, or write
it). No embeddings, no graph, no PageRank (that is R8, out of scope).

Brain absent (any exec workspace), domain set empty (internal contact), or no
match -> returns []. NEVER raises.

Public surface:
  relevant_principles_for(relationship_type, stage=None, *, limit=5, brain_root=None) -> list[dict]
  principles_for_domains(domains, *, limit=5, brain_root=None) -> list[dict]
each item: {"slug", "title", "keywords", "matched_domains", "confidence"}

Consumed by: scripts/odin-principles.py (CLI), and the citation steps in
/meeting-prep and /deal-strategy.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.markdown import parse_frontmatter
from scripts.utils.workspace import get_knowledge_dir

# (relationship_type -> relationship-domain keywords). Every canonical
# relationship_type from config/schemas/crm-contact.schema.json is mapped, so
# nothing falls through by accident. Internal/dormant types map to [] (no
# deal-side citation). Each domain actually carries principles in the brain.
RELATIONSHIP_DOMAINS: dict[str, list[str]] = {
    "prospect": ["negotiation", "persuasion", "sales"],
    "customer": ["partnerships", "negotiation", "sales"],
    "partner": ["partnerships", "channel"],
    "partner-active": ["partnerships", "channel"],
    "reseller": ["partnerships", "channel"],
    "ecosystem": ["partnerships", "channel"],
    "investor-active": ["fundraising", "negotiation", "term-sheet"],
    "investor-passive": ["fundraising", "negotiation", "term-sheet"],
    "shareholder": ["fundraising", "negotiation", "term-sheet"],
    "government": ["communication", "persuasion"],
    "media": ["communication", "persuasion"],
    "advisor": ["communication", "persuasion"],
    "vendor": ["communication", "persuasion"],
    "tribe": [],
    "tribe-leadership": [],
    "inactive": [],
}
# Any relationship_type not in the table above resolves to this fallback.
_DEFAULT_DOMAINS = ["communication", "persuasion"]

# The types that get NO principle citation, ever. Named as a set rather than
# left implicit in "their domain list happens to be empty", because that
# accident is what let a pipeline stage union deal-side domains onto an internal
# contact. Derived from the table so the two cannot drift.
INTERNAL_TYPES = frozenset(
    key for key, domains in RELATIONSHIP_DOMAINS.items() if not domains
)

# (pipeline stage -> additive domains, unioned with the relationship domains).
# Stage strings are case-sensitive, matching crm/config.md / context/pipeline.md.
STAGE_DOMAINS: dict[str, list[str]] = {
    "Negotiation": ["negotiation"],
    "Proposal": ["negotiation", "persuasion"],
    "Demo/POC": ["persuasion", "sales"],
    "Qualified": ["persuasion"],
    "Lead": ["persuasion"],
    "Won": [],
    "Lost": [],
}

_CONF_RANK = {"high": 0, "medium": 1, "low": 2}


def _load_principles(brain_root: Path | None) -> list[dict]:
    """Glob knowledge/odin-brain/principles/*.md and collect {slug,title,keywords,
    confidence}. Mirrors the unfiltered keyword_map build. Never raises."""
    if brain_root is None:
        brain_root = get_knowledge_dir() / "odin-brain"
    pdir = Path(brain_root) / "principles"
    if not pdir.exists():
        return []
    out: list[dict] = []
    for f in sorted(pdir.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # UnicodeDecodeError is NOT an OSError, and a principle file saved
            # in cp1251 is the ordinary way to hit it. "Never raises" has to
            # cover the decode as well as the read.
            continue  # skip an unreadable file; do not fail the whole query
        fm, _body = parse_frontmatter(text)
        # `keywords:` with no value after it is valid YAML and parses to None,
        # so `.get("keywords", [])` returns None, not the default - the key is
        # present. `[str(k) for k in None]` then raises TypeError out of a
        # function whose docstring ends "Never raises", taking down every
        # /odin consult that reads that one principle. A bare scalar
        # (`keywords: strategy`) is already normalised below; a number was not.
        kw = fm.get("keywords") or []
        if not isinstance(kw, (list, tuple, set)):
            kw = [kw]
        out.append({
            "slug": f.stem,
            "title": fm.get("title", f.stem),
            "keywords": [str(k) for k in kw],
            "confidence": fm.get("confidence", ""),
        })
    return out


def principles_for_domains(domains, *, limit: int = 5, brain_root: Path | None = None) -> list[dict]:
    """Return principles whose keywords intersect `domains`, ranked by
    intersection size (desc), then confidence (high>medium>low), then slug."""
    domain_set = set(domains or [])
    if not domain_set:
        return []
    scored: list[tuple[dict, set]] = []
    for p in _load_principles(brain_root):
        matched = set(p["keywords"]) & domain_set
        if matched:
            scored.append((p, matched))
    scored.sort(key=lambda pm: (-len(pm[1]), _CONF_RANK.get(pm[0]["confidence"], 3), pm[0]["slug"]))
    return [{
        "slug": p["slug"],
        "title": p["title"],
        "keywords": p["keywords"],
        "matched_domains": sorted(matched),
        "confidence": p["confidence"],
    } for p, matched in scored[:limit]]


def relevant_principles_for(relationship_type, stage=None, *, limit: int = 5,
                            brain_root: Path | None = None) -> list[dict]:
    """Resolve a contact's (relationship_type, stage) to relevant principles.

    Internal types (tribe/tribe-leadership/inactive) resolve to [] -> no
    citation, WHATEVER stage is passed.

    That last clause is the fix. The suppression was never conditioned on the
    relationship type at all: it was a side effect of the type's domain list
    being empty, and the stage domains were then unioned onto that empty list.
    So `relevant_principles_for("tribe", "Negotiation")` returned deal-side
    citations, contradicting this docstring and the module docstring both.
    Reproduced 2026-08-25. `scripts/odin-principles.py` passes `args.type,
    args.stage` straight through, so nothing upstream prevented the pairing.
    """
    if relationship_type in INTERNAL_TYPES:
        return []
    domains = list(RELATIONSHIP_DOMAINS.get(relationship_type, _DEFAULT_DOMAINS))
    domains += STAGE_DOMAINS.get(stage, [])
    return principles_for_domains(domains, limit=limit, brain_root=brain_root)
