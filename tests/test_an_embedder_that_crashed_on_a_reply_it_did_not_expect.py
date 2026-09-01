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


# Bodies that never reach `json.loads` at all, because the DECODE fails first.
# `UnicodeDecodeError` is a `ValueError` and a SIBLING of `json.JSONDecodeError`,
# so the clause covering the parse never covered the decode one expression above
# it. Each of these is a plausible 200 from something that is not ollama: a
# gzip-encoded reply whose header was dropped, a latin-1 error page, a raw
# protobuf frame.
UNDECODABLE_BODIES = [
    b"\xff\xfe{\"embeddings\": []}",
    b'{"error": "mod\xe8le introuvable"}',
    b"\x1f\x8b\x08\x00\x00\x00\x00\x00",
    b"\x80\x81\x82",
]


class _FakeResponse:
    def __init__(self, payload) -> None:
        self._payload = payload if isinstance(payload, bytes) \
            else payload.encode("utf-8")

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


def test_the_undecodable_corpus_really_does_fail_the_decode():
    """A vacuous parametrize otherwise: every row must fail `.decode("utf-8")`
    and not merely fail to parse, or the rows below prove nothing about the
    clause they were added for."""
    assert len(UNDECODABLE_BODIES) >= 4
    for body in UNDECODABLE_BODIES:
        with pytest.raises(UnicodeDecodeError):
            body.decode("utf-8")


@pytest.mark.parametrize("payload", UNDECODABLE_BODIES,
                         ids=[repr(b) for b in UNDECODABLE_BODIES])
def test_an_undecodable_reply_is_also_an_embedding_error(payload,
                                                          stub_transport):
    """The same contract breach as the non-object one, one expression earlier.

    MEASURED 2026-09-01 against `_post_with_retry` as it stood: a 200 carrying
    `b'{"embeddings": [[0.1]]}\\xff\\xfe'` left the module as a raw
    `UnicodeDecodeError`. It is a `ValueError` and a sibling of
    `json.JSONDecodeError`, so `except (json.JSONDecodeError, KeyError)` did not
    see it: no retry, no backoff, and not the one exception this module
    documents. `model_digest` in the same file catches bare `ValueError` and was
    never exposed - the guard existed in one half of the module and not the
    other, which is the identical shape as the isinstance guard this file was
    written for.
    """
    calls = stub_transport(payload)
    with pytest.raises(EmbeddingError) as caught:
        embed(["sovereignty"], model="bge-m3", host="http://stub:11434",
              keep_alive="30m")
    message = str(caught.value)
    assert "malformed response" in message, message
    # Retried like the parse failure beside it, rather than refused on the first
    # attempt like the shape failures: a transport that garbles one reply may
    # not garble the next.
    assert len(calls) == 3, (
        f"an undecodable body took {len(calls)} attempt(s); it is grouped with "
        "the malformed-JSON clause, which retries")


@pytest.mark.parametrize("payload", ["not json at all", "{", "{'single': 1}"],
                         ids=["prose", "truncated", "python-repr"])
def test_a_body_that_decodes_but_does_not_parse_is_also_an_embedding_error(
    payload, stub_transport
):
    """The sibling clause, which had no witness of its own.

    Measured 2026-09-01: removing `json.JSONDecodeError` from the except tuple
    left this file green at 18 passed - every existing row sent something that
    parsed. An error page served as `text/html` with a 200 is the ordinary way
    this arrives from a proxy.
    """
    calls = stub_transport(payload)
    with pytest.raises(EmbeddingError) as caught:
        embed(["sovereignty"], model="bge-m3", host="http://stub:11434",
              keep_alive="30m")
    assert "malformed response" in str(caught.value)
    assert len(calls) == 3


def test_a_read_phase_timeout_is_retried_and_not_propagated(monkeypatch):
    """The bare `TimeoutError` clause, unmeasured.

    Its own comment says a read-phase timeout "raises bare TimeoutError, not
    wrapped in URLError -- without this branch it propagated uncaught and
    skipped the retry/backoff below entirely". Measured 2026-09-01: replacing
    that clause left the file green at 18 passed.

    `TimeoutError` subclasses `OSError`, and `urllib.error.URLError` also
    subclasses `OSError` - but not each other, so the URLError clause above does
    NOT cover it and the ordering of the two is load-bearing.
    """
    calls: list[int] = []

    def fake_urlopen(*_args, **_kwargs):
        calls.append(1)
        raise TimeoutError("read timed out")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(embeddings.time, "sleep", lambda _s: None)

    with pytest.raises(EmbeddingError) as caught:
        embed(["sovereignty"], model="bge-m3", host="http://stub:11434",
              keep_alive="30m")
    assert "timed out waiting for" in str(caught.value)
    assert len(calls) == 3, (
        "a read-phase timeout skipped the retry loop instead of being retried")


def test_an_object_missing_embeddings_still_reports_its_keys(stub_transport):
    """The pre-existing empty-reply branch is untouched by the new guard."""
    stub_transport(json.dumps({"error": "model not found"}))
    with pytest.raises(EmbeddingError) as caught:
        embed(["sovereignty"], model="bge-m3", host="http://stub:11434",
              keep_alive="30m")
    message = str(caught.value)
    assert "no 'embeddings'" in message
    assert "non-object" not in message
