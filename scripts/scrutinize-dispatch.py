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
a debate - but only the harness may write it, and only for an exit code that
came from the intended check. Until 2026-08-13 any non-zero exit bought that
verdict, so a command that never ran - a shell pipeline passed as literal argv, a
mistyped pytest path collecting nothing, a missing executable - recorded as a
reproduced finding. `_run` now separates "the check ran and failed" from "the
check did not run", and the second records nothing (exit `4`).

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
  4  the command did not run its check, so its exit code is not evidence
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
from dataclasses import dataclass
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

# A reproduction command is run as a fixed argv, never through a shell, so a
# token the operator meant as a shell operator arrives at the child process as a
# literal argument and the pipeline they wrote never happens. The exit code that
# comes back is then an artifact of the malformed invocation, and it is non-zero,
# which is exactly the value `REPRODUCED` reads as proof. Refuse the command
# instead of recording the artifact.
SHELL_OPERATORS = frozenset({"|", "||", "&&", ";", "&", ">", ">>", "<", "<<"})
# Punctuation `shlex` splits out as its own token when it is UNQUOTED, beyond
# the operators above: `(` and `)` are how a command substitution shows up, and
# a backtick is the older spelling of the same thing. A fixed argv expands
# neither, so an unquoted one means the command would not do what it reads as.
# See `shell_operators_in_source`.
_SHELL_PUNCT = frozenset({"(", ")", "`", "$"})
# What `shlex` is told to treat as punctuation: its own default set plus the
# backtick, which it does not carry.
_SHELL_PUNCTUATION = "();<>|&`"

# Exit codes that report the command never got as far as its check.
EXIT_NOT_EXECUTABLE = 126
EXIT_NOT_FOUND = 127

# pytest is this workspace's only test harness and these codes are its way of
# saying the check did not run, as opposed to ran and failed:
#
#   2  interrupted before or during collection - a bad import in the test module
#      is the common case, and it is at least as frequent as the mistyped path
#      this carve-out was first written for
#   3  internal error
#   4  usage error
#   5  no tests collected - what a mistyped test path produces
#
# Only exit 1 means "tests ran and something failed", which is the only non-zero
# code that may be recorded as REPRODUCED. Exit 2 was missing until the
# 2026-08-13 audit, so a test module with a broken import was written into the
# run record as a reproduced finding for a check that never executed.
PYTEST_DID_NOT_RUN = frozenset({2, 3, 4, 5})

# Which argv IS a pytest invocation. `any("pytest" in tok for tok in cmd)` was
# an unanchored substring over EVERY token, and it was wrong in both directions.
#
# False positive: a data-file argument like `--log /tmp/pytest-run.log` made an
# ordinary script's exit 2 read as "no test ran", costing a real reproduction.
#
# False negative, and this is the dangerous one: a WRAPPER hides the token.
# `scripts/run-tests.py` is this workspace's own standard test command, it
# returns pytest's exit code verbatim, and no token of its argv holds "pytest" -
# so a test module with a broken import exited 2 and was written into the run
# record as a REPRODUCED finding for a check that never executed. That is
# precisely the defect the 2026-08-13 audit fixed, still reachable through a
# different invocation shape.
PYTEST_BINARIES = frozenset({"pytest", "py.test", "pytest.exe"})

# What pytest says when it did not run, for the wrapper case argv cannot settle.
# Matched only ALONGSIDE a PYTEST_DID_NOT_RUN code, and it can only ever REFUSE
# to record evidence, so a false match costs a re-run while a miss writes a
# fabricated proof.
PYTEST_DID_NOT_RUN_MARKERS = (
    "error during collection",
    "errors during collection",
    "error collecting",
    "no tests ran",
    "no tests collected",
    "(pytest exit ",          # scripts/run-tests.py's own failure banner
)


