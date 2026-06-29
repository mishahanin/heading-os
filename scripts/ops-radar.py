#!/usr/bin/env python3
"""ops-radar.py - the manual-actions + silent-health detector (Loop-Engineering).

Read-only by default. Aggregates the objective signals computed in
scripts/utils/ops_signals.py into a two-tier view:

  Tier A (machine-domain): ollama, memory-index. These SELF-HEAL (see `heal`)
    and stay SILENT unless auto-heal has failed >= AUTOHEAL_ESCALATE consecutive
    times - at which point a critical "auto-heal FAILED" line surfaces.
  Tier B (sovereign manual): backup, publish-to-fleet, weekly-review, cold-sweep,
    Odin collect/reflect. These surface an EXCEPTION-ONLY, COUNTS-ONLY nudge when
    objectively overdue and not suppressed.

Suppression: an `ack` silences a signal until its TTL expires OR its severity
band worsens; `crunch on` suppresses everything except the critical floor
(imminent data-loss / auto-heal failure) that always pierces.

Never auto-executes a Tier-B manual action; outbound sends stay human-gated. Data
I/O goes through the data-root helpers; state files are written atomically.

Usage:
    python3 scripts/ops-radar.py                  # detailed due-items view (or "all clear")
    python3 scripts/ops-radar.py --quiet          # counts-only one line; empty when nothing due
    python3 scripts/ops-radar.py --json           # machine-readable
    python3 scripts/ops-radar.py ack backup [--ttl 24h]
    python3 scripts/ops-radar.py crunch on|off
    python3 scripts/ops-radar.py heal             # Tier-A auto-heal (ollama/index)

Exit 0 always (a detector, not a gate).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Workspace import bootstrap (per development-standards.md)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils import ops_signals as ops  # noqa: E402
from scripts.utils.workspace import (  # noqa: E402
    get_data_root,
    get_outputs_dir,
    get_workspace_root,
    load_env,
)

# ============================================================
# Configuration
# ============================================================

TIER_A_TARGETS = ("ollama", "memory_index")
WEEKLY_KEYS = {"weekly_review", "odin_cadence", "publish"}  # default ack TTL 7d
DEFAULT_TTL_DAILY = 24 * 3600
DEFAULT_TTL_WEEKLY = 7 * 24 * 3600

ACK_FILE = "ack.json"
CRUNCH_FILE = "crunch.json"
AUTOHEAL_FILE = "autoheal.json"

# Known signal keys that `ack` will accept (plus the synthetic auto-heal keys).
KNOWN_KEYS = {
    "backup", "publish", "weekly_review", "cold_sweep", "odin_cadence",
    "queue", "ollama", "memory_index",
}


# ============================================================
# State (atomic JSON under outputs/operations/ops-radar/)
# ============================================================

def resolve_state_dir() -> Path:
    """Default state dir: outputs/operations/ops-radar/ under the data overlay."""
    return get_outputs_dir() / "operations" / "ops-radar"


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def parse_ttl(s: str | None, key: str) -> int:
    """Parse a TTL string (e.g. '24h', '7d', '3600s', '900') to seconds.

    None -> per-key default (weekly keys 7d, else 24h)."""
    if not s:
        return DEFAULT_TTL_WEEKLY if key in WEEKLY_KEYS else DEFAULT_TTL_DAILY
    s = s.strip().lower()
    try:
        if s.endswith("d"):
            return int(float(s[:-1]) * 86400)
        if s.endswith("h"):
            return int(float(s[:-1]) * 3600)
        if s.endswith("m"):
            return int(float(s[:-1]) * 60)
        if s.endswith("s"):
            return int(float(s[:-1]))
        return int(float(s))
    except ValueError:
        return DEFAULT_TTL_WEEKLY if key in WEEKLY_KEYS else DEFAULT_TTL_DAILY


# ============================================================
# Signal gathering
# ============================================================

def gather_live_signals(engine_root: Path, data_root: Path) -> list[dict]:
    """Compute every signal from live sources (read-only)."""
    return [
        ops.backup_state(engine_root, data_root),
        ops.publish_state(engine_root),
        ops.weekly_review_state(get_outputs_dir()),
        ops.cold_sweep_state(engine_root),
        ops.odin_cadence_state(engine_root),
        ops.queue_state(data_root),
        ops.ollama_state(),
        ops.index_freshness_state(engine_root, data_root),
    ]


def autoheal_signals(signals: list[dict], autoheal: dict) -> list[dict]:
    """Synthesize a critical 'auto-heal FAILED' signal for each Tier-A target
    that is due AND has failed auto-heal >= AUTOHEAL_ESCALATE consecutive times.

    A due Tier-A target below the escalation threshold produces NOTHING here
    (it stays silent - heal will retry). The synthetic signal is severity
    'critical' so it pierces crunch.
    """
    out: list[dict] = []
    by_key = {s["key"]: s for s in signals}
    for target in TIER_A_TARGETS:
        sig = by_key.get(target)
        if not sig or not sig["due"]:
            continue
        failures = int((autoheal.get(target) or {}).get("failures", 0))
        if failures >= ops.AUTOHEAL_ESCALATE:
            out.append({
                "key": f"{target}_autoheal",
                "value": {"failures": failures},
                "threshold": ops.AUTOHEAL_ESCALATE,
                "due": True,
                "severity": "critical",
                "tier": "A",
                "summary": f"auto-heal FAILED: {target} after {failures} tries",
            })
    return out


# ============================================================
# Suppression (ack + crunch)
# ============================================================

def ack_suppressed(sig: dict, ack: dict, now: float) -> bool:
    """True if `sig` is currently ack-silenced: an ack entry exists, is within
    TTL, and the signal's severity has not worsened past the acked band."""
    entry = ack.get(sig["key"])
    if not entry:
        return False
    acked_at = entry.get("acked_at", 0)
    ttl = entry.get("ttl_seconds", 0)
    if now >= acked_at + ttl:
        return False  # expired
    acked_band = entry.get("acked_band", "ok")
    return ops.severity_rank(sig["severity"]) <= ops.severity_rank(acked_band)


