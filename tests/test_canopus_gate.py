"""Tests for the Canopus freeze check inside the test gate."""
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.utils.canopus_freeze import build_manifest, write_freeze
from scripts.utils.canopus_gate import freeze_gate

STAMP = "2026-01-01T00:00:00+00:00"
REPO_ROOT = Path(__file__).resolve().parent.parent


def _git_init(directory: Path) -> None:
    """Make *directory* a repository, so the gate artifact has a HEAD to read."""
    for argv in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "builder@example.invalid"],
        ["config", "user.name", "Builder"],
    ):
        subprocess.run(["git", "-C", str(directory), *argv], check=True,
                       capture_output=True, text=True)


def _git_commit(directory: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(directory), "add", "-A"], check=True,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(directory), "commit", "-q", "-m", message],
                   check=True, capture_output=True, text=True)


def _bound_manifest(tree: Path, anchor: Path) -> dict:
    """A manifest frozen against the repository the anchor really lives in.

    Since wire 2.2 the gate judges that binding before it reads anything else, so
    a manifest built without `anchor_repo` over an anchor inside a repository
    resolves ANCHOR_UNBOUND and reddens. The two tests below are about which COPY
    of the anchor governs; without this they would be about the binding instead.
    """
    from scripts.utils.canopus_freeze import REPO_PRESENT
    from scripts.utils.canopus_git import repo_identity

    status, identity = repo_identity(anchor.parent)
    return build_manifest([tree / "tests"], tree, label="demo", frozen_at=STAMP,
                          anchor=anchor,
                          anchor_repo={"in_repo": status == REPO_PRESENT,
                                       "identity": identity})


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_alpha.py").write_text("def test_a():\n    assert True\n")
    return root


@pytest.fixture
def anchor(tmp_path: Path) -> Path:
    path = tmp_path / "outside" / "gate-artifact.md"
    path.parent.mkdir(parents=True)
    path.write_text("# gate artifact\n")
    return path


def test_gate_is_silent_with_no_freeze(tree, capsys):
    assert freeze_gate(tree) == 0
    assert capsys.readouterr().out == ""


def test_gate_passes_when_the_lock_is_held(tree, anchor, capsys):
    manifest = build_manifest([tree / "tests"], tree, label="demo",
                              frozen_at=STAMP, anchor=anchor)
    write_freeze(tree, manifest)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n")
    assert freeze_gate(tree) == 0
    assert "LOCK HELD" in capsys.readouterr().out


def test_gate_is_amber_but_passing_without_a_recorded_anchor(tree, anchor, capsys):
    manifest = build_manifest([tree / "tests"], tree, label="demo",
                              frozen_at=STAMP, anchor=anchor)
    write_freeze(tree, manifest)
    assert freeze_gate(tree) == 0
    assert "LOCK UNCONFIRMED" in capsys.readouterr().out


def test_gate_fails_on_loss_of_lock(tree, anchor, capsys):
    manifest = build_manifest([tree / "tests"], tree, label="demo",
                              frozen_at=STAMP, anchor=anchor)
    write_freeze(tree, manifest)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n")
    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    assert freeze_gate(tree) == 1
    assert "LOSS OF LOCK" in capsys.readouterr().out


def test_gate_fails_on_a_corrupt_manifest(tree, capsys):
    (tree / ".canopus").mkdir(parents=True)
    (tree / ".canopus" / "freeze.json").write_text("{ not json")
    assert freeze_gate(tree) == 1
    # The kind, not just the flag. This sentence is the operator's only exit
    # while every write is denied, and `release --force --reason` stopped
    # parsing the moment the kind became required.
    assert "release --force --window --reason" in capsys.readouterr().out


