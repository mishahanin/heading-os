"""Frozen contract for the ship-evidence slice.

The evidence page for the operator's second approval exists (`canopus.py pack`)
and nothing requires it. It writes nothing, so whether it ran cannot be
established afterwards, and on 2026-08-02 the slice shipped hours earlier was
signed off on a PROSE SUMMARY, which the standard's own NEVER list forbids. No
artifact records that this happened.

What this slice buys, stated narrowly because four voices agreed the wide claim
is false: it does NOT make the approval real, and it cannot, because no machine
can witness a human reading. What it does buy is that the attestation record
perishes the moment the working tree moves, so a pack event required to be no
older than the attestation forces the evidence render to happen AFTER the last
change rather than merely once per slice. Shipping without a fresh evidence
render becomes impossible, and skipping one becomes auditable instead of
invisible.

The ledger is trusted about the render exactly when it remembers the freeze it
is being asked about. That single test settles the fragility the council pressed
hardest on: a ledger that lost its own freeze event cannot answer, so the ship
degrades with a warning rather than refusing, and a gate that pushes an honest
operator toward `--force` is worse than no gate.

Every test imports the code under test INSIDE its body: the implementation does
not exist when this contract is frozen. Every fixture is minted by a real writer
- the ledger through the CLI's own `freeze` and `pack`, the attestation through
`build_attestation` and `write_attestation` - never hand-authored, per the fifth
planning-gate rule.

Each docstring OPENS with the criterion it claims, per scripts/sc-trace.py.
"""

import json
from pathlib import Path

import pytest


def _make_tree(root: Path) -> Path:
    """A synthetic working tree carrying a test gate, which a freeze requires."""
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_alpha.py").write_text(
        "def test_a():\n    assert True\n", encoding="utf-8"
    )
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run-tests.py").write_text("# stub test gate\n", encoding="utf-8")
    return root


@pytest.fixture
def tree(tmp_path: Path, monkeypatch) -> Path:
    """Its own scratch root, never the engine's, per the second authoring rule."""
    root = _make_tree(tmp_path / "tree")
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def anchor(tmp_path: Path) -> Path:
    """A scratch gate artifact OUTSIDE the tree: an anchor inside the build's own
    tree is refused, and the criteria section is what A12 demands of one."""
    path = tmp_path / "outside" / "gate-artifact.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# gate artifact\n\n"
        "## Phase 1 — Success criteria\n\n"
        "- **SC-1** WHEN a scratch slice runs, THE SYSTEM SHALL behave as the "
        "test says.\n",
        encoding="utf-8",
    )
    return path


def _run(argv, tree):
    from scripts.canopus import main

    return main(["--root", str(tree), *argv])


def _freeze(tree, anchor):
    return _run(["freeze", "tests/test_alpha.py", "--label", "shipev",
                 "--anchor", str(anchor)], tree)


def _ledger(tree) -> list:
    path = tree / ".canopus" / "history.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _events(tree, name) -> list:
    return [row for row in _ledger(tree) if row.get("event") == name]


def _root_of(tree) -> str:
    return json.loads(
        (tree / ".canopus" / "freeze.json").read_text(encoding="utf-8")
    )["root"]


def _attest(tree, when: str) -> None:
    """Mint an attestation through the real writers, stamped at `when`.

    Hand-authoring this dict is exactly the defect the previous slice shipped a
    gate against: the record's shape is the writer's business, and a fixture
    that invents it proves nothing about the shape the writer emits.

    The per-file counters are a MAPPING, not a bare count, and this fixture was
    written the wrong way first: `{"tests/test_alpha.py": 1}` reached
    `counts.get("collected")` and raised `AttributeError: 'int' object has no
    attribute 'get'`. Caught at step 4, by the rule that says check the fixture
    against the shape the real source emits, on the one slice that shipped that
    rule's enforcer.
    """
    from scripts.utils.canopus_freeze import build_attestation, write_attestation

    write_attestation(tree, build_attestation(
        root_digest=_root_of(tree),
        frozen_tests={"tests/test_alpha.py": {"collected": 1, "passed": 1}},
        exit_status=0,
        attested_at=when,
    ))


# ---------------------------------------------------------------------------
# SC-1 - a rendered pack leaves a record carrying the freeze root
# ---------------------------------------------------------------------------


def test_pack_records_one_event_carrying_the_current_freeze_root(tree, anchor):
    """SC-1. A step that leaves no trace cannot be required, and requiring it is
    the only thing THE LAW says fixes a step nobody runs."""
    assert _freeze(tree, anchor) == 0
    root = _root_of(tree)

    assert _run(["pack"], tree) == 0

    packs = _events(tree, "pack")
    assert len(packs) == 1
    assert packs[0]["root"] == root


# ---------------------------------------------------------------------------
# SC-2 - a second render of the same shippable state adds nothing
# ---------------------------------------------------------------------------


