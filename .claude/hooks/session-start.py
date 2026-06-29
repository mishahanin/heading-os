#!/usr/bin/env python3
"""SessionStart hook: surface urgent CRM contacts and data freshness alerts."""
import sys
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path


def _setup_wizard_banner(workspace_root):
    """Print a one-line setup-wizard banner if setup is incomplete. Gated on ceo-master.

    An ABSENT .workspace-identity.json resolves to ceo-master — the documented
    legacy default in scripts/utils/workspace.py:get_workspace_identity and in
    get_workspace_type() below. The banner MUST honour that same fallback:
    .workspace-identity.json is gitignored, so a fresh engine clone or a relocated
    workspace starts without it, and that absence means "legacy CEO master", never
    "unfinished exec setup". Suppress the banner on absent-file exactly as on an
    explicit ceo-master file; otherwise the wizard-status path fires phantom 0%.
    """
    if os.environ.get("CI") == "true" or os.environ.get("HEADING_OS_WIZARD_QUIET") == "1":
        return
    identity_path = workspace_root / ".workspace-identity.json"
    if not identity_path.exists():
        return
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if identity.get("type") == "ceo-master":
        return
    apply_script = workspace_root / "scripts" / "apply-wizard-answers.py"
    if not apply_script.exists():
        return
    try:
        result = subprocess.run(
            [sys.executable, str(apply_script), "--status"],
            cwd=workspace_root, capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return
        payload = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return
    pct = payload.get("completion_pct", 100)
    if pct >= 100:
        return
    print(f"[!] Workspace not fully set up ({pct}%). Type /setup-wizard to finish.\n")


def get_workspace_type(project_dir):
    """Read workspace identity to determine type."""
    identity_file = os.path.join(project_dir, ".workspace-identity.json")
    if os.path.isfile(identity_file):
        try:
            with open(identity_file, "r", encoding="utf-8") as f:
                return json.loads(f.read())
        except Exception as e:
            print(f"[session-start] get_workspace_type failed: {e}", file=sys.stderr)
    return {"role": "admin", "slug": "misha-hanin", "type": "ceo-master"}


def check_sync_status(project_dir, identity):
    """Check sync health for exec workspaces."""
    if identity.get("type") == "ceo-master":
        return None
    state_file = os.path.join(project_dir, ".sync", "state.json")
    if not os.path.isfile(state_file):
        return "SYNC: No sync state found. Run /sync to initialize."
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.loads(f.read())
        # Check last successful corporate pull
        corp = state.get("corporate_pull", {})
        last_success = corp.get("last_success", "")
        if last_success:
            last = datetime.fromisoformat(last_success.replace("Z", "+00:00"))
            hours_ago = (datetime.now(last.tzinfo) - last).total_seconds() / 3600
            if hours_ago > 24:
                return f"SYNC: Corporate content not updated in {int(hours_ago)} hours. Run /sync."
        failures = corp.get("consecutive_failures", 0)
        if failures >= 3:
            return f"SYNC: Corporate pull has failed {failures} times. Check network and run /sync."
    except Exception as e:
        print(f"[session-start] check_sync_status failed: {e}", file=sys.stderr)
    return None


_CRM_CACHE_TTL_SECONDS = 1800  # 30 minutes


def check_crm_health(project_dir):
    """Run CRM health check and extract RED contacts. Result cached for 30 minutes
    in .sessions/crm-health-cache.json to keep SessionStart fast."""
    script = os.path.join(project_dir, "scripts", "crm-health.py")
    if not os.path.isfile(script):
        return None

    cache_dir = os.path.join(project_dir, ".sessions")
    cache_file = os.path.join(cache_dir, "crm-health-cache.json")

    # Try cache first
    try:
        if os.path.isfile(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.loads(f.read())
            cached_at = cached.get("cached_at", 0)
            if (datetime.now().timestamp() - cached_at) < _CRM_CACHE_TTL_SECONDS:
                red_lines = cached.get("red_lines") or []
                return red_lines if red_lines else None
    except Exception as e:
        print(f"[session-start] crm-health cache read failed: {e}", file=sys.stderr)

    # Cache miss or stale - run the script
    try:
        result = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True, timeout=10,
            cwd=project_dir
        )
        if result.returncode == 0:
            output = result.stdout
            red_lines = [
                line.strip() for line in output.split("\n")
                if "RED" in line and line.strip()
            ]
            # Write cache (best-effort - never block on cache write failure)
            try:
                os.makedirs(cache_dir, mode=0o700, exist_ok=True)
                tmp_path = cache_file + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "cached_at": datetime.now().timestamp(),
                        "red_lines": red_lines,
                    }, f)
                os.replace(tmp_path, cache_file)
                # .sessions/ is a uniformly restricted store (SEC-006 / F-H2):
                # lock the cache to 0o600 so the live tree stays 0o600 across
                # session-start regenerations.
                os.chmod(cache_file, 0o600)
            except Exception as e:
                print(f"[session-start] crm-health cache write failed: {e}", file=sys.stderr)
            if red_lines:
                return red_lines
    except Exception as e:
        print(f"[session-start] check_crm-health failed: {e}", file=sys.stderr)
    return None


