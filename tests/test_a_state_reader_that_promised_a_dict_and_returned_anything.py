"""Two ways the checkpoint seam broke the one thing it promises not to break.

`scripts/utils/checkpoint_paths` is imported by five hooks on every turn, and
both of these functions carry a docstring saying a failure here must not reach
the operator. Measured 2026-08-30:

1. `read_json` is annotated `-> dict` and wraps `json.loads` in an `except`
   whose comment reads "a corrupt state file must not stop a turn". `json.loads`
   succeeds on any well-formed JSON, so `null`, `"oops"`, `[]` and `3` were
   returned unchanged. Every consumer then died on the value the function
   believed it had handled: `locked_state` raised TypeError on
   `state["session_auto"] = True`, and `auto_mode` raised AttributeError on
   `.get`. `_session_hard`, in the same file, already documents hand-edited
   state files as an anticipated input.

2. `handoff_dir` reaches two in-tree modules with nothing around them, under a
   docstring that says "A hook must not raise here ... a refusal that propagates
   costs a handoff nobody can regenerate". It needs no sabotage to break:
   `HEADING_OS_DATA` is pinned per host, and pointing it at a directory that has
   been moved or deleted makes `data_overlay_present()` raise `DataRootError`
   BY DESIGN ("Refusing to fall back"). That refusal is right for a tool about
   to write the operator's data and wrong for a hook about to write a handoff.

The redirect target matters as much as the redirect: `.claude/state/handoff` is
gitignored, so a handoff can never be committed into the engine by accident.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.utils.checkpoint_paths import (
    auto_mode,
    handoff_dir,
    locked_state,
    raise_unattended,
    read_json,
    unattended_mode,
)

REPO = Path(__file__).resolve().parent.parent

# Well-formed JSON, none of it an object. `"0"` and `"false"` are in here on
# purpose: a falsy non-dict slipped past `if state:` guards and looked fine.
NON_OBJECT_PAYLOADS = ['"oops"', "null", "[]", "3", "0", "false",
                       '["session_auto", true]']


def test_the_payload_corpus_is_not_empty_and_is_really_non_object():
    assert len(NON_OBJECT_PAYLOADS) >= 6
    for payload in NON_OBJECT_PAYLOADS:
        assert not isinstance(json.loads(payload), dict)


@pytest.mark.parametrize("payload", NON_OBJECT_PAYLOADS)
def test_read_json_answers_a_dict_for_every_parseable_non_object(payload, tmp_path):
    path = tmp_path / "checkpoint-abc.json"
    path.write_text(payload, encoding="utf-8")
    assert read_json(path) == {}


@pytest.mark.parametrize("payload", NON_OBJECT_PAYLOADS)
def test_a_hook_can_still_write_its_switch_over_a_non_object_state(payload, tmp_path):
    """`locked_state` is the read-modify-write every hook and the CLI use. It
    yielded whatever `read_json` returned, so the operator's switch died on the
    assignment and the state file kept its garbage."""
    path = tmp_path / "checkpoint-abc.json"
    path.write_text(payload, encoding="utf-8")

    with locked_state(path) as state:
        raise_unattended(state)

    written = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(written, dict)
    assert written["session_unattended"] is True
    assert unattended_mode(written) is True


@pytest.mark.parametrize("payload", NON_OBJECT_PAYLOADS)
def test_the_statusline_read_of_a_non_object_state_does_not_raise(payload, tmp_path):
    """`auto_mode` runs on every render. It raised AttributeError on a truthy
    non-dict, which is the whole status bar gone for the rest of the session."""
    path = tmp_path / "checkpoint-abc.json"
    path.write_text(payload, encoding="utf-8")
    assert auto_mode(read_json(path)) is False


def test_a_real_state_file_is_still_read_back_unchanged(tmp_path):
    """The negative case: the guard must not swallow a legitimate state."""
    path = tmp_path / "checkpoint-abc.json"
    payload = {"session_auto": True, "session_hard_threshold": 40,
               "compact_history": [{"trigger": "auto"}]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert read_json(path) == payload
    assert auto_mode(read_json(path)) is True


def test_an_absent_state_file_is_still_an_empty_dict(tmp_path):
    assert read_json(tmp_path / "checkpoint-nothing.json") == {}


def test_a_truncated_state_file_is_still_an_empty_dict(tmp_path):
    path = tmp_path / "checkpoint-abc.json"
    path.write_text('{"session_auto": tr', encoding="utf-8")
    assert read_json(path) == {}


# ------------------------------------------------------------------ handoff_dir

_HANDOFF_PROBE = r"""
import json, os, sys
sys.path.insert(0, {repo!r})
os.environ["HEADING_OS_DATA"] = sys.argv[2]
from pathlib import Path
from scripts.utils.checkpoint_paths import handoff_dir
project = Path(sys.argv[3])
try:
    print("RESULT " + str(handoff_dir(project, root=Path(sys.argv[1]))))
