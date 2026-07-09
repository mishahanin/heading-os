#!/usr/bin/env python3
"""Generate Claude Code plugin bundles from the HEADING OS monorepo (F-10.1).

The monorepo is the source of truth. This assembles installable plugin bundles
under a build dir (default: dist/marketplace/) from config/plugin-bundles.yaml,
without modifying any in-repo file.

Per built bundle it:
  - copies the declared skills, hooks, and scripts (plus scripts/utils/),
  - writes .claude-plugin/plugin.json (no version: auto-update per commit),
  - GENERATES hooks/hooks.json from the manifest hook_events (this workspace
    wires hooks in settings.local.json, which does not travel in a plugin),
  - GENERATES a SessionStart env hook that exports WORKSPACE_ROOT (and, when a
    userConfig data path is present, HEADING_OS_DATA) so bundled scripts resolve
    their root from the plugin cache,
  - writes NO root-marker files: in the plugin cache the root resolves from the
    exported WORKSPACE_ROOT plus paths.py's structural fallback, so a CLAUDE.md
    marker would be redundant AND trips `claude plugin validate` (see below),
  - rewrites `python scripts/...` invocations in built SKILL.md to the
    ${CLAUDE_PLUGIN_ROOT} form (the only path that resolves once a plugin is
    copied to ~/.claude/plugins/cache),
  - runs a completeness gate that FAILS the build if any built skill/hook
    references a scripts/ or .claude/hooks/ target that was not bundled.

Finally it writes .claude-plugin/marketplace.json listing the built bundles.

Usage:
  python scripts/dev/build-plugins.py --bundle heading-core
  python scripts/dev/build-plugins.py --all
  python scripts/dev/build-plugins.py --bundle heading-core --out /tmp/mkt
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.utils.paths import get_workspace_root  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a core dependency
    print("PyYAML is required (uv sync).", file=sys.stderr)
    sys.exit(2)

MARKETPLACE_NAME = "heading-os-marketplace"
OWNER = {"name": "Misha Hanin", "email": "misha.hanin@odinix.com"}

# Rewrite `python|python3|bash <ws> scripts/` -> `... "${CLAUDE_PLUGIN_ROOT}"/scripts/`
# Only the scripts/ prefix token is touched; the path suffix and args are left intact.
_REWRITE_RE = re.compile(r"\b(python3?|bash)(\s+)scripts/")
_REWRITE_SUB = r'\1\2"${CLAUDE_PLUGIN_ROOT}"/scripts/'

# Reference scanners for the completeness gate.
_SCRIPT_REF_RE = re.compile(r"scripts/([\w./-]+\.py)")
_HOOK_REF_RE = re.compile(r"\.claude/hooks/([\w./-]+\.py)")


def load_manifest(root: Path) -> dict:
    path = root / "config" / "plugin-bundles.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["bundles"]


def _copytree(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Never ship compiled-bytecode cruft in a bundle (stale, bloated, non-source).
    shutil.copytree(
        src,
        dst,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def collect_bundled_scripts(spec: dict, root: Path) -> set[str]:
    """Set of repo-relative script paths that WILL be bundled (for the gate)."""
    bundled = {f"scripts/{s}" for s in spec.get("scripts", [])}
    # scripts/utils/ is always bundled wholesale.
    for p in (root / "scripts" / "utils").rglob("*.py"):
        bundled.add(str(p.relative_to(root)))
    return bundled


def completeness_gate(spec: dict, root: Path) -> list[str]:
    """Return a list of missing targets referenced by the bundle's skills/hooks."""
    bundled_scripts = collect_bundled_scripts(spec, root)
    bundled_script_names = {Path(p).name for p in bundled_scripts}
    bundled_hooks = set(spec.get("hooks", []))
    missing: list[str] = []

    sources: list[Path] = []
    for skill in spec.get("skills", []):
        sm = root / ".claude" / "skills" / skill / "SKILL.md"
        if sm.exists():
            sources.append(sm)
    for hook in spec.get("hooks", []):
        hp = root / ".claude" / "hooks" / hook
        if hp.exists():
            sources.append(hp)

    for src in sources:
        text = src.read_text(encoding="utf-8")
        for ref in _SCRIPT_REF_RE.findall(text):
            rel = f"scripts/{ref}"
            # utils/ refs (path or dotted) are always covered; check by path and basename.
            if rel in bundled_scripts or Path(ref).name in bundled_script_names:
                continue
            if ref.startswith("utils/"):
                continue
            missing.append(f"{src.relative_to(root)} -> scripts/{ref}")
        for ref in _HOOK_REF_RE.findall(text):
            if Path(ref).name not in bundled_hooks:
                missing.append(f"{src.relative_to(root)} -> .claude/hooks/{ref}")
    return missing


def generate_hooks_json(spec: dict) -> dict:
    """Build the plugin hooks.json from the manifest hook_events + session_start_env."""
    hooks: dict = {}
    for event, blocks in (spec.get("hook_events") or {}).items():
        event_blocks = []
        for block in blocks:
            cmds = [
                {
                    "type": "command",
                    "command": f'python3 "${{CLAUDE_PLUGIN_ROOT}}/hooks/{name}"',
                }
                for name in block.get("hooks", [])
            ]
            event_blocks.append({"matcher": block.get("matcher", ""), "hooks": cmds})
        if event_blocks:
            hooks[event] = event_blocks
    if spec.get("session_start_env"):
        hooks.setdefault("SessionStart", []).append(
            {
                "matcher": "startup",
                "hooks": [
                    {
                        "type": "command",
                        "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/session-env.py"',
                    }
                ],
            }
        )
    return {"hooks": hooks}