def _is_pytest_command(cmd: list[str]) -> bool:
    """True when argv is UNAMBIGUOUSLY pytest. Three shapes, and only three.

    The executable itself (`pytest tests/`), the module form
    (`python -m pytest tests/`), and a PATH to the binary anywhere in argv
    (`env VAR=1 .venv/bin/pytest tests/`).

    A bare word `pytest` elsewhere in argv does NOT count: `tox -e pytest` and
    `make pytest` are wrappers, not invocations, and treating their exit codes as
    pytest's is the substring guess this replaced. When a wrapper really does hide
    pytest, `_says_pytest_did_not_run` settles it from the output instead.
    """
    if not cmd:
        return False
    if Path(cmd[0]).name in PYTEST_BINARIES:
        return True
    for index, arg in enumerate(cmd):
        if arg == "-m" and index + 1 < len(cmd) and cmd[index + 1] == "pytest":
            return True
        if ("/" in arg or "\\" in arg) and Path(arg).name in PYTEST_BINARIES:
            return True
    return False


def _says_pytest_did_not_run(*streams: str) -> bool:
    """True when the output carries pytest's own words for 'nothing ran'."""
    blob = "\n".join(s or "" for s in streams).lower()
    return any(marker in blob for marker in PYTEST_DID_NOT_RUN_MARKERS)

OUTPUT_TAIL_CHARS = 800


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
# plain .py file and only its imports say it schedules anything. The trigger is
# read from the SYNTAX, not from a substring search, because a substring search
# fires on any file that merely mentions scheduling - including this one, whose
# first live run matched its own marker table (2026-08-09 /scrutinize). A lens
# whose value is precision cannot open by flagging its own definition.
_SCHEDULER_IMPORT = "apscheduler"
_SCHEDULER_CALL = "add_job"

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


def schedules_work(path: str) -> bool:
    """True when a Python file IMPORTS a scheduler or CALLS add_job.

    Syntax, not substring: a string literal naming `add_job`, a docstring about
    APScheduler, or a test fixture mentioning either is not a scheduler and must
    not fire the lens. An unreadable or unparsable file is not one either.
    """
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0].lower() == _SCHEDULER_IMPORT for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0].lower() == _SCHEDULER_IMPORT:
                return True
        elif isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name == _SCHEDULER_CALL:
                return True
    return False


