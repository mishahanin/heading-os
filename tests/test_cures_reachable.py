"""The retired contract for `cures-reachable` — the two-command cure deadlocked.

Shipped 2026-08-04 and promoted here from
`tests/contract/2026-08-04-cures-reachable/`, where it was frozen. Every ID it
held is kept: a contract retired by deletion takes its coverage with it, and the
deadlock below is the kind of defect that returns the moment nothing watches for
it.

Two of these overlap with tests added during the slice's `/scrutinize` pass, and
the overlap is deliberate rather than untidy. SC-3 and SC-3b here call
`build_attestation` DIRECTLY with the argument handed in, which is what makes
them a statement about that function; `test_canopus_attest.py`'s
`test_a_run_taken_under_a_moved_enforcer_does_not_attest` and its sibling drive
the real pytest hooks, which is what makes them a statement about the RECORDER
sampling and passing it on. That pair is the defect the scrutiny pass found:
this file alone stayed green over a recorder that never populated the field.
Neither test replaces the other.

ONE defect, found by RUNNING the standard on 2026-08-04 rather than by reading
it. Editing an enforcer reddens the lock; `tests/conftest.py` then refuses to run
ANY pytest session; an `always_run` pre-commit hook runs one; so the commit
`repin` demands cannot be made, and `repin` refuses without it. The whole saving
the `manifest-split` slice advertised — a byte change to the checker costs a
re-pin, not a re-approval — is unreachable on this repository.

The fix is one lever, and it opens one hole that has to be closed with it:

1. `freeze_gate` lets the session run when a moved ENFORCER is the sole red
   cause. Every other cause still stops it.
2. A run taken under a moved enforcer cannot ATTEST. That is what pays for (1):
   the enforcer set holds the test runner, the interpreter chooser and
   `conftest.py`, so a green record produced by edited bytes is a DIFFERENT
   checker's word. The refusal is at RECORD time, because the read side is
   already covered — `tree_drift` compares HEAD and every dirty path, so an
   enforcer edited AFTER a clean attestation already voids it, committed or not.

Two things this slice deliberately does NOT touch, named here so a later reader
does not mistake the silence for coverage. An OBSOLETE manifest (a valid
manifest of a known older recipe) still raises the same `FreezeCorrupt` as a
damaged one, so a recipe bump still costs the FORCE escape. And nothing binds
the root-hash payload's SHAPE to the recipe. Both are real, both were met during
the `enforcer-set-bound` slice, and neither is this deadlock.

Every test imports the code under test INSIDE its body, and every test takes its
own scratch tree under `tmp_path`. Nothing here reads the engine's working tree,
so nothing here is decided by the lock this slice is itself running under.
"""

import subprocess
import sys
from pathlib import Path

import pytest

STAMP = "2026-01-01T00:00:00+00:00"
# parents[1], not [3]: this file lived two directories deeper while it was the
# frozen contract. The depth is spelled against THIS file's location rather than
# carried over, because the promotion is exactly when a path like this goes
# quietly wrong -- the CLI then resolves outside the repository, subprocess finds
# no such file, and the end-to-end test fails for a reason that has nothing to do
# with what it is testing.
_CLI = Path(__file__).resolve().parents[1] / "scripts" / "canopus.py"


def _attestable(**overrides):
    """Every argument a record needs to reach `attested: True`, bar the one
    under test.

    Spelled out rather than minimised, because `build_attestation` refuses on
    each of a process block, a plugin baseline and a tree description
    independently. A fixture carrying only the interesting argument refuses for
    three reasons that are not the criterion, and SC-3 would then pass over an
    implementation that had never read `enforcer_moved` at all.
    """
    from scripts.utils.canopus_freeze import TREE_RECIPE

    tree = {"recipe": TREE_RECIPE, "head": "a" * 40, "dirty": {}}
    kwargs = {
        "frozen_tests": {"tests/contract/test_c.py": {"collected": 1, "passed": 1}},
        "exit_status": 0,
        "attested_at": STAMP,
        "process": {"plugins": {}, "intree_plugins": [], "other_plugins": [],
                    "launcher": "pytest", "addopts_p": [], "env": {},
                    "workers": []},
        "plugin_baseline": [],
        "tree_at_start": dict(tree),
        "tree_at_finish": dict(tree),
    }
    kwargs.update(overrides)
    return kwargs


