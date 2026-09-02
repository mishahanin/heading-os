#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""memlog — an append-only memory log: LLM-optimal working memory for a skill.

A memlog is the dense, chronological record of everything that mattered in a piece of
work — every item the user generated or accepted — kept minimal like human memory: only
what's important, never bloated. It persists ACROSS sessions, so a fresh session can
load it and continue. It is NOT a deliverable; downstream artifacts (a deal brief, a
post series, a strategy memo) are *derived* from it on demand. The host skill supplies
the vocabulary by how it calls `append` — the tool stays neutral.

In this workspace it backs long, multi-turn skills that must survive context compaction:
`/deal-strategy` (a negotiation worked across many turns) and `/linkedin-series` (a
multi-post plan). On resume, the host skill reads the existing `.memlog.md` itself and
continues with `append`/`set` — it does NOT re-run `init` (init errors if the file
already exists, which is the intended guard).

It is a FLAT log: there are no sections or grouping. Every entry is one line, recorded
at the END in the order it happened. The chronology itself is the structure — an event
like "started technique X" is just another entry, same as an idea or an insight.

Two invariants make it trustworthy:

  1. Append-only, chronological. Entries land at the end, in the order they happen.
     Nothing is ever inserted backward, reordered, or grouped.
  2. Write-only / blind. Every command is an atomic, context-free write and echoes the
     new state as JSON, so the caller never re-reads the file mid-session. The one time
     the file is read is on resume — and the caller reads it itself, not via this script.

The file shape (.memlog.md):

    ---
    topic: ExampleTelco ODUN.ONE expansion — pricing round
    goal: land the line-rate POV without discount erosion
    status: active
    updated: 2026-06-04T14:22
    ---

    - (note) counterpart type: analyst; precise numbers matter
    - (decision) anchor at 347,850 not a round figure
    - (insight) their procurement deadline is the real lever, not the price
    - (direction) hold the discount; trade on deployment scope instead

Each entry may carry an optional `--type` — what KIND it is (idea, insight, question,
decision, technique, …) — and an optional `--by` naming who it came from (e.g. `user`,
`coach`), for sessions where authorship matters. Both render into one short inline tag:
`(idea)`, `(idea by user)`, `(by coach)`. Omit them for a plain note. The host skill
names the vocabulary; the script does not.

Commands:
  init   --workspace DIR [--field k=v ...]              create the memlog (errors if it exists)
  append --workspace DIR --text STR [--type T] [--by W]  append one entry at the end
  set    --workspace DIR --key K --value V              set/replace a frontmatter field

`append` and `set` exit 2 when the memlog does not exist, the mirror of init's
guard. A frontmatter key may not be empty, and may not carry a newline, a colon,
or surrounding whitespace, because any of those rewrites the file's shape from
inside the fence; a value is free text and is neutralized on render.

The workspace is the run folder; the memlog is always {workspace}/.memlog.md.
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

MEMLOG = ".memlog.md"


def now() -> str:
    # Standalone PEP-723 script (no workspace deps): attach the system-local
    # tzinfo via .astimezone() to stay tz-aware without importing get_default_tz.
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M")


def memlog_path(workspace: str) -> Path:
    return Path(workspace) / MEMLOG


def split(text: str) -> tuple[dict, str]:
    """Return (frontmatter dict in source order, body str). Frontmatter is plain key: value.

    The closing fence is the first line that is *exactly* `---`, so a `---` inside a
    field value (topic/goal are free user text) never truncates the frontmatter.
    """
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(".memlog.md has no frontmatter")
    end = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
    if end is None:
        raise ValueError(".memlog.md frontmatter is not terminated")
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, "\n".join(lines[end + 1:]).lstrip("\n")


def render(meta: dict, body: str) -> str:
    # Neutralize newlines in values so a multi-line field can't break the fence on re-read.
    fm = "\n".join(f"{k}: {' '.join(str(v).splitlines())}" for k, v in meta.items())
    return "---\n" + fm + "\n---\n\n" + body.rstrip("\n") + "\n"


def bad_key(key: str) -> str | None:
    """Why `key` cannot be a frontmatter key, or None when it can.

    `render` neutralizes newlines in VALUES, which leaves the KEY as the one
    field that can still rewrite the file's shape from the inside. Measured
    2026-08-30: `set --key $'note\nstatus' --value done` on an active memlog
    wrote a `note` line and a `status: done` line into the frontmatter, and the
    next `split` read the goal-tracking `status` as `done` while the ack the
    command had just printed still said `active`. A `:` does the same thing on
    one line, since `split` breaks on the first one and files the tail under a
    shorter key.

    Refused at the two doors that accept a key, `init --field` and `set --key`,
    rather than escaped on the way out: a key that cannot be typed back at the
    tool is not a key worth keeping.
    """
    if not key:
        return "must not be empty"
    if key != key.strip():
        return "must not start or end with whitespace"
    if "\n" in key or "\r" in key:
        return "must not contain a newline"
    if ":" in key:
        return "must not contain ':'"
    return None


