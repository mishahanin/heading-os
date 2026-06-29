#!/usr/bin/env python3
"""DEPRECATED (2026-06-24) — provisions the RETIRED old single-workspace model.

Use `.heading-os-data/admin/provision/provision_exec.py` for the two-part HEADING OS
topology (CEO-owned `heading-os-data-{slug}` overlay + `heading-os-corporate` content).
This script creates the old-model layout (`31c-workspace-{slug}` + a separate
`31c-crm-{slug}` repo + corporate-sync) that the hard-cut migration retires; it is
kept only for its canary/staging provisioning logic until that is ported into the
admin-layer tool. Do not run it to onboard a new executive.

Provision a new executive workspace with full directory structure, GitHub repos, and sync.

One-command exec workspace provisioning. Creates workspace directory, initializes git,
sets up GitHub repos, clones corporate content, registers exec in CRM central, and
installs scheduled sync.

Usage:
    python provision-exec.py --name "Omar Said" --title "COO" \\
        --email "erin@31c.io" --role coo --github-user emposha \\
        [--workspace-dir PATH] [--reprovisioning]
"""

import argparse
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.utils.workspace import (
    get_workspace_root, validate_admin, get_exec_slug, load_exec_registry,
    get_crm_central_path, get_corporate_repo_path, load_admin_config,
    load_github_org,
)
from scripts.utils.colors import GREEN, YELLOW, RED, CYAN, BOLD, RESET

# ============================================================
# Constants
# ============================================================

GITHUB_ORG = load_github_org()

PROVISION_STEPS = [
    "validate_prerequisites",
    "generate_slug",
    "create_workspace_dir",
    "create_directory_structure",
    "create_workspace_identity",
    "copy_python_requirements",
    "create_env_template",
    "generate_claude_md",
    "generate_personal_info",
    "create_settings_local_json",
    "create_github_repo",
    "add_github_collaborator",
    "init_git",
    "clone_corporate",
    "first_corporate_sync",
    "create_crm_repo",
    "register_in_exec_registry",
    "install_scheduled_sync",
    "copy_getting_started",
    "push_synced_content",
    "print_summary",
]

PREREQUISITES = {
    "git": {"check": ["git", "--version"], "install": "https://git-scm.com/downloads"},
    "python3": {"check": ["python3", "--version"], "install": "https://www.python.org/downloads/"},
    "node": {"check": ["node", "--version"], "install": "https://nodejs.org/"},
    "gh": {"check": ["gh", "--version"], "install": "https://cli.github.com/"},
    "claude": {"check": ["claude", "--version"], "install": "https://docs.anthropic.com/en/docs/claude-code"},
}

KNOWLEDGE_SUBDIRS = [
    "fleeting", "signals", "decisions", "meetings",
    "research", "strategy", "people", "technology",
]

# ============================================================
# Templates
# ============================================================

CLAUDE_MD_TEMPLATE = textwrap.dedent("""\
    # CLAUDE.md

    Operational workspace for **{name}, {title} of 31 Concept (31C)**.
    Claude operates as a strategic assistant across sessions.

    ---

    ## Quick Reference

    - **Who I am:** `personal/context/personal-info.md`
    - **31C overview:** `corporate/context/business-info.md`
    - **Strategy & priorities:** `corporate/context/strategy.md`
    - **Voice & communication:** `corporate/reference/misha-voice.md`
    - **Key contacts:** `personal/context/people.md`
    - **Personal CRM:** `personal/crm/contacts/`, config at `corporate/crm/config.md`
    - **Knowledge base:** `personal/knowledge/`
    - **Corporate content:** `corporate/` (synced from 31c-corporate, read-only)

    ---

    ## Workspace Architecture

    This is an **exec workspace** with two-layer structure:
    - `corporate/` -- shared content synced from 31c-corporate (DO NOT edit directly)
    - `personal/` -- your personal content (CRM, knowledge, outputs)

    ### Rules (auto-loaded from `.claude/rules/`)
    - `terminology.md` -- Required terms, operational vocabulary, Five Core Principles
    - `voice.md` -- Working preferences, communication principles

    ### Skills (`.claude/skills/`)
    Key skills available: `/prime`, `/crm`, `/zk`, `/telegram`, `/backup`

    ---

    ## Session Workflow

    1. **Start**: Run `/prime` to load context
    2. **Work**: Use skills or direct tasks
    3. **Sync**: Corporate content auto-syncs on schedule
    4. **Backup**: Use `/backup` to push personal changes
""")

PERSONAL_INFO_TEMPLATE = textwrap.dedent("""\
    # Personal Information

    - **Name:** {name}
    - **Title:** {title}
    - **Email:** {email}
    - **Role:** {role}
    - **Company:** 31 Concept (31C)

    ## About

    [Add your background, expertise, and focus areas here.]

    ## Current Focus

    [Add your current priorities and projects here.]
""")

ENV_TEMPLATE = textwrap.dedent("""\
    # 31C Workspace Environment
    # Generated {date}

    # Anthropic API (required for Claude features)
    ANTHROPIC_API_KEY=

    # Telegram (optional)
    TELEGRAM_API_ID=
    TELEGRAM_API_HASH=

    # GitHub token (usually handled by gh CLI)
    # GITHUB_TOKEN=
""")

GETTING_STARTED_TEMPLATE = textwrap.dedent("""\
    # Getting Started with Your 31C Workspace

    Welcome to your 31C executive workspace. Here's how to get oriented.

    ## First Steps

    1. **Fill in your personal info:** Edit `personal/context/personal-info.md`
    2. **Configure your .env:** Add API keys to `.env`
    3. **Start Claude:** Run `claude` in this directory
    4. **Run `/prime`** to load your full context

    ## Directory Structure

    - `corporate/` -- Shared 31C content (auto-synced, read-only)
    - `personal/` -- Your personal workspace
      - `context/` -- Your personal info and contacts
      - `crm/contacts/` -- Your CRM contact files
      - `knowledge/` -- Your Zettelkasten knowledge base
      - `outputs/` -- Your generated outputs
    - `.claude/` -- Claude configuration (rules, skills, hooks)
    - `config/` -- Workspace configuration

    ## Key Commands

    - `/prime` -- Initialize session with full context
    - `/crm` -- Manage your contacts
    - `/zk` -- Knowledge base operations
    - `/backup` -- Push changes to GitHub

    ## Sync

    Corporate content syncs automatically on schedule.
    Your personal content is backed up when you run `/backup`.

    ## Need Help?

    Contact the admin (Misha) or check `corporate/reference/` for guides.
""")