# ============================================================
# Scratch trees
# ============================================================

@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A working tree with a contract file and two enforcer files."""
    root = tmp_path / "tree"
    (root / "tests" / "contract").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    (root / "tests" / "contract" / "test_c.py").write_text(
        "def test_c():\n    assert True\n", encoding="utf-8")
    (root / "scripts" / "run-tests.py").write_text("# enforcer one\n", encoding="utf-8")
    (root / "scripts" / "gate.py").write_text("# enforcer two\n", encoding="utf-8")
    return root


@pytest.fixture
def anchor(tmp_path: Path) -> Path:
    path = tmp_path / "outside" / "gate-artifact.md"
    path.parent.mkdir(parents=True)
    path.write_text("# gate artifact\n\n"
                    "## Phase 1 — Success criteria\n\n"
                    "- **SC-1** WHEN a scratch slice runs, THE SYSTEM SHALL "
                    "behave as the test says.\n", encoding="utf-8")
    return path


_BOTH = ("scripts/run-tests.py", "scripts/gate.py")


def _manifest(tree: Path, anchor: Path, enforcers=_BOTH):
    from scripts.utils.canopus_freeze import build_manifest

    return build_manifest(
        [tree / "tests" / "contract"], tree,
        label="s", frozen_at=STAMP, anchor=anchor,
        content_only=[tree / rel for rel in enforcers])


def _git(repo: Path, *argv: str) -> None:
    subprocess.run(["git", "-C", str(repo), *argv], check=True,
                   capture_output=True, text=True)


def _gate_repo(anchor: Path) -> Path:
    """The anchor's directory as a repository with a SYNTHETIC identity."""
    gate = anchor.parent
    for argv in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "builder@example.invalid"],
                 ["config", "user.name", "Builder"],
                 ["commit", "-q", "--allow-empty", "-m", "seed"]):
        _git(gate, *argv)
    return gate


def _cli(tree: Path, *argv: str):
    return subprocess.run(
        [sys.executable, str(_CLI), "--root", str(tree), *argv],
        capture_output=True, text=True, cwd=str(tree))


def _held(tree: Path, anchor: Path):
    """A freeze written to *tree* with the anchor recording its root."""
    from scripts.utils.canopus_freeze import write_freeze

    manifest = _manifest(tree, anchor)
    write_freeze(tree, manifest)
    anchor.write_text(f"# gate\n\ncanopus-anchor: {manifest['root']}\n",
                      encoding="utf-8")
    return manifest


# ============================================================
# SC-1 — a moved ENFORCER alone no longer stops the suite
# ============================================================

def test_the_gate_lets_the_suite_run_when_the_enforcer_is_the_sole_cause(tree, anchor):
    """SC-1. WHEN the only red cause is a moved enforcer, THE SYSTEM SHALL let
    the pytest session run.

    The deadlock, at its root. `freeze_gate` returns non-zero for every LOSS OF
    LOCK alike, `pytest_sessionstart` raises on that, and the cure for this one
    cause needs a pytest session to get through the commit hooks.
    """
    from scripts.utils.canopus_gate import freeze_gate

    _held(tree, anchor)
    (tree / "scripts" / "gate.py").write_text("# enforcer two, edited\n", encoding="utf-8")

    assert freeze_gate(tree) == 0, (
        "an enforcer edit still stops every pytest session, so the commit that "
        "`repin` requires cannot be made and the documented cure is circular")


def test_the_gate_still_says_the_enforcer_moved_while_letting_the_run_through(
    tree, anchor, capsys
):
    """SC-1b. Permitted is not the same as silent, and this is the half a
    permissive fix loses. The operator has to be told, by name, and given the
    cure; a gate that quietly returns 0 has replaced a deadlock with a hole.

    The PAIRING is the assertion, which is why the exit code is checked here as
    well as in SC-1. While this was a frozen contract the two halves were
    satisfied by DIFFERENT states — the sentence was already printed on the way
    to exit 1, and only the exit code was red — which is why asserting the
    sentence alone was never enough. That reading is kept now that both halves
    hold at once: a later change that returned 0 in silence, or one that printed
    the cause and went on refusing, moves exactly one of these assertions, and
    either is the regression this criterion exists to catch.
    """
    from scripts.utils.canopus_gate import freeze_gate

    _held(tree, anchor)
    (tree / "scripts" / "gate.py").write_text("# enforcer two, edited\n", encoding="utf-8")
    capsys.readouterr()

    code = freeze_gate(tree)
    out = capsys.readouterr().out

    assert code == 0, "the run is still refused"
    assert "scripts/gate.py" in out, "the moved enforcer is not named"
    assert "repin" in out, "the cure is not offered"


