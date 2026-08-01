#!/usr/bin/env python3
"""Audit the code and text this workspace INSTALLS, not the code it writes.

Every existing layer here watches what we author. `prompt-guard.py` scans four
data ingest paths (`knowledge/`, `datastore/`, `crm/contacts/`,
`outputs/operations/`); the secret scanner and the leak walls scan this
repository. Nothing scans the plugin cache, and that cache is not inert: it is
loaded into every session, and several plugins register hooks that EXECUTE on
every tool call.

Measured on one operator's machine on 2026-08-02, before this existed: 10
plugins on disk (4 at version "unknown"), 116 markdown files, 75 scripts, 28
hook files, and 6 PostToolUse hooks from a single plugin, each running a bash
script out of that cache. Files of that surface scanned by any layer: zero.
`superpowers` moved 5.1.0 to 6.1.1 on 2026-07-14 and nobody read the diff.

    python scripts/harness-audit.py                    # report, exit 1 on findings
    python scripts/harness-audit.py --json
    python scripts/harness-audit.py --update-manifest  # accept, after reading

**This is a reporter, not a gate.** It refuses nothing, blocks no tool call, and
is wired into no hook. That is deliberate: THE LAW says an optional step that
does not repay the operator visibly is abandoned inside two months, so the honest
order is to measure the first run's yield and let that number decide whether it
earns a hook, a timer, or removal. Wiring it before measuring would be the
maximalist reflex this standard exists to refuse.

Three properties it holds:

1. **Third-party execution is enumerated.** Anything running in our session that
   this repository does not own is listed with its firing event and its source.
2. **An upgrade is reviewable.** The surface is hashed against a baseline
   committed to git (in the PRIVATE overlay, not the public engine: see
   `default_manifest_path`), so a version bump is a named list of changed files
   rather than nothing at all. A MISSING baseline is reported, never read as
   agreement.
3. **The instrument never becomes the carrier.** Persisted output holds hashes,
   paths and pattern classes, never the content of a file it flagged.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.atomic import atomic_write_text
from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW
from scripts.utils.injection_patterns import scan_content
from scripts.utils.paths import (DataRootError, get_data_root,
                                 get_workspace_root, home, state_dir)

# Text-bearing and executable extensions. A .png in a plugin's docs is not part
# of the instruction or execution surface and hashing it only adds noise.
SURFACE_SUFFIXES = frozenset({
    ".md", ".markdown", ".txt", ".py", ".sh", ".bash", ".zsh", ".js", ".mjs",
    ".cjs", ".ts", ".json", ".cmd", ".bat", ".ps1", ".toml", ".yaml", ".yml",
})

# Our own instruction surface: what this repository contributes to a session.
OUR_SURFACE_GLOBS = (".claude/skills/**/*.md", ".claude/rules/**/*.md",
                     ".claude/agents/**/*.md")
OUR_SURFACE_FILES = ("AGENTS.md", "CLAUDE.md")

# Files in THIS REPOSITORY that legitimately contain the phrases this tool hunts.
#
# Matched as repository-relative path prefixes, deliberately NOT as basenames.
# A basename allowance is the first thing an attacker aims at: ship a file called
# `prompt-guard.py` inside the plugin cache and disappear. Installed content is
# never covered by this list, whatever it calls itself.
ALLOWED_REPO_PREFIXES = (
    ".claude/hooks/prompt-guard.py",
    ".claude/rules/security.md",
    ".claude/rules/lethal-trifecta.md",
    ".claude/rules/hidden-chars.md",
    "scripts/harness-audit.py",
    "scripts/utils/injection_patterns.py",
    "tests/",
    "docs/SECURITY-MODEL.md",
    "SECURITY.md",
)

# The workspace's existing convention for prose that legitimately discusses the
# patterns it governs (`scripts/humanization-check.py` already honours it).
# Honoured for THIS REPOSITORY only, for the same reason the allowance is: a
# marker an attacker can write is a marker an attacker can hide behind.
SKIP_START = "<!-- audit-skip-start -->"
SKIP_END = "<!-- audit-skip-end -->"

MANIFEST_VERSION = 1


def default_manifest_path() -> Path:
    """Where the reviewed baseline lives, and why it is not in the engine.

    The engine repository is PUBLIC, and this baseline is a per-file sha256 index
    of ONE operator's installed plugins: noise on anybody else's clone, and 236
    hex digests that `detect-secrets` correctly reads as high-entropy strings.
    The first version of this tool committed it to `config/` and the commit gate
    refused, which was the gate being right. Weakening it with a pragma or an
    allow-list entry to push our own file is precisely the move this workspace
    forbids, so the file moved instead.

    It belongs in the private DATA overlay, which is version-controlled and
    pushed like the engine, so the property that made a tracked baseline worth
    having survives: an upgrade is a readable diff in git history.

    A clone with no overlay (a public checkout, CI) falls back to gitignored
    local state. Drift detection still works there; only the git-history part is
    lost, which is the correct degradation rather than a refusal to run.
    """
    try:
        return get_data_root() / "config" / "harness-manifest.json"
    except (DataRootError, RuntimeError, OSError):
        return state_dir("harness") / "manifest.json"


# ============================================================
# Locating the third-party surface
# ============================================================

def plugin_root() -> Path:
    """Where installed plugins live. Machine-dependent, so overridable."""
    override = os.environ.get("HEADING_OS_PLUGIN_ROOT")
    if override:
        return Path(override)
    return home() / ".claude" / "plugins" / "cache"


def user_settings_path() -> Path:
    """User-level settings, which register hooks this repository cannot see."""
    override = os.environ.get("HEADING_OS_USER_SETTINGS")
    if override:
        return Path(override)
    return home() / ".claude" / "settings.json"


def _surface_files(root: Path):
    """Every installed file on the instruction-or-execution surface, sorted.

    Sorted so a manifest diff in git is a diff of content, not of walk order.
    """
    if not root.is_dir():
        return []
    out = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix.lower() in SURFACE_SUFFIXES:
            out.append(path)
    return sorted(out)


# ============================================================
# Property 1 - third-party execution is enumerated
# ============================================================

def _hooks_from_mapping(mapping, source: str):
    """Hook entries out of a settings-shaped `{event: [{hooks: [...]}]}` block."""
    found = []
    if not isinstance(mapping, dict):
        return found
    for event, groups in mapping.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks") or []:
                if isinstance(hook, dict) and hook.get("command"):
                    found.append({"event": str(event),
                                  "command": str(hook["command"]),
                                  "source": source})
    return found


def third_party_hooks(root: Path, settings: Path):
    """Every hook command that runs in our session and is not ours.

    Two sources, because a hook can arrive by either road: a plugin's own
    `hooks.json`, and user-level settings that no file in this repository
    mentions.
    """
    found = []
    for path in sorted(root.rglob("hooks.json")) if root.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            found.append({"event": "?", "command": f"<unreadable: {type(exc).__name__}>",
                          "source": str(path)})
            continue
        block = data.get("hooks") if isinstance(data, dict) else None
        found.extend(_hooks_from_mapping(block if block is not None else data, str(path)))

    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            found.append({"event": "?", "command": f"<unreadable: {type(exc).__name__}>",
                          "source": str(settings)})
        else:
            found.extend(_hooks_from_mapping(data.get("hooks"), str(settings)))
    return found


# ============================================================
# Property 2 - an upgrade is reviewable
# ============================================================

def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_surface_index(root: Path):
    """`{path relative to the plugin root: sha256}` plus anything unreadable.

    Relative, never absolute: the manifest is committed to a PUBLIC repository
    and an absolute path would carry the operator's home directory into it.
    """
    index, unreadable = {}, []
    for path in _surface_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            index[rel] = _digest(path)
        except OSError as exc:
            unreadable.append({"path": rel, "error": type(exc).__name__})
    return index, unreadable


def compare(index, baseline):
    """Added, changed and removed, against the reviewed baseline."""
    recorded = baseline.get("entries") or {}
    added = sorted(p for p in index if p not in recorded)
    removed = sorted(p for p in recorded if p not in index)
    changed = sorted(p for p, h in index.items()
                     if p in recorded and recorded[p] != h)
    return {"added": added, "changed": changed, "removed": removed}


def read_manifest(path: Path):
    """The reviewed baseline, or None when there is not one.

    None is a finding, never a pass. Absent evidence of review would otherwise
    make a fresh clone look audited.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ============================================================
