#!/usr/bin/env python3
"""Judge dispatcher for `/scrutinize` - the seam that owns what prose could not.

Three things move out of the model's hands and into this file.

**Family assignment.** The adversarial split binds Skeptic and Meta-Judge to
different families, because a Meta-Judge ruling on its own family's refusal to
refute is the exact self-preference the mitigation exists to block. With a
two-family roster that is not a "rotation", it is a fixed split with a per-run
side swap, and the swap is derived from the run id rather than chosen.

**The sensitivity gate.** It consults `sensitivity_is_declared()` and NEVER
`is_sensitive()`. The latter is fail-closed: unset resolves sensitive, so a
dispatcher asking it would refuse every proxy call on an ordinary machine and
kill the k3 side of the roster permanently. That is not hypothetical - the
2026-08-09 scrutiny pass of this plan ran without its k3 side for exactly that
reason. A human who typed `SENSITIVE_MODE=on` knows something no default does;
an unset variable is the machine's default, not a declaration.

**Reproduction.** The model proposes a command, this file runs it, and the
fail-to-pass transition is observed across the fix rather than narrated. A
finding reproduced by a command needs no jury, which is why `REPRODUCED` outranks
a debate - but only the harness may write it.

Every result lands in the run record through `scripts/utils/scrutinize_record.py`.
What this does NOT do is make omission impossible: the Claude-side judge IS the
running session, so its verdict is still supplied. `--validate` on the record is
what makes that omission visible.

Usage:
  python scripts/scrutinize-dispatch.py --pass-start --run-id <id> --target <t>
  python scripts/scrutinize-dispatch.py --judge --run-id <id> --target <t> \\
      --finding H1 --pass 2.5a --side skeptic --brief-file /tmp/brief.txt
  python scripts/scrutinize-dispatch.py --role-scan --run-id <id> --target <t> \\
      --paths scripts/templates/systemd/reminders.timer
  python scripts/scrutinize-dispatch.py --currency --run-id <id> --target <t> \\
      --paths scripts/bridge-daemon.py
  python scripts/scrutinize-dispatch.py --reproduce --run-id <id> --target <t> \\
      --finding H1 --cmd "python3 -m pytest tests/test_x.py -q"

Exit codes:
  0  success, or a graceful currency degradation
  1  degraded: no judge verdict was produced (sensitivity or proxy)
  2  bad arguments
  3  the reproduction did not reproduce, or the promotion had nothing to join
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import re
import shlex
import subprocess  # nosec B404 - fixed argv reproduction commands, never shell=True
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.colors import GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.council_models import get_model  # noqa: E402
from scripts.utils.proxy_transport import call_model  # noqa: E402
from scripts.utils.scrutinize_record import (  # noqa: E402
    append_row,
    last_reproduction,
)
from scripts.utils.sensitive import sensitivity_is_declared  # noqa: E402

# Neither judge is pinned in this file, and that is deliberate.
#
# The Kimi side resolves through the council seam, so a new flagship is
# `python scripts/council-models.py --set kimi_reasoning=<new>` and no code
# changes. The Claude side has no pin AT ALL: that judge is the running session,
# so it is always whatever Opus the session is on, and a newer Opus reaches the
# judge layer the day it ships without anyone editing this skill.
KIMI_REASONING = "high"
_PYPI_BASE = "https://pypi.org/pypi/"
REPRODUCTION_TIMEOUT_S = 300


def kimi_model() -> str:
    """The Kimi judge pin, resolved at call time so a bump needs no code edit."""
    return get_model("kimi_reasoning")

# ============================================================
# Role lenses - a path match, not a judgement call
# ============================================================
# Each entry is (lens, glob). A lens fires iff a path in scope matches. The
# taxonomies these lenses carry live in references/role-lenses.md; this table is
# only the trigger, because a trigger decided by the model is not a trigger.
LENS_GLOBS: tuple[tuple[str, str], ...] = (
    ("ops", "*.service"),
    ("ops", "*.timer"),
    ("ops", "*install-*.sh"),
    ("ops", "*/templates/systemd/*"),
    ("boundary", "*routing-map.yaml"),
    ("boundary", "*/.claude/hooks/*"),
    ("boundary", ".claude/hooks/*"),
    ("boundary", "*leak-guard.py"),
    ("boundary", "*engine_guard.py"),
    ("boundary", "*tool-risk.json"),
)

# The scheduler lens is content-triggered, not path-triggered: a daemon is a
# plain .py file and only its imports say it schedules anything.
_SCHEDULER_MARKERS = ("apscheduler", "add_job")

# Import name is not distribution name. Assuming they are equal is how a currency
# check silently reports on a package nobody depends on.
IMPORT_TO_DISTRIBUTION: dict[str, str] = {
    "apscheduler": "APScheduler",
    "yaml": "PyYAML",
    "fastapi": "fastapi",
    "pydantic": "pydantic",
    "requests": "requests",
    "PIL": "pillow",
    "playwright": "playwright",
    "exchangelib": "exchangelib",
    "telethon": "Telethon",
    "httpx": "httpx",
    "uvicorn": "uvicorn",
    "pytest": "pytest",
}


def lenses_for(paths: list[str]) -> list[str]:
    """Which lenses fire for this scope. Order is stable, duplicates removed."""
    fired: list[str] = []
    for raw in paths:
        p = str(raw)
        for lens, glob in LENS_GLOBS:
            if fnmatch.fnmatch(p, glob) and lens not in fired:
                fired.append(lens)
        if "scheduler" not in fired and p.endswith(".py"):
            try:
                text = Path(p).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if any(marker in text for marker in _SCHEDULER_MARKERS):
                fired.append("scheduler")
    return fired


def distribution_for(import_name: str) -> str | None:
    """Map an import to its distribution, or None when it is not third-party."""
    return IMPORT_TO_DISTRIBUTION.get(import_name)


def imports_in(paths: list[str]) -> list[str]:
    """Top-level import names across the scope's Python files."""
    found: list[str] = []
    for raw in paths:
        p = Path(raw)
        if p.suffix != ".py" or not p.exists():
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.append(node.module.split(".")[0])
    seen: list[str] = []
    for name in found:
        if name not in seen:
            seen.append(name)
    return seen