except BaseException as exc:
    print("RAISED " + type(exc).__name__ + ": " + str(exc))
"""


def _probe_handoff(data_root: str, project: Path) -> str:
    """Resolve the handoff dir in a CHILD process, so the poisoned
    `HEADING_OS_DATA` and the workspace-root caches never touch this session."""
    proc = subprocess.run(
        [sys.executable, "-c", _HANDOFF_PROBE.format(repo=str(REPO)),
         str(REPO), data_root, str(project)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return proc.stdout.strip().splitlines()[-1]


def test_a_stale_data_root_pin_files_the_handoff_instead_of_losing_it(tmp_path):
    """The engine tree is real, the overlay pin is not. `data_overlay_present()`
    refuses by design; the hook must not inherit that refusal."""
    project = tmp_path / "project"
    project.mkdir()
    line = _probe_handoff(str(tmp_path / "gone" / "nowhere"), project)
    assert line.startswith("RESULT "), line
    assert Path(line[len("RESULT "):]) == project / ".claude" / "state" / "handoff"


def test_the_redirect_target_is_gitignored_and_never_inside_the_engine(tmp_path):
    """The point of `.claude/state/`: a handoff written there cannot be committed
    into the engine repository by accident. Asked of git, not of a comment."""
    project = tmp_path / "project"
    project.mkdir()
    line = _probe_handoff(str(tmp_path / "gone" / "nowhere"), project)
    target = Path(line[len("RESULT "):])
    assert REPO not in target.parents and target != REPO

    # The same relative location inside the engine clone is ignored by git.
    probe = REPO / ".claude" / "state" / "handoff" / "leak-probe.md"
    check = subprocess.run(
        ["git", "check-ignore", "-q", str(probe)], cwd=str(REPO),
        capture_output=True,
    )
    assert check.returncode == 0, ".claude/state/handoff is not gitignored"


def test_a_healthy_pin_still_resolves_to_the_overlay(tmp_path):
    """The negative case, and the one the guard must not break: with a real
    overlay the archive still goes to the data root, not to the fallback."""
    overlay = tmp_path / "overlay"
    (overlay / "knowledge").mkdir(parents=True)
    (overlay / "outputs").mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()

    line = _probe_handoff(str(overlay), project)
    assert line.startswith("RESULT "), line
    target = Path(line[len("RESULT "):])
    assert target == overlay / "outputs" / "operations" / "handoff-archive"
    assert target != project / ".claude" / "state" / "handoff"


def test_a_non_engine_tree_is_untouched_by_the_guard(tmp_path):
    """A plugin bundle in somebody else's repository never enters the branch at
    all, and must keep its project-local layout."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    project = tmp_path / "someone-elses-repo"
    project.mkdir()
    assert handoff_dir(project, root=bundle) == project / ".claude" / "handoff"
