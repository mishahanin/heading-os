"""Cross-vendor LLM fallback for long-running daemons.

Track A of the LLM-fit logging project (plans/2026-05-24-llm-fit-logging-
three-tracks.md). When Anthropic API errors out on a retriable failure
(connection reset, timeout, 429, 5xx), this module cascades the same prompt
through a configured fallback chain (Gemini / Grok via the existing
council wrappers) so the daemon keeps producing output instead of going
silent for the duration of the outage.

Permanent errors (auth, bad request) are not retried - the next vendor
would reject the same payload for the same reason.

Tool-use is NOT supported on the fallback path. None of the current Track A
targets (sentinel, eval-drift-daemon, email-intelligence) use tools, but if
a future caller does, set ``allow_fallback_for_tool_use=False`` (default) and
the wrapper will re-raise the original Anthropic error rather than silently
drop the tool definitions.

Cross-platform by construction: imports the gemini-consult.py /
grok-consult.py wrappers as Python libraries (no subprocess / no shell).
Runs anywhere the bridge daemon runs (WSL, native Linux, macOS, Windows).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scripts.utils.workspace import get_workspace_root

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = get_workspace_root()
CONFIG_PATH = WORKSPACE_ROOT / "config" / "llm_fallback.yaml"

_config_cache: dict | None = None


@dataclass
class LLMResult:
    """Normalised return shape for fallback-aware LLM calls.

    Callers that previously did ``response.content[0].text.strip()`` should
    now do ``result.text``. Vendor + model_id + fallback_triggered let the
    caller make any vendor-specific post-processing decisions (or just log
    them).
    """
    text: str
    vendor: str           # "anthropic" | "gemini" | "grok" | "kimi"
    model_id: str
    fallback_triggered: bool
    primary_error: str | None = None
    raw_anthropic_response: Any | None = None  # populated only when vendor == "anthropic"
    fallback_attempts: list[dict] = field(default_factory=list)


def _load_config() -> dict:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if not CONFIG_PATH.exists():
        raise RuntimeError(
            f"llm_fallback config missing at {CONFIG_PATH}. "
            "Restore it from git or copy from the corporate repo."
        )
    _config_cache = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return _config_cache


def _tier_for_model(model: str) -> str:
    """Substring match on the Anthropic model string -> tier name."""
    m = model.lower()
    if "haiku" in m:
        return "haiku"
    if "sonnet" in m:
        return "sonnet"
    if "opus" in m:
        return "opus"
    raise ValueError(f"cannot derive tier from model name: {model!r}")


def _flatten_to_prompt(system: Any, messages: list[dict]) -> str:
    """Collapse Anthropic-shape system+messages into a single plaintext prompt.

    Anthropic accepts ``system`` as either a string or a list of blocks
    (each ``{"type": "text", "text": "...", "cache_control": {...}}``). Gemini
    and Grok wrappers take a single string. We concatenate in a way that
    preserves the semantic structure - the fallback model has no concept of
    cache_control or system separation, but it does respond well to a plain
    "SYSTEM:\n... USER:\n..." layout.

    Skips non-text blocks defensively; if the caller is sending image blocks
    we have nothing meaningful to send to a text-only fallback.
    """
    parts: list[str] = []
    if isinstance(system, str) and system.strip():
        parts.append(f"SYSTEM:\n{system.strip()}")
    elif isinstance(system, list):
        text_chunks = [
            blk["text"] for blk in system
            if isinstance(blk, dict) and blk.get("type") == "text" and blk.get("text")
        ]
        if text_chunks:
            parts.append("SYSTEM:\n" + "\n\n".join(c.strip() for c in text_chunks))
    for msg in messages:
        role = (msg.get("role") or "").upper() or "USER"
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(f"{role}:\n{content.strip()}")
        elif isinstance(content, list):
            text_chunks = [
                blk["text"] for blk in content
                if isinstance(blk, dict) and blk.get("type") == "text" and blk.get("text")
            ]
            if text_chunks:
                parts.append(f"{role}:\n" + "\n\n".join(c.strip() for c in text_chunks))
    return "\n\n".join(parts)


def _is_retriable_anthropic_error(exc: Exception) -> bool:
    """True iff the exception class matches the retriable list in config."""
    retriable_names = set(_load_config().get("retriable_anthropic_errors", []))
    cls_chain = {c.__name__ for c in type(exc).__mro__}
    return bool(retriable_names & cls_chain)


# Track B: objective signals that a cheaper tier could have sufficed.
# NEVER asks Claude its opinion (self-preference bias). Only inspects the
# response: token count, tool use presence, stop reason. The aggregation
# script (scripts/llm-fit-report.py) buckets traces by skill and reports
# what fraction were flagged - acting on the data is a SEPARATE later
# decision, not part of Phase 2.

_DOWNGRADE_OUTPUT_TOKEN_THRESHOLD = 500


def _compute_downgrade_signal(raw_response: Any, model_used: str) -> dict | None:
    """Inspect an Anthropic response for objective downgrade signals.

    Returns None when the response shape doesn't match (Gemini/Grok
    fallback path - their wrappers return plain strings, not Message
    objects). Returns a dict of signals + a derived ``downgrade_candidate``
    flag otherwise.
    """
    try:
        output_tokens = getattr(raw_response.usage, "output_tokens", None)
        stop_reason = getattr(raw_response, "stop_reason", None)
        has_tool_use = any(
            getattr(b, "type", None) == "tool_use" for b in (raw_response.content or [])
        )
    except Exception:
        return None
    if output_tokens is None:
        return None
    # Haiku-tier calls are already cheap; flagging them as "downgrade
    # candidate" is noise. Only flag Sonnet/Opus calls that produced a
    # short, single-shot, normal-stop response.
    is_already_cheap = "haiku" in (model_used or "").lower()
    downgrade_candidate = (
        not is_already_cheap
        and output_tokens < _DOWNGRADE_OUTPUT_TOKEN_THRESHOLD
        and not has_tool_use
        and stop_reason == "end_turn"
    )
    return {
        "output_tokens": output_tokens,
        "stop_reason": stop_reason,
        "has_tool_use": has_tool_use,
        "downgrade_candidate": downgrade_candidate,
    }


def _tag_langfuse(result: LLMResult, skill_name: str) -> None:
    """Best-effort: tag the current Langfuse trace with vendor + fallback +
    downgrade signals.

    Silent on any failure - observability stuttering must never crash a
    daemon tick.
    """
    try:
        from langfuse import get_client  # type: ignore[import-not-found]
        tags = [
            f"vendor:{result.vendor}",
            f"model:{result.model_id}",
            f"skill:{skill_name}",
        ]
        if result.fallback_triggered:
            tags.append("fallback_triggered")
        metadata: dict = {
            "fallback_triggered": result.fallback_triggered,
            "primary_error": result.primary_error,
            "fallback_attempts": result.fallback_attempts,
        }
        # Track B downgrade-audit: only computable when Anthropic served the
        # response (Gemini/Grok wrappers return plain text, no token usage
        # or stop_reason).
        if result.vendor == "anthropic" and result.raw_anthropic_response is not None:
            signals = _compute_downgrade_signal(result.raw_anthropic_response, result.model_id)
            if signals is not None:
                metadata["downgrade_signals"] = signals
                if signals["downgrade_candidate"]:
                    tags.append("downgrade_candidate")
        get_client().update_current_trace(tags=tags, metadata=metadata)
    except Exception as exc:  # noqa: BLE001 - best-effort Langfuse tagging; must never break the LLM call
        logger.debug("llm_fallback: Langfuse tagging failed: %s", exc)


def call_anthropic_with_fallback(
    client: Any,
    *,
    model: str,
    max_tokens: int,
    system: Any,
    messages: list[dict],
    skill_name: str = "unknown",
    temperature: float = 1.0,
    allow_fallback_for_tool_use: bool = False,
    **anthropic_kwargs: Any,
) -> LLMResult:
    """Try Anthropic; on retriable failure cascade through the configured chain.

    Drop-in replacement for ``client.messages.create(...)`` for the
    text-in-text-out case. Returns an :class:`LLMResult` so callers can
    distinguish "served by Anthropic" from "served by fallback" without
    sniffing the response object.

    Tool calls: if ``anthropic_kwargs`` contains ``tools`` or ``tool_choice``
    and a fallback would be needed, this raises the Anthropic error
    unchanged unless ``allow_fallback_for_tool_use=True`` (which silently
    drops tool definitions on the fallback path - dangerous, opt-in only).
    """
    has_tools = "tools" in anthropic_kwargs or "tool_choice" in anthropic_kwargs
    primary_error: str | None = None
    raw_response: Any = None
    attempts: list[dict] = []

    # ---- Primary: Anthropic ----
    try:
        raw_response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            temperature=temperature,
            **anthropic_kwargs,
        )
        text = "".join(
            b.text for b in raw_response.content if getattr(b, "type", None) == "text"
        ).strip()
        result = LLMResult(
            text=text,
            vendor="anthropic",
            model_id=model,
            fallback_triggered=False,
            raw_anthropic_response=raw_response,
        )
        _tag_langfuse(result, skill_name)
        return result
    except Exception as exc:
        primary_error = f"{type(exc).__name__}: {exc}"
        if not _is_retriable_anthropic_error(exc):
            raise
        if has_tools and not allow_fallback_for_tool_use:
            logging.warning(
                "llm_fallback: anthropic %s but caller uses tools; "
                "re-raising rather than dropping tool definitions on fallback path. "
                "skill=%s err=%s",
                type(exc).__name__, skill_name, primary_error,
            )
            raise

    # ---- Cascade ----
    tier = _tier_for_model(model)
    chain = (_load_config().get("tiers", {}).get(tier, {}) or {}).get("chain", [])
    if not chain:
        logging.error(
            "llm_fallback: no fallback chain configured for tier %s; re-raising. "
            "skill=%s err=%s", tier, skill_name, primary_error,
        )
        # Re-raise the original error since we have nothing to fall back to.
        # The except above already captured it; re-raise by reconstructing a generic
        # RuntimeError preserving the message (the original exc is out of scope).
        raise RuntimeError(
            f"anthropic {tier} failed ({primary_error}) and no fallback chain configured"
        )

    flattened_prompt = _flatten_to_prompt(system, messages)
    for step in chain:
        vendor = step.get("vendor")
        model_id = step.get("model")
        try:
            text = _invoke_vendor(vendor, model_id, flattened_prompt, max_tokens, temperature)
            attempts.append({"vendor": vendor, "model": model_id, "ok": True})
            logging.warning(
                "llm_fallback: anthropic->%s for tier=%s skill=%s reason=%s",
                vendor, tier, skill_name, primary_error,
            )
            result = LLMResult(
                text=text,
                vendor=vendor,
                model_id=model_id,
                fallback_triggered=True,
                primary_error=primary_error,
                fallback_attempts=attempts,
            )
            _tag_langfuse(result, skill_name)
            return result
        except Exception as fallback_exc:
            attempts.append({
                "vendor": vendor,
                "model": model_id,
                "ok": False,
                "error": f"{type(fallback_exc).__name__}: {fallback_exc}",
            })
            logging.warning(
                "llm_fallback: fallback %s/%s failed (%s); continuing chain",
                vendor, model_id, fallback_exc,
            )
            continue

    # All fallbacks exhausted. Surface a clear error citing everything tried.
    chain_summary = "; ".join(
        f"{a['vendor']}/{a['model']}={a.get('error', 'ok')}" for a in attempts
    )
    raise RuntimeError(
        f"llm_fallback: anthropic failed ({primary_error}) and all "
        f"{len(chain)} fallbacks exhausted. Attempts: {chain_summary}"
    )


_vendor_fn_cache: dict[str, Any] = {}


def _load_consult_fn(script_relpath: str, fn_name: str) -> Any:
    """Load a function from a kebab-case Python script via importlib.

    The council wrappers ship as ``scripts/gemini-consult.py`` and
    ``scripts/grok-consult.py`` (kebab-case, CLI convention). Python cannot
    ``import`` hyphenated module names, so we register them via
    spec_from_file_location. Cached to avoid repeating the spec dance on
    every fallback. Renaming the wrappers would break the /council SKILL.md
    subprocess invocations and the skill's documented CLI contract.
    """
    cache_key = f"{script_relpath}::{fn_name}"
    if cache_key in _vendor_fn_cache:
        return _vendor_fn_cache[cache_key]
    import importlib.util
    script_path = WORKSPACE_ROOT / script_relpath
    spec = importlib.util.spec_from_file_location(
        script_relpath.replace("/", "_").replace("-", "_").removesuffix(".py"),
        script_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"llm_fallback: cannot load {script_relpath}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, fn_name, None)
    if fn is None:
        raise RuntimeError(f"llm_fallback: {script_relpath} has no {fn_name}()")
    _vendor_fn_cache[cache_key] = fn
    return fn


def _invoke_vendor(vendor: str, model: str, prompt: str, max_tokens: int, temperature: float) -> str:
    """Dispatch to the matching council wrapper (library-mode, no subprocess)."""
    if vendor == "gemini":
        consult_gemini = _load_consult_fn("scripts/gemini-consult.py", "consult_gemini")
        return consult_gemini(prompt, model=model, temperature=temperature, max_tokens=max_tokens)
    if vendor == "grok":
        consult_grok = _load_consult_fn("scripts/grok-consult.py", "consult_grok")
        return consult_grok(prompt, model=model, temperature=temperature, max_tokens=max_tokens)
    if vendor == "kimi":
        consult_kimi = _load_consult_fn("scripts/kimi-consult.py", "consult_kimi")
        return consult_kimi(prompt, model=model, temperature=temperature, max_tokens=max_tokens)
    raise ValueError(f"unknown fallback vendor: {vendor!r}")
