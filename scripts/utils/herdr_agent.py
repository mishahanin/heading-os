#!/usr/bin/env python3
"""
herdr_agent.py - the narrow seam between this workspace and the HERDR terminal
manager.

It exists for one reason: Claude Code has NO in-process entry point for
`/compact`. In the 2.1.235 binary `compactConversation` is internal and
`requestCompact` is transcript-file garbage collection, neither reachable from a
hook. So a workspace that wants to compact at its own threshold has to ask from
OUTSIDE, by submitting the literal text `/compact` to the terminal that hosts the
session. The harness then parses it exactly as it would parse the same characters
typed by hand.

That path is proven rather than assumed. On 2026-08-19, in session
`10f49ae5-1632-4483-874f-e3a5483afe99`:

    herdr agent prompt w37:p1 "/compact"
    -> {"type": "agent_prompted", ... "agent_status": "working"}

and the transcript then gained one `compact_boundary` carrying
`trigger: "manual"`, `preTokens: 324190`, `postTokens: 10929`. A prompt submitted
while `agent_status` is `working` is QUEUED and runs when the current turn ends,
which is why the caller must submit and then let its turn finish. A hook that
blocks after submitting deadlocks its own request.

Self-identification needs no hardcoded pane. `herdr agent list` returns, per
agent, an `agent_session` object shaped `{"kind": "id", "value": "<uuid>"}`
beside `pane_id`, and that uuid is the Claude session id a hook already holds.

WHAT THIS MODULE DELIBERATELY IS NOT: a "send text to a terminal" utility.
`submit_compact()` takes a pane and submits a fixed module constant. The string
is not a parameter and must not become one. Anything able to inject arbitrary
text into a live agent session is a capability this workspace should not grow by
accident, and a fixed literal removes the question rather than answering it.

WHY THE CONFIRMATION OPTIONS ARE UNUSED: `herdr agent prompt` accepts `--wait`,
`--until <STATUS>` and `--timeout <MS>`. The seam uses none of them and infers
the queue from the returned `agent_status`. `--wait` without `--timeout` waits
indefinitely, which is disqualifying inside a Stop hook with a 90-second budget,
and `--wait` with a timeout buys a confirmation the caller cannot act on anyway -
by then it has already decided to let the turn end. Do not "improve" this.
"""

import json
import shutil
import subprocess

# The one string this module is allowed to submit. Not a parameter, by design.
COMPACT_COMMAND = "/compact"

HERDR_BIN = "herdr"

# `agent list` walks every pane; `prompt` is a single socket call. The label
# calls sit inside a hook's grace period and must never eat it, so they get the
# tightest budget of the three.
LIST_TIMEOUT = 10
PROMPT_TIMEOUT = 10
LABEL_TIMEOUT = 2


class HerdrUnavailable(RuntimeError):
    """HERDR could not be reached, or answered with something unparseable.

    Raised for the binary being absent, a timeout, a non-zero exit, and JSON that
    does not parse. Deliberately NOT raised when the session simply is not hosted
    by HERDR - that is an ordinary outcome and `resolve_pane` returns None for it.
    The distinction matters to every caller: "not hosted" is a fact worth
    reporting, "could not tell" is not the same fact, and conflating them is the
    defect `.claude/rules/scope-claims.md` exists to prevent.
    """


