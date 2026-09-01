#!/usr/bin/env python3
"""Shard the engine tree, audit each shard with a model, keep the reports.

Usage:
    python scripts/engine-audit.py --list
    python scripts/engine-audit.py
    python scripts/engine-audit.py --only tests-33-p1
    python scripts/engine-audit.py --campaign 2026-09-01_my-campaign

Resumable: a shard whose report already exists is skipped, so a kill or a quota
403 costs only the shards in flight. That property is the whole design, because
these runs are long and get interrupted.

WHY THIS FILE IS TRACKED, which is the point of it existing at all.

The first two versions of this runner were scratch scripts. The first wrote its
reports to `/tmp` and lost 58 finished ones when a WSL re-init emptied that
directory on 2026-08-24; they had cost real model quota and could not be
regenerated. The second moved the reports to `.tmp/audit/out/` on ext4, which
survived the reboot but is covered by `.gitignore:127`, so 139 reports, the
pending list, the operator's decisions and THE RUNNER ITSELF were all one
`rm -rf .tmp` away from gone. Nothing about a 22 MB ledger of audit findings
belongs in an ignored directory.

So: the code lives here, in the engine, tracked and public. The reports go to the
private DATA overlay under `outputs/operations/audits/<campaign>/`, tracked
there, because a report about this repository's defects is operator data and not
shareable code.

Every model call goes through `scripts/utils/proxy_transport.call_model`. No
direct provider call and no credential read, ever, per
`auto-memory/never-bypass-the-proxy.md`.

What this does NOT do: judge the findings. A model report is a candidate list.
Confirming one means reading the code it points at, and refuting one is as
valuable as confirming it. The 2026-08 campaign found 42 confirmed against 69
refuted, so most of what comes back here is wrong.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import itertools
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import BOLD, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.proxy_transport import call_model  # noqa: E402
from scripts.utils.workspace import (  # noqa: E402
    get_default_tz,
    get_outputs_dir,
    get_workspace_root,
)

MODEL = "k3"
CHARS_PER_SHARD = 90_000     # ~22k tokens of source per request

# 12k, not 32k. `scripts/utils/proxy_transport` carries the measurement in its
# own source: 8192 tokens answered in 158 s, and 32768 blew a 240 s ceiling
# outright, because a bigger budget makes the model think longer. At 32k every
# shard hit the 300 s socket ceiling, retried at a higher budget with the
# ceiling doubled, and took over ten minutes each. An audit report is 2-4k
# tokens of output.
MAX_TOKENS = 12_000
TIMEOUT_S = 420

# Three at a time. Each worker holds one shard's text (~90 KB) and then waits on
# the network, so the cost is latency rather than memory: measured RSS for the
# serial runner was 101 MB.
WORKERS = 3

GROUPS = {
    "scripts": ("scripts", "*.py", ("scripts/utils",)),
    "scripts-utils": ("scripts/utils", "*.py", ()),
    "tests": ("tests", "*.py", ()),
    "hooks": (".claude/hooks", "*.py", ()),
}

PROMPT_HEAD = """You are auditing production Python from the HEADING OS engine.

Report ONLY defects you can point at in the code shown. For each finding give:
the file path, the function, the verbatim offending line(s), what breaks in
concrete terms, how to reproduce it, and a severity of HIGH / MEDIUM / LOW.

Rules:
- Never invent a line number. The files are pasted without them; locate a
  defect by file + function + the verbatim line.
- Do not report style, formatting, naming, or unused imports.
- Do not list clean files.
- A comment or docstring that contradicts the code IS a defect: say which is
  wrong.
- End with two sections: "What I would do, in order" and "Risks and
  assumptions in this audit", the second naming every dependency you were not
  shown and every assumption a finding rests on.

