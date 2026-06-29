#!/usr/bin/env python3
"""CEO-side fleet health reader for the bridge daemon.

Reads `<workspace>/.daemon-state/heartbeat.json` from the CEO workspace
and any exec workspaces synced under `../31c-exec-*/` or
`~/exec-workspaces/<slug>/`, then prints an N-cell status grid with
the daemon's posture per workspace.

Usage:
  python scripts/daemon-fleet-health.py
  python scripts/daemon-fleet-health.py --json       # machine-readable
  python scripts/daemon-fleet-health.py --stale 120  # custom stale threshold

Status conventions (matches spec section 3.7):
- ok: heartbeat within --stale seconds, no recent errors
- stale: heartbeat older than --stale seconds (default 120)
- version-mismatch: daemon version differs from this workspace's
  expected version (spec calls it 'config_loaded_version' drift)
- error: heartbeat parse failed OR recent_error_count > 0
- missing: heartbeat file does not exist

Phase 3 will plug this into a CEO dashboard surface; for now it's a
CLI so the CEO can run it from `/state-check` or as a cron.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.workspace import get_workspace_root

STALE_DEFAULT_S = 120

# Retired / non-fleet CEO clones that may still carry a stale
# `.daemon-state/heartbeat.json` as a sibling of the live engine workspace.
# The generic "any sibling with a heartbeat" discovery below would otherwise
# report them as perpetually-stale fleet members. They are not exec workspaces.
# - `ceo-main` retired at the 2026-06-15 two-part engine/data cutover.
# - `ceo-main-kimi` / `odin-heading-os` are dev scratch trees.
# The engine's own data sibling (`*-data`) is excluded by suffix: it holds no
# fleet daemon worth surfacing. Genuine execs use `31c-exec-*` / `31c-crm-*` /
# `~/exec-workspaces/<slug>` and are unaffected.
_NON_FLEET_SIBLINGS = frozenset({"ceo-main", "ceo-main-kimi", "odin-heading-os"})


def _is_non_fleet_sibling(name: str) -> bool:
    """True if a sibling dir is a retired CEO clone or the engine's data repo.

    Pure predicate (no filesystem) so it is unit-testable in isolation.
    """
    n = name.lower()
    return n in _NON_FLEET_SIBLINGS or n.endswith("-data")


def _candidate_workspaces() -> list[tuple[Path, str]]:
    """Return [(path, kind), ...] for the CEO workspace + exec mirrors.

    kind:
    - 'local': direct workspace path (heartbeat at <path>/.daemon-state/heartbeat.json)
    - 'crm-mirror': per-exec CRM repo (heartbeat at <path>/bridge-heartbeat.json,
      pushed by the exec's push-all.py)

    Conservative: doesn't recurse arbitrary directories.
    """
    ceo = get_workspace_root()
    out: list[tuple[Path, str]] = [(ceo, "local")]
    parent = ceo.parent
    if parent.is_dir():
        for child in sorted(parent.iterdir()):
            if not child.is_dir() or child == ceo:
                continue
            name = child.name.lower()
            # Skip retired CEO clones / the data sibling, even if a stale
            # heartbeat lingers (belt-and-braces; the file is also cleaned up).
            if _is_non_fleet_sibling(name):
                continue
            # Phase 1.162: per-exec CRM repos carry bridge-heartbeat.json
            # at the repo root (NOT inside .daemon-state/) because the
            # exec workspace's .daemon-state/ doesn't ship; only the
            # CRM repo does.
            if name.startswith("31c-crm-") and name != "31c-crm-central":
                if (child / "bridge-heartbeat.json").exists():
                    out.append((child, "crm-mirror"))
                continue
            # Local-style: an exec workspace cloned under the same parent.
            if (child / ".daemon-state" / "heartbeat.json").exists():
                out.append((child, "local"))
    home = Path.home() / "exec-workspaces"
    if home.is_dir():
        for child in sorted(home.iterdir()):
            if child.is_dir() and (child / ".daemon-state" / "heartbeat.json").exists():
                out.append((child, "local"))
    return out


def _read_heartbeat(workspace: Path, kind: str = "local") -> dict:
    """Return the heartbeat dict or a synthetic 'missing'/'error' record."""
    if kind == "crm-mirror":
        hb = workspace / "bridge-heartbeat.json"
    else:
        hb = workspace / ".daemon-state" / "heartbeat.json"
    if not hb.exists():
        return {
            "workspace": str(workspace),
            "status": "missing",
            "detail": f"{hb.name} does not exist",
        }
    try:
        data = json.loads(hb.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {
            "workspace": str(workspace),
            "status": "error",
            "detail": f"parse failed: {e}",
        }
    data["workspace"] = str(workspace)
    # Phase 1.162: surface mirror kind so the grid can show 'mirror' tag.
    data["kind"] = kind
    return data


def _read_corporate_config_version(workspace_root: Path) -> str | None:
    """Read corporate/daemon/config.yaml and return its `version:` field
    as a string, or None if the file is missing or unparseable.

    Phase 1.167: used by _classify() to flag execs whose daemons booted
    against an older corporate config version (the YAML file ships via
    /push-updates but the daemons hold their boot snapshot in memory
    until restart).
    """
    cfg_path = workspace_root / "corporate" / "daemon" / "config.yaml"
    if not cfg_path.is_file():
        return None
    try:
        import yaml
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    v = data.get("version")
    return str(v) if v is not None else None


def _classify(
    record: dict,
    stale_threshold_s: int,
    ceo_version: str | None,
    expected_config_version: str | None = None,
) -> str:
    """Return one of: ok | stale | version-mismatch | config-drift | error | missing.

    Phase 1.167 adds config-drift: heartbeat.config_loaded_version differs
    from the corporate repo's current daemon/config.yaml version. Distinct
    from version-mismatch (daemon CODE version drift) because the remedy
    is different: config-drift needs a daemon restart to pick up the new
    YAML; version-mismatch needs a code redeploy.
    """
    status = record.get("status")
    if status in ("missing", "error"):
        return status
    last_hb = record.get("last_heartbeat")
    if not last_hb:
        return "error"
    try:
        dt = datetime.fromisoformat(last_hb)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
    except ValueError:
        return "error"
    if age > stale_threshold_s:
        return "stale"
    if record.get("recent_error_count", 0) > 0:
        return "error"
    if ceo_version and record.get("version") and record["version"] != ceo_version:
        return "version-mismatch"
    if (
        expected_config_version
        and record.get("config_loaded_version")
        and record["config_loaded_version"] != expected_config_version
    ):
        return "config-drift"
    return "ok"


def _classify_beat(record: dict, stale_threshold_s: int) -> str:
    """Classify ONE per-daemon liveness beat (R14): ok | stale | error.

    Per-daemon beats (``.daemon-state/heartbeats/<name>.json``, written by
    ``daemon_heartbeat.beat``) carry only liveness, not the rich bridge fields,
    so there is no version-mismatch / config-drift dimension here - just age.
    A record that fails to parse or has no timestamp is ``error``. Absent
    daemons are NOT counted here (the watchdog owns expected-but-missing); this
    reconciles only over beats that are PRESENT, keeping the absent-heartbeats/
    path byte-identical to the legacy behaviour (scrutiny M3).
    """
    if record.get("status") == "error":
        return "error"
    last_hb = record.get("last_heartbeat")
    if not last_hb:
        return "error"
    try:
        dt = datetime.fromisoformat(last_hb)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
    except ValueError:
        return "error"
    return "stale" if age > stale_threshold_s else "ok"


def _collect_daemon_beats(workspace: Path, kind: str = "local") -> list[dict]:
    """Read the per-daemon liveness beats for a workspace, newest dir-listing
    order. Returns [] when ``heartbeats/`` is absent (back-compat) or for
    crm-mirror workspaces (mirrors carry only the rich bridge heartbeat)."""
    if kind != "local":
        return []
    beats_dir = workspace / ".daemon-state" / "heartbeats"
    if not beats_dir.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(beats_dir.glob("*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            out.append({"daemon": path.stem, "workspace": str(workspace),
                        "status": "error", "detail": f"parse failed: {e}"})
            continue
        rec["daemon"] = rec.get("daemon") or path.stem
        rec["workspace"] = str(workspace)
        out.append(rec)
    return out


def _format_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    return f"{int(seconds / 3600)}h"


def _verdict(
    records: Iterable[dict],
    stale_threshold_s: int,
    ceo_version: str | None,
    expected_config_version: str | None = None,
    beat_statuses: list[str] | None = None,
) -> tuple[str, str]:
    """One-line verdict + colour for the records.

    Returns (verdict_text, ansi_color). Use at the top of the grid output
    so a cron / grep pipeline sees the answer immediately without parsing
    the table.

    R14 (scrutiny M3): when ``beat_statuses`` (per-daemon liveness
    classifications) are supplied, the verdict is the WORST status across the
    legacy bridge records AND every present per-daemon beat - a green bridge
    with a stale sentinel reads as drift, not healthy. With no beats supplied
    the output is byte-identical to the legacy verdict.
    """
    records = list(records)
    beat_statuses = beat_statuses or []
    if not records and not beat_statuses:
        return ("No workspaces with heartbeat.json found.", GRAY)
    counts: dict[str, int] = {}
    for r in records:
        s = _classify(r, stale_threshold_s, ceo_version, expected_config_version)
        counts[s] = counts.get(s, 0) + 1
    for s in beat_statuses:
        counts[s] = counts.get(s, 0) + 1
    unit = "workspace/daemon" if beat_statuses else "workspace"
    if counts.get("error") or counts.get("missing"):
        bad = counts.get("error", 0) + counts.get("missing", 0)
        return (f"Fleet broken: {bad} {unit}(s) error or missing.", RED)
    drift_count = (
        counts.get("stale", 0)
        + counts.get("version-mismatch", 0)
        + counts.get("config-drift", 0)
    )
    if drift_count:
        return (
            f"Fleet drift: {drift_count} {unit}(s) stale, version-mismatch, or config-drift.",
            YELLOW,
        )
    return (f"Fleet healthy: {counts.get('ok', 0)} {unit}(s) ok.", GREEN)


def _print_grid(
    records: Iterable[dict],
    stale_threshold_s: int,
    ceo_version: str | None,
    expected_config_version: str | None = None,
    beats_by_ws: dict[str, list[dict]] | None = None,
) -> None:
    color_for = {
        "ok": GREEN,
        "stale": YELLOW,
        "version-mismatch": YELLOW,
        "config-drift": YELLOW,
        "error": RED,
        "missing": GRAY,
    }
    beats_by_ws = beats_by_ws or {}
    beat_statuses = [
        _classify_beat(b, stale_threshold_s)
        for beats in beats_by_ws.values() for b in beats
    ]
    records = list(records)
    verdict_text, verdict_color = _verdict(
        records, stale_threshold_s, ceo_version, expected_config_version, beat_statuses)
    print(f"{BOLD}{verdict_color}{verdict_text}{RESET}")
    print(
        f"{GRAY}stale threshold {stale_threshold_s}s · ceo version {ceo_version or '-'} · "
        f"corporate cfg v{expected_config_version or '-'}{RESET}"
    )
    print()
    print(f"  {'WORKSPACE':<40} {'STATUS':<18} {'UPTIME':<10} {'AGE':<8} {'VERSION':<10} {'SESS':<6}")
    print(f"  {'-' * 40} {'-' * 18} {'-' * 10} {'-' * 8} {'-' * 10} {'-' * 6}")
    counts: dict[str, int] = {}
    for r in records:
        s = _classify(r, stale_threshold_s, ceo_version, expected_config_version)
        counts[s] = counts.get(s, 0) + 1
        col = color_for.get(s, RESET)
        ws_name = Path(r.get("workspace", "?")).name[:40]
        age_str = "-"
        if r.get("last_heartbeat"):
            try:
                dt = datetime.fromisoformat(r["last_heartbeat"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_str = _format_age((datetime.now(timezone.utc) - dt).total_seconds())
            except ValueError:
                age_str = "?"
        uptime_str = "-"
        if isinstance(r.get("uptime_s"), int):
            uptime_str = _format_age(r["uptime_s"])
        version = r.get("version", "-")
        sessions = r.get("active_sessions", "-")
        print(f"  {ws_name:<40} {col}{s:<18}{RESET} {uptime_str:<10} {age_str:<8} {version:<10} {sessions:<6}")
        # When the daemon is alive but a source is erroring, surface the
        # first line of last_error so the operator sees WHICH source went
        # red without dumping heartbeat.json. The status flag conflates
        # "daemon alive" with "all sources healthy"; this line disambiguates.
        last_err = r.get("last_error")
        if s == "error" and isinstance(last_err, str) and last_err.strip():
            first_line = last_err.strip().splitlines()[0][:120]
            print(f"      {GRAY}-> {first_line}{RESET}")
        # R14: per-daemon liveness sub-rows (only when heartbeats/ is present).
        for beat in beats_by_ws.get(r.get("workspace", ""), []):
            bs = _classify_beat(beat, stale_threshold_s)
            counts[bs] = counts.get(bs, 0) + 1
            bcol = color_for.get(bs, RESET)
            bname = beat.get("daemon", "?")
            bage = "-"
            if beat.get("last_heartbeat"):
                try:
                    bdt = datetime.fromisoformat(beat["last_heartbeat"])
                    if bdt.tzinfo is None:
                        bdt = bdt.replace(tzinfo=timezone.utc)
                    bage = _format_age((datetime.now(timezone.utc) - bdt).total_seconds())
                except ValueError:
                    bage = "?"
            print(f"      {GRAY}└ {bname:<14}{RESET} {bcol}{bs:<10}{RESET} {GRAY}beat {bage}{RESET}")
    print()
    summary_bits = [f"{color_for.get(k, RESET)}{k}: {v}{RESET}" for k, v in sorted(counts.items())]
    print("  " + "  ".join(summary_bits) if summary_bits else "  no workspaces")


def _classify_fleet_exit_code(
    records, stale_threshold_s, ceo_version, expected_config_version: str | None = None,
    beat_statuses: list[str] | None = None,
) -> int:
    """Map fleet posture to a Unix-friendly exit code for cron / monitoring.

    Phase 1.163:
    - 0: fleet healthy (all workspaces ok, or empty fleet)
    - 1: fleet drift (any stale, version-mismatch, or config-drift)
    - 2: fleet broken (any error or missing record)

    Phase 1.167: config-drift joins the drift bucket (exit 1). Remedy is
    a daemon restart on the affected exec, which can be done out-of-hours.

    R14 (scrutiny M3): the exit code - consumed by `/bridge-health --gate` and
    cron - is the WORST status across the bridge records AND every present
    per-daemon liveness beat. A stale sentinel beat degrades a green bridge to
    exit 1, never a silent sub-row under an exit-0. With no beats the result is
    identical to the legacy exit code.

    Lets a cron task do `if /bridge-health; then ...` or
    `daemon-fleet-health.py || alert-pagerduty`.
    """
    statuses = {_classify(r, stale_threshold_s, ceo_version, expected_config_version) for r in records}
    statuses |= set(beat_statuses or [])
    if "error" in statuses or "missing" in statuses:
        return 2
    if "stale" in statuses or "version-mismatch" in statuses or "config-drift" in statuses:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a JSON report instead of the grid")
    parser.add_argument("--stale", type=int, default=STALE_DEFAULT_S,
                        help=f"seconds before a heartbeat is considered stale (default {STALE_DEFAULT_S})")
    parser.add_argument("--exit-zero", action="store_true",
                        help="always exit 0 (useful for CEO interactive runs; default returns 0/1/2)")
    args = parser.parse_args(argv)

    workspaces = _candidate_workspaces()
    records = [_read_heartbeat(w, kind) for (w, kind) in workspaces]
    # R14: per-daemon liveness beats, keyed by workspace path. Empty when no
    # workspace has a heartbeats/ dir, which keeps the legacy path unchanged.
    beats_by_ws: dict[str, list[dict]] = {}
    for (w, kind) in workspaces:
        beats = _collect_daemon_beats(w, kind)
        if beats:
            beats_by_ws[str(w)] = beats
    beat_statuses = [
        _classify_beat(b, args.stale)
        for beats in beats_by_ws.values() for b in beats
    ]
    # CEO version is the local workspace's version (first record when its
    # status is not missing/error).
    ceo_version = None
    for r in records:
        if r.get("status") in (None, "ok") and r.get("version"):
            ceo_version = r["version"]
            break
    # Phase 1.167: read the corporate config version from the CEO workspace.
    # Daemons that loaded an older version are flagged 'config-drift'.
    expected_config_version = _read_corporate_config_version(get_workspace_root())

    if args.json:
        verdict_text, _ = _verdict(
            records, args.stale, ceo_version, expected_config_version, beat_statuses)
        out = {
            "verdict": verdict_text,
            "stale_threshold_s": args.stale,
            "ceo_version": ceo_version,
            "expected_config_version": expected_config_version,
            "workspaces": [
                {**r, "classified": _classify(r, args.stale, ceo_version, expected_config_version)}
                for r in records
            ],
            "daemons": [
                {**b, "classified": _classify_beat(b, args.stale)}
                for beats in beats_by_ws.values() for b in beats
            ],
        }
        print(json.dumps(out, indent=2))
    else:
        _print_grid(records, args.stale, ceo_version, expected_config_version, beats_by_ws)

    if args.exit_zero:
        return 0
    return _classify_fleet_exit_code(
        records, args.stale, ceo_version, expected_config_version, beat_statuses)


if __name__ == "__main__":
    sys.exit(main())
