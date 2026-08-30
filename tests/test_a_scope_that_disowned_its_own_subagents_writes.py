#!/usr/bin/env python3
"""`session_scope` read the parent transcript and disowned its own subagents.

Claude Code records a dispatched agent's tool calls in a sidecar,
`<transcript-dir>/<session-id>/subagents/agent-*.jsonl`, and the parent
transcript never contains them. `files_written` streamed only the one file the
hook handed it, so every file a subagent wrote carried no `tool_use` block that
the scope could see and was classified as another author's.

MEASURED 2026-08-30 on this workspace, session
`bbbbbbbb-0000-4000-8000-000000000001`: of 80 changed Python files,
`narrow(changed, parent_transcript)` kept 43 and dropped 37. All 37 were written
by three of that same session's own subagents; the union over the parent and its
106 sidecars kept all 80 and dropped 0. Truly foreign: zero. `turn-check` then
printed "37 changed file(s) written by another session, not checked" - a
narrowed check reading as a complete one over 46% of the changed set, which is
the defect `.claude/rules/scope-claims.md` exists to refuse.

The fix must not become "keep everything": a sidecar under a DIFFERENT session
id is still another author's, and the negative control below is what proves the
scope still says no.

Run:
    .venv/bin/python -m pytest \\
        tests/test_a_scope_that_disowned_its_own_subagents_writes.py \\
        -q --no-header -p no:randomly
"""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.utils import session_scope  # noqa: E402
from scripts.utils.session_scope import files_written, narrow  # noqa: E402

SESSION = "bbbbbbbb-0000-4000-8000-000000000001"
OTHER_SESSION = "9f0c1d2e-0000-4000-8000-abcdefabcdef"


def _entry(tool: str, file_path: str) -> str:
    """One transcript record, in the shape both layers actually use.

    Verified 2026-08-30 against a real sidecar: a subagent's write block carries
    the same `type`/`name`/`input.file_path` nesting as the parent's, plus a
    `caller` key the scope has no reason to read.
    """
    return json.dumps({"message": {"content": [
        {"type": "tool_use", "name": tool, "id": "toolu_x",
         "caller": "agent", "input": {"file_path": file_path}}]}})


def _session(tmp_path: Path, session: str = SESSION) -> Path:
    """A transcript path with no sidecar directory yet."""
    projects = tmp_path / "projects"
    projects.mkdir(exist_ok=True)
    return projects / f"{session}.jsonl"


def _sidecar(transcript: Path, name: str, body: str) -> Path:
    """Write one `agent-*.jsonl` beside the transcript, in the real layout."""
    directory = transcript.parent / transcript.stem / "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"agent-{name}.jsonl"
    path.write_text(body, encoding="utf-8")
    return path


# ============================================================
# The measured failure
# ============================================================


def test_a_file_only_a_subagent_wrote_is_this_sessions(tmp_path):
    """The defect, in one assertion: the sidecar write must be attributed here."""
    parent = _session(tmp_path)
    mine = tmp_path / "parent.py"
    theirs = tmp_path / "by_subagent.py"
    parent.write_text(_entry("Write", str(mine)) + "\n", encoding="utf-8")
    _sidecar(parent, "a00000000000000a1", _entry("Edit", str(theirs)) + "\n")

    assert files_written(parent) == {mine, theirs}


def test_narrow_keeps_the_file_its_subagent_wrote(tmp_path):
    """The consequence the operator sees: it is checked, not dropped."""
    parent = _session(tmp_path)
    mine = tmp_path / "parent.py"
    theirs = tmp_path / "by_subagent.py"
    for f in (mine, theirs):
        f.write_text("x = 1\n", encoding="utf-8")
    parent.write_text(_entry("Write", str(mine)) + "\n", encoding="utf-8")
    _sidecar(parent, "a00000000000000d4", _entry("Write", str(theirs)) + "\n")

    assert narrow([mine, theirs], parent) == ([mine, theirs], 0)


def test_several_sidecars_all_count(tmp_path):
    """Three subagents wrote the 37 files in the measured run, not one."""
    parent = _session(tmp_path)
    parent.write_text("", encoding="utf-8")
    written = []
    for name in ("a00000000000000b2", "a00000000000000c3", "a00000000000000d4"):
        target = tmp_path / f"{name}.py"
        written.append(target)
        _sidecar(parent, name, _entry("Write", str(target)) + "\n")

    assert files_written(parent) == set(written)


# ============================================================
# The negative control: a fix that keeps everything is not a fix
# ============================================================


def test_another_sessions_sidecar_is_still_foreign(tmp_path):
    """A sidecar under a different session id must NOT be adopted.

    Without this the "fix" is just fail-open wearing a scope's clothes.
    """
    parent = _session(tmp_path)
    mine = tmp_path / "mine.py"
    strangers = tmp_path / "strangers.py"
    for f in (mine, strangers):
        f.write_text("x = 1\n", encoding="utf-8")
    parent.write_text(_entry("Write", str(mine)) + "\n", encoding="utf-8")

    # Same projects directory, different session id: a real parallel session.
    other = _session(tmp_path, OTHER_SESSION)
    other.write_text(_entry("Write", str(strangers)) + "\n", encoding="utf-8")
    _sidecar(other, "b111111111111111", _entry("Write", str(strangers)) + "\n")

    assert files_written(parent) == {mine}
    assert narrow([mine, strangers], parent) == ([mine], 1)