Files follow.
"""

ROOT = get_workspace_root()
FILE_MARKER = re.compile(r"===== FILE: (.+?) =====")


def campaign_dir(campaign: str) -> Path:
    """Where one campaign's reports live, in the private DATA overlay.

    Resolved on CALL, never at import: a module-level constant would freeze the
    answer during its own import and a caller repointing `HEADING_OS_DATA` would
    still write the operator's real data. See
    `tests/test_a_tracked_dir_list_frozen_before_any_test_could_move_it.py`.
    """
    return get_outputs_dir() / "operations" / "audits" / campaign / "shard-reports"


def default_campaign() -> str:
    """Today, in the operator's configured zone rather than the host's.

    A naive `datetime.now()` reads the ambient host zone, so two machines in
    one fleet would name the same campaign differently and the resume key
    would miss every report the other wrote. `HEADING_OS_TZ` is the single
    answer, per `.claude/rules/voice.md`.
    """
    return f"{datetime.now(get_default_tz()).strftime('%Y-%m-%d')}_engine-audit"


def build_shards() -> list[tuple[str, str]]:
    """(name, text) for every shard, deterministic in path order.

    Deterministic matters more than it looks: the name is the resume key, so a
    shard that changes name between runs is a shard that gets audited twice and
    one that never gets audited at all.
    """
    out: list[tuple[str, str]] = []
    for group, (rel, glob, excludes) in GROUPS.items():
        base = ROOT / rel
        if not base.is_dir():
            continue
        files = sorted(
            p for p in base.rglob(glob)
            if p.is_file()
            and not any(str(p).startswith(str(ROOT / e)) for e in excludes)
            and "__pycache__" not in p.parts
        )
        part, buf, size, idx = 1, [], 0, 0
        for p in files:
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                print(f"skip {p}: {exc}", file=sys.stderr)
                continue
            block = f"\n\n===== FILE: {p.relative_to(ROOT)} =====\n{text}"
            if size and size + len(block) > CHARS_PER_SHARD:
                out.append((f"{group}-{idx:02d}-p{part}", "".join(buf)))
                part, buf, size = part + 1, [], 0
                if part > 4:
                    idx, part = idx + 1, 1
            buf.append(block)
            size += len(block)
        if buf:
            out.append((f"{group}-{idx:02d}-p{part}", "".join(buf)))
    return out


def coverage(shards: list[tuple[str, str]]) -> dict[str, tuple[int, int]]:
    """{group: (files on disk, files inside a shard)}.

    A sharding walk that silently drops files produces a report that reads
    complete and is not, and nothing downstream can tell. This is the check that
    says so: it re-derives the on-disk set and compares it against the file
    markers actually embedded in the shard text, rather than trusting the loop
    above to have added what it iterated.
    """
    embedded: set[str] = set()
    for _, text in shards:
        embedded |= set(FILE_MARKER.findall(text))
    result = {}
    for group, (rel, glob, excludes) in GROUPS.items():
        base = ROOT / rel
        if not base.is_dir():
            continue
        on_disk = {
            str(p.relative_to(ROOT)) for p in base.rglob(glob)
            if p.is_file()
            and not any(str(p).startswith(str(ROOT / e)) for e in excludes)
            and "__pycache__" not in p.parts
        }
        result[group] = (len(on_disk), len(on_disk & embedded))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true",
                    help="print pending shards and the coverage table; run nothing")
    ap.add_argument("--only", help="run exactly one shard by name")
    ap.add_argument("--campaign", default=None,
                    help="campaign directory name (default: today's date)")
    args = ap.parse_args()

    campaign = args.campaign or default_campaign()
    out = campaign_dir(campaign)
    shards = build_shards()

    if args.list:
        print(f"{BOLD}{len(shards)} shard(s){RESET}  {GRAY}campaign {campaign}{RESET}")
        print(f"{GRAY}reports -> {out}{RESET}")
        for group, (disk, seen) in coverage(shards).items():
            missing = disk - seen
            colour = GREEN if missing == 0 else RED
            print(f"  {group:14s} {disk:4d} file(s) on disk, "
                  f"{colour}{seen:4d} inside a shard{RESET}"
                  + ("" if missing == 0 else f"  {RED}{missing} MISSING{RESET}"))
        pending = [(n, t) for n, t in shards if not (out / f"{n}.md").exists()]
        print(f"\n{len(pending)} pending")
        for name, text in pending[:40]:
            print(f"  {name:26} {len(text):>7} chars")
        if len(pending) > 40:
            print(f"  ... and {len(pending) - 40} more")
        return 0

    out.mkdir(parents=True, exist_ok=True)
    pending = [(n, t) for n, t in shards
               if not (out / f"{n}.md").exists()
               and (args.only is None or n == args.only)]
    if not pending:
        print(f"{GREEN}nothing pending{RESET} {GRAY}({len(shards)} shard(s), all reported){RESET}")
        return 0

    quota_gone = threading.Event()
    done = itertools.count(1)

    def _one(name: str, text: str) -> None:
        if quota_gone.is_set():
            return
        try:
            reply = call_model(MODEL, PROMPT_HEAD + text, max_tokens=MAX_TOKENS,
                               temperature=0.2, timeout=TIMEOUT_S)
        except Exception as exc:            # noqa: BLE001 - reported, not raised
            msg = str(exc)
            print(f"  {RED}ERROR{RESET} {name}: {type(exc).__name__}: {msg[:180]}",
                  file=sys.stderr, flush=True)
            if "permission_error" in msg or "403" in msg:
                # Stop STARTING work. In-flight calls finish and save; the rest
                # stay pending, which is what makes the run resumable.
                quota_gone.set()
            return
        # Written only on success. A half report would be skipped by the resume
        # logic on the next run, so the shard would never be audited and nothing
        # would say so.
        (out / f"{name}.md").write_text(str(reply), encoding="utf-8")
        print(f"[{next(done)}/{len(pending)}] wrote {name}.md", flush=True)

    with cf.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(lambda item: _one(*item), pending))

    on_disk = len(list(out.glob("*.md")))
    tail = f" {YELLOW}(stopped early: quota){RESET}" if quota_gone.is_set() else ""
    print(f"done; {on_disk} report(s) in {out}{tail}")
    return 1 if quota_gone.is_set() else 0


if __name__ == "__main__":
    sys.exit(main())
