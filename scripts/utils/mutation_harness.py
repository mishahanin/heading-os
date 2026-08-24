#!/usr/bin/env python3
"""Run a mutation set against a test file, with the child bounded.

Library module (snake_case per the workspace naming convention). Import it
from a scratch harness instead of re-writing the loop each time::

    from scripts.utils.mutation_harness import run_mutations
    raise SystemExit(run_mutations(ROOT, TESTS, MUTATIONS))

Why this exists, and why the bounds are not optional
----------------------------------------------------
Every scratch harness so far spawned ``pytest`` with a bare
``subprocess.run(...)`` -- no timeout, no memory cap. On 2026-08-24 one
mutation turned a paging loop in ``scripts/gmail-reader.py`` into an endless
one. The stub server answered forever, the pytest child grew to 47 GB against
a 48 GB WSL allocation, and the kernel OOM-killer took the child and then the
whole ``init.scope`` with it: the agent session, the terminal manager, and
every shell. ``/tmp`` was wiped by the distro re-init that followed, taking 58
generated audit reports with it.

That is the shape of the risk. A mutation exists precisely to break the code,
and "break" includes "never return" and "allocate without bound". A harness
that does not bound its child is a harness that can take the machine down.

Two bounds, both applied to the child:

* **Wall clock** -- ``timeout`` seconds per run. A timeout counts as CAUGHT:
  the mutation changed observable behaviour, which is exactly what a mutation
  test is looking for. It is reported as ``caught (timeout)`` so a hang is
  never confused with a clean assertion failure.
* **Address space** -- ``RLIMIT_AS`` on the child only, via ``preexec_fn``.
  The child gets MemoryError instead of the machine getting an OOM-killer.
  POSIX only; on other platforms the wall clock is the only bound and
  ``run_mutations`` says so once.

The baseline run gets the same bounds. A baseline that hangs is a broken
harness, not a finding.
"""

from __future__ import annotations

import os
import resource
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_TIMEOUT_S = 300
DEFAULT_MEMORY_LIMIT_GB = 4


def _limit_child(memory_limit_bytes: int):
    """preexec_fn that caps the child's address space. POSIX only."""

    def _apply():
        resource.setrlimit(resource.RLIMIT_AS,
                           (memory_limit_bytes, memory_limit_bytes))

    return _apply


def _clear_pycache(root: Path) -> None:
    """Stale bytecode makes a real mutation look like a caught one."""
    subprocess.run(["find", str(root), "-name", "__pycache__", "-type", "d",
                    "-exec", "rm", "-rf", "{}", "+"], capture_output=True)


def run_tests(root: Path, tests, *, timeout: int, memory_limit_bytes: int,
              python: str | None = None):
    """Run the tests once. Returns "pass" | "fail" | "timeout".

    `python` defaults to the repo venv, which is what the workspace runs; a
    caller passes it explicitly only to test this module itself.
    """
    _clear_pycache(root)
    test_args = [tests] if isinstance(tests, str) else list(tests)
    kwargs = {}
    if os.name == "posix" and memory_limit_bytes:
        kwargs["preexec_fn"] = _limit_child(memory_limit_bytes)
    try:
        proc = subprocess.run(
            [python or str(root / ".venv/bin/python"), "-m", "pytest", *test_args,
             "-q", "-x", "--no-header"],
            cwd=str(root), capture_output=True, text=True, timeout=timeout,
            **kwargs)
    except subprocess.TimeoutExpired:
        return "timeout"
    return "pass" if proc.returncode == 0 else "fail"


def run_mutations(root, tests, mutations, *, timeout: int = DEFAULT_TIMEOUT_S,
                  memory_limit_gb: int = DEFAULT_MEMORY_LIMIT_GB,
                  python: str | None = None) -> int:
    """Apply each mutation, run the tests, restore. Returns a process exit code.

    `mutations` is a sequence of ``(tag, relative_path, old, new)``. `old` must
    appear in the file; a missing anchor is reported and counted as a survivor,
    because a mutation that never applied proved nothing.

    Restoration happens in a ``finally``, and the backup is written before the
    edit, so a kill between the two still leaves the backup on disk beside the
    file. If this process is killed anyway, a ``.mutbak`` next to a source file
    is the sign: move it back before trusting the tree.
    """
    root = Path(root)
    limit = memory_limit_gb * 1024 ** 3
    if os.name != "posix":
        print("note: no address-space limit on this platform; the wall clock "
              f"({timeout}s) is the only bound", file=sys.stderr)

    baseline = run_tests(root, tests, timeout=timeout, memory_limit_bytes=limit,
                         python=python)
    if baseline != "pass":
        print(f"BASELINE {baseline.upper()}")
        return 2
    print("baseline green", flush=True)

    survivors = []
    for tag, rel, old, new in mutations:
        target = root / rel
        backup = target.with_suffix(target.suffix + ".mutbak")
        shutil.copy2(target, backup)
        try:
            text = target.read_text(encoding="utf-8")
            if old not in text:
                print(f"{tag:5} {rel:42} ANCHOR MISSING", flush=True)
                survivors.append((tag, "anchor missing"))
                continue
            target.write_text(text.replace(old, new, 1), encoding="utf-8")
            outcome = run_tests(root, tests, timeout=timeout,
                                memory_limit_bytes=limit, python=python)
            label = {"fail": "caught", "timeout": "caught (timeout)",
                     "pass": "SURVIVED"}[outcome]
            print(f"{tag:5} {rel:42} {label}", flush=True)
            if outcome == "pass":
                survivors.append((tag, rel))
        finally:
            shutil.move(str(backup), str(target))

    _clear_pycache(root)
    print(f"\n{len(mutations) - len(survivors)}/{len(mutations)} caught")
    for tag, why in survivors:
        print(f"  SURVIVOR {tag}: {why}")
    return 1 if survivors else 0
