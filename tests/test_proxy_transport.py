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
    with mock.patch.object(pt, "load_api_key", return_value=""), \
         pytest.raises(RuntimeError, match="CLIPROXY_API_KEY"):
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
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"), pytest.raises(RuntimeError) as ei:
        pt.call_model("kimi-for-coding", "q", max_tokens=120)
    m = str(ei.value).lower()
    assert "reasoning" in m and "max-tokens" in m
    assert "blocked by safety" not in m


# --------------------------------------------------- truncated-but-not-empty
#
# Until 2026-08-23 `finish_reason` was inspected ONLY when the content was
# empty: `if content.strip(): return content` came first. A model that emitted
# half an answer and then hit the budget returned that half as if it were the
# whole thing, exit 0, no warning. Which branch you landed in was a coin flip
# on how long the reasoning ran - the same prompt at the same budget produced
# an empty `length` (loud, retried, raised) on one call and a partial `length`
# (silent, returned) on the next. Measured against the live proxy on that date.
#
# Every caller of `call_model` captures the return value as a complete answer:
# `kimi-consult`, `grok-consult`, `gemini-consult`, `deep-research-advance` and
# `scrutinize-dispatch`. A half-written refutation vote counts as a vote.

def test_a_truncated_answer_is_never_returned_as_complete():
    """The defect: non-empty + length was returned verbatim, exit 0."""
    client = mock.MagicMock()
    client.chat.completions.create.side_effect = [
        _resp("1. First defect\n2. Second defe", "length"),
        _resp("1. First defect\n2. Second defect\n3. Third defect", "stop"),
    ]
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"):
        out = pt.call_model("kimi-for-coding", "q", max_tokens=1000)
    assert out.endswith("3. Third defect"), out
    assert client.chat.completions.create.call_count == 2, (
        "a cut-off answer was accepted without a retry"
    )


def test_a_still_truncated_retry_raises_and_says_it_was_partial():
    """Better a named failure than a half answer that reads as a whole one."""
    client = mock.MagicMock()
    client.chat.completions.create.side_effect = [
        _resp("half an answ", "length"),
        _resp("still half an answ", "length"),
    ]
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"), \
         pytest.raises(RuntimeError) as ei:
        pt.call_model("kimi-for-coding", "q", max_tokens=1000)
    m = str(ei.value).lower()
    assert "cut off" in m, m
    assert "18 characters" in m, "the operator cannot tell how much was lost"
    assert "blocked by safety" not in m


def test_the_partial_is_not_smuggled_out_through_the_error():
    """A caller must not be able to `str(exc)` its way to the truncated text
    and treat it as the answer. The length is diagnostic; the body is not."""
    client = mock.MagicMock()
    client.chat.completions.create.return_value = _resp("SECRET-PARTIAL", "length")
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"), \
         pytest.raises(RuntimeError) as ei:
        pt.call_model("k3", "q", max_tokens=1000)
    assert "SECRET-PARTIAL" not in str(ei.value)