def _run(args: list[str], timeout: int) -> dict:
    """Run one herdr subcommand and return its parsed payload.

    List arguments only, never `shell=True`, per the global security policy.
    """
    if shutil.which(HERDR_BIN) is None:
        raise HerdrUnavailable(f"{HERDR_BIN} is not on PATH")
    try:
        proc = subprocess.run(
            [HERDR_BIN, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise HerdrUnavailable(
            f"{HERDR_BIN} {' '.join(args)} timed out after {timeout}s"
        ) from exc
    except OSError as exc:
        raise HerdrUnavailable(f"{HERDR_BIN} could not be executed: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        first = detail[0] if detail else "no output"
        raise HerdrUnavailable(
            f"{HERDR_BIN} {' '.join(args)} exited {proc.returncode}: {first}"
        )
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise HerdrUnavailable(
            f"{HERDR_BIN} {' '.join(args)} returned unparseable output"
        ) from exc


def agents() -> list[dict]:
    """Every agent HERDR currently hosts, as raw records.

    Every shape this does not expect leaves as HerdrUnavailable, which is the
    one exception the callers handle. `_run` already converts UNPARSEABLE output;
    the gap this closes is output that parses into the wrong shape - a bare list
    where an object belongs, say, which a future HERDR release can introduce
    without warning. That used to leave `payload.get` raising AttributeError
    straight through `_herdr_status` and out of the Stop hook, so a third-party
    format change cost the session its whole checkpoint system: no offer, no
    save, no countdown, exit 1, no output.

    A malformed ENTRY raises rather than being skipped. Dropping it would answer
    "HERDR does not host this session" when the truth is "the lookup could not be
    trusted", and `resolve_pane` exists to keep those two apart.
    """
    payload = _run(["agent", "list"], LIST_TIMEOUT)
    if not isinstance(payload, dict):
        raise HerdrUnavailable(
            f"agent list returned {type(payload).__name__}, not an object"
        )
    result = payload.get("result")
    if result is None:
        result = {}
    if not isinstance(result, dict):
        raise HerdrUnavailable(
            f"agent list result is {type(result).__name__}, not an object"
        )
    found = result.get("agents")
    if not isinstance(found, list):
        raise HerdrUnavailable("agent list carried no agents array")
    if not all(isinstance(agent, dict) for agent in found):
        raise HerdrUnavailable("agent list carried a malformed agent record")
    return found


def resolve_pane(session_id: str) -> str | None:
    """The pane hosting this Claude session, or None when HERDR does not host it.

    None is a normal answer, not a failure. Callers degrade to whatever they do
    without HERDR; they must not report it as an error, and they must not report
    it as "not hosted" when the lookup itself failed - that case raises
    HerdrUnavailable instead, and the two are different sentences to an operator.
    """
    if not session_id:
        return None
    for agent in agents():
        session = agent.get("agent_session")
        # `or {}` alone covered a missing key and left a present-but-wrong one
        # crashing, which is the same defect `agents()` above just closed one
        # level up. A record that does not describe a session is not this
        # session, so skipping it is the honest reading here.
        if not isinstance(session, dict):
            continue
        if session.get("kind") == "id" and session.get("value") == session_id:
            pane = agent.get("pane_id")
            return pane if isinstance(pane, str) and pane else None
    return None


def submit_compact(pane_id: str) -> dict:
    """Submit the fixed `/compact` literal to one pane. Returns HERDR's payload.

    The submission is asynchronous by nature: if the agent is `working`, the
    prompt queues and runs when that turn ends. The returned `agent_status` says
    which of the two happened; nothing here waits for the result.
    """
    if not pane_id:
        raise HerdrUnavailable("submit_compact needs a pane id")
    return _run(["agent", "prompt", pane_id, COMPACT_COMMAND], PROMPT_TIMEOUT)


def set_label(pane_id: str, text: str) -> dict:
    """Set the agent's display label - the countdown surface during a wait.

    Chosen over writing to /dev/tty, which would fight Claude Code's own TUI for
    the same lines, and over hook stdout, which the harness shows only after the
    hook returns - by which time the wait it described is over.
    """
    if not pane_id:
        raise HerdrUnavailable("set_label needs a pane id")
    return _run(["agent", "rename", pane_id, text], LABEL_TIMEOUT)


def clear_label(pane_id: str) -> dict:
    """Restore the agent's own label. A frozen countdown is worse than none."""
    if not pane_id:
        raise HerdrUnavailable("clear_label needs a pane id")
    return _run(["agent", "rename", pane_id, "--clear"], LABEL_TIMEOUT)
