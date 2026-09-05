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

DELIVERY IS VERIFIED, NEVER TRUSTED
-----------------------------------
`herdr agent prompt` answers with the pane it was GIVEN, not the pane that
received the text. MEASURED 2026-09-06: a brief addressed to `w59:p1` was
answered `"pane_id":"w59:p1","agent_status":"working"` and landed in a
DIFFERENT yard, `presentation-book-4-uz`, where the operator had live work. It
arrived there as a `queue-operation` and then as a `promptSource: "queued"`
user record; the addressed workspace had no transcript at all. The same shape
was recorded on 2026-09-05 (register item 19) when a brief for `w56:p1`
executed in `w55`, and in both cases the addressed pane had NO AGENT RUNNING.

So this script asks two questions herdr's reply cannot answer:

* BEFORE sending, does the target pane's checkout hold a session at all? A
  workspace whose Claude project directory has no transcript has never run an
  agent, and sending to it is what misdelivers. Refuse.
* AFTER sending, did the text actually arrive THERE? Every brief carries a
  unique id line; this polls the target's own transcript for it and, when it
  does not appear, searches every other project directory and NAMES where it
  went. Exit non-zero either way.

Neither check trusts herdr. Both read the transcripts the harness writes.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.utils.checkpoint_paths import transcript_dir  # noqa: E402
from scripts.utils.clone_guard import require_main_clone  # noqa: E402
from scripts.utils.colors import CYAN, GRAY, GREEN, RED, RESET, YELLOW  # noqa: E402

HERDR_BIN = "herdr"

# `herdr agent prompt` is a single socket call. Ten seconds is the budget
# `scripts/utils/herdr_agent.py` measured for the same subcommand.
PROMPT_TIMEOUT = 10

# How long the delivered text may take to reach the receiving session's
# transcript. MEASURED 2026-09-06 on the misdelivered brief: enqueue at
# 21:30:09, the user record at 21:30:15, so six seconds from send to a record on
# disk. Twenty-five gives that four times over; a brief that has not landed by
# then has not landed.
DELIVERY_TIMEOUT = 25
DELIVERY_POLL = 1.0

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / ".claude" / "hooks" / "_dispatch.py"

BRIEF_ID_PREFIX = "X-HEADING-BRIEF-ID: "


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


def target_dir(checkout: Path, projects_root: Path | None) -> Path | None:
    """Where Claude Code keeps that checkout's transcripts.

    The slug rule is NOT reimplemented here. `checkpoint_paths.transcript_dir`
    owns it, and `tests/test_transcript_dir_has_one_owner.py` fails any second
    copy of the two-replacement mangle in `scripts/**` — it caught this file
    doing exactly that on 2026-09-06, in the push gate, before the copy could
    reach anyone. Fixing at the shared root is obligation 3 of
    `.claude/rules/development-standards.md` and this repository's dominant
    defect shape.

    `projects_root` relocates only the ROOT, for a fixture tree; the directory
    NAME still comes from the owner, so a test cannot pass against a rule the
    real run does not use. None when the owner declines, which it does off
    POSIX rather than guessing a slug it cannot verify.
    """
    owned = transcript_dir(checkout)
    if owned is None:
        return None
    return (projects_root / owned.name) if projects_root else owned


def checkout_for_pane(pane: str, workspaces: list) -> Path | None:
    """The checkout path of the workspace owning `pane`, from herdr's own list.

    Reading the list is not the same as trusting the SEND. The list is the only
    place the pane-to-checkout mapping exists; what it cannot tell us, and what
    this script therefore reads from the transcripts instead, is where a prompt
    actually went.
    """
    workspace_id = pane.split(":", 1)[0]
    for entry in workspaces:
        if entry.get("workspace_id") != workspace_id:
            continue
        worktree = entry.get("worktree") or {}
        path = worktree.get("checkout_path")
        return Path(path) if path else None
    return None


