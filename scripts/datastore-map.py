#!/usr/bin/env python3
"""Generate the datastore map, into the PRIVATE overlay, from the live tree.

## Why this exists

`.claude/rules/datastore.md` carried a hand-written map of the datastore: a
bullet list of folders and what each holds. It was written on 2026-04-20 and
never regenerated, so by 2026-09-02 it had drifted. MEASURED that day: it named
14 subtrees and omitted three whole top-level directories, roughly 150 files,
one of which is `datastore/personal/`.

A hand-maintained inventory of a tree that grows every week is the defect shape
this repository keeps finding. It falls behind silently, and a stale map is
worse than no map, because it is read with confidence.

## Why the output is PRIVATE and this code is not

`.claude/rules/` resolves `engine`, and the engine repository is public. The
rule file therefore sat in public while describing a private tree. It stayed
clean only because the three directories it omitted are exactly the ones
holding real counterparty names: regenerating it in place would have published
those names on the first run.

So the two halves are split by what they are, which is what
`.claude/rules/classification.md` asks for. This generator is code and ships
public. Its output is data and is written to `<data-root>/reference/`, which is
the private overlay. The rule keeps the policy, which is behaviour, and points
here for the inventory.

## What the map carries, and what it deliberately does not

It carries structure: directory names, file counts, the mix of file types, the
routing destination of each subtree, and how much of each subtree is reachable
by search. It does NOT carry file contents. The point is to answer "what is in
here and can I read it", not to duplicate the tree.

## Console-first

`--check` is the non-web verification path: it exits 1 when the map on disk no
longer matches the live tree, so staleness is a failure a gate or a timer can
see, not something a reader has to notice.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.paths import load_env  # noqa: E402
from scripts.utils.workspace import (  # noqa: E402
    get_corporate_root,
    get_datastore_dir,
    get_default_tz,
    get_routing_destination,
)

MAP_FILENAME = "datastore-map.md"

# Suffixes whose content a reader cannot open directly. Each one needs an
# `-extract.md` companion or it is invisible to the memory index, and therefore
# to `/recall`. This list is the reason the map reports reach at all.
OPAQUE_SUFFIXES = {
    ".pdf", ".xlsx", ".pptx", ".docx", ".dotx", ".xls", ".ppt", ".doc",
    ".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".otf", ".ttf", ".woff", ".woff2", ".eot", ".mp4", ".mp3", ".pcap",
}

# Suffixes a reader and the index can both read as they are.
READABLE_SUFFIXES = {".md", ".txt", ".json", ".jsonl", ".csv", ".yaml",
                     ".yml", ".html", ".css", ".py", ".sh", ".xml"}


def map_path() -> Path:
    """Where the generated map goes. Resolved at call time, never at import.

    `get_corporate_root()`, NOT `get_reference_dir()`. That distinction is the
    whole point of this function and it was got wrong once, on 2026-09-02,
    during the very change that split the map out of the public rule.

    `get_reference_dir()` returns the ENGINE root for the operator's own
    workspace, because `reference/` is engine content that ships in the public
    clone. Calling it here wrote a file naming every real datastore directory
    straight into the public repository. It was caught because it was still
    untracked, which is luck, not a control.

    `get_corporate_root()` resolves to the private data overlay, which is where
    the other `private`-routed `reference/` files already live.

    Resolved on every call for the reason documented at `datastore_dir()` in
    `scripts/datastore-extract.py`: frozen into a module constant, this would
    answer once during import and a caller that repointed the data root
    afterwards would still write the operator's real overlay.
    """
    return get_corporate_root() / "reference" / MAP_FILENAME


def refuse_if_inside_engine(target: Path) -> None:
    """Refuse to write the map anywhere inside the engine checkout.

    A belt beside `map_path()`'s brace. The map names real directories, the
    engine repository is public, and the failure that put it there was a single
    wrong helper call that no test and no scanner would have caught: the file
    was untracked, so `leak-guard.py` never saw it and the push scan never ran
    on it.

    This asks about the WRITE, not about the environment, so it holds however
    the path was derived.
    """
    engine = Path(__file__).resolve().parent.parent
    try:
        target.resolve().relative_to(engine)
    except ValueError:
        return  # outside the engine, which is the only acceptable answer
    raise SystemExit(
        f"{RED}REFUSING to write the datastore map inside the engine "
        f"checkout: {target}{RESET}\nThe map names real datastore directories "
        "and the engine repository is public. It belongs in the private data "
        "overlay. Check HEADING_OS_DATA and `get_corporate_root()`."
    )


def _walk(datastore: Path) -> list[Path]:
    """Every file in the datastore, skipping git internals."""
    return [
        p for p in datastore.rglob("*")
        if p.is_file() and ".git" not in p.parts
    ]


def _companion_stems(files: list[Path]) -> set[tuple[Path, str]]:
    """(parent, stem) of every `-extract.md` companion that exists."""
    return {
        (p.parent, p.stem[: -len("-extract")])
        for p in files
        if p.name.endswith("-extract.md")
    }


def survey(datastore: Path) -> dict:
    """Measure the tree. Pure: takes a path, returns numbers, writes nothing."""
    files = _walk(datastore)
    companions = _companion_stems(files)

    subtrees: dict[str, dict] = {}
    for path in sorted(files):
        rel = path.relative_to(datastore)
        top = rel.parts[0] if len(rel.parts) > 1 else "."
        entry = subtrees.setdefault(top, {
            "files": 0, "bytes": 0, "suffixes": Counter(),
            "opaque": 0, "opaque_reachable": 0, "reachable": 0,
            "newest": None, "newest_mtime": 0.0,
        })
        entry["files"] += 1
        try:
            stat = path.stat()
        except OSError as exc:
            print(f"{YELLOW}skipping unreadable {rel}: {exc}{RESET}",
                  file=sys.stderr)
            continue
        entry["bytes"] += stat.st_size
        entry["suffixes"][path.suffix.lower() or "(none)"] += 1
        if stat.st_mtime > entry["newest_mtime"]:
            entry["newest_mtime"] = stat.st_mtime
            entry["newest"] = str(rel)

        suffix = path.suffix.lower()
        if suffix in OPAQUE_SUFFIXES:
            entry["opaque"] += 1
            if (path.parent, path.stem) in companions:
                entry["opaque_reachable"] += 1
                entry["reachable"] += 1
        elif suffix in READABLE_SUFFIXES:
            entry["reachable"] += 1

    for top, entry in subtrees.items():
        if top == ".":
            # Files sitting at the datastore root are classified one by one,
            # not by a directory rule. Probing `datastore/` here reported the
            # directory default and mislabelled `INDEX.md`, which the map
            # routes `private` explicitly. With more than one root file the
            # honest answer is that there is no single destination.
            entry["routing"] = (
                get_routing_destination(f"datastore/{entry['newest']}")
                if entry["files"] == 1 else "per-file"
            )
        else:
            entry["routing"] = get_routing_destination(f"datastore/{top}/")
        entry["top_suffixes"] = entry["suffixes"].most_common(6)
        del entry["suffixes"]
        del entry["newest_mtime"]

    total = len(files)
    opaque = sum(e["opaque"] for e in subtrees.values())
    reachable = sum(e["reachable"] for e in subtrees.values())
    opaque_with_companion = sum(
        e["opaque_reachable"] for e in subtrees.values()
    )
    return {
        "generated": datetime.now(get_default_tz()).isoformat(timespec="seconds"),
        "root": str(datastore),
        "total_files": total,
        "opaque_files": opaque,
        "opaque_unreachable": opaque - opaque_with_companion,
        "reachable_files": reachable,
        "subtrees": dict(sorted(subtrees.items())),
    }


def render(data: dict) -> str:
    """The map, as markdown. Deterministic apart from the timestamp."""
    total = data["total_files"]
    reachable = data["reachable_files"]
    pct = (100 * reachable // total) if total else 0

    lines = [
        "# DataStore map (generated, do not edit by hand)",
        "",
        f"Generated {data['generated']} by `scripts/datastore-map.py`.",
        "",
        "This file is written into the PRIVATE data overlay because it names "
        "real directories. The policy that governs the datastore lives in the "
        "public engine at `.claude/rules/datastore.md`; this is the inventory "
        "that rule points at. Regenerate with `python scripts/datastore-map.py`.",
        "",
        "Every number here is measured from the tree at generation time. If "
        "the map disagrees with the tree, the tree wins and the map is stale: "
        "`python scripts/datastore-map.py --check` says so and exits 1.",
        "",
        "## Totals",
        "",
        f"- Files: **{total}**",
        f"- Readable without extraction: **{reachable}** ({pct}%)",
        f"- Binary files: **{data['opaque_files']}**, of which "
        f"**{data['opaque_unreachable']}** have no `-extract.md` companion and "
        "are therefore invisible to search",
        "",
        "## Subtrees",
        "",
        "| Directory | Routing | Files | Readable | Opaque | Main types | Newest |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for name, entry in data["subtrees"].items():
        types = ", ".join(f"{suf.lstrip('.')} {n}"
                          for suf, n in entry["top_suffixes"])
        newest = entry["newest"] or ""
        if len(newest) > 44:
            newest = newest[:41] + "..."
        lines.append(
            f"| `{name}` | {entry['routing']} | {entry['files']} | "
            f"{entry['reachable']} | {entry['opaque']} | {types} | `{newest}` |"
        )

    lines += [
        "",
        "## How to use this",
        "",
        "- **What is new**: `python scripts/datastore-log.py summary` reads "
        "git and answers what appeared, changed or vanished. This map answers "
        "what is here now; that tool answers what moved.",
        "- **Reaching a binary**: a PDF, spreadsheet or deck is invisible to "
        "search until `python scripts/datastore-extract.py` writes its "
        "`-extract.md` companion. The Opaque column counts the ones that still "
        "have none.",
        "- **Routing**: `private` never leaves the data overlay. `corporate` is "
        "shared down to executives. Neither is ever copied into the public "
        "engine repository, including into an example or a test fixture.",
        "",
    ]
    return "\n".join(lines)


def _body(text: str) -> str:
    """The map without its generation timestamp.

    `--check` compares content, not the clock. Including the timestamp would
    make every run report drift, which is a checker that always fires and is
    therefore a checker nobody reads.
    """
    return "\n".join(
        line for line in text.splitlines()
        if not line.startswith("Generated ")
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the datastore map into the private overlay.")
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the map on disk is stale; write nothing")
    parser.add_argument("--json", action="store_true",
                        help="print the survey as JSON on stdout")
    parser.add_argument("--stdout", action="store_true",
                        help="print the map instead of writing it")
    args = parser.parse_args()

    # BEFORE anything reads a clock. `HEADING_OS_TZ` lives in the gitignored
    # `.env` and is exported by nothing, so under systemd `get_default_tz()`
    # answers UTC unless this runs first. The generation stamp would then be
    # dated in UTC while the unit fires at 03:20 local, and around midnight the
    # two disagree by a day. `tests/test_timer_timezone.py` caught exactly that
    # here, which is the fourth time this workspace has shipped the defect.
    load_env(Path(__file__).resolve().parent.parent)

    datastore = get_datastore_dir()
    if not datastore.is_dir():
        print(f"{RED}no datastore at {datastore}. Nothing to map.{RESET}",
              file=sys.stderr)
        return 1

    data = survey(datastore)
    if data["total_files"] == 0:
        print(f"{RED}{datastore} holds no files. A map of an empty corpus is "
              f"not a map, and this is what a wrong data root looks like from "
              f"the outside.{RESET}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return 0

    text = render(data)
    if args.stdout:
        print(text)
        return 0

    target = map_path()
    refuse_if_inside_engine(target)

    # A read-only mirror must not be written, and `--check` is a read, so the
    # gate sits between them. The Steward VM pulls the data repo with
    # `git pull --ff-only` and nothing there ever commits, so ONE local write
    # aborts every later pull. That happened on 2026-08-30: five CRM cards were
    # rewritten as a side effect of a send, the mirror sat five commits behind
    # for three and a half days, and systemd reported SUCCESS throughout.
    # `reference/datastore-map.md` is tracked, so writing it there would wedge
    # the mirror the same way.
    if not args.check and os.environ.get("HEADING_OS_DATA_READONLY"):
        print(f"{YELLOW}HEADING_OS_DATA_READONLY is set: this host mirrors the "
              f"data repository and never writes to it. Skipping the map "
              f"write.{RESET} Run the generator on the operator's own "
              f"workstation and let the mirror pull the result.", file=sys.stderr)
        return 0

    if args.check:
        if not target.exists():
            print(f"{RED}no map at {target}. Run "
                  f"`python scripts/datastore-map.py` to write it.{RESET}",
                  file=sys.stderr)
            return 1
        try:
            current = target.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"{RED}cannot read {target}: {exc}{RESET}", file=sys.stderr)
            return 1
        if _body(current) != _body(text):
            print(f"{RED}the datastore map is STALE.{RESET} {target}",
                  file=sys.stderr)
            print(f"{YELLOW}Regenerate with `python "
                  f"scripts/datastore-map.py`.{RESET}", file=sys.stderr)
            return 1
        print(f"{GREEN}map is current{RESET} ({data['total_files']} files)")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    # Atomic: a reader must never see a half-written map, and a crash must
    # never leave one on disk.
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(target)

    pct = 100 * data["reachable_files"] // data["total_files"]
    print(f"{GREEN}wrote{RESET} {target}")
    print(f"  {BOLD}{data['total_files']}{RESET} files, "
          f"{BOLD}{data['reachable_files']}{RESET} readable without extraction "
          f"({pct}%), "
          f"{BOLD}{len(data['subtrees'])}{RESET} subtrees")
    return 0


if __name__ == "__main__":
    sys.exit(main())
