#!/usr/bin/env python3
"""Inbox Pulse daemon -- main entrypoint.

Wraps EWSConnection + state helpers into a long-running process that polls
the Inbox every 30 seconds and logs each new item to a JSONL file.

Usage::

    # One-shot health probe (env vars, state dir writable, EWS connectable)
    python -m scripts.inbox_pulse.daemon --check

    # Run forever (30s polling loop + JSONL logging + 60-second heartbeat)
    python -m scripts.inbox_pulse.daemon

Sovereignty discipline
----------------------
- NEVER log item.subject text (only len(subject))
- NEVER log item.sender.email_address raw (only the domain portion)
- NEVER log item.body or any body fragment
- NEVER use the langfuse SDK directly (Phase 3 wires observability via observability_safe)

Signal handling
---------------
SIGTERM and SIGINT both set _shutdown_event, which the main loop and the
heartbeat thread both respect. Clean exit occurs within one tick (<=60s).

Cursor management
-----------------
The daemon persists a datetime cursor in state/email-triage/inbox_cursor.json.
On first start (no cursor), the cursor is bootstrapped to now() -- skipping
any historical email. Each successful poll advances the cursor to the
datetime_received of the most-recent item processed, so daemon restarts
resume exactly where they left off.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Workspace on sys.path
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve()
_WORKSPACE = _HERE.parent.parent.parent
if str(_WORKSPACE) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE))

from scripts.inbox_pulse.exchange import EWSConnection  # noqa: E402
from scripts.inbox_pulse.paths import get_state_dir  # noqa: E402
from scripts.inbox_pulse.state import append_jsonl, load_state, save_state, write_heartbeat  # noqa: E402
from scripts.utils.colors import GREEN, RED, YELLOW, RESET  # noqa: E402
from scripts.utils.healthchecks import ping as hc_ping  # noqa: E402

__all__ = [
    "health_check",
    "_main_loop",
    "_heartbeat_loop",
    "_domain_of",
    "_handle_signal",
    "_shutdown_event",
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("inbox_pulse.daemon")

# ---------------------------------------------------------------------------
# Shutdown coordination
# ---------------------------------------------------------------------------
_shutdown_event = threading.Event()


def _handle_signal(signum: int, frame: object) -> None:
    """SIGTERM/SIGINT handler -- sets the shared shutdown event."""
    logger.info("Signal %s received -- initiating clean shutdown", signum)
    _shutdown_event.set()


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

# ---------------------------------------------------------------------------
# Polling interval
# ---------------------------------------------------------------------------
POLL_INTERVAL_SECONDS = 30

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _domain_of(addr: str) -> str:
    """Return the domain portion of an email address.

    Examples::

        _domain_of("victor@northgate.com") -> "northgate.com"
        _domain_of("no-at-sign")             -> ""
        _domain_of("")                        -> ""
    """
    if "@" not in addr:
        return ""
    return addr.split("@", 1)[1]


def _today_str() -> str:
    """Return today's date as YYYY-MM-DD (used for log file naming)."""
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# Health check (--check mode)
# ---------------------------------------------------------------------------


def health_check() -> int:
    """One-shot health probe. Returns 0 on success, 1 on any failure.

    Checks:
      1. Required env vars present (EXCHANGE_EMAIL, EXCHANGE_PASSWORD, EXCHANGE_SERVER)
      2. State directory is writable
      3. EWS is reachable (lightweight connect + disconnect)
    """
    # Load .env into os.environ first (idempotent; no-op if already loaded by systemd)
    try:
        from scripts.utils.workspace import load_env
        load_env()
    except Exception as exc:
        logger.warning("inbox-pulse: failed to load .env during preflight: %s", exc)

    # 1. Env vars
    for var in ["EXCHANGE_EMAIL", "EXCHANGE_PASSWORD", "EXCHANGE_SERVER"]:
        if not os.getenv(var):
            print(f"{RED}FAIL: {var} not set in .env{RESET}", file=sys.stderr)
            return 1

    # 2. State dir writable
    try:
        state_dir = get_state_dir()
        test_file = state_dir / ".health-check.tmp"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
    except Exception as exc:
        print(
            f"{RED}FAIL: state dir not writable ({state_dir}): {exc}{RESET}",
            file=sys.stderr,
        )
        return 1

    # 3. EWS reachable
    try:
        ews = EWSConnection()
        _ = ews.account  # triggers connect
        ews.disconnect()
    except Exception as exc:
        print(f"{RED}FAIL: EWS unreachable: {exc}{RESET}", file=sys.stderr)
        return 1

    print(f"{GREEN}OK: env vars present, state dir writable, EWS connectable{RESET}")
    return 0


# ---------------------------------------------------------------------------
# Heartbeat thread
# ---------------------------------------------------------------------------