def select_candidates(signals: list[dict], autoheal: dict) -> list[dict]:
    """The set of signals eligible to surface BEFORE suppression:
      - every due Tier-B signal
      - the synthetic auto-heal-failure signals (escalated Tier-A only)
    Non-escalated Tier-A signals are intentionally excluded (machine domain)."""
    candidates = [s for s in signals if s["tier"] == "B" and s["due"]]
    candidates.extend(autoheal_signals(signals, autoheal))
    return candidates


def assess(engine_root: Path, data_root: Path, state_dir: Path,
           signals: list[dict] | None = None, autoheal: dict | None = None,
           now: float | None = None) -> dict:
    """Full read-only assessment. Returns the structured result the renderers and
    the notify entrypoint consume. `signals`/`autoheal`/`now` are injectable for
    tests; live values are read otherwise."""
    now = time.time() if now is None else now
    if signals is None:
        signals = gather_live_signals(engine_root, data_root)
    if autoheal is None:
        autoheal = load_json(state_dir / AUTOHEAL_FILE)
    ack = load_json(state_dir / ACK_FILE)
    crunch_on = bool(load_json(state_dir / CRUNCH_FILE).get("on"))

    candidates = select_candidates(signals, autoheal)
    displayed: list[dict] = []
    suppressed: list[dict] = []
    for sig in candidates:
        if ack_suppressed(sig, ack, now):
            suppressed.append({**sig, "suppressed_by": "ack"})
            continue
        if crunch_on and sig["severity"] != "critical":
            suppressed.append({**sig, "suppressed_by": "crunch"})
            continue
        displayed.append(sig)

    # Strongest first for the wire line.
    displayed.sort(key=lambda s: ops.severity_rank(s["severity"]), reverse=True)
    quiet_line = "; ".join(s["summary"] for s in displayed)
    return {
        "signals": signals,
        "candidates": candidates,
        "displayed": displayed,
        "suppressed": suppressed,
        "crunch_on": crunch_on,
        "quiet_line": quiet_line,
    }


# ============================================================
# Rendering
# ============================================================

def render_detailed(result: dict) -> str:
    lines: list[str] = []
    crunch = " [CRUNCH]" if result["crunch_on"] else ""
    displayed = result["displayed"]
    if not displayed:
        return f"ops-radar{crunch}: all clear - nothing due."
    lines.append(f"ops-radar{crunch}: {len(displayed)} item(s) due")
    for s in displayed:
        lines.append(f"  [{s['severity']:>8}] {s['summary']}")
    sup = result["suppressed"]
    if sup:
        lines.append(f"  ({len(sup)} suppressed: " +
                     ", ".join(f"{s['key']}/{s['suppressed_by']}" for s in sup) + ")")
    return "\n".join(lines)