def test_a_second_pack_over_the_same_state_appends_no_second_event(tree, anchor):
    """SC-2. `pack` is re-runnable by design, so without idempotence a debugging
    session inflates the ledger with lines that carry no new fact."""
    assert _freeze(tree, anchor) == 0
    assert _run(["pack"], tree) == 0
    assert _run(["pack"], tree) == 0

    assert len(_events(tree, "pack")) == 1


# ---------------------------------------------------------------------------
# SC-3 - a ledger that cannot be written does not cost the page
# ---------------------------------------------------------------------------


def test_pack_prints_its_page_and_says_so_when_the_record_cannot_land(
    tree, anchor, monkeypatch, capsys
):
    """SC-3. The render is the operator's evidence and the record is telemetry
    about it; losing the second must never cost the first, and a silent loss
    would leave the ship gate refusing later for a reason nobody was told."""
    from scripts import canopus

    assert _freeze(tree, anchor) == 0
    capsys.readouterr()

    def failing(root, event, **kwargs):
        raise OSError("ledger is full")

    monkeypatch.setattr(canopus, "append_history", failing)
    assert _run(["pack"], tree) == 0

    captured = capsys.readouterr()
    assert "CANOPUS" in captured.out
    assert "not recorded" in captured.err


# ---------------------------------------------------------------------------
# SC-4 - a fresh render lets the ship through unchanged
# ---------------------------------------------------------------------------


def test_ship_proceeds_when_a_qualifying_pack_event_exists(tree, anchor):
    """SC-4. The gate must be silent on the path the discipline was followed, or
    it is noise the next slice learns to route around."""
    assert _freeze(tree, anchor) == 0
    assert _run(["pack"], tree) == 0

    assert _run(["release", "--ship", "--reason", "done"], tree) == 0
    assert not (tree / ".canopus" / "freeze.json").exists()
    assert _events(tree, "release")


# ---------------------------------------------------------------------------
# SC-5 - no render, and the ledger remembers the freeze: refuse
# ---------------------------------------------------------------------------


def test_ship_is_refused_when_the_ledger_remembers_the_freeze_but_holds_no_pack(
    tree, anchor, capsys
):
    """SC-5. This is the whole slice: the ship that skipped the evidence page.
    The freeze must survive the refusal, because a refusal that also ends the
    lock would leave the slice in a state neither shipped nor protected."""
    assert _freeze(tree, anchor) == 0
    capsys.readouterr()

    assert _run(["release", "--ship", "--reason", "done"], tree) != 0

    err = capsys.readouterr().err
    # Content, not emptiness: the refusal must name the command that clears it,
    # or the operator is left guessing and the gate becomes something to disable.
    assert "pack" in err
    assert (tree / ".canopus" / "freeze.json").exists()


# ---------------------------------------------------------------------------
# SC-6 - a render older than the attestation is not evidence about this tree
# ---------------------------------------------------------------------------


def test_a_pack_older_than_the_attestation_does_not_qualify(tree, anchor, capsys):
    """SC-6. The property this slice actually buys. The attestation dies when
    the working tree moves, so demanding the render be no older than it is what
    forces the page to describe the FINAL state rather than some earlier one."""
    assert _freeze(tree, anchor) == 0
    assert _run(["pack"], tree) == 0
    _attest(tree, "2099-01-01T00:00:00+00:00")
    capsys.readouterr()

    assert _run(["release", "--ship", "--reason", "done"], tree) != 0
    assert "pack" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# SC-7 - a ledger that lost the freeze cannot answer, so it does not refuse
# ---------------------------------------------------------------------------


def test_ship_degrades_with_a_warning_when_the_ledger_forgot_the_freeze(
    tree, anchor, capsys
):
    """SC-7. A gate that refuses on a lost ledger trains the operator to reach
    for --force, which inflates the friction telemetry and destroys the stigma
    that flag carries. Fail closed against haste, open against a broken disk."""
    assert _freeze(tree, anchor) == 0
    (tree / ".canopus" / "history.jsonl").unlink()
    capsys.readouterr()

    assert _run(["release", "--ship", "--reason", "done"], tree) == 0
    assert "unverifiable" in capsys.readouterr().err
    assert not (tree / ".canopus" / "freeze.json").exists()


# ---------------------------------------------------------------------------
# SC-8 - a mid-slice window is not an approval and is not gated
# ---------------------------------------------------------------------------


def test_a_window_release_requires_no_pack_event(tree, anchor):
    """SC-8. A window is the way BACK into the build, not the way out of it.
    Gating it would demand an evidence page for work that is not finished."""
    assert _freeze(tree, anchor) == 0

    assert _run(["release", "--window", "--reason", "the frozen set was wrong"],
                tree) == 0
    assert not (tree / ".canopus" / "freeze.json").exists()
    assert not _events(tree, "pack")