def test_the_sole_cause_question_is_asked_of_the_whole_state(tree, anchor):
    """SC-1c. The rule as a named function, so the gate and any later reader ask
    it once rather than each re-deriving it from the report's fields.

    Derived by asking `lock_state` what the state WOULD be with the enforcer
    axis empty, never by a second copy of the redness rule: two spellings of one
    decision is how the two come to disagree.
    """
    from scripts.utils.canopus_freeze import (
        ANCHOR_RECORDED, enforcer_is_sole_cause, verify_manifest)

    manifest = _manifest(tree, anchor)
    (tree / "scripts" / "gate.py").write_text("# enforcer two, edited\n", encoding="utf-8")
    report = verify_manifest(manifest, tree)

    assert enforcer_is_sole_cause(
        report, ANCHOR_RECORDED, report["recomputed_root"]) is True


# ============================================================
# SC-2 — every other red cause still stops the suite
# ============================================================

def test_a_moved_CONTRACT_still_stops_the_suite(tree, anchor):
    """SC-2. WHEN the contract itself moved, THE SYSTEM SHALL still stop the
    session before collection.

    The control the whole standard rests on. A fix that opened the gate for any
    red cause would buy the cure by giving up the thing being cured.
    """
    from scripts.utils.canopus_gate import freeze_gate

    _held(tree, anchor)
    (tree / "tests" / "contract" / "test_c.py").write_text(
        "def test_c():\n    assert False\n", encoding="utf-8")

    assert freeze_gate(tree) == 1, "a moved contract no longer stops the suite"


def test_a_moved_contract_AND_a_moved_enforcer_still_stops_the_suite(tree, anchor):
    """SC-2b. The pairing a "the enforcer moved, so let it through" shortcut gets
    wrong: the enforcer is not the SOLE cause here, and the graver one governs."""
    from scripts.utils.canopus_gate import freeze_gate

    _held(tree, anchor)
    (tree / "scripts" / "gate.py").write_text("# edited\n", encoding="utf-8")
    (tree / "tests" / "contract" / "test_c.py").write_text(
        "def test_c():\n    assert False\n", encoding="utf-8")

    assert freeze_gate(tree) == 1


def test_a_disagreeing_anchor_still_stops_the_suite_even_with_an_enforcer_moved(
    tree, anchor
):
    """SC-2c. The other axis. `lock_state` reads three, and a sole-cause test
    written against the content report alone would call this one permitted."""
    from scripts.utils.canopus_freeze import (
        ANCHOR_RECORDED, enforcer_is_sole_cause, verify_manifest)

    manifest = _manifest(tree, anchor)
    (tree / "scripts" / "gate.py").write_text("# edited\n", encoding="utf-8")
    report = verify_manifest(manifest, tree)

    assert enforcer_is_sole_cause(report, ANCHOR_RECORDED, "0" * 64) is False, (
        "the anchor records a hash this tree does not compute and the enforcer "
        "was still called the sole cause")


# ============================================================
# SC-3 — a run taken under a moved enforcer cannot attest
# ============================================================

def test_a_run_taken_while_an_enforcer_had_moved_does_not_attest(tree, anchor):
    """SC-3. WHEN an enforcer has moved, THE SYSTEM SHALL NOT record ATTESTED.

    This is what pays for SC-1. Letting the suite RUN under a moved checker is
    only safe while that run cannot claim anything, and the claim is made HERE,
    at record time. The root hash cannot carry this: `manifest-split` took the
    enforcer digests out of the payload on purpose, so a moved enforcer leaves
    `recomputed_root` exactly where it was and the record's root comparison sees
    nothing.
    """
    from scripts.utils.canopus_freeze import build_attestation

    manifest = _manifest(tree, anchor)
    record = build_attestation(**_attestable(
        root_digest=manifest["root"], enforcer_moved=["scripts/gate.py"]))

    assert record["attested"] is False, (
        "a run under an edited checker recorded itself as attesting the freeze")
    assert any("scripts/gate.py" in reason for reason in record["reasons"]), (
        f"no reason names the moved enforcer: {record['reasons']!r}")