def test_a_sibling_transcript_file_is_not_a_sidecar(tmp_path):
    """The scoping is the directory, not the `agent-` prefix.

    A file named like a sidecar but sitting beside the transcript rather than
    under `<session-id>/subagents/` belongs to nobody here.
    """
    parent = _session(tmp_path)
    mine = tmp_path / "mine.py"
    strangers = tmp_path / "strangers.py"
    for f in (mine, strangers):
        f.write_text("x = 1\n", encoding="utf-8")
    parent.write_text(_entry("Write", str(mine)) + "\n", encoding="utf-8")
    (parent.parent / "agent-decoy.jsonl").write_text(
        _entry("Write", str(strangers)) + "\n", encoding="utf-8")

    assert narrow([mine, strangers], parent) == ([mine], 1)


def test_the_writing_tools_are_still_the_only_ones_in_a_sidecar(tmp_path):
    """A subagent's Read is not authorship either. Same rule, both layers."""
    parent = _session(tmp_path)
    parent.write_text("", encoding="utf-8")
    read_only = tmp_path / "only_read.py"
    _sidecar(parent, "a00000000000000e5", _entry("Read", str(read_only)) + "\n")

    assert files_written(parent) == set()


# ============================================================
# Absent, corrupt, unreadable
# ============================================================


def test_no_sidecar_directory_changes_nothing(tmp_path):
    """The ordinary session: it dispatched no agent. No warning, no error."""
    parent = _session(tmp_path)
    mine = tmp_path / "mine.py"
    mine.write_text("x = 1\n", encoding="utf-8")
    parent.write_text(_entry("Write", str(mine)) + "\n", encoding="utf-8")

    assert not (parent.parent / parent.stem).exists()
    assert files_written(parent) == {mine}
    assert narrow([mine], parent) == ([mine], 0)


def test_an_empty_sidecar_directory_changes_nothing(tmp_path):
    """The directory exists because something else lives there (`tool-results`,
    `workflows`); no `agent-*.jsonl` in it is not an error."""
    parent = _session(tmp_path)
    mine = tmp_path / "mine.py"
    parent.write_text(_entry("Write", str(mine)) + "\n", encoding="utf-8")
    (parent.parent / parent.stem / "subagents").mkdir(parents=True)

    assert files_written(parent) == {mine}


def test_a_corrupt_line_in_a_sidecar_does_not_take_down_the_sweep(tmp_path):
    """One shredded record must not discard the writes that DID parse.

    Identical to the parent transcript's existing policy, because it is the same
    reader: `_blocks` returns "did not parse" and the loop keeps going.
    """
    parent = _session(tmp_path)
    parent.write_text("", encoding="utf-8")
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    _sidecar(parent, "a00000000000000a1", "\n".join([
        _entry("Write", str(a)),
        "garbage {",
        _entry("Edit", str(b)),
    ]) + "\n")

    assert files_written(parent) == {a, b}


def test_a_wholly_unparseable_sidecar_is_unknown_not_silence(tmp_path):
    """Unknown propagates: the caller widens back to everything.

    The module's stated invariant is "unknown is not empty". A sidecar it cannot
    read makes the write set unknowable, and answering with the partial set
    would drop the subagent's files exactly as before - the defect, restored
    through the error path.
    """
    parent = _session(tmp_path)
    mine = tmp_path / "mine.py"
    strangers = tmp_path / "strangers.py"
    for f in (mine, strangers):
        f.write_text("x = 1\n", encoding="utf-8")
    parent.write_text(_entry("Write", str(mine)) + "\n", encoding="utf-8")
    _sidecar(parent, "a00000000000000a1", "this is not json\n{nope\n\n")

    assert files_written(parent) is None
    assert narrow([mine, strangers], parent) == ([mine, strangers], 0)


def test_an_unreadable_parent_is_still_unknown_with_sidecars_present(tmp_path):
    """The parent's own contract is untouched by the new layer."""
    parent = _session(tmp_path)
    _sidecar(parent, "a00000000000000a1", _entry("Write", str(tmp_path / "a.py")) + "\n")

    assert files_written(parent) is None


def test_an_empty_sidecar_file_is_empty_not_unknown(tmp_path):
    """A sidecar created a moment ago has no content lines to fail to parse."""
    parent = _session(tmp_path)
    mine = tmp_path / "mine.py"
    parent.write_text(_entry("Write", str(mine)) + "\n", encoding="utf-8")
    _sidecar(parent, "a00000000000000a1", "")

    assert files_written(parent) == {mine}


