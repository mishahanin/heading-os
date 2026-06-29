"""Tests for scripts/polymarket.py.

Covers cases listed in plans/2026-05-01-polymarket-market-brief-ceo-intel.md
Success Criterion #5:
  - whitelist hit + 5 markets returned
  - whitelist hit + zero markets returned
  - whitelist miss
  - disambiguation filter applied
  - malformed Gamma response (graceful degradation)
Plus precedence rule (P2 fix), volume threshold (P5 fix), JSON wrapper
shape (P6 fix), markdown columns (P7 fix), and internal-use footer (P1 fix).
"""
from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest
import urllib.error

from scripts import polymarket as pm


# ---------- whitelist precedence (P2 fix) ----------


def test_whitelist_positive_only_returns_category():
    cat, has_neg = pm.match_whitelist("AI agents trending now")
    assert cat == "ai_big_tech"
    assert has_neg is False


def test_whitelist_negative_only_returns_none_with_negative_flag():
    cat, has_neg = pm.match_whitelist("DPI vendor landscape the legacy incumbent")
    assert cat is None
    assert has_neg is True


def test_whitelist_neither_returns_none_no_negative():
    cat, has_neg = pm.match_whitelist("random gardening question")
    assert cat is None
    assert has_neg is False


def test_whitelist_positive_wins_when_both_match():
    """P2: positive wins when both positive and negative keywords present."""
    cat, has_neg = pm.match_whitelist("AI policy in DPI ecosystem")
    assert cat == "ai_big_tech"
    assert has_neg is True
    # Caller fires anyway because positive matched


# ---------- query_polymarket end-to-end ----------


def _mock_gamma_response(markets: list[dict]) -> MagicMock:
    body = json.dumps(markets).encode("utf-8")
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=MagicMock(read=lambda: body))
    cm.__exit__ = MagicMock(return_value=False)
    return cm


def _gamma_market(question: str, volume: float, prices=("0.65", "0.35"),
                  end_date: str = "2026-12-31T00:00:00Z", slug: str = "test-slug") -> dict:
    return {
        "question": question,
        "slug": slug,
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(list(prices)),
        "volume": volume,
        "endDate": end_date,
    }


def test_outside_whitelist_skips_without_api_call():
    """Whitelist miss -> outside_whitelist, no HTTP call made."""
    with patch.object(pm.urllib.request, "urlopen") as urlopen:
        result = pm.query_polymarket("DPI vendor landscape")
    assert result["skip_reason"] == "outside_whitelist"
    assert result["markets"] == []
    assert result["whitelist_match"] is None
    urlopen.assert_not_called()


def test_whitelist_hit_returns_markets():
    """Whitelist hit + matches -> markets returned, skip_reason None."""
    markets_raw = [
        _gamma_market("Will OpenAI release GPT-5 by Q4 2026?", 250000),
        _gamma_market("Will Anthropic ship Claude 5 by 2026?", 180000),
    ]
    with patch.object(pm.urllib.request, "urlopen", return_value=_mock_gamma_response(markets_raw)):
        result = pm.query_polymarket("OpenAI")
    assert result["skip_reason"] is None
    assert result["whitelist_match"] == "ai_big_tech"
    assert len(result["markets"]) == 1  # only "OpenAI" appears in 1 question
    assert result["markets"][0]["question"].startswith("Will OpenAI")
    assert result["markets"][0]["volume_usd"] == 250000
    assert len(result["markets"][0]["outcomes"]) == 2


def test_whitelist_hit_zero_matches_returns_no_matches():
    """Whitelist hits but client-side filter returns nothing."""
    markets_raw = [_gamma_market("Will Bitcoin hit $200k in 2026?", 500000)]
    with patch.object(pm.urllib.request, "urlopen", return_value=_mock_gamma_response(markets_raw)):
        result = pm.query_polymarket("OpenAI")
    assert result["skip_reason"] == "no_matches"
    assert result["markets"] == []
    assert result["whitelist_match"] == "ai_big_tech"


def test_disambiguation_keywords_filter_drops_non_matching():
    """P4: --keywords narrows to markets containing at least one keyword."""
    markets_raw = [
        _gamma_market("Will Apple release a new iPhone in 2026?", 400000),
        _gamma_market("Will Apple farm prices rise in 2026?", 200000),  # decoy
    ]
    with patch.object(pm.urllib.request, "urlopen", return_value=_mock_gamma_response(markets_raw)):
        result = pm.query_polymarket("Apple", keywords=["iphone", "stock", "company"])
    assert len(result["markets"]) == 1
    assert "iPhone" in result["markets"][0]["question"]


