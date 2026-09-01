#!/usr/bin/env python3
"""Shared reader for `scripts/odin-cadence.py --json`.

Two callers ran that child with the same flag and wanted the same dict back:
`scripts/utils/ops_signals.py:odin_cadence_state` and
`scripts/generate-dashboard.py:collect_capture_payoff`. The first inspected
`returncode` and shape-guarded the parse; the second did neither, so a crashed
child was indistinguishable from a child with nothing to report. Both guards
live here now, once, so the next caller cannot pick up half of them.

A third call site, `scripts/prime-health-parallel.py:run_odin_cadence`,
deliberately does NOT use this. It runs `--quiet` for the one-line human nudge,
not `--json`, and its contract is a rendered status block rather than a cadence
report. It already checks `returncode` on its own terms.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

DEFAULT_TIMEOUT = 60


def read_cadence_json(engine_root: Path, *, script: Path | None = None,
                      timeout: int = DEFAULT_TIMEOUT) -> tuple[dict, str | None]:
    """Run `odin-cadence.py --json` under `engine_root` and return
    `(cadence, error)`.

    `cadence` is the parsed report, or `{}` when there is not one. `error` is
    None on success and a short human-readable reason otherwise, so a caller
    that renders can say WHY it has no numbers instead of drawing a blank that
    looks measured.

    A missing script is not an error. The cadence helper is ceo-only and is
    simply absent on an exec workspace, which returns `({}, None)`.

    `script` overrides the derived path for a caller that already holds it as a
    module constant. The default is spelled out here rather than in the callers
    so that the script this function runs and the flags it passes stay in one
    function body, which is what `_ops_signal_invocations` in
    `tests/test_a_flag_the_code_never_read.py` reads to prove every flag sent
    to a child is a flag that child declares.
    """
    target = script if script is not None else engine_root / "scripts" / "odin-cadence.py"
    if not target.exists():
        return {}, None

    try:
        proc = subprocess.run(
            [sys.executable, str(target), "--json"],
            cwd=str(engine_root),
            capture_output=True,
            text=True,
            # `errors="replace"`. `text=True` alone decodes STRICT UTF-8 and
            # raises UnicodeDecodeError inside `subprocess.run` itself, past the
            # handler below: UnicodeDecodeError is a ValueError, and neither
            # OSError nor subprocess.SubprocessError is one of its bases.
            # MEASURED 2026-09-01 with a child writing a raw 0xFF to stdout: the
            # call raised, so this function could not deliver the `(dict, str)`
            # its own docstring promises for every failure, and the traceback
            # killed the whole `generate-dashboard.py` run rather than drawing
            # one panel with a named reason. The sibling readers already had
            # this: `scripts/scrutinize-dispatch.py` passes `errors="replace"`
            # on the same grounds and `.claude/hooks/checkpoint-precompact.py`
            # names ValueError in its handler. This was the copy that missed it.
            # A mangled byte in a stderr tail is readable evidence; a traceback
            # is none.
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return {}, f"{target.name} did not run ({type(e).__name__}: {e})"

    if proc.returncode != 0:
        # The check one call site was missing. `odin-cadence.py` only ever
        # `return 0`s, so a non-zero exit is an uncaught exception: the
        # traceback goes to stderr and stdout is left EMPTY. Empty stdout
        # parses to `{}`, which reads exactly like a workspace that has
        # nothing to report, so the failure arrived as a confident blank.
        lines = (proc.stderr or "").strip().splitlines()
        tail = lines[-1].strip() if lines else "no stderr"
        return {}, f"{target.name} exited {proc.returncode}: {tail}"

    if not proc.stdout.strip():
        return {}, f"{target.name} exited 0 but printed nothing"

    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return {}, f"{target.name} printed unparseable JSON ({e})"

    if not isinstance(parsed, dict):
        # A helper that prints `null`, a list or a number emits valid JSON and
        # is not a cadence report; `.get` on it raises AttributeError.
        return {}, (f"{target.name} printed {type(parsed).__name__}, "
                    f"not a cadence object")

    return parsed, None
