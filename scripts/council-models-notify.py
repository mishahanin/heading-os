#!/usr/bin/env python3
"""council-models-notify.py -- daily timer entrypoint: nudge on stale /council pins.

Thin orchestrator (no LLM, no config write). On each fire it runs the read-only
freshness check across the three council providers (Grok via xAI, Gemini via
Google, Kimi via local ollama) and, when a pin is BROKEN or a newer flagship is
available, pushes a one-line nudge with the exact bump command to the CEO's
Telegram alert channel. When every pin is current it sends nothing.

Not naggy: it dedups on the actionable finding set, so an unchanged "grok-4.6
available" is announced ONCE, not every day. The nudge re-fires only when the
finding set changes (a new model appears, or a pin breaks). `/prime` and a
manual `python scripts/council-models.py --check` remain the backstop for a
still-pending bump. Adoption is always the CEO's one-command `--set`.

Recipient is read from the gitignored engine .env, never hardcoded here:
    COUNCIL_MODELS_TELEGRAM_TARGET -> OPS_RADAR_TELEGRAM_TARGET
    -> ODIN_CADENCE_TELEGRAM_TARGET -> unconfigured (no send)

Delivery is via the dedicated notifications bot (scripts/utils/telegram_notify.py).

A transient failure (probe or send) is logged and SWALLOWED (exit 0) so the
oneshot systemd unit is never left `failed`. Invoked by
scripts/templates/systemd/council-models-check.service (daily timer). Also
runnable by hand:
    python3 scripts/council-models-notify.py            # send only on a changed finding
    python3 scripts/council-models-notify.py --force     # send even if unchanged (test)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Workspace import bootstrap (per development-standards.md)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.workspace import get_outputs_dir, get_workspace_root  # noqa: E402
from scripts.utils.paths import load_env  # noqa: E402
from scripts.utils import telegram_notify  # noqa: E402

STATE_RELPATH = "operations/council/freshness-nudge-state.json"  # under get_outputs_dir()
# Unconfigured default when no env target is set -- never a send attempt
# (Telegram's own-account "me"/Saved Messages sentinel is not a valid
# fallback anywhere in this system).
DEFAULT_RECIPIENT = ""


def _log(msg: str) -> None:
    print(f"[council-models-notify] {msg}", file=sys.stderr)


def _signature(findings: list[dict]) -> list[str]:
    """Stable, sorted signature of the actionable findings (dedup key)."""
    from scripts.utils.council_freshness import is_actionable

    return sorted(
        f"{f['provider']}:{f['status']}:{f.get('candidate') or '-'}"
        for f in findings
        if is_actionable(f)
    )


def _state_path() -> Path:
    return get_outputs_dir() / STATE_RELPATH


def _load_last_signature() -> list[str]:
    try:
        with open(_state_path(), encoding="utf-8") as f:
            return list(json.load(f).get("signature", []))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError) as e:
        _log(f"could not read nudge state ({e}); treating as no prior nudge")
        return []


def _save_signature(signature: list[str]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"signature": signature}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description="Nudge Telegram when a /council model pin is stale.")
    ap.add_argument("--force", action="store_true",
                    help="Send even when the finding set is unchanged (delivery test).")
    args = ap.parse_args()

    root = get_workspace_root()
    load_env(root)  # make .env (API keys + *_TELEGRAM_TARGET) visible under systemd

    # Import after load_env so the freshness probes see the API keys.
    from scripts.utils import council_freshness as freshness

    try:
        findings = freshness.assess()
    except Exception as exc:  # noqa: BLE001 - boundary; a missed nudge is non-critical
        _log(f"freshness check failed to run ({type(exc).__name__}: {exc}); exiting 0")
        return 0

    line = freshness.nudge_line(findings)
    if not line:
        _log("all council pins current -- no nudge")
        _save_signature([])  # reset so the next new finding re-fires
        return 0

    signature = _signature(findings)
    if not args.force and signature == _load_last_signature():
        _log("finding set unchanged since last nudge -- suppressing (use --force to override)")
        return 0

    recipient = (
        os.environ.get("COUNCIL_MODELS_TELEGRAM_TARGET")
        or os.environ.get("OPS_RADAR_TELEGRAM_TARGET")
        or os.environ.get("ODIN_CADENCE_TELEGRAM_TARGET")
        or DEFAULT_RECIPIENT
    )
    # The comment beside DEFAULT_RECIPIENT says an empty target means no send is
    # ever ATTEMPTED, and the code did not implement that: it called
    # `notify("", line)` and left the decision to a module this file does not
    # own. Whether an unconfigured install fired a doomed API call every day
    # depended entirely on `telegram_notify` rejecting an empty chat id
    # internally. Make the comment true here, where it is written.
    if not recipient:
        _log("no telegram target configured (COUNCIL_MODELS_TELEGRAM_TARGET, "
             "OPS_RADAR_TELEGRAM_TARGET, ODIN_CADENCE_TELEGRAM_TARGET all "
             "unset) -- nudge not sent; /prime will backstop")
        return 0
    if not telegram_notify.notify(recipient, line):
        _log("nudge not delivered (see telegram_notify log); /prime will backstop")
        return 0

    _save_signature(signature)
    _log(f"nudge delivered to {recipient}: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
