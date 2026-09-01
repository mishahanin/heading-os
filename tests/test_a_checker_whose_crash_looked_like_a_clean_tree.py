#!/usr/bin/env python3
"""Three ways the Stop-hook turn check said nothing and meant something.

`.claude/hooks/turn-check.py` runs `scripts/turn-check.py` in a subprocess and
translates its JSON into the Stop-hook protocol. Three of its degradation paths
were unannounced or fatal. MEASURED 2026-08-31 by driving the real hook with
`CHECKER` pointed at a stub:

    stub writes a traceback to stderr, exits 1   -> exit 0, stdout '', stderr ''
    stub prints `[]`                             -> AttributeError, exit 1
    CHECKER missing from disk                    -> exit 0, stdout '', stderr ''

The first and third are the shape SEC-007 exists to refuse: a control whose
failure is byte-identical to its success. The hook already knew this and said so
three lines earlier, in its own words about the timeout branch, and then the
branch immediately below it returned 0 without a word. A syntax error in the
checker or in any module it imports, an absent pytest, and an OOM kill all reach
it, so the end-of-turn control could be gone permanently with nobody told. The
second is defensive rather than reachable today (the real checker always emits an
object) and it crashes rather than blocking, so a checker upgrade that changed
the output shape would take the hook out through a traceback.

The third defect is a coverage claim, not a crash. `session_scope.files_written`
answers None for an absent, unreadable or malformed transcript, and for any ONE
unreadable subagent sidecar out of the hundred-odd a busy session writes.
`narrow` collapsed that None into `(items, 0)`, which is byte-identical to a
genuine zero-drop, and the result dict carried nothing to tell the two apart:

    malformed   files_written -> None
    malformed   narrow(['a.py'], t)       -> (['a.py'], 0)
    absent      narrow(['a.py'], missing) -> (['a.py'], 0)
    reads-only  narrow(['a.py'], t2)      -> ([], 1)      <- a real scope answers 1

With `skipped_foreign` at 0 the hook printed no "Not covered by this check" line
and asserted the failure was in "the uncommitted Python edits in this turn". A
parallel session's deliberately-red TDD test then blocks this turn and is
attributed to it, which is the 2026-08-12 incident verbatim and the incident
`.claude/rules/scope-claims.md` was written about. Its obligation 3 is to widen
back to everything AND say the state is unknown; only the widening had been
built.

What this file pins, each with a case on both sides so no assertion here is
satisfied by a hook that shouts on every turn or one that refuses everything:

* a checker that reaches no verdict is announced on stderr, with its exit code,
  and a checker that answers normally still says nothing;
* a non-object result degrades instead of raising, and an object still routes;
* the result dict distinguishes established scope from unknown scope, and the
  hook renders the widening as an exclusion.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.utils.session_scope import narrow, narrow_with_scope  # noqa: E402

HOOK = ROOT / ".claude" / "hooks" / "turn-check.py"


def _load_checker():
    """`scripts/turn-check.py` as a module, under a name of this file's own.

    `tests/test_turn_check.py` registers `turn_check_mod` for the same file, and
    two test modules writing one `sys.modules` key is an order-dependent test.
    """
    spec = importlib.util.spec_from_file_location(
        "turn_check_degradation_mod", ROOT / "scripts" / "turn-check.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["turn_check_degradation_mod"] = mod
    spec.loader.exec_module(mod)
    return mod


tc = _load_checker()


# ============================================================
# Driving the real hook, with a stub where the checker goes
# ============================================================

def _tree(tmp_path: Path, stub: str | None) -> Path:
    """A scratch workspace holding a COPY of the real hook and a stub checker.

    The hook derives `WORKSPACE` from its own `__file__` and `CHECKER` from
    that, so a scratch tree of the same shape is what lets the stub take the
    checker's place while the hook itself runs unmodified, in its own process,
    from its real bytes on disk. Monkeypatching the module in-process would
    measure an import instead of a run.
    """
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy2(HOOK, hooks / "turn-check.py")
    if stub is not None:
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "turn-check.py").write_text(stub, encoding="utf-8")
    return tmp_path


def _drive(tree: Path, payload: dict | None = None) -> subprocess.CompletedProcess:
    """Run the copied hook the way Claude Code runs it: JSON on stdin."""
    return subprocess.run(
        [sys.executable, str(tree / ".claude" / "hooks" / "turn-check.py")],
        input=json.dumps(payload if payload is not None else {}),
        capture_output=True, text=True, timeout=60,
    )


def _emits(result: dict, exit_code: int = 0) -> str:
    """A stub checker that prints one result and exits with the given code."""
    return (f"import sys\n"
            f"print({json.dumps(json.dumps(result))})\n"
            f"sys.exit({exit_code})\n")


CRASHING_STUB = (
    "import sys\n"
    "print('Traceback (most recent call last):', file=sys.stderr)\n"
    "print(\"ImportError: no module named 'scripts.utils.colors'\", file=sys.stderr)\n"
    "sys.exit(1)\n"
)

SILENT_STUB = "import sys\nsys.exit(0)\n"


def test_the_copied_hook_is_the_real_one(tmp_path):
    """Anchor. Every case below drives a copy, so a copy that drifted from the
    source, or a tree the hook could not run in at all, would leave those cases
    measuring something other than this hook."""
    tree = _tree(tmp_path, _emits({"status": "pass", "files": 0, "tests_run": 0}))
    copied = tree / ".claude" / "hooks" / "turn-check.py"
    assert copied.read_bytes() == HOOK.read_bytes()
    proc = _drive(tree)
    assert proc.returncode == 0, proc.stderr


# ------------------------------------------------------------
# Defect 1: a checker that reached no verdict
# ------------------------------------------------------------

@pytest.mark.parametrize("label, stub, expected_exit", [
    ("a traceback and a non-zero exit", CRASHING_STUB, 1),
    ("no output at all", SILENT_STUB, 0),
])
def test_a_checker_that_printed_no_result_is_announced(tmp_path, label, stub,
                                                       expected_exit):
    """The measured defect. Silence here is indistinguishable from a clean tree.

    The exit code is part of the message on purpose: it is the only thing that
    separates "the checker died" from "the checker had nothing to say", and the
    old branch consulted it not at all.
    """
    proc = _drive(_tree(tmp_path, stub))
    assert proc.returncode == 0, "the hook is never fatal"
    assert proc.stdout.strip() == "", "no verdict means no block decision"
    assert proc.stderr.strip(), f"the hook degraded in silence: {label}"
    assert f"exit {expected_exit}" in proc.stderr, proc.stderr


def test_the_announcement_carries_the_checkers_own_error(tmp_path):
    """A line saying only "something went wrong" is not actionable, and the
    operator has no other window onto the child process."""
    proc = _drive(_tree(tmp_path, CRASHING_STUB))
    assert "ImportError" in proc.stderr, proc.stderr


def test_a_missing_checker_is_announced(tmp_path):
    """A renamed or moved script passes every turn forever. Same shape, and it
    is the branch furthest from the operator's attention."""
    proc = _drive(_tree(tmp_path, None))
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""
    assert "turn-check" in proc.stderr and "not found" in proc.stderr, proc.stderr