def test_gate_answers_rather_than_raising_on_an_unusable_anchor_path(tree, anchor):
    """The gate must NEVER raise. A raise here fails OPEN.

    Measured, not imagined: with an anchor whose directory holds an embedded NUL
    byte, `subprocess.run` raises ValueError, which is neither an OSError nor a
    SubprocessError, so it walked straight out of git_output, past freeze_gate's
    `except OSError`, and out of pytest_sessionstart as
    `ValueError: embedded null byte`. The enforcement layer of the freeze
    guarantee crashed the harness instead of reporting a state, and the
    pre-wire-2.1 path returned cleanly on the identical input.

    The same seam has a second route, pinned by the test below rather than left
    to a sentence: a gate artifact that is not UTF-8.
    """
    manifest = build_manifest([tree / "tests"], tree, label="demo",
                              frozen_at=STAMP, anchor=anchor)
    manifest["anchor"] = str(anchor.parent.parent / "out\x00side" / "gate-artifact.md")
    write_freeze(tree, manifest)

    assert freeze_gate(tree) == 1


def test_gate_answers_rather_than_raising_on_a_non_utf8_anchor(tree, anchor):
    """The second door on the same seam, and it was open, not masked.

    A gate artifact holding one non-UTF-8 byte raised UnicodeDecodeError out of
    read_anchor, which caught OSError only. That is a ValueError subclass, so it
    walked through anchor_state, resolve_anchor and freeze_gate and out of
    pytest_sessionstart. Widening git_output alone did not reach it: read_anchor
    runs first on the same file, so the crash simply arrived by the other door.
    Measured on this tree before the fix, both routes, not reasoned from types.
    """
    anchor.write_bytes(b"# gate artifact\n\xe9 not utf-8\n")
    manifest = build_manifest([tree / "tests"], tree, label="demo",
                              frozen_at=STAMP, anchor=anchor)
    write_freeze(tree, manifest)

    assert freeze_gate(tree) == 1


# ============================================================
# The gate's own wiring: which resolver it calls, and what it prints
# ============================================================
# In a bare temporary directory resolve_anchor and anchor_state agree, so
# substituting one for the other is invisible. These two put the gate artifact
# inside a repository, which is the only place the precedence decision is
# observable, and pin the gate to the answer the repository gives.


def test_gate_reads_the_committed_anchor_and_not_the_working_file(tree, anchor, capsys):
    """A line appended to the working copy must not reach LOCK HELD from the gate.

    This is the hole wire 2.1 exists to close, asserted at the surface that
    actually fires. Calling anchor_state here instead reads the working file,
    finds the freshly appended hash, and prints green over an approval nobody
    committed.
    """
    _git_init(anchor.parent)
    _git_commit(anchor.parent, "the gate artifact, with no approval in it")
    manifest = _bound_manifest(tree, anchor)
    write_freeze(tree, manifest)
    with anchor.open("a", encoding="utf-8") as handle:
        handle.write(f"\ncanopus-anchor: {manifest['root']}\n")

    assert freeze_gate(tree) == 0
    out = capsys.readouterr().out
    assert "LOCK UNCONFIRMED" in out
    assert "LOCK HELD" not in out


def test_gate_says_why_the_approval_is_unverified(tree, anchor, capsys):
    """An unexplained amber is what costs the operator, so the gate prints the reason.

    The lock axis already falls to amber on an uncommitted approval. This line
    adds the REASON, and it is the fourth approval surface: the other three are
    commands an operator chooses to type, and this one runs at every pytest
    session start.
    """
    _git_init(anchor.parent)
    _git_commit(anchor.parent, "the gate artifact, with no approval in it")
    manifest = _bound_manifest(tree, anchor)
    write_freeze(tree, manifest)
    with anchor.open("a", encoding="utf-8") as handle:
        handle.write(f"\ncanopus-anchor: {manifest['root']}\n")

    assert freeze_gate(tree) == 0
    out = capsys.readouterr().out
    assert "APPROVAL UNVERIFIED" in out
    assert "no approval is recorded in the committed state" in out


