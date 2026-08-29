"""Daemon liveness watchdog core (R14).

Classifies each daemon's per-daemon liveness beat
(``.daemon-state/heartbeats/<name>.json``, written by ``daemon_heartbeat.beat``)
against its configured cadence + grace, and routes a severity-tiered alert on a
missed beat with per-(daemon) dedup so a sustained outage does not spam.

This is the importable core (snake_case), called in-process by the bridge
daemon's ``_watchdog_job`` and wrapped by the ``scripts/daemon-watchdog.py``
CLI. It mirrors the ``cold_sweep_core`` / ``cold-sweep`` split so the daemon
never has to import a hyphenated module.

Console-first: it only reads files (heartbeats + a tiny dedup state file), so it
runs end to end with the bridge daemon down.

Known residual (scrutiny M4): the in-process watchdog runs *inside* the bridge
daemon, so a bridge-down event kills this push path with it. Bridge-down
detection therefore reverts to the ``daemon-fleet-health.py`` pull. The CLI here
still classifies the bridge from its on-disk beat when run standalone, which is
the manual mitigation.

CEO-only: alerts route to the CEO's Telegram via ``scripts/utils/alert.py``
(CEO-only during the spine prove-out).

Usage::

    from scripts import watchdog_core
    report = watchdog_core.check_once(workspace_root)
    # report == {"verdict": "ok"|"down", "daemons": [...], "alerts_fired": N, ...}
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.utils.timeparse import parse_iso
from scripts.utils.workspace import get_workspace_root

logger = logging.getLogger("x31c.watchdog")

# Defaults when config carries no per-daemon cadence.
DEFAULT_EXPECTED_S = 60
DEFAULT_GRACE_S = 120
DEFAULT_REALERT_MIN = 30

# The full fleet the spine knows about. This is the fallback expected set when a
# host declares no `daemon.watchdog.expect` scope. The fleet is split across
# hosts (see load_expected): the bridge runs on the CEO workspace; fireside and
# sentinel were migrated to the service host on 2026-05-23, and sync-exchange is
# the laptop's. A daemon in the *host's expected set* with no heartbeat file
# resolves to "missing" (a genuine down state), not silence.
#
# eval-drift was removed on 2026-08-03 with the daemon itself. A name kept here
# after its code is gone resolves "missing" forever, which is a genuine down
# state raised every ten minutes for a process nobody intends to run. A test now
# fails any name here with no installable unit template under
# scripts/templates/systemd/, which catches that drift in both directions.
EXPECTED_DAEMONS = ("bridge", "fireside", "sync-exchange", "sentinel")

HEARTBEATS_DIR = ".daemon-state/heartbeats"
LEGACY_BRIDGE_HEARTBEAT = ".daemon-state/heartbeat.json"
WATCHDOG_STATE_FILE = ".daemon-state/watchdog-state.json"


# ============================================================
# Time + formatting helpers
# ============================================================

def _now() -> datetime:
    return datetime.now(timezone.utc)


def format_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    return f"{int(seconds / 3600)}h"


# ============================================================
# Config cadence
# ============================================================

def load_expected(workspace_root: Path) -> tuple[str, ...]:
    """Return the daemons *this host's* watchdog should check.

    Reads ``daemon.watchdog.expect`` (a list of daemon names) from the merged
    bridge config. The fleet is split across hosts since the 2026-05-23 service-host
    migration: the CEO workspace runs only the bridge, while fireside and sentinel
    are supervised on the service host. A watchdog only sees heartbeat files on its
    own filesystem, so each host scopes itself to the daemons that actually beat
    there - otherwise the off-host daemons resolve "missing" and fire false
    criticals.

    When ``expect`` is present and non-empty, it is the authoritative scope.
    Absent or empty falls back to ``EXPECTED_DAEMONS`` (the full fleet) for
    back-compat with a single-host deployment. Config read is best-effort.
    """
    try:
        from scripts.bridge_daemon.config import load_config

        cfg = load_config(workspace_root) or {}
        raw = cfg.get("daemon", {}).get("watchdog", {}).get("expect")
        if isinstance(raw, list):
            names = tuple(str(x).strip() for x in raw if isinstance(x, str) and str(x).strip())
            if names:
                return names
    except Exception as exc:  # noqa: BLE001 - config read is best-effort; default below
        logger.debug("watchdog: expected-daemons config read failed: %s", exc)
    return EXPECTED_DAEMONS


def load_cadence(workspace_root: Path) -> dict[str, tuple[int, int]]:
    """Return ``{daemon: (expected_s, grace_s)}`` for this host's expected set.

    Scoped to ``load_expected(workspace_root)`` so a host only watches the
    daemons that beat on its own filesystem. Each expected daemon takes its
    ``daemon.watchdog.cadence.<name>`` entry, or the defaults when absent.
    Config read is best-effort: a missing/unreadable config yields all-default
    cadence over the expected set.
    """
    expected = load_expected(workspace_root)
    cfg: dict = {}
    try:
        from scripts.bridge_daemon.config import load_config

        cfg = load_config(workspace_root) or {}
    except Exception as exc:  # noqa: BLE001 - config read is best-effort; defaults below
        # Its two siblings in this file both log here; this one bound nothing
        # and said nothing. A daemon configured with a long cadence then silently
        # reverted to the 60s/120s defaults, was classified `silent`, and fired a
        # critical alert with no line anywhere explaining that the config was
        # never read.
        logger.debug("watchdog: cadence config read failed: %s", exc)
        cfg = {}
    # Every level, not just `daemon`. The chain sat outside the try above, so
    # `watchdog: off` -- one YAML typo turning a mapping into a scalar -- called
    # `.get` on a str, raised AttributeError out of here and out of
    # `check_once`, and landed in the bridge daemon's per-tick handler, which
    # logs and carries on. The 2-minute tick then did nothing forever while the
    # daemon reported itself healthy. That is exactly the failure `_seconds`
    # below was written to prevent, fixed for malformed VALUES and left open for
    # malformed CONTAINERS. The watchdog is the thing that notices silence; it
    # must not be the thing that goes silent.
    raw: dict = {}
    node = cfg
    for key in ("daemon", "watchdog", "cadence"):
        if node is None:
            break
        if not isinstance(node, dict):
            logger.warning(
                "watchdog: config path daemon.watchdog.cadence is malformed at "
                "%r (%s, expected a mapping); using default cadence for every "
                "daemon", key, type(node).__name__)
            node = None
            break
        node = node.get(key)
    if isinstance(node, dict):
        raw = node
    elif node is not None:
        logger.warning(
            "watchdog: daemon.watchdog.cadence is a %s, not a mapping; using "
            "default cadence for every daemon", type(node).__name__)
    def _seconds(entry: dict, key: str, default: int, name: str) -> int:
        """One cadence number, or the default.

        The docstring above promises "config read is best-effort", but a
        MALFORMED value broke that promise: `cadence.<name>.expected: ~` reached
        `int(None)` as a TypeError and a non-numeric string as a ValueError,
        neither caught. That raised out of `load_cadence`, out of `check_once`,
        and into the bridge daemon's per-tick handler, which logs and carries on
        -- so one typo in the config disabled the watchdog on every tick while
        the daemon kept reporting itself healthy. The watchdog is the thing that
        notices silence; it must not be the thing that goes silent.

        A bool is rejected before the int() call for a different reason. It does
        not raise: bool subclasses int, so YAML's `expected: true` reached
        `int(True)` and became a ONE-SECOND expected cadence. Every beat then
        looked overdue, the daemon classified `silent`, and the CEO was paged
        about a process that was running normally. A malformed value must fall
        back to the default, not to a number that fabricates an outage.
        """
        if isinstance(entry.get(key), bool):
            logger.warning(
                "watchdog: cadence.%s.%s is %r, not a number; using %ds",
                name, key, entry.get(key), default)
            return default
        try:
            return int(entry[key])
        except KeyError:
            return default
        except (TypeError, ValueError):
            logger.warning(
                "watchdog: cadence.%s.%s is %r, not a number; using %ds",
                name, key, entry.get(key), default)
            return default

    out: dict[str, tuple[int, int]] = {}
    for name in expected:
        entry = raw.get(name) if isinstance(raw, dict) else None
        if isinstance(entry, dict):
            expected_s = _seconds(entry, "expected", DEFAULT_EXPECTED_S, name)
            grace = _seconds(entry, "grace", DEFAULT_GRACE_S, name)
        else:
            expected_s, grace = DEFAULT_EXPECTED_S, DEFAULT_GRACE_S
        out[name] = (expected_s, grace)
    return out


def _realert_minutes(workspace_root: Path) -> int:
    try:
        from scripts.bridge_daemon.config import load_config

        cfg = load_config(workspace_root) or {}
        v = cfg.get("daemon", {}).get("watchdog", {}).get("realert_minutes")
        # `not isinstance(v, bool)` is load-bearing: bool subclasses int, and
        # YAML reads `realert_minutes: yes` / `true` as True, which passed the
        # numeric guard and returned 1. A one-MINUTE re-alert window turns a
        # sustained outage into a critical alert every minute, which is the
        # exact spam the dedup model exists to prevent.
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return int(v)
    except Exception as exc:  # noqa: BLE001 - best-effort; default below
        logger.debug("watchdog: realert-minutes config read failed: %s", exc)
    return DEFAULT_REALERT_MIN


# ============================================================
# Heartbeat read + classify
# ============================================================

def _read_beat(workspace_root: Path, name: str) -> dict | None:
    """Read a per-daemon liveness beat. Bridge falls back to its legacy
    rich heartbeat.json when the per-daemon file is absent."""
    path = workspace_root / HEARTBEATS_DIR / f"{name}.json"
    if not path.exists() and name == "bridge":
        path = workspace_root / LEGACY_BRIDGE_HEARTBEAT
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        # UnicodeError belongs here for the same reason the non-dict branch
        # below exists. `read_text(encoding="utf-8")` raises UnicodeDecodeError
        # on undecodable bytes, and that is a ValueError, neither an OSError nor
        # a JSONDecodeError (which only fires on text that DECODED and then
        # failed to parse). A torn write, a restore through a mangling tool or
        # any byte corruption therefore escaped this handler, escaped
        # `check_once`, and landed in the bridge daemon's blanket per-tick
        # handler, which logs and carries on. One corrupt beat file stopped the
        # WHOLE fleet being classified on every tick while the daemon reported
        # itself healthy. `classify`'s docstring says a beat with no parseable
        # timestamp is `missing`; for invalid UTF-8 that was not true.
        return None
    if not isinstance(data, dict):
        # Valid JSON, wrong shape. A beat file holding an array, a string or a
        # number reached `.get` in `_age_seconds` as an AttributeError, which
        # escaped `check_once` into the daemon's blanket per-tick handler and
        # took the WHOLE fleet's liveness classification down every tick, not
        # just this one daemon's. `classify`'s docstring already says a file
        # with no parseable timestamp is `missing`; this makes that true.
        logger.warning("watchdog: %s holds a %s, not an object; treating the "
                       "beat as missing", path, type(data).__name__)
        return None
    return data


def _age_seconds(record: dict | None, now: datetime) -> float | None:
    # `isinstance`, not truthiness. `classify` promises `missing` for "a file
    # with no parseable timestamp", and a non-empty non-dict has none; the old
    # falsy test let `[]` and `{}` through correctly and passed `[1, 2]` and
    # `"x"` straight into `.get`. `_read_beat` now filters those too, so this is
    # the second line of defence for a caller that builds a record itself.
    if not isinstance(record, dict) or not record:
        return None
    dt = parse_iso(record.get("last_heartbeat"))
    if dt is None:
        return None
    return (now - dt).total_seconds()


def classify(record: dict | None, threshold_s: float, now: datetime) -> str:
    """Return ``ok`` | ``silent`` | ``missing`` for one daemon's beat.

    - ``missing``: no heartbeat file, or a file with no parseable timestamp.
    - ``silent``: a beat older than ``threshold_s``.
    - ``ok``: a beat within ``threshold_s``.
    """
    if record is None:
        return "missing"
    age = _age_seconds(record, now)
    if age is None:
        return "missing"
    return "silent" if age > threshold_s else "ok"


# ============================================================
# Dedup state
# ============================================================

def _load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state_path: Path, state: dict) -> None:
    """Atomically persist the dedup state. Best-effort: never raises."""
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(state_path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(state, indent=2) + "\n")
            os.replace(tmp, state_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as e:
        logger.warning("watchdog state write failed: %s", e)


# ============================================================
# One pass
# ============================================================

def _default_alert():
    from scripts.utils import alert

    return alert.alert


def _delivered(result) -> bool:
    """True when an alert reached a channel a human reads.

    `alert()` returns `{"telegram": bool, "card": bool, "log": bool}`. `log` is
    always True and is NOT a channel for this purpose - a log line in a file
    nobody is watching is what "the watchdog fired and nobody came" looks like.

    An injected `alert_fn` may return anything, including None. An unrecognised
    return is treated as DELIVERED rather than undelivered, so a test double or
    a third-party callable cannot inflate the undelivered count into a false
    alarm about the alerting path itself. The claim this function makes is
    narrow on purpose: it reports what `alert()` reported, and nothing when
    `alert()` was not what ran.
    """
    if not isinstance(result, dict):
        return True
    return any(v for k, v in result.items() if k != "log")


def check_once(
    workspace_root: Path,
    *,
    now: datetime | None = None,
    alert_fn=None,
    state_path: Path | None = None,
    cadence: dict[str, tuple[int, int]] | None = None,
    realert_min: int | None = None,
    threshold_override: float | None = None,
) -> dict:
    """Run one watchdog pass: classify each configured daemon, fire deduped
    alerts on a missed beat, persist dedup state, and return a report.

    Dedup model (Design Decisions 6-7):
    - a daemon going ok -> down fires one critical alert immediately;
    - while down, it re-alerts only after ``realert_min`` minutes;
    - a daemon going down -> ok fires one ``info`` "resumed" alert.

    Args are injectable so tests drive the clock, the alert sink, the state
    file, and the cadence without touching the live workspace or Telegram.
    """
    now = now or _now()
    if alert_fn is None:
        alert_fn = _default_alert()
    if state_path is None:
        state_path = workspace_root / WATCHDOG_STATE_FILE
    if cadence is None:
        cadence = load_cadence(workspace_root)
    if realert_min is None:
        realert_min = _realert_minutes(workspace_root)
    realert_window = timedelta(minutes=realert_min)

    state = _load_state(state_path)
    daemons: list[dict] = []
    # `alerts_fired` counts alerts RAISED. `alerts_undelivered` counts the ones
    # that reached no channel beyond the log, which is the number that decides
    # whether a human learned about a dead daemon. `alert()` has always returned
    # `{"telegram": bool, "card": bool, "log": bool}` naming what actually
    # fired, and both call sites below discarded it - so "3 alert(s) fired"
    # could mean three alerts that reached nobody, and the grid printed the word
    # "fired" over it. That is the `.claude/rules/scope-claims.md` shape: the
    # method counted attempts, the sentence asserted delivery.
    alerts_fired = 0
    alerts_undelivered = 0

    for name, (expected, grace) in cadence.items():
        threshold = threshold_override if threshold_override is not None else (expected + grace)
        record = _read_beat(workspace_root, name)
        status = classify(record, threshold, now)
        age = _age_seconds(record, now)
        prev = state.get(name) if isinstance(state.get(name), dict) else {}
        prev_state = prev.get("state", "ok")
        last_alert = parse_iso(prev.get("last_alert_ts"))
        fired = False

        if status in ("silent", "missing"):
            should_alert = (
                prev_state != "down"
                or last_alert is None
                or (now - last_alert) >= realert_window
            )
            if should_alert:
                if status == "missing":
                    summary = f"daemon {name} missing - no heartbeat"
                else:
                    summary = f"daemon {name} silent {format_age(age or 0)}"
                detail = (
                    f"expected a liveness beat within {threshold}s; "
                    f"watchdog at {now.isoformat()}"
                )
                delivered = _delivered(
                    alert_fn("critical", summary, detail, source="watchdog"))
                fired = True
                alerts_fired += 1
                if not delivered:
                    alerts_undelivered += 1
                state[name] = {
                    "state": "down",
                    "condition": status,
                    "last_alert_ts": now.isoformat(),
                }
            else:
                # Still down, within the re-alert window: keep the prior stamp.
                state[name] = {
                    "state": "down",
                    "condition": status,
                    "last_alert_ts": prev.get("last_alert_ts"),
                }
        else:  # ok
            if prev_state == "down":
                # Recovery is `info`, which `alert()` routes to the log ONLY by
                # design. It is deliberately not counted as undelivered: a
                # severity that has no other channel cannot fail to use one.
                alert_fn(
                    "info",
                    f"daemon {name} heartbeat resumed",
                    f"recovered at {now.isoformat()}",
                    source="watchdog",
                )
                fired = True
                alerts_fired += 1
            state[name] = {"state": "ok"}

        daemons.append({
            "daemon": name,
            "status": status,
            "age_s": int(age) if isinstance(age, (int, float)) else None,
            "threshold_s": threshold,
            "fired": fired,
        })

    _save_state(state_path, state)
    verdict = "down" if any(d["status"] != "ok" for d in daemons) else "ok"
    return {
        "verdict": verdict,
        "checked_at": now.isoformat(),
        "alerts_fired": alerts_fired,
        "alerts_undelivered": alerts_undelivered,
        "daemons": daemons,
    }
