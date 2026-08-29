"""A non-object reply escaped `_post_with_retry` as the wrong exception.

`scripts/utils/embeddings.embed` documents exactly one failure: `EmbeddingError`.
`_post_with_retry` decoded the reply with `json.loads` and went straight to
`body.get("embeddings")`, so any endpoint answering HTTP 200 with valid JSON that
is not an object -- a proxy, a misconfigured gateway, an ollama version mismatch
-- raised `AttributeError`. That is in none of the retry clauses, so it left the
module immediately: no retry, no backoff, and not the exception every caller of
this module is written to handle. Measured 2026-08-30 for `[]`, `null`, `"oops"`
and `3`; all four crashed.

`model_digest`, in the same file, has always carried the `isinstance(body, dict)`
guard for the identical reason. The guard existed in one half of the module and
not the other.

The transport is stubbed at `urllib.request.urlopen`. Nothing here opens a
socket.
"""
from __future__ import annotations

import json
import urllib.request

import pytest

from scripts.utils import embeddings
from scripts.utils.embeddings import EmbeddingError, embed

# Every one of these is well-formed JSON and none of them is an object.
NON_OBJECT_BODIES = ["[]", "null", '"oops"', "3", "[[0.1, 0.2]]"]


class _FakeResponse:
    def __init__(self, payload: str) -> None:
        self._payload = payload.encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def stub_transport(monkeypatch):
    """Answer every POST with one canned body, and count the attempts."""
    calls: list[int] = []

    def install(payload: str):
        def fake_urlopen(*_args, **_kwargs):
            calls.append(1)
            return _FakeResponse(payload)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        # A retry sleeps 1.5s then 3.0s; the test asserts on attempts, not time.
        monkeypatch.setattr(embeddings.time, "sleep", lambda _s: None)
        return calls

    return install


def test_the_corpus_of_non_object_bodies_is_not_empty():
    assert len(NON_OBJECT_BODIES) >= 4
    for body in NON_OBJECT_BODIES:
        assert not isinstance(json.loads(body), dict)


@pytest.mark.parametrize("payload", NON_OBJECT_BODIES)
def test_a_non_object_reply_raises_embedding_error_and_not_attribute_error(
    payload, stub_transport
):
    """The documented exception, for a body that parses and is not a dict."""
    stub_transport(payload)
    with pytest.raises(EmbeddingError) as caught:
        embed(["sovereignty"], model="bge-m3", host="http://stub:11434",
              keep_alive="30m")
    # The type is the contract. Assert the message names the shape too, so a
    # future `except Exception -> raise EmbeddingError("failed")` blanket does
    # not satisfy this test while telling the operator nothing.
    assert "non-object" in str(caught.value)


@pytest.mark.parametrize("payload", NON_OBJECT_BODIES)
def test_a_non_object_reply_fails_the_call_once_and_not_three_times(
    payload, stub_transport
):
    """Refused immediately, deliberately, and as `EmbeddingError`.

    A 200 carrying the wrong SHAPE is a server-logic answer, not a transport
    blip, so it gets the same treatment as the sibling `no 'embeddings'` branch
    a few lines below it: one attempt, no 4.5 seconds of backoff. Only the
    exception TYPE changed here. The attempt count is asserted so a later
    "retry everything" edit has to come past this test rather than quietly
    charging every malformed reply three round trips.
    """
    calls = stub_transport(payload)
    with pytest.raises(EmbeddingError):
        embed(["пилот"], model="bge-m3", host="http://stub:11434",
              keep_alive="30m")
    assert len(calls) == 1


def test_a_well_shaped_reply_still_returns_its_vectors(stub_transport):
    """The negative case: the guard must not refuse a legitimate answer."""
    stub_transport(json.dumps({"embeddings": [[0.1, 0.2, 0.3]]}))
    got = embed(["sovereignty"], model="bge-m3", host="http://stub:11434",
                keep_alive="30m")
    assert got == [[0.1, 0.2, 0.3]]


def test_an_object_missing_embeddings_still_reports_its_keys(stub_transport):
    """The pre-existing empty-reply branch is untouched by the new guard."""
    stub_transport(json.dumps({"error": "model not found"}))
    with pytest.raises(EmbeddingError) as caught:
        embed(["sovereignty"], model="bge-m3", host="http://stub:11434",
              keep_alive="30m")
    message = str(caught.value)
    assert "no 'embeddings'" in message
    assert "non-object" not in message