SESSION_ENV_SCRIPT = '''#!/usr/bin/env python3
"""Generated by build-plugins.py. Exports the plugin's workspace root (and, when
provided, the operator data root) for the rest of the session's Bash calls.

WORKSPACE_ROOT is the first-honored override in scripts/utils/paths.py, so this
is what makes bundled scripts resolve their root from ~/.claude/plugins/cache
instead of marker-walking (the cache has no CLAUDE.md/.claude markers of the
real workspace). HEADING_OS_DATA is exported only when the operator supplied a
data path via plugin userConfig; otherwise the data root falls back to demo.
"""
import os

env_file = os.environ.get("CLAUDE_ENV_FILE")
plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
if env_file and plugin_root:
    lines = [f'export WORKSPACE_ROOT="{plugin_root}"\\n']
    data_root = os.environ.get("CLAUDE_PLUGIN_OPTION_DATA_ROOT")
    if data_root:
        lines.append(f'export HEADING_OS_DATA="{data_root}"\\n')
    with open(env_file, "a", encoding="utf-8") as f:
        f.writelines(lines)
'''

def rewrite_script_paths(text: str) -> tuple[str, int]:
    new, n = _REWRITE_RE.subn(_REWRITE_SUB, text)
    return new, n


def build_bundle(name: str, spec: dict, out_root: Path, root: Path) -> None:
    # Completeness gate FIRST (fail fast before writing anything).
    missing = completeness_gate(spec, root)
    if missing:
        print(f"[{name}] completeness gate FAILED, unbundled references:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        raise SystemExit(3)

    bundle = out_root / "plugins" / name
    if bundle.exists():
        shutil.rmtree(bundle)
    (bundle / ".claude-plugin").mkdir(parents=True, exist_ok=True)

    # plugin.json (no version: auto-update per commit).
    plugin_json = {
        "name": name,
        "description": " ".join((spec.get("description") or "").split()),
        "author": OWNER,
    }
    (bundle / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(plugin_json, indent=2) + "\n", encoding="utf-8"
    )

    # Skills (verbatim), then rewrite script paths in each built SKILL.md.
    rewrites = 0
    for skill in spec.get("skills", []):
        src = root / ".claude" / "skills" / skill
        if not src.is_dir():
            raise SystemExit(f"[{name}] skill not found: {skill}")
        _copytree(src, bundle / "skills" / skill)
    for skill_md in (bundle / "skills").rglob("SKILL.md"):
        text = skill_md.read_text(encoding="utf-8")
        new, n = rewrite_script_paths(text)
        if n:
            skill_md.write_text(new, encoding="utf-8")
            rewrites += n

    # Hooks (verbatim) + generated hooks.json + session-env hook.
    hooks_dir = bundle / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    for hook in spec.get("hooks", []):
        src = root / ".claude" / "hooks" / hook
        if not src.is_file():
            raise SystemExit(f"[{name}] hook not found: {hook}")
        shutil.copy2(src, hooks_dir / hook)
    if spec.get("session_start_env"):
        (hooks_dir / "session-env.py").write_text(SESSION_ENV_SCRIPT, encoding="utf-8")
    (hooks_dir / "hooks.json").write_text(
        json.dumps(generate_hooks_json(spec), indent=2) + "\n", encoding="utf-8"
    )

    # Scripts: enumerated + scripts/utils/ wholesale.
    scripts_dir = bundle / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for rel in spec.get("scripts", []):
        src = root / "scripts" / rel
        if not src.is_file():
            raise SystemExit(f"[{name}] script not found: scripts/{rel}")
        dst = scripts_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    _copytree(root / "scripts" / "utils", scripts_dir / "utils")

    # Root resolution in the plugin cache is handled two ways, neither needing a
    # marker file: the generated SessionStart hook exports WORKSPACE_ROOT (the
    # first-honored override in paths.py), and paths.py's structural fallback
    # (_FALLBACK_ROOT = <this file>.parent.parent.parent) already resolves to
    # the bundle root because scripts/utils/paths.py keeps its depth. A CLAUDE.md
    # marker at the plugin root is therefore redundant AND trips
    # `claude plugin validate` (root context warning), so it is NOT written.

    print(f"[{name}] built at {bundle} ({rewrites} script-path rewrites)")


def write_marketplace(names: list[str], manifest: dict, out_root: Path) -> None:
    (out_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    plugins = []
    for name in names:
        spec = manifest[name]
        plugins.append(
            {
                "name": name,
                "source": f"./plugins/{name}",
                "description": " ".join((spec.get("description") or "").split()),
                "category": "heading-os",
            }
        )
    market = {
        "name": MARKETPLACE_NAME,
        "owner": OWNER,
        "description": "HEADING OS: the operations engine an executive runs their company from, as installable plugin bundles.",
        "plugins": plugins,
    }
    (out_root / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(market, indent=2) + "\n", encoding="utf-8"
    )
    print(f"marketplace.json written for: {', '.join(names)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build HEADING OS plugin bundles.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--bundle", help="single bundle name to build")
    g.add_argument("--all", action="store_true", help="build every non-empty bundle")
    ap.add_argument("--out", help="output dir (default: dist/marketplace)")
    args = ap.parse_args(argv)

    root = get_workspace_root()
    manifest = load_manifest(root)
    out_root = Path(args.out).expanduser().resolve() if args.out else root / "dist" / "marketplace"

    if args.bundle:
        if args.bundle not in manifest:
            print(f"unknown bundle: {args.bundle}", file=sys.stderr)
            return 2
        names = [args.bundle]
    else:
        # --all builds only bundles that declare skills or hooks (skip placeholders).
        names = [n for n, s in manifest.items() if s.get("skills") or s.get("hooks")]

    out_root.mkdir(parents=True, exist_ok=True)
    for name in names:
        build_bundle(name, manifest[name], out_root, root)
    write_marketplace(names, manifest, out_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