def herdr_workspaces() -> list:
    """`herdr workspace list`, parsed. Empty list on any failure."""
    try:
        result = subprocess.run([HERDR_BIN, "workspace", "list"],
                                capture_output=True, text=True,
                                timeout=PROMPT_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    try:
        return (json.loads(result.stdout).get("result") or {}).get(
            "workspaces") or []
    except (ValueError, AttributeError):
        return []


def transcripts(project_dir: Path) -> list[Path]:
    """The session transcripts under a project directory, newest last."""
    try:
        return sorted(project_dir.glob("*.jsonl"))
    except OSError:
        return []


def carries(paths: list[Path], token: str) -> bool:
    """Whether any of those transcripts contains `token`."""
    needle = token.encode("utf-8")
    for path in paths:
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(1 << 20):
                    if needle in chunk:
                        return True
        except OSError:
            continue
    return False


def landed_elsewhere(projects_root: Path, token: str,
                     target: Path) -> list[str]:
    """Every project directory OTHER than the target whose transcript has it.

    This is the half that turned a silent misdelivery into a named one: on
    2026-09-06 the brief was found in `presentation-book-4-uz`, a yard with the
    operator's live work in it, while herdr had reported success for another.
    """
    found = []
    try:
        candidates = sorted(p for p in projects_root.iterdir() if p.is_dir())
    except OSError:
        return found
    for directory in candidates:
        if directory == target:
            continue
        if carries(transcripts(directory), token):
            found.append(directory.name)
    return found


def verify_delivery(target: Path, token: str, projects_root: Path,
                    timeout: float = DELIVERY_TIMEOUT) -> int:
    """Poll the ADDRESSED session's own transcript for the brief's id.

    Returns 0 when the text arrived there. Non-zero otherwise, having said where
    it went if it can find out.
    """
    deadline = time.monotonic() + timeout
    while True:
        if carries(transcripts(target), token):
            print(f"{GREEN}Delivery verified in {target.name}.{RESET}")
            return 0
        if time.monotonic() >= deadline:
            break
        time.sleep(DELIVERY_POLL)

    strays = landed_elsewhere(projects_root, token, target)
    print(f"{RED}herdr-brief: DELIVERY NOT VERIFIED after {timeout:.0f}s.{RESET}",
          file=sys.stderr)
    print(f"  The brief does not appear in the transcript of the workspace it "
          f"was addressed to ({target.name}).", file=sys.stderr)
    if strays:
        print(f"{RED}  IT LANDED IN: {', '.join(strays)}{RESET}",
              file=sys.stderr)
        print("  That session is now holding a brief written for another tree. "
              "Say so to whoever owns it; do not send again until you know why.",
              file=sys.stderr)
    else:
        print("  It was not found in any other project directory either, so it "
              "may still be in flight, or it may never have been delivered.",
              file=sys.stderr)
    print("  herdr's own reply names the pane it was GIVEN, not the pane that "
          "received the text, so a success line above establishes nothing.",
          file=sys.stderr)
    return 4


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
    parser.add_argument(
        "--projects-root", default=None,
        help=("where Claude Code writes session transcripts (default "
              "~/.claude/projects). Present so the delivery checks can be "
              "driven over a fixture tree; there is no flag to SKIP them."))
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

    # None, not a default: the owner resolves the whole path, and only a
    # fixture relocates the root.
    projects_root = Path(args.projects_root) if args.projects_root else None

    checkout = checkout_for_pane(args.pane, herdr_workspaces())
    if checkout is None:
        print(f"{RED}herdr-brief: REFUSED. herdr does not report a workspace "
              f"owning {args.pane}, so there is no checkout to verify delivery "
              f"against.{RESET}", file=sys.stderr)
        return 5
    target = target_dir(checkout, projects_root)
    if target is None:
        print(f"{RED}herdr-brief: REFUSED. The transcript directory for "
              f"{checkout} cannot be resolved on this platform, so delivery "
              f"cannot be verified and herdr's reply is the only evidence "
              f"there would be. That is what this script exists not to "
              f"trust.{RESET}", file=sys.stderr)
        return 5

    # The pre-flight. MEASURED twice, 2026-09-05 and 2026-09-06: both
    # misdeliveries were to a pane whose session had never started, and both
    # times herdr answered with the pane it was given.
    if not transcripts(target):
        print(
            f"{RED}herdr-brief: REFUSED. No agent has ever run in "
            f"{checkout}.{RESET}\n"
            f"  Its Claude project directory ({target}) holds no transcript, so "
            f"the pane has no session to receive this. Sending anyway is what "
            f"misdelivers: MEASURED 2026-09-06, a brief addressed to a pane in "
            f"exactly this state was answered with a success line naming that "
            f"pane and arrived in a DIFFERENT yard that had live work in it.\n"
            f"  Start an agent in that pane first, then send.",
            file=sys.stderr)
        return 5

    brief_id = f"{BRIEF_ID_PREFIX}{uuid.uuid4()}"
    payload = f"{compose(marker, args.text)}\n\n{brief_id}\n"
    print(f"{CYAN}About to send to {args.pane} ({checkout.name}):{RESET}")
    print(f"{GRAY}{'-' * 72}{RESET}")
    print(payload)
    print(f"{GRAY}{'-' * 72}{RESET}")

    status = send(args.pane, payload)
    if status != 0:
        return status

    print(f"{YELLOW}Verifying delivery in {target.name} ...{RESET}")
    return verify_delivery(target, brief_id, projects_root or target.parent)


if __name__ == "__main__":
    sys.exit(main())