@pytest.mark.parametrize("status", ["pass", "cached", "idle"])
def test_a_checker_that_answers_normally_still_says_nothing(tmp_path, status):
    """The other side, and the one that matters most.

    A hook that wrote to stderr unconditionally would satisfy every assertion
    above while making the end of every clean turn noisy, which is how a warning
    gets muted. Silence on the ordinary path is the contract.
    """
    proc = _drive(_tree(tmp_path, _emits({"status": status, "files": 1,
                                          "tests_run": 1,
                                          "reason": "nothing to do"})))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", proc.stdout
    assert proc.stderr.strip() == "", f"{status}: {proc.stderr}"


def test_a_real_failure_still_blocks_the_turn(tmp_path):
    """The hook's whole purpose, kept beside the degradations so that "never
    fatal" cannot quietly become "never blocks"."""
    proc = _drive(_tree(tmp_path, _emits(
        {"status": "fail", "lane": "compile", "files": 1, "tests_run": 0,
         "failures": ["scripts/x.py: SyntaxError: invalid syntax"]}, exit_code=1)))
    assert proc.returncode == 0
    decision = json.loads(proc.stdout.strip())
    assert decision["decision"] == "block"
    assert "SyntaxError" in decision["reason"]
    assert proc.stderr.strip() == "", proc.stderr


