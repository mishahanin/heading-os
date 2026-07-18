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
DEFAULT_TIMEOUT = 120.0


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
    raise RuntimeError(
        f"{model} returned an empty answer (finish_reason={finish_reason})."
    )
