"""Shared proxy transport for external model calls (council + research).

Routes every Kimi/Grok/Gemini call through the local CLIProxyAPI proxy
(127.0.0.1:8317, OpenAI-compatible), which fronts flat subscriptions instead of
per-token vendor keys. Prompt-agnostic: it sends the prompt verbatim and never
injects a system block, so each caller owns its own prompt coupling (council
injects the 31C block via council_prompts; deep-research sends raw prompts and so
never leaks business context into the third-party cloud).

Reproduces the thinking-model truncation retry (empty content + finish_reason=
length) that Kimi/Grok reasoning needs, and classifies OpenAI SDK exceptions into
RuntimeError with actionable messages.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.api import load_api_key  # noqa: E402

PROXY_BASE_URL = "http://127.0.0.1:8317/v1"
RETRY_CEILING = 16384
# 300s (was 120s): the k3 reasoning voice legitimately thinks for minutes on larger
# inputs, and 120s cut off council/scrutinize critiques mid-reason. Callers with a
# genuinely huge draft can still pass an explicit higher `timeout` (kimi-consult
# exposes --timeout). This is a socket read ceiling for ONE call, not a per-request
# target: a `length` retry makes a second call, so the worst-case wall time is
# `timeout * (1 + RETRY_TIMEOUT_GROWTH_CAP)`.
DEFAULT_TIMEOUT = 300.0
# The retry raises the token budget, and a bigger budget means a longer think, so
# the socket ceiling has to grow with it or the retry times out where the first
# call merely truncated. Measured 2026-08-23 against the live proxy: 8192 tokens
# answered in 158s, and 32768 tokens blew a 240s ceiling outright. Capped so a
# small `max_tokens` (whose ratio to RETRY_CEILING is enormous) cannot turn one
# call into an hour-long wait.
RETRY_TIMEOUT_GROWTH_CAP = 2.0
# The proxy answers 503 `auth_unavailable ... check Claude auth/key session and
# cooldown state` when a provider session is exhausted, and recovers on its own
# within seconds. The transport classified that as InternalServerError, printed
# "Transient; retry in 30 seconds", and then did not retry - so the caller ate a
# hard failure over a condition the message itself called temporary. Measured
# 2026-08-23: one cooldown window cost a 37-shard audit re-run 29 shards, each
# failing in 1 to 3 seconds, and the model answered normally on the next probe.
# Bounded, because a provider that is genuinely down must not spin on the
# subscription this proxy exists to protect.
SERVER_ERROR_ATTEMPTS = 4
SERVER_ERROR_BACKOFF = (5.0, 20.0, 45.0)


def _is_complete(content: str, finish_reason) -> bool:
    """A usable answer: visible text the model actually finished writing.

    `finish_reason == "length"` means the budget cut the answer off mid-word.
    That is true whether or not any text escaped, and until 2026-08-23 only the
    no-text half was noticed: `if content.strip(): return content` ran before
    `finish_reason` was looked at, so a half-written answer was returned as the
    whole thing with exit 0 and no warning. Which half you landed in was a coin
    flip on how long the reasoning ran — the same prompt at the same budget
    produced an empty `length` on one call and a partial `length` on the next.

    Every caller treats the return value as a complete answer, so a partial one
    is worse than an error: `scrutinize-dispatch` counts a half-written
    refutation as a vote, and the 2026-08-23 engine audit recorded truncated
    finding lists as finished shards.
    """
    return bool(content.strip()) and finish_reason != "length"


class _TransientServerError(Exception):
    """Internal: a proxy 503 the transport will retry. Never escapes call_model."""


def _make_client(api_key, timeout=DEFAULT_TIMEOUT):
    """Build the OpenAI SDK client pointed at the proxy. Isolated for test patching."""
    from openai import OpenAI
    return OpenAI(api_key=api_key, base_url=PROXY_BASE_URL, timeout=timeout)


def call_model(model, prompt, *, temperature=0.7, max_tokens=8192, timeout=DEFAULT_TIMEOUT,
               reasoning_effort=None):
    """Send `prompt` to `model` through the proxy; return the visible answer text.

    The return value is a COMPLETE answer or nothing: a response cut off by the
    token budget raises rather than returning its fragment (see `_is_complete`).

    Raises RuntimeError on missing key, API failure, or a genuine empty/truncated
    answer. On finish_reason=length — empty (reasoning ate the budget) or partial
    (the answer itself was cut) — retries once at a strictly higher budget, with
    the socket ceiling grown to match, before raising an accurate truncation
    error that names how much was lost — never a safety-block claim.

    `timeout` is the ceiling for ONE call. A retry makes a second one at up to
    `RETRY_TIMEOUT_GROWTH_CAP` times that, so budget for `timeout * 3` in the
    worst case. Passing a `max_tokens` and `timeout` large enough to finish on
    the first call is cheaper than relying on the retry.

    `reasoning_effort` (low/high/max) is optional and honored by thinking models
    (e.g. k3); when set it rides `extra_body={"reasoning_effort": ...}`. Omit it
    (leave as None) for models that don't support the field, such as the default
    kimi-for-coding.
    """
    from openai import (
        APIError,
        APIConnectionError,
        AuthenticationError,
        BadRequestError,
        NotFoundError,
        RateLimitError,
        APITimeoutError,
        InternalServerError,
    )

    api_key = load_api_key("CLIPROXY_API_KEY", required=False)
    if not api_key:
        raise RuntimeError(
            "CLIPROXY_API_KEY is missing from .env. Add the local CLIProxyAPI key "
            "(`cliproxy key`) before invoking the council."
        )

    def _call(tok_budget, call_timeout):
        """One attempt, retrying only the proxy's own transient 503."""
        last: Exception | None = None
        for attempt in range(SERVER_ERROR_ATTEMPTS):
            try:
                return _attempt(tok_budget, call_timeout)
            except _TransientServerError as e:
                last = e
                if attempt < SERVER_ERROR_ATTEMPTS - 1:
                    time.sleep(SERVER_ERROR_BACKOFF[
                        min(attempt, len(SERVER_ERROR_BACKOFF) - 1)])
        raise RuntimeError(
            f"{last} Still failing after {SERVER_ERROR_ATTEMPTS} attempts over "
            f"{sum(SERVER_ERROR_BACKOFF):.0f}s of backoff; the provider session "
            f"behind the proxy is not recovering."
        ) from last

    def _attempt(tok_budget, call_timeout):
        client = _make_client(api_key, timeout=call_timeout)
        create_kwargs = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": tok_budget,
        }
        if reasoning_effort:
            create_kwargs["extra_body"] = {"reasoning_effort": reasoning_effort}
        try:
            resp = client.chat.completions.create(**create_kwargs)
        except AuthenticationError as e:
            raise RuntimeError(
                f"Proxy auth failed for {model}: {e}. Check CLIPROXY_API_KEY in .env."
            ) from e
        except RateLimitError as e:
            raise RuntimeError(
                f"Proxy rate-limited for {model}: {e}. Retry shortly or check the "
                "subscription quota behind the proxy."
            ) from e
        except NotFoundError as e:
            raise RuntimeError(
                f"Proxy returned 404 for {model}: {e}. Check the model id (`cliproxy models`)."
            ) from e
        except BadRequestError as e:
            raise RuntimeError(
                f"Proxy rejected the request for {model}: {e}. Check model id and prompt."
            ) from e
        except APITimeoutError as e:
            raise RuntimeError(
                f"Proxy timeout for {model}: {e}. Retry or reduce --max-tokens."
            ) from e
        except APIConnectionError as e:
            raise RuntimeError(
                f"Proxy connection failed for {model}: {e}. Is CLIProxyAPI running? "
                "(`cliproxy status`)."
            ) from e
        except InternalServerError as e:
            # Handled by _call_with_server_retry below, which owns the backoff.
            raise _TransientServerError(
                f"Proxy server error for {model}: {e}."
            ) from e
        except APIError as e:
            raise RuntimeError(f"Proxy call failed for {model}: {e}") from e
        except Exception as e:  # network / non-APIError
            msg = str(e).lower()
            if "timeout" in msg or "timed out" in msg:
                raise RuntimeError(
                    f"Proxy timeout for {model}: {e}. Retry or reduce --max-tokens."
                ) from e
            raise RuntimeError(f"Proxy call failed for {model}: {e}") from e

        if not resp.choices:
            raise RuntimeError(f"Proxy returned no choices for {model}.")
        ch = resp.choices[0]
        return (ch.message.content or ""), ch.finish_reason

    content, finish_reason = _call(max_tokens, timeout)
    if _is_complete(content, finish_reason):
        return content

    if finish_reason == "length":
        ceiling = max(max_tokens * 2, RETRY_CEILING)
        if ceiling > max_tokens:
            growth = min(ceiling / max_tokens, RETRY_TIMEOUT_GROWTH_CAP)
            content, finish_reason = _call(ceiling, timeout * growth)
            if _is_complete(content, finish_reason):
                return content
        got = len(content.strip())
        if got:
            # Never put the partial itself in the message: a caller that
            # str()s the exception would treat it as the answer, which is the
            # defect this branch exists to prevent. The length is diagnostic.
            raise RuntimeError(
                f"{model} hit its token budget ({ceiling}) and the answer is cut "
                f"off mid-word ({got} characters returned, finish_reason=length). "
                "Raise --max-tokens and --timeout together, or split the prompt — "
                "a thinking-model truncation, not a safety block."
            )
        raise RuntimeError(
            f"{model} exhausted its token budget ({ceiling}) in the reasoning phase "
            "without a visible answer (finish_reason=length). Raise --max-tokens or "
            "simplify the prompt — a thinking-model truncation, not a safety block."
        )
    if finish_reason == "content_filter":
        raise RuntimeError(
            f"{model} returned empty: blocked by safety filters (content_filter)."
        )

    # `stop` with no content is a NORMAL termination that produced nothing, and
    # until 2026-08-19 it raised on the first occurrence with zero retries. On
    # that day the Kimi voice returned it twice during one `/scrutinize`, the
    # skill noted the drop and carried on as designed, and the refutation layer
    # ran at half its roster - a quiet degradation of a review, which is the
    # worst place to have one.
    #
    # The CAUSE is unreproduced and this retry does not claim to fix it. Prompt
    # size was ruled out afterwards (k3 answered a 361 864-character prompt), and
    # the proxy was updated from 7.2.129 to 7.2.136 in the same pass, so the
    # original conditions no longer exist to test against. What is defensible
    # without a diagnosis is that one empty completion should not be terminal:
    # `length` already earns a retry, and this case is strictly less informative
    # than that one. If the emptiness is deterministic for a given prompt, the
    # second call returns empty too and the error is raised exactly as before,
    # one call later.
    if finish_reason == "stop":
        content, finish_reason = _call(max_tokens, timeout)
        if _is_complete(content, finish_reason):
            return content

    raise RuntimeError(
        f"{model} returned an empty answer (finish_reason={finish_reason})."
    )
