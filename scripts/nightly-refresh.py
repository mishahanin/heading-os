#!/usr/bin/env python3
"""The night side of the day-mode contract, as one command.

Day mode (`scripts/day-mode.py`) is only safe because something else runs
everything it skipped. That something is this file, once a night, and the
contract it implements is not invented here: it is printed verbatim by
`python scripts/day-mode.py nightly` and quoted in that file's module docstring.
Read it before changing anything below.

The order is the whole point:

  1. Run the FULL suite through `scripts/run-tests.py`, unchanged. No selection,
     no verdict cache, no day-mode narrowing. That is the only run that covers
     what day mode cannot reach.
  2. Read that run's own pytest summary and refuse a HOLLOW pass: a suite that
     exited 0 while skipping more than the committed ceiling in
     `config/nightly-skip-baseline.json` did not prove what a green marker
     claims. See "The hollow pass" below.
  3. ONLY on success, move the base day mode selects against
     (`day-mode.py mark-green <HEAD>`) and record the passing files in the
     verdict store (`test-cache.py record --base <HEAD>`).
  4. ONLY on success, warm the derived artifacts a cold morning would otherwise
     pay for. Today that is the day-mode fact cache.
  5. On FAILURE, do none of 3 or 4. Revoke nothing. Make the failure LOUD.

Step 5 is the one that matters. A nightly that fails into a log nobody reads
converts day mode from a speed-up into a hole, because the green marker stops
advancing while `day-mode select` keeps narrowing against it. The failure goes
to the operator's own Telegram sink through `scripts/utils/telegram_notify.py`,
which `.claude/rules/lethal-trifecta.md` exempts from the outbound-send gate
BECAUSE it can only reach the operator: `own_targets()` resolves an allowlist
from the environment and refuses any recipient a caller could produce. This
file never names a recipient; it asks that module which sinks are the
operator's own and uses those.

The second property of a loud night -- notifying when the night did not RUN AT
ALL -- is NOT implemented as a second watchdog here, deliberately. The contract
already names its mechanism: the green marker's age. `day-mode.py select`
prints it on every run, so a marker that stopped advancing is visible at the
point of use. `--status` below is the direct read of the same question.

The hollow pass
---------------
MEASURED 2026-09-05, on the FIRST fire of this timer, an hour after it was
installed. The night reported `24599 passed, 240 skipped` and marked green.
The same tree in an ordinary shell reported `24836 passed, 2 skipped, 1
failed`. Both collected 24839. So 238 checks did not execute, one of them was
red, and this file moved the green marker to a0931fe and recorded 1085 green
verdicts over a run that had proved almost none of them.

The cause was the launcher's environment, not the code: a systemd user service
inherits the manager's PATH, which carries none of `~/.local/bin`, `~/bin` or
the nvm node bin, so every external tool the suite gates on (`gh`, `git-lfs`,
`node`, `npx`, `marp`, `uv`, `pre-commit`, `claude`, `herdr`) was absent and
the tests that need them skipped. Replacing ONLY the PATH in an otherwise
ordinary shell reproduced the night's numbers to the test.

That environment is fixed in the unit template, and that fix is a convenience:
it makes the night RUN what it should. The control is here, because the shape
outlives the cause. An exit code of 0 says "nothing that ran failed"; it does
not say "the checks ran". Anything that removes a test from the run (a missing
tool, a missing environment variable, an import guard, a `skipif` on a
capability the launcher lacks) produces the identical exit code. So this file
reads the run's own summary and refuses to mark green when the skip count
exceeds the ceiling committed in `config/nightly-skip-baseline.json`.

Why the summary and not `--junit-xml`, which would be the sturdier reading.
`--junit-xml` needs an argv, and there is no way to hand one to the pytest
child: the gate is `scripts/run-tests.py`, which takes no pass-through
arguments, and its `child_env()` drops every `PYTEST_`-prefixed variable by
blanket prefix, so `PYTEST_ADDOPTS` and `PYTEST_PLUGINS` are unreachable too.
The two ways to get the flag in are to edit `run-tests.py`, or to bypass it and
spell the gate's argv here, and the second is this repository's dominant defect
shape: a second copy of the marker expression that stops being fixed. So the
summary line it is, with the decay handled rather than ignored. `parse_outcomes`
refuses instead of guessing: a summary it cannot read is a REFUSAL to mark
green, not a zero. The day pytest rewords that line, the night goes loud.

Usage:
  python scripts/nightly-refresh.py              # the night's run
  python scripts/nightly-refresh.py --status     # what the last run did, and when
  python scripts/nightly-refresh.py --dry-run    # resolved paths and commands, no run

Persistence: one JSON run record under `.cache/nightly-refresh/`, written
atomically, beside the two SQLite stores this run refreshes
(`.cache/day-mode/facts.db`, `.cache/test-verdicts.db`). Nothing new listens on
a port and no daemon is added.

SCHEDULING IS NOT DONE HERE. This file installs nothing and starts nothing.
`scripts/install-nightly-refresh-timer.sh` is the installer, it runs in HELM
only, and its `--check` mode is how an operator ESTABLISHES that the timer is
armed rather than inferring it from the installer having been merged.

Tests: tests/test_a_nightly_that_marked_green_over_a_failing_suite.py
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.venv_guard import ensure_venv  # noqa: E402

# Called here, at the top, for the same reason `scripts/run-tests.py` calls it:
# pytest lives only in `.venv`. It is called BEFORE the suite rather than after,
# because this file later imports `run-tests.py` to read the one copy of the
# marker expression, and that import calls `ensure_venv()` again. A re-exec
# after the suite had run would restart this process and run the whole suite a
# second time; a re-exec here has done nothing yet and costs nothing.
ensure_venv()

from scripts.utils.colors import GREEN, RED, RESET, YELLOW  # noqa: E402
from scripts.utils.day_mode import build_index  # noqa: E402
from scripts.utils.paths import load_env  # noqa: E402
from scripts.utils.telegram_notify import notify, own_targets  # noqa: E402

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
RECORD_REL = Path(".cache/nightly-refresh/last-run.json")
SKIP_BASELINE_REL = Path("config/nightly-skip-baseline.json")

# How many lines off the end of the gate's output are kept for the summary read.
# The counts line is the last line pytest prints, and `run-tests.py` adds one
# more after it, so this is generous by two orders of magnitude on purpose: a
# plugin that prints a footer must not push the summary out of the window.
SUMMARY_TAIL_LINES = 200

# The words pytest's terminal reporter puts in its final counts line. Listed
# rather than matched as a bare `\w+` so a stray "3 workers" or "2 items" in a
# plugin's footer cannot be read as an outcome.
OUTCOME_WORDS = (
    "passed", "failed", "error", "errors", "skipped", "xfailed", "xpassed",
    "deselected", "warning", "warnings", "rerun", "reruns",
)

OUTCOME_ALIASES = {"errors": "error", "warnings": "warning", "reruns": "rerun"}

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_COUNT = re.compile(r"(\d+)\s+(" + "|".join(OUTCOME_WORDS) + r")\b")
# The tail every pytest counts line carries: `in 0.03s`, `in 979.12s`, and for a
# run over a minute `in 979.12s (0:16:19)`. It is what tells the counts line
# apart from a `-rs` short-summary body line, which carries no duration.
_DURATION = re.compile(r"\bin\s+\d+(?:\.\d+)?s\b")


# ============================================================
# Small helpers
# ============================================================

def _log(message: str) -> None:
    """One line to stdout, flushed. Under systemd this is the journal."""
    print(message, flush=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _head_revision(root: Path) -> str | None:
    """The revision this night is about, or None when git cannot answer.

    None is never read as "no changes": every caller treats it as a refusal,
    because a night that cannot name its revision cannot record one either.
    """
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _write_record(root: Path, payload: dict) -> None:
    """Atomic write of the run record: `.tmp` then `os.replace`."""
    path = root / RECORD_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _read_record(root: Path) -> dict | None:
    path = root / RECORD_REL
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Absent and unparseable are answered the same way ON PURPOSE: both mean
        # "this run record cannot tell you the night ran", and a caller that
        # distinguished them would still have to refuse in both branches.
        return None


def alarm(root: Path, subject: str, detail: str) -> int:
    """Print the failure loudly and push it to the operator's OWN sinks.

    Returns the number of sinks that accepted the message. A zero return is
    reported by the caller rather than swallowed: an unconfigured sink means the
    night failed AND nobody was told, which is strictly worse than either alone.

    The recipient is never named here. `own_targets()` resolves the operator's
    own sinks from the environment, and `notify()` refuses anything else, which
    is what keeps this off the outbound-send gate.
    """
    _log(f"{RED}nightly-refresh: {subject}{RESET}")
    for line in detail.splitlines():
        _log(f"  {line}")
    text = f"HEADING OS nightly-refresh: {subject}\n\n{detail}"
    delivered = 0
    for target in sorted(own_targets()):
        if notify(target, text):
            delivered += 1
    if delivered:
        _log(f"nightly-refresh: notified {delivered} operator sink(s).")
    else:
        _log(f"{RED}nightly-refresh: NO NOTIFICATION SENT{RESET} -- no operator"
             " sink is configured, or the transport refused. The failure above"
             " reached this log and nothing else.")
    return delivered


# ============================================================
# The corpus the night proves green
# ============================================================

def _gate_argv(root: Path) -> list[str]:
    """The pytest argv `scripts/run-tests.py` would use for the regression gate.

    Loaded from that file rather than restated, so the marker expression has ONE
    copy. A second copy is this repository's dominant defect shape: the fix
    lands in one of them and the other keeps the old answer. The file cannot be
    imported by name (`run-tests` is not a Python identifier), hence the path
    load.
    """
    path = root / "scripts" / "run-tests.py"
    spec = importlib.util.spec_from_file_location("_nightly_gate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return list(module.build_command(False))


def collect_corpus(root: Path) -> list[str]:
    """The test files the regression gate collects, repository-relative.

    Run BEFORE the suite, so a broken collection costs seconds rather than being
    discovered after eight minutes of green tests. The claim this establishes is
    narrow and exact: these are the files pytest collects under the SAME marker
    expression the gate runs. It is not a claim that each of them passed -- the
    caller only records them after the gate has exited 0 over that same
    selection.
    """
    # `-n auto` is dropped as a PAIR, not by value: xdist buys nothing for a
    # collection and a bare `a not in ("-n", "auto")` filter would also eat an
    # unrelated literal `auto` somewhere else in the argv.
    gate = _gate_argv(root)
    argv = []
    skip_next = False
    # Named `arg`, not `token`: ruff's S105 reads `token == "<literal>"` as a
    # hardcoded credential comparison, and a `noqa` to silence a rule that is
    # right about the shape and wrong about this variable is worse than the
    # word.
    for arg in gate:
        if skip_next:
            skip_next = False
            continue
        if arg == "-n":
            skip_next = True
            continue
        argv.append(arg)
    # No second `-q`. The gate's argv already carries one, and pytest counts
    # them: `-q -q` is `-qq`, at which `--collect-only` prints no node ids at
    # all. That is how this returned an EMPTY corpus over a tree with tests in
    # it, which the empty-corpus refusal below caught rather than recording a
    # green night over nothing.
    argv += ["--collect-only", "-p", "no:randomly", "-p", "no:cacheprovider"]
    result = subprocess.run(argv, cwd=str(root), capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"pytest collection exited {result.returncode}:\n{result.stderr.strip()[-2000:]}")
    files = set()
    for line in result.stdout.splitlines():
        node = line.strip()
        if "::" not in node:
            continue
        candidate = node.split("::", 1)[0]
        if candidate.endswith(".py"):
            files.add(candidate)
    return sorted(files)


# ============================================================
# What the run actually proved
# ============================================================

def parse_outcomes(output: str) -> dict[str, int] | None:
    """pytest's own counts, off its final summary line, or None.

    None means "this output does not carry a summary I can read", and every
    caller treats it as a REFUSAL rather than as an absence of skips. That
    direction is the whole point: a reader that returned `{}` here would score
    an unreadable run as zero skips and mark it green, which is the defect this
    function exists to catch wearing different clothes.

    The line is found by shape, not by position: the last line that carries both
    a duration (`in 12.34s`) and at least one `<count> <outcome>` pair. Colour is
    stripped first, because the child writes to a pipe and something upstream may
    still be forcing it (FORCE_COLOR is set in this workspace's own shells).
    """
    for raw in reversed(output.splitlines()):
        line = _ANSI.sub("", raw).strip()
        if not _DURATION.search(line):
            continue
        counts = _COUNT.findall(line)
        if not counts:
            continue
        # pytest prints "1 error" and "3 errors" for the same outcome, so the
        # plurals are folded by an explicit map. A caller reading
        # `outcomes["error"]` must not be answered zero on a run with three.
        return {OUTCOME_ALIASES.get(word, word): int(number)
                for number, word in counts}
    return None


def read_skip_ceiling(root: Path) -> int | None:
    """The committed maximum skip count, or None when it cannot be read.

    Absent, unparseable, missing the key, and not a non-negative integer are all
    answered None, and the caller refuses on None. A night that cannot establish
    what an honest skip count looks like cannot certify one, and the alternative
    (defaulting to some number written here) puts a second copy of the ceiling in
    the code, where it would be the one nobody updates.
    """
    try:
        data = json.loads((root / SKIP_BASELINE_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ceiling = data.get("max_skips") if isinstance(data, dict) else None
    if isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling < 0:
        return None
    return ceiling


def run_gate(argv: list[str], cwd: Path) -> tuple[int, str]:
    """Run the gate, echoing every line as it arrives, and return (exit, text).

    Echoed rather than captured: under systemd this stdout IS the journal, and a
    nightly that goes silent for sixteen minutes and then prints everything at
    once is a nightly nobody can watch. Only the tail is kept, because the
    summary sits at the end and the progress body can run to hundreds of lines.

    One behaviour this changes for a human running the command by hand: the
    child's stdout is now a pipe rather than the terminal, so pytest picks its
    own default width and drops colour unless something forces it. Under the
    timer, which is the case that matters, it was already a pipe to the journal.
    """
    tail: collections.deque[str] = collections.deque(maxlen=SUMMARY_TAIL_LINES)
    process = subprocess.Popen(  # noqa: S603 - argv is built here, never shell
        argv, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1)
    if process.stdout is not None:
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            tail.append(line)
    return process.wait(), "".join(tail)


# ============================================================
# The night
# ============================================================

def run(root: Path, *, dry_run: bool = False) -> int:
    load_env(root)  # .env holds HEADING_OS_TZ and the *_TELEGRAM_TARGET names
    started = _now()

    revision = _head_revision(root)
    if revision is None:
        alarm(root, "could not resolve HEAD",
              f"git rev-parse HEAD failed in {root}. Nothing was run and"
              " nothing was recorded.")
        return 1

    gate = [sys.executable, str(root / "scripts" / "run-tests.py")]
    day_mode = [sys.executable, str(root / "scripts" / "day-mode.py"),
                "mark-green", revision]
    verdicts = [sys.executable, str(root / "scripts" / "test-cache.py"),
                "record", "--base", revision, "--from", "-"]

    ceiling = read_skip_ceiling(root)

    if dry_run:
        _log(f"root:      {root}")
        _log(f"revision:  {revision}")
        _log(f"record:    {root / RECORD_REL}")
        _log(f"baseline:  {root / SKIP_BASELINE_REL} "
             f"(max_skips={ceiling if ceiling is not None else 'UNREADABLE'})")
        _log(f"sinks:     {len(own_targets())} operator sink(s) configured")
        for label, argv in (("suite", gate), ("mark-green", day_mode),
                            ("verdicts", verdicts)):
            _log(f"{label + ':':<10} {' '.join(argv)}")
        _log("warm:      scripts.utils.day_mode.build_index(use_cache=True)")
        return 0

    # Read BEFORE the suite, for the same reason the collection pass runs
    # before it: a night that cannot establish its own ceiling can apply no
    # verdict, and finding that out after sixteen minutes of green tests helps
    # nobody.
    if ceiling is None:
        alarm(root, "the skip ceiling could not be read, the suite was NOT run",
              f"expected a JSON object with a non-negative integer"
              f" \"max_skips\" at {root / SKIP_BASELINE_REL}.\n"
              "That file is what tells a hollow pass from a real one, so a night"
              " without it can certify nothing. Nothing was marked green and no"
              " verdict was recorded.")
        _write_record(root, {"status": "baseline_unreadable", "started": started,
                             "finished": _now(), "revision": revision})
        return 1

    try:
        corpus = collect_corpus(root)
    except (RuntimeError, OSError) as exc:
        alarm(root, "test collection failed, the suite was NOT run", str(exc))
        _write_record(root, {"status": "collect_failed", "started": started,
                             "finished": _now(), "revision": revision,
                             "detail": str(exc)[:2000]})
        return 1
    if not corpus:
        alarm(root, "test collection returned NO files, the suite was NOT run",
              "An empty corpus is a collection failure, not a clean tree. Nothing"
              " was marked green and no verdict was recorded.")
        _write_record(root, {"status": "collect_empty", "started": started,
                             "finished": _now(), "revision": revision})
        return 1
    _log(f"nightly-refresh: {len(corpus)} test file(s) collected at {revision[:12]}")

    # ---- 1. the full suite, unchanged --------------------------------------
    _log(f"$ {' '.join(gate)}")
    gate_rc, gate_output = run_gate(gate, root)

    if gate_rc != 0:
        # 5. FAILURE. No marker, no verdicts, no warm, and nothing revoked. The
        # previous green marker stays exactly where it was: revoking it here
        # would silently widen every later day-mode selection with no record of
        # why, and leaving it is what makes the stalled marker visible.
        alarm(root, f"the full suite FAILED (pytest exit {gate_rc})",
              f"revision: {revision}\n"
              f"root:     {root}\n"
              "No known-green marker was moved and no verdict was recorded, so"
              " day mode still selects against the previous green revision."
              " Nothing was revoked.\n"
              "Reproduce: python scripts/run-tests.py")
        _write_record(root, {"status": "suite_failed", "started": started,
                             "finished": _now(), "revision": revision,
                             "gate_exit": gate_rc, "collected": len(corpus)})
        return gate_rc

    # ---- 2. the pass was not hollow ----------------------------------------
    # Exit 0 says "nothing that ran failed". It does not say "the checks ran".
    # Everything below this point writes a claim that they did, so the claim is
    # established here or the night stops.
    outcomes = parse_outcomes(gate_output)
    if outcomes is None:
        alarm(root, "the suite passed but its summary could not be read",
              f"revision: {revision}\n"
              "No line in the gate's output carried a pytest counts summary, so"
              " the number of tests that never ran is unknown. An unknown skip"
              " count is refused rather than assumed to be zero: assuming zero is"
              " exactly how a run that skipped 238 checks was marked green on"
              " 2026-09-05.\n"
              "No known-green marker was moved and no verdict was recorded."
              " Nothing was revoked.\n"
              "Reproduce: python scripts/run-tests.py")
        _write_record(root, {"status": "summary_unreadable", "started": started,
                             "finished": _now(), "revision": revision,
                             "gate_exit": 0, "collected": len(corpus),
                             "max_skips": ceiling})
        return 1

    skipped = outcomes.get("skipped", 0)
    if skipped > ceiling:
        alarm(root, f"the suite PASSED but SKIPPED {skipped} tests, over the"
                    f" committed ceiling of {ceiling}",
              f"revision: {revision}\n"
              f"root:     {root}\n"
              f"summary:  {', '.join(f'{n} {word}' for word, n in sorted(outcomes.items()))}\n"
              f"ceiling:  {root / SKIP_BASELINE_REL}\n"
              "A skip count this far above the baseline means checks did not RUN,"
              " and a green marker over a run that did not run them is a lie the"
              " next day's selection narrows against.\n"
              "The usual cause is the launcher's environment, not the tree. A"
              " systemd user service inherits the manager's PATH, which carries"
              " neither ~/.local/bin nor ~/bin nor the nvm node bin, so gh,"
              " git-lfs, node, npx, marp, uv, pre-commit, claude and herdr are all"
              " absent and every test gated on them skips. Compare:\n"
              "  systemctl --user show-environment | grep '^PATH='\n"
              "  bash scripts/install-nightly-refresh-timer.sh --check\n"
              "No known-green marker was moved and no verdict was recorded."
              " Nothing was revoked.\n"
              "Reproduce: python scripts/run-tests.py")
        _write_record(root, {"status": "skips_exceeded", "started": started,
                             "finished": _now(), "revision": revision,
                             "gate_exit": 0, "collected": len(corpus),
                             "outcomes": outcomes, "max_skips": ceiling})
        return 1
    _log(f"nightly-refresh: {skipped} skipped, within the committed ceiling"
         f" of {ceiling}")

    # ---- 3. ONLY on success: move the base, record the verdicts ------------
    failures: list[str] = []

    # stdin=DEVNULL, not inherited: under a systemd timer stdin is already null,
    # but under any other launcher an inherited pipe leaves a child that reads
    # stdin blocking forever, and a nightly that hangs is a nightly that never
    # reports.
    _log(f"$ {' '.join(day_mode)}")
    if subprocess.run(day_mode, cwd=str(root), check=False,
                      stdin=subprocess.DEVNULL).returncode != 0:
        failures.append("day-mode.py mark-green failed: the green marker did NOT"
                        " advance, so day mode keeps selecting against the older"
                        " revision.")

    _log(f"$ {' '.join(verdicts)} ({len(corpus)} files on stdin)")
    recorded = subprocess.run(verdicts, cwd=str(root), check=False,
                              input="\n".join(corpus), text=True)
    if recorded.returncode != 0:
        failures.append("test-cache.py record failed: no verdicts were stored"
                        " for this revision.")

    # ---- 4. ONLY on success: warm what a cold morning would pay for --------
    # The day-mode fact cache is the one derived artifact in the speed inventory
    # that is cheap to rebuild, has no automatic refresh, and whose reader
    # self-heals only by paying the full cold parse (measured 6.6s cold against
    # 0.31s warm). The memory indexes have their own timer and the CodeGraph
    # index has its own watcher; neither is rebuilt here.
    try:
        index = build_index(root, use_cache=True)
        _log(f"nightly-refresh: day-mode fact cache warm "
             f"({len(index.test_files)} test files indexed)")
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        failures.append(f"warming the day-mode fact cache failed: {exc}")

    finished = _now()
    if failures:
        # The suite passed but the night did not finish its job. Loud, because
        # the visible symptom tomorrow is a marker that did not move, which is
        # indistinguishable from a nightly that never fired.
        alarm(root, "the suite PASSED but the post-run steps did not complete",
              f"revision: {revision}\n" + "\n".join(f"- {f}" for f in failures))
        _write_record(root, {"status": "partial", "started": started,
                             "finished": finished, "revision": revision,
                             "gate_exit": 0, "collected": len(corpus),
                             "outcomes": outcomes, "max_skips": ceiling,
                             "failures": failures})
        return 1

    _write_record(root, {"status": "green", "started": started,
                         "finished": finished, "revision": revision,
                         "gate_exit": 0, "collected": len(corpus),
                         "outcomes": outcomes, "max_skips": ceiling,
                         "failures": []})
    _log(f"{GREEN}nightly-refresh: green at {revision[:12]}; "
         f"{len(corpus)} test files recorded, caches warm.{RESET}")
    return 0


def status(root: Path) -> int:
    """What the last run did, and when. The read half of "did the night fire?".

    Exit 0 only when the last recorded run was green. `--status` answers about
    the RECORD, not about the timer: a timer that was never installed and a
    timer that was uninstalled both leave the same absent record, and
    `scripts/install-nightly-refresh-timer.sh --check` is what tells those apart.
    """
    record = _read_record(root)
    if record is None:
        _log(f"{RED}nightly-refresh: no run record at {root / RECORD_REL}.{RESET}")
        _log("  This night has never completed here, or the record is unreadable.")
        _log("  Whether the timer is armed is a different question:")
        _log("    bash scripts/install-nightly-refresh-timer.sh --check")
        return 2
    state = record.get("status", "unknown")
    _log(f"status:    {state}")
    _log(f"revision:  {record.get('revision', '?')}")
    _log(f"started:   {record.get('started', '?')}")
    _log(f"finished:  {record.get('finished', '?')}")
    _log(f"collected: {record.get('collected', '?')} test file(s)")
    outcomes = record.get("outcomes")
    if isinstance(outcomes, dict) and outcomes:
        _log("summary:   " + ", ".join(f"{n} {word}"
                                       for word, n in sorted(outcomes.items()))
             + f" (ceiling {record.get('max_skips', '?')} skips)")
    for failure in record.get("failures") or []:
        _log(f"  {YELLOW}{failure}{RESET}")
    if state == "green":
        return 0
    _log(f"{RED}The last night did not finish green.{RESET}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nightly-refresh.py",
        description="Run the full suite, then move the day-mode base and warm the caches.")
    parser.add_argument("--root", help="repository root (default: this checkout)")
    parser.add_argument("--status", action="store_true",
                        help="print what the last run did, and exit non-zero unless it was green")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the resolved paths and commands without running anything")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else DEFAULT_ROOT
    if args.status:
        return status(root)
    return run(root, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
