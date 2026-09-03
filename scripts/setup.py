#!/usr/bin/env python3
"""
setup.py -- Bootstrap script for 31C executive workspaces.
===========================================================
Run once after cloning your workspace repo to set up repos, sync corporate
content, register with CRM central, and install scheduled sync.

Self-contained at import: the module top level uses only the Python 3.11+
standard library (plus local ANSI color constants), so it loads cleanly on a
fresh clone before sys.path is configured. The one workspace utility it needs
(scripts/utils/schedule) is imported lazily inside the function that needs it,
which calls `_ensure_workspace_importable()` first.
Idempotent: safe to re-run -- completed steps are skipped.
Cross-platform: Windows, macOS, Linux.

Usage:
    python scripts/setup.py
"""

import argparse
import getpass
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

# ============================================================
# Configuration
# ============================================================

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# GITHUB_ORG used to be computed here and read NOWHERE in this module. The block
# that produced it also mutated sys.path at import time and called into the
# workspace seam, which can raise types its `except` did not name -- so a module
# whose docstring promises "self-contained at import" could die on import, for a
# value nothing used. `sync-corporate.py` resolves the org itself, where it is
# actually needed.
#
# CORPORATE_REPO STAYS, and is not dead: it is the canonical corporate repo name
# for the whole workspace, and `tests/test_docs_name_the_corporate_repo_the_code_uses.py`
# reads this literal out of this file to check every doc against it. Deleting it
# unanchors that guard, which is how it was caught.
CORPORATE_REPO = "heading-os-corporate"

# Colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

PREREQUISITES = {
    "git": {"check": [["git", "--version"]], "install": "https://git-scm.com/downloads"},
    "python3": {"check": [["python3", "--version"], ["python", "--version"], ["py", "--version"]], "install": "https://www.python.org/downloads/"},
    "node": {"check": [["node", "--version"]], "install": "https://nodejs.org/"},
    "gh": {"check": [["gh", "--version"]], "install": "https://cli.github.com/"},
    "claude": {"check": [["claude", "--version"]], "install": "https://docs.anthropic.com/en/docs/claude-code"},
}

# State file for idempotency
STATE_FILE = WORKSPACE_ROOT / ".sync" / "setup-state.json"


# ============================================================
# Helpers / Output Utilities
# ============================================================

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def print_banner():
    """Print the setup banner."""
    print(f"\n{BOLD}{CYAN}{'=' * 56}{RESET}")
    print(f"{BOLD}{CYAN}  31C HEADING OS Workspace Setup  v{VERSION}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 56}{RESET}\n")


def ok(msg: str):
    print(f"  {GREEN}[ok]{RESET}    {msg}")


def skip(msg: str):
    print(f"  {GREEN}[skip]{RESET}  {msg}")


def warn(msg: str):
    print(f"  {YELLOW}[warn]{RESET}  {msg}")


def fail(msg: str):
    print(f"  {RED}[FAIL]{RESET}  {msg}")


def step_header(num: int, title: str):
    print(f"\n{BOLD}Step {num}: {title}{RESET}")