# ============================================================
# Family assignment
# ============================================================
def swap_for_run(run_id: str) -> bool:
    """Derive the side swap from the run id, so nothing chooses it per call."""
    digest = hashlib.sha256(run_id.encode("utf-8")).digest()
    return bool(digest[0] & 1)


def assign_families(*, swap: bool) -> dict[str, str]:
    """Skeptic and Meta-Judge are never the same family. That is the whole rule."""
    if swap:
        return {"advocate": "claude", "skeptic": "claude", "meta": "kimi"}
    return {"advocate": "kimi", "skeptic": "kimi", "meta": "claude"}


# ============================================================
# Judge
# ============================================================
def judge(
    *, run_id: str, target: str, finding_id: str, pass_: str, brief: str,
    family: str, verdict: str | None = None,
) -> int:
    """Dispatch one judge call and record its verdict. Returns an exit code.

    `family="claude"` records a verdict the running session produced, without a
    proxy call - the Claude judge IS this session. `family="kimi"` makes the call.
    """
    if family == "claude":
        append_row(run_id=run_id, kind="verdict", target=target,
                   finding_id=finding_id, pass_=pass_, judge_family="claude",
                   verdict=verdict)
        return 0

    if sensitivity_is_declared():
        append_row(
            run_id=run_id, kind="degraded", target=target, finding_id=finding_id,
            degraded="SENSITIVE_MODE declared; no proxy call made, k3 side not exercised")
        print(f"{YELLOW}sensitive session declared: k3 judge not dispatched{RESET}",
              file=sys.stderr)
        return 1

    try:
        response = call_model(
            kimi_model(), brief, temperature=0.4, max_tokens=4096,
            reasoning_effort=KIMI_REASONING,
        )
    except Exception as exc:  # noqa: BLE001 - any transport fault degrades identically
        append_row(run_id=run_id, kind="degraded", target=target,
                   finding_id=finding_id, degraded=f"proxy call failed: {exc}")
        print(f"{RED}proxy call failed: {exc}{RESET}", file=sys.stderr)
        return 1

    text = response if isinstance(response, str) else str(response)
    append_row(run_id=run_id, kind="verdict", target=target, finding_id=finding_id,
               pass_=pass_, judge_family="kimi", verdict=_verdict_in(text))
    print(text)
    return 0


_VERDICT_RE = re.compile(
    r"\b(REFUTE_PARTIAL|REFUTATION_FAILED|REFUTED|CORRECT_DOWNGRADE|CORRECT"
    r"|INCORRECT|AMBIGUOUS)\b")