# ============================================================
# Helpers
# ============================================================

def name_to_slug(name: str) -> str:
    """Convert a display name to kebab-case slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug


def load_provision_state(workspace_dir: Path) -> dict:
    """Load provision state for idempotency tracking."""
    state_file = workspace_dir / ".sync" / "provision-state.json"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"completed_steps": [], "started_at": None, "slug": None}


def save_provision_state(workspace_dir: Path, state: dict) -> None:
    """Save provision state."""
    state_dir = workspace_dir / ".sync"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "provision-state.json"
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")


def mark_step_done(workspace_dir: Path, state: dict, step: str) -> None:
    """Mark a provisioning step as completed."""
    if step not in state["completed_steps"]:
        state["completed_steps"].append(step)
        state["last_step"] = step
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        save_provision_state(workspace_dir, state)


def step_done(state: dict, step: str) -> bool:
    """Check if a step is already completed."""
    return step in state.get("completed_steps", [])


def run_cmd(cmd: list, cwd: str = None, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess command."""
    return subprocess.run(
        cmd, cwd=cwd, check=check,
        capture_output=capture, text=True,
    )


# ============================================================
# Provisioning Steps
# ============================================================

def validate_prerequisites(state: dict, args, workspace_dir: Path) -> bool:
    """Check that all required tools are installed."""
    if step_done(state, "validate_prerequisites") and not args.reprovisioning:
        print(f"  {GREEN}[skip]{RESET} Prerequisites already validated")
        return True

    print(f"\n{BOLD}Step 1: Validating prerequisites{RESET}")
    all_ok = True
    for tool, info in PREREQUISITES.items():
        try:
            result = run_cmd(info["check"], check=False)
            if result.returncode == 0:
                version = (result.stdout or result.stderr).strip().split("\n")[0]
                print(f"  {GREEN}[ok]{RESET} {tool}: {version}")
            else:
                raise FileNotFoundError
        except (FileNotFoundError, OSError):
            print(f"  {RED}[missing]{RESET} {tool}: Install from {info['install']}")
            all_ok = False

    if not all_ok:
        print(f"\n{RED}ERROR: Missing prerequisites. Install them and re-run.{RESET}")
        return False

    mark_step_done(workspace_dir, state, "validate_prerequisites")
    return True


def create_directory_structure(state: dict, args, workspace_dir: Path) -> bool:
    """Create the full workspace directory tree."""
    if step_done(state, "create_directory_structure"):
        print(f"  {GREEN}[skip]{RESET} Directory structure already exists")
        return True

    print(f"\n{BOLD}Step 4: Creating directory structure{RESET}")
    dirs = [
        "corporate",
        "personal/context",
        "personal/crm/contacts",
        "personal/outputs",
        ".claude/rules",
        ".claude/skills",
        ".claude/hooks",
        ".sync/logs",
        "scripts/utils",
        "config",
    ]
    for subdir in KNOWLEDGE_SUBDIRS:
        dirs.append(f"personal/knowledge/{subdir}")

    for d in dirs:
        (workspace_dir / d).mkdir(parents=True, exist_ok=True)
        print(f"  {GREEN}[ok]{RESET} {d}/")

    # Drop .gitkeep into empty personal/ subdirs so git clone preserves them.
    # Without this, empty dirs disappear in fresh clones and scripts like
    # knowledge-health.py error with 'Knowledge directory not found'.
    gitkeep_targets = ["personal/crm/contacts", "personal/outputs"]
    for subdir in KNOWLEDGE_SUBDIRS:
        gitkeep_targets.append(f"personal/knowledge/{subdir}")
    for d in gitkeep_targets:
        keep = workspace_dir / d / ".gitkeep"
        if not keep.exists():
            keep.touch()

    mark_step_done(workspace_dir, state, "create_directory_structure")
    return True