def test_a_hidden_repository_reddens_the_gate_and_says_so(tree, anchor, capsys):
    """Wire 2.2, in the ordinary suite: the blinded gate reddens, and names why.

    The regression trail for the frozen contract's SC-2, plus the half the
    contract does not assert: the message. The per-file report is EMPTY on this
    branch, because nothing in the contract moved, so pointing the operator at
    `verify` for a report with nothing in it is how a true red reads like a bug.
    """
    _git_init(anchor.parent)
    _git_commit(anchor.parent, "the gate artifact")
    manifest = _bound_manifest(tree, anchor)
    write_freeze(tree, manifest)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n", encoding="utf-8")
    (anchor.parent / ".git").rename(anchor.parent / ".git-hidden")

    assert freeze_gate(tree) == 1
    out = capsys.readouterr().out
    assert "LOSS OF LOCK" in out
    assert "the approval cannot be attributed" in out
    assert "for the per-file report" not in out


def test_a_moved_contract_and_a_hidden_repository_are_both_named(tree, anchor, capsys):
    """When both are wrong, the gate says both. It used to say only the binding.

    LOSS OF LOCK is reached independently by an unbound anchor and by a moved
    contract, and the two co-occur: this test edits a frozen file AND renames
    the anchor repository's `.git` in the same run. The branch above keyed on
    the binding alone, so the operator was told about the repository and never
    about the movement, its own comment ("nothing in the contract moved") was
    false, and the remedy it implies re-freezes the moved contract with the
    per-file diff never once read.
    """
    _git_init(anchor.parent)
    _git_commit(anchor.parent, "the gate artifact")
    manifest = _bound_manifest(tree, anchor)
    write_freeze(tree, manifest)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n", encoding="utf-8")

    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    (anchor.parent / ".git").rename(anchor.parent / ".git-hidden")

    assert freeze_gate(tree) == 1
    out = capsys.readouterr().out
    assert "LOSS OF LOCK" in out
    assert "The frozen contract moved" in out
    assert "for the per-file report" in out
    assert "the approval cannot be attributed" in out


def test_a_deleted_anchor_does_not_claim_the_contract_moved(tree, anchor, capsys):
    """The commonest red the anchor exists to produce, and the message was false.

    Freeze against an anchor, commit it, then delete the artifact: `git show
    HEAD:<rel>` still answers, so `report["held"]` is TRUE and nothing in the
    contract has moved. The gate said "The frozen contract moved" anyway and sent
    the operator to `verify`, which then reports every frozen file intact.
    """
    _git_init(anchor.parent)
    _git_commit(anchor.parent, "the gate artifact, with no approval in it")
    manifest = _bound_manifest(tree, anchor)
    write_freeze(tree, manifest)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n", encoding="utf-8")
    _git_commit(anchor.parent, "the approval")
    anchor.unlink()

    assert freeze_gate(tree) == 1
    out = capsys.readouterr().out
    assert "LOSS OF LOCK" in out
    assert "is gone" in out
    assert "The frozen contract moved" not in out
    assert "for the per-file report" not in out


def test_a_disagreeing_anchor_does_not_claim_the_contract_moved(tree, anchor, capsys):
    """The second cause that arrives with the contract intact: a hash mismatch.

    A legitimate contract edit re-frozen without a new approval lands here, and
    the committed anchor still records the previous root. Nothing on the tree has
    moved since the freeze, so naming movement is false and the remedy it implies
    is the wrong one: what this needs is `approve --replace --reason`, not a diff.
    """
    _git_init(anchor.parent)
    _git_commit(anchor.parent, "the gate artifact, with no approval in it")
    manifest = _bound_manifest(tree, anchor)
    write_freeze(tree, manifest)
    # A full 64-character digest of another tree, never a truncation of this one:
    # the comparison is whole-value, and a short expected value would be a
    # comparison a builder with a shell could satisfy by hand.
    stale = "b" * 64
    anchor.write_text(f"canopus-anchor: {stale}\n", encoding="utf-8")
    _git_commit(anchor.parent, "an approval of a different freeze")

    assert freeze_gate(tree) == 1
    out = capsys.readouterr().out
    assert "LOSS OF LOCK" in out
    assert stale in out
    assert manifest["root"] in out
    assert "The frozen contract moved" not in out