# ============================================================
# Subcommands
# ============================================================

def cmd_ack(args, state_dir: Path, engine_root: Path, data_root: Path) -> int:
    key = args.key
    if key not in KNOWN_KEYS:
        print(f"ops-radar: unknown signal key {key!r}. Known: {', '.join(sorted(KNOWN_KEYS))}",
              file=sys.stderr)
        return 0
    # Acked band = the signal's CURRENT severity, so a later worsening re-surfaces.
    signals = gather_live_signals(engine_root, data_root)
    cur = next((s for s in signals if s["key"] == key), None)
    band = cur["severity"] if cur else "ok"
    ack = load_json(state_dir / ACK_FILE)
    ttl = parse_ttl(args.ttl, key)
    now = time.time()
    ack[key] = {"acked_at": now, "ttl_seconds": ttl, "acked_band": band}
    save_json_atomic(state_dir / ACK_FILE, ack)
    hrs = ttl / 3600
    print(f"ops-radar: ack {key} for {hrs:.0f}h (band={band}); re-surfaces on worsening or expiry.")
    return 0


def cmd_crunch(args, state_dir: Path) -> int:
    on = args.mode == "on"
    save_json_atomic(state_dir / CRUNCH_FILE, {"on": on, "since": time.time()})
    print(f"ops-radar: crunch {'ON' if on else 'OFF'}"
          + (" - Tier-B suppressed except the critical floor." if on else " - normal posture."))
    return 0


# ============================================================
# Tier-A auto-heal (ollama / memory-index)
# ============================================================

def record_heal_result(autoheal: dict, target: str, ok: bool) -> dict:
    """Pure: increment a target's consecutive-failure counter on failure, reset
    it to 0 on success. Returns a NEW dict (no in-place mutation)."""
    out = {k: dict(v) if isinstance(v, dict) else v for k, v in autoheal.items()}
    entry = dict(out.get(target) or {})
    entry["failures"] = 0 if ok else int(entry.get("failures", 0)) + 1
    out[target] = entry
    return out