def _ensure_workspace_importable():
    """Put the workspace ROOT on sys.path, for the lazy `scripts.*` imports.

    Called from inside the functions that import a workspace module, never at
    module import time: the docstring promises a stdlib-only import surface on
    a fresh clone, and an import-time seam call is what was removed above.

    Without it the documented launch `python scripts/setup.py` puts only
    `<repo>/scripts` on sys.path, so `import scripts` has no anchor and step 11
    died with an unhandled ModuleNotFoundError. It passed review because this
    repo's own .venv carries an editable .pth that makes the root importable --
    and that venv is exactly what a fresh exec clone does not have, since step
    10 is what creates it.
    """
    root = str(WORKSPACE_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def run_cmd(cmd: list, cwd: str = None, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess command."""
    return subprocess.run(
        cmd, cwd=cwd, check=check,
        capture_output=capture, text=True,
    )


# ============================================================
# State Management
# ============================================================

# ---------------------------------------------------------------------------
# State management (idempotency)
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """Load setup state for idempotency, with both required keys guaranteed.

    A file that PARSES is not a file that fits, and this one is operator-visible
    at `.sync/setup-state.json`. Until 2026-08-25 whatever `json.loads` returned
    was handed straight back, and the very next line of `main` is
    `state["started_at"]`: `{}` raised KeyError, a JSON list raised TypeError,
    and either killed the wizard on the run whose whole point is to resume an
    interrupted one. The `is_done` / `mark_done` pair made it worse by being
    inconsistent - `is_done` uses `.get`, so a file with no `completed_steps`
    survived the early checks and died later, inside the first `mark_done`,
    after the identity and prerequisite steps had already taken effect.
    """
    loaded = None
    if STATE_FILE.exists():
        try:
            loaded = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            # `UnicodeDecodeError` is a SIBLING of `json.JSONDecodeError` under
            # `ValueError`, not a subclass, and it comes out of `read_text`
            # before `json.loads` is handed anything at all. So the one
            # corruption this function most has to survive walked past it.
            #
            # The truncated-write case is not hypothetical here: `save_state`
            # became atomic only in this same shard, and a state file left by
            # the older non-atomic write can be cut mid-UTF-8-sequence.
            # MEASURED 2026-09-01 on `b'{"completed_steps": ["ident\\xc3'`: a
            # raw UnicodeDecodeError out of `load_state`, which is called on
            # `main`'s first line - so the wizard died on the run whose whole
            # purpose is to resume an interrupted one, which is the failure the
            # docstring above describes and the shard was opened for.
            loaded = None
    state = {"completed_steps": [], "started_at": None}
    if isinstance(loaded, dict):
        for key, default in state.items():
            loaded.setdefault(key, default)
        if not isinstance(loaded["completed_steps"], list):
            # A wrong TYPE subscripts and appends as badly as a missing key.
            loaded["completed_steps"] = []
        return loaded
    return state


def save_state(state: dict):
    """Save setup state."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    # Atomic, written inline with stdlib only: a crash mid-write left truncated
    # JSON, `load_state` swallowed the parse error, and the idempotency
    # mechanism reset all progress -- at the one moment it exists to help, an
    # interrupted setup. `scripts.utils.atomic` is deliberately NOT imported:
    # this module's docstring promises a self-contained stdlib import surface,
    # and the sys.path mutation that would need was just removed.
    tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def mark_done(state: dict, step: str):
    """Mark a step as completed."""
    if step not in state["completed_steps"]:
        state["completed_steps"].append(step)
    save_state(state)


def is_done(state: dict, step: str) -> bool:
    """Check if a step is already completed."""
    return step in state.get("completed_steps", [])


# ============================================================
# Wizard Steps: Identity & Prerequisites
# ============================================================

# ---------------------------------------------------------------------------
# Step 1: Banner (always shown)
# ---------------------------------------------------------------------------

def step_banner():
    """Print the setup banner."""
    print_banner()


# ---------------------------------------------------------------------------
# Step 2: Detect workspace identity
# ---------------------------------------------------------------------------

def step_detect_identity(state: dict) -> dict:
    """Read .workspace-identity.json and return identity dict."""
    step_header(2, "Detecting workspace identity")

    identity_file = WORKSPACE_ROOT / ".workspace-identity.json"
    if not identity_file.exists():
        fail("This doesn't look like a provisioned 31C workspace.")
        fail(f"Missing: {identity_file}")
        print(f"\n  {YELLOW}If you just cloned the repo, the admin needs to provision")
        print(f"  your workspace first using provision-exec.py.{RESET}")
        sys.exit(1)

    try:
        identity = json.loads(identity_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        # `UnicodeDecodeError` for the same reason `load_state` carries it: it
        # is a SIBLING of `json.JSONDecodeError` under `ValueError` and is
        # raised by `read_text` before `json.loads` sees anything. Without it an
        # undecodable identity file answered a traceback instead of the named
        # `Cannot read` above, on step 1 of the wizard.
        fail(f"Cannot read .workspace-identity.json: {e}")
        sys.exit(1)

    slug = identity.get("slug", "unknown")
    ws_type = identity.get("type", "unknown")
    ok(f"Workspace: {slug} (type: {ws_type})")
    return identity


# ---------------------------------------------------------------------------
# Step 3: Check prerequisites
# ---------------------------------------------------------------------------

def step_check_prerequisites(state: dict) -> bool:
    """Verify all required tools are installed."""
    if is_done(state, "check_prerequisites"):
        step_header(3, "Checking prerequisites")
        skip("Prerequisites already verified")
        return True

    step_header(3, "Checking prerequisites")
    all_ok = True

    for tool, info in PREREQUISITES.items():
        found = False
        for candidate in info["check"]:
            resolved = shutil.which(candidate[0])
            if resolved is None:
                continue
            try:
                result = run_cmd([resolved] + candidate[1:], check=False)
                if result.returncode == 0:
                    version = (result.stdout or result.stderr).strip().split("\n")[0]
                    ok(f"{tool}: {version}")
                    found = True
                    break
            except (FileNotFoundError, OSError):
                continue
        if not found:
            fail(f"{tool}: NOT FOUND")
            print(f"         Install from: {CYAN}{info['install']}{RESET}")
            all_ok = False

    if not all_ok:
        print(f"\n{RED}ERROR: Missing prerequisites. Install them and re-run setup.{RESET}")
        sys.exit(1)

    mark_done(state, "check_prerequisites")
    return True


# ---------------------------------------------------------------------------
# Step 4: Verify GitHub auth
# ---------------------------------------------------------------------------

def step_verify_github_auth(state: dict) -> bool:
    """Verify gh CLI is authenticated."""
    if is_done(state, "verify_github_auth"):
        step_header(4, "Verifying GitHub authentication")
        skip("GitHub auth already verified")
        return True

    step_header(4, "Verifying GitHub authentication")

    try:
        result = run_cmd(["gh", "auth", "status"], check=False)
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode == 0:
            ok("GitHub CLI authenticated")
        else:
            fail("GitHub CLI is not authenticated")
            print(f"\n  {YELLOW}Run the following to authenticate:{RESET}")
            print(f"    gh auth login")
            print(f"\n  Choose: GitHub.com -> HTTPS -> Yes -> Login with browser")
            sys.exit(1)
    except (FileNotFoundError, OSError):
        fail("Cannot run gh auth status")
        sys.exit(1)

    mark_done(state, "verify_github_auth")
    return True


# ============================================================
# Wizard Steps: Environment & Repo Cloning
# ============================================================

# ---------------------------------------------------------------------------
# Step 5: Setup .env
# ---------------------------------------------------------------------------

def step_setup_env(state: dict) -> bool:
    """Create .env with API key if it doesn't exist."""
    step_header(5, "Setting up .env")

    env_file = WORKSPACE_ROOT / ".env"
    if env_file.exists():
        skip(".env already exists")
        return True

    print(f"\n  {CYAN}Your workspace needs an Anthropic API key for Claude features.{RESET}")
    print(f"  Get one at: https://console.anthropic.com/settings/keys\n")

    api_key = ""
    try:
        # getpass, not input: a key typed at input() is echoed to the terminal,
        # so it lands in the scrollback, in any recording of the session, and in
        # the transcript when this runs under an agent. It is imported at the top
        # of this file and was, until 2026-08-23, never used.
        api_key = getpass.getpass("  Enter your ANTHROPIC_API_KEY (input hidden, "
                                  "or press Enter to skip): ").strip()
    except EOFError:
        # KeyboardInterrupt is NOT caught here. Swallowing it wrote a keyless
        # .env and ran ten more steps, so the user trying to bail out got a
        # half-configured workspace -- and because .env then "already exists",
        # the key prompt never appeared again. `main` has a handler that says
        # "re-run to continue"; this let nothing reach it.
        print()

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    env_content = textwrap.dedent(f"""\
        # 31C Workspace Environment
        # Generated {date_str}

        # Anthropic API (required for Claude features)
        ANTHROPIC_API_KEY={api_key}

        # Telegram (optional)
        TELEGRAM_API_ID=
        TELEGRAM_API_HASH=

        # GitHub token (usually handled by gh CLI)
        # GITHUB_TOKEN=
    """)

    # Created 0600 FROM BIRTH, not chmod'd afterwards. `write_text` applies the
    # process umask (commonly 0644), so the API key sat world-readable for the
    # window between the write and the chmod -- and the write was not atomic
    # either, so a concurrent reader could see a partial key.
    if platform.system() != "Windows":
        fd = os.open(str(env_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(env_content)
        env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    else:
        # Windows uses ACLs, not POSIX modes; the mode argument is ignored there.
        env_file.write_text(env_content, encoding="utf-8")
    if api_key:
        ok(".env created with API key")
    else:
        warn(".env created without API key (add it later)")

    return True


# ---------------------------------------------------------------------------
# Step 6: Clone .corporate-repo/
# ---------------------------------------------------------------------------

def step_clone_corporate(state: dict) -> bool:
    """Clone or update the corporate content clone (.corporate-repo/).

    Delegates to scripts/sync-corporate.py -- the single implementation of the
    corporate clone/pull (M4 reconciliation, 2026-06-26). The exec workspace reads
    corporate content directly from .corporate-repo/ via get_corporate_root();
    nothing is copied into the workspace (see step_corporate_sync, now a no-op)."""
    if is_done(state, "clone_corporate"):
        step_header(6, "Corporate repo")
        skip("Corporate repo already set up")
        return True

    step_header(6, "Cloning corporate repo")
    try:
        result = run_cmd(
            [sys.executable, str(WORKSPACE_ROOT / "scripts" / "sync-corporate.py")],
            cwd=str(WORKSPACE_ROOT), check=False,
        )
        if result.returncode == 0:
            ok(".corporate-repo/ ready (clone/pull via sync-corporate.py)")
            mark_done(state, "clone_corporate")
        else:
            # NOT marked done. Marking a failed clone complete meant the
            # "idempotent, safe to re-run" promise actively prevented recovery:
            # the workspace never got .corporate-repo/, and the only way to
            # retry was to delete .sync/setup-state.json by hand.
            warn("sync-corporate.py reported an issue -- NOT marking this step "
                 "done; re-run setup to retry the clone")
    except (FileNotFoundError, OSError) as e:
        warn(f"Could not run sync-corporate.py: {e} -- NOT marking this step done")
    return True


# ---------------------------------------------------------------------------
# Step 7: Clone .crm-central-repo/
# ---------------------------------------------------------------------------

def step_clone_crm_central(state: dict, identity: dict) -> bool:
    """DEPRECATED: 31c-crm-central was replaced by per-exec CRM repos
    (mishahanin/31c-crm-{slug}) in the build 28 CRM isolation migration.

    This step is now a no-op for all workspaces. CEO aggregation reads directly
    from per-exec repos via aggregate-crm.py. Execs push their own contacts to
    their per-exec data repo (heading-os-data-{slug}) via push-all.py.
    """
    if is_done(state, "clone_crm_central"):
        step_header(7, "CRM central repo")
        skip("CRM central repo (deprecated -- per-exec repos used instead)")
        return True

    step_header(7, "CRM central repo (deprecated)")
    skip("31c-crm-central deprecated; per-exec CRM repos handle isolation. No clone performed.")
    mark_done(state, "clone_crm_central")
    return True


# ============================================================
# Filesystem Helpers / Corporate Sync
# ============================================================

# ---------------------------------------------------------------------------
# Step 8: First corporate sync
# ---------------------------------------------------------------------------

def _set_readonly(path: Path):
    """Set a file to read-only (cross-platform)."""
    if platform.system() == "Windows":
        current = path.stat().st_mode
        path.chmod(current & ~stat.S_IWRITE)
    else:
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)


def _make_writable(path: Path):
    """Make a file writable before overwriting."""
    if path.exists():
        try:
            path.chmod(stat.S_IWRITE | path.stat().st_mode)
        except OSError:
            pass


def _copy_tree(src: Path, dst: Path, preserve_files: list[str] = None):
    """Copy directory tree, optionally preserving specific files in dst.

    Args:
        src: Source directory
        dst: Destination directory
        preserve_files: List of filenames to NOT overwrite in dst
    """
    preserve_files = preserve_files or []
    count = 0

    for src_file in src.rglob("*"):
        if src_file.is_dir():
            continue

        rel = src_file.relative_to(src)
        dst_file = dst / rel

        # Check if this file should be preserved
        if rel.name in preserve_files and dst_file.exists():
            continue

        dst_file.parent.mkdir(parents=True, exist_ok=True)
        _make_writable(dst_file)

        try:
            shutil.copy2(str(src_file), str(dst_file))
            count += 1
        except PermissionError:
            pass  # Skip locked files silently

    return count


def step_corporate_sync(state: dict) -> bool:
    """No-op in the read-directly model (2026-06-26).

    Corporate content is read in place from .corporate-repo/ via
    get_corporate_root(); it is no longer copied into an in-tree corporate/ tree.
    The legacy copy also pulled .claude/{rules,skills,hooks}, scripts/, and docs/
    from the corporate repo -- obsolete now that execs receive ALL engine code by
    cloning the engine repo and the published heading-os-corporate carries content
    only (no code). Retained as a no-op so the step sequence and the resumable
    state file stay stable. The former copy helpers (_set_readonly, _make_writable,
    _copy_tree) are now unused but left in place rather than deleted."""
    if is_done(state, "corporate_sync"):
        step_header(8, "Corporate sync")
        skip("Corporate sync (read-directly model -- no copy)")
        return True

    step_header(8, "Corporate sync")
    skip("Read-directly model: corporate content is read from .corporate-repo/; nothing copied.")
    mark_done(state, "corporate_sync")
    return True


# ---------------------------------------------------------------------------
# Step 9: Create CRM central directory
# ---------------------------------------------------------------------------

def step_crm_central_dir(state: dict, identity: dict) -> bool:
    """DEPRECATED: per-exec CRM repos replaced 31c-crm-central in the build 28
    CRM isolation migration. This step is now a no-op for all workspaces.

    Execs push their contacts to their own per-exec data repo
    (heading-os-data-{slug}) via push-all.py.
    """
    if is_done(state, "crm_central_dir"):
        step_header(9, "CRM central directory")
        skip("CRM central directory (deprecated)")
        return True

    step_header(9, "CRM central directory (deprecated)")
    skip("31c-crm-central deprecated; per-exec CRM repos handle contact push.")
    mark_done(state, "crm_central_dir")
    return True


# ============================================================
# Wizard Steps: Dependencies & Scheduling
# ============================================================

# ---------------------------------------------------------------------------
# Step 10: Install Python dependencies
# ---------------------------------------------------------------------------

def step_install_python_deps(state: dict) -> bool:
    """Install Python dependencies (layered: .corporate-repo/requirements.txt + root requirements.txt).

    Required because scheduled scripts (Sentinel) and the daemons depend on
    third-party packages (exchangelib, weasyprint, playwright, etc.).

    Spec: docs/superpowers/specs/2026-04-27-layered-requirements-distribution-design.md
    """
    if is_done(state, "install_python_deps"):
        step_header(10, "Python dependencies")
        skip("Python dependencies already installed")
        return True

    step_header(10, "Installing Python dependencies")

    # Preferred path: uv sync (isolated venv from pyproject.toml + uv.lock).
    # Avoids the PEP 668 system-Python rejection modern distros enforce, and
    # installs the exact locked set. The rationale lived at
    # docs/security/DEPENDENCY-POLICY.md, which routes private and so is absent
    # from a public clone; CONTRIBUTING.md carries the part a contributor needs
    # (exact pins, no silent dependency bumps).
    if shutil.which("uv"):
        print("  Running: uv sync --all-extras --all-groups")
        try:
            run_cmd(["uv", "sync", "--all-extras", "--all-groups"], cwd=str(WORKSPACE_ROOT))
            ok("Dependencies installed via uv (pyproject.toml + uv.lock, all extras)")
            mark_done(state, "install_python_deps")
            return True
        except subprocess.CalledProcessError as e:
            err = (str(e.stderr) if e.stderr else str(e)).strip()
            fail(f"uv sync failed: {err[:300]}")
            print(f"\n  {YELLOW}Try manually: uv sync --all-extras --all-groups{RESET}")
            sys.exit(1)

    warn("uv not found - install it (https://astral.sh/uv) for reproducible installs.")
    warn("Falling back to layered pip install.")

    files_installed = []

    # Layer 1: the corporate clone's requirements, if it publishes one.
    # This read `corporate/requirements.txt`, a path only the deleted
    # copy-into-workspace step ever produced, so the branch was dead on every
    # install while its output claimed a layered design the repo dropped.
    corp_req = WORKSPACE_ROOT / ".corporate-repo" / "requirements.txt"
    if corp_req.exists():
        print(f"  Layer 1: {corp_req.relative_to(WORKSPACE_ROOT)}")
        print(f"    Running: {sys.executable} -m pip install -r {corp_req}")
        try:
            run_cmd([sys.executable, "-m", "pip", "install", "-r", str(corp_req), "--quiet"])
            ok(f"Installed dependencies from {corp_req.relative_to(WORKSPACE_ROOT)}")
            files_installed.append(str(corp_req.relative_to(WORKSPACE_ROOT)))
        except subprocess.CalledProcessError as e:
            err = (str(e.stderr) if e.stderr else str(e)).strip()
            fail(f"pip install of {corp_req.relative_to(WORKSPACE_ROOT)} failed: {err[:300]}")
            print(f"\n  {YELLOW}Try manually: python -m pip install -r {corp_req.relative_to(WORKSPACE_ROOT)}{RESET}")
            sys.exit(1)
    else:
        print(f"  Layer 1: {corp_req.relative_to(WORKSPACE_ROOT)} not present (skipping)")

    # Layer 2: root requirements.txt (personal Python deps)
    root_req = WORKSPACE_ROOT / "requirements.txt"
    if root_req.exists():
        print(f"  Layer 2: {root_req.relative_to(WORKSPACE_ROOT)}")
        print(f"    Running: {sys.executable} -m pip install -r {root_req}")
        try:
            run_cmd([sys.executable, "-m", "pip", "install", "-r", str(root_req), "--quiet"])
            ok(f"Installed dependencies from {root_req.relative_to(WORKSPACE_ROOT)}")
            files_installed.append(str(root_req.relative_to(WORKSPACE_ROOT)))
        except subprocess.CalledProcessError as e:
            err = (str(e.stderr) if e.stderr else str(e)).strip()
            fail(f"pip install of {root_req.relative_to(WORKSPACE_ROOT)} failed: {err[:300]}")
            print(f"\n  {YELLOW}Try manually: python -m pip install -r {root_req.relative_to(WORKSPACE_ROOT)}{RESET}")
            sys.exit(1)
    else:
        print(f"  Layer 2: {root_req.relative_to(WORKSPACE_ROOT)} not present (skipping)")

    if not files_installed:
        # Every other message in this block renders `corp_req.relative_to(...)`
        # and prints `.corporate-repo/requirements.txt`; this one alone held a
        # literal `corporate/`, so a single run named the same file two
        # different ways and one of the two named nothing on disk.
        warn(f"Neither {corp_req.relative_to(WORKSPACE_ROOT)} nor "
             f"{root_req.relative_to(WORKSPACE_ROOT)} found - skipping pip install.")
        warn("Some scheduled scripts may fail until dependencies are installed.")
    else:
        ok(f"Installed: {', '.join(files_installed)}")

    mark_done(state, "install_python_deps")
    return True


# ---------------------------------------------------------------------------
# Step 11: Install scheduled sync (+ Sentinel) via shared helper
# ---------------------------------------------------------------------------

def step_install_sync(state: dict, identity: dict, reinstall: bool = False, install_sentinel: bool = True) -> bool:
    """Install the 15-min Sentinel schedule.

    The hourly workspace-sync schedule was retired -- see
    plans/2026-06-26-retire-workspace-sync-disk-import.md. Code-down is now a
    plain `git pull`, data-up is `push-all.py`, and first-run record recovery is
    `import-legacy-records.py`; nothing installs a destructive sync task anymore.
    This step keeps only the Sentinel comms-monitor schedule. Uses
    scripts/utils/schedule.py for all platform-specific install/verify/logging.
    Honors `--reinstall-schedule` so operators can force a re-install, and
    `--no-sentinel-schedule`, which declines the install FOR THAT RUN ONLY and
    records nothing.
    """
    _ensure_workspace_importable()
    try:
        from scripts.utils.schedule import install_sentinel_schedule
    except ImportError as exc:
        # Say it and keep going. This step used to raise straight out of main(),
        # which only catches KeyboardInterrupt, so steps 12 (verify) and 13
        # (summary) never ran and the operator was left with a traceback after
        # ten steps had already taken effect.
        step_header(11, "Installing scheduled tasks")
        fail(f"Could not load the schedule helper: {exc}")
        warn("Install the Sentinel schedule later with: "
             "python scripts/setup.py --reinstall-schedule")
        return False

    if is_done(state, "install_sync") and not reinstall:
        step_header(11, "Scheduled tasks")
        skip("Scheduled tasks already installed (use --reinstall-schedule to force)")
        return True

    step_header(11, "Installing scheduled tasks")

    slug = identity.get("slug", "unknown")
    if not install_sentinel:
        # `--no-sentinel-schedule` is PER-RUN (operator decision, 2026-08-29):
        # it declines the install on the run it is typed on and writes no
        # persistent opt-out. So nothing is recorded. `mark_done` used to run
        # here regardless, which turned one flagged run into a permanent
        # opt-out: every later plain run read `install_sync`, printed
        # "Scheduled tasks already installed", and installed nothing. The
        # monitor that exists to notice a silent daemon was itself silently
        # absent, behind a green line, and recovery needed an operator who
        # already knew about `--reinstall-schedule`.
        skip("Sentinel schedule declined for this run; nothing recorded, so a "
             "plain re-run will install it")
        return True

    install_sentinel_schedule(slug, WORKSPACE_ROOT)
    mark_done(state, "install_sync")
    return True


# ============================================================
# Wizard Steps: Verification & Summary
# ============================================================

# ---------------------------------------------------------------------------
# Step 12: Verify
# ---------------------------------------------------------------------------

def step_verify(state: dict) -> bool:
    """Verify that key files and directories exist."""
    step_header(12, "Verifying setup")

    checks_passed = 0
    checks_total = 0

    # Check corporate content
    checks_total += 1
    # `.corporate-repo/`, not `corporate/`: the in-tree copy was retired, so
    # this check failed on every correctly-set-up workspace and trained the
    # operator to ignore a permanent "Checks passed: 3/4".
    biz_info = WORKSPACE_ROOT / ".corporate-repo" / "context" / "business-info.md"
    if biz_info.exists():
        # The path PROBED, not a path that no longer exists. The comment above
        # records that `corporate/` was retired and the check was repointed at
        # `.corporate-repo/`, and then both messages went on naming the dead
        # tree. A passing line asserting a file at a location the method never
        # opened is the `.claude/rules/scope-claims.md` defect, and it is the
        # worse of the two: the operator has no reason to doubt a green check.
        ok(f"{biz_info.relative_to(WORKSPACE_ROOT)} exists")
        checks_passed += 1
    else:
        warn(f"{biz_info.relative_to(WORKSPACE_ROOT)} not found "
             f"(corporate repo may be empty)")

    # Check skills
    checks_total += 1
    skills_dir = WORKSPACE_ROOT / ".claude" / "skills"
    if skills_dir.exists() and any(skills_dir.iterdir()):
        skill_count = sum(1 for _ in skills_dir.iterdir() if _.is_dir())
        ok(f".claude/skills/ has {skill_count} skill(s)")
        checks_passed += 1
    else:
        warn(".claude/skills/ is empty or missing")

    # Check workspace identity
    checks_total += 1
    identity_file = WORKSPACE_ROOT / ".workspace-identity.json"
    if identity_file.exists():
        try:
            ident = json.loads(identity_file.read_text(encoding="utf-8"))
            if ident.get("slug") and ident.get("type"):
                ok(f".workspace-identity.json valid (slug: {ident['slug']})")
                checks_passed += 1
            else:
                warn(".workspace-identity.json missing slug or type")
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            # A file that will not decode IS malformed, which is what this arm
            # already says. Without `UnicodeDecodeError` it raised instead, and
            # `step_verify` is the diagnostic step - so the run died on the
            # check that exists to report the problem.
            warn(".workspace-identity.json is malformed")
    else:
        warn(".workspace-identity.json not found")

    # Check .env
    checks_total += 1
    env_file = WORKSPACE_ROOT / ".env"
    if env_file.exists():
        ok(".env exists")
        checks_passed += 1
    else:
        warn(".env not found")

    print(f"\n  Checks passed: {checks_passed}/{checks_total}")
    return checks_passed > 0


# ---------------------------------------------------------------------------
# Step 13: Summary
# ---------------------------------------------------------------------------
# It read "Step 12" until 2026-09-02, which is the number `step_verify` prints
# through `step_header(12, ...)` one section above. `main` invokes this one
# under "# Step 13: Summary", so the file carried two step 12s and no step 13,
# and anyone cross-referencing the on-screen step count against the source
# landed on the verify section or concluded a step was missing.

def step_summary(identity: dict):
    """Print success summary with next steps."""
    slug = identity.get("slug", "unknown")

    print(f"\n{BOLD}{GREEN}{'=' * 56}{RESET}")
    print(f"{BOLD}{GREEN}  Setup complete!{RESET}")
    print(f"{BOLD}{GREEN}{'=' * 56}{RESET}")

    print(f"\n{BOLD}Workspace:{RESET}  {WORKSPACE_ROOT}")
    print(f"{BOLD}Identity:{RESET}   {slug} ({identity.get('type', 'unknown')})")
    # The last thing a new operator reads, so it has to be true. All three
    # lines here described a world that no longer exists: the corporate copy
    # into an in-tree corporate/ (step 8's docstring says nothing is copied),
    # the CRM-central repo (steps 7 and 9 call it deprecated), and the hourly
    # sync (step 11's docstring says it was retired).
    print(f"{BOLD}Corporate:{RESET}  read in place from .corporate-repo/ (nothing is copied in)")
    print(f"{BOLD}Data:{RESET}       your own work lives in the data overlay; "
          f"push it with scripts/push-all.py")
    print(f"{BOLD}Sync:{RESET}       code-down: git pull  |  data-up: push-all.py  |  "
          f"Sentinel on a 15-min schedule")

    print(f"\n{BOLD}Next steps:{RESET}")
    print(f"  1. Edit {CYAN}.env{RESET} to add/verify your API keys")
    print(f"  2. Edit {CYAN}personal/context/personal-info.md{RESET} with your details")
    print(f"  3. Launch Claude Code:")
    print(f"     {CYAN}claude{RESET}")
    print(f"  4. Run {CYAN}/prime{RESET} to load your context")
    print()


# ============================================================
# Main / CLI
# ============================================================

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Installs the Sentinel schedule (systemd timer, launchd agent or a Windows
    # scheduled task, `scripts/utils/schedule.py`), so it is a daemon entry
    # point. A fresh clone is a MAIN clone -- `.git` is a directory there -- so
    # setting up a new checkout is unaffected; only a worktree is refused.
    #
    # Imported HERE, not at module level: this module's docstring promises a
    # stdlib-only import surface so it loads on a fresh clone before sys.path is
    # configured, and a top-level `from scripts.utils...` would break that. This
    # repo's own .venv carries an editable .pth that makes the root importable,
    # so the breakage would have been invisible in every local test and would
    # have appeared only on the fresh exec clone -- the same trap recorded at
    # `_ensure_workspace_importable`.
    _ensure_workspace_importable()
    from scripts.utils.clone_guard import require_main_clone
    require_main_clone(__file__)
    parser = argparse.ArgumentParser(
        description="31C HEADING OS Workspace Setup",
    )
    parser.add_argument(
        "--reinstall-schedule",
        action="store_true",
        help="Force reinstall of scheduled sync / Sentinel tasks (overwrites existing).",
    )
    parser.add_argument(
        "--no-sentinel-schedule",
        action="store_true",
        help="Skip installing the Sentinel scheduled task (not recommended).",
    )
    args = parser.parse_args()

    # Step 1: Banner
    step_banner()

    # Load idempotency state
    state = load_state()
    if state["started_at"] is None:
        state["started_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state)

    # Step 2: Detect workspace identity
    identity = step_detect_identity(state)

    # Step 3: Check prerequisites
    step_check_prerequisites(state)

    # Step 4: Verify GitHub auth
    step_verify_github_auth(state)

    # Step 5: Setup .env
    step_setup_env(state)

    # Step 6: Clone corporate repo
    step_clone_corporate(state)

    # Step 7: Clone CRM central repo
    step_clone_crm_central(state, identity)

    # Step 8: First corporate sync
    step_corporate_sync(state)

    # Step 9: Create CRM central directory
    step_crm_central_dir(state, identity)

    # Step 10: Install Python dependencies
    step_install_python_deps(state)

    # Step 11: Install scheduled sync (+ Sentinel + dry-run validation)
    step_install_sync(
        state,
        identity,
        reinstall=args.reinstall_schedule,
        install_sentinel=not args.no_sentinel_schedule,
    )

    # Step 12: Verify
    step_verify(state)

    # Step 13: Summary
    step_summary(identity)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}Setup interrupted. Re-run to continue (completed steps will be skipped).{RESET}")
        sys.exit(130)
