"""The production-shape check is wired into both gates, and in the right order.

The frozen contract for this slice pinned the checker's BEHAVIOUR and bound no
criterion to its wiring, which is the "a renderer nothing calls" gap in its own
right: a checker nobody invokes is dead code plus a claim that reads as
enforced. These tests close that gap from outside the contract.

Both halves matter and they catch different things. At freeze the import closure
can only reach modules that already exist, so a slice EXTENDING code that reads a
record store is refused there. A slice building a brand new module is not, and
that is precisely the case the slice exists for: gate_yield.py did not exist when
its own contract was frozen. The hard half therefore runs when a session
attests, by which time the code is on disk.
"""

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _calls_in(tree, name):
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
    ]


def test_the_freeze_path_consults_the_production_shape_check():
    """The soft half: approve and freeze share one candidate builder, so one
    call there covers both entry points."""
    tree = ast.parse((_ROOT / "scripts" / "canopus.py").read_text(encoding="utf-8"))

    assert _calls_in(tree, "shape_refusal"), "canopus.py never calls shape_refusal"


def test_the_attestation_path_consults_the_check_before_it_writes():
    """The hard half, and the ordering IS the requirement.

    A check that ran after write_attestation would refuse nothing: the record
    the refusal exists to withhold would already be on disk. Pinning only "it is
    called somewhere in the file" would pass for that broken arrangement.
    """
    source = (_ROOT / "scripts" / "utils" / "canopus_gate.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)

    checks = _calls_in(tree, "shape_refusal")
    writes = _calls_in(tree, "write_attestation")

    assert checks, "canopus_gate.py never calls shape_refusal"
    assert writes, "canopus_gate.py never calls write_attestation"
    assert min(c.lineno for c in checks) < max(w.lineno for w in writes), (
        "shape_refusal runs after write_attestation, so the record it exists to "
        "withhold is already written by the time it answers"
    )


def test_an_unexpected_fault_inside_the_checker_refuses_nothing():
    """Totality, and the frozen contract does not pin it.

    Found by mutation at step 11: deleting the catch-all in shape_refusal kills
    none of the ten contract tests. Both SC-6 cases return "" before they ever
    reach it, one on an empty file list and one on the inner SyntaxError
    handler, so the guarantee that a bug in this checker cannot become a wall no
    slice can pass was measured by nothing.

    An int is not path-like, so Path() raises TypeError well inside the try,
    which is a fault of a kind no inner handler names.
    """
    from scripts.utils.production_shape import shape_refusal

    assert shape_refusal([1], Path("/nonexistent-root")) == ""


def test_the_checker_is_inside_the_frozen_enforcer_set():
    """A gate whose own checker can be edited mid-slice is not a gate.

    The sibling guard in tests/test_canopus_freeze.py recomputes the whole
    import closure; this one names the single file this slice added, so a
    revert that drops it from the documented command fails here with the reason
    rather than as an opaque closure diff.
    """
    skill = (_ROOT / ".claude" / "skills" / "canopus" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "--content scripts/utils/production_shape.py" in skill