# Injection in loaded content
# ============================================================

def _is_allowed_repo_path(rel: str) -> bool:
    return any(rel == p or rel.startswith(p) for p in ALLOWED_REPO_PREFIXES)


def _blank_skipped(text: str) -> str:
    """Skip-marked regions, blanked rather than removed, so line numbers hold."""
    out, skipping = [], False
    for line in text.split("\n"):
        if SKIP_START in line:
            skipping = True
        elif SKIP_END in line:
            skipping = False
        out.append("" if skipping else line)
    return "\n".join(out)


def _scan_one(path: Path, label: str, findings, scanned, unreadable,
              honour_skip: bool = False):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        unreadable.append({"path": label, "error": type(exc).__name__})
        return
    scanned.append(label)
    if honour_skip:
        text = _blank_skipped(text)
    for line_num, _snippet, category in scan_content(text):
        # The category and the line, never the snippet: this output is written
        # to a terminal the operator reads and may be redirected to a file.
        findings.append({"path": label, "line": line_num, "category": category})


def scan_loaded_content(repo: Path, root: Path):
    """Injection findings across everything that loads into a session."""
    findings, scanned, unreadable = [], [], []

    ours = []
    for pattern in OUR_SURFACE_GLOBS:
        ours.extend(repo.glob(pattern))
    for name in OUR_SURFACE_FILES:
        candidate = repo / name
        if candidate.is_file():
            ours.append(candidate)
    for path in sorted(set(ours)):
        rel = path.relative_to(repo).as_posix()
        if _is_allowed_repo_path(rel):
            continue
        _scan_one(path, rel, findings, scanned, unreadable, honour_skip=True)

    for path in _surface_files(root):
        # No allowance here, by design. See ALLOWED_REPO_PREFIXES.
        _scan_one(path, f"plugins/{path.relative_to(root).as_posix()}",
                  findings, scanned, unreadable)

    return findings, scanned, unreadable


