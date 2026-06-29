"""perplexity_client.research() returns (content, citations) and builds the payload correctly."""
from __future__ import annotations

import io
import json
from unittest import mock

import pytest

from scripts.utils import perplexity_client as pc


def _fake_response(payload_dict):
    """Return an object whose .read() yields the JSON bytes, usable as a urlopen ctx manager."""
    body = json.dumps(payload_dict).encode("utf-8")
    cm = mock.MagicMock()
    cm.__enter__.return_value.read.return_value = body
    return cm


def test_research_returns_content_and_citations():
    api_payload = {
        "choices": [{"message": {"content": "answer text"}}],
        "citations": ["https://a.com", "https://b.com"],
    }
    with mock.patch.object(pc, "load_api_key", return_value="key"), \
         mock.patch("urllib.request.urlopen", return_value=_fake_response(api_payload)):
        content, citations = pc.research("what is X?")
    assert content == "answer text"
    assert citations == ["https://a.com", "https://b.com"]


def test_research_sets_include_domain_filter():
    captured = {}

    def fake_urlopen(req, timeout=60):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _fake_response({"choices": [{"message": {"content": "x"}}], "citations": []})

    with mock.patch.object(pc, "load_api_key", return_value="key"), \
         mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        pc.research("q", domains="reuters.com,bbc.com")
    assert captured["body"]["search_domain_filter"] == ["reuters.com", "bbc.com"]


def test_research_sets_exclude_domain_filter():
    captured = {}

    def fake_urlopen(req, timeout=60):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _fake_response({"choices": [{"message": {"content": "x"}}], "citations": []})

    with mock.patch.object(pc, "load_api_key", return_value="key"), \
         mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        pc.research("q", exclude_domains="pinterest.com,quora.com")
    assert captured["body"]["search_domain_filter"] == ["-pinterest.com", "-quora.com"]


def test_research_converts_httperror_to_runtimeerror():
    import urllib.error

    def boom(req, timeout=60):
        raise urllib.error.HTTPError(
            url="https://api.perplexity.ai/chat/completions",
            code=429, msg="Too Many Requests", hdrs=None,
            fp=io.BytesIO(b"rate limited"),
        )

    with mock.patch.object(pc, "load_api_key", return_value="key"), \
         mock.patch("urllib.request.urlopen", side_effect=boom):
        with pytest.raises(RuntimeError, match="Perplexity API error 429"):
            pc.research("q")


def test_research_omits_recency_filter_when_none():
    captured = {}

    def fake_urlopen(req, timeout=60):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _fake_response({"choices": [{"message": {"content": "x"}}], "citations": []})

    with mock.patch.object(pc, "load_api_key", return_value="key"), \
         mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        pc.research("q", recency=None)
    assert "search_recency_filter" not in captured["body"]


def test_research_includes_recency_filter_when_set():
    captured = {}

    def fake_urlopen(req, timeout=60):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _fake_response({"choices": [{"message": {"content": "x"}}], "citations": []})

    with mock.patch.object(pc, "load_api_key", return_value="key"), \
         mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        pc.research("q", recency="month")
    assert captured["body"]["search_recency_filter"] == "month"


def test_research_converts_socket_timeout_to_runtimeerror():
    def boom(req, timeout=90):
        raise TimeoutError("The read operation timed out")

    with mock.patch.object(pc, "load_api_key", return_value="key"), \
         mock.patch("urllib.request.urlopen", side_effect=boom):
        with pytest.raises(RuntimeError, match="timeout/socket"):
            pc.research("q")