def create_workspace_identity(state: dict, args, workspace_dir: Path, slug: str) -> bool:
    """Create .workspace-identity.json."""
    if step_done(state, "create_workspace_identity"):
        # Idempotent canary-flag patch: re-running with --canary on an already
        # provisioned exec promotes them to canary without requiring a full
        # re-provision (which would also reset GitHub repos, scheduled tasks, etc.).
        if getattr(args, "canary", False):
            identity_file = workspace_dir / ".workspace-identity.json"
            if identity_file.exists():
                try:
                    existing = json.loads(identity_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    existing = None
                if isinstance(existing, dict) and not existing.get("canary"):
                    existing["canary"] = True
                    identity_file.write_text(
                        json.dumps(existing, indent=2), encoding="utf-8",
                    )
                    print(f"  {GREEN}[patch]{RESET} Set canary=true on existing .workspace-identity.json")
        print(f"  {GREEN}[skip]{RESET} Workspace identity already exists")
        return True

    print(f"\n{BOLD}Step 5: Creating workspace identity{RESET}")
    identity = {
        "role": "exec",
        "slug": slug,
        "type": "exec-workspace",
    }
    if getattr(args, "canary", False):
        identity["canary"] = True
    identity_file = workspace_dir / ".workspace-identity.json"
    identity_file.write_text(json.dumps(identity, indent=2), encoding="utf-8")
    canary_tag = " (CANARY)" if identity.get("canary") else ""
    print(f"  {GREEN}[ok]{RESET} .workspace-identity.json created (slug: {slug}){canary_tag}")

    mark_step_done(workspace_dir, state, "create_workspace_identity")
    return True


def copy_python_requirements(state: dict, args, workspace_dir: Path) -> bool:
    """Write an empty personal requirements.txt stub at the exec workspace root.

    The exec needs a requirements.txt at workspace root so setup.py (Step 10)
    can run `pip install -r requirements.txt`. Shared 31C platform deps now
    arrive via corporate sync at <exec>/corporate/requirements.txt; the root
    file is for the exec's PERSONAL deps only.

    Spec: docs/superpowers/specs/2026-04-27-layered-requirements-distribution-design.md
    """
    if step_done(state, "copy_python_requirements"):
        print(f"  {GREEN}[skip]{RESET} requirements.txt stub already created")
        return True

    print(f"\n{BOLD}Step 5b: Writing personal requirements.txt stub{RESET}")
    dst = workspace_dir / "requirements.txt"

    if dst.exists():
        print(f"  {GREEN}[ok]{RESET} {dst} already exists - leaving as-is")
        mark_step_done(workspace_dir, state, "copy_python_requirements")
        return True

    stub = (
        "# Personal Python dependencies for this exec workspace.\n"
        "#\n"
        "# Shared 31C platform deps live at corporate/requirements.txt and arrive\n"
        "# automatically via hourly corporate sync. Add ONLY your personal pins here.\n"
        "#\n"
        "# After editing, run: pip install -r requirements.txt\n"
    )
    dst.write_text(stub, encoding="utf-8")
    print(f"  {GREEN}[ok]{RESET} stub written to {dst}")
    mark_step_done(workspace_dir, state, "copy_python_requirements")
    return True


def create_env_template(state: dict, args, workspace_dir: Path) -> bool:
    """Create .env template with placeholder values."""
    if step_done(state, "create_env_template"):
        print(f"  {GREEN}[skip]{RESET} .env template already exists")
        return True

    print(f"\n{BOLD}Step 6: Creating .env template{RESET}")
    env_content = ENV_TEMPLATE.format(date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    env_file = workspace_dir / ".env"
    env_file.write_text(env_content, encoding="utf-8")
    print(f"  {GREEN}[ok]{RESET} .env template created")

    mark_step_done(workspace_dir, state, "create_env_template")
    return True


def generate_claude_md(state: dict, args, workspace_dir: Path) -> bool:
    """Generate personalized CLAUDE.md."""
    if step_done(state, "generate_claude_md"):
        print(f"  {GREEN}[skip]{RESET} CLAUDE.md already generated")
        return True

    print(f"\n{BOLD}Step 7: Generating CLAUDE.md{RESET}")

    # Try rich template from templates/ directory first, fall back to inline
    admin_root = get_workspace_root()
    if not (admin_root / "templates").exists():
        print(f"  {RED}[error]{RESET} Templates directory not found at {admin_root / 'templates'}")
        print(f"  {RED}        Run this script from the admin workspace.{RESET}")
        return False

    rich_template = admin_root / "templates" / "CLAUDE.md.template"
    if rich_template.exists():
        content = rich_template.read_text(encoding="utf-8")
        subs = {
            "{{EXEC_NAME}}": args.name,
            "{{EXEC_TITLE}}": args.title,
            "{{EXEC_SLUG}}": name_to_slug(args.name),
            "{{EXEC_EMAIL}}": args.email,
            "{{EXEC_ROLE}}": args.role,
        }
        for k, v in subs.items():
            content = content.replace(k, v)
        print(f"  {GREEN}[ok]{RESET} Using rich template ({len(content)} chars)")
    else:
        content = CLAUDE_MD_TEMPLATE.format(name=args.name, title=args.title)
        print(f"  {YELLOW}[warn]{RESET} Rich template not found, using inline fallback")

    claude_md = workspace_dir / "CLAUDE.md"
    claude_md.write_text(content, encoding="utf-8")
    print(f"  {GREEN}[ok]{RESET} CLAUDE.md generated for {args.name}")

    mark_step_done(workspace_dir, state, "generate_claude_md")
    return True


def generate_personal_info(state: dict, args, workspace_dir: Path) -> bool:
    """Generate personal/context/personal-info.md from template."""
    if step_done(state, "generate_personal_info"):
        print(f"  {GREEN}[skip]{RESET} personal-info.md already generated")
        return True

    print(f"\n{BOLD}Step 8: Generating personal-info.md{RESET}")

    admin_root = get_workspace_root()
    if not (admin_root / "templates").exists():
        print(f"  {RED}[error]{RESET} Templates directory not found at {admin_root / 'templates'}")
        print(f"  {RED}        Run this script from the admin workspace.{RESET}")
        return False

    rich_template = admin_root / "templates" / "personal-info.md.template"
    if rich_template.exists():
        content = rich_template.read_text(encoding="utf-8")
        subs = {
            "{{EXEC_NAME}}": args.name,
            "{{EXEC_TITLE}}": args.title,
            "{{EXEC_SLUG}}": name_to_slug(args.name),
            "{{EXEC_EMAIL}}": args.email,
            "{{EXEC_ROLE}}": args.role,
            "{{DATE}}": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }
        for k, v in subs.items():
            content = content.replace(k, v)
    else:
        content = PERSONAL_INFO_TEMPLATE.format(
            name=args.name, title=args.title, email=args.email, role=args.role,
        )

    info_file = workspace_dir / "personal" / "context" / "personal-info.md"
    info_file.write_text(content, encoding="utf-8")
    print(f"  {GREEN}[ok]{RESET} personal/context/personal-info.md generated")

    mark_step_done(workspace_dir, state, "generate_personal_info")
    return True


def create_settings_local_json(state: dict, args, workspace_dir: Path) -> bool:
    """Generate .claude/settings.local.json for the exec workspace."""
    if step_done(state, "create_settings_local_json"):
        print(f"  {GREEN}[skip]{RESET} settings.local.json already generated")
        return True

    print(f"\n{BOLD}Step 8b: Generating .claude/settings.local.json{RESET}")

    target_platform = args.platform or platform.system().lower()

    permissions_allow = [
        "Bash(python3:*)",
        "Bash(python:*)",
        "Bash(curl:*)",
        "Bash(git:*)",
        "WebSearch",
    ]
    if target_platform == "darwin":
        permissions_allow.append("Read(/Users/*/31c-workspace-*/**)")
    elif target_platform == "windows":
        permissions_allow.append("Read(C:/*/31c-workspace-*/**)")

    settings = {
        "permissions": {
            "allow": permissions_allow,
        },
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 .claude/hooks/session-start.py",
                            "timeout": 15,
                        }
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Write|Edit",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 .claude/hooks/post-write-sanitize.py",
                            "timeout": 15,
                        }
                    ],
                }
            ],
            "PreToolUse": [
                {
                    "matcher": "Write|Edit",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 .claude/hooks/protect-corporate.py",
                            "timeout": 5,
                        }
                    ],
                }
            ],
        },
    }

    settings_dir = workspace_dir / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_file = settings_dir / "settings.local.json"
    settings_file.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(f"  {GREEN}[ok]{RESET} settings.local.json generated ({target_platform})")

    mark_step_done(workspace_dir, state, "create_settings_local_json")
    return True