def _systemctl(args: list[str]) -> bool:
    """Run `systemctl <args>`; True only on exit 0. A missing binary, an absent
    unit ('Unit not found'), or a denied privilege all return False (never a
    silent success) so the caller counts the heal as failed."""
    import subprocess
    try:
        proc = subprocess.run(["systemctl", *args], capture_output=True,
                              text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _ollama_reachable() -> bool:
    return bool(ops.ollama_state()["value"]["reachable"])


def _spawn_ollama_serve() -> bool:
    """Spawn `ollama serve` detached. True if the binary launched (present on
    PATH); False if absent. Reachability is re-probed by the caller."""
    import subprocess
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def restart_ollama() -> tuple[bool, str]:
    """Host-probed ollama restart. On THIS host ollama is a SYSTEM snap unit (a
    `systemctl --user restart ollama` fails 'Unit not found'); the managed system
    unit needs polkit/sudo and may be ungranted. Strategy, in order:

      1. managed user unit  (fleet-safe; absent here -> falls through)
      2. managed system unit (polkit; may be denied -> falls through)
      3. detached `ollama serve`, then re-probe

    Returns (ok, note). ok is True ONLY if ollama is reachable afterward, so a
    privilege denial or an absent unit is a real failure, not a no-op."""
    if _systemctl(["--user", "restart", "ollama"]) and _ollama_reachable():
        return True, "restarted via systemctl --user"
    if _systemctl(["restart", "ollama"]) and _ollama_reachable():
        return True, "restarted via systemctl (system unit)"
    if _spawn_ollama_serve():
        for _ in range(5):
            time.sleep(1)
            if _ollama_reachable():
                return True, "started via `ollama serve`"
    return False, "no managed unit restartable and `ollama serve` did not come up"


def rebuild_index(engine_root: Path) -> tuple[bool, str]:
    """Trigger an incremental memory-index build. (ok, note)."""
    import subprocess
    script = engine_root / "scripts" / "memory-index.py"
    if not script.exists():
        return False, "memory-index.py absent"
    try:
        proc = subprocess.run(
            ["python3", str(script), "build"],
            cwd=str(engine_root), capture_output=True, text=True, timeout=1800,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"build raised: {type(exc).__name__}"
    if proc.returncode == 0:
        return True, "memory-index rebuilt"
    return False, f"build exit {proc.returncode}"


def run_autoheal(state_dir: Path, engine_root: Path, data_root: Path,
                 signals: list[dict] | None = None,
                 restart_fn=None, rebuild_fn=None) -> dict:
    """Attempt Tier-A self-heal for due ollama/index; track consecutive failures.

    `signals`/`restart_fn`/`rebuild_fn` are injectable for tests. A healthy
    target resets its counter; a failed heal increments it (escalating into the
    Tier-B nudge at AUTOHEAL_ESCALATE)."""
    if signals is None:
        signals = gather_live_signals(engine_root, data_root)
    restart_fn = restart_fn or restart_ollama
    rebuild_fn = rebuild_fn or (lambda: rebuild_index(engine_root))
    autoheal = load_json(state_dir / AUTOHEAL_FILE)
    by_key = {s["key"]: s for s in signals}
    actions: list[dict] = []

    ol = by_key.get("ollama", {"due": False})
    ollama_up = not ol.get("due")
    if ol.get("due"):
        ok, note = restart_fn()
        autoheal = record_heal_result(autoheal, "ollama", ok)
        ollama_up = ok
        actions.append({"target": "ollama", "ok": ok, "note": note})
    else:
        autoheal = record_heal_result(autoheal, "ollama", True)

    idx = by_key.get("memory_index", {"due": False})
    if idx.get("due"):
        if ollama_up:
            ok, note = rebuild_fn()
            autoheal = record_heal_result(autoheal, "memory_index", ok)
            actions.append({"target": "memory_index", "ok": ok, "note": note})
        else:
            # cannot embed without the model up -> a real (counted) failure
            autoheal = record_heal_result(autoheal, "memory_index", False)
            actions.append({"target": "memory_index", "ok": False, "note": "skipped: ollama down"})
    else:
        autoheal = record_heal_result(autoheal, "memory_index", True)

    save_json_atomic(state_dir / AUTOHEAL_FILE, autoheal)
    return {"autoheal": autoheal, "actions": actions}


def cmd_heal(args, state_dir: Path, engine_root: Path, data_root: Path) -> int:
    result = run_autoheal(state_dir, engine_root, data_root)
    actions = result["actions"]
    if not actions:
        print("ops-radar heal: Tier-A healthy - nothing to heal.")
        return 0
    for a in actions:
        status = "ok" if a["ok"] else "FAILED"
        failures = result["autoheal"].get(a["target"], {}).get("failures", 0)
        print(f"ops-radar heal: {a['target']} {status} ({a['note']})"
              + (f" [{failures} consecutive failures]" if not a["ok"] else ""))
    return 0


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Manual-actions + silent-health detector.")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--quiet", action="store_true",
                    help="print a counts-only one-line summary; nothing when nothing is due")
    sub = ap.add_subparsers(dest="cmd")

    p_ack = sub.add_parser("ack", help="silence a signal until TTL or worsening")
    p_ack.add_argument("key", help="signal key (e.g. backup, cold_sweep)")
    p_ack.add_argument("--ttl", default=None, help="e.g. 24h, 7d (default per-signal)")

    p_crunch = sub.add_parser("crunch", help="toggle crunch-mode suppression")
    p_crunch.add_argument("mode", choices=["on", "off"])

    sub.add_parser("heal", help="Tier-A auto-heal (ollama / memory-index)")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    root = get_workspace_root()
    load_env(root)
    engine_root = root
    data_root = get_data_root()
    state_dir = resolve_state_dir()

    if args.cmd == "ack":
        return cmd_ack(args, state_dir, engine_root, data_root)
    if args.cmd == "crunch":
        return cmd_crunch(args, state_dir)
    if args.cmd == "heal":
        return cmd_heal(args, state_dir, engine_root, data_root)

    # Default: assess + render.
    result = assess(engine_root, data_root, state_dir)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0
    if args.quiet:
        if result["quiet_line"]:
            print(result["quiet_line"])
        return 0
    print(render_detailed(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
