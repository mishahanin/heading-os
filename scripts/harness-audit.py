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

Tests: tests/test_an_edit_that_deleted_the_addresses_it_promised_to_keep.py, tests/test_harness_audit.py, tests/test_harness_audit_contract.py

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

# Vendored dependency trees, pruned from the walk. Added 2026-08-20.
#
# `mattpocock-skills` 1.2.2 ships 24 MB of npm packages — prettier, @babel,
# @changesets, @manypkg — which is release tooling for the plugin's own
# repository, shipped to every consumer. It cost this audit its whole signal:
# 1596 of 1596 drift lines were `added` under that one tree, so a genuine change
# to `superpowers` or `claude-security` would have been invisible inside the
# wall, and all 46 "injected instruction pattern" hits came from the same place
# (iconv-lite, human-id, whatwg-url).
#
# THE CONDITION THAT MAKES THIS WRONG, stated so it can be re-checked: pruning
# means the audit stops vouching for content that IS on disk and could in
# principle be executed. It is acceptable only while the pruned tree ships no
# hooks. `mattpocock-skills/1.2.2/hooks` does not exist. Re-check that on any
# version bump of a plugin that vendors dependencies, and if one ever ships a
# hook from inside `node_modules`, remove it from this set rather than trusting
# the assumption.
PRUNED_DIRS = frozenset({"node_modules", ".git", "__pycache__", ".venv"})

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


def installed_plugins_path() -> Path:
    """The record of which cached plugin version is actually loaded."""
    override = os.environ.get("HEADING_OS_INSTALLED_PLUGINS")
    if override:
        return Path(override)
    return home() / ".claude" / "plugins" / "installed_plugins.json"


def disabled_plugin_keys(repo: Path) -> set:
    """Plugin keys switched OFF in any settings file that can switch one off.

    Installation and enablement are two different facts, and this tool knew only
    the first. `installed_plugins.json` records what was fetched and never
    forgets; `enabledPlugins` in a settings file records whether the loader
    starts it. On 2026-08-20 `security-guidance` was set to false in
    `.claude/settings.json` and this audit still printed its eight hooks under
    the words "running in this session" — the same shape of over-claim that
    `.claude/rules/scope-claims.md` was written for, one layer down.

    Only an explicit `false` disables. An absent key means enabled, which is the
    harness's own default and the safe reading for an audit: an unlisted plugin
    is reported as live rather than hidden.

    Three files can carry the key. The repository's tracked settings and its
    gitignored local settings are read from the passed repo root; the user-level
    file is read through `user_settings_path()` so the env override applies.
    """
    disabled = set()
    candidates = [
        repo / ".claude" / "settings.json",
        repo / ".claude" / "settings.local.json",
        user_settings_path(),
    ]
    for path in candidates:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # absent or unparseable: contributes nothing, hides nothing
        enabled = data.get("enabledPlugins") if isinstance(data, dict) else None
        if not isinstance(enabled, dict):
            continue
        for key, value in enabled.items():
            if value is False:
                disabled.add(key)
    return disabled


def disabled_install_paths(path: Path, repo: Path) -> set:
    """Resolved `installPath`s of plugins a settings file switched OFF.

    Kept SEPARATE from `active_install_paths` on purpose. `_is_loaded` treats an
    unknown path as live — dormant requires proof — so simply removing a
    disabled plugin from the active set would move it into "unknown" and it would
    still be reported as running. That is exactly what the first attempt at this
    fix did on 2026-08-20, and the audit still printed all eight
    `security-guidance` hooks minutes after the plugin was disabled.

    An explicit `false` in `enabledPlugins` IS the proof `_is_loaded` asks for,
    so it gets its own set and its own branch.
    """
    off = disabled_plugin_keys(repo)
    if not off:
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return set()
    paths = set()
    for key, entries in plugins.items():
        if key not in off:
            continue
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get("installPath"), str):
                paths.add(Path(entry["installPath"]).resolve())
    return paths


