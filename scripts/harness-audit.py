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

Tests: tests/test_an_edit_that_deleted_the_addresses_it_promised_to_keep.py, tests/test_harness_audit.py, tests/test_harness_audit_contract.py, tests/test_an_audit_that_vouched_for_a_surface_it_never_read.py

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
#
# Five globs, and the last two were missing until 2026-08-29. Measured that day
# with one identical payload planted in all six surfaces: four were scanned and
# flagged, `.claude/commands/` and `.claude/hooks/` drew no finding, no
# `unreadable` entry and no note, and the run exited 0 over them.
#
# `.claude/commands/*.md` is a slash command's BODY, injected as the prompt the
# moment the operator types the command. That is the most direct instruction
# surface this repository owns, and it was the one nothing read.
#
# `.claude/hooks/**/*.py` is scanned AS TEXT, and it is Python rather than
# markdown, which is a deliberate choice with a known cost. The scan is a
# substring hunt over lines, so a hook that QUOTES the vocabulary it guards
# would trip it. Measured 2026-08-29: none of the 17 Python hooks here does, so
# nothing needed an allowance, and `prompt-guard.py` reaches the vocabulary
# through an import of `scripts/utils/injection_patterns.py` rather than by
# spelling it. The cost is worth paying, because a hook does not merely load -
# it EXECUTES on every tool call, so text nobody read matters more here than
# anywhere else on the surface. The `<!-- audit-skip-* -->` convention works in a
# Python comment as well, which is the escape hatch for a hook that grows a
# legitimate quote.
OUR_SURFACE_GLOBS = (".claude/skills/**/*.md", ".claude/rules/**/*.md",
                     ".claude/agents/**/*.md", ".claude/commands/**/*.md",
                     ".claude/hooks/**/*.py")
OUR_SURFACE_FILES = ("AGENTS.md", "CLAUDE.md")

# THIS SWEEP DELIBERATELY DOES NOT ASK GIT WHAT TO SKIP. Do not "fix" it.
#
# On 2026-08-29 a shard made `classification-health.py` git-aware, because that
# tool REPORTS a corpus and 427 of its 2363 rows were files git ignores. This
# tool is not that. It hunts prompt-injection phrasing in the surface the
# harness LOADS, and the harness does not consult `.gitignore` before reading a
# file. Measured the same day: the sweep sees 250 files and git ignores exactly
# one of them, a scratch `.sample-deck.marp-src-*.md` sitting inside a skill
# directory. That file is still readable by the harness, so excluding it would
# reduce security coverage to make a count tidier.
#
# The rule of thumb: a tool that REPORTS a corpus filters by git, a tool that
# SCANS for danger does not. Over-scanning here costs one extra finding;
# under-scanning costs the finding that mattered.