def test_a_moved_contract_and_a_deleted_anchor_are_both_named(tree, anchor, capsys):
    """Two true causes, two sentences. Either alone leaves half the work undone.

    The pair the previous shape could not produce: it printed the movement
    sentence and nothing about the anchor, so an operator who re-froze the moved
    contract met the same red again with no idea why.
    """
    _git_init(anchor.parent)
    _git_commit(anchor.parent, "the gate artifact, with no approval in it")
    manifest = _bound_manifest(tree, anchor)
    write_freeze(tree, manifest)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n", encoding="utf-8")
    _git_commit(anchor.parent, "the approval")

    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    anchor.unlink()

    assert freeze_gate(tree) == 1
    out = capsys.readouterr().out
    assert "LOSS OF LOCK" in out
    assert "The frozen contract moved" in out
    assert "for the per-file report" in out
    assert "is gone" in out


def test_a_substituted_repository_reddens_the_gate(tree, anchor, capsys):
    """Identity, not presence, at the surface that fires.

    The substitute is a real repository carrying a COMMITTED anchor line holding
    exactly the hash the gate wants, so a binding recording only "the anchor was
    inside SOME repository" calls this green.
    """
    _git_init(anchor.parent)
    _git_commit(anchor.parent, "the gate artifact")
    manifest = _bound_manifest(tree, anchor)
    write_freeze(tree, manifest)

    shutil.rmtree(anchor.parent / ".git")
    _git_init(anchor.parent)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n", encoding="utf-8")
    _git_commit(anchor.parent, "a fresh repository carrying a forged approval")

    assert freeze_gate(tree) == 1
    out = capsys.readouterr().out
    assert "LOSS OF LOCK" in out
    assert "a different repository than the freeze recorded" in out


def test_run_tests_calls_the_gate_before_pytest():
    """The wiring is the whole point, so assert it explicitly.

    Source inspection rather than an import: run-tests.py calls ensure_venv() at
    import time, which re-execs the interpreter outside .venv. Matched on the
    call name only, not the exact argument text, so renaming a local does not
    fail a test that is about ordering.
    """
    source = (REPO_ROOT / "scripts" / "run-tests.py").read_text()
    assert "freeze_gate(" in source
    assert source.index("freeze_gate(") < source.index("subprocess.run(")


# ============================================================
# The conftest hook — the gate over the CLASS of pytest invocations
# ============================================================
# run-tests.py runs once at the end of a slice, or not at all. Bare
# `pytest tests/test_thing.py` is the inner-loop command a build runs dozens of
# times, and it has to refuse the same way. The hook is directly testable, so it
# is tested behaviourally rather than by reading the source.