def lenses_for(paths: list[str]) -> list[str]:
    """Which lenses fire for this scope. Order is stable, duplicates removed."""
    fired: list[str] = []
    for raw in paths:
        p = str(raw)
        for lens, glob in LENS_GLOBS:
            if fnmatch.fnmatch(p, glob) and lens not in fired:
                fired.append(lens)
        if "scheduler" not in fired and p.endswith(".py") and schedules_work(p):
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
        except (OSError, SyntaxError, ValueError):
            # ValueError: ast.parse raises it on source holding a null byte.
            # `schedules_work` already guarded for it; without it here the
            # currency check -- documented as NEVER fatal -- died on one
            # malformed file in scope.
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
        # An omitted `--verdict` wrote a kind="verdict" row carrying None and
        # returned 0. `scrutinize_record.validate()` counts verdict rows by kind
        # alone, so the empty row SATISFIED --validate - and this module's own
        # docstring names --validate as the one mechanism that makes the
        # Claude-side omission visible. The backstop for the single omission the
        # design admits was defeated by the row this branch wrote. Record the
        # degradation instead, and return the exit code the table already
        # reserves for "no judge verdict was produced".
        if not (verdict or "").strip():
            append_row(run_id=run_id, kind="degraded", target=target,
                       finding_id=finding_id,
                       degraded="claude judge dispatched without --verdict; the "
                                "running session produced no verdict to record")
            print(f"{RED}--verdict is required for --family claude: the Claude "
                  f"judge IS this session, so its verdict is supplied, never "
                  f"inferred{RESET}", file=sys.stderr)
            return 1
        # The same normalisation the kimi branch does, on the branch that did
        # none. `append_row` refuses a verdict outside its vocabulary by
        # RAISING, and nothing caught it here: measured 2026-08-29,
        # `--verdict REFUTTED` left this module as an uncaught
        # `ValueError: unknown verdict 'REFUTTED'` with exit 1 and NOTHING
        # recorded - no verdict row and no `degraded` row either, so
        # `--validate` saw a finding nobody judged. `_verdict_in` accepts the
        # seven judge tokens and nothing else, which also refuses `REPRODUCED`
        # and `FALSIFIED`: those are reproduction outcomes, not rulings, and
        # `append_row`'s wider vocabulary would have taken them.
        token = _verdict_in(verdict)
        if token is None:
            append_row(run_id=run_id, kind="degraded", target=target,
                       finding_id=finding_id,
                       degraded=f"claude judge verdict {verdict!r} names no "
                                "recognised verdict token; nothing was recorded")
            print(f"{RED}{verdict!r} is not a verdict token: expected one of "
                  f"{', '.join(sorted(_JUDGE_VERDICTS))}{RESET}", file=sys.stderr)
            return 1
        append_row(run_id=run_id, kind="verdict", target=target,
                   finding_id=finding_id, pass_=pass_, judge_family="claude",
                   verdict=token)
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
    found = _verdict_in(text)
    if found is None:
        # The SAME defect the claude branch above documents and refuses, on the
        # branch nobody fixed. `_verdict_in` returns None when the answer carries
        # no recognisable verdict token: a refusal, a truncated reply, a
        # reformatted one. That None went into a `kind="verdict"` row and this
        # function returned 0, and `scrutinize_record.validate()` counts verdict
        # rows BY KIND, so the empty row satisfied the reconciliation. A k3 side
        # that decided nothing reported as a completed refutation pass.
        # Reproduced 2026-08-26 with a stubbed proxy answering
        # "I considered the finding at length but cannot decide.": row written
        # with `"verdict": null`, exit 0, `validate()` defects `[]`.
        #
        # Print the answer first: the operator needs to read what came back in
        # order to decide whether to re-ask or to judge it themselves.
        print(text)
        append_row(run_id=run_id, kind="degraded", target=target,
                   finding_id=finding_id,
                   degraded="k3 judge answered without a recognisable verdict "
                            "token; no verdict was recorded")
        print(f"{RED}k3 answered but named no verdict, so nothing was recorded "
              f"for {finding_id}: re-ask, or judge it in-session with "
              f"--family claude --verdict{RESET}", file=sys.stderr)
        return 1

    append_row(run_id=run_id, kind="verdict", target=target, finding_id=finding_id,
               pass_=pass_, judge_family="kimi", verdict=found)
    print(text)
    return 0


# The seven tokens a JUDGE may rule. Longest spelling first, but the ORDER is
# not what keeps `CORRECT` from swallowing `CORRECT_DOWNGRADE` - the `\b`
# anchors on both regexes are, since `_` is a word character and the boundary
# after `CORRECT` cannot fall inside `CORRECT_DOWNGRADE`. Measured 2026-08-29:
# with the anchors, either order resolves all seven correctly; without them,
# only this order does. The order is the belt behind the braces, so a later
# edit that loosens an anchor does not silently start recording the wrong
# ruling. Deliberately narrower than `scrutinize_record.VERDICTS`,
# which also admits `REPRODUCED` and `FALSIFIED` - those are reproduction
# outcomes and no judge rules them.
#
# Both regexes below are BUILT from this tuple. They used to spell the seven out
# twice more, in two hand-maintained alternations, and a third copy was about to
# go into the operator-facing error message. This audit keeps finding the same
# defect shape: a fix that lands in one of N copies.
_JUDGE_VERDICT_ORDER = (
    "REFUTE_PARTIAL", "REFUTATION_FAILED", "REFUTED",
    "CORRECT_DOWNGRADE", "CORRECT", "INCORRECT", "AMBIGUOUS",
)
_JUDGE_VERDICTS = frozenset(_JUDGE_VERDICT_ORDER)
_VERDICT_ALT = "|".join(_JUDGE_VERDICT_ORDER)


_VERDICT_RE = re.compile(rf"\b({_VERDICT_ALT})\b")