def check_corporate_updates(project_dir, identity):
    """Check if corporate content has been updated since last sync."""
    if identity.get("type") != "exec-workspace":
        return None

    version_file = os.path.join(project_dir, "corporate", "VERSION")
    state_file = os.path.join(project_dir, ".sync", "state.json")

    if not os.path.isfile(version_file):
        return None

    try:
        with open(version_file, "r", encoding="utf-8") as f:
            current_version = f.read().strip()
    except Exception as e:
        print(f"[session-start] check_corporate_updates version read failed: {e}", file=sys.stderr)
        return None

    if not os.path.isfile(state_file):
        return None

    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.loads(f.read())
        last_version = state.get("corporate_pull", {}).get("last_version", "")
        if last_version and last_version != current_version:
            return f"CORPORATE UPDATE: New content available (v{current_version}). Run /sync to update."
    except Exception as e:
        print(f"[session-start] check_corporate_updates state read failed: {e}", file=sys.stderr)

    return None


def check_dep_update_marker(project_dir, identity):
    """Check for pending dep-update marker; return banner string or None.

    Auto-clears stale markers (where corporate/requirements.txt is absent).

    Spec: docs/superpowers/specs/2026-04-27-layered-requirements-distribution-design.md
    """
    if identity.get("type") != "exec-workspace":
        return None

    marker = os.path.join(project_dir, ".sync", "dep-update-pending.json")
    if not os.path.isfile(marker):
        return None

    corp_req = os.path.join(project_dir, "corporate", "requirements.txt")
    if not os.path.isfile(corp_req):
        # Stale marker - corporate file gone. Auto-clear.
        try:
            os.remove(marker)
            return "DEP UPDATE: stale marker cleared (corporate/requirements.txt absent)."
        except OSError as e:
            print(f"[session-start] failed to clear stale dep marker: {e}", file=sys.stderr)
            return None

    return (
        "DEP UPDATE: New platform dependencies in corporate/requirements.txt. "
        "Run: pip install -r corporate/requirements.txt && "
        "python scripts/clear-dep-marker.py"
    )


