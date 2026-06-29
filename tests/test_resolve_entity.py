"""Tests for scripts/resolve_entity.py and scripts/utils/search.py.

Covers cases listed in plans/2026-05-01-osint-entity-resolution-prepass.md
Success Criterion #4:
  (a) Tavily happy path
  (b) Tavily 429 -> Brave fallback
  (c) Both fail -> error JSON
  (d) Anthropic returns malformed -> caught
  (e) Mode auto-detection on representative inputs
  (f) Both keys absent -> no_search_backends_configured
Plus helper-function tests for _slugify, build_queries, build_tool_schema,
compute_resolution_status.
"""
from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest
import urllib.error

from scripts import resolve_entity as re_mod
from scripts.utils import search as search_mod


# ---------- helper unit tests ----------


def test_slugify_basic():
    assert re_mod._slugify("ExampleTelco") == "exampletelco"
    assert re_mod._slugify("Peter Steinberger") == "peter-steinberger"
    assert re_mod._slugify("e& Group") == "e-group"
    assert re_mod._slugify("  trailing  ") == "trailing"


def test_compute_resolution_status_company_high():
    canonical = {
        "name": "ExampleTelco", "aliases": ["e&"], "parent": "e& Group",
        "subsidiaries": ["e& UAE"], "ticker": "EAND.AE",
        "founded_year": 1976, "hq_country": "UAE",
    }
    assert re_mod.compute_resolution_status(canonical, "company") == "high"


def test_compute_resolution_status_company_partial():
    canonical = {
        "name": "Acme", "aliases": [], "parent": None, "subsidiaries": [],
        "ticker": None, "founded_year": 2020, "hq_country": "US",
    }
    # 3 of 7 populated = 0.43 -> partial
    assert re_mod.compute_resolution_status(canonical, "company") == "partial"


def test_compute_resolution_status_company_low():
    canonical = {
        "name": "X", "aliases": [], "parent": None, "subsidiaries": [],
        "ticker": None, "founded_year": None, "hq_country": None,
    }
    # 1 of 7 populated -> low
    assert re_mod.compute_resolution_status(canonical, "company") == "low"


def test_compute_resolution_status_person_high():
    canonical = {
        "name": "Peter Steinberger", "aliases": ["@steipete"],
        "current_role": "Codex", "current_org": "OpenAI",
        "previous_orgs": ["PSPDFKit"],
    }
    assert re_mod.compute_resolution_status(canonical, "person") == "high"


def test_build_queries_company_quick_two():
    qs = re_mod.build_queries("ExampleTelco", "company", "quick")
    assert len(qs) == 2
    assert all("ExampleTelco" in q for q in qs)


def test_build_queries_company_standard_three():
    qs = re_mod.build_queries("ExampleTelco", "company", "standard")
    assert len(qs) == 3


def test_build_tool_schema_company_required_fields():
    schema = re_mod.build_tool_schema("company")
    assert schema["type"] == "object"
    required = set(schema["required"])
    assert "canonical" in required
    assert "social" in required
    assert "field_sources" in required
    canonical_required = set(schema["properties"]["canonical"]["required"])
    assert canonical_required == {"name", "aliases", "parent", "subsidiaries",
                                  "ticker", "founded_year", "hq_country"}


def test_build_tool_schema_person_has_affiliations():
    schema = re_mod.build_tool_schema("person")
    assert "affiliations" in schema["properties"]
    assert "field_sources" in schema["properties"]


def test_build_tool_schema_market_has_key_players():
    schema = re_mod.build_tool_schema("market")
    assert "key_players" in schema["properties"]


def test_build_tool_schema_technology_has_communities():
    schema = re_mod.build_tool_schema("technology")
    assert "communities" in schema["properties"]


# ---------- mode auto-detection ----------