_VERDICT_LINE_RE = re.compile(
    rf"^\s*(?:\*\*)?VERDICT(?:\*\*)?\s*[:\-]\s*(?:\*\*)?\s*({_VERDICT_ALT})\b",
    re.MULTILINE | re.IGNORECASE)


def _verdict_in(text: str) -> str | None:
    """The judge's RULING, not the first verdict word it happens to type.

    Taking the first regex match anywhere in free text recorded the opposite of
    the ruling whenever the judge reasoned before concluding: "This is not
    REFUTED because ... Overall: CORRECT_DOWNGRADE" was recorded as REFUTED, and
    that record is the artefact `--validate` reconciles as ground truth.

    Order of trust:
      1. an explicit `VERDICT: <TOKEN>` line -- the LAST one, so a restated
         conclusion wins over the format example a judge may quote;
      2. failing that, the LAST bare token in the text, which is where a
         conclusion sits when the prose runs to the end.
    """
    body = text or ""
    lines = list(_VERDICT_LINE_RE.finditer(body))
    if lines:
        return lines[-1].group(1).upper()
    bare = list(_VERDICT_RE.finditer(body))
    return bare[-1].group(1) if bare else None


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
@dataclass(frozen=True)
class CommandRun:
    """One observed execution, and whether its exit code means anything.

    `unusable` is the field that matters. A non-zero exit is the evidence behind
    `REPRODUCED`, so an exit code produced by a command that never reached its
    check is not weak evidence, it is a false positive wearing the same number.
    When `unusable` is set, the caller records nothing.
    """

    exit_code: int | None
    stdout_tail: str = ""
    stderr_tail: str = ""
    unusable: str | None = None


