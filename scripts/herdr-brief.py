#!/usr/bin/env python3
"""Deliver a task brief from HELM into a YARD, marked as machine-to-machine.

Usage:

    python scripts/herdr-brief.py w37:p1 "TASK FROM HELM. Read this whole brief..."
    python scripts/herdr-brief.py w37:p1 "$(cat brief.md)"

This is the only sanctioned way HELM sends a brief to a YARD. It prepends one
fixed line to the text and then delegates to `herdr agent prompt <PANE> <TEXT>`.

WHY IT EXISTS
-------------
The release gate in `.claude/hooks/_dispatch.py` decides that the operator
authorised a commit or a push by reading the most recent transcript record with
`promptSource: "typed"` and matching it against a word list. A prompt injected
into a session by ANOTHER Claude session through `herdr agent prompt` lands as
exactly that record. MEASURED 2026-09-05 on one transcript holding both kinds --
a brief delivered from HELM and three prompts the operator typed into that same
yard by hand -- every field is identical except `parentUuid`, `promptId`, `uuid`
and `timestamp`. So any session able to call `herdr agent prompt` could
manufacture the operator's authorisation somewhere else, and on 2026-09-04 every
brief HELM wrote ended with "COMMIT when green", which forged one in every yard
it opened.

No field in the record says who produced the text, so the discriminator has to
be IN the text, put there by the sender. That is this script.

WHAT THIS ESTABLISHES, AND WHAT IT DOES NOT
-------------------------------------------
Together with the gate, it establishes that A PROMPT CARRYING THE MARKER DOES
NOT AUTHORISE A RELEASE. It does NOT establish that a prompt WITHOUT the marker
was typed by a human. Nothing in the transcript can establish that.

The mechanism is therefore FAIL-OPEN by construction: an unmarked brief still
authorises, and the only thing standing behind that is this script plus
`tests/test_a_brief_from_another_session_that_authorised_a_release.py`. A brief
sent by hand with a bare `herdr agent prompt` carries no marker and is
indistinguishable from the operator. Sending one is the hole; not a bug here.

Two designs that look like they would close it were rejected on measurement, and
are recorded so they are not rebuilt. A local challenge-response ("2FA") cannot
work: the verifier and the adversary run as the SAME UID on the SAME machine, so
any secret the gate can read to check an answer, a peer session can read to
produce one, and reading from the tty does not help because `herdr pane run`
types into a pane. A LENGTH CEILING on the authorising prompt was refuted on the
operator's own data: MEASURED 2026-09-06 over 97 of his typed prompts containing
a release word, across six HELM transcripts, min 3, median 26, p90 248, max
5272, and 12 of the 97 longer than 200 characters.

WHY IT DOES NOT LIVE IN `scripts/utils/herdr_agent.py`
------------------------------------------------------
That module says of itself, and it is right: "WHAT THIS MODULE DELIBERATELY IS
NOT: a 'send text to a terminal' utility. The string is not a parameter and must
not become one." Making its fixed `/compact` literal a parameter would grow an
arbitrary-injection capability inside the seam a hook calls on every turn. This
script builds its own argv instead, and the capability stays where an operator
types it.

HELM ONLY. Delivering a prompt changes the behaviour of a running process, which
`CLAUDE.md` puts outside a YARD categorically. `require_main_clone` refuses.
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.clone_guard import require_main_clone  # noqa: E402
from scripts.utils.colors import CYAN, GRAY, GREEN, RED, RESET  # noqa: E402

HERDR_BIN = "herdr"

# `herdr agent prompt` is a single socket call. Ten seconds is the budget
# `scripts/utils/herdr_agent.py` measured for the same subcommand.
PROMPT_TIMEOUT = 10

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / ".claude" / "hooks" / "_dispatch.py"


def brief_marker() -> str:
    """The marker literal, read from the wall that acts on it.

    Loaded from `.claude/hooks/_dispatch.py` rather than spelled again here. A
    security literal written twice is one edit away from being written once:
    this repository's dominant defect shape is a fix that landed in one of N
    copies. The hook is not an importable package (`.claude` is not a Python
    path component), so it is loaded by file path, exactly as the test suite
    loads it. Import cost measured 2026-09-06: 24 ms.
    """
    spec = importlib.util.spec_from_file_location("heading_release_gate", GATE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the release gate from {GATE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._BRIEF_MARKER


def compose(marker: str, text: str) -> str:
    """The marker on its own first line, then the brief verbatim."""
    return f"{marker}\n\n{text}"


def send(pane: str, payload: str) -> int:
    """Hand the composed brief to HERDR. List argv, never `shell=True`."""
    if shutil.which(HERDR_BIN) is None:
        print(f"{RED}herdr-brief: `{HERDR_BIN}` is not on PATH.{RESET}",
              file=sys.stderr)
        return 3
    argv = [HERDR_BIN, "agent", "prompt", pane, payload]
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                timeout=PROMPT_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        print(f"{RED}herdr-brief: `{HERDR_BIN} agent prompt` did not answer "
              f"within {PROMPT_TIMEOUT}s. Nothing is confirmed sent.{RESET}",
              file=sys.stderr)
        return 3
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        print(f"{RED}herdr-brief: herdr exited {result.returncode}.{RESET}",
              file=sys.stderr)
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
        return 3
    print(f"{GREEN}Sent to {pane}.{RESET}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="herdr-brief.py",
        description="Send a marked machine-to-machine brief to a YARD pane.",
        epilog=("The marker makes the brief unable to authorise a commit or a "
                "push in the receiving session. It does NOT make an UNMARKED "
                "prompt trustworthy: no field in a transcript record tells the "
                "operator's typing apart from another session's."),
    )
    parser.add_argument("pane", help="HERDR pane id, e.g. w37:p1")
    parser.add_argument("text", help="the brief, verbatim")
    args = parser.parse_args()

    # After `parse_args`, so `--help` answers from anywhere. A help text is a
    # read; the guard exists to stop a SEND, and a script whose `--help` refuses
    # in a YARD is a script nobody can read the usage of while working on it.
    require_main_clone(__file__)

    marker = brief_marker()

    already = args.text.count(marker)
    if already:
        print(
            f"{RED}herdr-brief: REFUSED. The text already carries the marker "
            f"{already} time(s), so the brief this would send would carry it "
            f"{already + 1}.{RESET}\n"
            f"  A brief is marked once, here, at the moment it is sent. A text "
            f"that arrives already carrying the marker has been through this "
            f"path before or was hand-assembled, and either way what would go "
            f"out is not what its author wrote.\n"
            f"  Remove the marker line from the text and run this again.",
            file=sys.stderr)
        return 2

    payload = compose(marker, args.text)
    print(f"{CYAN}About to send to {args.pane}:{RESET}")
    print(f"{GRAY}{'-' * 72}{RESET}")
    print(payload)
    print(f"{GRAY}{'-' * 72}{RESET}")
    return send(args.pane, payload)


if __name__ == "__main__":
    sys.exit(main())