def create_github_repo(state: dict, args, workspace_dir: Path, slug: str) -> bool:
    """Create private GitHub repo for the exec workspace."""
    if step_done(state, "create_github_repo"):
        print(f"  {GREEN}[skip]{RESET} GitHub repo already created")
        return True

    print(f"\n{BOLD}Step 9: Creating GitHub repo{RESET}")
    repo_name = f"31c-workspace-{slug}"
    try:
        # Check if repo already exists
        check = run_cmd(["gh", "repo", "view", f"{GITHUB_ORG}/{repo_name}"], check=False)
        if check.returncode == 0:
            print(f"  {YELLOW}[exists]{RESET} Repo {GITHUB_ORG}/{repo_name} already exists")
        else:
            run_cmd([
                "gh", "repo", "create", f"{GITHUB_ORG}/{repo_name}",
                "--private", "--description", f"31C exec workspace for {args.name}",
            ])
            print(f"  {GREEN}[ok]{RESET} Created repo: {GITHUB_ORG}/{repo_name}")
    except subprocess.CalledProcessError as e:
        print(f"  {RED}[error]{RESET} Failed to create repo: {e.stderr}")
        return False

    mark_step_done(workspace_dir, state, "create_github_repo")
    return True


def add_github_collaborator(state: dict, args, workspace_dir: Path, slug: str) -> bool:
    """Add the exec's GitHub user as a collaborator on the workspace repo."""
    if step_done(state, "add_github_collaborator"):
        print(f"  {GREEN}[skip]{RESET} GitHub collaborator already added")
        return True

    if not args.github_user:
        print(f"  {YELLOW}[skip]{RESET} No --github-user provided, skipping collaborator setup")
        print(f"         Add manually: gh api repos/{GITHUB_ORG}/31c-workspace-{slug}/collaborators/USERNAME -X PUT")
        mark_step_done(workspace_dir, state, "add_github_collaborator")
        return True

    print(f"\n{BOLD}Step 9b: Adding GitHub collaborator{RESET}")

    # The per-exec CRM repo (31c-crm-{slug}) collaborator grant happens in
    # create_crm_repo() below. Workspace + corporate granted here.
    # Note: 31c-crm-central is intentionally NOT in this list - it was
    # deprecated by the build 28 CRM isolation migration. Per-exec privacy
    # model: each exec only has access to their own 31c-crm-{slug} repo.
    repos = [
        (f"31c-workspace-{slug}", "push"),
        ("31c-corporate", "pull"),
    ]

    failures = []
    for repo_name, perm in repos:
        try:
            run_cmd([
                "gh", "api",
                f"repos/{GITHUB_ORG}/{repo_name}/collaborators/{args.github_user}",
                "-X", "PUT",
                "-f", f"permission={perm}",
            ])
            print(f"  {GREEN}[ok]{RESET} Added {args.github_user} to {GITHUB_ORG}/{repo_name} ({perm})")
        except subprocess.CalledProcessError as e:
            print(f"  {RED}[error]{RESET} Failed to add to {repo_name}: {e.stderr}")
            print(f"         Retry manually: gh api repos/{GITHUB_ORG}/{repo_name}/collaborators/{args.github_user} -X PUT -f permission={perm}")
            failures.append(repo_name)

    if failures:
        print(f"  {RED}[fail]{RESET} Collaborator add failed for: {', '.join(failures)}. Step NOT marked done so it will retry on re-run.")
        return False

    print(f"  {CYAN}[info]{RESET} Invitations sent to {args.github_user} for workspace + corporate (CRM granted separately)")
    mark_step_done(workspace_dir, state, "add_github_collaborator")
    return True