def test_the_retry_gets_a_bigger_socket_ceiling_than_the_first_call():
    """A doubled token budget means a longer think. Reusing the first call's
    timeout is what turned a truncation into a timeout: measured 2026-08-23,
    8192 tokens answered in 158s and 32768 tokens blew a 240s ceiling."""
    seen = []

    def fake_make_client(api_key, timeout=pt.DEFAULT_TIMEOUT):
        seen.append(timeout)
        c = mock.MagicMock()
        c.chat.completions.create.side_effect = [
            _resp("", "length"), _resp("ok", "stop"),
        ][len(seen) - 1:]
        return c

    with mock.patch.object(pt, "_make_client", side_effect=fake_make_client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"):
        pt.call_model("k3", "q", max_tokens=8192, timeout=300.0)
    assert len(seen) == 2, seen
    assert seen[1] > seen[0], f"retry reused the first ceiling: {seen}"


def test_the_retry_ceiling_growth_is_bounded():
    """Scaling with the budget without a cap would let one call wait an hour."""
    seen = []

    def fake_make_client(api_key, timeout=pt.DEFAULT_TIMEOUT):
        seen.append(timeout)
        c = mock.MagicMock()
        c.chat.completions.create.side_effect = [
            _resp("", "length"), _resp("ok", "stop"),
        ][len(seen) - 1:]
        return c

    with mock.patch.object(pt, "_make_client", side_effect=fake_make_client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"):
        pt.call_model("k3", "q", max_tokens=16, timeout=300.0)  # ratio would be 1024x
    assert seen[1] <= seen[0] * pt.RETRY_TIMEOUT_GROWTH_CAP, seen


def test_a_complete_answer_still_costs_exactly_one_call():
    """The guard must not add a round trip to the common path."""
    client = mock.MagicMock()
    client.chat.completions.create.return_value = _resp("the answer", "stop")
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"):
        assert pt.call_model("grok-4.5", "q") == "the answer"
    assert client.chat.completions.create.call_count == 1


def test_empty_content_filter_raises_safety_error():
    client = mock.MagicMock()
    client.chat.completions.create.return_value = _resp("", "content_filter")
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"), \
         pytest.raises(RuntimeError, match="content_filter"):
        pt.call_model("gemini-3-flash", "q")


def test_empty_stop_raises_empty_answer_after_one_retry():
    """Still raises, but not on the first empty completion.

    `stop` with no content used to be terminal immediately. On 2026-08-19 the
    Kimi voice returned it twice during one `/scrutinize`; the skill noted the
    drop and carried on, so the refutation layer silently ran at half roster.
    A deterministic emptiness still raises - one call later, which is what the
    second `_resp` here stands for.
    """
    client = mock.MagicMock()
    client.chat.completions.create.side_effect = [_resp("", "stop"), _resp("", "stop")]
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"), \
         pytest.raises(RuntimeError, match="finish_reason=stop"):
        pt.call_model("gemini-3-flash", "q")
    assert client.chat.completions.create.call_count == 2, (
        "an empty answer is terminal again, so one transient blank drops a voice"
    )


def test_a_transient_empty_stop_recovers():
    """The case the retry exists for: blank once, answers on the retry."""
    client = mock.MagicMock()
    client.chat.completions.create.side_effect = [
        _resp("", "stop"),
        _resp("the critique", "stop"),
    ]
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"):
        out = pt.call_model("k3", "q")
    assert out == "the critique"
    assert client.chat.completions.create.call_count == 2


def test_the_empty_retry_is_bounded_to_one():
    """A retry loop against a model that always returns blank would burn the
    subscription quota the proxy exists to protect."""
    client = mock.MagicMock()
    client.chat.completions.create.return_value = _resp("", "stop")
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"), \
         pytest.raises(RuntimeError):
        pt.call_model("k3", "q")
    assert client.chat.completions.create.call_count == 2, (
        f"expected exactly one retry, made "
        f"{client.chat.completions.create.call_count} calls"
    )


def test_a_content_filter_block_is_never_retried():
    """A safety block is a decision, not a hiccup. Retrying it wastes a call and
    would let a genuine block read as a transient blank."""
    client = mock.MagicMock()
    client.chat.completions.create.return_value = _resp("", "content_filter")
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"), \
         pytest.raises(RuntimeError, match="content_filter"):
        pt.call_model("gemini-3-flash", "q")
    assert client.chat.completions.create.call_count == 1


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


# ------------------------------------------------ a transient 503 is retried
#
# The proxy returns 503 `auth_unavailable ... check Claude auth/key session and
# cooldown state` when a provider session is exhausted. It recovers on its own
# within seconds. `call_model` classified this as InternalServerError, told the
# caller "Transient; retry in 30 seconds", and then did not retry. Measured
# 2026-08-23: a 37-shard audit re-run lost 29 shards to a single cooldown
# window, each failing in 1-3 seconds, and k3 answered normally on the next
# manual probe. A transport that names a failure transient must handle it.

def _sleepless(monkeypatch):
    """Retry backoff must not actually sleep in the suite."""
    slept = []
    monkeypatch.setattr(pt.time, "sleep", slept.append)
    return slept


def test_a_transient_503_is_retried_rather_than_raised(monkeypatch):
    from openai import InternalServerError
    boom = InternalServerError("auth_unavailable: no auth available",
                               response=mock.MagicMock(), body=None)
    client = mock.MagicMock()
    client.chat.completions.create.side_effect = [boom, _resp("recovered", "stop")]
    slept = _sleepless(monkeypatch)
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"):
        assert pt.call_model("k3", "q") == "recovered"
    assert client.chat.completions.create.call_count == 2
    assert slept, "retried with no backoff at all: that hammers a cooling proxy"


def test_the_503_retry_backs_off_and_is_bounded(monkeypatch):
    """A provider down for good must not spin forever on the subscription."""
    from openai import InternalServerError
    boom = InternalServerError("auth_unavailable", response=mock.MagicMock(), body=None)
    client = mock.MagicMock()
    client.chat.completions.create.side_effect = boom
    slept = _sleepless(monkeypatch)
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"), \
         pytest.raises(RuntimeError, match="server error"):
        pt.call_model("k3", "q")
    assert client.chat.completions.create.call_count == pt.SERVER_ERROR_ATTEMPTS
    assert slept == sorted(slept), f"backoff must not shrink: {slept}"
    assert len(slept) == pt.SERVER_ERROR_ATTEMPTS - 1


def test_a_rate_limit_is_not_swept_into_the_503_retry(monkeypatch):
    """Rate limiting is a quota decision with its own message; retrying it
    silently would burn the subscription this proxy exists to protect."""
    from openai import RateLimitError
    client = mock.MagicMock()
    client.chat.completions.create.side_effect = RateLimitError(
        "slow down", response=mock.MagicMock(), body=None)
    _sleepless(monkeypatch)
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"), \
         pytest.raises(RuntimeError, match="rate-limited"):
        pt.call_model("k3", "q")
    assert client.chat.completions.create.call_count == 1


def test_an_auth_failure_is_not_retried_either(monkeypatch):
    """A wrong key is a decision, not a hiccup."""
    from openai import AuthenticationError
    client = mock.MagicMock()
    client.chat.completions.create.side_effect = AuthenticationError(
        "401", response=mock.MagicMock(), body=None)
    _sleepless(monkeypatch)
    with mock.patch.object(pt, "_make_client", return_value=client), \
         mock.patch.object(pt, "load_api_key", return_value="cpx-test"), \
         pytest.raises(RuntimeError, match="auth failed"):
        pt.call_model("k3", "q")
    assert client.chat.completions.create.call_count == 1
