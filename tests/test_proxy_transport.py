"""proxy_transport.call_model routes to the CLIProxyAPI proxy and reproduces the
thinking-model length-truncation retry. No live calls — patched client only."""
from __future__ import annotations

from unittest import mock

import pytest
pytest.importorskip("openai")

from scripts.utils import proxy_transport as pt


def _resp(content, finish_reason):
    msg = mock.MagicMock()
    msg.content = content
    ch = mock.MagicMock()
    ch.message = msg
    ch.finish_reason = finish_reason
    r = mock.MagicMock()
    r.choices = [ch]
    return r


def test_base_url_is_the_local_proxy():
    assert pt.PROXY_BASE_URL == "http://127.0.0.1:8317/v1"


def test_returns_content():
    client = mock.MagicMock()
    client.chat.completions.create.return_value = _resp("the answer", "stop")
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"):
        assert pt.call_model("grok-4.5", "q") == "the answer"


def test_missing_key_raises_named_error():
    with mock.patch.object(pt, "load_api_key", return_value=""):
        with pytest.raises(RuntimeError, match="CLIPROXY_API_KEY"):
            pt.call_model("grok-4.5", "q")


def test_empty_length_retries_then_succeeds():
    client = mock.MagicMock()
    client.chat.completions.create.side_effect = [
        _resp("", "length"),
        _resp("recovered", "stop"),
    ]
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"):
        out = pt.call_model("kimi-for-coding", "q", max_tokens=1000)
    assert out == "recovered"
    assert client.chat.completions.create.call_count == 2


def test_empty_length_exhausted_raises_precise_error():
    client = mock.MagicMock()
    client.chat.completions.create.side_effect = [_resp("", "length"), _resp("", "length")]
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"):
        with pytest.raises(RuntimeError) as ei:
            pt.call_model("kimi-for-coding", "q", max_tokens=120)
    m = str(ei.value).lower()
    assert "reasoning" in m and "max-tokens" in m
    assert "blocked by safety" not in m


def test_empty_content_filter_raises_safety_error():
    client = mock.MagicMock()
    client.chat.completions.create.return_value = _resp("", "content_filter")
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"):
        with pytest.raises(RuntimeError, match="content_filter"):
            pt.call_model("gemini-3-flash", "q")


def test_empty_stop_raises_empty_answer():
    client = mock.MagicMock()
    client.chat.completions.create.return_value = _resp("", "stop")
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"):
        with pytest.raises(RuntimeError, match="finish_reason=stop"):
            pt.call_model("gemini-3-flash", "q")


def test_forwards_timeout_to_client():
    captured = {}

    def fake_make_client(api_key, timeout=120.0):
        captured["timeout"] = timeout
        c = mock.MagicMock()
        c.chat.completions.create.return_value = _resp("ok", "stop")
        return c

    with mock.patch.object(pt, "_make_client", side_effect=fake_make_client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"):
        pt.call_model("grok-4.5", "q", timeout=200.0)
    assert captured["timeout"] == 200.0


def test_reasoning_effort_rides_extra_body():
    client = mock.MagicMock()
    client.chat.completions.create.return_value = _resp("ok", "stop")
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"):
        pt.call_model("k3", "q", reasoning_effort="high")
    _, kwargs = client.chat.completions.create.call_args
    assert kwargs["extra_body"] == {"reasoning_effort": "high"}


def test_reasoning_effort_omitted_when_none():
    client = mock.MagicMock()
    client.chat.completions.create.return_value = _resp("ok", "stop")
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"):
        pt.call_model("kimi-for-coding", "q")
    _, kwargs = client.chat.completions.create.call_args
    assert "extra_body" not in kwargs