def init_git(state: dict, args, workspace_dir: Path, slug: str) -> bool:
    """Initialize git, add remote, initial commit and push."""
    if step_done(state, "init_git"):
        print(f"  {GREEN}[skip]{RESET} Git already initialized")
        return True

    print(f"\n{BOLD}Step 10: Initializing git{RESET}")
    repo_name = f"31c-workspace-{slug}"
    ws = str(workspace_dir)

    # Create .gitignore
    # Note: .workspace-identity.json is INTENTIONALLY tracked (not ignored) so
    # setup.py can detect identity on first run without re-provisioning.
    gitignore = workspace_dir / ".gitignore"
    gitignore.write_text(
        ".env\n.env.*\n!.env.example\n"
        ".corporate-repo/\n.crm-central-repo/\n"
        "corporate/\n"
        ".sync/\n.heartbeat.json\n"
        "__pycache__/\n*.pyc\n*.pyo\n"
        ".DS_Store\nThumbs.db\nDesktop.ini\n"
        ".claude/settings.local.json\n.claude/settings.json\n"
        "node_modules/\n",
        encoding="utf-8",
    )

    try:
        # Init if not already a git repo
        if not (workspace_dir / ".git").exists():
            run_cmd(["git", "init"], cwd=ws)
            print(f"  {GREEN}[ok]{RESET} git init")

        # Set local git identity for this exec
        run_cmd(["git", "config", "user.name", args.name], cwd=ws)
        run_cmd(["git", "config", "user.email", args.email], cwd=ws)
        print(f"  {GREEN}[ok]{RESET} Git identity set: {args.name} <{args.email}>")

        run_cmd(["gh", "auth", "setup-git"], cwd=ws, check=False)
        print(f"  {GREEN}[ok]{RESET} Git credential helper configured via gh")

        # Add remote
        result = run_cmd(["git", "remote", "get-url", "origin"], cwd=ws, check=False)
        if result.returncode != 0:
            run_cmd(["git", "remote", "add", "origin", f"https://github.com/{GITHUB_ORG}/{repo_name}.git"], cwd=ws)
            print(f"  {GREEN}[ok]{RESET} Remote added: origin -> {GITHUB_ORG}/{repo_name}")

        # Initial commit
        run_cmd(["git", "add", "-A"], cwd=ws)
        run_cmd(["git", "commit", "-m", "Initial workspace setup"], cwd=ws)
        run_cmd(["git", "branch", "-M", "main"], cwd=ws)
        run_cmd(["git", "push", "-u", "origin", "main"], cwd=ws)
        print(f"  {GREEN}[ok]{RESET} Initial commit pushed to main")
    except subprocess.CalledProcessError as e:
        print(f"  {RED}[error]{RESET} Git operation failed: {e.stderr or e.stdout}")
        return False

    mark_step_done(workspace_dir, state, "init_git")
    return True


def clone_corporate(state: dict, args, workspace_dir: Path) -> bool:
    """Clone 31c-corporate repo to .corporate-repo/."""
    if step_done(state, "clone_corporate"):
        print(f"  {GREEN}[skip]{RESET} Corporate repo already cloned")
        return True

    print(f"\n{BOLD}Step 11: Cloning corporate repo{RESET}")
    corp_dir = workspace_dir / ".corporate-repo"
    if corp_dir.exists():
        print(f"  {YELLOW}[exists]{RESET} .corporate-repo/ already present")
    else:
        try:
            run_cmd([
                "gh", "repo", "clone", f"{GITHUB_ORG}/31c-corporate",
                str(corp_dir),
            ])
            print(f"  {GREEN}[ok]{RESET} Cloned 31c-corporate to .corporate-repo/")
        except subprocess.CalledProcessError as e:
            print(f"  {RED}[error]{RESET} Failed to clone corporate repo: {e.stderr}")
            return False

    mark_step_done(workspace_dir, state, "clone_corporate")
    return True