# ------------------------------------------------------------
# Defect 2: a result that parsed but was not an object
# ------------------------------------------------------------

@pytest.mark.parametrize("payload", ["[]", '"x"', "3", "null"])
def test_a_result_that_is_not_an_object_degrades_instead_of_raising(tmp_path,
                                                                    payload):
    """`result.get` on a list raises AttributeError, which exits 1 with a
    traceback and blocks nothing. The stdin payload one screen above is guarded
    the same way, for the same reason: the shape comes from elsewhere."""
    stub = f"print({payload!r})\n"
    proc = _drive(_tree(tmp_path, stub))
    assert proc.returncode == 0, proc.stderr
    assert "AttributeError" not in proc.stderr, proc.stderr
    assert "not an object" in proc.stderr, proc.stderr
    assert proc.stdout.strip() == ""


def test_an_object_result_is_not_mistaken_for_the_wrong_shape(tmp_path):
    """Positive twin: the guard must not refuse the shape the checker emits."""
    proc = _drive(_tree(tmp_path, _emits({"status": "pass", "files": 2,
                                          "tests_run": 3})))
    assert "not an object" not in proc.stderr
    assert proc.stderr.strip() == ""


# ============================================================
# Defect 3: a widened check that read as a narrowed one
# ============================================================

def _transcript(path: Path, written: list[Path], tool: str = "Edit") -> Path:
    lines = [json.dumps({"message": {"content": [
        {"type": "tool_use", "name": tool, "input": {"file_path": str(p)}}
    ]}}) for p in written]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_an_unreadable_transcript_reports_that_scope_is_unknown(tmp_path):
    """The unit boundary. `(items, 0)` was the answer for both a transcript that
    could not be read and one that dropped nothing, and the caller had to print
    a sentence about coverage from those two numbers alone."""
    malformed = tmp_path / "malformed.jsonl"
    malformed.write_text("this is not json\n{nope\n\n", encoding="utf-8")

    assert narrow_with_scope([Path("a.py")], malformed) == ([Path("a.py")], 0, False)
    assert narrow_with_scope([Path("a.py")], tmp_path / "absent.jsonl") == (
        [Path("a.py")], 0, False)
    assert narrow_with_scope([Path("a.py")], None) == ([Path("a.py")], 0, False)


def test_an_established_scope_says_so(tmp_path):
    """The other side. A flag that answered False everywhere would make the
    exclusion line permanent, which is noise rather than information."""
    mine = tmp_path / "mine.py"
    mine.write_text("X = 1\n", encoding="utf-8")
    theirs = tmp_path / "theirs.py"
    theirs.write_text("Y = 2\n", encoding="utf-8")
    t = _transcript(tmp_path / "session.jsonl", [mine])

    kept, dropped, known = narrow_with_scope([mine, theirs], t)
    assert (kept, dropped, known) == ([mine], 1, True)

    reads_only = _transcript(tmp_path / "reads.jsonl", [mine], tool="Read")
    assert narrow_with_scope([mine], reads_only) == ([], 1, True)


def test_the_two_value_contract_is_unchanged(tmp_path):
    """`narrow` has four other test files and one production caller behind it.
    The third value is additive; the old shape still answers as it did."""
    mine = tmp_path / "mine.py"
    mine.write_text("X = 1\n", encoding="utf-8")
    t = _transcript(tmp_path / "session.jsonl", [mine])
    assert narrow([mine], t) == ([mine], 0)
    assert narrow([mine], tmp_path / "absent.jsonl") == ([mine], 0)


