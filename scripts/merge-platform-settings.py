#!/usr/bin/env python3
"""Merge a platform settings template into the live settings, losing nothing.

## What this replaces, and what it cost

`scripts/setup-platform.sh` ended in one line, `cp "$TEMPLATE" "$TARGET"`, under
a header that said "Safe to run multiple times (idempotent)". It is not
idempotent. It is destructive, and `scripts/vps-sync.sh` invokes it from a cron
whenever the template changes.

MEASURED 2026-09-02 against the live workspace, by comparing the two files
rather than by reading the code: one run would have discarded 29 permission
entries and three whole top-level keys.

- `autoMemoryDirectory`, the pointer at the private data overlay's auto-memory.
  Losing it does not raise; it silently redirects every memory write.
- `outputStyle`, the operator's chosen way of being spoken to.
- `enabledPlugins`, which plugins are on.

None of those exist in any template, because every one of them is per-instance
by definition. The template cannot carry them and the copy cannot keep them.

## The shape of the fix

The caller has a real need: when the template gains a hook or a permission, that
addition must reach the live file. So refusing outright would trade silent data
loss for a silent update gap, which is the same disease facing the other way.

Merge instead, with one rule that decides every case: **the template proposes,
the live file disposes.** A key the live file already has keeps its live value.
A key only the template has is added. Permission lists are unioned, because a
permission is a grant and dropping one breaks a workflow that used to run.

Reads and writes JSON only, with no dependency beyond the standard library, so
it runs on a fresh clone under the system interpreter before `.venv` exists.

## `--check`: is this clone armed?

`.claude/settings.local.json` is gitignored, and it is the ONLY place the session
hooks are registered. The tracked `.claude/settings.json` registers exactly one.

MEASURED 2026-09-02 by comparing the two files: a clone where this script has
never run arms 2 hooks of 17. The 15 absent ones include `_dispatch.py`, which is
the single entry point for eleven PreToolUse walls, the release gate and the
secret scanner among them. Nothing anywhere reported that state, and the step
that fixes it (`bash scripts/setup-platform.sh`) was named in no document a
person setting the workspace up would read.

`--check` compares the two files and names every registration the live file
lacks. It exits 1 when the live file is absent or short, so it can be read by a
gate. It says only what a file comparison establishes: a hook is REGISTERED, not
that a hook RAN.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Lists under `permissions` are GRANTS. Union rather than replace: a permission
# present locally and absent from the template is one the operator added on
# purpose, and dropping it breaks a workflow that used to run without asking.
PERMISSION_LISTS = ("allow", "deny", "ask")


def merge_settings(template: dict, live: dict) -> dict:
    """Template proposes, live disposes. Pure: no I/O, no clock, no globals.

    Deliberately shallow apart from `permissions` and `hooks`. A deep merge of
    arbitrary nested JSON invents a policy for structures nobody has thought
    about, and the wrong guess there is as silent as the copy this replaces.
    Where a live value exists at the top level, it wins unexamined.
    """
    merged = dict(live)

    for key, value in template.items():
        if key not in merged:
            merged[key] = value

    # `permissions`: union each grant list, keep the live order, append the
    # template's additions. Order is preserved because these files are read by
    # humans and a reshuffled list produces a diff nobody can review.
    t_perms = template.get("permissions") or {}
    l_perms = merged.get("permissions") or {}
    if t_perms or l_perms:
        out = dict(l_perms)
        for name in PERMISSION_LISTS:
            live_entries = list(l_perms.get(name, []))
            seen = set(live_entries)
            for entry in t_perms.get(name, []):
                if entry not in seen:
                    live_entries.append(entry)
                    seen.add(entry)
            if live_entries or name in t_perms or name in l_perms:
                out[name] = live_entries
        for name, value in t_perms.items():
            if name not in out:
                out[name] = value
        merged["permissions"] = out

    # `hooks`: a hook GROUP the template adds is new capability and must land.
    # A group the live file already defines is left exactly as it is, because
    # merging two lists of matchers produces duplicate registrations, and a
    # duplicated hook runs twice.
    t_hooks = template.get("hooks") or {}
    l_hooks = merged.get("hooks") or {}
    if t_hooks or l_hooks:
        out_hooks = dict(l_hooks)
        for group, value in t_hooks.items():
            if group not in out_hooks:
                out_hooks[group] = value
        merged["hooks"] = out_hooks

    return merged


#: Any `*.py` token inside a hook's command string. The commands wrap the hook
#: in a `python3 -c` bootstrap, so the script name is the only stable part of
#: them; the surrounding bootstrap differs between platforms and has been
#: rewritten twice without any hook changing.
_PY_TOKEN = re.compile(r"[\w.-]+\.py")


def hook_registrations(settings: dict) -> set[tuple[str, str]]:
    """Every `(event, hook script)` pair a settings mapping registers.

    Pure, and deliberately tolerant of a malformed `hooks` block: a settings
    file that has been hand-edited into a shape this does not recognise yields
    FEWER pairs, so `--check` reports it as short rather than as armed. Failing
    toward over-reporting is the required direction here, per
    `.claude/rules/scope-claims.md`.
    """
    out: set[tuple[str, str]] = set()
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return out
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            entries = group.get("hooks")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                command = entry.get("command")
                if not isinstance(command, str):
                    continue
                for name in _PY_TOKEN.findall(command):
                    out.add((str(event), name))
    return out


def _load(path: Path, what: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"cannot read the {what} at {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"the {what} at {path} is not valid JSON: {exc}. Refusing to "
            f"merge, because guessing what it meant is how the live file gets "
            f"replaced by a guess."
        ) from exc
    if not isinstance(data, dict):
        raise SystemExit(f"the {what} at {path} is not a JSON object")
    return data


def _describe(before: dict, after: dict) -> list[str]:
    """What the merge added, in the operator's terms."""
    lines = []
    new_keys = sorted(set(after) - set(before))
    if new_keys:
        lines.append(f"added top-level key(s): {', '.join(new_keys)}")
    b = before.get("permissions") or {}
    a = after.get("permissions") or {}
    for name in PERMISSION_LISTS:
        gained = len(a.get(name, [])) - len(b.get(name, []))
        if gained:
            lines.append(f"added {gained} `{name}` permission entry(ies)")
    gained_hooks = sorted(set(after.get("hooks") or {}) - set(before.get("hooks") or {}))
    if gained_hooks:
        lines.append(f"added hook group(s): {', '.join(gained_hooks)}")
    return lines