def first_corporate_sync(state: dict, args, workspace_dir: Path) -> bool:
    """Copy corporate content from .corporate-repo/ to corporate/."""
    if step_done(state, "first_corporate_sync"):
        print(f"  {GREEN}[skip]{RESET} Corporate sync already done")
        return True

    print(f"\n{BOLD}Step 12: Running first corporate sync{RESET}")
    src = workspace_dir / ".corporate-repo"
    dst = workspace_dir / "corporate"

    if not src.exists():
        print(f"  {YELLOW}[warn]{RESET} Corporate repo not found, skipping sync")
        mark_step_done(workspace_dir, state, "first_corporate_sync")
        return True

    # Copy contents, skip .git and .gitignore
    copied = 0
    for item in src.iterdir():
        if item.name in (".git", ".gitignore"):
            continue
        dest_item = dst / item.name
        try:
            if item.is_dir():
                if dest_item.exists():
                    shutil.rmtree(dest_item)
                shutil.copytree(item, dest_item)
                copied += 1
            else:
                shutil.copy2(item, dest_item)
                copied += 1
        except PermissionError:
            print(f"  {YELLOW}[warn]{RESET} Skipped locked file: {item.name}")

    # Also copy rules, skills, hooks, scripts, docs to workspace root (not just corporate/)
    for subdir in [".claude/rules", ".claude/skills", ".claude/hooks", "docs"]:
        src_sub = src / subdir
        dst_sub = workspace_dir / subdir
        if src_sub.exists():
            if dst_sub.exists():
                shutil.rmtree(dst_sub)
            shutil.copytree(src_sub, dst_sub)
            print(f"  {GREEN}[ok]{RESET} Synced {subdir}/")

    # Copy scripts
    src_scripts = src / "scripts"
    dst_scripts = workspace_dir / "scripts"
    if src_scripts.exists():
        if dst_scripts.exists():
            shutil.rmtree(dst_scripts)
        shutil.copytree(src_scripts, dst_scripts)
        print(f"  {GREEN}[ok]{RESET} Synced scripts/")

    print(f"  {GREEN}[ok]{RESET} Synced {copied} items from corporate repo")

    # Place READ-ONLY marker in corporate/
    admin_root = get_workspace_root()
    readme_src = admin_root / "templates" / "corporate-readme.txt"
    readme_dst = dst / "_READ_ONLY_DO_NOT_EDIT.txt"
    if readme_src.exists() and not readme_dst.exists():
        shutil.copy2(readme_src, readme_dst)
        print(f"  {GREEN}[ok]{RESET} Placed _READ_ONLY_DO_NOT_EDIT.txt in corporate/")

    # Initialize build tracking from corporate repo BUILD.json
    corp_build_path = src / "BUILD.json"
    sync_dir = workspace_dir / ".sync"
    sync_dir.mkdir(parents=True, exist_ok=True)
    if corp_build_path.exists():
        try:
            build_data = json.loads(corp_build_path.read_text(encoding="utf-8"))
            (sync_dir / "last-build.json").write_text(
                json.dumps(build_data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"  {GREEN}[ok]{RESET} Initialized build tracking (build {build_data.get('build', '?')}, v{build_data.get('version', '?')})")
        except Exception as e:
            print(f"  {YELLOW}[warn]{RESET} Could not initialize build tracking: {e}")

    mark_step_done(workspace_dir, state, "first_corporate_sync")
    return True


def create_crm_repo(state: dict, args, workspace_dir: Path, slug: str) -> bool:
    """Create the per-exec CRM repository on GitHub and seed it."""
    if step_done(state, "create_crm_repo"):
        print(f"  {GREEN}[skip]{RESET} Per-exec CRM repo already created")
        return True

    print(f"\n{BOLD}Creating per-exec CRM repository{RESET}")

    repo_name = f"31c-crm-{slug}"
    full_repo = f"{GITHUB_ORG}/{repo_name}"
    description = f"Personal CRM for {args.name}"

    # Verify github_user is present
    github_user = getattr(args, "github_user", None)
    if not github_user:
        print(f"  {RED}[error]{RESET} --github-user is required to set up the per-exec CRM repo.")
        return False

    # 1. Create the repo (idempotent -- succeeds if it already exists)
    result = subprocess.run([
        "gh", "repo", "create", full_repo, "--private",
        "--description", description,
    ], capture_output=True, text=True)
    if result.returncode != 0 and "already exists" not in (result.stderr or ""):
        print(f"  {RED}[error]{RESET} Failed to create {full_repo}: {result.stderr}")
        return False
    print(f"  {GREEN}[ok]{RESET} Repo {full_repo} ready")

    # 2. Add exec as collaborator
    result = subprocess.run([
        "gh", "api", f"repos/{full_repo}/collaborators/{github_user}",
        "-X", "PUT", "-f", "permission=push",
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  {RED}[error]{RESET} Could not add {github_user} as collaborator: {result.stderr}")
        print(f"         Retry manually: gh api repos/{full_repo}/collaborators/{github_user} -X PUT -f permission=push")
        print(f"         Step NOT marked done so it will retry on re-run.")
        return False
    print(f"  {GREEN}[ok]{RESET} {github_user} added as collaborator")

    # 3. Seed initial commit (clone, add README + contacts/, push)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        clone_dir = tmp / repo_name
        result = subprocess.run([
            "gh", "repo", "clone", full_repo, str(clone_dir),
        ], capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"  {YELLOW}[warn]{RESET} Could not clone for seed: {result.stderr}")
            mark_step_done(workspace_dir, state, "create_crm_repo")
            return True
        (clone_dir / "contacts").mkdir(exist_ok=True)
        (clone_dir / "contacts" / ".gitkeep").write_text("", encoding="utf-8")
        (clone_dir / "README.md").write_text(
            f"# {repo_name}\n\nPrivate CRM repository for **{args.name}** ({slug}).\n\n"
            f"Pushed via `scripts/push-all.py` from the exec machine.\n",
            encoding="utf-8",
        )
        for cmd in [
            ["git", "add", "-A"],
            ["git", "commit", "-m", "Initial commit: seed contacts/ + README"],
            ["git", "push", "origin", "main"],
        ]:
            result = subprocess.run(cmd, cwd=str(clone_dir), capture_output=True, text=True)
            if result.returncode != 0 and "nothing to commit" not in (result.stdout + result.stderr):
                print(f"  {YELLOW}[warn]{RESET} Seed step {cmd[1]} failed: {result.stderr}")

    print(f"  {GREEN}[ok]{RESET} Per-exec CRM repo seeded for {slug}")
    mark_step_done(workspace_dir, state, "create_crm_repo")
    return True


def register_in_exec_registry(state: dict, args, workspace_dir: Path, slug: str) -> bool:
    """Add exec to config/exec-registry.json in corporate repo."""
    if step_done(state, "register_in_exec_registry"):
        # Idempotent canary-flag patch on the registry entry (companion to the
        # patch in create_workspace_identity).
        if getattr(args, "canary", False):
            corp_repo = get_corporate_repo_path()
            registry_file = corp_repo / "config" / "exec-registry.json"
            if not registry_file.exists():
                registry_file = workspace_dir / "config" / "exec-registry.json"
            if registry_file.exists():
                try:
                    registry = json.loads(registry_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    registry = None
                if isinstance(registry, dict):
                    patched = False
                    for entry in registry.get("executives", []):
                        if entry.get("slug") == slug and not entry.get("canary"):
                            entry["canary"] = True
                            patched = True
                            break
                    if patched:
                        registry_file.write_text(
                            json.dumps(registry, indent=2), encoding="utf-8",
                        )
                        print(f"  {GREEN}[patch]{RESET} Set canary=true on registry entry for {slug}")
        print(f"  {GREEN}[skip]{RESET} Exec already registered")
        return True

    print(f"\n{BOLD}Step 14: Registering in exec registry{RESET}")
    corp_repo = get_corporate_repo_path()
    if not corp_repo.exists():
        # Fall back to local config
        corp_repo = workspace_dir

    registry_dir = corp_repo / "config"
    registry_dir.mkdir(parents=True, exist_ok=True)
    registry_file = registry_dir / "exec-registry.json"

    if registry_file.exists():
        try:
            registry = json.loads(registry_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            registry = {"version": "1.0", "executives": []}
    else:
        registry = {"version": "1.0", "executives": []}

    # Check if already registered
    existing = [e for e in registry["executives"] if e.get("slug") == slug]
    if existing:
        print(f"  {YELLOW}[exists]{RESET} {slug} already in registry")
    else:
        entry = {
            "slug": slug,
            "name": args.name,
            "title": args.title,
            "email": args.email,
            "role": args.role,
            "status": "active",
            "provisioned_at": datetime.now(timezone.utc).isoformat(),
            "workspace_repo": f"31c-workspace-{slug}",
            "platform": args.platform or platform.system().lower(),
        }
        if args.github_user:
            entry["github_user"] = args.github_user
        if getattr(args, "canary", False):
            entry["canary"] = True
        registry["executives"].append(entry)
        registry_file.write_text(json.dumps(registry, indent=2), encoding="utf-8")
        print(f"  {GREEN}[ok]{RESET} Added {slug} to exec-registry.json")

    # Try to commit and push if in a git repo
    try:
        cwd = str(corp_repo)
        run_cmd(["git", "add", "config/exec-registry.json"], cwd=cwd)
        status = run_cmd(["git", "status", "--porcelain"], cwd=cwd)
        if status.stdout.strip():
            run_cmd(["git", "commit", "-m", f"Register exec: {args.name} ({slug})"], cwd=cwd)
            run_cmd(["git", "push"], cwd=cwd)
            print(f"  {GREEN}[ok]{RESET} Pushed registry update to corporate repo")
    except subprocess.CalledProcessError:
        print(f"  {YELLOW}[warn]{RESET} Could not push registry update (commit locally)")

    mark_step_done(workspace_dir, state, "register_in_exec_registry")
    return True


def install_scheduled_sync(state: dict, args, workspace_dir: Path, slug: str) -> bool:
    """Install the Sentinel (15-min) scheduled task.

    The hourly workspace-sync schedule was retired -- see
    plans/2026-06-26-retire-workspace-sync-disk-import.md. On a clean HEADING OS
    deploy, code-down is a plain `git pull`, data-up is `push-all.py`, and
    first-run record recovery is `import-legacy-records.py`. No destructive sync
    task is installed anymore. Uses scripts/utils/schedule.py for all
    platform-specific logic so this script never drifts from setup.py.
    """
    from scripts.utils.schedule import install_sentinel_schedule

    if step_done(state, "install_scheduled_sync"):
        print(f"  {GREEN}[skip]{RESET} Scheduled tasks already installed")
        return True

    print(f"\n{BOLD}Step 15: Installing Sentinel schedule{RESET}")
    target_platform = (args.platform or platform.system()).lower()

    install_sentinel_schedule(slug, workspace_dir, target_platform=target_platform)

    mark_step_done(workspace_dir, state, "install_scheduled_sync")
    return True


def copy_getting_started(state: dict, args, workspace_dir: Path) -> bool:
    """Copy GETTING-STARTED.md to workspace root."""
    if step_done(state, "copy_getting_started"):
        print(f"  {GREEN}[skip]{RESET} GETTING-STARTED.md already present")
        return True

    print(f"\n{BOLD}Step 16: Creating GETTING-STARTED docs{RESET}")

    admin_root = get_workspace_root()
    if not (admin_root / "templates").exists():
        print(f"  {RED}[error]{RESET} Templates directory not found at {admin_root / 'templates'}")
        print(f"  {RED}        Run this script from the admin workspace.{RESET}")
        return False

    for fname in ["GETTING-STARTED.md", "GETTING-STARTED.html"]:
        rich = admin_root / "templates" / fname
        if rich.exists():
            shutil.copy2(rich, workspace_dir / fname)
            print(f"  {GREEN}[ok]{RESET} {fname} (rich template)")
        elif fname == "GETTING-STARTED.md":
            (workspace_dir / fname).write_text(GETTING_STARTED_TEMPLATE, encoding="utf-8")
            print(f"  {YELLOW}[warn]{RESET} {fname} (inline fallback)")

    mark_step_done(workspace_dir, state, "copy_getting_started")
    return True


def push_synced_content(state: dict, args, workspace_dir: Path, slug: str) -> bool:
    """Commit and push content synced after the initial commit.

    The initial commit in init_git() runs before first_corporate_sync and
    copy_getting_started populate scripts/, .claude/, docs/, and
    GETTING-STARTED files. Without this step the remote repo is an empty
    skeleton and the exec's clone is missing everything they need to run.
    """
    if step_done(state, "push_synced_content"):
        print(f"  {GREEN}[skip]{RESET} Synced content already pushed")
        return True

    print(f"\n{BOLD}Step 17: Pushing synced content to remote{RESET}")
    ws = str(workspace_dir)

    try:
        # Check for any untracked or modified files
        result = run_cmd(["git", "status", "--porcelain"], cwd=ws, check=False)
        if result.returncode != 0 or not (result.stdout or "").strip():
            print(f"  {GREEN}[ok]{RESET} Nothing to commit - remote already in sync")
            mark_step_done(workspace_dir, state, "push_synced_content")
            return True

        run_cmd(["git", "add", "-A"], cwd=ws)
        run_cmd(
            ["git", "commit", "-m", "Add synced corporate content and GETTING-STARTED docs"],
            cwd=ws,
        )
        run_cmd(["git", "push", "origin", "main"], cwd=ws)
        print(f"  {GREEN}[ok]{RESET} Synced content committed and pushed")
    except subprocess.CalledProcessError as e:
        print(f"  {RED}[error]{RESET} Push failed: {e.stderr or e.stdout}")
        return False

    mark_step_done(workspace_dir, state, "push_synced_content")
    return True


# ============================================================
# CLI / Main
# ============================================================

def main():
    # Hard-refuse: this script provisions the RETIRED single-workspace model
    # (31c-workspace-{slug} + separate 31c-crm-{slug} + scheduled corporate-sync),
    # which the two-part HEADING OS topology replaces. Running it would create an
    # old-model layout that no longer syncs. Use the admin-layer tool instead.
    # Escape hatch (transition only): set HEADING_OS_ALLOW_LEGACY_PROVISION=1.
    import os as _os
    if _os.environ.get("HEADING_OS_ALLOW_LEGACY_PROVISION") != "1":
        print(
            f"{RED}REFUSED: scripts/provision-exec.py provisions the retired "
            f"single-workspace model and is deprecated.{RESET}\n"
            f"Use the two-part HEADING OS provisioner:\n"
            f"  {CYAN}python .heading-os-data/admin/provision/provision_exec.py "
            f"--slug <slug> --name \"<Name>\" --role <role> --github-user <gh> "
            f"[--canary] [--dry-run]{RESET}\n"
            f"(override for transition only: HEADING_OS_ALLOW_LEGACY_PROVISION=1)",
            file=sys.stderr,
        )
        sys.exit(2)
    parser = argparse.ArgumentParser(
        description="Provision a new 31C executive workspace.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--name", required=True, help="Full name (e.g., 'Sam Carter')")
    parser.add_argument("--title", required=True, help="Title (e.g., 'CSO')")
    parser.add_argument("--email", required=True, help="Corporate email (e.g., 'bob@31c.io')")
    parser.add_argument("--role", required=True, help="Role identifier (e.g., 'cso')")
    parser.add_argument("--github-user", default=None,
                        help="Exec's GitHub username (for collaborator access)")
    parser.add_argument("--workspace-dir", type=Path, default=None,
                        help="Workspace directory (default: sibling of admin workspace)")
    parser.add_argument("--platform", choices=["windows", "darwin", "linux"], default=None,
                        help="Target platform for the exec (default: current OS)")
    parser.add_argument("--reprovisioning", action="store_true",
                        help="Re-run prerequisite checks even if already passed")
    parser.add_argument("--canary", action="store_true",
                        help="Provision as the canary exec (tracks corporate staging branch, "
                             "runs canary-smoke.py post-sync). Only one exec should be canary "
                             "at a time.")

    args = parser.parse_args()

    # Admin gate
    validate_admin()

    print(f"\n{BOLD}{CYAN}31C Executive Workspace Provisioning{RESET}")
    print(f"{'=' * 50}")
    print(f"  Name:  {args.name}")
    print(f"  Title: {args.title}")
    print(f"  Email: {args.email}")
    print(f"  Role:  {args.role}")

    # Step 2: Generate slug
    slug = name_to_slug(args.name)
    print(f"  Slug:  {slug}")
    if args.github_user:
        print(f"  GitHub: {args.github_user}")

    # Step 3: Determine workspace directory
    # Default: sibling of admin workspace (e.g., .../31c-workspace-{slug}/)
    if args.workspace_dir:
        workspace_dir = args.workspace_dir.resolve()
    else:
        admin_root = get_workspace_root()
        workspace_dir = (admin_root.parent / f"31c-workspace-{slug}").resolve()
    print(f"  Dir:   {workspace_dir}")
    print(f"{'=' * 50}")

    # Ensure workspace dir exists
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Load idempotency state
    state = load_provision_state(workspace_dir)
    if state["started_at"] is None:
        state["started_at"] = datetime.now(timezone.utc).isoformat()
        state["slug"] = slug
        save_provision_state(workspace_dir, state)

    # Execute steps
    steps = [
        ("validate_prerequisites", lambda: validate_prerequisites(state, args, workspace_dir)),
        ("create_directory_structure", lambda: create_directory_structure(state, args, workspace_dir)),
        ("create_workspace_identity", lambda: create_workspace_identity(state, args, workspace_dir, slug)),
        ("copy_python_requirements", lambda: copy_python_requirements(state, args, workspace_dir)),
        ("create_env_template", lambda: create_env_template(state, args, workspace_dir)),
        ("generate_claude_md", lambda: generate_claude_md(state, args, workspace_dir)),
        ("generate_personal_info", lambda: generate_personal_info(state, args, workspace_dir)),
        ("create_settings_local_json", lambda: create_settings_local_json(state, args, workspace_dir)),
        ("create_github_repo", lambda: create_github_repo(state, args, workspace_dir, slug)),
        ("add_github_collaborator", lambda: add_github_collaborator(state, args, workspace_dir, slug)),
        ("init_git", lambda: init_git(state, args, workspace_dir, slug)),
        ("clone_corporate", lambda: clone_corporate(state, args, workspace_dir)),
        ("first_corporate_sync", lambda: first_corporate_sync(state, args, workspace_dir)),
        ("create_crm_repo", lambda: create_crm_repo(state, args, workspace_dir, slug)),
        ("register_in_exec_registry", lambda: register_in_exec_registry(state, args, workspace_dir, slug)),
        ("install_scheduled_sync", lambda: install_scheduled_sync(state, args, workspace_dir, slug)),
        ("copy_getting_started", lambda: copy_getting_started(state, args, workspace_dir)),
        ("push_synced_content", lambda: push_synced_content(state, args, workspace_dir, slug)),
    ]

    for step_name, step_fn in steps:
        if not step_fn():
            print(f"\n{RED}Provisioning halted at step: {step_name}{RESET}")
            print(f"Fix the issue and re-run. Completed steps will be skipped (idempotent).")
            sys.exit(1)

    # Final summary
    print(f"\n{'=' * 50}")
    print(f"{BOLD}{GREEN}Provisioning complete!{RESET}")
    print(f"{'=' * 50}")
    print(f"\n{BOLD}Summary:{RESET}")
    print(f"  Workspace:   {workspace_dir}")
    print(f"  GitHub repo: {GITHUB_ORG}/31c-workspace-{slug}")
    if args.github_user:
        print(f"  GitHub user: {args.github_user} (collaborator invite sent)")
    print(f"  CRM central: contacts/{slug}/")
    print(f"  Sync:        Scheduled (hourly)")

    print(f"\n{BOLD}Next steps for {args.name}:{RESET}")
    if args.github_user:
        print(f"  0. {args.name} must accept the GitHub repo invite")
    print(f"  1. Clone: git clone https://github.com/{GITHUB_ORG}/31c-workspace-{slug}.git")
    print(f"  2. cd 31c-workspace-{slug}")
    print(f"  3. Edit .env to add API keys")
    print(f"  4. Edit personal/context/personal-info.md")
    print(f"  5. Run: claude")
    print(f"  6. In Claude, run: /prime")
    print(f"\n  Read GETTING-STARTED.md for full onboarding guide.")


if __name__ == "__main__":
    main()