@pytest.fixture
def conftest_module():
    """A second copy of tests/conftest.py, loaded by path.

    Importing it fresh keeps the monkeypatched _ENGINE_ROOT off the conftest the
    live session is running under. Executing it twice is inert: it sets one env
    default and defines hooks.
    """
    path = REPO_ROOT / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location("canopus_conftest_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_conftest_hook_is_silent_when_no_freeze_is_active(conftest_module, tree, monkeypatch, capsys):
    monkeypatch.setattr(conftest_module, "_ENGINE_ROOT", tree)
    conftest_module.pytest_sessionstart(session=None)
    assert capsys.readouterr().out == ""


def test_conftest_hook_passes_while_the_lock_is_held(conftest_module, tree, anchor, monkeypatch, capsys):
    manifest = build_manifest([tree / "tests"], tree, label="demo",
                              frozen_at=STAMP, anchor=anchor)
    write_freeze(tree, manifest)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n")
    monkeypatch.setattr(conftest_module, "_ENGINE_ROOT", tree)
    conftest_module.pytest_sessionstart(session=None)
    assert "LOCK HELD" in capsys.readouterr().out


def test_conftest_hook_aborts_the_session_when_the_contract_moved(
    conftest_module, tree, anchor, monkeypatch
):
    """A moved contract stops the run before collection, so bare pytest cannot
    reach green on a target the builder moved."""
    manifest = build_manifest([tree / "tests"], tree, label="demo",
                              frozen_at=STAMP, anchor=anchor)
    write_freeze(tree, manifest)
    anchor.write_text(f"canopus-anchor: {manifest['root']}\n")
    (tree / "tests" / "test_alpha.py").write_text("def test_a():\n    assert False\n")
    monkeypatch.setattr(conftest_module, "_ENGINE_ROOT", tree)
    with pytest.raises(pytest.UsageError, match="frozen test contract moved"):
        conftest_module.pytest_sessionstart(session=None)


def test_conftest_hook_aborts_on_a_corrupt_manifest(conftest_module, tree, monkeypatch):
    (tree / ".canopus").mkdir(parents=True)
    (tree / ".canopus" / "freeze.json").write_text("{ not json")
    monkeypatch.setattr(conftest_module, "_ENGINE_ROOT", tree)
    with pytest.raises(pytest.UsageError):
        conftest_module.pytest_sessionstart(session=None)


def test_the_gate_names_an_open_release_window(tree, capsys):
    from scripts.utils.canopus_freeze import append_history

    append_history(tree, "release", digest="", label="demo",
                   reason="mid-build recipe change", kind="window")

    assert freeze_gate(tree) == 0
    out = capsys.readouterr().out
    assert "release window is open" in out
    assert "mid-build recipe change" in out


def test_the_gate_is_silent_after_a_shipped_slice(tree, capsys):
    from scripts.utils.canopus_freeze import append_history

    append_history(tree, "release", digest="", label="demo",
                   reason="wire 2.2 shipped", kind="ship")

    assert freeze_gate(tree) == 0
    assert capsys.readouterr().out == ""


def test_the_gate_survives_an_unreadable_ledger(tree, capsys):
    """A raise in the gate fails OPEN, so an unparseable ledger must not raise."""
    from scripts.utils.canopus_freeze import history_state_path

    path = history_state_path(tree)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xe9 not utf-8 and not json\n")

    assert freeze_gate(tree) == 0


# ============================================================
# The never-raise SHAPE
# ============================================================
# Three separate repairs in one slice pinned three separate inputs, and the
# fourth input walked past all three. These two tests are about the shape rather
# than about an input: whatever the state directory looks like, and whatever
# raises inside, freeze_gate returns an int and fails closed.


@pytest.mark.skipif(getattr(os, "geteuid", lambda: 1)() == 0,
                    reason="mode 000 denies nothing to root")
def test_the_gate_survives_an_unreadable_state_directory(tree, capsys):
    """`.canopus/` at mode 000, measured before the fix, not reasoned about.

    `read_freeze` calls `Path.exists()`, which re-raises PermissionError rather
    than answering False, and the gate's handler named FreezeCorrupt only. So the
    gate raised straight out: the pytest session start crashed instead of
    reporting a state, and the PreToolUse dispatcher's catch-all logged an
    advisory and CONTINUED while writes to frozen paths sailed through.
    """
    state = tree / ".canopus"
    state.mkdir(parents=True)
    (state / "freeze.json").write_text("{}")
    # The mode is captured and restored rather than reset to a literal, so the
    # tmp tree is left exactly as pytest made it and no permissive mask is
    # written down here for a linter to argue about.
    original = state.stat().st_mode
    os.chmod(state, 0o000)
    try:
        assert freeze_gate(tree) == 1
    finally:
        os.chmod(state, original)
    assert "could not be established" in capsys.readouterr().out


def test_the_gate_fails_closed_on_an_unexpected_exception(tree, monkeypatch, capsys):
    """The shape, stated as a test: an exception of ANY type exits 1, not up.

    Deliberately a type no handler in this file names, because naming one more
    handler per measured input is exactly the pattern that left the mode-000 case
    open after three repairs of the same invariant.
    """
    import scripts.utils.canopus_gate as gate

    def explode(_root):
        raise RuntimeError("something no handler here names")

    monkeypatch.setattr(gate, "read_freeze", explode)

    assert gate.freeze_gate(tree) == 1
    out = capsys.readouterr().out
    assert "RuntimeError" in out
    assert "something no handler here names" in out
