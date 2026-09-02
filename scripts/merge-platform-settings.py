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
"""
from __future__ import annotations

import argparse
import json
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
    args = parser.parse_args()

    if not args.template.is_file():
        print(f"template not found: {args.template}", file=sys.stderr)
        return 1

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