def _check(template_path: Path, target_path: Path) -> int:
    """Name every hook registration the live file lacks. 0 armed, 1 short."""
    template = _load(template_path, "template")
    expected = hook_registrations(template)

    if not expected:
        # A template that registers nothing would make every clone pass, which
        # is a guard green over an empty corpus. Refuse instead.
        print(f"{template_path} registers no hooks at all. Refusing to certify "
              f"any clone against it.", file=sys.stderr)
        return 1

    if not target_path.exists():
        print(f"NOT ARMED: {target_path} does not exist, so 0 of "
              f"{len(expected)} session hook registrations are present.")
        print(f"  Fix: bash scripts/setup-platform.sh")
        return 1

    live = _load(target_path, "live settings file")
    missing = sorted(expected - hook_registrations(live))

    if missing:
        print(f"NOT ARMED: {target_path.name} is missing "
              f"{len(missing)} of {len(expected)} hook registration(s) that "
              f"{template_path.name} defines:")
        for event, script in missing:
            print(f"  {event:18s} {script}")
        print(f"  Fix: bash scripts/setup-platform.sh")
        return 1

    # Says only what a comparison of two files establishes. It does not say a
    # hook ran, and it cannot: this process never sees the harness load them.
    print(f"armed: {target_path.name} registers all {len(expected)} hook(s) "
          f"that {template_path.name} defines")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Merge a settings template into the live settings file.")
    parser.add_argument("template", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--force", action="store_true",
                        help="REPLACE the live file with the template, "
                             "discarding every local key. Backed up first.")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change; write nothing")
    parser.add_argument("--check", action="store_true",
                        help="report whether the live file registers every hook "
                             "the template does; write nothing; exit 1 if not")
    args = parser.parse_args()

    if not args.template.is_file():
        print(f"template not found: {args.template}", file=sys.stderr)
        return 1

    if args.check:
        return _check(args.template, args.target)

    if not args.target.exists():
        # First install. No merge to do and nothing to lose, so this path needs
        # no JSON parsing at all and works on a clone that has no venv yet.
        if args.dry_run:
            print(f"would create {args.target} from {args.template.name}")
            return 0
        args.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.template, args.target)
        print(f"created {args.target.name} from {args.template.name}")
        return 0

    template = _load(args.template, "template")
    live = _load(args.target, "live settings file")

    result = template if args.force else merge_settings(template, live)
    changes = _describe(live, result)

    if args.force:
        lost = sorted(set(live) - set(template))
        if lost:
            print(f"--force DISCARDS local key(s): {', '.join(lost)}")

    if result == live:
        print(f"{args.target.name} already carries everything in "
              f"{args.template.name}; nothing to do")
        return 0

    if args.dry_run:
        print(f"would update {args.target.name}:")
        for line in changes or ["values differ"]:
            print(f"  {line}")
        return 0

    # Back up BEFORE writing, and beside the file so the operator finds it.
    # `.claude/settings.local.json.bak-*` is gitignored; without that entry a
    # backup of this file would be an untracked candidate carrying the
    # operator's private paths and permissions into a public repository.
    # `.astimezone()` rather than the workspace's `get_default_tz()`: this file
    # deliberately imports nothing outside the standard library, because it runs
    # on a fresh clone whose `.venv` does not exist yet. Aware rather than naive
    # so the stamp is unambiguous, and local so it matches the other filenames
    # the operator sees in that directory.
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    backup = args.target.with_name(f"{args.target.name}.bak-{stamp}")
    shutil.copy2(args.target, backup)

    # Atomic: a half-written settings file is a workspace that will not start.
    tmp = args.target.with_name(f"{args.target.name}.tmp")
    tmp.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    tmp.replace(args.target)

    print(f"updated {args.target.name} (backup: {backup.name})")
    for line in changes or ["values differ"]:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