# ============================================================
# Cost: this runs on every turn
# ============================================================


def test_each_sidecar_is_read_exactly_once(tmp_path, monkeypatch):
    """106 sidecars existed on the measured day. Reading one twice is 106 extra
    passes over a hook budget the operator waits on."""
    parent = _session(tmp_path)
    parent.write_text("", encoding="utf-8")
    for i in range(5):
        _sidecar(parent, f"a{i:016x}", _entry("Write", str(tmp_path / f"{i}.py")) + "\n")

    seen: list[Path] = []
    real = session_scope._scan

    def counting(path):
        seen.append(Path(path))
        return real(path)

    monkeypatch.setattr(session_scope, "_scan", counting)
    session_scope.files_written(parent)

    assert len(seen) == len(set(seen)), f"a file was read twice: {seen}"
    assert len(seen) == 6, f"expected the parent plus 5 sidecars, got {seen}"


def test_the_meta_sidecar_files_are_not_read(tmp_path):
    """Every `agent-*.jsonl` has an `agent-*.meta.json` twin. Reading those would
    double the file count for nothing; they hold no `tool_use` block."""
    parent = _session(tmp_path)
    parent.write_text("", encoding="utf-8")
    directory = parent.parent / parent.stem / "subagents"
    directory.mkdir(parents=True)
    (directory / "agent-a00000000000000a1.meta.json").write_text(
        json.dumps({"agentType": "general-purpose", "spawnDepth": 1}),
        encoding="utf-8")

    assert [p.name for p in session_scope._subagent_transcripts(parent)] == []
    assert files_written(parent) == set()


# ============================================================
# What the operator is told
# ============================================================


def _hook_reason(monkeypatch, result: dict) -> str | None:
    """Drive `.claude/hooks/turn-check.py` with a canned checker result."""
    spec = importlib.util.spec_from_file_location(
        "hook_turn_check", ROOT / ".claude" / "hooks" / "turn-check.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(result) + "\n", stderr="")

    monkeypatch.setattr(hook.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "transcript_path": "/x/session.jsonl"})))
    emitted: list[str] = []
    monkeypatch.setattr("builtins.print",
                        lambda *a, **k: emitted.append(" ".join(str(x) for x in a)))
    hook.main()
    monkeypatch.undo()
    for line in emitted:
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if parsed.get("decision") == "block":
            return parsed["reason"]
    return None


def test_the_hook_reports_the_number_that_was_actually_excluded(monkeypatch, tmp_path):
    """The count the operator reads is the count `narrow` dropped, not a guess."""
    parent = _session(tmp_path)
    mine = tmp_path / "mine.py"
    by_agent = tmp_path / "by_agent.py"
    strangers = tmp_path / "strangers.py"
    for f in (mine, by_agent, strangers):
        f.write_text("x = 1\n", encoding="utf-8")
    parent.write_text(_entry("Write", str(mine)) + "\n", encoding="utf-8")
    _sidecar(parent, "a00000000000000b2", _entry("Write", str(by_agent)) + "\n")

    kept, dropped = narrow([mine, by_agent, strangers], parent)
    assert kept == [mine, by_agent]
    assert dropped == 1

    reason = _hook_reason(monkeypatch, {
        "status": "fail", "lane": "tests", "failures": ["tests/test_x.py::test_y"],
        "skipped_foreign": dropped})
    assert reason is not None
    assert "Not covered by this check" in reason
    assert f"{dropped} changed file(s)" in reason


def test_the_exclusion_line_does_not_assert_an_author_it_never_established(
        monkeypatch, tmp_path):
    """A dropped file is another session's OR a Bash edit here. The sentence has
    to carry both, because `WRITING_TOOLS` cannot tell them apart."""
    reason = _hook_reason(monkeypatch, {
        "status": "fail", "lane": "tests", "failures": ["tests/test_x.py::test_y"],
        "skipped_foreign": 3})
    assert reason is not None
    assert "Bash" in reason, (
        "the exclusion names only 'another session', which the scope cannot "
        "establish: an edit made here through Bash lands in the same bucket")


def test_the_checker_note_carries_the_same_disjunction():
    """One wording, two renderers. The human note and the hook must not drift."""
    spec = importlib.util.spec_from_file_location(
        "turn_check_cli", ROOT / "scripts" / "turn-check.py")
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)

    note = checker._foreign_note({"skipped_foreign": 4})
    assert "4 changed file(s)" in note
    assert "Bash" in note
    assert checker._foreign_note({"skipped_foreign": 0}) == ""


@pytest.mark.parametrize("body", ["null\n", "42\n", '"a string"\n'])
def test_a_sidecar_holding_a_json_scalar_is_unknown(tmp_path, body):
    """A scalar is not a transcript record; it must not read as "wrote nothing"."""
    parent = _session(tmp_path)
    parent.write_text(_entry("Write", str(tmp_path / "a.py")) + "\n", encoding="utf-8")
    _sidecar(parent, "a00000000000000a1", body)

    assert files_written(parent) is None