def test_a_run_with_the_enforcer_intact_still_attests(tree, anchor):
    """SC-3b. Without this, SC-3 is satisfied by never attesting at all.

    The pairing matters more than either half. A `build_attestation` that
    refused a little more readily would pass SC-3 and silently make every
    ordinary slice unable to reach step 9, which is a worse failure than the one
    SC-3 closes: it is invisible until somebody is stuck.
    """
    from scripts.utils.canopus_freeze import build_attestation

    manifest = _manifest(tree, anchor)
    record = build_attestation(**_attestable(
        root_digest=manifest["root"], enforcer_moved=[]))

    assert record["attested"] is True, record["reasons"]


def test_the_moved_enforcer_answer_is_required_not_defaulted(tree, anchor):
    """SC-3c [failure-mode]. A caller that does not answer gets an ERROR, never a
    silent "nothing moved".

    The defect shape this slice must not repeat. `lock_state` shipped its anchor
    pair as optional and the greener reading became the default, so a caller that
    forgot them was told LOCK HELD over a freeze nobody approved. An optional
    `enforcer_moved` would do the same thing one axis over: every attestation
    written by a caller that had not been updated would read clean.
    """
    from scripts.utils.canopus_freeze import build_attestation

    manifest = _manifest(tree, anchor)
    kwargs = _attestable(root_digest=manifest["root"])

    with pytest.raises(TypeError):
        build_attestation(**kwargs)


# ============================================================
# SC-4 — the two-command cure completes
# ============================================================

def test_the_documented_enforcer_cure_completes_end_to_end(tree, anchor):
    """SC-4 [integration]. WHEN an enforcer is edited under a held freeze, THE
    SYSTEM SHALL let the cure complete: the suite runs, the commit is possible,
    `repin` is accepted, and `verify` returns to LOCK HELD.

    The whole slice in one sequence, through the CLI. The pre-commit hook that
    exposed the deadlock is repository configuration rather than code, so what
    stands in for it here is the gate call the hook's pytest run makes: if that
    returns 0, the hook passes and the commit lands.
    """
    from scripts.utils.canopus_gate import freeze_gate

    gate = _gate_repo(anchor)
    subprocess.run(["git", "init", "-q"], cwd=str(tree), check=True,
                   capture_output=True)
    for argv in (["config", "user.email", "builder@example.invalid"],
                 ["config", "user.name", "Builder"]):
        _git(tree, *argv)
    (tree / ".gitignore").write_text(".canopus/\n", encoding="utf-8")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "the tree")

    approved = _cli(tree, "approve", "tests/contract", "--label", "s",
                    "--anchor", str(anchor),
                    "--content", "scripts/run-tests.py", "--content", "scripts/gate.py")
    assert approved.returncode == 0, approved.stderr
    _git(gate, "add", anchor.name)
    _git(gate, "commit", "-q", "-m", "the approval")
    frozen = _cli(tree, "freeze", "tests/contract", "--label", "s",
                  "--anchor", str(anchor),
                  "--content", "scripts/run-tests.py", "--content", "scripts/gate.py")
    assert frozen.returncode == 0, f"{frozen.stdout}\n{frozen.stderr}"

    (tree / "scripts" / "gate.py").write_text("# enforcer two, fixed\n", encoding="utf-8")
    assert freeze_gate(tree) == 0, "step 1 of the cure is still blocked"

    _git(tree, "add", "-A")
    _git(tree, "commit", "-q", "-m", "the enforcer fix")

    repinned = _cli(tree, "repin", "--reason", "the enforcer was fixed")
    assert repinned.returncode == 0, f"{repinned.stdout}\n{repinned.stderr}"

    checked = _cli(tree, "verify")
    assert "LOCK HELD" in checked.stdout, f"{checked.stdout}\n{checked.stderr}"
