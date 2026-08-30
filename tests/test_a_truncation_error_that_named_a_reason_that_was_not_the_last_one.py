"""A truncation error that named a reason that was not the last one.

`proxy_transport.call_model`'s `length` branch retries at a higher token budget
and re-reads `finish_reason` from that retry. Both error messages under it
nonetheless hardcoded `finish_reason=length`, so when the retry terminated on
anything else the raised error asserted a truncation that was not the terminal
state - and in the empty-answer case it also reported "without a visible answer"
while discarding the characters the FIRST call had returned. Diagnosis then goes
at the token budget when the terminal state was, say, an empty `stop`.

This is the same message-accuracy defect the dated comment block above the
branch was written to end ("nothing named the truncation this function's
docstring promises to name"), pointing the other way: a message naming a
truncation that was not the outcome.

The stubs here replace `_make_client`, so no call leaves this process.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils import proxy_transport  # noqa: E402


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content, finish_reason):
        self.message = _Message(content)
        self.finish_reason = finish_reason


class _Response:
    def __init__(self, content, finish_reason):
        self.choices = [_Choice(content, finish_reason)]


class _Completions:
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def create(self, **_kwargs):
        content, finish_reason = self._script[min(self.calls,
                                                  len(self._script) - 1)]
        self.calls += 1
        return _Response(content, finish_reason)


class _Client:
    def __init__(self, script):
        self.chat = type("chat", (), {"completions": _Completions(script)})()


@pytest.fixture
def scripted(monkeypatch):
    monkeypatch.setattr(proxy_transport, "load_api_key",
                        lambda name, required=False: "not-a-real-key")

    def install(script):
        client = _Client(script)
        monkeypatch.setattr(proxy_transport, "_make_client",
                            lambda api_key, timeout=None: client)
        return client

    return install


def _call(**kwargs):
    return proxy_transport.call_model("k3", "a prompt", max_tokens=64, timeout=1,
                                      **kwargs)


def test_a_retry_that_ends_on_stop_is_not_reported_as_a_length_truncation(scripted):
    scripted([("partial answer", "length"), ("", "stop")])
    with pytest.raises(RuntimeError) as excinfo:
        _call()
    assert "finish_reason=stop" in str(excinfo.value)
    assert "finish_reason=length" not in str(excinfo.value)


def test_the_characters_the_first_call_returned_are_named(scripted):
    scripted([("partial answer", "length"), ("", "stop")])
    with pytest.raises(RuntimeError) as excinfo:
        _call()
    assert "14 characters" in str(excinfo.value)


def test_a_genuine_length_truncation_still_says_length(scripted):
    scripted([("partial answer", "length"), ("a longer partial", "length")])
    with pytest.raises(RuntimeError) as excinfo:
        _call()
    message = str(excinfo.value)
    assert "finish_reason=length" in message
    assert "cut off mid-word" in message


def test_an_empty_length_on_both_calls_still_says_length(scripted):
    scripted([("", "length"), ("", "length")])
    with pytest.raises(RuntimeError) as excinfo:
        _call()
    assert "finish_reason=length" in str(excinfo.value)
    assert "exhausted its token budget" in str(excinfo.value)


def test_no_earlier_partial_means_no_claim_about_one(scripted):
    scripted([("", "length"), ("", "length")])
    with pytest.raises(RuntimeError) as excinfo:
        _call()
    assert "before the retry" not in str(excinfo.value)


def test_a_retry_that_succeeds_returns_the_answer(scripted):
    scripted([("partial", "length"), ("the whole answer", "stop")])
    assert _call() == "the whole answer"


def test_a_complete_first_call_never_reaches_the_branch(scripted):
    client = scripted([("the whole answer", "stop")])
    assert _call() == "the whole answer"
    assert client.chat.completions.calls == 1