def check_stale_files(project_dir, identity=None):
    """Check context files for staleness (>14 days since last verified).

    Two-tier alert:
      - WARNING (>14 days): data getting stale, should refresh soon
      - CRITICAL (>30 days): data unreliable, refresh urgently
    Returns list of (filename, days_old, severity) tuples.
    """
    # Use workspace-aware path for context directory
    if identity and identity.get("type") == "exec-workspace":
        context_dir = os.path.join(project_dir, "corporate", "context")
    else:
        # CEO: context/ lives under the DATA root (HEADING OS split). A session
        # launched from the engine clone has no context/ at project_dir, so resolve
        # via get_data_root() and fall back to project_dir for the in-tree case.
        try:
            sys.path.insert(0, project_dir)
            from scripts.utils.workspace import get_data_root
            context_dir = str(get_data_root() / "context")
        except Exception:  # noqa: BLE001 -- alerts are best-effort, never block start
            context_dir = os.path.join(project_dir, "context")
    stale = []
    warn_threshold = datetime.now() - timedelta(days=14)
    crit_threshold = datetime.now() - timedelta(days=30)

    if not os.path.isdir(context_dir):
        return stale

    for fname in os.listdir(context_dir):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(context_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    if "Last verified:" in line or "last verified:" in line.lower():
                        for part in line.split():
                            try:
                                d = datetime.strptime(part.strip(), "%Y-%m-%d")
                                days_old = (datetime.now() - d).days
                                if d < crit_threshold:
                                    stale.append((fname, days_old, "CRITICAL"))
                                elif d < warn_threshold:
                                    stale.append((fname, days_old, "WARNING"))
                                break
                            except ValueError:
                                continue
                        break
        except Exception as e:
            print(f"[session-start] check_stale_files error reading {fname}: {e}", file=sys.stderr)
            continue
    return stale


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[session-start] failed to parse input: {e}", file=sys.stderr)
        input_data = {}

    project_dir = input_data.get("cwd", os.getcwd())
    workspace_root = Path(project_dir)
    _setup_wizard_banner(workspace_root)

    identity = get_workspace_type(project_dir)
    alerts = []

    # Check sync status (exec workspaces only)
    sync_alert = check_sync_status(project_dir, identity)
    if sync_alert:
        alerts.append(sync_alert)

    # Check corporate updates (exec workspaces only)
    corp_alert = check_corporate_updates(project_dir, identity)
    if corp_alert:
        alerts.append(corp_alert)

    # Check dep-update marker (exec workspaces only)
    dep_alert = check_dep_update_marker(project_dir, identity)
    if dep_alert:
        alerts.append(dep_alert)

    # Check CRM health
    red_contacts = check_crm_health(project_dir)
    if red_contacts:
        alerts.append(f"CRM ALERT: {len(red_contacts)} contact(s) need attention today")

    # Check stale context files (two-tier: >14d warning, >30d critical)
    stale = check_stale_files(project_dir, identity)
    if stale:
        critical = [f"{f} ({d}d)" for f, d, s in stale if s == "CRITICAL"]
        warning = [f"{f} ({d}d)" for f, d, s in stale if s == "WARNING"]
        if critical:
            alerts.append(f"STALE DATA (CRITICAL): {', '.join(critical)} -- data unreliable, update urgently")
        if warning:
            alerts.append(f"STALE DATA (WARNING): {', '.join(warning)} -- approaching staleness, refresh soon")

    # Check for workspace update notification (exec workspaces only)
    if identity.get("type") == "exec-workspace":
        update_file = os.path.join(project_dir, ".sync", "last-update.json")
        if os.path.isfile(update_file):
            try:
                with open(update_file, "r", encoding="utf-8") as f:
                    update = json.loads(f.read())
                if not update.get("notified", True):
                    version = update.get("version", "?")
                    build = update.get("build", "?")
                    summary = update.get("summary", "")
                    applied = update.get("applied_at", "")[:16]
                    msg = f"WORKSPACE UPDATE: v{version} (build {build})"
                    if applied:
                        msg += f" applied at {applied}"
                    if summary:
                        msg += f" -- {summary}"
                    alerts.append(msg)
                    # Mark as notified
                    update["notified"] = True
                    with open(update_file, "w", encoding="utf-8") as f:
                        f.write(json.dumps(update, indent=2))
            except Exception as e:
                print(f"[session-start] workspace update notification failed: {e}", file=sys.stderr)

    if alerts:
        context = "Session alerts:\n" + "\n".join(f"- {a}" for a in alerts)
        json.dump({
            "additionalContext": context
        }, sys.stdout)

    sys.exit(0)


if __name__ == "__main__":
    main()