def _tail(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= OUTPUT_TAIL_CHARS else "..." + text[-OUTPUT_TAIL_CHARS:]


def shell_operators_in_source(raw: str) -> list[str]:
    """The shell operators an operator typed UNQUOTED in a `--cmd` string.

    This is the check that has to see the RAW text, because `shlex.split`
    destroys the one piece of information that decides the question: quoting.

    `shlex.split` does not treat `|`, `>` or `&` as delimiters, so an UNSPACED
    pipeline survives as one ordinary-looking argument. `/bin/ls /nope|wc -l`
    splits to `['/bin/ls', '/nope|wc', '-l']`, and `'/nope|wc'` equals no member
    of SHELL_OPERATORS, so `_reject_shell_syntax` passed it. Measured 2026-08-26
    with the same pipeline written both ways: spaced, refused with exit 4;
    unspaced, run as a fixed argv, the child failed on the mangled path, and the
    non-zero exit was recorded as `verdict: "REPRODUCED"` with the stderr tail
    `cannot access '/definitely-not-here|wc'` sitting in the record as its own
    disproof. That is verbatim the defect this module's docstring says was
    closed on 2026-08-13.

    Scanning the SPLIT tokens for stray metacharacters was tried first and is
    wrong: it refused `python3 -c "import sys; sys.exit(3)"`, where the `;`
    belongs to Python and was quoted on purpose. After the split, a quoted `;`
    and a bare one are the same characters. `shlex` with `punctuation_chars`
    keeps them apart, so the guard refuses shell syntax without refusing an
    inline snippet.

    Two corrections, both measured 2026-08-29 by driving `--reproduce`:

    A NAMED SET OF OPERATORS IS ALWAYS ONE SPELLING SHORT. `shlex` returns a RUN
    of adjacent punctuation as ONE token, and the enumerated set held no
    combined form. `2>&1` tokenizes to `2`, `>&`, `1`; `&>` and `|&` yield
    themselves; `>|` and `<>` likewise. Each equalled no member of either set,
    so `/bin/cat /shard58-no-such-file 2>&1` ran as a fixed argv, `cat` exited 1
    over two filenames that do not exist, and the record gained
    `verdict: "REPRODUCED"`. That is verbatim the defect the 2026-08-26 pass
    closed for the SPACED spelling and left open for the combined one. The test
    is now structural: a token made only of punctuation characters is an
    operator, whatever it spells.

    AND THE POSIX FLAG ERASED THE QUOTING THIS FUNCTION EXISTS TO READ. Under
    `posix=True` `shlex` strips the quotes, so `grep -c ';' /etc/hostname` -
    a legal command whose `;` belongs to `grep` - arrived as the bare token `;`
    and was refused. Quoting only survived when the quoted region carried other
    characters too. `posix=False` keeps the quote characters inside the token,
    which is what actually separates `';'` from `;`.
    """
    try:
        # The default punctuation set is `();<>|&`. The backtick is added
        # because it is the older spelling of a command substitution and a fixed
        # argv expands it no better than `$(`.
        #
        # `commenters` is emptied to match `shlex.split`, which uses none. With
        # the default `#` this lexer STOPPED at the first hash while the split
        # carried on past it, so `/bin/cat /nope #x|/bin/cat` was scanned as two
        # tokens, the `|` was never seen, and the run recorded REPRODUCED off a
        # missing-file exit. Measured 2026-08-29.
        lexer = shlex.shlex(raw, posix=False, punctuation_chars=_SHELL_PUNCTUATION)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        # Unbalanced quotes. `main` hits the same fault when it calls
        # `shlex.split`, and refuses the command there with a recorded row; this
        # guard has nothing to add, so it stays silent.
        return []
    # `_SHELL_PUNCT` is still consulted for the one character it holds that is
    # not punctuation to `shlex`: a bare `$`, which `$(cmd)` leaves behind as
    # its own token once the parentheses split away.
    return sorted({t for t in tokens
                   if t and (all(ch in _SHELL_PUNCTUATION for ch in t)
                             or t in _SHELL_PUNCT)})


def _reject_shell_syntax(cmd: list[str]) -> str | None:
    """The operator's shell operators, which this harness will not honour.

    Whole-token comparison, on purpose. This takes an argv LIST, and a list is
    already argv: an in-process caller that passes `["python3", "-c", "a; b"]`
    built those three arguments deliberately and no shell was ever going to see
    them. The quoting question belongs to `shell_operators_in_source`, which
    reads the raw `--cmd` string before it is split.
    """
    found = sorted({tok for tok in cmd if tok in SHELL_OPERATORS})
    if not found:
        return None
    return (f"the command carries shell syntax ({' '.join(sorted(set(found)))}) "
            "but runs as a fixed argv with no shell, so it would not do what it "
            "reads as; rewrite it as a single command or wrap it in a script")


def _run(cmd: list[str], source: str | None = None) -> CommandRun:
    """Run a reproduction command and report whether its exit code is evidence.

    `source` is the RAW `--cmd` string when there is one. It is checked before
    the argv, because quoting is the thing that decides whether a `;` belongs to
    the shell or to a `-c` payload, and `shlex.split` has already thrown it away
    by the time `cmd` exists. Refusing HERE and not at the CLI is deliberate:
    this path already returns an unusable CommandRun, and the callers turn that
    into a `degraded` row - which is what `--validate` reads.
    """
    if not cmd:
        return CommandRun(None, unusable="empty command")
    if source:
        typed = shell_operators_in_source(source)
        if typed:
            return CommandRun(None, unusable=(
                f"the command carries shell syntax ({' '.join(typed)}) but runs as "
                "a fixed argv with no shell, so it would not do what it reads as; "
                "rewrite it as a single command or wrap it in a script"))
        # And then STOP. The argv check below is for a caller that had no raw
        # string; running it here re-imposes the quote-blindness the raw check
        # exists to remove. `shlex.split` strips quotes, so `grep -c ';' f`
        # arrives as the whole token `;` and was refused - measured 2026-08-29,
        # a legal command the raw guard had just cleared. The raw guard now
        # catches every unquoted punctuation run, so a whole-token operator that
        # survives it was quoted on purpose and belongs to the child.
    else:
        rejected = _reject_shell_syntax(cmd)
        if rejected:
            return CommandRun(None, unusable=rejected)
    # `errors="replace"`, because a reproduction command is allowed to print
    # bytes. `text=True` alone decodes strict UTF-8, and `UnicodeDecodeError` is
    # raised INSIDE `subprocess.run`, past every handler below: measured
    # 2026-08-29, `--cmd "/bin/cat /bin/cat"` left this module as a traceback
    # with exit 1 and NOTHING in the run record, so `--validate` could not tell
    # the attempt from a finding nobody tried. A mangled tail is readable
    # evidence; a traceback is none.
    try:
        proc = subprocess.run(  # nosec B603 - list argv from the operator, never shell
            cmd, capture_output=True, text=True, errors="replace",
            timeout=REPRODUCTION_TIMEOUT_S)
    except FileNotFoundError:
        return CommandRun(None, unusable=f"executable not found: {cmd[0]}")
    except PermissionError:
        return CommandRun(None, unusable=f"not executable: {cmd[0]}")
    except subprocess.TimeoutExpired:
        return CommandRun(
            None, unusable=f"no exit within {REPRODUCTION_TIMEOUT_S}s: the check "
                           "did not finish, so its outcome is unknown")

    code = proc.returncode
    out, err = _tail(proc.stdout), _tail(proc.stderr)
    if code < 0:
        return CommandRun(code, out, err,
                          unusable=f"killed by signal {-code} before it could report")
    if code in (EXIT_NOT_EXECUTABLE, EXIT_NOT_FOUND):
        return CommandRun(code, out, err,
                          unusable=f"exit {code}: the command was never executed")
    if code in PYTEST_DID_NOT_RUN and (
            _is_pytest_command(cmd) or _says_pytest_did_not_run(out, err)):
        via = "" if _is_pytest_command(cmd) else " (reported through a wrapper)"
        return CommandRun(code, out, err,
                          unusable=f"pytest exit {code}{via}: no test ran (collection "
                                   "error, internal error, usage error or nothing "
                                   "collected), so nothing was checked")
    return CommandRun(code, out, err)


def _refuse_unusable(run: CommandRun) -> None:
    print(f"{RED}the command did not run its check: {run.unusable}{RESET}",
          file=sys.stderr)
    if run.stderr_tail:
        print(run.stderr_tail, file=sys.stderr)


def _recorded_cmd(cmd: list[str], source: str | None) -> str:
    """The command as evidence: what the operator typed, or a re-runnable spelling.

    `" ".join(cmd)` was neither. `shlex.split` has already thrown the quoting
    away by the time `cmd` exists, so joining on a space recorded
    `--cmd "python3 -c \\"import sys; sys.exit(1)\\""` as
    `python3 -c import sys; sys.exit(1)` - measured 2026-08-30. That string is
    not the command that ran, and feeding it back to `--cmd` is REFUSED by this
    module's own guard (`shell_operators_in_source` returns `['(', ')', ';']`
    for it), so the row's own evidence field fails the harness that wrote it.

    The raw `--cmd` string is exact when the CLI supplied one. An in-process
    caller passes a ready argv and no source, and there `shlex.join` quotes each
    argument so the recorded string splits back to the same argv.
    """
    return source if source else shlex.join(cmd)


def reproduce(*, run_id: str, target: str, finding_id: str, cmd: list[str],
              source: str | None = None) -> int:
    """Run the proposed command and record the pre-fix exit code.

    `source` is the raw `--cmd` string when the CLI supplied one, so the
    shell-syntax guard can read quoting that `shlex.split` has erased.
    An in-process caller passing a ready argv list omits it.
    """
    run = _run(cmd, source=source)
    if run.unusable:
        # Recorded, not merely printed. The module docstring promises every
        # result lands in the run record, and `judge()` already writes a
        # `degraded` row on both of its failure paths; this one printed to
        # stderr and returned, so `--validate` saw a finding with no attempt
        # against it and could not tell that from a finding nobody tried.
        append_row(run_id=run_id, kind="degraded", target=target,
                   finding_id=finding_id,
                   degraded=f"reproduction refused: {run.unusable}")
        _refuse_unusable(run)
        return 4
    if run.exit_code == 0:
        append_row(run_id=run_id, kind="degraded", target=target,
                   finding_id=finding_id,
                   degraded="command exits 0 before any fix: it reproduces nothing")
        print(f"{RED}command exits 0 before any fix: it reproduces nothing{RESET}",
              file=sys.stderr)
        return 3
    append_row(run_id=run_id, kind="reproduction", target=target,
               finding_id=finding_id, verdict="REPRODUCED",
               reproduction={"cmd": _recorded_cmd(cmd, source),
                             "exit_before": run.exit_code,
                             "exit_after": None,
                             "stdout_tail": run.stdout_tail,
                             "stderr_tail": run.stderr_tail})
    print(f"{GREEN}REPRODUCED (exit {run.exit_code}){RESET}")
    return 0


def promote(*, run_id: str, target: str, finding_id: str, cmd: list[str],
            source: str | None = None) -> int:
    """Join a stored pre-fix exit to a freshly observed post-fix one.

    `source` is the raw `--cmd` string when the CLI supplied one, so the
    shell-syntax guard can read quoting that `shlex.split` has erased.
    An in-process caller passing a ready argv list omits it.
    """
    prior = last_reproduction(run_id, finding_id)
    if not prior or not (prior.get("reproduction") or {}).get("exit_before"):
        print(f"{RED}no prior reproduction for {finding_id}: nothing to promote{RESET}",
              file=sys.stderr)
        return 3
    run = _run(cmd, source=source)
    if run.unusable:
        append_row(run_id=run_id, kind="degraded", target=target,
                   finding_id=finding_id,
                   degraded=f"promotion refused: {run.unusable}")
        _refuse_unusable(run)
        return 4
    if run.exit_code != 0:
        print(f"{RED}command still exits {run.exit_code}: the fix did not falsify it{RESET}",
              file=sys.stderr)
        return 3
    append_row(run_id=run_id, kind="reproduction", target=target,
               finding_id=finding_id, verdict="FALSIFIED",
               reproduction={"cmd": _recorded_cmd(cmd, source),
                             "exit_before": prior["reproduction"]["exit_before"],
                             "exit_after": run.exit_code,
                             "stdout_tail": run.stdout_tail,
                             "stderr_tail": run.stderr_tail})
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
        try:
            cmd = shlex.split(args.cmd)
        except ValueError as exc:
            # Unbalanced quotes. `shell_operators_in_source` stays silent on
            # this fault and its comment used to say `main` reports it; `main`
            # had no handler at all. Measured 2026-08-29: the ValueError left
            # the interpreter as a traceback with exit 1 and NOTHING in the run
            # record, which is neither of the two exit codes this CLI documents
            # and leaves `--validate` unable to see the attempt.
            append_row(run_id=args.run_id, kind="degraded", target=args.target,
                       finding_id=args.finding,
                       degraded=f"--cmd could not be parsed: {exc}")
            print(f"{RED}--cmd could not be parsed: {exc}{RESET}", file=sys.stderr)
            return 4
        fn = reproduce if args.reproduce else promote
        return fn(run_id=args.run_id, target=args.target, finding_id=args.finding,
                  cmd=cmd, source=args.cmd)

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
        try:
            brief = Path(args.brief_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"{RED}ERROR: cannot read --brief-file: {exc}{RESET}",
                  file=sys.stderr)
            return 2
    if family == "kimi" and not brief.strip():
        # An external judge answering an EMPTY prompt still returns a verdict,
        # and that verdict was recorded with the same standing as a real
        # adjudication -- a check that cannot meaningfully fail, plus a paid
        # call for it.
        print(f"{RED}ERROR: --brief-file with non-empty content is required "
              f"for the kimi judge{RESET}", file=sys.stderr)
        return 2
    return judge(run_id=args.run_id, target=args.target, finding_id=args.finding,
                 pass_=args.pass_, brief=brief, family=family, verdict=args.verdict)


if __name__ == "__main__":
    sys.exit(main())
