"""`check_yard_write_guard` — a worktree may not write into HELM.

Driven through the REAL dispatcher as a REAL process, in a REAL worktree, with
the payload shape MEASURED from `_dispatch.py` on 2026-09-03: `tool_name`,
`tool_input`, `cwd`, and a block rendered as
`hookSpecificOutput.permissionDecision == "deny"` with exit 0.

Every case is asserted in both directions. The same payload that must be
refused from a YARD is sent again from HELM and must be permitted, because a
guard that denies everything satisfies each refusal below and makes the main
clone unusable within the hour.

Three of these cases exist only because an earlier revision of the design
failed them:

  * `cat <HELM>/x; rm -rf <HELM>/y` was PERMITTED, because the guard asked
    whether the whole command string STARTED with a read-only verb.
  * `cd /tmp && cat <HELM>/x` was REFUSED, by the same rule, for the same
    reason.
  * `cd <HELM> && rm -rf x` was PERMITTED even after the chain was split,
    because the link that names HELM is a read-only `cd` and the link that
    does the damage names no path at all.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DISPATCH_REL = Path(".claude") / "hooks" / "_dispatch.py"
DATA_SIBLING = ROOT.parent / ".heading-os-data"


def _run(checkout: Path, payload: dict) -> dict | None:
    """Feed one payload to the dispatcher in `checkout`. Return its decision."""
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)  # nothing here may depend on it
    result = subprocess.run(
        [sys.executable, str(checkout / DISPATCH_REL)],
        input=json.dumps(payload), cwd=str(checkout),
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0, (
        f"dispatcher exited {result.returncode}: {result.stderr}")
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def _denied(decision: dict | None) -> bool:
    if not decision:
        return False
    specific = decision.get("hookSpecificOutput", {})
    return specific.get("permissionDecision") == "deny"


def _yard_denied(decision: dict | None) -> bool:
    """Denied BY THIS WALL specifically.

    `_denied` alone is not enough. `check_release_gate` sits earlier in the
    dispatcher's CHECKS list and refuses any `git push` or `git commit` the
    operator did not ask for in the current turn, so an end-to-end push case
    is refused before this wall is ever consulted. Asserting "something said
    no" would therefore pass with this wall deleted. Every case below asks
    which wall answered.
    """
    return _denied(decision) and "YARD write guard" in _reason(decision)


def _call_the_guard_directly(checkout: Path, payload: dict) -> dict | None:
    """Call `check_yard_write_guard` in `checkout`, bypassing the CHECKS order.

    Needed for the push cases, where an earlier wall legitimately answers
    first. This still drives the real function in the real worktree; only the
    dispatcher's ordering is stepped around.
    """
    harness = (
        "import json, sys, importlib.util\n"
        "spec = importlib.util.spec_from_file_location('d', sys.argv[1])\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "print(json.dumps(mod.check_yard_write_guard(json.loads(sys.argv[2]))))\n"
    )
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    result = subprocess.run(
        [sys.executable, "-c", harness, str(checkout / DISPATCH_REL),
         json.dumps(payload)],
        cwd=str(checkout), capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _reason(decision: dict | None) -> str:
    if not decision:
        return ""
    return decision.get("hookSpecificOutput", {}).get(
        "permissionDecisionReason", "")


def _write(path, cwd) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": str(path)},
            "cwd": str(cwd)}


def _bash(command, cwd) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command},
            "cwd": str(cwd)}


# ============================================================
# The wall is silent in HELM
# ============================================================

def test_helm_may_write_inside_itself():
    assert not _yard_denied(_run(ROOT, _write(ROOT / "scripts" / "x.py", ROOT)))


def test_helm_may_run_git_in_the_data_overlay():
    """HELM records the data's history. That is the whole point of the rule.

    Asked of THIS wall. The release gate may still refuse the same command for
    its own reason, which is correct and none of this wall's business.
    """
    assert _call_the_guard_directly(
        ROOT, _bash(f"git -C {DATA_SIBLING} commit -m x", ROOT)) is None


def test_helm_may_push_as_far_as_this_wall_is_concerned():
    assert _call_the_guard_directly(
        ROOT, _bash("git push origin main", ROOT)) is None


# ============================================================
# Writes: into HELM refused, everywhere else permitted
# ============================================================

def test_a_write_into_helm_from_a_yard_is_refused(armed_worktree):
    decision = _run(armed_worktree, _write(ROOT / "scripts" / "x.py",
                                           armed_worktree))
    assert _yard_denied(decision)
    assert "HELM" in _reason(decision)


def test_a_write_into_helm_is_still_refused_after_cd_into_helm(armed_worktree):
    """`cwd` is the agent's, not the guard's evidence.

    The predicate is the SHAPE of this checkout's `.git`, so moving the shell
    into HELM changes nothing. An earlier design read `CLAUDE_PROJECT_DIR` and
    would have concluded "this session is HELM" and allowed the write.
    """
    decision = _run(armed_worktree, _write(ROOT / "scripts" / "x.py", ROOT))
    assert _yard_denied(decision)


def test_a_relative_write_that_resolves_into_helm_is_refused(armed_worktree):
    decision = _run(armed_worktree, _write("scripts/x.py", ROOT))
    assert _yard_denied(decision)


def test_a_write_inside_the_yard_itself_is_permitted(armed_worktree):
    assert not _yard_denied(
        _run(armed_worktree, _write(armed_worktree / "scripts" / "x.py",
                                    armed_worktree)))


def test_a_relative_write_inside_the_yard_is_permitted(armed_worktree):
    assert not _yard_denied(
        _run(armed_worktree, _write("scripts/x.py", armed_worktree)))


def test_a_write_into_the_data_overlay_is_permitted(armed_worktree):
    """The rule people get wrong. Files into the overlay are the normal case:
    a task produces a report, a deck, a PDF. Only git in it is closed."""
    target = DATA_SIBLING / "outputs" / "operations" / "a-report.md"
    assert not _yard_denied(_run(armed_worktree, _write(target, armed_worktree)))


def test_a_write_to_a_temporary_directory_is_permitted(armed_worktree, tmp_path):
    assert not _yard_denied(
        _run(armed_worktree, _write(tmp_path / "scratch.txt", armed_worktree)))


def test_an_edit_with_no_destination_is_refused(armed_worktree):
    """Fail closed: where it would land cannot be established."""
    decision = _run(armed_worktree,
                    {"tool_name": "Edit", "tool_input": {},
                     "cwd": str(armed_worktree)})
    assert _yard_denied(decision)


# ============================================================
# Bash: the chain, and the three cases an earlier revision got wrong
# ============================================================

@pytest.mark.parametrize("command", [
    "rm -rf {helm}/scripts",
    "echo x > {helm}/scripts/x.py",
    "cp /tmp/x {helm}/scripts/x.py",
    "tee {helm}/scripts/x.py",
    "cat {helm}/x; rm -rf {helm}/y",
    "cd {helm} && rm -rf scripts",
    "cd {helm} && echo x > y",
])
def test_a_bash_command_reaching_into_helm_is_refused(armed_worktree, command):
    decision = _run(armed_worktree,
                    _bash(command.format(helm=ROOT), armed_worktree))
    assert _yard_denied(decision), command


@pytest.mark.parametrize("command", [
    "cat {helm}/CLAUDE.md",
    "cd /tmp && cat {helm}/CLAUDE.md",
    "rg needle {helm}/scripts",
    "git log --oneline -5",
    "ls {helm}/scripts",
    "head -20 {helm}/pyproject.toml",
])
def test_a_read_only_bash_command_is_permitted_from_a_yard(
    armed_worktree, command,
):
    assert not _yard_denied(
        _run(armed_worktree, _bash(command.format(helm=ROOT), armed_worktree))
    ), command


def test_the_same_refused_commands_are_permitted_from_helm():
    """The pair that stops this suite being satisfied by a guard that denies
    everything."""
    for command in ("rm -rf {helm}/scratch",
                    "cat {helm}/x; rm -rf {helm}/y",
                    "cd {helm} && rm -rf scratch"):
        assert not _yard_denied(_run(ROOT, _bash(command.format(helm=ROOT), ROOT))), \
            command


# ============================================================
# Publishing
# ============================================================

@pytest.mark.parametrize("command", [
    "git push",
    "git push origin HEAD",
    "git push --force origin my-branch",
    "cd /tmp && git push",
])
def test_pushing_from_a_yard_is_refused(armed_worktree, command):
    """Asked of this wall directly.

    End to end, `check_release_gate` answers first and refuses every push the
    operator did not ask for, which is correct and would hide whether this wall
    works at all. The case that matters is the one where the operator DID ask:
    the release gate opens, and this wall must still refuse.
    """
    decision = _call_the_guard_directly(armed_worktree,
                                        _bash(command, armed_worktree))
    assert decision is not None, command
    assert decision.get("decision") == "block"
    assert "PUBLIC" in decision.get("reason", "")


# ============================================================
# The one rule: files into the overlay yes, git in it no
# ============================================================

@pytest.mark.skipif(not DATA_SIBLING.is_dir(),
                    reason="no data overlay beside this clone, so the paths "
                           "these cases name do not exist here. The write and "
                           "HELM cases above are unaffected.")
@pytest.mark.parametrize("template", [
    "git -C {data} commit -m x",
    "git -C {data} add .",
    "git -C {data} push",
    "cd {data} && git commit -m x",
    "cd {data} && git add -A && git commit -m x",
])
def test_running_git_in_the_data_overlay_from_a_yard_is_refused(
    armed_worktree, template,
):
    """Asked of this wall directly, for the same reason as the push cases.

    `check_release_gate` sits earlier and refuses any commit the operator did
    not ask for in the current turn, so end to end it answers first. The case
    that matters is the one where the operator DID ask for a data commit: that
    gate opens, and this wall must still refuse, because the refusal is about
    WHERE the commit is being made from, not about whether it was requested.
    """
    payload = _bash(template.format(data=DATA_SIBLING), armed_worktree)
    decision = _call_the_guard_directly(armed_worktree, payload)
    assert decision is not None, template
    assert decision.get("decision") == "block", template
    # `git -C <data> push` is refused by the push branch, which runs first and
    # is the more specific answer for it. Either refusal is this wall's.
    assert ("the one rule" in decision.get("reason", "")
            or "PUBLIC" in decision.get("reason", "")), template
    # And end to end it is refused by something, which is the property the
    # operator actually experiences.
    assert _denied(_run(armed_worktree, payload)), template


@pytest.mark.skipif(not DATA_SIBLING.is_dir(),
                    reason="no data overlay beside this clone")
@pytest.mark.parametrize("template", [
    "git -C {data} log --oneline -5",
    "git -C {data} status",
    "cat {data}/CLAUDE.operational.md",
    "ls {data}",
])
def test_reading_the_data_overlay_from_a_yard_is_permitted(
    armed_worktree, template,
):
    assert not _yard_denied(
        _run(armed_worktree,
             _bash(template.format(data=DATA_SIBLING), armed_worktree))
    ), template


# ============================================================
# Payload shapes this wall must not touch
# ============================================================

@pytest.mark.parametrize("tool_name", ["Read", "Grep", "Glob", "Agent", "Task"])
def test_non_writing_tools_are_not_this_walls_business(armed_worktree,
                                                       tool_name):
    """The dispatcher is registered under five matchers, so these arrive here.

    Named explicitly rather than left to the matcher list: "the settings file
    will not send it" is an assumption about a file, and this dispatcher has
    been wrong about its own matchers before.
    """
    decision = _run(armed_worktree, {
        "tool_name": tool_name,
        "tool_input": {"file_path": str(ROOT / "CLAUDE.md")},
        "cwd": str(armed_worktree),
    })
    assert not _yard_denied(decision)


def test_a_bash_payload_with_no_command_is_not_refused(armed_worktree):
    decision = _run(armed_worktree, {"tool_name": "Bash", "tool_input": {},
                                     "cwd": str(armed_worktree)})
    assert "YARD write guard" not in _reason(decision)


def test_a_payload_with_no_cwd_still_resolves(armed_worktree):
    """`cwd` is absent in some shapes; WORKSPACE is the fallback, and WORKSPACE
    in a worktree is the worktree."""
    decision = _run(armed_worktree,
                    {"tool_name": "Write",
                     "tool_input": {"file_path": "scripts/x.py"}})
    assert not _yard_denied(decision)
