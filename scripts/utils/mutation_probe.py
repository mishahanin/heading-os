"""Run mutations against a test command, and refuse a verdict without a control.

Canopus step 11 asks whether a green contract is STRONG: break the code on
purpose, and a contract worth having goes red. The trap is that a mutation which
does not do what its author thinks produces a confident, wrong "SURVIVED", and
the author then weakens or rewrites a guard that was fine.

That happened twice in one slice on 2026-08-03. One mutation anchored on a
four-space indent and landed inside the wrong function, so the contract went red
for a reason unrelated to the hypothesis. Another inserted an import into a
middle module without a CALL, so the chain it was meant to test was broken by
construction and its survival meant nothing. Both were caught by re-reading the
mutation afterwards, which is luck rather than method.

So every mutation here carries a CONTROL: a predicate over the mutated sources
that must hold for the mutation to be the thing its label claims. A failing
control yields `invalid` -- never `survived`, never `killed`. The verdict a
reader most wants to trust is the one this makes impossible to fake.

Usage:

    from scripts.utils.mutation_probe import Mutation, run_mutations

    def _calls_the_helper(sources):
        body = sources["scripts/a.py"].split("def main")[1]
        return None if "helper(" in body else "main never calls helper"

    results = run_mutations(
        [Mutation(label="drop the guard",
                  edits=(("scripts/a.py", "if not ok:\\n        return", ""),),
                  control=_calls_the_helper)],
        command=[".venv/bin/python", "-m", "pytest", "tests/contract/x/", "-q"],
        root=Path("."),
    )

Every file is restored and its sha256 re-verified before the next mutation runs;
a restore that does not match raises rather than continuing, because a mutation
harness that leaves the tree dirty corrupts every verdict after it.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

KILLED = "killed"
SURVIVED = "survived"
INVALID = "invalid"

# The control the module's own docstring promises and did not run. See
# `run_mutations`: without it a suite that was ALREADY red reported every
# mutation as `killed`, and `Result.trustworthy` answered True.
BASELINE_RED = "baseline-red"

# A control returns None when the mutation is what it claims, or a one-line
# reason why it is not. It receives the MUTATED source of every edited file,
# keyed by the path as given.
Control = Callable[[dict], "str | None"]


@dataclass(frozen=True)
class Mutation:
    """One deliberate break, with the control that proves it is really that break.

    `edits` are (relative path, exact old text, new text) triples, applied once
    each. An anchor must appear EXACTLY ONCE. Absent means the author is
    describing code that is not there; ambiguous means the edit lands wherever
    the anchor happens to come first, which may not be the code the label names.
    Both are invalid mutations, never `survived` and never `killed`.
    """

    label: str
    edits: Sequence[tuple]
    control: Control


@dataclass(frozen=True)
class Result:
    label: str
    verdict: str
    detail: str = ""

    @property
    def trustworthy(self) -> bool:
        """A verdict a reader may act on. `invalid` is not one.

        Neither is `baseline-red`: a `killed` taken from a suite that was already
        failing says nothing about the mutation.
        """
        return self.verdict in (KILLED, SURVIVED)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_mutations(mutations: Iterable[Mutation], command: Sequence[str],
                  root: Path, timeout: int = 600) -> list:
    """Apply each mutation, run `command`, restore, and verify the restore.

    `PYTHONDONTWRITEBYTECODE=1` is forced for the child: a stale .pyc from a
    previous mutation once produced a false SURVIVED, and the run had to be
    repeated twice to notice.
    """
    root = Path(root)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    results = []

    # THE CONTROL. Run `command` on the UNMUTATED tree first, and refuse a
    # verdict if it does not pass.
    #
    # Without it a suite that was already red reported every mutation as
    # `killed`, because the verdict was read off a non-zero exit code alone and
    # a red suite exits non-zero whatever you do to the source. Reproduced
    # 2026-08-25: a scratch tree with a deliberately failing assertion, one
    # mutation, output `killed`, `Result.trustworthy` True. That is the exact
    # shape this module's opening line calls out - "refuse a verdict without a
    # control ... the verdict a reader most wants to trust is the one this makes
    # impossible to fake" - and the `control` predicate could not catch it,
    # because it only ever inspects the MUTATED sources. The sibling
    # `scripts/utils/mutation_harness.py` has run this baseline all along; the
    # asymmetry was the whole defect.
    mutations = list(mutations)
    try:
        baseline = subprocess.run(command, cwd=str(root), env=env,
                                  capture_output=True, text=True, timeout=timeout)
        baseline_detail = "" if baseline.returncode == 0 else (
            f"exit {baseline.returncode}: "
            f"{(baseline.stderr or baseline.stdout or '').strip().splitlines()[-1:]}")
    except (OSError, subprocess.SubprocessError) as exc:
        baseline_detail = f"the control run could not start: {exc}"
    if baseline_detail:
        return [Result(m.label, BASELINE_RED,
                       f"the command does not pass on the unmutated tree "
                       f"({baseline_detail}); every verdict below would be "
                       f"read off a failure that was already there")
                for m in mutations]

    for mutation in mutations:
        saved = {}
        invalid = None
        try:
            for relpath, old, new in mutation.edits:
                path = root / relpath
                if path not in saved:
                    # ONCE per file, not once per edit. Snapshotting per edit
                    # reads the already-mutated text as the "original" for a
                    # second edit to the same file, and the restore then puts
                    # that partially-mutated text back and calls it clean.
                    # Measured: two edits to one file left the first edit in the
                    # tree, and the harness reported a successful restore.
                    saved[path] = (path.read_text(encoding="utf-8"), _digest(path))
                current = path.read_text(encoding="utf-8")
                occurrences = current.count(old)
                if occurrences == 0:
                    invalid = f"anchor not found in {relpath}"
                    break
                if occurrences > 1:
                    # `replace(old, new, 1)` patches the FIRST match, which for
                    # an anchor that is not unique is whichever function comes
                    # first in the file. The sibling harness measured the harm
                    # on 2026-08-26: three mutations landed in
                    # `_install_windows_task` and `_post_json` instead of their
                    # targets, so the target code was never mutated and all
                    # three were reported SURVIVED - a confident wrong verdict
                    # that sends the reader to weaken a guard that was fine.
                    # This module exists to make that impossible, and it had the
                    # `not present` half of the check and not this one.
                    invalid = (f"anchor matches {occurrences} places in "
                               f"{relpath}; a mutation that may land somewhere "
                               f"other than where it was aimed proves nothing")
                    break
                path.write_text(current.replace(old, new, 1), encoding="utf-8")

            if invalid is None:
                mutated = {relpath: (root / relpath).read_text(encoding="utf-8")
                           for relpath, _old, _new in mutation.edits}
                invalid = mutation.control(mutated)

            if invalid is None:
                # A mutation exists to break the code, and "break" includes
                # "never return": the sibling harness records a mutation that
                # turned a paging loop into an endless one. The baseline run
                # above catches `SubprocessError` (which covers TimeoutExpired)
                # and this one did not, so a hanging mutation raised out of the
                # function, no `Result` was recorded for it, and EVERY REMAINING
                # mutation in the batch was dropped - the caller got an
                # exception where the signature promises `list[Result]`. A
                # timeout is a CAUGHT mutation: observable behaviour changed,
                # which is what the probe is looking for. It is labelled so a
                # hang is never read as a clean assertion failure.
                try:
                    proc = subprocess.run(command, cwd=str(root), env=env,
                                          capture_output=True, text=True,
                                          timeout=timeout)
                except subprocess.TimeoutExpired:
                    results.append(Result(mutation.label, KILLED,
                                          f"timeout after {timeout}s"))
                else:
                    verdict = KILLED if proc.returncode != 0 else SURVIVED
                    results.append(Result(mutation.label, verdict))
            else:
                results.append(Result(mutation.label, INVALID, invalid))
        finally:
            for path, (original, digest) in saved.items():
                path.write_text(original, encoding="utf-8")
                if _digest(path) != digest:
                    raise RuntimeError(
                        f"restore of {path} does not match its pre-mutation digest; "
                        f"the tree is dirty and every later verdict is worthless"
                    )

    return results


def render(results: Sequence[Result]) -> str:
    """A table for the gate artifact. `invalid` is shouted, not tucked away.

    So is `baseline-red`, and more loudly: it means no verdict in the table was
    measured against anything.
    """
    width = max((len(r.label) for r in results), default=0)
    lines = []
    for r in results:
        mark = "  " if r.verdict == KILLED else "!!"
        detail = f"  ({r.detail})" if r.detail else ""
        lines.append(f"{mark} {r.verdict:<9} {r.label:<{width}}{detail}")
    return "\n".join(lines)