def _verdict_in(text: str) -> str | None:
    """First recognised verdict token in a judge response, or None."""
    match = _VERDICT_RE.search(text or "")
    return match.group(1) if match else None


# ============================================================
# Role scan and currency
# ============================================================
def role_scan(*, run_id: str, target: str, paths: list[str]) -> int:
    for lens in lenses_for(paths):
        append_row(run_id=run_id, kind="role", target=target, role=lens)
    return 0


def pinned_version(distribution: str) -> str | None:
    """The pin from uv.lock, or None when the distribution is not locked."""
    lock = Path(__file__).resolve().parent.parent / "uv.lock"
    if not lock.exists():
        return None
    text = lock.read_text(encoding="utf-8", errors="ignore")
    pattern = re.compile(
        r'^\[\[package\]\]\s*\nname\s*=\s*"' + re.escape(distribution.lower())
        + r'"\s*\nversion\s*=\s*"(?P<v>[^"]+)"',
        re.MULTILINE | re.IGNORECASE)
    match = pattern.search(text)
    return match.group("v") if match else None


def latest_version(distribution: str) -> str | None:
    """Latest released version, from the index that actually carries versions.

    Not Context7. Context7 exposes a `versions` array and `scripts/context7.py`
    reads it, but measured 2026-08-09 that array is empty for every distribution
    this engine depends on: it is a documentation index, not a release registry,
    and asking it for a version number returns nothing forever. A currency check
    built on it would answer `inconclusive` on every run - a check that cannot
    fail, which is exactly the defect class this whole change exists to remove.

    So the version comes from PyPI's read-only JSON API (stdlib urllib, no key,
    no dependency, and only the package name leaves the machine), and Context7
    keeps the job it is good at: `docs_url` below points the reviewer at current
    documentation instead of at recalled API shapes.
    """
    url = f"{_PYPI_BASE}{urllib.parse.quote(distribution, safe='')}/json"
    # Assert the scheme and host rather than suppressing the warning about them.
    # The distribution name comes from a fixed table above, but a guard that
    # holds regardless of where the name came from costs one line.
    if not url.startswith(_PYPI_BASE):
        raise ValueError(f"refusing a non-PyPI URL: {url!r}")
    req = urllib.request.Request(  # noqa: S310 - scheme and host guarded above
        url, headers={"User-Agent": "heading-os-scrutinize"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - same guard
        data = json.loads(resp.read().decode("utf-8"))
    return (data.get("info") or {}).get("version")


def docs_url(distribution: str) -> str | None:
    """A Context7 library id for the distribution, so the reviewer can read
    current docs rather than recall them. Best-effort; never fatal."""
    script = Path(__file__).resolve().parent / "context7.py"
    try:
        proc = subprocess.run(  # nosec B603 - fixed argv, no shell
            [sys.executable, str(script), distribution, "--list", "--json"],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            return None
        results = (json.loads(proc.stdout or "{}") or {}).get("results") or []
        return results[0].get("id") if results else None
    except Exception:  # noqa: BLE001 - a docs pointer is never worth a failure
        return None


def currency(*, run_id: str, target: str, imports: list[str]) -> int:
    """Version-currency check. NEVER fatal - an unknown answer is `inconclusive`.

    Version currency, not API-pattern currency: the latter is a judgement only a
    model can make, which would put a model-authored row back into the record
    this whole seam exists to take out of the model's hands.
    """
    for name in imports:
        dist = distribution_for(name)
        if dist is None:
            continue  # stdlib or first-party; nothing to be current against
        # Look the two up INDEPENDENTLY. A single try around both discarded a
        # pin that had already been read, so every row came back inconclusive
        # with no pinned value - a check that cannot fail proves nothing.
        pinned = latest = None
        try:
            pinned = pinned_version(dist)
        except Exception as exc:  # noqa: BLE001 - the lock read is never fatal
            print(f"{YELLOW}pin unreadable for {dist}: {exc}{RESET}", file=sys.stderr)
        try:
            latest = latest_version(dist)
        except Exception as exc:  # noqa: BLE001 - degradation is the contract
            print(f"{YELLOW}latest unknown for {dist}: {exc}{RESET}", file=sys.stderr)
        result = (
            ("ok" if pinned == latest else "mismatch")
            if pinned and latest else "inconclusive"
        )
        append_row(run_id=run_id, kind="currency", target=target,
                   currency={"import": name, "distribution": dist,
                             "pinned": pinned, "latest": latest, "result": result,
                             "docs": docs_url(dist) if result == "mismatch" else None})
    return 0


# ============================================================
# Reproduction
# ============================================================
def _run(cmd: list[str]) -> int:
    proc = subprocess.run(  # nosec B603 - list argv from the operator, never shell
        cmd, capture_output=True, text=True, timeout=REPRODUCTION_TIMEOUT_S)
    return proc.returncode


def reproduce(*, run_id: str, target: str, finding_id: str, cmd: list[str]) -> int:
    """Run the proposed command and record the pre-fix exit code."""
    exit_before = _run(cmd)
    if exit_before == 0:
        print(f"{RED}command exits 0 before any fix: it reproduces nothing{RESET}",
              file=sys.stderr)
        return 3
    append_row(run_id=run_id, kind="reproduction", target=target,
               finding_id=finding_id, verdict="REPRODUCED",
               reproduction={"cmd": " ".join(cmd), "exit_before": exit_before,
                             "exit_after": None})
    print(f"{GREEN}REPRODUCED (exit {exit_before}){RESET}")
    return 0


def promote(*, run_id: str, target: str, finding_id: str, cmd: list[str]) -> int:
    """Join a stored pre-fix exit to a freshly observed post-fix one."""
    prior = last_reproduction(run_id, finding_id)
    if not prior or not (prior.get("reproduction") or {}).get("exit_before"):
        print(f"{RED}no prior reproduction for {finding_id}: nothing to promote{RESET}",
              file=sys.stderr)
        return 3
    exit_after = _run(cmd)
    if exit_after != 0:
        print(f"{RED}command still exits {exit_after}: the fix did not falsify it{RESET}",
              file=sys.stderr)
        return 3
    append_row(run_id=run_id, kind="reproduction", target=target,
               finding_id=finding_id, verdict="FALSIFIED",
               reproduction={"cmd": " ".join(cmd),
                             "exit_before": prior["reproduction"]["exit_before"],
                             "exit_after": exit_after})
    print(f"{GREEN}FALSIFIED{RESET}")
    return 0


# ============================================================
# CLI
# ============================================================
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dispatch /scrutinize judge work.")
    mode = parser.add_mutually_exclusive_group(required=True)
    for flag in ("pass-start", "judge", "role-scan", "currency", "reproduce", "promote"):
        mode.add_argument(f"--{flag}", action="store_true")

    parser.add_argument("--run-id", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--finding")
    parser.add_argument("--pass", dest="pass_", choices=["2.5a", "2.5b"])
    parser.add_argument("--side", choices=["advocate", "skeptic", "meta"])
    parser.add_argument("--family", choices=["claude", "kimi"])
    parser.add_argument("--verdict")
    parser.add_argument("--brief-file")
    parser.add_argument("--paths", nargs="*", default=[])
    parser.add_argument("--cmd")

    args = parser.parse_args(argv)

    if args.pass_start:
        append_row(run_id=args.run_id, kind="pass_start", target=args.target)
        print(f"{GREEN}pass opened: {args.run_id}{RESET}")
        return 0

    if args.role_scan:
        return role_scan(run_id=args.run_id, target=args.target, paths=args.paths)

    if args.currency:
        return currency(run_id=args.run_id, target=args.target,
                        imports=imports_in(args.paths))

    if args.reproduce or args.promote:
        if not (args.finding and args.cmd):
            print(f"{RED}ERROR: --finding and --cmd are required{RESET}",
                  file=sys.stderr)
            return 2
        fn = reproduce if args.reproduce else promote
        return fn(run_id=args.run_id, target=args.target, finding_id=args.finding,
                  cmd=shlex.split(args.cmd))

    # --judge
    if not (args.finding and args.pass_):
        print(f"{RED}ERROR: --finding and --pass are required{RESET}", file=sys.stderr)
        return 2
    family = args.family
    if family is None:
        if not args.side:
            print(f"{RED}ERROR: --family or --side is required{RESET}", file=sys.stderr)
            return 2
        family = assign_families(swap=swap_for_run(args.run_id))[args.side]
    brief = ""
    if args.brief_file:
        brief = Path(args.brief_file).read_text(encoding="utf-8")
    return judge(run_id=args.run_id, target=args.target, finding_id=args.finding,
                 pass_=args.pass_, brief=brief, family=family, verdict=args.verdict)


if __name__ == "__main__":
    sys.exit(main())