def active_install_paths(path: Path) -> set | None:
    """Resolved `installPath`s the loader records, or None if unreadable.

    The cache keeps every version it ever fetched; the loader reads exactly one
    per plugin. Walking the cache therefore over-counts, and this tool printed
    that over-count under the words "running in this session" until 2026-08-12,
    when it reported `superpowers` 6.1.1 and 6.2.0 as two live SessionStart
    hooks. Only 6.2.0 was loaded; 6.1.1 was an orphan the cache had not swept.

    INSTALLED, not enabled. A paragraph here used to claim this function also
    subtracts plugins disabled in a settings file. It never did: that belongs
    to `_is_loaded`, through the separate `disabled_install_paths` set, and
    the unused `repo` parameter was the leftover of the reverted attempt. A
    reader trusting the old text could have "simplified away" the `disabled`
    argument and put the 2026-08-20 bug straight back.

    None means the record could not be read, and a caller must then treat every
    cached hook as live. An audit that hides an executing hook because a JSON
    file was unreadable fails in the one direction it must not. An EMPTY but
    perfectly readable record returns an empty set, not None: `return active
    or None` used to collapse "no plugins recorded" into the unreadable
    sentinel and print a false alarm about file health.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return None
    active = set()
    for entries in plugins.values():
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            install = entry.get("installPath")
            if isinstance(install, str) and install:
                active.add(Path(install).resolve())
    return active


def _surface_files(root: Path):
    """Every installed file on the instruction-or-execution surface, sorted.

    Sorted so a manifest diff is a diff of content, not of walk order.
    """
    return _walk_surface(root)[0]


def _walk_surface(root: Path):
    """`(real files, symlinks)` on the surface.

    Symlinks are NOT followed, because following one leaves the plugin root and
    a target chosen by the content being audited is exactly the wrong thing to
    hash. They are also not silently dropped, which is what this tool did until
    it was tested with one: a symlinked `innocent.md` pointing at a payload was
    absent from the baseline AND absent from the injection scan, and the audit
    exited 0. Unvouchable content on the loaded surface is a finding, so a
    symlink is reported by name and never resolved.

    Vendored dependency trees are pruned; see PRUNED_DIRS for the reasoning and
    the condition that would make the pruning wrong.

    The walk is `os.walk(followlinks=False)` and not `Path.rglob`, because
    rglob handled only symlinked FILES and the promise above is about content.
    On Python 3.11 (this workspace's floor) rglob descends THROUGH a symlinked
    directory, and the children it yields are not themselves symlinks, so
    `is_symlink()` never fired: a `docs-link -> /tmp/payload` inside the plugin
    cache had its contents hashed into the baseline and scanned as ordinary
    vouched surface. A symlinked directory also carries no surface suffix, so
    the suffix filter discarded the link itself before it could be reported.
    Followed on one interpreter, invisible on another, and never a finding on
    either. Directory links are now recorded before any suffix test and never
    descended.
    """
    if not root.is_dir():
        return [], []
    files, links = [], []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        keep = []
        for name in dirnames:
            if name in PRUNED_DIRS:
                continue
            child = here / name
            if child.is_symlink():
                # Reported by name, never resolved and never descended. No
                # suffix test: a directory has none, and dropping it here is
                # the silent hole this replaces.
                links.append(child)
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            child = here / name
            if child.suffix.lower() not in SURFACE_SUFFIXES:
                continue
            if child.is_symlink():
                links.append(child)
            elif child.is_file():
                files.append(child)
    return sorted(files), sorted(links)


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


def _is_loaded(source: Path, active: set | None, disabled: set | None = None) -> bool:
    """Whether a plugin's `hooks.json` belongs to the version actually loaded.

    Dormant requires PROOF, not absence of proof. A hook is called dormant only
    when the record names a different version of the SAME plugin, which is what
    a superseded directory in the cache looks like. Anything the record does not
    mention at all is reported as live.

    That asymmetry is the whole point. The first version of this function
    reversed it and asked only "is this under an active path", which made every
    hook under an unrelated cache root vanish from the report -- the audit
    would have gone quiet about hooks that genuinely run, which is worse than
    the over-count it was written to fix. The repository's own contract test
    caught it.
    """
    resolved = source.resolve()
    # Explicit disable beats everything, including the unknown-is-live default:
    # a settings file saying false IS the proof this function asks for.
    if disabled and any(resolved == p or p in resolved.parents for p in disabled):
        return False
    if active is None:
        return True
    if any(resolved == path or path in resolved.parents for path in active):
        return True
    # Under the plugin directory of an active version, but not under that
    # version: the cache kept an older copy the loader no longer reads.
    return not any(path.parent in resolved.parents for path in active)


def third_party_hooks(root: Path, settings: Path, active: set | None = None,
                      disabled: set | None = None):
    """Every hook command on the installed surface, flagged by whether it loads.

    Two sources, because a hook can arrive by either road: a plugin's own
    `hooks.json`, and user-level settings that no file in this repository
    mentions. Each entry carries `loaded`: a cached version the loader does not
    read is still reported, but never under the claim that it is running.
    """
    found = []
    for path in sorted(root.rglob("hooks.json")) if root.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            found.append({"event": "?", "command": f"<unreadable: {type(exc).__name__}>",
                          "source": str(path), "loaded": True})
            continue
        block = data.get("hooks") if isinstance(data, dict) else None
        loaded = _is_loaded(path, active, disabled)
        for entry in _hooks_from_mapping(block if block is not None else data, str(path)):
            found.append({**entry, "loaded": loaded})

    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            found.append({"event": "?", "command": f"<unreadable: {type(exc).__name__}>",
                          "source": str(settings), "loaded": True})
        else:
            for entry in _hooks_from_mapping(data.get("hooks"), str(settings)):
                found.append({**entry, "loaded": True})
    return found


# ============================================================
# Property 2 - an upgrade is reviewable
# ============================================================

def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_surface_index(root: Path):
    """`{path relative to the plugin root: sha256}` plus anything unreadable.

    Relative, never absolute: the manifest is version-controlled, and an
    absolute path would carry the operator's home directory into it. That
    reason used to read "committed to a PUBLIC repository", which describes a
    design `default_manifest_path` explicitly reverted -- the baseline lives in
    the PRIVATE overlay now, and did before this sentence was written. The
    choice survives the correction; the stated reason did not.
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

    live = [e for e in hooks if e.get("loaded", True)]
    dormant = [e for e in hooks if not e.get("loaded", True)]
    qualifier = ("" if result.get("activation_known", True)
                 else f" {YELLOW}(activation record unreadable, so every cached "
                      f"hook is listed as live){RESET}")
    print(f"{BOLD}{len(live)} third-party hook(s){RESET} "
          f"{GRAY}running in this session and not owned by this repository{RESET}"
          f"{qualifier}")
    for entry in live:
        print(f"  {YELLOW}{entry['event']:<20}{RESET} {entry['command'][:96]}")
        print(f"  {GRAY}{'':<20} from {entry['source']}{RESET}")
    print()

    if dormant:
        # Two different reasons land a hook here now, and the line says both
        # rather than the one it used to: a superseded VERSION the loader skips,
        # and a plugin a settings file switched OFF. Naming only the first would
        # be the same over-claim in reverse — a reader would conclude a disabled
        # plugin was merely an old copy.
        print(f"{GRAY}{len(dormant)} further hook(s) are on the installed surface "
              f"but not in this session - either a superseded version the loader "
              f"does not read, or a plugin set false in enabledPlugins:{RESET}")
        for entry in dormant:
            print(f"  {GRAY}{entry['event']:<20} {entry['source']}{RESET}")
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

    if result["symlinks"]:
        print()
        print(f"{RED}{BOLD}{len(result['symlinks'])} symlink(s) on the installed "
              f"surface{RESET} {GRAY}not followed, so not hashed and not scanned; "
              f"content this audit cannot vouch for{RESET}")
        for path in result["symlinks"]:
            print(f"  {RED}symlink {RESET} {path}")

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
    parser.add_argument("--allow-empty", action="store_true",
                        help="With --update-manifest, accept a surface with no "
                             "files. Needed because an empty surface is almost "
                             "always a mistyped root.")
    parser.add_argument("--update-manifest", action="store_true", dest="update",
                        help="accept the current installed surface as reviewed")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    repo = get_workspace_root()
    root = plugin_root()
    manifest_path = (Path(args.manifest) if args.manifest
                     else default_manifest_path())

    index, hash_unreadable = build_surface_index(root)
    symlinks = [p.relative_to(root).as_posix() for p in _walk_surface(root)[1]]

    if args.update:
        # An index that emptied out is almost always a mistyped root, and
        # accepting it would mint a baseline everything matches forever. Refusing
        # is the same rule as "a missing baseline is not agreement".
        previous = read_manifest(manifest_path)
        # Any empty surface, not only one that replaces a baseline. On a first
        # run with a mistyped HEADING_OS_PLUGIN_ROOT there IS no previous
        # baseline, so the old guard stood aside, wrote {"entries": {}}, and
        # every later run then found index == baseline == empty: no drift, no
        # findings, exit 0, forever, scanning nothing. --allow-empty makes
        # accepting nothing a thing the operator typed.
        if not index and not args.allow_empty:
            had = len(previous["entries"]) if previous and previous.get("entries") else 0
            over = (f"over a baseline of {had} file(s)" if had
                    else "as the first baseline")
            print(f"{RED}Refusing to accept an EMPTY surface {over}.{RESET}\n"
                  f"{GRAY}Nothing was found under {root}. If the plugins really "
                  f"are gone, re-run with --allow-empty; if the root is wrong, "
                  f"fix it.{RESET}", file=sys.stderr)
            return 2
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

    active = active_install_paths(installed_plugins_path())
    result = {
        "third_party_hooks": third_party_hooks(
            root, user_settings_path(), active,
            disabled_install_paths(installed_plugins_path(), repo)),
        "activation_known": active is not None,
        "baseline_missing": baseline is None,
        "drift": ({"added": [], "changed": [], "removed": []} if baseline is None
                  else compare(index, baseline)),
        "injection": findings,
        "scanned": scanned,
        "unreadable": unreadable,
        "symlinks": symlinks,
        "surface_files": len(index),
    }

    if args.as_json:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        _render(result)

    drifted = any(result["drift"][k] for k in ("added", "changed", "removed"))
    # `unreadable` exits 1 for the same reason `symlinks` does: it is content
    # this audit cannot vouch for. A chmod-000 payload in a plugin's hooks
    # directory defeats both the hash baseline and the injection scan, and the
    # old exit code called that green. The docstring points at CI, where a
    # green audit that audited nothing is worse than no audit.
    if (result["baseline_missing"] or drifted or result["injection"]
            or result["symlinks"] or result["unreadable"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
