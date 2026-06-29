"""Severity-tiered alert router (R14).

One entry point - ``alert(severity, summary, ...)`` - that fans a notice out to
the right channels by severity, with graceful degradation so a failed channel
never escalates into a crash. The three channels:

- **Telegram** push to the CEO's Saved Messages (best-effort; for outages that
  must reach off-machine). Subprocess to the CEO-only telegram_client skill.
- **Action Queue** card (``action_type="alert"``, surfaced read-only). The
  console-first surface the CEO already checks; survives Telegram being down.
- **Log** - always, tagged with the current ``[trace_id]``. The always-on floor.

Routing (Design Decision 6, plan 2026-06-03-next-phase-r3-r14):

    critical -> Telegram + Action Queue card + log   (daemon down / permanent fail)
    warning  ->            Action Queue card + log    (transient, recoverable)
    info     ->                              log only

A Telegram send failure (no session, offline, missing client) degrades to
card+log and NEVER raises. ``alert`` itself never raises - a channel error is
logged and the remaining channels still fire.

CEO-only: alerts route to the CEO's Telegram, so this module is CEO-only during
the spine prove-out (private routing rule in config/routing-map.yaml).

Usage::

    from scripts.utils import alert
    fired = alert.alert("critical", "daemon sentinel silent 6m", source="watchdog")
    # fired == {"telegram": True, "card": True, "log": True}
"""
from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from scripts.utils import trace
from scripts.utils.workspace import get_workspace_root

# Injected at bridge startup via init().  None until then (graceful degradation).
_aq_append_fn: Callable[..., dict] | None = None


def init(aq_fn: Callable[..., dict]) -> None:
    """Inject the action_queue.append_cards callable.

    Called once at bridge startup:
        import scripts.utils.alert as alert
        from scripts.bridge_daemon.sources import action_queue
        alert.init(action_queue.append_cards)
    """
    global _aq_append_fn
    _aq_append_fn = aq_fn

logger = logging.getLogger("x31c.alert")

SEVERITIES = ("critical", "warning", "info")

# Severity -> Action Queue card priority.
_PRIORITY = {"critical": "P1", "warning": "P2", "info": "P3"}

# Path to the CEO-only Telegram client (sends to Saved Messages via "send me").
_TELEGRAM_CLIENT = ".claude/skills/telegram/scripts/telegram_client.py"
_TELEGRAM_TIMEOUT_S = 30


def _telegram_target(workspace_root: Path) -> str:
    """Read daemon.alert.telegram_target from merged config; default "me"."""
    try:
        from scripts.bridge_daemon.config import load_config

        cfg = load_config(workspace_root)
        target = cfg.get("daemon", {}).get("alert", {}).get("telegram_target")
        if isinstance(target, str) and target.strip():
            return target.strip()
    except Exception as exc:  # noqa: BLE001 - config read is best-effort; default below
        logger.debug("alert: telegram_target config read failed: %s", exc)
    return "me"


def _send_telegram(workspace_root: Path, message: str) -> bool:
    """Push a message to Telegram via the CEO-only client. Best-effort.

    Returns True on a clean send, False on any failure (missing client, no
    session, non-zero exit, timeout). NEVER raises.
    """
    client = workspace_root / _TELEGRAM_CLIENT
    if not client.is_file():
        logger.warning("alert: telegram client missing at %s; skipping push", client)
        return False
    target = _telegram_target(workspace_root)
    try:
        result = subprocess.run(
            [sys.executable, str(client), "send", target, message],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=_TELEGRAM_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("alert: telegram send raised %s; degrading to card+log", exc)
        return False
    if result.returncode != 0:
        stderr = (result.stderr or "").strip().replace("\n", " ")[:200]
        logger.warning("alert: telegram send exit %s (%s); degrading to card+log",
                       result.returncode, stderr or "no stderr")
        return False
    return True


def _post_card(workspace_root: Path, severity: str, summary: str, detail: str,
               source: str) -> bool:
    """Append an Action Queue ``alert`` card (read-only surfaced notice).

    Returns True if the card was appended, False on any failure. NEVER raises.
    """
    card = {
        "action_type": "alert",
        "title": summary,
        "reasoning": detail or summary,
        "priority": _PRIORITY.get(severity, "P3"),
        "source": source or "alert",
        "severity": severity,
        "citations": [],
    }
    if _aq_append_fn is None:
        return False
    try:
        res = _aq_append_fn(workspace_root, [card])
    except Exception as exc:  # noqa: BLE001 - card path must not crash the alert
        logger.warning("alert: card append raised %s; log-only for this alert", exc)
        return False
    return bool(res.get("ok") and res.get("added"))


def alert(severity: str, summary: str, detail: str = "", *, source: str = "") -> dict:
    """Route a notice to channels by severity. Never raises.

    Args:
        severity: one of "critical", "warning", "info". An unknown value is
            treated as "warning" (card+log) - safer than dropping to log-only.
        summary: one-line headline (the card title / Telegram subject line).
        detail: optional longer context (the card reasoning / Telegram body).
        source: optional origin label (e.g. "watchdog", "executor").

    Returns:
        dict naming which channels fired, e.g.
        ``{"telegram": True, "card": True, "log": True}``. ``telegram`` and
        ``card`` are False when that channel was not attempted or failed; ``log``
        is always True.
    """
    sev = severity if severity in SEVERITIES else "warning"
    tid = trace.get() or "-"
    fired = {"telegram": False, "card": False, "log": False}

    # Log is the always-on floor. The factory in trace_filter stamps trace_id
    # on the record; we also embed it in the text so a plain handler still shows
    # it, matching the convention for direct-append surfaces (trace-id.md).
    level = {"critical": logging.ERROR, "warning": logging.WARNING}.get(sev, logging.INFO)
    msg = f"[{tid}] alert/{sev}"
    if source:
        msg += f" ({source})"
    msg += f": {summary}"
    if detail:
        msg += f" - {detail}"
    logger.log(level, msg)
    fired["log"] = True

    if sev not in ("critical", "warning"):
        return fired  # info is log-only

    workspace_root = get_workspace_root()

    # Both channels run independently; one failing never blocks the other.
    fired["card"] = _post_card(workspace_root, sev, summary, detail, source)

    if sev == "critical":
        tele_msg = f"31C alert ({source})" if source else "31C alert"
        tele_msg += f": {summary}"
        if detail:
            tele_msg += f"\n{detail}"
        fired["telegram"] = _send_telegram(workspace_root, tele_msg)

    return fired
