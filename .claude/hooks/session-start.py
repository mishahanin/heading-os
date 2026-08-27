#!/usr/bin/env python3
"""SessionStart hook: surface urgent CRM contacts and data freshness alerts."""
import sys
import json
import os
import re
import subprocess
from datetime import date, datetime, timedelta
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
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        # A half-finished setup that never gets its nudge because the status
        # probe times out looks identical to a finished one. Say so once.
        print(f"session-start: setup status unavailable "
              f"({exc.__class__.__name__}): {exc}", file=sys.stderr)
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


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _red_contacts(output: str) -> list:
    """The overdue CONTACT lines from crm-health.py output, not its header.

    The filter was `"RED" in line`, which matches the section HEADER
    `RED - Overdue` and nothing else: the contact lines below it carry a name, a
    company and a day count, never the word RED. So `len()` of the result was
    the number of headers, always 1, and the session banner read "CRM ALERT: 1
    contact(s) need attention today" whether one contact was overdue or forty.
    Measured 2026-08-23 against a three-overdue fixture: the hook reported 1.

    The substring was also unanchored, so a contact or company containing RED
    (REDACTED, REDMOND, a person called FRED) counted as an alert.

    Read the section instead: everything indented under the RED header, up to
    the blank line that ends it. ANSI codes are stripped, because crm-health.py
    colourizes unconditionally and the cached strings are shown to a human.
    """
    lines = _ANSI_RE.sub("", output).split("\n")
    contacts = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if not inside:
            if stripped.startswith("RED - "):
                inside = True
            continue
        if not stripped:
            break  # blank line ends the section
        if not line.startswith((" ", "\t")):
            break  # next section header, unindented
        contacts.append(stripped)
    return contacts


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
            if (datetime.now().astimezone().timestamp() - cached_at) < _CRM_CACHE_TTL_SECONDS:
                red_lines = cached.get("red_contacts") or []
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
            red_lines = _red_contacts(output)
            # Write cache (best-effort - never block on cache write failure)
            try:
                os.makedirs(cache_dir, mode=0o700, exist_ok=True)
                tmp_path = cache_file + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "cached_at": datetime.now().astimezone().timestamp(),
                        "red_contacts": red_lines,
                    }, f)
                # chmod the TEMP file, then replace. Doing it the other way
                # round left the cache at the default umask (commonly 0644)
                # between the replace and the chmod, and it holds CRM contact
                # lines. Found by the 2026-08-23 audit. os.replace preserves the
                # source file's mode, so the live file is 0o600 from the instant
                # it exists.
                # .sessions/ is a uniformly restricted store (SEC-006 / F-H2).
                os.chmod(tmp_path, 0o600)
                os.replace(tmp_path, cache_file)
            except Exception as e:
                print(f"[session-start] crm-health cache write failed: {e}", file=sys.stderr)
            if red_lines:
                return red_lines
        else:
            # `subprocess.run` does not raise on a non-zero exit, so the outer
            # handler never fired and this branch did not exist: a crm-health
            # run that failed produced no alert and no line on any stream. The
            # operator reads an absent CRM alert as "nothing is overdue" while
            # the red debt keeps growing. Every other failure path in this file
            # reports to stderr; this one is now one of them, and the operator
            # also gets an alert, because "the check did not run" is exactly the
            # thing a silent alarm must not hide.
            tail = (result.stderr or result.stdout or "").strip().splitlines()
            detail = tail[-1] if tail else "no output"
            print(f"[session-start] crm-health exited {result.returncode}: {detail}",
                  file=sys.stderr)
            return [f"CRM HEALTH CHECK DID NOT RUN (exit {result.returncode}: "
                    f"{detail}). Overdue contacts are UNKNOWN, not zero."]
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
    Returns list of (filename, days_old, severity) tuples. Severity is
    WARNING, CRITICAL, or NOT_CHECKED - the last one carrying the reason the
    scan could not run, with days_old 0.
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
        except Exception as e:  # noqa: BLE001 -- best-effort, never blocks start
            # The ONE handler in this file that logged nothing. `get_data_root`
            # raises DataRootError by design when HEADING_OS_DATA names a path
            # that has moved, and this caught it, silently pointed at
            # `<engine>/context` - which does not exist, because context/ is
            # DATA-routed - and returned an empty list. The file's own comment
            # elsewhere names the standard: a freshness alarm that fails toward
            # silence is the worst way for it to fail.
            print(f"[session-start] data-root resolve failed, staleness falls "
                  f"back to the engine tree: {e}", file=sys.stderr)
            context_dir = os.path.join(project_dir, "context")
    stale = []
    warn_threshold = datetime.now().astimezone() - timedelta(days=14)
    crit_threshold = datetime.now().astimezone() - timedelta(days=30)

    if not os.path.isdir(context_dir):
        # "nothing to check" and "could not look" are different answers, and the
        # second one was indistinguishable from the first.
        print(f"[session-start] no context directory at {context_dir}; staleness "
              f"was NOT checked.", file=sys.stderr)
        # A TUPLE, in this function's declared shape. It returned a bare string
        # until 2026-08-27, and the caller unpacks three values per item, so the
        # string was unpacked CHARACTER BY CHARACTER and `main()` died with
        # `ValueError: too many values to unpack (expected 3)`. Every session on
        # a workspace with no context/ directory - which is every fresh public
        # clone, and every engine-only checkout - printed a traceback at
        # SessionStart. No test saw it because they all ran against the
        # operator's own data root, which has a context/ directory.
        return [(f"no context directory at {context_dir}", 0, "NOT_CHECKED")]

    for fname in os.listdir(context_dir):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(context_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    if "Last verified:" in line or "last verified:" in line.lower():
                        for part in line.split():
                            # `.strip()` removes whitespace only, so a line
                            # ending "Last verified: 2026-06-01." yielded the
                            # token "2026-06-01." and strptime raised, silently
                            # skipping the file. A freshness alarm that fails
                            # toward silence is the worst way for it to fail.
                            # Found by the 2026-08-23 audit; latent, since no
                            # context file carries one today.
                            token = part.strip().strip(".,;:!?()[]{}<>\'\"\u2018\u2019\u201c\u201d")
                            try:
                                d = datetime.strptime(token, "%Y-%m-%d").replace(tzinfo=datetime.now().astimezone().tzinfo)
                                days_old = (datetime.now().astimezone() - d).days
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


# ============================================================
# Active threads panel
# ============================================================
#
# The `## Active Threads` block in the auto-memory index used to put the running
# set in front of the operator at every session start. It was retired on
# 2026-08-27 because it was a stale COPY: on its last day it listed 3 threads
# against 33 active on disk, and each row quoted a live status and a live date,
# which `.claude/rules/memory-discipline.md` forbids in an always-loaded index.
#
# What the operator lost with it was passive awareness, and that was worth
# keeping. This restores it from the RECORD instead of from a copy: the panel is
# computed from the thread files on every session start, so it cannot go stale,
# and nothing is written anywhere.
#
# Read-only. Console-first: `python scripts/thread.py list` is the same answer
# and stays the primary interface; this is a convenience surface over it.

THREAD_PANEL_DAYS = 14      # a thread untouched for longer is not "running"
THREAD_PANEL_ROWS = 12      # a panel longer than this is a wall, not a summary
THREAD_PANEL_UNREADABLE_NAMED = 3   # name the broken files, then say how many more


def _parse_thread_or_reason(parse_thread_file, path):
    """Parse one thread file. Returns (thread, None) or (None, reason).

    The failure is RETURNED to the caller rather than counted here. A handler
    that absorbed it would make a broken thread file invisible, and an invisible
    broken file is a thread silently missing from the panel.
    """
    try:
        return parse_thread_file(path), None
    except Exception as exc:  # noqa: BLE001 - returned, never dropped
        return None, f"{exc.__class__.__name__}: {exc}"


def _thread_panel_lines(project_dir):
    """Lines for the active-threads panel, or a one-line reason it is absent.

    Returns (lines, note). `note` is non-empty only when the panel could not be
    computed, and it is printed: silence about a check that did not run reads as
    "nothing to report", which is the failure this whole change exists to end.
    """
    root = Path(project_dir).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from scripts.utils.threads_lib import is_quiet, parse_thread_file
        from scripts.utils.workspace import get_default_tz, get_threads_dir
    except ImportError as exc:
        return [], f"active-threads panel unavailable ({exc.__class__.__name__}: {exc})"

    try:
        threads_root = get_threads_dir()
        today = datetime.now(get_default_tz()).date()
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        return [], f"active-threads panel unavailable ({exc.__class__.__name__}: {exc})"
    if not threads_root.is_dir():
        return [], ""

    active, quiet, unreadable = [], 0, []
    for type_ in ("business", "personal"):
        type_dir = threads_root / type_
        if not type_dir.is_dir():
            continue
        for path in sorted(type_dir.glob("*.md")):
            thread, reason = _parse_thread_or_reason(parse_thread_file, path)
            if thread is None:
                unreadable.append(f"{type_}/{path.name} ({reason})")
                continue
            if thread.status != "active":
                continue
            # A quiet thread must not be surfaced proactively. This panel is the
            # definition of proactive, so it is the first place that rule binds.
            if is_quiet(thread, today):
                quiet += 1
                continue
            try:
                age = (today - date.fromisoformat(thread.last_touched)).days
            except (TypeError, ValueError):
                age = None
            active.append((age, thread))

    if not active and not quiet and not unreadable:
        return [], ""

    # An unparseable date sorts first: it is a defect worth seeing, not a thread
    # worth burying at the bottom of a truncated list.
    active.sort(key=lambda row: (row[0] is not None, row[0] if row[0] is not None else 0))
    recent = [row for row in active if row[0] is None or row[0] <= THREAD_PANEL_DAYS]
    shown = recent[:THREAD_PANEL_ROWS]

    # Two different reasons a thread is absent, named separately. "20 more
    # active" collapsed them, and a reader cannot tell a thread that went quiet
    # for a month from one the row cap cut off this morning.
    older = len(active) - len(recent)
    head = f"Active threads: {len(active)} active"
    if quiet:
        head += f", {quiet} quiet"
    if unreadable:
        head += f", {len(unreadable)} unreadable"
    head += ". Showing "
    head += (f"{len(shown)} of {len(recent)}" if len(shown) < len(recent)
             else f"the {len(shown)}")
    head += f" touched in the last {THREAD_PANEL_DAYS} days"
    if older:
        head += f"; {older} older"
    head += "."
    lines = [head]
    for age, thread in shown:
        age_text = "no date" if age is None else ("today" if age == 0 else f"{age}d")
        lines.append(f"- {thread.type}/{thread.id} - {thread.title} ({age_text})")
    # A count says a thread is broken; the name says which one. Without it the
    # operator has to walk the registry by hand to find the file to repair.
    for name in unreadable[:THREAD_PANEL_UNREADABLE_NAMED]:
        lines.append(f"  Unreadable: {name}")
    if len(unreadable) > THREAD_PANEL_UNREADABLE_NAMED:
        lines.append(f"  Unreadable: and {len(unreadable) - THREAD_PANEL_UNREADABLE_NAMED} more")
    lines.append("  Full set: `python scripts/thread.py list`")
    return lines, ""


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[session-start] failed to parse input: {e}", file=sys.stderr)
        input_data = {}

    # A payload that is valid JSON but not an object still has `.get` called on
    # it. `[]`, `"x"` and `3` all parse, then raise an uncaught AttributeError.
    # Measured 2026-08-23 with `echo '[]' | python <hook>`: exit 1, traceback.
    # `.claude/hooks/checkpoint-inject.py` fixed this shape on 2026-08-20 and
    # these were missed. Degrade to the empty dict, which every path below
    # already handles, rather than dropping the hook's whole job.
    if not isinstance(input_data, dict):
        print(f"[session-start] payload was {type(input_data).__name__}, not an "
              "object; continuing with defaults", file=sys.stderr)
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
        not_checked = [f for f, _d, s in stale if s == "NOT_CHECKED"]
        if not_checked:
            # "could not look" is not "nothing is stale", and the alert has to
            # say which one it is.
            alerts.append("CONTEXT STALENESS NOT CHECKED: "
                          + "; ".join(not_checked))
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
                    # tmp + os.replace, per the global atomic-state-write rule.
                    # A crash mid-write left a truncated file, after which the
                    # read above raises forever and the notification can never
                    # be delivered. Found by the 2026-08-23 audit.
                    tmp_update = update_file + ".tmp"
                    with open(tmp_update, "w", encoding="utf-8") as f:
                        f.write(json.dumps(update, indent=2))
                    os.replace(tmp_update, update_file)
            except Exception as e:
                print(f"[session-start] workspace update notification failed: {e}", file=sys.stderr)

    if alerts:
        # PLAIN TEXT on stdout, which is what SessionStart injects.
        #
        # This wrote `{"additionalContext": ...}` as JSON, a key the SessionStart
        # schema does not define, onto the SAME stream that already carries the
        # setup banner printed at the top of this file. Whichever way the harness
        # reads that stream, the pair is wrong: as raw context the operator gets a
        # literal JSON blob, and as a single JSON document the banner breaks the
        # parse and NEITHER is delivered. The whole alert pipeline - sync failure,
        # corporate update, dependency marker, CRM red debt, stale context - then
        # exits 0 reporting success either way.
        #
        # Plain text is what the evidence supports: `checkpoint-inject.py` is a
        # registered SessionStart hook in this same directory, it prints raw
        # text, and its output demonstrably reaches the session. The one hook
        # here that uses the `hookSpecificOutput` wrapper, `memory-inject.py`, is
        # disabled and registered nowhere, so it is not evidence of a live path.
        print("Session alerts:")
        for alert in alerts:
            print(f"- {alert}")

    panel, panel_note = _thread_panel_lines(project_dir)
    if panel_note:
        print(f"[session-start] {panel_note}", file=sys.stderr)
    if panel:
        if alerts:
            print()
        for line in panel:
            print(line)

    sys.exit(0)


if __name__ == "__main__":
    main()
