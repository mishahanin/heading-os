"""kimi_transport.reason() returns content and retries once on length-truncated empties."""
from __future__ import annotations

from unittest import mock

import pytest

from scripts.utils import kimi_transport as kt


def _choice(content, finish_reason):
    msg = mock.MagicMock()
    msg.content = content
    ch = mock.MagicMock()
    ch.message = msg
    ch.finish_reason = finish_reason
    resp = mock.MagicMock()
    resp.choices = [ch]
    return resp


def test_reason_returns_content():
    client = mock.MagicMock()
    client.chat.completions.create.return_value = _choice("the answer", "stop")
    with mock.patch.object(kt, "_make_client", return_value=client), \
         mock.patch.object(kt, "load_api_key", return_value="key"):
        out = kt.reason("prompt")
    assert out == "the answer"


def test_reason_retries_once_on_length_empty():
    client = mock.MagicMock()
    client.chat.completions.create.side_effect = [
        _choice("", "length"),                 # first call: reasoning ate the budget
        _choice("recovered answer", "stop"),   # retry at higher budget
    ]
    with mock.patch.object(kt, "_make_client", return_value=client), \
         mock.patch.object(kt, "load_api_key", return_value="key"):
        out = kt.reason("prompt", max_tokens=1000)
    assert out == "recovered answer"
    assert client.chat.completions.create.call_count == 2


def test_reason_raises_on_missing_key():
    with mock.patch.object(kt, "load_api_key", return_value=""):
        with pytest.raises(RuntimeError, match="OLLAMA_API_KEY"):
            kt.reason("prompt")


def test_reason_raises_on_content_filter():
    client = mock.MagicMock()
    client.chat.completions.create.return_value = _choice("", "content_filter")
    with mock.patch.object(kt, "_make_client", return_value=client), \
         mock.patch.object(kt, "load_api_key", return_value="key"):
        with pytest.raises(RuntimeError, match="content_filter"):
            kt.reason("prompt")


def test_reason_raises_on_generic_empty():
    client = mock.MagicMock()
    client.chat.completions.create.return_value = _choice("", "stop")
    with mock.patch.object(kt, "_make_client", return_value=client), \
         mock.patch.object(kt, "load_api_key", return_value="key"):
        with pytest.raises(RuntimeError, match="finish_reason=stop"):
            kt.reason("prompt")


def test_reason_forwards_timeout_to_client():
    captured = {}

    def fake_make_client(api_key, timeout=120.0):
        captured["timeout"] = timeout
        client = mock.MagicMock()
        client.chat.completions.create.return_value = _choice("ok", "stop")
        return client

    with mock.patch.object(kt, "_make_client", side_effect=fake_make_client), \
         mock.patch.object(kt, "load_api_key", return_value="key"):
        kt.reason("prompt", timeout=180.0)
    assert captured["timeout"] == 180.0
