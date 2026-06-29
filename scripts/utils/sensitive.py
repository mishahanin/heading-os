"""Session sensitivity flag — fail-closed successor to the removed `_secure/` vault.

The old marker-file vault gave observability air-gapping *for free*: activating the
vault (the act that made a session sensitive) was the same act that turned Langfuse
tracing off. Removing the vault decouples "this session is sensitive" from "telemetry
is suppressed", so a naive replacement flag would be **fail-open** — forget to set it
and prompts ship to Langfuse. The council named that exact regression.

The fix is the flag's *default*, not a resurrected tree. `is_sensitive()` returns
``True`` (protected) for an unset, empty, garbage, or truthy ``SENSITIVE_MODE`` — it is
``False`` only when the variable is **explicitly and deliberately cleared**. Net effect:
telemetry is opt-in (set ``SENSITIVE_MODE=off`` to permit it), never opt-out. A missing
env var therefore degrades to "no telemetry", never to "telemetry on".

Two features absorbed from the vault:
1. Observability air-gap — `scripts/utils/observability.py` and `observability_safe.py`
   call `is_sensitive()` and suppress tracing when it is True.
2. External-API prompt sanitization — design skills (`/design`, `/flux-image`,
   `/pptx-generator`) call `sanitize_prompt_guidance()` before any external API call
   when `is_sensitive()` is True.

Credentials remain in `.env` (gitignored); `.env` is the credential boundary, so no
minimal vault tree is retained.
"""

from __future__ import annotations

import os

__all__ = ["is_sensitive", "sanitize_prompt_guidance"]

# The ONLY values that clear sensitivity. Everything else (including unset/empty/garbage)
# resolves to sensitive — that asymmetry is the fail-closed property.
_CLEARED = frozenset({"off", "0", "false", "no", "cleared"})


def is_sensitive() -> bool:
    """True (protected) unless ``SENSITIVE_MODE`` is explicitly cleared.

    Fail-closed: unset / empty / unrecognised / truthy → True. Only the exact
    cleared tokens in ``_CLEARED`` (case-insensitive) → False.
    """
    return os.environ.get("SENSITIVE_MODE", "").strip().lower() not in _CLEARED


def sanitize_prompt_guidance() -> str:
    """Return the checklist a skill must apply to any text leaving for an external API
    while the session is sensitive. The content stays local; only the *query* goes out,
    so the query must carry no project-identifying detail.

    Lifted verbatim from the removed `secure-flux` / vault-active sanitization rule so
    no guidance was lost in the vault removal.
    """
    return (
        "SENSITIVE_MODE is active. Before sending any prompt or query to an external "
        "service (Replicate/FLUX image generation, WebSearch, WebFetch), strip all "
        "project-identifying detail. The prompt must contain NO codenames, company "
        "names, people names, deal terms, or strategic specifics — use abstract, "
        "generic descriptions. Example: 'corporate acquisition integration diagram', "
        "NOT 'Phoenix acquisition of CompanyX showing product merger'. The content "
        "stays local; keep the outbound query clean."
    )
