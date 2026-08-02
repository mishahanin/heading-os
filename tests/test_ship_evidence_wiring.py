"""The ship-evidence gate is wired into `release --ship`, and in the right order.

The frozen contract for this slice decides the RULES: which ledger state refuses,
which render qualifies, and when a perished attestation must stop a ship. It
cannot decide that the CLI asks. `attestation_refusal` in particular is a pure
function over two strings, so a contract that only calls it directly would pass
against a release path that never consults it -- which is exactly the defect the
previous slice shipped and had to be told about at step 11: a checker with no
consumer is dead code plus a claim that reads as enforced.

Every scratch root in the contract is a plain directory rather than a git working
copy, so `tree_state` answers None there and the perished-attestation branch is
unreachable by construction. That is correct behaviour and it is also why these
tests exist: they make the tree judgeable on purpose, which is the only way to
reach the branch through the CLI at all.
"""

import ast
import json
from pathlib import Path

import pytest

from scripts import canopus
from scripts.canopus import main

_ROOT = Path(__file__).resolve().parents[1]


def _make_tree(root: Path) -> Path:
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_alpha.py").write_text(
        "def test_a():\n    assert True\n", encoding="utf-8"
    )
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run-tests.py").write_text("# stub test gate\n", encoding="utf-8")
    return root


@pytest.fixture
def tree(tmp_path: Path, monkeypatch) -> Path:
    root = _make_tree(tmp_path / "tree")
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def anchor(tmp_path: Path) -> Path:
    path = tmp_path / "outside" / "gate-artifact.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# gate artifact\n\n## Phase 1 — Success criteria\n\n"
        "- **SC-1** WHEN a scratch slice runs, THE SYSTEM SHALL behave as the "
        "test says.\n",
        encoding="utf-8",
    )
    return path


def _run(argv, tree):
    return main(["--root", str(tree), *argv])


def _freeze(tree, anchor):
    return _run(["freeze", "tests/test_alpha.py", "--label", "shipev",
                 "--anchor", str(anchor)], tree)


def _judgeable(monkeypatch):
    """Make the scratch tree answer as a git working copy would.

    The value is opaque to the branch under test: `cmd_release` only asks
    whether the sample is None, and `attestation_state` compares it against the
    record's own tree block, which a scratch attestation does not carry.
    """
    monkeypatch.setattr(canopus, "tree_state", lambda root: {"tests/test_alpha.py": "abc"})


def _calls_in(tree, name):
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and ((isinstance(node.func, ast.Name) and node.func.id == name)
                 or (isinstance(node.func, ast.Attribute) and node.func.attr == name))]


def test_a_ship_is_refused_when_the_record_does_not_attest_a_judgeable_tree(
    tree, anchor, monkeypatch, capsys
):
    """The wiring, end to end through the CLI rather than through the helper.

    No run has attested this freeze, so the record cannot stand for the tree.
    With the tree judgeable the ship must stop, and the freeze must survive it:
    a refusal that also ended the lock would leave the slice neither shipped nor
    protected.
    """
    assert _freeze(tree, anchor) == 0
    assert _run(["pack"], tree) == 0
    _judgeable(monkeypatch)
    capsys.readouterr()

    assert _run(["release", "--ship", "--reason", "done"], tree) != 0

    err = capsys.readouterr().err
    assert "run-tests" in err
    assert (tree / ".canopus" / "freeze.json").exists()


def test_the_refusal_lands_in_the_ledger_as_a_declared_cause(
    tree, anchor, monkeypatch, capsys
):
    """A refusal nothing counts is the state the denial counter exists to end.

    The cause must also be a member of the declared vocabulary, or the yield
    report renders a class nothing named -- the drift `evidence_missing` made
    two hours before this test was written.
    """
    from scripts.utils.gate_yield import CAUSES

    assert _freeze(tree, anchor) == 0
    assert _run(["pack"], tree) == 0
    _judgeable(monkeypatch)
    capsys.readouterr()
    assert _run(["release", "--ship", "--reason", "done"], tree) != 0

    # The cause rides in `kind`, not in a field of its own: `record_refusal`
    # reuses the ledger's existing columns rather than widening the schema, and
    # a test asserting on a `cause` key passes vacuously against a ledger that
    # has none. Read from the writer, not from the shape the reader expected.
    causes = [json.loads(line).get("kind")
              for line in (tree / ".canopus" / "history.jsonl")
              .read_text(encoding="utf-8").splitlines() if line.strip()]
    assert "attestation_perished" in causes
    assert "attestation_perished" in CAUSES


def test_a_tree_that_cannot_be_judged_does_not_stop_the_ship(tree, anchor):
    """The other arm, and the one that keeps the gate honest.

    Without it this file would pass against a blanket refusal on NOT ATTESTED,
    which is a wall no root outside a git working copy could pass and a refusal
    on a fault rather than on haste.
    """
    assert _freeze(tree, anchor) == 0
    assert _run(["pack"], tree) == 0

    assert _run(["release", "--ship", "--reason", "done"], tree) == 0
    assert not (tree / ".canopus" / "freeze.json").exists()


def test_the_attestation_check_runs_before_the_release_is_recorded():
    """The ordering IS the requirement, and "called somewhere" would not be.

    A check running after the ledger append would refuse nothing worth
    refusing: the release event would already be written and the freeze already
    cleared. Read from the source, because no test can observe an ordering that
    both branches make invisible.
    """
    source = (_ROOT / "scripts" / "canopus.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    release = next(node for node in ast.walk(module)
                   if isinstance(node, ast.FunctionDef) and node.name == "cmd_release")

    checks = _calls_in(release, "attestation_refusal")
    # The RELEASE append specifically. `cmd_release` opens with the --force
    # path, whose own `_record("force_release", ...)` sits above this gate by
    # design and is never subject to it, so comparing against the first `_record`
    # in the function measured the wrong call and failed on correct code.
    records = [node for node in _calls_in(release, "_record")
               if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
               and node.args[1].value == "release"]
    evidence = _calls_in(release, "evidence_state")

    assert checks, "cmd_release never calls attestation_refusal"
    assert records, "cmd_release never records a release event"
    assert min(c.lineno for c in checks) < min(r.lineno for r in records), (
        "the attestation check runs after the release is recorded, so the event "
        "it exists to withhold is already in the ledger"
    )
    assert evidence and min(c.lineno for c in checks) < min(e.lineno for e in evidence), (
        "the evidence check runs first, so a perished attestation sends the "
        "operator to `pack` before the gate that invalidates that page anyway"
    )