def touch(meta: dict) -> None:
    """Stamp `updated` and keep it last so the field order stays predictable."""
    meta.pop("updated", None)
    meta["updated"] = now()


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def entry_count(body: str) -> int:
    return sum(1 for ln in body.splitlines() if ln.startswith("- "))


def ack(path: Path, meta: dict, body: str) -> None:
    """Echo new state so the caller never re-reads the file to know where it stands."""
    print(json.dumps({
        "ok": True,
        "memlog": str(path),
        "status": meta.get("status", ""),
        "entries": entry_count(body),
    }))


def cmd_init(args) -> int:
    path = memlog_path(args.workspace)
    if path.exists():
        print(f"error: {path} already exists; use append/set to update it", file=sys.stderr)
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    meta: dict[str, str] = {}
    for pair in args.field or []:
        if "=" not in pair:
            print(f"error: --field expects key=value, got {pair!r}", file=sys.stderr)
            return 2
        k, v = pair.split("=", 1)
        k = k.strip()
        why = bad_key(k)
        if why:
            print(f"error: --field key {k!r} {why}", file=sys.stderr)
            return 2
        meta[k] = v.strip()
    meta.setdefault("status", "active")
    touch(meta)
    write_atomic(path, render(meta, ""))
    ack(path, meta, "")
    return 0


def cmd_append(args) -> int:
    path = memlog_path(args.workspace)
    # `cmd_init`'s guard from the other side, and the same exit code: init
    # refuses a memlog that is already there, append and set refuse one that is
    # not. Both used to meet the absence as a raw FileNotFoundError traceback,
    # in a script whose every other refusal is a printed line and exit 2.
    if not path.exists():
        print(f"error: {path} does not exist; run init first", file=sys.stderr)
        return 2
    meta, body = split(path.read_text(encoding="utf-8"))
    text = " ".join(args.text.split())  # collapse newlines/runs → one-line entry, no prose bloat
    label = args.type or ""
    if args.by:
        label = f"{label} by {args.by}".strip()  # attribution: "(idea by user)" / "(by coach)"
    tag = f"({label}) " if label else ""
    entry = f"- {tag}{text}"
    body = (body.rstrip("\n") + "\n" + entry) if body.strip() else entry  # always at the end
    touch(meta)
    write_atomic(path, render(meta, body))
    ack(path, meta, body)
    return 0


def cmd_set(args) -> int:
    path = memlog_path(args.workspace)
    if not path.exists():
        print(f"error: {path} does not exist; run init first", file=sys.stderr)
        return 2
    why = bad_key(args.key)
    if why:
        print(f"error: --key {args.key!r} {why}", file=sys.stderr)
        return 2
    meta, body = split(path.read_text(encoding="utf-8"))
    meta[args.key] = args.value
    touch(meta)
    write_atomic(path, render(meta, body))
    ack(path, meta, body)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="create the memlog")
    pi.add_argument("--workspace", required=True)
    pi.add_argument("--field", action="append", metavar="KEY=VALUE", help="frontmatter field (repeatable)")
    pi.set_defaults(func=cmd_init)

    pa = sub.add_parser("append", help="append one entry at the end")
    pa.add_argument("--workspace", required=True)
    pa.add_argument("--text", required=True)
    pa.add_argument("--type", help="entry kind, rendered as an inline tag")
    pa.add_argument("--by", help="who the entry came from (e.g. user, coach); rendered into the tag")
    pa.set_defaults(func=cmd_append)

    pset = sub.add_parser("set", help="set a frontmatter field")
    pset.add_argument("--workspace", required=True)
    pset.add_argument("--key", required=True)
    pset.add_argument("--value", required=True)
    pset.set_defaults(func=cmd_set)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        # The other half of "missing or malformed". `cmd_append` and `cmd_set`
        # both read the file through `split`, which REFUSES a memlog whose
        # frontmatter fence is absent or unterminated by raising ValueError --
        # the only ValueError this module raises. The absence of the file was
        # given a printed line and exit 2 on 2026-08-30; a `.memlog.md` a human
        # or a host skill had hand-edited into a broken fence still reached the
        # operator as a raw traceback until 2026-09-02. MEASURED that day on a
        # scratch memlog holding the single line `no fence here`:
        # `append --text hello` printed `ValueError: .memlog.md has no
        # frontmatter` over eight frames.
        #
        # Caught once at the dispatch rather than at each of the two `split`
        # call sites: a third command that reads the file inherits the refusal
        # instead of re-earning it.
        print(f"error: {memlog_path(args.workspace)} is malformed: {exc}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
