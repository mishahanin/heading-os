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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.utils.api import load_api_key  # noqa: E402

PROXY_BASE_URL = "http://127.0.0.1:8317/v1"
RETRY_CEILING = 16384
# 300s (was 120s): the k3 reasoning voice legitimately thinks for minutes on larger
# inputs, and 120s cut off council/scrutinize critiques mid-reason. Callers with a
# genuinely huge draft can still pass an explicit higher `timeout` (kimi-consult
# exposes --timeout). This is a socket read ceiling, not a per-request target.
DEFAULT_TIMEOUT = 300.0


def _make_client(api_key, timeout=DEFAULT_TIMEOUT):
    """Build the OpenAI SDK client pointed at the proxy. Isolated for test patching."""
    from openai import OpenAI
    return OpenAI(api_key=api_key, base_url=PROXY_BASE_URL, timeout=timeout)


def call_model(model, prompt, *, temperature=0.7, max_tokens=8192, timeout=DEFAULT_TIMEOUT,
               reasoning_effort=None):
    """Send `prompt` to `model` through the proxy; return the visible answer text.

    Raises RuntimeError on missing key, API failure, or a genuine empty/truncated
    answer. On empty content + finish_reason=length (reasoning ate the budget),
    retries once at a strictly higher budget before raising an accurate truncation
    error — never a safety-block claim.

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

    client = _make_client(api_key, timeout=timeout)

    def _call(tok_budget):
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
            raise RuntimeError(
                f"Proxy server error for {model}: {e}. Transient; retry in 30 seconds."
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

    content, finish_reason = _call(max_tokens)
    if content.strip():
        return content

    if finish_reason == "length":
        ceiling = max(max_tokens * 2, RETRY_CEILING)
        if ceiling > max_tokens:
            content, finish_reason = _call(ceiling)
            if content.strip():
                return content
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
        content, finish_reason = _call(max_tokens)
        if content.strip():
            return content

    raise RuntimeError(
        f"{model} returned an empty answer (finish_reason={finish_reason})."
    )