def _heartbeat_loop(
    shutdown_event: threading.Event,
    queue_depth_fn,
    tick_seconds: int = 60,
) -> None:
    """Write a heartbeat to state.json every tick_seconds.

    Exits cleanly when shutdown_event is set (wait() returns True on set).
    tick_seconds is parameterised so tests can use a short interval.
    """
    while not shutdown_event.is_set():
        try:
            write_heartbeat(extra={"queue_depth": queue_depth_fn()})
        except Exception as exc:
            logger.warning("Heartbeat write failed: %s", exc)
        shutdown_event.wait(tick_seconds)


# ---------------------------------------------------------------------------
# Main loop (extracted for testability)
# ---------------------------------------------------------------------------


def _main_loop(
    shutdown_event: threading.Event,
    ews: EWSConnection,
    write_log_fn,
    fetch_item_fn,
    get_cursor_fn,
    set_cursor_fn,
    rules_engine=None,
    classifier=None,
) -> None:
    """Polling loop. Polls Inbox every POLL_INTERVAL_SECONDS for new items.

    Args:
        shutdown_event:  Set by signal handler to request clean exit.
        ews:             EWSConnection instance (already initialised).
        write_log_fn:    Callable(filename: str, entry: dict) -- append to JSONL.
        fetch_item_fn:   Callable(item_id: str) -> Item -- for enrichment.
        get_cursor_fn:   Callable() -> datetime | None -- load persisted cursor.
        set_cursor_fn:   Callable(datetime) -> None -- persist cursor.
        rules_engine:    RulesEngine instance for YAML auto-reload (Phase 2+).
        classifier:      CheapClassifier instance for shadow-mode scoring (Phase 2+).

    Cursor management:
    - On first poll (cursor is None), sets cursor to now (skips historical email).
    - After each successful poll cycle, cursor advances to the most-recent
      item's datetime_received (or stays put if no new items).
    - Cursor persists across daemon restarts via state file (key: inbox_cursor).

    Shadow-mode (Phase 2):
    - On each cycle, reloads rules YAML if mtime changed (cheap check).
    - For each new item, runs CheapClassifier and merges tier_guess + weight +
      reason_breakdown into the log entry.
    - Still NO Telegram pushes, NO Exchange categories, NO LLM calls.

    Reconnects with backoff on any Exchange error.
    """
    cursor = get_cursor_fn()  # returns datetime or None

    # Bootstrap: if no cursor, set to now (skip any historical email)
    if cursor is None:
        cursor = datetime.now(timezone.utc)
        set_cursor_fn(cursor)
        logger.info("Bootstrap cursor set to now (no historical poll)")

    while not shutdown_event.is_set():
        try:
            # Reload rules YAML if it changed on disk (no-op if mtime unchanged)
            if rules_engine is not None and rules_engine.reload_if_changed():
                logger.info("Rules YAML reloaded")

            latest_received = cursor
            for event in ews.poll_inbox(since=cursor):
                if shutdown_event.is_set():
                    break

                log_entry: dict = {
                    "ts": event["timestamp"],
                    "event_type": event["event_type"],
                    "message_id": event["item_id"],
                    "parent_folder_id": event["parent_folder_id"],
                    "sender_domain": "",
                    "subject_length": 0,
                    "mode": "shadow",
                    # Classifier output defaults (overwritten below if sender present)
                    "tier_guess": "LOW",
                    "weight": 0,
                    "reason_breakdown": {},
                }

                # Enrichment: fetch sender + subject + recipients for classification.
                # sender_email, subject, recipients_to, and recipients_cc are all
                # TRANSIENT -- consumed by classify() and NOT stored in log_entry
                # (sovereignty discipline: full addresses must never appear in JSONL).
                sender_email = ""
                subject = ""
                recipients_to: list[str] = []
                recipients_cc: list[str] = []
                try:
                    item = fetch_item_fn(event["item_id"])
                    if item.sender and item.sender.email_address:
                        sender_email = item.sender.email_address
                        log_entry["sender_domain"] = _domain_of(sender_email)
                    if item.subject:
                        subject = item.subject
                        log_entry["subject_length"] = len(subject)
                    # NEW: extract recipient lists for TL+To/CC classifier rule.
                    # Full addresses are transient -- NEVER assigned to log_entry.
                    if item.to_recipients:
                        recipients_to = [
                            r.email_address
                            for r in item.to_recipients
                            if r and r.email_address
                        ]
                    if item.cc_recipients:
                        recipients_cc = [
                            r.email_address
                            for r in item.cc_recipients
                            if r and r.email_address
                        ]
                except Exception as exc:
                    logger.debug("Enrichment skipped for %s: %s", event["item_id"], exc)

                # Classify (only when sender is known; skip with LOW defaults otherwise)
                if sender_email and classifier is not None:
                    try:
                        result = classifier.classify(
                            sender_email=sender_email,
                            subject=subject,
                            body_preview="",  # body preview not yet wired; classifier handles empty
                            recipients_to=recipients_to or None,
                            recipients_cc=recipients_cc or None,
                        )
                        log_entry["tier_guess"] = result["tier_guess"]
                        log_entry["weight"] = result["weight"]
                        log_entry["reason_breakdown"] = result["reason_breakdown"]
                    except Exception as exc:
                        logger.warning("Classification failed for %s: %s", event["item_id"], exc)
                        # log_entry keeps defaults (LOW / 0 / {})

                write_log_fn(f"log-{_today_str()}.jsonl", log_entry)
                logger.debug(
                    "Logged event %s domain=%s tier=%s",
                    event["event_type"],
                    log_entry["sender_domain"],
                    log_entry["tier_guess"],
                )

                # Update cursor candidate to this item's datetime_received
                if event["datetime_received"]:
                    item_received = datetime.fromisoformat(event["datetime_received"])
                    if latest_received is None or item_received > latest_received:
                        latest_received = item_received

            # Advance cursor if any new items were processed.
            # Add 1 second so the next filter(datetime_received__gt=cursor) does not
            # re-fetch the boundary item (EWS on-prem truncates received timestamps
            # to whole seconds, so __gt with the exact timestamp is unreliable).
            if latest_received != cursor:
                cursor = latest_received + timedelta(seconds=1)
                set_cursor_fn(cursor)
                logger.debug("Cursor advanced to %s", cursor.isoformat())

        except Exception as exc:
            logger.warning("Poll cycle failed, retrying after backoff: %s", exc, exc_info=True)
            shutdown_event.wait(60)  # longer backoff on error
            continue

        # Deadman: a clean poll cycle pings the Healthchecks.io check. The
        # `continue` above skips this on failure, so a wedged Exchange poll
        # stops the pings and trips an external alert. Best-effort, never raises.
        hc_ping("STEWARD_HC_EMAIL_TRIAGE")
        # Sleep between polls (interruptible by shutdown)
        shutdown_event.wait(POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Parse args and dispatch to health_check or run-forever mode."""
    parser = argparse.ArgumentParser(
        description="Inbox Pulse daemon -- EWS polling subscription + JSONL event log"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="One-shot health probe. Exits 0 on success, 1 on failure.",
    )
    args = parser.parse_args()

    if args.check:
        return health_check()

    # --- Run-forever mode ---
    logger.info("%sInbox Pulse daemon starting (Phase 2 shadow mode)%s", YELLOW, RESET)

    # Load .env into os.environ (idempotent; no-op if already loaded by systemd
    # EnvironmentFile= directive on the VM)
    try:
        from scripts.utils.workspace import load_env
        load_env()
    except Exception as exc:
        logger.warning("inbox-pulse: failed to load .env at startup: %s", exc)

    # Validate required env vars early
    for var in ["EXCHANGE_EMAIL", "EXCHANGE_PASSWORD", "EXCHANGE_SERVER"]:
        if not os.getenv(var):
            logger.error("Missing required env var: %s", var)
            return 1

    from scripts.utils.workspace import get_workspace_root, get_data_config_dir
    from scripts.utils.paths import get_data_root
    workspace_root = get_workspace_root()
    # HEADING OS engine/data split: shareable engine config lives on the engine
    # workspace_root; the classifier's CRM/pipeline/threads reads AND the triage
    # rules are DATA, so they resolve under the data root. On transitional ceo-main
    # the two are identical.
    data_root = get_data_root()

    # Phase 2: RulesEngine + CheapClassifier
    from scripts.inbox_pulse.overrides import RulesEngine
    from scripts.inbox_pulse.rules import CheapClassifier

    rules_path = get_data_config_dir() / "email-triage-rules.yaml"  # config-DATA -> data root
    rules_engine = RulesEngine(yaml_path=rules_path)

    ews = EWSConnection()

    classifier = CheapClassifier(
        rules=rules_engine,
        workspace_root=workspace_root,
        account=ews.account,
        my_email=os.getenv("EXCHANGE_EMAIL"),
        data_root=data_root,
    )

    # Initial heartbeat
    write_heartbeat(extra={"queue_depth": 0})

    # Heartbeat background thread
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(_shutdown_event, lambda: 0),
        kwargs={"tick_seconds": 60},
        daemon=True,
        name="inbox-pulse-heartbeat",
    )
    heartbeat_thread.start()

    logger.info("%sDaemon running. Starting inbox polling (shadow-mode classification)...%s", GREEN, RESET)

    # Cursor persistence helpers
    _CURSOR_FILE = "inbox_cursor.json"

    def get_cursor() -> datetime | None:
        state = load_state(_CURSOR_FILE, default=None)
        if state and "cursor" in state:
            return datetime.fromisoformat(state["cursor"])
        return None

    def set_cursor(dt: datetime) -> None:
        save_state(_CURSOR_FILE, {"cursor": dt.isoformat()})

    _main_loop(
        shutdown_event=_shutdown_event,
        ews=ews,
        write_log_fn=append_jsonl,
        fetch_item_fn=ews.fetch_item,
        get_cursor_fn=get_cursor,
        set_cursor_fn=set_cursor,
        rules_engine=rules_engine,
        classifier=classifier,
    )

    # Clean exit
    ews.disconnect()
    write_heartbeat(extra={"queue_depth": 0, "shutting_down": True})
    logger.info("Inbox Pulse daemon stopped cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
