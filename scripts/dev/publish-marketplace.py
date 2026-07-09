#!/usr/bin/env python3
"""Publish the HEADING OS plugin marketplace to its own git repo.

The engine monorepo is the source of truth; the `heading-os-marketplace` repo is
the distribution artifact (a Claude Code plugin marketplace people install from).
This script keeps the two in sync reproducibly: it builds the bundles fresh via
`build-plugins.py`, syncs the built tree (`.claude-plugin/marketplace.json` plus
`plugins/*`) into a checkout of the marketplace repo, refreshes the repo meta
(README, LICENSE, .gitignore), and commits and pushes.

Never hand-edit the marketplace repo: re-run this and let the diff be the change.

One-time bootstrap (create the public repo and clone it next to the engine):

  gh repo create mishahanin/heading-os-marketplace --public \
    --description "HEADING OS: installable Claude Code plugin bundles." \
    --clone
  mv heading-os-marketplace ../heading-os-marketplace   # a sibling of the engine

Then, on every publish:

  python scripts/dev/publish-marketplace.py --repo-dir ../heading-os-marketplace
  python scripts/dev/publish-marketplace.py --repo-dir ../heading-os-marketplace --no-push
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.colors import BOLD, CYAN, GRAY, GREEN, RED, RESET  # noqa: E402
from scripts.utils.paths import get_workspace_root  # noqa: E402

DEFAULT_REPO_DIR = "../heading-os-marketplace"
REPO_SLUG = "mishahanin/heading-os-marketplace"


def _run(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def build_marketplace(engine_root: Path, out: Path) -> dict:
    """Build every non-empty bundle into `out`; return the parsed marketplace.json."""
    builder = engine_root / "scripts" / "dev" / "build-plugins.py"
    _run([sys.executable, str(builder), "--all", "--out", str(out)])
    import json

    mkt = json.loads((out / ".claude-plugin" / "marketplace.json").read_text())
    return mkt


def sync_into_repo(build_out: Path, repo_dir: Path) -> None:
    """Replace the repo's distribution tree with the freshly built one."""
    # Wipe the generated parts only; leave the repo's own meta (README, LICENSE, .git).
    for rel in (".claude-plugin", "plugins"):
        target = repo_dir / rel
        if target.exists():
            shutil.rmtree(target)
    shutil.copytree(build_out / ".claude-plugin", repo_dir / ".claude-plugin")
    shutil.copytree(build_out / "plugins", repo_dir / "plugins")


def _readme(mkt: dict) -> str:
    rows = "\n".join(
        f"| `{p['name']}` | {p.get('description', '').strip()} |" for p in mkt.get("plugins", [])
    )
    first = mkt["plugins"][0]["name"] if mkt.get("plugins") else "heading-core"
    name = mkt.get("name", "heading-os-marketplace")
    return f"""# HEADING OS Marketplace

A [Claude Code](https://docs.claude.com/en/docs/claude-code) plugin marketplace
for [HEADING OS](https://github.com/mishahanin/heading-os), the operations engine
an executive runs their company from.

This repository is a **generated distribution artifact**. The source of truth is
the engine monorepo; the bundles here are built from it by
`scripts/dev/publish-marketplace.py`. Do not hand-edit anything under
`plugins/` or `.claude-plugin/`: re-run the publisher and let the diff be the
change.

## Install

Inside Claude Code:

```
/plugin marketplace add {REPO_SLUG}
/plugin install {first}@{name}
```

(Or the CLI form: `claude plugin marketplace add {REPO_SLUG}`.)

## Bundles

| Bundle | What it carries |
| --- | --- |
{rows}

Plugins here omit a `version`, so each marketplace commit is a new version and
installs update automatically. Skills call their bundled scripts through
`${{CLAUDE_PLUGIN_ROOT}}`, and a `SessionStart` hook resolves your data overlay
at runtime, so no private data is ever bundled.

## Sovereignty

The sovereignty-core bundle ships the guard hooks, and the engine's non-bypassable
push-time content scan and the `send_capable -> gated` invariant remain the
backstops. Outbound send stays human-gated everywhere; nothing here changes that.

## License

Apache-2.0, matching the engine. See `LICENSE`.
"""


def write_repo_meta(repo_dir: Path, engine_root: Path, mkt: dict) -> None:
    (repo_dir / "README.md").write_text(_readme(mkt))
    shutil.copy2(engine_root / "LICENSE", repo_dir / "LICENSE")
    (repo_dir / ".gitignore").write_text("__pycache__/\n*.pyc\n*.pyo\n.DS_Store\n")


def ensure_identity(repo_dir: Path, engine_root: Path) -> None:
    """Mirror the engine's git identity into the marketplace repo if it has none."""
    if _run(["git", "config", "user.email"], cwd=repo_dir, check=False).stdout.strip():
        return
    for key in ("user.name", "user.email"):
        val = _run(["git", "config", key], cwd=engine_root, check=False).stdout.strip()
        if val:
            _run(["git", "config", key, val], cwd=repo_dir)


def commit_and_push(repo_dir: Path, engine_root: Path, message: str, push: bool) -> int:
    ensure_identity(repo_dir, engine_root)
    _run(["git", "add", "-A"], cwd=repo_dir)
    status = _run(["git", "status", "--porcelain"], cwd=repo_dir).stdout.strip()
    if not status:
        print(f"{GRAY}Nothing to publish (marketplace already up to date).{RESET}")
        return 0
    _run(["git", "commit", "-m", message], cwd=repo_dir)
    print(f"{GREEN}Committed:{RESET} {message}")
    if not push:
        print(f"{GRAY}--no-push: commit made, not pushed.{RESET}")
        return 0
    _run(["git", "push", "-u", "origin", "HEAD"], cwd=repo_dir)
    behind_ahead = _run(
        ["git", "rev-list", "--left-right", "--count", "origin/main...HEAD"],
        cwd=repo_dir,
        check=False,
    ).stdout.strip()
    if behind_ahead not in ("0\t0", ""):
        print(f"{RED}Push verification failed (ahead/behind = {behind_ahead!r}).{RESET}")
        return 1
    print(f"{GREEN}Pushed to {REPO_SLUG} (verified in sync).{RESET}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Publish the HEADING OS plugin marketplace.")
    ap.add_argument("--repo-dir", default=DEFAULT_REPO_DIR, help=f"marketplace repo checkout (default: {DEFAULT_REPO_DIR})")
    ap.add_argument("--message", default="chore: publish marketplace from engine", help="commit message")
    ap.add_argument("--no-push", action="store_true", help="commit only, do not push")
    args = ap.parse_args(argv)

    engine_root = get_workspace_root()
    repo_dir = (engine_root / args.repo_dir).resolve()
    if not (repo_dir / ".git").is_dir():
        print(
            f"{RED}Not a git repo: {repo_dir}{RESET}\n"
            f"Bootstrap it first (one-time):\n"
            f"  gh repo create {REPO_SLUG} --public --clone\n"
            f"  mv heading-os-marketplace {repo_dir}",
            file=sys.stderr,
        )
        return 2

    print(f"{BOLD}{CYAN}Publishing marketplace -> {repo_dir}{RESET}")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "marketplace"
        mkt = build_marketplace(engine_root, out)
        print(f"  built {len(mkt.get('plugins', []))} bundle(s): {', '.join(p['name'] for p in mkt.get('plugins', []))}")
        sync_into_repo(out, repo_dir)
        write_repo_meta(repo_dir, engine_root, mkt)
    return commit_and_push(repo_dir, engine_root, args.message, push=not args.no_push)


if __name__ == "__main__":
    raise SystemExit(main())