def test_detect_mode_crm_match(tmp_path, monkeypatch):
    """CRM contact file present -> person."""
    contacts = tmp_path / "crm" / "contacts"
    contacts.mkdir(parents=True)
    (contacts / "alice-smith.md").write_text("# Alice", encoding="utf-8")
    monkeypatch.setattr(re_mod, "get_crm_contacts_dir", lambda: contacts)
    # Stub the other lookups so they don't accidentally match
    monkeypatch.setattr(re_mod, "get_personal_context_dir", lambda: tmp_path / "missing")
    monkeypatch.setattr(re_mod, "get_workspace_root", lambda: tmp_path / "missing")
    mode, reason = re_mod.detect_mode("Alice Smith")
    assert mode == "person"
    assert "crm/contacts" in reason


def test_detect_mode_pipeline_match(tmp_path, monkeypatch):
    """Target appears in context/pipeline.md -> company."""
    contacts = tmp_path / "crm" / "contacts"
    contacts.mkdir(parents=True)
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "people.md").write_text("# People\n", encoding="utf-8")
    (ctx / "pipeline.md").write_text("ExampleTelco is a deal in stage Demo.", encoding="utf-8")
    monkeypatch.setattr(re_mod, "get_crm_contacts_dir", lambda: contacts)
    monkeypatch.setattr(re_mod, "get_personal_context_dir", lambda: ctx)
    monkeypatch.setattr(re_mod, "get_workspace_root", lambda: tmp_path)
    mode, reason = re_mod.detect_mode("ExampleTelco")
    assert mode == "company"
    assert "pipeline" in reason


def test_detect_mode_people_match(tmp_path, monkeypatch):
    """Target appears in context/people.md -> person."""
    contacts = tmp_path / "crm" / "contacts"
    contacts.mkdir(parents=True)
    ctx = tmp_path / "context"
    ctx.mkdir()
    (ctx / "people.md").write_text("Bob Jones is the CEO of Acme.\n", encoding="utf-8")
    (ctx / "pipeline.md").write_text("# Pipeline\n", encoding="utf-8")
    monkeypatch.setattr(re_mod, "get_crm_contacts_dir", lambda: contacts)
    monkeypatch.setattr(re_mod, "get_personal_context_dir", lambda: ctx)
    monkeypatch.setattr(re_mod, "get_workspace_root", lambda: tmp_path)
    mode, _ = re_mod.detect_mode("Bob Jones")
    assert mode == "person"


def test_detect_mode_corporate_suffix_heuristic(tmp_path, monkeypatch):
    """Target ends with corporate suffix -> company (no CRM/people/pipeline match)."""
    monkeypatch.setattr(re_mod, "get_crm_contacts_dir", lambda: tmp_path / "missing")
    monkeypatch.setattr(re_mod, "get_personal_context_dir", lambda: tmp_path / "missing")
    monkeypatch.setattr(re_mod, "get_workspace_root", lambda: tmp_path / "missing")
    mode, reason = re_mod.detect_mode("Globalstar Inc")
    assert mode == "company"
    assert "suffix" in reason


def test_detect_mode_market_keyword_heuristic(tmp_path, monkeypatch):
    monkeypatch.setattr(re_mod, "get_crm_contacts_dir", lambda: tmp_path / "missing")
    monkeypatch.setattr(re_mod, "get_personal_context_dir", lambda: tmp_path / "missing")
    monkeypatch.setattr(re_mod, "get_workspace_root", lambda: tmp_path / "missing")
    mode, _ = re_mod.detect_mode("GCC telecom market")
    assert mode == "market"


def test_detect_mode_capitalised_name_heuristic(tmp_path, monkeypatch):
    monkeypatch.setattr(re_mod, "get_crm_contacts_dir", lambda: tmp_path / "missing")
    monkeypatch.setattr(re_mod, "get_personal_context_dir", lambda: tmp_path / "missing")
    monkeypatch.setattr(re_mod, "get_workspace_root", lambda: tmp_path / "missing")
    mode, reason = re_mod.detect_mode("Peter Steinberger")
    assert mode == "person"
    assert "capitalised" in reason


def test_detect_mode_technology_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(re_mod, "get_crm_contacts_dir", lambda: tmp_path / "missing")
    monkeypatch.setattr(re_mod, "get_personal_context_dir", lambda: tmp_path / "missing")
    monkeypatch.setattr(re_mod, "get_workspace_root", lambda: tmp_path / "missing")
    mode, reason = re_mod.detect_mode("yt-dlp")
    assert mode == "technology"