# Files in THIS REPOSITORY that legitimately contain the phrases this tool hunts.
#
# Matched as repository-relative paths, deliberately NOT as basenames. A basename
# allowance is the first thing an attacker aims at: ship a file called
# `prompt-guard.py` inside the plugin cache and disappear. Installed content is
# never covered by this list, whatever it calls itself.
#
# An entry naming a FILE matches only that exact path; an entry meaning a subtree
# must end in `/`. See `_is_allowed_repo_path` for what a boundary-less prefix
# match let through.
#
# EVERY ENTRY MUST BE REACHABLE BY THE CORPUS ABOVE, and
# `tests/test_an_audit_that_vouched_for_a_surface_it_never_read.py` asserts it.
# Six of the nine entries this list carried until 2026-08-29 named paths no glob
# could ever produce: `scripts/harness-audit.py`,
# `scripts/utils/injection_patterns.py`, `tests/`, `docs/SECURITY-MODEL.md`,
# `SECURITY.md`, and `.claude/hooks/prompt-guard.py`. They read as coverage and
# were carve-outs from a scan that never happened, which is how the two missing
# surfaces stayed invisible: the list looked like it already reached further than
# it did. Widening the corpus to a new tree means adding that tree's allowances
# back in the same change, rather than keeping them speculatively now.
#
# `.claude/hooks/prompt-guard.py` is NOT re-listed, even though adding
# `.claude/hooks/**/*.py` finally made it reachable. Measured 2026-08-29 with the
# allowance removed: the whole own-tree corpus produces zero findings, this hook
# included, because the vocabulary it guards moved out to
# `scripts/utils/injection_patterns.py` and the hook only imports it. Exempting a
# file that executes on every tool call, for a phrase it does not contain, would
# re-open the exact hole this change closes on the riskiest file on the surface.
# If a future edit gives it a legitimate quote, the audit says so out loud and
# the fix is the `<!-- audit-skip-* -->` markers or an entry here, decided then.
#
# The three that remain are reachable, and each also matches nothing today. They
# stay because those rules discuss the vocabulary by design and the next edit to
# one is likelier to quote it than not.
ALLOWED_REPO_PREFIXES = (
    ".claude/rules/security.md",
    ".claude/rules/lethal-trifecta.md",
    ".claude/rules/hidden-chars.md",
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

    This paragraph used to say rglob "descends THROUGH a symlinked directory"
    on Python 3.11. Measured on the pinned interpreter (3.11.15, 2026-08-30):
    it does NOT. Over `real/docs-link -> /tmp/rgtest/target`, `rglob("*")`
    yielded the link and nothing under it. The false half is corrected here
    rather than deleted, because a wrong claim about a past measurement is what
    the next audit re-derives from.

    The defect that was real: rglob yields the symlinked DIRECTORY itself, and
    a directory link carries no surface suffix, so the suffix filter discarded
    it before it could be reported. A `docs-link -> /tmp/payload` inside the
    plugin cache was therefore neither hashed nor named - unvouchable content
    on the loaded surface, and no finding. Whether an interpreter descends is
    also not this tool's to assume; `os.walk(followlinks=False)` states the
    answer instead of inheriting it. Directory links are now recorded before
    any suffix test and never descended.
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
            # `_is_loaded`, not a hardcoded True. An unreadable `hooks.json`
            # tells us nothing about the file's CONTENT, and it tells us nothing
            # NEW about activation either -- the settings files that switch a
            # plugin off are still perfectly readable, and this function already
            # holds their answer in `disabled`. Hardcoding True skipped that
            # answer, so a plugin set `false` in `enabledPlugins` whose cached
            # `hooks.json` was corrupt printed under "running in this session".
            # MEASURED 2026-08-30. That is the over-claim this file has been
            # fixed for twice (2026-08-12 superseded versions, 2026-08-20
            # `security-guidance`); `.claude/rules/scope-claims.md` obligation 1
            # says resolve the claim where a resolver exists, and here one does.
            # Absence of proof still reads as live, via `_is_loaded` itself.
            found.append({"event": "?", "command": f"<unreadable: {type(exc).__name__}>",
                          "source": str(path),
                          "loaded": _is_loaded(path, active, disabled)})
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
            # `isinstance`, like every other JSON read in this file. `json.loads`
            # returns any JSON type, so a settings file holding `[]` or `"x"`
            # reached `.get` and raised AttributeError, which the handler above
            # does not catch: the whole audit died with a traceback and produced
            # no report at all. This tool's own rule is that an unreadable record
            # degrades to "treat everything as live", never to silence, and a
            # crash is the loudest form of silence there is.
            block = data.get("hooks") if isinstance(data, dict) else None
            for entry in _hooks_from_mapping(block, str(settings)):
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

    `entries` is shape-checked here and not only at the top level. A hand-edited
    manifest whose `entries` is a JSON LIST parsed cleanly, passed the old
    isinstance test, and then killed `compare` with `TypeError: list indices
    must be integers` on the first path present in both -- and `--update-manifest`
    with `len(previous["entries"])`. A manifest nobody can read as a baseline is
    not a baseline, so it takes the same road as a missing one: reported loudly,
    never mistaken for agreement.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if "entries" in data and not isinstance(data["entries"], dict):
        return None
    return data


# ============================================================
# Injection in loaded content
# ============================================================

def _is_allowed_repo_path(rel: str) -> bool:
    """Whether a repo-relative path is one of the three allow-listed FILES.

    The boundary matters. A bare `rel.startswith(p)` also matched anything whose
    path merely begins with an entry's characters: `.claude/rules/security.md/`
    as a DIRECTORY holding `payload.md`, and the sibling file
    `.claude/rules/security.md.draft.md`. Both are reachable by the
    `.claude/rules/**/*.md` glob, and both were dropped from the injection scan
    and counted under the reviewed rule file's allowance. The comment above
    `ALLOWED_REPO_PREFIXES` argues at length that a basename allowance is
    attackable; a boundary-less prefix reopens the same shape inside the repo.
    Every entry today names a file, so a file entry matches by EQUALITY only.
    An entry that means a whole subtree has to say so by ending in `/`, which is
    a thing the author types on purpose rather than a boundary the matcher
    invents.
    """
    return any(rel == p or (p.endswith("/") and rel.startswith(p))
               for p in ALLOWED_REPO_PREFIXES)


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
    """Injection findings over the corpus named by `OUR_SURFACE_GLOBS`.

    Returns `(findings, scanned, unreadable, allowed_skipped)`. The corpus is
    this repository's own instruction and hook surface plus the installed plugin
    surface, and it is NOT everything that reaches a session: settings files,
    `.claude/hooks/*.sh`, MCP server output and anything the operator pastes are
    all outside it. The fourth return value is the allow-list drop count, which
    `.claude/rules/scope-claims.md` obligation 2 requires be reported rather than
    absorbed into silence - a skipped file used to leave no trace at all, so the
    printed sentence counted it as neither scanned nor excluded.
    """
    findings, scanned, unreadable, allowed_skipped = [], [], [], []

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
            allowed_skipped.append(rel)
            continue
        _scan_one(path, rel, findings, scanned, unreadable, honour_skip=True)

    for path in _surface_files(root):
        # No allowance here, by design. See ALLOWED_REPO_PREFIXES.
        _scan_one(path, f"plugins/{path.relative_to(root).as_posix()}",
                  findings, scanned, unreadable)

    return findings, scanned, unreadable, allowed_skipped


# ============================================================
# Output
# ============================================================

def _corpus_summary() -> str:
    """The corpus, DERIVED from the constants rather than described by hand.

    A hand-written sentence about coverage is the thing that goes stale the
    moment a glob is added, and a stale coverage sentence is the defect
    `.claude/rules/scope-claims.md` is about. Reading the constants means the
    printed claim cannot outrun the code that produces it.
    """
    return ", ".join((*OUR_SURFACE_GLOBS, *OUR_SURFACE_FILES))


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
        # Names the corpus, never "everything that loads". The scan reads this
        # repository's skills, rules, agents, commands and Python hooks plus the
        # installed plugin surface; settings files and shell hooks are outside
        # it, and the allow-listed files below were not read at all. Saying "in N
        # loaded file(s)" over that is the over-claim `.claude/rules/scope-claims.md`
        # exists to refuse.
        print(f"{GREEN}No injected instruction patterns{RESET} "
              f"{GRAY}in the {len(result['scanned'])} file(s) scanned: this "
              f"repository's {_corpus_summary()} plus the installed plugin "
              f"surface. Settings files and non-Python hooks are outside the "
              f"corpus.{RESET}")

    if result["allowed_skipped"]:
        print(f"{GRAY}{len(result['allowed_skipped'])} file(s) were NOT scanned: "
              f"they are allow-listed as legitimately carrying the phrases this "
              f"tool hunts, so nothing here is evidence either way:{RESET}")
        for path in result["allowed_skipped"]:
            print(f"  {GRAY}allowed  {path}{RESET}")

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
        # What the acceptance does NOT cover, named at the moment of acceptance.
        # A normal run exits 1 over both of these, calling them content the audit
        # cannot vouch for; the update branch had them in hand and printed
        # neither, so the one moment a human asserts "I reviewed this" minted a
        # silently partial baseline. `.claude/rules/scope-claims.md` obligation
        # 2: an exclusion nobody can see reads as coverage. Reported, not
        # refused -- the operator asked to accept, and a caveat they can read is
        # the honest half of that.
        if hash_unreadable:
            print(f"{YELLOW}{len(hash_unreadable)} file(s) could not be read and "
                  f"are NOT in this baseline{RESET} {GRAY}nothing here was "
                  f"reviewed and nothing will detect a change to it{RESET}",
                  file=sys.stderr)
            for entry in hash_unreadable:
                print(f"  {YELLOW}{entry['error']:<20}{RESET} {entry['path']}",
                      file=sys.stderr)
        if symlinks:
            print(f"{YELLOW}{len(symlinks)} symlink(s) on the accepted surface"
                  f"{RESET} {GRAY}not followed, so not hashed and not covered by "
                  f"this baseline{RESET}", file=sys.stderr)
            for path in symlinks:
                print(f"  {YELLOW}symlink {RESET} {path}", file=sys.stderr)
        return 0

    baseline = read_manifest(manifest_path)
    findings, scanned, scan_unreadable, allowed_skipped = scan_loaded_content(
        repo, root)

    # Deduped on the FILE, not on the label. The two producers name the same
    # plugin file differently -- `build_surface_index` records the path relative
    # to the plugin root, `scan_loaded_content` prefixes it with `plugins/` -- so
    # keying on `entry["path"]` never matched and one chmod-000 file was listed
    # twice and counted as two. MEASURED 2026-08-30. The printed line is a count
    # of files that could not be read, so a doubled count is a wrong measurement,
    # not a cosmetic repeat. Repo-relative labels never start with `plugins/`
    # (the own-tree corpus is `.claude/**`, `AGENTS.md`, `CLAUDE.md`), so
    # stripping that one prefix cannot collide two different files.
    seen, unreadable = set(), []
    for entry in hash_unreadable + scan_unreadable:
        key = entry["path"]
        if key.startswith("plugins/"):
            key = key[len("plugins/"):]
        if key not in seen:
            seen.add(key)
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
        "allowed_skipped": allowed_skipped,
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