def _fail_over(tmp_path, monkeypatch, transcript) -> dict:
    """Run the checker over one broken file with the given transcript."""
    broken = tmp_path / "broken.py"
    broken.write_text("def f(:\n", encoding="utf-8")
    monkeypatch.setattr(tc, "changed_python_files", lambda: [broken])
    result = tc.run(timeout=30, use_cache=False, transcript=transcript)
    assert result["status"] == "fail", result
    return result


def test_the_result_carries_the_unknown_scope(tmp_path, monkeypatch):
    """What the hook reads. Without this field the hook cannot tell a widened
    run from a narrowed one, whatever it prints."""
    malformed = tmp_path / "malformed.jsonl"
    malformed.write_text("not json at all\n", encoding="utf-8")
    assert _fail_over(tmp_path, monkeypatch, malformed)["scope_unknown"] is True
    assert _fail_over(tmp_path, monkeypatch, None)["scope_unknown"] is True


def test_the_result_does_not_cry_unknown_over_a_real_scope(tmp_path, monkeypatch):
    """Positive twin at the same seam."""
    broken = tmp_path / "broken.py"
    broken.write_text("def f(:\n", encoding="utf-8")
    t = _transcript(tmp_path / "session.jsonl", [broken])
    monkeypatch.setattr(tc, "changed_python_files", lambda: [broken])
    result = tc.run(timeout=30, use_cache=False, transcript=t)
    assert result["status"] == "fail", result
    assert result["scope_unknown"] is False
    assert result["skipped_foreign"] == 0


_FAILING = {"status": "fail", "lane": "compile", "files": 1, "tests_run": 0,
            "failures": ["scripts/x.py: SyntaxError: invalid syntax"],
            "skipped_foreign": 0, "skipped_contract": 0, "deselected_slow": 0,
            "unmeasured": 0}


def _reason(tmp_path, result: dict) -> str:
    proc = _drive(_tree(tmp_path, _emits(result, exit_code=1)))
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())["reason"]


def test_the_block_message_names_the_widening(tmp_path):
    """The operator-facing half, and the only half the operator reads.

    The message keeps saying the failure is in the uncommitted Python edits in
    this turn, because that sentence is registered in
    `tests/test_scope_claims.py` and rewording it would retire the registration.
    Obligation 2 of the rule is satisfied the way the other three exclusions
    satisfy it: by naming what the run did not establish.
    """
    reason = _reason(tmp_path, dict(_FAILING, scope_unknown=True))
    assert "Not covered by this check:" in reason, reason
    assert "session scope could not be established" in reason, reason
    assert "another session wrote" in reason, reason


def test_the_block_message_stays_quiet_when_scope_is_known(tmp_path):
    """Without this, an exclusion printed unconditionally would satisfy the test
    above while telling the operator every turn that nothing is attributable."""
    reason = _reason(tmp_path, dict(_FAILING, scope_unknown=False))
    assert "Not covered by this check" not in reason, reason
    assert "session scope" not in reason, reason


def test_an_older_result_without_the_field_is_read_as_known(tmp_path):
    """The field is absent from an `idle` or `cached` result, and from any
    checker predating it. Absent must mean quiet, not a bare "None" in the
    operator's message."""
    reason = _reason(tmp_path, dict(_FAILING))
    assert "session scope" not in reason, reason
    assert "None" not in reason, reason


def test_the_widening_note_sits_beside_the_other_exclusions(tmp_path):
    """One list, one rendering. A note appended by its own separate branch would
    read as a second sentence and drift from the three that were already there.
    """
    reason = _reason(tmp_path, dict(_FAILING, scope_unknown=True,
                                    deselected_slow=4))
    tail = reason.split("Not covered by this check:")[1]
    assert "session scope could not be established" in tail
    assert "4 slow test(s) not run here" in tail
    assert tail.count(";") == 1, tail