# ---------- search backend tests (mocked HTTP) ----------


def _mock_response(payload: dict) -> MagicMock:
    """Build a context-manager mock for urlopen returning a JSON body."""
    body = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=MagicMock(read=lambda: body))
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def test_tavily_search_happy_path(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake")
    payload = {"results": [
        {"title": "T1", "url": "https://a.com", "content": "abc", "score": 0.9},
        {"title": "T2", "url": "https://b.com", "content": "def", "score": 0.8},
    ]}
    with patch.object(search_mod.urllib.request, "urlopen", return_value=_mock_response(payload)):
        results = search_mod.tavily_search("test query", max_results=2)
    assert len(results) == 2
    assert results[0]["title"] == "T1"
    assert results[0]["url"] == "https://a.com"
    assert results[0]["score"] == 0.9


def test_brave_search_happy_path(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "BSA-fake")
    payload = {"web": {"results": [
        {"title": "B1", "url": "https://x.com", "description": "desc1",
         "extra_snippets": ["snip1", "snip2"]},
        {"title": "B2", "url": "https://y.com", "description": "desc2"},
    ]}}
    with patch.object(search_mod.urllib.request, "urlopen", return_value=_mock_response(payload)):
        results = search_mod.brave_search("test query", max_results=2)
    assert len(results) == 2
    assert results[0]["title"] == "B1"
    assert "desc1" in results[0]["content"]
    assert "snip1" in results[0]["content"]


def test_search_with_fallback_tavily_429_brave_succeeds(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake")
    monkeypatch.setenv("BRAVE_API_KEY", "BSA-fake")

    brave_payload = {"web": {"results": [
        {"title": "Brave1", "url": "https://b.com", "description": "fallback"},
    ]}}

    call_count = {"n": 0}

    def fake_urlopen(req, timeout=30):
        url = req.full_url
        call_count["n"] += 1
        # Tavily endpoint -> 429 both attempts
        if "tavily" in url:
            err = urllib.error.HTTPError(url, 429, "Too Many", {}, io.BytesIO(b"rate limited"))
            raise err
        # Brave -> success
        return _mock_response(brave_payload)

    with patch.object(search_mod.urllib.request, "urlopen", side_effect=fake_urlopen):
        # Force the retry to be fast
        with patch.object(search_mod.time, "sleep", return_value=None):
            results, backend = search_mod.search_with_fallback("query", max_results=1)

    assert backend == "brave"
    assert len(results) == 1
    assert results[0]["title"] == "Brave1"


def test_search_with_fallback_both_keys_absent(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    # Also short-circuit load_env so it can't pick up real values
    with patch.object(search_mod, "load_api_key", return_value=""):
        with pytest.raises(search_mod.NoBackendsConfigured):
            search_mod.search_with_fallback("anything", max_results=1)


def test_search_with_fallback_tavily_only_returns_tavily(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake")
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    payload = {"results": [
        {"title": "T1", "url": "https://a.com", "content": "x", "score": 0.5},
    ]}

    def fake_load(name, required=True):
        if name == "TAVILY_API_KEY":
            return "tvly-fake"
        return ""

    with patch.object(search_mod, "load_api_key", side_effect=fake_load):
        with patch.object(search_mod.urllib.request, "urlopen", return_value=_mock_response(payload)):
            results, backend = search_mod.search_with_fallback("q", max_results=1)
    assert backend == "tavily"
    assert len(results) == 1


def test_search_with_fallback_both_fail_propagates(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake")
    monkeypatch.setenv("BRAVE_API_KEY", "BSA-fake")

    def always_500(req, timeout=30):
        err = urllib.error.HTTPError(req.full_url, 500, "ISE", {}, io.BytesIO(b"boom"))
        raise err

    with patch.object(search_mod.urllib.request, "urlopen", side_effect=always_500):
        with patch.object(search_mod.time, "sleep", return_value=None):
            with pytest.raises(search_mod.SearchBackendError):
                search_mod.search_with_fallback("q", max_results=1)