# ============================================================
# Output
# ============================================================

def _render(result) -> None:
    hooks = result["third_party_hooks"]
    print(f"{BOLD}Harness audit{RESET} {GRAY}what this workspace installs and "
          f"executes, as opposed to what it writes{RESET}")
    print()

    print(f"{BOLD}{len(hooks)} third-party hook(s){RESET} "
          f"{GRAY}running in this session and not owned by this repository{RESET}")
    for entry in hooks:
        print(f"  {YELLOW}{entry['event']:<20}{RESET} {entry['command'][:96]}")
        print(f"  {GRAY}{'':<20} from {entry['source']}{RESET}")
    print()

    if result["baseline_missing"]:
        print(f"{RED}{BOLD}No reviewed baseline.{RESET} "
              f"{GRAY}Nothing has been accepted, so nothing can be compared. "
              f"Read the surface, then re-run with --update-manifest.{RESET}")
    else:
        drift = result["drift"]
        total = sum(len(drift[k]) for k in ("added", "changed", "removed"))
        if total:
            print(f"{RED}{BOLD}{total} file(s) differ from the reviewed baseline"
                  f"{RESET}")
            for kind in ("changed", "added", "removed"):
                for path in drift[kind]:
                    print(f"  {RED}{kind:<8}{RESET} {path}")
        else:
            print(f"{GREEN}Installed surface matches the reviewed baseline"
                  f"{RESET} {GRAY}({result['surface_files']} files){RESET}")
    print()

    if result["injection"]:
        print(f"{RED}{BOLD}{len(result['injection'])} injected instruction "
              f"pattern(s) in loaded content{RESET}")
        for finding in result["injection"]:
            print(f"  {RED}{finding['category']:<20}{RESET} "
                  f"{finding['path']}:{finding['line']}")
    else:
        print(f"{GREEN}No injected instruction patterns{RESET} "
              f"{GRAY}in {len(result['scanned'])} loaded file(s){RESET}")

    if result["unreadable"]:
        print()
        print(f"{YELLOW}{len(result['unreadable'])} file(s) could not be read"
              f"{RESET} {GRAY}silence on an unreadable file reads the same as a "
              f"clean one, so they are named{RESET}")
        for entry in result["unreadable"]:
            print(f"  {YELLOW}{entry['error']:<20}{RESET} {entry['path']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit the harness this workspace installs and executes.")
    parser.add_argument("--manifest", default=None,
                        help="reviewed baseline (default: the private data overlay)")
    parser.add_argument("--update-manifest", action="store_true", dest="update",
                        help="accept the current installed surface as reviewed")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    repo = get_workspace_root()
    root = plugin_root()
    manifest_path = (Path(args.manifest) if args.manifest
                     else default_manifest_path())

    index, hash_unreadable = build_surface_index(root)

    if args.update:
        atomic_write_text(manifest_path, json.dumps(
            {"version": MANIFEST_VERSION, "entries": index}, indent=2,
            sort_keys=True) + "\n")
        print(f"{GREEN}Accepted {len(index)} installed file(s) as reviewed."
              f"{RESET} {GRAY}{manifest_path}{RESET}")
        return 0

    baseline = read_manifest(manifest_path)
    findings, scanned, scan_unreadable = scan_loaded_content(repo, root)

    seen, unreadable = set(), []
    for entry in hash_unreadable + scan_unreadable:
        if entry["path"] not in seen:
            seen.add(entry["path"])
            unreadable.append(entry)

    result = {
        "third_party_hooks": third_party_hooks(root, user_settings_path()),
        "baseline_missing": baseline is None,
        "drift": ({"added": [], "changed": [], "removed": []} if baseline is None
                  else compare(index, baseline)),
        "injection": findings,
        "scanned": scanned,
        "unreadable": unreadable,
        "surface_files": len(index),
    }

    if args.as_json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _render(result)

    drifted = any(result["drift"][k] for k in ("added", "changed", "removed"))
    if result["baseline_missing"] or drifted or result["injection"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
