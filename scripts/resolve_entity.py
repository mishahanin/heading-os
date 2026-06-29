#!/usr/bin/env python3
"""resolve-entity.py - Entity-resolution helper for /osint Phase 0.5.

Takes a target string + mode, runs 2-3 targeted web searches via
scripts/utils/search.py (Tavily primary, Brave fallback), and uses
an Anthropic Haiku/Sonnet tool-use call to extract a structured plan
to stdout as JSON.

Usage:
    python scripts/resolve-entity.py "ExampleTelco" --mode auto
    python scripts/resolve-entity.py "Peter Steinberger" --mode person --depth quick
    python scripts/resolve-entity.py "GCC telecom" --mode market --output pretty
    python scripts/resolve-entity.py "Deep Packet Inspection" --mode technology --model sonnet

Output: JSON object with canonical, social, people/competitors/etc fields per mode,
plus resolution_status (deterministic from canonical fill rate), backend_used,
field_sources map, and search_queries_used.

Errors: emits structured error JSON, exits non-zero.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.api import load_api_key
from scripts.utils.observability import observe
from scripts.utils.search import search_with_fallback, NoBackendsConfigured, SearchBackendError
from scripts.utils.workspace import (
    get_crm_contacts_dir,
    get_personal_context_dir,
    get_workspace_root,
)


MODELS = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
}


def _slugify(name: str) -> str:
    """Lowercase, hyphenate, drop non-alphanumeric. Mirrors crm/contacts/ naming."""
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def detect_mode(target: str) -> tuple[str, str]:
    """Return (mode, reason) using CRM/people/pipeline cross-reference, then heuristic.

    Order:
      1. crm/contacts/{slug}.md exists -> person
      2. context/people.md mentions target -> person
      3. context/pipeline.md mentions target -> company
      4. Heuristic: ends with corporate suffix -> company; "market"/region -> market;
         multi-word capitalised -> person; else technology
    """
    slug = _slugify(target)

    contacts_dir = get_crm_contacts_dir()
    if contacts_dir.exists() and (contacts_dir / f"{slug}.md").exists():
        return "person", "matched crm/contacts/"

    people_file = get_personal_context_dir() / "people.md"
    if people_file.exists():
        try:
            text = people_file.read_text(encoding="utf-8", errors="replace").lower()
            if target.lower() in text:
                return "person", "matched context/people.md"
        except OSError:
            pass

    pipeline_file = get_personal_context_dir() / "pipeline.md"
    if pipeline_file.exists():
        try:
            text = pipeline_file.read_text(encoding="utf-8", errors="replace").lower()
            if target.lower() in text:
                return "company", "matched context/pipeline.md"
        except OSError:
            pass

    suffixes = ("inc", "ltd", "corp", "corporation", "group", "co", "llc", "gmbh", "ag", "sa", "plc")
    parts = target.lower().split()
    if parts and parts[-1] in suffixes:
        return "company", "heuristic: corporate suffix"

    region_words = {"market", "telecom", "region", "africa", "asia", "europe", "middle east", "gcc", "cis"}
    if any(w in target.lower() for w in region_words):
        return "market", "heuristic: market/region keyword"

    if len(target.split()) >= 2 and target[0].isupper():
        return "person", "heuristic: multi-word capitalised"

    return "technology", "heuristic: fallback"


CANONICAL_FIELDS = {
    "company": ["name", "aliases", "parent", "subsidiaries", "ticker", "founded_year", "hq_country"],
    "person": ["name", "aliases", "current_role", "current_org", "previous_orgs"],
    "market": ["name", "region", "sector", "key_terms"],
    "technology": ["name", "aliases", "category", "standards_bodies"],
}


def build_queries(target: str, mode: str, depth: str) -> list[str]:
    """Build 2 (quick) or 3 (standard) targeted search queries per mode."""
    base = [target]
    if mode == "company":
        base = [
            f"{target} canonical name parent company subsidiaries",
            f"{target} CEO leadership Twitter X handle LinkedIn",
        ]
        if depth == "standard":
            base.append(f"{target} ticker stock exchange founded headquarters")
    elif mode == "person":
        base = [
            f"{target} current role company affiliation biography",
            f"{target} Twitter X handle GitHub LinkedIn personal site",
        ]
        if depth == "standard":
            base.append(f"{target} previous companies roles career history")
    elif mode == "market":
        base = [
            f"{target} key players operators vendors regulators",
            f"{target} market overview definition scope",
        ]
        if depth == "standard":
            base.append(f"{target} industry associations standards bodies")
    else:  # technology
        base = [
            f"{target} technology overview category standards",
            f"{target} key vendors implementations communities",
        ]
        if depth == "standard":
            base.append(f"{target} GitHub Reddit Hacker News discussion")
    return base


def build_tool_schema(mode: str) -> dict:
    """Build the Anthropic tool input_schema for a given mode."""
    if mode == "company":
        properties = {
            "canonical": {"type": "object", "properties": {
                "name": {"type": "string"},
                "aliases": {"type": "array", "items": {"type": "string"}},
                "parent": {"type": ["string", "null"]},
                "subsidiaries": {"type": "array", "items": {"type": "string"}},
                "ticker": {"type": ["string", "null"]},
                "founded_year": {"type": ["integer", "null"]},
                "hq_country": {"type": ["string", "null"]},
            }, "required": ["name", "aliases", "parent", "subsidiaries", "ticker", "founded_year", "hq_country"]},
            "people": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"},
                "role": {"type": "string"},
                "x_handle": {"type": ["string", "null"]},
                "linkedin_url": {"type": ["string", "null"]},
            }, "required": ["name", "role", "x_handle", "linkedin_url"]}},
            "social": {"type": "object", "properties": {
                "x_handle": {"type": ["string", "null"]},
                "linkedin_url": {"type": ["string", "null"]},
                "github_org": {"type": ["string", "null"]},
                "website": {"type": ["string", "null"]},
            }, "required": ["x_handle", "linkedin_url", "github_org", "website"]},
            "products": {"type": "array", "items": {"type": "string"}},
            "competitors": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"},
                "x_handle": {"type": ["string", "null"]},
            }, "required": ["name", "x_handle"]}},
            "regulators": {"type": "array", "items": {"type": "string"}},
            "field_sources": {"type": "object", "additionalProperties": {"type": "integer"}},
        }
        required = ["canonical", "people", "social", "products", "competitors", "regulators", "field_sources"]
    elif mode == "person":
        properties = {
            "canonical": {"type": "object", "properties": {
                "name": {"type": "string"},
                "aliases": {"type": "array", "items": {"type": "string"}},
                "current_role": {"type": ["string", "null"]},
                "current_org": {"type": ["string", "null"]},
                "previous_orgs": {"type": "array", "items": {"type": "string"}},
            }, "required": ["name", "aliases", "current_role", "current_org", "previous_orgs"]},
            "social": {"type": "object", "properties": {
                "x_handle": {"type": ["string", "null"]},
                "linkedin_url": {"type": ["string", "null"]},
                "github_username": {"type": ["string", "null"]},
                "personal_website": {"type": ["string", "null"]},
            }, "required": ["x_handle", "linkedin_url", "github_username", "personal_website"]},
            "affiliations": {"type": "array", "items": {"type": "object", "properties": {
                "organization": {"type": "string"},
                "role": {"type": "string"},
                "active": {"type": "boolean"},
            }, "required": ["organization", "role", "active"]}},
            "field_sources": {"type": "object", "additionalProperties": {"type": "integer"}},
        }
        required = ["canonical", "social", "affiliations", "field_sources"]
    elif mode == "market":
        properties = {
            "canonical": {"type": "object", "properties": {
                "name": {"type": "string"},
                "region": {"type": ["string", "null"]},
                "sector": {"type": ["string", "null"]},
                "key_terms": {"type": "array", "items": {"type": "string"}},
            }, "required": ["name", "region", "sector", "key_terms"]},
            "key_players": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"},
                "type": {"type": "string"},
                "x_handle": {"type": ["string", "null"]},
            }, "required": ["name", "type", "x_handle"]}},
            "regulators": {"type": "array", "items": {"type": "string"}},
            "industry_associations": {"type": "array", "items": {"type": "string"}},
            "field_sources": {"type": "object", "additionalProperties": {"type": "integer"}},
        }
        required = ["canonical", "key_players", "regulators", "industry_associations", "field_sources"]
    else:  # technology
        properties = {
            "canonical": {"type": "object", "properties": {
                "name": {"type": "string"},
                "aliases": {"type": "array", "items": {"type": "string"}},
                "category": {"type": ["string", "null"]},
                "standards_bodies": {"type": "array", "items": {"type": "string"}},
            }, "required": ["name", "aliases", "category", "standards_bodies"]},
            "vendors": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"},
                "x_handle": {"type": ["string", "null"]},
                "github_org": {"type": ["string", "null"]},
            }, "required": ["name", "x_handle", "github_org"]}},
            "communities": {"type": "array", "items": {"type": "object", "properties": {
                "platform": {"type": "string"},
                "identifier": {"type": "string"},
            }, "required": ["platform", "identifier"]}},
            "field_sources": {"type": "object", "additionalProperties": {"type": "integer"}},
        }
        required = ["canonical", "vendors", "communities", "field_sources"]

    return {"type": "object", "properties": properties, "required": required}


def compute_resolution_status(canonical: dict, mode: str) -> str:
    """resolution_status from canonical-block fill rate. >=80% high, 30-80% partial, <30% low."""
    fields = CANONICAL_FIELDS[mode]
    populated = 0
    for f in fields:
        v = canonical.get(f)
        if v is None:
            continue
        if isinstance(v, list) and len(v) == 0:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        populated += 1
    rate = populated / len(fields) if fields else 0
    if rate >= 0.8:
        return "high"
    if rate >= 0.3:
        return "partial"
    return "low"


@observe()
def call_anthropic(target: str, mode: str, search_results: list[dict], model_key: str) -> dict:
    """Call Anthropic with tool_use to extract structured plan. Returns the tool_use input."""
    import anthropic

    api_key = load_api_key("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = (
        "You are an entity-resolution helper. Extract structured facts about the target "
        "from the supplied search results into the resolve_entity tool. "
        "Populate every field strictly from the search results - never invent. "
        "Use null for fields with no supporting evidence. Empty arrays for lists with no items. "
        "For field_sources, map each populated leaf field path (dot-notation, e.g., 'social.x_handle') "
        "to the integer index of the source it came from in the search results list (0-based)."
    )

    sources_text = ""
    for i, r in enumerate(search_results):
        sources_text += (
            f"\n[{i}] {r.get('title', '')}\n"
            f"    URL: {r.get('url', '')}\n"
            f"    Content: {(r.get('content', '') or '')[:1000]}\n"
        )

    user_prompt = f"Target: {target}\nMode: {mode}\n\nSearch results:{sources_text}\n\nCall resolve_entity with the extracted plan."

    tool = {
        "name": "resolve_entity",
        "description": f"Return the resolved entity plan for the {mode} target.",
        "input_schema": build_tool_schema(mode),
    }

    response = client.messages.create(
        model=MODELS[model_key],
        max_tokens=2000,
        system=[{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_prompt}],
        tools=[tool],
        tool_choice={"type": "tool", "name": "resolve_entity"},
    )

    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    raise RuntimeError("Anthropic response contained no tool_use block")


def main() -> int:
    parser = argparse.ArgumentParser(description="Entity-resolution helper for /osint Phase 0.5")
    parser.add_argument("target", help="Target string (company, person, market, technology)")
    parser.add_argument("--mode", default="auto",
                        choices=["auto", "company", "person", "market", "technology"])
    parser.add_argument("--output", default="json", choices=["json", "pretty"])
    parser.add_argument("--depth", default="standard", choices=["quick", "standard"])
    parser.add_argument("--model", default="haiku", choices=["haiku", "sonnet"])
    args = parser.parse_args()

    mode_reason = ""
    mode = args.mode
    if mode == "auto":
        mode, mode_reason = detect_mode(args.target)

    queries = build_queries(args.target, mode, args.depth)

    aggregated: list[dict] = []
    backend_used = ""
    try:
        for q in queries:
            results, backend = search_with_fallback(q, max_results=5)
            backend_used = backend
            aggregated.extend(results)
    except NoBackendsConfigured as e:
        out = {"error": "no_search_backends_configured",
               "hint": "set TAVILY_API_KEY or BRAVE_API_KEY in .env",
               "detail": str(e)}
        print(json.dumps(out, indent=2 if args.output == "pretty" else None))
        return 2
    except SearchBackendError as e:
        out = {"error": "search_backend_failed", "detail": str(e)}
        print(json.dumps(out, indent=2 if args.output == "pretty" else None))
        return 3

    seen_urls: set[str] = set()
    deduped: list[dict] = []
    for r in aggregated:
        url = r.get("url", "")
        if url and url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append(r)

    try:
        plan = call_anthropic(args.target, mode, deduped, args.model)
    except Exception as e:  # pragma: no cover - depends on live SDK
        out = {"error": "extraction_failed", "detail": str(e)[:300]}
        print(json.dumps(out, indent=2 if args.output == "pretty" else None))
        return 4

    canonical = plan.get("canonical", {}) or {}
    resolution_status = compute_resolution_status(canonical, mode)

    output = {
        "target": args.target,
        "mode": mode,
        "mode_detection": mode_reason if args.mode == "auto" else "explicit",
        "backend_used": backend_used,
        "resolution_status": resolution_status,
        "model_used": MODELS[args.model],
        **plan,
        "search_queries_used": queries,
        "sources": [{"url": r.get("url", ""), "title": r.get("title", "")} for r in deduped],
    }

    print(json.dumps(output, indent=2 if args.output == "pretty" else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