def test_volume_threshold_filters_below_min():
    """P5: --min-volume-usd drops below-threshold markets."""
    markets_raw = [
        _gamma_market("AI big market: will model X ship?", 50000),
        _gamma_market("AI tiny market: will indie release Y?", 500),  # below threshold
    ]
    with patch.object(pm.urllib.request, "urlopen", return_value=_mock_gamma_response(markets_raw)):
        result = pm.query_polymarket("AI", min_volume_usd=10000.0)
    assert len(result["markets"]) == 1
    assert result["markets"][0]["volume_usd"] == 50000


def test_limit_parameter_caps_result_count():
    markets_raw = [
        _gamma_market(f"AI prediction market #{i}", 100000 + i * 1000) for i in range(8)
    ]
    with patch.object(pm.urllib.request, "urlopen", return_value=_mock_gamma_response(markets_raw)):
        result = pm.query_polymarket("AI", limit=3)
    assert len(result["markets"]) == 3


def test_malformed_gamma_response_returns_fetch_error():
    """Malformed JSON from Gamma -> graceful degradation with skip_reason fetch_error."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=MagicMock(read=lambda: b"not valid json {"))
    cm.__exit__ = MagicMock(return_value=False)
    with patch.object(pm.urllib.request, "urlopen", return_value=cm):
        result = pm.query_polymarket("AI agents")
    assert result["skip_reason"] == "fetch_error"
    assert result["markets"] == []
    assert "error" in result


def test_gamma_api_http_error_returns_fetch_error():
    """Gamma 500 after retry exhaustion -> fetch_error."""
    err = urllib.error.HTTPError("https://gamma-api.polymarket.com/markets",
                                  500, "ISE", {}, io.BytesIO(b"boom"))
    with patch.object(pm.urllib.request, "urlopen", side_effect=err):
        with patch.object(pm.time, "sleep", return_value=None):
            result = pm.query_polymarket("AI")
    assert result["skip_reason"] == "fetch_error"


# ---------- output wrapper shape (P6 fix) ----------


def test_json_wrapper_has_required_keys():
    """P6: top-level wrapper must have markets, skip_reason, query_used, whitelist_match."""
    result = pm.query_polymarket("DPI acmenetworks")  # outside whitelist
    assert set(result.keys()) >= {"markets", "skip_reason", "query_used", "whitelist_match"}


# ---------- markdown rendering (P7 + P1 fixes) ----------


def test_render_markdown_columns_match_spec():
    """P7: pin columns Market | Top Outcome | Probability | Volume | End Date."""
    markets = [{
        "question": "Will OpenAI release GPT-5 by Q4 2026?",
        "outcomes": [{"name": "Yes", "probability": 0.65}, {"name": "No", "probability": 0.35}],
        "end_date": "2026-12-31",
        "volume_usd": 250000.0,
        "link": "https://polymarket.com/event/test",
    }]
    md = pm.render_markdown(markets)
    assert "Market" in md
    assert "Top Outcome" in md
    assert "Probability" in md
    assert "Volume" in md
    assert "End Date" in md
    assert "Yes" in md
    assert "65%" in md
    assert "$250,000" in md


def test_render_markdown_includes_internal_use_footer():
    """P1: every markdown render must include the internal-use footer."""
    md = pm.render_markdown([])
    assert "internal signal only" in md.lower()
    assert "never used in external" in md.lower()


def test_render_markdown_empty_emits_no_matches_message():
    md = pm.render_markdown([])
    assert "No matching" in md
    assert pm.INTERNAL_USE_FOOTER in md


def test_render_markdown_truncates_long_questions():
    long_question = "Will " + "very " * 30 + "long topic resolve by 2026?"
    markets = [{
        "question": long_question,
        "outcomes": [{"name": "Yes", "probability": 0.5}],
        "end_date": "2026-12-31",
        "volume_usd": 100000.0,
        "link": "",
    }]
    md = pm.render_markdown(markets)
    assert "..." in md


# ---------- normalisation helpers ----------


def test_parse_outcomes_and_prices_handles_string_json():
    market = {"outcomes": '["Yes", "No"]', "outcomePrices": '["0.7", "0.3"]'}
    out = pm._parse_outcomes_and_prices(market)
    assert len(out) == 2
    assert out[0]["name"] == "Yes"
    assert out[0]["probability"] == 0.7


def test_parse_outcomes_and_prices_handles_missing_data():
    out = pm._parse_outcomes_and_prices({})
    assert out == []


def test_parse_outcomes_and_prices_handles_malformed_json():
    out = pm._parse_outcomes_and_prices({"outcomes": "not json", "outcomePrices": "[]"})
    assert out == []


def test_normalise_market_builds_polymarket_link():
    market = _gamma_market("Test", 1000, slug="test-event-slug")
    n = pm.normalise_market(market)
    assert n["link"] == "https://polymarket.com/event/test-event-slug"
    assert n["volume_usd"] == 1000.0
